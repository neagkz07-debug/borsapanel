"""Mobil panel (Streamlit). Alt alta kartlar; yatay sütun kullanılmaz.

Panel yalnızca portfolio.json yazar; state.json ve journal.json'u okur. Gemini'yi çağırmaz.
Gizli değerler Streamlit Secrets'tan gelir: APP_PASSWORD, GH_TOKEN, GH_REPO (ve isteğe bağlı GH_BRANCH).
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Optional

import streamlit as st

import config
import journal as jr
import news
import store
import utils
from utils import fmt_pct, fmt_tl

st.set_page_config(page_title="BIST30 Masası", page_icon="📈", layout="centered")

ETIKETLER = {
    "EKLEME_YAP": "GÜÇLÜ YÜKSELİŞ (ekleme fırsatı)", "TUT": "NORMAL / TUT", "KAR_AL": "KÂR AL",
    "STOP_OL": "STOP SEVİYESİNDE", "AL": "AL ADAYI", "BEKLE": "BEKLE",
    "HABER_RISKI": "HABER RİSKİ", "VERI_YOK": "VERİ YOK",
}
REJIM = {"RISK_ON": "🟢 RISK_ON", "NOTR": "🟡 NÖTR", "RISK_OFF": "🔴 RISK_OFF"}
YON = {"OLUMLU": "olumlu", "OLUMSUZ": "olumsuz", "BELIRSIZ": "belirsiz"}


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return st.secrets[name]
    except Exception:  # noqa: BLE001
        return os.environ.get(name, default)


def renk(x: Optional[float], metin: str) -> str:
    if x is None:
        return metin
    return f":green[{metin}]" if x >= 0 else f":red[{metin}]"


def kapi() -> None:
    """Şifre kapısı: doğru girilene kadar sayfa durur. Şifre tanımlı değilse kapalı kalır."""
    sifre = secret("APP_PASSWORD")
    if not sifre and os.environ.get("PANEL_DEV") == "1":
        return
    if not sifre:
        st.error("APP_PASSWORD tanımlı değil. Streamlit Secrets'a ekle.")
        st.stop()
    if st.session_state.get("giris"):
        return
    st.title("📈 BIST30 Masası")
    girilen = st.text_input("Şifre", type="password")
    if girilen:
        if girilen == sifre:
            st.session_state["giris"] = True
            st.rerun()
        time.sleep(2)
        st.error("Şifre yanlış.")
    st.stop()


@st.cache_resource
def get_store():
    repo, token = secret("GH_REPO"), secret("GH_TOKEN")
    if repo and token:
        return store.GitHubStore(repo, token, secret("GH_BRANCH", "main") or "main")
    return store.LocalStore(os.environ.get("LOCAL_ROOT", "."))


@st.cache_data(ttl=60, show_spinner=False)
def oku_bot_dosyasi(_depo, yol: str, varsayilan_anahtar: str):
    varsayilan = {"kayitlar": []} if yol == "journal.json" else {}
    return _depo.read(yol, varsayilan)[0] or varsayilan


@st.cache_data(ttl=120, show_spinner=False)
def canli_fiyatlar(semboller: tuple) -> dict:
    """Tek indirme çağrısıyla son kapanışlar (≈15 dk gecikmeli). Alınamayan sembol sözlükte yoktur."""
    if not semboller:
        return {}
    try:
        import data
        raw = data.YFinanceProvider(period="5d").history([config.yahoo_kodu(s) for s in semboller])
        return {s: float(raw[config.yahoo_kodu(s)]["Close"].iloc[-1]) for s in semboller
                if config.yahoo_kodu(s) in raw}
    except Exception:  # noqa: BLE001
        return {}


def kaydet(degistir, mesaj: str) -> bool:
    """portfolio.json'u okuyup değiştirip yazar; sha çakışmasında yeniden dener."""
    try:
        get_store().update("portfolio.json", lambda p: degistir(store.normalize_portfolio(p)),
                           store.empty_portfolio(), message=f"panel: {mesaj} ({utils.iso(utils.now_ist())})")
        st.cache_data.clear()
        return True
    except ValueError as e:
        st.error(str(e))
    except Exception as e:  # noqa: BLE001
        st.error(f"Kaydedilemedi: {e}")
    return False


