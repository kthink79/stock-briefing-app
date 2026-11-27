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


class TestKeywordThreeWayFilter(unittest.TestCase):
    """키워드 트레이싱 3중 필터 — ①단어 경계 ②복합 맥락 ③비증시 노이즈.

    사용자가 스크린샷으로 제보한 오탐 두 건(수학이란·신간 / 반정부시위 사형선고)이
    다시 들어오지 않고, 실제 중동·유가 기사는 그대로 남는지를 고정한다.
    """

    def _rule(self, keyword="미국 이란 전쟁", cfg=None):
        cfg = cfg or live.DEFAULT_CONFIG
        entry = next((k for k in cfg["trace_keywords"] if k["keyword"] == keyword), {})
        return live.keyword_rule(keyword, entry)

    def test_copula_and_suffix_iran_are_blocked(self):
        rule = self._rule()
        for title in (
            "AI가 난제를 푸는 시대에 인간에게 수학이란, 신간",
            "무엇이란 무엇인가…철학 입문서 출간",
            "장동혁, ‘조작 징계’ 사과 요구에…“탈당·창당이란 좋은 제도 있다”",
            "이란성 쌍둥이 부모의 고민",
        ):
            self.assertFalse(live.article_passes(title, rule), title)
        # 앞이 공백·문장부호·시작이고 뒤에 조사/합성어가 붙은 진짜 지명·국가는 통과
        self.assertTrue(live.token_present("이란", "이란 외무, 조현에 외국군 주둔은 분쟁 키워"))
        self.assertTrue(live.token_present("이란", "한-이란 외교장관 통화"))
        self.assertTrue(live.token_present("이란", "이란전쟁에 생사 기로"))

    def test_social_noise_is_blocked(self):
        rule = self._rule()
        self.assertFalse(live.article_passes(
            "이란서 반정부시위 가담 여성 6명 사형선고...고문도 당해", rule))
        self.assertTrue(live.is_noise_title("AI가 난제를 푸는 시대에 인간에게 수학이란, 신간"))
        self.assertTrue(live.is_noise_title("프로야구 개막전 매진"))
        # 경제·증시 신호가 함께 있는 기사는 노이즈 단어가 있어도 남긴다
        self.assertFalse(live.is_noise_title("프로야구단 인수전…구단주 주가 급등"))
        self.assertFalse(live.is_noise_title("엔비디아 실적 발표"))

    def test_geopolitical_articles_pass(self):
        rule = self._rule()
        self.assertTrue(live.article_passes("이란 외무, 조현에 외국군 주둔은 분쟁 키워", rule))
        self.assertTrue(live.article_passes("호르무즈 해협 기뢰·이란 긴장 유가 급등", rule))
        self.assertTrue(live.article_passes("이란 드론에 쿠웨이트서 이탈리아인 2명 사망…유조선도 기뢰 피격", rule))

    def test_iran_alone_without_geopolitical_context_is_blocked(self):
        rule = self._rule()
        self.assertFalse(live.article_passes("이란, 대통령 선거 일정 발표", rule))
        self.assertFalse(live.article_passes("서울 다산콜센터 해외서도 벤치마킹…이란에 '128콜센터' 설립", rule))

    def test_filter_relevant_applies_all_three_filters(self):
        arts = [
            {"title": "AI가 난제를 푸는 시대에 인간에게 수학이란, 신간"},
            {"title": "이란 외무, 조현에 외국군 주둔은 분쟁 키워"},
            {"title": "이란 반정부시위 가담 여성 6명 사형선고…고문도 당해"},
            {"title": "이란·튀니지…120다산콜재단 상담 운영 경험 배운다"},
            {"title": "호르무즈 해협 기뢰·이란 긴장 유가 급등"},
        ]
        picked = live.filter_relevant(arts, ["이란"], live.GEOPOLITICAL_CONTEXT)
        self.assertEqual([a["title"] for a in picked],
                         ["이란 외무, 조현에 외국군 주둔은 분쟁 키워",
                          "호르무즈 해협 기뢰·이란 긴장 유가 급등"])

    def test_latin_tokens_use_word_boundary(self):
        self.assertFalse(live.token_present("AI", "TRAIN SERVICES DELAYED"))
        self.assertFalse(live.token_present("AI", "PAID SUBSCRIPTIONS"))
        self.assertTrue(live.token_present("AI", "NEW AI MODEL RELEASED"))
        self.assertTrue(live.token_present("HBM", "SK하이닉스 HBM4 양산 확대"))

    def test_multi_word_keyword_requires_every_word(self):
        rule = live.keyword_rule("미국 이란 전쟁")  # require 없음 → 세 낱말 모두 필요
        self.assertTrue(rule["require_all"])
        self.assertTrue(live.keyword_matches("미국-이란 전쟁 발발 가능성", rule))
        self.assertFalse(live.keyword_matches("우크라이나 전쟁 장기화", rule))

    def test_sanitize_news_cleans_existing_articles(self):
        news = {
            "keywords": {
                "미국 이란 전쟁": {"articles": [
                    {"title": "AI가 난제를 푸는 시대에 인간에게 수학이란, 신간"},
                    {"title": "이란 외무, 조현에 외국군 주둔은 분쟁 키워"},
                    {"title": "이란 반정부시위 가담 여성 6명 사형선고…고문도 당해"},
                    {"title": "이란 외무, 조현에 외국군 주둔은 분쟁 키워"},  # 중복
                ]},
                "엔비디아": {"articles": [
                    {"title": "엔비디아 실적 발표"},
                    {"title": "무관한 기사"},
                ]},
            },
            "pool": {"이란": {"articles": [
                {"title": "장동혁, “탈당·창당이란 좋은 제도 있다”"},
                {"title": "이란군, 호르무즈서 LNG선·유조선 기뢰 충돌 화재"},
            ]}},
            "companies": {}, "sectors": [],
        }
        report = live.sanitize_news(news, live.DEFAULT_CONFIG)
        self.assertEqual(report["removed"], 5)  # 오탐 3 + 중복 1 + 엔비디아 무관 1 + pool 1
        self.assertEqual([a["title"] for a in news["keywords"]["미국 이란 전쟁"]["articles"]],
                         ["이란 외무, 조현에 외국군 주둔은 분쟁 키워"])
        self.assertEqual([a["title"] for a in news["keywords"]["엔비디아"]["articles"]],
                         ["엔비디아 실적 발표"])
        self.assertEqual([a["title"] for a in news["pool"]["이란"]["articles"]],
                         ["이란군, 호르무즈서 LNG선·유조선 기뢰 충돌 화재"])

    def test_save_merge_rechecks_stored_keyword_tags(self):
        news = {"keywords": {"미국 이란 전쟁": {"query": "이란", "articles": []}},
                "companies": {}, "errors": []}
        snapshot = {"status": "live", "fetched_at": live.iso_kst(live.now_kst()),
                    "news": [
                        {"title": "서울 다산콜센터…이란에 '128콜센터' 설립",
                         "keywords": ["미국 이란 전쟁"], "keyword": "미국 이란 전쟁",
                         "published_at": live.iso_kst(live.now_kst())},
                        {"title": "이란 외무, 조현에 외국군 주둔은 분쟁 키워",
                         "keywords": ["미국 이란 전쟁"], "keyword": "미국 이란 전쟁",
                         "published_at": live.iso_kst(live.now_kst())},
                    ],
                    "top_stories": [], "calendar": [], "errors": []}
        merged = live.merge_save_news(news, snapshot, live.DEFAULT_CONFIG)
        titles = [a["title"] for a in merged["keywords"]["미국 이란 전쟁"]["articles"]]
        self.assertEqual(titles, ["이란 외무, 조현에 외국군 주둔은 분쟁 키워"])

    def test_default_config_carries_filter_rules(self):
        iran = next(k for k in live.DEFAULT_CONFIG["trace_keywords"] if k["id"] == "trace_iran")
        for word in ("전쟁", "분쟁", "호르무즈", "유가", "원유", "이스라엘", "미국",
                     "트럼프", "제재", "미사일", "드론", "공습", "기뢰", "외교", "핵", "중동"):
            self.assertIn(word, iran["context_any"])
        self.assertIn("noise_exclude", live.DEFAULT_CONFIG["news_filter"])
        self.assertIn("market_context", live.DEFAULT_CONFIG["news_filter"])

    def test_committed_config_has_three_way_filter(self):
        path = os.path.join(ROOT, "data", "config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        iran = next(k for k in cfg["trace_keywords"] if k["id"] == "trace_iran")
        self.assertIn("호르무즈", iran["context_any"])
        self.assertTrue(cfg["news_filter"]["noise_exclude"])
        for word in ("신간", "사형", "반정부", "야구", "연예"):
            self.assertIn(word, cfg["news_filter"]["noise_exclude"])


if __name__ == "__main__":
    unittest.main()
