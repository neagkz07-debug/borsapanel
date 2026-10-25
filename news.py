"""Haber katmanı: RSS, GDELT, tekilleştirme, kaynak güveni, olay durumu.

Ağ çağrıları enjekte edilebilir (testlerde sahte fonksiyon verilir).
"""
from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote, urlparse

import requests

import config
import utils

log = logging.getLogger("news")


# ---------------------------------------------------------------------------
# Kaynak güveni
# ---------------------------------------------------------------------------
def domain_of(url_or_host: str) -> str:
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    host = urlparse(s).netloc if "://" in s else s
    host = host.split("/")[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(domain: str, listed: str) -> bool:
    return domain == listed or domain.endswith("." + listed)


def source_tier(domain: str, title: str = "") -> str:
    padded = f" {utils.normalize_text(title or '')} "
    if any(f" {utils.normalize_text(w)} " in padded for w in config.MANIPULASYON_KELIMELERI):
        return "T3"
    d = (domain or "").lower()
    if d in ("kap (manuel)", "kap.org.tr") or d.startswith("kap"):
        return "T1"
    for tier in ("T1", "T2"):
        if any(_matches(d, x) for x in config.KAYNAK_GUVENI[tier]):
            return tier
    return "T3"


# ---------------------------------------------------------------------------
# Başlık normalizasyonu ve kümeleme
# ---------------------------------------------------------------------------
def normalize_title(title: str, source_name: str = "") -> str:
    t = title or ""
    if source_name and t.endswith(f" - {source_name}"):
        t = t[: -len(f" - {source_name}")]
    elif " - " in t:
        t = t.rsplit(" - ", 1)[0]
    return utils.normalize_text(t)


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_items(items: list[dict], threshold: float = config.KUMELEME_ESIGI) -> list[dict]:
    """items: {'title','source','domain','url','published'(iso)} -> kümeler (c1, c2, ...)."""
    ordered = sorted(items, key=lambda x: x.get("published") or "")
    clusters: list[dict] = []
    for it in ordered:
        toks = set(normalize_title(it["title"], it.get("source", "")).split())
        placed = False
        for c in clusters:
            if jaccard(toks, c["_tokens"]) >= threshold:
                c["items"].append(it)
                placed = True
                break
        if not placed:
            clusters.append({"_tokens": toks, "items": [it]})
    out = []
    for i, c in enumerate(clusters, start=1):
        its = c["items"]
        evidence = []
        seen = set()
        for it in its:
            tier = source_tier(it.get("domain", ""), it["title"])
            key = (it.get("domain", ""), it["title"])
            if key in seen:
                continue
            seen.add(key)
            evidence.append({"title": it["title"], "source": it.get("domain", "") or it.get("source", ""),
                             "url": it.get("url", ""), "published": it.get("published", ""),
                             "tier": tier})
        out.append({
            "id": f"c{i}",
            "title": its[0]["title"],
            "first_seen": min((x.get("published") or "") for x in its) or None,
            "source_count": len({e["source"] for e in evidence}),
            "evidence": evidence,
        })
    return out


def is_trusted(evidence: list[dict]) -> bool:
    """>=1 T1 kaynak veya >=2 farklı T1/T2 kaynak (6.3)."""
    good = {e["source"] for e in evidence if e.get("tier") in ("T1", "T2")}
    has_t1 = any(e.get("tier") == "T1" for e in evidence)
    return has_t1 or len(good) >= 2


def has_credible_source(evidence: list[dict]) -> bool:
    return any(e.get("tier") in ("T1", "T2") for e in evidence)


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------
def fetch_rss(query: str, now: Optional[datetime] = None, max_items: int = 8,
              hours: int = config.RSS_SAAT, http_get: Callable = requests.get) -> list[dict]:
    """Google News RSS -> öğe listesi. Hata olursa boş liste (log'a yazar)."""
    import feedparser  # geç içe aktarım: testlerde şart olmasın

    now = now or utils.now_ist()
    url = config.RSS_URL.format(q=quote(query))
    try:
        resp = http_get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (bist30-bot)"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:  # noqa: BLE001
        log.warning("RSS hatası (%s): %s", query, e)
        return []
    out = []
    cutoff = now - timedelta(hours=hours)
    for e in feed.entries:
        pub = None
        if getattr(e, "published_parsed", None):
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(utils.TZ)
        if pub and pub < cutoff:
            continue
        src = getattr(e, "source", None)
        src_name = (src.get("title") if src else "") or ""
        src_href = (src.get("href") if src else "") or ""
        title = e.get("title", "")
        if src_name and title.endswith(f" - {src_name}"):
            title = title[: -len(f" - {src_name}")]
        out.append({
            "title": title.strip(),
            "source": src_name,
            "domain": domain_of(src_href) or domain_of(src_name),
            "url": e.get("link", ""),
            "published": utils.iso(pub) if pub else "",
        })
        if len(out) >= max_items:
            break
    return out


def collect_rss_for_symbol(symbol: str, deep: bool, now: datetime,
                           fetch: Callable = fetch_rss, sleep: Callable = time.sleep) -> list[dict]:
    name = config.SIRKET_ADI.get(symbol, symbol)
    queries = [f'"{name}" hisse']
    if deep:
        queries.append(f"{symbol} hisse")
    items: list[dict] = []
    for q in queries:
        items.extend(fetch(q, now=now, max_items=config.RSS_HISSE_BASLIK))
        sleep(config.RSS_BEKLEME_SN)
    # aynı URL'yi iki kez sayma
    seen, uniq = set(), []
    for it in items:
        k = it.get("url") or it["title"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return uniq


def collect_general(now: datetime, fetch: Callable = fetch_rss, sleep: Callable = time.sleep) -> list[dict]:
    out = []
    for q in ("Borsa İstanbul", "TCMB"):
        out.extend(fetch(q, now=now, max_items=config.RSS_GENEL_BASLIK))
        sleep(config.RSS_BEKLEME_SN)
    return out


# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------
def parse_timeline(js: dict) -> list[tuple[str, float]]:
    """GDELT timeline JSON -> [(YYYYMMDD, değer)]. Beklenen yapı: timeline[0].data[{date,value}]."""
    try:
        data = js["timeline"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return []
    out = []
    for p in data:
        try:
            out.append((str(p["date"])[:8], float(p["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def daily_means(points: list[tuple[str, float]]) -> dict[str, float]:
    acc: dict[str, list[float]] = {}
    for d, v in points:
        acc.setdefault(d, []).append(v)
    return {d: sum(v) / len(v) for d, v in acc.items()}


def gdelt_metrics(vol_points: list[tuple[str, float]], tone_points: list[tuple[str, float]]) -> Optional[dict]:
    vol = daily_means(vol_points)
    tone = daily_means(tone_points)
    days = sorted(vol)
    if len(days) < 4:
        return None
    last, prev = days[-1], days[-7:-1]
    base = [vol[d] for d in prev]
    sd = statistics.pstdev(base) if len(base) > 1 else 0.0
    vol_z = (vol[last] - statistics.mean(base)) / sd if sd > 0 else 0.0
    tone_delta = None
    tdays = sorted(tone)
    if len(tdays) >= 4:
        tbase = [tone[d] for d in tdays[-7:-1]]
        tone_delta = tone[tdays[-1]] - statistics.mean(tbase)
    return {"vol_z": round(vol_z, 2),
            "tone_delta": round(tone_delta, 2) if tone_delta is not None else None}


def fetch_gdelt(company: str, http_get: Callable = requests.get,
                sleep: Callable = time.sleep) -> Optional[dict]:
    """Şirket adı için son 7 günün hacim ve ton sapması. Hata olursa None."""
    q = f'"{company}" sourcelang:turkish'
    pts = {}
    for mode in ("TimelineVol", "TimelineTone"):
        try:
            r = http_get(config.GDELT_URL, timeout=20,
                         params={"query": q, "mode": mode, "timespan": "7d", "format": "json"})
            r.raise_for_status()
            pts[mode] = parse_timeline(r.json())
        except Exception as e:  # noqa: BLE001
            log.warning("GDELT hatası (%s, %s): %s", company, mode, e)
            return None
        finally:
            sleep(config.GDELT_BEKLEME_SN)
    return gdelt_metrics(pts["TimelineVol"], pts["TimelineTone"])


# ---------------------------------------------------------------------------
# Manuel (KAP Mobil) haber
# ---------------------------------------------------------------------------
_YON = {"OLUMLU": "OLUMLU", "OLUMSUZ": "OLUMSUZ", "BELIRSIZ": "BELIRSIZ"}


def manual_events(symbol: str, manual_items: list[dict], now: datetime) -> list[dict]:
    out = []
    for m in manual_items or []:
        if m.get("symbol") != symbol:
            continue
        t0 = utils.parse_iso(m.get("tarih"))
        if t0 is None:
            continue
        valid_h = float(m.get("gecerlilik_saat", 48))
        if now > t0 + timedelta(hours=valid_h):
            continue
        tur = m.get("tur", "GENEL")
        etype = {"BILANCO": "KAR_BILANCO", "TEMETTU": "TEMETTU"}.get(tur, "DIGER")
        out.append({
            "event_type": etype,
            "direction": _YON.get(m.get("yon", "BELIRSIZ"), "BELIRSIZ"),
            "severity": max(1, min(5, int(m.get("onem", 3)))),
            "source_count": 1,
            "first_seen": utils.iso(t0),
            "trusted": True,
            "origin": "manuel",
            "summary": (m.get("not") or "")[:140],
            "evidence": [{"title": m.get("not", ""), "source": "KAP (manuel)", "url": "",
                          "published": utils.iso(t0), "tier": "T1"}],
        })
    return out


def prune_manual(manual_items: list[dict], now: datetime) -> list[dict]:
    keep = []
    for m in manual_items or []:
        t0 = utils.parse_iso(m.get("tarih"))
        if t0 and now <= t0 + timedelta(hours=float(m.get("gecerlilik_saat", 48))):
            keep.append(m)
    return keep[-config.MANUEL_HABER_MAX:]


def keyword_risk_events(clusters: list[dict]) -> list[dict]:
    """LLM yokken temkinli yedek: güvenilir kümede riskli ifade -> olumsuz önem-3 olay."""
    out = []
    for c in clusters:
        norm = f" {utils.normalize_text(' '.join(e['title'] for e in c['evidence']))} "
        if not any(f" {utils.normalize_text(k)} " in norm for k in config.NEGATIF_IFADELER):
            continue
        if not is_trusted(c["evidence"]):
            continue
        out.append({
            "event_type": "DAVA_CEZA_SORUSTURMA", "direction": "OLUMSUZ", "severity": 3,
            "confidence": 40, "summary": c["title"][:140], "source_count": c["source_count"],
            "first_seen": c.get("first_seen"), "trusted": True, "evidence": c["evidence"][:6],
            "origin": "kural",
        })
    return out


# ---------------------------------------------------------------------------
# Olay durumu -> karar bayrakları
# ---------------------------------------------------------------------------
def build_news_state(events: list[dict], gdelt: Optional[dict], llm_risk_flag: bool = False,
                     summary: str = "", confidence: Optional[int] = None) -> dict:
    """Doğrulanmış olaylardan sembol haber durumu üretir (state.json 'news')."""
    score = 0
    for e in events:
        if not has_credible_source(e["evidence"]):
            continue
        sign = {"OLUMLU": 1, "OLUMSUZ": -1}.get(e["direction"], 0)
        score += sign * e["severity"] * (1 if e["trusted"] else 0.5)
    sentiment = "OLUMLU" if score >= 3 else "OLUMSUZ" if score <= -3 else "NOTR"
    flags = news_flags({"events": events})
    return {
        "sentiment": sentiment,
        "risk_flag": flags["veto"],
        "events": events,
        "gdelt": gdelt,
        "llm_risk_flag": bool(llm_risk_flag),
        "summary": summary,
        "confidence": confidence,
    }


def news_flags(news_state: Optional[dict]) -> dict:
    """6.6 kuralları için bayraklar."""
    events = (news_state or {}).get("events", []) or []
    veto = neg3 = catalyst = False
    top = None
    for e in events:
        trusted = bool(e.get("trusted"))
        sev = int(e.get("severity", 0))
        credible = has_credible_source(e.get("evidence", []))
        if e.get("direction") == "OLUMSUZ" and credible:
            if trusted and sev >= 4:
                veto = True
            elif sev == 3 or (sev >= 4 and not trusted):
                neg3 = True
        if e.get("direction") == "OLUMLU" and trusted and sev >= config.KATALIZOR_MIN_ONEM:
            catalyst = True
            if top is None or (sev, e.get("source_count", 0)) > (top["severity"], top.get("source_count", 0)):
                top = {"severity": sev, "source_count": e.get("source_count", 0),
                       "event_type": e.get("event_type")}
    return {"veto": veto, "neg3": neg3, "catalyst": catalyst, "top_event": top}


def event_key(symbol: str, e: dict) -> str:
    return f"{symbol}|{e.get('event_type')}|{e.get('direction')}|{(e.get('first_seen') or '')[:10]}"


def top_event_for_journal(news_state: Optional[dict]) -> dict:
    events = (news_state or {}).get("events", []) or []
    if not events:
        return {"event_type": None, "severity": None, "source_count": None}
    best = max(events, key=lambda e: (e.get("severity", 0), e.get("source_count", 0)))
    return {"event_type": best.get("event_type"), "severity": best.get("severity"),
            "source_count": best.get("source_count")}
