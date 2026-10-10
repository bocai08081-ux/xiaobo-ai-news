"""GPU price board.

Prices are not invented. A model without a checked quote stays at 「待采集」.
Existing quotes in gpus.json are kept when the catalog is rewritten.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fetch_news import beijing_now, format_beijing_stamp

REGIONS = (
    {"id": "domestic", "label": "国内", "currency": "CNY", "subtitle": "中国大陆 · 人民币"},
    {"id": "overseas", "label": "海外", "currency": "USD", "subtitle": "海外渠道 · 标价待核"},
)

CHANNELS = {
    "domestic": [
        {"id": "all", "label": "全部渠道"},
        {"id": "jd-self", "label": "京东自营"},
        {"id": "jd-third", "label": "京东第三方"},
        {"id": "taobao", "label": "淘宝"},
        {"id": "tmall", "label": "天猫"},
    ],
    "overseas": [
        {"id": "all", "label": "全部渠道"},
        {"id": "amazon", "label": "Amazon"},
        {"id": "newegg", "label": "Newegg"},
        {"id": "official", "label": "官网"},
    ],
}

BRANDS = (
    {"id": "all", "label": "全部"},
    {"id": "nvidia", "label": "英伟达"},
    {"id": "amd", "label": "AMD"},
    {"id": "cn", "label": "国产显卡"},
)

# Consumer boards the page tracks. VRAM is the common retail spec; the card
# note tells the reader the exact partner board is still unchecked.
_DOMESTIC = (
    ("rtx-5090", "nvidia", "英伟达", "RTX 5090", "32GB", "具体板卡版本待确认"),
    ("rtx-5080", "nvidia", "英伟达", "RTX 5080", "16GB", "具体板卡版本待确认"),
    ("rtx-5070", "nvidia", "英伟达", "RTX 5070", "12GB", "显存以在售型号为准"),
    ("rx-7900-xtx", "amd", "AMD", "RX 7900 XTX", "24GB", "具体板卡版本待确认"),
    ("rx-9070-xt", "amd", "AMD", "RX 9070 XT", "16GB", "具体板卡版本待确认"),
    ("rx-9060-xt", "amd", "AMD", "RX 9060 XT", "8GB", "具体板卡版本待确认"),
    ("mtt-s80", "cn", "国产显卡", "摩尔线程 MTT S80", "16GB", "具体板卡版本待确认"),
)

_OVERSEAS = (
    ("rtx-5090-os", "nvidia", "英伟达", "RTX 5090", "32GB", "海外零售型号待核对"),
    ("rtx-5080-os", "nvidia", "英伟达", "RTX 5080", "16GB", "海外零售型号待核对"),
    ("rtx-5070-os", "nvidia", "英伟达", "RTX 5070", "12GB", "显存以在售型号为准"),
    ("rx-7900-xtx-os", "amd", "AMD", "RX 7900 XTX", "24GB", "海外零售型号待核对"),
    ("rx-9070-xt-os", "amd", "AMD", "RX 9070 XT", "16GB", "海外零售型号待核对"),
    ("rx-9060-xt-os", "amd", "AMD", "RX 9060 XT", "8GB", "海外零售型号待核对"),
)


def catalog() -> list[dict]:
    rows = []
    for spec, region in ((_DOMESTIC, "domestic"), (_OVERSEAS, "overseas")):
        for model_id, brand, brand_label, name, vram, note in spec:
            rows.append(
                {
                    "id": model_id,
                    "region": region,
                    "brand": brand,
                    "brandLabel": brand_label,
                    "name": name,
                    "vram": vram,
                    "note": note,
                }
            )
    return rows


def _valid_quote(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    price = row.get("price")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        return False
    if not row.get("modelId") or not row.get("region") or not row.get("channel"):
        return False
    return True


def load_quotes(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, list):
        return []
    return [row for row in quotes if _valid_quote(row)]


def publish_market(now: datetime | None = None, path: Path | None = None) -> dict:
    current = now or beijing_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target = path or (Path(__file__).resolve().parents[1] / "site" / "data" / "gpus.json")
    payload = {
        "timezone": "Asia/Shanghai",
        "updatedAt": current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updatedLabel": format_beijing_stamp(current),
        "regions": list(REGIONS),
        "channels": CHANNELS,
        "brands": list(BRANDS),
        "models": catalog(),
        "quotes": load_quotes(target),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] 显卡行情 型号 {len(payload['models'])}，报价 {len(payload['quotes'])}", file=sys.stderr)
    return payload
