from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from ..config.settings import get_settings

_engine = None


def get_engine():  # pragma: no cover - simple accessor
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, echo=False)
        SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    engine = get_engine()
    with Session(engine) as session:
        yield session
