from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Subscriber(Base):
    """
    订阅用户（按邮箱识别）
    """
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    timezone = Column(String(64), nullable=True)  # 用户时区，如 "Asia/Shanghai"
    verified = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    subscriptions = relationship("Subscription", back_populates="subscriber")


class Subscription(Base):
    """
    用户对某个领域(domain)的订阅，以及该领域的发送时间。
    """
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), nullable=False)
    domain = Column(String(64), nullable=False)  # 如 "ai", "finance"
    send_hour = Column(Integer, nullable=False, default=7)
    send_minute = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    subscriber = relationship("Subscriber", back_populates="subscriptions")

    __table_args__ = (
        UniqueConstraint("subscriber_id", "domain", name="uq_subscriber_domain"),
    )


class DailyDigest(Base):
    """
    每个领域每天只生成一次的日报内容。
    """
    __tablename__ = "daily_digests"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(64), nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    subject = Column(String(512), nullable=False)
    html = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("domain", "date", name="uq_digest_domain_date"),
    )


class SendLog(Base):
    """
    记录每天对每个用户每个领域是否已经发送，避免重复发送。
    """
    __tablename__ = "send_logs"

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), nullable=False)
    domain = Column(String(64), nullable=False)
    date = Column(String(10), nullable=False)
    success = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    subscriber = relationship("Subscriber")

    __table_args__ = (
        UniqueConstraint("subscriber_id", "domain", "date", name="uq_send_once"),
    )