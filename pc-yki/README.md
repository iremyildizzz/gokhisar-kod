# HSS — Hava Savunma Sistemi Yazılımı

YOLOv8s tabanlı hedef tespiti, ByteTrack takibi, dost-düşman ayrımı (IFF) ve
PID tabanlı taret yönelimi içeren hibrit mimarili hava savunma sistemi yazılımı.

Sistem üç fiziksel katmanda çalışır:

```
┌─────────────────────────┐   TCP/JSON    ┌──────────────────────┐   UART    ┌──────────────────┐
│  PC (YKİ)               │ ────────────► │  Raspberry Pi 5      │ ────────► │  STM32F411       │
│  Python + PySide6       │  hedef koord. │  Python              │  ANG /    │  C (bare metal)  │
│  ─ YOLOv8s tespit       │  sınıf, kilit │  ─ PID yönelim       │  ATES_ET  │  ─ 50 Hz PWM     │
│  ─ HSV küçük hedef      │  angajman     │  ─ Yasaklı bölge     │           │  ─ Servo sürüş   │
│  ─ Doğrulama + IFF      │               │  ─ LiDAR doğrulama   │ ◄──────── │  ─ MOSFET ateş.  │
│  ─ ByteTrack takip      │               │  ─ Angajman kararı   │  DURUM    │  ─ Geri bildirim │
│  ─ Önceliklendirme      │               │                      │           │                  │
│  ─ Durum makinesi + GUI │               │                      │           │                  │
└─────────────────────────┘               └──────────────────────┘           └──────────────────┘
```

## Klasör Yapısı

```
hss/
├── requirements.txt
├── pc/                          # Yer Kontrol İstasyonu (PC)
│   ├── config.py                # ★ TÜM eşikler/sabitler burada — kod içine sabit gömme!
│   ├── main.py                  # Pipeline: modülleri yaşam döngüsüne göre bağlar + giriş noktası
│   ├── detection/
│   │   ├── yolo_detector.py     # YOLOv8s sarmalayıcı + Detection veri sınıfı + ROI çıkarımı
│   │   └── hsv_detector.py      # HSV küçük hedef tespiti + dinamik ROI + yedek algılama
│   ├── validation/matcher.py    # Maket–balon eşleştirme (2 yöntem)
│   ├── iff/friend_foe.py        # Dost-düşman ayrımı (Hue medyan + zamansal oylama)
│   ├── tracking/tracker.py      # ByteTrack sarmalayıcı + servo için 2. Kalman filtresi
│   ├── evaluation/prioritizer.py# Ağırlıklı öncelik puanı + angajman adayı seçimi
│   ├── lifecycle/state_machine.py # Durum makinesi + kilit + 3 koşullu imha doğrulaması


```

---

## Kurulum
### Atış Kontrol (RPi5 + STM32F411)

Bu depo yalnızca **atış kontrol** yazılımını içerir:

| Birim | Görev |
|--------|--------|
| **Raspberry Pi 5** | TCP komut alma, PID, LiDAR menzil, UART binary |
| **STM32F411** | Servo PWM + MOSFET tetik, failsafe |

Görüntü işleme / arayüz bu repoda yoktur.

#### Pinler

- **PA6** → X servo (pan)
- **PA7** → Y servo (tilt)
- **PB1** → Tetik (IRLZ44N)
- **PA9 / PA10** → USART1 ↔ RPi UART

#### Klasörler

- `rpi5/` — Python atış kontrol servisi
- `stm32f411/` — C firmware
- `PROTOCOL.md` — UART + TCP mesaj formatı

#### Çalıştırma (RPi)

```bash
cd rpi5
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m fire_control.main --tcp-port 5005 --stm-port /dev/ttyAMA0 --lidar-port /dev/ttyAMA1
```

### PC
```bash
python -m venv .venv
source .venv/bin/activate 
pip install -r requirements.txt
```

