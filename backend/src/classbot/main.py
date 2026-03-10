# main.py
from __future__ import annotations

import io
import json
import logging
import re
import secrets
import hashlib

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.staticfiles import StaticFiles
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ollama
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlmodel import Field, Session, SQLModel, create_engine, select

from classbot.orchestrator.classbot import ClassBot
from classbot.prompts import GENERIC_RUBRIC
from classbot.retrieval import add_to_corpus, corpus_stats

# ---------------- Logging ----------------
logger = logging.getLogger("classbot.api")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

# ---------------- APP/FILES ----------------
BASE_DIR = Path(__file__).resolve().parent
FRONT_DIST = BASE_DIR.parents[2] / "front" / "dist"
ADMIN_HTML_PATH = BASE_DIR / "admin.html"
LOGIN_HTML_PATH = BASE_DIR / "login.html"



classbot = ClassBot()

# ---------------- DB MODELS ----------------
class Lesson(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    context: str = ""
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=False)


class Question(SQLModel, table=True):
    id: str = Field(primary_key=True)
    lessonId: str = Field(index=True)
    text: str
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=True)


class Response(SQLModel, table=True):
    id: str = Field(primary_key=True)
    lessonId: str = Field(index=True)
    questionId: str = Field(index=True)
    studentId: str = Field(index=True)
    studentName: str
    text: str
    score: Optional[int] = Field(default=None)  # 0-100
    rationale: Optional[str] = None
    mistakes: Optional[str] = None  # JSON string list
    tips: Optional[str] = None  # JSON string list
    createdAt: datetime = Field(default_factory=datetime.utcnow)


# ---------------- AUTH MODELS ----------------
class User(SQLModel, table=True):
    id: str = Field(primary_key=True)
    username: str = Field(index=True, unique=True)
    name: str
    role: str  # "student" | "teacher"
    password_hash: str
    createdAt: datetime = Field(default_factory=datetime.utcnow)


class SessionToken(SQLModel, table=True):
    token: str = Field(primary_key=True)
    userId: str = Field(index=True)
    expiresAt: datetime
    createdAt: datetime = Field(default_factory=datetime.utcnow)


# ---------------- SCHEMAS ----------------
class CreateLessonIn(BaseModel):
    name: str
    context: str = ""
    questions: List[str] = []  # opcional


class ActivateLessonIn(BaseModel):
    lessonId: str


class AddQuestionIn(BaseModel):
    text: str
    active: bool = True


class SubmitResponseIn(BaseModel):
    lessonId: str
    questionId: str
    studentId: str
    studentName: str
    answerText: str


class SuggestQuestionsIn(BaseModel):
    context: str
    n: int = 6
    model: Optional[str] = None


class SuggestQuestionsOut(BaseModel):
    questions: List[str]


class ChatIn(BaseModel):
    lessonId: str
    studentId: Optional[str] = None
    studentName: Optional[str] = None
    message: str
    model: Optional[str] = None
    temperature: Optional[float] = None


class ChatOut(BaseModel):
    refused: bool
    answer: str
    citations: List[int] = []
    evidence: List[str] = []


class GradeFeedbackOut(BaseModel):
    score0_10: int
    scorePct: int
    rationale: str
    mistakes: List[str] = []
    tips: List[str] = []


class SubmitResponseOut(BaseModel):
    id: str
    lessonId: str
    questionId: str
    studentId: str
    studentName: str
    text: str
    createdAt: datetime
    feedback: GradeFeedbackOut


# ---- AUTH SCHEMAS ----
class RegisterIn(BaseModel):
    name: str
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class AuthOut(BaseModel):
    token: str
    username: str
    name: str
    role: str


# ---------------- APP ----------------
app = FastAPI(title="SmartEdu API", version="0.3.3-auth-loginfile")

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em produção restringe
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONT_DIST.exists():
    assets_dir = FRONT_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/app", include_in_schema=False)
    def serve_student_app():
        return FileResponse(str(FRONT_DIST / "index.html"))

    @app.get("/app/{path:path}", include_in_schema=False)
    def serve_student_app_spa(path: str):
        # SPA fallback: qualquer rota do React devolve index.html
        return FileResponse(str(FRONT_DIST / "index.html"))
else:
    logger.warning("[FRONT] front/dist não existe. Corre: (cd front) npm run build")


