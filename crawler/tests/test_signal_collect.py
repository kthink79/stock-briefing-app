# -*- coding: utf-8 -*-
"""매수·매도 시점 분석 데이터 수집기(scripts/collect_signals.py) 테스트."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import collect_signals as sig  # noqa: E402

# 실제 네이버 siseJson 응답 형태(작은따옴표 헤더 + 공백/탭 + 이중따옴표 행)
NAVER_SISE_BODY = """

 [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],

\t
\t\t
["20260601", 319500, 354500, 319500, 349000, 45052488, 48.3],
\t\t
["20260602", 360500, 370000, 342000, 360500, 44720282, 48.07],
\t\t
["20261125", 178500, 179900, 166000, 166900, 21801583, 45.8]
"""

YAHOO_BODY = {
    "chart": {
        "result": [{
            "meta": {"currency": "USD", "regularMarketPrice": 147.99,
                     "fiftyTwoWeekHigh": 243.37, "fiftyTwoWeekLow": 124.5},
            "timestamp": [1764081000, 1764167400, 1764253800],
            "indicators": {"quote": [{
                "open": [180.0, 181.0, None],
                "high": [182.0, 183.0, None],
                "low": [178.0, 179.0, None],
                "close": [181.0, 182.5, None],
                "volume": [1000000, 1200000, None],
            }]},
        }],
        "error": None,
    }
}

INTEGRATION_BODY = {
    "itemCode": "005930",
    "stockName": "삼성전자",
    "totalInfos": [
        {"code": "per", "key": "PER", "value": "5.25배"},
        {"code": "pbr", "key": "PBR", "value": "1.80배"},
        {"code": "eps", "key": "EPS", "value": "31,809원"},
        {"code": "bps", "key": "BPS", "value": "92,903원"},
        {"code": "highPriceOf52Weeks", "key": "52주 최고", "value": "380,000"},
        {"code": "lowPriceOf52Weeks", "key": "52주 최저", "value": "99,300"},
        {"code": "foreignRate", "key": "외인소진율", "value": "45.90%"},
        {"code": "dividendYieldRatio", "key": "배당수익률", "value": "1.00%"},
    ],
    "dealTrendInfos": [
        {"bizdate": "20261125", "foreignerPureBuyQuant": "-5,763,083",
         "organPureBuyQuant": "-948,317", "individualPureBuyQuant": "+6,534,711",
         "foreignerHoldRatio": "45.80%"},
        {"bizdate": "20261124", "foreignerPureBuyQuant": "+2,348,072",
         "organPureBuyQuant": "+1,176,494", "individualPureBuyQuant": "-3,400,633",
         "foreignerHoldRatio": "45.90%"},
    ],
}


def fake_record(code, market, name, funds=None):
    return {
        "schema": 1, "code": code, "name": name, "market": market,
        "currency": "USD" if market == "US" else "KRW",
        "updated_at": sig._iso(sig._now()),
        "price": 100,
        "bars": [[20261101 + i, 100, 101, 99, 100, 1000, 45.0] for i in range(30)],
        "supply": {"asof": "20261125", "days": [], "foreign_rate": 45.0},
        "valuation": {"per": 10, "pbr": 1.5},
    }


class ParseTest(unittest.TestCase):
    def test_parse_naver_sise_rows(self):
        bars = sig.parse_naver_sise(NAVER_SISE_BODY)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0], [20260601, 319500, 354500, 319500, 349000, 45052488, 48.3])
        self.assertEqual(bars[-1][0], 20261125)
        self.assertEqual(bars[-1][6], 45.8)

    def test_parse_naver_sise_trims_to_120(self):
        rows = "\n".join(
            '["2026%02d%02d", 100, 110, 90, %d, 1000, 40.0],' % (1 + i // 28, 1 + i % 28, 100 + i)
            for i in range(200))
        bars = sig.parse_naver_sise(rows)
        self.assertEqual(len(bars), 120)

    def test_parse_naver_sise_ignores_garbage(self):
        self.assertEqual(sig.parse_naver_sise(""), [])
        self.assertEqual(sig.parse_naver_sise("not a json"), [])

    def test_parse_yahoo_chart(self):
        bars, meta = sig.parse_yahoo_chart(YAHOO_BODY)
        self.assertEqual(len(bars), 2)          # close 가 null 인 봉은 버린다
        self.assertEqual(meta["currency"], "USD")
        self.assertEqual(bars[0][4], 181)
        self.assertIsNone(bars[0][6])           # 미국 종목은 외국인 소진율 없음

    def test_parse_yahoo_chart_empty(self):
        bars, meta = sig.parse_yahoo_chart({"chart": {"result": None, "error": "x"}})
        self.assertEqual(bars, [])
        self.assertEqual(meta, {})

    def test_parse_integration(self):
        out = sig.parse_integration(INTEGRATION_BODY)
        val = out["valuation"]
        self.assertEqual(val["per"], 5.25)
        self.assertEqual(val["eps"], 31809.0)
        self.assertEqual(val["high52"], 380000.0)
        self.assertEqual(val["foreign_rate"], 45.9)
        supply = out["supply"]
        self.assertEqual(supply["asof"], "20261125")
        self.assertEqual(len(supply["days"]), 2)
        # 순매매 수량 문자열의 부호·쉼표 처리
        self.assertEqual(supply["days"][0]["f"], -5763083.0)
        self.assertEqual(supply["days"][1]["o"], 1176494.0)


class CoverageTest(unittest.TestCase):
    def universe(self):
        return {
            "kr": {
                "005930": ["삼성전자", "KOSPI", 166900, -11.9, 9757439000000, 5.25, 1.8],
                "000660": ["SK하이닉스", "KOSPI", 500000, 1.0, 300000000000, 4.0, 2.0],
                "012450": ["한화에어로스페이스", "KOSPI", 622000, -2.0, 28000000000, 22, 3.3],
                "035720": ["카카오", "KOSPI", 32050, 1.0, 14000000000, 29, 1.2],
            },
            "us": {
                "NVDA": ["엔비디아", "NASDAQ", 148, 1.0, 3600000000000],
                "AAPL": ["애플", "NASDAQ", 354, 1.0, 5300000000000],
                "TSLA": ["테슬라", "NASDAQ", 300, 1.0, 900000000000],
            },
        }

    def test_config_codes_come_first(self):
        config = {"kr_stocks": {"035720": "카카오"}, "us_stocks": {"TSLA": "TSLA.O"}}
        targets = sig.build_coverage(config, self.universe(), {}, kr_limit=10, us_limit=10)
        codes = [t["code"] for t in targets]
        self.assertEqual(codes[:2], ["035720", "TSLA"])
        self.assertEqual(len(codes), len(set(codes)))

    def test_limits_and_market_cap_order(self):
        config = {"kr_stocks": {}, "us_stocks": {}}
        targets = sig.build_coverage(config, self.universe(), {}, kr_limit=2, us_limit=1)
        kr = [t["code"] for t in targets if t["market"] == "KR"]
        us = [t["code"] for t in targets if t["market"] == "US"]
        self.assertEqual(kr, ["005930", "000660"])       # 시가총액 순
        self.assertEqual(us, ["AAPL"])
        self.assertEqual(len(targets), 3)

    def test_stale_codes_are_prioritized(self):
        config = {"kr_stocks": {}, "us_stocks": {}}
        now_iso = sig._iso(sig._now())
        prev = {"codes": {
            "005930": {"u": now_iso},
            "000660": {"u": "2026-01-01T09:00:00+09:00"},   # 아주 오래됨 → 먼저 갱신
            "012450": {"u": now_iso},
            "035720": {"u": now_iso},
        }}
        targets = sig.build_coverage(config, self.universe(), prev, kr_limit=4, us_limit=0)
        self.assertEqual(targets[0]["code"], "000660")

    def test_never_collected_codes_come_before_stale_ones(self):
        config = {"kr_stocks": {}, "us_stocks": {}}
        prev = {"codes": {"005930": {"u": sig._iso(sig._now())}}}
        targets = sig.build_coverage(config, self.universe(), prev, kr_limit=4, us_limit=0)
        self.assertNotEqual(targets[0]["code"], "005930")   # 한 번도 못 받은 종목이 먼저

    def test_needs_refresh(self):
        now = sig._now()
        self.assertTrue(sig.needs_refresh(None, now, 20, 6))
        fresh = {"updated_at": sig._iso(now), "bars": [[int(now.strftime("%Y%m%d")), 1, 1, 1, 1, 1, None]]}
        self.assertFalse(sig.needs_refresh(fresh, now, 20, 6))
        old = {"updated_at": "2026-01-01T09:00:00+09:00",
               "bars": [[20260101, 1, 1, 1, 1, 1, None]]}
        self.assertTrue(sig.needs_refresh(old, now, 20, 6))


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="signal_test_")
        self.patches = [
            mock.patch.object(sig, "SIGNAL_DIR", self.tmp),
            mock.patch.object(sig, "HISTORY_DIR", os.path.join(self.tmp, "h")),
            mock.patch.object(sig, "MANIFEST_PATH", os.path.join(self.tmp, "manifest.json")),
        ]
        for p in self.patches:
            p.start()
        os.makedirs(os.path.join(self.tmp, "h"), exist_ok=True)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def coverage(self):
        return [
            {"code": "005930", "market": "KR", "name": "삼성전자", "rank": 0},
            {"code": "NVDA", "market": "US", "name": "엔비디아", "rank": 0},
        ]

    def test_collect_writes_records_and_manifest(self):
        res = sig.collect(coverage=self.coverage(), limit=10, budget=30,
                          fetch=fake_record, workers=1)
        self.assertEqual(res["ok"], 2)
        self.assertEqual(res["failed"], 0)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "h"))),
                         ["005930.json", "NVDA.json"])
        with open(os.path.join(self.tmp, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["counts"]["kr"], 1)
        self.assertEqual(manifest["counts"]["us"], 1)
        self.assertEqual(manifest["counts"]["with_bars"], 2)
        self.assertEqual(manifest["codes"]["005930"]["b"], 30)

    def test_second_run_skips_fresh_codes(self):
        sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=fake_record)
        calls = []

        def counting_fetch(code, market, name, funds=None):
            calls.append(code)
            return fake_record(code, market, name, funds)

        res = sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=counting_fetch)
        self.assertEqual(calls, [])          # 방금 받은 종목은 다시 받지 않는다
        self.assertEqual(res["ok"], 0)

    def test_failure_keeps_previous_file(self):
        sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=fake_record)
        before = open(os.path.join(self.tmp, "h", "005930.json"), encoding="utf-8").read()

        def broken(code, market, name, funds=None):
            raise RuntimeError("network down")

        res = sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=broken, force=True)
        self.assertEqual(res["ok"], 0)
        self.assertEqual(res["failed"], 2)
        after = open(os.path.join(self.tmp, "h", "005930.json"), encoding="utf-8").read()
        self.assertEqual(before, after)      # 실패해도 직전 데이터 유지(가짜 값 금지)
        self.assertTrue(any("network down" in e for e in res["errors"]))

    def test_short_bars_rejected_when_previous_exists(self):
        sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=fake_record)

        def short(code, market, name, funds=None):
            rec = fake_record(code, market, name, funds)
            rec["bars"] = rec["bars"][:3]
            return rec

        res = sig.collect(coverage=self.coverage(), limit=10, budget=30, fetch=short, force=True)
        self.assertEqual(res["failed"], 2)
        self.assertTrue(any("봉 부족" in e for e in res["errors"]))

    def test_stage_for_ci_is_noop_locally(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_ACTIONS", None)
            with mock.patch("subprocess.run") as run:
                sig.stage_for_ci([os.path.join(self.tmp, "manifest.json")])
                run.assert_not_called()

    def test_build_record_naver_uses_fundamentals_fallback(self):
        funds = {"items": {"005930": {"per": 28.68, "pbr": 2.96, "eps": 6605.0, "bps": 63997.0}}}
        with mock.patch.object(sig, "fetch_naver_history", return_value=sig.parse_naver_sise(NAVER_SISE_BODY)), \
             mock.patch.object(sig, "fetch_integration", side_effect=RuntimeError("404")):
            rec = sig.build_record("005930", "KR", "삼성전자", funds)
        self.assertEqual(rec["market"], "KR")
        self.assertEqual(rec["price"], 166900)
        self.assertEqual(rec["valuation"]["per"], 28.68)     # integration 실패 → 기존 재무지표로 보완
        self.assertEqual(rec["supply"]["days"], [])

    def test_build_record_us_computes_per(self):
        with mock.patch.object(sig, "fetch_yahoo_history",
                               return_value=sig.parse_yahoo_chart(YAHOO_BODY)):
            rec = sig.build_record("NVDA", "US", "엔비디아", {"us": {"items": {"NVDA": {"eps": 10.0, "pbr": 11.3}}}})
        self.assertEqual(rec["valuation"]["high52"], 243.37)
        self.assertEqual(rec["valuation"]["pbr"], 11.3)
        self.assertAlmostEqual(rec["valuation"]["per"], 14.8, places=1)   # 147.99 / 10

    def test_build_record_without_bars_is_none(self):
        with mock.patch.object(sig, "fetch_naver_history", return_value=[]), \
             mock.patch.object(sig, "fetch_integration", return_value={"supply": {}, "valuation": {}}):
            self.assertIsNone(sig.build_record("000000", "KR", "없는종목"))


if __name__ == "__main__":
    unittest.main()
