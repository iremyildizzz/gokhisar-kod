/**
 * @file trigger.h
 * @brief MOSFET tetik — PB1 (IRLZ44N gate), aktif-high pulse
 */
#ifndef ATIS_TRIGGER_H
#define ATIS_TRIGGER_H

#include <stdint.h>
#include <stdbool.h>

#define TRIGGER_PULSE_MS  120u

void Trigger_Init(void);
void Trigger_Abort(void);
void Trigger_RequestFire(void);
void Trigger_Tick1ms(void);

bool Trigger_IsBusy(void);
bool Trigger_ConsumeFiredFlag(void);

#endif /* ATIS_TRIGGER_H */
