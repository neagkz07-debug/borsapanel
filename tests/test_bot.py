import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import bot
import config
import data
import notify
import store
import utils

NOW_CLOSE = datetime(2026, 10, 26, 18, 15, tzinfo=utils.TZ)   # Pazartesi
NOW_OPEN = datetime(2026, 10, 26, 10, 15, tzinfo=utils.TZ)
NOW_INTRA = datetime(2026, 10, 26, 14, 30, tzinfo=utils.TZ)


def make_prices(n=120, end="2026-10-26", drift=0.002, base=100.0, seed=1, last_jump=0.0, vol=1_000_000):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    close = base * np.cumprod(1 + drift + rng.normal(0, 0.006, n))
    close[-1] *= (1 + last_jump)
    df = pd.DataFrame({"Open": close * 0.999, "High": close * 1.006, "Low": close * 0.994,
                       "Close": close, "Volume": np.full(n, float(vol))}, index=idx)
    return df


class FakeProvider(data.PriceProvider):
    def __init__(self, overrides=None, end="2026-10-26"):
        self.calls = []
        self.overrides = overrides or {}
        self.end = end

    def history(self, symbols):
        symbols = list(symbols)
        self.calls.append(symbols)
        out = {}
        for i, s in enumerate(symbols):
            if s in self.overrides:
                if self.overrides[s] is not None:
                    out[s] = self.overrides[s]
                continue
            if s == "USDTRY=X":
                out[s] = make_prices(drift=0.0005, base=30, seed=99, end=self.end)
            elif s == "XU030.IS":
                out[s] = make_prices(drift=0.002, base=10000, seed=7, end=self.end)
            elif s == "XU100.IS":
                continue
            else:
                out[s] = make_prices(drift=0.002 if i % 2 == 0 else -0.003, seed=i + 3, end=self.end)
        return out


class FakeNotifier(notify.Notifier):
    def __init__(self):
        super().__init__(token="t", chat_id="c", dry_run=True)

    def send(self, text):
        self.sent.append(text)
        return True


def rss_fake(q, now=None, max_items=8):
    return [{"title": f"{q} haberi bir", "source": "AA", "domain": "aa.com.tr", "url": "https://aa/1",
             "published": utils.iso(NOW_CLOSE - timedelta(hours=3))}]


def gdelt_fake(company, sleep=None):
    return {"vol_z": 0.5, "tone_delta": -0.2}


def setup_root(tmp_path, positions=None, settings=None):
    pf = {"settings": {"sermaye": 100000, **(settings or {})}, "positions": positions or [], "manuel_haber": []}
    (tmp_path / "portfolio.json").write_text(json.dumps(pf), encoding="utf-8")
    return str(tmp_path)


def deps(provider=None, notifier=None, client=None):
    return bot.Deps(provider=provider or FakeProvider(), notifier=notifier or FakeNotifier(), llm_client=client,
                    rss_fetch=rss_fake, gdelt_fetch=gdelt_fake, sleep=lambda s: None)


def read(tmp_path, name):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------
def test_resolve_mode():
    d = lambda h, m: datetime(2026, 10, 26, h, m, tzinfo=utils.TZ)
    assert bot.resolve_mode(d(9, 31)) == "open"
    assert bot.resolve_mode(d(11, 29)) == "open"
    assert bot.resolve_mode(d(11, 30)) == "intraday"
    assert bot.resolve_mode(d(17, 29)) == "intraday"
    assert bot.resolve_mode(d(17, 30)) == "close"
    assert bot.resolve_mode(d(10, 0), "close") == "close"


def test_holiday_skipped(tmp_path):
    root = setup_root(tmp_path)
    now = datetime(2026, 10, 29, 18, 15, tzinfo=utils.TZ)
    d = deps()
    st = bot.run(root, now=now, deps=d)
    assert st["son_calisma"]["durum"] == "ATLANDI_TATIL"
    assert d.provider.calls == []


