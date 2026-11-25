# -*- coding: utf-8 -*-
"""SAVE(세이브티커) 연동 테스트 — 파서·병합·폴백.

saveticker.com 은 Cloudflare 봇 차단 뒤에 있어 GitHub Actions 익명 요청은 대개 막힌다.
그래서 (1) 응답 파싱과 (2) 키워드/기업 매칭, (3) 차단·실패 시 저장된 스냅샷 폴백이
본 수집을 멈추지 않는지를 실제 응답 모양으로 고정한다.
"""
import importlib.util
import json
import os
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "collect_live_data", os.path.join(ROOT, "scripts", "collect_live_data.py"))
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)

CF_PAGE = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
           b"<body><script>window._cf_chl_opt={cType:'interactive'}</script></body></html>")

CFG = {
    "kr_stocks": {"005930": "삼성전자", "000660": "SK하이닉스", "012450": "한화에어로스페이스"},
    "us_stocks": {"NVDA": "NVDA.O", "AMD": "AMD.O"},
    "trace_keywords": [
        {"id": "trace_nvda", "keyword": "엔비디아", "query": "엔비디아", "require": ["엔비디아"]},
        {"id": "trace_hbm", "keyword": "HBM", "query": "HBM", "require": ["HBM"]},
        {"id": "trace_iran", "keyword": "미국 이란 전쟁", "query": "이란", "require": ["이란"]},
        {"id": "trace_trump", "keyword": "트럼프", "query": "트럼프", "require": ["트럼프"]},
    ],
}

NOW = live.now_kst()


def _raw(id="news_1", title="엔비디아 실적 발표", source="reuters", channel=None,
         hours_ago=1, tickers=None, content="본문", top=False):
    created = (NOW - timedelta(hours=hours_ago)).astimezone(live.timezone.utc)
    return {
        "id": id,
        "title": title,
        "content": content,
        "source": source,
        "channel": channel,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "view_count": 1234,
        "tickers": tickers or [],
        "content_labels": [{"id": 7, "name": "속보"}],
        "is_top_story": top,
        "is_deleted": False,
    }


class SaveParserTest(unittest.TestCase):
    def test_cloudflare_challenge_is_detected(self):
        self.assertIn("Cloudflare", live.save_blocked_reason(CF_PAGE))
        self.assertEqual(live.save_blocked_reason(b'{"news_list":[]}'), "")

    def test_item_becomes_article_with_badge_and_url(self):
        art = live.parse_save_item(_raw(channel="save-pick", source="로이터"), [], {}, now=NOW)
        self.assertEqual(art["badge"], "SAVE PICK")
        self.assertEqual(art["origin"], "SAVE")
        self.assertEqual(art["url"], "https://www.saveticker.com/news/news_1")
        self.assertRegex(art["published_at"], r"\+09:00$")
        self.assertEqual(art["labels"], ["속보"])

    def test_reuters_and_plain_badges(self):
        # 매체 구분은 출처(source)가 하고, 배지는 SAVE PICK / SAVE 두 가지로 둔다
        self.assertEqual(live.save_article_kind({"source": "reuters"}), "SAVE")
        self.assertEqual(live.save_article_kind({"source": "블룸버그"}), "SAVE")
        self.assertEqual(live.save_article_source({"source": "reuters"}), "로이터")
        self.assertEqual(live.save_article_source({"source": "FOX뉴스"}), "FOX뉴스")

    def test_old_item_is_dropped(self):
        self.assertIsNone(live.parse_save_item(_raw(hours_ago=24 * 30), [], {}, max_age_days=10, now=NOW))

    def test_keyword_matching_uses_configured_require_tokens(self):
        rules = live.save_keyword_rules(CFG)
        art = live.parse_save_item(
            _raw(title="트럼프, 이란과 간접 협상 지속 시사", content="합의 없으면 타격"),
            rules, {}, now=NOW)
        self.assertIn("미국 이란 전쟁", art["keywords"])
        self.assertIn("트럼프", art["keywords"])
        self.assertEqual(art["keyword"], "미국 이란 전쟁")

    def test_company_matching_by_save_ticker(self):
        codes = live.save_ticker_codes(CFG)
        raw = _raw(title="SK하이닉스 관련", tickers=[
            {"id": 1, "symbol": "SKHY", "name": "SK하이닉스 ADR"},
            {"id": 2, "symbol": "005930.KR", "name": "삼성전자"},
            {"id": 3, "symbol": "AVGO", "name": "브로드컴"},
        ])
        art = live.parse_save_item(raw, [], codes, now=NOW)
        self.assertEqual(art["companies"], ["000660", "005930"])

    def test_payload_dedupes_same_title(self):
        raw = _raw(title="(카더라) 미 SEC, 엔비디아 회계 문제 조사")
        dup = dict(raw, id="news_2")
        arts = live.parse_save_news_payload({"news_list": [raw, dup]}, CFG, now=NOW)
        self.assertEqual(len(arts), 1)

    def test_calendar_events_are_kst_sorted_and_trimmed(self):
        # 고정 날짜를 쓰면 실행일이 지나면서 '지난 일정'이 되어 실패한다 — 현재 시각 기준으로 만든다
        soon = (NOW + timedelta(hours=2)).replace(second=0, microsecond=0)
        payload = {"events": [
            {"id": "1", "title": "10월 PCE가격지수 ★★★", "event_date": soon.strftime("%Y-%m-%dT%H:%M:%S"),
             "content": [{"type": "text", "content": "이전: 3.6%\n예측: 3.8%"}], "market": "us"},
            {"id": "2", "title": "지난 일정", "event_date": "2020-01-01T09:00:00", "content": []},
        ]}
        events = live.parse_save_calendar_payload(payload, days=14, now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["importance"], 3)
        self.assertIn("예측", events[0]["note"])


