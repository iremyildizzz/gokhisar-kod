"""Entegrasyon katmanı testleri.

Kapsam bilerek dar tutuldu: burada YOLO'nun doğru tespit yapıp yapmadığı
sınanmıyor (o `pc/vision/tests/test_modules.py`'nin ve sahanın işi). Sınanan şey,
iki deponun birleştiği yerlerdeki sözleşmeler:

* sınıf kimliği çevirisi doğru mu,
* sistem durum makinesi boru hattı çıktısından beklenen durumu türetiyor mu,
* RPi telemetrisi parçalı TCP paketlerinden doğru ayrıştırılıyor mu,
* mutlak açı → artım dönüşümü doğru mu.

Bunlar test edilmezse hata ancak sahada, taret yanlış yöne dönerken görülür.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pc.integration.class_map import ClassMap, normalize  # noqa: E402
from pc.integration.rpi_channel import RpiChannel  # noqa: E402
from pc.integration.settings import RpiSettings, Settings  # noqa: E402
from shared.classes import BALLOON_CLASS_ID, MODEL_CLASS_IDS  # noqa: E402
from pc.integration.system_state import (  # noqa: E402
    SystemState,
    SystemStateMachine,
)

import config as vision_config  # noqa: E402


# ----------------------------------------------------------------------
# Sınıf eşlemesi
# ----------------------------------------------------------------------
def test_normalize_handles_turkish_and_separators():
    assert normalize("Mini-Micro_İHA") == "mini micro iha"
    assert normalize("  Savaş Uçağı ") == "savas ucagi"


def test_model_class_ids_are_remapped_to_config_space():
    """Eğitilmiş modelin sınıf sırası `pc/config.py` ile aynı değil.

    Model: 0=helikopter 1=iha 2=jet 3=mini-micro-iha 4=rocket
    config: 0=füze 1=helikopter 2=İHA 3=uçak 4=balon

    Çeviri yapılmazsa RPi'ye giden `class_id` yanlış güvenli mesafe tablosunu
    seçer; bu doğrudan bir güvenlik hatasıdır.
    """
    mapping = ClassMap({0: "helikopter", 1: "iha", 2: "jet",
                        3: "mini-micro-iha", 4: "rocket"})
    assert mapping.to_config_id(0) == 1        # helikopter
    assert mapping.to_config_id(1) == 2        # İHA
    assert mapping.to_config_id(2) == 3        # uçak
    assert mapping.to_config_id(3) == 2        # mini/mikro İHA de İHA'dır
    assert mapping.to_config_id(4) == 0        # rocket → balistik füze
    assert not mapping.has_balloon_class       # modelde balon yok


def test_unknown_class_is_dropped_not_guessed():
    mapping = ClassMap({0: "iha", 1: "kus"})
    assert mapping.to_config_id(0) == 2
    assert mapping.to_config_id(1) is None
    assert "kus" in mapping.unmapped_names


def test_display_names_are_human_readable():
    mapping = ClassMap({0: "mini-micro-iha", 1: "jet"})
    assert mapping.display_name(0) == "Mini/Micro İHA"
    assert mapping.display_name(1) == "Savaş Uçağı (Jet)"


def test_matcher_receives_remapped_detector():
    """`TargetMatcher` ikinci yöntemde YOLO'yu kendi çağırıyor.

    O çağrı ham model kimliği döndürürse, matcher'ın `MODEL_CLASS_IDS`
    süzgeci ham uzayda 4 numaralı **rocket**'i "maket değil" sayıp eler.
    Sarmalayıcı devredeyse füze de doğrulanabilir olmalı.
    """
    from pc.integration.vision_pipeline import _RemappedYolo

    class _FakeYolo:
        model = type("m", (), {"names": {}})()

        def detect(self, frame):
            return [_det(0)]

        def detect_in_roi(self, frame, roi):
            return [_det(4)]        # ham uzayda "rocket"

    def _det(class_id):
        from detection.yolo_detector import Detection
        return Detection(0.0, 0.0, 10.0, 10.0, conf=0.9, class_id=class_id)

    mapping = ClassMap({0: "helikopter", 1: "iha", 2: "jet",
                        3: "mini-micro-iha", 4: "rocket"})

    def remap(dets):
        import dataclasses
        out = []
        for det in dets:
            config_id = mapping.to_config_id(det.class_id)
            if config_id is not None:
                out.append(dataclasses.replace(det, class_id=config_id))
        return out

    wrapped = _RemappedYolo(_FakeYolo(), remap)
    roi_dets = wrapped.detect_in_roi(None, (0, 0, 10, 10))
    assert roi_dets[0].class_id == 0                      # füze
    assert roi_dets[0].class_id in MODEL_CLASS_IDS


def test_config_ids_cover_rpi_safe_distance_table():
    """RPi'nin güvenli mesafe tablosu 0-4 arası kimlik bekliyor."""
    valid = set(MODEL_CLASS_IDS) | {BALLOON_CLASS_ID}
    mapping = ClassMap({0: "helikopter", 1: "iha", 2: "jet",
                        3: "mini-micro-iha", 4: "rocket"})
    for model_id in range(5):
        assert mapping.to_config_id(model_id) in valid