## Modüller (Kod Haritası)

Veri, her karede şu sırayla akar (hepsi `main.py` → `Pipeline.process`):

### 1. Tespit — `detection/`
- `YoloDetector.detect(frame)` → `list[Detection]` (tam kare YOLOv8s)

- `HsvBalloonDetector`: HSV tabanlı iki farklı algoritmik mekanizma barındırır:
  1. **Küçük Hedef Tespiti Algoritması (`detect` / `detect_under_object`)**:
     Tam kare YOLO modeli nesneleri (maketleri) tespit eder. Eğer tespit edilen nesnenin altındaki balon bulunamazsa (30 frame dengesizliği veya LiDAR 10-15m + hareket şartı karşılandığında):
     - **Aşama 1 (Model Çözümü)**: Nesnenin altındaki dinamik ROI bölgesinde (`lower_roi`) önce YOLO modeli ile balon tespiti denenir.
     - **Aşama 2 (HSV Çözümü Fallback)**: Eğer YOLO modeli balon bulamazsa, HSV tabanlı çift eşikli maskeleme → morfolojik temizleme → kontur analizi çözümü uygulanarak (`detect_under_object`) nesne altındaki balon tespit edilir.
     
     **Tetiklenme Şartları:**
     - **Şart 1 (Nesne-Balon Dengesizliği):** Görüntüde maket nesne var fakat altındaki balon eşleşmiyorsa bu durum **30 frame** boyunca sürdüğünde tetiklenir.
     - **Şart 2 (LiDAR + Hareket):** Telemetriden gelen LiDAR mesafesi **10 – 15 metre** arasında ise VE görüntüde **hareketlilik** tespit edilirse tetiklenir.

  2. **Yedek Balon Algılama Algoritması (`detect_backup`)**:
     YOLO modelinin çökmesi veya sistem performans düşüşü durumlarında (`backup_mode = True`) devreye giren yedek mekanizmadır. YOLO olmadan aynı HSV boru hattı ile aday bölgeleri bulur; boyut, şekil ve en-boy oranı geometrik filtreleriyle yanlış pozitifleri eleyerek doğrulanan bölgeleri doğrudan takibe aktarır.

**`Detection`** tüm sistemin ortak veri sınıfıdır: `x1,y1,x2,y2,conf,class_id,source`.
`source` alanı `"yolo" | "hsv" | "yolo_roi"` olabilir; takipte kimlik
sürekliliği bu ortak format sayesinde korunur.

### 2. Doğrulama — `validation/matcher.py`
`TargetMatcher.match(frame, models, balloons)` iki yöntemle maket–balon eşler:
1. Maketin altındaki dinamik bölgeye düşen balon → doğrudan eşleşme.
   Bölge derinliği maket piksel yüksekliğine bağlıdır; hedef küçüldükçe
   kademeli genişler (`MATCH_REGION_*` sabitleri).
2. Eşleşmeyen **HSV** balonlarının üst ROI'sinde YOLO yeniden çalıştırılır;
   maket bulunursa eşleşir. Eşleşmeyenler elenmez, sonraki karede tekrar denenir.

### 3. IFF — `iff/friend_foe.py`
- Kırmızı = `DUSMAN`, camgöbeği = `DOST` (Hue medyanı, düşük doygunluklu
  pikseller dışlanır)
- **Tek kare asla yeterli değildir**: her `track_id` için 15 karelik geçmiş
  tutulur; en az `IFF_VOTE_MIN_FRAMES = 5` tutarlı oy gerekir
- `stage=2` → balonlu tüm maketler doğrudan düşman

