/**
 * @file servo.c
 * @brief TIM3 PWM 50 Hz — PA6 (CH1) / PA7 (CH2)
 *
 * SystemClock: SYSCLK 84 MHz, APB1 42 MHz → TIM3 clock 84 MHz.
 * PSC=83 → 1 MHz tick, ARR=19999 → 50 Hz (20 ms).
 * Pulse compare = mikroaniye (500..2500) → açı lineer map.
 */
#include "servo.h"
#include "stm32f4xx_hal.h"

extern TIM_HandleTypeDef htim3;

#define PWM_PERIOD_US   20000u
#define PULSE_MIN_US      500u
#define PULSE_MAX_US     2500u

static int16_t s_pan_cdeg  = SERVO_HOME_CDEG;
static int16_t s_tilt_cdeg = SERVO_HOME_CDEG;
static bool    s_enabled   = false;
static bool    s_limited   = false;

static int16_t clamp_i16(int16_t v, int16_t lo, int16_t hi, bool *hit)
{
    if (v < lo) {
        *hit = true;
        return lo;
    }
    if (v > hi) {
        *hit = true;
        return hi;
    }
    return v;
}

static uint16_t angle_to_pulse_us(int16_t cdeg, int16_t min_cdeg, int16_t max_cdeg)
{
    /* cdeg → 500..2500 µs */
    int32_t span = (int32_t)max_cdeg - (int32_t)min_cdeg;
    int32_t pos  = (int32_t)cdeg - (int32_t)min_cdeg;
    int32_t us   = (int32_t)PULSE_MIN_US +
                   (pos * (int32_t)(PULSE_MAX_US - PULSE_MIN_US)) / span;
    if (us < (int32_t)PULSE_MIN_US) {
        us = (int32_t)PULSE_MIN_US;
    }
    if (us > (int32_t)PULSE_MAX_US) {
        us = (int32_t)PULSE_MAX_US;
    }
    return (uint16_t)us;
}

static void apply_pwm(void)
{
    uint16_t pan_us  = angle_to_pulse_us(s_pan_cdeg,  SERVO_PAN_MIN_CDEG,  SERVO_PAN_MAX_CDEG);
    uint16_t tilt_us = angle_to_pulse_us(s_tilt_cdeg, SERVO_TILT_MIN_CDEG, SERVO_TILT_MAX_CDEG);

    if (!s_enabled) {
        /* Çıkışı nötr pulse'ta tut (hold) — failsafe'de motorlara ani sıfır yok */
        __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, pan_us);
        __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_2, tilt_us);
        return;
    }

    __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, pan_us);
    __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_2, tilt_us);
}

void Servo_Init(void)
{
    s_pan_cdeg  = SERVO_HOME_CDEG;
    s_tilt_cdeg = SERVO_HOME_CDEG;
    s_enabled   = false;
    s_limited   = false;

    HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_2);
    apply_pwm();
}

void Servo_SetEnabled(bool enabled)
{
    s_enabled = enabled;
    apply_pwm();
}

bool Servo_SetAnglesCdeg(int16_t pan_cdeg, int16_t tilt_cdeg)
{
    s_limited = false;
    s_pan_cdeg  = clamp_i16(pan_cdeg,  SERVO_PAN_MIN_CDEG,  SERVO_PAN_MAX_CDEG,  &s_limited);
    s_tilt_cdeg = clamp_i16(tilt_cdeg, SERVO_TILT_MIN_CDEG, SERVO_TILT_MAX_CDEG, &s_limited);
    apply_pwm();
    return !s_limited;
}

void Servo_Home(void)
{
    s_limited   = false;
    s_pan_cdeg  = SERVO_HOME_CDEG;
    s_tilt_cdeg = SERVO_HOME_CDEG;
    apply_pwm();
}

void Servo_Hold(void)
{
    apply_pwm();
}

int16_t Servo_GetPanCdeg(void)
{
    return s_pan_cdeg;
}

int16_t Servo_GetTiltCdeg(void)
{
    return s_tilt_cdeg;
}

bool Servo_WasLimited(void)
{
    return s_limited;
}