class SaveSnapshotTest(unittest.TestCase):
    def _bundle(self):
        return {
            "top": {"news_list": [_raw(id="news_top", title="SAVE PICK 주요뉴스", channel="save-pick", top=True)]},
            "keywords": {"news_list": [_raw(id="news_kw", title="엔비디아 신제품 공개")]},
            "tickers": {"news_list": [_raw(id="news_tk", title="삼성전자 실적 발표",
                                           tickers=[{"id": 1, "symbol": "005930.KR", "name": "삼성전자"}])]},
            "calendar": {"events": [{"id": "9", "title": "FOMC ★★★",
                                     "event_date": NOW.strftime("%Y-%m-%d") + "T23:00:00",
                                     "content": [], "market": "us"}]},
            "via": "browser-session",
        }

    def test_snapshot_merges_all_sources_and_marks_top_stories(self):
        snap = live.build_save_snapshot(self._bundle(), CFG, "2026-11-23T10:00:00+09:00", now=NOW)
        self.assertEqual(snap["count"], 3)
        self.assertEqual(snap["fetched_via"], "browser-session")
        self.assertEqual([a["url"] for a in snap["top_stories"]],
                         ["https://www.saveticker.com/news/news_top"])
        self.assertEqual(len(snap["calendar"]), 1)
        self.assertEqual(snap["interests"]["keywords"][0], "엔비디아")
        self.assertIn("005930.KR", snap["interests"]["tickers"])


