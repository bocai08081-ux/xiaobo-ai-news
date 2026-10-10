#!/usr/bin/env python3
"""Tests for the daily briefing, GPU board, and people cards. No network."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing import (
    assign_category,
    cards_from_llm,
    date_label,
    fallback_card,
    has_cjk,
    item_day,
    parse_briefing_payload,
    publish_briefings,
    render_cards,
    select_items,
    verified_label,
)
from market import publish_market
from people import match_person, publish_people, time_label

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 10, 10, 0, tzinfo=SHANGHAI)


def sample_item(**overrides) -> dict:
    item = {
        "id": "cn1",
        "title": "量子位报道免费推理额度有了新的说法",
        "url": "https://www.qbitai.com/2026/10/example",
        "source": "量子位",
        "category": "cn",
        "publishedAt": "2026-10-09T16:30:00Z",
        "summary": "免费账户的每月推理额度被写成不含在套餐里，实际调用前需要先核对计费文档，避免按旧规则预估成本。",
        "summaryZh": None,
        "meta": "",
        "snapshot": False,
        "lang": "zh",
    }
    item.update(overrides)
    return item


class BriefingTest(unittest.TestCase):
    def test_changelog_listing_apple_silicon_is_not_a_mac_story(self):
        item = {
            "source": "llama.cpp",
            "title": "llama.cpp b11539",
            "summary": "Website notes. macOS Apple Silicon (arm64) macOS Intel (x64) iOS build matrix " * 8,
        }
        self.assertEqual(assign_category(item), "models")
        card = fallback_card(item)
        self.assertIn("新版本", card["title"])
        self.assertNotIn("macOS", card["summary"])

    def test_low_score_hn_and_trending_repos_are_not_briefed(self):
        from briefing import is_noise

        hn = sample_item(category="hn", meta="3 分 · 1 评论", snapshot=False, title="GPGPU-SIM", lang="en")
        trending = sample_item(category="github", snapshot=True, source="GitHub Trending", title="unslothai/unsloth", lang="en")
        self.assertTrue(is_noise(hn))
        self.assertTrue(is_noise(trending))

    def test_categories_follow_the_page_chips(self):
        self.assertEqual(assign_category({"source": "MLX", "title": "MLX 0.2"}), "mac")
        self.assertEqual(assign_category({"source": "AMD ROCm", "title": "ROCm 10.1"}), "amd")
        self.assertEqual(assign_category({"source": "OpenAI", "title": "CUDA graphs in TensorRT", "summary": ""}), "nvidia")
        self.assertEqual(assign_category({"source": "OpenAI", "title": "Sora can cut a longer video", "summary": ""}), "vision")
        self.assertEqual(assign_category({"source": "量子位", "title": "新的开源语言模型发布", "summary": ""}), "models")

    def test_snapshot_stays_on_the_run_day(self):
        item = sample_item(snapshot=True, publishedAt="2026-07-23T07:55:24Z")
        self.assertEqual(item_day(item, "2026-10-10"), "2026-10-10")
        self.assertEqual(date_label(item), "日期待核实")
        self.assertIn("待核查", verified_label(item))

    def test_utc_evening_displays_as_next_beijing_day(self):
        item = sample_item(snapshot=False, publishedAt="2026-10-09T16:30:00Z")
        self.assertEqual(item_day(item, "2026-10-10"), "2026-10-10")
        self.assertEqual(date_label(item), "10/10")
        self.assertEqual(verified_label(item), "媒体 · 10/10 核查")

    def test_pacific_offset_converts_before_display(self):
        published = datetime(2026, 10, 9, 12, 0, tzinfo=timezone(timedelta(hours=-7)))
        item = sample_item(publishedAt=published.isoformat())
        self.assertEqual(date_label(item), "10/10")
        self.assertEqual(time_label(item), "10/10 03:00")

    def test_fallback_card_is_chinese_and_keeps_source(self):
        os.environ.pop("SUMMARY_API_KEY", None)
        card = fallback_card(sample_item(), featured=True)
        self.assertTrue(has_cjk(card["title"]))
        self.assertTrue(has_cjk(card["summary"]))
        self.assertTrue(has_cjk(card["takeaway"]))
        self.assertTrue(has_cjk(card["applicability"]))
        self.assertEqual(card["sourceName"], "量子位")
        self.assertEqual(card["sourceUrl"], "https://www.qbitai.com/2026/10/example")
        self.assertNotIn("http", card["summary"])

    def test_english_item_is_rewritten_without_a_key(self):
        os.environ.pop("SUMMARY_API_KEY", None)
        card = fallback_card(
            sample_item(
                id="en1",
                title="OpenAI rolls out a faster mode",
                summary="The company made generation up to eight times faster for one model.",
                source="OpenAI",
                category="official",
                lang="en",
                url="https://openai.com/news/faster",
            )
        )
        self.assertTrue(has_cjk(card["summary"]))
        self.assertTrue(has_cjk(card["takeaway"]))
        self.assertNotIn("eight times faster", card["summary"])
        self.assertEqual(card["sourceUrl"], "https://openai.com/news/faster")
        self.assertEqual(card["verifiedLabel"].split(" · ")[0], "官方")

    def test_selection_skips_tutorial_dumps_and_repeats_a_source(self):
        items = [
            sample_item(id="a", source="量子位", title="量子位报道免费推理额度有了新的说法"),
            sample_item(id="b", source="量子位", title="量子位又写了一条同样来源的后续报道", url="https://example.com/b"),
            sample_item(id="d", source="OpenAI", category="official", lang="en", title="CUDA graphs on RTX cards", url="https://openai.com/a"),
            sample_item(id="e", source="The Verge", category="media", lang="en", title="ROCm support note", url="https://theverge.com/a"),
            sample_item(id="f", source="Anthropic", category="official", lang="en", title="Sora video update", url="https://anthropic.com/a"),
            sample_item(id="g", source="MLX", category="local", lang="en", title="MLX 0.4", url="https://github.com/ml-explore/mlx/releases/tag/v0.4"),
            sample_item(
                id="c",
                source="GitHub Trending",
                category="github",
                snapshot=True,
                title="microsoft/generative-ai-for-beginners",
                lang="en",
                url="https://github.com/microsoft/generative-ai-for-beginners",
            ),
        ]
        picked = select_items(items, NOW, limit=9)
        ids = [item["id"] for item in picked]
        self.assertIn("a", ids)
        self.assertNotIn("b", ids)
        self.assertNotIn("c", ids)

    def test_no_key_still_renders_cards(self):
        os.environ.pop("SUMMARY_API_KEY", None)
        cards, mode = render_cards([sample_item()], NOW, use_llm=False)
        self.assertEqual(mode, "fallback")
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0]["featured"])
        self.assertTrue(has_cjk(cards[0]["summary"]))

    def test_llm_cards_keep_original_urls(self):
        raw = """```json
        {"cards":[{"id":"cn1","category":"models","title":"免费额度要重新核对","summary":"文档把每月推理额度拿掉了，调用前得先看计费。","takeaway":"先改你的成本估算，再决定要不要留在免费档。","applicability":"正在用这家推理接口的人需要看。"}]}
        ```"""
        cards = cards_from_llm(raw, [sample_item()])
        self.assertEqual(cards[0]["sourceUrl"], "https://www.qbitai.com/2026/10/example")
        self.assertEqual(cards[0]["title"], "免费额度要重新核对")
        self.assertTrue(cards[0]["featured"])
        self.assertEqual(parse_briefing_payload(raw)[0]["id"], "cn1")

    def test_english_llm_fields_fall_back_per_card(self):
        raw = json.dumps(
            {
                "cards": [
                    {
                        "id": "cn1",
                        "category": "models",
                        "title": "English only title",
                        "summary": "Still English.",
                        "takeaway": "先核对再换模型。",
                        "applicability": "用这套接口的人要看。",
                    }
                ]
            }
        )
        cards = cards_from_llm(raw, [sample_item()])
        self.assertNotEqual(cards[0]["title"], "English only title")
        self.assertTrue(has_cjk(cards[0]["summary"]))
        self.assertEqual(cards[0]["takeaway"], "先核对再换模型。")

    def test_archive_keeps_an_existing_day(self):
        os.environ.pop("SUMMARY_API_KEY", None)
        from tempfile import TemporaryDirectory

        yesterday = sample_item(
            id="y1",
            publishedAt="2026-10-08T02:00:00Z",
            url="https://example.com/yesterday",
            title="昨天那条中文报道已经写进归档",
        )
        today = sample_item()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            marker = {"date": "2026-10-08", "timezone": "Asia/Shanghai", "cards": [{"title": "保留"}], "updatedLabel": "10/08 12:00"}
            (folder / "2026-10-08.json").write_text(json.dumps(marker), encoding="utf-8")
            index = publish_briefings([yesterday, today], NOW, folder)
            kept = json.loads((folder / "2026-10-08.json").read_text(encoding="utf-8"))
            self.assertEqual(kept["cards"][0]["title"], "保留")
            self.assertTrue((folder / "2026-10-10.json").exists())
            self.assertEqual(index["timezone"], "Asia/Shanghai")
            self.assertEqual(index["days"][0]["date"], "2026-10-10")


class MarketAndPeopleTest(unittest.TestCase):
    def test_market_keeps_real_quotes_and_does_not_invent_prices(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "gpus.json"
            path.write_text(
                json.dumps(
                    {
                        "quotes": [
                            {"modelId": "rx-9070-xt", "region": "domestic", "channel": "jd-self", "price": 6999, "note": "页面参考标价"},
                            {"modelId": "rx-9070-xt", "region": "domestic", "channel": "jd-self", "price": "待采集"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            payload = publish_market(NOW, path)
            self.assertEqual(len(payload["quotes"]), 1)
            self.assertEqual(payload["quotes"][0]["price"], 6999)
            self.assertTrue(any(model["name"] == "RX 7900 XTX" for model in payload["models"]))
            self.assertEqual(payload["updatedLabel"], "10/10 10:00")

    def test_english_person_post_gets_a_chinese_title(self):
        from people import PEOPLE, post_from_item

        item = sample_item(
            title="Trump’s attempt to rename AI is looking awfully artificial",
            summary="President Donald Trump has a knack for turning words against his enemies in public remarks about artificial intelligence.",
            source="The Verge",
            category="media",
            lang="en",
            url="https://theverge.com/policy/example",
        )
        post = post_from_item(item, PEOPLE[2])
        self.assertIn("特朗普", post["title"])
        self.assertTrue(has_cjk(post["summary"]))
        self.assertEqual(post["timeLabel"], "10/10 00:30")

    def test_people_match_and_beijing_time(self):
        item = sample_item(
            title="马斯克回应了一则关于算力融资的转述",
            publishedAt="2026-10-09T06:57:00Z",
            url="https://example.com/musk",
        )
        self.assertEqual(match_person(item)["id"], "musk")
        self.assertEqual(time_label(item), "10/09 14:57")
        self.assertIsNone(match_person(sample_item(title="量子位报道免费推理额度有了新的说法", summary="与人物无关的正文。")))

    def test_pinned_posts_survive_regeneration(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "people.json"
            path.write_text(
                json.dumps(
                    {
                        "posts": [
                            {
                                "id": "pin",
                                "personId": "tibo",
                                "title": "手工钉住的一条",
                                "sourceUrl": "https://example.com/pinned",
                                "pinned": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            payload = publish_people(
                [sample_item(title="马斯克回应了一则关于算力融资的转述", url="https://example.com/musk")],
                NOW,
                path,
            )
            self.assertEqual(payload["posts"][0]["title"], "手工钉住的一条")
            self.assertEqual(payload["people"][0]["handle"], "thsottiaux")
            self.assertTrue(any(post["personId"] == "musk" for post in payload["posts"]))
            self.assertTrue(has_cjk(payload["posts"][-1]["context"]))


if __name__ == "__main__":
    unittest.main()
