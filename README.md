# Horcrux Monitoring

Daemon that monitors [Horcrux](https://github.com/strangelove-ventures/horcrux) threshold signer health via Prometheus metrics and TCP probes, sending alerts to Slack and optionally Telegram.

## Features

- Prometheus metrics parsing (signing height, missed votes, cosigner errors, raft state)
- TCP probes for cosigners and sentry nodes
- Slack notifications (required) + Telegram (optional)
- Scheduled status reports (3x/day, configurable)
- Alert cooldown to avoid spam
- State persistence across restarts
- Per-node naming for multi-cosigner deployments

## Installation

```bash
sudo git clone https://github.com/de4ps/horcrux-monitoring.git /opt/horcrux-monitoring
sudo /opt/horcrux-monitoring/install.sh
sudo nano /etc/horcrux-monitoring/config.yaml
sudo systemctl enable --now horcrux-monitoring
```

## Configuration

Edit `config.yaml`:

```yaml
name: "cosigner-1"                              # Node name shown in reports
horcrux_config: /home/horcrux/.horcrux/config.yaml  # Path to horcrux config

slack:
  webhook_url: "https://hooks.slack.com/..."     # Or set SLACK_WEBHOOK_URL env var
```

Secrets can be set via environment variables instead of the config file:
- `SLACK_WEBHOOK_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

See [config.example.yaml](config.example.yaml) for all options.

## Usage

```bash
# Single check, stdout only (test)
python3 -m horcrux_monitor --config config.yaml --dry-run --once

# Single check with real notifications
python3 -m horcrux_monitor --config config.yaml --once

# Daemon mode
python3 -m horcrux_monitor --config config.yaml

# Debug logging
python3 -m horcrux_monitor --config config.yaml --debug
```

## systemd

```bash
# Optional: env file for secrets
sudo cp horcrux-monitoring.env.example /etc/horcrux-monitoring/env
sudo nano /etc/horcrux-monitoring/env

# Create state directory
sudo mkdir -p /var/lib/horcrux-monitoring
sudo chown horcrux:horcrux /var/lib/horcrux-monitoring

# Install service
sudo cp horcrux-monitoring.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now horcrux-monitoring
sudo journalctl -u horcrux-monitoring -f
```

## Network Requests

Every check cycle (default 30s), the monitor makes these requests:

| # | Type | Target | Purpose |
|---|------|--------|---------|
| 1 | HTTP GET | `http://{debugAddr}/metrics` | Fetch Prometheus metrics from local horcrux node |
| 2 | TCP handshake only | `{cosigner.p2pAddr}` per remote cosigner | Check port is open (no data sent, just SYN/ACK + FIN) |
| 3 | TCP handshake only | `{chainNode.privValAddr}` per sentry | Check port is open (no data sent, just SYN/ACK + FIN) |
| 4 | HTTP GET | `http://{sentry_host}:{rpc_port}/status` per sentry | Fetch latest block height from CometBFT RPC |
| 5 | HTTP POST | Slack webhook | Send alert/report (only when needed) |
| 6 | HTTP POST | Telegram API | Send alert/report (optional, only when needed) |

All addresses are read from the horcrux config file. RPC host is derived from `privValAddr`, port from `thresholds.rpc_port` (default 26657). For a 7-cosigner + 4-sentry setup: **1 HTTP GET (metrics) + 6 TCP probes + 4 TCP probes + 4 HTTP GET (RPC) = 15 requests per cycle**. Self-cosigner is skipped.

## Multi-cosigner setup

Set a unique `name` in each node's config:

```yaml
# On cosigner 1
name: "cosigner-1"

# On cosigner 2
name: "cosigner-2"
```

Reports will include the name: `📊 Horcrux Status Report [cosigner-1] — 2025-02-11 13:00 (Dubai)`
