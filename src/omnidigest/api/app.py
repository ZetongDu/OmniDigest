# src/omnidigest/api/app.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# 复用你现有的调度入口
from src.omnidigest.delivery.schedule_worker import run_once
from src.omnidigest.config.settings import get_settings

app = FastAPI(title="OmniDigest API", version="0.1.0")

# ---- CORS（便于后面前端/小程序/管理台接入；如需限制可改 ENV） ----
allow_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allow_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 简单鉴权：Bearer <TRIGGER_TOKEN> 或 x-api-key 头 ----
def _check_auth(req: Request) -> None:
    token_expected = os.getenv("TRIGGER_TOKEN") or ""
    if not token_expected:
        # 如果你没配 TRIGGER_TOKEN，就拒绝触发（避免被乱调）
        raise HTTPException(status_code=403, detail="Trigger disabled: TRIGGER_TOKEN not set.")

    # 1) Authorization: Bearer <token>
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.replace("Bearer ", "").strip()
        if token and token == token_expected:
            return

    # 2) x-api-key: <token>
    if req.headers.get("x-api-key", "") == token_expected:
        return

    raise HTTPException(status_code=401, detail="Unauthorized")

# ---- 基础信息 ----
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/version")
def version():
    s = get_settings()
    return {
        "app": s.app_name,
        "env": s.app_env,
        "timezone": s.timezone,
        "version": "0.1.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }

# ---- 手动触发：POST /trigger?domain=ai ----
@app.post("/trigger")
def trigger(background: BackgroundTasks, request: Request, domain: str = Query("ai")):
    _check_auth(request)

    # 这里用后台任务，不阻塞 HTTP 请求
    def _job():
        try:
            logger.info(f"[API trigger] run_once({domain}) accepted")
            run_once(domain)
            logger.info(f"[API trigger] run_once({domain}) finished")
        except Exception as e:
            logger.exception(f"[API trigger] run_once({domain}) failed: {e}")

    background.add_task(_job)
    return {
        "accepted": True,
        "domain": domain,
        "queued_at": datetime.now(timezone.utc).isoformat()
    }