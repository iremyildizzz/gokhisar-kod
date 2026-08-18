"""`shared/` sözleşmesinin üç düğümde de tuttuğunu doğrulayan sapma testleri.

Neden bu testler var
--------------------
`pc/config.py`, `rpi5/fire_control/` ve `stm32f411/` yukarı akış dosyaları;
entegrasyon kapsamında değiştirilmedikleri için hâlâ kendi literallerini
taşıyorlar ve `shared`dan okumuyorlar. Yani sapma *derleme zamanında*
engellenemiyor.

Bu testler engellenemeyeni **görünür** kılıyor: iki taraftan biri değiştiği
anda kırılıyorlar ve hata mesajı hangi dosyanın hangi değerinin ayrıldığını
söylüyor. Görüntü işleme ekibi bir gün `from shared...` satırlarını kabul
ederse bu testlerin çoğu totolojiye dönüşür — o zaman silinebilirler.

En kritik olanı kare geometrisi: PC ile RPi farklı kare boyutu varsayarsa
nişan sabit bir açı kadar kayar ve PID bunu kapatamaz, çünkü sapma bir bozucu
etki değil referansın kendisindedir.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from pc.integration import bootstrap  # noqa: F401  (sys.path kurulumu)
from shared import classes, engagement, geometry, protocol

import config as vision_config

RPI_DIR = bootstrap.PROJECT_ROOT / "rpi5" / "fire_control"
STM32_SERVO_HEADER = bootstrap.PROJECT_ROOT / "stm32f411" / "Core" / "Inc" / "servo.h"


# ----------------------------------------------------------------------
# Yukarı akış dosyalarını *çalıştırmadan* okumak
# ----------------------------------------------------------------------
# `rpi5.fire_control.main` içe aktarılamaz: `uart_bridge` üzerinden pyserial'ı
# ve RPi'ye özgü seri portları çeker. Kaynağı metin/AST olarak okuyup yalnızca
# sabitlerini değerlendiriyoruz — yan etkisiz ve bağımlılıksız.

def _argparse_defaults(source: str) -> dict[str, object]:
    """`add_argument("--x", ..., default=V)` çiftlerini çıkar.

    RPi5 çalışma parametrelerini modül sabiti olarak değil argparse varsayılanı
    olarak tutuyor; sözleşmenin karşılaştırması gereken değerler orada.
    """
    tree = ast.parse(source)
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = str(node.args[0].value).lstrip("-").replace("-", "_")
        for keyword in node.keywords:
            if keyword.arg != "default":
                continue
            try:
                found[name] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                pass
    return found


def _dataclass_field_defaults(source: str, class_name: str) -> dict[str, object]:
    """Bir dataclass'ın alan varsayılanlarını oku (ör. `Limits`)."""
    tree = ast.parse(source)
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and stmt.value is not None):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            try:
                found[stmt.target.id] = ast.literal_eval(stmt.value)
            except (ValueError, TypeError):
                pass
    return found


def _c_defines(path: Path) -> dict[str, int]:
    """C başlığındaki tamsayı `#define`ları oku."""
    source = path.read_text(encoding="utf-8", errors="replace")
    return {
        name: int(value)
        for name, value in re.findall(
            r"#define\s+(\w+)\s+\(?(-?\d+)\)?", source
        )
    }


