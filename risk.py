"""Risk motoru: stop/hedef, lot, risk bütçesi, sektör limiti, takvim, aday seçimi.

Tüm fonksiyonlar saf (girdi -> çıktı); ağ ve dosya erişimi yok.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import config
import utils


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# Stop / hedef
# ---------------------------------------------------------------------------
def compute_r(ref_price: float, atr_value: float) -> float:
    """R = 2 x ATR14; referans fiyatın %4'ü ile %10'u arasında sınırlanır."""
    r = config.R_ATR_CARPANI * atr_value
    lo = config.R_MIN_ORAN * ref_price
    hi = config.R_MAX_ORAN * ref_price
    return min(max(r, lo), hi)


def position_levels(cost: float, atr_value: float, highest_close: Optional[float],
                    prev_stop: Optional[float] = None, prev_cost: Optional[float] = None) -> dict:
    """Pozisyon için R, trailing stop ve hedef.

    Stop asla aşağı inmez: aynı maliyetli önceki stop biliniyorsa ondan düşük olamaz.
    """
    r = compute_r(cost, atr_value)
    initial = cost - r
    trail = (highest_close - r) if highest_close else initial
    stop = max(initial, trail)
    if prev_stop and prev_cost is not None and abs(prev_cost - cost) < 1e-9:
        stop = max(stop, prev_stop)
    target = cost + config.HEDEF_R_KATI * r
    return {"R": r, "stop": stop, "target": target}


def decide_position_action(*, price: float, stop: float, target: float, rsi: float,
                           pnl_pct: float, trend: str, sma20: float, mode: str,
                           regime_label: str, last_add_signal: Optional[date],
                           today: date, budget_ok: bool = True) -> str:
    """Kural tabanlı pozisyon aksiyonu (STOP_OL > KAR_AL > EKLEME_YAP > TUT)."""
    if price <= stop:
        return "STOP_OL"
    if price >= target or (rsi >= config.KAR_AL_RSI and pnl_pct >= config.KAR_AL_MIN_KAR):
        return "KAR_AL"
    lo, hi = config.EKLEME_RSI_ARALIK
    cooled = (last_add_signal is None
              or utils.trading_days_between(last_add_signal, today) >= config.EKLEME_BEKLEME_GUN)
    if (mode == "close" and trend == "YUKSELIS" and lo <= rsi <= hi
            and price <= sma20 * (1 + config.EKLEME_SMA20_UST_LIMIT)
            and pnl_pct > 0 and cooled and regime_label == "RISK_ON" and budget_ok):
        return "EKLEME_YAP"
    return "TUT"


# ---------------------------------------------------------------------------
# Lot ve risk bütçesi
# ---------------------------------------------------------------------------
def position_size(sermaye: float, risk_yuzde: float, carpan: float, entry: float,
                  stop: float, max_pozisyon_orani: float) -> dict:
    rps = round(entry - stop, 6)
    if sermaye <= 0 or entry <= 0 or rps <= 0:
        return {"lot": 0, "risk_tl": 0.0, "target_risk_tl": 0.0, "notional": 0.0,
                "capped": False, "risk_per_share": max(rps, 0.0)}
    target_risk = sermaye * risk_yuzde / 100.0 * carpan
    lot = int(math.floor(target_risk / rps))
    capped = False
    max_notional = sermaye * max_pozisyon_orani
    if lot * entry > max_notional:
        lot = int(math.floor(max_notional / entry))
        capped = True
    return {
        "lot": lot,
        "risk_tl": round(lot * rps, 2),
        "target_risk_tl": round(target_risk, 2),
        "notional": round(lot * entry, 2),
        "capped": capped,
        "risk_per_share": rps,
    }


def open_risk_tl(positions: list[dict]) -> float:
    """positions: [{'price','stop','adet'}] -> stopa inilirse kaybedilecek toplam TL."""
    total = 0.0
    for p in positions:
        total += max(0.0, p["price"] - p["stop"]) * p["adet"]
    return total


def fit_to_budget(sized: dict, remaining_risk_tl: float) -> tuple[Optional[dict], Optional[str]]:
    """Lotu kalan risk bütçesine sığdır; tam lotun %50'sinden azsa eler."""
    full = sized["lot"]
    rps = sized["risk_per_share"]
    if full <= 0 or rps <= 0:
        return None, "LOT_SIFIR"
    if full * rps <= remaining_risk_tl + 1e-9:
        return sized, None
    new_lot = int(math.floor(max(remaining_risk_tl, 0.0) / rps))
    if new_lot <= 0 or new_lot < 0.5 * full:
        return None, "RISK_BUTCESI_YETERSIZ"
    out = dict(sized)
    out["lot"] = new_lot
    out["risk_tl"] = round(new_lot * rps, 2)
    out["notional"] = round(new_lot * (sized["notional"] / full), 2)
    out["budget_reduced"] = True
    return out, None


