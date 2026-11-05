import unittest
from unittest.mock import patch
from crawler.crawler import StockDataCollector, DEFAULT_FALLBACK_MACRO, DEFAULT_FALLBACK_SECTOR_NEWS
from crawler.gemini_analyzer import GeminiBriefingGenerator


class TestStockBriefing(unittest.TestCase):
    def setUp(self):
        self.collector = StockDataCollector()
        self.generator = GeminiBriefingGenerator()

    def test_stock_data_collector_normal_flow(self):
        data = self.collector.compile_raw_report()

        self.assertIn("macro", data)
        self.assertIn("sectors", data)
        self.assertIn("indices", data["macro"])
        self.assertIn("SOX (필라델피아 반도체)", data["macro"]["indices"])
        self.assertIn("반도체 (Semiconductors)", data["sectors"])
        self.assertIn("우주항공 (Aerospace)", data["sectors"])
        self.assertIn("빅테크 (Big Tech)", data["sectors"])
        self.assertEqual(data["macro"].get("status"), "live")

    def test_gemini_prompt_builder(self):
        data = self.collector.compile_raw_report()
        prompt = self.generator.build_analysis_prompt(data)

        self.assertIn("모닝 브리핑 리포트", prompt)
        self.assertIn("반도체", prompt)
        self.assertIn("우주항공", prompt)
        self.assertIn("필라델피아 반도체", prompt)

    def test_fetch_macro_indicators_fallback_on_exception(self):
        with patch.object(self.collector, "_collect_macro_indicators", side_effect=RuntimeError("API Network Timeout")):
            data = self.collector.fetch_macro_indicators()

            self.assertIsNotNone(data)
            self.assertEqual(data.get("status"), "fallback")
            self.assertIn("indices", data)
            self.assertIn("exchange_rate", data)
            self.assertIn("SOX (필라델피아 반도체)", data["indices"])
            self.assertIn("USD/KRW", data["exchange_rate"])
            self.assertEqual(data["indices"]["NASDAQ"]["value"], DEFAULT_FALLBACK_MACRO["indices"]["NASDAQ"]["value"])

    def test_fetch_macro_indicators_fallback_on_corrupted_data(self):
        with patch.object(self.collector, "_collect_macro_indicators", return_value={"corrupted": True}):
            data = self.collector.fetch_macro_indicators()

            self.assertEqual(data.get("status"), "fallback")
            self.assertIn("indices", data)
            self.assertIn("exchange_rate", data)

    def test_fetch_sector_news_fallback_on_exception(self):
        with patch.object(self.collector, "_collect_sector_news", side_effect=Exception("Parsing failure")):
            news = self.collector.fetch_sector_news("반도체 (Semiconductors)")

            self.assertIsInstance(news, list)
            self.assertGreater(len(news), 0)
            self.assertIn("[대체 데이터]", news[0]["title"])

    def test_fetch_sector_news_invalid_or_unknown_sector(self):
        # Empty string
        news_empty = self.collector.fetch_sector_news("")
        self.assertIsInstance(news_empty, list)
        self.assertGreater(len(news_empty), 0)

        # None type (handled safely by fallback)
        news_none = self.collector.fetch_sector_news(None)  # type: ignore
        self.assertIsInstance(news_none, list)
        self.assertGreater(len(news_none), 0)

        # Unknown sector
        news_unknown = self.collector.fetch_sector_news("Unknown Sector X")
        self.assertIsInstance(news_unknown, list)
        self.assertGreater(len(news_unknown), 0)

    def test_compile_raw_report_with_macro_failure(self):
        with patch.object(self.collector, "fetch_macro_indicators", side_effect=Exception("Catastrophic error")):
            data = self.collector.compile_raw_report()

            self.assertIn("macro", data)
            self.assertEqual(data["macro"].get("status"), "fallback")
            self.assertIn("sectors", data)
            self.assertIn("반도체 (Semiconductors)", data["sectors"])

    def test_compile_raw_report_with_sector_failure(self):
        with patch.object(self.collector, "fetch_sector_news", side_effect=Exception("Sector fetch error")):
            data = self.collector.compile_raw_report()

            self.assertIn("macro", data)
            self.assertIn("sectors", data)
            for sec in self.collector.sectors:
                self.assertIn(sec, data["sectors"])
                self.assertGreater(len(data["sectors"][sec]), 0)

    def test_gemini_prompt_builder_with_fallback_data(self):
        fallback_macro = self.collector.get_fallback_macro_indicators()
        fallback_sectors = {
            sec: self.collector.get_fallback_sector_news(sec) for sec in self.collector.sectors
        }
        fallback_report = {
            "macro": fallback_macro,
            "sectors": fallback_sectors
        }

        prompt = self.generator.build_analysis_prompt(fallback_report)
        self.assertIn("모닝 브리핑 리포트", prompt)
        self.assertIn("SOX (필라델피아 반도체)", prompt)
        self.assertIn("반도체", prompt)

        briefing = self.generator.generate_briefing(fallback_report)
        self.assertIn("Gemini Analysis Output", briefing)


if __name__ == "__main__":
    unittest.main()
