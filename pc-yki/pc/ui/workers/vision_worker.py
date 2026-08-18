"""VisionWorker — tam görüntü işleme boru hattını arka planda çalıştırır.

`DetectionWorker`'ın yaptığı iş yalnızca YOLO çıkarımıydı: kutu üret, ekrana
çiz. KTR 4.4'teki zincir bunun çok ötesinde — doğrulama, takip, IFF,
önceliklendirme, hedef kilidi ve imha değerlendirmesi. Bu worker,
`pc.integration.vision_pipeline.VisionPipeline`'ı sarmalayarak o zinciri
UI'ı bloklamadan çalıştırır.

`DetectionWorker` silinmedi: model dosyası dışında hiçbir şeye ihtiyaç
duymadığı için hızlı bir "sadece tespit" doğrulaması yapmak isteyene duruyor.
Uygulamanın varsayılan yolu artık VisionWorker.

Tasarım, `DetectionWorker`'daki iki kararı bilerek tekrarlıyor:

* **Latest-only kuyruk.** Boru hattı 30 FPS'in altına düşerse kuyruk şişip
  ekrandaki kutular saniyelerce geriden gelir. Tek slotluk kuyrukta eski kare
  atılır; gösterilen sonuç her zaman *en yeni* kareye aittir. Nişan alan bir
  sistemde bayat kutu, yavaş kutudan çok daha tehlikelidir.
* **Modelin worker thread'inde yüklenmesi.** `torch` + `ultralytics` import
  zinciri saniyeler sürer; ana thread'de yapılırsa pencere donuk açılır.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QMutex, QMutexLocker, QWaitCondition, Signal

from pc.integration import bootstrap  # noqa: F401  (sys.path kurulumu)
from pc.integration.settings import PipelineSettings
from pc.ui.workers.base_worker import BaseWorker
from pc.ui.workers.detection_worker import Detection, DetectionFrame
from shared.classes import BALLOON_CLASS_ID

import config

# Kutu renkleri (RGB). IFF sonucu renkle taşınıyor: operatör etiketi okumadan,
# çevresel görüşle bile dost/düşman ayrımını yapabilmeli.
COLOR_FRIEND = (60, 220, 90)      # DOST — yeşil
COLOR_FOE = (235, 60, 60)         # DÜŞMAN — kırmızı
COLOR_UNKNOWN = (240, 200, 60)    # BİLİNMİYOR — sarı
COLOR_BALLOON = (235, 120, 200)   # Balon (angajman hedefi değil) — pembe
COLOR_LOCKED = (255, 255, 255)    # Kilitli hedef — beyaz, dikkat çekici


class VisionWorker(BaseWorker):
    """Tam boru hattını çalıştıran worker.

    Sinyaller:
        result_ready(object)     : `PipelineResult` — mantık katmanı için.
        detections_ready(object) : `DetectionFrame` — çizim katmanı için.
        pipeline_loaded(bool,str): Model yüklendi mi, açıklama.
    """

    result_ready = Signal(object)
    detections_ready = Signal(object)
    pipeline_loaded = Signal(bool, str)

    def __init__(self, settings: PipelineSettings, stage: int = 3, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._initial_stage = stage
        self._pipeline = None

        self._frame_mutex = QMutex()
        self._frame_cond = QWaitCondition()
        self._pending_frame: Optional[bytes] = None
        self._pending_t: float = 0.0

        # Aşağıdaki istekler UI thread'inden gelir ama boru hattına yalnızca
        # worker thread'i dokunmalı. Bayrak olarak biriktirilip döngünün
        # başında uygulanırlar; böylece Kalman/FSM durumuna eşzamanlı erişim
        # olmaz.
        self._cmd_mutex = QMutex()
        self._requested_stage: Optional[int] = None
        self._requested_backup: Optional[bool] = None
        self._reset_requested = False
        self._fired_track_ids: list[int] = []

    # ------------------------------------------------------------------
    # UI tarafından çağrılan API (hepsi thread-safe, hemen döner)
    # ------------------------------------------------------------------
    def submit_frame(self, jpeg_bytes: bytes) -> None:
        with QMutexLocker(self._frame_mutex):
            self._pending_frame = jpeg_bytes
            self._pending_t = time.perf_counter()
        self._frame_cond.wakeAll()

    def set_stage(self, stage: int) -> None:
        """2. Aşama (tümü düşman) / 3. Aşama (renk tabanlı IFF) geçişi."""
        with QMutexLocker(self._cmd_mutex):
            self._requested_stage = int(stage)

    def set_backup_mode(self, enabled: bool) -> None:
        """YOLO devre dışı, yalnızca HSV: model çökerse görev sürsün."""
        with QMutexLocker(self._cmd_mutex):
            self._requested_backup = bool(enabled)

    def notify_fired(self, track_id: int) -> None:
        """Ateşleme bildirimi; imha değerlendirmesini başlatır."""
        with QMutexLocker(self._cmd_mutex):
            self._fired_track_ids.append(int(track_id))

    def reset_pipeline(self) -> None:
        """Takip/IFF/yaşam döngüsü durumunu temizle (RESET düğmesi)."""
        with QMutexLocker(self._cmd_mutex):
            self._reset_requested = True

    # ------------------------------------------------------------------
    # QThread gövdesi
    # ------------------------------------------------------------------
    def run(self):
        if not self._load_pipeline():
            return
        try:
            while self.is_running:
                self._apply_pending_commands()
                frame_bytes = self._pop_latest_frame()
                if frame_bytes is None:
                    continue
                jpeg_bytes, queued_at = frame_bytes
                self._process(jpeg_bytes, queued_at)
        finally:
            self._pipeline = None
            self.emit_status("Görüntü işleme boru hattı durdu")

    def _load_pipeline(self) -> bool:
        try:
            from pc.integration.vision_pipeline import VisionPipeline
        except Exception as exc:
            msg = self._dependency_hint(exc)
            self.emit_error(msg)
            self.pipeline_loaded.emit(False, msg)
            return False

        try:
            pipeline = VisionPipeline(self._settings, stage=self._initial_stage)
            self.emit_status(f"Model yükleniyor: {self._settings.weights_path}")
            description = pipeline.load()
        except FileNotFoundError as exc:
            self.emit_error(str(exc))
            self.pipeline_loaded.emit(False, str(exc))
            return False
        except Exception as exc:
            msg = self._dependency_hint(exc)
            self.emit_error(msg)
            self.pipeline_loaded.emit(False, msg)
            return False

        self._pipeline = pipeline
        self.emit_status(description)
        self.pipeline_loaded.emit(True, description)
        return True

    @staticmethod
    def _dependency_hint(exc: Exception) -> str:
        """Eksik paket hatalarını kurulum komutuna çevir.

        Sahada en sık karşılaşılan başlangıç hatası eksik bağımlılık oluyor;
        çıplak bir ImportError yerine ne yazılacağını söylemek arıza süresini
        kısaltıyor.
        """
        name = getattr(exc, "name", None)
        hints = {
            "ultralytics": "pip install 'ultralytics>=8.2,<9'",
            "torch": "pip install torch",
            "supervision": "pip install 'supervision>=0.20'",
            "cv2": "pip install opencv-python",
            "filterpy": "pip install filterpy",
        }
        if name in hints:
            return f"Gerekli paket bulunamadı: '{name}'. Kurmak için: {hints[name]}"
        if isinstance(exc, ModuleNotFoundError) and "numpy._core" in str(exc):
            return ("Model NumPy 2.x ile kaydedilmiş, ortamda NumPy 1.x var. "
                    "Çözüm: pip install -U 'numpy>=1.26.1,<3.0'")
        return f"Boru hattı başlatılamadı: {type(exc).__name__}: {exc}"

    def _apply_pending_commands(self) -> None:
        with QMutexLocker(self._cmd_mutex):
            stage = self._requested_stage
            backup = self._requested_backup
            reset = self._reset_requested
            fired = self._fired_track_ids
            self._requested_stage = None
            self._requested_backup = None
            self._reset_requested = False
            self._fired_track_ids = []

        if self._pipeline is None:
            return
        if reset:
            self._pipeline.reset()
            self.emit_status("Takip/IFF durumu sıfırlandı")
        if stage is not None:
            self._pipeline.set_stage(stage)
            self.emit_status(f"{stage}. Aşama IFF kuralları etkin")
        if backup is not None and backup != self._pipeline.backup_mode:
            self._pipeline.backup_mode = backup
            self.emit_status(
                "Yedek mod: yalnızca HSV tespiti" if backup else "YOLO tespiti etkin"
            )
        for track_id in fired:
            self._pipeline.notify_fired(track_id)

    def _pop_latest_frame(self) -> Optional[tuple[bytes, float]]:
        self._frame_mutex.lock()
        try:
            while self._pending_frame is None and self.is_running:
                # 100 ms timeout: kare gelmese bile is_running ve bekleyen
                # komutlar düzenli olarak kontrol edilebilsin.
                self._frame_cond.wait(self._frame_mutex, 100)
            frame = self._pending_frame
            queued_at = self._pending_t
            self._pending_frame = None
            self._pending_t = 0.0
            if frame is None:
                return None
            return frame, queued_at
        finally:
            self._frame_mutex.unlock()

    # ------------------------------------------------------------------
    # Kare işleme
    # ------------------------------------------------------------------
    def _process(self, jpeg_bytes: bytes, queued_at: float = 0.0) -> None:
        try:
            import cv2

            t0 = time.perf_counter()
            queue_ms = max(0.0, (t0 - queued_at) * 1000.0) if queued_at > 0.0 else 0.0

            buffer = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            if frame is None:
                return

            result = self._pipeline.process(frame, t_capture=queued_at)
            self.detections_ready.emit(
                self._to_detection_frame(result, queue_ms=queue_ms)
            )
            self.result_ready.emit(result)
        except Exception as exc:
            # Tek bir bozuk kare görevi düşürmemeli; hatayı bildir, devam et.
            self.emit_error(f"Boru hattı hatası: {type(exc).__name__}: {exc}")
            time.sleep(0.05)

    def _to_detection_frame(self, result, *, queue_ms: float = 0.0) -> DetectionFrame:
        """`PipelineResult`'ı mevcut çizim katmanının anladığı biçime çevir.

        Çizilenler: takip edilen her hedef (IFF rengiyle) ve takip altına
        girmemiş balonlar. Balonlar ByteTrack'in aktivasyon eşiğinin altında
        kaldığı için hiçbir zaman iz üretmez; ama doğrulamanın neye baktığını
        operatörün görmesi gerekir.
        """
        drawables: list[Detection] = []

        for view in result.tracks:
            is_balloon = view.config_class_id == BALLOON_CLASS_ID
            if view.locked:
                color = COLOR_LOCKED
            elif getattr(view, "predicted", False):
                # Kalman tahmini — ölçüm yok, kutu ilerliyor
                color = (180, 180, 220)
            elif is_balloon:
                color = COLOR_BALLOON
            elif view.is_friendly is True:
                color = COLOR_FRIEND
            elif view.is_friendly is False:
                color = COLOR_FOE
            else:
                color = COLOR_UNKNOWN

            label = view.display_name
            if view.locked:
                label = f"{label} [KİLİT]"
            elif view.is_candidate:
                label = f"{label} [ADAY]"
            if getattr(view, "predicted", False):
                label = f"{label} [TAHMİN]"

            drawables.append(Detection(
                bbox_xyxy=view.bbox,
                cls_id=view.config_class_id,
                cls_name=label,
                confidence=view.confidence,
                color=color,
                track_id=view.track_id,
            ))

        tracked_boxes = [view.bbox for view in result.tracks]
        for det in result.detections:
            if det.class_id != BALLOON_CLASS_ID:
                continue
            if any(_overlaps(det, box) for box in tracked_boxes):
                continue
            drawables.append(Detection(
                bbox_xyxy=(det.x1, det.y1, det.x2, det.y2),
                cls_id=det.class_id,
                cls_name="Balon",
                confidence=det.conf,
                color=COLOR_BALLOON,
            ))

        total_ms = float(getattr(result, "total_ms", 0.0) or 0.0)
        fps = (1000.0 / total_ms) if total_ms > 1.0 else 0.0
        summary = dict(getattr(result, "latency_summary", {}) or {})
        if queue_ms > 0 and "queue_delay" not in summary:
            summary["queue_delay"] = float(queue_ms)
        return DetectionFrame(
            detections=drawables,
            frame_width=result.frame_width,
            frame_height=result.frame_height,
            inference_ms=result.inference_ms,
            total_ms=total_ms,
            queue_ms=float(queue_ms),
            fps=fps,
            latency_summary=summary,
        )

    # ------------------------------------------------------------------
    def stop_worker(self):
        with QMutexLocker(self._frame_mutex):
            self._pending_frame = None
        self._frame_cond.wakeAll()
        super().stop_worker()


def _overlaps(det, box: tuple[float, float, float, float], threshold: float = 0.5) -> bool:
    """Tespit, verilen kutuyla büyük ölçüde çakışıyor mu (IoU)."""
    bx1, by1, bx2, by2 = box
    ix1, iy1 = max(det.x1, bx1), max(det.y1, by1)
    ix2, iy2 = min(det.x2, bx2), min(det.y2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return False
    union = det.area + (bx2 - bx1) * (by2 - by1) - intersection
    return union > 0 and intersection / union > threshold
