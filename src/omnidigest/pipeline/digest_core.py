# src/omnidigest/pipeline/digest_core.py
from __future__ import annotations

import os
import importlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

from loguru import logger

from ..config.settings import get_settings
from ..delivery.emailer import Emailer, EmailMessage


# ================== 对外统一返回结构 ==================

@dataclass
class DigestCoreResult:
    html: str
    md: str
    subject: str
    output_files: List[str]
    email_result: Optional[Dict[str, Any]]
    meta: Dict[str, Any]


# ================== 基础 I/O ==================

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
    优先环境变量 RECIPIENTS_{DOMAIN}，否则走 EMAIL_TEST_TO
    """
    key = f"RECIPIENTS_{domain.upper()}"
    raw = os.getenv(key, "") or ""
    lst = [x.strip() for x in raw.split(",") if x.strip()]
    if not lst and settings.email_test_to:
        lst = [settings.email_test_to]
    return lst


# ================== 动态解析工具 ==================

def _resolve_callable(
    module_path: str,
    func_candidates: List[str] | None = None,
    class_method_candidates: List[Tuple[str, str]] | None = None,
) -> Optional[Callable | Tuple[type, str]]:
    """
    和你备份版 run_digest.py 一样的策略：解析不到别报错。
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


# ================== 兜底实现 ==================

def _fallback_ingest(feeds: List[Dict[str, Any]], max_items_per_feed: int = 20) -> List[Dict[str, Any]]:
    """极简 RSS 抓取；需要 feedparser。"""
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


def _fallback_compose_html(domain: str, summaries: List[Dict[str, Any]], highlights: List[str]) -> Dict[str, Any]:
    """
    兜底排版，防止 compose 撞到异常。
    """
    today = date.today().isoformat()
    subj = f"OmniDigest · {domain.capitalize()} Daily · {today}"

    lis = []
    for s in summaries:
        title = s.get("title") or s.get("raw", {}).get("title", "")
        url = s.get("url") or s.get("raw", {}).get("url", "")
        brief = s.get("summary") or s.get("text") or s.get("brief") or ""
        lis.append(
            f"<li style='margin:10px 0'><a href='{url}' target='_blank'>{title}</a>"
            f"<div style='font-size:12px;opacity:.8'>{brief}</div></li>"
        )
    hl_block = ""
    if highlights:
        hl_li = "".join([f"<li>{h}</li>" for h in highlights])
        hl_block = f"<h3>Highlights</h3><ul>{hl_li}</ul>"

    html = f"""
    <html>
      <body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.5;padding:12px;">
        <h2 style="margin:0 0 8px 0">{subj}</h2>
        {hl_block}
        <h3>Today’s Links</h3>
        <ol>{''.join(lis)}</ol>
        <hr/>
        <div style="font-size:12px;color:#888">Generated at {datetime.now(timezone.utc).isoformat()}</div>
      </body>
    </html>
    """
    return {"subject": subj, "html": html, "md": ""}


# ================== 各流水线环节 ==================

def _ingest_articles(domain_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
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
            ("RSSIngestor", "_ingest"),  # 兼容可能的私有实现
        ],
    )

    # 情况 A：直接函数
    if callable(resolved):
        try:
            arts = resolved(feeds)
            if isinstance(arts, list):
                return arts
            logger.warning("Project ingest returned non-list; using fallback.")
        except Exception as e:
            logger.warning("Project ingest failed, using fallback. {}", e)
        return _fallback_ingest(feeds)

    # 情况 B：类方法
    if isinstance(resolved, tuple) and len(resolved) == 2:
        klass, method_name = resolved
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

    # 情况 C：完全解析不到
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

    # fallback
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


# ================== 内容结构化拼装 ==================

