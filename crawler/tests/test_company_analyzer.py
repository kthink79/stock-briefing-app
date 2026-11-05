import unittest
from crawler.company_analyzer import CompanyValuationAnalyzer


class TestCompanyAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = CompanyValuationAnalyzer()

    def test_search_samsung(self):
        profile = self.analyzer.search_company("삼성전자")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "삼성전자")
        self.assertAlmostEqual(profile.per, 11.8)
        self.assertAlmostEqual(profile.pbr, 1.05)
        self.assertTrue(profile.eps > 0)
        self.assertIn("펀더멘털 종합 평가", profile.gemini_valuation_summary)
        self.assertTrue(len(profile.earnings_news) > 0)

    def test_search_sk_hynix(self):
        profile = self.analyzer.search_company("000660")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "SK하이닉스")
        self.assertAlmostEqual(profile.per, 8.5)
        self.assertTrue("어닝 서프라이즈" in profile.quarterly_earnings["operating_profit"])

    def test_search_aerospace_hanwha(self):
        profile = self.analyzer.search_company("한화에어로스페이스")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.ticker, "012450")
        self.assertTrue(profile.price > 300000)

    def test_search_nvidia(self):
        profile = self.analyzer.search_company("NVDA")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.currency, "USD")
        self.assertTrue(profile.per > 40)


if __name__ == "__main__":
    unittest.main()
