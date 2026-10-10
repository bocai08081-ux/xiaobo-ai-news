"""Turn collected news into daily Chinese briefing cards.

Days follow the Asia/Shanghai calendar. An OpenAI-compatible chat API is used
when SUMMARY_API_KEY is set; otherwise cards are still written in Chinese from
the source text and a small set of editing rules.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fetch_news import (
    BOT_UA,
    MAX_BYTES,
    PRIORITY,
    beijing_date_key,
    beijing_now,
    format_beijing_day,
    format_beijing_stamp,
    parse_datetime,
)

CATEGORIES = (
    {"id": "models", "label": "AI 模型"},
    {"id": "vision", "label": "图像与视频"},
    {"id": "mac", "label": "Mac"},
    {"id": "nvidia", "label": "英伟达"},
    {"id": "amd", "label": "AMD"},
)

CATEGORY_LABEL = {row["id"]: row["label"] for row in CATEGORIES}
CATEGORY_IDS = set(CATEGORY_LABEL)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_NOISE = re.compile(
    r"for[- ]beginners|cookbook|handson[- ]ml|awesome[- ]|教程合集",
    re.I,
)

_MAC = re.compile(
    r"\bmlx\b|apple intelligence|apple silicon|macbook|mac studio|\bm[1-5]\s*(pro|max|ultra)\b|\bon a mac\b|在 Mac",
    re.I,
)
_AMD = re.compile(r"\brocm\b|\bradeon\b|\bamd\b|\b7900\b|\b9070\b|\b9060\b", re.I)
_NVIDIA = re.compile(r"nvidia|英伟达|geforce|\bcuda\b|tensorrt|\brtx\b", re.I)
_VISION = re.compile(
    r"sora|midjourney|stable[- ]diffusion|\bflux\b|image-to-video|text-to-video|"
    r"text-to-image|视频生成|图像生成|文生视频|文生图|kling|runway|\bltx\b|绘画|视频模型",
    re.I,
)

APPLICABILITY = {
    "models": "适合已经在用大模型接口或开源权重的人。还没固定工作流的，看完标题再决定要不要跟。",
    "vision": "适合在做图像或视频生成的人。只处理文字、不用多模态的可以跳过。",
    "mac": "只针对 Apple 芯片上的本地模型。Windows 或 NVIDIA 机器不用按这条改环境。",
    "nvidia": "针对 NVIDIA 显卡、CUDA 或英伟达软件栈。AMD 显卡和纯 Mac 环境通常对不上。",
    "amd": "针对 AMD 显卡和 ROCm。CUDA 专用的步骤不要直接搬过来。",
}


def has_cjk(text: str | None) -> bool:
    return bool(_CJK.search(text or ""))


def cjk_count(text: str | None) -> int:
    return len(_CJK.findall(text or ""))


def assign_category(item: dict) -> str:
    source = item.get("source") or ""
    if source in {"MLX", "mlx-lm"}:
        return "mac"
    if source in {"AMD ROCm", "Lemonade"}:
        return "amd"
    # Long release notes mention every build target. Don't let "Apple Silicon"
    # in a changelog turn a general release into a Mac card.
    summary = item.get("summary") or ""
    if len(summary) > 160:
        summary = ""
    blob = " ".join(part for part in (item.get("title"), item.get("meta"), source, summary) if part)
    if _MAC.search(blob):
        return "mac"
    if _AMD.search(blob) and not _NVIDIA.search(blob):
        return "amd"
    if _NVIDIA.search(blob):
        return "nvidia"
    if _VISION.search(blob):
        return "vision"
    return "models"


def is_noise(item: dict) -> bool:
    if item.get("snapshot") and item.get("category") in {"github", "models"}:
        return True
    blob = f"{item.get('title') or ''} {item.get('summary') or ''}"
    if item.get("snapshot") and _NOISE.search(blob):
        return True
    if _NOISE.search(item.get("title") or "") and item.get("category") in {"github", "models"}:
        return True
    if item.get("category") == "hn":
        match = re.search(r"(\d+)\s*分", item.get("meta") or "")
        points = int(match.group(1)) if match else 0
        if points < 20:
            return True
    return False


def score_item(item: dict, now: datetime) -> float:
    score = float(PRIORITY.get(item.get("category") or "", 0))
    if item_is_zh(item) or item.get("category") == "cn":
        score += 18
    published = parse_datetime(item.get("publishedAt"))
    if published and not item.get("snapshot"):
        age_h = (now.astimezone(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 3600
        if age_h < 0:
            age_h = 0
        if age_h <= 18:
            score += 24
        elif age_h <= 36:
            score += 12
        elif age_h <= 72:
            score += 4
        else:
            score -= 30
    if item.get("snapshot"):
        score -= 40
    if item.get("category") == "official":
        score += 8
    if item.get("category") == "local":
        score += 12
    title = item.get("title") or ""
    if re.search(r"体验|上手|部署", title):
        score += 8
    if re.search(r"创造营|孵化|正式启动|范式下|生命力", title):
        score -= 28
    if title.count("；") >= 2 or title.count(";") >= 2:
        score -= 18
    return score


def item_day(item: dict, today: str) -> str:
    """Beijing calendar day. Snapshots belong to the run's day, not the model's createdAt."""
    if item.get("snapshot"):
        return today
    published = parse_datetime(item.get("publishedAt"))
    if published is None:
        return today
    return beijing_date_key(published)


def date_label(item: dict) -> str:
    if item.get("snapshot"):
        return "日期待核实"
    published = parse_datetime(item.get("publishedAt"))
    if published is None:
        return "日期待核实"
    return format_beijing_day(published)


def verifier_name(item: dict) -> str:
    category = item.get("category")
    if category in {"official", "local"}:
        return "官方"
    if category in {"cn", "media"}:
        return "媒体"
    if category == "papers":
        return "论文"
    return "综合"


def verified_label(item: dict) -> str:
    day = date_label(item)
    name = verifier_name(item)
    if day == "日期待核实":
        return f"{name} · 待核查"
    return f"{name} · {day} 核查"


def _tidy_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    if cleaned[-1] not in "。！？…":
        cleaned += "。"
    return cleaned


def item_is_zh(item: dict) -> bool:
    if item.get("lang") == "zh":
        return True
    return cjk_count(item.get("title") or "") >= 6


def chinese_title(item: dict, category: str) -> str:
    title = re.sub(r"\s+", " ", (item.get("title") or "").strip())
    if not title:
        return f"{CATEGORY_LABEL[category]}有一条新动态"
    release = re.fullmatch(r"([A-Za-z0-9.+_-]+)\s+(b\d+)", title)
    if release:
        return f"{release.group(1)} 发布了新版本 {release.group(2)}"
    versioned = re.fullmatch(r"([A-Za-z0-9 .+-]+?)\s+v?(\d+\.\d+(?:\.\d+)?)", title)
    if versioned:
        return f"{versioned.group(1).strip()} {versioned.group(2)} 发布了更新"
    if item_is_zh(item):
        return title
    source = item.get("source") or "来源"
    if source == "AMD ROCm":
        version = re.search(r"(\d+\.\d+(?:\.\d+)?)", title)
        if version:
            return f"AMD 发布 ROCm {version.group(1)}"
    short = title if len(title) <= 42 else title[:41].rstrip() + "…"
    return f"{source}：{short}"


_FOOTER = re.compile(r"(?:#|＃)?欢迎关注.*|更多精彩内容.*|微信号[:：].*")


def usable_excerpt(title: str, excerpt: str) -> str:
    """Keep a short Chinese deck. Drop footers and excerpts that don't match the title."""
    text = _FOOTER.sub("", excerpt or "")
    text = re.sub(r"\s+", " ", text).strip(" #")
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？])", text)
    short = "".join(part for part in parts[:2]).strip() or text
    if len(short) > 160:
        short = short[:159].rstrip() + "…"
    if cjk_count(short) < 12:
        return ""
    if len(short) > 80:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}", title)[:6]
        if tokens and not any(token in short for token in tokens):
            return ""
    if short[-1] not in "。！？…":
        short += "。"
    return short


