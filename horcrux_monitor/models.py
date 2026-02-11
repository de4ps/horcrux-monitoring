from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class Severity(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class CheckStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    severity: Severity = Severity.OK
    alert_key: str = ""

    def __post_init__(self):
        if not self.alert_key:
            self.alert_key = self.name
        if self.status == CheckStatus.CRITICAL:
            self.severity = Severity.CRITICAL
        elif self.status == CheckStatus.WARNING:
            self.severity = Severity.WARNING
        else:
            self.severity = Severity.OK


@dataclass
class CosignerStatus:
    shard_id: int
    address: str
    tcp_ok: bool
    missed_shares: Optional[int]  # None for self
    is_self: bool = False

    @property
    def status(self) -> CheckStatus:
        if not self.tcp_ok:
            return CheckStatus.WARNING
        if self.missed_shares is not None and self.missed_shares > 0:
            return CheckStatus.WARNING
        return CheckStatus.OK


@dataclass
class SentryStatus:
    index: int
    address: str
    tcp_ok: bool
    block_height: Optional[int] = None
    rpc_ok: bool = True

    @property
    def status(self) -> CheckStatus:
        if not self.tcp_ok or not self.rpc_ok:
            return CheckStatus.WARNING
        return CheckStatus.OK


@dataclass
class FullReport:
    timestamp: float = field(default_factory=time.time)

    # Signing metrics
    last_prevote_height: Optional[int] = None
    last_precommit_height: Optional[int] = None
    missed_prevotes: Optional[int] = None
    missed_precommits: Optional[int] = None
    seconds_since_last_precommit: Optional[float] = None
    insufficient_cosigner_errors: Optional[int] = None

    # Cosigners & sentries
    cosigners: list = field(default_factory=list)
    sentries: list = field(default_factory=list)

    # Raft
    raft_election_timeouts: Optional[int] = None
    seconds_since_last_ephemeral_share: Optional[float] = None

    # Check results
    checks: list = field(default_factory=list)

    # Metrics endpoint reachable
    metrics_ok: bool = True

    @property
    def has_problems(self) -> bool:
        return any(
            c.status in (CheckStatus.WARNING, CheckStatus.CRITICAL)
            for c in self.checks
        )

    @property
    def has_critical(self) -> bool:
        return any(c.status == CheckStatus.CRITICAL for c in self.checks)


@dataclass
class AlertState:
    severity: Severity
    message: str
    first_seen: float
    last_alerted: float
    count: int = 1