class SaveMergeTest(unittest.TestCase):
    def _news(self):
        return {
            "keywords": {"엔비디아": {"query": "엔비디아", "articles": [
                {"keyword": "엔비디아", "title": "엔비디아 신제품 공개", "source": "AI타임스",
                 "url": "https://news.google.com/x", "date": "2026-11-23", "time": "09:00",
                 "published_at": live.iso_kst(NOW - timedelta(hours=2))},
            ]}},
            "companies": {},
            "errors": [],
        }

    def _snapshot(self, extra=None):
        arts = [
            live.parse_save_item(_raw(id="news_a", title="엔비디아 신제품 공개", hours_ago=3),
                                 live.save_keyword_rules(CFG), live.save_ticker_codes(CFG), now=NOW),
            live.parse_save_item(_raw(id="news_b", title="트럼프, 이란 협상 시사", hours_ago=4),
                                 live.save_keyword_rules(CFG), live.save_ticker_codes(CFG), now=NOW),
            live.parse_save_item(_raw(id="news_c", title="삼성전자 파운드리 수주", hours_ago=5,
                                      tickers=[{"id": 1, "symbol": "005930.KR", "name": "삼성전자"}]),
                                 live.save_keyword_rules(CFG), live.save_ticker_codes(CFG), now=NOW),
        ]
        return {"status": "cached", "fetched_at": "2026-11-23T10:00:00+09:00",
                "news": [a for a in arts if a] + (extra or []), "top_stories": [], "calendar": [],
                "errors": []}

    def test_merge_adds_save_articles_and_skips_duplicate_titles(self):
        news = live.merge_save_news(self._news(), self._snapshot(), CFG)
        titles = [a["title"] for a in news["keywords"]["엔비디아"]["articles"]]
        self.assertEqual(titles.count("엔비디아 신제품 공개"), 1)  # 구글 기사와 중복 → 추가 안 함
        self.assertEqual(news["save"]["status"], "cached")
        self.assertTrue(news["save"]["stale"])
        self.assertEqual(news["save"]["count"], 3)

    def test_merge_creates_missing_buckets_and_tags_companies(self):
        news = live.merge_save_news({"keywords": {}, "companies": {}, "errors": []},
                                    self._snapshot(), CFG)
        self.assertIn("미국 이란 전쟁", news["keywords"])
        self.assertTrue(news["keywords"]["미국 이란 전쟁"]["articles"])
        self.assertIn("005930", news["companies"])
        self.assertEqual(news["companies"]["005930"]["articles"][0]["origin"], "SAVE")
        self.assertEqual(news["save"]["added"]["companies"], 1)

    def test_merge_caps_save_articles_per_bucket(self):
        extra = []
        for i in range(12):
            art = live.parse_save_item(
                _raw(id=f"news_x{i}", title=f"엔비디아 관련 단독 {i}보", hours_ago=6 + i),
                live.save_keyword_rules(CFG), live.save_ticker_codes(CFG), now=NOW)
            if art:
                extra.append(art)
        news = live.merge_save_news({"keywords": {}, "companies": {}, "errors": []},
                                    self._snapshot(extra), CFG)
        save_articles = [a for a in news["keywords"]["엔비디아"]["articles"]
                         if a.get("origin") == "SAVE"]
        self.assertLessEqual(len(save_articles), live.SAVE_PER_BUCKET)


class SaveFallbackTest(unittest.TestCase):
    def setUp(self):
        self._orig = live.fetch_save_bundle

    def tearDown(self):
        live.fetch_save_bundle = self._orig

    def test_blocked_fetch_falls_back_to_saved_snapshot(self):
        def boom(cfg):
            raise live.SaveFetchError("Cloudflare 봇 차단(Just a moment)")
        live.fetch_save_bundle = boom
        prev = {"status": "live", "fetched_at": "2026-11-23T09:00:00+09:00",
                "news": [{"title": "저장된 기사", "published_at": live.iso_kst(NOW - timedelta(hours=1))}],
                "top_stories": [], "calendar": []}
        snap = live.collect_save(CFG, prev)
        self.assertEqual(snap["status"], "cached")
        self.assertTrue(snap["stale"])
        self.assertEqual(snap["news"][0]["title"], "저장된 기사")
        self.assertIn("Cloudflare", snap["errors"][0])

    def test_blocked_fetch_without_cache_is_unavailable_not_an_exception(self):
        def boom(cfg):
            raise live.SaveFetchError("HTTP 403")
        live.fetch_save_bundle = boom
        snap = live.collect_save(CFG, {})
        self.assertEqual(snap["status"], "unavailable")
        self.assertEqual(snap["news"], [])

    def test_unexpected_error_also_returns_a_snapshot(self):
        def boom(cfg):
            raise ValueError("boom")
        live.fetch_save_bundle = boom
        snap = live.collect_save(CFG, {})
        self.assertEqual(snap["status"], "unavailable")

    def test_disabled_flag_skips_network(self):
        def boom(cfg):
            raise AssertionError("호출되면 안 됨")
        live.fetch_save_bundle = boom
        snap = live.collect_save(dict(CFG, save_enabled=False), {})
        self.assertEqual(snap["status"], "disabled")

    def test_live_snapshot_is_written_payload_shape(self):
        live.fetch_save_bundle = lambda cfg: {
            "top": {"news_list": [_raw(title="SAVE PICK 헤드라인", channel="save-pick", top=True)]},
            "calendar": {"events": []},
            "via": "live",
        }
        snap = live.collect_save(CFG, {})
        self.assertEqual(snap["status"], "live")
        self.assertEqual(snap["count"], 1)
        json.dumps(snap)  # 저장 가능한 형태(직렬화 오류 없음)