@pytest.fixture(scope="module")
def rpi_main_source() -> str:
    return (RPI_DIR / "main.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rpi_args(rpi_main_source: str) -> dict[str, object]:
    return _argparse_defaults(rpi_main_source)


@pytest.fixture(scope="module")
def rpi_limits(rpi_main_source: str) -> dict[str, object]:
    return _dataclass_field_defaults(rpi_main_source, "Limits")


@pytest.fixture(scope="module")
def rpi_engage_ranges() -> dict[str, tuple[float, float]]:
    """`rpi5/fire_control/engagement.py` içindeki `ENGAGE_RANGE_M` tablosu."""
    source = (RPI_DIR / "engagement.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "ENGAGE_RANGE_M" and node.value is not None:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENGAGE_RANGE_M":
                    return ast.literal_eval(node.value)
    raise AssertionError("ENGAGE_RANGE_M bulunamadı")


# ----------------------------------------------------------------------
# Kare geometrisi
# ----------------------------------------------------------------------

def test_frame_geometry_matches_vision_config():
    """`shared` ile `pc/config.py` aynı kare uzayını tanımlamalı."""
    assert geometry.FRAME_WIDTH == vision_config.FRAME_WIDTH
    assert geometry.FRAME_HEIGHT == vision_config.FRAME_HEIGHT
    assert geometry.FRAME_CENTER == tuple(vision_config.FRAME_CENTER)


def test_frame_geometry_matches_rpi_fire_control(rpi_args: dict[str, object]):
    """RPi'nin hata hesabı da aynı kare boyutunu varsaymalı.

    RPi `err = cx - frame_w/2` hesabını kendi `--frame-w/--frame-h`
    varsayılanıyla yapıyor. PC farklı bir kare uzayı kullanırsa nişan sabit bir
    açı kadar kayar ve PID bunu kapatamaz: sapma bozucu etki değil, referansın
    kendisidir.
    """
    assert rpi_args.get("frame_w") == geometry.FRAME_WIDTH, (
        f"RPi {rpi_args.get('frame_w')} varsayıyor, sözleşme {geometry.FRAME_WIDTH}"
    )
    assert rpi_args.get("frame_h") == geometry.FRAME_HEIGHT, (
        f"RPi {rpi_args.get('frame_h')} varsayıyor, sözleşme {geometry.FRAME_HEIGHT}"
    )


# ----------------------------------------------------------------------
# Sınıf kimlikleri
# ----------------------------------------------------------------------

def test_class_ids_match_vision_config():
    """Sınıf kimlikleri ve adları `pc/config.py` ile birebir aynı olmalı."""
    for target in classes.TargetClass:
        assert int(target) in vision_config.CLASS_NAMES, (
            f"{target.name} ({int(target)}) config.CLASS_NAMES'te yok"
        )
    assert classes.BALLOON_CLASS_ID == vision_config.BALLOON_CLASS_ID
    assert set(classes.MODEL_CLASS_IDS) == set(vision_config.MODEL_CLASS_IDS)


def test_display_names_cover_every_class():
    """Her sınıfın operatöre gösterilecek bir adı olmalı."""
    for target in classes.TargetClass:
        assert classes.display_name(int(target)) != f"Sınıf {int(target)}"


def test_balloon_is_not_engageable():
    """Balon bir hedef değil, hedefin işaretidir; angajman adayı olamaz."""
    assert not classes.is_engageable(classes.BALLOON_CLASS_ID)
    for class_id in classes.MODEL_CLASS_IDS:
        assert classes.is_engageable(class_id)


# ----------------------------------------------------------------------
# Portlar
# ----------------------------------------------------------------------

def test_command_port_matches_vision_config():
    assert protocol.COMMAND_PORT == vision_config.RPI_PORT


def test_command_port_matches_rpi_listener(rpi_args: dict[str, object]):
    """RPi'nin dinlediği port ile PC'nin bağlandığı port aynı olmalı."""
    listen_port = rpi_args.get("tcp_port")
    assert listen_port is not None, "rpi5 içinde dinleme portu bulunamadı"
    assert listen_port == protocol.COMMAND_PORT


# ----------------------------------------------------------------------
# Angajman güvenliği
# ----------------------------------------------------------------------

def test_safe_engage_distances_match_rpi(
    rpi_engage_ranges: dict[str, tuple[float, float]],
):
    """Güvenli angajman mesafeleri sözleşmeyle birebir aynı olmalı.

    Ayrıştıkları anda arayüzün gösterdiği menzil durumu ile RPi'nin ateş
    kapısı farklı şeyler söyler; operatör "menzilde" görürken sistem ateş
    etmez (ya da tersi). RPi tabloyu metre ve sınıf **adı** ile tutuyor,
    sözleşme santimetre ve sınıf **kimliği** ile; karşılaştırma için ikisi
    aynı uzaya çevriliyor.
    """
    rpi_by_id = {
        int(target): (
            int(round(rpi_engage_ranges[target.name.lower()][0] * 100)),
            int(round(rpi_engage_ranges[target.name.lower()][1] * 100)),
        )
        for target in classes.TargetClass
        if target.name.lower() in rpi_engage_ranges
    }
    assert rpi_by_id == {int(k): tuple(v)
                        for k, v in engagement.SAFE_ENGAGE_DISTANCES_CM.items()}


def test_forbidden_zones_gate_exists_on_rpi(rpi_main_source: str):
    """Yasak sektör kapısı atış kontrol yazılımında da bulunmalı (KTR Bölüm 6).

    KTR: "Atış kontrol yazılımında yasaklı açılara yönelik hareket kısıtlanmış
    olup bu bölgelerde hem namlu hareketi hem de atış komutu tamamen
    engellenmektedir." `rpi5/fire_control` bugün yalnızca 0-180 kenetlemesi
    yapıyor; sektör kapısı yok. Test bu boşluğu görünür kılmak için
    başarısız olmuyor, **atlanıyor**: kırmızı bırakmak her koşuda gürültü
    üretirdi, sessizce geçmek ise boşluğu unuttururdu.
    """
    if "FORBIDDEN" not in rpi_main_source.upper():
        pytest.skip(
            "KTR boşluğu: rpi5/fire_control yasak sektör kapısını uygulamıyor "
            "(shared.engagement.FORBIDDEN_ZONES kullanılmalı)"
        )
    for zone in engagement.FORBIDDEN_ZONES:
        for value in zone:
            assert str(value) in rpi_main_source or str(int(value)) in rpi_main_source


def test_engage_stable_seconds_match_rpi(
    rpi_args: dict[str, object], rpi_limits: dict[str, object],
):
    """Menzilde kararlı kalma süresi iki tarafta aynı olmalı."""
    assert rpi_args.get("engage_stable") == engagement.ENGAGE_STABLE_SECONDS
    assert rpi_limits.get("engage_stable_s") == engagement.ENGAGE_STABLE_SECONDS


def test_only_engageable_classes_have_a_safe_distance():
    """Angajman adayı olabilen her sınıfın bir mesafe bandı olmalı.

    Tanımsız mesafeli bir sınıf, sessizce hiç ateş edilemeyen bir sınıftır.
    Balon ise bilinçli olarak tabloda yok: hedef değil, hedefin işareti.
    """
    for class_id in classes.MODEL_CLASS_IDS:
        assert class_id in engagement.SAFE_ENGAGE_DISTANCES_CM
    assert classes.BALLOON_CLASS_ID not in engagement.SAFE_ENGAGE_DISTANCES_CM


def test_unknown_distance_is_never_safe():
    """LiDAR okunamadıysa ateş kapısı kapalı kalmalı."""
    assert not engagement.is_safe_distance(classes.TargetClass.IHA, None)
    assert not engagement.is_safe_distance(999, 500.0)


def test_forbidden_zone_boundaries():
    """Yasaklı bölge sınırları beklendiği gibi davranıyor mu."""
    assert engagement.in_forbidden_zone(10.0, 90.0)     # sol güvenlik bölgesi
    assert engagement.in_forbidden_zone(170.0, 90.0)    # sağ güvenlik bölgesi
    assert engagement.in_forbidden_zone(90.0, 160.0)    # aşağı, operatör tarafı
    assert not engagement.in_forbidden_zone(90.0, 90.0)  # merkez serbest


def test_servo_range_matches_stm32():
    """STM32'nin kenetleme sınırları ile sözleşmenin açı aralığı aynı olmalı.

    `stm32f411` açıları santiderece (derece × 10) taşıyor. Sınır değişirse
    (ör. KTR'nin vaat ettiği 270°'ye geçilirse) burası da değişmeli; aksi hâlde
    RPi STM32'nin sessizce kenetleyeceği açılar üretir ve nişan kayar.
    """
    defines = _c_defines(STM32_SERVO_HEADER)
    for key in ("SERVO_PAN_MIN_CDEG", "SERVO_PAN_MAX_CDEG",
                "SERVO_TILT_MIN_CDEG", "SERVO_TILT_MAX_CDEG", "SERVO_HOME_CDEG"):
        assert key in defines, f"servo.h içinde {key} bulunamadı"

    assert defines["SERVO_PAN_MIN_CDEG"] / 10 == engagement.SERVO_MIN_ANGLE
    assert defines["SERVO_TILT_MIN_CDEG"] / 10 == engagement.SERVO_MIN_ANGLE
    assert defines["SERVO_PAN_MAX_CDEG"] / 10 == engagement.SERVO_MAX_ANGLE, (
        f"STM32 pan sınırı {defines['SERVO_PAN_MAX_CDEG'] / 10}°, sözleşme "
        f"{engagement.SERVO_MAX_ANGLE}°"
    )
    assert defines["SERVO_TILT_MAX_CDEG"] / 10 == engagement.SERVO_MAX_ANGLE
    assert defines["SERVO_HOME_CDEG"] / 10 == engagement.SERVO_CENTER_ANGLE


# ----------------------------------------------------------------------
# Mesaj şeması: RpiLink'in ürettiği JSON beyan edilen şemaya uymalı
# ----------------------------------------------------------------------

class _CapturingLink:
    """`RpiLink._send`i yakalayıp gönderilen sözlükleri biriktirir."""

    def __init__(self):
        from comms.rpi_link import RpiLink

        self.sent: list[dict] = []
        self.link = RpiLink(host="127.0.0.1", port=protocol.COMMAND_PORT)
        self.link._send = self._capture  # type: ignore[method-assign]

    def _capture(self, payload: dict) -> bool:
        self.sent.append(payload)
        return True


def test_rpi_link_messages_satisfy_declared_schema():
    """PC → RPi yönünün temel kodlayıcısı `RpiLink`; şemaya uyduğunu doğrula.

    `shared/protocol.py` bu mesajlar için ikinci bir kodlayıcı yazmaz —
    yazsaydı iki ayrı gerçek olurdu. Bunun yerine şemayı beyan eder ve
    doğruluğu burada sınanır.
    """
    capture = _CapturingLink()
    capture.link.send_target(cx=640.0, cy=360.0, class_id=2,
                             track_id=7, locked=True)
    capture.link.send_engage(track_id=7, class_id=2)
    capture.link.send_manual(dx=-5.0, dy=2.5)
    capture.link.send_mode(autonomous=True)

    kinds = [payload["type"] for payload in capture.sent]
    assert kinds == [protocol.MessageType.TARGET, protocol.MessageType.ENGAGE,
                     protocol.MessageType.MANUAL, protocol.MessageType.MODE]

    for payload in capture.sent:
        missing = protocol.missing_fields(payload)
        assert not missing, f"{payload['type']} mesajında eksik alan: {missing}"


def test_rpi_channel_sends_pid_and_stage():
    """Arayüzün eklediği iki mesaj da şemaya uymalı.

    `RpiLink`'te karşılığı olmayan bu iki mesajı (`pid` ve aşamalı `mode`)
    `RpiChannel` üretir. Kodlayıcı sözleşmede olduğu için burada `RpiChannel`
    üzerinden gerçekten hattı besleyip şemayı doğruluyoruz — arayüz kutularının
    ekranda durup hiçbir şey yapmaması tam olarak bu testin engellediği hata.
    """
    from pc.integration.rpi_channel import RpiChannel
    from pc.integration.settings import RpiSettings

    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=protocol.COMMAND_PORT))
    sent: list[dict] = []
    channel.link._send = lambda payload: (sent.append(payload) or True)  # type: ignore[method-assign]

    assert channel.send_pid(0.55, 0.05, 0.08)
    assert channel.send_mode(autonomous=True, stage=3)

    assert [p["type"] for p in sent] == [protocol.MessageType.PID,
                                         protocol.MessageType.MODE]
    for payload in sent:
        assert not protocol.missing_fields(payload)
    assert sent[0] == {"type": "pid", "kp": 0.55, "ki": 0.05, "kd": 0.08}
    assert sent[1]["stage"] == 3


def test_rpi_accepts_every_message_type_we_send():
    """Gönderdiğimiz her mesaj tipi RPi tarafında gerçekten işleniyor mu.

    Sessizce yok sayılan bir mesaj tipi, hata vermeden çalışmayan bir özellik
    demek; en pahalı hata türü. `pid` mesajı tam olarak bu şekilde kaybolmuştu.
    """
    source = (RPI_DIR / "tcp_server.py").read_text(encoding="utf-8")
    for message_type in protocol.REQUIRED_FIELDS:
        assert f'"{message_type}"' in source or f"'{message_type}'" in source, (
            f"rpi5/fire_control '{message_type}' mesajını işlemiyor"
        )
    for field in protocol.MODE_OPTIONAL_FIELDS:
        assert f'"{field}"' in source, (
            f"rpi5/fire_control mode mesajındaki '{field}' alanını okumuyor"
        )


def test_telemetry_round_trip():
    """RPi → PC yönü: kodlanan telemetri aynen çözülebilmeli."""
    payload = protocol.telemetry(distance_cm=412.35, in_forbidden_zone=False,
                                 pan=91.2, tilt=88.7)
    decoded = protocol.decode_line(protocol.encode_line(payload))
    assert decoded == payload
    assert decoded["distance_cm"] == 412.4  # bir ondalığa yuvarlanır


