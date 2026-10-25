# BIST30 Masası — swing trade karar destek ve alarm sistemi

Telefondan yönetilen, **ücretsiz** servislerle çalışan bir karar destek masasıdır. **Emir göndermez;** alımı ve satımı Midas'ta sen yaparsın. Bot fiyatı, haberi ve riski izler, Telegram'dan alarm verir.

**Ne yapar:** BIST 30'u tarar (trend adayları + haber katalizörü adayları), açık pozisyonların stop/hedef/kâr-al/ekleme durumunu hesaplar, haberleri toplayıp güvenilirliğini tartar, lot ve risk bütçesini hesaplar, her sinyali ileriye dönük test günlüğüne yazar.
**Ne yapmaz:** otomatik emir, Midas bağlantısı, vergi hesabı, getiri vaadi. Haber katmanı geriye dönük test edilemez; bu yüzden gerçek sinyal kalitesi `Karne` bölümünde zamanla ölçülür (30'dan az kayıtta sonuçlar kesin değildir).

---

## 1. Dosyalar

| Dosya | Görevi |
|---|---|
| `bot.py` | Ana akış (GitHub Actions'ta çalışır) |
| `app.py` | Streamlit mobil paneli |
| `config.py` | BIST 30 listesi, eşikler, takvim, model adı |
| `indicators.py`, `risk.py` | Göstergeler, rejim, stop/hedef, lot, aday kuralları (saf fonksiyonlar) |
| `news.py`, `pipeline.py`, `llm.py` | Haber toplama, kümeleme, kaynak güveni, Gemini |
| `journal.py` | İleriye dönük test günlüğü ve karne |
| `data.py`, `store.py`, `notify.py`, `messages.py`, `utils.py` | Fiyat verisi, GitHub depolama, Telegram, mesaj metinleri, yardımcılar |
| `portfolio.json` | Pozisyonlar, ayarlar, haber notları (**panel yazar**) |
| `state.json`, `journal.json` | Bot çıktıları (**bot yazar**) |
| `.github/workflows/trade_check.yml` | Zamanlanmış çalışma (kökteki `trade_check.yml` aynı dosyanın görünür kopyasıdır) |
| `requirements.txt` | Bağımlılıklar |
| `tests/` | Birim ve entegrasyon testleri (isteğe bağlı; telefonda yüklemek şart değil) |

Üç JSON dosyasını her biri tek taraf yazdığı için çakışma olmaz.

## 2. Kurulum (hepsi telefondan)

1. **Dosyaları aç.** Zip'i telefonda aç (iPhone: Dosyalar'da zip'e dokun; Android: Files uygulaması). Klasörlerin içinden dosyaları görebilirsin.
2. **Private repo aç.** GitHub → New repository → ad: `bist30-trader` → **Private**.
3. **Dosyaları yükle.** Repo sayfasında `Add file → Upload files`; zip'ten çıkan **kök dizindeki tüm dosyaları** seç (`.py`, `.json`, `.txt`, `.md`, `trade_check.yml`) ve commit et. Mobil tarayıcı klasör seçemediği için `tests/` klasörünü atlayabilirsin. (iPhone Dosyalar uygulaması `.` ile başlayan klasörleri göstermez; bu yüzden workflow'un görünür bir kopyası kökte `trade_check.yml` adıyla durur.)
4. **Workflow'u doğru yere koy.** `Add file → Create new file`, ad kutusuna şunu yaz: `.github/workflows/trade_check.yml` (eğik çizgiler klasör açar). Kökteki `trade_check.yml` dosyasının içeriğini kopyalayıp yapıştır, commit et. Bu adım olmadan zamanlanmış çalışma başlamaz. *(Mobilde zorlanırsan tarayıcıda "Masaüstü sitesi" modunu aç.)*
5. **Telegram botu.** Telegram'da `@BotFather` → `/newbot` → token'ı not al. Yeni botuna bir mesaj yaz. Chat ID'ni `@userinfobot` ile öğren.
6. **Gemini anahtarı.** Google AI Studio'dan ücretsiz API anahtarı al.
7. **Actions Secrets.** Repo → Settings → Secrets and variables → Actions → New repository secret: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
8. **Panel için token.** GitHub → Settings → Developer settings → Fine-grained tokens: yalnızca bu repo, yalnızca **Contents: Read and write**.
9. **Paneli yayınla.** share.streamlit.io → New app → repo `bist30-trader`, ana dosya `app.py` → Advanced settings → Secrets:
   ```toml
   GH_TOKEN = "github_pat_..."
   GH_REPO = "kullanici/bist30-trader"
   APP_PASSWORD = "uzun-ve-benzersiz-bir-sifre"
   ```
   `APP_PASSWORD` tanımlı değilse panel hiç açılmaz (güvenli varsayılan).
10. **İlk test.** Repo → Actions → *BIST30 Bot* → Run workflow → `dry_run` işaretli, `mode = close`, `force` işaretli. Log'da rejim, haber ve aday satırlarını gör; Telegram'a mesaj gitmemeli. Sonra `dry_run` olmadan bir kez çalıştır: Telegram'a akşam özeti gelmeli.
11. **Panel.** Adresi telefonda aç → *Ayarlar*'dan sermayeni gir → menüden "Ana Ekrana Ekle".
12. **KAP Mobil.** BIST 30 şirketlerini takip listesine ekle, anlık uyarıyı aç. Önemli bildirimi panelde *Haber notu ekle* ile işle (KAP'ın resmî API'si aboneliklidir; ücretsiz yol bu).
13. **Midas.** Her alımdan sonra Zarar Durdur emrini gir, tetik fiyatını panele yaz. Bot kendi stopunu esas alır; Midas stopu yalnızca karşılaştırma ve hatırlatma içindir.

## 3. Çalışma saatleri ve modlar (Türkiye saati)

| Saat | Mod | Ne yapar |
|---|---|---|
| 10:15 | `open` | Pozisyonlar + haber katmanı (+ Pazartesi sağlık satırı) |
| 12:30, 14:30, 16:30 | `intraday` | Yalnızca kural tabanlı alarmlar (stop/kâr-al); ağ haberi çekmez |
| 18:15 | `close` | Pozisyonlar + BIST 30 taraması + tam haber + günlük + akşam özeti |

GitHub zamanlayıcısı 5–30 dk gecikebilir; mod saat aralığına göre belirlendiği için sorun olmaz. Manuel çalıştırmada `mode` ile zorlanabilir.

## 4. Telegram mesajları ve tekrar kuralı

- **STOP** her koşuda tekrar eder (fiyat stopun altında kaldıkça).
- **KÂR AL / EKLEME** aynı durum için bir kez; durum değişince yeniden.
- **HABER RİSKİ** olay başına bir kez.
- **AL ADAYI** ve **akşam özeti** günde bir kez. **Hata** uyarısı günde bir kez.

## 5. Önemli stratejik kurallar (özet)

- Risk: işlem başına %2 (katalizör girişte yarısı, rejim NÖTR iken ayrıca yarısı); toplam açık risk limiti %6; en fazla 5 pozisyon; tek pozisyon sermayenin en fazla %25'i; sektör başına en fazla 2.
- Stop = maliyet − R (R = 2×ATR, %4–%10 aralığına sıkıştırılır); trailing stop yalnızca yükselir. Hedef = +2R. Net R:R (0,15% kayma dahil) 1,5'in altındaysa aday elenir.
- Rejim: `XU030.IS` (yoksa `XU100.IS`). RISK_OFF'ta yeni alım yok; kur şoku (USDTRY günlük ≥%2) ve PPK günü/önceki işlem günü alımı durdurur.
- Haber: güvenilir olumsuz haber (önem ≥4) adayı eler; yalnızca zayıf kaynaklı haber önem 2'yi geçemez; Gemini yalnızca temkinli yönde etkili olur ve **hiçbir zaman adet, maliyet, sermaye, K/Z veya Midas stopu görmez** (testle doğrulanır).

## 6. Doğrulama durumu — dürüst not

**Doğrulananlar (bu ortamda):** 89 otomatik test geçti. Göstergeler, lot hesabı (519 ve 370 lot örnekleri), net R:R, rejim, stop mandalı, aday kuralları, haber kümeleme ve kaynak güveni, Gemini yanıt doğrulaması (kanıtsız olay atılır), günlük ölçümü, Telegram tekrar kuralları, tatil atlama, bayat veri, hata uyarısı, GitHub depolama çakışması yeniden denemesi ve panelin Streamlit test aracıyla açılması sahte veriyle denendi.

**Bu ortamda canlı denenemeyenler:** Yahoo Finance (yfinance), Google News RSS, GDELT, Gemini, Telegram ve GitHub API'sine bu ortamdan erişim yok. Bu yüzden ilk `dry_run` koşusunu mutlaka kontrol et ve log'daki `WARNING` satırlarına bak.

**Belgelerden kontrol edilenler:**
- **Gemini modeli:** `gemini-2.5-flash` Google tarafından Ekim 2026'da kapatıldı (16 Ekim). `config.py` içinde `GEMINI_MODEL = "gemini-3.8-flash"` ve yedekler (`gemini-3.6-flash`, `gemini-3.5-flash`) var; ana model 404 verirse yedeğe geçilir. Model adlarını AI Studio'dan teyit et.
- **Arama doğrulaması (grounding):** Güncel fiyat sayfasında Gemini 3.x Flash için ücretsiz katmanda *kullanılamaz* görünüyor. Bot bir kez dener; başarısızsa o koşuda bırakır ve uyarı yazar. Haber akışı grounding olmadan da çalışır. (İstersen `GEMINI_ARAMA_DOGRULAMA = False` yap.)
- **GDELT:** `mode=TimelineVol` / `TimelineTone` ve "5 saniyede bir istek" sınırı belgelerle uyumlu; `timespan=7d` ve yanıt şeması topluluk belgelerine dayanıyor. GDELT hata verirse haber yalnızca RSS ile devam eder; GDELT karar vermez, bilgi notudur.
- **`XU030.IS`:** Yahoo'daki varlığı doğrulanamadı. Yoksa `XU100.IS`, o da yoksa rejim NÖTR kabul edilir ve panelde uyarı görünür.

## 7. Bakım

- **BIST 30 listesi:** her çeyrekte (Ocak, Nisan, Temmuz, Ekim) Borsa İstanbul duyurusuyla karşılaştır; `config.py` → `BIST30`, `SIRKET_ADI`, `SEKTOR`.
- **Takvim:** `MAKRO_TAKVIM`, `TATIL_GUNLERI`, `YARIM_GUNLER`, `BILANCO_PENCERELERI` her yıl sonunda güncellenir (şu an Ocak 2027'ye kadar).
- **Bedelsiz/bölünme/birleşme:** adet ve maliyeti panelde *Üzerine yaz* ile elle düzelt; kesim gününde stop alarmı yanıltıcı olabilir.
- **Eşikler:** Karnede en az 30 tamamlanmış kayıt birikmeden değiştirme; tek kötü haftaya göre ayar yapma.
- **Token şüphesi:** GitHub'dan token'ı iptal et, yenisini Streamlit Secrets'a yaz.
- **Zamanlayıcı:** Depoda 60 gün hareket olmazsa GitHub zamanlanmış işleri durdurur; bot her koşuda commit attığı için normalde sorun olmaz.
- **Actions sürümleri:** `actions/checkout`, `setup-python` için uyarı çıkarsa sürüm numarasını artır.

## 8. Bilinen sınırlar

- Yahoo verisi ~15 dk gecikmelidir ve zaman zaman boş döner; veri bayatsa aday taraması atlanır ve uyarı gelir.
- İş günü içi (`intraday`) kontroller yalnızca fiyat/stop kuralıdır; haber akışı sabah ve akşam çalışır.
- Arife (yarım gün) ve resmî tatiller `config.py` tablosundadır; Borsa İstanbul duyurularıyla teyit et.
- Haber güveni kaynak listesiyle (T1/T2/T3) ölçülür; liste `config.py` → `KAYNAK_GUVENI`.
- Gemini'ye yalnızca hisse kodu, şirket adı ve haber başlıkları gider; hangi hisseleri izlediğin dolaylı olarak anlaşılabilir.
- Bu bir karar destek aracıdır; yatırım tavsiyesi değildir. Kayıplar senin sorumluluğundadır.

## 9. Testleri çalıştırmak (isteğe bağlı)

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 10. Sorun giderme

| Belirti | Bak |
|---|---|
| Panel "APP_PASSWORD tanımlı değil" diyor | Streamlit Secrets'a `APP_PASSWORD` ekle |
| Panel "portfolio.json okunamadı" | `GH_REPO` (`kullanici/repo`) ve token yetkisi (Contents: Read and write) |
| Telegram mesajı gelmiyor | Secrets adları, bota önce mesaj yazdın mı, `dry_run` kapalı mı |
| "Veri güncel değil" | Yahoo geçici sorunu; sonraki koşuda düzelir. Sürerse log'a bak |
| "Yapay zekâ sınıflandırması yapılamadı" | API anahtarı/kota/model adı; bot kural yedeğiyle devam eder |
| Workflow push hatası | Settings → Actions → General → Workflow permissions: *Read and write* |
