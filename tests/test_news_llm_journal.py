import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import config
import journal
import llm
import news
import utils

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=utils.TZ)


def item(title, domain, hours_ago=2, source=None):
    return {"title": title, "source": source or domain, "domain": domain,
            "url": f"https://{domain}/x", "published": utils.iso(NOW - timedelta(hours=hours_ago))}


# ---------------- news.py ----------------
def test_source_tier_basic_and_manipulation():
    assert news.source_tier("aa.com.tr", "Şirket kâr açıkladı") == "T1"
    assert news.source_tier("www.hurriyet.com.tr", "x") == "T2"
    assert news.source_tier("bilinmeyen.com", "x") == "T3"
    assert news.source_tier("aa.com.tr", "Bu hisse uçacak, kaçırmayın") == "T3"


def test_garanti_company_name_is_not_manipulation():
    assert news.source_tier("aa.com.tr", "Garanti BBVA ikinci çeyrek kârı açıkladı") == "T1"


def test_domain_of():
    assert news.domain_of("https://www.aa.com.tr/tr/ekonomi/x") == "aa.com.tr"
    assert news.domain_of("bloomberght.com") == "bloomberght.com"
    assert news.domain_of("") == ""


def test_clustering_merges_similar_titles():
    items = [
        item("THY ikinci çeyrek net kârını açıkladı - AA", "aa.com.tr"),
        item("THY ikinci çeyrek net kârını açıkladı - Hürriyet", "hurriyet.com.tr"),
        item("Merkez Bankası faiz kararı bekleniyor", "ntv.com.tr"),
    ]
    cl = news.cluster_items(items)
    assert len(cl) == 2
    big = max(cl, key=lambda c: c["source_count"])
    assert big["source_count"] == 2
    assert {c["id"] for c in cl} == {"c1", "c2"}


def test_is_trusted_rules():
    t1 = [{"source": "aa.com.tr", "tier": "T1"}]
    two_t2 = [{"source": "a.com", "tier": "T2"}, {"source": "b.com", "tier": "T2"}]
    one_t2 = [{"source": "a.com", "tier": "T2"}]
    t3 = [{"source": "x.com", "tier": "T3"}, {"source": "y.com", "tier": "T3"}]
    assert news.is_trusted(t1)
    assert news.is_trusted(two_t2)
    assert not news.is_trusted(one_t2)
    assert not news.is_trusted(t3)


def test_gdelt_parse_and_metrics():
    js = {"timeline": [{"series": "Volume", "data": [
        {"date": "20261019T000000Z", "value": 1.0}, {"date": "20261020T000000Z", "value": 1.2},
        {"date": "20261021T000000Z", "value": 0.8}, {"date": "20261022T000000Z", "value": 1.0},
        {"date": "20261023T000000Z", "value": 1.1}, {"date": "20261024T000000Z", "value": 0.9},
        {"date": "20261025T000000Z", "value": 4.0}]}]}
    pts = news.parse_timeline(js)
    assert len(pts) == 7
    tone = [(d, -1.0) for d, _ in pts[:-1]] + [(pts[-1][0], -5.0)]
    m = news.gdelt_metrics(pts, tone)
    assert m["vol_z"] > 3
    assert m["tone_delta"] == pytest.approx(-4.0, abs=0.01)
    assert news.parse_timeline({"foo": 1}) == []
    assert news.gdelt_metrics(pts[:2], tone[:2]) is None


def test_fetch_gdelt_handles_error():
    def boom(*a, **k):
        raise RuntimeError("down")
    assert news.fetch_gdelt("Türk Hava Yolları", http_get=boom, sleep=lambda s: None) is None


