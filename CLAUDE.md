# Horcrux Monitoring

Python daemon that monitors Horcrux threshold signer health via Prometheus metrics and TCP probes, sending alerts to Slack/Telegram.

## Run

```bash
python3 -m horcrux_monitor --config config.yaml --dry-run --once   # single check, stdout only
python3 -m horcrux_monitor --config config.yaml --once             # single check with notifications
python3 -m horcrux_monitor --config config.yaml                    # daemon mode
```

## Project Structure

- `horcrux_monitor/` — main package
  - `__main__.py` — CLI args, daemon loop, signal handling
  - `config.py` — YAML config + horcrux config loading, env overrides
  - `models.py` — dataclasses: Severity, CheckStatus, CheckResult, CosignerStatus, SentryStatus, FullReport, AlertState
  - `collector.py` — Prometheus text parser + TCP probes
  - `checker.py` — health check logic → list of CheckResult
  - `state.py` — alert state tracking, cooldown, scheduled report timing
  - `report.py` — format FullReport for Slack/Telegram/log
  - `notifiers/` — base + slack + telegram + logger

## Network Requests (per check cycle, every 30s)

### To local node
| # | Type | Target | Source | Purpose |
|---|------|--------|--------|---------|
| 1 | HTTP GET | `http://{debugAddr}/metrics` | `collector.fetch_metrics()` | Fetch Prometheus metrics (signing height, missed votes, cosigner errors, raft state) |

`debugAddr` is read from horcrux config (e.g. `127.0.0.1:2112`). Timeout: `metrics_timeout` (default 5s).

### To remote cosigners
| # | Type | Target | Source | Purpose |
|---|------|--------|--------|---------|
| 2 | TCP handshake only | `{cosigner.p2pAddr}` (e.g. `192.168.101.102:2223`) | `collector.tcp_probe()` | Check cosigner port is open |

One probe per remote cosigner. Skipped for `is_self`. Addresses from horcrux config `cosigners[].p2pAddr`. Timeout: `thresholds.tcp_timeout` (default 3s). **No data is sent** — only TCP 3-way handshake (SYN/SYN-ACK/ACK) then immediate FIN.

### To sentry nodes
| # | Type | Target | Source | Purpose |
|---|------|--------|--------|---------|
| 3 | TCP handshake only | `{chainNode.privValAddr}` (e.g. `192.168.100.101:11010`) | `collector.tcp_probe()` | Check sentry port is open |
| 4 | HTTP GET | `http://{host}:{rpc_port}/status` (e.g. `http://192.168.100.101:26657/status`) | `collector.fetch_block_height()` | Fetch latest block height from CometBFT RPC |

TCP probe: one per sentry, addresses from horcrux config `chainNodes[].privValAddr`. Timeout: `thresholds.tcp_timeout` (default 3s). **No data is sent** — handshake + close.

RPC height: host extracted from `privValAddr`, port from `thresholds.rpc_port` (default 26657). Response JSON: `result.sync_info.latest_block_height`.

### Outbound notifications
| # | Type | Target | Source | Purpose |
|---|------|--------|--------|---------|
| 5 | HTTP POST | Slack webhook URL | `notifiers/slack.py` | Send alert/report |
| 6 | HTTP POST | `api.telegram.org` | `notifiers/telegram.py` | Send alert/report (optional) |

Only sent when there is something to report (alert, recovery, scheduled report).

### Summary per cycle

For a typical 7-cosigner + 4-sentry setup: **1 HTTP GET (metrics) + 6 TCP probes (cosigners) + 4 TCP probes (sentries) + 4 HTTP GET (sentry RPC) = 15 requests**.

## Conventions

- Python 3.11+, stdlib `zoneinfo` for timezone handling
- Minimal dependencies: `pyyaml`, `requests` only
- No async — simple sequential loop with `time.sleep`
- Prometheus text format parsed manually (no external parser)
- Config: monitoring YAML + horcrux YAML (separate files)
- Env vars override YAML for secrets: `SLACK_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