def kanit_baglantilari(events: list[dict], limit: int = 3) -> str:
    satirlar, goruldu = [], set()
    for ev in events:
        for k in ev.get("evidence", []):
            if k["source"] in goruldu:
                continue
            goruldu.add(k["source"])
            etiket = f"{k['source']} ({k.get('tier', '?')})"
            satirlar.append(f"[{etiket}]({k['url']})" if k.get("url") else etiket)
            if len(satirlar) >= limit:
                return " · ".join(satirlar)
    return " · ".join(satirlar)


def olay_satiri(ev: dict) -> str:
    isaret = {"OLUMLU": "🟢", "OLUMSUZ": "🔴"}.get(ev["direction"], "⚪")
    guven = "güvenilir" if ev.get("trusted") else "doğrulanmamış"
    ozet = ev.get("summary") or (ev["evidence"][0]["title"] if ev.get("evidence") else "")
    return (f"{isaret} {ev['event_type']} · {YON.get(ev['direction'])} · önem {ev['severity']}/5 · "
            f"{ev.get('source_count', 0)} kaynak · {guven}" + (f"\n\n{ozet}" if ozet else ""))


# ---------------------------------------------------------------------------
# Bölümler
# ---------------------------------------------------------------------------
def serit(state: dict) -> None:
    rj = state.get("rejim") or {}
    with st.container(border=True):
        etiket = REJIM.get(rj.get("label"), "⚪ Rejim verisi yok")
        st.subheader(etiket)
        if rj.get("available"):
            st.caption(f"{rj['index']} {fmt_tl(rj['close'], 0)} · SMA20 {fmt_tl(rj['sma20'], 0)} · SMA50 {fmt_tl(rj['sma50'], 0)}")
        if rj.get("fx_shock"):
            st.error(f"Kur şoku: USDTRY günlük {fmt_pct(rj.get('usdtry_1d_pct'))}")
        for n in state.get("makro", [])[:3]:
            st.caption(f"📅 {n}")
        son = state.get("son_calisma") or {}
        z = utils.parse_iso(son.get("zaman"))
        if not z:
            st.warning("Bot henüz çalışmadı. GitHub → Actions → Run workflow ile ilk koşuyu başlat.")
        else:
            yas = utils.now_ist() - z
            st.caption(f"Son bot çalışması: {z.strftime('%d.%m %H:%M')} · {son.get('mod')} · {son.get('durum')}")
            if yas > timedelta(hours=26):
                st.warning("Bot 26 saatten uzun süredir çalışmadı.")
        for w in son.get("uyarilar", []):
            st.caption(f"⚠️ {w}")


def ayarlar_formu(portfolio: dict, zorunlu: bool) -> None:
    s = portfolio["settings"]
    kutu = st.container(border=True) if zorunlu else st.expander("⚙️ Ayarlar")
    with kutu:
        if zorunlu:
            st.warning("Önce stratejiye ayırdığın toplam sermayeyi gir (lot hesabı buna bağlı).")
        with st.form("ayarlar"):
            sermaye = st.number_input("Sermaye (TL)", min_value=0.0, value=float(s["sermaye"]), step=1000.0)
            risk_y = st.number_input("İşlem başına risk (%)", 0.1, 5.0, float(s["risk_yuzde"]), 0.1)
            kat = st.number_input("Katalizör risk çarpanı", 0.1, 1.0, float(s["katalizor_risk_carpani"]), 0.1)
            mx = st.number_input("Maksimum pozisyon", 1, 10, int(s["max_pozisyon"]), 1)
            toplam = st.number_input("Toplam açık risk limiti (%)", 1.0, 20.0, float(s["toplam_risk_limiti_yuzde"]), 0.5)
            oran = st.number_input("Tek pozisyon üst sınırı (sermayenin oranı)", 0.05, 1.0, float(s["max_pozisyon_orani"]), 0.05)
            sek = st.number_input("Sektör başına en fazla pozisyon", 1, 5, int(s["max_sektor_pozisyon"]), 1)
            if st.form_submit_button("Ayarları kaydet"):
                yeni = {"sermaye": sermaye, "risk_yuzde": risk_y, "katalizor_risk_carpani": kat, "max_pozisyon": mx,
                        "toplam_risk_limiti_yuzde": toplam, "max_pozisyon_orani": oran, "max_sektor_pozisyon": sek}

                def f(p):
                    p["settings"] = store.normalize_settings(yeni)
                    return p
                if kaydet(f, "ayarlar"):
                    st.rerun()


