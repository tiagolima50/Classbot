const BASE_URL = import.meta.env.VITE_API_URL || "";

function getToken() {
  return localStorage.getItem("token") || "";
}

async function request(path, options = {}) {
  const token = getToken();

  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await res.text();
  const data = text ? safeJson(text) : null;

  if (!res.ok) {
    let msg = `Erro HTTP ${res.status}`;
    if (data && data.detail) msg = data.detail;
    throw new Error(msg);
  }

  return data;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function parseMaybeJsonArray(v) {
  if (Array.isArray(v)) return v;

  if (typeof v === "string") {
    const s = v.trim();
    if (!s) return [];
    try {
      const parsed = JSON.parse(s);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  return [];
}

/**
 * Normaliza respostas vindas de:
 * - /answers (docente): score/rationale/mistakes/tips vêm no topo (DB)
 * - /responses (aluno): score/rationale/mistakes/tips vêm dentro de feedback
 */
function normalizeResponseRow(r) {
  if (!r || typeof r !== "object") return r;

  const fb = r.feedback && typeof r.feedback === "object" ? r.feedback : null;

  let score = null;

  if (fb?.scorePct != null) score = Number(fb.scorePct);
  else if (r.score != null) score = Number(r.score);
  else if (fb?.score != null) {
    const s10 = Number(fb.score);
    score = Number.isFinite(s10) ? Math.round(s10 * 10) : 0;
  } else {
    score = 0;
  }

  if (!Number.isFinite(score)) score = 0;
  score = Math.max(0, Math.min(100, Math.round(score)));

  const rationale =
    (fb?.rationale != null ? String(fb.rationale) : String(r.rationale ?? "")).trim();

  const mistakesRaw = fb?.mistakes != null ? fb.mistakes : r.mistakes;
  const tipsRaw = fb?.tips != null ? fb.tips : r.tips;

  return {
    ...r,
    score,
    rationale,
    mistakes: parseMaybeJsonArray(mistakesRaw),
    tips: parseMaybeJsonArray(tipsRaw),
  };
}

export const api = {
  // -------- AUTH --------
  async register({ name, username, password }) {
    return request("/auth/register", {
      method: "POST",
      body: JSON.stringify({ name, username, password }),
    });
  },

  async login(username, password) {
    const data = await request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });

    // guarda sessão
    localStorage.setItem("token", data.token);
    localStorage.setItem("role", data.role);
    localStorage.setItem("username", data.username);
    localStorage.setItem("name", data.name);

    return data;
  },

  async me() {
    return request("/auth/me");
  },

  async logout() {
    try {
      await request("/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    localStorage.removeItem("token");
    localStorage.removeItem("role");
    localStorage.removeItem("username");
    localStorage.removeItem("name");
  },

  // -------- APP --------
  async lessons() {
    return (await request("/lessons")) ?? [];
  },

  async activeLesson() {
    const data = await request("/lessons/active");
    return data?.activeLesson ?? data ?? null;
  },

  async lessonQuestions(lessonId, { onlyActive = true } = {}) {
    if (!lessonId) return [];
    const params = new URLSearchParams();
    params.set("onlyActive", String(onlyActive));
    return (
      (await request(
        `/lessons/${encodeURIComponent(lessonId)}/questions?${params.toString()}`
      )) ?? []
    );
  },

  /**
   * IMPORTANTE:
   * O backend ignora studentId/studentName enviados e usa os do utilizador autenticado.
   * Mantemos parâmetros aqui para compatibilidade com a UI atual.
   */
  async submitResponse({ lessonId, questionId, studentId, studentName, answerText }) {
    const payload = { lessonId, questionId, studentId, studentName, answerText };

    const res = await request("/responses", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    return normalizeResponseRow(res);
  },

  async answers({ lessonId, questionId, minScore } = {}) {
    const params = new URLSearchParams();
    if (lessonId) params.set("lessonId", lessonId);
    if (questionId) params.set("questionId", questionId);
    if (minScore !== undefined && minScore !== null && String(minScore).trim() !== "") {
      params.set("minScore", String(minScore));
    }

    const qs = params.toString();
    const rows = await request(`/answers${qs ? `?${qs}` : ""}`);
    return Array.isArray(rows) ? rows.map(normalizeResponseRow) : [];
  },

  async studentStats(studentId) {
    return request(`/students/${encodeURIComponent(studentId)}/stats`);
  },

  async errors(lessonId) {
    return request(`/reports/lesson/${encodeURIComponent(lessonId)}/errors`);
  },

  async evolution(lessonId) {
    return request(`/reports/lesson/${encodeURIComponent(lessonId)}/evolution`);
  },

  async chat({ lessonId, studentId, studentName, message, model, temperature } = {}) {
    return request("/chat", {
      method: "POST",
      body: JSON.stringify({ lessonId, studentId, studentName, message, model, temperature }),
    });
  },
};