# ----------------------------------------------------------------------
# Sistem durum makinesi
# ----------------------------------------------------------------------
@dataclass
class FakeTrack:
    track_id: int = 1
    validated: bool = False


@dataclass
class FakeResult:
    tracks: list = field(default_factory=list)
    detections: list = field(default_factory=list)
    candidate: object | None = None
    locked: bool = False
    new_track_ids: list = field(default_factory=list)
    lost_track_ids: list = field(default_factory=list)
    destroyed_track_ids: list = field(default_factory=list)


def test_state_machine_starts_idle_and_ignores_results_until_started():
    fsm = SystemStateMachine()
    assert fsm.state is SystemState.IDLE
    assert fsm.update(FakeResult(detections=[object()])) is SystemState.IDLE


def test_state_machine_progresses_through_engagement_chain():
    fsm = SystemStateMachine()
    fsm.on_start()
    assert fsm.state is SystemState.SCANNING

    assert fsm.update(FakeResult(detections=[object()])) is SystemState.DETECT
    assert fsm.update(FakeResult(
        detections=[object()],
        tracks=[FakeTrack(validated=True)],
    )) is SystemState.TRACK
    assert fsm.update(FakeResult(
        tracks=[FakeTrack(validated=True)],
        candidate=FakeTrack(),
    )) is SystemState.EVALUATE
    assert fsm.update(FakeResult(
        tracks=[FakeTrack(validated=True)],
        candidate=FakeTrack(),
        locked=True,
    )) is SystemState.TARGET_LOCK


def test_lock_takes_precedence_over_weaker_conditions():
    """Kilit varken 'TESPİT' göstermek operatörü yanıltırdı."""
    fsm = SystemStateMachine()
    fsm.on_start()
    result = FakeResult(detections=[object()], tracks=[FakeTrack()],
                        candidate=FakeTrack(), locked=True)
    assert fsm.update(result) is SystemState.TARGET_LOCK


def test_fail_safe_is_latched_until_reset():
    """Hatanın kendiliğinden 'geçmesi' sahte bir güven yaratır."""
    fsm = SystemStateMachine()
    fsm.on_start()
    fsm.on_fail_safe("model yok")
    assert fsm.state is SystemState.FAIL_SAFE
    assert fsm.update(FakeResult(locked=True)) is SystemState.FAIL_SAFE
    assert fsm.fail_safe_reason == "model yok"
    assert fsm.on_reset() is SystemState.IDLE
    assert fsm.fail_safe_reason is None


def test_destroyed_state_is_held_visible():
    fsm = SystemStateMachine()
    fsm.on_start()
    fsm.update(FakeResult(tracks=[FakeTrack()]))
    assert fsm.update(FakeResult(destroyed_track_ids=[1])) is SystemState.DESTROYED
    # Sonraki karede hedef yok; durum yine de kısa süre görünür kalmalı.
    assert fsm.update(FakeResult()) is SystemState.DESTROYED


def test_stop_returns_to_idle():
    fsm = SystemStateMachine()
    fsm.on_start()
    fsm.update(FakeResult(detections=[object()]))
    assert fsm.on_stop() is SystemState.IDLE