def practical_takeaway(title: str, category: str) -> str:
    short = re.split(r"[：:|｜]", title.strip(), maxsplit=1)[0].strip() or CATEGORY_LABEL[category]
    if len(short) > 28:
        cut = short[:27]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        short = cut.rstrip() + "…"
    if re.search(r"融资|估值|收购", title):
        return f"这是公司层面的消息。先分清「{short}」有没有改变你能用的产品，再决定要不要跟。"
    if re.search(r"站台|回应|表示", title):
        return f"这是公开表态，不是一次产品更新。先看「{short}」有没有落到你能用的工具上。"
    lines = {
        "models": f"先看「{short}」改的是模型、接口还是权限，再决定要不要换掉你现在的默认方案。",
        "vision": f"先确认「{short}」对分辨率、时长和能否商用怎么说，再替换你现在的出图或出视频流程。",
        "mac": f"先核对「{short}」支持的芯片和内存，再在自己的 Mac 上改本地环境。",
        "nvidia": f"先对一下「{short}」涉及的驱动、CUDA 和卡型，再决定要不要升级。",
        "amd": f"先确认「{short}」是否覆盖你的 AMD 卡和当前驱动，再动现有的推理环境。",
    }
    return lines[category]


def narrate(item: dict, category: str) -> dict[str, str]:
    """Chinese title, summary, takeaway, and applicability. Works without an API key."""
    title = chinese_title(item, category)
    source = item.get("source") or "这个来源"
    label = CATEGORY_LABEL[category]
    excerpt = usable_excerpt(item.get("title") or "", item.get("summary") or "")
    if excerpt:
        summary = excerpt
    elif item_is_zh(item):
        summary = _tidy_sentence(f"{source}报道了这件事：{item.get('title') or title}")
    else:
        original = item.get("title") or title
        summary = (
            f"{source}报道了与{label}有关的新动态，原文标题是「{original}」。"
            "先把它当成今天要核对的结论，细节以原文为准。"
        )
    return {
        "title": title,
        "summary": summary,
        "takeaway": practical_takeaway(title, category),
        "applicability": APPLICABILITY[category],
    }


