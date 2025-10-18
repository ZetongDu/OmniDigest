from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from loguru import logger

from ..analyzer.impact_analyzer import analyze_impact
from ..composer.report_composer import ReportComposer
from ..config.settings import get_settings
from ..data_ingestion.rss_ingestor import RssIngestor
from ..delivery.emailer import EmailMessage, Emailer
from ..processing.dedup import deduplicate_articles
from ..processing.normalizer import normalize_articles
from ..storage.models import Digest
from ..summarizer.summarizer import Summarizer


@dataclass
class DigestRunResult:
    digest: Digest
    output_files: List[str]


def run_digest_pipeline(domain: str) -> DigestRunResult:
    settings = get_settings()
    domain_config = settings.load_domain_config(domain)
    feeds = domain_config.get("feeds", [])
    domain_name = domain_config.get("name", domain.title())

    ingestor = RssIngestor(feeds=feeds)
    raw_articles = ingestor.ingest()
    normalized_articles = normalize_articles(raw_articles)
    deduped_articles = deduplicate_articles(normalized_articles)

    summarizer = Summarizer()
    summaries = summarizer.summarize_articles(deduped_articles)
    highlights = summarizer.generate_digest_highlights(summaries)

    insights: List[str] = []
    if settings.enable_analysis:
        keywords = domain_config.get("analysis", {}).get("keywords", [])
        insights = analyze_impact(deduped_articles, keywords=keywords)

    composer = ReportComposer()
    report = composer.compose(
        domain_name=domain_name,
        summaries=summaries,
        highlights=highlights,
        insights=insights,
    )

    digest = Digest(domain=domain, generated_at=datetime.utcnow(), articles=deduped_articles, highlights=highlights)

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    base_filename = f"{date_str}-{domain}"
    markdown_path = out_dir / f"{base_filename}.md"
    html_path = out_dir / f"{base_filename}.html"
    markdown_path.write_text(report.markdown, encoding="utf-8")
    html_path.write_text(report.html, encoding="utf-8")
    logger.info("Digest written to {} and {}", markdown_path, html_path)

    emailer = Emailer()
    message = EmailMessage(
        subject=f"{domain_name} Digest - {date_str}",
        body_text=report.markdown,
        body_html=report.html if settings.enable_html_email else None,
    )
    emailer.send(message)

    return DigestRunResult(digest=digest, output_files=[str(markdown_path), str(html_path)])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run OmniDigest pipeline")
    parser.add_argument("--domain", required=True, help="Domain slug to run")
    args = parser.parse_args()
    run_digest_pipeline(args.domain)


if __name__ == "__main__":
    main()
