# Hava Savunma — tam yığın (PC + RPi + STM32)

Bu depo yedek/snapshot: YKİ (PC), atış kontrol (RPi5), STM32 firmware.

## Klasörler
- `pc-yki/` — Gökhisar Yer Kontrol İstasyonu (PySide6 + vision)
- `rpi5/` — atis-kontrol fire_control
- `stm32f411/` — üretim STM32 (hss Core)
- `stm32f411-atis-kontrol/` — atis-kontrol içindeki STM kopyası (varsa)

## Çalıştırma
PC: `cd pc-yki && python main.py --rpi-host <PI_IP>`
RPi: `cd rpi5 && python -m fire_control.main --tcp-port 5005 --video-host <PC_IP>`

Model `.pt` dosyaları bu repoya konmadı (boyut); PC'de `--weights` ile ver.