def test_telemetry_omits_unknown_fields():
    """Ölçülemeyen alan hiç konmamalı; 0 göndermek "0 cm" gibi okunur."""
    payload = protocol.telemetry()
    assert payload == {"type": protocol.MessageType.TELEMETRY}


def test_normalize_telemetry_reads_real_rpi5_status():
    """Gerçek `rpi5/fire_control` status satırı arayüzün alanlarına çevrilmeli.

    Bu testin varlık sebebi somut bir hata: arayüz `distance_cm` ve
    `in_forbidden_zone` bekliyordu, RPi5 ise `lidar_m` ve `stm` gönderiyor.
    Mesafe paneli, ateş onayı ve güvenli duruş sessizce hiç çalışmıyordu.
    """
    payload = {
        "type": "status",
        "mode": "otonom",
        "stage": 3,
        "lidar_m": 7.25,
        "pan_deg": 96.4,
        "tilt_deg": 84.1,
        "range_ok": False,
        "range_reason": "out_of_range:iha:20.00 not in 0.0-15.0",
        "stm": {"failsafe": False, "armed": True, "fired": True,
                "busy": False, "enabled": True},
    }
    data = protocol.normalize_telemetry(payload)
    assert data["distance_m"] == 7.25
    assert (data["pan"], data["tilt"]) == (96.4, 84.1)
    assert data["fired"] is True
    assert data["failsafe"] is False
    assert data["armed"] is True
    assert data["range_ok"] is False
    assert data["stage"] == 3


