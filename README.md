# Horcrux Monitoring

Daemon that monitors [Horcrux](https://github.com/strangelove-ventures/horcrux) threshold signer health via Prometheus metrics and CometBFT RPC, sending alerts to Slack and optionally Telegram.

## Features

- Prometheus metrics parsing (signing height, missed votes, cosigner errors, raft state)
- CometBFT RPC block height checks for sentry nodes
- Slack notifications (required) + Telegram (optional)
- Scheduled status reports (3x/day, configurable)
- Alert cooldown to avoid spam
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

# Install service
sudo cp horcrux-monitoring.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now horcrux-monitoring
sudo journalctl -u horcrux-monitoring -f
```

## How It Works

Every `check_interval` (default 30s) the daemon collects metrics and runs health checks.

### Reports

| Type | When | Content |
|------|------|---------|
| Startup | On daemon start | Full status |
| Alert | New problem detected | Full status |
| Recovery | Problem resolved | Full status |
| Scheduled | 09:00, 13:00, 17:00 Dubai time | Full status |

### Alert Timing

- **New problem** — full report sent immediately
- **Ongoing problem** — re-alert after `alert_cooldown` (default 5min)
- **Recovery** — full report when problem clears
- **Cosigner unreachable** — alert after missed shares grow for 3 consecutive checks (90s), single hiccup ignored

### Health Checks

| Check | Source | Severity | Condition |
|-------|--------|----------|-----------|
| Metrics endpoint down | HTTP GET `/metrics` | Critical | Unreachable |
| Height stale | `signer_last_prevote_height` | Critical | No change for 3 checks (90s) |
| Missed precommits | `signer_missed_precommits` | Critical | > threshold (default 3) |
| Seconds since last sign | `signer_seconds_since_last_precommit` | Critical | > threshold (default 30s) |
| Insufficient cosigners | `signer_error_total_insufficient_cosigners` | Critical | Counter increasing |
| Missed prevotes | `signer_missed_prevotes` | Warning | > threshold (default 5) |
| Cosigner unreachable | `signer_missed_ephemeral_shares` | Warning | Growing for 3+ checks |
| Sentry unreachable | RPC `/status` | Warning | Unreachable |
| Raft election timeouts | `signer_total_raft_leader_election_timeout` | Warning | Counter increasing |

## Network Requests

Every check cycle (default 30s), the monitor makes these requests:

| # | Type | Target | Purpose |
|---|------|--------|---------|
| 1 | HTTP GET | `http://{debugAddr}/metrics` | Fetch Prometheus metrics from local horcrux node |
| 2 | HTTP GET | `http://{sentry_host}:{rpc_port}/status` per sentry | Fetch latest block height from CometBFT RPC |
| 3 | HTTP POST | Slack webhook | Send alert/report (only when needed) |
| 4 | HTTP POST | Telegram API | Send alert/report (optional, only when needed) |

All read-only. No TCP probes, no connections to signing infrastructure. Cosigner health determined from Prometheus metrics (`signer_missed_ephemeral_shares`). RPC host derived from `privValAddr`, port from `thresholds.rpc_port` (default 26657). For a 4-sentry setup: **1 HTTP GET (metrics) + 4 HTTP GET (RPC) = 5 requests per cycle**.

## Multi-cosigner setup

Set a unique `name` in each node's config:

```yaml
# On cosigner 1
name: "cosigner-1"

# On cosigner 2
name: "cosigner-2"
```

Reports will include the name: `📊 Horcrux Status Report [cosigner-1] — 2025-02-11 13:00 (Dubai)`
