# -*- coding: utf-8 -*-
"""매수·매도 시점 분석 엔진(signal-engine.js) 테스트.

signal-engine.js 는 브라우저 모듈이지만 점수 계산(compute)은 DOM 없이 동작한다.
quickjs 로 파일을 그대로 실행해 규칙·가중치·신뢰도·뉴스 감성을 검증한다.
quickjs/esprima 가 없는 환경에서는 건너뛴다(다른 테스트는 그대로 통과).
"""
import json
import math
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_JS = os.path.join(ROOT, "signal-engine.js")

try:  # pragma: no cover - 선택적 의존성
    import quickjs
    HAVE_JS = True
except Exception:  # pragma: no cover
    HAVE_JS = False


def make_bars(start, drift, count=130, noise=0.004, vol=1000000):
    """일봉 생성: start 가격에서 drift(일별 수익률)만큼 움직이는 합성 시계열."""
    from datetime import datetime, timedelta
    bars = []
    price = float(start)
    day = datetime(2026, 5, 4)
    for i in range(count):
        wobble = 1 + noise * math.sin(i / 3.0)
        close = price * wobble
        open_ = price
        high = max(open_, close) * 1.004
        low = min(open_, close) * 0.996
        bars.append([int((day + timedelta(days=i)).strftime("%Y%m%d")),
                     int(open_), int(high), int(low), int(close),
                     int(vol * (1 + 0.3 * math.sin(i / 2.0))), 45.0])
        price *= (1 + drift)
    return bars


UP_BARS = make_bars(10000, 0.006)
DOWN_BARS = make_bars(60000, -0.006)

SUPPLY_BUY = {"asof": "20261125", "foreign_rate": 45.9, "days": [
    {"d": "20261125", "f": 1200000, "o": 300000, "i": -1500000, "rate": 45.9},
    {"d": "20261124", "f": 900000, "o": 200000, "i": -1100000, "rate": 45.8},
    {"d": "20261123", "f": 400000, "o": 150000, "i": -550000, "rate": 45.7},
]}
SUPPLY_SELL = {"asof": "20261125", "foreign_rate": 40.0, "days": [
    {"d": "20261125", "f": -1200000, "o": -300000, "i": 1500000, "rate": 40.0},
    {"d": "20261124", "f": -900000, "o": -200000, "i": 1100000, "rate": 40.2},
    {"d": "20261123", "f": -400000, "o": -150000, "i": 550000, "rate": 40.4},
]}
VALUATION_CHEAP = {"per": 7.5, "pbr": 0.9, "high52": 12000, "low52": 9000, "eps": 1500}
VALUATION_RICH = {"per": 62.0, "pbr": 9.0, "high52": 12000, "low52": 3000, "eps": 100}


def js_literal(value):
    return json.dumps(value, ensure_ascii=False)


