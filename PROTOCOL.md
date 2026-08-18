# Atış Kontrol Haberleşme Protokolü

| Bağlantı | Ortam | Biçim |
|----------|-------|-------|
| Üst katman → Raspberry Pi 5 | Ethernet TCP | JSON |
| RPi5 ↔ STM32F411 | UART 3.3V TTL | **7 bayt binary frame** |
| RPi5 ↔ TF02-PRO | UART | LiDAR frame |

## RPi5 → STM32 (7 bayt)

| Bayt | Alan | Açıklama |
|------|------|----------|
| 0 | `SYNC` | `0xAA` |
| 1–2 | `pan_cdeg` | int16 LE, derece × 10 |
| 3–4 | `tilt_cdeg` | int16 LE, derece × 10 |
| 5 | `flags` | kontrol alanı |
| 6 | `checksum` | XOR(bayt0…bayt5) |

### Flags (bayt 5)

| Bit | Mask | Anlam |
|-----|------|-------|
| 0 | `0x01` | `FIRE` |
| 1 | `0x02` | `ARM` |
| 2 | `0x04` | `HEARTBEAT` |
| 3 | `0x08` | `HOME` |
| 4–5 | `0x30` | Aşama 0–3 |
| 6 | `0x40` | `SAFE` |
| 7 | `0x80` | `ENABLE` |

Ateşleme: `ENABLE + ARM + FIRE`, failsafe yokken.

## STM32 → RPi5 (7 bayt)

| Bayt | Alan | Açıklama |
|------|------|----------|
| 0 | `SYNC` | `0x55` |
| 1 | `status` | durum bayrakları |
| 2–3 | `pan_cdeg` | uygulanan pan |
| 4–5 | `tilt_cdeg` | uygulanan tilt |
| 6 | `checksum` | XOR(bayt0…bayt5) |

### Status

| Bit | Mask | Anlam |
|-----|------|-------|
| 0 | `0x01` | `FIRED` |
| 1 | `0x02` | `ARMED` |
| 2 | `0x04` | `FAILSAFE` |
| 3 | `0x08` | `ENABLED` |
| 4 | `0x10` | `BUSY` |
| 5 | `0x20` | `ANGLE_LIMIT` |

## Aşama davranışları (RPi)

TCP port varsayılan: **5005**

| Aşama | Giriş | RPi işi | LiDAR menzil |
|-------|-------|---------|--------------|
| **1** | `manual` / `servo` | açı → STM | Yok |
| **2** | `target` (cx/cy veya err) | optik → PID → ateş | Yok |
| **3** | `target` + `engage` | PID + sınıf mesafesi | Var |

Piksel → açı mesafeden bağımsızdır (16mm + IMX296 modeli).

## TCP JSON örnekleri

```json
{"type":"mode","autonomous":true,"stage":3}
{"type":"manual","dx":5,"dy":0}
{"type":"target","cx":640,"cy":360,"class_id":3,"track_id":7,"locked":true}
{"type":"engage","track_id":7,"class_id":3,"stage":3}
```

`class_id` (gokhisar ile aynı):

| id | ad | Görünen ad | Aşama-3 menzil |
|----|-----|------------|----------------|
| 0 | `fuze` | Balistik Füze | 5–15 m |
| 1 | `helikopter` | Helikopter | 5–15 m |
| 2 | `iha` | İHA | 0–15 m |
| 3 | `ucak` | Savaş Uçağı | 10–15 m |
| 4 | `balon` | Balon | imha yok |

## Donanım pinleri (STM32F411)

| İşlev | Pin |
|-------|-----|
| X servo | **PA6** |
| Y servo | **PA7** |
| Tetik | **PB1** |
| UART TX / RX | **PA9 / PA10** |
| Baud | 115200 8N1 |

Servo açı: **0°…180°**, orta (home) **90°**, PWM 50 Hz.

## Güvenlik

- 200 ms UART sessizliği → STM32 `FAILSAFE`
- Failsafe’de tetik kapalı, servo hold
- Açı limit aşımında ateş engeli
