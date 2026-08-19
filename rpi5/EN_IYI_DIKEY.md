# en_iyi_dikey — PC + RPi + STM yığını

PID RPi’de çalışır; YKİ katsayı gönderir; STM açı komutunu uygular.

## Preset `en_iyi_dikey` (varsayılan)

| Eksen | P | I | D |
|-------|------|---|-------|
| pan   | 0.034 | 0 | 0.010 |
| tilt  | 0.018 | 0 | 0.022 |

Gravity FF kapalı (`Kg=0`). Tilt yakın bant daha yumuşak.

## Çalıştırma (RPi)

```bash
cd rpi5
python -m fire_control.main \
  --stm-port /dev/ttyAMA0 \
  --lidar-port "" \
  --tcp-port 5005 \
  --video-host <PC_IP> \
  --frame-w 640 --frame-h 480 \
  --pid-preset en_iyi_dikey
```

YKİ açılınca P/D kutuları `en_iyi_dikey` ile gelir; RPi tilt’i oranlayarak yumuşak tutar.

Diğer presetler: `iyi_yatay` (aynı aile), `dikey_ayar1` (eski ortak kazanç + FF).