def _compose(
    domain: str,
    summaries: list[dict],
    highlights: list[str],
    impacts: list,   # 可为主题分组(list[dict with 'theme']), 或 list[dict cards], 或 list[str]
) -> Dict[str, Any]:
    """
    主题分组 + 卡片化渲染（增强版）
    - Highlights：Top Signals（最多5条，若无则从主题卡片或头条智能兜底）
    - Industry Impact：优先渲染“主题分组 -> 卡片”；其次卡片平铺；最后 bullets 兜底
    - 每个主题下展示“Covered Passages”：以 Passage N（映射到 Headlines 的真实序号）为标签，链接直达外部原文
    - Headlines：保持你当前风格
    """

    # ------- 小工具 -------
    def _bullets_html(items: list[str], heading: str) -> str:
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

    def _shorten(txt: str, limit: int = 280) -> str:
        if not txt:
            return ""
        txt = txt.strip()
        if len(txt) <= limit:
            return txt
        return txt[:limit].rstrip() + "..."

    def _dir_badge(direction: str) -> str:
        d = (direction or "").lower()
        mapping = {"positive": "↑ 正向", "negative": "↓ 负向", "neutral": "• 中性", "uncertain": "? 不确定"}
        return mapping.get(d, d or "")

    def _art_key(a: dict) -> str:
        """用 URL（或标题）生成稳定键，供 Passage 索引使用。"""
        u = (a.get("url") or a.get("link") or "").strip().lower()
        if u:
            return u
        return (a.get("title") or "").strip().lower()

    # ------- Headlines：确定前 10 条，并构建 key->index 映射 -------
    headlines = summaries[:10]
    index_map: dict[str, int] = {}
    for idx, a in enumerate(headlines, start=1):
        k = _art_key(a)
        if k:
            index_map[k] = idx

    # ------- Top Signals：若 highlights 为空，做“智能兜底” -------
    if highlights:
        top_signals = highlights[:5]
    else:
        derived: list[str] = []
        # 优先从主题分组的首卡片提炼
        if isinstance(impacts, list) and impacts and isinstance(impacts[0], dict) and ("theme" in impacts[0]):
            for g in impacts:
                cards = g.get("impact_cards") or []
                if cards:
                    c0 = cards[0]
                    title = (c0.get("title") or "").strip()
                    impact_txt = (c0.get("impact") or "").strip()
                    if title:
                        derived.append(title)
                    elif impact_txt:
                        derived.append(_shorten(impact_txt, 120))
                    if len(derived) >= 5:
                        break
        # 再从 Headlines 兜底
        if not derived:
            for a in headlines[:3]:
                t = (a.get("title") or a.get("summary") or "").strip()
                if t:
                    derived.append(_shorten(t, 120))
        top_signals = derived[:5]

    html_highlights = _bullets_html(top_signals, "Top Signals") if top_signals else ""

    # ------- Industry Impact：渲染卡片 & Covered Passages -------
    def _render_cards(cards: list[dict]) -> str:
        if not cards:
            return ""
        blocks = []
        for c in cards[:3]:
            title = (c.get("title") or "Industry insight").strip()
            impact = (c.get("impact") or "").strip()
            who = c.get("who") or []
            direction = (c.get("direction") or "").strip()
            conf = c.get("confidence")
            tags = c.get("tags") or []

            meta_bits = []
            if who:
                meta_bits.append(f"影响对象：{', '.join([str(w) for w in who[:5]])}")
            if direction:
                meta_bits.append(_dir_badge(direction))
            if isinstance(conf, (int, float)):
                meta_bits.append(f"置信度：{int(round(float(conf)*100))}%")
            if tags:
                meta_bits.append(f"标签：{', '.join([str(t) for t in tags[:6]])}")

            meta_html = f"<div style='font-size:12px; color:#555; margin-top:6px;'>{' ｜ '.join(meta_bits)}</div>" if meta_bits else ""

            blocks.append(
                "<div style=\"margin-bottom:12px; padding:12px; background:#fafafa; border:1px solid #eee; border-radius:10px;\">"
                f"<div style='font-weight:600; font-size:14px; margin-bottom:4px;'>{title}</div>"
                f"<div style='font-size:13px; color:#222; line-height:1.5;'>{impact}</div>"
                f"{meta_html}"
                "</div>"
            )
        return "".join(blocks)

    def _render_passage_links(items: list[dict]) -> str:
        """
        Covered Passages（全局编号版）：
        - 标签显示为 Passage N（N = 该条在 Headlines 中的真实序号）
        - 链接跳外部原文，避免邮件内锚点不准
        - 最多展示 6 条
        """
        if not items:
            return ""
        lines: list[str] = []
        shown = 0
        for it in items:
            if shown >= 6:
                break
            url = (it.get("link") or it.get("url") or "").strip()
            if not url:
                continue
            k = _art_key(it)
            num = index_map.get(k)  # 可能为 None（不在前10 headlines 或匹配失败）
            label = f"Passage {num}" if isinstance(num, int) else f"Passage {shown + 1}"

            lines.append(
                "<div style='font-size:12px; line-height:1.4; margin:2px 0;'>"
                f"• <a href='{url}' target='_blank' style='color:#1a73e8; text-decoration:none;'>{label}</a>"
                "</div>"
            )
            shown += 1

        if not lines:
            return ""
        return (
            "<div style='margin-top:6px; padding:8px; background:#fff; border:1px dashed #e5e7eb; border-radius:8px;'>"
            "<div style='font-weight:600; font-size:12px; color:#333; margin-bottom:4px;'>Covered Passages</div>"
            f"{''.join(lines)}"
            "</div>"
        )

    def _render_grouped_impacts(groups: list[dict]) -> str:
        sections = []
        for g in groups:
            theme = (g.get("theme") or "Theme").title()
            cards = g.get("impact_cards") or []
            items = g.get("items") or []  # 该主题覆盖的 headlines
            if not cards:
                continue
            sections.append(
                "<div style='margin-bottom:18px;'>"
                f"<div style='font-weight:700; font-size:14px; margin:4px 0 8px;'>{theme}</div>"
                f"{_render_cards(cards)}"
                f"{_render_passage_links(items)}"
                "</div>"
            )
        if not sections:
            return ""
        return (
            "<div style='margin-bottom:20px;'>"
            "<div style='font-weight:600; font-size:14px; margin-bottom:6px;'>Industry Impact</div>"
            f"{''.join(sections)}"
            "</div>"
        )

    html_impact = ""
    # 1) 主题分组
    if isinstance(impacts, list) and impacts and isinstance(impacts[0], dict) and ("theme" in impacts[0]):
        html_impact = _render_grouped_impacts(impacts)
    # 2) 平铺卡片
    elif isinstance(impacts, list) and impacts and isinstance(impacts[0], dict):
        html_impact = (
            "<div style='margin-bottom:20px;'>"
            "<div style='font-weight:600; font-size:14px; margin-bottom:6px;'>Industry Impact</div>"
            f"{_render_cards(impacts)}"
            "</div>"
        )
    # 3) bullets 兜底
    elif isinstance(impacts, list) and impacts and isinstance(impacts[0], str):
        cleaned = [str(i).strip("•-* \t") for i in impacts if i]
        if len(cleaned) <= 2:
            html_impact = _bullets_html(cleaned, "Industry Impact")
        else:
            paragraphs = []
            for i in range(0, min(len(cleaned), 6), 2):
                chunk = " ".join(cleaned[i:i+2])
                paragraphs.append(
                    "<p style='margin-bottom:8px; font-size:13px; line-height:1.5; color:#222;'>"
                    f"{chunk}"
                    "</p>"
                )
            html_impact = (
                "<div style='margin-bottom:20px;'>"
                "<div style='font-weight:600; font-size:14px; margin-bottom:6px;'>Industry Impact</div>"
                f"{''.join(paragraphs)}"
                "</div>"
            )

    # ------- Headlines -------
    article_blocks_html = []
    for idx, art in enumerate(headlines, start=1):
        anchor_id = f"p{idx}"  # 仍保留 id，供未来 web 版内链
        title = art.get("title") or "(no title)"
        prefix = f"<span style='font-weight:600; font-size:12px; color:#666; margin-right:6px;'>Passage {idx}</span>"
        title_html = f"{prefix}{title}"

        desc = art.get("summary") or art.get("description") or ""
        desc = _shorten(desc)
        link = art.get("link") or art.get("url") or ""
        source = art.get("source") or ""

        article_blocks_html.append(
            "<div style='margin-bottom:16px;' id='{anchor}'>"
            "<div style='font-weight:600; font-size:14px; line-height:1.4;'>{title}</div>"
            "<div style='font-size:13px; color:#444; line-height:1.4; margin:4px 0 4px 0;'>{desc}</div>"
            "<div style='font-size:12px; color:#1a73e8;'>"
            "<a href='{link}' target='_blank' style='color:#1a73e8;text-decoration:none;'>"
            "Source ↗ {source}"
            "</a>"
            "</div>"
            "</div>"
            .format(anchor=anchor_id, title=title_html, desc=desc, link=link, source=source)
        )

    today_str = datetime.now().strftime("%Y-%m-%d")
    header_title = f"{domain.upper()} Daily Brief · {today_str}"

    full_html = (
        "<html>"
        "<body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "background:#ffffff; color:#000; padding:20px; line-height:1.5;'>"
        f"<div style='font-size:16px; font-weight:600; margin-bottom:12px;'>{header_title}</div>"
        f"{html_highlights}"
        f"{html_impact}"
        "<div style='font-weight:600; font-size:14px; margin:20px 0 8px;'>Today's Headlines</div>"
        f"<div>{''.join(article_blocks_html)}</div>"
        "<div style='font-size:11px; color:#888; margin-top:32px; border-top:1px solid #eee; padding-top:12px;'>"
        "Generated by OmniDigest – internal preview"
        "</div>"
        "</body>"
        "</html>"
    )

    return {"html": full_html, "subject": header_title, "md": ""}


