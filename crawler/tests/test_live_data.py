# -*- coding: utf-8 -*-
"""실시간 데이터 수집기(scripts/collect_live_data.py) 단위 테스트."""
import importlib.util
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "collect_live_data", os.path.join(ROOT, "scripts", "collect_live_data.py"))
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)

KST = timezone(timedelta(hours=9))

DOMESTIC_SAMPLE = {
    "itemCode": "005930",
    "stockName": "삼성전자",
    "closePrice": "196,800",
    "compareToPreviousClosePrice": "0",
    "fluctuationsRatio": "0.00",
    "marketStatus": "PREOPEN",
    "localTradedAt": "2026-11-17T08:26:05.123208+09:00",
    "currencyType": {"code": "KRW"},
    "marketValueFull": "1,150,547,630,054,400",
    "overMarketPriceInfo": {
        "tradingSessionType": "PRE_MARKET",
        "overPrice": "191,400",
        "compareToPreviousClosePrice": "-5,400",
        "fluctuationsRatio": "-2.74",
        "localTradedAt": "2026-11-17T08:26:00+09:00",
        "accumulatedTradingVolumeRaw": "1265316",
    },
}

US_SAMPLE = {
    "reutersCode": "NVDA.O",
    "stockName": "엔비디아",
    "closePrice": "156.14",
    "compareToPreviousClosePrice": "-6.71",
    "fluctuationsRatio": "-4.12",
    "marketStatus": "CLOSE",
    "localTradedAt": "2026-11-16T16:00:00-05:00",
    "currencyType": {"code": "USD"},
}

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"HBM" - Google 뉴스</title>
<item>
  <title>[단독] SK하이닉스 HBM4 양산 확대 - 연합뉴스</title>
  <link>https://news.google.com/rss/articles/AAA</link>
  <pubDate>{recent}</pubDate>
  <source url="https://www.yna.co.kr">연합뉴스</source>
</item>
<item>
  <title>[단독] SK하이닉스 HBM4 양산 확대 - 연합뉴스</title>
  <link>https://news.google.com/rss/articles/BBB</link>
  <pubDate>{recent}</pubDate>
  <source url="https://www.yna.co.kr">연합뉴스</source>
</item>
<item>
  <title>삼성전자, HBM4 초기 양산 수율 개선 - 한국경제</title>
  <link>https://news.google.com/rss/articles/CCC</link>
  <pubDate>{recent}</pubDate>
  <source url="https://www.hankyung.com">한국경제</source>
</item>
<item>
  <title>아주 오래된 기사 - 옛신문</title>
  <link>https://news.google.com/rss/articles/DDD</link>
  <pubDate>{old}</pubDate>
  <source url="https://old.example">옛신문</source>
