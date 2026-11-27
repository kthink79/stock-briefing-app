# -*- coding: utf-8 -*-
"""앱(app.js)의 키워드 3중 필터 테스트.

서버 수집기(scripts/collect_live_data.py)와 같은 규칙이 브라우저 앱에도 적용됐는지
확인한다. 기기에 저장돼 있던 옛 기사는 이 규칙으로 다시 걸러지므로, 여기서
'수학이란·신간', '반정부시위 사형선고'가 차단되고 중동·유가 기사는 남는지 고정한다.
quickjs/esprima가 없는 환경에서는 건너뛴다.
"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_JS = os.path.join(ROOT, "app.js")

try:  # pragma: no cover - 선택적 의존성
    import esprima
    import quickjs
    HAVE_JS = True
except Exception:  # pragma: no cover
    HAVE_JS = False

FILTER_CONSTS = ["KEYWORD_NOISE_TOKENS", "KEYWORD_MARKET_TOKENS", "IRAN_CONTEXT_TOKENS",
                 "KEYWORD_BLOCK_PHRASES"]
FILTER_FUNCS = ["stripKeywordPhrases", "newsTokenPresent", "newsHasAny", "newsTitleIsNoise",
                "traceArticleOk"]


def _extract_filter_source() -> str:
    """app.js에서 필터 상수 선언과 함수 선언만 원문 그대로 뽑아낸다."""
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    tree = esprima.parseScript(src, {"range": True})
    chunks = []
    found = set()
    for node in tree.body:
        kind = getattr(node, "type", "")
        if kind == "VariableDeclaration":
            names = [d.id.name for d in node.declarations if getattr(d, "id", None) is not None]
            if any(n in FILTER_CONSTS for n in names):
                chunks.append(src[node.range[0]:node.range[1]])
                found.update(n for n in names if n in FILTER_CONSTS)
        elif kind == "FunctionDeclaration" and node.id.name in FILTER_FUNCS:
            chunks.append(src[node.range[0]:node.range[1]])
            found.add(node.id.name)
    missing = [n for n in FILTER_CONSTS + FILTER_FUNCS if n not in found]
    if missing:
        raise AssertionError("app.js에서 찾지 못한 필터 정의: " + ", ".join(missing))
    return "\n\n".join(chunks)


@unittest.skipUnless(HAVE_JS, "quickjs/esprima 미설치 — JS 로직 테스트 건너뜀")
class TestKeywordFilterJs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = quickjs.Context()
        cls.ctx.eval(_extract_filter_source())

    def ok(self, title, keyword="미국 이란 전쟁", summary=""):
        article = {"title": title, "summary": summary}
        return bool(self.ctx.eval(
            "traceArticleOk(%s, %s)" % (json.dumps(article, ensure_ascii=False),
                                        json.dumps(keyword, ensure_ascii=False))))

    def test_copula_iran_and_book_news_are_blocked(self):
        self.assertFalse(self.ok("AI가 난제를 푸는 시대에 인간에게 수학이란, 신간"))
        self.assertFalse(self.ok("장동혁, “탈당·창당이란 좋은 제도 있다”", keyword="이란"))
        self.assertFalse(self.ok("이란성 쌍둥이 부모의 고민"))

    def test_social_noise_article_is_blocked(self):
        self.assertFalse(self.ok("이란서 반정부시위 가담 여성 6명 사형선고...고문도 당해"))

    def test_geopolitical_articles_stay(self):
        self.assertTrue(self.ok("이란 외무, 조현에 외국군 주둔은 분쟁 키워"))
        self.assertTrue(self.ok("호르무즈 해협 기뢰·이란 긴장 유가 급등"))
        self.assertTrue(self.ok("이란 드론에 쿠웨이트서 이탈리아인 2명 사망…유조선도 기뢰 피격"))

    def test_iran_without_context_is_blocked(self):
        self.assertFalse(self.ok("이란, 대통령 선거 일정 발표"))
        self.assertFalse(self.ok("서울 다산콜센터 해외서도 벤치마킹…이란에 '128콜센터' 설립"))

    def test_other_keywords_only_use_noise_filter(self):
        self.assertTrue(self.ok("엔비디아 실적 발표", keyword="엔비디아"))
        self.assertTrue(self.ok("프로야구단 인수전…구단주 주가 급등", keyword="트럼프"))
        self.assertFalse(self.ok("이란 반정부시위 사형선고", keyword="트럼프"))

    def test_latin_token_boundary_is_kept(self):
        self.assertTrue(self.ctx.eval("newsTokenPresent('HBM', 'SK하이닉스 HBM4 양산 확대')"))
        self.assertFalse(self.ctx.eval("newsTokenPresent('AI', 'TRAIN SERVICES DELAYED')"))


if __name__ == "__main__":
    unittest.main()