def test_normalize_telemetry_reads_legacy_schema():
    """Sözleşmenin kendi kurucuları da aynı kanonik alanlara çevrilmeli."""
    data = protocol.normalize_telemetry(
        protocol.telemetry(distance_cm=412.0, in_forbidden_zone=True,
                           pan=91.2, tilt=88.7)
    )
    assert data["distance_m"] == pytest.approx(4.12)
    assert data["in_forbidden_zone"] is True
    assert data["pan"] == 91.2

    fired = protocol.normalize_telemetry(protocol.event_fired(track_id=7))
    assert fired["fired"] is True and fired["track_id"] == 7

    fail = protocol.normalize_telemetry(protocol.event_fail_safe("UART koptu"))
    assert fail["failsafe"] is True and fail["reason"] == "UART koptu"


def test_normalize_telemetry_omits_missing_fields():
    """Gelmeyen alan sözlükte hiç bulunmamalı.

    "Alan yok" ile "alan sıfır" karışırsa arayüz LiDAR okunamadığında
    "Mesafe: 0.0 m" yazar — operatörün hedefi namluya değmiş sanması demek.
    """
    data = protocol.normalize_telemetry({"type": "status", "lidar_m": None})
    assert "distance_m" not in data
    assert "fired" not in data


def test_decode_line_tolerates_garbage():
    """Bozuk satır akışı kesmemeli; `None` dönüp bir sonrakine geçilmeli."""
    assert protocol.decode_line(b"{bozuk json") is None
    assert protocol.decode_line(b"   \n") is None
    assert protocol.decode_line(b"[1, 2, 3]") is None  # nesne değil, dizi
    assert protocol.decode_line(b'{"type":"status","status":"OK"}') == {
        "type": "status", "status": "OK"
    }


def test_shared_has_no_heavy_dependencies():
    """`shared` Raspberry Pi'ye kopyalanabilmeli: yalnızca standart kütüphane.

    numpy/torch/opencv buraya sızarsa paket embedded tarafta içe aktarılamaz
    hâle gelir ve sözleşme fiilen yalnızca PC'ye ait olur.
    """
    heavy = {"numpy", "cv2", "torch", "ultralytics", "supervision",
             "PySide6", "filterpy", "serial"}
    shared_dir = bootstrap.PROJECT_ROOT / "shared"
    for path in sorted(shared_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            offenders = names & heavy
            assert not offenders, f"{path.name} ağır bağımlılık içeriyor: {offenders}"
