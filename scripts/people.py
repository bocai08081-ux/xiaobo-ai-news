"""People section.

Posts are distilled from the same collected articles when the title mentions
someone on the watchlist. Nothing is invented to fill an empty day. Pinned
posts already stored in people.json are kept.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from briefing import CATEGORY_LABEL, assign_category, cjk_count, narrate
from fetch_news import beijing_now, format_beijing_stamp, parse_datetime

PEOPLE = (
    {
        "id": "tibo",
        "name": "Tibo",
        "handle": "thsottiaux",
        "initial": "T",
        "patterns": (r"thsottiaux", r"\bTibo\b"),
    },
    {
        "id": "musk",
        "name": "马斯克",
        "handle": "elonmusk",
        "initial": "马",
        "patterns": (r"马斯克", r"Elon\s+Musk", r"\bMusk\b", r"elonmusk"),
    },
    {
        "id": "trump",
        "name": "特朗普",
        "handle": "realDonaldTrump",
        "initial": "特",
        "patterns": (r"特朗普", r"\bTrump\b", r"realDonaldTrump"),
    },
)

_SOCIAL_HOST = re.compile(r"(?:^|\.)((?:twitter|x)\.com)$", re.I)


def match_person(item: dict) -> dict | None:
    blob = " ".join(
        part for part in (item.get("title"), item.get("summary"), item.get("url")) if part
    )
    for person in PEOPLE:
        if any(re.search(pattern, blob, flags=re.I) for pattern in person["patterns"]):
            return person
    return None


def time_label(item: dict) -> str:
    if item.get("snapshot"):
        return "时间待核实"
    published = parse_datetime(item.get("publishedAt"))
    if published is None:
        return "时间待核实"
    return format_beijing_stamp(published)


def link_label(url: str) -> str:
    host = ""
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0].lower()
        if host.startswith("www."):
            host = host[4:]
    if _SOCIAL_HOST.search(host):
        return "查看原帖"
    return "查看原文"


def tags_for(item: dict, social: bool) -> list[str]:
    tags = [CATEGORY_LABEL[assign_category(item)]]
    if item.get("category") == "official":
        tags.append("官方")
    elif social:
        tags.append("原帖")
    else:
        tags.append("转述")
    return tags


def post_from_item(item: dict, person: dict) -> dict | None:
    url = (item.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    category = assign_category(item)
    text = narrate(item, category)
    title = text["title"]
    if cjk_count(title) < 8:
        title = f"{person['name']}有一则新动态，来自{item.get('source') or '公开报道'}"
    social = link_label(url) == "查看原帖"
    source = item.get("source") or "公开来源"
    if social:
        context = "这条链到本人的公开帖子。帖子本身可能很短，卡片里的说明是编辑补上的上下文，不是帖子原文。"
        source_note = f"原帖来自 @{person['handle']}。卡片标题和说明是中文整理，时间已换算成北京时间。"
    else:
        context = f"上文来自{source}的公开报道，不是 @{person['handle']} 的原帖。转述会漏掉语气和后续回复，不能单独当成本人发言。"
        source_note = f"来源是{source}。只因为标题或摘要提到了{person['name']}，才收进人物动态。"
    return {
        "id": item.get("id") or "",
        "personId": person["id"],
        "title": title,
        "summary": text["summary"],
        "context": context,
        "sourceNote": source_note,
        "sourceName": source,
        "sourceUrl": url,
        "linkLabel": link_label(url),
        "timeLabel": time_label(item),
        "publishedAt": item.get("publishedAt") or "",
        "tags": tags_for(item, social),
        "pinned": False,
    }


def posts_from_items(items: list[dict], *, limit: int = 8) -> list[dict]:
    found = []
    seen: set[str] = set()
    ordered = sorted(items, key=lambda row: row.get("publishedAt") or "", reverse=True)
    for item in ordered:
        person = match_person(item)
        if person is None:
            continue
        url = item.get("url") or ""
        if url in seen:
            continue
        post = post_from_item(item, person)
        if post is None:
            continue
        seen.add(url)
        found.append(post)
        if len(found) >= limit:
            break
    return found


def _load_pinned(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        return []
    pinned = []
    for row in posts:
        if isinstance(row, dict) and row.get("pinned") and row.get("sourceUrl") and row.get("personId"):
            pinned.append(row)
    return pinned


def publish_people(items: list[dict], now: datetime | None = None, path: Path | None = None) -> dict:
    current = now or beijing_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target = path or (Path(__file__).resolve().parents[1] / "site" / "data" / "people.json")
    pinned = _load_pinned(target)
    pinned_urls = {row.get("sourceUrl") for row in pinned}
    generated = [post for post in posts_from_items(items) if post["sourceUrl"] not in pinned_urls]
    posts = pinned + generated
    payload = {
        "timezone": "Asia/Shanghai",
        "updatedAt": current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updatedLabel": format_beijing_stamp(current),
        "people": [
            {"id": person["id"], "name": person["name"], "handle": person["handle"], "initial": person["initial"]}
            for person in PEOPLE
        ],
        "posts": posts,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] 人物动态 {len(posts)} 条", file=sys.stderr)
    return payload