### 4. Takip — `tracking/tracker.py`
- `TargetTracker.update(all_dets)`: YOLO + HSV tespitlerinin birleşik listesi `TRACK_LOW_CONF = 0.1` eşiğiyle filtrelenerek ByteTrack'e verilir.
- **İki Turlu Eşleştirme (ByteTrack)**:
  - 1. Tur: `TRACK_HIGH_CONF = 0.5` üzerindeki yüksek güvenli tespitler IoU + Macar (Hungarian) algoritması ile eşleştirilir.
  - 2. Tur: `0.1 <= conf < 0.5` arasındaki düşük güvenli tespitler, Stage 1'de eşleşmeyen takiplerin telafi edilmesinde kullanılır (kayıp/perdelenmiş hedefleri kurtarır).
  - `conf < 0.1` olan gürültü tespitleri önceden elenerek ölü sabit sorunu çözülmüştür.
- Ölçüm yoksa `misses` artar; `TRACK_BUFFER = 30` kare sonunda hedef düşer
- `ServoKalman`: ByteTrack'ten **bağımsız** ikinci Kalman filtresi; servoya
  giden merkez koordinatını yumuşatır (`update()` ölçümlü, `predict_only()` ölçümsüz)

### 5. Değerlendirme — `evaluation/prioritizer.py`
```
puan = 0.35·boyut + 0.25·merkez_yakınlığı + 0.20·takip_kararlılığı + 0.10·angajman_uygunluğu + 0.10·servo_kararlılığı
```
Beş ölçütlü ağırlıklı formül kullanılarak puanlama yapılır. DÜŞMAN doğrulanmış hedefler arasında en yüksek puanlı hedef angajman adayı seçilir.

### 6. Yaşam Döngüsü — `lifecycle/state_machine.py`
Aşağıdaki durum makinesini yönetir; kilit toleransını ve imha koşullarını denetler.


## Hedef Yaşam Döngüsü

```
                 tespit           maket-balon        DÜŞMAN (IFF          öncelik
                 edildi           eşleşti            oylaması)            birincisi
   ┌────────┐            ┌──────────┐        ┌───────┐         ┌──────────┐         ┌─────────────┐
   │ DETECT ├───────────►│ VALIDATE ├───────►│ TRACK ├────────►│ EVALUATE ├────────►│ TARGET_LOCK │
   └────────┘            └──────────┘        └───▲───┘         └──────────┘         └──────┬──────┘
                                                 │                                         │
                                                 │  tolerans dışına çıkma /                │ merkezde kararlı
                                                 │  imha koşulları sağlanamadı             │ + LiDAR onayı
                                                 └────────────────────────────┐            ▼
                                                                              │      [ATEŞLEME]
                                                                    ┌─────────┴──┐        │
                                                                    │ 3 koşul eş │◄───────┘
                                                                    │ zamanlı mı?│  DESTROY_EVAL_DELAY_S
                                                                    └─────┬──────┘  sonra değerlendirilir
                                                                          │ evet
                                                                          ▼
                                                                    ┌───────────┐
                                                                    │ DESTROYED │
                                                                    └───────────┘
```

İmha üç koşulun **eş zamanlı** sağlanmasını gerektirir:
1. `DESTROY_MISS_FRAMES` boyunca yeniden tespit edilememe
2. Takip zincirinin sonlanması
3. Güven skorunun `DESTROY_CONF_THRESHOLD` altında kalması

Sağlanamazsa hedef TRACK'e döner (yanlış imha kararı önlenir).

---


## Ayar Parametreleri (Tuning)

Sahada ayarlanması gerekenler (hepsi `pc/config.py` ve `rpi/` içinde):