def portfoy_kartlari(portfolio: dict, state: dict) -> None:
    st.header("Portföy")
    pozlar = portfolio["positions"]
    if not pozlar:
        with st.container(border=True):
            st.write("Açık pozisyon yok.")
        return
    semboller = tuple(sorted(p["symbol"] for p in pozlar))
    canli = canli_fiyatlar(semboller)
    sinyal = {r["symbol"]: r for r in state.get("pozisyonlar", [])}
    toplam_deger = toplam_maliyet = 0.0
    for p in pozlar:
        fiyat = canli.get(p["symbol"]) or (sinyal.get(p["symbol"]) or {}).get("price")
        if fiyat:
            toplam_deger += fiyat * p["adet"]
            toplam_maliyet += p["maliyet"] * p["adet"]
    with st.container(border=True):
        st.metric("Toplam pozisyon değeri", f"{fmt_tl(toplam_deger)} TL")
        kz = toplam_deger - toplam_maliyet
        kzp = (kz / toplam_maliyet * 100) if toplam_maliyet else None
        st.metric("Toplam K/Z", f"{fmt_tl(kz)} TL", delta=fmt_pct(kzp) if kzp is not None else None)
        st.caption(f"Fiyatlar yaklaşık 15 dk gecikmeli · güncelleme {utils.now_ist().strftime('%H:%M')}")

    for p in pozlar:
        sym = p["symbol"]
        sg = sinyal.get(sym) or {}
        fiyat = canli.get(sym) or sg.get("price")
        with st.container(border=True):
            st.subheader(sym)
            if not fiyat:
                st.write("Fiyat alınamadı")
            else:
                kz_tl = (fiyat - p["maliyet"]) * p["adet"]
                kz_p = (fiyat / p["maliyet"] - 1) * 100
                st.write(f"Son fiyat **{fmt_tl(fiyat)}** · {p['adet']} adet · maliyet {fmt_tl(p['maliyet'])}")
                st.write(f"K/Z {renk(kz_p, fmt_pct(kz_p))} · {renk(kz_tl, fmt_tl(kz_tl) + ' TL')}")
            if sg.get("price"):
                st.markdown(f"**{ETIKETLER.get(sg.get('action'), sg.get('action'))}**")
                st.write(f"Bot stopu {fmt_tl(sg['stop'])} · hedef {fmt_tl(sg['target'])} · RSI {sg['rsi']:.0f} · {sg.get('trend', '')}")
                ms = p.get("midas_stop")
                st.write(f"Midas stopu: {fmt_tl(ms) if ms else 'girilmemiş'}")
                if sg.get("midas_note"):
                    st.warning(sg["midas_note"])
            else:
                st.caption("Bot henüz bu pozisyonu değerlendirmedi.")
            ns = (state.get("haber") or {}).get(sym)
            if ns:
                for ev in ns.get("events", [])[:3]:
                    st.markdown(olay_satiri(ev))
                k = kanit_baglantilari(ns.get("events", []))
                if k:
                    st.caption("Kaynaklar: " + k)
                g = ns.get("gdelt")
                if g and g.get("vol_z", 0) >= 2:
                    st.caption(f"📰 Haber hareketliliği yüksek (z={g['vol_z']}, ton değişimi {g.get('tone_delta')})")


def aday_kartlari(state: dict) -> None:
    st.header("Bugünün adayları")
    adaylar = state.get("adaylar") or []
    if not adaylar:
        with st.container(border=True):
            st.write("Aday yok.")
    for c in adaylar:
        with st.container(border=True):
            tur = "TREND" if c["type"] == "TREND" else "KATALİZÖR (yarım risk)"
            st.subheader(f"{c['symbol']} · {tur}")
            st.write(f"Kapanış {fmt_tl(c['close'])} · giriş bölgesi {fmt_tl(c['entry_zone'][0])}–{fmt_tl(c['entry_zone'][1])}")
            st.write(f"Stop {fmt_tl(c['stop_loss'])} · hedef {fmt_tl(c['target_price'])} · net R:R {fmt_tl(c['net_rr'])}")
            if c.get("lot"):
                st.write(f"Önerilen lot **{c['lot']}** · risk {fmt_tl(c['risk_tl'], 0)} TL · tutar {fmt_tl(c['notional'], 0)} TL")
            else:
                st.caption("Lot için sermaye gir.")
            ns = (state.get("haber") or {}).get(c["symbol"])
            if ns:
                for ev in ns.get("events", [])[:2]:
                    st.markdown(olay_satiri(ev))
                k = kanit_baglantilari(ns.get("events", []))
                if k:
                    st.caption("Kaynaklar: " + k)
    izleme = state.get("izleme") or []
    if izleme:
        with st.expander(f"İzleme listesi (BEKLE) · {len(izleme)}"):
            for w in izleme:
                st.write(f"**{w['symbol']}** ({w['type']}) — {w['reason']}")