if __name__ == "__main__":
    unittest.main()


class SaveSnapshotPdfScriptTest(unittest.TestCase):
    """브라우저 인쇄본(PDF) 텍스트 복원 — 스냅샷 재생성 스크립트."""

    def _script(self):
        import importlib.util as iu
        path = os.path.join(ROOT, "scripts", "save_snapshot_from_pdfs.py")
        spec = iu.spec_from_file_location("save_snapshot_from_pdfs", path)
        mod = iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_repair_restores_json_text(self):
        mod = self._script()
        printed = ('{"news_list": [{"title": "엔비디아\x01실적\x01발표",'
                   '"content": "본문\\n둘째줄"}]}\u0001pretty\x01print\x01적용\n'
                   '{"id": 1}\npretty\x01print\x01적용')
        repaired = mod.repair_text(printed)
        self.assertNotIn("\x01", repaired)
        self.assertNotIn("pretty", repaired)
        self.assertNotIn("\n", repaired)
        self.assertIn("엔비디아 실적 발표", repaired)

    def test_extract_json_from_file(self):
        import tempfile
        mod = self._script()
        payload = {"events": [{"id": "1", "title": "FOMC ★★★", "event_date": "2026-11-25T22:30:00"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            path = f.name
        try:
            self.assertEqual(mod.extract_save_json(path)["events"][0]["title"], "FOMC ★★★")
        finally:
            os.unlink(path)


class SaveMergeRefreshTest(unittest.TestCase):
    """같은 SAVE 기사가 새 스냅샷에서 출처·배지가 바뀌었을 때 갱신되는지."""

    def test_existing_save_article_is_refreshed_not_duplicated(self):
        old = {"keyword": "엔비디아", "title": "엔비디아 신제품 공개",
               "source": "reuters(SAVE)", "badge": "SAVE·로이터", "origin": "SAVE",
               "url": "https://www.saveticker.com/news/news_old",
               "published_at": live.iso_kst(NOW - timedelta(hours=3))}
        news = {"keywords": {"엔비디아": {"query": "엔비디아", "articles": [old]}},
                "companies": {}, "errors": []}
        art = live.parse_save_item(_raw(id="news_new", title="엔비디아 신제품 공개", source="reuters",
                                        hours_ago=3),
                                   live.save_keyword_rules(CFG), live.save_ticker_codes(CFG), now=NOW)
        snap = {"status": "live", "fetched_at": live.iso_kst(NOW), "news": [art],
                "top_stories": [], "calendar": [], "errors": []}
        merged = live.merge_save_news(news, snap, CFG)
        arts = merged["keywords"]["엔비디아"]["articles"]
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["source"], "로이터")
        self.assertEqual(arts[0]["badge"], "SAVE")
        self.assertEqual(arts[0]["url"], "https://www.saveticker.com/news/news_new")
        self.assertEqual(merged["save"]["added"]["keywords"], 0)
