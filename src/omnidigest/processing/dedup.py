from typing import Iterable, List

from loguru import logger

from ..storage.models import Article


def deduplicate_articles(articles: Iterable[Article]) -> List[Article]:
    buffer = list(articles)
    seen: set[str] = set()
    deduped: List[Article] = []
    for article in buffer:
        key = article.link or article.title
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    logger.debug("Deduplicated articles from {} to {}", len(buffer), len(deduped))
    return deduped


__all__ = ["deduplicate_articles"]
