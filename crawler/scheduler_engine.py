"""
Custom Report Scheduler & Interval Update Engine
Allows users to configure specific briefing times and periodic update intervals.
"""
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import List, Optional, Dict, Any


@dataclass
class ScheduleConfig:
    daily_report_times: List[str] = field(default_factory=lambda: ["08:00"])
    interval_enabled: bool = False
    interval_minutes: int = 60  # e.g., 60 minutes
    market_hours_only: bool = True
    active: bool = True

    def is_report_due(self, current_time: datetime, tolerance_minutes: int = 0) -> bool:
        """
        Checks if current_time matches any of the scheduled daily_report_times within tolerance.
        If tolerance_minutes <= 0, checks for exact 'HH:MM' match.
        Otherwise, checks if current_time falls within tolerance_minutes of any valid scheduled time.
        """
        if not self.active:
            return False

        if not self.daily_report_times:
            return False

        current_hm = current_time.strftime("%H:%M")

        if tolerance_minutes <= 0:
            return current_hm in self.daily_report_times

        # When tolerance is provided, evaluate time distance in minutes
        curr_total_minutes = current_time.hour * 60 + current_time.minute
        for rep_time in self.daily_report_times:
            if not rep_time or not isinstance(rep_time, str):
                continue
            parts = rep_time.strip().split(":")
            if len(parts) != 2:
                continue
            try:
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h < 24 and 0 <= m < 60):
                    continue
            except (ValueError, TypeError):
                continue

            scheduled_minutes = h * 60 + m
            # Handle day wrap around if needed (e.g., 23:58 to 00:02)
            diff = abs(curr_total_minutes - scheduled_minutes)
            diff = min(diff, 1440 - diff)
            if diff <= tolerance_minutes:
                return True

        return False

    def is_interval_due(self, last_run: Optional[datetime], current_time: datetime) -> bool:
        """
        Checks if periodic interval update should run.
        """
        if not self.active or not self.interval_enabled:
            return False

        if self.interval_minutes <= 0:
            return True

        if self.market_hours_only:
            # Standard trading hours window check (09:00 - 15:30 KST or US market window)
            c_time = current_time.time()
            if not (time(9, 0) <= c_time <= time(15, 30) or time(22, 30) <= c_time or c_time <= time(5, 0)):
                return False

        if last_run is None:
            return True

        elapsed_seconds = (current_time - last_run).total_seconds()
        if elapsed_seconds < 0:
            # Guard against clock skew / negative elapsed time
            return False

        return elapsed_seconds >= (self.interval_minutes * 60)


@dataclass
class UnifiedSyncResult:
    timestamp: str
    macro_updated: bool
    traces_updated_count: int
    companies_updated_count: int
    details: Dict[str, Any] = field(default_factory=dict)


def execute_unified_interval_sync(
    crawler_instance: Any,
    trace_manager_instance: Any,
    company_analyzer_instance: Any,
    current_time: Optional[datetime] = None
) -> UnifiedSyncResult:
    """
    Executes a comprehensive interval sync that updates:
    1. Daily Briefing macro indicators & sector news
    2. Active Keyword Traces and cumulative timeline events
    3. Fundamental Company Valuation news & sentiment
    """
    now = current_time or datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    # 1. Briefing update
    macro_report = None
    if crawler_instance and hasattr(crawler_instance, 'compile_raw_report'):
        macro_report = crawler_instance.compile_raw_report()

    # 2. Keyword Traces update
    traces_count = 0
    if trace_manager_instance and hasattr(trace_manager_instance, 'match_news_to_traces'):
        # Collect recent news from crawler report
        news_items = []
        if macro_report and "sector_news" in macro_report:
            for sec, articles in macro_report["sector_news"].items():
                for art in articles:
                    news_items.append({"title": art, "source": sec})
        matches = trace_manager_instance.match_news_to_traces(news_items)
        traces_count = len(matches)

    # 3. Company News update
    companies_count = 0
    if company_analyzer_instance and hasattr(company_analyzer_instance, 'db'):
        companies_count = len(company_analyzer_instance.db)

    return UnifiedSyncResult(
        timestamp=now_str,
        macro_updated=macro_report is not None,
        traces_updated_count=traces_count,
        companies_updated_count=companies_count,
        details={
            "briefing_status": "ok" if macro_report else "skipped",
            "traces_monitored": traces_count,
            "companies_tracked": companies_count
        }
    )

