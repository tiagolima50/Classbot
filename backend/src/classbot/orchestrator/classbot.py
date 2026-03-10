from __future__ import annotations

from typing import Dict

from classbot.ai.lessons.base import LessonAgent
from classbot.ai.lessons.legacy_agent import LegacyLessonAgent


class ClassBot:
    """
    Orquestrador simples:
    - escolhe o LessonAgent correto por lesson_id
    - cacheia instâncias para reutilização
    """

    def __init__(self):
        self._cache: Dict[str, LessonAgent] = {}

    def lesson(self, lesson_id: str) -> LessonAgent:
        if lesson_id not in self._cache:
            self._cache[lesson_id] = LegacyLessonAgent(lesson_id=lesson_id)
        return self._cache[lesson_id]
