from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger
from premailer import transform

from ..config.settings import get_settings
from ..summarizer.summarizer import ArticleSummary


@dataclass
class ComposedReport:
    markdown: str
    html: str


class ReportComposer:
    def __init__(self) -> None:
        template_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.settings = get_settings()

    def compose(
        self,
        domain_name: str,
        summaries: Iterable[ArticleSummary],
        highlights: str,
        insights: List[str] | None = None,
    ) -> ComposedReport:
        generated_at = datetime.utcnow()
        context = {
            "domain_name": domain_name,
            "summaries": list(summaries),
            "highlights": highlights,
            "insights": insights or [],
            "generated_at": generated_at,
        }
        markdown_template = self.env.get_template("daily_digest.md.j2")
        html_template = self.env.get_template("daily_digest.html.j2")
        markdown = markdown_template.render(**context)
        html = html_template.render(**context)
        if self.settings.enable_html_email:
            html = transform(html)
        logger.debug("Composed report for {}", domain_name)
        return ComposedReport(markdown=markdown.strip(), html=html.strip())


__all__ = ["ReportComposer", "ComposedReport"]
