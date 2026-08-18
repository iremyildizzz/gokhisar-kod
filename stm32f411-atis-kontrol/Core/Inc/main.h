/**
 * @file main.h
 * Atış kontrol — STM32F411CE (veya Nucleo-F411RE uyumlu pinler)
 */
#ifndef ATIS_MAIN_H
#define ATIS_MAIN_H

#include "stm32f4xx_hal.h"

#define HEARTBEAT_TIMEOUT_MS  200u
#define TELEM_PERIOD_MS        50u

extern UART_HandleTypeDef huart1;
extern TIM_HandleTypeDef  htim3;

void Error_Handler(void);

#endif /* ATIS_MAIN_H */