def net_rr(entry: float, target: float, stop: float,
           kayma_yuzde: float = config.KAYMA_YUZDE,
           komisyon_yuzde: float = config.KOMISYON_YUZDE) -> float:
    if entry <= stop:
        return 0.0
    slip = entry * kayma_yuzde / 100.0
    fee = entry * komisyon_yuzde / 100.0
    num = target - entry - 2 * slip - fee
    den = entry - stop + 2 * slip + fee
    return num / den


# ---------------------------------------------------------------------------
# Sektör
# ---------------------------------------------------------------------------
def sector_of(symbol: str) -> str:
    return config.SEKTOR.get(symbol, f"DIGER_{symbol}")


def sector_has_room(symbol: str, occupied: list[str], limit: int) -> bool:
    sec = sector_of(symbol)
    n = sum(1 for s in occupied if sector_of(s) == sec)
    return n < limit


# ---------------------------------------------------------------------------
# Takvim
# ---------------------------------------------------------------------------
def macro_block_reason(today: date, takvim=None) -> Optional[str]:
    takvim = config.MAKRO_TAKVIM if takvim is None else takvim
    for d, tur, ad in takvim:
        if tur != "PPK_KARAR":
            continue
        if d == today:
            return f"PPK karar günü ({ad})"
        if utils.previous_trading_day(d) == today:
            return f"PPK kararı öncesi son işlem günü ({utils.fmt_date_tr(d)})"
    return None


def macro_notes(today: date, days_ahead: int = 14, takvim=None) -> list[str]:
    takvim = config.MAKRO_TAKVIM if takvim is None else takvim
    out = []
    for d, tur, ad in sorted(takvim, key=lambda x: x[0]):
        delta = (d - today).days
        if 0 <= delta <= days_ahead:
            out.append(f"{utils.fmt_date_tr(d)}: {ad}")
    return out


def macro_events_today(today: date, takvim=None) -> list[tuple[date, str, str]]:
    takvim = config.MAKRO_TAKVIM if takvim is None else takvim
    return [(d, t, a) for d, t, a in takvim if d == today]


def in_bilanco_window(today: date, pencereler=None) -> bool:
    pencereler = config.BILANCO_PENCERELERI if pencereler is None else pencereler
    return any(a <= today <= b for a, b in pencereler)


def earnings_block(symbol: str, manual_items: list[dict], today: date) -> Optional[str]:
    """manuel_haber içinde tur=BILANCO ve olay_tarihi yakınsa giriş engeli nedeni."""
    for m in manual_items or []:
        if m.get("symbol") != symbol or m.get("tur") != "BILANCO":
            continue
        d = utils.parse_date(m.get("olay_tarihi"))
        if d is None or d < today:
            continue
        gun = utils.trading_days_between(today, d)
        if gun <= config.BILANCO_ONCESI_GUN:
            return f"Bilanço {utils.fmt_date_tr(d)} (≤{config.BILANCO_ONCESI_GUN} işlem günü)"
    return None


