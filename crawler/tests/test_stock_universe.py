# -*- coding: utf-8 -*-
"""전종목 검색·등록 인덱스(scripts/collect_live_data.py) 단위 테스트."""
import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "collect_live_data", os.path.join(ROOT, "scripts", "collect_live_data.py"))
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)

LIST_SAMPLE = {
    "stockType": "domestic",
    "itemCode": "207940",
    "stockName": "삼성바이오로직스",
    "sosok": "0",
    "closePrice": "1,424,000",
    "compareToPreviousClosePrice": "1,000",
    "fluctuationsRatio": "0.07",
    "accumulatedTradingVolume": "30,347",
    "marketValue": "659,183",
    "localTradedAt": "2026-11-17T12:51:22+09:00",
    "marketStatus": "OPEN",
    "stockExchangeType": {"code": "KS", "name": "KOSPI"},
}

WORLD_SAMPLE = {
    "reutersCode": "AMD.O",
    "stockName": "AMD(어드밴스드 마이크로 디바이시스)",
    "closePrice": "504.28",
    "compareToPreviousClosePrice": "-29.78",
    "fluctuationsRatio": "-5.58",
    "accumulatedTradingVolume": "37,115,577",
    "marketValueFull": "827,063,198,934",
    "localTradedAt": "2026-11-16T16:00:00-05:00",
    "marketStatus": "CLOSE",
    "currencyType": {"code": "USD"},
    "stockExchangeType": {"name": "NASDAQ"},
}

DAUM_SAMPLE = {
    "name": "알테오젠",
    "tradePrice": 175300.0,
    "per": 84.13,
    "pbr": 27.03,
    "eps": 2041.0,
    "bps": 6353.0,
    "marketCap": 12210510328200.0,
}


class TestUniverseParsing(unittest.TestCase):
    def test_market_list_item_normalized(self):
        entry = live.parse_market_list_item(LIST_SAMPLE)
        self.assertEqual(entry["code"], "207940")
        self.assertEqual(entry["name"], "삼성바이오로직스")
        self.assertEqual(entry["market"], "KOSPI")
        self.assertEqual(entry["price"], 1424000.0)
        self.assertAlmostEqual(entry["pct"], 0.07)
        self.assertEqual(entry["mcap_eok"], 659183.0)

    def test_market_list_item_rejects_non_numeric_code(self):
        bad = dict(LIST_SAMPLE, itemCode="KOSPI")
        self.assertIsNone(live.parse_market_list_item(bad))
        self.assertIsNone(live.parse_market_list_item(dict(LIST_SAMPLE, itemCode="", stockName="")))

    def test_market_list_item_defaults_market_from_sosok(self):
        item = dict(LIST_SAMPLE, sosok="1")
        item.pop("stockExchangeType")
        self.assertEqual(live.parse_market_list_item(item)["market"], "KOSDAQ")

    def test_world_item_strips_reuters_suffix(self):
        entry = live.parse_world_list_item(WORLD_SAMPLE)
        self.assertEqual(entry["symbol"], "AMD")
        self.assertEqual(entry["reuters"], "AMD.O")
        self.assertEqual(entry["market"], "NASDAQ")
        self.assertEqual(entry["price"], 504.28)
        self.assertAlmostEqual(entry["pct"], -5.58)
        self.assertEqual(entry["mcap"], 827063198934.0)

    def test_daum_fundamental_parsed(self):
        f = live.parse_daum_fundamental(DAUM_SAMPLE)
        self.assertAlmostEqual(f["per"], 84.13)
        self.assertAlmostEqual(f["pbr"], 27.03)
        self.assertAlmostEqual(f["eps"], 2041.0)
        self.assertIsNone(live.parse_daum_fundamental({"name": "x"}))


