# src/omnidigest/pipeline/run_digest.py
from __future__ import annotations

import os
import importlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

from loguru import logger

from ..config.settings import get_settings
from ..delivery.emailer import Emailer


# =============== 基础工具 ===============

@dataclass
class DigestRunResult:
    output_files: List[str]
    email_result: Optional[Dict[str, Any]]
    meta: Dict[str, Any]


def _ensure_out_dir() -> Path:
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _write_outputs(domain: str, html: str, md: str = "") -> List[str]:
    out_dir = _ensure_out_dir()
    today = date.today().isoformat()
    files: List[str] = []

    if md:
        p_md = out_dir / f"{today}-{domain}.md"
        p_md.write_text(md, encoding="utf-8")
        files.append(str(p_md.resolve()))

    p_html = out_dir / f"{today}-{domain}.html"
    p_html.write_text(html, encoding="utf-8")
    files.append(str(p_html.resolve()))

    logger.info("Digest written to {}", files)
    return files


def _load_recipients(domain: str, settings) -> List[str]:
    """
    从环境变量里拿收件人：
      RECIPIENTS_AI=
      RECIPIENTS_FINANCE=
    如果没配，就 fallback 到 EMAIL_TEST_TO（也就是你和内测用户）
    """
    key = f"RECIPIENTS_{domain.upper()}"
    raw = os.getenv(key, "") or ""
    lst = [x.strip() for x in raw.split(",") if x.strip()]
    if not lst and settings.email_test_to:
        lst = [settings.email_test_to]
    return lst


# =============== 动态 import 工具 ===============

def _resolve_callable(
    module_path: str,
    func_candidates: List[str] | None = None,
    class_method_candidates: List[Tuple[str, str]] | None = None,
) -> Optional[Callable | Tuple[type, str]]:
    """
    去 module 里找我们需要的函数/方法。找不到就返回 None，不抛异常。
    """
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        logger.debug("Module {} not importable: {}", module_path, e)
        return None

    if func_candidates:
        for name in func_candidates:
            fn = getattr(mod, name, None)
            if callable(fn):
                logger.debug("Resolved {}.{}()", module_path, name)
                return fn

    if class_method_candidates:
        for cls_name, method_name in class_method_candidates:
            cls = getattr(mod, cls_name, None)
            if cls is not None:
                logger.debug("Resolved class {}.{} in {}", cls_name, method_name, module_path)
                return (cls, method_name)

    return None


# =============== 兜底实现（RSS 抓取、去重、简易 HTML） ===============

def _fallback_ingest(feeds: List[Dict[str, Any]], max_items_per_feed: int = 20) -> List[Dict[str, Any]]:
    """
    非常稳的兜底抓取：依赖 feedparser。
    结构是 list[ {title, url, summary, source, ...}, ... ]
    """
    try:
        import feedparser
    except Exception as e:
        raise ImportError(f"feedparser not available: {e}")

    items: List[Dict[str, Any]] = []
    for f in feeds:
        url = f.get("url") if isinstance(f, dict) else str(f)
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
            for entry in (parsed.entries or [])[:max_items_per_feed]:
                items.append({
                    "title": entry.get("title") or "",
                    "url": entry.get("link") or entry.get("id") or "",
                    "summary": entry.get("summary") or entry.get("description") or "",
                    "published": entry.get("published") or entry.get("updated") or "",
                    "source": (parsed.feed.get("title") if getattr(parsed, "feed", None) else "") or "",
                })
        except Exception as e:
            logger.warning("Fallback ingest failed for {}: {}", url, e)

    logger.debug("Fallback ingest collected {} items", len(items))
    return items


