"""
DetectionWorker — YOLOv8 (Ultralytics) ile arka planda nesne tespiti.

Tasarım hedefleri:
- UI thread'ini bloklamamak (QThread içinde çalışır).
- GStreamer worker'ını yavaşlatmamak (frame'leri "fire-and-forget" alır).
- Düşük gecikme (latency): detector inference'ı yavaş bile olsa, ekrandaki
  bbox'lar her zaman *en son* frame'e yakın olmalı; o yüzden kuyrukta
  yalnızca 1 frame tutarız ve yenisi gelince eskisini ATARIZ.
  Bu yaygın bir "drop-old" / "latest-only" desenidir; kuyruğun şişerek
  detection'ın gerçeklikten 5-10 sn geri kalmasını engeller.

Veri Akışı:
    GStreamerVideoWorker.frame_received(bytes)
        │
        ▼
    DetectionWorker.submit_frame(bytes)   ← thread-safe, hemen döner
        │  (QMutex ile korunur, son frame referansı güncellenir)
        ▼
    DetectionWorker.run() döngüsü
        - WaitCondition'ı bekler
        - en yeni frame'i alır
        - JPEG → np.array (BGR)
        - YOLO inference
        - Detection listesi oluştur
        - detections_ready(list[Detection]) emit eder
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from PySide6.QtCore import Signal, QMutex, QMutexLocker, QWaitCondition

from pc.ui.workers.base_worker import BaseWorker
from pc.ui.utils.config import ModelConfig


# ----------------------------------------------------------------------
# Veri Modeli
# ----------------------------------------------------------------------
@dataclass
class Detection:
    """
    Tek bir nesne tespiti.

    Neden dataclass?
    - Boilerplate (__init__, __repr__, __eq__) otomatik üretilir.
    - Field'lar tip-anotasyonlu, IDE/lint dostu.
    - Tuple yerine kullanırsak çağrı yerinde "[0], [1]" yerine ".bbox_xyxy"
      okuruz (anlam taşır).

    bbox koordinatları:
    - xyxy formatı: (x1, y1, x2, y2). Ultralytics'in standardı.
    - Birim: piksel (orijinal görüntünün — model resize'i geri scale eder).
    """
    bbox_xyxy: tuple[float, float, float, float]
    cls_id: int
    cls_name: str
    confidence: float

    # Aşağıdaki iki alan tam boru hattı (VisionWorker) tarafından doldurulur.
    # Saf YOLO modunda boş kalırlar ve çizim katmanı eski davranışına döner.
    # color : sınıf rengi yerine kullanılacak RGB. IFF sonucu renkle
    #         taşındığı için (DOST yeşil / DÜŞMAN kırmızı) sınıf renginden
    #         daha bilgilendiricidir.
    # track_id : ByteTrack kimliği; etikette "#3" olarak gösterilir, böylece
    #         operatör aynı hedefin kareler boyunca korunduğunu görebilir.
    color: tuple[int, int, int] | None = None
    track_id: int | None = None

    @property
    def width(self) -> float:
        return self.bbox_xyxy[2] - self.bbox_xyxy[0]

    @property
    def height(self) -> float:
        return self.bbox_xyxy[3] - self.bbox_xyxy[1]

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass
class DetectionFrame:
    """
    Bir frame'in inference sonucu — bbox'ların hangi görüntü boyutunda
    çizildiğini bilebilmemiz için frame_size'ı da taşırız.
    """
    detections: List[Detection] = field(default_factory=list)
    frame_width: int = 0
    frame_height: int = 0
    inference_ms: float = 0.0
    # PC tarafı süreler (ms) — kamera/UDP uçtan uca lag değildir
    total_ms: float = 0.0      # decode + YOLO + takip + IFF...
    queue_ms: float = 0.0      # kare geldi → işlenmeye başlandı
    fps: float = 0.0
    latency_summary: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------
class DetectionWorker(BaseWorker):
    """
    YOLOv8 detection worker.

    Sinyaller:
    - detections_ready(DetectionFrame) : Yeni inference sonucu hazır.
    - model_loaded(bool, str)          : Model yüklendi mi? (ok, bilgi mesajı)

    BaseWorker'dan miras:
    - status_changed(str), error_occurred(str)
    """

    detections_ready = Signal(object)   # DetectionFrame
    model_loaded = Signal(bool, str)

    def __init__(
        self,
        weights_path: str | None = None,
        img_size: int | None = None,
        conf: float | None = None,
        iou: float | None = None,
        device: str | None = None,
        parent=None,
    ):
        super().__init__(parent)

        self._weights = weights_path or ModelConfig.WEIGHTS_PATH
        self._img_size = img_size or ModelConfig.IMG_SIZE
        self._conf = conf if conf is not None else ModelConfig.CONF_THRESHOLD
        self._iou = iou if iou is not None else ModelConfig.IOU_THRESHOLD
        self._device = device or ModelConfig.DEVICE

        # Modelin lazy-load edilmesi: pahalı bir import zinciri (torch +
        # ultralytics) UI başlatılırken beklemeyi azaltsın diye run()
        # içinde, yani worker thread'inde yüklenir.
        self._model = None
        self._class_names: dict[int, str] = {}

        # Latest-only kuyruk için senkronizasyon primitifleri
        # ----------------------------------------------------
        # _frame_mutex: _pending_frame değişkenini koruyan mutex
        # _frame_cond : Yeni frame geldiğinde run() döngüsünü uyandıran
        #               condition variable.
        # _pending_frame: Tek slotluk "kuyruk". Bytes (JPEG) veya None.
        self._frame_mutex = QMutex()
        self._frame_cond = QWaitCondition()
        self._pending_frame: Optional[bytes] = None

    # ------------------------------------------------------------------
    # Public API — UI / başka worker'lar tarafından çağrılır
    # ------------------------------------------------------------------
    def submit_frame(self, jpeg_bytes: bytes) -> None:
        """
        Yeni bir frame teslim et. Hemen döner; inference asenkron çalışır.

        Eğer önceki frame henüz işlenmediyse, ESKİSİ atılır ve yenisi
        konur. Bu, tracking'de "stale detection" görmemek için kritiktir.
        """
        with QMutexLocker(self._frame_mutex):
            self._pending_frame = jpeg_bytes
        # Bekleyen run() döngüsünü uyandır
        self._frame_cond.wakeAll()

    # ------------------------------------------------------------------
    # QThread.run override
    # ------------------------------------------------------------------
    def run(self):
        if not self._load_model():
            return

        try:
            while self.is_running:
                frame_bytes = self._pop_latest_frame()
                if frame_bytes is None:
                    # Frame yoksa beklemeye dön; CPU yakmıyoruz
                    continue
                self._process_frame(frame_bytes)
        finally:
            # PyTorch GPU bellek vs. — referansı serbest bırak
            self._model = None
            self.emit_status("Detection worker durdu")

    # ------------------------------------------------------------------
    # Yardımcı: model yükleme
    # ------------------------------------------------------------------
    def _load_model(self) -> bool:
        """
        Ultralytics YOLO modelini yükle. Hatayı UI'a bildir.

        Hata kategorileri (kullanıcıya net mesaj vermek için ayrıştırılır):
        - Dosya yok → açık mesaj
        - ultralytics modülü gerçekten kurulmamış → "pip install" tavsiyesi
        - Diğer her şey (NumPy ABI uyumsuzluğu, bozuk .pt vs.) → exception
          tipi + tam mesajı; yanlış yönlendiren bir tahmin yapmıyoruz.
        """
        if not Path(self._weights).is_file():
            msg = f"Model dosyası bulunamadı: {self._weights}"
            self.emit_error(msg)
            self.model_loaded.emit(False, msg)
            return False

        # 1) Bağımlılık import'u — gerçekten paket eksikliği bu blokta yakalanır.
        try:
            from ultralytics import YOLO  # noqa: WPS433
            import torch                   # noqa: WPS433
        except ModuleNotFoundError as e:
            # Paket adı genelde "No module named 'X'" şeklinde gelir.
            missing = getattr(e, "name", None) or "ultralytics/torch"
            msg = (
                f"Gerekli paket bulunamadı: '{missing}'. "
                "Kurmak için: pip install 'ultralytics>=8.2,<9'"
            )
            self.emit_error(msg)
            self.model_loaded.emit(False, msg)
            return False
        except Exception as e:
            # Import sırasında ImportError dışı bir şey çıkarsa onu da göster.
            msg = f"Bağımlılık import hatası: {type(e).__name__}: {e}"
            self.emit_error(msg)
            self.model_loaded.emit(False, msg)
            return False

        # 2) Model yükleme — burada en sık görülen hata NumPy ABI
        #    uyumsuzluğu ("No module named 'numpy._core'") veya bozuk .pt.
        try:
            device = self._resolve_device(torch)
            self.emit_status(f"Model yükleniyor: {self._weights} (device={device})")

            self._model = YOLO(self._weights)
            try:
                self._model.to(device)
            except Exception:
                # Bazı ultralytics sürümlerinde .to() destek tipine bağlı; sorun değil
                pass
            self._device_resolved = device

            self._class_names = dict(self._model.names)
            self.emit_status(
                f"Model yüklendi. Sınıflar: {list(self._class_names.values())}"
            )
            self.model_loaded.emit(True, f"YOLOv8 hazır ({device})")
            return True

        except ModuleNotFoundError as e:
            # Sıkça görülen sebep: NumPy 2.x ile kaydedilen .pt'yi NumPy 1.x ile
            # açmaya çalışmak ("No module named 'numpy._core'"). Çözümü mesaja
            # gömüyoruz ki kullanıcı doğrudan ne yapacağını görsün.
            text = str(e)
            hint = ""
            if "numpy._core" in text or "numpy>=" in text:
                hint = (
                    "\nÇözüm: NumPy'yi yükseltin → "
                    "pip install -U 'numpy>=1.26.1,<3.0'"
                )
            msg = f"Model yüklenemedi (ModuleNotFoundError): {text}{hint}"
            self.emit_error(msg)
            self.model_loaded.emit(False, msg)
            return False
        except Exception as e:
            msg = f"Model yüklenemedi: {type(e).__name__}: {e}"
            self.emit_error(msg)
            self.model_loaded.emit(False, msg)
            return False

    def _resolve_device(self, torch_module) -> str:
        """'auto' isteğini gerçek device adına çevir."""
        if self._device == "auto":
            return "cuda:0" if torch_module.cuda.is_available() else "cpu"
        if self._device == "cuda" and not torch_module.cuda.is_available():
            raise RuntimeError("CUDA istendi ama bu sistemde mevcut değil.")
        return self._device

    # ------------------------------------------------------------------
    # Yardımcı: kuyruktan en yeni frame'i pop et
    # ------------------------------------------------------------------
    def _pop_latest_frame(self) -> Optional[bytes]:
        """
        Bekleyen frame varsa al ve sıfırla; yoksa CV ile uyu.
        100 ms timeout ile uyanırız ki is_running flag'i kontrol edilebilsin.
        """
        self._frame_mutex.lock()
        try:
            while self._pending_frame is None and self.is_running:
                self._frame_cond.wait(self._frame_mutex, 100)
            frame = self._pending_frame
            self._pending_frame = None
            return frame
        finally:
            self._frame_mutex.unlock()

    # ------------------------------------------------------------------
    # Yardımcı: tek bir frame için inference
    # ------------------------------------------------------------------
    def _process_frame(self, jpeg_bytes: bytes) -> None:
        try:
            import cv2  # lazy import (zaten projede var)

            # JPEG → BGR numpy
            np_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is None:
                return

            h, w = frame.shape[:2]

            import time
            t0 = time.perf_counter()

            # Ultralytics çağrısı:
            # - verbose=False → her frame'de print etmez
            # - imgsz: model giriş boyutu (eğitimle aynı)
            # - conf, iou: filtreleme eşikleri
            results = self._model.predict(
                source=frame,
                imgsz=self._img_size,
                conf=self._conf,
                iou=self._iou,
                verbose=False,
                device=getattr(self, "_device_resolved", None),
            )

            inf_ms = (time.perf_counter() - t0) * 1000.0

            detections = self._extract_detections(results)
            payload = DetectionFrame(
                detections=detections,
                frame_width=w,
                frame_height=h,
                inference_ms=inf_ms,
            )
            self.detections_ready.emit(payload)

        except Exception as e:
            # Inference sırasında oluşan hatalar worker'ı düşürmesin;
            # sadece logla ve bir sonraki frame'e geç.
            self.emit_error(f"Inference hatası: {e}")

    def _extract_detections(self, results) -> List[Detection]:
        """
        Ultralytics result objesini bizim Detection listesine çevir.

        results -> list[Results]; tek görüntü verdiğimiz için results[0]
        kullanılır. results[0].boxes:
            - xyxy: (N, 4) tensor — koordinatlar
            - cls : (N,) tensor — sınıf indeksleri
            - conf: (N,) tensor — confidence skorları
        """
        out: List[Detection] = []
        if not results:
            return out

        r0 = results[0]
        boxes = getattr(r0, "boxes", None)
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return out

        xyxy = boxes.xyxy.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        confs = boxes.conf.cpu().numpy()

        for (x1, y1, x2, y2), cid, cf in zip(xyxy, clss, confs):
            out.append(Detection(
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                cls_id=int(cid),
                cls_name=self._class_names.get(int(cid), str(cid)),
                confidence=float(cf),
            ))
        return out

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------
    def stop_worker(self):
        """
        BaseWorker.stop_worker is_running=False yapar; ama worker condition
        variable'da uyuyor olabilir. Onu da uyandıralım ki döngü çıkışı
        görsün.
        """
        with QMutexLocker(self._frame_mutex):
            self._pending_frame = None
        self._frame_cond.wakeAll()
        super().stop_worker()