class TestBuildUniverse(unittest.TestCase):
    def setUp(self):
        self.kr = {
            "005930": {"code": "005930", "name": "삼성전자", "market": "KOSPI", "price": 192000.0,
                       "pct": -2.44, "mcap_eok": 11224855.0, "volume": 100.0,
                       "as_of": "2026-11-17T12:51:22+09:00", "status": "OPEN"},
            "196170": {"code": "196170", "name": "알테오젠", "market": "KOSDAQ", "price": 175300.0,
                       "pct": 2.1, "mcap_eok": 122105.0, "volume": 10.0,
                       "as_of": "2026-11-17T12:51:00+09:00", "status": "OPEN"},
        }
        self.us = {
            "AMD": {"symbol": "AMD", "reuters": "AMD.O", "name": "AMD(어드밴스드 마이크로 디바이시스)",
                    "market": "NASDAQ", "price": 504.28, "pct": -5.58, "mcap": 827063198934.0,
                    "volume": 1.0, "as_of": "2026-11-16T16:00:00-05:00", "status": "CLOSE"},
        }
        self.funds = {"date": "2026-11-17", "items": {"005930": {"per": 29.8, "pbr": 3.08,
                                                                 "eps": 6605.0, "bps": 63997.0, "d": "2026-11-17"}}}

    def test_row_shape_and_order(self):
        uni = live.build_universe(self.kr, self.us, self.funds)
        self.assertEqual(len(uni["kr"]["005930"]), 9)
        self.assertEqual(uni["kr"]["005930"][0], "삼성전자")
        self.assertEqual(uni["kr"]["005930"][1], "KOSPI")
        self.assertEqual(uni["kr"]["005930"][4], 11224855)
        self.assertEqual(uni["kr"]["005930"][5], 29.8)
        self.assertEqual(uni["kr"]["005930"][6], 3.08)

    def test_missing_fundamentals_are_null_not_fake(self):
        uni = live.build_universe(self.kr, self.us, self.funds)
        self.assertIsNone(uni["kr"]["196170"][5])
        self.assertIsNone(uni["kr"]["196170"][6])
        self.assertIsNone(uni["us"]["AMD"][5])
        self.assertIsNone(uni["us"]["AMD"][6])

    def test_file_level_metadata(self):
        uni = live.build_universe(self.kr, self.us, self.funds)
        self.assertEqual(uni["counts"]["kr"], 2)
        self.assertEqual(uni["counts"]["us"], 1)
        self.assertEqual(uni["as_of"]["kr"], "2026-11-17T12:51:22+09:00")
        self.assertEqual(uni["status"]["us"], "CLOSE")
        self.assertEqual(uni["fund_date"], "2026-11-17")
        self.assertIn("AMD", uni["us"])

    def test_universe_is_json_serializable_and_compact(self):
        uni = live.build_universe(self.kr, self.us, self.funds)
        blob = json.dumps(uni, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(blob), 1000)
        self.assertEqual(json.loads(blob)["kr"]["005930"][0], "삼성전자")

    def test_us_fundamentals_become_per_pbr(self):
        us_funds = {"date": "2026-11-17", "items": {"AMD": {"eps": 4.6, "pbr": 12.81}}}
        uni = live.build_universe(self.kr, self.us, self.funds, us_funds)
        row = uni["us"]["AMD"]
        self.assertAlmostEqual(row[5], round(504.28 / 4.6, 2))   # PER = 현재가 / TTM EPS
        self.assertAlmostEqual(row[6], 12.81)
        self.assertAlmostEqual(row[7], 4.6)
        self.assertAlmostEqual(row[8], round(504.28 / 12.81, 2))  # BPS = 현재가 / PBR
        self.assertEqual(uni["counts"]["us_fund"], 1)


class TestUSFundamentals(unittest.TestCase):
    def test_ttm_eps_is_last_four_quarters(self):
        summary = {"chartEps": {"columns": [["x", "2025.09.27", "2025.12.27", "2026.03.28", "2026.06.27", "2026.09.26"],
                                            ["EPS", "0.76", "0.92", "0.84", "1.38", "1.46"]]}}
        quarter = {"rowList": [{"title": "매출액", "columns": {}},
                                {"title": "PBR", "columns": {"2026.06.27": {"value": "12.00"},
                                                               "2026.09.26": {"value": "12.81"}}}]}
        f = live.parse_us_fundamentals(summary, quarter)
        self.assertAlmostEqual(f["eps"], round(0.92 + 0.84 + 1.38 + 1.46, 4))
        self.assertAlmostEqual(f["pbr"], 12.81)

    def test_missing_data_returns_none(self):
        self.assertIsNone(live.parse_us_fundamentals({}, {}))
        self.assertIsNone(live.parse_us_fundamentals({"chartEps": {"columns": []}}, {}))


