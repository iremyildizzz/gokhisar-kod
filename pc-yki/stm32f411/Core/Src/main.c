/**
 * @file main.c
 * @brief Atış kontrol yazılımı — STM32F411
 *
 * PA6  TIM3_CH1  → X servo (pan)
 * PA7  TIM3_CH2  → Y servo (tilt)
 * PB1  GPIO      → MOSFET tetik (IRLZ44N)
 * PA9  USART1_TX → Raspberry Pi 5 RX
 * PA10 USART1_RX ← Raspberry Pi 5 TX
 *
 * CubeMX ile üretilecek: SystemClock, USART1 115200, TIM3 PWM 50 Hz.
 * Bu dosya uygulama mantığını içerir; MX_* init çağrıları Cube iskeletiyle birleştirilir.
 */
#include "main.h"
#include "protocol.h"
#include "servo.h"
#include "trigger.h"

#include <string.h>

/* ---- CubeMX üretimi beklenen handle'lar ---- */
UART_HandleTypeDef huart1;
TIM_HandleTypeDef  htim3;

/* ---- RX ring ---- */
#define RX_RING_SIZE 64
static volatile uint8_t  s_rx_ring[RX_RING_SIZE];
static volatile uint16_t s_rx_head = 0;
static volatile uint16_t s_rx_tail = 0;
static uint8_t s_rx_byte = 0;

static uint8_t  s_frame_buf[PROTO_FRAME_LEN];
static uint8_t  s_frame_idx = 0;

static volatile uint32_t s_ms = 0;
static uint32_t s_last_cmd_ms = 0;
static uint32_t s_last_telem_ms = 0;

static bool     s_failsafe = true;
static bool     s_armed    = false;
static bool     s_enabled  = false;
static uint8_t  s_stage    = 0;
static bool     s_fire_edge_armed = false; /* FIRE rising-edge için */

/* CubeMX stub'ları — gerçek projede Cube üretimi kullanılır */
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_TIM3_Init(void);

static void rx_ring_push(uint8_t b)
{
    uint16_t next = (uint16_t)((s_rx_head + 1u) % RX_RING_SIZE);
    if (next == s_rx_tail) {
        return; /* overrun: drop */
    }
    s_rx_ring[s_rx_head] = b;
    s_rx_head = next;
}

static bool rx_ring_pop(uint8_t *b)
{
    if (s_rx_head == s_rx_tail) {
        return false;
    }
    *b = s_rx_ring[s_rx_tail];
    s_rx_tail = (uint16_t)((s_rx_tail + 1u) % RX_RING_SIZE);
    return true;
}

static void enter_failsafe(void)
{
    s_failsafe = true;
    s_armed    = false;
    Trigger_Abort();
    Servo_SetEnabled(false);
    Servo_Hold();
}

static void send_telemetry(bool fired_event)
{
    ProtoTelemetry tel;
    tel.pan_cdeg  = Servo_GetPanCdeg();
    tel.tilt_cdeg = Servo_GetTiltCdeg();
    tel.status    = 0;

    if (fired_event) {
        tel.status |= STATUS_FIRED;
    }
    if (s_armed) {
        tel.status |= STATUS_ARMED;
    }
    if (s_failsafe) {
        tel.status |= STATUS_FAILSAFE;
    }
    if (s_enabled && !s_failsafe) {
        tel.status |= STATUS_ENABLED;
    }
    if (Trigger_IsBusy()) {
        tel.status |= STATUS_BUSY;
    }
    if (Servo_WasLimited()) {
        tel.status |= STATUS_ANGLE_LIM;
    }

    uint8_t frame[PROTO_FRAME_LEN];
    Proto_BuildUplink(&tel, frame);
    HAL_UART_Transmit(&huart1, frame, PROTO_FRAME_LEN, 20);
}

static void handle_command(const ProtoCommand *cmd)
{
    s_last_cmd_ms = s_ms;
    s_stage = cmd->stage;

    if (cmd->flags & FLAG_SAFE) {
        enter_failsafe();
        return;
    }

    /* geçerli frame geldi → failsafe kalkabilir */
    s_failsafe = false;

    if (cmd->flags & FLAG_HOME) {
        Servo_Home();
    }

    s_enabled = (cmd->flags & FLAG_ENABLE) != 0;
    Servo_SetEnabled(s_enabled && !s_failsafe);

    if (s_enabled) {
        Servo_SetAnglesCdeg(cmd->pan_cdeg, cmd->tilt_cdeg);
    }

    s_armed = (cmd->flags & FLAG_ARM) != 0;

    /* ATES_ET: rising edge + ARM + ENABLE + açı OK */
    const bool fire_req = (cmd->flags & FLAG_FIRE) != 0;
    if (fire_req && !s_fire_edge_armed) {
        s_fire_edge_armed = true;
        const bool ok =
            s_armed &&
            s_enabled &&
            !s_failsafe &&
            !Servo_WasLimited() &&
            !Trigger_IsBusy();

        if (ok) {
            Trigger_RequestFire();
            /* KTR: "ateşleme gerçekleşti" geri bildirimi */
            send_telemetry(true);
        }
    }
    if (!fire_req) {
        s_fire_edge_armed = false;
    }
}