def _fallback_dedup(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for a in articles:
        key = (a.get("url") or "").strip().lower() or (a.get("title") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(a)
    return deduped


# =============== 各步骤（项目实现优先，失败就兜底） ===============

def _ingest_articles(domain_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    1. 优先用你项目里的 rss_ingestor (ingest / ingest_feeds / RssIngestor.ingest ...)
    2. 如果失败，就用 _fallback_ingest()
    """
    feeds = domain_cfg.get("feeds", [])
    if not feeds:
        logger.warning("No feeds configured in domain config.")
        return []

    resolved = _resolve_callable(
        "src.omnidigest.data_ingestion.rss_ingestor",
        func_candidates=["ingest", "ingest_feeds", "run", "fetch_feeds"],
        class_method_candidates=[
            ("RssIngestor", "ingest"),
            ("RSSIngestor", "ingest"),
            ("RssIngestor", "run"),
            ("RSSIngestor", "run"),
        ],
    )

    # A: 直接函数
    if callable(resolved):
        try:
            arts = resolved(feeds)
            if isinstance(arts, list):
                return arts
            logger.warning("Project ingest returned non-list; using fallback.")
        except Exception as e:
            logger.warning("Project ingest failed, using fallback. {}", e)
        return _fallback_ingest(feeds)

    # B: 类方法 (cls, method)
    if isinstance(resolved, tuple) and len(resolved) == 2:
        klass, method_name = resolved
        # 先尝试带 feeds 实例化，再尝试无参
        for ctor in (lambda: klass(feeds), lambda: klass()):
            try:
                inst = ctor()
                meth = getattr(inst, method_name, None)
                if callable(meth):
                    try:
                        try:
                            arts = meth(feeds)
                        except TypeError:
                            arts = meth()
                        if isinstance(arts, list):
                            return arts
                    except Exception as e:
                        logger.debug("Call {}.{} failed: {}", klass.__name__, method_name, e)
            except Exception as e:
                logger.debug("Instantiate {} failed: {}", klass.__name__, e)

        logger.warning("Project class-based ingestor unusable; using fallback.")
        return _fallback_ingest(feeds)

    # C: 完全找不到 → 兜底
    logger.debug("No project ingestor resolved; using fallback.")
    return _fallback_ingest(feeds)


def _normalize(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fn = _resolve_callable(
        "src.omnidigest.processing.normalizer",
        func_candidates=["normalize_articles", "normalize"],
    )
    if callable(fn):
        try:
            return fn(articles)  # type: ignore
        except Exception as e:
            logger.warning("normalize failed, skip: {}", e)
    return articles


def _dedup(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fn = _resolve_callable(
        "src.omnidigest.processing.dedup",
        func_candidates=["deduplicate_articles", "dedup", "deduplicate"],
    )
    if callable(fn):
        try:
            return fn(articles)  # type: ignore
        except Exception as e:
            logger.warning("dedup failed, use fallback: {}", e)
            return _fallback_dedup(articles)
    return _fallback_dedup(articles)


def _summarize(articles: List[Any]) -> List[Dict[str, Any]]:
    """
    尝试调用项目内 summarizer.summarize_articles；
    如果失败，手工构造简短摘要，兼容 dict 或 Article-like 对象
    """
    fn = _resolve_callable(
        "src.omnidigest.summarizer.summarizer",
        func_candidates=["summarize_articles", "summarize"],
    )
    if callable(fn):
        try:
            summaries = fn(articles)  # type: ignore
            if isinstance(summaries, list) and summaries:
                return summaries
        except Exception as e:
            logger.warning("LLM summarizer failed; fallback will be used. {}", e)

    # fallback summarization
    fallback: List[Dict[str, Any]] = []

    def _get_attr_or_key(obj: Any, *candidates: str, default: str = "") -> str:
        for name in candidates:
            if isinstance(obj, dict):
                if name in obj and obj[name]:
                    return str(obj[name])
            else:
                if hasattr(obj, name):
                    val = getattr(obj, name)
                    if val:
                        return str(val)
        return default

    for a in articles:
        brief = _get_attr_or_key(
            a,
            "summary", "description", "abstract", "snippet",
            default=""
        )
        if len(brief) > 400:
            brief = brief[:400] + "..."

        title = _get_attr_or_key(
            a,
            "title", "headline", "name",
            default="[No Title]"
        )

        url = _get_attr_or_key(
            a,
            "url", "link", "permalink", "href",
            default=""
        )

        source = _get_attr_or_key(
            a,
            "source", "site", "publisher", "feed", "channel",
            default=""
        )

        fallback.append({
            "title": title,
            "url": url,
            "summary": brief,
            "source": source,
            "raw": a,
        })

    logger.warning("Used fallback summaries (compatible w/ dict & Article): {}", len(fallback))
    return fallback


def _highlights(summaries: List[Dict[str, Any]]) -> List[str]:
    fn = _resolve_callable(
        "src.omnidigest.summarizer.summarizer",
        func_candidates=["generate_digest_highlights", "highlights", "gen_highlights"],
    )
    if callable(fn):
        try:
            hl = fn(summaries)  # type: ignore
            if isinstance(hl, list):
                return hl
        except Exception as e:
            logger.warning("highlight generator failed, fallback empty: {}", e)
    return []


def _compose(
    domain: str,
    summaries: List[Dict[str, Any]],
    highlights_and_impacts: List[str],
) -> Dict[str, Any]:
    """
    拼最终 HTML（高亮 + 影响 + 今日头条列表）
    不做合规/免责声明/花哨格式，保持你之前“读上去很像日报”的感觉
    """

    # 简单切分：前 5 条当 Top Signals，剩下当 Industry Impact
    top_signals = highlights_and_impacts[:5]
    impact_section = highlights_and_impacts[5:]

    def _shorten(txt: str, limit: int = 300) -> str:
        if not txt:
            return ""
        txt = txt.strip()
        if len(txt) <= limit:
            return txt
        return txt[:limit].rstrip() + "..."

    article_blocks_html: List[str] = []
    for art in summaries[:10]:  # 只拿前10条，防止邮件太长
        # art 结构来自 _summarize / summarizer
        title = art.get("title") or "(no title)"
        desc = art.get("summary") or art.get("description") or ""
        desc = _shorten(desc)
        link = art.get("url") or art.get("link") or ""
        source = art.get("source") or ""

        article_blocks_html.append(f"""
        <div style="margin-bottom:16px;">
            <div style="font-weight:600; font-size:14px; line-height:1.4;">{title}</div>
            <div style="font-size:13px; color:#444; line-height:1.4; margin:4px 0 4px 0;">
                {desc}
            </div>
            <div style="font-size:12px; color:#1a73e8;">
                <a href="{link}" target="_blank" style="color:#1a73e8;text-decoration:none;">Source ↗ {source}</a>
            </div>
        </div>
        """)

    def _bullets_html(items: List[str], heading: str) -> str:
        if not items:
            return ""
        lis = "".join(
            f'<li style="margin-bottom:6px; line-height:1.4; font-size:13px; color:#111;">{h}</li>'
            for h in items
        )
        return f"""
        <div style="margin-bottom:20px;">
            <div style="font-weight:600; font-size:14px; margin-bottom:6px;">{heading}</div>
            <ul style="padding-left:18px; margin:0;">
                {lis}
            </ul>
        </div>
        """

    html_highlights = _bullets_html(top_signals, "Top Signals")
    html_impact = _bullets_html(impact_section, "Industry Impact")

    today_str = datetime.now().strftime("%Y-%m-%d")
    header_title = f"{domain.upper()} Daily Brief · {today_str}"

    full_html = f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
                 background:#ffffff; color:#000; padding:20px; line-height:1.5;">

        <div style="font-size:16px; font-weight:600; margin-bottom:12px;">
            {header_title}
        </div>

        {html_highlights}
        {html_impact}

        <div style="font-weight:600; font-size:14px; margin:20px 0 8px;">
            Today's Headlines
        </div>
        <div>
            {''.join(article_blocks_html)}
        </div>

        <div style="font-size:11px; color:#888; margin-top:32px; border-top:1px solid #eee; padding-top:12px;">
            Generated by OmniDigest – internal preview
        </div>
    </body>
    </html>
    """

    return {
        "subject": header_title,
        "html": full_html,
        "md": "",
    }


# =============== 编排入口 ===============

def run_digest_pipeline(domain: str) -> DigestRunResult:
    settings = get_settings()
    logger.info("Run digest pipeline for domain='{}' tz='{}'", domain, settings.timezone)

    # 载入领域配置 (ai.yaml / finance.yaml ...)
    domain_cfg = settings.load_domain_config(domain)
    logger.debug("Loaded domain config keys: {}", list(domain_cfg.keys()))

    # 1) 抓取
    articles = _ingest_articles(domain_cfg)
    logger.debug("Ingested {} articles", len(articles))

    # 2) 规范化 & 去重
    articles = _normalize(articles)
    articles = _dedup(articles)
    logger.debug("After normalize/dedup: {} articles", len(articles))

    # 3) 摘要 & 高亮
    summaries = _summarize(articles)
    logger.debug("Summaries generated: {}", len(summaries))

    highlights = _highlights(summaries)
    logger.debug("Highlights generated: {}", len(highlights))

    # 4) Impact Insights（行业影响 / why it matters）
    impact_insights: List[str] = []
    try:
        from src.omnidigest.analyzer.impact_analyzer import analyze_impact
        impact_insights = analyze_impact(summaries, domain)
    except Exception as e:
        logger.warning("Impact analysis module unavailable or failed: {}", e)

    logger.debug("Impact insights generated: {}", len(impact_insights))

    # 5) 组装 HTML
    composed = _compose(
        domain,
        summaries,
        highlights + impact_insights,
    )

    subject: str = composed.get(
        "subject",
        f"OmniDigest · {domain.capitalize()} Daily · {date.today().isoformat()}",
    )
    html: str = composed.get("html", "")
    md: str = composed.get("md", "")

    if not html:
        raise RuntimeError("compose failed: no HTML content.")

    # 6) 写入 out/
    output_files = _write_outputs(domain, html=html, md=md)

    # 7) 组装收件人
    to_list = _load_recipients(domain, settings)

    # 8) 发邮件
    email_result: Optional[Dict[str, Any]] = None
    try:
        emailer = Emailer()
        email_result = emailer.send(
            to=to_list or None,        # 如果你给了空列表，它会 fallback EMAIL_TEST_TO
            subject=subject,
            html=html,
        )
        logger.info("Email send result: {}", email_result)
    except Exception as e:
        logger.exception("Email send failed: {}", e)
        email_result = {"sent": False, "reason": str(e)}

    # 9) 返回结构化结果，方便调试
    return DigestRunResult(
        output_files=output_files,
        email_result=email_result,
        meta={
            "domain": domain,
            "recipients": to_list,
            "articles": len(articles),
            "summaries": len(summaries),
            "highlights": len(highlights),
            "impact_insights": len(impact_insights),
            "has_md": bool(md),
        },
    )


__all__ = ["run_digest_pipeline", "DigestRunResult"]


# CLI 入口：允许手动触发
if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Run OmniDigest pipeline once.")
    parser.add_argument(
        "domain",
        nargs="?",
        default="ai",
        help="Which domain to run digest for (e.g. ai, finance)",
    )
    args = parser.parse_args()

    try:
        result = run_digest_pipeline(args.domain)
        print("Generated files:", getattr(result, "output_files", None))
        print("Email result:", getattr(result, "email_result", None))
    except Exception as e:
        logger.exception("Digest pipeline crashed: {}", e)
        sys.exit(1)