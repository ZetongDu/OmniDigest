from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ..config.settings import AppSettings, get_settings


@dataclass
class SummaryResult:
    text: str
    engine: str


class LLMEngine:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or get_settings()

    def summarize(self, prompt: str, max_sentences: int = 3) -> SummaryResult:
        engine = self._select_engine()
        logger.debug("Generating summary with engine {}", engine)
        text = self._mock_summary(prompt, max_sentences=max_sentences)
        return SummaryResult(text=text, engine=engine)

    def _select_engine(self) -> str:
        if self.settings.openai_api_key:
            return "openai"
        if self.settings.anthropic_api_key:
            return "anthropic"
        if self.settings.google_api_key:
            return "google"
        return "mock"

    @staticmethod
    def _mock_summary(prompt: str, max_sentences: int = 3) -> str:
        sentences = [segment.strip() for segment in prompt.split('.') if segment.strip()]
        selected = sentences[:max_sentences]
        if not selected:
            return "No summary available."
        text = '. '.join(selected)
        if len(selected) < len(sentences):
            text += '...'
        return text


__all__ = ["LLMEngine", "SummaryResult"]
