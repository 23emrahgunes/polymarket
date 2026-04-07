# Ghost Trader v1.0 👻🤖

## Projeye Genel Bakış (Project Overview)

**Ghost Trader v1.0**, **Polymarket** tahmin piyasaları ile **Binance Futures** üzerindeki gerçek zamanlı fiyatlar arasındaki fiyat farklarını tespit etmek ve bunlardan yararlanmak için tasarlanmış otomatik bir Python botudur.

Bot, Binance ticker güncellemelerinden elde edilen **Geçmiş Oynaklık (Historical Volatility)** verilerini ve olasılık değerlendirmesi için **Black-Scholes modelini** kullanarak, bir Polymarket hissesinin matematiksel olasılığına ("Edge") göre ne zaman "yanlış fiyatlandırıldığını" belirler. İşlemler, yüksek doğruluklu bir **Paper Trading** ortamında yürütülmeden önce nihai bir güven puanı oluşturmak için **Claude 3.5 Sonnet** tarafından ikincil bir analize tabi tutulur.

---

## Mimari (Architecture)

Bot, modüler ve asenkron bir yapıda tasarlanmıştır:

- **`src/scanner.py`**: Binance WebSocket'leri için `ccxt.pro` ve Polymarket piyasa keşfi için `py-clob-client` kullanan yüksek frekanslı bir piyasa tarayıcısıdır.
- **`src/logic.py`**: Aşağıdakileri hesaplayan "Matematik Motoru"dur:
  - 24 saatlik 1 dakikalık ticker verilerinden **Geçmiş Oynaklık (Historical Volatility - HV)**.
  - Black-Scholes risk nötr modelini kullanarak **İma Edilen Olasılık (Implied Probability)**.
  - Teknik sinyal onayı için **RSI (14)**.
- **`src/brain.py`**: Güven puanı oluşturmak için Claude 3.5 Sonnet ile entegre olan AI modülüdür (v1.0 için simüle edilmiştir).
- **`src/trading.py`**: **1.000$ sanal bakiye**, `asyncio.Lock` aracılığıyla "Double Spending" önleme ve otomatik sonuç (Resolution) takibi özelliklerine sahip sağlam bir **Paper Trading** modülüdür.
- **`src/database.py`**: İşlem geçmişini ve cüzdan bakiyesini saklamak için **SQLite (`aiosqlite`)** kullanan bir veri saklama (**Persistence**) katmanıdır.
- **`src/main.py`**: Tüm bileşenleri bir `asyncio` olay döngüsünde birbirine bağlayan merkezi düzenleyicidir.

---

## Kurulum (Installation)

1. **Depoyu kopyalayın (Clone):**
   ```bash
   git clone <repository_url>
   cd ghost-trader
   ```

2. **Sanal ortam oluşturun (Virtual Environment):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows üzerinde: venv\Scripts\activate
   ```

3. **Bağımlılıkları yükleyin:**
   ```bash
   pip install -r requirements.txt
   ```

---

## Yapılandırma (Configuration)

Bot, yapılandırma için ortam değişkenlerini kullanır. Örnek dosyayı temel alarak bir `.env` dosyası oluşturun:

```bash
cp .env.example .env
```

**Temel Parametreler:**
- `VIRTUAL_BALANCE`: Paper Trading cüzdanı için başlangıç tutarı (1000$).
- `TRADE_SIZE_FIXED`: İşlem başına harcanan tutar (50$).
- `EDGE_THRESHOLD`: Bir işlemi tetiklemek için gereken minimum fiyat farkı (%5 = 0.05).
- `CONFIDENCE_THRESHOLD`: İşlem yürütmek için gereken minimum AI güven puanı (0.7).

---

## Çalıştırma (How to Run)

### Botu Başlatın
Bot, piyasaları sürekli tarayan uzun ömürlü bir süreç olarak çalışır.
```bash
python3 -m src.main
```

### Otomatik Testleri Çalıştırın
Tüm çekirdek modüllerin birim testleri için `pytest` kullanıyoruz.
```bash
pytest -q
```

### Doğrulama Senaryoları (Verification Scripts)
Botun API bağlantılarını ve veri akışını doğrulamak için yardımcı scriptler mevcuttur:
- `python3 src/check_api.py`: Gamma ve CLOB API bağlantılarını test eder.
- `python3 src/verify_stream.py`: Polymarket global işlem akışını terminale yazdırır.
- `python3 tests/final_check_v3.py`: En son mimariyi (Whale Tracker vb.) doğrular.
- `python3 scripts/verify_runtime_trade.py`: Runtime sinyal yolunu ve veritabanı kaydını doğrular.

---

## Docker Desteği (Docker Support)

Sistem yöneticileri için, Ghost Trader'ı stabilite ve veri kalıcılığı sağlamak amacıyla konteyner içinde çalıştırabilirsiniz.

1. **İmajı oluşturun (Build):**
   ```bash
   docker build -t ghost-trader .
   ```

2. **Konteyneri çalıştırın:**
   ```bash
   docker run -d --name ghost-bot \
     -v $(pwd)/data:/app/data \
     --env-file .env \
     ghost-trader

### Konfigürasyon Güncelleme ve Yeniden Başlatma (Updating Config & Restarting)
Yeni strateji ayarlarını (örneğin slippage limitleri) aktif etmek için konteyneri şu komutla yeniden başlatın:
```bash
docker restart ghost-bot
```
   ```
*`-v` bayrağı, konteyner silinse bile `ghost_trader.db` dosyasının ana makinenizde kalıcı (**Persistence**) olmasını sağlar.*

---

## Veritabanı ve Günlükler (Database & Logs)

Bot, yapılandırılmış işlem geçmişini **`data/ghost_trader.db`** dosyasında tutar. Herhangi bir SQLite görüntüleyici veya komut satırı kullanarak işlem sonuçlarınızı ve cüzdan bakiyenizi görüntüleyebilirsiniz:

```bash
# Açık işlemleri görüntüle (Open Trades)
sqlite3 data/ghost_trader.db "SELECT * FROM trades WHERE status = 'OPEN';"

# Sanal cüzdan bakiyesini kontrol et
sqlite3 data/ghost_trader.db "SELECT balance FROM wallet WHERE id = 1;"
```

### Önemli Notlar (Production Reliability Notes)
- **Paper Trading**: Bot varsayılan olarak PAPER (simülasyon) modunda çalışır. Gerçek işlem yapmaz.
- **Exchange Fallback**: Binance'in kısıtlı olduğu bölgelerde bot otomatik olarak Coinbase'e geçiş yapar.
- **Veri Kalıcılığı**: Docker kullanımında `data/` dizini volume olarak bağlanmalıdır.
- **DEBUG_SIGNAL_MODE**: `DEBUG_SIGNAL_MODE=true` ortam değişkeni ile sinyal filtrelerini baypas ederek runtime sinyal yolunu test edebilirsiniz.
- **Market Sınırlamaları**: Bot, 'CRYPTO' ve 'SPORTS' kategorilerinde otomatik işlem yapar; 'POLITICS' gibi diğer kategoriler sadece analiz amaçlı loglanır.

---

*Ghost Trader v1.0 - Gölgelerde ticaret yapın.* 🌙
