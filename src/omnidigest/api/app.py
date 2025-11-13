from __future__ import annotations

import os
from datetime import datetime, timezone, date, timedelta
from typing import Optional, Dict, List

from fastapi import (
    FastAPI,
    HTTPException,
    Header,
    Depends,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from loguru import logger
from jose import jwt
from jose.exceptions import JWTError

from ..config.settings import get_settings
from ..pipeline.digest_core import run_digest_core
from ..delivery.emailer import Emailer, EmailMessage
from ..db import SessionLocal, init_db, models as db_models

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore


app = FastAPI(title="OmniDigest API", version="0.4.0")


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


class MagicLinkRequest(BaseModel):
    email: EmailStr


# ============= 鉴权&时间工具 =============

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


def _get_secret_key() -> str:
    settings = get_settings()
    secret = getattr(settings, "secret_key", None) or os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY / secret_key is not configured")
    return secret


def _create_session_token(subscriber: db_models.Subscriber) -> str:
    """
    基于 Subscriber 信息生成一个登录会话 JWT，存进 cookie。
    有效期：30 天。
    """
    secret = _get_secret_key()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=30)

    payload = {
        "sub": str(subscriber.id),
        "email": subscriber.email,
        "exp": exp,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _decode_session_token(token: str) -> Optional[Dict]:
    secret = _get_secret_key()
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
        return data
    except JWTError as e:
        logger.warning("Session token decode failed: {}", e)
        return None


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
    手动触发一次：仅供你本地/调试使用。
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


@app.post("/unsubscribe")
def unsubscribe(email: EmailStr, domain: str = Query("ai")):
    """
    简单退订接口：按 email + domain 关闭 active。
    """
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


# ============= Magic Link 登录入口（Step 5） =============

@app.post("/auth/magic-link")
def request_magic_link(req: MagicLinkRequest, request: Request):
    """
    请求一封登录/管理订阅用的 magic link。
    - 如果邮箱不存在，则先创建 Subscriber（默认 verified=False）
    - 生成 30 分钟有效的 JWT token
    - 发送带有链接的邮件
    """
    secret = _get_secret_key()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=30)

    db = SessionLocal()
    try:
        # 找或创建 Subscriber
        sub = (
            db.query(db_models.Subscriber)
            .filter(db_models.Subscriber.email == req.email)
            .one_or_none()
        )
        if not sub:
            sub = db_models.Subscriber(
                email=req.email,
                timezone=get_settings().timezone or "Asia/Shanghai",
                verified=False,
                created_at=now,
            )
            db.add(sub)
            db.commit()
            db.refresh(sub)

        # 生成 magic token
        payload = {
            "email": sub.email,
            "sub_id": sub.id,
            "exp": exp,
            "iat": now,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        # 生成 magic link URL（基于当前请求构造）
        magic_url = str(request.url_for("magic_login")) + f"?token={token}"

        # 发邮件
        try:
            html = f"""
            <html>
              <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
                <p>Hi,</p>
                <p>Click the link below to access your OmniDigest subscription:</p>
                <p><a href="{magic_url}" target="_blank">Open OmniDigest</a></p>
                <p style="font-size:12px;color:#888;">This link will expire in 30 minutes.</p>
              </body>
            </html>
            """
            msg = EmailMessage(
                to=[sub.email],
                subject="Your OmniDigest magic link",
                body_html=html,
            )
            res = Emailer().send(msg)
            logger.info("Magic link email send result: {}", res)
        except Exception as e:
            logger.exception("Magic link send failed: {}", e)
            raise HTTPException(status_code=500, detail="Failed to send magic link email")

        return {"status": "ok"}
    finally:
        db.close()


@app.get("/auth/magic", name="magic_login", response_class=HTMLResponse)
def magic_login(token: str):
    """
    用户点击邮件里的 magic link 访问这里。
    - 校验 JWT（签名 + 过期）
    - 找到 Subscriber，标记 verified=True
    - 生成长期 session token 写入 cookie
    - 返回一个简单的 HTML 欢迎页（后续可以替换为前端）
    """
    secret = _get_secret_key()

    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as e:
        logger.warning("Magic link token invalid: {}", e)
        return HTMLResponse(
            "<h3>Link expired or invalid.</h3>",
            status_code=400,
        )

    email = data.get("email")
    sub_id = data.get("sub_id")

    if not email or not sub_id:
        return HTMLResponse(
            "<h3>Invalid link.</h3>",
            status_code=400,
        )

    db = SessionLocal()
    try:
        sub = (
            db.query(db_models.Subscriber)
            .filter(db_models.Subscriber.id == sub_id)
            .one_or_none()
        )
        if not sub:
            return HTMLResponse(
                "<h3>Subscriber not found.</h3>",
                status_code=404,
            )

        # 标记 verified=True
        if not sub.verified:
            sub.verified = True
            db.commit()

        # 生成 session token
        session_token = _create_session_token(sub)

        # 简单欢迎页（后续可替换成前端页面）
        html = f"""
        <html>
          <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px;">
            <h2>Welcome back 👋</h2>
            <p>You are logged in as <b>{sub.email}</b>.</p>
            <p>We will send your <b>AI Daily Brief</b> according to your preferences.</p>
            <p style="margin-top:20px;font-size:13px;color:#666;">
              You can close this tab now.
            </p>
          </body>
        </html>
        """

        response = HTMLResponse(html)
        # 写入 session cookie（简化版）
        response.set_cookie(
            key="omnidigest_session",
            value=session_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=30 * 24 * 3600,
        )
        return response
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