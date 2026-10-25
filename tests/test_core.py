from datetime import date

import numpy as np
import pandas as pd
import pytest

import config
import indicators as ind
import risk
import utils


def make_df(n=120, start=100.0, drift=0.002, vol=1_000_000, seed=1, noise=0.01):
    rng = np.random.default_rng(seed)
    rets = drift + rng.normal(0, noise, n)
    close = start * np.cumprod(1 + rets)
    high = close * 1.01
    low = close * 0.99
    idx = pd.bdate_range(end="2026-10-23", periods=n)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(n, float(vol))}, index=idx)


# ----------------------------- göstergeler ---------------------------------
def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0, abs=1e-6)
    r = ind.rsi(make_df()["Close"])
    assert r.dropna().between(0, 100).all()


def test_sma_and_atr_values():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert ind.sma(s, 3).iloc[-1] == pytest.approx(4.0)
    df = make_df(60, noise=0.0, drift=0.0)
    a = ind.atr(df["High"], df["Low"], df["Close"], 14)
    # sabit fiyat: TR = high-low = %2
    assert a.iloc[-1] == pytest.approx(100.0 * 0.02, rel=1e-6)


def test_snapshot_needs_enough_bars():
    assert ind.snapshot(make_df(30)) is None
    snap = ind.snapshot(make_df(120))
    assert snap is not None
    assert snap["trend"] in {"YUKSELIS", "YATAY", "DUSUS"}
    assert snap["date"] == date(2026, 10, 23)


def test_trend_classification():
    assert ind.classify_trend(110, 105, 100) == "YUKSELIS"
    assert ind.classify_trend(90, 95, 100) == "DUSUS"
    assert ind.classify_trend(100, 105, 100) == "YATAY"


def test_regime_labels():
    up = make_df(120, drift=0.004, noise=0.002)
    down = make_df(120, drift=-0.004, noise=0.002)
    assert ind.build_regime(up, None)["label"] == "RISK_ON"
    assert ind.build_regime(down, None)["label"] == "RISK_OFF"
    assert ind.build_regime(None, None)["label"] == "NOTR"


def test_fx_shock_flag():
    usd = pd.DataFrame({"Close": [30.0, 30.7]}, index=pd.bdate_range(end="2026-10-23", periods=2))
    reg = ind.build_regime(make_df(120), usd)
    assert reg["fx_shock"] is True


# --------------------------------- risk ------------------------------------
def test_compute_r_clamps():
    assert risk.compute_r(100, 1.0) == pytest.approx(4.0)   # 2 ATR=2 -> alt sınır %4
    assert risk.compute_r(100, 3.0) == pytest.approx(6.0)
    assert risk.compute_r(100, 10.0) == pytest.approx(10.0)  # üst sınır %10


def test_lot_example_trend_matches_spec():
    # Şartname 7.1: sermaye 100.000, risk %2, kapanış 48,10, stop 45,40
    s = risk.position_size(100_000, 2.0, 1.0, 48.10, 45.40, 0.25)
    assert s["lot"] == 519
    assert s["capped"] is True
    assert s["risk_tl"] == pytest.approx(1401.3, abs=0.1)


def test_lot_example_catalyst_matches_spec():
    s = risk.position_size(100_000, 2.0, 0.5, 48.10, 45.40, 0.25)
    assert s["lot"] == 370
    assert s["capped"] is False
    assert s["risk_tl"] == pytest.approx(999.0, abs=0.1)
    assert s["notional"] == pytest.approx(17797.0, abs=0.5)


def test_net_rr_example_matches_spec():
    assert risk.net_rr(48.10, 53.50, 45.40) == pytest.approx(1.85, abs=0.01)


def test_net_rr_zero_when_invalid():
    assert risk.net_rr(10, 12, 10) == 0.0


