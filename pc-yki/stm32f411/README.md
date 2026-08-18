# STM32F411 Atış Kontrol Firmware

## Pinler

| İşlev | Pin | Timer/GPIO |
|-------|-----|------------|
| X servo | PA6 | TIM3_CH1 PWM 50 Hz |
| Y servo | PA7 | TIM3_CH2 PWM 50 Hz |
| Tetik MOSFET | PB1 | GPIO OUT + pulldown |
| UART TX | PA9 | USART1 → RPi RX |
| UART RX | PA10 | USART1 ← RPi TX |

## CubeMX kurulumu (önerilen)

1. Chip: STM32F411CEUx (BlackPill) veya F411RE
2. SYS → Serial Wire, HSI+PLL → **SYSCLK 84 MHz** (PLLM=8, PLLN=84, PLLP=/2)
   - APB1=/2 → 42 MHz; timer clock = 84 MHz
3. USART1: Asynchronous, 115200 8N1, PA9/PA10, NVIC enable
4. TIM3: PWM Generation CH1+CH2
   - PSC = 83, ARR = 19999 → 50 Hz @ 84 MHz timer clock
5. Bu klasördeki `protocol.*`, `servo.*`, `trigger.*`, `main.c` uygulama dosyalarını projeye ekleyin

## Davranış

- RPi’den 7 bayt frame gelir → pan/tilt PWM güncellenir
- `ENABLE|ARM|FIRE` rising edge → PB1’de ~120 ms pulse → `STATUS_FIRED` uplink
- 200 ms frame yoksa FAILSAFE (tetik kapalı, servo hold)

Detay: `../PROTOCOL.md`
