"""Telegram bildirimi. Hata fırlatmaz; DRY_RUN'da yalnızca ekrana yazar."""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

import requests

log = logging.getLogger("notify")

LIMIT = 4000  # Telegram sınırı 4096


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += (line + "\n")
    if cur.strip():
        parts.append(cur.rstrip("\n"))
    return parts


class Notifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None,
                 dry_run: bool = False, http_post: Callable = requests.post):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID")
        self.dry_run = dry_run
        self.http_post = http_post
        self.sent: list[str] = []

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        self.sent.append(text)
        if self.dry_run or not self.configured:
            print("---- TELEGRAM (kuru çalışma) ----")
            print(text)
            print("---------------------------------")
            return False
        ok = True
        for part in split_message(text):
            try:
                r = self.http_post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": part, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                    timeout=15,
                )
                if r.status_code != 200:
                    log.warning("Telegram HTTP %s", r.status_code)  # token loglanmaz
                    ok = False
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram hatası: %s", type(e).__name__)
                ok = False
        return ok