| Parametre | Yer | Açıklama |
|---|---|---|
| `HSV_RED_*`, `HSV_CYAN_*` | config.py | Işık koşuluna göre renk aralıkları — **ilk ayarlanacak şey** |
| `MIN_CIRCULARITY` | config.py | Balon dairesellik eşiği (düşürmek = daha toleranslı) |
| `YOLO_CONF_THRESHOLD` | config.py | Yanlış pozitif/negatif dengesi |
| `LOCK_TOLERANCE_PX`, `LOCK_STABLE_FRAMES` | config.py | Kilit hassasiyeti |
| `W_SIZE / W_STABILITY / W_SERVO` | config.py | Öncelik puan ağırlıkları (toplam 1.0) |
| `kp, ki, kd` | rpi/pid_controller.py | PID katsayıları — önce yalnız Kp, sonra Kd, en son Ki |
| `FORBIDDEN_ZONES` | rpi/main.py | Mekanik montaja göre yasaklı açı bölgeleri |
| `SAFE_ENGAGE_DISTANCES` | rpi/main.py | Sınıf bazlı güvenli angajman mesafeleri (cm) |
| `SERVO_MIN_US / MAX_US` | stm32/main.c | Kullanılan servonun gerçek darbe aralığı |

**PID ayar sırası:** `Ki=Kd=0` ile başlayıp hedefe salınımlı yaklaşana dek `Kp`
artırın → salınımı sönümleyene dek `Kd` ekleyin → kalıcı hata varsa çok küçük `Ki`.

---
# Performans Ölçüm Kodu Analizi


## 1. FPS Ölçümleri

Kodda dört ayrı FPS değeri ölçülmektedir:

| Ölçüm | Kod adı | Açıklama |
|---|---|---|
| Kamera FPS | `camera_fps` | Kamera tarafında üretilen kare hızı |
| Network RX FPS | `network_rx_fps` | Ağ üzerinden alınan kare hızı |
| Processing FPS | `processing_fps` | İşlemesi tamamlanan kare hızı |
| GUI FPS | `gui_fps` | Arayüzde gösterilen kare hızı |

`FPSMeter` sınıfı son 2 saniyelik kayan pencere üzerinden FPS hesabı yapar.

Örnek gösterimi:

```text
CAM       30.0 FPS
NET       29.7 FPS
AI        24.4 FPS
GUI       24.2 FPS
```

---

## 2. Frame Sayaçları ve Frame Kaybı

Kod aşağıdaki sayaçları tutmaktadır:

```text
camera_frames
received_frames
processed_frames
dropped_frames
queue_overwrites
frame_drop_percent
```

Frame drop yüzdesi şu mantıkla hesaplanır:

```text
FrameDrop % = DroppedFrames / CameraFrames × 100
```

`queue_overwrite()` çağrıldığında hem queue overwrite sayacı hem de dropped frame sayacı artmaktadır.

---

## 3. Preprocessing Süresi

Aşağıdaki iki çağrı arasındaki süre ölçülmektedir:

```python
preprocess_start(frame_id)
preprocess_end(frame_id)
```

Bu ölçüm model öncesi işlemleri kapsayabilir:

- Resize
- Renk dönüşümü
- Tensor hazırlama
- Normalizasyon

Kod aşağıdaki istatistikleri üretir:

```text
mean
min
max
std
p50
p95
p99
```

---

## 4. Inference Süresi

Aşağıdaki iki çağrı arasındaki süre ölçülmektedir:

```python
inference_start(frame_id)
inference_end(frame_id)
```

Bu değer model çıkarım süresini verir.

Örnek:

```text
Inference Mean   24.8 ms
Inference P95    28.7 ms
Inference P99    34.2 ms
```

Not: Kod son inference süresini ayrı bir değişken olarak tutmaz. `LatencyMeter`, son 1000 örneği saklar ve bunların istatistiklerini üretir.

---

## 5. Postprocessing Süresi

Aşağıdaki iki çağrı arasındaki süre ölçülmektedir:

```python
postprocess_start(frame_id)
postprocess_end(frame_id)
```

Bu aşama örneğin şunları içerebilir:

- Model çıktılarının okunması
- Bounding box işleme
- Class bilgisi işleme
- Tracking için veri hazırlama

Yine `mean`, `min`, `max`, `std`, `p50`, `p95`, `p99` değerleri hesaplanır.

---