# ----------------------------------------------------------------------
# RPi kanalı — gerçek soket üzerinden
# ----------------------------------------------------------------------
class _StubRpi:
    """Tek bağlantı kabul eden, satır tabanlı JSON konuşan sahte RPi."""

    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received: list[dict] = []
        self.conn: socket.socket | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.conn, _ = self.server.accept()
        self._ready.set()
        buffer = b""
        try:
            while True:
                data = self.conn.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self.received.append(json.loads(line))
        except OSError:
            pass

    def wait_connected(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def push(self, payload) -> None:
        raw = payload if isinstance(payload, bytes) else (
            json.dumps(payload).encode() + b"\n")
        self.conn.sendall(raw)

    def close(self):
        # Yalnızca close() çağırmak yetmiyor: başka bir thread aynı sokette
        # recv üzerinde bloklu olduğu için FIN karşı tarafa gitmiyor.
        # shutdown() bağlantıyı gerçekten kapatır.
        if self.conn is not None:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for sock in (self.conn, self.server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


@pytest.fixture
def stub_rpi():
    stub = _StubRpi()
    yield stub
    stub.close()


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_channel_connect_and_send_json(stub_rpi):
    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=stub_rpi.port))
    assert channel.connect()
    assert stub_rpi.wait_connected()

    assert channel.send_mode(True)
    assert channel.send_target(640.0, 360.0, 2, 7, True)
    assert channel.send_engage(7, 2)
    assert _wait_for(lambda: len(stub_rpi.received) >= 3)

    kinds = [m["type"] for m in stub_rpi.received]
    assert kinds == ["mode", "target", "engage"]
    target = stub_rpi.received[1]
    assert target["cx"] == 640.0 and target["class_id"] == 2
    assert target["locked"] is True
    channel.close()


def test_channel_manual_sends_deltas(stub_rpi):
    """`PanTiltController.manual()` artım bekler; mutlak açı gönderilemez."""
    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=stub_rpi.port))
    assert channel.connect()
    assert stub_rpi.wait_connected()
    channel.send_manual(-5.0, 2.5)
    assert _wait_for(lambda: stub_rpi.received)
    assert stub_rpi.received[0] == {"type": "manual", "dx": -5.0, "dy": 2.5}
    channel.close()


def test_channel_parses_split_telemetry_lines(stub_rpi):
    """TCP akış tabanlıdır: bir JSON satırı iki pakete bölünebilir.

    Tamponlama olmasaydı bölünen satır atılır, mesafe göstergesi rastgele
    donardı.
    """
    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=stub_rpi.port))
    assert channel.connect()
    assert stub_rpi.wait_connected()

    payload = json.dumps({"type": "telemetry", "distance_cm": 412.0}).encode()
    stub_rpi.push(payload[:10])
    assert channel.poll(timeout=0.2) == []          # satır henüz tamamlanmadı
    stub_rpi.push(payload[10:] + b"\n")

    messages = []
    _wait_for(lambda: messages.extend(channel.poll(timeout=0.2)) or messages)
    assert messages == [{"type": "telemetry", "distance_cm": 412.0}]
    channel.close()


def test_channel_ignores_malformed_lines(stub_rpi):
    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=stub_rpi.port))
    assert channel.connect()
    assert stub_rpi.wait_connected()
    stub_rpi.push(b"bu json degil\n")
    stub_rpi.push({"type": "telemetry", "distance_cm": 100.0})

    messages = []
    _wait_for(lambda: messages.extend(channel.poll(timeout=0.2)) or messages)
    assert messages == [{"type": "telemetry", "distance_cm": 100.0}]
    channel.close()


def test_channel_detects_disconnect(stub_rpi):
    channel = RpiChannel(RpiSettings(host="127.0.0.1", port=stub_rpi.port))
    assert channel.connect()
    assert stub_rpi.wait_connected()
    stub_rpi.close()
    assert _wait_for(lambda: (channel.poll(timeout=0.1), not channel.connected)[1])


# ----------------------------------------------------------------------
# Ayarlar
# ----------------------------------------------------------------------
def test_settings_replace_placeholder_host(monkeypatch):
    """`pc/config.py`'deki 'xxxx.xxxx.xxxx.xxxx' yer tutucusu bağlanılabilir değil."""
    monkeypatch.delenv("GOKHISAR_RPI_HOST", raising=False)
    settings = RpiSettings.from_env()
    assert settings.host != "xxxx.xxxx.xxxx.xxxx"
    assert settings.port == vision_config.RPI_PORT


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("GOKHISAR_RPI_HOST", "10.1.2.3")
    monkeypatch.setenv("GOKHISAR_RPI_PORT", "6000")
    settings = RpiSettings.from_env()
    assert settings.host == "10.1.2.3"
    assert settings.port == 6000


def test_settings_summary_is_complete():
    lines = Settings.load().summary()
    assert any("RPi" in line for line in lines)
    assert any("Video" in line for line in lines)
