"""Sabit ayarlar, BIST 30 listesi, eşikler ve takvim.

Bu dosyadaki listeler elle bakım ister (README'deki "Bakım" bölümüne bak):
- BIST30: her çeyrekte Borsa İstanbul duyurusuyla karşılaştır.
- MAKRO_TAKVIM, TATIL_GUNLERI, BILANCO_PENCERELERI: yıl sonunda güncelle.
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Genel
# ---------------------------------------------------------------------------
TZ_NAME = "Europe/Istanbul"
# gemini-2.5-flash 16 Ekim 2026'da kapatıldı (Google duyurusu); güncel model adını
# AI Studio -> "Get API key" sayfasından / ai.google.dev model listesinden doğrula.
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_YEDEK_MODELLER = ["gemini-3.6-flash", "gemini-3.5-flash"]  # ana model 404 verirse sırayla denenir
GEMINI_ARAMA_DOGRULAMA = True
GROUNDING_GUNLUK_LIMIT = 8
GROUNDING_MODLARI = {"close"}  # arama doğrulaması hangi modlarda çalışsın

# ---------------------------------------------------------------------------
# BIST 30 (1 Ekim - 31 Aralık 2026 dönemi; TRMET girdi, DSTKF çıktı)
# ---------------------------------------------------------------------------
BIST30 = [
    "AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "EKGYO", "ENKAI", "EREGL",
    "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD", "MGROS", "PETKM",
    "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO",
    "TRALT", "TRMET", "TTKOM", "TUPRS", "VAKBN", "YKBNK",
]

# Haber sorgularında kullanılan şirket adları
SIRKET_ADI = {
    "AEFES": "Anadolu Efes",
    "AKBNK": "Akbank",
    "ASELS": "Aselsan",
    "ASTOR": "Astor Enerji",
    "BIMAS": "BİM Mağazalar",
    "EKGYO": "Emlak Konut",
    "ENKAI": "Enka İnşaat",
    "EREGL": "Ereğli Demir Çelik",
    "FROTO": "Ford Otosan",
    "GARAN": "Garanti Bankası",
    "GUBRF": "Gübre Fabrikaları",
    "ISCTR": "İş Bankası",
    "KCHOL": "Koç Holding",
    "KRDMD": "Kardemir",
    "MGROS": "Migros",
    "PETKM": "Petkim",
    "PGSUS": "Pegasus",
    "SAHOL": "Sabancı Holding",
    "SASA": "Sasa Polyester",
    "SISE": "Şişecam",
    "TAVHL": "TAV Havalimanları",
    "TCELL": "Turkcell",
    "THYAO": "Türk Hava Yolları",
    "TOASO": "Tofaş",
    "TRALT": "Türk Altın İşletmeleri",
    "TRMET": "TR Anadolu Metal Madencilik",
    "TTKOM": "Türk Telekom",
    "TUPRS": "Tüpraş",
    "VAKBN": "Vakıfbank",
    "YKBNK": "Yapı Kredi",
}

# Sektör limiti için (kullanıcı doğrulamalı)
SEKTOR = {
    "AEFES": "GIDA_ICECEK",
    "AKBNK": "BANKA",
    "ASELS": "SAVUNMA",
    "ASTOR": "ELEKTRIK_EKIPMAN",
    "BIMAS": "PERAKENDE",
    "EKGYO": "GAYRIMENKUL",
    "ENKAI": "INSAAT",
    "EREGL": "DEMIR_CELIK",
    "FROTO": "OTOMOTIV",
    "GARAN": "BANKA",
    "GUBRF": "KIMYA",
    "ISCTR": "BANKA",
    "KCHOL": "HOLDING",
    "KRDMD": "DEMIR_CELIK",
    "MGROS": "PERAKENDE",
    "PETKM": "KIMYA",
    "PGSUS": "HAVACILIK",
    "SAHOL": "HOLDING",
    "SASA": "KIMYA",
    "SISE": "SANAYI",
    "TAVHL": "HAVACILIK",
    "TCELL": "TELEKOM",
    "THYAO": "HAVACILIK",
    "TOASO": "OTOMOTIV",
    "TRALT": "MADENCILIK",
    "TRMET": "MADENCILIK",
    "TTKOM": "TELEKOM",
    "TUPRS": "ENERJI_RAFINERI",
    "VAKBN": "BANKA",
    "YKBNK": "BANKA",
}


def yahoo_kodu(sembol: str) -> str:
    return f"{sembol}.IS"


ENDEKS_KODLARI = ["XU030.IS", "XU100.IS"]  # ilki yoksa ikincisi
KUR_KODU = "USDTRY=X"

# ---------------------------------------------------------------------------
# Gösterge ve strateji eşikleri
# ---------------------------------------------------------------------------
VERI_PERIYODU = "1y"
MIN_BAR = 60

RSI_PERIYOT = 14
ATR_PERIYOT = 14
SMA_KISA = 20
SMA_UZUN = 50

R_ATR_CARPANI = 2.0       # R = 2 x ATR
R_MIN_ORAN = 0.04         # R, referans fiyatın %4'ünden az olamaz
R_MAX_ORAN = 0.10         # %10'undan çok olamaz
HEDEF_R_KATI = 2.0        # hedef = giriş + 2R

KAR_AL_RSI = 70.0
KAR_AL_MIN_KAR = 4.0      # RSI >= 70 ve kâr >= %4
EKLEME_RSI_ARALIK = (45.0, 62.0)
EKLEME_SMA20_UST_LIMIT = 0.03  # SMA20'nin en fazla %3 üzerinde
EKLEME_BEKLEME_GUN = 5

TREND_RSI_ARALIK = (45.0, 65.0)
TREND_SMA20_BANT = 0.03
TREND_MAX_5G_GETIRI = 8.0
TREND_MIN_HACIM_ORANI = 0.8
TREND_KIRILIM_HACIM_ORANI = 1.2
TREND_MIN_PUAN = 3

KATALIZOR_MAX_2G_GETIRI = 5.0
KATALIZOR_MAX_RSI = 72.0
KATALIZOR_MIN_HACIM_ORANI = 1.0
KATALIZOR_MIN_ONEM = 4

ADAY_MAX = 3
ADAY_MAX_NOTR = 2
ADAY_COOLDOWN_ISLEM_GUNU = 5

MIN_NET_RR = 1.5
KAYMA_YUZDE = 0.15        # her yön için
KOMISYON_YUZDE = 0.0      # Midas BIST komisyonu 0 (resmi destek sayfasına göre)

USDTRY_SOK_ESIGI = 2.0    # günlük % değişim

MIDAS_STOP_FARK_ESIGI = 1.5  # bot stopu midas_stop'tan en az %1,5 yüksekse hatırlat

# Varsayılan kullanıcı ayarları (panelden değişir)
VARSAYILAN_AYARLAR = {
    "sermaye": 0,
    "risk_yuzde": 2.0,
    "katalizor_risk_carpani": 0.5,
    "max_pozisyon": 5,
    "toplam_risk_limiti_yuzde": 6.0,
    "max_pozisyon_orani": 0.25,
    "max_sektor_pozisyon": 2,
}

# ---------------------------------------------------------------------------
# Haber
# ---------------------------------------------------------------------------
RSS_URL = "https://news.google.com/rss/search?q={q}&hl=tr&gl=TR&ceid=TR:tr"
RSS_SAAT = 48
RSS_HISSE_BASLIK = 8
RSS_GENEL_BASLIK = 5
RSS_BEKLEME_SN = 0.5

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_HISSE = 12
GDELT_BEKLEME_SN = 5.0

KUMELEME_ESIGI = 0.6

# Kaynak güveni: alan adı sonu -> katman. Kullanıcı doğrulamalı başlangıç listesi.
KAYNAK_GUVENI = {
    "T1": [
        "kap.org.tr", "aa.com.tr", "bloomberght.com", "reuters.com",
        "bloomberg.com", "dunya.com", "ekonomim.com", "tcmb.gov.tr",
        "borsaistanbul.com", "trthaber.com", "ft.com", "wsj.com",
    ],
    "T2": [
        "hurriyet.com.tr", "milliyet.com.tr", "sabah.com.tr", "ntv.com.tr",
        "cnnturk.com", "haberturk.com", "sozcu.com.tr", "cumhuriyet.com.tr",
        "cnbce.com", "milligazete.com.tr", "birgun.net", "yenisafak.com",
        "haberler.com", "investing.com", "foreks.com", "finansgundem.com",
        "paraanaliz.com", "borsagundem.com", "ekonomim.com.tr", "ensonhaber.com",
    ],
}
# Kelime/ifade; başlıkta TAM kelime olarak aranır ("Garanti BBVA" tetiklemez).
MANIPULASYON_KELIMELERI = [
    "uçacak", "patlayacak", "kaçırmayın", "garanti kazanç", "kesin kazanç",
    "kesin yükseliş", "bomba haber", "tavan serisi", "fırsatı kaçırma",
    "roket gibi", "servet kazan", "sakın satmayın",
]

# Gemini yokken (kota/hata) devreye giren temkinli kural: bu ifadeler güvenilir bir kümede
# geçerse olumsuz (önem 3) olay üretilir; veto üretmez, yalnızca puan düşürür.
NEGATIF_IFADELER = [
    "soruşturma", "iflas", "konkordato", "temerrüt", "işlem yasağı", "işlem sırası kapatıldı",
    "spk ceza", "kâr uyarısı", "zarar açıkladı", "dava açıldı", "el konuldu", "haciz",
]

OLAY_TURLERI = [
    "KAR_BILANCO", "TEMETTU", "BEDELSIZ_SERMAYE", "SOZLESME_SIPARIS",
    "YONETIM_ORTAKLIK", "DAVA_CEZA_SORUSTURMA", "KREDI_BORC", "MAKRO_SEKTOR",
    "OPERASYONEL", "DIGER",
]

# ---------------------------------------------------------------------------
# Takvim (doğrulanmalı; yıl sonunda güncelle)
# ---------------------------------------------------------------------------
# (tarih, tür, açıklama). tür: PPK_KARAR | RAPOR | OZET
MAKRO_TAKVIM = [
    (date(2026, 10, 22), "PPK_KARAR", "TCMB PPK faiz kararı"),
    (date(2026, 10, 30), "OZET", "PPK toplantı özeti"),
    (date(2026, 11, 12), "RAPOR", "TCMB Enflasyon Raporu"),
    (date(2026, 11, 27), "RAPOR", "TCMB Finansal İstikrar Raporu"),
    (date(2026, 12, 10), "PPK_KARAR", "TCMB PPK faiz kararı"),
    (date(2026, 12, 17), "OZET", "PPK toplantı özeti"),
    (date(2026, 12, 18), "RAPOR", "2027 Para Politikası Metni"),
    (date(2027, 1, 21), "PPK_KARAR", "TCMB PPK faiz kararı"),
]

# Borsa İstanbul'un kapalı olduğu hafta içi günler (resmi tatil). Doğrulanmalı.
TATIL_GUNLERI = {
    date(2026, 10, 29),  # Cumhuriyet Bayramı
    date(2027, 1, 1),    # Yılbaşı
}
YARIM_GUNLER = {date(2026, 10, 28)}  # arife: yarım gün seans

# Yaklaşık bilanço açıklama pencereleri (başlangıç, bitiş). Doğrulanmalı.
BILANCO_PENCERELERI = [
    (date(2026, 10, 15), date(2026, 11, 20)),
    (date(2027, 2, 15), date(2027, 3, 15)),
    (date(2027, 4, 20), date(2027, 5, 20)),
    (date(2027, 7, 20), date(2027, 8, 31)),
]
BILANCO_ONCESI_GUN = 3  # bilanço tarihi bilinen hissede açıklamaya 3 işlem günü kala giriş yok

# ---------------------------------------------------------------------------
# Çalışma modları (Istanbul saati)
# ---------------------------------------------------------------------------
MOD_OPEN_BASLANGIC = (9, 30)    # 09:30-11:29
MOD_INTRADAY_BASLANGIC = (11, 30)  # 11:30-17:29
MOD_CLOSE_BASLANGIC = (17, 30)  # 17:30 ve sonrası
PIYASA_ACILIS = (10, 0)

JOURNAL_MAX_KAYIT = 1000
MANUEL_HABER_MAX = 50
