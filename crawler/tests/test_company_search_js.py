# -*- coding: utf-8 -*-
"""종목 검색·선택 등록(브라우저 앱 로직) 테스트.

app.js에서 검색 관련 순수 함수를 그대로 떼어내 quickjs로 실행해
'코드/이름 정확 일치 → 접두 → 포함 → 시가총액 순' 규칙을 검증한다.
quickjs/esprima가 없는 환경에서는 건너뛴다(다른 단위 테스트는 그대로 통과).
"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_JS = os.path.join(ROOT, "app.js")
INDEX_HTML = os.path.join(ROOT, "index.html")

try:  # pragma: no cover - 선택적 의존성
    import esprima
    import quickjs
    HAVE_JS = True
except Exception:  # pragma: no cover
    HAVE_JS = False

SEARCH_FUNCS = ["normalizeSearchText", "universeRowToEntry", "buildUniverseIndex", "searchCompanies"]

FIXTURE_UNIVERSE = {
    "schema": 1,
    "as_of": {"kr": "2026-11-17T12:51:22+09:00", "us": "2026-11-16T16:00:00-05:00"},
    "status": {"kr": "OPEN", "us": "CLOSE"},
    "kr": {
        "005930": ["삼성전자", "KOSPI", 192000.0, -2.44, 11224855, 29.8, 3.08, 6605.0, 63997.0],
        "005935": ["삼성전자우", "KOSPI", 155300.0, -3.72, 1246082, None, None, None, None],
        "207940": ["삼성바이오로직스", "KOSPI", 1424000.0, 0.07, 659183, 53.85, 8.9, 26423.0, 159902.0],
        "196170": ["알테오젠", "KOSDAQ", 175300.0, 2.1, 122105, 84.13, 27.03, 2041.0, 6353.0],
        "035720": ["카카오", "KOSPI", 32050.0, -1.23, 141982, 29.03, 1.27, 1118.0, 25625.0],
    },
    "us": {
        "AMD": ["AMD(어드밴스드 마이크로 디바이시스)", "NASDAQ", 504.28, -5.58, 827063198934.0, None, None, None, None],
        "TSLA": ["테슬라", "NASDAQ", 379.74, -2.69, 1501020302414.0, None, None, None, None],
    },
    "counts": {"kr": 5, "us": 2, "fund": 3},
}

# 삼성SDI(006400) 종목명 미표시 이슈 재현용 데이터
FIXTURE_UNIVERSE_SDI = {
    "schema": 1,
    "as_of": {"kr": "2026-11-19T18:34:00+09:00", "us": "2026-11-18T16:00:00-05:00"},
    "status": {"kr": "CLOSE", "us": "CLOSE"},
    "kr": {
        "005930": ["삼성전자", "KOSPI", 196400.0, 0.87, 11482000, 7.7, 2.03, 25506.0, 96875.0],
        "006400": ["삼성SDI", "KOSPI", 427500.0, -1.61, 344503, -49.4, 1.59, -8796.0, 272485.0],
        "006405": ["삼성SDI우", "KOSPI", 175400.0, -2.93, 2838, 0.0, 0.0, 0.0, 0.0],
    },
    "us": {
        "TSLA": ["테슬라", "NASDAQ", 379.74, -2.69, 1501020302414.0, None, None, None, None],
    },
    "counts": {"kr": 3, "us": 1, "fund": 1},
}


def _extract_functions(src: str) -> str:
    """app.js에서 SEARCH_FUNCS 함수 선언만 원문 그대로 뽑아낸다."""
    tree = esprima.parseScript(src, {"range": True})
    chunks = []
    found = set()
    for node in tree.body:
        if getattr(node, "type", "") == "FunctionDeclaration" and node.id.name in SEARCH_FUNCS:
            chunks.append(src[node.range[0]:node.range[1]])
            found.add(node.id.name)
    missing = [n for n in SEARCH_FUNCS if n not in found]
    if missing:
        raise AssertionError("app.js에서 찾지 못한 함수: " + ", ".join(missing))
    return "\n\n".join(chunks)


@unittest.skipUnless(HAVE_JS, "quickjs/esprima 미설치 — JS 로직 테스트 건너뜀")
class TestCompanySearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP_JS, encoding="utf-8") as f:
            src = f.read()
        funcs = _extract_functions(src)
        cls.ctx = quickjs.Context()
        cls.ctx.eval("var LIVE = { universe: { index: [], map: {} } };")
        cls.ctx.eval(funcs)
        cls.ctx.eval("var __payload = " + json.dumps(FIXTURE_UNIVERSE, ensure_ascii=False) + ";")
        cls.ctx.eval("var __built = buildUniverseIndex(__payload);")
        cls.ctx.eval("LIVE.universe.index = __built.index; LIVE.universe.map = __built.map;")

    def search(self, query, limit=10):
        js = "JSON.stringify(searchCompanies(%s, %d))" % (json.dumps(query, ensure_ascii=False), limit)
        return json.loads(self.ctx.eval(js))

    def test_index_covers_kr_and_us(self):
        self.assertEqual(self.ctx.eval("LIVE.universe.index.length"), 7)
        entry = json.loads(self.ctx.eval("JSON.stringify(LIVE.universe.map['005930'])"))
        self.assertEqual(entry["name"], "삼성전자")
        self.assertEqual(entry["cur"], "KRW")
        self.assertEqual(entry["mcap"], 11224855)
        us = json.loads(self.ctx.eval("JSON.stringify(LIVE.universe.map['AMD'])"))
        self.assertEqual(us["cur"], "USD")
        self.assertIsNone(us["per"])

    def test_exact_name_match_comes_first(self):
        rows = self.search("삼성바이오로직스")
        self.assertEqual(rows[0]["code"], "207940")

    def test_exact_code_match(self):
        rows = self.search("005930")
        self.assertEqual(rows[0]["code"], "005930")
        self.assertEqual(rows[0]["name"], "삼성전자")

    def test_us_ticker_match(self):
        rows = self.search("AMD")
        self.assertEqual(rows[0]["code"], "AMD")
        self.assertEqual(rows[0]["cur"], "USD")

    def test_partial_korean_query_ranked_by_market_cap(self):
        rows = self.search("삼성")
        codes = [r["code"] for r in rows]
        self.assertEqual(codes[:3], ["005930", "005935", "207940"])

    def test_mid_string_match_still_found(self):
        rows = self.search("로직스")
        self.assertEqual(rows[0]["name"], "삼성바이오로직스")

    def test_unknown_query_returns_empty(self):
        self.assertEqual(self.search("없는종목명"), [])
        self.assertEqual(self.search("ZZZZ"), [])

    def test_limit_applied(self):
        self.assertEqual(len(self.search("삼", limit=2)), 2)


class TestBriefingLayout(unittest.TestCase):
    """모닝 브리핑 페이지 구성: 중복 시황 요약·오래된 공매도 카드가 없어야 한다."""

    def setUp(self):
        with open(INDEX_HTML, encoding="utf-8") as f:
            self.html = f.read()
        with open(APP_JS, encoding="utf-8") as f:
            self.js = f.read()

    def test_duplicate_market_summary_card_removed(self):
        self.assertNotIn("실시간 시황 요약", self.html)
        self.assertNotIn("briefing-live-summary", self.html)
        self.assertNotIn("briefing-live-summary", self.js)

    def test_checkpoint_card_remains(self):
        self.assertIn("실시간 시장 체크포인트", self.html)
        self.assertIn('id="briefing-checkpoints"', self.html)
        self.assertIn("briefing.checkpoints", self.js)

    def test_stale_short_selling_card_removed(self):
        self.assertNotIn("공매도", self.html)
        self.assertNotIn("공매도", self.js)


NAME_FUNCS = ["normalizeSearchText", "universeRowToEntry", "buildUniverseIndex", "universeLookup",
              "isBareCodeName", "rememberName", "readNameCache", "resolveCompanyName",
              "applyUniverseQuoteToCompany", "makeCompanyRecord", "fmtNum", "formatPriceValue",
              "formatChangeText", "formatCap"]

LOCALSTORAGE_STUB = """
var localStorage = {
  store: {},
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(this.store, k) ? this.store[k] : null; },
  setItem: function (k, v) { this.store[k] = String(v); },
  removeItem: function (k) { delete this.store[k]; }
};
var NAME_CACHE_KEY = 'stock_company_names_v1';
var NAME_CACHE_LIMIT = 400;
var nameCacheMemo = null;
"""


def _extract_named(src: str, names) -> str:
    tree = esprima.parseScript(src, {"range": True})
    chunks = []
    found = set()
    for node in tree.body:
        if getattr(node, "type", "") == "FunctionDeclaration" and node.id.name in names:
            chunks.append(src[node.range[0]:node.range[1]])
            found.add(node.id.name)
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError("app.js에서 찾지 못한 함수: " + ", ".join(missing))
    return "\n\n".join(chunks)


@unittest.skipUnless(HAVE_JS, "quickjs/esprima 미설치 — JS 로직 테스트 건너뜀")
class TestCompanyNameRecovery(unittest.TestCase):
    """관심기업 종목명 복구 로직 — 코드만 남은 레코드도 한글 이름으로 되살린다."""

    def build(self, universe_payload):
        with open(APP_JS, encoding="utf-8") as f:
            src = f.read()
        ctx = quickjs.Context()
        ctx.eval("var LIVE = { universe: { index: [], map: {} }, market: { stocks: {} } };")
        ctx.eval("var predefinedCompaniesDB = {};")
        ctx.eval(LOCALSTORAGE_STUB)
        ctx.eval(_extract_named(src, NAME_FUNCS))
        ctx.eval("var __p = " + json.dumps(universe_payload, ensure_ascii=False) + ";")
        ctx.eval("var __b = buildUniverseIndex(__p); LIVE.universe.index = __b.index; LIVE.universe.map = __b.map;")
        return ctx

    def test_bare_code_name_is_repaired_from_universe(self):
        ctx = self.build(FIXTURE_UNIVERSE_SDI)
        ctx.eval("var rec = {name: '006400', ticker: '006400', fromUniverse: true, per: '-', pbr: '-', cap: '—'};")
        ctx.eval("applyUniverseQuoteToCompany(rec, universeLookup('006400'));")
        self.assertEqual(ctx.eval("rec.name"), "삼성SDI")
        self.assertEqual(ctx.eval("rec.ticker"), "006400")
        self.assertEqual(ctx.eval("rec.livePrice"), 427500)
        self.assertNotEqual(ctx.eval("String(rec.per)"), "-")

    def test_resolve_prefers_korean_name_over_bare_code(self):
        ctx = self.build(FIXTURE_UNIVERSE_SDI)
        self.assertEqual(ctx.eval("resolveCompanyName('006400', {name: '006400'})"), "삼성SDI")
        self.assertEqual(ctx.eval("resolveCompanyName('006400', null)"), "삼성SDI")
        # 이미 정상인 이름은 그대로 둔다(미국 종목의 한글 별칭 등)
        self.assertEqual(ctx.eval("resolveCompanyName('005930', {name: '삼성전자'})"), "삼성전자")

    def test_bare_code_detection(self):
        ctx = self.build(FIXTURE_UNIVERSE_SDI)
        self.assertTrue(ctx.eval("isBareCodeName('006400', '006400')"))
        self.assertTrue(ctx.eval("isBareCodeName('종목(006400)', '006400')"))
        self.assertTrue(ctx.eval("isBareCodeName('', '006400')"))
        self.assertTrue(ctx.eval("isBareCodeName('AMD', 'AMD')"))
        self.assertFalse(ctx.eval("isBareCodeName('삼성SDI', '006400')"))
        self.assertFalse(ctx.eval("isBareCodeName('테슬라', 'TSLA')"))

    def test_name_shown_even_when_universe_unavailable(self):
        # 전종목 목록을 한 번도 못 받은 상태에서도 이름 캐시가 있으면 한글 이름이 보인다
        ctx = self.build({"kr": {}, "us": {}})
        ctx.eval("rememberName('006400', '삼성SDI');")
        self.assertEqual(ctx.eval("resolveCompanyName('006400', {name: '006400'})"), "삼성SDI")
        self.assertEqual(ctx.eval("resolveCompanyName('006400', null)"), "삼성SDI")

    def test_name_is_cached_when_resolved(self):
        ctx = self.build(FIXTURE_UNIVERSE_SDI)
        ctx.eval("resolveCompanyName('006400', {name: '006400'});")
        self.assertEqual(ctx.eval("localStorage.getItem('stock_company_names_v1')").count("삼성SDI"), 1)


class TestCompanySearchWiring(unittest.TestCase):
    """검색 입력창·후보 목록·등록 배선이 실제 파일에 있는지 확인한다."""

    def setUp(self):
        with open(INDEX_HTML, encoding="utf-8") as f:
            self.html = f.read()
        with open(APP_JS, encoding="utf-8") as f:
            self.js = f.read()

    def test_input_wires_search_handlers(self):
        self.assertIn('id="company-search-input"', self.html)
        self.assertIn('oninput="onCompanySearchInput()"', self.html)
        self.assertIn('onkeydown="onCompanySearchKey(event)"', self.html)
        self.assertIn('id="company-suggest"', self.html)

    def test_suggest_box_styles_exist(self):
        self.assertIn(".suggest-box", self.html)
        self.assertIn(".suggest-item", self.html)

    def test_js_exposes_search_and_register_functions(self):
        for name in ["onCompanySearchInput", "onCompanySearchKey", "renderCompanySuggest",
                     "pickCompanySuggest", "registerCompanyByCode", "makeCompanyRecord",
                     "applyUniverseQuoteToCompany", "loadUniverse", "hydrateCompanyNews"]:
            self.assertIn("function " + name + "(", self.js)

    def test_blank_registration_path_removed(self):
        # 예전에는 목록에 없는 입력을 '종목(코드)' 빈 카드로 등록했다 — 그 경로가 없어야 한다
        self.assertNotIn("`종목(${val})`", self.js)
        self.assertNotIn("자동 수집 대상이 아니어서 시세는 비어 있습니다", self.js)
        self.assertIn("종목을 찾지 못했습니다", self.js)


if __name__ == "__main__":
    unittest.main()
