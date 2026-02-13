import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .models import AlertState, CheckStatus, FullReport, Severity

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

        Returns dict with keys:
          - new_alerts: list of CheckResult (new problems)
          - re_alerts: list of CheckResult (ongoing, cooldown expired)
          - recoveries: list of (alert_key, message, duration_seconds)

        WARNING alerts: notify once on first occurrence, then only in
        scheduled reports (3x/day).  A grace period equal to alert_cooldown
        prevents flapping counter-delta checks from generating repeated
        new-alert / recovery pairs.

        CRITICAL alerts: keep full re-alert-on-cooldown behaviour.
        """
        now = time.time()
        new_alerts = []
        re_alerts = []
        recoveries = []

        # Collect current problem keys
        current_problems = {}
        for check in report.checks:
            if check.status in (CheckStatus.WARNING, CheckStatus.CRITICAL):
                current_problems[check.alert_key] = check

        # Check for new and ongoing problems
        for key, check in current_problems.items():
            if key not in self.active_alerts:
                # New alert
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
                alert.severity = check.severity
                alert.count += 1
                # Problem came back during grace period — cancel recovery
                if alert.recovered_at is not None:
                    alert.recovered_at = None
                # Re-alert only for CRITICAL on cooldown expiry
                if now - alert.last_alerted >= self.alert_cooldown:
                    alert.last_alerted = now
                    if check.severity == Severity.CRITICAL:
                        re_alerts.append(check)

        # Check for recoveries
        resolved_keys = [k for k in self.active_alerts if k not in current_problems]
        for key in resolved_keys:
            alert = self.active_alerts[key]

            if alert.severity == Severity.CRITICAL:
                # CRITICAL: recover immediately, notify
                self.active_alerts.pop(key)
                duration = now - alert.first_seen
                recoveries.append((key, alert.message, duration))
            else:
                # WARNING: grace period to absorb flapping
                if alert.recovered_at is None:
                    alert.recovered_at = now
                elif now - alert.recovered_at >= self.alert_cooldown:
                    # Grace period elapsed — silently drop, no recovery notification
                    self.active_alerts.pop(key)

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
