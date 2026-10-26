"""Haber akışı: toplama -> kümeleme -> (Gemini | kural yedeği) -> sembol bazlı durum.

Bot bu modülü yalnızca `open` ve `close` modlarında çağırır; `intraday` modunda
önceki haber durumu yeniden kullanılır (yalnızca manuel notlar tazelenir).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

import config
import llm
import news
import utils

log = logging.getLogger("pipeline")

ESKI_OLAY_SAAT = 72
HABER_GECERLILIK_SAAT = 30


def _fresh_events(events: list[dict], now: datetime) -> list[dict]:
    out = []
    for e in events or []:
        fs = utils.parse_iso(e.get("first_seen"))
        if fs and now - fs > timedelta(hours=ESKI_OLAY_SAAT):
            continue
        out.append(e)
    return out


def reuse_previous(state_news: dict, news_updated: Optional[str], now: datetime) -> dict:
    """Önceki koşunun haber durumu (taze ise). Sadece manuel olmayan olaylar taşınır."""
    t = utils.parse_iso(news_updated)
    if not t or now - t > timedelta(hours=HABER_GECERLILIK_SAAT):
        return {}
    kept = {}
    for sym, st in (state_news or {}).items():
        ev = [e for e in _fresh_events(st.get("events", []), now) if e.get("origin") != "manuel"]
        kept[sym] = {**st, "events": ev}
