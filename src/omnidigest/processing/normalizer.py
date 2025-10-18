from datetime import datetime
from typing import Iterable, List

from loguru import logger

from ..storage.models import Article


def normalize_articles(articles: Iterable[Article]) -> List[Article]:
    normalized: List[Article] = []
    for article in articles:
        summary = (article.summary or "").strip()
        normalized_article = Article(
            title=article.title.strip() or "Untitled",
            link=article.link.strip(),
            summary=summary,
            published=article.published or datetime.utcnow(),
            tags=sorted(set(article.tags)),
        )
        normalized.append(normalized_article)
    logger.debug("Normalized {} articles", len(normalized))
    return normalized


__all__ = ["normalize_articles"]
