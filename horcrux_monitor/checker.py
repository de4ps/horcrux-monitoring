import logging
from typing import Dict, List, Optional

from .models import CheckResult, CheckStatus, CosignerStatus, SentryStatus, FullReport
from .config import Config
from .collector import (
    fetch_metrics, get_metric, get_labeled_metrics,
    fetch_block_height, parse_address,
)

log = logging.getLogger(__name__)


class Checker:
    def __init__(self, config: Config):
        self.config = config
        self.prev_insufficient_cosigners: Optional[float] = None
        self.prev_raft_election_timeouts: Optional[float] = None
        self.prev_missed_shares: Dict[str, int] = {}   # addr → previous value
        self.cosigner_miss_streak: Dict[str, int] = {}  # addr → consecutive growing checks
        self.prev_height: Optional[int] = None
        self.height_stale_count: int = 0
        self.prev_sentry_connect_tries: Optional[float] = None
        self.prev_invalid_signatures: Optional[float] = None
        self.prev_beyond_block_errors: Optional[float] = None
        self.prev_failed_sign_votes: Optional[float] = None
        self.prev_goroutines: Optional[int] = None
        self.goroutine_grow_streak: int = 0

    def run(self) -> FullReport:
        """Run all health checks and return a FullReport."""
        cfg = self.config
        report = FullReport()
        checks: List[CheckResult] = []

        # Fetch metrics
        metrics = None
        if cfg.metrics_url:
            metrics = fetch_metrics(cfg.metrics_url, cfg.metrics_timeout)

        if metrics is None:
            report.metrics_ok = False
            checks.append(CheckResult(
                name="metrics_endpoint",
                status=CheckStatus.CRITICAL,
                message="Metrics endpoint unreachable",
                alert_key="metrics_endpoint",
            ))
        else:
            report.metrics_ok = True
            self._check_raft(metrics, report, checks)
            self._check_signing(metrics, report, checks)
            self._check_sentry_connect(metrics, report, checks)
            self._check_error_counters(metrics, report, checks)
            self._check_signing_freshness(metrics, report, checks)
            self._check_process_health(metrics, report, checks)

        self._check_cosigners(metrics, report, checks)
        self._check_sentries(report, checks)
        self._check_sentry_divergence(report, checks)

        report.checks = checks
        return report

    def _check_signing(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        cfg = self.config
        th = cfg.thresholds

        # Last prevote height
        val = get_metric(metrics, "signer_last_prevote_height")
        if val is not None:
            report.last_prevote_height = int(val)

        # Last precommit height
        val = get_metric(metrics, "signer_last_precommit_height")
        if val is not None:
            report.last_precommit_height = int(val)

        # Height stale check
        current_height = report.last_prevote_height
        if current_height is not None:
            if self.prev_height is not None and current_height <= self.prev_height:
                self.height_stale_count += 1
            else:
                self.height_stale_count = 0
            self.prev_height = current_height

            # If ephemeral shares are fresh, this cosigner is an active
            # Raft follower — stale signing height is expected, not a problem.
            eph = report.seconds_since_last_ephemeral_share
            is_active_follower = eph is not None and eph < cfg.block_time * 3

            if self.height_stale_count >= th["height_stale_checks"] and not is_active_follower:
                checks.append(CheckResult(
                    name="height_stale",
                    status=CheckStatus.CRITICAL,
                    message=f"Height stale at {current_height:,} for {self.height_stale_count} checks",
                    alert_key="height_stale",
                ))
            else:
                checks.append(CheckResult(
                    name="height_stale",
                    status=CheckStatus.OK,
                    message=f"Last prevote height: {current_height:,}",
                    alert_key="height_stale",
                ))

        # Missed prevotes
        val = get_metric(metrics, "signer_missed_prevotes")
        if val is not None:
            report.missed_prevotes = int(val)
            if int(val) > th["missed_prevotes"]:
                checks.append(CheckResult(
                    name="missed_prevotes",
                    status=CheckStatus.WARNING,
                    message=f"Missed prevotes (consecutive): {int(val)}",
                    alert_key="missed_prevotes",
                ))
            else:
                checks.append(CheckResult(
                    name="missed_prevotes",
                    status=CheckStatus.OK,
                    message=f"Missed prevotes (consecutive): {int(val)}",
                    alert_key="missed_prevotes",
                ))

        # Missed precommits
        val = get_metric(metrics, "signer_missed_precommits")
        if val is not None:
            report.missed_precommits = int(val)
            if int(val) > th["missed_precommits"]:
                checks.append(CheckResult(
                    name="missed_precommits",
                    status=CheckStatus.CRITICAL,
                    message=f"Missed precommits (consecutive): {int(val)}",
                    alert_key="missed_precommits",
                ))
            else:
                checks.append(CheckResult(
                    name="missed_precommits",
                    status=CheckStatus.OK,
                    message=f"Missed precommits (consecutive): {int(val)}",
                    alert_key="missed_precommits",
                ))

        # Seconds since last precommit (informational only, no alert —
        # non-leader cosigners legitimately show large values)
        val = get_metric(metrics, "signer_seconds_since_last_precommit")
        if val is not None:
            report.seconds_since_last_precommit = val
            # Fresh precommit means this cosigner is the Raft leader
            report.is_raft_leader = val < cfg.block_time * 3

        # Insufficient cosigner errors (counter — detect increase)
        val = get_metric(metrics, "signer_error_total_insufficient_cosigners")
        if val is not None:
            report.insufficient_cosigner_errors = int(val)
            if self.prev_insufficient_cosigners is not None:
                delta = val - self.prev_insufficient_cosigners
                if delta > 0:
                    checks.append(CheckResult(
                        name="insufficient_cosigners",
                        status=CheckStatus.CRITICAL,
                        message=f"Insufficient cosigner errors: {int(val):,} (+{int(delta)} since last check)",
                        alert_key="insufficient_cosigners",
                    ))
                else:
                    checks.append(CheckResult(
                        name="insufficient_cosigners",
                        status=CheckStatus.OK,
                        message=f"Insufficient cosigner errors: {int(val):,} (stable)",
                        alert_key="insufficient_cosigners",
                    ))
            else:
                checks.append(CheckResult(
                    name="insufficient_cosigners",
                    status=CheckStatus.OK,
                    message=f"Insufficient cosigner errors: {int(val):,} (stable)",
                    alert_key="insufficient_cosigners",
                ))
            self.prev_insufficient_cosigners = val

    def _check_raft(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        # Raft election timeouts (counter — detect increase)
        val = get_metric(metrics, "signer_total_raft_leader_election_timeout")
        if val is not None:
            report.raft_election_timeouts = int(val)
            if self.prev_raft_election_timeouts is not None:
                delta = val - self.prev_raft_election_timeouts
                if delta > 0:
                    checks.append(CheckResult(
                        name="raft_election_timeouts",
                        status=CheckStatus.WARNING,
                        message=f"Election timeouts: {int(val):,} (+{int(delta)} since last check)",
                        alert_key="raft_election_timeouts",
                    ))
                else:
                    checks.append(CheckResult(
                        name="raft_election_timeouts",
                        status=CheckStatus.OK,
                        message=f"Election timeouts: {int(val):,} (stable)",
                        alert_key="raft_election_timeouts",
                    ))
            else:
                checks.append(CheckResult(
                    name="raft_election_timeouts",
                    status=CheckStatus.OK,
                    message=f"Election timeouts: {int(val):,} (stable)",
                    alert_key="raft_election_timeouts",
                ))
            self.prev_raft_election_timeouts = val

        # Seconds since last ephemeral share
        val = get_metric(metrics, "signer_seconds_since_last_local_ephemeral_share_time")
        if val is not None:
            report.seconds_since_last_ephemeral_share = val


    def _check_cosigners(self, metrics: Optional[Dict], report: FullReport, checks: List[CheckResult]):
        cfg = self.config
        th = cfg.thresholds

        # Get missed ephemeral shares from metrics
        # peerid label is the full p2pAddr, e.g. peerid="tcp://192.168.101.102:9876"
        missed_shares_by_addr = {}
        if metrics:
            labeled = get_labeled_metrics(metrics, "signer_missed_ephemeral_shares")
            for label, val in labeled.items():
                # label is like: peerid="tcp://192.168.101.102:9876"
                try:
                    addr_key = label.split('"')[1]
                    missed_shares_by_addr[addr_key] = int(val)
                except (IndexError, ValueError):
                    continue

        for cs in cfg.cosigners:
            shard_id = cs["shard_id"]
            addr = cs["address"]
            # Self = cosigner whose address is NOT in metrics (no missed shares for self)
            is_self = bool(addr) and addr not in missed_shares_by_addr and bool(missed_shares_by_addr)
            shares = None if is_self else missed_shares_by_addr.get(addr)

            status = CosignerStatus(
                shard_id=shard_id,
                address=addr or "(self)",
                missed_shares=shares,
                is_self=is_self,
            )
            report.cosigners.append(status)

            # Missed shares — alert when growing for 2+ consecutive checks (ignores brief hiccups)
            if shares is not None and not is_self:
                prev = self.prev_missed_shares.get(addr)
                self.prev_missed_shares[addr] = shares
                if prev is not None and shares > prev:
                    self.cosigner_miss_streak[addr] = self.cosigner_miss_streak.get(addr, 0) + 1
                else:
                    self.cosigner_miss_streak[addr] = 0

                if self.cosigner_miss_streak.get(addr, 0) >= 3:
                    checks.append(CheckResult(
                        name=f"cosigner_{shard_id}_shares",
                        status=CheckStatus.WARNING,
                        message=f"Cosigner shard {shard_id} ({addr}) missed shares growing ({shares})",
                        alert_key=f"cosigner_{shard_id}_shares",
                    ))

    def _check_sentry_connect(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        """Check signer_sentry_connect_tries gauge — grows while horcrux can't reach a sentry."""
        val = get_metric(metrics, "signer_sentry_connect_tries")
        if val is not None:
            report.sentry_connect_tries = int(val)
            if self.prev_sentry_connect_tries is not None:
                delta = val - self.prev_sentry_connect_tries
                if delta > 0:
                    checks.append(CheckResult(
                        name="sentry_connect_tries",
                        status=CheckStatus.WARNING,
                        message=f"Sentry connect retries: {int(val):,} (+{int(delta)} since last check)",
                        alert_key="sentry_connect_tries",
                    ))
                else:
                    checks.append(CheckResult(
                        name="sentry_connect_tries",
                        status=CheckStatus.OK,
                        message=f"Sentry connect retries: {int(val):,} (stable)",
                        alert_key="sentry_connect_tries",
                    ))
            else:
                checks.append(CheckResult(
                    name="sentry_connect_tries",
                    status=CheckStatus.OK,
                    message=f"Sentry connect retries: {int(val):,} (stable)",
                    alert_key="sentry_connect_tries",
                ))
            self.prev_sentry_connect_tries = val

    def _check_sentries(self, report: FullReport, checks: List[CheckResult]):
        cfg = self.config
        th = cfg.thresholds
        rpc_port = th["rpc_port"]

        for i, sentry in enumerate(cfg.sentries):
            addr = sentry["address"]
            host, _ = parse_address(addr)
            block_height = fetch_block_height(host, rpc_port, cfg.metrics_timeout)
            rpc_ok = block_height is not None

            status = SentryStatus(
                index=i + 1,
                address=addr,
                host=host,
                block_height=block_height,
                rpc_ok=rpc_ok,
            )
            report.sentries.append(status)

            if not rpc_ok:
                checks.append(CheckResult(
                    name=f"sentry_{i}_rpc",
                    status=CheckStatus.WARNING,
                    message=f"Sentry {i + 1} ({addr}) RPC unreachable (port {rpc_port})",
                    alert_key=f"sentry_{i}_rpc",
                ))

    def _check_error_counters(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        cfg = self.config

        # Helper for counter-delta checks
        def _counter_delta(metric_name, prev_attr, report_field, check_name, alert_key, severity, label):
            val = get_metric(metrics, metric_name)
            if val is None:
                return
            setattr(report, report_field, int(val))
            prev = getattr(self, prev_attr)
            if prev is not None:
                delta = val - prev
                if delta > 0:
                    checks.append(CheckResult(
                        name=check_name,
                        status=severity,
                        message=f"{label}: {int(val):,} (+{int(delta)} since last check)",
                        alert_key=alert_key,
                    ))
                else:
                    checks.append(CheckResult(
                        name=check_name,
                        status=CheckStatus.OK,
                        message=f"{label}: {int(val):,} (stable)",
                        alert_key=alert_key,
                    ))
            else:
                checks.append(CheckResult(
                    name=check_name,
                    status=CheckStatus.OK,
                    message=f"{label}: {int(val):,} (stable)",
                    alert_key=alert_key,
                ))
            setattr(self, prev_attr, val)

        _counter_delta(
            "signer_error_total_invalid_signatures",
            "prev_invalid_signatures",
            "invalid_signature_errors",
            "invalid_signatures", "invalid_signatures",
            CheckStatus.CRITICAL, "Invalid signature errors",
        )
        _counter_delta(
            "signer_total_beyond_block_errors",
            "prev_beyond_block_errors",
            "beyond_block_errors",
            "beyond_block_errors", "beyond_block_errors",
            CheckStatus.WARNING, "Beyond-block errors",
        )
        _counter_delta(
            "signer_total_failed_sign_vote",
            "prev_failed_sign_votes",
            "failed_sign_votes",
            "failed_sign_votes", "failed_sign_votes",
            CheckStatus.WARNING, "Failed sign votes",
        )

    def _check_signing_freshness(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        cfg = self.config
        th = cfg.thresholds
        block_time = cfg.block_time

        # Seconds since last sign finish (only meaningful for leader)
        val = get_metric(metrics, "signer_seconds_since_last_local_sign_finish_time")
        if val is not None:
            report.seconds_since_last_sign_finish = val
            threshold = block_time * th["sign_finish_stale_factor"]
            is_leader = report.is_raft_leader
            if is_leader and val > threshold:
                checks.append(CheckResult(
                    name="sign_finish_stale",
                    status=CheckStatus.WARNING,
                    message=f"Last sign finish {val:.1f}s ago (threshold {threshold}s)",
                    alert_key="sign_finish_stale",
                ))
            else:
                checks.append(CheckResult(
                    name="sign_finish_stale",
                    status=CheckStatus.OK,
                    message=f"Last sign finish {val:.1f}s ago",
                    alert_key="sign_finish_stale",
                ))

        # Ephemeral share staleness
        eph = report.seconds_since_last_ephemeral_share
        if eph is not None:
            threshold = block_time * th["ephemeral_share_stale_factor"]
            if eph > threshold:
                checks.append(CheckResult(
                    name="ephemeral_share_stale",
                    status=CheckStatus.WARNING,
                    message=f"Last ephemeral share {eph:.1f}s ago (threshold {threshold}s)",
                    alert_key="ephemeral_share_stale",
                ))
            else:
                checks.append(CheckResult(
                    name="ephemeral_share_stale",
                    status=CheckStatus.OK,
                    message=f"Last ephemeral share {eph:.1f}s ago",
                    alert_key="ephemeral_share_stale",
                ))

    def _check_sentry_divergence(self, report: FullReport, checks: List[CheckResult]):
        th = self.config.thresholds
        heights = [s.block_height for s in report.sentries if s.block_height is not None]
        if len(heights) < 2:
            return
        divergence = max(heights) - min(heights)
        max_allowed = th["sentry_height_divergence"]
        if divergence > max_allowed:
            checks.append(CheckResult(
                name="sentry_height_divergence",
                status=CheckStatus.WARNING,
                message=f"Sentry height divergence: {divergence} blocks (max {max_allowed})",
                alert_key="sentry_height_divergence",
            ))
        else:
            checks.append(CheckResult(
                name="sentry_height_divergence",
                status=CheckStatus.OK,
                message=f"Sentry height divergence: {divergence} blocks",
                alert_key="sentry_height_divergence",
            ))

    def _check_process_health(self, metrics: Dict, report: FullReport, checks: List[CheckResult]):
        th = self.config.thresholds

        # File descriptor usage
        open_fds = get_metric(metrics, "process_open_fds")
        max_fds = get_metric(metrics, "process_max_fds")
        if open_fds is not None:
            report.process_open_fds = int(open_fds)
        if max_fds is not None:
            report.process_max_fds = int(max_fds)
        if open_fds is not None and max_fds is not None and max_fds > 0:
            pct = (open_fds / max_fds) * 100
            threshold = th["fd_usage_percent"]
            if pct > threshold:
                checks.append(CheckResult(
                    name="fd_usage",
                    status=CheckStatus.WARNING,
                    message=f"FD usage: {int(open_fds)}/{int(max_fds)} ({pct:.0f}%, threshold {threshold}%)",
                    alert_key="fd_usage",
                ))
            else:
                checks.append(CheckResult(
                    name="fd_usage",
                    status=CheckStatus.OK,
                    message=f"FD usage: {int(open_fds)}/{int(max_fds)} ({pct:.0f}%)",
                    alert_key="fd_usage",
                ))

        # Memory usage
        mem = get_metric(metrics, "process_resident_memory_bytes")
        if mem is not None:
            report.process_memory_bytes = int(mem)
            mem_threshold = th["memory_bytes"]
            if mem_threshold > 0 and mem > mem_threshold:
                checks.append(CheckResult(
                    name="memory_usage",
                    status=CheckStatus.WARNING,
                    message=f"Resident memory: {_fmt_bytes(mem)} (threshold {_fmt_bytes(mem_threshold)})",
                    alert_key="memory_usage",
                ))
            else:
                checks.append(CheckResult(
                    name="memory_usage",
                    status=CheckStatus.OK,
                    message=f"Resident memory: {_fmt_bytes(mem)}",
                    alert_key="memory_usage",
                ))

        # Goroutine growth
        goroutines = get_metric(metrics, "go_goroutines")
        if goroutines is not None:
            report.go_goroutines = int(goroutines)
            if self.prev_goroutines is not None and int(goroutines) > self.prev_goroutines:
                self.goroutine_grow_streak += 1
            else:
                self.goroutine_grow_streak = 0
            self.prev_goroutines = int(goroutines)

            growth_threshold = th["goroutine_growth_checks"]
            if self.goroutine_grow_streak >= growth_threshold:
                checks.append(CheckResult(
                    name="goroutine_growth",
                    status=CheckStatus.WARNING,
                    message=f"Goroutines growing: {int(goroutines)} (growing for {self.goroutine_grow_streak} checks)",
                    alert_key="goroutine_growth",
                ))
            else:
                checks.append(CheckResult(
                    name="goroutine_growth",
                    status=CheckStatus.OK,
                    message=f"Goroutines: {int(goroutines)}",
                    alert_key="goroutine_growth",
                ))


def _fmt_bytes(b: float) -> str:
    """Format bytes into human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"
