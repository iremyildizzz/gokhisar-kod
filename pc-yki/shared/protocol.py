"""PC ↔ Raspberry Pi haberleşme sözleşmesi — KTR 4.3, Şekil 4.13.

Taşıma: TCP, satır sonu ile ayrılmış JSON nesneleri (newline-delimited JSON).
KTR "veri güvenilirliği gereksinimi nedeniyle TCP/IP" ve "esnekliğin artırılması
amacıyla JSON formatı" diyor; uygulama bunu birebir karşılıyor.

İki yön farklı sahiplere ait
----------------------------
**PC → RPi.** Temel mesajların (target/engage/manual/mode) kodlayıcısı görüntü
işleme reposundaki `pc/vision/comms/rpi_link.py` (`RpiLink`). Orası
değiştirilmediği için arayüzün ihtiyaç duyduğu ek mesajlar (`pid`, `mode`
içindeki `stage`) `pc/integration/rpi_channel.py` tarafından bu sözleşmeye göre
üretilir. Şema burada **beyan ediliyor**, `tests/test_contract.py` iki tarafın
da gerçekten bu alanları ürettiğini doğruluyor.

**RPi → PC.** Karşı taraf artık `rpi5/fire_control/` paketi ve gerçekten
telemetri gönderiyor: 200 ms'de bir `type="status"` satırı (LiDAR mesafesi,
uygulanan pan/tilt, STM32 durum bayrakları). Ancak alan adları buradaki eski
`telemetry()` kurucusundan farklı. İki şemayı arayüzde ayrı ayrı ele almak
"hangi alan gerçek" sorusunu her panele taşırdı; bunun yerine
`normalize_telemetry()` iki şemayı tek kanonik sözlüğe indirir ve arayüz
yalnızca onu okur.
"""

import json
from typing import Any

# ----------------------------------------------------------------------
# Portlar
# ----------------------------------------------------------------------

#: PC → RPi komut kanalı (TCP). `rpi5/fire_control/tcp_server.py` bu portu dinler.
COMMAND_PORT: int = 5005

#: RPi → PC video akışı (UDP, RTP/JPEG). GStreamer `udpsrc` bu portu dinler.
VIDEO_PORT: int = 5000

#: Saha kurulumunda RPi'nin varsayılan adresi; ortam değişkeniyle ezilebilir.
DEFAULT_RPI_HOST: str = "192.168.1.100"


# ----------------------------------------------------------------------
# Mesaj tipleri
# ----------------------------------------------------------------------

class MessageType:
    """`type` alanının alabileceği değerler."""

    # PC → RPi
    TARGET = "target"
    ENGAGE = "engage"
    MANUAL = "manual"
    MODE = "mode"
    #: Operatörün YKİ'den ayarladığı PID katsayıları (KTR 4.3: "sistemin PID
    #: katsayıları operatöre görüntülenmekte olup ayrı olarak ayarlanabilmektedir").
    PID = "pid"

    # RPi → PC
    TELEMETRY = "telemetry"
    EVENT = "event"
    STATUS = "status"


class EventName:
    """`event` mesajlarındaki olay adları."""

    FIRED = "fired"
    FAIL_SAFE = "fail_safe"


# ----------------------------------------------------------------------
# Şema beyanı (PC → RPi yönü; kodlayıcı RpiLink'te)
# ----------------------------------------------------------------------

#: Mesaj tipi -> zorunlu alan adları. `t` gibi bilgilendirme amaçlı alanlar
#: zorunlu sayılmaz; RPi onları okumuyor.
REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    MessageType.TARGET: frozenset({"cx", "cy", "class_id", "track_id", "locked"}),
    MessageType.ENGAGE: frozenset({"track_id", "class_id"}),
    MessageType.MANUAL: frozenset({"dx", "dy"}),
    MessageType.MODE: frozenset({"autonomous"}),
    MessageType.PID: frozenset({"kp", "ki", "kd"}),
}

#: `mode` mesajındaki isteğe bağlı yarışma aşaması. `rpi5/fire_control` bu alanı
#: okuyup Aşama-3 LiDAR menzil kapısını açıyor; alan gönderilmezse aşamayı
#: kendi başına 1/2 olarak tahmin eder ve 3'e hiç çıkamaz. Arayüzdeki
#: "2. AŞAMA / 3. AŞAMA" seçiminin donanımda karşılığı olması bu alana bağlı.
MODE_OPTIONAL_FIELDS: frozenset[str] = frozenset({"stage"})

