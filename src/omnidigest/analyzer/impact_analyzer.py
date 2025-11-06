# src/omnidigest/analyzer/impact_analyzer.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from collections import defaultdict, Counter
from urllib.parse import urlparse
from loguru import logger

# 配置读取
try:
    from ..config.settings import get_settings
except Exception:
    # 兼容相对导入失败的情况
    from src.omnidigest.config.settings import get_settings  # type: ignore

# LLM（可选）
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception as e:
    logger.warning("OpenAI SDK not available or import failed: {}", e)
    _HAS_OPENAI = False


# ---------------- 工具 ----------------

def _hostname(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""

def _normalize_tags(tags: Any) -> List[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        return [t.strip().lower() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip().lower() for t in tags if str(t).strip()]
    return []

def _coerce_cards(raw: Any) -> List[Dict[str, Any]]:
    """
    将 LLM 的输出（可能是 JSON 数组/对象/或文本 bullets）规整为 List[dict] 的“洞察卡片”
    目标 schema：
      {
        "title": str,
        "impact": str,
        "who": List[str],
        "direction": "positive|negative|neutral|uncertain",
        "confidence": 0.0-1.0,
        "tags": List[str]
      }
    """
    def _as_card(d: Dict[str, Any]) -> Dict[str, Any]:
        title = str(d.get("title") or d.get("headline") or d.get("theme") or "").strip()
        impact = str(d.get("impact") or d.get("analysis") or d.get("why") or "").strip()
        who = d.get("who") or d.get("entities") or d.get("companies") or []
        direction = (d.get("direction") or d.get("polarity") or d.get("sentiment") or "").strip().lower()
        confidence = d.get("confidence")
        tags = _normalize_tags(d.get("tags") or d.get("dimensions"))

        if isinstance(who, str):
            who = [w.strip() for w in who.split(",") if w.strip()]
        if isinstance(confidence, str):
            try:
                confidence = confidence.strip()
                if confidence.endswith("%"):
                    confidence = float(confidence[:-1]) / 100.0
                else:
                    confidence = float(confidence)
            except Exception:
                confidence = None
        if isinstance(confidence, int):
            confidence = max(0, min(100, confidence)) / 100.0
        if not title and impact:
            title = (impact[:80] + "…") if len(impact) > 80 else impact
        return {
            "title": title or "Industry insight",
            "impact": impact,
            "who": who if isinstance(who, list) else [],
            "direction": direction or "",
            "confidence": confidence if isinstance(confidence, (int, float)) else None,
            "tags": tags,
        }

    # 快速路径：list[dict]
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return [_as_card(x) for x in raw]

    # dict
    if isinstance(raw, dict):
        return [_as_card(raw)]

    # 文本 bullets（降级）
    if isinstance(raw, str):
        lines = [ln.strip("•-* \t") for ln in raw.splitlines() if ln.strip()]
        cards = []
        for s in lines:
            if len(s) < 3:
                continue
            cards.append({
                "title": s[:80] + ("…" if len(s) > 80 else ""),
                "impact": s,
                "who": [], "direction": "", "confidence": None, "tags": []
            })
        return cards

    return []


def _extract_json(text: str) -> List[Any]:
    import json, re
    text = (text or "").strip()
    blocks = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates = blocks if blocks else [text]
    out: List[Any] = []
    for blob in candidates:
        try:
            data = json.loads(blob)
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)
        except Exception:
            continue
    return out


# ---------------- 主题分组（基于 feeds tags + 关键词补齐） ----------------

def _build_domain_tag_index(domain_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    从 domain 配置构建：host -> tags 的索引
    ai.yaml 示例：
      feeds:
        - url: https://openai.com/research/rss.xml
          tags: [openai, models, research]
    """
    index: Dict[str, List[str]] = {}
    for f in domain_cfg.get("feeds", []):
        url = f.get("url") or ""
        host = _hostname(url)
        if not host:
            continue
        tags = _normalize_tags(f.get("tags"))
        index[host] = tags
    return index

def _tag_article(summary: Dict[str, Any], host2tags: Dict[str, List[str]], keywords: List[str]) -> List[str]:
    """
    给摘要打标签：
    1) 由 URL 域名映射 feeds tags（主路径）
    2) keywords 匹配（补充路径）
    """
    tags: List[str] = []
    url = summary.get("url") or summary.get("link") or ""
    host = _hostname(url)
    if host and host in host2tags:
        tags.extend(host2tags[host])

    text = (summary.get("title") or "") + " " + (summary.get("summary") or "")
    text_l = text.lower()
    for kw in keywords:
        kw_l = kw.lower().strip()
        if kw_l and kw_l in text_l:
            tags.append(kw_l)

    # 去重
    uniq = []
    seen = set()
    for t in tags:
        if t not in seen:
            uniq.append(t)
            seen.add(t)
    return uniq

def _group_by_theme(summaries: List[Dict[str, Any]], domain_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    输出主题块：
      [
        {"theme": "models", "items": [summary,...]},
        ...
      ]
    规则：
    - 先由 host->tags + keywords 打标签
    - 统计标签频次，选出 2~4 个主题（高频优先）
    - 将打到这些主题标签的文章归入相应主题（每篇可进多个主题，但我们去重）
    - 若没有足够标签，则生成 1 个 "general" 主题
    """
    host2tags = _build_domain_tag_index(domain_cfg)
    keywords = domain_cfg.get("analysis", {}).get("keywords", []) or []

    tagged_items: List[Tuple[Dict[str, Any], List[str]]] = []
    tag_counter: Counter = Counter()

    for s in summaries:
        tags = _tag_article(s, host2tags, keywords)
        if not tags:
            continue
        tagged_items.append((s, tags))
        tag_counter.update(tags)

    if not tag_counter:
        # 实在没有标签，就做一个总主题
        return [{"theme": "general", "items": summaries[:10]}]

    # 选出 2~4 个主题（出现频次高的）
    top_tags = [t for t, _ in tag_counter.most_common(6)]
    # 小清洗：把非常相近的词做个简单合并（可按需扩展）
    alias = {
        "ai": "ai",
        "models": "models",
        "model": "models",
        "research": "research",
        "openai": "openai",
        "anthropic": "anthropic",
        "deepmind": "deepmind",
        "startup": "startup",
        "innovation": "innovation",
    }
    normalized = []
    seen = set()
    for t in top_tags:
        t2 = alias.get(t, t)
        if t2 not in seen:
            normalized.append(t2)
            seen.add(t2)
    # 控制 2~4
    if len(normalized) < 2:
        themes = normalized
    else:
        themes = normalized[:4]

    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in themes}
    for s, tags in tagged_items:
        for t in tags:
            t2 = alias.get(t, t)
            if t2 in buckets:
                buckets[t2].append(s)

    # 去重并限量（每主题最多 6 条作为 LLM 输入，展示时不限）
    out: List[Dict[str, Any]] = []
    for theme in themes if themes else ["general"]:
        items = buckets.get(theme, [])
        uniq = []
        seen_urls = set()
        for it in items:
            u = (it.get("url") or it.get("link") or "").strip()
            if not u:
                key = (it.get("title") or "").strip().lower()
            else:
                key = u.lower()
            if key and key not in seen_urls:
                seen_urls.add(key)
                uniq.append(it)
        out.append({"theme": theme, "items": uniq[:6]})
    return out


