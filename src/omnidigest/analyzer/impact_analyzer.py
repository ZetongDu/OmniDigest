from typing import Iterable, List

from loguru import logger

from ..storage.models import Article


def analyze_impact(articles: Iterable[Article], keywords: List[str] | None = None) -> List[str]:
    keywords = keywords or []
    insights: List[str] = []
    lower_keywords = [kw.lower() for kw in keywords]
    for article in articles:
        if any(keyword in article.summary.lower() for keyword in lower_keywords):
            insight = f"Potential impact detected in '{article.title}'"
            insights.append(insight)
    logger.debug("Impact analysis generated {} insights", len(insights))
    return insights


__all__ = ["analyze_impact"]