## 6. Toplam Frame Processing Süresi

Kodda `frame_processing_latency` metriği vardır.

Bu değer:

```text
network frame received
        ↓
preprocess
        ↓
inference
        ↓
postprocess complete
```

arasındaki toplam süreyi ölçer.

Kabaca:

```text
T_processing = T_postprocess_end - T_network_receive
```

UI'da şu şekilde gösterilebilir:

```text
VISION
Processing      35 ms
Inference       27 ms
Preprocess       4 ms
Postprocess      2 ms
```

---

## 7. Video / Network Frame Gecikmesi

Kodda:

```text
network_frame_latency
```

metriği vardır.

Bu değer `camera_frame()` ile `network_frame_received()` arasındaki farktan hesaplanır.

Ancak burada önemli bir dikkat noktası vardır:

- `camera_frame()` Raspberry Pi üzerinde
- `network_frame_received()` PC üzerinde

çalışıyorsa ve iki cihaz da kendi `time.perf_counter_ns()` değerini kullanıyorsa bu timestamp'ler doğrudan karşılaştırılamaz.

Bu nedenle cihazlar arası gerçek video gecikmesini ölçmek için ortak saat senkronizasyonu veya paket içine wall-clock timestamp eklenmesi gerekir.

---

## 8. TCP Gecikmesi

TCP tarafında:

```python
sender_timestamp_ns
receiver_ns = time.time_ns()
```

kullanılarak gecikme hesaplanmaktadır.

Bu yaklaşım ancak gönderici ve alıcı cihazların saatleri senkronize ise anlamlıdır.

Örneğin NTP/PTP senkronizasyonu yoksa ölçüm gerçek network latency değerine saat farkını da ekleyebilir.

---

## 9. Network Veri Aktarım Hızı

Kod aşağıdaki değerleri ölçer:

```text
network_tx_mbps
network_rx_mbps
```

`psutil.net_io_counters()` kullanılarak gönderilen/alınan byte farkı zamana bölünür.

Formül:

```text
Mbps = ΔBytes × 8 / Δt / 1,000,000
```

Dikkat: Bu değer yalnızca video trafiğini değil, seçilen network interface üzerindeki tüm trafiği kapsar.

---

## 10. CPU Ölçümleri

Kod iki ayrı CPU metriği verir:

```text
cpu_percent
process_cpu_percent
```

### `cpu_percent`
Sistemin genel CPU kullanımını gösterir.

### `process_cpu_percent`
Sadece çalışan Python uygulamasının CPU kullanımını gösterir.

---

## 11. RAM Ölçümleri

Kod şu değerleri ölçer:

```text
ram_percent
ram_used_mb
process_ram_mb
```

Bunlar sırasıyla:

- Sistem RAM kullanım yüzdesi
- Kullanılan toplam RAM
- Uygulamanın kullandığı RAM

değerlerini verir.

`process_ram_mb` uzun süreli testlerde memory leak gözlemlemek için faydalıdır.

---

## 12. GPU Ölçümleri

NVIDIA GPU mevcutsa kod şu metrikleri verir:

```text
gpu_usage
gpu_memory_usage
gpu_temperature
```

Notlar:

- Yalnızca NVIDIA GPU için çalışır.
- Varsayılan olarak `GPU 0` izlenir.
- Çoklu GPU sisteminde diğer GPU'lar izlenmez.

---

## 13. Raspberry Pi / CPU Sıcaklığı

Kod:

```text
temperature_c
```

değerini üretir.

Öncelikle:

```text
/sys/class/thermal/thermal_zone0/temp
```

okunur.

Bu başarısız olursa:

```text
vcgencmd measure_temp
```

komutu denenir.

Raspberry Pi üzerinde sıcaklık takibi için uygundur.

---

## 14. TCP Bağlantı Sağlığı

TCP için şu metrikler tutulur:

```text
tcp_messages
tcp_errors
tcp_reconnects
tcp_age_ms
tcp_state
```

`tcp_age_ms`, son TCP mesajı geleli geçen süreyi gösterir.

Durum mantığı:

```text
< 500 ms        RECEIVING
500–2000 ms     STALE
> 2000 ms       DISCONNECTED
```

Bu değer UI için oldukça uygundur.

---

## 15. LiDAR Ölçümleri

Kod şu LiDAR metriklerini üretir:

```text
lidar_samples
lidar_valid_percent
lidar_invalid_samples
lidar_timeouts
lidar_distance
lidar_age_ms
lidar_state
```

Örnek UI:

```text
LiDAR
RECEIVING

Distance     6243 mm
Valid         99.7 %
Age             8 ms
Timeout          2
```

Eksik nokta:

```text
LiDAR Hz
```

şu anda hesaplanmamaktadır.

---

## 16. UART Ölçümleri

UART tarafında şu değerler tutulur:

```text
uart_tx
uart_rx
uart_checksum_errors
uart_invalid_frames
uart_timeouts
uart_success_percent
uart_age_ms
uart_state
```

Örnek:

```text
STM32 UART
RECEIVING

TX          18453
RX          18440
Checksum        0
Invalid         2
Timeout         1
Success      99.9 %
Age           9 ms
```

---

## 17. Application Error Sayısı

Kod:

```python
application_error()
```

çağrıldığında:

```text
application_errors
```

sayacını artırır.

Exception handler'lara bağlanarak toplam uygulama hata sayısı takip edilebilir.

---

## 18. Tanımlı Olup Gerçekte Kullanılmayan Metrikler

Kodda şu nesneler tanımlıdır:

```python
self.lidar_age = LatencyMeter()
self.uart_latency = LatencyMeter()
```

Ancak bunlara veri ekleyen `.add_ms(...)` çağrıları bulunmamaktadır.

Bu nedenle:

- Gerçek LiDAR latency istatistiği ölçülmüyor.
- Gerçek UART latency istatistiği ölçülmüyor.

Önemli ayrım:

```text
LiDAR Age ≠ LiDAR Latency
UART Age  ≠ UART Latency
```

`Age`, son veri geleli geçen süreyi ifade eder.

---

## 19. Network Packet Sayaçları Kullanılmıyor

Kodda:

```python
self.network_packets
self.network_packet_errors
```

tanımlıdır.

Ancak bu sayaçlar artırılmadığı ve `snapshot()` içine eklenmediği için şu anda gerçek anlamda kullanılmamaktadır.

Dolayısıyla:

```text
Packet Count
Packet Error
Packet Loss %
```

ölçümleri mevcut değildir.

---

## 20. Network Jitter Yok

Kodda:

```text
network_rx_mbps
video_latency
FPS
```

bulunmasına rağmen:

```text
packet jitter
frame arrival jitter
```

ölçümleri bulunmamaktadır.

UDP/RTP video akışı için ayrıca eklenebilir.

---

## 21. GUI Latency Yok

Kod `gui_ns` timestamp'ini saklar ancak bunun üzerinden herhangi bir gecikme metriği hesaplamaz.

Dolayısıyla:

```text
GUI FPS
```

vardır fakat:

```text
GUI render latency
processing → GUI latency
camera → GUI latency
```

ölçümleri yoktur.

---

## 22. Görüntü Kalitesi Ölçümleri Yok

Kod görüntü performansını ölçer ancak görüntü kalitesi için şu metrikler bulunmamaktadır:

```text
Blur
Sharpness
Brightness
Contrast
Exposure
Image Noise
SNR
```

Sistem UI'ında gerekirse özellikle:

```text
Brightness
Sharpness / Blur
```

değerleri saniyede bir kez hesaplanabilir.

---

# 23. Tam Ölçüm Özeti

