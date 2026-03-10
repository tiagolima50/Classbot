# grader.py
import json
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

import ollama

from classbot.config import DEFAULT_MODEL, DEFAULT_TEMPERATURE, OLLAMA_HOST
from classbot.prompts import SYSTEM_MSG, PROMPT_TMPL
from classbot.retrieval import retrieve_snippets

# ---------------- Logging ----------------
logger = logging.getLogger("classbot.grader")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

# ---------- Ollama host (opcional) ----------
if OLLAMA_HOST:
    ollama.set_host(OLLAMA_HOST)

# ---------- Modelos por tarefa ----------
GRADING_MODEL = "llama3.2:3b"  # rápido para grading (JSON curto)
CHAT_MODEL = "llama3.1:8b"     # melhor qualidade para tutor/chat

# ---------- Tuning rápido (desempenho) ----------
# Nota: para exercícios/quiz, convém mais tokens, senão o JSON corta.
GRADE_NUM_PREDICT = 220
CHAT_NUM_PREDICT = 256
QUIZ_NUM_PREDICT = 420  # ✅ importante para MCQ (evita truncamento)

# ---------- Utils ----------
def _cap_text(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    t = str(text).strip()
    if len(t) <= max_chars:
        return t
    return t[:max_chars].rstrip() + "\n\n[...conteúdo truncado para desempenho...]"


# ---------- JSON parsing ----------
def _extract_first_json(text: str) -> Optional[str]:
    """
    Extrai o primeiro objecto JSON {...} de forma robusta, ignorando markdown.
    """
    if not isinstance(text, str):
        return None
    text = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()

    start = None
    depth = 0
    in_str = False
    esc = False

    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        return text[start : i + 1]
    return None


# ---------- JSON repair ----------
JSON_REPAIR_SYSTEM = "És um validador/formatador. Converte para JSON válido estrito, sem texto extra."

def _repair_to_json(raw_text: str, *, model: str, allow_quiz: bool = False) -> Optional[dict]:
    """
    Segunda passagem (repair) para transformar output do LLM em JSON estrito.
    - allow_quiz=True: permite preservar objecto 'quiz' para MCQ.
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    keep_keys = ["score", "rationale", "mistakes", "tips", "citations", "evidence"]
    if allow_quiz:
        keep_keys.append("quiz")
    keep_keys_str = ", ".join(keep_keys)

    extra_quiz_rules = ""
    if allow_quiz:
        extra_quiz_rules = """
- Se existir "quiz", tem de ser um objecto com:
  - question (str)
  - options (dict com chaves A, B, C, D)
  - correct (A|B|C|D)
  - explanation (str)
""".rstrip()

    repair_prompt = f"""
Converte o texto abaixo para JSON válido estrito.

Regras:
- Responde APENAS com JSON (sem markdown, sem texto extra).
- Mantém apenas as chaves: {keep_keys_str}.
- score deve ser int 0-10.
- mistakes, tips, citations, evidence devem ser listas (mesmo que vazias).
{extra_quiz_rules}

TEXTO:
{raw_text}
""".strip()

    try:
        resp = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": JSON_REPAIR_SYSTEM},
                {"role": "user", "content": repair_prompt},
            ],
            format="json",
            options={
                "temperature": 0.0,
                "num_predict": 320 if allow_quiz else 220,
                "top_k": 20,
                "top_p": 0.8,
            },
        )
        fixed = resp.get("message", {}).get("content", "")

        if isinstance(fixed, dict):
            return fixed

        if isinstance(fixed, str):
            fixed = fixed.strip()
            try:
                return json.loads(fixed)
            except Exception:
                blob = _extract_first_json(fixed)
                if blob:
                    try:
                        return json.loads(blob)
                    except Exception:
                        return None
    except Exception:
        return None

    return None


# ---------- Retrieval compat ----------
def _safe_retrieve(query: str, *, uc: str, mode: str) -> Tuple[str, list]:
    """
    Compatibilidade: retrieve_snippets pode devolver:
      - apenas str (retrieval_block)
      - (retrieval_block, sources)
    """
    res = retrieve_snippets(query, uc=uc, mode=mode)
    if isinstance(res, tuple) and len(res) == 2:
        return (str(res[0] or ""), res[1] or [])
    return (str(res or ""), [])


# ---------- Ollama wrapper ----------
def call_ollama_json(
    system: str,
    prompt: str,
    *,
    model: str,
    temperature: float,
    num_predict: int = 256,
    top_k: int = 40,
    top_p: float = 0.9,
    num_gpu: Optional[int] = None,
    allow_quiz: bool = False,  # ✅ NOVO: preservar quiz + repair com quiz
) -> Dict[str, Any]:
    """
    Wrapper robusto:
    - tenta format=json
    - extrai primeiro JSON se vier texto
    - se falhar, faz repair pass
    - normaliza campos
    - preserva quiz quando allow_quiz=True
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    def _chat(use_json: bool = True):
        opts = {
            "temperature": float(temperature),
            "num_predict": int(num_predict),
            "top_k": int(top_k),
            "top_p": float(top_p),
        }
        if num_gpu is not None:
            opts["num_gpu"] = int(num_gpu)

        kwargs = {"model": model, "messages": messages, "options": opts}
        if use_json:
            kwargs["format"] = "json"
        return ollama.chat(**kwargs)

    t0 = time.time()
    try:
        resp = _chat(use_json=True)
        used_json = True
    except Exception:
        resp = _chat(use_json=False)
        used_json = False
    t_llm = round(time.time() - t0, 3)

    raw = resp.get("message", {}).get("content", "")
    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

    def _loads(txt: str):
        try:
            return json.loads(txt)
        except Exception:
            return None

    # parse inicial
    t1 = time.time()
    data = None
    if isinstance(raw, dict):
        data = raw
    if data is None:
        blob = _extract_first_json(raw_text)
        if blob:
            data = _loads(blob)
    t_parse = round(time.time() - t1, 3)

    # repair se necessário
    repaired_used = False
    if not isinstance(data, dict):
        repaired = _repair_to_json(raw_text, model=model, allow_quiz=allow_quiz)
        if isinstance(repaired, dict):
            data = repaired
            repaired_used = True
        else:
            data = {
                "score": 0,
                "rationale": raw_text.strip(),
                "mistakes": [],
                "tips": [],
                "citations": [],
                "evidence": [],
            }

    # normalização forte
    try:
        score = int(round(float(data.get("score", 0))))
    except Exception:
        score = 0
    score = max(0, min(10, score))

    rationale = str(data.get("rationale", "")).strip()

    mistakes = data.get("mistakes") or []
    tips = data.get("tips") or []
    if not isinstance(mistakes, list):
        mistakes = []
    if not isinstance(tips, list):
        tips = []
    mistakes = [str(x).strip() for x in mistakes if str(x).strip()]
    tips = [str(x).strip() for x in tips if str(x).strip()]

    citations_raw = data.get("citations") or []
    if not isinstance(citations_raw, list):
        citations_raw = []
    citations = []
    for c in citations_raw:
        try:
            citations.append(int(c))
        except Exception:
            continue

    evidence_raw = data.get("evidence") or []
    if not isinstance(evidence_raw, list):
        evidence_raw = []
    evidence = [str(e).strip() for e in evidence_raw if str(e).strip()]

    # ✅ preservar quiz
    quiz = None
    if allow_quiz:
        q = data.get("quiz")
        if isinstance(q, dict):
            quiz = q

    logger.info(
        "ollama_chat | model=%s | temp=%.2f | num_predict=%d | top_k=%d | top_p=%.2f | num_gpu=%s | prompt_chars=%d | used_json=%s | repaired=%s | allow_quiz=%s | t_llm=%.3fs | t_parse=%.3fs",
        model,
        float(temperature),
        int(num_predict),
        int(top_k),
        float(top_p),
        str(num_gpu),
        len(prompt or ""),
        str(used_json),
        str(repaired_used),
        str(allow_quiz),
        float(t_llm),
        float(t_parse),
    )

    out: Dict[str, Any] = {
        "score": score,
        "rationale": rationale,
        "mistakes": mistakes,
        "tips": tips,
        "citations": citations,
        "evidence": evidence,
        "_latency_sec": t_llm,
        "_timing": {"llm": t_llm, "parse": t_parse},
        "_raw": raw_text,
        "_repaired": repaired_used,
    }

    if quiz is not None:
        out["quiz"] = quiz

    return out


# ---------- API principal ----------
def grade_answer(
    *,
    question: str,
    context: str,
    rubric: Dict[str, Any],
    student_answer: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    uc: str = "Global",
) -> Dict[str, Any]:
    t0_total = time.time()

    query_for_retrieval = f"PERGUNTA:\n{question}\n\nRESPOSTA:\n{student_answer}"
    retrieval_block, sources = _safe_retrieve(query_for_retrieval, uc=uc, mode="grade")

    full_context = retrieval_block.strip()

    prompt = PROMPT_TMPL.format(
        question=question,
        context=full_context,
        scale=rubric["scale"],
        criteria=", ".join([c["name"] for c in rubric["criteria"]]),
        scoring=json.dumps(rubric["scoring"], ensure_ascii=False),
        student_answer=student_answer.strip(),
    ) + """

REGRAS OBRIGATÓRIAS:
- Baseia a avaliação EXCLUSIVAMENTE no 'contexto' acima e nas 'Fontes numeradas'.
- NÃO uses conhecimento externo. Se faltar informação no contexto, define score <= 3 e explica "não está no material".
- Mantém "rationale" com no máximo 3 frases.
- "mistakes": no máximo 2 itens.
- "tips": no máximo 2 itens.
- Se usares Fontes numeradas, devolve:
    "citations": [índices], "evidence": [1 excerto curto].
- Output JSON válido: { "score": int 0–10, "rationale": str, "mistakes": [str], "tips": [str], "citations": [int], "evidence": [str] }.
""".strip()

    use_model = (model or DEFAULT_MODEL).strip()
    if use_model == DEFAULT_MODEL:
        use_model = GRADING_MODEL

    result = call_ollama_json(
        SYSTEM_MSG,
        prompt,
        model=use_model,
        temperature=0.0,
        num_predict=GRADE_NUM_PREDICT,
        top_k=20,
        top_p=0.8,
        allow_quiz=False,
    )

    t_total = round(time.time() - t0_total, 3)
    logger.info(
        "grade_answer_total | uc=%s | model=%s | t_total=%.3fs | full_context_chars=%d | student_chars=%d",
        uc,
        use_model,
        t_total,
        len(full_context or ""),
        len(student_answer or ""),
    )

    result["retrieved"] = sources
    result["_timing_total_sec"] = t_total
    return result


# --- Tutor / Chat (por aula) ---
_PENDING_QUIZ: Dict[str, Dict[str, Any]] = {}

def _is_exercise_request(msg: str) -> bool:
    m = (msg or "").lower()
    triggers = [
        "exercício", "exercicio", "quiz", "pergunta de treino", "treino",
        "escolha múltipla", "escolha multipla", "multiple choice", "mcq",
        "cria um exercício", "criar um exercício", "cria um quiz",
        "cria-me um exercício", "faz um exercício", "cria um exercício sobre"
    ]
    return any(t in m for t in triggers)

def _extract_choice(msg: str) -> Optional[str]:
    m = (msg or "").strip().upper()
    m = re.sub(r"\s+", " ", m)
    if m in {"A", "B", "C", "D"}:
        return m
    for ch in ["A", "B", "C", "D"]:
        if m.startswith(f"{ch})") or m.startswith(f"{ch} ") or m.endswith(f" {ch}"):
            return ch
    m2 = re.search(r"\b([ABCD])\b", m)
    return m2.group(1) if m2 else None


TUTOR_SYSTEM = """
És um tutor académico em PT-PT.

OBJETIVO:
- Explicar conceitos de forma clara e concisa.
- Podes criar exercícios de treino.

REGRAS:
- Nunca dês a solução final de exercícios (quando o aluno pede para resolver).
- Se o aluno pedir solução direta, dá apenas pistas.
- Usa apenas o contexto fornecido da aula (RAG).
- Se não houver info suficiente no contexto, diz explicitamente que não está no material da aula.
- Mantém respostas curtas e objetivas.
- Não incluas citations nem evidence.

REGRA DE GROUNDING (OBRIGATÓRIA):
- Para qualquer afirmação factual, tens de conseguir apontar suporte no CONTEXTO.
- Devolve SEMPRE:
  - grounded: true/false
  - support: 1-2 excertos LITERAIS copiados do CONTEXTO (máx 18 palavras cada).
- Se não conseguires encontrar excertos literais no CONTEXTO que suportem a tua resposta:
  - grounded = false
  - rationale deve dizer: "Não está no material da aula."

FORMATO:
- Responde APENAS JSON válido no formato:
{
  "score": 0,
  "rationale": "resposta do tutor aqui",
  "mistakes": [],
  "tips": [],
  "grounded": true,
  "support": ["excerto literal 1", "excerto literal 2"],
  "quiz": {
    "question": "texto",
    "options": { "A": "…", "B": "…", "C": "…", "D": "…" },
    "correct": "A",
    "explanation": "1-2 frases"
  }
}

Notas:
- "quiz" é opcional (só quando estás a CRIAR um exercício).
- Se não estiveres a criar exercício, não incluas "quiz".
""".strip()

def tutor_chat(
    *,
    message: str,
    context: str,
    uc: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    t0_total = time.time()

    # 1) se há quiz pendente, avaliar sem LLM
    pending = _PENDING_QUIZ.get(uc)
    if pending:
        choice = _extract_choice(message)
        if not choice:
            return {
                "refused": False,
                "answer": "Por favor responde apenas com a letra A, B, C ou D (ex.: 'A').",
                "_latency_sec": 0.0,
                "_timing_total_sec": round(time.time() - t0_total, 3),
                "_model_used": "none",
                "_exercise": True,
            }

        correct = (pending.get("correct") or "").strip().upper()
        explanation = (pending.get("explanation") or "").strip()

        _PENDING_QUIZ.pop(uc, None)

        if choice == correct:
            ans = f"✅ Certo — a opção {choice} está correta."
            if explanation:
                ans += f"\n{explanation}"
        else:
            ans = f"❌ Incorreto — escolheste {choice}, mas a correta é {correct}."
            if explanation:
                ans += f"\n{explanation}"

        return {
            "refused": False,
            "answer": ans.strip(),
            "_latency_sec": 0.0,
            "_timing_total_sec": round(time.time() - t0_total, 3),
            "_model_used": "none",
            "_exercise": True,
        }

    # 2) retrieval normal
    retrieval_block, sources = _safe_retrieve(message, uc=uc, mode="tutor")

    lesson_hint = _cap_text(context, max_chars=220)
    full_context = f"""{lesson_hint}

{retrieval_block}
""".strip()

    wants_exercise = _is_exercise_request(message)
    msg_lower = (message or "").lower()

    # 3) decide modelo/budget
    use_model = "llama3.2:3b"
    num_predict = 160

    if wants_exercise:
        use_model = "llama3.1:8b"
        num_predict = QUIZ_NUM_PREDICT
        temperature = 0.2
    else:
        if any(x in msg_lower for x in [
            "explica detalhadamente", "passo a passo", "não percebi",
            "em detalhe", "demonstra", "prova"
        ]) or len(msg_lower) > 220:
            use_model = "llama3.1:8b"
            num_predict = 220

    # 4) prompts
    if wants_exercise:
        user_prompt = f"""
CONTEXTO DA AULA (RAG):
{full_context}

PEDIDO DO ALUNO:
{message}

TAREFA:
Cria UM exercício de escolha múltipla (MCQ) sobre o tema pedido, usando APENAS o contexto.

Regras do exercício:
- 1 pergunta.
- 4 opções (A, B, C, D).
- 1 opção correta (em "correct").
- Dá uma explicação MUITO curta (1-2 frases) em "explanation".
- NÃO escrevas a resposta correta no texto da pergunta que apareça para o aluno.
- Mantém tudo em PT-PT.

Output JSON (obrigatório): {{
  "score": 0,
  "rationale": "",
  "mistakes": [],
  "tips": [],
  "quiz": {{
    "question": "Pergunta aqui",
    "options": {{ "A": "...", "B": "...", "C": "...", "D": "..." }},
    "correct": "A",
    "explanation": "Explicação 1-2 frases"
  }}
}}
""".strip()
    else:
        user_prompt = f"""
CONTEXTO DA AULA (RAG):
{full_context}

MENSAGEM DO ALUNO:
{message}

TAREFA:
- Responde como tutor.
- Mantém a resposta curta e clara (máx. 6-8 linhas).
- Se pedirem para resolver exercício, não dês a solução final (dá apenas pistas).
- Se faltar informação no contexto, diz explicitamente que não está no material.

Output JSON com as chaves: score, rationale, mistakes, tips.
""".strip()

    # 5) call LLM (✅ allow_quiz quando exercício)
    out = call_ollama_json(
        system=TUTOR_SYSTEM,
        prompt=user_prompt,
        model=use_model,
        temperature=float(temperature),
        num_predict=int(num_predict),
        top_k=40,
        top_p=0.9,
        allow_quiz=wants_exercise,
    )

    t_total = round(time.time() - t0_total, 3)
    logger.info(
        "tutor_chat_total | uc=%s | model=%s | exercise=%s | t_total=%.3fs | full_context_chars=%d | msg_chars=%d",
        uc,
        use_model,
        str(wants_exercise),
        t_total,
        len(full_context or ""),
        len(message or ""),
    )

    # 6) construir resposta final para o frontend
    answer_text = ""
    if wants_exercise:
        quiz = out.get("quiz")
        if quiz is None:
            # fallback extra: tentar parse do raw
            raw = out.get("_raw", "")
            blob = _extract_first_json(raw)
            if blob:
                try:
                    parsed = json.loads(blob)
                    quiz = parsed.get("quiz")
                except Exception:
                    quiz = None

        if isinstance(quiz, dict):
            q_text = str(quiz.get("question", "")).strip()
            options = quiz.get("options") or {}
            if not isinstance(options, dict):
                options = {}

            A = str(options.get("A", "")).strip()
            B = str(options.get("B", "")).strip()
            C = str(options.get("C", "")).strip()
            D = str(options.get("D", "")).strip()
            correct = str(quiz.get("correct", "")).strip().upper()
            explanation = str(quiz.get("explanation", "")).strip()

            if correct in {"A", "B", "C", "D"}:
                _PENDING_QUIZ[uc] = {
                    "correct": correct,
                    "explanation": explanation,
                    "created_at": time.time(),
                }

            lines = []
            if q_text:
                lines.append(f"PERGUNTA: {q_text}")
            lines.append(f"A) {A}")
            lines.append(f"B) {B}")
            lines.append(f"C) {C}")
            lines.append(f"D) {D}")
            lines.append("")
            lines.append("Responde apenas com a letra A, B, C ou D.")
            answer_text = "\n".join(lines).strip()
        else:
            answer_text = str(out.get("rationale", "")).strip() or "O tutor não conseguiu gerar um exercício."
    else:
        answer_text = str(out.get("rationale", "")).strip()

    return {
        "refused": False,
        "answer": answer_text,
        "_latency_sec": out.get("_latency_sec"),
        "_timing_total_sec": t_total,
        "retrieved": sources,
        "_model_used": use_model,
        "_exercise": wants_exercise,
    }