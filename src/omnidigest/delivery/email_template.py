# src/omnidigest/delivery/email_template.py
from __future__ import annotations
from typing import List, Dict, Any
from datetime import datetime

# 这个模板是“专业简报 + 投研风”基础版，完全行内样式，尽量兼容邮件客户端。
# 约定输入：
# - subject: str
# - top_signals: List[str]
# - impact: 可能是：
#     A) 主题分组: List[{"theme": str, "impact_cards": List[dict], "items": List[dict]}]
#     B) 卡片平铺: List[dict]
#     C) bullets: List[str]
# - headlines: List[dict]  (每项含 title/summary/url/source)
#
# 返回：HTML 字符串

def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _badge(direction: str | None) -> str:
    d = (direction or "").lower()
    m = {"positive": "↑ 正向", "negative": "↓ 负向", "neutral": "• 中性", "uncertain": "? 不确定"}
    return m.get(d, d)

def _render_top(top_signals: List[str]) -> str:
    if not top_signals:
        return ""
    lis = "".join(
        f"<li style='margin:6px 0; font-size:13px; line-height:1.5; color:#111;'>{_escape(x)}</li>"
        for x in top_signals[:5]
    )
    return (
        "<tr><td style='padding:0 0 8px 0; font-weight:600; font-size:14px;'>Top Signals</td></tr>"
        f"<tr><td><ul style='margin:0; padding-left:18px;'>{lis}</ul></td></tr>"
    )

def _render_cards(cards: List[Dict[str, Any]]) -> str:
    if not cards:
        return ""
    blocks = []
    for c in cards[:3]:
        title = _escape(c.get("title") or "Industry insight")
        impact = _escape(c.get("impact") or "")
        who = c.get("who") or []
        direction = c.get("direction")
        conf = c.get("confidence")
        tags = c.get("tags") or []
        meta_bits: List[str] = []
        if who:
            meta_bits.append("影响对象：" + ", ".join([_escape(str(w)) for w in who[:5]]))
        if direction:
            meta_bits.append(_badge(direction))
        if isinstance(conf, (int, float)):
            meta_bits.append(f"置信度：{int(round(float(conf)*100))}%")
        if tags:
            meta_bits.append("标签：" + ", ".join([_escape(str(t)) for t in tags[:6]]))
        meta = " ｜ ".join(meta_bits)
        meta_html = f"<div style='font-size:12px; color:#555; margin-top:6px;'>{meta}</div>" if meta else ""
        blocks.append(
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='border:1px solid #eee; border-radius:10px; background:#fafafa; margin:0 0 12px 0;'>"
            "<tr><td style='padding:12px;'>"
            f"<div style='font-weight:600; font-size:14px; margin-bottom:4px;'>{title}</div>"
            f"<div style='font-size:13px; color:#222; line-height:1.5;'>{impact}</div>"
            f"{meta_html}"
            "</td></tr></table>"
        )
    return "".join(blocks)

def _render_passages(items: List[Dict[str, Any]], index_map: Dict[str, int]) -> str:
    # 显示 “Passage N” → 外部原文链接（最稳妥）
    def _key(a: Dict[str, Any]) -> str:
        u = (a.get("url") or a.get("link") or "").strip().lower()
        if u:
            return u
        return (a.get("title") or "").strip().lower()

    if not items:
        return ""
    lines = []
    shown = 0
    for it in items:
        if shown >= 6:
            break
        url = (it.get("link") or it.get("url") or "").strip()
        if not url:
            continue
        k = _key(it)
        n = index_map.get(k)
        label = f"Passage {n}" if isinstance(n, int) else f"Passage {shown + 1}"
        lines.append(
            "<div style='font-size:12px; line-height:1.4; margin:2px 0;'>"
            f"• <a href='{url}' target='_blank' style='color:#1a73e8; text-decoration:none;'>{_escape(label)}</a>"
            "</div>"
        )
        shown += 1
    if not lines:
        return ""
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='border:1px dashed #e5e7eb; border-radius:8px; background:#fff; margin:6px 0 0 0;'>"
        "<tr><td style='padding:8px;'>"
        "<div style='font-weight:600; font-size:12px; color:#333; margin-bottom:4px;'>Covered Passages</div>"
        f"{''.join(lines)}"
        "</td></tr></table>"
    )

