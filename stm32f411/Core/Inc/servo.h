/**
 * @file servo.h
 * @brief DS5160 HV pan/tilt PWM — PA6=TIM3_CH1, PA7=TIM3_CH2, 50 Hz
 *
 * Açı uzayı gokhisar: 0°…180°.
 * Pan home 90°, tilt home 80° (UI elevation −10°).
 */
#ifndef ATIS_SERVO_H
#define ATIS_SERVO_H

#include <stdint.h>
#include <stdbool.h>

/* cdeg = derece * 10 */
#define SERVO_PAN_MIN_CDEG    (0)
#define SERVO_PAN_MAX_CDEG    (1800)
#define SERVO_TILT_MIN_CDEG   (0)
#define SERVO_TILT_MAX_CDEG   (1800)
#define SERVO_PAN_HOME_CDEG   (900)   /* 90.0° */
#define SERVO_TILT_HOME_CDEG  (800)   /* 80.0° → elev −10° */
#define SERVO_HOME_CDEG       SERVO_PAN_HOME_CDEG  /* geri uyum */

void Servo_Init(void);
void Servo_SetEnabled(bool enabled);
bool Servo_SetAnglesCdeg(int16_t pan_cdeg, int16_t tilt_cdeg);
void Servo_Home(void);
void Servo_Hold(void);

int16_t Servo_GetPanCdeg(void);
int16_t Servo_GetTiltCdeg(void);
bool    Servo_WasLimited(void);

#endif /* ATIS_SERVO_H */
