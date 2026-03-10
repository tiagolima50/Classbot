from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from classbot.grader import tutor_chat, grade_answer
from classbot.retrieval import add_to_corpus, corpus_stats
from classbot.ai.lessons.base import LessonAgent, ChatResult, GradeResult

logger = logging.getLogger("classbot.lesson_agent")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


class LegacyLessonAgent(LessonAgent):
    """
    Adapter que implementa a interface LessonAgent usando as funções atuais.
    """

    def __init__(self, lesson_id: str):
        super().__init__(lesson_id=lesson_id)
        self.agent_id = str(uuid.uuid4())

        logger.info(
            "agent_init | lesson_id=%s | agent_id=%s",
            self.lesson_id,
            self.agent_id,
        )

    def chat(
        self,
        *,
        message: str,
        context: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatResult:
        logger.info(
            "agent_call | task=chat | lesson_id=%s | agent_id=%s | msg_chars=%d",
            self.lesson_id,
            self.agent_id,
            len(message or ""),
        )

        out = tutor_chat(
            message=message,
            context=context,
            uc=self.lesson_id,
            model=model,
            temperature=temperature,
        )

        return ChatResult(
            refused=bool(out.get("refused", False)),
            answer=(out.get("answer", "") or ""),
            citations=(out.get("citations", []) or []),
            evidence=(out.get("evidence", []) or []),
        )

    def grade(
        self,
        *,
        question: str,
        student_answer: str,
        context: str,
        rubric: Dict[str, Any],
    ) -> GradeResult:
        logger.info(
            "agent_call | task=grade | lesson_id=%s | agent_id=%s | q_chars=%d | a_chars=%d",
            self.lesson_id,
            self.agent_id,
            len(question or ""),
            len(student_answer or ""),
        )

        out = grade_answer(
            question=question,
            context=context,
            rubric=rubric,
            student_answer=student_answer,
            uc=self.lesson_id,
        )
        return GradeResult(**out)

    def add_docs(self, docs: List[str]) -> None:
        logger.info(
            "agent_call | task=add_docs | lesson_id=%s | agent_id=%s | n_docs=%d",
            self.lesson_id,
            self.agent_id,
            len(docs or []),
        )
        add_to_corpus(docs, uc=self.lesson_id)

    def corpus_stats(self) -> Dict[str, Any]:
        return corpus_stats(self.lesson_id)