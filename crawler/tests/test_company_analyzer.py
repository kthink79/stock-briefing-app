import unittest
from crawler.company_analyzer import CompanyValuationAnalyzer


class TestCompanyAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = CompanyValuationAnalyzer()

    def test_search_samsung(self):
        profile = self.analyzer.search_company("삼성전자")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "삼성전자")
        self.assertAlmostEqual(profile.per, 7.58, places=1)
        self.assertAlmostEqual(profile.pbr, 1.99, places=1)
        self.assertTrue(profile.eps > 0)
        self.assertIn("펀더멘털 종합 평가", profile.gemini_valuation_summary)
        self.assertTrue(len(profile.earnings_news) > 0)

    def test_search_sk_hynix(self):
        profile = self.analyzer.search_company("000660")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "SK하이닉스")
        self.assertAlmostEqual(profile.per, 5.24, places=1)
        self.assertTrue("어닝 서프라이즈" in profile.quarterly_earnings["operating_profit"])

    def test_search_aerospace_hanwha(self):
        profile = self.analyzer.search_company("한화에어로스페이스")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.ticker, "012450")
        self.assertTrue(profile.price > 500000)

    def test_search_nvidia(self):
        profile = self.analyzer.search_company("NVDA")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.currency, "USD")
        self.assertTrue(profile.per > 30)

    def test_update_live_quotes(self):
        updates = {
            "005930": {"price": 205000.0, "change_pct": "+3.01%"}
        }
        res = self.analyzer.update_live_quotes(updates)
        self.assertIn("005930", res)
        self.assertEqual(res["005930"].price, 205000.0)
        self.assertEqual(res["005930"].change_pct, "+3.01%")
        # PER = 205000 / 25506 = 8.0
        self.assertAlmostEqual(res["005930"].per, 8.0, places=1)

    def test_register_and_remove_company(self):
        new_company = {
            "ticker": "005380",
            "name": "현대차",
            "currency": "KRW",
            "price": 254000.0,
            "change_pct": "+2.42%",
            "market_cap": "53.6조원",
            "per": 5.2,
            "pbr": 0.65,
            "eps": 48846.0,
            "bps": 390769.0,
            "dividend_yield": "5.10%",
            "quarterly_earnings": {
                "quarter": "2026 3Q",
                "revenue": "42.93조원",
                "operating_profit": "3.58조원",
                "comment": "인도법인 상장 효과 및 로보틱스 협력"
            },
            "earnings_news": [
                {"title": "현대차 254,000원 안착", "source": "연합뉴스"}
            ]
        }
        profile = self.analyzer.register_company(new_company)
        self.assertEqual(profile.name, "현대차")
        self.assertEqual(profile.ticker, "005380")
        self.assertAlmostEqual(profile.per, 5.2, places=1)

        # Test removal
        removed = self.analyzer.remove_company("005380")
        self.assertTrue(removed)
        # Search for non-existent returns default
        fallback = self.analyzer.search_company("005380")
        self.assertEqual(fallback.name, "삼성전자")



if __name__ == "__main__":
    unittest.main()
