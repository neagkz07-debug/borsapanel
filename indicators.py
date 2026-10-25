"""Teknik göstergeler ve piyasa rejimi (saf fonksiyonlar, ağ yok)."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

import config


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # kayıp hiç yoksa RSI 100
    out = out.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder ATR."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def classify_trend(close: float, sma20: float, sma50: float) -> str:
    if close > sma20 > sma50:
        return "YUKSELIS"
    if close < sma20 < sma50:
        return "DUSUS"
    return "YATAY"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    need = ["Open", "High", "Low", "Close", "Volume"]
    d = df.copy()
    for c in need:
        if c not in d.columns:
            d[c] = np.nan
    d = d.dropna(subset=["Close"])
    return d


def snapshot(df: Optional[pd.DataFrame], min_bars: int = config.MIN_BAR) -> Optional[dict]:
    """Son bara göre gösterge özeti. Yeterli bar yoksa None."""
    if df is None or len(df) == 0:
        return None
    d = _clean(df)
    if len(d) < min_bars:
        return None
    close = d["Close"].astype(float)
    high = d["High"].astype(float)
    low = d["Low"].astype(float)
    vol = d["Volume"].astype(float)

    s20 = sma(close, config.SMA_KISA)
    s50 = sma(close, config.SMA_UZUN)
    r = rsi(close, config.RSI_PERIYOT)
    a = atr(high, low, close, config.ATR_PERIYOT)

    last = float(close.iloc[-1])
    if np.isnan(s50.iloc[-1]) or np.isnan(r.iloc[-1]) or np.isnan(a.iloc[-1]):
        return None

    prior_high20 = float(close.iloc[-21:-1].max()) if len(close) >= 21 else float("nan")
    prior_vol = vol.iloc[-21:-1].mean() if len(vol) >= 21 else float("nan")
    vol_ratio = float(vol.iloc[-1] / prior_vol) if prior_vol and prior_vol > 0 else float("nan")

    def ret(n: int) -> float:
        if len(close) <= n:
            return float("nan")
        return float((close.iloc[-1] / close.iloc[-1 - n] - 1.0) * 100.0)

    idx_last = d.index[-1]
    last_date = idx_last.date() if hasattr(idx_last, "date") else idx_last

    return {
        "date": last_date,
        "close": last,
        "sma20": float(s20.iloc[-1]),
        "sma50": float(s50.iloc[-1]),
        "rsi": float(r.iloc[-1]),
        "atr": float(a.iloc[-1]),
        "prior_high20": prior_high20,
        "breakout": bool(last > prior_high20) if not np.isnan(prior_high20) else False,
        "vol_ratio": vol_ratio,
        "ret2": ret(2),
        "ret5": ret(5),
        "trend": classify_trend(last, float(s20.iloc[-1]), float(s50.iloc[-1])),
        "bars": int(len(d)),
    }


def highest_close_since(df: pd.DataFrame, since) -> Optional[float]:
    """`since` (date) dahil olmak üzere sonraki en yüksek kapanış."""
    if df is None or len(df) == 0:
        return None
    d = _clean(df)
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    mask = idx >= pd.Timestamp(since)
    sub = d.loc[mask, "Close"]
    if len(sub) == 0:
        return None
    return float(sub.max())


# ---------------------------------------------------------------------------
# Piyasa rejimi
# ---------------------------------------------------------------------------
def classify_regime(index_snapshot: Optional[dict]) -> str:
    if not index_snapshot:
        return "NOTR"
    c, s20, s50 = index_snapshot["close"], index_snapshot["sma20"], index_snapshot["sma50"]
    if c > s50 and s20 > s50:
        return "RISK_ON"
    if c < s50 and s20 < s50:
        return "RISK_OFF"
    return "NOTR"


def pct_change_1d(df: Optional[pd.DataFrame]) -> Optional[float]:
    if df is None:
        return None
    d = _clean(df)
    if len(d) < 2:
        return None
    c = d["Close"].astype(float)
    return float((c.iloc[-1] / c.iloc[-2] - 1.0) * 100.0)


def build_regime(index_df: Optional[pd.DataFrame], usdtry_df: Optional[pd.DataFrame],
                 index_symbol: str = "XU030.IS") -> dict:
    snap = snapshot(index_df, min_bars=config.SMA_UZUN + 1) if index_df is not None else None
    label = classify_regime(snap)
    usd = pct_change_1d(usdtry_df)
    return {
        "label": label,
        "index": index_symbol,
        "close": round(snap["close"], 2) if snap else None,
        "sma20": round(snap["sma20"], 2) if snap else None,
        "sma50": round(snap["sma50"], 2) if snap else None,
        "usdtry_1d_pct": round(usd, 2) if usd is not None else None,
        "fx_shock": bool(usd is not None and usd >= config.USDTRY_SOK_ESIGI),
        "available": snap is not None,
    }
