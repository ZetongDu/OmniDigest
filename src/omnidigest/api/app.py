from __future__ import annotations

import os
from datetime import datetime, timezone, date
from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from pydantic import BaseModel, EmailStr
from loguru import logger

from ..config.settings import get_settings
from ..pipeline.digest_core import run_digest_core
from ..delivery.emailer import Emailer, EmailMessage
from ..db import SessionLocal, init_db, models as db_models

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore


app = FastAPI(title="OmniDigest API", version="0.3.0")


# ============= 启动时初始化 DB =============

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("OmniDigest API started. DB initialized.")


# ============= 公共模型 =============

class RunResult(BaseModel):
    domain: str
    files: List[str]
    sent: bool
    meta: Dict


class SubscribeRequest(BaseModel):
    email: EmailStr
    domain: str = "ai"
    hour: int = 7
    minute: int = 0
    timezone: Optional[str] = None


class SubscribeResponse(BaseModel):
    email: EmailStr
    domain: str
    hour: int
    minute: int
    timezone: str


# ============= 鉴权工具 =============

def verify_trigger_token(authorization: Optional[str] = Header(None)) -> None:
    token = os.getenv("TRIGGER_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="TRIGGER_TOKEN not configured")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    provided = authorization.split(" ", 1)[1].strip()
    if provided != token:
        raise HTTPException(status_code=403, detail="Invalid token")


def get_tz(tz_name: Optional[str]) -> ZoneInfo:
    if not tz_name:
        settings = get_settings()
        tz_name = settings.timezone or os.getenv("TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


# ============= 基础探活 =============

@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/version")
def version():
    s = get_settings()
    return {
        "app": s.app_name,
        "env": s.app_env,
        "timezone": s.timezone,
    }


# ============= 手动触发（调试） =============

@app.post("/run_digest", response_model=RunResult)
def run_digest(domain: str = Query("ai")):
    """
    手动触发一次：仅供你调试使用。
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )


# ============= 安全单次触发（运维用） =============

@app.post("/trigger", response_model=RunResult, dependencies=[Depends(verify_trigger_token)])
def trigger(domain: str = Query("ai")):
    """
    安全触发一次（不看用户订阅，直接对某个 domain 跑一次）：
    - 用于手动修复/补发。
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )


# ============= 订阅接口（Step 4A） =============

@app.post("/subscribe", response_model=SubscribeResponse)
def subscribe(req: SubscribeRequest):
    """
    创建或更新一个订阅：
    - email: 用户邮箱
    - domain: 订阅领域（ai / finance / ...）
    - hour, minute: 用户希望接收该领域日报的本地时间
    - timezone: 用户时区（可选，不传则用系统默认）

    当前版本：简化为自动 verified=True，后续可加邮件验证流程。
    """
    settings = get_settings()
    tz_name = req.timezone or settings.timezone or "Asia/Shanghai"
    domain = req.domain.lower()

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # 1) 查或建 Subscriber
        sub = (
            db.query(db_models.Subscriber)
            .filter(db_models.Subscriber.email == req.email)
            .one_or_none()
        )
        if not sub:
            sub = db_models.Subscriber(
                email=req.email,
                timezone=tz_name,
                verified=True,
                created_at=now,
            )
            db.add(sub)
            db.flush()
        else:
            if not sub.timezone:
                sub.timezone = tz_name

        # 2) 查或建 Subscription
        s = (
            db.query(db_models.Subscription)
            .filter(
                db_models.Subscription.subscriber_id == sub.id,
                db_models.Subscription.domain == domain,
            )
            .one_or_none()
        )
        if not s:
            s = db_models.Subscription(
                subscriber_id=sub.id,
                domain=domain,
                send_hour=req.hour,
                send_minute=req.minute,
                active=True,
                created_at=now,
            )
            db.add(s)
        else:
            s.send_hour = req.hour
            s.send_minute = req.minute
            s.active = True

        db.commit()

        return SubscribeResponse(
            email=sub.email,
            domain=domain,
            hour=s.send_hour,
            minute=s.send_minute,
            timezone=sub.timezone or tz_name,
        )
    finally:
        db.close()


# 可选：简单退订接口
@app.post("/unsubscribe")
def unsubscribe(email: EmailStr, domain: str = Query("ai")):
    domain = domain.lower()
    db = SessionLocal()
    try:
        sub = (
            db.query(db_models.Subscriber)
            .filter(db_models.Subscriber.email == email)
            .one_or_none()
        )
        if not sub:
            return {"status": "ok", "message": "no such subscriber"}

        s = (
            db.query(db_models.Subscription)
            .filter(
                db_models.Subscription.subscriber_id == sub.id,
                db_models.Subscription.domain == domain,
            )
            .one_or_none()
        )
        if not s:
            return {"status": "ok", "message": "no such subscription"}

        s.active = False
        db.commit()
        return {"status": "ok", "message": "unsubscribed"}
    finally:
        db.close()


# ============= 真·Cron：生成每日 Digest + 按订阅发送 =============

def _get_or_create_daily_digest(db, domain: str, today: str) -> db_models.DailyDigest:
    existing = (
        db.query(db_models.DailyDigest)
        .filter(
            db_models.DailyDigest.domain == domain,
            db_models.DailyDigest.date == today,
        )
        .one_or_none()
    )
    if existing:
        return existing

    # 只生成，不在这里群发
    result = run_digest_core(domain, write_outputs=True, send_email=False)
    dd = db_models.DailyDigest(
        domain=domain,
        date=today,
        subject=result.subject,
        html=result.html,
    )
    db.add(dd)
    db.commit()
    db.refresh(dd)
    logger.info("DailyDigest created for domain={} date={}", domain, today)
    return dd


@app.post("/cron", dependencies=[Depends(verify_trigger_token)])
def cron():
    """
    由 GitHub Actions 每 N 分钟调用一次。

    流程：
    1. 确保今天每个 domain 有一份 DailyDigest（没有就生成一次）
    2. 查 subscriptions：
       - 用户 verified & active
       - 按用户时区判断是否到达其配置的发送时间
       - 若到达且今天还没给该用户该 domain 发过 → 发送邮件 + 写 SendLog
    """
    settings = get_settings()
    now_utc = datetime.now(timezone.utc)
    today = date.fromtimestamp(now_utc.timestamp()).isoformat()

    raw_domains = os.getenv("DOMAINS", "ai")
    domains = [d.strip() for d in raw_domains.split(",") if d.strip()]

    db = SessionLocal()
    sent_records: List[Dict] = []
    try:
        for domain in domains:
            # 1) 确保有当日 Digest
            dd = _get_or_create_daily_digest(db, domain, today)

            # 2) 查该 domain 的订阅
            q = (
                db.query(db_models.Subscription, db_models.Subscriber)
                .join(db_models.Subscriber, db_models.Subscription.subscriber_id == db_models.Subscriber.id)
                .filter(
                    db_models.Subscription.domain == domain,
                    db_models.Subscription.active.is_(True),
                    db_models.Subscriber.verified.is_(True),
                )
            )

            for sub, user in q:
                # 2.1 计算用户本地时间
                user_tz = get_tz(user.timezone)
                user_now = now_utc.astimezone(user_tz)

                # 2.2 未到用户设定时间 → 跳过
                if (user_now.hour, user_now.minute) < (sub.send_hour, sub.send_minute):
                    continue

                # 2.3 检查今天是否已发送过
                existing_log = (
                    db.query(db_models.SendLog)
                    .filter(
                        db_models.SendLog.subscriber_id == user.id,
                        db_models.SendLog.domain == domain,
                        db_models.SendLog.date == today,
                    )
                    .one_or_none()
                )
                if existing_log:
                    continue  # 已发过，跳过

                # 2.4 发送邮件
                success = False
                try:
                    msg = EmailMessage(
                        to=[user.email],
                        subject=dd.subject,
                        body_html=dd.html,
                    )
                    res = Emailer().send(msg)
                    success = bool(res and res.get("sent", True))
                    logger.info(
                        "Cron send to {} domain={} success={}",
                        user.email, domain, success,
                    )
                except Exception as e:
                    logger.exception(
                        "Cron send failed for {} domain={}: {}",
                        user.email, domain, e,
                    )
                    success = False

                # 2.5 写入发送记录（无论成功失败，确保不重复尝试刷爆）
                log = db_models.SendLog(
                    subscriber_id=user.id,
                    domain=domain,
                    date=today,
                    success=success,
                )
                db.add(log)
                db.commit()

                if success:
                    sent_records.append(
                        {"email": user.email, "domain": domain}
                    )

        return {
            "time_utc": now_utc.isoformat(),
            "sent": sent_records,
        }
    finally:
        db.close()