def test_close_run_end_to_end(tmp_path):
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 100, "maliyet": 100.0, "giris_tarihi": "2026-10-01", "midas_stop": None}])
    n = FakeNotifier()
    st = bot.run(root, now=NOW_CLOSE, deps=deps(notifier=n))
    assert st["son_calisma"]["durum"] == "TAMAM"
    assert st["rejim"]["label"] in ("RISK_ON", "NOTR", "RISK_OFF")
    state = read(tmp_path, "state.json")
    assert state["pozisyonlar"][0]["symbol"] == "THYAO"
    assert state["pozisyonlar"][0]["action"] in ("TUT", "KAR_AL", "STOP_OL", "EKLEME_YAP")
    assert "journal.json" and os.path.exists(tmp_path / "journal.json")
    # akşam özeti bir kez gitti
    assert sum("Akşam özeti" in m for m in n.sent) == 1
    # aynı gün ikinci close koşusunda özet tekrarlanmaz
    n2 = FakeNotifier()
    bot.run(root, now=NOW_CLOSE + timedelta(minutes=5), deps=deps(notifier=n2))
    assert sum("Akşam özeti" in m for m in n2.sent) == 0


def test_dry_run_writes_nothing(tmp_path):
    root = setup_root(tmp_path)
    bot.run(root, now=NOW_CLOSE, dry_run=True, deps=deps())
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "journal.json").exists()


def test_stop_alert_every_run_and_ratchet(tmp_path):
    # THYAO düşüşte; maliyet güncel fiyatın çok üstünde -> STOP_OL
    prov = FakeProvider({"THYAO.IS": make_prices(drift=-0.004, base=200, seed=5)})
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 100, "maliyet": 250.0, "giris_tarihi": "2026-09-01", "midas_stop": 200.0}])
    n1, n2 = FakeNotifier(), FakeNotifier()
    bot.run(root, now=NOW_OPEN, deps=deps(provider=prov, notifier=n1))
    bot.run(root, now=NOW_INTRA, deps=deps(provider=prov, notifier=n2))
    assert any("STOP — THYAO" in m for m in n1.sent)
    assert any("STOP — THYAO" in m for m in n2.sent)   # STOP her koşuda


def test_take_profit_notified_once(tmp_path):
    prov = FakeProvider({"THYAO.IS": make_prices(drift=0.01, base=100, seed=4)})
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 100, "maliyet": 100.0, "giris_tarihi": "2026-07-01", "midas_stop": 90.0}])
    n1, n2 = FakeNotifier(), FakeNotifier()
    st = bot.run(root, now=NOW_INTRA, deps=deps(provider=prov, notifier=n1))
    act = st["pozisyonlar"][0]["action"]
    assert act in ("KAR_AL", "TUT")
    if act == "KAR_AL":
        assert sum("KÂR AL" in m for m in n1.sent) == 1
        bot.run(root, now=NOW_INTRA + timedelta(hours=1), deps=deps(provider=prov, notifier=n2))
        assert sum("KÂR AL" in m for m in n2.sent) == 0


def test_stale_data_skips_candidates_and_warns(tmp_path):
    root = setup_root(tmp_path)
    prov = FakeProvider(end="2026-10-22")   # bugünkü bar yok
    n = FakeNotifier()
    st = bot.run(root, now=NOW_CLOSE, deps=deps(provider=prov, notifier=n))
    assert st["adaylar"] == []
    assert any("güncel değil" in w for w in st["son_calisma"]["uyarilar"])


def test_missing_symbol_data_does_not_crash(tmp_path):
    prov = FakeProvider({"THYAO.IS": None})
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 10, "maliyet": 100.0, "giris_tarihi": "2026-10-01"}])
    st = bot.run(root, now=NOW_CLOSE, deps=deps(provider=prov))
    assert st["pozisyonlar"][0]["action"] == "VERI_YOK"
    assert st["son_calisma"]["durum"] == "TAMAM"


def test_error_is_reported_once_per_day(tmp_path):
    class Boom(data.PriceProvider):
        def history(self, symbols):
            raise RuntimeError("yahoo çöktü")
    root = setup_root(tmp_path)
    n = FakeNotifier()
    st = bot.run(root, now=NOW_OPEN, deps=deps(provider=Boom(), notifier=n))
    assert st["_hata"] and any("Bot hatası" in m for m in n.sent)
    n2 = FakeNotifier()
    bot.run(root, now=NOW_INTRA, deps=deps(provider=Boom(), notifier=n2))
    assert not any("Bot hatası" in m for m in n2.sent)


