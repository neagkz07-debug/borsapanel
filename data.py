"""Fiyat verisi sağlayıcıları. Bot yalnızca PriceProvider arayüzüne bağımlıdır."""
from __future__ import annotations

import logging
import time
from typing import Iterable, Optional

import pandas as pd

import config

log = logging.getLogger("data")

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


class PriceProvider:
    def history(self, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
        """Yahoo kodu (THYAO.IS, XU030.IS, USDTRY=X) -> günlük OHLCV. Alınamayan anahtar yoktur."""
        raise NotImplementedError


def _tidy(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    keep = [c for c in FIELDS if c in d.columns]
    if "Close" not in keep:
        return None
    d = d[keep].dropna(subset=["Close"])
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d if len(d) else None


def split_download(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download(group_by='ticker') çıktısını sembol sözlüğüne ayır (saf fonksiyon, test edilebilir)."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or len(raw) == 0:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        top = set(raw.columns.get_level_values(0))
        for s in symbols:
            if s in top:
                d = _tidy(raw[s])
                if d is not None:
                    out[s] = d
    elif len(symbols) == 1:
        d = _tidy(raw)
        if d is not None:
            out[symbols[0]] = d
    return out


class YFinanceProvider(PriceProvider):
    def __init__(self, period: str = config.VERI_PERIYODU, retries: int = 2, sleep=time.sleep):
        self.period = period
        self.retries = retries
        self.sleep = sleep

    def _bulk(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        import yfinance as yf
        for attempt in range(self.retries + 1):
            try:
                raw = yf.download(symbols, period=self.period, interval="1d", group_by="ticker",
                                  auto_adjust=False, progress=False, threads=True)
                got = split_download(raw, symbols)
                if got:
                    return got
            except Exception as e:  # noqa: BLE001
                log.warning("yfinance toplu indirme hatası (deneme %d): %s", attempt + 1, e)
            self.sleep(2 * (attempt + 1))
        return {}

    def _single(self, symbol: str) -> Optional[pd.DataFrame]:
        import yfinance as yf
        try:
            return _tidy(yf.Ticker(symbol).history(period=self.period, interval="1d", auto_adjust=False))
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance tekil hata (%s): %s", symbol, e)
            return None

    def history(self, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
        symbols = list(dict.fromkeys(symbols))
        out = self._bulk(symbols)
        for s in symbols:
            if s not in out:
                d = self._single(s)
                if d is not None:
                    out[s] = d
        missing = [s for s in symbols if s not in out]
        if missing:
            log.warning("Veri alınamayan semboller: %s", ", ".join(missing))
        return out


def first_available_index(prices: dict[str, pd.DataFrame]) -> tuple[Optional[str], Optional[pd.DataFrame]]:
    for code in config.ENDEKS_KODLARI:
        if code in prices:
            return code, prices[code]
    return None, None