#: Geçerli yarışma aşamaları. 1 = manuel görev, 2 = tüm hedefler düşman,
#: 3 = renk tabanlı IFF + LiDAR menzil doğrulaması.
STAGES: tuple[int, ...] = (1, 2, 3)

#: Manuel yönelim komutu **mutlak açı değil artım** taşır:
#: `rpi5/fire_control/tcp_server.py` gelen dx/dy'yi `manual_dpan/manual_dtilt`
#: birikimine ekliyor, ana döngü de bunu mevcut açının üzerine uyguluyor.
#: Arayüz mutlak açı gösterdiği için `RpiLinkWorker` çevrimi yapar; bu not
#: sözleşmenin en kolay yanlış anlaşılan maddesi.
MANUAL_IS_DELTA: bool = True


def missing_fields(payload: dict[str, Any]) -> frozenset[str]:
    """Bir mesajda eksik olan zorunlu alanlar; bilinmeyen tipte boş küme."""
    required = REQUIRED_FIELDS.get(payload.get("type", ""))
    if required is None:
        return frozenset()
    return required - payload.keys()


# ----------------------------------------------------------------------
# PC → RPi kurucuları (`RpiLink`'te karşılığı olmayan mesajlar)
# ----------------------------------------------------------------------

def pid(kp: float, ki: float, kd: float) -> dict[str, Any]:
    """Operatörün ayarladığı PID katsayıları (pan ve tilt eksenine ortak).

    `RpiLink` bu mesajı üretmiyor ve `pc/vision/` değiştirilmiyor; kodlayıcı
    bu yüzden sözleşmede duruyor ve `RpiChannel` onu kullanıyor.
    """
    return {"type": MessageType.PID, "kp": float(kp), "ki": float(ki), "kd": float(kd)}


def mode(autonomous: bool, stage: int | None = None) -> dict[str, Any]:
    """Çalışma kipi (+ isteğe bağlı yarışma aşaması).

    `RpiLink.send_mode()` yalnızca `autonomous` gönderiyor. Aşama bilgisi
    olmadan RPi tarafındaki Aşama-3 kapıları hiç açılmadığı için arayüz bu
    kurucuyu kullanır.
    """
    payload: dict[str, Any] = {"type": MessageType.MODE, "autonomous": bool(autonomous)}
    if stage is not None:
        payload["stage"] = int(stage)
    return payload


# ----------------------------------------------------------------------
# Satır kodlama / çözme
# ----------------------------------------------------------------------

