"""Görüntü işleme boru hattı orkestratörü.

Görüntü işleme deposunda tespit, doğrulama, IFF, takip, önceliklendirme ve
yaşam döngüsü modülleri tek tek mevcut; ancak bunları birbirine bağlayan
`pc/vision/main.py` (README'de geçen `Pipeline` sınıfı) depoda **yok**. Bu modül o
eksik halkayı, `pc/` altındaki hiçbir dosyaya dokunmadan tamamlar: ilgili
sınıfları örnekler, KTR 4.2.2'deki sırayla çağırır ve arayüzün tüketebileceği
tek bir sonuç nesnesi üretir.

Kare başına akış (KTR 4.2.2.1 – 4.2.2.10)::

    kare
      ├─ YOLOv8s tam kare tespiti            (4.2.2.1)
      ├─ HSV küçük hedef tespiti             (4.2.2.1)
      ├─ dinamik ROI'de YOLO yeniden çıkarım (4.2.2.1)
      ├─ maket–balon eşleştirme              (4.2.2.2)
      ├─ ByteTrack ile kimlik sürekliliği    (4.2.2.4)
      ├─ HSV + zamansal oylamalı IFF         (4.2.2.3)
      ├─ ağırlıklı öncelik puanı             (4.2.2.5)
      ├─ kilit toleransı denetimi            (4.2.2.6)
      ├─ servo Kalman yumuşatması            (4.2.2.4)
      └─ üç koşullu imha doğrulaması         (4.2.2.9)
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from pc.integration import bootstrap  # noqa: F401  (sys.path kurulumu)
from pc.integration.class_map import ClassMap
from pc.integration.latency_tracker import LatencyTracker
from pc.integration.settings import PipelineSettings
from shared.classes import BALLOON_CLASS_ID, MODEL_CLASS_IDS

import config as vision_config
from detection.hsv_detector import HsvBalloonDetector
from detection.yolo_detector import Detection, YoloDetector
from evaluation.prioritizer import TargetPrioritizer
from iff.friend_foe import FriendFoeClassifier, IFFLabel
from lifecycle.state_machine import TargetLifecycleManager, TargetState
from tracking.tracker import (ServoKalman, TargetTracker, TrackedTarget,
                              detections_same_object)
from validation.matcher import TargetMatcher

# Doğrulanmış maketi takip kaydıyla eşlerken kabul edilen asgari örtüşme.
_VALIDATION_IOU = 0.35
# `TrackedTarget.servo_corrections` listesinin üst sınırı; önceliklendirme
# zaten son 20 örneği kullanıyor.
_SERVO_HISTORY_LEN = 40

IFF_LABEL_TEXT = {
    IFFLabel.FRIEND: "DOST",
    IFFLabel.FOE: "DÜŞMAN",
    IFFLabel.UNKNOWN: "BİLİNMİYOR",
}


@dataclass
class TrackView:
    """Bir takip kaydının arayüze taşınabilir, salt-okunur görünümü.

    Arayüze `TrackedTarget` nesnesini doğrudan vermiyoruz: o nesne boru hattı
    iş parçacığında kare kare değişiyor; UI iş parçacığı onu okurken veri
    yarışı oluşur. Her karede kopyasını çıkarmak bu riski tümden kaldırır.
    """

    track_id: int
    config_class_id: int
    display_name: str
    confidence: float
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    iff: str
    is_friendly: bool | None
    state: str
    priority: float
    validated: bool
    is_candidate: bool
    locked: bool
    misses: int
    predicted: bool = False  # True: bu karede kutu Kalman tahmini


@dataclass
class PipelineResult:
    """Tek bir karenin boru hattı çıktısı."""

    frame_width: int
    frame_height: int
    detections: list[Detection] = field(default_factory=list)
    tracks: list[TrackView] = field(default_factory=list)
    candidate: TrackView | None = None
    locked: bool = False
    servo_target: tuple[float, float] | None = None
    balloon_count: int = 0
    validated_count: int = 0
    inference_ms: float = 0.0
    total_ms: float = 0.0
    stage: int = 3
    backup_mode: bool = False
    new_track_ids: list[int] = field(default_factory=list)
    lost_track_ids: list[int] = field(default_factory=list)
    destroyed_track_ids: list[int] = field(default_factory=list)
    latency_summary: dict[str, float] = field(default_factory=dict)


class _RemappedYolo(YoloDetector):
    """`TargetMatcher`'a verilen, çıktısı config sınıf uzayına çevrilmiş dedektör.

    `TargetMatcher.match()` ikinci doğrulama yönteminde YOLO'yu balonun üst
    ROI'sinde kendisi çalıştırıyor ve sonucu `config.MODEL_CLASS_IDS`
    (= {0,1,2,3}) ile süzüyor. Ama o çağrı ham model kimlikleri döndürür ve
    ham uzayda 4 numara **rocket**'tir — yani füze, "maket değil" sayılıp
    eleniyordu. Sonuç: balonu görülen ama tam karede kaçırılan bir füze hedefi
    hiçbir zaman doğrulanamıyordu; tam da bu yöntemin çözmesi gereken durum.

    `pc/vision/validation/matcher.py` değiştirilemeyeceği için dedektör sarmalanıyor:
    matcher aynı arayüzü görür, ama artık `pc/config.py` uzayında kimlikler alır.
    """

    def __init__(self, yolo: YoloDetector, remap):
        self._yolo = yolo
        self._remap = remap

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return self._remap(self._yolo.detect(frame))

    def detect_in_roi(self, frame: np.ndarray, roi) -> list[Detection]:
        return self._remap(self._yolo.detect_in_roi(frame, roi))

    def __getattr__(self, name):
        return getattr(self._yolo, name)


def _iou(a: Detection, b: Detection) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    union = a.area + b.area - intersection
    return intersection / union if union > 0 else 0.0


class VisionPipeline:
    """Görüntü işleme modüllerini KTR'deki sırayla çalıştıran orkestratör."""

    def __init__(self, settings: PipelineSettings, stage: int = 3):
        self.settings = settings
        self.stage = stage
        self.backup_mode = False

        self.class_map = ClassMap()
        self.yolo: YoloDetector | None = None
        self.hsv = HsvBalloonDetector()
        self.matcher: TargetMatcher | None = None
        self.iff = FriendFoeClassifier(stage=stage)
        self.tracker = TargetTracker()
        self.prioritizer = TargetPrioritizer()
        self.lifecycle = TargetLifecycleManager()
        self.servo_kalman = ServoKalman()
        self.latency = LatencyTracker()

        # Kare boyutu bilerek `shared`den değil `pc/config.py`den okunuyor:
        # normalizasyonun amacı, görüntü işleme modüllerinin *kendi* eşiklerinin
        # varsaydığı uzaya girmek (`FRAME_CENTER`, `SMALL_TARGET_PX_HEIGHT`,
        # öncelik puanındaki kare alanı). İkisi ayrılırsa `tests/test_contract.py`
        # kırılır; ayrıldıkları anda doğru davranış config.py'yi izlemektir.
        self._frame_size = (vision_config.FRAME_WIDTH, vision_config.FRAME_HEIGHT)
        self._known_track_ids: set[int] = set()
        self._candidate_id: int | None = None
        self._last_fps: int = 30

    # ------------------------------------------------------------------
    # Kurulum
    # ------------------------------------------------------------------
    def load(self) -> str:
        """Ağırlıkları yükle ve sınıf eşlemesini kur; özet mesaj döndür.

        Ultralytics çağrısı saniyeler sürebildiği için arayüz iş parçacığında
        değil, çağıran worker'ın kendi iş parçacığında yapılmalıdır.
        """
        weights = self.settings.weights_path
        if weights is None:
            raise FileNotFoundError(
                "YOLO ağırlık dosyası bulunamadı. models/best.pt koyun veya "
                "GOKHISAR_YOLO_WEIGHTS ortam değişkeniyle yol verin."
            )
        if not weights.is_file():
            raise FileNotFoundError(f"YOLO ağırlık dosyası yok: {weights}")

        self.yolo = YoloDetector(model_path=str(weights))

        model_names = getattr(self.yolo.model, "names", None)
        self.class_map = ClassMap(dict(model_names) if model_names else None)

        # Sınıf eşlemesi kurulduktan sonra: matcher, ROI çıkarımını kendisi
        # yaptığı için sarmalanmış dedektörü almalı.
        self.matcher = TargetMatcher(_RemappedYolo(self.yolo, self._remap))
        return self.class_map.describe()

    # ------------------------------------------------------------------
    # Çalışma zamanı denetimleri
    # ------------------------------------------------------------------
    def set_stage(self, stage: int) -> None:
        """Yarışma aşamasını değiştir (2 = balonlu maket düşman, 3 = renkli IFF).

        Sınıflandırıcı yeniden kuruluyor çünkü aşama değişince önceki aşamanın
        oy geçmişi anlamsızdır; taşınırsa yeni aşamada yanlış etiket üretir.
        """
        if stage == self.stage:
            return
        self.stage = stage
        self.iff = FriendFoeClassifier(stage=stage)

    def notify_fired(self, track_id: int) -> None:
        """Ateşleme gerçekleşti; imha değerlendirme sayacını başlat."""
        self.lifecycle.on_fired(track_id)

    def reset(self) -> None:
        """Tüm takip/karar durumunu sıfırla (RESET butonu)."""
        self.tracker = TargetTracker()
        self.iff = FriendFoeClassifier(stage=self.stage)
        self.lifecycle = TargetLifecycleManager()
        self.servo_kalman = ServoKalman()
        self.hsv.reset_condition()
        self.latency.reset()
        self._known_track_ids.clear()
        self._candidate_id = None

    # ------------------------------------------------------------------
    # Kare işleme
    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, t_capture: float = 0.0) -> PipelineResult:
        pipeline_start = time.perf_counter()
        self.latency.record_queue_delay(t_capture)

        frame = self._normalize_frame(frame)
        height, width = frame.shape[:2]

        detections, inference_ms = self._detect(frame)
        models = [d for d in detections
                  if d.class_id in MODEL_CLASS_IDS]
        balloons = [d for d in detections
                    if d.class_id == BALLOON_CLASS_ID]

        validated = []
        if self.matcher is not None:
            with self.latency.measure("matching"):
                validated, _unmatched = self.matcher.match(frame, models, balloons)

        with self.latency.measure("tracking"):
            tracked = self.tracker.update(detections)
            self._accumulate_servo_corrections(tracked)

        validated_ids = self._link_validated_to_tracks(validated, tracked)
        for track_id in validated_ids:
            self.lifecycle.on_validated(track_id)

        with self.latency.measure("iff_classification"):
            self._run_iff(frame, tracked)

        with self.latency.measure("prioritization"):
            candidate = self._select_candidate(tracked)
        locked = self._update_lock(candidate)
        servo_target = self._update_servo_estimate(candidate)

        destroyed = self._evaluate_destruction(tracked)
        new_ids, lost_ids = self._sync_track_bookkeeping(tracked)

        views = self._build_track_views(tracked, candidate, locked)
        candidate_view = next(
            (v for v in views if candidate is not None
             and v.track_id == candidate.track_id), None)

        self.latency.record_end_to_end(t_capture, pipeline_start)
        total_ms = float(self.latency.stage_times.get("total_pipeline", 0.0))
        if total_ms <= 0.0:
            total_ms = (time.perf_counter() - pipeline_start) * 1000.0
        if total_ms > 0:
            self._last_fps = max(1, int(1000.0 / total_ms))
            self.tracker.set_fps(self._last_fps)

        return PipelineResult(
            frame_width=width,
            frame_height=height,
            detections=detections,
            tracks=views,
            candidate=candidate_view,
            locked=locked,
            servo_target=servo_target,
            balloon_count=len(balloons),
            validated_count=len(validated),
            inference_ms=inference_ms,
            total_ms=total_ms,
            stage=self.stage,
            backup_mode=self.backup_mode,
            new_track_ids=new_ids,
            lost_track_ids=lost_ids,
            destroyed_track_ids=destroyed,
            latency_summary=self.latency.get_summary(),
        )

    # ------------------------------------------------------------------
    # Adımlar
    # ------------------------------------------------------------------
    def _normalize_frame(self, frame: np.ndarray) -> np.ndarray:
        """Kareyi `pc/config.py`'nin varsaydığı çözünürlüğe getir.

        Kilit toleransı (`LOCK_TOLERANCE_PX`), küçük hedef eşiği
        (`SMALL_TARGET_PX_HEIGHT`) ve öncelik puanındaki kare alanı hep
        `FRAME_WIDTH x FRAME_HEIGHT` varsayımıyla yazılmıştır; `FRAME_CENTER`
        de oradan türer. Kamera başka bir çözünürlükte yayın yaparsa bu
        eşikler sessizce yanlış ölçeğe kayar ve taret hedefi hiç merkezleyemez.
        Kareyi tek noktada ölçekleyerek tüm boru hattını tasarlandığı koordinat
        uzayında tutuyoruz; sonuçtaki kare boyutu da arayüze bildirildiği için
        çizim katmanı kutuları geri ölçekleyebiliyor.
        """
        target_w, target_h = self._frame_size
        height, width = frame.shape[:2]
        if (width, height) == (target_w, target_h):
            return frame
        interpolation = cv2.INTER_AREA if width > target_w else cv2.INTER_LINEAR
        return cv2.resize(frame, (target_w, target_h), interpolation=interpolation)

    def _detect(self, frame: np.ndarray) -> tuple[list[Detection], float]:
        """Tespit adımı: YOLO tam kare + HSV + dinamik ROI iyileştirmesi."""
        yolo_dets: list[Detection] = []
        with self.latency.measure("yolo_detection"):
            if self.yolo is not None and not self.backup_mode:
                yolo_dets = self._remap(self.yolo.detect(frame))
        inference_ms = float(self.latency.stage_times.get("yolo_detection", 0.0))

        hsv_dets: list[Detection] = []
        roi_dets: list[Detection] = []
        with self.latency.measure("hsv_detection"):
            if self.settings.hsv_assist or self.backup_mode:
                if self.backup_mode:
                    hsv_dets = self.hsv.detect_backup(frame)
                else:
                    models = [d for d in yolo_dets if d.class_id in MODEL_CLASS_IDS]
                    balloons = [d for d in yolo_dets if d.class_id == BALLOON_CLASS_ID]
                    triggered = self.hsv.update_condition(len(models), len(balloons), frame=frame)

                    if triggered:
                        # Nesnesi tespit edilen ama altında balonu bulunamayan hedefleri bul
                        unmatched_models = [
                            m for m in models
                            if not any(_iou(m, b) > 0.1 for b in balloons)
                        ]
                        for model in unmatched_models:
                            # Nesnenin altına önce Model (YOLO), bulamazsa HSV çözümü uygula
                            found = self.hsv.detect_under_object(frame, model, yolo_detector=self.yolo, remap_fn=self._remap)
                            hsv_dets.extend(found)

                        if not hsv_dets:
                            hsv_dets = self.hsv.detect(frame, force=True)

                # Düşük FPS'te ROI refine kareyi daha da yavaşlatır (Sena)
                skip_roi_refine = self._last_fps < 20
                if (self.yolo is not None and self.settings.roi_refine
                        and not self.backup_mode and hsv_dets and not skip_roi_refine):
                    for balloon in self._balloons_needing_refine(hsv_dets, yolo_dets):
                        roi = HsvBalloonDetector.dynamic_roi(balloon, frame.shape)
                        roi_dets.extend(self._remap(self.yolo.detect_in_roi(frame, roi)))

        return self._dedupe(yolo_dets + roi_dets + hsv_dets), inference_ms

    def _balloons_needing_refine(
        self, balloons: list[Detection], models: list[Detection]
    ) -> list[Detection]:
        """Yakınlaştırılmış ikinci geçişe gerçekten ihtiyacı olan balonlar.

        ROI yenilemesi pahalı: kırpılan bölge de aynı 640 piksele ölçeklendiği
        için her çağrı neredeyse tam bir kare çıkarımı kadar sürüyor. Ölçüm:
        CPU'da tam kare ~290 ms; sınırsız yenilemede kare süresi 1 saniyeyi
        aşıyordu.

        Üzerinde zaten maket tespit edilmiş balon için bu masrafı ödemek
        anlamsız; KTR'deki amaç uzaktaki küçük hedefi yakalamaktı. Kalanların
        en büyükleri seçilir, çünkü küçük lekeler genelde gürültüdür ve
        yakınlaştırılınca da bir şey çıkmaz.
        """
        needing = [
            balloon for balloon in balloons
            if not any(_iou(balloon, model) > 0.1 for model in models)
        ]
        needing.sort(key=lambda d: d.area, reverse=True)
        return needing[:self.settings.max_roi_refine]

    def _remap(self, detections: list[Detection]) -> list[Detection]:
        """Model sınıf kimliklerini `pc/config.py` uzayına çevir.

        Eşlenemeyen sınıflar listeden düşer; rastgele bir kimliğe zorlamak
        tanınmayan bir nesnenin geçerli bir hedef sanılmasına yol açardı.
        """
        remapped: list[Detection] = []
        for det in detections:
            config_id = self.class_map.to_config_id(det.class_id)
            if config_id is None:
                continue
            remapped.append(dataclasses.replace(det, class_id=config_id))
        return remapped

    @staticmethod
    def _dedupe(detections: list[Detection]) -> list[Detection]:
        """Aynı nesnenin farklı yollardan gelen kopyalarını tekilleştir.

        Tam kare YOLO ile ROI YOLO çoğu zaman aynı maketi iki kez üretir; ikisi
        de takibe girerse ByteTrack aynı nesneye iki kimlik verir ve öncelik
        sıralaması bozulur. Güveni yüksek olan tutulur.
        """
        kept: list[Detection] = []
        for det in sorted(detections, key=lambda d: d.conf, reverse=True):
            if any(detections_same_object(other, det) for other in kept):
                continue
            kept.append(det)
        return kept

    @staticmethod
    def _accumulate_servo_corrections(tracked: dict[int, TrackedTarget]) -> None:
        """Önceliklendirmenin kullandığı servo düzeltme metriğini besle.

        `TargetPrioritizer` her hedef için `servo_corrections` listesini okur
        ama listeyi kimse doldurmuyordu; boş kalırsa tüm hedefler aynı sabit
        0.5 puanı alır ve puanın servo bileşeni etkisizleşir. Merkez
        koordinatının kareler arası yer değiştirmesi, taretin o hedefi merkezde
        tutmak için yapması gereken düzeltmenin doğrudan ölçüsüdür.
        """
        for target in tracked.values():
            if len(target.center_history) < 2:
                continue
            (px, py), (cx, cy) = target.center_history[-2], target.center_history[-1]
            target.servo_corrections.append(math.hypot(cx - px, cy - py))
            if len(target.servo_corrections) > _SERVO_HISTORY_LEN:
                del target.servo_corrections[:-_SERVO_HISTORY_LEN]

    @staticmethod
    def _link_validated_to_tracks(validated, tracked: dict[int, TrackedTarget]) -> set[int]:
        """Doğrulanmış maketleri takip kimlikleriyle ilişkilendir.

        `TargetMatcher` takipten önceki ham tespitlerle çalışır ve takip kimliği
        bilmez; ByteTrack ise kutuyu Kalman tahminiyle bir miktar oynatır. İki
        taraf en yüksek örtüşmeye göre eşleştirilir.
        """
        matched: set[int] = set()
        for operational in validated:
            best_id, best_iou = None, _VALIDATION_IOU
            for track_id, target in tracked.items():
                if target.det.class_id not in MODEL_CLASS_IDS:
                    continue
                score = _iou(target.det, operational.model_det)
                if score > best_iou:
                    best_id, best_iou = track_id, score
            if best_id is not None:
                matched.add(best_id)
        return matched

    def _run_iff(self, frame: np.ndarray, tracked: dict[int, TrackedTarget]) -> None:
        """Dost-düşman sınıflandırmasını çalıştır (balonlar hedef değildir)."""
        for track_id, target in tracked.items():
            if target.det.class_id == BALLOON_CLASS_ID:
                continue
            label = self.iff.classify(frame, target.det, track_id)
            self.lifecycle.on_iff(track_id, label)

    def _select_candidate(self, tracked: dict[int, TrackedTarget]) -> TrackedTarget | None:
        """Angajman / takip adayını seç.

        Balon-only modelde maket+IFF yok; balonlar doğrudan takip adayı olur
        (servo merkeze götürsün). Ateş kararı ayrı kapılarda kalır.
        """
        # Test/KTR: kısa miss'te Kalman tahmini varken adayı hemen bırakma
        max_miss = int(getattr(vision_config, "TRACK_CANDIDATE_MAX_MISSES", 5))
        if getattr(vision_config, "TRACKING_TEST_MODE", False):
            max_miss = max(max_miss, 8)
        candidates_input = []
        for track_id, target in tracked.items():
            if target.det.class_id == BALLOON_CLASS_ID:
                if target.misses > max_miss:
                    continue
                rec = self.lifecycle.get(track_id)
                # Matcher/IFF balonu atladığı için yaşam döngüsünü burada ilerlet.
                if rec.state is TargetState.DETECT:
                    rec.state = TargetState.TRACK
                if rec.state is TargetState.TRACK:
                    rec.iff = IFFLabel.FOE
                    rec.state = TargetState.EVALUATE
                if rec.state in (TargetState.EVALUATE, TargetState.TARGET_LOCK):
                    candidates_input.append((target, IFFLabel.FOE))
                continue

            # Kayıp maket izini kilit adayı yapma (Sena)
            if target.misses > 0:
                continue
            record = self.lifecycle.records.get(track_id)
            if record is None or record.iff is not IFFLabel.FOE:
                continue
            if record.state in (TargetState.EVALUATE, TargetState.TARGET_LOCK):
                candidates_input.append((target, record.iff))
        candidate = self.prioritizer.select(candidates_input)
        if candidate is not None:
            self.lifecycle.on_selected_for_lock(candidate.track_id)
        return candidate

    def _update_lock(self, candidate: TrackedTarget | None) -> bool:
        if candidate is None:
            return False
        record = self.lifecycle.get(candidate.track_id)
        if record.state is not TargetState.TARGET_LOCK:
            return False
        return self.lifecycle.update_lock(record, candidate)

    def _update_servo_estimate(
        self, candidate: TrackedTarget | None
    ) -> tuple[float, float] | None:
        """Servoya gidecek merkez — dt'li Kalman + lead (Sena).

        TRACKING_TEST_MODE servo *gönderimini* UI'da keser; burada tahmin
        üretilmeye devam eder (görsel doğrulama). Aday değişince reset.
        """
        dt = 1.0 / max(1, self._last_fps)
        if candidate is None:
            self._candidate_id = None
            if self.servo_kalman.initialized:
                return self.servo_kalman.predict_only(dt=dt)
            return None

        if candidate.track_id != self._candidate_id:
            self.servo_kalman.reset()
            self._candidate_id = candidate.track_id
        return self.servo_kalman.update(candidate.det.cx, candidate.det.cy, dt=dt)

    def _evaluate_destruction(self, tracked: dict[int, TrackedTarget]) -> list[int]:
        """Ateşlenmiş hedefler için üç koşullu imha doğrulaması (KTR 4.2.2.9).

        Değerlendirme ateşlemeden hemen sonra yapılamaz: hedef daha kadrajdadır,
        üç koşul sağlanmaz ve `evaluate_destroyed` hedefi TRACK'e geri düşürüp
        `fired` bayrağını temizler — yani imha hiç doğrulanamaz. Bu yüzden
        `destroy_eval_delay_s` kadar beklenir.
        """
        destroyed: list[int] = []
        now = time.time()
        for track_id, record in list(self.lifecycle.records.items()):
            if not record.fired:
                continue
            if now - record.fire_time < self.settings.destroy_eval_delay_s:
                continue
            if self.lifecycle.evaluate_destroyed(record, tracked.get(track_id)):
                destroyed.append(track_id)
        return destroyed

    def _sync_track_bookkeeping(
        self, tracked: dict[int, TrackedTarget]
    ) -> tuple[list[int], list[int]]:
        """Yeni/kaybolan kimlikleri raporla ve düşen kayıtları temizle."""
        current = set(tracked)
        new_ids = sorted(current - self._known_track_ids)
        lost_ids = sorted(self._known_track_ids - current)
        self._known_track_ids = current

        for track_id in lost_ids:
            record = self.lifecycle.records.get(track_id)
            # Ateşlenmiş ama henüz imhası doğrulanmamış hedefin kaydı
            # silinemez; silinirse `fired` bayrağı kaybolur ve imha
            # değerlendirmesi hiç çalışmaz.
            if record is not None and record.fired:
                continue
            self.iff.drop(track_id)
            self.lifecycle.drop(track_id)
        return new_ids, lost_ids

    def _build_track_views(
        self,
        tracked: dict[int, TrackedTarget],
        candidate: TrackedTarget | None,
        locked: bool,
    ) -> list[TrackView]:
        candidate_id = candidate.track_id if candidate else None
        views: list[TrackView] = []
        max_misses = int(getattr(vision_config, "TRACK_MAX_DRAW_MISSES", 2))
        for track_id, target in tracked.items():
            # Uzun miss hayaletlerini UI'dan çıkar (Sena: TRACK_MAX_DRAW_MISSES)
            if target.misses > max_misses:
                continue
            record = self.lifecycle.records.get(track_id)
            iff_label = record.iff if record else IFFLabel.UNKNOWN
            state = record.state.name if record else TargetState.DETECT.name
            is_balloon = target.det.class_id == BALLOON_CLASS_ID
            views.append(TrackView(
                track_id=track_id,
                config_class_id=target.det.class_id,
                display_name=self.class_map.display_name_for_config_id(
                    target.det.class_id),
                confidence=target.det.conf,
                bbox=(target.det.x1, target.det.y1, target.det.x2, target.det.y2),
                center=(target.det.cx, target.det.cy),
                iff=IFF_LABEL_TEXT[iff_label],
                is_friendly=(None if iff_label is IFFLabel.UNKNOWN
                             else iff_label is IFFLabel.FRIEND),
                state=state,
                priority=0.0 if is_balloon else self.prioritizer.score(target),
                validated=bool(record and record.state not in (
                    TargetState.DETECT, TargetState.VALIDATE)),
                is_candidate=(track_id == candidate_id),
                locked=(track_id == candidate_id and locked),
                misses=target.misses,
                predicted=bool(getattr(target, "predicted", False)),
            ))
        views.sort(key=lambda v: v.priority, reverse=True)
        return views
