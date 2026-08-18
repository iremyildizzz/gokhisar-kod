#include "trigger.h"
#include "stm32f4xx_hal.h"

#define TRIG_GPIO_PORT  GPIOB
#define TRIG_GPIO_PIN   GPIO_PIN_1

static volatile uint16_t s_pulse_left_ms = 0;
static volatile bool     s_fired_latched = false;

void Trigger_Init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin   = TRIG_GPIO_PIN;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_PULLDOWN; /* KTR: 10k pull-down false-trigger önlemi */
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(TRIG_GPIO_PORT, &g);

    HAL_GPIO_WritePin(TRIG_GPIO_PORT, TRIG_GPIO_PIN, GPIO_PIN_RESET);
    s_pulse_left_ms = 0;
    s_fired_latched = false;
}

void Trigger_Abort(void)
{
    s_pulse_left_ms = 0;
    HAL_GPIO_WritePin(TRIG_GPIO_PORT, TRIG_GPIO_PIN, GPIO_PIN_RESET);
}

void Trigger_RequestFire(void)
{
    if (s_pulse_left_ms > 0) {
        return; /* zaten ateşleniyor */
    }
    s_pulse_left_ms = TRIGGER_PULSE_MS;
    HAL_GPIO_WritePin(TRIG_GPIO_PORT, TRIG_GPIO_PIN, GPIO_PIN_SET);
    s_fired_latched = true;
}

void Trigger_Tick1ms(void)
{
    if (s_pulse_left_ms == 0) {
        return;
    }
    s_pulse_left_ms--;
    if (s_pulse_left_ms == 0) {
        HAL_GPIO_WritePin(TRIG_GPIO_PORT, TRIG_GPIO_PIN, GPIO_PIN_RESET);
    }
}

bool Trigger_IsBusy(void)
{
    return s_pulse_left_ms > 0;
}

bool Trigger_ConsumeFiredFlag(void)
{
    bool f = s_fired_latched;
    s_fired_latched = false;
    return f;
}
