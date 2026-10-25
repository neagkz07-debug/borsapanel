"""Ortak yardımcılar: saat, sayı biçimi, JSON okuma/yazma, Türkçe metin."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TZ_NAME)


def now_ist() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Sayı biçimleri (Türkçe)
# --------------------------------------------------------------------------
def fmt_tl(x: Optional[float], digits: int = 2) -> str:
    """1234.5 -> '1.234,50'"""
    if x is None:
        return "-"
    s = f"{x:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(x: Optional[float], digits: int = 1) -> str:
    """3.66 -> '%3,7' ; -3.66 -> '-%3,7'"""
    if x is None:
        return "-"
    s = f"{abs(x):.{digits}f}".replace(".", ",")
    return f"-%{s}" if x < 0 else f"%{s}"


# --------------------------------------------------------------------------
# Türkçe metin
# --------------------------------------------------------------------------
def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    return _SPACES.sub(" ", _PUNCT.sub(" ", tr_lower(s))).strip()


# --------------------------------------------------------------------------
# JSON dosyaları (atomik yazım)
# --------------------------------------------------------------------------
def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path: str, data: Any) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --------------------------------------------------------------------------
# İşlem günü yardımcıları
# --------------------------------------------------------------------------
def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in config.TATIL_GUNLERI


def previous_trading_day(d: date) -> date:
    x = d - timedelta(days=1)
    while not is_trading_day(x):
        x -= timedelta(days=1)
    return x


def next_trading_day(d: date) -> date:
    x = d + timedelta(days=1)
    while not is_trading_day(x):
        x += timedelta(days=1)
    return x


def trading_days_between(a: date, b: date) -> int:
    """a (dahil değil) ile b (dahil) arasındaki işlem günü sayısı; a>=b ise 0."""
    if a >= b:
        return 0
    n = 0
    x = a
    while x < b:
        x = next_trading_day(x)
        if x <= b:
            n += 1
    return n


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def fmt_date_tr(d: date) -> str:
    """date(2026,11,12) -> '12 Kasım'"""
    return f"{d.day} {_AYLAR[d.month - 1]}"
