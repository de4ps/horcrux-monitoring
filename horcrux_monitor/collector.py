import logging
import requests
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)


def fetch_metrics(url: str, timeout: int = 5) -> Optional[Dict[str, float]]:
    """Fetch and parse Prometheus text format metrics."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return parse_prometheus_text(resp.text)
    except Exception as e:
        log.warning("Failed to fetch metrics from %s: %s", url, e)
        return None


def parse_prometheus_text(text: str) -> Dict[str, float]:
    """Parse Prometheus text exposition format into a flat dict.

    Keys include label suffixes for labeled metrics:
      signer_missed_ephemeral_shares{peerid="2"} → "signer_missed_ephemeral_shares{peerid=\"2\"}"
    """
    metrics = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            # Split into name (with optional labels) and value
            if " " in line:
                key, val_str = line.rsplit(" ", 1)
                # Prometheus format: metric_name [labels] value [timestamp]
                metrics[key.strip()] = float(val_str)
        except (ValueError, IndexError):
            continue
    return metrics


def get_metric(metrics: Dict[str, float], name: str) -> Optional[float]:
    """Get a metric value by exact name."""
    return metrics.get(name)


def get_labeled_metrics(metrics: Dict[str, float], prefix: str) -> Dict[str, float]:
    """Get all metrics matching a prefix (for labeled metrics).

    Returns dict of label_content → value, e.g.:
      prefix="signer_missed_ephemeral_shares" →
        {"peerid=\"2\"": 0.0, "peerid=\"3\"": 5.0}
    """
    result = {}
    search = prefix + "{"
    for key, val in metrics.items():
        if key.startswith(search) and key.endswith("}"):
            label_part = key[len(prefix) + 1:-1]  # content between { and }
            result[label_part] = val
    return result


def fetch_block_height(host: str, rpc_port: int, timeout: int = 5) -> Optional[int]:
    """Fetch latest block height from CometBFT/Tendermint RPC /status endpoint."""
    url = f"http://{host}:{rpc_port}/status"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        height = int(data["result"]["sync_info"]["latest_block_height"])
        return height
    except Exception as e:
        log.warning("Failed to fetch block height from %s: %s", url, e)
        return None


def parse_address(addr: str) -> Tuple[str, int]:
    """Parse host:port string. Returns (host, port)."""
    if ":" in addr:
        parts = addr.rsplit(":", 1)
        return parts[0], int(parts[1])
    return addr, 0
