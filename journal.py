"""İleriye dönük test günlüğü (journal.json).

Her sinyal (veto edilenler dahil) kaydedilir; sonraki günlerde G1/G3/G5 getirileri ve
HEDEF/STOP ilk vuruşu ölçülür. Geriye dönük backtest yoktur; amaç gerçek sinyal kalitesini
zamanla ölçmektir (n < 30 ise skor kartı uyarı verir).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

import config
import utils

log = logging.getLogger("journal")

ILK_GUNLER = (1, 3, 5)


def entry_id(day: date, symbol: str, kind: str) -> str:
    return f"{day.isoformat()}-{symbol}-{kind}"


def make_entry(day: date, symbol: str, kind: str, ref_price: float, stop: float, target: float,
               rr: Optional[float], regime: str, news_info: Optional[dict] = None,
               veto: Optional[str] = None, score: Optional[int] = None) -> dict:
    return {
        "id": entry_id(day, symbol, kind),
        "tarih": day.isoformat(),
        "symbol": symbol,
        "tur": kind,                 # TREND | KATALIZOR
        "ref_fiyat": round(float(ref_price), 4),
        "stop": round(float(stop), 4),
        "hedef": round(float(target), 4),
        "rr": None if rr is None else round(float(rr), 2),
        "rejim": regime,
        "skor": score,
        "haber": news_info or {"event_type": None, "severity": None, "source_count": None},
        "veto": veto,                # None | HABER | REJIM | TAKVIM | RISK
        "sonuc": {"durum": "BEKLIYOR"},
    }


def add_entries(journal: dict, entries: list[dict]) -> int:
    """Aynı id tekrar eklenmez. Dönen: eklenen sayı."""
    items = journal.setdefault("kayitlar", [])
    ids = {x["id"] for x in items}
    added = 0
    for e in entries:
        if e["id"] in ids:
            continue
        items.append(e)
        ids.add(e["id"])
        added += 1
    if len(items) > config.JOURNAL_MAX_KAYIT:
        # önce tamamlanmış kayıtlardan en eskileri at
        done = [x for x in items if x["sonuc"].get("durum") == "TAMAM"]
        drop = len(items) - config.JOURNAL_MAX_KAYIT
        drop_ids = {x["id"] for x in done[:drop]}
        journal["kayitlar"] = [x for x in items if x["id"] not in drop_ids][-config.JOURNAL_MAX_KAYIT:]
    return added


def _bars_after(df: pd.DataFrame, day: date) -> pd.DataFrame:
    """Sinyal gününden SONRAKİ günlük mumlar (G1 = ilk sonraki işlem günü)."""
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d = df.copy()
    d.index = idx.normalize()
    return d[d.index > pd.Timestamp(day)]


def measure(entry: dict, df: pd.DataFrame) -> dict:
    """Bir kaydın sonucunu mevcut fiyat verisinden hesapla.

    G1/G3/G5 getirisi: ilk sonraki işlem günü AÇILIŞINA göre n. gün kapanışı.
    first_hit: ilk 5 günde önce STOP mu HEDEF mi? Aynı gün ikisi de vurulduysa BELIRSIZ.
    """
    day = utils.parse_date(entry["tarih"])
    after = _bars_after(df, day)
    n = len(after)
    res: dict = {"durum": "BEKLIYOR", "gun_sayisi": min(n, 5)}
    if n == 0:
        return res
    entry_px = float(after["Open"].iloc[0])
    res["giris_acilis"] = round(entry_px, 4)
    for g in ILK_GUNLER:
        if n >= g:
            res[f"g{g}"] = round((float(after["Close"].iloc[g - 1]) / entry_px - 1) * 100, 2)
    window = after.iloc[:5]
    res["max_lehte"] = round((float(window["High"].max()) / entry_px - 1) * 100, 2)
    res["max_aleyhte"] = round((float(window["Low"].min()) / entry_px - 1) * 100, 2)
    first_hit = "YOK"
    for _, row in window.iterrows():
        hit_stop = float(row["Low"]) <= entry["stop"]
        hit_tgt = float(row["High"]) >= entry["hedef"]
        if hit_stop and hit_tgt:
            first_hit = "BELIRSIZ"
            break
        if hit_stop:
            first_hit = "STOP"
            break
        if hit_tgt:
            first_hit = "HEDEF"
            break
    res["first_hit"] = first_hit
    if n >= 5 or first_hit in ("STOP", "HEDEF", "BELIRSIZ"):
        res["durum"] = "TAMAM" if n >= 5 else "ACIK_VURULDU"
    return res


def update_outcomes(journal: dict, prices: dict[str, pd.DataFrame]) -> int:
    """BEKLIYOR / ACIK_VURULDU kayıtların sonuçlarını güncelle. Dönen: güncellenen sayı."""
    changed = 0
    for e in journal.get("kayitlar", []):
        if e["sonuc"].get("durum") == "TAMAM":
            continue
        df = prices.get(e["symbol"])
        if df is None or df.empty:
            continue
        new = measure(e, df)
        if new != e["sonuc"]:
            e["sonuc"] = new
            changed += 1
    return changed


def scorecard(journal: dict, min_n: int = 30) -> dict:
    """Tamamlanmış (5 gün) kayıtlar üzerinden özet. Veto edilenler ayrı raporlanır."""
    done = [e for e in journal.get("kayitlar", []) if e["sonuc"].get("durum") == "TAMAM"]

    def agg(rows: list[dict]) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        g1 = [r["sonuc"]["g1"] for r in rows if "g1" in r["sonuc"]]
        g3 = [r["sonuc"]["g3"] for r in rows if "g3" in r["sonuc"]]
        g5 = [r["sonuc"]["g5"] for r in rows if "g5" in r["sonuc"]]
        hits = [r["sonuc"].get("first_hit") for r in rows]
        decided = [h for h in hits if h in ("HEDEF", "STOP")]
        return {
            "n": n,
            "g1_ort": round(sum(g1) / len(g1), 2) if g1 else None,
            "g3_ort": round(sum(g3) / len(g3), 2) if g3 else None,
            "g5_ort": round(sum(g5) / len(g5), 2) if g5 else None,
            "g5_pozitif_oran": round(100 * sum(1 for x in g5 if x > 0) / len(g5), 1) if g5 else None,
            "hedef": hits.count("HEDEF"),
            "stop": hits.count("STOP"),
            "belirsiz": hits.count("BELIRSIZ"),
            "yok": hits.count("YOK"),
            "hedef_orani": round(100 * hits.count("HEDEF") / len(decided), 1) if decided else None,
        }

    taken = [e for e in done if not e.get("veto")]
    vetoed = [e for e in done if e.get("veto")]
    out = {
        "toplam_kayit": len(journal.get("kayitlar", [])),
        "tamamlanan": len(done),
        "hepsi": agg(done),
        "alinabilir": agg(taken),
        "veto_edilen": agg(vetoed),
        "tur": {k: agg([e for e in taken if e["tur"] == k]) for k in ("TREND", "KATALIZOR")},
        "rejim": {k: agg([e for e in done if e.get("rejim") == k]) for k in ("RISK_ON", "NOTR", "RISK_OFF")},
        "olay_turu": {k: agg([e for e in done if (e.get("haber") or {}).get("event_type") == k])
                      for k in sorted({(e.get("haber") or {}).get("event_type") for e in done} - {None})},
        "uyari": None,
    }
    if len(taken) < min_n:
        out["uyari"] = (f"Örneklem küçük (n={len(taken)}, hedef ≥{min_n}). "
                        "Bu sayılar karar için değil, gözlem için.")
    return out


def format_scorecard_tr(sc: dict) -> str:
    """Telegram/panel için kısa Türkçe özet."""
    a = sc["alinabilir"]
    if a["n"] == 0:
        return "Günlük: henüz tamamlanmış sinyal yok."
    lines = [f"Günlük: {sc['tamamlanan']} tamamlanan sinyal",
             f"G5 ort {utils.fmt_pct(a['g5_ort'])} · pozitif oran {utils.fmt_pct(a['g5_pozitif_oran'], 0)}",
             f"Hedef/Stop: {a['hedef']}/{a['stop']}"]
    v = sc["veto_edilen"]
    if v["n"]:
        lines.append(f"Veto edilen {v['n']} sinyalde G5 ort {utils.fmt_pct(v['g5_ort'])} (veto işe yarıyor mu?)")
    if sc["uyari"]:
        lines.append(sc["uyari"])
    return "\n".join(lines)