# ---------------- LLM 生成卡片 ----------------

def _llm_cards_for_theme(theme: str, items: List[Dict[str, Any]], domain: str) -> List[Dict[str, Any]] | List[str]:
    """
    对单个主题块生成 2–3 张结构化洞察卡片
    """
    if not _HAS_OPENAI or not items:
        return []
    try:
        headlines = []
        for s in items:
            title = s.get("title") or ""
            brief = s.get("summary") or ""
            if brief and len(brief) > 180:
                brief = brief[:180] + "..."
            if title or brief:
                headlines.append({"title": title, "brief": brief})

        system = f"You are an industry analyst for {domain}. Output JSON only."
        user = f"""
Theme: {theme}

These are today's items (title + brief):
{headlines}

Task:
Synthesize 2-3 concise "impact cards" in STRICT JSON (array). Schema:
{{
  "title": "≤12 words insight headline",
  "impact": "why this matters (≤2 sentences) — focus on cost/competition/regulation/infra/demand",
  "who": ["affected companies/segments"],
  "direction": "positive|negative|neutral|uncertain",
  "confidence": 0.0-1.0,
  "tags": ["short tags like 'models','infra','pricing'"]
}}

Return ONLY a JSON array. No extra text.
"""

        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.5,
            max_tokens=700,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _extract_json(raw)
        cards = _coerce_cards(data)
        if cards:
            return cards[:3]
        # 降级：按行 bullets
        if raw:
            return _coerce_cards(raw)[:3]
        return []
    except Exception as e:
        logger.warning("LLM impact cards failed for theme '{}': {}", theme, e)
        return []


# ---------------- 主入口 ----------------

def analyze_impact(summaries: List[Dict[str, Any]], domain: str = "ai") -> List[Dict[str, Any]]:
    """
    返回“主题分组版”的 Industry Impact：
    [
      {
        "theme": "models",
        "items": [ ... summaries used ... ],
        "impact_cards": [ {title,impact,who,direction,confidence,tags}, ... ]  # 或空
      },
      ...
    ]
    失败或不可用时返回 []，不影响主流程。
    """
    if not summaries:
        return []

    settings = get_settings()
    domain_cfg = {}
    try:
        domain_cfg = settings.load_domain_config(domain)
    except Exception as e:
        logger.warning("load_domain_config({}) failed: {}", domain, e)
        domain_cfg = {"feeds": [], "analysis": {}}

    # 1) 主题分组（基于 feeds tags + keywords）
    themed_blocks = _group_by_theme(summaries[:15], domain_cfg)

    # 2) 针对每个主题生成卡片（LLM 可用才做）
    for blk in themed_blocks:
        theme = blk.get("theme") or "general"
        items = blk.get("items") or []
        cards = _llm_cards_for_theme(theme, items, domain)
        blk["impact_cards"] = cards if cards else []

    # 控制主题数（2–4）
    if len(themed_blocks) >= 5:
        themed_blocks = themed_blocks[:4]
    elif len(themed_blocks) == 1 and themed_blocks[0].get("theme") == "general" and len(themed_blocks[0].get("items") or []) == 0:
        return []

    logger.info("Impact themes generated: {}", [b.get("theme") for b in themed_blocks])
    return themed_blocks