static void poll_uart_frames(void)
{
    uint8_t b;
    while (rx_ring_pop(&b)) {
        if (s_frame_idx == 0) {
            if (b != PROTO_SYNC_DOWN) {
                continue;
            }
            s_frame_buf[0] = b;
            s_frame_idx = 1;
            continue;
        }

        s_frame_buf[s_frame_idx++] = b;
        if (s_frame_idx < PROTO_FRAME_LEN) {
            continue;
        }

        s_frame_idx = 0;
        ProtoCommand cmd;
        if (Proto_ParseDownlink(s_frame_buf, &cmd)) {
            handle_command(&cmd);
        }
    }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        rx_ring_push(s_rx_byte);
        HAL_UART_Receive_IT(&huart1, &s_rx_byte, 1);
    }
}

void HAL_SYSTICK_Callback(void)
{
    s_ms++;
    Trigger_Tick1ms();
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_TIM3_Init();
    MX_USART1_UART_Init();

    Trigger_Init();
    Servo_Init();
    enter_failsafe();

    HAL_UART_Receive_IT(&huart1, &s_rx_byte, 1);

    while (1) {
        poll_uart_frames();

        /* RD-08: heartbeat / frame timeout */
        if (!s_failsafe && (s_ms - s_last_cmd_ms) > HEARTBEAT_TIMEOUT_MS) {
            enter_failsafe();
        }

        if ((s_ms - s_last_telem_ms) >= TELEM_PERIOD_MS) {
            s_last_telem_ms = s_ms;
            const bool fired = Trigger_ConsumeFiredFlag();
            send_telemetry(fired);
        }
    }
}

/* -------------------------------------------------------------------------- */
/* Minimal Cube-benzeri init (Nucleo-F411 / BlackPill F411CE için başlangıç) */
/* Gerçek projede CubeMX çıktısı ile değiştirin.                              */
/* -------------------------------------------------------------------------- */

void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    osc.HSIState       = RCC_HSI_ON;
    osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    osc.PLL.PLLState   = RCC_PLL_ON;
    osc.PLL.PLLSource  = RCC_PLLSOURCE_HSI;
    /* HSI 16 / PLLM8 = 2 MHz → ×PLLN84 = 168 → /2 = 84 MHz SYSCLK
     * APB1 = 42 MHz → TIM clock = 84 MHz (PSC=83 → 1 MHz tick, ARR=19999 → 50 Hz) */
    osc.PLL.PLLM       = 8;
    osc.PLL.PLLN       = 84;
    osc.PLL.PLLP       = RCC_PLLP_DIV2;
    osc.PLL.PLLQ       = 4;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        Error_Handler();
    }

    clk.ClockType      = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                         RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider  = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV2;
    clk.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_2) != HAL_OK) {
        Error_Handler();
    }
}

static void MX_GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    /* PB1 Trigger_Init içinde kurulur */
}

static void MX_USART1_UART_Init(void)
{
    __HAL_RCC_USART1_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* PA9 TX / PA10 RX AF7 — Init öncesi pin mux */
    GPIO_InitTypeDef g = {0};
    g.Pin       = GPIO_PIN_9 | GPIO_PIN_10;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_PULLUP;
    g.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA, &g);

    huart1.Instance          = USART1;
    huart1.Init.BaudRate     = 115200;
    huart1.Init.WordLength   = UART_WORDLENGTH_8B;
    huart1.Init.StopBits     = UART_STOPBITS_1;
    huart1.Init.Parity       = UART_PARITY_NONE;
    huart1.Init.Mode         = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    if (HAL_UART_Init(&huart1) != HAL_OK) {
        Error_Handler();
    }

    HAL_NVIC_SetPriority(USART1_IRQn, 1, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
}

static void MX_TIM3_Init(void)
{
    __HAL_RCC_TIM3_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* PA6 / PA7 AF2 TIM3 */
    GPIO_InitTypeDef g = {0};
    g.Pin       = GPIO_PIN_6 | GPIO_PIN_7;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_LOW;
    g.Alternate = GPIO_AF2_TIM3;
    HAL_GPIO_Init(GPIOA, &g);

    htim3.Instance               = TIM3;
    htim3.Init.Prescaler         = 83;      /* TIMCLK 84 MHz / 84 = 1 MHz */
    htim3.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim3.Init.Period            = 19999;   /* 20 ms → 50 Hz servo */
    htim3.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    if (HAL_TIM_PWM_Init(&htim3) != HAL_OK) {
        Error_Handler();
    }

    TIM_OC_InitTypeDef oc = {0};
    oc.OCMode     = TIM_OCMODE_PWM1;
    oc.Pulse      = 1500; /* 1.5 ms = orta */
    oc.OCPolarity = TIM_OCPOLARITY_HIGH;
    oc.OCFastMode = TIM_OCFAST_DISABLE;
    HAL_TIM_PWM_ConfigChannel(&htim3, &oc, TIM_CHANNEL_1);
    HAL_TIM_PWM_ConfigChannel(&htim3, &oc, TIM_CHANNEL_2);
}

void USART1_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart1);
}

void Error_Handler(void)
{
    __disable_irq();
    while (1) {
    }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
    (void)file;
    (void)line;
}
#endif
