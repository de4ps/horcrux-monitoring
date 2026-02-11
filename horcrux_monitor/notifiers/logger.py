import logging

from .base import BaseNotifier

log = logging.getLogger(__name__)


class LogNotifier(BaseNotifier):
    """Notifier that prints to stdout/log. Always active."""

    def send(self, message: str) -> bool:
        log.info("Report:\n%s", message)
        return True
