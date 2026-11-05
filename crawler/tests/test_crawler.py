import unittest
from crawler.crawler import StockDataCollector
from crawler.gemini_analyzer import GeminiBriefingGenerator


class TestStockBriefing(unittest.TestCase):
    def test_stock_data_collector(self):
        collector = StockDataCollector()
        data = collector.compile_raw_report()

        self.assertIn("macro", data)
        self.assertIn("sectors", data)
        self.assertIn("indices", data["macro"])
        self.assertIn("SOX (필라델피아 반도체)", data["macro"]["indices"])
        self.assertIn("반도체 (Semiconductors)", data["sectors"])
        self.assertIn("우주항공 (Aerospace)", data["sectors"])

    def test_gemini_prompt_builder(self):
        collector = StockDataCollector()
        data = collector.compile_raw_report()

        generator = GeminiBriefingGenerator()
        prompt = generator.build_analysis_prompt(data)

        self.assertIn("모닝 브리핑 리포트", prompt)
        self.assertIn("반도체", prompt)
        self.assertIn("우주항공", prompt)
        self.assertIn("필라델피아 반도체", prompt)


if __name__ == "__main__":
    unittest.main()
