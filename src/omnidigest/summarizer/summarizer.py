from dataclasses import dataclass
from typing import Iterable, List

from loguru import logger

from ..storage.models import Article
from .llm_engine import LLMEngine


@dataclass
class ArticleSummary:
    article: Article
    summary: str


class Summarizer:
    def __init__(self, engine: LLMEngine | None = None):
        self.engine = engine or LLMEngine()

    def summarize_articles(self, articles: Iterable[Article]) -> List[ArticleSummary]:
        summaries: List[ArticleSummary] = []
        for article in articles:
            result = self.engine.summarize(article.summary or article.title)
            summaries.append(ArticleSummary(article=article, summary=result.text))
        logger.info("Generated summaries for {} articles", len(summaries))
        return summaries

    def generate_digest_highlights(self, summaries: Iterable[ArticleSummary]) -> str:
        titles = [summary.article.title for summary in summaries]
        if not titles:
            return "No articles available today."
        highlight = f"Top stories: {', '.join(titles[:3])}."
        logger.debug("Generated highlights: {}", highlight)
        return highlight


__all__ = ["Summarizer", "ArticleSummary"]
