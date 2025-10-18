from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from ..config.settings import get_settings


@dataclass
class EmailMessage:
    subject: str
    body_text: str
    body_html: str | None = None
    recipients: Iterable[str] | None = None


class Emailer:
    def __init__(self) -> None:
        self.settings = get_settings()

    def send(self, message: EmailMessage) -> bool:
        if not self.settings.sendgrid_api_key or not self.settings.email_from:
            logger.warning("Email configuration missing. Skipping send.")
            return False
        recipients = list(message.recipients or []) or [self.settings.email_test_to]
        logger.info(
            "Sending email via {} from {} to {}", self.settings.email_provider or "sendgrid", self.settings.email_from, recipients
        )
        # Placeholder for actual email sending logic
        return True


__all__ = ["Emailer", "EmailMessage"]
