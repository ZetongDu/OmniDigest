# src/omnidigest/delivery/schedule_worker.py
from __future__ import annotations
import os, time
from datetime import datetime
from typing import List
import portalocker
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from ..config.settings import get_settings
from ..pipeline.digest_core import run_digest_core
from dotenv import load_dotenv
load_dotenv(".env")  # 让 os.getenv 能读到 .env

settings = get_settings()
tz = settings.timezone or "Asia/Shanghai"

# 从 .env 读取领域列表（默认只跑 ai）：DOMAINS=ai,finance
DOMAINS: List[str] = [s.strip() for s in os.getenv("DOMAINS", "ai").split(",") if s.strip()]
STAGGER_MINUTES = 5  # 各领域错峰间隔（分钟）

# 国内网络：如未设置代理则自动设置到 127.0.0.1:4780
if os.getenv("HTTP_PROXY") is None and os.getenv("HTTPS_PROXY") is None:
    os.environ["HTTP_PROXY"]  = "http://127.0.0.1:4780"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:4780"

LOG_DIR = os.path.abspath(os.path.join(os.getcwd(), "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
LOCK_PATH = os.path.join(LOG_DIR, "scheduler.lock")

def run_once(domain: str):
    """单次执行（文件锁防并发重入）"""
    with portalocker.Lock(LOCK_PATH, timeout=5):
        start = time.time()
        logger.info(f"[{domain}] scheduled run start at {datetime.now().isoformat()}")
        try:
            run_digest_core(domain, write_outputs=True, send_email=True)
            logger.info(f"[{domain}] scheduled run finished in {time.time() - start:.1f}s")
        except Exception as e:
            logger.exception(f"[{domain}] scheduled run failed: {e}")

def main():
    sched = BlockingScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
    )

    base_hour = 7  # 每天 07:00 开始，按 STAGGER_MINUTES 错峰
    for i, domain in enumerate(DOMAINS):
        trigger = CronTrigger(hour=base_hour, minute=i * STAGGER_MINUTES)
        sched.add_job(run_once, trigger, args=[domain], id=f"digest_{domain}")
        logger.info(f"Scheduled {domain} at {base_hour:02d}:{i*STAGGER_MINUTES:02d} ({tz})")

    logger.info(f"Worker scheduler started tz={tz} domains={DOMAINS}")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker scheduler stopped")

if __name__ == "__main__":
    main()
