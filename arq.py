import os

GRAPHVIZ_BIN = r"C:\Program Files\Graphviz\bin"
os.environ["PATH"] = GRAPHVIZ_BIN + os.pathsep + os.environ.get("PATH", "")

from graphviz import Digraph

def make_diagram(out_name: str = "architecture_diagram"):
    dot = Digraph("ArquiteturaGeral", format="png")
    dot.attr(rankdir="TB", fontsize="14", labelloc="t", label="Arquitetura geral (visão em camadas)")
    dot.attr("node", shape="box", style="rounded", fontname="Helvetica", fontsize="11")

    # =========================
    # Layer 1 — Frontends
    # =========================
    with dot.subgraph(name="cluster_frontends") as c:
        c.attr(label="1) Frontends", color="gray50", style="rounded")
        c.attr("node", style="rounded,filled", fillcolor="#EEF2FF")  # light indigo

        docente = "Docente UI\n(Admin • React/HTML)\n- cria/ativa aulas\n- carrega contexto (.txt)\n- gera/edita perguntas\n- vê respostas/relatórios"
        aluno = "Aluno UI\n(React)\n- vê aula ativa\n- responde perguntas\n- feedback (score/erros/dicas)\n- chat/tutor"

        c.node("doc_ui", docente)
        c.node("stu_ui", aluno)

    # =========================
    # Layer 2 — Backend (FastAPI)
    # =========================
    with dot.subgraph(name="cluster_backend") as c:
        c.attr(label="2) Backend API (FastAPI)", color="gray50", style="rounded")
        c.attr("node", style="rounded,filled", fillcolor="#ECFDF5")  # light green

        c.node("ls", "Lesson Service\n- CRUD aulas\n- ativar aula\n- associar contexto")
        c.node("qs", "Question Service\n- CRUD perguntas por aula")
        c.node("as", "Assessment Service\n- corrigir (LLM + rubric + RAG)\n- guardar score/rationale/mistakes/tips")
        c.node("ag", "Agent Service (Tutor)\n- invocar agente da aula\n- RAG + memória do aluno\n- decidir ação\n- gerar resposta tutor")
        c.node("ars", "Analytics/Reports Service\n- erros frequentes\n- evolução\n- por aluno/turma")

        # Relações internas (lógicas)
        c.edge("ls", "qs", label="aulas → perguntas")
        c.edge("qs", "as", label="perguntas → correção")
        c.edge("as", "ars", label="resultados → relatórios")

    # =========================
    # Layer 3 — Storage
    # =========================
    with dot.subgraph(name="cluster_storage") as c:
        c.attr(label="3) Armazenamento (Persistência)", color="gray50", style="rounded")

        c.attr("node", style="rounded,filled", fillcolor="#FFFBEB")  # light amber
        c.node("db", "DB Relacional\n(SQLite agora → Postgres depois)\n\nTabelas:\n- Lesson\n- Question\n- Response\n- LessonAgentConfig\n- StudentLessonMemory\n- ChatMessage (opcional)")

        c.attr("node", style="rounded,filled", fillcolor="#FDF2F8")  # light pink
        c.node("vs", "Vector Store (RAG)\nnamespace = lesson_id\n- embeddings + chunks do contexto (.txt)\n- conhecimento por aula")

    # =========================
    # Cross-layer edges
    # =========================
    # UIs -> Backend
    dot.edge("doc_ui", "ls", label="REST/JSON")
    dot.edge("doc_ui", "qs", label="REST/JSON")
    dot.edge("doc_ui", "ars", label="REST/JSON")

    dot.edge("stu_ui", "ls", label="REST/JSON")
    dot.edge("stu_ui", "qs", label="REST/JSON")
    dot.edge("stu_ui", "as", label="REST/JSON")
    dot.edge("stu_ui", "ag", label="REST/JSON")
    dot.edge("stu_ui", "ars", label="REST/JSON")

    # Backend -> DB
    dot.edge("ls", "db", label="CRUD/SQL")
    dot.edge("qs", "db", label="CRUD/SQL")
    dot.edge("as", "db", label="INSERT Response\n(score/rationale/mistakes/tips)")
    dot.edge("ag", "db", label="GET AgentConfig + Memory\nUPSERT Memory\nChatMessage (opcional)")
    dot.edge("ars", "db", label="queries/agregações")

    # Backend -> Vector Store
    dot.edge("ls", "vs", label="add_to_corpus(context)\nuc=lesson_id")
    dot.edge("as", "vs", label="retrieval\nnamespace=lesson_id")
    dot.edge("ag", "vs", label="retrieval\nnamespace=lesson_id")

    # Render PNG
    dot.render(out_name, cleanup=True)
    print(f"Gerado: {out_name}.png")

    # Render SVG também (útil para docs)
    dot_svg = Digraph("ArquiteturaGeralSVG", format="svg")
    dot_svg.source = dot.source
    dot_svg.render(out_name, cleanup=True)
    print(f"Gerado: {out_name}.svg")

if __name__ == "__main__":
    make_diagram()
