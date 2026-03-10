# prompts.py

SYSTEM_MSG = (
    "És um avaliador objetivo para respostas curtas em PT. "
    "Lê a pergunta, a resposta do aluno e a rubrica. "
    "Atribui uma nota na escala 0–10 (inteiro) e dá feedback curto e acionável. "
    'Devolve apenas JSON no formato: {"score": <int 0-10>, '
    '"rationale": "1-2 frases a justificar", "mistakes": ["ponto fraco 1"], '
    '"tips": ["dica concreta 1"]}'
)

# ——— Pergunta 1 (técnica): RAG ———
QUESTION_RAG = "Explica, em 2–3 frases, como o RAG melhora a precisão das respostas de um chatbot."
CONTEXT_RAG = (
    "RAG = Retrieval-Augmented Generation: primeiro, um módulo de retrieval encontra "
    "trechos/documentos relevantes numa base externa; depois, o modelo de linguagem gera a resposta "
    "usando esse contexto. Benefícios: maior precisão factual, cobertura de conhecimento e possibilidade "
    "de citar fontes. Limitações: depende da qualidade do retrieval e do contexto fornecido."
)
RUBRIC_RAG = {
    "scale": "0-10",
    "criteria": [
        {
            "name": "Definição correta de RAG",
            "desc": "O aluno deve explicar que RAG combina recuperação de informação (retrieval) com geração de texto (generation) e usa documentos externos como contexto. Não avaliar com base no contexto fornecido, apenas no que o aluno escreveu.",
            "weight": 4
        },
        {
            "name": "Precisão factual / benefício",
            "desc": "O aluno deve mencionar benefícios factuais — como maior precisão, menor alucinação ou uso de fontes. Não assumir estes pontos se não forem explicitamente mencionados na resposta.",
            "weight": 4
        },
        {
            "name": "Clareza e concisão",
            "desc": "A resposta deve ser clara, objetiva e em 2–3 frases. Penalizar respostas vagas, genéricas ou demasiado longas.",
            "weight": 2
        },
    ],
    "scoring": {
        "10": "Cobre todos os critérios com explicações explícitas e corretas.",
        "7": "Cobre a maioria, mas falta detalhe ou usa termos vagos.",
        "4": "Resposta parcial, incompleta ou apenas genérica.",
        "0": "Não responde à pergunta ou está incorreta."
    }
}

# ——— Pergunta 2 (simples, conhecimento geral): poupança de energia ———
QUESTION_ENERGY = "Indica duas formas simples de poupar energia em casa e explica, em 1 frase, por que cada uma ajuda."
CONTEXT_ENERGY = (
    "Exemplos de medidas de poupança de energia em casa: trocar lâmpadas incandescentes por LED; "
    "desligar aparelhos da tomada/standby; regular o termóstato (reduzir aquecimento ou ar condicionado); "
    "utilizar programas económicos na máquina da roupa/loiça; aproveitar luz natural; "
    "isolar janelas e portas; tomar duches mais curtos."
)
RUBRIC_ENERGY = {
    "scale": "0-10",
    "criteria": [
        {"name": "Duas medidas válidas", "desc": "apresenta 2 ações concretas e corretas", "weight": 6},
        {"name": "Justificação breve", "desc": "explica em 1 frase por que cada medida poupa energia", "weight": 3},
        {"name": "Clareza", "desc": "resposta clara e direta", "weight": 1},
    ],
    "scoring": {
        "10": "Duas medidas corretas + justificações claras e breves.",
        "7": "Duas medidas corretas mas justificações incompletas/vagas.",
        "4": "Só uma medida correta ou explicações confusas.",
        "0": "Responde de forma irrelevante ou incorreta."
    }
}

# ——— Pergunta 3 (geral): conceito de sistemas distribuídos ———
# Pergunta/Contexto/Rubrica embutidos diretamente na entrada do catálogo
# (mantemos o estilo das anteriores e a mesma escala 0–10)
QUESTIONS = {
    "RAG (técnica)": {
        "question": QUESTION_RAG,
        "context": CONTEXT_RAG,
        "rubric": RUBRIC_RAG,
    },
    "Poupança de energia (geral)": {
        "question": QUESTION_ENERGY,
        "context": CONTEXT_ENERGY,
        "rubric": RUBRIC_ENERGY,
    },
    "Conceito de Sistemas Distribuídos (geral)": {
        "question": "O que é um sistema distribuído e quais são as principais vantagens e desafios associados à sua utilização?",
        "context": (
            "Um sistema distribuído é um conjunto de computadores independentes "
            "que se apresenta aos utilizadores como um sistema único e coerente. "
            "As suas vantagens incluem escalabilidade, tolerância a falhas e melhor desempenho, "
            "permitindo distribuir carga e executar tarefas em paralelo. "
            "Os principais desafios incluem comunicação e coordenação entre nós, "
            "latência e sincronização de relógios, consistência de dados, deteção de falhas "
            "e questões de segurança. O objetivo é garantir transparência e fiabilidade "
            "mesmo em presença de falhas parciais."
        ),
        "rubric": {
            "scale": "0–10",
            "criteria": [
                {"name": "Definição correta de sistema distribuído"},
                {"name": "Identificação de vantagens"},
                {"name": "Identificação de desafios"},
                {"name": "Clareza e coerência da explicação"},
            ],
            "scoring": {
                "Excelente": "Explica corretamente o conceito, indica múltiplas vantagens e desafios, com linguagem clara.",
                "Bom": "Define corretamente e menciona pelo menos uma vantagem e um desafio.",
                "Insuficiente": "Confunde o conceito ou não identifica vantagens/desafios relevantes."
            }
        },
    },
}

GENERIC_RUBRIC = {
    "scale": "0-10",
    "criteria": [
        {
            "name": "Cobertura dos conceitos principais",
            "desc": "A resposta aborda os pontos mais relevantes pedidos na pergunta.",
            "weight": 5,
        },
        {
            "name": "Correção e precisão",
            "desc": "Os conceitos estão factualmente corretos e bem relacionados.",
            "weight": 3,
        },
        {
            "name": "Clareza e organização",
            "desc": "A resposta é clara, bem estruturada e usa linguagem adequada.",
            "weight": 2,
        },
    ],
    "scoring": {
        "10": "Responde de forma completa, correta e clara a todos os pontos principais.",
        "7": "Responde bem à maior parte dos pontos, com pequenas falhas ou alguma vagueza.",
        "4": "Resposta parcial ou com vários pontos pouco claros/incorretos.",
        "0": "Resposta irrelevante, muito incorreta ou em branco.",
    },
}

# Qual aparece selecionada por defeito
DEFAULT_QUESTION_KEY = "RAG (técnica)"

# Template do prompt enviado ao modelo
PROMPT_TMPL = """\
Pergunta:
{question}

Contexto/solução de referência:
{context}

Rubrica de avaliação (escala {scale}):
- Critérios: {criteria}
- Guias de pontuação: {scoring}

Resposta do aluno:
{student_answer}

Tarefa:
1) Atribui uma nota inteira entre 0 e 10 conforme a rubrica.
2) Justifica em 1–2 frases.
3) Lista erros/omissões e 1–3 dicas concretas.
4) Responde apenas com JSON válido.
"""
