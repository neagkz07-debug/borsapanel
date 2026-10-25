import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

import streamlit as st  # noqa: E402

import bot  # noqa: E402
import data  # noqa: E402
import test_bot as tb  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for k in ("APP_PASSWORD", "GH_TOKEN", "GH_REPO", "PANEL_DEV", "LOCAL_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(data.YFinanceProvider, "history", lambda self, symbols: {})
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def make_root(tmp_path, with_bot=True):
    pf = {"settings": {"sermaye": 100000}, "manuel_haber": [],
          "positions": [{"symbol": "THYAO", "adet": 100, "maliyet": 100.0, "giris_tarihi": "2026-09-20",
                         "midas_stop": None}]}
    (tmp_path / "portfolio.json").write_text(json.dumps(pf), encoding="utf-8")
    if with_bot:
        bot.run(str(tmp_path), now=tb.NOW_CLOSE, deps=tb.deps())
    return str(tmp_path)


def test_gate_fails_closed_without_password():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any("APP_PASSWORD" in e.value for e in at.error)
    assert len(at.metric) == 0


def test_wrong_and_right_password(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "gizli123")
    monkeypatch.setenv("LOCAL_ROOT", make_root(tmp_path))
    monkeypatch.setattr("time.sleep", lambda s: None)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert at.text_input and len(at.metric) == 0
    at.text_input[0].set_value("yanlis").run()
    assert any("yanlış" in e.value for e in at.error)
    assert len(at.metric) == 0
    at.text_input[0].set_value("gizli123").run()
    assert not at.exception
    assert len(at.metric) >= 1


def test_dashboard_renders_with_bot_state(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_DEV", "1")
    monkeypatch.setenv("LOCAL_ROOT", make_root(tmp_path))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    headers = [h.value for h in at.header]
    assert "Portföy" in headers and "Bugünün adayları" in headers
    assert any("THYAO" in s.value for s in at.subheader)
    assert any("RISK_ON" in s.value or "NÖTR" in s.value or "RISK_OFF" in s.value for s in at.subheader)


def test_dashboard_without_bot_state(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_DEV", "1")
    monkeypatch.setenv("LOCAL_ROOT", make_root(tmp_path, with_bot=False))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Bot henüz çalışmadı" in w.value for w in at.warning)


def test_settings_required_when_capital_missing(monkeypatch, tmp_path):
    (tmp_path / "portfolio.json").write_text(json.dumps({"settings": {}, "positions": []}), encoding="utf-8")
    monkeypatch.setenv("PANEL_DEV", "1")
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("sermayeyi gir" in w.value for w in at.warning)


def test_add_position_form_writes_portfolio(monkeypatch, tmp_path):
    root = make_root(tmp_path, with_bot=False)
    monkeypatch.setenv("PANEL_DEV", "1")
    monkeypatch.setenv("LOCAL_ROOT", root)
    at = AppTest.from_file(APP, default_timeout=30).run()
    # "poz_ekle" formundaki alanlar
    sel = [s for s in at.selectbox if s.label == "Hisse" and "THYAO" in s.options][0]
    sel.set_value("GARAN")
    [n for n in at.number_input if n.label == "Lot Adedi"][0].set_value(50)
    [n for n in at.number_input if n.label == "Alış Maliyeti (TL)"][0].set_value(25.5)
    [b for b in at.button if b.label == "Portföye Kaydet"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    pf = json.loads(open(os.path.join(root, "portfolio.json"), encoding="utf-8").read())
    garan = [p for p in pf["positions"] if p["symbol"] == "GARAN"]
    assert garan and garan[0]["adet"] == 50 and garan[0]["maliyet"] == 25.5