@unittest.skipUnless(HAVE_JS, "quickjs 미설치 — JS 엔진 테스트 건너뜀")
class TestSignalEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ENGINE_JS, encoding="utf-8") as f:
            src = f.read()
        cls.ctx = quickjs.Context()
        cls.ctx.eval(src)
        assert cls.ctx.eval("typeof StockSignal") == "object"

    def compute(self, data, settings=None):
        ctx = self.ctx
        ctx.eval("var __data = " + js_literal(data) + ";")
        ctx.eval("var __settings = " + js_literal(settings) + ";")
        return json.loads(ctx.eval(
            "JSON.stringify(StockSignal.compute(__data, __settings))"))

    # ---- 지표
    def test_smas_ordered_in_uptrend(self):
        ind = json.loads(self.ctx.eval(
            "var __ind = StockSignal.computeIndicators(" + js_literal(UP_BARS) + ");"
            "JSON.stringify(__ind)"))
        self.assertGreater(ind["ma5"], ind["ma20"])
        self.assertGreater(ind["ma20"], ind["ma60"])
        self.assertGreater(ind["close"], ind["ma20"])
        self.assertGreater(ind["rsi"], 50)

    # ---- 기본 점수 방향
    def test_uptrend_scores_above_downtrend(self):
        up = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                           "supply": SUPPLY_BUY, "valuation": VALUATION_CHEAP,
                           "news": {"score": 0, "count": 0, "pos": [], "neg": []}})
        down = self.compute({"bars": DOWN_BARS, "currency": "KRW", "market": "KR",
                             "supply": SUPPLY_SELL, "valuation": VALUATION_RICH,
                             "news": {"score": 0, "count": 0, "pos": [], "neg": []}})
        self.assertIsNotNone(up["score"])
        self.assertGreater(up["score"], down["score"])
        self.assertGreaterEqual(up["score"], 55)
        self.assertLessEqual(down["score"], 45)
        self.assertIn(up["grade"]["label"], ("매수우위", "매수관심", "관망"))

    # ---- 신뢰도·동적 가중치
    def test_confidence_drops_when_data_missing(self):
        full = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                             "supply": SUPPLY_BUY, "valuation": VALUATION_CHEAP,
                             "news": {"score": 0.4, "count": 5, "pos": [{"title": "수주"}], "neg": []}})
        chart_only = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR"})
        self.assertGreater(full["confidence"], chart_only["confidence"])
        self.assertEqual(full["confidence"], 100)
        self.assertLess(chart_only["confidence"], 50)
        # 데이터가 없는 그룹은 점수에서 빠지고 남은 그룹으로 다시 정규화된다
        self.assertEqual([g["available"] for g in chart_only["groups"]], [True, False, False, False])
        self.assertEqual(chart_only["score"], round(
            [g for g in chart_only["groups"] if g["key"] == "chart"][0]["score"], 1))

    def test_us_market_marks_supply_unavailable(self):
        res = self.compute({"bars": UP_BARS, "currency": "USD", "market": "US",
                            "valuation": {"per": 20, "pbr": 3, "high52": 200, "low52": 80}})
        supply = [g for g in res["groups"] if g["key"] == "supply"][0]
        self.assertFalse(supply["available"])
        reason = [r for r in res["rules"] if r["id"] == "supply"][0]["reason"]
        self.assertIn("해외", reason)

    # ---- 가중치·규칙 ON/OFF
    def test_weights_change_result(self):
        base = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                             "supply": SUPPLY_SELL, "valuation": VALUATION_RICH})
        chart_heavy = self.compute(
            {"bars": UP_BARS, "currency": "KRW", "market": "KR",
             "supply": SUPPLY_SELL, "valuation": VALUATION_RICH},
            {"weights": {"chart": 100, "news": 0, "supply": 0, "valuation": 0}, "rules": {}})
        self.assertGreater(chart_heavy["score"], base["score"])
        chart_group = [g for g in chart_heavy["groups"] if g["key"] == "chart"][0]
        self.assertEqual(chart_heavy["score"], round(chart_group["score"], 1))

    def test_rules_can_be_turned_off(self):
        res = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                            "supply": SUPPLY_BUY, "valuation": VALUATION_CHEAP},
                           {"weights": {"chart": 40, "news": 25, "supply": 20, "valuation": 15},
                            "rules": {"rsi": False, "macd": False, "bollinger": False,
                                      "volume": False, "news": False}})
        off = [r["id"] for r in res["rules"] if r.get("off")]
        self.assertEqual(sorted(off), sorted(["rsi", "macd", "bollinger", "volume", "news"]))
        # 남은 규칙(이동평균·수급·밸류에이션)으로 계산된다
        self.assertEqual(res["counts"]["enabled"], 3)
        self.assertTrue(all(g["enabled"] is False for g in res["groups"] if g["key"] == "news"))

    def test_all_weights_zero_gives_no_score(self):
        res = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR"},
                           {"weights": {"chart": 0, "news": 0, "supply": 0, "valuation": 0},
                            "rules": {}})
        self.assertIsNone(res["score"])
        self.assertEqual(res["grade"]["label"], "데이터 없음")

    # ---- 개별 규칙
    def test_rsi_oversold_rule(self):
        bars = make_bars(50000, -0.02, count=60)
        res = self.compute({"bars": bars, "currency": "KRW", "market": "KR"})
        rsi = [r for r in res["rules"] if r["id"] == "rsi"][0]
        self.assertTrue(rsi["available"])
        self.assertGreater(rsi["score"], 70)

    def test_volume_rule_flags_surge(self):
        bars = [b[:] for b in UP_BARS]
        bars[-1][5] = int(bars[-1][5] * 3)
        bars[-1][4] = int(bars[-2][4] * 1.05)
        res = self.compute({"bars": bars, "currency": "KRW", "market": "KR"})
        vol = [r for r in res["rules"] if r["id"] == "volume"][0]
        self.assertGreater(vol["score"], 70)

    def test_valuation_prefers_cheap(self):
        cheap = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                              "valuation": VALUATION_CHEAP})
        rich = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                             "valuation": VALUATION_RICH})
        cv = [r for r in cheap["rules"] if r["id"] == "valuation"][0]["score"]
        rv = [r for r in rich["rules"] if r["id"] == "valuation"][0]["score"]
        self.assertGreater(cv, rv)

    # ---- 뉴스 감성
    def test_news_sentiment_signs(self):
        pos = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                            "news": {"score": 0.8, "count": 4,
                                     "pos": [{"title": "수주"}], "neg": []}})
        neg = self.compute({"bars": UP_BARS, "currency": "KRW", "market": "KR",
                            "news": {"score": -0.8, "count": 4,
                                     "pos": [], "neg": [{"title": "적자"}]}})
        pn = [r for r in pos["rules"] if r["id"] == "news"][0]["score"]
        nn = [r for r in neg["rules"] if r["id"] == "news"][0]["score"]
        self.assertGreater(pn, 70)
        self.assertLess(nn, 30)

    def test_analyze_news_keywords(self):
        now = "2026-11-26T05:00:00+09:00"
        ctx = self.ctx
        ctx.eval("var __now = Date.parse(" + js_literal(now) + ");")
        articles = [
            {"title": "삼성전자, 500단 낸드 최대 실적…목표가 상향", "source": "전자신문",
             "published_at": "2026-11-25T09:00:00+09:00", "url": "u1"},
            {"title": "삼성전자 급락…외국인 순매도에 실적 부진 우려", "source": "서울경제",
             "published_at": "2026-11-25T10:00:00+09:00", "url": "u2"},
        ]
        out = json.loads(ctx.eval(
            "JSON.stringify(StockSignal.analyzeNews(" + js_literal(articles) + ", __now))"))
        self.assertEqual(out["count"], 2)
        self.assertGreaterEqual(out["posCount"], 1)
        self.assertGreaterEqual(out["negCount"], 1)
        self.assertLess(abs(out["score"]), 0.6)  # 호재·악재가 섞이면 극단으로 치우치지 않는다

    def test_analyze_news_time_decay(self):
        ctx = self.ctx
        ctx.eval("var __now2 = Date.parse('2026-11-26T05:00:00+09:00');")
        fresh = [{"title": "삼성전자 목표가 상향", "published_at": "2026-11-25T09:00:00+09:00"}]
        old = [{"title": "삼성전자 목표가 상향", "published_at": "2026-11-01T09:00:00+09:00"}]
        a = json.loads(ctx.eval("JSON.stringify(StockSignal.analyzeNews(" + js_literal(fresh) + ", __now2))"))
        b = json.loads(ctx.eval("JSON.stringify(StockSignal.analyzeNews(" + js_literal(old) + ", __now2))"))
        self.assertGreater(a["score"], b["score"])

    def test_negation_phrase_flips(self):
        ctx = self.ctx
        pos = json.loads(ctx.eval(
            "JSON.stringify(StockSignal.scoreArticle('삼성전자 3분기 적자 탈출', ''))"))
        neg = json.loads(ctx.eval(
            "JSON.stringify(StockSignal.scoreArticle('삼성전자 3분기 적자 전환', ''))"))
        self.assertGreater(pos["polarity"], 0)
        self.assertLess(neg["polarity"], 0)

    # ---- 카드 렌더링(문자열)
    def test_card_html_contains_expected_parts(self):
        ctx = self.ctx
        ctx.eval("var __res = StockSignal.compute(" + js_literal({
            "bars": UP_BARS, "currency": "KRW", "market": "KR",
            "supply": SUPPLY_BUY, "valuation": VALUATION_CHEAP,
            "news": {"score": 0.5, "count": 3, "pos": [{"title": "삼성전자 최대 실적", "source": "전자신문",
                                                          "date": "2026-11-25", "url": "https://example.com/a"}],
                     "neg": [], "posCount": 1, "negCount": 0}}) + ", null);")
        html = ctx.eval("StockSignal.__cardHtml(__res, {code: '005930', record: {updated_at: '2026-11-26T05:00:00+09:00'}, livePrice: null})")
        for token in ["매수·매도 시점 분석", "신뢰도", "차트", "뉴스", "수급", "밸류에이션",
                      "호재", "악재", "삼성전자 최대 실적", "https://example.com/a"]:
            self.assertIn(token, html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_settings_html_renders_sliders(self):
        html = self.ctx.eval("StockSignal.__settingsHtml()")
        for group in ["chart", "news", "supply", "valuation"]:
            self.assertIn('id="sig-weight-' + group + '"', html)
        self.assertIn("StockSignal.setRule", html)
        self.assertIn("이동평균(5·20·60·120)", html)

    # ---- 설정 정규화
    def test_settings_normalized(self):
        settings = json.loads(self.ctx.eval(
            "JSON.stringify(StockSignal.normalizeSettings({weights:{chart:500, news:-3}}))"))
        self.assertEqual(settings["weights"]["chart"], 100)
        self.assertEqual(settings["weights"]["news"], 0)
        self.assertEqual(settings["weights"]["supply"], 20)
        self.assertTrue(settings["rules"]["ma_trend"])

    # ---- 등급 경계
    def test_grade_boundaries(self):
        ctx = self.ctx
        for score, label in [(85, "매수우위"), (65, "매수관심"), (50, "관망"),
                             (35, "매도주의"), (10, "매도우위")]:
            got = json.loads(ctx.eval(
                "JSON.stringify(StockSignal.grade(%s, 80))" % score))
            self.assertEqual(got["label"], label, score)
        low_conf = json.loads(ctx.eval("JSON.stringify(StockSignal.grade(65, 20))"))
        self.assertIn("신뢰도", low_conf["label"])


@unittest.skipUnless(HAVE_JS, "quickjs 미설치 — JS 엔진 테스트 건너뜀")
class TestEngineHtml(unittest.TestCase):
    """index.html·app.js 연결(마운트 지점·스크립트 로드) 확인."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            cls.html = f.read()
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as f:
            cls.app = f.read()

    def test_company_tab_has_signal_card(self):
        ok = 'id="comp-signal-card"' in self.html and 'id="comp-signal-body"' in self.html
        self.assertTrue(ok, "기업 탭에 신호 카드 컨테이너가 없습니다")

    def test_settings_tab_has_weight_card(self):
        self.assertIn('id="sig-settings-body"', self.html)

    def test_engine_script_loaded_before_app(self):
        engine = self.html.find("signal-engine.js")
        app = self.html.find("app.js")
        self.assertTrue(0 < engine < app, "signal-engine.js 가 app.js 보다 먼저 로드되어야 합니다")

    def test_app_mounts_engine(self):
        self.assertIn("StockSignal.mount", self.app)

    def test_settings_tab_renders_engine_settings(self):
        # 설정 탭 전환(switchTab)은 index.html 인라인 스크립트에 있으므로 거기서 호출한다
        self.assertIn("StockSignal.renderSettings", self.html)


if __name__ == "__main__":
    unittest.main()
