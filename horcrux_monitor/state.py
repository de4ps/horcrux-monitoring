import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .models import AlertState, CheckStatus, FullReport

log = logging.getLogger(__name__)


class StateManager:
    def __init__(self, alert_cooldown: int,
                 scheduled_hours: List[int], timezone: str):
        self.alert_cooldown = alert_cooldown
        self.scheduled_hours = sorted(scheduled_hours)
        self.tz = ZoneInfo(timezone)
        self.active_alerts: Dict[str, AlertState] = {}
        self.last_scheduled_hour: Optional[int] = None

    def process_report(self, report: FullReport) -> dict:
        """Process a report and determine what notifications to send.

        WARNING: only in scheduled reports (3x/day) and startup.
        CRITICAL: immediate alert + re-alert on cooldown + recovery.
        """
        now = time.time()
        new_alerts = []
        re_alerts = []
        recoveries = []

        # Only track CRITICAL alerts
        current_critical = {}
        for check in report.checks:
            if check.status == CheckStatus.CRITICAL:
                current_critical[check.alert_key] = check

        for key, check in current_critical.items():
            if key not in self.active_alerts:
                self.active_alerts[key] = AlertState(
                    severity=check.severity,
                    message=check.message,
                    first_seen=now,
                    last_alerted=now,
                    count=1,
                )
                new_alerts.append(check)
            else:
                alert = self.active_alerts[key]
                alert.message = check.message
                alert.count += 1
                if now - alert.last_alerted >= self.alert_cooldown:
                    alert.last_alerted = now
                    re_alerts.append(check)

        # Recoveries (CRITICAL only)
        resolved_keys = [k for k in self.active_alerts if k not in current_critical]
        for key in resolved_keys:
            alert = self.active_alerts.pop(key)
            duration = now - alert.first_seen
            recoveries.append((key, alert.message, duration))

        return {
            "new_alerts": new_alerts,
            "re_alerts": re_alerts,
            "recoveries": recoveries,
        }

    def is_scheduled_report_due(self) -> bool:
        """Check if a scheduled report should be sent now."""
        now = datetime.now(self.tz)
        current_hour = now.hour

        if current_hour in self.scheduled_hours:
            if self.last_scheduled_hour != current_hour:
                self.last_scheduled_hour = current_hour
                return True
        return False

    def format_duration(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.0f}m"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h{mins}m"