def pozisyon_formlari(portfolio: dict) -> None:
    with st.expander("➕ Pozisyon ekle / güncelle"):
        with st.form("poz_ekle"):
            sym = st.selectbox("Hisse", config.BIST30)
            adet = st.number_input("Lot Adedi", min_value=1, value=100, step=1)
            maliyet = st.number_input("Alış Maliyeti (TL)", min_value=0.01, value=10.00, step=0.01, format="%.2f")
            tarih = st.date_input("Alış Tarihi", value=utils.now_ist().date())
            midas = st.number_input("Midas stop (TL, boşsa 0)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
            mod = st.radio("Hisse zaten portföydeyse", ["Ekle (ağırlıklı ortalama maliyet)", "Üzerine yaz"])
            if st.form_submit_button("Portföye Kaydet"):
                fn = store.add_position if mod.startswith("Ekle") else store.replace_position

                def f(p):
                    return fn(p, sym, int(adet), float(maliyet), tarih.isoformat(), float(midas) or None)
                if kaydet(f, f"{sym} kaydet"):
                    st.success("Kaydedildi.")
                    st.rerun()
        st.caption("Bedelsiz sermaye artırımı, birleşme veya bölünme sonrası adet ve maliyeti elle güncelle (Üzerine yaz).")

    if not portfolio["positions"]:
        return
    held = [p["symbol"] for p in portfolio["positions"]]
    with st.expander("➖ Kısmi sat / kapat"):
        with st.form("poz_sat"):
            sym = st.selectbox("Pozisyon", held, key="sat_sym")
            adet = st.number_input("Satılan lot", min_value=1, value=1, step=1)
            if st.form_submit_button("Kısmi Sat"):
                if kaydet(lambda p: store.partial_sell(p, sym, int(adet)), f"{sym} kısmi satış"):
                    st.success("Güncellendi.")
                    st.rerun()
        with st.form("poz_sil"):
            sym2 = st.selectbox("Pozisyon", held, key="sil_sym")
            onay = st.checkbox("Pozisyonu tamamen kapattığımı onaylıyorum")
            if st.form_submit_button("Pozisyonu Kapat/Sil"):
                if not onay:
                    st.error("Önce onay kutusunu işaretle.")
                elif kaydet(lambda p: store.remove_position(p, sym2), f"{sym2} kapat"):
                    st.rerun()
    with st.expander("🛑 Midas stop güncelle"):
        with st.form("midas"):
            sym3 = st.selectbox("Pozisyon", held, key="midas_sym")
            deger = st.number_input("Midas tetik fiyatı (TL, boşaltmak için 0)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
            if st.form_submit_button("Kaydet"):
                if kaydet(lambda p: store.set_midas_stop(p, sym3, float(deger) or None), f"{sym3} midas stop"):
                    st.rerun()


def haber_notu_formu(portfolio: dict) -> None:
    with st.expander("📰 Haber notu ekle (KAP Mobil)"):
        with st.form("haber"):
            adaylar = sorted(set(config.BIST30) | {p["symbol"] for p in portfolio["positions"]})
            sym = st.selectbox("Hisse", adaylar, key="haber_sym")
            tur = st.selectbox("Tür", store.MANUEL_TURLER)
            yon = st.radio("Yön", ["OLUMLU", "OLUMSUZ", "BELIRSIZ"], horizontal=False)
            onem = st.slider("Önem", 1, 5, 3)
            metin = st.text_area("Kısa not", max_chars=300)
            sure = st.number_input("Geçerlilik (saat)", 1, 336, 48)
            olay = st.date_input("Olay tarihi (bilanço için)", value=None)
            if st.form_submit_button("Notu ekle"):
                if not metin.strip():
                    st.error("Kısa not boş olamaz.")
                else:
                    ot = olay.isoformat() if olay else None
                    if kaydet(lambda p: store.add_manual_news(p, sym, tur, yon, onem, metin.strip(), utils.now_ist(),
                                                              ot, int(sure)), f"{sym} haber notu"):
                        st.success("Not eklendi.")
                        st.rerun()
        aktif = [m for m in portfolio["manuel_haber"]]
        if aktif:
            st.caption(f"{len(aktif)} aktif not")


def karne(journal_data: dict) -> None:
    with st.expander("🎯 Karne (ileriye dönük test)"):
        sc = jr.scorecard(journal_data)
        st.write(f"Toplam kayıt: {sc['toplam_kayit']} · tamamlanan: {sc['tamamlanan']}")
        if sc["uyari"]:
            st.warning(sc["uyari"])

        def blok(ad: str, a: dict) -> None:
            if not a.get("n"):
                st.caption(f"{ad}: kayıt yok")
                return
            st.write(f"**{ad}** · n={a['n']} · G1 {fmt_pct(a['g1_ort'])} · G3 {fmt_pct(a['g3_ort'])} · "
                     f"G5 {fmt_pct(a['g5_ort'])} · hedef/stop {a['hedef']}/{a['stop']} · belirsiz {a['belirsiz']} · yok {a['yok']}"
                     + (" · ⚠️ örnek az" if a["n"] < 30 else ""))
        blok("Alınabilir sinyaller", sc["alinabilir"])
        blok("Engellenen (veto)", sc["veto_edilen"])
        for k, v in sc["tur"].items():
            blok(k, v)
        for k, v in sc["rejim"].items():
            blok(f"Rejim {k}", v)
        for k, v in sc["olay_turu"].items():
            blok(f"Olay {k}", v)
        st.caption("Engellenen sinyaller belirgin kötüyse haber filtresi işe yarıyordur; fark yoksa gevşetilir.")


def saglik(state: dict) -> None:
    sg = state.get("saglik") or {}
    with st.expander("🩺 Sağlık"):
        if int(sg.get("ardisik_hata", 0)) >= 2:
            st.error(f"Bot art arda {sg['ardisik_hata']} kez hata verdi. GitHub Actions kaydına bak.")
        if sg.get("veri_eksik"):
            st.warning("Veri alınamayan: " + ", ".join(sg["veri_eksik"]))
        if sg.get("veri_bayat"):
            st.warning("Fiyat verisi güncel değil.")
        st.write(f"Haber kaynağı: {'çalışıyor' if sg.get('haber_ok', True) else 'ulaşılamadı'}")
        st.write(f"Yapay zekâ: {'çalışıyor' if sg.get('yapay_zeka_ok') else 'kapalı / kural modu'}")
        gem = state.get("gemini") or {}
        if gem:
            st.write(f"Arama doğrulaması bugün: {gem.get('grounding', 0)}/{config.GROUNDING_GUNLUK_LIMIT}")
        if sg.get("son_basarili"):
            st.caption(f"Son başarılı koşu: {sg['son_basarili'][:16].replace('T', ' ')}")


# ---------------------------------------------------------------------------
def main() -> None:
    kapi()
    depo = get_store()
    try:
        ham, _ = depo.read("portfolio.json", None)
    except Exception as e:  # noqa: BLE001
        st.error(f"portfolio.json okunamadı: {e}")
        st.stop()
    portfolio = store.normalize_portfolio(ham)
    # Süresi dolan manuel haberleri oturum başına bir kez temizle
    if not st.session_state.get("temizlendi"):
        st.session_state["temizlendi"] = True
        temiz = news.prune_manual(portfolio["manuel_haber"], utils.now_ist())
        if len(temiz) != len(portfolio["manuel_haber"]):
            def f(p):
                p["manuel_haber"] = news.prune_manual(p["manuel_haber"], utils.now_ist())
                return p
            if kaydet(f, "süresi dolan notları temizle"):
                portfolio["manuel_haber"] = temiz

    state = oku_bot_dosyasi(depo, "state.json", "s")
    journal_data = oku_bot_dosyasi(depo, "journal.json", "j")

    st.title("📈 BIST30 Masası")
    serit(state)
    sermaye_yok = float(portfolio["settings"]["sermaye"]) <= 0
    if sermaye_yok:
        ayarlar_formu(portfolio, zorunlu=True)
    portfoy_kartlari(portfolio, state)
    aday_kartlari(state)
    pozisyon_formlari(portfolio)
    haber_notu_formu(portfolio)
    if not sermaye_yok:
        ayarlar_formu(portfolio, zorunlu=False)
    karne(journal_data)
    saglik(state)
    st.caption("Karar destek aracıdır; yatırım tavsiyesi değildir. Emirler Midas'ta elle verilir.")


main()
