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


if __name__ == "__main__":
    unittest.main()