def test_fit_to_budget():
    s = risk.position_size(100_000, 2.0, 1.0, 48.10, 45.40, 1.0)  # 740 lot, ~2000 TL risk
    ok, why = risk.fit_to_budget(s, 1500)
    assert ok is not None and ok["lot"] == int(1500 // 2.7)
    ok2, why2 = risk.fit_to_budget(s, 500)
    assert ok2 is None and why2 == "RISK_BUTCESI_YETERSIZ"
    ok3, _ = risk.fit_to_budget(s, 5000)
    assert ok3["lot"] == s["lot"]


def test_open_risk():
    pos = [{"price": 110, "stop": 100, "adet": 10}, {"price": 90, "stop": 95, "adet": 50}]
    assert risk.open_risk_tl(pos) == pytest.approx(100.0)  # ikincisi stop altında -> 0


def test_position_levels_trailing_and_ratchet():
    lv = risk.position_levels(100.0, 2.0, 100.0)
    assert lv["R"] == pytest.approx(4.0)
    assert lv["stop"] == pytest.approx(96.0)
    assert lv["target"] == pytest.approx(108.0)
    lv2 = risk.position_levels(100.0, 2.0, 120.0)           # zirve 120 -> stop 116
    assert lv2["stop"] == pytest.approx(116.0)
    lv3 = risk.position_levels(100.0, 2.0, 105.0, prev_stop=116.0, prev_cost=100.0)
    assert lv3["stop"] == pytest.approx(116.0)              # aşağı inmez
    lv4 = risk.position_levels(100.0, 2.0, 105.0, prev_stop=116.0, prev_cost=90.0)
    assert lv4["stop"] == pytest.approx(101.0)              # maliyet değişti -> ratchet yok


BASE = dict(price=105.0, stop=96.0, target=108.0, rsi=55.0, pnl_pct=5.0, trend="YUKSELIS",
            sma20=104.0, mode="close", regime_label="RISK_ON", last_add_signal=None,
            today=date(2026, 10, 26))


def test_position_actions_priority():
    assert risk.decide_position_action(**{**BASE, "price": 95.0}) == "STOP_OL"
    assert risk.decide_position_action(**{**BASE, "price": 109.0}) == "KAR_AL"
    assert risk.decide_position_action(**{**BASE, "rsi": 72.0, "pnl_pct": 5.0}) == "KAR_AL"
    assert risk.decide_position_action(**BASE) == "EKLEME_YAP"
    assert risk.decide_position_action(**{**BASE, "mode": "intraday"}) == "TUT"
    assert risk.decide_position_action(**{**BASE, "regime_label": "NOTR"}) == "TUT"
    assert risk.decide_position_action(**{**BASE, "budget_ok": False}) == "TUT"
    assert risk.decide_position_action(**{**BASE, "last_add_signal": date(2026, 10, 23)}) == "TUT"
    assert risk.decide_position_action(**{**BASE, "last_add_signal": date(2026, 10, 12)}) == "EKLEME_YAP"


def test_macro_block_ppk():
    assert risk.macro_block_reason(date(2026, 12, 10)) is not None
    assert risk.macro_block_reason(date(2026, 12, 9)) is not None      # önceki işlem günü
    assert risk.macro_block_reason(date(2026, 12, 8)) is None
    assert risk.macro_block_reason(date(2026, 11, 12)) is None         # rapor günü: sadece not
    notes = risk.macro_notes(date(2026, 11, 5), 14)
    assert any("Enflasyon Raporu" in n for n in notes)


def test_bilanco_window_and_earnings_block():
    assert risk.in_bilanco_window(date(2026, 11, 1)) is True
    assert risk.in_bilanco_window(date(2026, 12, 1)) is False
    items = [{"symbol": "EREGL", "tur": "BILANCO", "olay_tarihi": "2026-10-28"}]
    assert risk.earnings_block("EREGL", items, date(2026, 10, 26)) is not None
    assert risk.earnings_block("THYAO", items, date(2026, 10, 26)) is None
    far = [{"symbol": "EREGL", "tur": "BILANCO", "olay_tarihi": "2026-11-20"}]
    assert risk.earnings_block("EREGL", far, date(2026, 10, 26)) is None


def test_sector_limit():
    assert risk.sector_has_room("GARAN", ["AKBNK"], 2) is True
    assert risk.sector_has_room("GARAN", ["AKBNK", "ISCTR"], 2) is False
    assert risk.sector_has_room("THYAO", ["AKBNK", "ISCTR"], 2) is True


def test_utils_formatting_and_days():
    assert utils.fmt_tl(1234.5) == "1.234,50"
    assert utils.fmt_pct(-3.66) == "-%3,7"
    assert utils.fmt_pct(3.66) == "%3,7"
    assert utils.is_trading_day(date(2026, 10, 29)) is False   # tatil
    assert utils.previous_trading_day(date(2026, 10, 30)) == date(2026, 10, 28)
    assert utils.trading_days_between(date(2026, 10, 23), date(2026, 10, 27)) == 2


# ----------------------------- aday seçimi ---------------------------------
def snap_stub(**kw):
    s = {"close": 100.0, "sma20": 99.0, "sma50": 95.0, "rsi": 55.0, "atr": 2.5,
         "prior_high20": 101.0, "breakout": False, "vol_ratio": 1.1, "ret2": 1.0,
         "ret5": 2.0, "trend": "YUKSELIS", "bars": 120, "date": date(2026, 10, 23)}
    s.update(kw)
    return s


SETTINGS = {"sermaye": 100_000, "risk_yuzde": 2.0, "max_pozisyon": 5,
            "toplam_risk_limiti_yuzde": 6.0, "max_pozisyon_orani": 0.25,
            "max_sektor_pozisyon": 2, "katalizor_risk_carpani": 0.5}
RISK_ON = {"label": "RISK_ON", "fx_shock": False}
TODAY = date(2026, 10, 26)


def run_select(snaps, **kw):
    args = dict(snapshots=snaps, held=set(), open_positions=[], settings=SETTINGS,
                regime=RISK_ON, today=TODAY, news={}, manual_items=[], cooldowns={})
    args.update(kw)
    return risk.select_candidates(**args)


def test_trend_score_full_and_threshold():
    ok, score = risk.trend_candidate_score(snap_stub())
    assert ok and score == 4
    ok, score = risk.trend_candidate_score(snap_stub(rsi=80.0, ret5=12.0))
    assert ok and score == 2                      # puan 3 altında -> aday olmaz
    ok, _ = risk.trend_candidate_score(snap_stub(trend="YATAY"))
    assert not ok


def test_select_accepts_trend_candidate_with_lot():
    res = run_select({"EREGL": snap_stub()})
    assert len(res["accepted"]) == 1
    c = res["accepted"][0]
    assert c["type"] == "TREND" and c["action"] == "AL"
    assert c["lot"] > 0 and c["stop_loss"] < c["close"] < c["target_price"]
    assert c["net_rr"] >= 1.5


def test_risk_off_blocks_everything_but_lists_watch():
    res = run_select({"EREGL": snap_stub()}, regime={"label": "RISK_OFF", "fx_shock": False})
    assert res["accepted"] == []
    assert res["watchlist"][0]["veto_kind"] == "REJIM"


def test_news_veto_blocks_candidate():
    res = run_select({"EREGL": snap_stub()}, news={"EREGL": {"veto": True}})
    assert res["accepted"] == []
    assert res["watchlist"][0]["veto_kind"] == "HABER"


def test_ppk_day_blocks_entries():
    res = run_select({"EREGL": snap_stub()}, today=date(2026, 12, 9))
    assert res["accepted"] == []
    assert res["watchlist"][0]["veto_kind"] == "TAKVIM"


def test_catalyst_candidate_half_risk():
    s = snap_stub(trend="YATAY", rsi=60.0)       # trend adayı değil
    nw = {"EREGL": {"catalyst": True, "top_event": {"severity": 4, "source_count": 2}}}
    res = run_select({"EREGL": s}, news=nw)
    assert len(res["accepted"]) == 1
    c = res["accepted"][0]
    assert c["type"] == "KATALIZOR"
    trend_res = run_select({"EREGL": snap_stub()})
    assert c["risk_tl"] < trend_res["accepted"][0]["risk_tl"]


def test_sector_limit_and_slots_in_selection():
    snaps = {s: snap_stub() for s in ["GARAN", "AKBNK", "ISCTR", "YKBNK"]}
    res = run_select(snaps, held=set())
    syms = [c["symbol"] for c in res["accepted"]]
    assert len(syms) == 2                               # banka sektörü limiti 2
    assert any("Sektör" in w["reason"] or "kapasite" in w["reason"] for w in res["watchlist"])


def test_notr_regime_halves_risk_and_caps_candidates():
    snaps = {s: snap_stub() for s in ["EREGL", "THYAO", "ASELS"]}
    res = run_select(snaps, regime={"label": "NOTR", "fx_shock": False})
    assert len(res["accepted"]) == 2
    full = run_select({"EREGL": snap_stub()})["accepted"][0]["risk_tl"]
    assert res["accepted"][0]["risk_tl"] < full


def test_cooldown_excludes_symbol():
    res = run_select({"EREGL": snap_stub()}, cooldowns={"EREGL": "2026-10-23"})
    assert res["accepted"] == [] and res["watchlist"] == []


def test_no_sermaye_returns_candidates_without_lot():
    res = run_select({"EREGL": snap_stub()}, settings={**SETTINGS, "sermaye": 0})
    assert res["accepted"][0]["lot"] is None


def test_budget_exhausted_eliminates():
    pos = [{"price": 110.0, "stop": 50.0, "adet": 100}]  # 6.000 TL açık risk = limit
    res = run_select({"EREGL": snap_stub()}, open_positions=pos, held={"THYAO"})
    assert res["accepted"] == []
    assert res["watchlist"][0]["veto_kind"] == "RISK"
