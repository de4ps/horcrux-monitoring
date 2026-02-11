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
        self.prev_height: Optional[int] = None
        self.height_stale_count: int = 0

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
            self._check_signing(metrics, report, checks)
            self._check_raft(metrics, report, checks)

        # TCP probes
        self._check_cosigners(metrics, report, checks)
        self._check_sentries(report, checks)

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

            if self.height_stale_count >= th["height_stale_checks"]:
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

        # Seconds since last precommit
        val = get_metric(metrics, "signer_seconds_since_last_precommit")
        if val is not None:
            report.seconds_since_last_precommit = val
            if val > th["seconds_since_last_sign"]:
                checks.append(CheckResult(
                    name="seconds_since_last_sign",
                    status=CheckStatus.CRITICAL,
                    message=f"Seconds since last precommit: {val:.1f}s",
                    alert_key="seconds_since_last_sign",
                ))
            else:
                checks.append(CheckResult(
                    name="seconds_since_last_sign",
                    status=CheckStatus.OK,
                    message=f"Seconds since last precommit: {val:.1f}s",
                    alert_key="seconds_since_last_sign",
                ))

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

            # Missed shares check
            if shares is not None and shares > th["missed_ephemeral_shares"]:
                checks.append(CheckResult(
                    name=f"cosigner_{shard_id}_shares",
                    status=CheckStatus.WARNING,
                    message=f"Cosigner shard {shard_id} ({addr}) missed {shares} ephemeral shares",
                    alert_key=f"cosigner_{shard_id}_shares",
                ))

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