# ---------------------------------------------------------------------------
# Aday kuralları
# ---------------------------------------------------------------------------
def trend_candidate_score(snap: dict) -> tuple[bool, int]:
    """(uygun mu, puan). Zorunlu koşul: trend YUKSELIS."""
    if snap["trend"] != "YUKSELIS":
        return False, 0
    score = 0
    lo, hi = config.TREND_RSI_ARALIK
    if lo <= snap["rsi"] <= hi:
        score += 1
    pullback = abs(snap["close"] / snap["sma20"] - 1.0) <= config.TREND_SMA20_BANT
    vr = snap.get("vol_ratio")
    breakout = bool(snap.get("breakout")) and not _isnan(vr) and vr >= config.TREND_KIRILIM_HACIM_ORANI
    if pullback or breakout:
        score += 1
    r5 = snap.get("ret5")
    if not _isnan(r5) and r5 <= config.TREND_MAX_5G_GETIRI:
        score += 1
    if not _isnan(vr) and vr >= config.TREND_MIN_HACIM_ORANI:
        score += 1
    return True, score


def catalyst_technical_ok(snap: dict) -> bool:
    vr = snap.get("vol_ratio")
    r2 = snap.get("ret2")
    return bool(
        snap["close"] > snap["sma50"]
        and snap["trend"] != "DUSUS"
        and not _isnan(r2) and r2 <= config.KATALIZOR_MAX_2G_GETIRI
        and snap["rsi"] <= config.KATALIZOR_MAX_RSI
        and not _isnan(vr) and vr >= config.KATALIZOR_MIN_HACIM_ORANI
    )


def _cooldown_active(symbol: str, cooldowns: dict, today: date) -> bool:
    d = utils.parse_date((cooldowns or {}).get(symbol))
    if d is None:
        return False
    return utils.trading_days_between(d, today) < config.ADAY_COOLDOWN_ISLEM_GUNU