def fallback_card(item: dict, *, featured: bool = False) -> dict:
    category = assign_category(item)
    text = narrate(item, category)
    return {
        "id": item.get("id") or "",
        "category": category,
        "categoryLabel": CATEGORY_LABEL[category],
        "title": text["title"],
        "summary": text["summary"],
        "takeaway": text["takeaway"],
        "applicability": text["applicability"],
        "sourceName": item.get("source") or "来源",
        "sourceUrl": item.get("url") or "",
        "dateLabel": date_label(item),
        "verifiedLabel": verified_label(item),
        "featured": featured,
    }


def select_items(items: list[dict], now: datetime, *, limit: int = 9) -> list[dict]:
    ranked = [item for item in items if not is_noise(item) and item.get("title") and item.get("url")]
    ranked.sort(key=lambda item: (score_item(item, now), item.get("publishedAt") or ""), reverse=True)
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    per_category: dict[str, int] = {}

    def take(item: dict, source_cap: int, category_cap: int) -> bool:
        if len(picked) >= limit:
            return False
        source = item.get("source") or ""
        category = assign_category(item)
        if source and per_source.get(source, 0) >= source_cap:
            return False
        if per_category.get(category, 0) >= category_cap:
            return False
        picked.append(item)
        if source:
            per_source[source] = per_source.get(source, 0) + 1
        per_category[category] = per_category.get(category, 0) + 1
        return True

    for item in ranked:
        take(item, 1, 3)
    if len(picked) < min(limit, 4):
        picked_ids = {item.get("id") for item in picked}
        for item in ranked:
            if item.get("id") in picked_ids:
                continue
            if take(item, 2, 4):
                picked_ids.add(item.get("id"))
    return picked