engine = create_engine(
    "sqlite:///smartedu_lessons.db",
    connect_args={"check_same_thread": False},
)

def init_db() -> None:
    SQLModel.metadata.create_all(engine)

init_db()

# ---------------- AUTH HELPERS ----------------
def _role_from_username(username: str) -> str:
    u = (username or "").strip().lower()
    if u.startswith("d"):
        return "teacher"
    if u.startswith("a") or u.startswith("pg") or u.startswith("e"):
        return "student"
    raise HTTPException(400, "Formato de username inválido.")


def _hash_password(password: str, salt_hex: str | None = None) -> str:
    if not password or len(password) < 4:
        raise HTTPException(400, "Password demasiado curta.")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2$120000${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, _hash_hex = stored.split("$", 3)
        if algo != "pbkdf2":
            return False
        test = _hash_password(password, salt_hex)
        return secrets.compare_digest(test, stored)
    except Exception:
        return False


def _get_current_user(authorization: str = Header(default="")) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sem autenticação.")
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "Sem autenticação.")

    with Session(engine) as s:
        st = s.get(SessionToken, token)
        if not st:
            raise HTTPException(401, "Sessão inválida.")
        if st.expiresAt < datetime.utcnow():
            try:
                s.delete(st)
                s.commit()
            except Exception:
                pass
            raise HTTPException(401, "Sessão expirada.")
        user = s.get(User, st.userId)
        if not user:
            raise HTTPException(401, "Utilizador inválido.")
        return user


def _require_role(user: User, role: str) -> None:
    if user.role != role:
        raise HTTPException(403, "Sem permissões.")


def _seed_teacher() -> None:
    username = "d1234"
    password = "Admin123!"
    with Session(engine) as s:
        exists = s.exec(select(User).where(User.username == username)).first()
        if not exists:
            u = User(
                id="u_teacher_1",
                username=username,
                name="Docente",
                role="teacher",
                password_hash=_hash_password(password),
            )
            s.add(u)
            s.commit()
            logger.info("[AUTH] Seed teacher criado | username=%s | password=%s", username, password)

_seed_teacher()

# ---------------- Helpers (gerais) ----------------
def _to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)

    md = getattr(obj, "model_dump", None)
    if callable(md):
        return md()

    d1 = getattr(obj, "dict", None)
    if callable(d1):
        return d1()

    return dict(getattr(obj, "__dict__", {}) or {})


def placeholder_score(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    base = min(100, max(0, round(len(t) / 4)))
    bonus = 10 if any(
        k in t.lower() for k in ["exemplo", "conceito", "característica", "caracteristica"]
    ) else 0
    return max(0, min(100, base + bonus))


def _extract_json_object(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _dedupe_questions(qs: List[str], n: int) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for q in qs or []:
        if not isinstance(q, str):
            continue
        t = q.strip().lstrip("•").lstrip("-").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(t)
        if len(cleaned) >= n:
            break
    return cleaned


def _parse_date_yyyy_mm_dd(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def _safe_json_list(s: Optional[str]) -> List[str]:
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        return []
    except Exception:
        return []


def _norm_err(t: str) -> str:
    x = (t or "").strip().lower()
    x = re.sub(r"\s+", " ", x)
    x = x.strip(" .;:,-–—•")
    return x


def _percentile(sorted_vals: List[int], p: float) -> int:
    if not sorted_vals:
        return 0
    p = max(0.0, min(1.0, float(p)))
    idx = int(round((len(sorted_vals) - 1) * p))
    idx = max(0, min(len(sorted_vals) - 1, idx))
    return int(sorted_vals[idx])


# ---------------- Helpers (PDF / gráficos) ----------------
def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=160)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _plot_histogram(scores: List[int], title: str) -> bytes:
    bins = list(range(0, 110, 10))  # 0..100
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.hist(scores, bins=bins)
    ax.set_title(title)
    ax.set_xlabel("Pontuação")
    ax.set_ylabel("Nº respostas")
    ax.set_xlim(0, 100)
    return _fig_to_png_bytes(fig)


def _plot_line_avg_per_question(q_labels: List[str], avgs: List[float], title: str) -> bytes:
    fig = plt.figure()
    ax = fig.add_subplot(111)
    xs = list(range(1, len(avgs) + 1))
    ax.plot(xs, avgs, marker="o")
    ax.set_title(title)
    ax.set_xlabel("Pergunta")
    ax.set_ylabel("Média (%)")
    ax.set_ylim(0, 100)
    ax.set_xticks(xs)
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    return _fig_to_png_bytes(fig)


def _draw_wrapped_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    line_height: float,
) -> float:
    if not text:
        return y

    words = text.split()
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, "Helvetica", 10) <= max_width:
            line = test
        else:
            c.drawString(x, y, line)
            y -= line_height
            line = w
            if y < 2 * cm:
                c.showPage()
                c.setFont("Helvetica", 10)
                y = A4[1] - 2 * cm
    if line:
        c.drawString(x, y, line)
        y -= line_height
    return y