def test_manual_negative_news_alerts_held_position_once(tmp_path):
    root = setup_root(tmp_path, positions=[
        {"symbol": "GARAN", "adet": 100, "maliyet": 100.0, "giris_tarihi": "2026-10-10", "midas_stop": 80.0}])
    pf = json.loads((tmp_path / "portfolio.json").read_text())
    pf["manuel_haber"] = [{"symbol": "GARAN", "tarih": utils.iso(NOW_INTRA - timedelta(hours=2)), "tur": "GENEL",
                           "yon": "OLUMSUZ", "onem": 5, "not": "BDDK idari para cezası", "gecerlilik_saat": 48}]
    (tmp_path / "portfolio.json").write_text(json.dumps(pf), encoding="utf-8")
    n1, n2 = FakeNotifier(), FakeNotifier()
    bot.run(root, now=NOW_INTRA, deps=deps(notifier=n1))
    bot.run(root, now=NOW_INTRA + timedelta(hours=1), deps=deps(notifier=n2))
    assert sum("HABER RİSKİ — GARAN" in m for m in n1.sent) == 1
    assert sum("HABER RİSKİ — GARAN" in m for m in n2.sent) == 0


def test_candidates_blocked_by_regime_off(tmp_path):
    # Endeks düşüşte -> RISK_OFF -> kabul edilen aday yok
    prov = FakeProvider({"XU030.IS": make_prices(drift=-0.004, base=10000, seed=8)})
    root = setup_root(tmp_path)
    st = bot.run(root, now=NOW_CLOSE, deps=deps(provider=prov))
    assert st["rejim"]["label"] == "RISK_OFF"
    assert st["adaylar"] == []


def test_gemini_prompt_never_contains_position_data(tmp_path):
    class Client:
        def __init__(self):
            self.prompts = []
            self.models = self

        def generate_content(self, **kw):
            self.prompts.append(str(kw.get("contents")))
            raise RuntimeError("429")
    c = Client()
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 7777, "maliyet": 123.45, "giris_tarihi": "2026-10-01", "midas_stop": 111.11}],
        settings={"sermaye": 987654})
    bot.run(root, now=NOW_CLOSE, deps=deps(client=c))
    assert c.prompts
    joined = " ".join(c.prompts)
    for secret in ("7777", "123,45", "123.45", "111.11", "987654", "987.654"):
        assert secret not in joined


def test_intraday_does_not_scan_candidates_or_call_news(tmp_path):
    root = setup_root(tmp_path)
    calls = []
    d = deps()
    d.rss_fetch = lambda *a, **k: calls.append(1) or []
    st = bot.run(root, now=NOW_INTRA, deps=d)
    assert calls == []
    assert st["adaylar"] == []


def test_monday_health_line_once(tmp_path):
    root = setup_root(tmp_path)
    n1, n2 = FakeNotifier(), FakeNotifier()
    bot.run(root, now=NOW_OPEN, deps=deps(notifier=n1))
    bot.run(root, now=NOW_OPEN + timedelta(minutes=30), deps=deps(notifier=n2))
    assert sum("Sistem çalışıyor" in m for m in n1.sent) == 1
    assert sum("Sistem çalışıyor" in m for m in n2.sent) == 0


# ---------------- store / data ----------------
def test_store_position_ops():
    p = store.empty_portfolio()
    p = store.add_position(p, "thyao", 100, 100.0, "2026-10-01", 90)
    p = store.add_position(p, "THYAO", 100, 120.0, "2026-10-20")
    pos = p["positions"][0]
    assert pos["adet"] == 200 and pos["maliyet"] == pytest.approx(110.0)
    assert pos["giris_tarihi"] == "2026-10-01" and pos["midas_stop"] == 90
    p = store.partial_sell(p, "THYAO", 50)
    assert p["positions"][0]["adet"] == 150
    with pytest.raises(ValueError):
        store.partial_sell(p, "THYAO", 999)
    p = store.set_midas_stop(p, "THYAO", 100)
    assert p["positions"][0]["midas_stop"] == 100
    p = store.partial_sell(p, "THYAO", 150)
    assert p["positions"] == []
    with pytest.raises(ValueError):
        store.add_position(p, "THY!", 10, 10, "2026-10-01")
    with pytest.raises(ValueError):
        store.add_position(p, "THYAO", 0, 10, "2026-10-01")


def test_normalize_settings_clamps():
    s = store.normalize_settings({"risk_yuzde": 50, "max_pozisyon": 99, "sermaye": -5, "toplam_risk_limiti_yuzde": "abc"})
    assert s["risk_yuzde"] == 5.0 and s["max_pozisyon"] == 10 and s["sermaye"] == 0.0
    assert s["toplam_risk_limiti_yuzde"] == config.VARSAYILAN_AYARLAR["toplam_risk_limiti_yuzde"]


