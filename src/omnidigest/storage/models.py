from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, SQLModel


class Article(SQLModel, table=False):
    title: str
    link: str
    summary: str
    published: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)


class Digest(SQLModel, table=False):
    domain: str
    generated_at: datetime
    articles: List[Article] = Field(default_factory=list)
    highlights: Optional[str] = None


__all__ = ["Article", "Digest"]
