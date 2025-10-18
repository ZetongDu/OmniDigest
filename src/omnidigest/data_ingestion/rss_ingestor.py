from datetime import datetime
from typing import Iterable, List

import feedparser
from loguru import logger

from ..storage.models import Article


class RssIngestor:
    def __init__(self, feeds: Iterable[dict]):
        self.feeds = list(feeds)

    def ingest(self) -> List[Article]:
        articles: List[Article] = []
        for feed in self.feeds:
            url = feed.get("url")
            tags = feed.get("tags", [])
            if not url:
                logger.warning("Skipping feed without URL: {}", feed)
                continue
            logger.info("Fetching RSS feed: {}", url)
            parsed = feedparser.parse(url)
            for entry in parsed.entries:
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6])
                summary = getattr(entry, "summary", "")
                article = Article(
                    title=getattr(entry, "title", "Untitled"),
                    link=getattr(entry, "link", ""),
                    summary=summary,
                    published=published,
                    tags=list(tags),
                )
                articles.append(article)
        logger.info("Ingested {} articles", len(articles))
        return articles


__all__ = ["RssIngestor"]