# ================== 真正统一入口 ==================

def run_digest_core(
    domain: str,
    *,
    write_outputs: bool = True,
    send_email: bool = True,
    explicit_recipients: Optional[List[str]] = None,
) -> DigestCoreResult:
    settings = get_settings()
    logger.info("Run digest CORE for domain='{}' tz='{}'", domain, settings.timezone)

    # 领域配置
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

    # 4) Impact Insights
    impact_insights: List[str] = []
    try:
        from src.omnidigest.analyzer.impact_analyzer import analyze_impact
        impact_insights = analyze_impact(summaries, domain)
    except Exception as e:
        logger.warning("Impact analysis module unavailable or failed: {}", e)

    logger.debug("Impact insights generated: {}", len(impact_insights))

    # 5) 组装
    composed = _compose(domain, summaries, highlights, impact_insights)
    subject: str = composed.get(
        "subject",
        f"OmniDigest · {domain.capitalize()} Daily · {date.today().isoformat()}",
    )
    html: str = composed.get("html", "")
    md: str = composed.get("md", "")

    if not html:
        fb = _fallback_compose_html(domain, summaries, highlights)
        subject = fb["subject"]
        html = fb["html"]
        md = fb["md"]

    # 6) 写文件
    output_files: List[str] = []
    if write_outputs:
        output_files = _write_outputs(domain, html=html, md=md)

    # 7) 收件人
    recipients = explicit_recipients
    if recipients is None:
        recipients = _load_recipients(domain, settings)

    # 8) 发邮件
    email_result: Optional[Dict[str, Any]] = None
    if send_email:
        try:
            msg = EmailMessage(
                to=recipients or None,
                subject=subject,
                body_html=html,
            )
            email_result = Emailer().send(msg)
            logger.info("Email send result: {}", email_result)
        except Exception as e:
            logger.exception("Email send failed: {}", e)
            email_result = {"sent": False, "reason": str(e)}

    return DigestCoreResult(
        html=html,
        md=md,
        subject=subject,
        output_files=output_files,
        email_result=email_result,
        meta={
            "domain": domain,
            "recipients": recipients,
            "articles": len(articles),
            "summaries": len(summaries),
            "highlights": len(highlights),
            "impact_insights": len(impact_insights),
            "has_md": bool(md),
        },
    )