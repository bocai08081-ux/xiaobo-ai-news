#!/usr/bin/env python3
"""Pure tests for fetch_news. No network."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_news import (
    accept_simon,
    canonical_url,
    cn_ai_text,
    dedupe,
    detect_lang,
    html_to_text,
    in_window,
    make_item,
    parse_datetime,
    parse_feed,
    parse_lm_studio_releases,
    parse_summary_payload,
    parse_trending_page,
)


class FetchLogicTest(unittest.TestCase):
    def test_canonical_url_strips_tracking_and_www(self):
        url = "https://www.Example.com/news/post/?utm_source=rss&id=3"
        self.assertEqual(canonical_url(url), "https://example.com/news/post?id=3")

    def test_youtube_urls_collapse(self):
        self.assertEqual(
            canonical_url("https://youtu.be/abc123?si=track"),
            "https://youtube.com/watch?v=abc123",
        )
        self.assertEqual(
            canonical_url("https://www.youtube.com/watch?v=abc123&t=12"),
            "https://youtube.com/watch?v=abc123",
        )

    def test_parse_datetime_formats(self):
        rfc = parse_datetime("Tue, 08 Oct 2026 12:00:00 GMT")
        self.assertEqual(rfc.year, 2026)
        iso = parse_datetime("2026-10-08T12:00:00Z")
        self.assertEqual(iso, datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc))
        day = parse_datetime("Oct 8, 2026")
        self.assertEqual(day.year, 2026)
        kr = parse_datetime("2026-10-09 21:55:09  +0800")
        self.assertEqual(kr, datetime(2026, 10, 9, 13, 55, 9, tzinfo=timezone.utc))

    def test_window(self):
        now = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
        self.assertTrue(in_window(now - timedelta(hours=71), now))
        self.assertFalse(in_window(now - timedelta(hours=73), now))

    def test_html_to_text(self):
        self.assertEqual(html_to_text("<p>你好 <b>AI</b></p>"), "你好 AI")

    def test_rss_and_atom(self):
        rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>A &amp; B</title>
            <link>https://example.com/a?utm_source=x</link>
            <pubDate>Thu, 08 Oct 2026 01:00:00 GMT</pubDate>
            <description><![CDATA[<p>摘要</p>]]></description>
          </item>
        </channel></rss>""".encode()
        rows = parse_feed(rss)
        self.assertEqual(rows[0]["title"], "A & B")
        self.assertIn("摘要", rows[0]["summary"])

        atom = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>mlx 0.1</title>
            <link rel="alternate" href="https://github.com/ml-explore/mlx/releases/tag/v0.1"/>
            <updated>2026-10-08T00:00:00Z</updated>
            <content>notes</content>
          </entry>
        </feed>""".encode()
        rows = parse_feed(atom)
        self.assertEqual(rows[0]["url"], "https://github.com/ml-explore/mlx/releases/tag/v0.1")

    def test_non_feed_is_rejected(self):
        with self.assertRaises(Exception):
            parse_feed("<!DOCTYPE html><html>数据服务</html>".encode())

    def test_dedupe_prefers_official_over_hn(self):
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        official = make_item(
            title="New model",
            url="https://openai.com/news/model?utm_source=rss",
            source="OpenAI",
            category="official",
            published_at=now,
        )
        hn = make_item(
            title="New model",
            url="https://openai.com/news/model",
            source="Hacker News",
            category="hn",
            published_at=now,
            meta="20 分",
        )
        kept = dedupe([hn, official])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "OpenAI")

    def test_trending_parser(self):
        html = """
        <article class="Box-row">
          <h2 class="h3"><a href="/org/Cool-LLM">org / Cool-LLM</a></h2>
          <p class="col-9 color-fg-muted my-1">A local LLM runner</p>
          <span itemprop="programmingLanguage">Python</span>
          1,234 stars today
        </article>
        """
        repos = parse_trending_page(html)
        self.assertEqual(repos[0]["name"], "org/Cool-LLM")
        self.assertEqual(repos[0]["stars"], "1,234")
        self.assertIn("LLM", repos[0]["description"])

    def test_lm_studio_escaped_json(self):
        html = (
            '<a href="/changelog/bionic-v1.1.7"><span>Bionic 1.1.7</span></a>'
            '{\\"version\\":\\"1.1.7\\",\\"build\\":7,\\"releaseDateIso\\":\\"2026-10-01T03:50:40.078Z\\"}'
        )
        releases = parse_lm_studio_releases(html)
        self.assertEqual(releases[0][0], "1.1.7")
        self.assertEqual(releases[0][2], "https://lmstudio.ai/changelog/bionic-v1.1.7")

    def test_summary_payload_parser(self):
        text = """```json
        [{"id":"abc","summaryZh":"一句话摘要"}]
        ```"""
        self.assertEqual(parse_summary_payload(text), {"abc": "一句话摘要"})

    def test_payload_shape_roundtrip_fields(self):
        item = make_item(
            title="标题很长的一条中文资讯",
            url="https://example.com/x",
            source="量子位",
            category="cn",
            published_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        json.dumps(item, ensure_ascii=False)
        self.assertEqual(item["lang"], "zh")

    def test_detect_lang_mixed_product_names(self):
        self.assertEqual(detect_lang("Claude Haiku 5.5"), "en")
        self.assertEqual(detect_lang("陶哲轩转发抵制声明，数学界和OpenAI彻底撕破脸"), "zh")
        self.assertEqual(detect_lang("OpenAI全面上线GPT-6"), "zh")
        self.assertEqual(detect_lang("jialinyyzz/humanizer"), "en")

    def test_cn_ai_text_ignores_latin_substrings(self):
        self.assertFalse(cn_ai_text("Wayfair与雨果跨境达成战略合作，助推供应商高质量出海"))
        self.assertTrue(cn_ai_text("当年实习生要挑战李飞飞做世界模型"))
        self.assertTrue(cn_ai_text("OpenAI全面上线GPT-6"))
        self.assertTrue(cn_ai_text("AI家电，攻占黄金周"))
        self.assertFalse(cn_ai_text("华为和小米，开启新一轮涨价"))

    def test_accept_simon_skips_quotes(self):
        self.assertFalse(accept_simon("Quoting Ben Affleck", "Claude said something"))
        self.assertTrue(accept_simon("Claude Haiku 5.5", ""))
        self.assertFalse(accept_simon("A recipe for soup", "tomatoes"))


if __name__ == "__main__":
    unittest.main()