def llm_config() -> dict | None:
    key = os.environ.get("SUMMARY_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("SUMMARY_API_BASE", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return {"key": key, "base": base or "https://api.openai.com/v1", "model": model}


def parse_briefing_payload(text: str) -> list[dict]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start_obj = cleaned.find("{")
    start_arr = cleaned.find("[")
    if start_obj == -1 and start_arr == -1:
        raise ValueError("解读接口没有返回 JSON")
    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        end = cleaned.rfind("]")
        data = json.loads(cleaned[start_arr : end + 1])
    else:
        end = cleaned.rfind("}")
        data = json.loads(cleaned[start_obj : end + 1])
    if isinstance(data, dict):
        cards = data.get("cards")
    else:
        cards = data
    if not isinstance(cards, list):
        raise ValueError("解读接口返回的不是卡片数组")
    return [row for row in cards if isinstance(row, dict)]


def _candidate_prompt(items: list[dict]) -> str:
    rows = []
    for item in items:
        published = parse_datetime(item.get("publishedAt"))
        rows.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "source": item.get("source"),
                "feedCategory": item.get("category"),
                "suggestedCategory": assign_category(item),
                "summary": item.get("summary") or "",
                "meta": item.get("meta") or "",
                "beijingTime": format_beijing_stamp(published) if published and not item.get("snapshot") else "",
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def complete_chat(items: list[dict]) -> str | None:
    """Call the configured chat API. Returns None when unconfigured or on failure."""
    cfg = llm_config()
    if cfg is None or not items:
        return None
    system = (
        "你是「小波AI资讯站」的编辑。读者是中文 AI 社区，关心资讯解读、AI 工具和能不能上手。"
        "从候选里挑今天最值得看的，最多 9 条，宁缺毋滥。丢掉教程合集、重复的小版本和低信息热榜。"
        "全部写成简体中文，不要留整段英文；产品名、模型名、人名可以保留原文。"
        "title 是主标题，尽量 28 个字以内，先写结论。"
        "summary 用 2 到 4 句说明发生了什么。"
        "takeaway 用 1 到 2 句写对读者的影响或建议，要具体。"
        "applicability 用 1 句说明什么人用得上、什么人可以跳过。"
        "category 只能是 models、vision、mac、nvidia、amd 之一。"
        '只输出 JSON：{"cards":[{"id","category","title","summary","takeaway","applicability"}]}'
    )
    body = json.dumps(
        {
            "model": cfg["model"],
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": _candidate_prompt(items)},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base']}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {cfg['key']}",
            "Content-Type": "application/json",
            "User-Agent": BOT_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read(MAX_BYTES).decode("utf-8", "replace"))
        return payload["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 — briefing must still build
        message = re.sub(r"\s+", " ", str(exc))[:240]
        print(f"[fail] 简报解读 {message}", file=sys.stderr)
        return None


def cards_from_llm(text: str, items: list[dict]) -> list[dict]:
    by_id = {item.get("id"): item for item in items if item.get("id")}
    built = []
    seen: set[str] = set()
    for row in parse_briefing_payload(text):
        item_id = str(row.get("id") or "").strip()
        item = by_id.get(item_id)
        if item is None or item_id in seen:
            continue
        category = str(row.get("category") or "").strip()
        if category not in CATEGORY_IDS:
            category = assign_category(item)
        base = narrate(item, category)
        title = str(row.get("title") or "").strip()
        summary = str(row.get("summary") or "").strip()
        takeaway = str(row.get("takeaway") or "").strip()
        applicability = str(row.get("applicability") or "").strip()
        if not has_cjk(title):
            title = base["title"]
        if not has_cjk(summary):
            summary = base["summary"]
        if not has_cjk(takeaway):
            takeaway = base["takeaway"]
        if not has_cjk(applicability):
            applicability = base["applicability"]
        built.append(
            {
                "id": item_id,
                "category": category,
                "categoryLabel": CATEGORY_LABEL[category],
                "title": title,
                "summary": _tidy_sentence(summary) if not summary.endswith(("。", "！", "？", "…")) else summary,
                "takeaway": takeaway,
                "applicability": applicability,
                "sourceName": item.get("source") or "来源",
                "sourceUrl": item.get("url") or "",
                "dateLabel": date_label(item),
                "verifiedLabel": verified_label(item),
                "featured": False,
            }
        )
        seen.add(item_id)
        if len(built) >= 9:
            break
    if built:
        built[0]["featured"] = True
    return built


def render_cards(items: list[dict], now: datetime, *, use_llm: bool) -> tuple[list[dict], str]:
    pool = select_items(items, now, limit=12 if use_llm else 9)
    if use_llm and pool:
        raw = complete_chat(pool)
        if raw:
            try:
                cards = cards_from_llm(raw, pool)
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"[fail] 简报解读 {exc}", file=sys.stderr)
                cards = []
            if len(cards) >= 3:
                print(f"[ok] 简报解读 {len(cards)}", file=sys.stderr)
                return cards, "llm"
            print("[fail] 简报解读 有效卡片不足，改用规则摘编", file=sys.stderr)
    elif not use_llm:
        print("[skip] 简报解读 未配置 SUMMARY_API_KEY，使用规则摘编", file=sys.stderr)
    chosen = pool[:9]
    cards = [fallback_card(item, featured=(index == 0)) for index, item in enumerate(chosen)]
    return cards, "fallback"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def briefing_document(day: str, now: datetime, cards: list[dict], mode: str) -> dict:
    return {
        "date": day,
        "timezone": "Asia/Shanghai",
        "generatedAt": now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updatedLabel": format_beijing_stamp(now),
        "mode": mode,
        "cards": cards,
    }


def publish_briefings(items: list[dict], now: datetime | None = None, directory: Path | None = None) -> dict:
    """Write today's briefing and seed any missing earlier days. Returns the index."""
    current = now or beijing_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    folder = directory or (Path(__file__).resolve().parents[1] / "site" / "data" / "briefings")
    folder.mkdir(parents=True, exist_ok=True)
    today = beijing_date_key(current)
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item_day(item, today), []).append(item)

    use_llm = llm_config() is not None
    today_cards, today_mode = render_cards(grouped.get(today, []), current, use_llm=use_llm)
    _write_json(folder / f"{today}.json", briefing_document(today, current, today_cards, today_mode))

    for day in sorted(grouped):
        if day >= today:
            continue
        path = folder / f"{day}.json"
        if path.exists():
            continue
        historical = [item for item in grouped[day] if not item.get("snapshot")]
        if not historical:
            continue
        cards, mode = render_cards(historical, current, use_llm=False)
        if not cards:
            continue
        _write_json(path, briefing_document(day, current, cards, mode))

    return write_index(folder, current)


def write_index(folder: Path, now: datetime) -> dict:
    days = []
    for path in sorted(folder.glob("????-??-??.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        day = payload.get("date") or path.stem
        try:
            # The file name is a Beijing calendar date, not a UTC instant.
            label = datetime.fromisoformat(day).strftime("%m/%d")
        except ValueError:
            label = day
        days.append(
            {
                "date": day,
                "label": label,
                "cardCount": len(payload.get("cards") or []),
                "updatedLabel": payload.get("updatedLabel") or "",
            }
        )
    index = {
        "timezone": "Asia/Shanghai",
        "updatedAt": now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updatedLabel": format_beijing_stamp(now),
        "days": days,
    }
    _write_json(folder / "index.json", index)
    print(f"[ok] 简报 {len(days)} 天，今天 {days[0]['cardCount'] if days else 0} 条", file=sys.stderr)
    return index
