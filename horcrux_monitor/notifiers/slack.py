import logging
import requests

from .base import BaseNotifier

log = logging.getLogger(__name__)


class SlackNotifier(BaseNotifier):
    def __init__(self, webhook_url: str, mention: str = ""):
        self.webhook_url = webhook_url
        self.mention = mention

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            log.warning("Slack webhook URL not configured, skipping")
            return False

        text = message
        if self.mention:
            text = f"{self.mention}\n{text}"

        try:
            resp = requests.post(
                self.webhook_url,
                json={"text": text},
                timeout=10,
            )
            if resp.status_code == 200:
                log.debug("Slack notification sent")
                return True
            else:
                log.error("Slack webhook returned %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            log.error("Failed to send Slack notification: %s", e)
            return False
