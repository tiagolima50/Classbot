from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


# =========================================================
# OUTPUTS ESTRUTURADOS
# =========================================================

class ChatResult(BaseModel):
    """
    Resultado do modo tutor (/chat).

    Deve bater exatamente com o que o endpoint /chat devolve.
    """
    refused: bool = False
    answer: str = ""
    citations: List[int] = []
    evidence: List[str] = []


class GradeResult(BaseModel):
    """
    Resultado da avaliação automática (/responses).
    Mantém o formato atual do teu grader.
    """
    score: float
    rationale: str = ""
    mistakes: List[str] = []
    tips: List[str] = []
    citations: List[int] = []
    evidence: List[str] = []


# =========================================================
# INTERFACE DO LESSON AGENT
# =========================================================

class LessonAgent(ABC):
    """
    Interface comum para qualquer aula.

    IMPORTANTE:
    - O main.py só fala com esta interface.
    - A implementação interna pode mudar (legacy hoje, LangChain amanhã).
    """

    def __init__(self, lesson_id: str):
        self.lesson_id = lesson_id

    # ---------------- CHAT ----------------

    @abstractmethod
    def chat(
        self,
        *,
        message: str,
        context: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatResult:
        """
        Responde a dúvidas do aluno usando o corpus da aula.
        """
        raise NotImplementedError

    # ---------------- GRADING ----------------

    @abstractmethod
    def grade(
        self,
        *,
        question: str,
        student_answer: str,
        context: str,
        rubric: Dict[str, Any],
    ) -> GradeResult:
        """
        Avalia uma resposta aberta usando RAG por aula.
        """
        raise NotImplementedError

    # ---------------- CORPUS ----------------

    @abstractmethod
    def add_docs(self, docs: List[str]) -> None:
        """
        Adiciona documentos ao corpus da aula.
        """
        raise NotImplementedError

    @abstractmethod
    def corpus_stats(self) -> Dict[str, Any]:
        """
        Devolve estatísticas do índice vetorial da aula.
        """
        raise NotImplementedError
