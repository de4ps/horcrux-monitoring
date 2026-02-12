from datetime import datetime
from typing import List, Tuple
from zoneinfo import ZoneInfo

from .models import CheckResult, CheckStatus, FullReport, CosignerStatus, SentryStatus


EMOJI = {
    CheckStatus.OK: "\u2705",       # ✅
    CheckStatus.WARNING: "\u26a0\ufe0f",   # ⚠️
    CheckStatus.CRITICAL: "\U0001f534",     # 🔴
}


def format_full_report(report: FullReport, timezone: str, name: str = "",
                       title: str = "Horcrux Status Report") -> str:
    """Format a full status report for display."""
    tz = ZoneInfo(timezone)
    now = datetime.fromtimestamp(report.timestamp, tz=tz)
    time_str = now.strftime("%Y-%m-%d %H:%M")
    tz_name = timezone.split("/")[-1]

    if report.has_critical:
        status_icon = "\U0001f534"  # 🔴
    elif report.has_problems:
        status_icon = "\u26a0\ufe0f"  # ⚠️
    else:
        status_icon = "\u2705"  # ✅

    header = f"{status_icon} {title}"
    if name:
        header += f" [{name}]"
    header += f" \u2014 {time_str} ({tz_name})"
    lines = [header, ""]

    # Signing section
    lines.append("*Signing:*")
    if report.last_prevote_height is not None:
        st = _check_status_for(report, "height_stale")
        lines.append(f"  {EMOJI[st]} Last prevote height: {report.last_prevote_height:,}")
    if report.last_precommit_height is not None:
        lines.append(f"  \u2705 Last precommit height: {report.last_precommit_height:,}")
    if report.missed_prevotes is not None:
        st = _check_status_for(report, "missed_prevotes")
        lines.append(f"  {EMOJI[st]} Missed prevotes (consecutive): {report.missed_prevotes}")
    if report.missed_precommits is not None:
        st = _check_status_for(report, "missed_precommits")
        lines.append(f"  {EMOJI[st]} Missed precommits (consecutive): {report.missed_precommits}")
    if report.insufficient_cosigner_errors is not None:
        st = _check_status_for(report, "insufficient_cosigners")
        label = _check_message_suffix(report, "insufficient_cosigners")
        lines.append(f"  {EMOJI[st]} Insufficient cosigner errors: {report.insufficient_cosigner_errors:,}{label}")

    if not report.metrics_ok:
        lines.append(f"  \U0001f534 Metrics endpoint unreachable")

    # Cosigners section
    if report.cosigners:
        lines.append("")
        lines.append("*Cosigners:*")
        for cs in report.cosigners:
            lines.append(_format_cosigner(cs))

    # Sentries section
    if report.sentries:
        lines.append("")
        lines.append("*Sentries (chain nodes):*")
        for s in report.sentries:
            lines.append(_format_sentry(s))

    # Raft section
    raft_lines = []
    if report.raft_election_timeouts is not None:
        st = _check_status_for(report, "raft_election_timeouts")
        label = _check_message_suffix(report, "raft_election_timeouts")
        raft_lines.append(f"  {EMOJI[st]} Election timeouts: {report.raft_election_timeouts:,}{label}")
    if report.seconds_since_last_ephemeral_share is not None:
        raft_lines.append(f"  \u2705 Last ephemeral share: {report.seconds_since_last_ephemeral_share:.1f}s ago")

    if raft_lines:
        lines.append("")
        lines.append("*Raft:*")
        lines.extend(raft_lines)

    return "\n".join(lines)


def format_problem_alert(checks: List[CheckResult], name: str = "",
                         is_re_alert: bool = False) -> str:
    """Format a problem alert with only the failing checks."""
    prefix = "\U0001f6a8 Horcrux Alert" if not is_re_alert else "\U0001f6a8 Horcrux Alert (ongoing)"
    if name:
        prefix += f" [{name}]"
    lines = [prefix, ""]
    for check in checks:
        emoji = EMOJI.get(check.status, "\u2753")
        lines.append(f"{emoji} {check.message}")
    return "\n".join(lines)


def format_recovery(recoveries: List[Tuple[str, str, float]], format_duration,
                    name: str = "") -> str:
    """Format a recovery notification."""
    header = "\u2705 Horcrux Recovery"
    if name:
        header += f" [{name}]"
    lines = [header, ""]
    for key, message, duration in recoveries:
        dur_str = format_duration(duration)
        lines.append(f"\u2705 Recovered: {message} (was down for {dur_str})")
    return "\n".join(lines)


def format_startup_report(report: FullReport, timezone: str, name: str = "") -> str:
    """Format a startup report."""
    return format_full_report(report, timezone, name=name, title="Horcrux Monitor Started")


def _check_status_for(report: FullReport, alert_key: str) -> CheckStatus:
    """Find the status of a specific check in the report."""
    for check in report.checks:
        if check.alert_key == alert_key:
            return check.status
    return CheckStatus.OK


def _check_message_suffix(report: FullReport, alert_key: str) -> str:
    """Extract parenthetical suffix from check message (e.g., ' (stable)')."""
    for check in report.checks:
        if check.alert_key == alert_key:
            msg = check.message
            paren_idx = msg.rfind("(")
            if paren_idx >= 0:
                return " " + msg[paren_idx:]
    return ""


def _host_from_address(addr: str) -> str:
    """Extract host from address like 'tcp://192.168.100.2:2222' -> '192.168.100.2'."""
    if "://" in addr:
        addr = addr.split("://", 1)[1]
    return addr.rsplit(":", 1)[0] if ":" in addr else addr


def _format_cosigner(cs: CosignerStatus) -> str:
    host = _host_from_address(cs.address) if cs.address else "self"
    if cs.is_self:
        return f"  \u2705 Shard {cs.shard_id} ({host}) \u2014 self"
    elif cs.missed_shares is not None:
        emoji = EMOJI[cs.status]
        return f"  {emoji} Shard {cs.shard_id} ({host}) \u2014 missed shares: {cs.missed_shares}"
    else:
        return f"  \u2705 Shard {cs.shard_id} ({host}) \u2014 missed shares: n/a"


def _format_sentry(s: SentryStatus) -> str:
    emoji = EMOJI[s.status]
    height_str = f"{s.block_height:,}" if s.block_height is not None else "unreachable"
    return f"  {emoji} Sentry {s.index} ({s.host}) \u2014 height: {height_str}"
