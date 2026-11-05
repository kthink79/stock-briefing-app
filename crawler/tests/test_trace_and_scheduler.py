import os
import shutil
import unittest
from datetime import datetime
from crawler.scheduler_engine import ScheduleConfig
from crawler.trace_engine import TraceManager, TraceEvent


class TestTraceAndScheduler(unittest.TestCase):
    def setUp(self):
        self.test_storage_dir = "data/test_env"
        self.test_storage_path = os.path.join(self.test_storage_dir, "test_traces.json")
        os.makedirs(self.test_storage_dir, exist_ok=True)
        self.manager = TraceManager(storage_path=self.test_storage_path)

    def tearDown(self):
        if os.path.exists(self.test_storage_dir):
            shutil.rmtree(self.test_storage_dir)

    def test_add_and_list_traces(self):
        trace = self.manager.add_trace("트럼프")
        self.assertEqual(trace.keyword_or_topic, "트럼프")
        self.assertTrue(len(self.manager.list_traces()) == 1)

    def test_match_news_to_traces(self):
        self.manager.add_trace("트럼프")
        self.manager.add_trace("미국 이란 전쟁")

        news_items = [
            {"title": "트럼프 전 대통령, 관세 정책 관련 새로운 입장 표명", "source": "Bloomberg"},
            {"title": "중동 긴장 고조... 미국 이란 전쟁 우려에 국제 유가 급등", "source": "Reuters"},
            {"title": "글로벌 AI 반도체 컨퍼런스 개막", "source": "TechNews"}
        ]

        matches = self.manager.match_news_to_traces(news_items)

        # Check that matches occurred
        matched_trace_names = [self.manager.traces[tid].keyword_or_topic for tid, events in matches.items() if events]
        self.assertIn("트럼프", matched_trace_names)
        self.assertIn("미국 이란 전쟁", matched_trace_names)

        # Verify timeline events accumulated
        for trace in self.manager.list_traces():
            if trace.keyword_or_topic == "트럼프":
                self.assertEqual(len(trace.events), 1)
                self.assertIn("관세 정책", trace.events[0].title)
                self.assertIn("누적 타임라인 분석", trace.accumulated_summary)
            elif trace.keyword_or_topic == "미국 이란 전쟁":
                self.assertEqual(len(trace.events), 1)
                self.assertIn("국제 유가 급등", trace.events[0].title)

    def test_schedule_config(self):
        config = ScheduleConfig(
            daily_report_times=["08:00", "15:30"],
            interval_enabled=True,
            interval_minutes=60,
            market_hours_only=False
        )

        t_0800 = datetime(2026, 11, 6, 8, 0)
        t_0805 = datetime(2026, 11, 6, 8, 5)
        self.assertTrue(config.is_report_due(t_0800))
        self.assertFalse(config.is_report_due(t_0805))

        # Test interval
        last_run = datetime(2026, 11, 6, 9, 0)
        curr_due = datetime(2026, 11, 6, 10, 0)
        curr_not_due = datetime(2026, 11, 6, 9, 30)

        self.assertTrue(config.is_interval_due(last_run, curr_due))
        self.assertFalse(config.is_interval_due(last_run, curr_not_due))

    def test_trace_keyword_matching_regex_and_partial(self):
        # 1. Regex pattern matching
        self.assertTrue(TraceManager.is_keyword_matched(r"엔비디아|NVDA", "NVDA 주가 사상 최고치 경신"))
        self.assertTrue(TraceManager.is_keyword_matched(r"엔비디아|NVDA", "엔비디아 신제품 발표"))
        self.assertFalse(TraceManager.is_keyword_matched(r"엔비디아|NVDA", "AMD 실적 발표"))

        # Complex regex with wildcards
        self.assertTrue(TraceManager.is_keyword_matched(r"AI.*반도체", "AI 차세대 고대역폭 반도체 수요 급증"))
        self.assertFalse(TraceManager.is_keyword_matched(r"AI.*반도체", "AI 소프트웨어 시장"))

        # 2. Word boundary matching: Alphanumeric terms should not match inside unrelated words
        self.assertFalse(TraceManager.is_keyword_matched("AI", "TRAIN SERVICES DELAYED"))
        self.assertFalse(TraceManager.is_keyword_matched("AI", "PAID SUBSCRIPTIONS"))
        self.assertTrue(TraceManager.is_keyword_matched("AI", "NEW AI MODEL RELEASED"))
        self.assertTrue(TraceManager.is_keyword_matched("AI", "Generative AI is booming"))

        # 3. Multi-word topic: all tokens must appear; single token match must NOT trigger
        topic = "미국 이란 전쟁"
        # Matches when all key components are present
        self.assertTrue(TraceManager.is_keyword_matched(topic, "미국과 이란 간의 긴장 고조... 전쟁 위기감 고조"))
        self.assertTrue(TraceManager.is_keyword_matched(topic, "미국-이란 전쟁 발발 가능성"))
        # Should not match news that only has "미국"
        self.assertFalse(TraceManager.is_keyword_matched(topic, "미국 증시 3대 지수 일제히 상승 마감"))
        # Should not match news that only has "전쟁"
        self.assertFalse(TraceManager.is_keyword_matched(topic, "우크라이나 전쟁 장기화 영향"))

        # 4. Special regex characters treated safely
        self.assertTrue(TraceManager.is_keyword_matched("S&P 500", "S&P 500 index rises 1.5%"))
        self.assertTrue(TraceManager.is_keyword_matched("C++", "Modern C++ features in trading engine"))
        self.assertTrue(TraceManager.is_keyword_matched("[특징주]", "[특징주] 삼성전자 외인 매수세 유입"))

        # 5. Invalid / Empty / Whitespace inputs
        self.assertFalse(TraceManager.is_keyword_matched("", "Some headline"))
        self.assertFalse(TraceManager.is_keyword_matched("   ", "Some headline"))
        self.assertFalse(TraceManager.is_keyword_matched("AI", ""))
        self.assertFalse(TraceManager.is_keyword_matched("AI", "   "))
        self.assertFalse(TraceManager.is_keyword_matched(None, "Some headline"))
        self.assertFalse(TraceManager.is_keyword_matched("AI", None))

    def test_trace_duplicate_events_and_impact_level(self):
        self.manager.add_trace("긴급속보")
        news = [
            {"title": "긴급속보: 중동 지역 군사적 충돌 발생", "source": "NewsA"},
            {"title": "긴급속보: 중동 지역 군사적 충돌 발생", "source": "NewsB"},  # duplicate title
            {"title": "일반 소식: 긴급속보 관련 후속 점검", "source": "NewsC"}
        ]
        matches = self.manager.match_news_to_traces(news)
        trace_id = list(self.manager.traces.keys())[0]
        # Should only add 2 events (duplicate ignored)
        self.assertEqual(len(matches[trace_id]), 2)
        # Check impact level
        self.assertEqual(matches[trace_id][0].impact_level, "High")  # Contains "긴급" and "충돌/전쟁"

    def test_scheduler_edge_cases(self):
        # 1. Active flag: inactive config should not trigger reports or interval
        inactive_config = ScheduleConfig(
            daily_report_times=["08:00"],
            interval_enabled=True,
            interval_minutes=60,
            market_hours_only=False,
            active=False
        )
        t_0800 = datetime(2026, 11, 6, 8, 0)
        self.assertFalse(inactive_config.is_report_due(t_0800))
        self.assertFalse(inactive_config.is_interval_due(None, t_0800))

        # 2. Tolerance minutes in report due
        config = ScheduleConfig(
            daily_report_times=["08:00", "23:59"],
            interval_enabled=True,
            interval_minutes=30,
            market_hours_only=False
        )
        # Tolerance = 0 (exact match)
        self.assertTrue(config.is_report_due(datetime(2026, 11, 6, 8, 0), tolerance_minutes=0))
        self.assertFalse(config.is_report_due(datetime(2026, 11, 6, 8, 2), tolerance_minutes=0))

        # Tolerance = 5 minutes
        self.assertTrue(config.is_report_due(datetime(2026, 11, 6, 8, 3), tolerance_minutes=5))
        self.assertTrue(config.is_report_due(datetime(2026, 11, 6, 7, 57), tolerance_minutes=5))
        self.assertFalse(config.is_report_due(datetime(2026, 11, 6, 8, 6), tolerance_minutes=5))

        # Tolerance with midnight wrap around (23:59 and 00:01 is 2 min diff)
        self.assertTrue(config.is_report_due(datetime(2026, 11, 6, 0, 1), tolerance_minutes=3))

        # Malformed scheduled time format should not crash
        bad_config = ScheduleConfig(daily_report_times=["invalid", "25:99", ""])
        self.assertFalse(bad_config.is_report_due(datetime(2026, 11, 6, 8, 0), tolerance_minutes=5))

        # 3. Interval edge cases: clock skew (current_time < last_run)
        now = datetime(2026, 11, 6, 10, 0)
        future_run = datetime(2026, 11, 6, 10, 30)
        self.assertFalse(config.is_interval_due(future_run, now))

        # Interval <= 0 minutes
        zero_interval_config = ScheduleConfig(
            interval_enabled=True,
            interval_minutes=0,
            market_hours_only=False
        )
        self.assertTrue(zero_interval_config.is_interval_due(now, now))

        # Market hours window testing (e.g. 16:00 is outside KR market and before US market)
        market_config = ScheduleConfig(
            interval_enabled=True,
            interval_minutes=10,
            market_hours_only=True
        )
        t_after_kr_close = datetime(2026, 11, 6, 16, 0)  # 16:00 KST
        self.assertFalse(market_config.is_interval_due(None, t_after_kr_close))

        # Within KR market hours (e.g., 10:00 KST)
        t_kr_open = datetime(2026, 11, 6, 10, 0)
        self.assertTrue(market_config.is_interval_due(None, t_kr_open))


if __name__ == "__main__":
    unittest.main()
