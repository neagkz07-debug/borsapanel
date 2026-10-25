"""Veri katmanı: portfolio.json düzenleme kuralları ve GitHub/yerel depolama.

Bot yalnızca state.json ve journal.json yazar; portfolio.json'u yalnızca panel yazar.
Böylece iki taraf aynı dosyada çakışmaz. Panel yazarken sha çakışması olursa yeniden dener.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional

import requests

import config
import utils

SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,6}$")


# ---------------------------------------------------------------------------
# Ayar / portföy doğrulama (saf fonksiyonlar)
# ---------------------------------------------------------------------------
def _num(v: Any, default: float) -> float:
    try:
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_settings(s: Optional[dict]) -> dict:
    d = config.VARSAYILAN_AYARLAR
    s = s or {}
    return {
        "sermaye": _clamp(_num(s.get("sermaye"), d["sermaye"]), 0.0, 1e12),
        "risk_yuzde": _clamp(_num(s.get("risk_yuzde"), d["risk_yuzde"]), 0.1, 5.0),
        "katalizor_risk_carpani": _clamp(_num(s.get("katalizor_risk_carpani"), d["katalizor_risk_carpani"]), 0.1, 1.0),
        "max_pozisyon": int(_clamp(_num(s.get("max_pozisyon"), d["max_pozisyon"]), 1, 10)),
        "toplam_risk_limiti_yuzde": _clamp(_num(s.get("toplam_risk_limiti_yuzde"), d["toplam_risk_limiti_yuzde"]), 1.0, 20.0),
        "max_pozisyon_orani": _clamp(_num(s.get("max_pozisyon_orani"), d["max_pozisyon_orani"]), 0.05, 1.0),
        "max_sektor_pozisyon": int(_clamp(_num(s.get("max_sektor_pozisyon"), d["max_sektor_pozisyon"]), 1, 5)),
    }


def empty_portfolio() -> dict:
    return {"settings": normalize_settings(None), "positions": [], "manuel_haber": []}


def normalize_portfolio(p: Optional[dict]) -> dict:
    p = copy.deepcopy(p) if isinstance(p, dict) else {}
    out = {
        "settings": normalize_settings(p.get("settings")),
        "positions": [],
        "manuel_haber": [m for m in (p.get("manuel_haber") or []) if isinstance(m, dict)],
    }
    for pos in p.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        sym = str(pos.get("symbol", "")).upper().replace(".IS", "").strip()
        adet = _num(pos.get("adet"), 0)
        maliyet = _num(pos.get("maliyet"), 0)
        if not SYMBOL_RE.match(sym) or adet <= 0 or maliyet <= 0:
            continue
        out["positions"].append({
            "symbol": sym, "adet": int(adet), "maliyet": round(maliyet, 4),
            "giris_tarihi": str(pos.get("giris_tarihi") or "")[:10] or None,
            "midas_stop": _num(pos.get("midas_stop"), 0) or None,
            "not": str(pos.get("not", ""))[:200],
        })
    return out


def validate_position_input(symbol: str, adet: Any, maliyet: Any) -> tuple[bool, str]:
    sym = (symbol or "").upper().replace(".IS", "").strip()
    if not SYMBOL_RE.match(sym):
        return False, "Hisse kodu geçersiz (örn. THYAO)."
    a, m = _num(adet, 0), _num(maliyet, 0)
    if a <= 0 or a != int(a):
        return False, "Adet pozitif tam sayı olmalı."
    if m <= 0:
        return False, "Maliyet pozitif olmalı."
    return True, ""


# ---------------------------------------------------------------------------
# Portföy işlemleri (saf; yeni sözlük döndürür)
# ---------------------------------------------------------------------------
def add_position(portfolio: dict, symbol: str, adet: int, maliyet: float, tarih: str,
                 midas_stop: Optional[float] = None) -> dict:
    """Aynı hisse varsa ağırlıklı ortalama maliyetle birleştirir (ilk giriş tarihi korunur)."""
    ok, msg = validate_position_input(symbol, adet, maliyet)
    if not ok:
        raise ValueError(msg)
    p = normalize_portfolio(portfolio)
    sym = symbol.upper().replace(".IS", "").strip()
    adet, maliyet = int(adet), float(maliyet)
    for pos in p["positions"]:
        if pos["symbol"] == sym:
            toplam = pos["adet"] + adet
            pos["maliyet"] = round((pos["adet"] * pos["maliyet"] + adet * maliyet) / toplam, 4)
            pos["adet"] = toplam
            if midas_stop:
                pos["midas_stop"] = float(midas_stop)
            return p
    p["positions"].append({"symbol": sym, "adet": adet, "maliyet": round(maliyet, 4),
                           "giris_tarihi": tarih[:10], "midas_stop": midas_stop or None, "not": ""})
    return p


def replace_position(portfolio: dict, symbol: str, adet: int, maliyet: float, tarih: str,
                     midas_stop: Optional[float] = None) -> dict:
    """"Üzerine yaz": mevcut pozisyonu verilen değerlerle değiştirir (tarih de yenilenir)."""
    ok, msg = validate_position_input(symbol, adet, maliyet)
    if not ok:
        raise ValueError(msg)
    p = remove_position(portfolio, symbol)
    return add_position(p, symbol, adet, maliyet, tarih, midas_stop)


def partial_sell(portfolio: dict, symbol: str, adet: int) -> dict:
    p = normalize_portfolio(portfolio)
    sym = symbol.upper()
    for pos in p["positions"]:
        if pos["symbol"] == sym:
            if adet <= 0 or adet > pos["adet"]:
                raise ValueError("Satış adedi mevcut adetten fazla olamaz.")
            pos["adet"] -= int(adet)
            if pos["adet"] == 0:
                p["positions"].remove(pos)
            return p
    raise ValueError("Pozisyon bulunamadı.")


def remove_position(portfolio: dict, symbol: str) -> dict:
    p = normalize_portfolio(portfolio)
    p["positions"] = [x for x in p["positions"] if x["symbol"] != symbol.upper()]
    return p


def set_midas_stop(portfolio: dict, symbol: str, value: Optional[float]) -> dict:
    p = normalize_portfolio(portfolio)
    for pos in p["positions"]:
        if pos["symbol"] == symbol.upper():
            pos["midas_stop"] = float(value) if value and value > 0 else None
            return p
    raise ValueError("Pozisyon bulunamadı.")


MANUEL_TURLER = ["BILANCO", "TEMETTU", "GENEL"]


def add_manual_news(portfolio: dict, symbol: str, tur: str, yon: str, onem: int, metin: str,
                    zaman: datetime, olay_tarihi: Optional[str] = None, gecerlilik_saat: int = 48) -> dict:
    sym = symbol.upper().replace(".IS", "").strip()
    if sym not in config.BIST30 and not any(x["symbol"] == sym for x in normalize_portfolio(portfolio)["positions"]):
        raise ValueError("Hisse BIST 30 listesinde ya da portföyde değil.")
    if tur not in MANUEL_TURLER or yon not in ("OLUMLU", "OLUMSUZ", "BELIRSIZ"):
        raise ValueError("Tür/yön geçersiz.")
    p = normalize_portfolio(portfolio)
    item = {"symbol": sym, "tarih": utils.iso(zaman), "tur": tur, "yon": yon,
            "onem": int(_clamp(_num(onem, 3), 1, 5)), "not": (metin or "")[:300],
            "gecerlilik_saat": int(_clamp(_num(gecerlilik_saat, 48), 1, 24 * 14))}
    if olay_tarihi:
        item["olay_tarihi"] = olay_tarihi[:10]
    p["manuel_haber"].append(item)
    p["manuel_haber"] = p["manuel_haber"][-config.MANUEL_HABER_MAX:]
    return p


# ---------------------------------------------------------------------------
# Depolama
# ---------------------------------------------------------------------------
class Conflict(Exception):
    pass


class LocalStore:
    """Geliştirme/test için dizin tabanlı depo (GitHubStore ile aynı arayüz)."""

    def __init__(self, root: str):
        self.root = root

    def read(self, path: str, default: Any = None) -> tuple[Any, Optional[str]]:
        full = os.path.join(self.root, path)
        data = utils.read_json(full, default)
        return data, (str(os.path.getmtime(full)) if os.path.exists(full) else None)

    def write(self, path: str, data: Any, sha: Optional[str], message: str = "") -> Optional[str]:
        full = os.path.join(self.root, path)
        utils.write_json_atomic(full, data)
        return str(os.path.getmtime(full))

    def update(self, path: str, mutate: Callable[[Any], Any], default: Any, message: str = "", retries: int = 3) -> Any:
        data, sha = self.read(path, default)
        new = mutate(data)
        self.write(path, new, sha, message)
        return new


class GitHubStore:
    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, branch: str = "main", session: Optional[Any] = None):
        self.repo, self.token, self.branch = repo, token, branch
        self.s = session or requests.Session()

    def _headers(self, raw: bool = False) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _url(self, path: str) -> str:
        return f"{self.API}/repos/{self.repo}/contents/{path}"

    def read(self, path: str, default: Any = None) -> tuple[Any, Optional[str]]:
        r = self.s.get(self._url(path), headers=self._headers(), params={"ref": self.branch}, timeout=20)
        if r.status_code == 404:
            return default, None
        r.raise_for_status()
        js = r.json()
        sha = js.get("sha")
        if js.get("encoding") == "base64" and js.get("content"):
            text = base64.b64decode(js["content"]).decode("utf-8")
        else:  # 1 MB üstü dosyalar: ham içerik
            rr = self.s.get(self._url(path), headers=self._headers(raw=True),
                            params={"ref": self.branch}, timeout=30)
            rr.raise_for_status()
            text = rr.text
        try:
            return json.loads(text), sha
        except json.JSONDecodeError:
            return default, sha

    def write(self, path: str, data: Any, sha: Optional[str], message: str = "panel güncelleme") -> Optional[str]:
        body = {"message": message, "branch": self.branch,
                "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode()}
        if sha:
            body["sha"] = sha
        r = self.s.put(self._url(path), headers=self._headers(), json=body, timeout=30)
        if r.status_code in (409, 422):
            raise Conflict(r.text[:200])
        r.raise_for_status()
        return r.json().get("content", {}).get("sha")

    def update(self, path: str, mutate: Callable[[Any], Any], default: Any,
               message: str = "panel güncelleme", retries: int = 3) -> Any:
        last: Optional[Exception] = None
        for _ in range(retries):
            data, sha = self.read(path, default)
            new = mutate(data)
            try:
                self.write(path, new, sha, message)
                return new
            except Conflict as e:
                last = e
        raise RuntimeError(f"Kaydedilemedi (çakışma): {last}")
