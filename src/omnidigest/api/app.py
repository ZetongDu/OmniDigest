from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from pydantic import BaseModel

from ..config.settings import get_settings
from ..pipeline.digest_core import run_digest_core

app = FastAPI(title="OmniDigest API", version="0.1.0")


# ---------- 基础模型 ----------

class RunResult(BaseModel):
    domain: str
    files: list[str]
    sent: bool
    meta: dict


# ---------- 工具：校验触发口令 ----------

def verify_trigger_token(authorization: Optional[str] = Header(None)) -> None:
    """
    用于 /trigger 接口的简单鉴权：
    - 从环境变量 TRIGGER_TOKEN 读取口令
    - 要求请求头：Authorization: Bearer <token>
    """
    token = os.getenv("TRIGGER_TOKEN")
    if not token:
        # 服务端没配 token 就是配置问题，直接 500
        raise HTTPException(status_code=500, detail="TRIGGER_TOKEN not configured")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    provided = authorization.split(" ", 1)[1].strip()
    if provided != token:
        raise HTTPException(status_code=403, detail="Invalid token")


# ---------- 基础探活 ----------

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


# ---------- 手动触发：不鉴权（仅用于调试，后续可关） ----------

@app.post("/run_digest", response_model=RunResult)
def run_digest(domain: str = Query("ai")):
    """
    手动触发一次：直接在浏览器试用用的。
    示例：POST /run_digest?domain=ai
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )


# ---------- 安全触发：给 GitHub Actions 用 ----------

@app.post("/trigger", response_model=RunResult, dependencies=[Depends(verify_trigger_token)])
def trigger(domain: str = Query("ai")):
    """
    安全触发入口：
    - 只接受带正确 Authorization Bearer token 的请求
    - 给 GitHub Actions / 其他 scheduler 调用
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )