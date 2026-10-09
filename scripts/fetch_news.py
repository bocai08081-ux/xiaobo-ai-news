#!/usr/bin/env python3
"""Build site/data/news.json for 小波AI资讯站.

Each source is isolated: a failure is recorded and the rest of the build
continues. No API keys are required.

Optional Chinese summaries (skipped unless configured):

  SUMMARY_API_KEY    secret for an OpenAI-compatible chat API
  SUMMARY_API_BASE   default https://api.openai.com/v1
  SUMMARY_MODEL      default gpt-4o-mini

When the key is absent, items keep their original excerpt and summaryZh is null.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "site" / "data" / "news.json"
WINDOW_HOURS = 72
SHANGHAI = ZoneInfo("Asia/Shanghai")
BOT_UA = "xiaobo-ai-news/1.0 (+https://github.com/bocai08081-ux/xiaobo-ai-news)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MAX_BYTES = 5_000_000

# Higher number wins when two items share a canonical URL or title.
PRIORITY = {
    "official": 80,
    "local": 70,
    "cn": 60,
    "media": 50,
    "video": 40,
    "papers": 35,
    "github": 30,
    "models": 25,
    "hn": 20,
}

CN_HINT = re.compile(
    r"AI|AIGC|GPT|LLM|Agent|Claude|Gemini|DeepSeek|OpenAI|Anthropic|Qwen|Sora|"
    r"人工智能|大模型|模型|智能体|智能|机器人|具身|多模态|生成式|开源|芯片|算力|"
    r"英伟达|扩散|训练|推理|算法|论文|编程|代码|数学|谷歌|字节|清华|自动驾驶|智驾|触觉|机器学习",
    re.I,
)

# General tech sites. Latin keywords need a boundary so "Wayfair" does not match "AI".
CN_AI = re.compile(
    r"(?:^|[^A-Za-z])("
    r"A\.?I\.?|AIGC|GPT|LLMs?|ChatGPT|Claude|Gemini|DeepSeek|OpenAI|Anthropic|"
    r"Qwen|Sora|Copilot|Midjourney|Kimi|Manus|Agents?"
    r")(?:[^A-Za-z]|$)|"
    r"人工智能|大模型|智能体|具身智能|多模态|生成式|机器学习|世界模型|基础模型|"
    r"英伟达|智驾|自动驾驶|机器人",
    re.I,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

AI_HINT = re.compile(
    r"(?:^|[^a-z0-9])("
    r"a\.?i\.?|llm|llms|gpt|chatgpt|claude|gemini|llama|mistral|qwen|deepseek|"
    r"diffusion|transformer|machine[- ]learning|deep[- ]learning|neural|"
    r"huggingface|hugging face|openai|anthropic|ollama|mlx|inference|generative|"
    r"multimodal|whisper|embedding|fine[- ]tun|gguf|langchain|langgraph|"
    r"agentic|vlm|stable[- ]diffusion|text[- ]to[- ]image|llm\.cpp|llama\.cpp|"
    r"\brag\b|vector|cuda|rocm|gemma|phi-?\d|sora|midjourney"
    r")(?:[^a-z0-9]|$)",
    re.I,
)

TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
}


class FetchError(Exception):
    pass


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", unescape(value).strip())
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    cleaned = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=SHANGHAI)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def html_to_text(value: str | None, limit: int = 280) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urllib.parse.urlsplit(raw)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        video_id = parts.path.strip("/")
        if video_id:
            return f"https://youtube.com/watch?v={video_id}"
    path = parts.path.rstrip("/")
    query = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in TRACKING_KEYS
    ]
    if host in {"youtube.com", "m.youtube.com"} and path in {"/watch", "/shorts"}:
        for key, val in query:
            if key == "v" and val:
                return f"https://youtube.com/watch?v={val}"
    query.sort()
    return urllib.parse.urlunsplit((parts.scheme.lower() or "https", host, path, urllib.parse.urlencode(query), ""))


def normalized_title(title: str) -> str:
    text = unescape(title or "").lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)
    return text.strip()


def item_id(url: str, title: str) -> str:
    seed = canonical_url(url) or normalized_title(title)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def make_item(
    *,
    title: str,
    url: str,
    source: str,
    category: str,
    published_at: datetime,
    summary: str = "",
    meta: str = "",
    snapshot: bool = False,
) -> dict | None:
    title = html_to_text(title, 240)
    url = (url or "").strip()
    if not title or not url.startswith(("http://", "https://")):
        return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return {
        "id": item_id(url, title),
        "title": title,
        "url": url,
        "source": source,
        "category": category,
        "publishedAt": isoformat(published_at),
        "summary": html_to_text(summary, 280),
        "summaryZh": None,
        "meta": html_to_text(meta, 120),
        "snapshot": snapshot,
        "lang": detect_lang(title),
    }


def in_window(published_at: datetime, now: datetime, hours: int = WINDOW_HOURS) -> bool:
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    start = now - timedelta(hours=hours)
    end = now + timedelta(hours=6)
    return start <= published_at <= end


def http_get(url: str, *, timeout: int = 25, browser: bool = False, retries: int = 1) -> bytes:
    headers = {
        "User-Agent": BROWSER_UA if browser else BOT_UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise FetchError(f"响应超过 {MAX_BYTES // 1_000_000}MB，已中止")
                if data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403, 404, 410}:
                raise FetchError(f"HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, gzip.BadGzipFile, FetchError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(1.1)
    if isinstance(last, FetchError):
        raise last
    raise FetchError(str(last or "请求失败"))


def child_text(el: ET.Element, name: str) -> str:
    for child in list(el):
        if local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def entry_link(el: ET.Element) -> str:
    alternate = ""
    fallback = ""
    for child in list(el):
        if local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        text = (child.text or "").strip()
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate" and not alternate:
            alternate = href
        elif href and not fallback:
            fallback = href
        if text.startswith("http"):
            return text
    return alternate or fallback


def entry_time(el: ET.Element) -> datetime | None:
    for name in ("published", "updated", "pubDate", "date", "created"):
        found = parse_datetime(child_text(el, name))
        if found:
            return found
    return None


def parse_feed(xml_bytes: bytes) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FetchError(f"无法解析订阅源：{exc}") from exc
    entries = []
    for el in root.iter():
        if local_name(el.tag) not in {"item", "entry"}:
            continue
        title = child_text(el, "title")
        link = entry_link(el)
        published = entry_time(el)
        summary = child_text(el, "summary") or child_text(el, "description") or child_text(el, "content")
        if title and link and published:
            entries.append(
                {
                    "title": title,
                    "url": link,
                    "published_at": published,
                    "summary": summary,
                }
            )
    if not entries:
        head = xml_bytes[:180].lstrip().lower()
        if not head.startswith(b"<?xml") and b"<rss" not in head and b"<feed" not in head:
            raise FetchError("返回的不是 RSS/Atom")
        raise FetchError("订阅源里没有可解析的条目")
    return entries


def feed_items(url: str, source: str, category: str, now: datetime, *, browser: bool = False, cap: int = 12) -> list[dict]:
    payload = http_get(url, browser=browser)
    items = []
    for entry in parse_feed(payload):
        if not in_window(entry["published_at"], now):
            continue
        built = make_item(
            title=entry["title"],
            url=entry["url"],
            source=source,
            category=category,
            published_at=entry["published_at"],
            summary=entry["summary"],
        )
        if built:
            items.append(built)
    items.sort(key=lambda row: row["publishedAt"], reverse=True)
    return items[:cap]


def looks_like_ai(*parts: str) -> bool:
    blob = " ".join(part for part in parts if part)
    return bool(AI_HINT.search(blob))


def detect_lang(title: str) -> str:
    """zh when the title is meaningfully Chinese, including mixed product names."""
    text = title or ""
    cjk = len(_CJK_RE.findall(text))
    if cjk >= 8:
        return "zh"
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk >= 4 and cjk >= latin * 0.25:
        return "zh"
    return "en"


def cn_ai_text(title: str) -> bool:
    return bool(CN_AI.search(title or ""))


def accept_simon(title: str, summary: str = "") -> bool:
    if (title or "").strip().lower().startswith("quoting "):
        return False
    return looks_like_ai(title, summary)


def fetch_hn(now: datetime) -> list[dict]:
    cutoff = int((now - timedelta(hours=WINDOW_HOURS)).timestamp())
    queries = [
        "LLM",
        "GPT",
        "ChatGPT",
        "Claude",
        "Gemini",
        "OpenAI",
        "Anthropic",
        "DeepSeek",
        "Qwen",
        '"machine learning"',
        '"language model"',
        '"diffusion model"',
        "llama.cpp",
        '"Hugging Face"',
        '"AI agent"',
    ]
    found: dict[str, dict] = {}
    errors = 0
    for query in queries:
        params = urllib.parse.urlencode(
            {
                "tags": "story",
                "query": query,
                "hitsPerPage": 20,
                "numericFilters": f"created_at_i>{cutoff},points>=2",
            }
        )
        url = f"https://hn.algolia.com/api/v1/search_by_date?{params}"
        try:
            payload = json.loads(http_get(url).decode("utf-8", "replace"))
        except (FetchError, json.JSONDecodeError):
            errors += 1
            continue
        for hit in payload.get("hits") or []:
            title = hit.get("title") or ""
            # Match the title only. Story text often mentions "AI" in passing
            # and would pull in unrelated Show HN posts.
            if not looks_like_ai(title):
                continue
            object_id = hit.get("objectID")
            external = hit.get("url") or ""
            link = external or (f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
            published = parse_datetime(hit.get("created_at"))
            if not published or not in_window(published, now):
                continue
            points = hit.get("points") or 0
            comments = hit.get("num_comments") or 0
            built = make_item(
                title=title,
                url=link,
                source="Hacker News",
                category="hn",
                published_at=published,
                summary=hit.get("story_text") or "",
                meta=f"{points} 分 · {comments} 评论",
            )
            if not built:
                continue
            key = canonical_url(built["url"])
            previous = found.get(key)
            if previous is None or (hit.get("points") or 0) > _points(previous):
                found[key] = built
    if not found and errors == len(queries):
        raise FetchError("Algolia 查询全部失败")
    items = list(found.values())
    items.sort(key=lambda row: row["publishedAt"], reverse=True)
    return items[:40]


def _points(item: dict) -> int:
    match = re.search(r"(\d+)", item.get("meta") or "")
    return int(match.group(1)) if match else 0


def fetch_hf_papers(now: datetime) -> list[dict]:
    dates = [(now.astimezone(SHANGHAI) - timedelta(days=offset)).date().isoformat() for offset in range(4)]
    seen: set[str] = set()
    items = []
    failures = 0
    for day in dates:
        url = f"https://huggingface.co/api/daily_papers?date={urllib.parse.quote(day)}"
        try:
            payload = json.loads(http_get(url).decode("utf-8", "replace"))
        except (FetchError, json.JSONDecodeError):
            failures += 1
            continue
        if not isinstance(payload, list):
            failures += 1
            continue
        for row in payload:
            paper = row.get("paper") or {}
            paper_id = paper.get("id") or ""
            if not paper_id or paper_id in seen:
                continue
            seen.add(paper_id)
            published = parse_datetime(row.get("publishedAt") or paper.get("publishedAt"))
            if published is None:
                published = datetime.fromisoformat(day).replace(tzinfo=SHANGHAI)
            if not in_window(published, now):
                continue
            upvotes = paper.get("upvotes")
            meta = f"{upvotes} 赞" if isinstance(upvotes, int) else ""
            built = make_item(
                title=row.get("title") or paper.get("title") or paper_id,
                url=f"https://huggingface.co/papers/{paper_id}",
                source="Hugging Face 论文",
                category="papers",
                published_at=published,
                summary=row.get("summary") or paper.get("summary") or "",
                meta=meta,
            )
            if built:
                items.append(built)
    if not items and failures == len(dates):
        raise FetchError("每日论文接口全部失败")
    items.sort(key=lambda row: row["publishedAt"], reverse=True)
    return items[:25]


def compact_count(value: int | None) -> str:
    if not isinstance(value, int):
        return ""
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f} 亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f} 万"
    return str(value)


def fetch_hf_models(now: datetime) -> list[dict]:
    url = "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=20"
    payload = json.loads(http_get(url).decode("utf-8", "replace"))
    if not isinstance(payload, list):
        raise FetchError("模型接口返回了意外的数据")
    items = []
    for row in payload:
        model_id = row.get("id") or row.get("modelId")
        if not model_id:
            continue
        likes = compact_count(row.get("likes"))
        downloads = compact_count(row.get("downloads"))
        bits = []
        if likes:
            bits.append(f"{likes}赞")
        if downloads:
            bits.append(f"{downloads}次下载")
        pipeline = row.get("pipeline_tag") or ""
        if pipeline:
            bits.append(pipeline)
        published = parse_datetime(row.get("createdAt")) or now
        built = make_item(
            title=model_id,
            url=f"https://huggingface.co/{model_id}",
            source="Hugging Face 模型",
            category="models",
            published_at=published,
            meta=" · ".join(bits),
            snapshot=True,
        )
        if built:
            items.append(built)
        if len(items) >= 12:
            break
    if not items:
        raise FetchError("没有解析到热门模型")
    return items


def parse_trending_page(html: str) -> list[dict]:
    repos = []
    for article in re.findall(r'<article class="Box-row">(.*?)</article>', html, flags=re.S):
        href_match = re.search(r'<h2[^>]*>.*?<a[^>]+href="(/[^"]+)"', article, flags=re.S)
        if not href_match:
            continue
        path = href_match.group(1).split("?")[0].strip("/")
        if path.count("/") != 1:
            continue
        desc_match = re.search(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', article, flags=re.S)
        description = html_to_text(desc_match.group(1), 240) if desc_match else ""
        stars_match = re.search(r"([0-9,]+)\s+stars today", article)
        lang_match = re.search(r'itemprop="programmingLanguage"[^>]*>([^<]+)', article)
        repos.append(
            {
                "name": path,
                "url": f"https://github.com/{path}",
                "description": description,
                "stars": stars_match.group(1) if stars_match else "",
                "language": html_to_text(lang_match.group(1), 40) if lang_match else "",
            }
        )
    return repos


def fetch_github_trending(now: datetime) -> list[dict]:
    pages = [
        "https://github.com/trending?since=daily",
        "https://github.com/trending/python?since=daily",
        "https://github.com/trending/jupyter-notebook?since=daily",
        "https://github.com/trending/typescript?since=daily",
        "https://github.com/trending/rust?since=daily",
        "https://github.com/trending/c%2B%2B?since=daily",
    ]
    seen: set[str] = set()
    items = []
    parsed_pages = 0
    failures = 0
    for page in pages:
        try:
            html = http_get(page, browser=True).decode("utf-8", "replace")
        except FetchError:
            failures += 1
            continue
        repos = parse_trending_page(html)
        if not repos:
            failures += 1
            continue
        parsed_pages += 1
        for repo in repos:
            if repo["name"].lower() in seen:
                continue
            if not looks_like_ai(repo["name"], repo["description"]):
                continue
            seen.add(repo["name"].lower())
            meta_bits = []
            if repo["stars"]:
                meta_bits.append(f"今日 {repo['stars']} star")
            if repo["language"]:
                meta_bits.append(repo["language"])
            built = make_item(
                title=repo["name"],
                url=repo["url"],
                source="GitHub Trending",
                category="github",
                published_at=now,
                summary=repo["description"],
                meta=" · ".join(meta_bits) or "今日热榜",
                snapshot=True,
            )
            if built:
                items.append(built)
    if parsed_pages == 0:
        raise FetchError("热榜页面无法解析" if failures else "热榜页面为空")
    return items[:15]


def fetch_youtube(channel_id: str, source: str, now: datetime) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    return feed_items(url, source, "video", now, cap=8)


def fetch_release_feed(url: str, source: str, now: datetime, *, title_prefix: str = "") -> list[dict]:
    payload = http_get(url)
    items = []
    for entry in parse_feed(payload):
        if not in_window(entry["published_at"], now):
            continue
        title = entry["title"]
        if title_prefix and title_prefix.lower() not in title.lower():
            title = f"{title_prefix} {title}"
        built = make_item(
            title=title,
            url=entry["url"],
            source=source,
            category="local",
            published_at=entry["published_at"],
            summary=entry["summary"],
        )
        if built:
            items.append(built)
    items.sort(key=lambda row: row["publishedAt"], reverse=True)
    return items[:5]


def parse_lm_studio_releases(html: str) -> list[tuple[str, str, str]]:
    """Return (version, iso timestamp, absolute url) from the changelog page."""
    normalized = html.replace('\\"', '"')
    pairs = re.findall(r'"version":"([^"]+)","build":\d+,"releaseDateIso":"([^"]+)"', normalized)
    links = re.findall(r'href="(/changelog/[^"]+)"', html)
    releases = []
    for version, stamp in pairs:
        needle = f"v{version}"
        href = next((link for link in links if link.rstrip("/").endswith(needle)), "")
        url = f"https://lmstudio.ai{href}" if href else "https://lmstudio.ai/changelog"
        releases.append((version, stamp, url))
    return releases


def fetch_lm_studio(now: datetime) -> list[dict]:
    html = http_get("https://lmstudio.ai/changelog", browser=True).decode("utf-8", "replace")
    pairs = parse_lm_studio_releases(html)
    if not pairs:
        raise FetchError("更新日志页面没有解析到版本")
    items = []
    for version, stamp, url in pairs:
        published = parse_datetime(stamp)
        if not published or not in_window(published, now):
            continue
        built = make_item(
            title=f"LM Studio {version}",
            url=url,
            source="LM Studio",
            category="local",
            published_at=published,
        )
        if built:
            items.append(built)
    return items[:5]


def fetch_anthropic(now: datetime) -> list[dict]:
    html = http_get("https://www.anthropic.com/news", browser=True).decode("utf-8", "replace")
    anchors = re.findall(r'<a[^>]+href="(/news/[^"]+)"[^>]*>(.*?)</a>', html, flags=re.S)
    seen: set[str] = set()
    items = []
    for href, inner in anchors:
        if href in seen:
            continue
        seen.add(href)
        time_match = re.search(r"<time[^>]*>([^<]+)</time>", inner)
        title_match = re.search(r"<h4[^>]*>(.*?)</h4>", inner, flags=re.S)
        if not title_match:
            title_match = re.search(r'__title[^"]*"[^>]*>(.*?)</span>', inner, flags=re.S)
        if not time_match or not title_match:
            continue
        published = parse_datetime(html_to_text(time_match.group(1), 40))
        if not published or not in_window(published, now):
            continue
        body_match = re.search(r"<p[^>]*>(.*?)</p>", inner, flags=re.S)
        built = make_item(
            title=title_match.group(1),
            url=f"https://www.anthropic.com{href}",
            source="Anthropic",
            category="official",
            published_at=published,
            summary=body_match.group(1) if body_match else "",
        )
        if built:
            items.append(built)
    if not anchors:
        raise FetchError("新闻页没有解析到文章链接")
    items.sort(key=lambda row: row["publishedAt"], reverse=True)
    return items[:12]


def fetch_jiqizhixin(now: datetime) -> list[dict]:
    candidates = [
        "https://www.jiqizhixin.com/rss",
        "https://www.jiqizhixin.com/feed",
        "https://www.jiqizhixin.com/rss.xml",
    ]
    last_error = "公开 RSS 不可达"
    for url in candidates:
        try:
            payload = http_get(url, browser=True)
        except FetchError as exc:
            last_error = str(exc)
            continue
        head = payload[:400].lstrip().lower()
        if head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head:
            items = []
            for entry in parse_feed(payload):
                if not in_window(entry["published_at"], now):
                    continue
                built = make_item(
                    title=entry["title"],
                    url=entry["url"],
                    source="机器之心",
                    category="cn",
                    published_at=entry["published_at"],
                    summary=entry["summary"],
                )
                if built:
                    items.append(built)
            items.sort(key=lambda row: row["publishedAt"], reverse=True)
            return items[:12]
        text = payload.decode("utf-8", "replace")
        if "数据服务" in text and "articles/" not in text:
            last_error = "站点拦截了公开订阅，转向了数据服务页"
            continue
        scraped = []
        for href, inner in re.findall(r'href="(https://www\.jiqizhixin\.com/articles/[^"]+)"[^>]*>(.*?)</a>', text, flags=re.S):
            title = html_to_text(inner, 180)
            if not title:
                continue
            built = make_item(
                title=title,
                url=href,
                source="机器之心",
                category="cn",
                published_at=now,
                snapshot=True,
            )
            if built:
                scraped.append(built)
        if scraped:
            return scraped[:12]
        last_error = "返回的页面里没有文章或订阅源"
    raise FetchError(last_error)


def fetch_qbitai(now: datetime) -> list[dict]:
    items = feed_items("https://www.qbitai.com/feed", "量子位", "cn", now, browser=True)
    return [item for item in items if CN_HINT.search(item["title"])]


def fetch_cn_media(url: str, source: str, now: datetime) -> list[dict]:
    """General Chinese tech feeds, kept only when the title is about AI."""
    items = feed_items(url, source, "cn", now, cap=40)
    return [item for item in items if cn_ai_text(item["title"])][:8]


def fetch_simon(now: datetime) -> list[dict]:
    items = feed_items(
        "https://simonwillison.net/atom/everything/",
        "Simon Willison",
        "media",
        now,
        cap=20,
    )
    kept = [item for item in items if accept_simon(item["title"], item.get("summary") or "")]
    return kept[:6]


def source_specs(now: datetime):
    return [
        ("hn", "Hacker News", "hn", lambda: fetch_hn(now)),
        ("hf-papers", "Hugging Face 论文", "papers", lambda: fetch_hf_papers(now)),
        ("hf-models", "Hugging Face 模型", "models", lambda: fetch_hf_models(now)),
        ("gh-trending", "GitHub Trending", "github", lambda: fetch_github_trending(now)),
        ("yt-aiexplained", "AI Explained", "video", lambda: fetch_youtube("UCNJ1Ymd5yFuUPtn21xtRbbw", "AI Explained", now)),
        ("yt-matthew", "Matthew Berman", "video", lambda: fetch_youtube("UCawZsQWqfGSbCI5yjkdVkTA", "Matthew Berman", now)),
        ("yt-twomin", "Two Minute Papers", "video", lambda: fetch_youtube("UCbfYPyITQ-7l4upoX8nvctg", "Two Minute Papers", now)),
        ("yt-wes", "Wes Roth", "video", lambda: fetch_youtube("UCqcbQf6yw5KzRoDDcZ_wBSw", "Wes Roth", now)),
        ("verge", "The Verge", "media", lambda: feed_items("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge", "media", now)),
        ("techcrunch", "TechCrunch", "media", lambda: feed_items("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch", "media", now)),
        ("simon", "Simon Willison", "media", lambda: fetch_simon(now)),
        ("openai", "OpenAI", "official", lambda: feed_items("https://openai.com/news/rss.xml", "OpenAI", "official", now)),
        ("google-ai", "Google AI", "official", lambda: feed_items("https://blog.google/innovation-and-ai/technology/ai/rss/", "Google AI", "official", now)),
        ("deepmind", "Google DeepMind", "official", lambda: feed_items("https://deepmind.google/blog/rss.xml", "Google DeepMind", "official", now)),
        ("anthropic", "Anthropic", "official", lambda: fetch_anthropic(now)),
        ("qbitai", "量子位", "cn", lambda: fetch_qbitai(now)),
        ("jiqizhixin", "机器之心", "cn", lambda: fetch_jiqizhixin(now)),
        ("leiphone", "雷峰网", "cn", lambda: fetch_cn_media("https://www.leiphone.com/feed", "雷峰网", now)),
        ("ifanr", "爱范儿", "cn", lambda: fetch_cn_media("https://www.ifanr.com/feed", "爱范儿", now)),
        ("geekpark", "极客公园", "cn", lambda: fetch_cn_media("https://www.geekpark.net/rss", "极客公园", now)),
        ("36kr", "36氪", "cn", lambda: fetch_cn_media("https://www.36kr.com/feed-article", "36氪", now)),
        ("tmtpost", "钛媒体", "cn", lambda: fetch_cn_media("https://www.tmtpost.com/rss.xml", "钛媒体", now)),
        ("infoq", "InfoQ", "cn", lambda: fetch_cn_media("https://www.infoq.cn/feed", "InfoQ", now)),
        ("solidot", "Solidot", "cn", lambda: fetch_cn_media("https://www.solidot.org/index.rss", "Solidot", now)),
        ("mlx", "MLX", "local", lambda: fetch_release_feed("https://github.com/ml-explore/mlx/releases.atom", "MLX", now, title_prefix="MLX")),
        ("mlx-lm", "mlx-lm", "local", lambda: fetch_release_feed("https://github.com/ml-explore/mlx-lm/releases.atom", "mlx-lm", now, title_prefix="mlx-lm")),
        ("llamacpp", "llama.cpp", "local", lambda: fetch_release_feed("https://github.com/ggml-org/llama.cpp/releases.atom", "llama.cpp", now, title_prefix="llama.cpp")),
        ("ollama", "Ollama", "local", lambda: fetch_release_feed("https://github.com/ollama/ollama/releases.atom", "Ollama", now, title_prefix="Ollama")),
        ("lmstudio", "LM Studio", "local", lambda: fetch_lm_studio(now)),
        ("rocm", "AMD ROCm", "local", lambda: fetch_release_feed("https://github.com/ROCm/TheRock/releases.atom", "AMD ROCm", now)),
        ("lemonade", "Lemonade", "local", lambda: fetch_release_feed("https://github.com/lemonade-sdk/lemonade/releases.atom", "Lemonade", now, title_prefix="Lemonade")),
    ]


def run_source(spec):
    source_id, name, category, func = spec
    try:
        items = func()
        print(f"[ok] {name} {len(items)}", file=sys.stderr)
        return {
            "id": source_id,
            "name": name,
            "category": category,
            "ok": True,
            "count": len(items),
            "error": None,
        }, items
    except Exception as exc:  # noqa: BLE001 — one source must not fail the build
        message = str(exc).strip() or exc.__class__.__name__
        message = re.sub(r"\s+", " ", message)[:240]
        print(f"[fail] {name} {message}", file=sys.stderr)
        if os.environ.get("FETCH_DEBUG"):
            traceback.print_exc()
        return {
            "id": source_id,
            "name": name,
            "category": category,
            "ok": False,
            "count": 0,
            "error": message,
        }, []


def dedupe(items: list[dict]) -> list[dict]:
    kept: list[dict] = []
    index_by_url: dict[str, int] = {}
    index_by_title: dict[str, int] = {}

    def better(candidate: dict, current: dict) -> bool:
        cand_priority = PRIORITY.get(candidate["category"], 0)
        curr_priority = PRIORITY.get(current["category"], 0)
        if cand_priority != curr_priority:
            return cand_priority > curr_priority
        return candidate["publishedAt"] > current["publishedAt"]

    for item in items:
        url_key = canonical_url(item["url"])
        title_key = normalized_title(item["title"])
        slot = index_by_url.get(url_key)
        if slot is None and title_key:
            slot = index_by_title.get(title_key)
        if slot is None:
            kept.append(item)
            slot = len(kept) - 1
        elif better(item, kept[slot]):
            kept[slot] = item
        index_by_url[url_key] = slot
        if title_key:
            index_by_title[title_key] = slot
    return kept


def parse_summary_payload(text: str) -> dict[str, str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise FetchError("摘要接口没有返回 JSON 数组")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, list):
        raise FetchError("摘要接口返回的不是数组")
    mapped = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or "").strip()
        summary = html_to_text(str(row.get("summaryZh") or ""), 80)
        if row_id and summary:
            mapped[row_id] = summary
    return mapped


def maybe_summarize(items: list[dict]) -> dict | None:
    """Attach summaryZh when SUMMARY_API_KEY is set. Never raises."""
    key = os.environ.get("SUMMARY_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("SUMMARY_API_BASE", "https://api.openai.com/v1").strip().rstrip("/") or "https://api.openai.com/v1"
    model = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    batch = [item for item in items if not item.get("snapshot") and not item.get("summaryZh")][:30]
    if not batch:
        return {"id": "summary-zh", "name": "中文摘要", "category": "summary", "ok": True, "count": 0, "error": None}
    prompt = json.dumps(
        [{"id": item["id"], "title": item["title"], "source": item["source"], "summary": item.get("summary") or ""} for item in batch],
        ensure_ascii=False,
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是资讯编辑。把每条 AI 新闻写成一句简体中文摘要，保留专有名词。"
                        "只输出 JSON 数组，元素为 {\"id\",\"summaryZh\"}，summaryZh 不超过 40 个汉字。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": BOT_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read(MAX_BYTES).decode("utf-8", "replace"))
        text = payload["choices"][0]["message"]["content"]
        mapped = parse_summary_payload(text)
    except Exception as exc:  # noqa: BLE001 — summary is optional
        message = re.sub(r"\s+", " ", str(exc))[:240]
        print(f"[fail] 中文摘要 {message}", file=sys.stderr)
        return {"id": "summary-zh", "name": "中文摘要", "category": "summary", "ok": False, "count": 0, "error": message}
    applied = 0
    for item in items:
        summary = mapped.get(item["id"])
        if summary:
            item["summaryZh"] = summary
            applied += 1
    print(f"[ok] 中文摘要 {applied}", file=sys.stderr)
    return {"id": "summary-zh", "name": "中文摘要", "category": "summary", "ok": True, "count": applied, "error": None}


def build_payload(now: datetime | None = None) -> dict:
    current = now or now_utc()
    specs = source_specs(current)
    statuses = []
    collected: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_source, spec) for spec in specs]
        for future in as_completed(futures):
            status, items = future.result()
            statuses.append(status)
            collected.extend(items)
    order = {spec[0]: index for index, spec in enumerate(specs)}
    statuses.sort(key=lambda row: order.get(row["id"], 999))
    items = dedupe(collected)
    summary_status = maybe_summarize(items)
    if summary_status:
        statuses.append(summary_status)
    items.sort(key=lambda row: (row["publishedAt"], row["title"]), reverse=True)
    shanghai = current.astimezone(SHANGHAI)
    return {
        "generatedAt": isoformat(current),
        "generatedAtShanghai": shanghai.strftime("%Y-%m-%d %H:%M"),
        "timezone": "Asia/Shanghai",
        "windowHours": WINDOW_HOURS,
        "items": items,
        "sources": statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取小波 AI 资讯并写出 news.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(1 for source in payload["sources"] if source["ok"])
    print(
        f"wrote {args.output} items={len(payload['items'])} sources_ok={ok}/{len(payload['sources'])}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
