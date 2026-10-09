"""The ONLY contract the AI layer must satisfy. Swap KeywordBackend for a spaCy /
Sentence-Transformers / LLM backend later by implementing these 3 methods."""
from abc import ABC, abstractmethod

class AIBackend(ABC):
    @abstractmethod
    def extract_skills(self, text: str, catalog: list[dict]) -> list[tuple[int, float]]:
        """-> [(skill_id, confidence 0..1)]"""

    @abstractmethod
    def suggest_improvements(self, gap: list[dict], score: float) -> list[str]:
        """gap rows: {skill_name, importance, required, matched}"""

    @abstractmethod
    def build_resume(self, profile: dict, jd: dict, matched: list[str], missing: list[str]) -> str:
        """-> resume text (markdown)"""