def test_add_manual_news_validation():
    p = store.empty_portfolio()
    p = store.add_manual_news(p, "THYAO", "BILANCO", "OLUMLU", 9, "iyi", NOW_OPEN, olay_tarihi="2026-11-05")
    assert p["manuel_haber"][0]["onem"] == 5 and p["manuel_haber"][0]["olay_tarihi"] == "2026-11-05"
    with pytest.raises(ValueError):
        store.add_manual_news(p, "ZZZZZ", "GENEL", "OLUMLU", 3, "x", NOW_OPEN)
    with pytest.raises(ValueError):
        store.add_manual_news(p, "THYAO", "YOK", "OLUMLU", 3, "x", NOW_OPEN)


class FakeSession:
    """GitHub contents API sahtesi (sha çakışması dahil)."""
    def __init__(self):
        self.files, self.n, self.fail_next_put = {}, 0, 0

    def _resp(self, code, js=None, text=""):
        r = type("R", (), {})()
        r.status_code, r._js, r.text = code, js, text
        r.json = lambda: r._js
        r.raise_for_status = lambda: None if code < 400 else (_ for _ in ()).throw(RuntimeError(code))
        return r

    def get(self, url, headers=None, params=None, timeout=None):
        path = url.split("/contents/")[1]
        if path not in self.files:
            return self._resp(404)
        import base64
        content, sha = self.files[path]
        return self._resp(200, {"sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()})

    def put(self, url, headers=None, json=None, timeout=None):
        import base64
        path = url.split("/contents/")[1]
        if self.fail_next_put:
            self.fail_next_put -= 1
            return self._resp(409, text="conflict")
        cur = self.files.get(path)
        if cur and json.get("sha") != cur[1]:
            return self._resp(409, text="sha mismatch")
        self.n += 1
        sha = f"sha{self.n}"
        self.files[path] = (base64.b64decode(json["content"]), sha)
        return self._resp(200, {"content": {"sha": sha}})


def test_github_store_roundtrip_and_conflict_retry():
    s = FakeSession()
    gh = store.GitHubStore("u/r", "tok", session=s)
    assert gh.read("portfolio.json", {"x": 1}) == ({"x": 1}, None)
    gh.update("portfolio.json", lambda d: {"a": 1}, {}, "t")
    s.fail_next_put = 2     # iki çakışma, üçüncüde başarılı
    out = gh.update("portfolio.json", lambda d: {**d, "b": 2}, {}, "t")
    assert out == {"a": 1, "b": 2}
    assert gh.read("portfolio.json")[0] == {"a": 1, "b": 2}
    s.fail_next_put = 5
    with pytest.raises(RuntimeError):
        gh.update("portfolio.json", lambda d: d, {}, "t", retries=3)


def test_split_download_multiindex():
    a = make_prices(n=70, seed=1)
    b = make_prices(n=70, seed=2)
    raw = pd.concat({"AAA.IS": a, "BBB.IS": b}, axis=1)
    out = data.split_download(raw, ["AAA.IS", "BBB.IS", "CCC.IS"])
    assert set(out) == {"AAA.IS", "BBB.IS"}
    assert list(out["AAA.IS"].columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_notifier_splits_long_messages_and_survives_errors():
    long = "\n".join(["satır"] * 2000)
    assert all(len(p) <= notify.LIMIT for p in notify.split_message(long))

    def boom(*a, **k):
        raise RuntimeError("ağ yok")
    n = notify.Notifier(token="t", chat_id="c", http_post=boom)
    assert n.send("merhaba") is False


def test_grounding_failure_stops_after_first_attempt(tmp_path):
    class Client:
        def __init__(self):
            self.calls = 0
            self.models = self

        def generate_content(self, model, contents=None, config=None):
            self.calls += 1
            raise RuntimeError("400 grounding not available on free tier")
    c = Client()
    root = setup_root(tmp_path, positions=[
        {"symbol": "THYAO", "adet": 10, "maliyet": 100.0, "giris_tarihi": "2026-10-01"},
        {"symbol": "GARAN", "adet": 10, "maliyet": 100.0, "giris_tarihi": "2026-10-01"}])
    st = bot.run(root, now=NOW_CLOSE, deps=deps(client=c))
    # 1 başarısız arama + 1 sınıflandırma denemesi; 8 arama harcanmaz
    assert c.calls <= 2
    assert st["gemini"]["grounding"] == 1
    assert any("Arama doğrulaması" in w for w in st["son_calisma"]["uyarilar"])
