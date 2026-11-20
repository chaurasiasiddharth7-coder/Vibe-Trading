"""Telegram alerts (max 20 a minute). Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID they only go to the log."""

from __future__ import annotations

import json
import logging
import os
import time as _time
import urllib.request
from collections import deque
from typing import Callable, Iterable

from src.live.profit_guard.models import Alert

logger = logging.getLogger(__name__)
ICON = {"info": "ℹ️", "warn": "⚠️", "critical": "🛑"}


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - fixed Telegram host
        r.read()


class AlertSender:
    def __init__(self, token: str | None = None, chat_id: str | None = None, *, per_minute: int = 20,
                 post: Callable[[str, dict], None] = _post, clock: Callable[[], float] = _time.monotonic) -> None:
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.per_minute, self.post, self.clock = per_minute, post, clock
        self.sent: deque[float] = deque()
        self.dropped = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, alerts: Iterable[Alert]) -> int:
        n = 0
        for a in alerts:
            line = f"{ICON.get(a.level, '')} Vibe Desk: {a.text}"
            logger.warning(line) if a.level != "info" else logger.info(line)
            if not self.enabled:
                continue
            now = self.clock()
            while self.sent and now - self.sent[0] >= 60:
                self.sent.popleft()
            if len(self.sent) >= self.per_minute:
                self.dropped += 1
                continue
            try:
                self.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          {"chat_id": self.chat_id, "text": line, "disable_web_page_preview": True})
                self.sent.append(now)
                n += 1
            except Exception as exc:  # noqa: BLE001 - an alert failure must never stop the guard
                logger.error("telegram send failed: %s", exc)
        return n
