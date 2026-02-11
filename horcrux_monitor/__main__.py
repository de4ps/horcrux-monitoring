import argparse
import logging
import signal
import sys
import time

from .config import Config
from .checker import Checker
from .state import StateManager
from .report import (
    format_full_report,
    format_problem_alert,
    format_recovery,
    format_startup_report,
)
from .notifiers.base import BaseNotifier
from .notifiers.slack import SlackNotifier
from .notifiers.telegram import TelegramNotifier
from .notifiers.logger import LogNotifier

log = logging.getLogger("horcrux_monitor")

running = True


def main():
    parser = argparse.ArgumentParser(description="Horcrux Monitoring Daemon")
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print reports to stdout only, do not send notifications")
    parser.add_argument("--once", action="store_true",
                        help="Run a single check and exit")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info("Loading config from %s", args.config)
    config = Config(args.config)

    # Build notifier list
    notifiers: list[BaseNotifier] = [LogNotifier()]

    if not args.dry_run:
        if config.slack_webhook_url:
            notifiers.append(SlackNotifier(config.slack_webhook_url, config.slack_mention))
            log.info("Slack notifier enabled")
        if config.telegram_enabled and config.telegram_bot_token:
            notifiers.append(TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id))
            log.info("Telegram notifier enabled")

    checker = Checker(config)
    state = StateManager(
        state_file=config.state_file if not args.dry_run else "",
        alert_cooldown=config.alert_cooldown,
        scheduled_hours=config.scheduled_hours,
        timezone=config.timezone,
    )

    # Signal handling
    def handle_signal(signum, frame):
        global running
        log.info("Received signal %d, shutting down...", signum)
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("Horcrux monitor starting (interval=%ds, dry_run=%s, once=%s)",
             config.check_interval, args.dry_run, args.once)

    # Startup report
    report = checker.run()
    name = config.name
    startup_msg = format_startup_report(report, config.timezone, name=name)
    notify_all(notifiers, startup_msg)
    state.process_report(report)  # Initialize state from first run
    state.save_state()

    if args.once:
        return

    # Daemon loop
    while running:
        time.sleep(config.check_interval)
        if not running:
            break

        try:
            report = checker.run()
            result = state.process_report(report)

            # Problem alerts
            if result["new_alerts"]:
                msg = format_problem_alert(result["new_alerts"], name=name)
                notify_all(notifiers, msg)

            if result["re_alerts"]:
                msg = format_problem_alert(result["re_alerts"], name=name, is_re_alert=True)
                notify_all(notifiers, msg)

            # Recovery notifications
            if result["recoveries"]:
                msg = format_recovery(result["recoveries"], state.format_duration, name=name)
                notify_all(notifiers, msg)

            # Scheduled reports
            if state.is_scheduled_report_due():
                msg = format_full_report(report, config.timezone, name=name)
                notify_all(notifiers, msg)

            state.save_state()

        except Exception:
            log.exception("Error in monitoring loop")

    log.info("Horcrux monitor stopped")


def notify_all(notifiers: list[BaseNotifier], message: str):
    for n in notifiers:
        try:
            n.send(message)
        except Exception:
            log.exception("Notifier %s failed", type(n).__name__)


if __name__ == "__main__":
    main()
