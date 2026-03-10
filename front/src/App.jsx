import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";
import { api } from "./api";
import "./styles.css";

const BACKEND_LOGIN_URL =
  import.meta.env.VITE_BACKEND_LOGIN_URL || "http://127.0.0.1:8000/login";

function getSessionFromStorage() {
  return {
    token: localStorage.getItem("token") || "",
    role: localStorage.getItem("role") || "",
    username: localStorage.getItem("username") || "",
    name: localStorage.getItem("name") || "",
  };
}

function clearSessionAndGoLogin() {
  localStorage.removeItem("token");
  localStorage.removeItem("role");
  localStorage.removeItem("username");
  localStorage.removeItem("name");
  window.location.href = BACKEND_LOGIN_URL;
}

export default function App() {
  const [booting, setBooting] = useState(true);
  const [student, setStudent] = useState(null); // { id, name }

  useEffect(() => {
    async function boot() {
      const sess = getSessionFromStorage();

      if (!sess.token) {
        clearSessionAndGoLogin();
        return;
      }

      try {
        // Fonte de verdade: backend
        const me = await api.me();
        console.log("[auth/me]", me);

        // atualiza storage (caso esteja desatualizado)
        if (me?.username) localStorage.setItem("username", me.username);
        if (me?.name) localStorage.setItem("name", me.name);
        if (me?.role) localStorage.setItem("role", me.role);

        // se for teacher (ou outra role), não deve estar no front do aluno
        if (me?.role && me.role !== "student") {
          window.location.replace("/admin");
          return;
        }

        // Evita perpetuar nome velho via localStorage:
        // só usamos fallback para username, não para name.
        const finalUsername = me?.username || sess.username || "";
        const finalName = me?.name || finalUsername || "Aluno";

        setStudent({ id: finalUsername, name: finalName });
      } catch (e) {
        console.error("[boot] /auth/me falhou", e);
        clearSessionAndGoLogin();
        return;
      } finally {
        setBooting(false);
      }
    }

    boot();
  }, []);

  if (booting) {
    return (
      <div className="app" style={{ padding: 24 }}>
        <div className="card">
          <div className="section-title">A carregar sessão…</div>
          <div className="muted">A validar token e a obter dados do utilizador.</div>
        </div>
      </div>
    );
  }

  if (!student) {
    // já deve ter redirecionado, mas fica seguro
    return null;
  }

  return (
    <div className="app">
      <StudentLayout student={student} />
    </div>
  );
}