def encode_line(payload: dict[str, Any]) -> bytes:
    """Bir mesajı hat üzerinde taşınacak bayt satırına çevir."""
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def decode_line(line: bytes | str) -> dict[str, Any] | None:
    """Tek bir satırı çöz; bozuk satırda `None` (bağlantı düşürülmez).

    Gürültülü bir satır yüzünden akışı kesmek yanlış olur: TCP üzerinde tek
    bir bozuk kare, sonraki geçerli telemetriyi kaybetmeyi gerektirmez.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


# ----------------------------------------------------------------------
# RPi → PC telemetri kurucuları (bu yönün sahibi shared)
# ----------------------------------------------------------------------

def telemetry(distance_cm: float | None = None,
              in_forbidden_zone: bool | None = None,
              pan: float | None = None,
              tilt: float | None = None) -> dict[str, Any]:
    """Periyodik durum telemetrisi.

    Alanlar isteğe bağlı: LiDAR okunamadığında `distance_cm` hiç konmaz.
    Arayüz bilmediği alanı sessizce yok sayar, eksik alanı da — böylece RPi
    tarafı yeni alan eklediğinde PC'de kod değişikliği gerekmez.
    """
    payload: dict[str, Any] = {"type": MessageType.TELEMETRY}
    if distance_cm is not None:
        payload["distance_cm"] = round(float(distance_cm), 1)
    if in_forbidden_zone is not None:
        payload["in_forbidden_zone"] = bool(in_forbidden_zone)
    if pan is not None:
        payload["pan"] = round(float(pan), 1)
    if tilt is not None:
        payload["tilt"] = round(float(tilt), 1)
    return payload


def event_fired(track_id: int | None = None) -> dict[str, Any]:
    """STM32'nin "ateşleme gerçekleşti" bildirimi (KTR 4.2.2.8 kapalı çevrim).

    PC bu mesajı aldığı anda imha değerlendirme sayacını başlatır; bu yüzden
    zamanlaması, PC'nin angajman *talebi* gönderdiği andan daha doğrudur.
    """
    payload: dict[str, Any] = {"type": MessageType.EVENT, "event": EventName.FIRED}
    if track_id is not None:
        payload["track_id"] = int(track_id)
    return payload


def event_fail_safe(reason: str) -> dict[str, Any]:
    """Güvenli duruşa geçiş bildirimi (KTR 4.4.2 FAIL_SAFE)."""
    return {"type": MessageType.EVENT, "event": EventName.FAIL_SAFE, "reason": reason}


def status(text: str) -> dict[str, Any]:
    """Serbest metinli durum satırı; arayüzün durum çubuğunda gösterilir."""
    return {"type": MessageType.STATUS, "status": text}


# ----------------------------------------------------------------------
# RPi → PC telemetri normalleştirme
# ----------------------------------------------------------------------

#: `rpi5/fire_control/main.py`'nin periyodik `status` satırındaki alan adları
#: yukarıdaki `telemetry()` kurucusundan farklı: mesafe metre cinsinden
#: `lidar_m`, açılar `pan_deg`/`tilt_deg`, STM32 bayrakları `stm` sözlüğünde.
#: Arayüzün iki şemayı ayrı ayrı bilmesi gerekmesin diye çeviri burada.
def normalize_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Gelen telemetriyi kanonik sözlüğe çevir.

    Dönen sözlükte yalnızca **gerçekten gelen** alanlar bulunur; böylece
    arayüz "alan yok" ile "alan sıfır" arasını ayırt edebilir. Desteklenen
    girişler: `rpi5/fire_control` `status` satırı ve bu modüldeki
    `telemetry()` / `event_*()` / `status()` kurucuları.

    Kanonik alanlar: ``distance_m``, ``pan``, ``tilt``, ``in_forbidden_zone``,
    ``fired``, ``failsafe``, ``armed``, ``enabled``, ``range_ok``,
    ``range_reason``, ``track_id``, ``reason``, ``status_text``, ``mode``,
    ``stage``.
    """
    out: dict[str, Any] = {}

    def _num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    # --- Mesafe: rpi5 metre, eski şema santimetre ---
    lidar_m = _num(payload.get("lidar_m"))
    if lidar_m is not None:
        out["distance_m"] = lidar_m
    else:
        distance_cm = _num(payload.get("distance_cm"))
        if distance_cm is not None:
            out["distance_m"] = distance_cm / 100.0

    # --- Uygulanan servo açıları ---
    for canonical, keys in (("pan", ("pan_deg", "pan")), ("tilt", ("tilt_deg", "tilt"))):
        for key in keys:
            value = _num(payload.get(key))
            if value is not None:
                out[canonical] = value
                break

    if "in_forbidden_zone" in payload:
        out["in_forbidden_zone"] = bool(payload["in_forbidden_zone"])

    # --- STM32 durum bayrakları (rpi5: iç içe `stm` sözlüğü) ---
    stm = payload.get("stm")
    if isinstance(stm, dict):
        for flag in ("fired", "armed", "failsafe", "enabled", "busy"):
            if flag in stm:
                out[flag] = bool(stm[flag])

    # --- Eski şema: ayrık olay mesajları ---
    event = payload.get("event")
    if event == EventName.FIRED:
        out["fired"] = True
    elif event == EventName.FAIL_SAFE:
        out["failsafe"] = True
    if isinstance(payload.get("reason"), str):
        out["reason"] = payload["reason"]
    if isinstance(payload.get("range_reason"), str):
        out["range_reason"] = payload["range_reason"]
    if "range_ok" in payload:
        out["range_ok"] = bool(payload["range_ok"])
    if isinstance(payload.get("track_id"), int):
        out["track_id"] = payload["track_id"]
    if isinstance(payload.get("mode"), str):
        out["mode"] = payload["mode"]
    if isinstance(payload.get("stage"), int):
        out["stage"] = payload["stage"]
    if isinstance(payload.get("status"), str):
        out["status_text"] = payload["status"]

    return out