def select_candidates(*, snapshots: dict, held: set, open_positions: list[dict],
                      settings: dict, regime: dict, today: date, news: dict,
                      manual_items: list[dict], cooldowns: dict) -> dict:
    """Aday ve izleme listesi üretir.

    news[symbol]: {'veto': bool, 'neg3': bool, 'catalyst': bool, 'top_event': dict|None}
    Dönüş: {'accepted': [...], 'watchlist': [...]}
    Her kayıt: symbol, type, action, close, entry_zone, stop_loss, target_price, lot,
    risk_tl, net_rr, score, sector, reason, veto_kind
    """
    st = {**config.VARSAYILAN_AYARLAR, **(settings or {})}
    label = regime.get("label", "NOTR")
    fx_shock = bool(regime.get("fx_shock"))
    macro_reason = macro_block_reason(today)
    in_window = in_bilanco_window(today)

    # Global engel nedenleri (haber/rejim/takvim kaynaklı: günlükte vetoed olarak ölçülür)
    global_block: Optional[tuple[str, str]] = None
    if label == "RISK_OFF":
        global_block = ("Piyasa rejimi RISK_OFF", "REJIM")
    elif fx_shock:
        global_block = ("Kur şoku (USDTRY)", "REJIM")
    elif macro_reason:
        global_block = (macro_reason, "TAKVIM")

    pool = []
    for sym, snap in snapshots.items():
        if sym in held or snap is None:
            continue
        if _cooldown_active(sym, cooldowns, today):
            continue
        nw = news.get(sym) or {}
        ok, score = trend_candidate_score(snap)
        if ok:
            if in_window:
                score -= 1
            if nw.get("neg3"):
                score -= 1
        if ok and score >= config.TREND_MIN_PUAN:
            pool.append({"symbol": sym, "type": "TREND", "score": score, "snap": snap})
        elif nw.get("catalyst") and catalyst_technical_ok(snap):
            pool.append({"symbol": sym, "type": "KATALIZOR", "score": 0, "snap": snap})

    def sort_key(c):
        rank = 0 if c["type"] == "TREND" else 1
        if c["type"] == "TREND":
            return (rank, -c["score"], abs(c["snap"]["rsi"] - 55.0))
        ev = (news.get(c["symbol"]) or {}).get("top_event") or {}
        return (rank, -(ev.get("severity") or 0), -(ev.get("source_count") or 0))

    pool.sort(key=sort_key)

    accepted: list[dict] = []
    watch: list[dict] = []

    def make(c, action, reason, veto_kind, sized=None, rr=None, levels=None):
        snap = c["snap"]
        close = snap["close"]
        lv = levels or {}
        return {
            "symbol": c["symbol"], "type": c["type"], "action": action,
            "close": round(close, 2), "score": c["score"], "sector": sector_of(c["symbol"]),
            "entry_zone": [round(close * 0.99, 2), round(close * 1.005, 2)],
            "stop_loss": round(lv["stop"], 2) if lv else None,
            "target_price": round(lv["target"], 2) if lv else None,
            "lot": sized["lot"] if sized else None,
            "risk_tl": sized["risk_tl"] if sized else None,
            "notional": sized.get("notional") if sized else None,
            "net_rr": round(rr, 2) if rr is not None else None,
            "reason": reason, "veto_kind": veto_kind,
        }

    max_cand = config.ADAY_MAX_NOTR if label == "NOTR" else config.ADAY_MAX
    slots = max(0, int(st["max_pozisyon"]) - len(held))
    sermaye = float(st["sermaye"] or 0)
    limit_tl = sermaye * float(st["toplam_risk_limiti_yuzde"]) / 100.0
    remaining = limit_tl - open_risk_tl(open_positions)
    occupied = list(held)

    for c in pool:
        sym = c["symbol"]
        snap = c["snap"]
        nw = news.get(sym) or {}
        close = snap["close"]
        r = compute_r(close, snap["atr"])
        lv = {"stop": close - r, "target": close + config.HEDEF_R_KATI * r}

        # 1) haber / rejim / takvim engelleri
        if global_block:
            if len(watch) < 5:
                watch.append(make(c, "BEKLE", global_block[0], global_block[1], levels=lv))
            continue
        if nw.get("veto"):
            watch.append(make(c, "BEKLE", "Güvenilir olumsuz haber", "HABER", levels=lv))
            continue
        eb = earnings_block(sym, manual_items, today)
        if eb:
            watch.append(make(c, "BEKLE", eb, "TAKVIM", levels=lv))
            continue

        # 2) kapasite
        if len(accepted) >= max_cand or len(accepted) >= slots:
            watch.append(make(c, "BEKLE", "Aday/pozisyon kapasitesi dolu", "RISK", levels=lv))
            continue
        if not sector_has_room(sym, occupied, int(st["max_sektor_pozisyon"])):
            watch.append(make(c, "BEKLE", f"Sektör limiti ({sector_of(sym)})", "RISK", levels=lv))
            continue

        # 3) net R:R
        rr = net_rr(close, lv["target"], lv["stop"])
        if rr < config.MIN_NET_RR:
            watch.append(make(c, "BEKLE", f"Net R:R düşük ({rr:.2f})", "RISK", levels=lv, rr=rr))
            continue

        # 4) lot ve risk bütçesi
        sized = None
        if sermaye > 0:
            carpan = 1.0 if c["type"] == "TREND" else float(st["katalizor_risk_carpani"])
            if label == "NOTR":
                carpan *= 0.5
            sized = position_size(sermaye, float(st["risk_yuzde"]), carpan, close,
                                  lv["stop"], float(st["max_pozisyon_orani"]))
            sized, why = fit_to_budget(sized, remaining)
            if sized is None:
                watch.append(make(c, "BEKLE", "Risk bütçesi/lot uygun değil" if why else "Lot 0",
                                  "RISK", levels=lv, rr=rr))
                continue
            remaining -= sized["risk_tl"]

        accepted.append(make(c, "AL", "", None, sized=sized, rr=rr, levels=lv))
        occupied.append(sym)

    return {"accepted": accepted, "watchlist": watch}
