"""Ana bot: veri -> rejim -> pozisyon kararları -> haber -> adaylar -> bildirim -> state/journal.

Çalıştırma:  python bot.py [--mode open|intraday|close] [--dry-run] [--force]
Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY, RUN_MODE, DRY_RUN
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Optional

import config
import data
import indicators
import journal as jr
import llm
import messages
import news
import notify
import pipeline
import risk
import store
import utils

log = logging.getLogger("bot")

MODES = ("open", "intraday", "close")


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def resolve_mode(now: datetime, override: Optional[str] = None) -> str:
    if override in MODES:
        return override
    hm = (now.hour, now.minute)
    if hm >= config.MOD_CLOSE_BASLANGIC:
        return "close"
    if hm >= config.MOD_INTRADAY_BASLANGIC:
        return "intraday"
    return "open"


def expected_last_bar(today: date, mode: str) -> date:
    if not utils.is_trading_day(today):
        return utils.previous_trading_day(today)
    return utils.previous_trading_day(today) if mode == "open" else today


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


@dataclass
class Deps:
    provider: Any = None
    notifier: Any = None
    llm_client: Any = "auto"
    rss_fetch: Callable = news.fetch_rss
    gdelt_fetch: Callable = news.fetch_gdelt
    sleep: Callable = time.sleep


def add_lot_suggestion(price: float, stop: float, adet: int, settings: dict, remaining_risk: float) -> int:
    """EKLEME için yarım risk lotu; pozisyon oranı sınırı mevcut tutar düşülerek uygulanır."""
    sermaye = float(settings["sermaye"] or 0)
    if sermaye <= 0:
        return 0
    sized = risk.position_size(sermaye, float(settings["risk_yuzde"]), 0.5, price, stop,
                               float(settings["max_pozisyon_orani"]))
    room = sermaye * float(settings["max_pozisyon_orani"]) - adet * price
    max_lot = int(math.floor(max(room, 0) / price))
    sized["lot"] = min(sized["lot"], max_lot)
    sized["risk_tl"] = round(sized["lot"] * sized["risk_per_share"], 2)
    sized["notional"] = round(sized["lot"] * price, 2)
    fitted, _ = risk.fit_to_budget(sized, remaining_risk)
    return fitted["lot"] if fitted else 0


# ---------------------------------------------------------------------------
# Pozisyon değerlendirme
# ---------------------------------------------------------------------------
def evaluate_positions(portfolio: dict, snaps: dict, prices: dict, state: dict, regime: dict,
                       mode: str, today: date) -> list[dict]:
    settings = portfolio["settings"]
    prev_all = state.get("pozisyon_durum", {})
    rows: list[dict] = []
    for pos in portfolio["positions"]:
        sym = pos["symbol"]
        snap = snaps.get(sym)
        if snap is None:
            rows.append({"symbol": sym, "adet": pos["adet"], "maliyet": pos["maliyet"], "action": "VERI_YOK",
                         "price": None, "stop": None, "target": None, "pnl_pct": None, "rsi": None})
            continue
        since = utils.parse_date(pos.get("giris_tarihi")) or today
        hc = indicators.highest_close_since(prices[sym], since)
        prev = prev_all.get(sym, {})
        lv = risk.position_levels(pos["maliyet"], snap["atr"], hc, prev.get("stop"), prev.get("cost"))
        price = snap["close"]
        rows.append({
            "symbol": sym, "adet": pos["adet"], "maliyet": pos["maliyet"], "midas_stop": pos.get("midas_stop"),
            "price": price, "stop": lv["stop"], "target": lv["target"], "R": lv["R"],
            "pnl_pct": (price / pos["maliyet"] - 1.0) * 100.0,
            "rsi": snap["rsi"], "trend": snap["trend"], "sma20": snap["sma20"],
            "sektor": risk.sector_of(sym),
        })
    # risk bütçesi
    ok_rows = [r for r in rows if r.get("price")]
    sermaye = float(settings["sermaye"] or 0)
    limit_tl = sermaye * float(settings["toplam_risk_limiti_yuzde"]) / 100.0
    remaining = limit_tl - risk.open_risk_tl([{"price": r["price"], "stop": r["stop"], "adet": r["adet"]} for r in ok_rows])
    budget_ok = remaining > 0 if sermaye > 0 else True
    for r in ok_rows:
        prev = prev_all.get(r["symbol"], {})
        last_add = utils.parse_date(prev.get("last_add_signal"))
        r["action"] = risk.decide_position_action(
            price=r["price"], stop=r["stop"], target=r["target"], rsi=r["rsi"], pnl_pct=r["pnl_pct"],
            trend=r["trend"], sma20=r["sma20"], mode=mode, regime_label=regime.get("label", "NOTR"),
            last_add_signal=last_add, today=today, budget_ok=budget_ok)
        if r["action"] == "EKLEME_YAP":
            r["ekleme_lot"] = add_lot_suggestion(r["price"], r["stop"], r["adet"], settings, remaining)
        ms = r.get("midas_stop")
        if not ms:
            r["midas_note"] = "Midas'ta stop emri tanımlı değil"
        elif r["stop"] >= ms * (1 + config.MIDAS_STOP_FARK_ESIGI / 100.0):
            r["midas_note"] = f"Midas stopunu {utils.fmt_tl(r['stop'])} seviyesine yükselt (şu an {utils.fmt_tl(ms)})"
    return rows


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------
def run(root: str = ".", mode_override: Optional[str] = None, dry_run: bool = False, force: bool = False,
        deps: Optional[Deps] = None, now: Optional[datetime] = None) -> dict:
    deps = deps or Deps()
    now = now or utils.now_ist()
    today = now.date()
    mode = resolve_mode(now, mode_override)
    notifier = deps.notifier or notify.Notifier(dry_run=dry_run)
    st = store.LocalStore(root)

    portfolio = store.normalize_portfolio(st.read("portfolio.json", None)[0])
    state = st.read("state.json", {})[0] or {}
    journal = st.read("journal.json", {"kayitlar": []})[0] or {"kayitlar": []}
    notified = state.setdefault("bildirim", {})
    warnings: list[str] = []

    def persist(extra: Optional[dict] = None) -> None:
        if extra:
            state.update(extra)
        if dry_run:
            return
        st.write("state.json", state, None)
        st.write("journal.json", journal, None)

    if not force and not utils.is_trading_day(today):
        state["son_calisma"] = {"zaman": utils.iso(now), "mod": mode, "durum": "ATLANDI_TATIL"}
        persist()
        return state

    try:
        return _run_inner(root, mode, now, today, dry_run, deps, notifier, portfolio, state, journal,
                          notified, warnings, persist)
    except Exception as e:  # noqa: BLE001
        log.error("Bot hatası: %s\n%s", e, traceback.format_exc())
        state["son_calisma"] = {"zaman": utils.iso(now), "mod": mode, "durum": "HATA", "hata": str(e)[:300]}
        saglik = state.setdefault("saglik", {})
        saglik["ardisik_hata"] = int(saglik.get("ardisik_hata", 0)) + 1
        if notified.get("hata_gun") != today.isoformat():
            notifier.send(messages.error_alert(f"{type(e).__name__}: {e}"))
            notified["hata_gun"] = today.isoformat()
        persist()
        state["_hata"] = True
        return state


def _run_inner(root, mode, now, today, dry_run, deps, notifier, portfolio, state, journal,
               notified, warnings, persist) -> dict:
    todays = today.isoformat()
    settings = portfolio["settings"]
    pos_syms = [p["symbol"] for p in portfolio["positions"]]

    # ---- 1) Veri
    need = set(pos_syms)
    if mode == "close":
        need |= set(config.BIST30)
        need |= {e["symbol"] for e in journal.get("kayitlar", []) if e["sonuc"].get("durum") != "TAMAM"}
    yahoo = [config.yahoo_kodu(s) for s in sorted(need)] + config.ENDEKS_KODLARI + [config.KUR_KODU]
    provider = deps.provider or data.YFinanceProvider()
    raw = provider.history(yahoo)
    prices = {s: raw[config.yahoo_kodu(s)] for s in need if config.yahoo_kodu(s) in raw}
    snaps = {s: indicators.snapshot(df) for s, df in prices.items()}
    snaps = {s: v for s, v in snaps.items() if v is not None}
    missing = [s for s in pos_syms if s not in snaps]
    if missing:
        warnings.append("Veri/gösterge yok: " + ", ".join(missing))

    idx_code, idx_df = data.first_available_index(raw)
    regime = indicators.build_regime(idx_df, raw.get(config.KUR_KODU), idx_code or config.ENDEKS_KODLARI[0])
    if not regime["available"]:
        warnings.append("Endeks verisi yok; rejim NÖTR kabul edildi.")

    if snaps:
        last_bar = max(s["date"] for s in snaps.values())
    elif idx_df is not None and len(idx_df):   # pozisyon yokken yalnızca endeks verisi vardır
        last_bar = idx_df.index[-1].date()
    else:
        last_bar = None
    expected = expected_last_bar(today, mode)
    stale = last_bar is None or last_bar < expected
    if stale:
        warnings.append(f"Veri güncel değil (son bar {last_bar}, beklenen {expected}).")
        if notified.get("bayat_gun") != todays and (pos_syms or mode == "close"):
            notifier.send(messages.stale_alert(last_bar, expected))
            notified["bayat_gun"] = todays

    # ---- 2) Haber
    news_raw = pipeline.reuse_previous(state.get("haber", {}), state.get("haber_zamani"), now)
    general_state = state.get("haber_genel", {})
    gemini_state = state.get("gemini", {})
    client = deps.llm_client
    if client == "auto":
        client = llm.make_client(os.environ.get("GEMINI_API_KEY"))
    if mode in ("open", "close"):
        news_syms = sorted(set(pos_syms) | (set(config.BIST30) if mode == "close" else set()))
        ground, gd = [], []
        if mode == "close":
            pre = risk.select_candidates(
                snapshots={s: snaps[s] for s in config.BIST30 if s in snaps}, held=set(pos_syms),
                open_positions=[], settings=settings, regime=regime, today=today, news={},
                manual_items=portfolio["manuel_haber"], cooldowns=state.get("cooldowns", {}))
            cand_syms = [c["symbol"] for c in pre["accepted"] + pre["watchlist"]]
            ground = list(dict.fromkeys(pos_syms + cand_syms))
            gd = list(dict.fromkeys(pos_syms + cand_syms))
        out = pipeline.refresh(symbols=news_syms, deep_symbols=set(pos_syms), now=now, mode=mode,
                               ground_symbols=ground, gdelt_symbols=gd, gemini_state=gemini_state,
                               client=client, rss_fetch=deps.rss_fetch, gdelt_fetch=deps.gdelt_fetch,
                               sleep=deps.sleep)
        news_raw, general_state, gemini_state = out["news"], out["general"], out["gemini"]
        warnings += out["warnings"]
        state["haber_zamani"] = utils.iso(now)
        state["llm_ok"] = out["llm_ok"]
    news_state = pipeline.apply_manual(news_raw, portfolio["manuel_haber"], pos_syms, now)
    flags = {s: pipeline.flags_for(v) for s, v in news_state.items() if s != "GENEL"}

    # ---- 3) Pozisyonlar
    rows = evaluate_positions(portfolio, snaps, prices, state, regime, mode, today)
    pdurum = state.setdefault("pozisyon_durum", {})
    for r in rows:
        if not r.get("price"):
            continue
        prev = pdurum.get(r["symbol"], {})
        act = r["action"]
        if act == "STOP_OL":
            notifier.send(messages.stop_alert(r))
        elif act == "KAR_AL" and prev.get("son_bildirim") != "KAR_AL":
            notifier.send(messages.take_profit_alert(r))
        elif act == "EKLEME_YAP" and prev.get("son_bildirim") != "EKLEME_YAP":
            notifier.send(messages.add_alert(r))
            prev["last_add_signal"] = todays
        prev["son_bildirim"] = act if act in ("STOP_OL", "KAR_AL", "EKLEME_YAP") else "TUT"
        prev["stop"] = round(r["stop"], 4)
        prev["cost"] = r["maliyet"]
        pdurum[r["symbol"]] = prev
    for s in list(pdurum):
        if s not in pos_syms:
            del pdurum[s]

    # Haber riski (pozisyonlar için, olay başına bir kez)
    seen = notified.setdefault("haber", [])
    for r in rows:
        ns = news_state.get(r["symbol"])
        if not ns:
            continue
        for ev in ns.get("events", []):
            if ev.get("direction") != "OLUMSUZ" or not news.has_credible_source(ev.get("evidence", [])):
                continue
            if ev["severity"] < 3:
                continue
            key = news.event_key(r["symbol"], ev)
            if key in seen:
                continue
            notifier.send(messages.news_risk_alert(r["symbol"], ev))
            seen.append(key)
        if ns.get("llm_risk_flag") and not ns.get("events"):
            key = f"{r['symbol']}|LLM_RISK|{todays}"
            if key not in seen:
                notifier.send(messages.llm_risk_alert(r["symbol"], ns.get("summary", "")))
                seen.append(key)
    notified["haber"] = seen[-200:]

    # ---- 4) Adaylar ve günlük (yalnızca close)
    accepted: list[dict] = []
    watch: list[dict] = []
    if mode == "close" and not stale:
        open_pos = [{"price": r["price"], "stop": r["stop"], "adet": r["adet"]} for r in rows if r.get("price")]
        cand = risk.select_candidates(
            snapshots={s: snaps[s] for s in config.BIST30 if s in snaps}, held=set(pos_syms),
            open_positions=open_pos, settings=settings, regime=regime, today=today,
            news={s: v for s, v in flags.items()}, manual_items=portfolio["manuel_haber"],
            cooldowns=state.get("cooldowns", {}))
        accepted, watch = cand["accepted"], cand["watchlist"]
        entries = []
        for c in accepted + [w for w in watch if w.get("veto_kind") in ("HABER", "REJIM", "TAKVIM")]:
            ns = news_state.get(c["symbol"])
            snap_day = snaps[c["symbol"]]["date"]
            entries.append(jr.make_entry(
                snap_day, c["symbol"], c["type"], c["close"], c["stop_loss"], c["target_price"],
                c.get("net_rr"), regime["label"], news.top_event_for_journal(ns),
                veto=c.get("veto_kind"), score=c.get("score")))
        jr.add_entries(journal, entries)
        jr.update_outcomes(journal, {s: prices[s] for s in prices if s in config.BIST30})
        if accepted and notified.get("aday_gun") != todays:
            for c in accepted:
                top = (news_state.get(c["symbol"]) or {}).get("events", [])
                info = max(top, key=lambda e: e["severity"]) if top else None
                notifier.send(messages.candidate_alert(c, info))
                state.setdefault("cooldowns", {})[c["symbol"]] = todays
            notified["aday_gun"] = todays
    state["cooldowns"] = {s: d for s, d in state.get("cooldowns", {}).items()
                          if utils.trading_days_between(utils.parse_date(d) or today, today) <= 15}

    # ---- 5) Özet ve sağlık
    macro = risk.macro_notes(today)
    sc = jr.scorecard(journal)
    if mode == "close" and notified.get("ozet_gun") != todays:
        notifier.send(messages.evening_summary(
            today, regime, [r for r in rows if r.get("price")], accepted, watch, macro,
            jr.format_scorecard_tr(sc) if sc["tamamlanan"] else "", warnings,
            (general_state or {}).get("summary") or None, scanned=not stale))
        notified["ozet_gun"] = todays
    if mode == "open" and today.weekday() == 0 and notified.get("pazartesi_gun") != todays:
        notifier.send(messages.health_line(last_bar, bool(state.get("llm_ok"))))
        notified["pazartesi_gun"] = todays

    # ---- 6) Kaydet
    persist({
        "guncelleme": utils.iso(now),
        "son_calisma": {"zaman": utils.iso(now), "mod": mode, "durum": "TAMAM", "kuru": dry_run,
                        "uyarilar": warnings, "son_bar": last_bar.isoformat() if last_bar else None},
        "rejim": regime,
        "pozisyonlar": [_jsonable(r) for r in rows],
        "adaylar": accepted,
        "izleme": watch[:8],
        "haber": {s: v for s, v in news_state.items() if s != "GENEL"},
        "haber_genel": general_state,
        "gemini": gemini_state,
        "makro": macro,
        "skor_karti": sc,
        "saglik": {"ardisik_hata": 0, "veri_eksik": missing, "veri_bayat": bool(stale),
                   "haber_ok": not any("Haber kaynağına" in w for w in warnings),
                   "yapay_zeka_ok": bool(state.get("llm_ok")) if mode in ("open", "close") else state.get("llm_ok"),
                   "son_basarili": utils.iso(now)},
    })
    return state


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = None if math.isnan(v) else round(v, 4)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default=os.environ.get("RUN_MODE") or None)
    ap.add_argument("--dry-run", action="store_true",
                    default=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"))
    ap.add_argument("--force", action="store_true",
                    default=os.environ.get("FORCE", "").lower() in ("1", "true", "yes"))
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = run(root=a.root, mode_override=a.mode or None, dry_run=a.dry_run, force=a.force)
    return 1 if state.get("_hata") else 0


if __name__ == "__main__":
    sys.exit(main())