# ---------------- Startup ----------------
@app.on_event("startup")
def warmup_rag_on_startup():
    with Session(engine) as s:
        lessons = s.exec(select(Lesson)).all()

    rebuilt = 0
    skipped = 0

    t0 = datetime.utcnow()
    for lesson in lessons:
        ctx = (lesson.context or "").strip()
        if not ctx:
            skipped += 1
            continue

        add_to_corpus(
            [ctx],
            max_tokens=800,
            overlap_tokens=200,
            use_hf_tokenizer=True,
            uc=lesson.id,
        )
        rebuilt += 1

    dt = (datetime.utcnow() - t0).total_seconds()
    logger.info(
        "[RAG] warmup concluído | aulas indexadas=%d | sem contexto=%d | t_total=%.3fs",
        rebuilt,
        skipped,
        dt,
    )


# ---------------- API ----------------
@app.get("/health")
def health():
    return {"ok": True}


# ---------------- AUTH API ----------------
@app.post("/auth/register")
def auth_register(payload: RegisterIn):
    username = (payload.username or "").strip()
    name = (payload.name or "").strip()
    password = payload.password or ""

    role = _role_from_username(username)
    if role != "student":
        raise HTTPException(403, "Registo disponível apenas para alunos (a..., pg..., e...).")

    with Session(engine) as s:
        exists = s.exec(select(User).where(User.username == username)).first()
        if exists:
            raise HTTPException(409, "Username já existe.")

        u = User(
            id=f"u{int(datetime.utcnow().timestamp() * 1000)}",
            username=username,
            name=name or username,
            role="student",
            password_hash=_hash_password(password),
        )
        s.add(u)
        s.commit()

    return {"ok": True}


@app.post("/auth/login", response_model=AuthOut)
def auth_login(payload: LoginIn):
    username = (payload.username or "").strip()
    password = payload.password or ""

    with Session(engine) as s:
        user = s.exec(select(User).where(User.username == username)).first()
        if not user or not _verify_password(password, user.password_hash):
            raise HTTPException(401, "Credenciais inválidas.")

        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=10)

        s.add(SessionToken(token=token, userId=user.id, expiresAt=expires))
        s.commit()

        return AuthOut(token=token, username=user.username, name=user.name, role=user.role)