class TestUniverseFileOnDisk(unittest.TestCase):
    """배포용 data/stock_universe.json 이 실제로 생성되어 있는지 확인한다."""

    def setUp(self):
        if not os.path.exists(live.UNIVERSE_PATH):
            self.skipTest("stock_universe.json 없음 (수집 전)")

    def test_universe_has_kr_us_and_rows(self):
        with open(live.UNIVERSE_PATH, encoding="utf-8") as f:
            uni = json.load(f)
        self.assertGreater(uni["counts"]["kr"], 1000)
        self.assertGreater(uni["counts"]["us"], 100)
        self.assertEqual(len(uni["kr"]), uni["counts"]["kr"])
        self.assertIn("005930", uni["kr"])
        self.assertEqual(uni["kr"]["005930"][0], "삼성전자")
        for code, row in list(uni["kr"].items())[:50]:
            self.assertEqual(len(row), 9, code)
            self.assertTrue(code.isdigit())
        for sym, row in list(uni["us"].items())[:50]:
            self.assertEqual(len(row), 9, sym)

    def test_us_symbols_list_present(self):
        symbols = live.load_us_symbols()
        self.assertGreater(len(symbols), 300)
        self.assertIn("AMD", symbols)
        self.assertIn("TSLA", symbols)


class TestCompanyNews(unittest.TestCase):
    """카드 뉴스 회전 수집 대상 선택 로직."""

    def setUp(self):
        self.universe = {
            "kr": {
                "005930": ["삼성전자", "KOSPI", 100.0, 1.0, 500000, 10.0, 1.0, 100.0, 1000.0],
                "196170": ["알테오젠", "KOSDAQ", 100.0, 1.0, 400000, None, None, None, None],
                "000001": ["소형주", "KOSDAQ", 100.0, 1.0, 10, None, None, None, None],
            },
            "us": {
                "AMD": ["AMD(어드밴스드 마이크로 디바이시스)", "NASDAQ", 100.0, 1.0, 900000, None, None, None, None],
            },
        }

    def test_targets_sorted_by_market_cap_and_us_name_cleaned(self):
        targets = live.company_news_targets(self.universe)
        self.assertEqual(list(targets.keys())[:2], ["005930", "196170"])
        self.assertEqual(targets["AMD"], "AMD")

    def test_missing_entries_are_selected_first(self):
        targets = live.company_news_targets(self.universe)
        selected = live.select_company_news_targets(targets, {"companies": {}})
        self.assertEqual(len(selected), min(live.COMPANY_NEWS_PER_RUN, len(targets)))
        self.assertIn("005930", selected)

    def test_fresh_entries_are_skipped(self):
        now = live.now_kst()
        fresh = {code: {"fetched_at": live.iso_kst(now), "articles": [{"title": "x"}]}
                 for code in live.company_news_targets(self.universe)}
        self.assertEqual(live.select_company_news_targets(live.company_news_targets(self.universe), {"companies": fresh}), [])

    def test_empty_articles_are_recollected(self):
        now = live.now_kst()
        stale = {code: {"fetched_at": live.iso_kst(now), "articles": []}
                 for code in live.company_news_targets(self.universe)}
        selected = live.select_company_news_targets(live.company_news_targets(self.universe), {"companies": stale})
        self.assertEqual(len(selected), len(stale))

    def test_collect_company_news_keeps_previous_on_failure(self):
        previous = {"companies": {"005930": {"name": "삼성전자", "fetched_at": "2026-11-17T00:00:00+09:00",
                                            "articles": [{"title": "옛 기사"}]}}}
        orig = live.fetch_news_for
        live.fetch_news_for = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
        try:
            res = live.collect_company_news(self.universe, previous, force=True)
        finally:
            live.fetch_news_for = orig
        self.assertEqual(res["fetched"], 0)
        self.assertTrue(res["errors"])
        self.assertEqual(res["companies"]["005930"]["articles"][0]["title"], "옛 기사")


if __name__ == "__main__":
    unittest.main()
