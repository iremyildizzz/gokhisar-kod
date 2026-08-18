# Raspberry Pi 5 — Atış Kontrol

Üst katmandan TCP JSON alır → PID / menzil kararı → STM32’ye UART binary gönderir.

## Kurulum

```bash
cd rpi5
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

UART: `raspi-config` → Serial Port açık, login shell kapalı.

Kablo:
- RPi TXD → STM32 PA10 (RX)
- RPi RXD → STM32 PA9 (TX)
- GND ortak
- LiDAR ayrı UART (`/dev/ttyAMA1`)

## Çalıştırma

```bash
python -m fire_control.main \
  --stm-port /dev/ttyAMA0 \
  --lidar-port "" \
  --tcp-port 5005 \
  --video-host 192.168.137.147 \
  --frame-w 1280 \
  --frame-h 720
```

`--video-host` = **PC IP**. Kamera fire_control ile birlikte sürekli UDP:5000’e gider.
Arayüz Başlat = sadece dinler; Durdur = PC tarafını kapatır, Pi yayına devam eder.

Örnek giriş mesajları: `../PROTOCOL.md`
