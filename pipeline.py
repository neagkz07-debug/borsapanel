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
    return kept


def apply_manual(base: dict, manual_items: list[dict], symbols: list[str], now: datetime) -> dict:
    """Her çalışmada manuel (KAP) notları taze olaylarla birleştirip haber durumunu üretir."""
    result: dict = {}
    all_syms = set(base) | set(symbols) | {m.get("symbol") for m in manual_items or [] if m.get("symbol")}
    for sym in sorted(s for s in all_syms if s and s != "GENEL"):
        prev = base.get(sym, {})
        events = _fresh_events([e for e in prev.get("events", []) if e.get("origin") != "manuel"], now)
        events = news.manual_events(sym, manual_items, now) + events
        if not events and not prev.get("gdelt") and not prev.get("llm_risk_flag"):
            continue
        result[sym] = news.build_news_state(
            events, prev.get("gdelt"), llm_risk_flag=prev.get("llm_risk_flag", False),
            summary=prev.get("summary", ""), confidence=prev.get("confidence"))
    if "GENEL" in base:
        result["GENEL"] = base["GENEL"]
    return result


def flags_for(news_state: Optional[dict]) -> dict:
    """Karar bayrakları. Gemini'nin işaretlediği ama kanıtı olmayan risk yalnızca -1 puan (neg3) yapar."""
    f = news.news_flags(news_state)
    if (news_state or {}).get("llm_risk_flag") and not f["veto"]:
        f["neg3"] = True
    return f


def refresh(*, symbols: list[str], deep_symbols: set[str], now: datetime, mode: str,
            ground_symbols: list[str], gdelt_symbols: list[str], gemini_state: dict,
            client, rss_fetch: Callable = news.fetch_rss, gdelt_fetch: Callable = news.fetch_gdelt,
            sleep: Callable = lambda s: None) -> dict:
    """Haberleri topla ve sınıflandır.

    Dönüş: {'news': {sym: ham_durum}, 'general': {...}, 'gemini': yeni_kota_durumu,
            'warnings': [...], 'llm_ok': bool}
    """
    warnings: list[str] = []
    clusters_by_symbol: dict[str, list[dict]] = {}
    for sym in symbols:
        items = news.collect_rss_for_symbol(sym, sym in deep_symbols, now, fetch=rss_fetch, sleep=sleep)
        clusters_by_symbol[sym] = news.cluster_items(items)
    general_items = news.collect_general(now, fetch=rss_fetch, sleep=sleep)
    general = news.cluster_items(general_items)
    if not any(clusters_by_symbol.values()) and not general:
        warnings.append("Haber kaynağına ulaşılamadı (RSS boş).")

    # --- Gemini arama doğrulaması (kota sınırlı, yalnızca izinli modlarda)
    today = now.date()
    gem = dict(gemini_state or {})
    if client is not None and config.GEMINI_ARAMA_DOGRULAMA and mode in config.GROUNDING_MODLARI:
        for sym in ground_symbols:
            if llm.grounding_budget_left(gem, today) <= 0:
                break
            res = llm.grounded_search(client, sym)
            gem = llm.bump_grounding(gem, today)
            if res is None:
                # Ücretsiz katmanda arama doğrulaması kapalı olabilir; bugün bir daha deneme.
                warnings.append("Arama doğrulaması kullanılamadı (ücretsiz katmanda kapalı olabilir); atlandı.")
                break
            if res.get("cluster"):
                clusters_by_symbol.setdefault(sym, []).append(res["cluster"])

    # --- Sınıflandırma
    llm_result = llm.classify(client, clusters_by_symbol, general) if client is not None else None
    llm_ok = llm_result is not None or (client is not None and not any(clusters_by_symbol.values()))
    if client is not None and not llm_ok and any(clusters_by_symbol.values()):
        warnings.append("Yapay zekâ sınıflandırması yapılamadı (kota/hata); kural yedeği kullanıldı.")

    raw: dict[str, dict] = {}
    for sym, clusters in clusters_by_symbol.items():
        if llm_result is not None:
            events = list(llm_result["events"].get(sym, []))
            risk = llm_result["risk_flags"].get(sym, "")
        else:
            events = news.keyword_risk_events(clusters)
            risk = ""
        if events or risk:
            raw[sym] = {"events": events, "llm_risk_flag": bool(risk),
                        "summary": risk or (events[0].get("summary", "") if events else ""),
                        "confidence": None, "gdelt": None}

    # --- GDELT (bilgi amaçlı; karar bayrağını değiştirmez)
    for sym in gdelt_symbols[:config.GDELT_MAX_HISSE]:
        g = gdelt_fetch(config.SIRKET_ADI.get(sym, sym), sleep=sleep)
        if g:
            raw.setdefault(sym, {"events": [], "llm_risk_flag": False, "summary": "",
                                 "confidence": None, "gdelt": None})["gdelt"] = g

    gen_events = (llm_result["events"].get("GENEL", []) if llm_result is not None else [])
    general_state = {"events": gen_events, "summary": gen_events[0]["summary"] if gen_events else ""}
    return {"news": raw, "general": general_state, "gemini": gem, "warnings": warnings, "llm_ok": llm_ok}