def _render_impact(impacts: Any, index_map: Dict[str, int]) -> str:
    # 主题分组
    if isinstance(impacts, list) and impacts and isinstance(impacts[0], dict) and ("theme" in impacts[0]):
        sections = []
        for g in impacts:
            theme = (g.get("theme") or "Theme").title()
            cards = g.get("impact_cards") or []
            items = g.get("items") or []
            if not cards:
                continue
            sections.append(
                "<tr><td style='padding:16px 0 0 0; font-weight:700; font-size:14px;'>" + _escape(theme) + "</td></tr>"
                f"<tr><td>{_render_cards(cards)}</td></tr>"
                f"<tr><td>{_render_passages(items, index_map)}</td></tr>"
            )
        if not sections:
            return ""
        return (
            "<tr><td style='padding:16px 0 8px 0; font-weight:600; font-size:14px;'>Industry Impact</td></tr>"
            + "".join(sections)
        )
    # 平铺卡片
    if isinstance(impacts, list) and impacts and isinstance(impacts[0], dict):
        return (
            "<tr><td style='padding:16px 0 8px 0; font-weight:600; font-size:14px;'>Industry Impact</td></tr>"
            f"<tr><td>{_render_cards(impacts)}</td></tr>"
        )
    # bullets 兜底
    if isinstance(impacts, list) and impacts and isinstance(impacts[0], str):
        lis = "".join(
            f"<li style='margin:6px 0; font-size:13px; line-height:1.5; color:#111;'>{_escape(s.strip('•-* '))}</li>"
            for s in impacts[:6]
        )
        return (
            "<tr><td style='padding:16px 0 8px 0; font-weight:600; font-size:14px;'>Industry Impact</td></tr>"
            f"<tr><td><ul style='margin:0; padding-left:18px;'>{lis}</ul></td></tr>"
        )
    return ""

def _render_headlines(headlines: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, a in enumerate(headlines, start=1):
        title = _escape(a.get("title") or "(no title)")
        desc = _escape(a.get("summary") or a.get("description") or "")
        if len(desc) > 280:
            desc = desc[:280].rstrip() + "..."
        url = (a.get("link") or a.get("url") or "").strip()
        source = _escape(a.get("source") or "")
        prefix = f"<span style='font-weight:600; font-size:12px; color:#666; margin-right:6px;'>Passage {idx}</span>"
        rows.append(
            "<tr><td style='padding:0 0 16px 0'>"
            f"<div style='font-weight:600; font-size:14px; line-height:1.4;'>{prefix}{title}</div>"
            f"<div style='font-size:13px; color:#444; line-height:1.5; margin:4px 0 4px 0;'>{desc}</div>"
            "<div style='font-size:12px;'>"
            f"<a href='{url}' target='_blank' style='color:#1a73e8; text-decoration:none;'>Source ↗ {source}</a>"
            "</div>"
            "</td></tr>"
        )
    return (
        "<tr><td style='padding:16px 0 8px 0; font-weight:600; font-size:14px;'>Today's Headlines</td></tr>"
        + "".join(rows)
    )

def render_email(
    subject: str,
    top_signals: List[str],
    impacts: Any,
    headlines: List[Dict[str, Any]],
) -> str:
    # 为 Covered Passages 计算：key -> index
    def _key(a: Dict[str, Any]) -> str:
        u = (a.get("url") or a.get("link") or "").strip().lower()
        if u:
            return u
        return (a.get("title") or "").strip().lower()

    top_headlines = headlines[:10]
    index_map = {}
    for idx, a in enumerate(top_headlines, start=1):
        index_map[_key(a)] = idx

    today = datetime.now().strftime("%Y-%m-%d")
    header = f"{subject or 'Daily Brief'}"

    html = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>" + _escape(header) + "</title></head>"
        "<body style='margin:0; padding:0; background:#f6f7f9;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#f6f7f9; padding:16px 0;'>"
        "<tr><td align='center'>"
        "<table role='presentation' width='640' cellpadding='0' cellspacing='0' style='width:640px; max-width:100%; background:#fff; border:1px solid #eee; border-radius:12px; padding:20px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'>"
        "<tr><td>"
        f"<div style='font-size:16px; font-weight:700; margin-bottom:8px;'>{_escape(header)}</div>"
        f"<div style='font-size:12px; color:#777; margin-bottom:12px;'>{today}</div>"
        "</td></tr>"
        f"{_render_top(top_signals)}"
        f"{_render_impact(impacts, index_map)}"
        f"{_render_headlines(top_headlines)}"
        "<tr><td style='border-top:1px solid #eee; padding-top:12px; font-size:11px; color:#888; text-align:left;'>"
        "Generated by OmniDigest – internal preview"
        "</td></tr>"
        "</table>"
        "</td></tr></table>"
        "</body></html>"
    )
    return html