| Grup | Ölçüm | Durum |
|---|---|---|
| Video | Camera FPS | ✅ |
| Video | Received FPS | ✅ |
| Video | Processing FPS | ✅ |
| Video | GUI FPS | ✅ |
| Frame | Camera frame count | ✅ |
| Frame | Received frame count | ✅ |
| Frame | Processed frame count | ✅ |
| Frame | Dropped frames | ✅ |
| Frame | Drop % | ✅ |
| Queue | Queue overwrite | ✅ |
| Processing | Preprocess latency | ✅ |
| Processing | Inference latency | ✅ |
| Processing | Postprocess latency | ✅ |
| Processing | Total processing latency | ✅ |
| Network | TX Mbps | ✅ |
| Network | RX Mbps | ✅ |
| Network | Video latency | ⚠️ Cihazlar arası kullanımda dikkat |
| Network | Packet loss | ❌ |
| Network | Jitter | ❌ |
| TCP | Message count | ✅ |
| TCP | Error count | ✅ |
| TCP | Reconnect | ✅ |
| TCP | Last data age | ✅ |
| TCP | State | ✅ |
| TCP | Latency | ⚠️ Saat senkronizasyonu gerekli |
| LiDAR | Sample count | ✅ |
| LiDAR | Valid % | ✅ |
| LiDAR | Invalid count | ✅ |
| LiDAR | Timeout | ✅ |
| LiDAR | Distance | ✅ |
| LiDAR | Data age | ✅ |
| LiDAR | Status | ✅ |
| LiDAR | Hz | ❌ |
| UART | TX | ✅ |
| UART | RX | ✅ |
| UART | Checksum error | ✅ |
| UART | Invalid frame | ✅ |
| UART | Timeout | ✅ |
| UART | Success % | ✅ |
| UART | Data age | ✅ |
| UART | Status | ✅ |
| UART | Latency | ❌ |
| Hardware | CPU | ✅ |
| Hardware | Application CPU | ✅ |
| Hardware | RAM | ✅ |
| Hardware | Application RAM | ✅ |
| Hardware | GPU | ✅ NVIDIA |
| Hardware | VRAM | ✅ NVIDIA |
| Hardware | GPU temperature | ✅ NVIDIA |
| Hardware | Raspberry Pi temperature | ✅ |
| Software | Application errors | ✅ |
| Image | Sharpness | ❌ |
| Image | Brightness | ❌ |
| Image | Blur | ❌ |

# 24. UI İçin Önerilen Ana Metrikler

Ana operatör ekranında aşağıdaki metriklerin gösterilmesi yeterlidir:

```text
CAM FPS
NET FPS
AI FPS
FRAME DROP %

PROCESSING ms
VIDEO DATA AGE / LATENCY

NETWORK Mbps

CPU
GPU
RAM
RPi TEMP

LiDAR
 ├─ ONLINE
 ├─ Hz
 └─ Data Age

TCP
 ├─ STATUS
 └─ Data Age

STM32 UART
 ├─ STATUS
 ├─ Error
 └─ Data Age
```

Daha ayrıntılı değerler Debug / Performance ekranına taşınabilir:

```text
p50
p95
p99
queue overwrite
checksum error
reconnect count
invalid frame
timeout
total counters
```

# 25. Genel Değerlendirme

Kod güçlü bir başlangıçtır ve özellikle aşağıdaki alanları iyi kapsar:

- FPS takibi
- Görüntü işleme süreleri
- Sistem kaynak kullanımı
- TCP bağlantı sağlığı
- LiDAR veri sağlığı
- UART hata ve durum takibi

Ancak tam bir gerçek zamanlı sistem sağlık ekranı için şu metriklerin eklenmesi önerilir:

- LiDAR Hz
- UART frame rate
- UDP/RTP packet loss
- Network jitter
- Doğru cihazlar-arası video latency
- GUI render latency
- İsteğe bağlı görüntü kalitesi ölçümleri
