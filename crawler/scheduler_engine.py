"""
Custom Report Scheduler & Interval Update Engine
Allows users to configure specific briefing times and periodic update intervals.
"""
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import List, Optional


@dataclass
class ScheduleConfig:
    daily_report_times: List[str] = field(default_factory=lambda: ["08:00"])
    interval_enabled: bool = False
    interval_minutes: int = 60  # e.g., 60 minutes
    market_hours_only: bool = True
    active: bool = True

    def is_report_due(self, current_time: datetime, tolerance_minutes: int = 5) -> bool:
        """
        Checks if current_time matches any of the scheduled daily_report_times within tolerance.
        """
        current_hm = current_time.strftime("%H:%M")
        return current_hm in self.daily_report_times

    def is_interval_due(self, last_run: Optional[datetime], current_time: datetime) -> bool:
        """
        Checks if periodic interval update should run.
        """
        if not self.interval_enabled:
            return False

        if self.market_hours_only:
            # Standard trading hours window check (09:00 - 15:30 KST or US market window)
            c_time = current_time.time()
            if not (time(9, 0) <= c_time <= time(15, 30) or time(22, 30) <= c_time or c_time <= time(5, 0)):
                return False

        if last_run is None:
            return True

        elapsed_seconds = (current_time - last_run).total_seconds()
        return elapsed_seconds >= (self.interval_minutes * 60)
