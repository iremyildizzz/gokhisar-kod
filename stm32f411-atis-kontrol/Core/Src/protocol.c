#include "protocol.h"

bool Proto_ParseDownlink(const uint8_t frame[PROTO_FRAME_LEN], ProtoCommand *out)
{
    if (frame == 0 || out == 0) {
        return false;
    }

    out->valid = false;

    if (frame[0] != PROTO_SYNC_DOWN) {
        return false;
    }

    if (proto_checksum(frame, 6) != frame[6]) {
        return false;
    }

    out->pan_cdeg  = (int16_t)((uint16_t)frame[1] | ((uint16_t)frame[2] << 8));
    out->tilt_cdeg = (int16_t)((uint16_t)frame[3] | ((uint16_t)frame[4] << 8));
    out->flags     = frame[5];
    out->stage     = proto_stage_from_flags(frame[5]);
    out->valid     = true;
    return true;
}

void Proto_BuildUplink(const ProtoTelemetry *tel, uint8_t frame[PROTO_FRAME_LEN])
{
    frame[0] = PROTO_SYNC_UP;
    frame[1] = tel->status;
    frame[2] = (uint8_t)(tel->pan_cdeg & 0xFF);
    frame[3] = (uint8_t)((tel->pan_cdeg >> 8) & 0xFF);
    frame[4] = (uint8_t)(tel->tilt_cdeg & 0xFF);
    frame[5] = (uint8_t)((tel->tilt_cdeg >> 8) & 0xFF);
    frame[6] = proto_checksum(frame, 6);
}
