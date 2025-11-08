from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from ..config.settings import get_settings
from loguru import logger

settings = get_settings()

# 使用 AppSettings 里的 DATABASE_URL
DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

Base = declarative_base()


def init_db() -> None:
    """
    初始化数据库表结构（在应用启动时调用一次）。
    本地默认使用 sqlite，线上可配置为 Postgres。
    """
    from . import models  # noqa: F401
    logger.info("Creating database tables if not exist...")
    Base.metadata.create_all(bind=engine)