"""Entegrasyon ayarları — köprü katmanının tek ayar noktası.

Görüntü işleme reposunun `pc/config.py` dosyası olduğu gibi bırakılmıştır; içinde
sahada mutlaka ezilmesi gereken iki yer tutucu vardır:

* ``YOLO_MODEL_PATH = "yolo_modeli.pt"`` — depoda böyle bir dosya yok.
* ``RPI_HOST = "xxxx.xxxx.xxxx.xxxx"`` — geçerli bir adres değil.

Bu modül, `pc/config.py`'yi değiştirmeden bu değerleri gerçek değerlerle
değiştirir. Öncelik sırası her ayar için aynıdır:
ortam değişkeni → depo içindeki makul varsayılan → `pc/config.py` değeri.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pc.integration import bootstrap
from shared import protocol

import config as vision_config

# `config.RPI_HOST` içindeki yer tutucu. Gerçek bir adresle karışma ihtimali
# olmadığı için birebir karşılaştırma yeterli.
_HOST_PLACEHOLDER = "xxxx.xxxx.xxxx.xxxx"

# Ağırlık dosyası için arama sırası. İlk bulunan kullanılır.
_WEIGHT_CANDIDATES = (
    "balloon_best_052.pt",
    "best.pt",
    "balon.pt",
    "yolo_modeli.pt",
    "yolov8s.pt",
)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "evet")


def resolve_weights_path() -> Path | None:
    """Kullanılacak YOLO ağırlık dosyasını bul.

    ``GOKHISAR_YOLO_WEIGHTS`` verilmişse doğrudan o kullanılır (dosya yoksa da
    döndürülür ki hata mesajı kullanıcının yazdığı yolu göstersin). Aksi hâlde
    `models/` içinde bilinen adlar, en son da `config.YOLO_MODEL_PATH` denenir.
    Hiçbiri yoksa ``None`` döner ve çağıran taraf kullanıcıya net bir mesaj
    gösterir.
    """
    override = os.environ.get("GOKHISAR_YOLO_WEIGHTS", "").strip()
    if override:
        return Path(override).expanduser()

    for name in _WEIGHT_CANDIDATES:
        candidate = bootstrap.MODELS_DIR / name
        if candidate.is_file():
            return candidate

    configured = Path(vision_config.YOLO_MODEL_PATH)
    if not configured.is_absolute():
        configured = bootstrap.PROJECT_ROOT / configured
    if configured.is_file():
        return configured

    return None


@dataclass(frozen=True)
class RpiSettings:
    """PC → Raspberry Pi 5 bağlantısı (KTR 4.3: TCP/IP + JSON)."""

    host: str
    port: int
    reconnect_period_s: float = 2.0
    # Otonom modda hedef koordinatı gönderim üst sınırı. Boru hattı 30 Hz'e
    # kadar sonuç üretebilir; PID 50 Hz PWM sürdüğü için bundan fazlası
    # RPi tarafında fayda sağlamaz, sadece soket trafiği yaratır.
    max_target_rate_hz: float = 30.0

    @classmethod
    def from_env(cls) -> "RpiSettings":
        configured_host = vision_config.RPI_HOST
        if configured_host == _HOST_PLACEHOLDER:
            configured_host = protocol.DEFAULT_RPI_HOST
        return cls(
            host=_env("GOKHISAR_RPI_HOST", configured_host),
            # Port sözleşmeden okunur; `config.RPI_PORT` ile aynı olduğunu
            # `tests/test_contract.py` doğrular.
            port=_env_int("GOKHISAR_RPI_PORT", protocol.COMMAND_PORT),
            reconnect_period_s=_env_float("GOKHISAR_RPI_RECONNECT_S", 2.0),
            max_target_rate_hz=_env_float("GOKHISAR_TARGET_RATE_HZ", 30.0),
        )


@dataclass(frozen=True)
class VideoSettings:
    """Raspberry Pi → PC video akışı (KTR 4.3: UDP + RTP/MJPEG)."""

    udp_port: int

    @classmethod
    def from_env(cls) -> "VideoSettings":
        return cls(udp_port=_env_int("GOKHISAR_UDP_VIDEO_PORT",
                                     protocol.VIDEO_PORT))


@dataclass(frozen=True)
class PipelineSettings:
    """Görüntü işleme boru hattının çalışma zamanı davranışı."""

    weights_path: Path | None = field(default_factory=resolve_weights_path)

    # HSV yardımcı tespiti ve dinamik ROI iyileştirmesi (KTR 4.2.2.1).
    # Kapatmak boru hattını saf YOLO'ya indirger; zayıf donanımda kare hızını
    # yükseltmek için kullanılabilir.
    hsv_assist: bool = True
    roi_refine: bool = True
    # ROI iyileştirmesi her aday için ayrı bir YOLO çıkarımı demektir. Sınırsız
    # bırakılırsa gürültülü bir karede kare süresi patlar; en büyük N adayla
    # sınırlıyoruz.
    # Kare başına en fazla kaç balon için ikinci (yakınlaştırılmış) YOLO
    # geçişi yapılacağı. Her geçiş tam kare çıkarımı kadar pahalı olduğu için
    # varsayılan düşük tutuldu; GPU'lu bir makinede artırılabilir.
    max_roi_refine: int = 2

    # `TargetLifecycleManager.evaluate_destroyed` ateşlemeden hemen sonra
    # çağrılırsa hedef henüz kaybolmadığı için hep "imha yok" der ve durumu
    # TRACK'e düşürür. KTR 4.2.2.9'daki "belirli bir süre" bu gecikmedir.
    # `pc/config.py` bu sabiti tanımlamadığı için burada tutuluyor.
    destroy_eval_delay_s: float = 2.0

    # Kilit + operatör kilidi açıkken angajman talebinin tekrar gönderilme
    # aralığı. RPi tarafı talebi tüketip sıfırladığı için periyodik tekrar
    # gerekir, ama her karede göndermek gereksiz.
    engage_repeat_s: float = 1.0

    @classmethod
    def from_env(cls) -> "PipelineSettings":
        return cls(
            weights_path=resolve_weights_path(),
            hsv_assist=_env_flag("GOKHISAR_HSV_ASSIST", True),
            roi_refine=_env_flag("GOKHISAR_ROI_REFINE", True),
                    max_roi_refine=_env_int("GOKHISAR_MAX_ROI_REFINE", 2),
            destroy_eval_delay_s=_env_float("GOKHISAR_DESTROY_DELAY_S", 2.0),
            engage_repeat_s=_env_float("GOKHISAR_ENGAGE_REPEAT_S", 1.0),
        )


@dataclass(frozen=True)
class Settings:
    """Uygulamanın tüm entegrasyon ayarları."""

    rpi: RpiSettings
    video: VideoSettings
    pipeline: PipelineSettings

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            rpi=RpiSettings.from_env(),
            video=VideoSettings.from_env(),
            pipeline=PipelineSettings.from_env(),
        )

    def summary(self) -> list[str]:
        """Başlangıçta log paneline basılacak insan okunur özet."""
        weights = self.pipeline.weights_path
        return [
            f"Model: {weights if weights else 'BULUNAMADI'}",
            f"RPi komut kanalı: tcp://{self.rpi.host}:{self.rpi.port} (JSON)",
            f"Video: udp://0.0.0.0:{self.video.udp_port} (RTP/JPEG)",
            f"Kare uzayı: {vision_config.FRAME_WIDTH}x{vision_config.FRAME_HEIGHT}",
        ]
