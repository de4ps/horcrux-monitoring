import os
import yaml
import logging

log = logging.getLogger(__name__)

DEFAULTS = {
    "check_interval": 30,
    "metrics_timeout": 5,
    "block_time": 6,
    "alert_cooldown": 300,
    "timezone": "Asia/Dubai",
"scheduled_reports": {"hours": [9, 13, 17]},
    "thresholds": {
        "missed_precommits": 3,
        "missed_prevotes": 5,
        "missed_ephemeral_shares": 5,
        "seconds_since_last_sign": 30,
        "height_stale_checks": 3,
        "tcp_timeout": 3,
        "rpc_port": 26657,
    },
}


class Config:
    def __init__(self, path: str):
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        # Merge defaults
        for k, v in DEFAULTS.items():
            if k not in data:
                data[k] = v
            elif isinstance(v, dict):
                merged = dict(v)
                merged.update(data[k])
                data[k] = merged

        self.name: str = data.get("name", "horcrux")
        self.check_interval: int = data["check_interval"]
        self.metrics_timeout: int = data["metrics_timeout"]
        self.block_time: int = data["block_time"]
        self.alert_cooldown: int = data["alert_cooldown"]
        self.timezone: str = data["timezone"]
        self.scheduled_hours: list = data["scheduled_reports"]["hours"]
        self.thresholds: dict = data["thresholds"]

        # Slack config with env overrides
        slack = data.get("slack", {})
        self.slack_webhook_url: str = os.environ.get(
            "SLACK_WEBHOOK_URL", slack.get("webhook_url", "")
        )
        self.slack_mention: str = slack.get("mention", "")

        # Telegram config with env overrides
        tg = data.get("telegram", {})
        self.telegram_enabled: bool = tg.get("enabled", False)
        self.telegram_bot_token: str = os.environ.get(
            "TELEGRAM_BOT_TOKEN", tg.get("bot_token", "")
        )
        self.telegram_chat_id: str = os.environ.get(
            "TELEGRAM_CHAT_ID", tg.get("chat_id", "")
        )

        # Load horcrux config
        horcrux_path = data.get("horcrux_config", "")
        self.debug_addr = ""
        self.cosigners = []  # list of {shard_id, address, is_self}
        self.sentries = []   # list of {address}
        self.threshold = 0
        self.shards_total = 0

        if horcrux_path:
            self._load_horcrux_config(horcrux_path)

    def _load_horcrux_config(self, path: str):
        try:
            with open(path) as f:
                hc = yaml.safe_load(f) or {}
        except Exception as e:
            log.error("Failed to load horcrux config %s: %s", path, e)
            return

        self.debug_addr = hc.get("debugAddr", "")

        # Parse cosigners from signState or cosigner config
        self.threshold = hc.get("threshold", 0)
        self.shards_total = hc.get("shards", 0) or len(hc.get("cosigners", [])) + 1

        # Parse cosigners - Horcrux config has cosigner entries
        cosigners_cfg = hc.get("cosigners", [])
        shard_id = hc.get("shardID", 0)

        # Build cosigner list
        for cs in cosigners_cfg:
            p2p_addr = cs.get("p2pAddr", "")
            cs_shard = cs.get("shardID", 0)
            self.cosigners.append({
                "shard_id": cs_shard,
                "address": p2p_addr,
                "is_self": (cs_shard == shard_id),
            })

        # If this node's shard isn't in cosigners, add it
        if shard_id and not any(c["shard_id"] == shard_id for c in self.cosigners):
            self.cosigners.append({
                "shard_id": shard_id,
                "address": "",
                "is_self": True,
            })

        # Sort by shard_id
        self.cosigners.sort(key=lambda c: c["shard_id"])

        # Parse chain nodes / sentries
        chain_nodes = hc.get("chainNodes", [])
        for node in chain_nodes:
            addr = node.get("privValAddr", "")
            if addr:
                self.sentries.append({"address": addr})

    @property
    def metrics_url(self) -> str:
        if not self.debug_addr:
            return ""
        addr = self.debug_addr
        if not addr.startswith("http"):
            addr = f"http://{addr}"
        return f"{addr}/metrics"