</item>
</channel></rss>
"""


class TestNumericParsing(unittest.TestCase):
    def test_num_handles_formats(self):
        self.assertEqual(live.num("1,336.50"), 1336.50)
        self.assertEqual(live.num("-0.24%"), -0.24)
        self.assertIsNone(live.num("-"))
        self.assertIsNone(live.num(""))
        self.assertIsNone(live.num(None))
        self.assertEqual(live.num(5.129), 5.129)


class TestNaverQuote(unittest.TestCase):
    def test_domestic_preopen_with_extended_session(self):
        q = live.parse_naver_quote(DOMESTIC_SAMPLE)
        self.assertEqual(q["name"], "삼성전자")
        self.assertEqual(q["price"], 196800)
        self.assertEqual(q["prev_close"], 196800)  # 종가 - 등락폭
        self.assertEqual(q["market_status"], "PREOPEN")
        self.assertEqual(q["currency"], "KRW")
        self.assertAlmostEqual(q["market_value"], 1150547630054400)
        self.assertEqual(q["extended"]["session"], "PRE_MARKET")
        self.assertEqual(q["extended"]["price"], 191400)
        self.assertEqual(q["extended"]["pct"], -2.74)

    def test_us_quote_prev_close(self):
        q = live.parse_naver_quote(US_SAMPLE)
        self.assertEqual(q["currency"], "USD")
        self.assertAlmostEqual(q["price"], 156.14)
        self.assertAlmostEqual(q["prev_close"], 162.85)
        self.assertAlmostEqual(q["pct"], -4.12)
        self.assertNotIn("extended", q)


class TestGoogleNewsRss(unittest.TestCase):
    def _xml(self):
        now = datetime.now(KST)
        recent = (now - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        old = (now - timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        return RSS_SAMPLE.format(recent=recent, old=old).encode("utf-8")

    def test_parse_and_dedupe(self):
        arts = live.parse_google_news_rss(self._xml(), "HBM", max_age_days=10)
        self.assertEqual(len(arts), 2)  # 중복 1건 제거 + 30일 전 기사 제외
        titles = [a["title"] for a in arts]
        self.assertIn("[단독] SK하이닉스 HBM4 양산 확대", titles)
        self.assertIn("삼성전자, HBM4 초기 양산 수율 개선", titles)
        self.assertEqual(arts[0]["source"], "연합뉴스")
        self.assertRegex(arts[0]["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(arts[0]["time"], r"^\d{2}:\d{2}$")
        self.assertIn("+09:00", arts[0]["published_at"])

    def test_title_helpers(self):
        self.assertEqual(live.clean_title("[속보] 삼성전자 급등 - 연합뉴스", "연합뉴스"), "[속보] 삼성전자 급등")
        self.assertEqual(live.clean_title("삼성전자 급등 | 한국경제", "한국경제"), "삼성전자 급등")
        self.assertEqual(live.title_key("[속보] 삼성전자 급등!"), live.title_key("삼성전자 급등"))


class TestRelevanceFilter(unittest.TestCase):
    def test_keyword_tokens(self):
        self.assertEqual(live.keyword_tokens("미국 이란 전쟁"), ["미국", "이란", "전쟁"])
        self.assertEqual(live.keyword_tokens("HBM"), ["HBM"])
        self.assertEqual(live.keyword_tokens(""), [])

    def test_filter_relevant_drops_noise(self):
        arts = [
            {"title": "SK하이닉스 HBM4 양산 확대"},
            {"title": "부동산 대신 AI 데이터센터…中 은행 돈몰이"},
            {"title": "미국 항모 집결, 이란 대응 전략은?"},
        ]
        picked = live.filter_relevant(arts, ["이란"])
        self.assertEqual(len(picked), 1)
        self.assertIn("이란", picked[0]["title"])
        self.assertEqual(len(live.filter_relevant(arts, [])), 3)


class TestBriefing(unittest.TestCase):
    def _market(self):
        return {
            "indices": {
                "DOW": {"price": 51608.17, "pct": -1.41},
                "NASDAQ": {"price": 24509.76, "pct": -2.49},
                "SP500": {"price": 7416.59, "pct": -1.92},
                "SOX": {"price": 9610.65, "pct": -5.28},
                "KOSPI": {"price": 5100.50, "pct": 0.0},
            },
            "rates": {"US10Y": {"price": 5.129, "bp": -2.39}, "US2Y": {"price": 4.832}},
            "fx": {"USDKRW": {"price": 1336.50, "pct": -0.11}},
            "stocks": {
                "NVDA": {"name": "엔비디아", "price": 156.14, "pct": -4.12},
                "AAPL": {"name": "애플", "price": 352.78, "pct": -0.98},
                "005930": {"name": "삼성전자", "price": 196800, "pct": 0.0,
                           "extended": {"price": 191400, "pct": -2.74}},
            },
        }

    def test_headline_uses_real_numbers(self):
        b = live.build_briefing(self._market(), {"kr": ["005930"], "us": ["NVDA", "AAPL"]},
                                {"sectors": []})
        self.assertIn("51,608.17", b["headline"])
        self.assertIn("-5.28%", b["headline"])
        joined = " ".join(c["detail"] for c in b["checkpoints"])
        self.assertIn("5.129%", joined)
        self.assertIn("1,336.50", joined)
        self.assertIn("-2.74%", joined)   # 장전 시간외 수치
        self.assertTrue(any(c["title"] == "반도체·기술주 흐름" for c in b["checkpoints"]))

    def test_no_data_means_empty_not_invented(self):
        b = live.build_briefing({"indices": {}, "rates": {}, "fx": {}, "stocks": {}},
                                {"kr": [], "us": []}, {"sectors": []})
        self.assertEqual(b["headline"], "")
        self.assertEqual(b["checkpoints"], [])
        self.assertEqual(b["sector_news"], [])


class TestChangeDetection(unittest.TestCase):
    def test_significant_change_ignores_timestamp(self):
        base = {"generated_at": "a", "stocks": {"005930": {"price": 1, "as_of": "x"}}}
        same = {"generated_at": "b", "stocks": {"005930": {"price": 1, "as_of": "y"}}}
        moved = {"generated_at": "b", "stocks": {"005930": {"price": 2, "as_of": "y"}}}
        self.assertFalse(live.significant_change(base, same))
        self.assertTrue(live.significant_change(base, moved))

    def test_is_fresh(self):
        now = live.now_kst()
        self.assertTrue(live.is_fresh(live.iso_kst(now - timedelta(minutes=5)), 20))
        self.assertFalse(live.is_fresh(live.iso_kst(now - timedelta(minutes=30)), 20))
        self.assertFalse(live.is_fresh(None, 20))


class TestConfig(unittest.TestCase):
    def test_default_config_keys(self):
        for key in ("kr_stocks", "us_stocks", "kr_indices", "us_indices", "rates", "fx",
                    "trace_keywords", "sector_news", "keyword_pool"):
            self.assertIn(key, live.DEFAULT_CONFIG)
        self.assertEqual(len(live.DEFAULT_CONFIG["us_indices"]), 4)

    def test_committed_config_matches_shape(self):
        path = os.path.join(ROOT, "data", "config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("005930", cfg["kr_stocks"])
        self.assertTrue(any(k["keyword"] == "엔비디아" for k in cfg["trace_keywords"]))


class TestPolicyRates(unittest.TestCase):
    """기준금리: 공식 소스 파싱 + 실패 시 직전 성공값 유지(하드코딩 복귀 금지)."""

    def test_parse_nyfed_policy(self):
        payload = {"refRates": [{"effectiveDate": "2026-11-18", "type": "EFFR",
                               "percentRate": 3.88,
                               "targetRateFrom": 3.75, "targetRateTo": 4.00}]}
        p = live.parse_nyfed_policy(payload)
        self.assertEqual(p["low"], 3.75)
        self.assertEqual(p["high"], 4.00)
        self.assertEqual(p["effr"], 3.88)
        self.assertEqual(p["as_of"], "2026-11-18")
        self.assertIn("뉴욕연은", p["source"])

    def test_parse_nyfed_without_range_is_none(self):
        self.assertIsNone(live.parse_nyfed_policy({"refRates": []}))
        self.assertIsNone(live.parse_nyfed_policy({"refRates": [{"effectiveDate": "2026-11-18"}]}))

    def test_parse_fred_target_takes_last_row(self):
        csv_text = ("observation_date,DFEDTARU,DFEDTARL\n"
                    "2026-10-28,4.25,4.00\n"
                    "2026-10-29,4.00,3.75\n")
        p = live.parse_fred_target(csv_text)
        self.assertEqual(p["low"], 3.75)
        self.assertEqual(p["high"], 4.00)
        self.assertEqual(p["as_of"], "2026-10-29")

    def test_parse_fred_target_skips_empty_cells(self):
        csv_text = "observation_date,DFEDTARU,DFEDTARL\n2026-11-19,4.00,3.75\n2026-11-20,,\n"
        p = live.parse_fred_target(csv_text)
        self.assertEqual((p["low"], p["high"], p["as_of"]), (3.75, 4.00, "2026-11-19"))
        self.assertIsNone(live.parse_fred_target("nope"))

    def test_parse_bok_base_rate(self):
        html = ('<div class="table tac"><table class="fixed">'
                '<thead><tr><th colspan="2">변경일자</th><th class="fb">기준금리</th></tr></thead>'
                '<tbody>'
                '<tr><td class="fb">2026</td><td>08월 27일</td><td>3.00</td></tr>\n'
                '<tr><td class="fb">2026</td><td>07월 16일</td><td>2.75</td></tr>\n'
                '</tbody></table></div>')
        p = live.parse_bok_base_rate(html)
        self.assertEqual(p["rate"], 3.00)
        self.assertEqual(p["as_of"], "2026-08-27")
        self.assertIn("한국은행", p["source"])
        self.assertIsNone(live.parse_bok_base_rate("<html>표 없음</html>"))

    def test_collect_uses_live_values(self):
        orig_us, orig_kr = live.fetch_us_policy_rate, live.fetch_kr_policy_rate
        try:
            live.fetch_us_policy_rate = lambda: {"low": 3.75, "high": 4.00, "effr": 3.88,
                                                 "as_of": "2026-11-18", "source": "s"}
            live.fetch_kr_policy_rate = lambda: {"rate": 3.00, "as_of": "2026-08-27", "source": "s"}
            out, errors = live.collect_policy_rates({})
        finally:
            live.fetch_us_policy_rate, live.fetch_kr_policy_rate = orig_us, orig_kr
        self.assertEqual(errors, [])
        self.assertEqual(out["US"]["status"], "ok")
        self.assertFalse(out["US"]["stale"])
        self.assertEqual(out["KR"]["rate"], 3.00)
        self.assertTrue(out["US"]["checked_at"])

    def test_collect_keeps_last_success_on_failure(self):
        """조회 실패 시 하드코딩 기본값(5.25~5.50 / 3.25)으로 되돌아가면 안 된다."""
        previous = {"market": {"policy_rates": {
            "US": {"low": 3.75, "high": 4.00, "as_of": "2026-11-18",
                   "status": "stale", "stale": True, "checked_at": "2026-11-20T09:00:00+09:00"},
            "KR": {"rate": 3.00, "as_of": "2026-08-27",
                   "status": "stale", "stale": True, "checked_at": "2026-11-20T09:00:00+09:00"}}}}

        def boom():
            raise RuntimeError("network down")

        orig_us, orig_kr = live.fetch_us_policy_rate, live.fetch_kr_policy_rate
        try:
            live.fetch_us_policy_rate = boom
            live.fetch_kr_policy_rate = boom
            out, errors = live.collect_policy_rates(previous)
        finally:
            live.fetch_us_policy_rate, live.fetch_kr_policy_rate = orig_us, orig_kr
        self.assertEqual((out["US"]["low"], out["US"]["high"]), (3.75, 4.00))
        self.assertEqual(out["KR"]["rate"], 3.00)
        self.assertEqual(out["US"]["as_of"], "2026-11-18")
        self.assertTrue(out["US"]["stale"])
        self.assertEqual(out["US"]["status"], "stale")
        self.assertTrue(any("유지" in e for e in errors))

    def test_collect_without_previous_marks_unavailable(self):
        def boom():
            raise RuntimeError("network down")

        orig_us, orig_kr = live.fetch_us_policy_rate, live.fetch_kr_policy_rate
        try:
            live.fetch_us_policy_rate = boom
            live.fetch_kr_policy_rate = boom
            out, _ = live.collect_policy_rates({})
        finally:
            live.fetch_us_policy_rate, live.fetch_kr_policy_rate = orig_us, orig_kr
        self.assertEqual(out["US"]["status"], "unavailable")
        self.assertIsNone(out["US"].get("low"))

    def test_collect_reuses_fresh_cache(self):
        previous = {"market": {"policy_rates": {
            "US": {"low": 3.75, "high": 4.00, "as_of": "2026-11-18", "status": "ok",
                   "stale": False, "checked_at": live.iso_kst(live.now_kst())},
            "KR": {"rate": 3.00, "as_of": "2026-08-27", "status": "ok",
                   "stale": False, "checked_at": live.iso_kst(live.now_kst())}}}}

        def boom():
            raise RuntimeError("should not be called")

        orig_us, orig_kr = live.fetch_us_policy_rate, live.fetch_kr_policy_rate
        try:
            live.fetch_us_policy_rate = boom
            live.fetch_kr_policy_rate = boom
            out, errors = live.collect_policy_rates(previous)
        finally:
            live.fetch_us_policy_rate, live.fetch_kr_policy_rate = orig_us, orig_kr
        self.assertEqual(errors, [])
        self.assertEqual(out["US"]["status"], "ok")

    def test_significant_change_ignores_check_time(self):
        a = {"generated_at": "a", "market": {"policy_rates": {
            "US": {"low": 3.75, "high": 4.00, "as_of": "2026-11-18", "status": "ok",
                   "stale": False, "checked_at": "2026-11-20T09:00:00+09:00"}}}}
        b = {"generated_at": "b", "market": {"policy_rates": {
            "US": {"low": 3.75, "high": 4.00, "as_of": "2026-11-18", "status": "ok",
                   "stale": True, "checked_at": "2026-11-20T10:00:00+09:00"}}}}
        c = {"generated_at": "b", "market": {"policy_rates": {
            "US": {"low": 3.50, "high": 3.75, "as_of": "2026-12-10", "status": "ok",
                   "stale": False, "checked_at": "2026-11-20T10:00:00+09:00"}}}}
        self.assertFalse(live.significant_change(a, b))
        self.assertTrue(live.significant_change(a, c))

    def test_html_has_no_hardcoded_policy_rates(self):
        """카드에 하드코딩된 5.25~5.50 / 3.25 가 남아 있으면 실시간화가 아니다."""
        for rel in ("index.html", os.path.join("android", "app", "src", "main", "assets", "index.html")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                html = f.read()
            self.assertNotIn("5.25 ~ 5.50%", html, rel)
            self.assertNotIn(">3.25%<", html, rel)
            self.assertIn('id="policy-us-val"', html, rel)
            self.assertIn('id="policy-kr-val"', html, rel)

    def test_app_js_renders_policy_rates(self):
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("renderPolicyRates", js)
        self.assertIn("market.policy_rates", js)


if __name__ == "__main__":
    unittest.main()
