from __future__ import annotations

import os
from datetime import datetime, timezone, date
from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from pydantic import BaseModel

from ..config.settings import get_settings
from ..pipeline.digest_core import run_digest_core

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python <3.9 兜底（Render 默认 3.11，一般用不到）
    from backports.zoneinfo import ZoneInfo  # type: ignore

app = FastAPI(title="OmniDigest API", version="0.2.0")


# ================= 公共模型 =================

class RunResult(BaseModel):
    domain: str
    files: List[str]
    sent: bool
    meta: Dict


# ================= 触发鉴权 =================

def verify_trigger_token(authorization: Optional[str] = Header(None)) -> None:
    """
    用于受保护触发接口 (/trigger, /cron) 的简单鉴权：
    - 环境变量 TRIGGER_TOKEN 存放口令
    - 请求头需要：Authorization: Bearer <token>
    """
    token = os.getenv("TRIGGER_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="TRIGGER_TOKEN not configured")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    provided = authorization.split(" ", 1)[1].strip()
    if provided != token:
        raise HTTPException(status_code=403, detail="Invalid token")


# ================= 基础探活 =================

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


# ================= 手动触发（调试用） =================

@app.post("/run_digest", response_model=RunResult)
def run_digest(domain: str = Query("ai")):
    """
    手动触发一次（不鉴权）：用于你自己在浏览器或工具里测试。
    示例：POST /run_digest?domain=ai
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )


# ================= 安全单次触发（外部系统直接点火） =================

@app.post("/trigger", response_model=RunResult, dependencies=[Depends(verify_trigger_token)])
def trigger(domain: str = Query("ai")):
    """
    安全触发入口：
    - 带 Authorization: Bearer <TRIGGER_TOKEN>
    - 立即对指定 domain 跑一次 digest（不管时间）
    """
    result = run_digest_core(domain, write_outputs=True, send_email=True)
    return RunResult(
        domain=domain,
        files=result.output_files,
        sent=bool(result.email_result and result.email_result.get("sent", True)),
        meta=result.meta,
    )


# ================= 智能定时调度入口（给 GitHub Actions 调） =================

# 进程内简单状态，避免同一天重复发送（在不加数据库前足够用）
_last_run_date: Dict[str, str] = {}  # { "ai": "2025-11-08", "finance": "2025-11-08" }


def _get_tz():
    settings = get_settings()
    tz_name = settings.timezone or os.getenv("TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _should_run_for_domain(domain: str, now: datetime) -> bool:
    """
    决定某个 domain 在当前时刻是否应该跑：
    - 读取环境变量 DIGEST_{DOMAIN}_HOUR / DIGEST_{DOMAIN}_MINUTE
      例如：DIGEST_AI_HOUR=7, DIGEST_AI_MINUTE=0
    - 如果当前时间 >= 配置时间，且今天还没给这个 domain 跑过 → 返回 True
    - 否则 False
    """
    key = domain.lower()
    last = _last_run_date.get(key)
    today = now.date().isoformat()

    # 已经跑过今天了
    if last == today:
        return False

    env_prefix = f"DIGEST_{domain.upper()}_"
    hour = int(os.getenv(env_prefix + "HOUR", "7"))
    minute = int(os.getenv(env_prefix + "MINUTE", "0"))

    if (now.hour, now.minute) < (hour, minute):
        return False

    # 标记为今天已经执行（避免同一次 /cron 被多次调用）
    _last_run_date[key] = today
    return True


@app.post("/cron", dependencies=[Depends(verify_trigger_token)])
def cron():
    """
    智能调度入口（推荐给 GitHub Actions 调用）：

    典型用法：
    - GitHub Actions 每 10 分钟调用一次 /cron
    - /cron 自己根据:
        - DOMAINS 环境变量 (如 "ai,finance")
        - 每个 domain 的 DIGEST_xxx_HOUR / MINUTE
      判断哪些 domain 需要在“今天的这个时刻”执行 digest。

    好处：
    - 定时时间放在环境变量里，可随时调整，无需改代码 / 改 workflow
    - 未来可以扩展为 per-user / per-plan 的策略调度
    """
    settings = get_settings()
    tz = _get_tz()
    now = datetime.now(tz)

    raw_domains = os.getenv("DOMAINS", "ai")
    domains = [d.strip() for d in raw_domains.split(",") if d.strip()]

    ran: List[Dict] = []
    skipped: List[str] = []

    for domain in domains:
        if _should_run_for_domain(domain, now):
            result = run_digest_core(domain, write_outputs=True, send_email=True)
            ran.append({
                "domain": domain,
                "files": result.output_files,
                "meta": result.meta,
            })
        else:
            skipped.append(domain)

    return {
        "time": now.isoformat(),
        "ran": ran,
        "skipped": skipped,
    }