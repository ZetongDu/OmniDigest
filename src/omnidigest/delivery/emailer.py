# src/omnidigest/delivery/emailer.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union, List

from loguru import logger
from ..config.settings import get_settings

# 读取全局配置（.env）
settings = get_settings()


# ---- 统一的邮件消息结构 ----
# 与 pipeline 的约定保持一致：优先使用 body_html/body_text
# 同时兼容旧字段 html（若提供则映射到 body_html）
@dataclass
class EmailMessage:
    # 收件人可选；若未提供，在发送阶段回退到 settings.email_test_to
    to: Optional[Union[str, List[str]]] = None
    subject: str = ""
    body_html: str = ""                 # 标准：HTML 正文
    body_text: Optional[str] = None     # 可选：纯文本正文
    html: Optional[str] = None          # 兼容字段：旧代码可能传 html
    # 可选字段
    reply_to: Optional[str] = None
    cc: Optional[Union[str, List[str]]] = None
    bcc: Optional[Union[str, List[str]]] = None

    def normalize(self) -> "EmailMessage":
        if not self.body_html and self.html:
            self.body_html = self.html
        return self


class Emailer:
    def __init__(self) -> None:
        self.provider = (settings.email_provider or "").lower()
        self.SendGridAPIClient = None
        if self.provider == "sendgrid":
            try:
                from sendgrid import SendGridAPIClient  # sendgrid==6.12.x
                self.SendGridAPIClient = SendGridAPIClient
            except Exception as e:
                logger.exception(f"SendGrid import/init failed: {e}")
                self.SendGridAPIClient = None

    def send(
        self,
        to: Union[str, List[str], EmailMessage],
        subject: Optional[str] = None,
        html: Optional[str] = None,
        body_text: Optional[str] = None,
    ) -> dict:
        """
        统一入口：
        - 支持直接传 EmailMessage
        - 也兼容老签名 send(to, subject, html[, body_text])
        """
        if isinstance(to, EmailMessage):
            msg = to.normalize()
        else:
            msg = EmailMessage(
                to=to,
                subject=subject or "",
                body_html=html or "",
                body_text=body_text,
                reply_to=getattr(settings, "email_reply_to", None),
            )
        if self.provider == "sendgrid":
            return self._send_sendgrid(msg)
        logger.info("Email provider not configured — skip send (no provider).")
        return {"sent": False, "reason": "no provider"}

    # ----------------- SendGrid 实现 -----------------
    def _send_sendgrid(self, msg: EmailMessage) -> dict:
        if not self.SendGridAPIClient:
            logger.warning("SendGrid client unavailable — skip send.")
            return {"sent": False, "reason": "sendgrid client not available"}

        if not (settings.sendgrid_api_key and settings.email_from):
            logger.warning("SendGrid config incomplete — skip send.")
            return {"sent": False, "reason": "sendgrid config incomplete"}

        try:
            from sendgrid.helpers.mail import Mail
        except Exception as e:
            logger.exception(f"SendGrid helpers import failed: {e}")
            return {"sent": False, "reason": str(e)}

        msg.normalize()

        # 收件人兜底：优先 msg.to，其次 .env 的 EMAIL_TEST_TO
        to_emails = msg.to or settings.email_test_to
        if not to_emails:
            logger.warning("No recipient (msg.to and EMAIL_TEST_TO are both empty) — skip send.")
            return {"sent": False, "reason": "no recipient"}

        mail = Mail(
            from_email=settings.email_from,
            to_emails=to_emails,
            subject=msg.subject,
            html_content=msg.body_html,
            plain_text_content=msg.body_text if msg.body_text else None,  # sendgrid 6.12.x 支持
        )

        # Reply-To（优先消息里的，其次 .env）
        reply_to = msg.reply_to or getattr(settings, "email_reply_to", None)
        if reply_to:
            try:
                mail.reply_to = reply_to
            except Exception:
                pass

        # 简单支持 CC/BCC
        def _as_list(x):
            if not x:
                return None
            return x if isinstance(x, list) else [x]

        if _as_list(msg.cc):
            try:
                mail.cc = _as_list(msg.cc)
            except Exception:
                pass
        if _as_list(msg.bcc):
            try:
                mail.bcc = _as_list(msg.bcc)
            except Exception:
                pass

        logger.info(f"Sending email via sendgrid from {settings.email_from} to {to_emails}")
        try:
            client = self.SendGridAPIClient(settings.sendgrid_api_key)
            resp = client.send(mail)
            # headers 在 6.12.x 可能不是标准 dict，这里做容错提取
            msg_id = None
            try:
                hdrs = getattr(resp, "headers", {})
                if isinstance(hdrs, dict):
                    msg_id = hdrs.get("x-message-id")
            except Exception:
                pass
            logger.info(f"Email sent (SendGrid): {resp.status_code}  id={msg_id}")
            return {"sent": int(resp.status_code) in (200, 202), "id": msg_id, "status": resp.status_code}
        except Exception as e:
            logger.exception(f"SendGrid send failed: {e}")
            return {"sent": False, "reason": str(e)}
