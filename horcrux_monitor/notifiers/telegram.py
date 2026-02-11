import logging
import requests

from .base import BaseNotifier

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier(BaseNotifier):
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, message: str) -> bool:
        if not self.bot_token or not self.chat_id:
            log.warning("Telegram bot token or chat ID not configured, skipping")
            return False

        try:
            resp = requests.post(
                API_URL.format(token=self.bot_token),
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                log.debug("Telegram notification sent")
                return True
            else:
                log.error("Telegram API error: %s", data.get("description", resp.text))
                return False
        except Exception as e:
            log.error("Failed to send Telegram notification: %s", e)
            return False
