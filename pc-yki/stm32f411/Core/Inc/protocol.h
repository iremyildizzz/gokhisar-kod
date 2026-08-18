/**
 * @file protocol.h
 * @brief RPi5 <-> STM32F411 7-byte UART binary frame (KTR 4.3)
 */
#ifndef ATIS_PROTOCOL_H
#define ATIS_PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>

#define PROTO_SYNC_DOWN   0xAAu
#define PROTO_SYNC_UP     0x55u
#define PROTO_FRAME_LEN   7u

#define FLAG_FIRE         0x01u
#define FLAG_ARM          0x02u
#define FLAG_HEARTBEAT    0x04u
#define FLAG_HOME         0x08u
#define FLAG_STAGE_MASK   0x30u
#define FLAG_SAFE         0x40u
#define FLAG_ENABLE       0x80u

#define STATUS_FIRED      0x01u
#define STATUS_ARMED      0x02u
#define STATUS_FAILSAFE   0x04u
#define STATUS_ENABLED    0x08u
#define STATUS_BUSY       0x10u
#define STATUS_ANGLE_LIM  0x20u

typedef struct {
    int16_t pan_cdeg;   /* derece * 10 */
    int16_t tilt_cdeg;
    uint8_t flags;
    uint8_t stage;      /* 0..3 */
    bool    valid;
} ProtoCommand;

typedef struct {
    int16_t pan_cdeg;
    int16_t tilt_cdeg;
    uint8_t status;
} ProtoTelemetry;

static inline uint8_t proto_checksum(const uint8_t *b, uint8_t n)
{
    uint8_t x = 0;
    for (uint8_t i = 0; i < n; i++) {
        x ^= b[i];
    }
    return x;
}

static inline uint8_t proto_stage_from_flags(uint8_t flags)
{
    return (uint8_t)((flags & FLAG_STAGE_MASK) >> 4);
}

bool Proto_ParseDownlink(const uint8_t frame[PROTO_FRAME_LEN], ProtoCommand *out);
void Proto_BuildUplink(const ProtoTelemetry *tel, uint8_t frame[PROTO_FRAME_LEN]);

#endif /* ATIS_PROTOCOL_H */