def test_fetch_rss_parses_and_filters_old(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>THY kar acikladi - AA</title><link>https://n/1</link>
    <pubDate>Mon, 26 Oct 2026 05:00:00 GMT</pubDate><source url="https://www.aa.com.tr">AA</source></item>
    <item><title>Eski haber - X</title><link>https://n/2</link>
    <pubDate>Mon, 01 Jun 2026 05:00:00 GMT</pubDate><source url="https://x.com">X</source></item>
    </channel></rss>"""

    class R:
        content = xml
        def raise_for_status(self): pass
    out = news.fetch_rss("THY", now=NOW, http_get=lambda *a, **k: R())
    assert len(out) == 1
    assert out[0]["domain"] == "aa.com.tr"
    assert out[0]["title"] == "THY kar acikladi"


def test_fetch_rss_error_returns_empty():
    def boom(*a, **k):
        raise RuntimeError("x")
    assert news.fetch_rss("THY", now=NOW, http_get=boom) == []


def test_manual_events_and_expiry():
    items = [
        {"symbol": "THYAO", "tarih": utils.iso(NOW - timedelta(hours=5)), "tur": "BILANCO",
         "yon": "OLUMLU", "onem": 5, "not": "Net kâr beklentinin üstünde", "gecerlilik_saat": 48},
        {"symbol": "THYAO", "tarih": utils.iso(NOW - timedelta(hours=100)), "tur": "GENEL",
         "yon": "OLUMSUZ", "onem": 4, "not": "eski", "gecerlilik_saat": 48},
        {"symbol": "GARAN", "tarih": utils.iso(NOW - timedelta(hours=1)), "tur": "GENEL",
         "yon": "OLUMLU", "onem": 3, "not": "x"},
    ]
    ev = news.manual_events("THYAO", items, NOW)
    assert len(ev) == 1 and ev[0]["trusted"] and ev[0]["severity"] == 5
    assert ev[0]["event_type"] == "KAR_BILANCO"
    assert len(news.prune_manual(items, NOW)) == 2


def _ev(direction, sev, trusted, tier="T1"):
    return {"event_type": "DIGER", "direction": direction, "severity": sev, "trusted": trusted,
            "source_count": 2, "evidence": [{"source": "aa.com.tr", "tier": tier}]}


def test_news_flags():
    f = news.news_flags({"events": [_ev("OLUMSUZ", 4, True)]})
    assert f["veto"] and not f["catalyst"]
    f = news.news_flags({"events": [_ev("OLUMSUZ", 3, True)]})
    assert f["neg3"] and not f["veto"]
    f = news.news_flags({"events": [_ev("OLUMLU", 4, True)]})
    assert f["catalyst"] and f["top_event"]["severity"] == 4
    # güvenilmeyen olumlu olay katalizör olamaz
    f = news.news_flags({"events": [_ev("OLUMLU", 5, False)]})
    assert not f["catalyst"]
    # T3 kanıtlı olumsuz olay veto üretmez
    f = news.news_flags({"events": [_ev("OLUMSUZ", 5, True, tier="T3")]})
    assert not f["veto"] and not f["neg3"]
    assert news.news_flags(None) == {"veto": False, "neg3": False, "catalyst": False, "top_event": None}


def test_build_news_state_sentiment():
    st = news.build_news_state([_ev("OLUMLU", 4, True)], None)
    assert st["sentiment"] == "OLUMLU"
    st = news.build_news_state([_ev("OLUMSUZ", 4, True)], None)
    assert st["sentiment"] == "OLUMSUZ" and st["risk_flag"]


# ---------------- llm.py ----------------
def make_clusters():
    return {"THYAO": news.cluster_items([
        item("THY rekor kâr açıkladı", "aa.com.tr"),
        item("THY rekor kâr açıkladı", "bloomberght.com"),
    ]), "GARAN": []}


def test_prompt_has_no_private_data():
    p = llm.build_prompt(make_clusters())
    low = p.lower().replace("bedelsiz_sermaye", "")  # meşru olay türü adı
    for bad in llm.YASAK_ALANLAR:
        assert bad not in low
    assert "THYAO" in p and "[c1]" in p


def test_validate_response_drops_invalid():
    cl = make_clusters()
    raw = {"events": [
        {"symbol": "THYAO", "cluster_ids": ["c1"], "event_type": "KAR_BILANCO", "direction": "OLUMLU",
         "severity": 9, "confidence": 80, "summary": "Rekor kâr"},
        {"symbol": "THYAO", "cluster_ids": ["c99"], "event_type": "KAR_BILANCO", "direction": "OLUMLU", "severity": 4},
        {"symbol": "THYAO", "cluster_ids": ["c1"], "event_type": "UYDURMA", "direction": "OLUMLU", "severity": 4},
        {"symbol": "XXXX", "cluster_ids": ["c1"], "event_type": "DIGER", "direction": "OLUMLU", "severity": 4},
        "garbage",
    ], "risk_flags": [{"symbol": "THYAO", "reason": "dava"}]}
    out = llm.validate_response(raw, cl)
    assert len(out["events"]["THYAO"]) == 1
    e = out["events"]["THYAO"][0]
    assert e["severity"] == 5 and e["trusted"] and e["source_count"] == 2
    assert out["dropped"] == 4
    assert out["risk_flags"] == {"THYAO": "dava"}
    assert llm.validate_response(None, cl)["events"] == {}


def test_validate_caps_weak_source_severity():
    cl = {"THYAO": news.cluster_items([item("THY hakkında söylenti", "blog.example")])}
    raw = {"events": [{"symbol": "THYAO", "cluster_ids": ["c1"], "event_type": "DIGER",
                       "direction": "OLUMLU", "severity": 5}]}
    e = llm.validate_response(raw, cl)["events"]["THYAO"][0]
    assert e["severity"] == 2 and not e["trusted"]


class FakeResp:
    def __init__(self, text, chunks=None):
        self.text = text
        web = [type("C", (), {"web": type("W", (), {"uri": f"https://v/{d}", "domain": d, "title": d})()})() for d in (chunks or [])]
        gm = type("G", (), {"grounding_chunks": web})()
        self.candidates = [type("Cand", (), {"grounding_metadata": gm})()]


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []
        self.models = self

    def generate_content(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.resp


def test_classify_with_fake_client():
    cl = make_clusters()
    payload = {"events": [{"symbol": "THYAO", "cluster_ids": ["c1"], "event_type": "KAR_BILANCO",
                           "direction": "OLUMLU", "severity": 4}]}
    client = FakeClient(FakeResp(json.dumps(payload)))
    out = llm.classify(client, cl)
    assert out["events"]["THYAO"][0]["trusted"]
    sent = str(client.calls[0]["contents"]).lower().replace("bedelsiz_sermaye", "")
    for bad in llm.YASAK_ALANLAR:
        assert bad not in sent


def test_classify_failure_returns_none():
    cl = make_clusters()
    assert llm.classify(FakeClient(exc=RuntimeError("429 RESOURCE_EXHAUSTED")), cl) is None
    assert llm.classify(FakeClient(FakeResp("not json")), cl) is None
    assert llm.classify(None, cl) is None
    assert llm.classify(FakeClient(FakeResp("{}")), {"THYAO": []}) is None


def test_grounded_search_builds_pseudo_cluster():
    client = FakeClient(FakeResp("THY yeni uçak siparişi verdi.", ["aa.com.tr", "bloomberght.com"]))
    out = llm.grounded_search(client, "THYAO")
    c = out["cluster"]
    assert c["id"] == "g_THYAO" and c["source_count"] == 2
    assert news.is_trusted(c["evidence"])
    # Kaynak yoksa küme yok
    out = llm.grounded_search(FakeClient(FakeResp("Bir şey oldu.", [])), "THYAO")
    assert out["cluster"] is None
    out = llm.grounded_search(FakeClient(FakeResp("Önemli gelişme yok.", ["aa.com.tr"])), "THYAO")
    assert out["cluster"] is None
    assert llm.grounded_search(FakeClient(exc=RuntimeError("boom")), "THYAO") is None


def test_grounding_budget():
    today = date(2026, 10, 26)
    assert llm.grounding_budget_left(None, today) == config.GROUNDING_GUNLUK_LIMIT
    st = llm.bump_grounding(None, today, 3)
    assert llm.grounding_budget_left(st, today) == config.GROUNDING_GUNLUK_LIMIT - 3
    assert llm.grounding_budget_left(st, date(2026, 10, 27)) == config.GROUNDING_GUNLUK_LIMIT


# ---------------- journal.py ----------------
def make_df(day: date, rows):
    """rows: [(open, high, low, close)] sinyal gününden sonraki günler."""
    idx, d = [], day
    for _ in rows:
        d = utils.next_trading_day(d)
        idx.append(pd.Timestamp(d))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=pd.DatetimeIndex(idx))


def entry(day=date(2026, 10, 20), stop=95.0, target=110.0, **kw):
    return journal.make_entry(day, "THYAO", "TREND", 100.0, stop, target, 1.8, "RISK_ON", **kw)


def test_add_entries_dedupes():
    j = {}
    e = entry()
    assert journal.add_entries(j, [e, dict(e)]) == 1
    assert journal.add_entries(j, [e]) == 0


def test_measure_waiting_when_no_data():
    df = make_df(date(2026, 10, 20), [])
    assert journal.measure(entry(), df)["durum"] == "BEKLIYOR"


def test_measure_target_hit_and_returns():
    rows = [(100, 104, 99, 103), (103, 106, 102, 105), (105, 111, 104, 110),
            (110, 112, 108, 111), (111, 113, 109, 112)]
    r = journal.measure(entry(), make_df(date(2026, 10, 20), rows))
    assert r["durum"] == "TAMAM"
    assert r["first_hit"] == "HEDEF"
    assert r["g1"] == pytest.approx(3.0)
    assert r["g3"] == pytest.approx(10.0)
    assert r["g5"] == pytest.approx(12.0)


def test_measure_stop_first_and_ambiguous():
    rows = [(100, 101, 94, 96)]
    r = journal.measure(entry(), make_df(date(2026, 10, 20), rows))
    assert r["first_hit"] == "STOP" and r["durum"] == "ACIK_VURULDU"
    rows = [(100, 111, 94, 100)]
    r = journal.measure(entry(), make_df(date(2026, 10, 20), rows))
    assert r["first_hit"] == "BELIRSIZ"


def test_update_outcomes_and_scorecard_warning():
    j = {}
    day = date(2026, 10, 12)
    journal.add_entries(j, [entry(day=day), journal.make_entry(day, "GARAN", "KATALIZOR", 50, 47, 56, 2.0,
                                                                "NOTR", veto="HABER")])
    rows = [(100, 104, 99, 103), (103, 106, 102, 105), (105, 111, 104, 110), (110, 112, 108, 111), (111, 113, 109, 112)]
    prices = {"THYAO": make_df(day, rows),
              "GARAN": make_df(day, [(50, 51, 49, 50)] * 5)}
    assert journal.update_outcomes(j, prices) == 2
    sc = journal.scorecard(j)
    assert sc["tamamlanan"] == 2
    assert sc["alinabilir"]["n"] == 1 and sc["veto_edilen"]["n"] == 1
    assert sc["uyari"] and "Örneklem küçük" in sc["uyari"]
    assert "Veto edilen" in journal.format_scorecard_tr(sc)
    # tamamlanmış kayıt yeniden hesaplanmaz
    assert journal.update_outcomes(j, prices) == 0


def test_journal_trims_oldest_completed(monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_MAX_KAYIT", 3)
    j = {"kayitlar": []}
    es = []
    for i in range(5):
        e = journal.make_entry(date(2026, 10, 1) + timedelta(days=i), "THYAO", "TREND", 100, 95, 110, 1.8, "RISK_ON")
        e["sonuc"] = {"durum": "TAMAM"}
        es.append(e)
    journal.add_entries(j, es)
    assert len(j["kayitlar"]) == 3
    assert j["kayitlar"][0]["tarih"] == "2026-10-03"


def test_model_fallback_on_404(monkeypatch):
    class NotFound(Exception):
        code = 404

    class C:
        def __init__(self):
            self.models, self.used = self, []

        def generate_content(self, model, **kw):
            self.used.append(model)
            if model == config.GEMINI_MODEL:
                raise NotFound("This model is no longer available.")
            return FakeResp(json.dumps({"events": []}))
    c = C()
    out = llm.classify(c, make_clusters())
    assert out is not None
    assert c.used == [config.GEMINI_MODEL, config.GEMINI_YEDEK_MODELLER[0]]


def test_non_404_error_does_not_try_other_models():
    class C:
        def __init__(self):
            self.models, self.used = self, []

        def generate_content(self, model, **kw):
            self.used.append(model)
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
    c = C()
    assert llm.classify(c, make_clusters()) is None
    assert len(c.used) == 1


def test_old_gemini_model_not_default():
    assert "2.5" not in config.GEMINI_MODEL
