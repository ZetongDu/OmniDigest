from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from ..config.settings import get_settings
from ..pipeline.run_digest import run_digest_pipeline


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone)

    def job(domain: str):
        logger.info("Scheduled digest run for {}", domain)
        run_digest_pipeline(domain)

    scheduler.add_job(job, "cron", args=["ai"], hour=7, id="ai_digest")
    scheduler.add_job(job, "cron", args=["finance"], hour=8, id="finance_digest")
    scheduler.start()
    logger.info("Scheduler started with timezone {}", settings.timezone)
    return scheduler


__all__ = ["start_scheduler"]