@app.get("/auth/me")
def auth_me(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    return {"username": user.username, "name": user.name, "role": user.role}


@app.post("/auth/logout")
def auth_logout(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        return {"ok": True}
    token = authorization.replace("Bearer ", "").strip()
    with Session(engine) as s:
        st = s.get(SessionToken, token)
        if st:
            s.delete(st)
            s.commit()
    return {"ok": True}


# ---------- AULAS ----------
@app.get("/lessons")
def list_lessons(authorization: str = Header(default="")):
    _ = _get_current_user(authorization)
    with Session(engine) as s:
        return s.exec(select(Lesson).order_by(Lesson.createdAt.desc())).all()


@app.get("/lessons/active")
def get_active_lesson(authorization: str = Header(default="")):
    _ = _get_current_user(authorization)
    with Session(engine) as s:
        lesson = s.exec(select(Lesson).where(Lesson.active == True)).first()
    return {"activeLesson": lesson}


@app.post("/lessons")
def create_lesson(payload: CreateLessonIn, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "Nome da aula vazio.")

    context = (payload.context or "").strip()
    questions = [q.strip() for q in (payload.questions or []) if q and q.strip()]

    lesson_id = f"a{int(datetime.utcnow().timestamp() * 1000)}"

    lesson = Lesson(id=lesson_id, name=name, context=context, active=False)

    with Session(engine) as s:
        s.add(lesson)
        for qt in questions:
            q = Question(
                id=f"q{int(datetime.utcnow().timestamp() * 1000)}_{abs(hash(qt))%100000}",
                lessonId=lesson_id,
                text=qt,
                active=True,
            )
            s.add(q)
        s.commit()
        s.refresh(lesson)

    if context:
        add_to_corpus([context], max_tokens=800, overlap_tokens=200, use_hf_tokenizer=True, uc=lesson_id)

    return lesson


@app.post("/lessons/activate")
def activate_lesson(payload: ActivateLessonIn, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    with Session(engine) as s:
        lesson = s.get(Lesson, payload.lessonId)
        if not lesson:
            raise HTTPException(404, "Aula não encontrada.")

        all_lessons = s.exec(select(Lesson)).all()
        for l in all_lessons:
            l.active = False
            s.add(l)

        lesson.active = True
        s.add(lesson)

        s.commit()
        s.refresh(lesson)
        return {"ok": True, "activeLesson": lesson}


# ---------- SUGESTÃO DE PERGUNTAS (IA) ----------
@app.post("/lessons/suggest-questions", response_model=SuggestQuestionsOut)
def suggest_questions(payload: SuggestQuestionsIn, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    ctx = (payload.context or "").strip()
    if not ctx:
        raise HTTPException(400, "Contexto vazio.")

    n = max(3, min(10, int(payload.n or 6)))
    model = (payload.model or "llama3.1:8b").strip()

    prompt = f"""
Gera {n} perguntas curtas e objetivas (nível ensino superior) com base no contexto abaixo.

Regras:
- Perguntas em PT-PT.
- Evita perguntas demasiado genéricas.
- Variedade: definição/conceito, comparação, aplicação e erros comuns.
- Não devolvas explicações nem texto extra.

Responde APENAS JSON válido:
{{"questions": ["...", "..."]}}

Contexto:
{ctx}
""".strip()

    def run_ollama(use_gpu: bool):
        opts: Dict[str, Any] = {"temperature": 0.4}
        if not use_gpu:
            opts["num_gpu"] = 0
        return ollama.generate(model=model, prompt=prompt, options=opts)

    try:
        r = run_ollama(use_gpu=True)
        out_text = (r.get("response") or "").strip()
    except Exception:
        try:
            r = run_ollama(use_gpu=False)
            out_text = (r.get("response") or "").strip()
        except Exception as e2:
            raise HTTPException(500, f"Falha ao gerar sugestões (GPU e CPU): {e2}")

    data = _extract_json_object(out_text)
    qs = data.get("questions", [])
    if not isinstance(qs, list):
        qs = []

    return {"questions": _dedupe_questions(qs, n)}


# ---------- PERGUNTAS ----------
@app.get("/lessons/{lesson_id}/questions")
def list_questions(lesson_id: str, onlyActive: bool = True, authorization: str = Header(default="")):
    _ = _get_current_user(authorization)
    with Session(engine) as s:
        stmt = select(Question).where(Question.lessonId == lesson_id)
        if onlyActive:
            stmt = stmt.where(Question.active == True)
        stmt = stmt.order_by(Question.createdAt.asc())
        return s.exec(stmt).all()


@app.post("/lessons/{lesson_id}/questions")
def add_question(lesson_id: str, payload: AddQuestionIn, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Texto da pergunta vazio.")

    with Session(engine) as s:
        lesson = s.get(Lesson, lesson_id)
        if not lesson:
            raise HTTPException(404, "Aula não encontrada.")

        q = Question(
            id=f"q{int(datetime.utcnow().timestamp() * 1000)}",
            lessonId=lesson_id,
            text=text,
            active=bool(payload.active),
        )
        s.add(q)
        s.commit()
        s.refresh(q)
        return q


@app.delete("/questions/{question_id}")
def delete_question(question_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    with Session(engine) as s:
        q = s.get(Question, question_id)
        if not q:
            raise HTTPException(404, "Pergunta não encontrada.")
        s.delete(q)
        s.commit()
        return {"ok": True}


# ---------- RESPOSTAS (ALUNO) ----------
@app.post("/responses", response_model=SubmitResponseOut)
def submit_response(payload: SubmitResponseIn, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "student")

    # anti-spoofing: identidade vem do utilizador autenticado
    student_id = user.username
    student_name = user.name

    with Session(engine) as s:
        lesson = s.get(Lesson, payload.lessonId)
        if not lesson:
            raise HTTPException(404, "Aula inválida")

        q = s.get(Question, payload.questionId)
        if not q or q.lessonId != payload.lessonId:
            raise HTTPException(404, "Pergunta inválida para esta aula")

        score_pct: int = 0
        score0_10_int: int = 0
        rationale: str = ""
        mistakes_list: List[str] = []
        tips_list: List[str] = []

        try:
            agent = classbot.lesson(lesson.id)
            raw = agent.grade(
                question=q.text,
                context=lesson.context or "",
                rubric=GENERIC_RUBRIC,
                student_answer=payload.answerText,
            )
            result = _to_dict(raw)

            score0_10 = result.get("score", 0) or 0
            try:
                score0_10_int = int(round(float(score0_10)))
            except Exception:
                score0_10_int = 0
            score0_10_int = max(0, min(10, score0_10_int))

            score_pct = max(0, min(100, int(round(score0_10_int * 10))))
            rationale = (result.get("rationale", "") or "").strip()

            mistakes_list = result.get("mistakes") or []
            tips_list = result.get("tips") or []
            if not isinstance(mistakes_list, list):
                mistakes_list = []
            if not isinstance(tips_list, list):
                tips_list = []

            mistakes_list = [str(x).strip() for x in mistakes_list if str(x).strip()]
            tips_list = [str(x).strip() for x in tips_list if str(x).strip()]
        except Exception:
            logger.exception("Erro ao chamar LessonAgent.grade (fallback placeholder_score)")
            score_pct = placeholder_score(payload.answerText)
            score0_10_int = max(0, min(10, int(round(score_pct / 10))))
            rationale = ""
            mistakes_list = []
            tips_list = []

        r = Response(
            id=f"r{int(datetime.utcnow().timestamp() * 1000)}",
            lessonId=payload.lessonId,
            questionId=payload.questionId,
            studentId=student_id,
            studentName=student_name,
            text=payload.answerText.strip(),
            score=score_pct,
            rationale=rationale,
            mistakes=json.dumps(mistakes_list, ensure_ascii=False),
            tips=json.dumps(tips_list, ensure_ascii=False),
        )

        s.add(r)
        s.commit()
        s.refresh(r)

        return SubmitResponseOut(
            id=r.id,
            lessonId=r.lessonId,
            questionId=r.questionId,
            studentId=r.studentId,
            studentName=r.studentName,
            text=r.text,
            createdAt=r.createdAt,
            feedback=GradeFeedbackOut(
                score0_10=score0_10_int,
                scorePct=score_pct,
                rationale=rationale,
                mistakes=mistakes_list,
                tips=tips_list,
            ),
        )


# ---------- CHAT ----------
@app.post("/chat", response_model=ChatOut)
def chat(payload: ChatIn, authorization: str = Header(default="")):
    _ = _get_current_user(authorization)

    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(400, "Mensagem vazia.")

    with Session(engine) as s:
        lesson = s.get(Lesson, payload.lessonId)
        if not lesson:
            raise HTTPException(404, "Aula não encontrada.")

    try:
        agent = classbot.lesson(lesson.id)
        raw = agent.chat(
            message=msg,
            context=lesson.context or "",
            model=(payload.model or "llama3.1:8b"),
            temperature=float(payload.temperature) if payload.temperature is not None else 0.2,
        )
        result = _to_dict(raw)

        return {
            "refused": bool(result.get("refused", False)),
            "answer": result.get("answer", ""),
            "citations": result.get("citations", []) or [],
            "evidence": result.get("evidence", []) or [],
        }
    except Exception:
        logger.exception("Erro ao chamar LessonAgent.chat")
        return {"refused": False, "answer": "Ocorreu um erro ao processar a mensagem.", "citations": [], "evidence": []}


# ---------- DOCENTE: RESPOSTAS ----------
@app.get("/answers")
def list_answers(
    lessonId: Optional[str] = None,
    questionId: Optional[str] = None,
    studentId: Optional[str] = None,
    minScore: Optional[int] = None,
    maxScore: Optional[int] = None,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    limit: int = 300,
    authorization: str = Header(default=""),
):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    limit = max(10, min(2000, int(limit or 300)))
    d_from = _parse_date_yyyy_mm_dd(fromDate)
    d_to = _parse_date_yyyy_mm_dd(toDate)

    with Session(engine) as s:
        stmt = select(Response)

        if lessonId:
            stmt = stmt.where(Response.lessonId == lessonId)
        if questionId:
            stmt = stmt.where(Response.questionId == questionId)
        if studentId:
            stmt = stmt.where(Response.studentId == studentId)

        if minScore is not None:
            stmt = stmt.where(Response.score >= int(minScore))
        if maxScore is not None:
            stmt = stmt.where(Response.score <= int(maxScore))

        if d_from is not None:
            stmt = stmt.where(Response.createdAt >= datetime.combine(d_from, datetime.min.time()))
        if d_to is not None:
            stmt = stmt.where(Response.createdAt <= datetime.combine(d_to, datetime.max.time()))

        stmt = stmt.order_by(Response.createdAt.desc()).limit(limit)
        return s.exec(stmt).all()


# ---------- ESTATÍSTICAS DO ALUNO ----------
@app.get("/students/{student_id}/stats")
def student_stats(student_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    if user.role == "student" and user.username != student_id:
        raise HTTPException(403, "Sem permissões.")

    with Session(engine) as s:
        rows = s.exec(select(Response).where(Response.studentId == student_id)).all()

    by_lesson: Dict[str, Dict[str, Any]] = {}
    for a in rows:
        g = by_lesson.setdefault(a.lessonId, {"history": [], "avg": 0})
        g["history"].append({"date": a.createdAt.date().isoformat(), "score": a.score or 0})

    for v in by_lesson.values():
        scores = [h["score"] for h in v["history"]]
        v["avg"] = round(sum(scores) / len(scores)) if scores else 0

    overall = round(sum(v["avg"] for v in by_lesson.values()) / (len(by_lesson) or 1)) if by_lesson else 0

    return {"overallAvg": overall, "perLesson": [{"lessonId": k, **v} for k, v in by_lesson.items()]}


# ---------- RELATÓRIOS (teacher-only) ----------
@app.get("/reports/lesson/{lesson_id}/summary")
def lesson_summary(
    lesson_id: str,
    questionId: Optional[str] = None,
    studentId: Optional[str] = None,
    minScore: Optional[int] = None,
    maxScore: Optional[int] = None,
    threshold: int = 50,
    authorization: str = Header(default=""),
):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    threshold = max(0, min(100, int(threshold or 50)))

    with Session(engine) as s:
        stmt = select(Response).where(Response.lessonId == lesson_id)

        if questionId:
            stmt = stmt.where(Response.questionId == questionId)
        if studentId:
            stmt = stmt.where(Response.studentId == studentId)
        if minScore is not None:
            stmt = stmt.where(Response.score >= int(minScore))
        if maxScore is not None:
            stmt = stmt.where(Response.score <= int(maxScore))

        rows = s.exec(stmt).all()

    scores = [int(r.score or 0) for r in rows]
    total = len(scores)
    scores_sorted = sorted(scores)
    total_students = len({r.studentId for r in rows if r.studentId})

    avg = round(sum(scores) / total) if total else 0
    med = _percentile(scores_sorted, 0.50)
    p25 = _percentile(scores_sorted, 0.25)
    p75 = _percentile(scores_sorted, 0.75)

    below = sum(1 for s0 in scores if s0 < threshold)
    pct_below = round((below / total) * 100) if total else 0

    bins = [0] * 10
    for s0 in scores:
        b = min(9, max(0, int(s0) // 10))
        bins[b] += 1

    by_q: Dict[str, List[int]] = {}
    for r in rows:
        by_q.setdefault(r.questionId, []).append(int(r.score or 0))

    with Session(engine) as s:
        q_text: Dict[str, str] = {}
        qids = list(by_q.keys())
        if qids:
            qs = s.exec(select(Question).where(Question.id.in_(qids))).all()
            q_text = {q.id: q.text for q in qs}

    hardest = []
    for qid, vals in by_q.items():
        hardest.append(
            {
                "questionId": qid,
                "questionText": (q_text.get(qid) or "")[:120],
                "count": len(vals),
                "avg": round(sum(vals) / len(vals)) if vals else 0,
            }
        )
    hardest.sort(key=lambda x: (x["avg"], -x["count"]))

    return {
        "totalResponses": total,
        "totalStudents": total_students,
        "avg": avg,
        "median": med,
        "p25": p25,
        "p75": p75,
        "pctBelowThreshold": pct_below,
        "threshold": threshold,
        "histBins": bins,
        "hardestQuestions": hardest[:8],
    }


@app.get("/reports/lesson/{lesson_id}/errors-top")
def lesson_errors_top(
    lesson_id: str,
    questionId: Optional[str] = None,
    studentId: Optional[str] = None,
    minScore: Optional[int] = None,
    maxScore: Optional[int] = None,
    top: int = 12,
    authorization: str = Header(default=""),
):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    top = max(3, min(50, int(top or 12)))

    with Session(engine) as s:
        stmt = select(Response).where(Response.lessonId == lesson_id)

        if questionId:
            stmt = stmt.where(Response.questionId == questionId)
        if studentId:
            stmt = stmt.where(Response.studentId == studentId)
        if minScore is not None:
            stmt = stmt.where(Response.score >= int(minScore))
        if maxScore is not None:
            stmt = stmt.where(Response.score <= int(maxScore))

        rows = s.exec(stmt).all()

    counts: Dict[str, int] = {}
    for r in rows:
        errs = _safe_json_list(r.mistakes)
        unique = set(_norm_err(e) for e in errs if _norm_err(e))
        for e in unique:
            counts[e] = counts.get(e, 0) + 1

    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [{"pattern": k, "count": v} for k, v in items]


@app.get("/reports/lesson/{lesson_id}/students")
def lesson_students(lesson_id: str, threshold: int = 50, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    threshold = max(0, min(100, int(threshold or 50)))

    with Session(engine) as s:
        rows = s.exec(select(Response).where(Response.lessonId == lesson_id)).all()

    by_student: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r.studentId, r.studentName)
        g = by_student.setdefault(key, {"count": 0, "scores": []})
        g["count"] += 1
        g["scores"].append(int(r.score or 0))

    out = []
    for (sid, sname), g in by_student.items():
        scores = g["scores"]
        avg = round(sum(scores) / len(scores)) if scores else 0
        pct_below = round(sum(1 for x in scores if x < threshold) / len(scores) * 100) if scores else 0
        out.append({"studentId": sid, "studentName": sname, "count": g["count"], "avg": avg, "pctBelow": pct_below})

    out.sort(key=lambda x: (-x["count"], x["avg"]))
    return out


@app.get("/reports/lesson/{lesson_id}/errors")
def lesson_errors(lesson_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    def classify(t: str) -> str:
        tl = (t or "").lower()
        if "exemplo" not in tl:
            return "Falta de exemplos"
        if not any(k in tl for k in ["supervisionad", "não supervisionad", "nao supervisionad"]):
            return "Não aborda termos-chave"
        return "Resposta superficial"

    with Session(engine) as s:
        ans = s.exec(select(Response).where(Response.lessonId == lesson_id)).all()
        buckets: Dict[str, int] = {}
        for a in ans:
            label = classify(a.text)
            buckets[label] = buckets.get(label, 0) + 1
        return [{"pattern": k, "count": buckets[k]} for k in sorted(buckets, key=buckets.get, reverse=True)]


@app.get("/reports/lesson/{lesson_id}/evolution")
def lesson_evolution(lesson_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    with Session(engine) as s:
        ans = s.exec(select(Response).where(Response.lessonId == lesson_id)).all()
        by_date: Dict[str, List[int]] = {}
        for a in ans:
            d = a.createdAt.date().isoformat()
            by_date.setdefault(d, []).append(a.score or 0)
        return [{"date": d, "avg": round(sum(v) / len(v))} for d, v in sorted(by_date.items())]


# ---------- CORPUS ----------
@app.get("/lessons/{lesson_id}/corpus/stats")
def get_lesson_corpus_stats(lesson_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")
    return corpus_stats(lesson_id)


# ---------- PDF: RELATÓRIO DA AULA ----------
@app.get("/reports/lesson/{lesson_id}/pdf")
def export_lesson_report_pdf(lesson_id: str, authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    _require_role(user, "teacher")

    with Session(engine) as s:
        lesson = s.get(Lesson, lesson_id)
        if not lesson:
            raise HTTPException(404, "Aula não encontrada.")

        questions = s.exec(
            select(Question).where(Question.lessonId == lesson_id).order_by(Question.createdAt.asc())
        ).all()

        responses = s.exec(select(Response).where(Response.lessonId == lesson_id)).all()

    scores = [int(r.score or 0) for r in responses]
    total_responses = len(responses)
    unique_students = len({r.studentId for r in responses if r.studentId})
    avg = round(sum(scores) / total_responses) if total_responses else 0

    scores_by_q: Dict[str, List[int]] = defaultdict(list)
    for r in responses:
        if r.questionId:
            scores_by_q[r.questionId].append(int(r.score or 0))

    q_avgs: List[float] = []
    q_labels: List[str] = []
    for idx, q in enumerate(questions, start=1):
        arr = scores_by_q.get(q.id, [])
        q_labels.append(f"P{idx}")
        q_avgs.append((sum(arr) / len(arr)) if arr else 0.0)

    mistake_counter = Counter()
    example_by_mistake: Dict[str, str] = {}
    for r in responses:
        ms = _safe_json_list(r.mistakes)
        for m in ms:
            key = (m or "").strip()
            if not key:
                continue
            mistake_counter[key] += 1
            if key not in example_by_mistake:
                txt = (r.text or "").strip().replace("\n", " ")
                example_by_mistake[key] = (txt[:160] + "…") if len(txt) > 160 else txt

    top_mistakes = mistake_counter.most_common(8)

    hist_png = _plot_histogram(scores, f"Distribuição de notas — {lesson.name}")
    line_png = _plot_line_avg_per_question(q_labels, q_avgs, f"Média por pergunta — {lesson.name}")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    margin = 2 * cm

    def header():
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, H - margin, f"Relatório da aula — {lesson.name}")
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.grey)
        c.drawString(margin, H - margin - 14, f"Gerado em {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        c.setFillColor(colors.black)

    header()
    y = H - margin - 40

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Resumo")
    y -= 16

    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Aula ID: {lesson.id}")
    y -= 14
    c.drawString(margin, y, f"Respostas: {total_responses} | Alunos ativos: {unique_students} | Média: {avg}%")
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Distribuição de notas")
    y -= 10

    img1 = ImageReader(io.BytesIO(hist_png))
    img_w = W - 2 * margin
    img_h = 7.5 * cm
    c.drawImage(img1, margin, y - img_h, width=img_w, height=img_h, preserveAspectRatio=True, anchor="sw")
    y -= (img_h + 18)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Média por pergunta")
    y -= 10

    img2 = ImageReader(io.BytesIO(line_png))
    img_h2 = 7.5 * cm
    if y - img_h2 < 2 * cm:
        c.showPage()
        header()
        y = H - margin - 40

    c.drawImage(img2, margin, y - img_h2, width=img_w, height=img_h2, preserveAspectRatio=True, anchor="sw")
    y -= (img_h2 + 18)

    if y < 5 * cm:
        c.showPage()
        header()
        y = H - margin - 40

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Maiores dúvidas (agregação de mistakes)")
    y -= 14

    c.setFont("Helvetica", 10)
    if not top_mistakes:
        c.drawString(margin, y, "Sem dados suficientes para inferir dúvidas.")
        y -= 14
    else:
        max_width = W - 2 * margin
        for i, (m, cnt) in enumerate(top_mistakes, start=1):
            if y < 3 * cm:
                c.showPage()
                header()
                y = H - margin - 40
                c.setFont("Helvetica", 10)

            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, f"{i}. ({cnt}x) {m}")
            y -= 12

            ex = example_by_mistake.get(m, "")
            if ex:
                c.setFont("Helvetica", 10)
                y = _draw_wrapped_text(c, f"Ex.: {ex}", margin + 10, y, max_width - 10, 12)
                y -= 6

    c.showPage()
    c.save()

    buf.seek(0)
    filename = f"relatorio_{lesson_id}.pdf"
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- LOGIN UI (ficheiro separado) ----------
@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    try:
        html = LOGIN_HTML_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Ficheiro login.html não encontrado")
    return HTMLResponse(html)


# ---------- ADMIN UI ----------
@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin():
    # opcional: podes exigir token também no admin.html via JS (recomendado)
    try:
        html = ADMIN_HTML_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Ficheiro admin.html não encontrado")
    return HTMLResponse(html)


# ---------- ROOT ----------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login")