function StudentLayout({ student }) {
  const [view, setView] = useState("participar"); // participar | perfil | chat

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="dot" />
          <span>SmartEdu</span>
        </div>

        <div className="student-card">
          <div className="avatar">{initials(student.name)}</div>
          <div className="student-info">
            <div className="student-name">{student.name}</div>
            <div className="student-role">Aluno</div>
          </div>
        </div>

        <nav className="menu">
          <button
            className={`menu-item ${view === "participar" ? "active" : ""}`}
            onClick={() => setView("participar")}
          >
            Participar
          </button>
          <button
            className={`menu-item ${view === "perfil" ? "active" : ""}`}
            onClick={() => setView("perfil")}
          >
            Perfil
          </button>
          <button
            className={`menu-item ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
        </nav>

        <div className="sidebar-footer">v0.2 • aluno</div>
      </aside>

      <main className="main">
        {view === "participar" && <Participar student={student} />}
        {view === "perfil" && <Perfil student={student} />}
        {view === "chat" && <Chat student={student} />}
      </main>
    </div>
  );
}

function Participar({ student }) {
  const [lesson, setLesson] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [questionId, setQuestionId] = useState("");
  const [answer, setAnswer] = useState("");
  const [last, setLast] = useState(null);
  const [loading, setLoading] = useState(false);

  async function refreshActiveLesson() {
    const active = await api.activeLesson();
    setLesson(active);

    if (!active) {
      setQuestions([]);
      setQuestionId("");
      return;
    }

    const qs = await api.lessonQuestions(active.id, { onlyActive: true });
    setQuestions(qs);

    setQuestionId((prev) => {
      if (!prev && qs.length) return qs[0].id;
      if (prev && !qs.some((q) => q.id === prev)) return qs[0]?.id || "";
      return prev;
    });
  }

  useEffect(() => {
    refreshActiveLesson().catch((e) => {
      console.error(e);
      alert("Erro ao carregar a aula ativa.");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentQuestion = useMemo(
    () => questions.find((q) => q.id === questionId) || null,
    [questions, questionId]
  );

  function computeScorePct(x) {
    const fb = x?.feedback;
    const candidates = [
      x?.score,
      x?.scorePct,
      fb?.scorePct,
      fb?.score != null ? Math.round(Number(fb.score) * 10) : null,
    ];
    for (const c of candidates) {
      const n = typeof c === "number" ? c : Number(c);
      if (Number.isFinite(n)) return Math.max(0, Math.min(100, Math.round(n)));
    }
    return 0;
  }

  function computeRationaleText(x) {
    let r = x?.rationale ?? x?.feedback?.rationale ?? "";
    if (typeof r !== "string") r = String(r ?? "");
    r = r.trim();

    if (r.startsWith("{") && r.includes('"score"')) {
      try {
        const parsed = JSON.parse(r);
        const rr = parsed?.rationale;
        if (typeof rr === "string" && rr.trim()) return rr.trim();
      } catch {}
    }
    return r;
  }

  async function submit() {
    if (!lesson) return alert("Não há nenhuma aula ativa neste momento.");
    if (!currentQuestion) return alert("Seleciona uma pergunta.");
    if (!answer.trim()) return alert("Escreve a tua resposta.");

    try {
      setLoading(true);

      const res = await api.submitResponse({
        lessonId: lesson.id,
        questionId: currentQuestion.id,

        // estes dois o backend ignora, mas mantemos
        studentId: student.id,
        studentName: student.name,

        answerText: answer,
      });

      setLast(res);
      setAnswer("");
    } catch (err) {
      console.error(err);
      alert(err?.message || "Ocorreu um erro ao avaliar a resposta.");
    } finally {
      setLoading(false);
    }
  }

  const scorePct = useMemo(() => computeScorePct(last), [last]);
  const rationaleText = useMemo(() => computeRationaleText(last), [last]);

  return (
    <div className="page">
      <header className="page-header">
        <h1>Participar</h1>
        <div className="header-right">
          {lesson ? `Aula ativa: ${lesson.name}` : "Sem aula ativa"}
        </div>
      </header>

      <div className="grid two">
        <section className="card">
          <div className="section-title">Aula ativa</div>

          {lesson ? (
            <>
              <div className="row between">
                <div className="muted">
                  {questions.length
                    ? `${questions.length} pergunta(s) disponíveis`
                    : "Sem perguntas ativas nesta aula."}
                </div>
                <button
                  className="button secondary"
                  onClick={() => refreshActiveLesson()}
                  disabled={loading}
                >
                  Atualizar
                </button>
              </div>

              {questions.length ? (
                <div
                  style={{
                    marginTop: 10,
                    display: "flex",
                    gap: 8,
                    flexWrap: "wrap",
                  }}
                >
                  {questions.map((q, idx) => (
                    <button
                      key={q.id}
                      className={`button secondary ${
                        q.id === questionId ? "active-q" : ""
                      }`}
                      onClick={() => setQuestionId(q.id)}
                      disabled={loading}
                      style={{
                        padding: "8px 10px",
                        borderRadius: 999,
                        border: "1px solid #dfe4f0",
                        background: q.id === questionId ? "#e7eeff" : "#eef1f9",
                        color: q.id === questionId ? "#1f46b2" : "#1f2b4a",
                        boxShadow: "none",
                      }}
                      title={q.text}
                    >
                      Pergunta {idx + 1}
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <div className="muted">O professor ainda não ativou nenhuma aula.</div>
          )}
        </section>

        <section className="card">
          <div className="section-title">Pergunta selecionada</div>
          <div className="question-box">
            {currentQuestion ? (
              currentQuestion.text
            ) : (
              <span className="muted">
                {lesson ? "Seleciona uma pergunta." : "Sem aula ativa."}
              </span>
            )}
          </div>
        </section>

        {lesson && currentQuestion && (
          <section className="card">
            <div className="section-title">A tua resposta</div>
            <textarea
              className="textarea"
              rows={10}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="Escreve aqui a tua resposta…"
              disabled={loading}
            />
            <div className="row between">
              <div className="muted">
                {loading
                  ? "A tua resposta está a ser avaliada…"
                  : "Dica: inclui conceitos-chave e pelo menos um exemplo."}
              </div>
              <button className="button" onClick={submit} disabled={loading}>
                {loading ? "A avaliar…" : "Submeter"}
              </button>
            </div>
          </section>
        )}

        {last && !loading && (
          <section className="card highlight">
            <div className="row between">
              <div>
                <div className="small">Classificação</div>
                <div className="score">{scorePct}%</div>
              </div>
              <div className="muted small">
                Feedback gerado automaticamente com LLM.
              </div>
            </div>

            {rationaleText ? (
              <>
                <div className="section-title" style={{ marginTop: "8px" }}>
                  Resumo do LLM
                </div>
                <p className="muted">{rationaleText}</p>
              </>
            ) : null}
          </section>
        )}
      </div>
    </div>
  );
}

function Chat({ student }) {
  const [lessons, setLessons] = useState([]);
  const [lessonId, setLessonId] = useState("");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    async function loadLessons() {
      try {
        const all = await api.lessons();
        setLessons(all);
        if (all.length) {
          const active = all.find((l) => l.active);
          setLessonId(active ? active.id : all[0].id);
        }
      } catch (e) {
        console.error(e);
        setLessons([]);
      }
    }
    loadLessons();
  }, []);

  const currentLesson = useMemo(
    () => lessons.find((l) => l.id === lessonId) || null,
    [lessons, lessonId]
  );

  async function send() {
    if (!lessonId) return alert("Seleciona uma aula primeiro.");
    if (!input.trim()) return;

    const userMsg = input.trim();

    setMessages((old) => [...old, { from: "user", text: userMsg, at: Date.now() }]);
    setInput("");
    setLoading(true);

    try {
      const res = await api.chat({
        lessonId,
        studentId: student.id,
        studentName: student.name,
        message: userMsg,
      });

      const answerText =
        (res && res.answer && res.answer.trim()) ||
        "O tutor não conseguiu gerar uma resposta.";

      setMessages((old) => [
        ...old,
        {
          from: "tutor",
          text: answerText,
          citations: res?.citations || [],
          evidence: res?.evidence || [],
          at: Date.now(),
        },
      ]);
    } catch (err) {
      console.error(err);
      setMessages((old) => [
        ...old,
        {
          from: "tutor",
          text:
            "Ocorreu um erro ao obter resposta do tutor. Tenta novamente dentro de alguns segundos.",
          at: Date.now(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Chat</h1>
        <div className="header-right">
          {currentLesson ? `Aula selecionada: ${currentLesson.name}` : "Seleciona uma aula"}
        </div>
      </header>

      <section className="card">
        <div className="row between" style={{ gap: 12, flexWrap: "wrap" }}>
          <div style={{ minWidth: 260, flex: 1 }}>
            <div className="section-title">Aula</div>
            <select
              className="select"
              value={lessonId}
              onChange={(e) => setLessonId(e.target.value)}
            >
              {lessons.length === 0 && <option value="">Sem aulas disponíveis</option>}
              {lessons.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} {l.active ? "• (ativa)" : ""}
                </option>
              ))}
            </select>
            <div className="small" style={{ marginTop: 6 }}>
              O tutor responde com base no material desta aula, mesmo que não esteja ativa.
              Não resolve exercícios por ti, só explica e dá pistas.
            </div>
          </div>
        </div>

        <div
          style={{
            marginTop: 14,
            borderRadius: 12,
            border: "1px solid #e8ebf3",
            padding: 10,
            maxHeight: 340,
            overflowY: "auto",
            background: "#f7f9ff",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {messages.length === 0 ? (
            <div className="muted">
              Começa por escrever uma pergunta sobre a matéria da aula selecionada.
            </div>
          ) : (
            messages.map((m, idx) => (
              <div
                key={m.at ?? idx}
                style={{
                  alignSelf: m.from === "user" ? "flex-end" : "flex-start",
                  maxWidth: "80%",
                }}
              >
                <div
                  className="small"
                  style={{
                    marginBottom: 2,
                    textAlign: m.from === "user" ? "right" : "left",
                  }}
                >
                  {m.from === "user" ? student.name : "Tutor IA"}
                </div>
                <div
                  style={{
                    background: m.from === "user" ? "#2b66f6" : "#ffffff",
                    color: m.from === "user" ? "#fff" : "#1c2231",
                    borderRadius: 12,
                    padding: "8px 10px",
                    border: m.from === "user" ? "none" : "1px solid #e3e8f5",
                    whiteSpace: "pre-wrap",
                    boxShadow:
                      m.from === "user" ? "0 3px 6px rgba(43,102,246,.25)" : "none",
                  }}
                >
                  {m.text}
                </div>
              </div>
            ))
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <div className="section-title">A tua pergunta</div>
          <textarea
            className="textarea"
            rows={3}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Pergunta algo sobre a matéria desta aula…"
            disabled={loading || !lessonId}
          />
          <div className="row between" style={{ marginTop: 6 }}>
            <div className="muted small">
              {loading
                ? "O tutor está a preparar a resposta…"
                : "O tutor não faz exercícios por ti: explica, dá exemplos e pistas."}
            </div>
            <button
              className="button"
              onClick={send}
              disabled={loading || !lessonId || !input.trim()}
            >
              {loading ? "A responder…" : "Enviar"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Perfil({ student }) {
  const [lessons, setLessons] = useState([]);
  const [stats, setStats] = useState(null);
  const [statsErr, setStatsErr] = useState("");

  useEffect(() => {
    api.lessons()
      .then(setLessons)
      .catch((e) => {
        console.error("[lessons]", e);
        setLessons([]);
      });
  }, []);

  useEffect(() => {
    setStatsErr("");
    api.studentStats(student.id)
      .then((s) => {
        console.log("[studentStats]", s);
        setStats(s);
      })
      .catch((e) => {
        console.error("[studentStats] erro", e);
        setStats(null);
        setStatsErr(e?.message || "Erro ao carregar estatísticas.");
      });
  }, [student.id]);

  const lessonName = (lessonId) =>
    lessons.find((l) => l.id === lessonId)?.name || lessonId;

  return (
    <div className="page">
      <header className="page-header">
        <h1>Perfil</h1>
        <div className="header-right">{student.name}</div>
      </header>

      <section className="card">
        <div className="kpi">
          <div className="small">Média Global</div>
          <div className="kpi-value">{stats?.overallAvg ?? 0}%</div>
        </div>

        <div className="section-title mt">Evolução por Aula</div>

        {statsErr ? (
          <div className="muted" style={{ marginTop: 8, color: "#b00020" }}>
            {statsErr}
          </div>
        ) : null}

        <div className="evolutions">
          {stats?.perLesson?.length ? (
            stats.perLesson.map(({ lessonId, history }) => {
              if (!history?.length) return null;
              return (
                <div key={lessonId} className="evo-card">
                  <div className="small">{lessonName(lessonId)}</div>
                  <div className="chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={history}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                        <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Line type="monotone" dataKey="score" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="muted">Ainda sem dados.</div>
          )}
        </div>
      </section>
    </div>
  );
}

function initials(name = "") {
  return name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}