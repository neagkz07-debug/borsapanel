"""Telegram mesaj metinleri (HTML). Dinamik her metin kaçışlanır."""
from __future__ import annotations

from datetime import date
from typing import Optional

import utils
from utils import fmt_pct, fmt_tl, html_escape as esc

DISCLAIMER = "Karar destek bildirimidir; emir Midas'ta elle verilir."

REJIM_ETIKET = {"RISK_ON": "RISK_ON (olumlu)", "RISK_OFF": "RISK_OFF (alım yok)", "NOTR": "NÖTR (yarım risk)"}


def stop_alert(p: dict) -> str:
    return (f"🔴 <b>STOP — {esc(p['symbol'])}</b>\n"
            f"Fiyat {fmt_tl(p['price'])} ≤ stop {fmt_tl(p['stop'])} · K/Z {fmt_pct(p['pnl_pct'])}\n"
            f"Midas'ta pozisyonu kapat. {DISCLAIMER}")


def take_profit_alert(p: dict) -> str:
    return (f"🟡 <b>KÂR AL — {esc(p['symbol'])}</b>\n"
            f"Fiyat {fmt_tl(p['price'])} · K/Z {fmt_pct(p['pnl_pct'])} · RSI {p['rsi']:.0f}\n"
            f"Hedef {fmt_tl(p['target'])} · stop {fmt_tl(p['stop'])}\n"
            f"Kısmi/tam kâr almayı değerlendir. {DISCLAIMER}")


def add_alert(p: dict) -> str:
    lot = p.get("ekleme_lot")
    lot_txt = f"\nÖnerilen ek lot: {lot}" if lot else "\nLot önerisi için panelde sermaye girili olmalı."
    return (f"🟢 <b>EKLEME — {esc(p['symbol'])}</b>\n"
            f"Trend sağlam, geri çekilme bölgesi · RSI {p['rsi']:.0f} · K/Z {fmt_pct(p['pnl_pct'])}"
            f"{lot_txt}\nStop {fmt_tl(p['stop'])}. {DISCLAIMER}")


def news_risk_alert(symbol: str, ev: dict) -> str:
    srcs = ", ".join(esc(e["source"]) for e in ev.get("evidence", [])[:3])
    ozet = esc(ev.get("summary") or (ev["evidence"][0]["title"] if ev.get("evidence") else ""))
    return (f"⚠️ <b>HABER RİSKİ — {esc(symbol)}</b>\n"
            f"{ozet}\nÖnem {ev.get('severity')}/5 · kaynak: {srcs or '-'}\n"
            f"Pozisyonu gözden geçir; stop disiplinini koru.")


def llm_risk_alert(symbol: str, reason: str) -> str:
    return (f"⚠️ <b>HABER RİSKİ (yapay zekâ) — {esc(symbol)}</b>\n{esc(reason)}\n"
            f"Doğrulanmamış işaret: haberi kendin kontrol et.")


def candidate_alert(c: dict, news_info: Optional[dict] = None) -> str:
    tur = "TREND" if c["type"] == "TREND" else "KATALİZÖR (yarım risk)"
    lines = [f"🟢 <b>AL ADAYI — {esc(c['symbol'])}</b> · {tur}",
             f"Kapanış {fmt_tl(c['close'])} · bölge {fmt_tl(c['entry_zone'][0])}–{fmt_tl(c['entry_zone'][1])}",
             f"Stop {fmt_tl(c['stop_loss'])} · Hedef {fmt_tl(c['target_price'])} · net R:R {fmt_tl(c['net_rr'])}"]
    if c.get("lot"):
        lines.append(f"Lot {c['lot']} · risk {fmt_tl(c['risk_tl'], 0)} TL · tutar {fmt_tl(c['notional'], 0)} TL")
    else:
        lines.append("Lot için panelde sermaye gir.")
    if c["type"] == "KATALIZOR" and news_info:
        lines.append(f"Haber: {esc(str(news_info.get('summary') or news_info.get('event_type') or ''))}")
    lines.append(f"Alımdan sonra Midas'a stop emri gir ve panele ekle. {DISCLAIMER}")
    return "\n".join(lines)


def error_alert(err: str) -> str:
    return f"❗ <b>Bot hatası</b>\n{esc(err[:300])}\nAyrıntı için GitHub Actions kaydına bak."


def stale_alert(last_bar: Optional[date], expected: date) -> str:
    lb = utils.fmt_date_tr(last_bar) if last_bar else "yok"
    return (f"⚠️ <b>Veri güncel değil</b>\nSon fiyat çubuğu: {lb} (beklenen {utils.fmt_date_tr(expected)}). "
            f"Bugünkü aday taraması atlandı; stop uyarıları eldeki son fiyata göre.")


def health_line(last_bar: Optional[date], gemini_ok: bool) -> str:
    lb = utils.fmt_date_tr(last_bar) if last_bar else "yok"
    return (f"✅ Sistem çalışıyor · son veri {lb} · yapay zekâ {'açık' if gemini_ok else 'kapalı (kural modu)'}")


def evening_summary(today: date, regime: dict, positions: list[dict], accepted: list[dict],
                    watch: list[dict], macro: list[str], scorecard_text: str,
                    warnings: list[str], news_general: Optional[str], scanned: bool = True) -> str:
    out = [f"📊 <b>Akşam özeti — {utils.fmt_date_tr(today)}</b>"]
    label = regime.get("label", "NOTR")
    reg = REJIM_ETIKET.get(label, label)
    if regime.get("available"):
        out.append(f"Rejim: {reg} · {esc(regime['index'])} {fmt_tl(regime['close'], 0)}")
    else:
        out.append("Rejim: veri yok (nötr kabul edildi)")
    if regime.get("fx_shock"):
        out.append(f"⚠️ Kur şoku: USDTRY {fmt_pct(regime.get('usdtry_1d_pct'))}")
    if positions:
        out.append("")
        out.append("<b>Pozisyonlar</b>")
        for p in positions:
            out.append(f"• {esc(p['symbol'])} {fmt_tl(p['price'])} ({fmt_pct(p['pnl_pct'])}) → <b>{p['action']}</b>"
                       f" · stop {fmt_tl(p['stop'])} · hedef {fmt_tl(p['target'])}")
            if p.get("midas_note"):
                out.append(f"  ↳ {esc(p['midas_note'])}")
    else:
        out.append("\nAçık pozisyon yok.")
    out.append("")
    if accepted:
        out.append("<b>Adaylar:</b> " + ", ".join(esc(c["symbol"]) for c in accepted))
    elif not scanned:
        out.append("Aday taraması yapılmadı (veri güncel değil).")
    else:
        out.append("Aday yok.")
    blocked = [w for w in watch if w.get("veto_kind") in ("HABER", "REJIM", "TAKVIM")]
    if blocked:
        out.append("Engellenen: " + ", ".join(f"{esc(w['symbol'])} ({esc(w['reason'])})" for w in blocked[:4]))
    if news_general:
        out.append(f"Genel haber: {esc(news_general)}")
    if macro:
        out.append("")
        out.append("<b>Yaklaşan</b>: " + " · ".join(esc(m) for m in macro[:3]))
    if scorecard_text:
        out.append("")
        out.append(esc(scorecard_text))
    if warnings:
        out.append("")
        out.append("⚠️ " + " | ".join(esc(w) for w in warnings[:3]))
    return "\n".join(out)
