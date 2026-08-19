"""Hedef Takip Modülü — ByteTrack + Sena bytetrackupdate2 ID map.

KTR 4.2.2.4:
  • ByteTrack kimlik
  • ID map / Re-ID — ByteTrack ID değişse bile kanonik ID kalır
  • Ölçüm yokken coast (TRACK_BUFFER)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
# pyrefly: ignore [missing-import]
import supervision as sv

import config
from detection.yolo_detector import Detection


def _as_float(x) -> float:
    """NumPy 2.x: float(array([v])) TypeError verir — tek skaler çıkar."""
    if isinstance(x, np.ndarray):
        return float(x.reshape(-1)[0])
    return float(x)


def _as_int(x) -> int:
    if isinstance(x, np.ndarray):
        return int(x.reshape(-1)[0])
    return int(x)


def _box_iou(a: np.ndarray, b: np.ndarray) -> float:
    """İki xyxy kutusu arasındaki IoU."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return float(intersection / union) if union > 0 else 0.0


def _box_center_dist(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    acx, acy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    return math.hypot(acx - bcx, acy - bcy)


def boxes_same_object(a: np.ndarray, b: np.ndarray,
                      iou_threshold: float | None = None,
                      center_ratio: float | None = None) -> bool:
    """IoU veya merkez yakınlığına göre iki kutunun aynı nesneye ait olup olmadığı."""
    iou_threshold = config.DEDUPE_IOU if iou_threshold is None else iou_threshold
    center_ratio = config.DEDUPE_CENTER_RATIO if center_ratio is None else center_ratio
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if _box_iou(a, b) > iou_threshold:
        return True
    max_side = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return _box_center_dist(a, b) < max_side * center_ratio


def detections_same_object(a: Detection, b: Detection,
                           iou_threshold: float | None = None,
                           center_ratio: float | None = None) -> bool:
    """Detection çifti için aynı-nesne testi."""
    if a.class_id != b.class_id:
        return False
    return boxes_same_object(a.as_xyxy(), b.as_xyxy(), iou_threshold, center_ratio)


def _detection_at_center(
    det: Detection, cx: float, cy: float, *, conf: float | None = None
) -> Detection:
    """Aynı boyutta, yeni merkezli kutu (Kalman tahmini için)."""
    w = max(1.0, det.x2 - det.x1)
    h = max(1.0, det.y2 - det.y1)
    return Detection(
        cx - w / 2.0,
        cy - h / 2.0,
        cx + w / 2.0,
        cy + h / 2.0,
        conf=det.conf if conf is None else conf,
        class_id=det.class_id,
        source=det.source,
    )


class TrackKalman:
    """Sabit-hız Kalman — merkez (cx, cy) + hız (vx, vy).

    KTR: ölçüm varken doğru/yumuşat; ölçüm yokken gelecek tahmini.
    Hızlı balon için süreç gürültüsü yüksek, ölçüme güven yüksek tutulur
    (kutu geride kalmasın).
    """

    def __init__(self) -> None:
        q = float(getattr(config, "TRACK_KALMAN_PROCESS", 5e-2))
        r = float(getattr(config, "TRACK_KALMAN_MEASURE", 1e-2))
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]],
            np.float32,
        )
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], np.float32
        )
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * q
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * r
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        meas = np.array([[np.float32(cx)], [np.float32(cy)]])
        if not self.initialized:
            self.kf.statePost = np.array(
                [[np.float32(cx)], [np.float32(cy)], [0], [0]], np.float32
            )
            self.initialized = True
        self.kf.predict()
        est = self.kf.correct(meas)
        return _as_float(est[0, 0]), _as_float(est[1, 0])

    def predict_only(self) -> tuple[float, float]:
        """Ölçüm yok — KTR 'yalnız tahmin adımı'."""
        if not self.initialized:
            return 0.0, 0.0
        pred = self.kf.predict()
        return _as_float(pred[0, 0]), _as_float(pred[1, 0])

    @property
    def velocity(self) -> tuple[float, float]:
        if not self.initialized:
            return 0.0, 0.0
        s = self.kf.statePost
        return _as_float(s[2, 0]), _as_float(s[3, 0])


class LightweightHistogramReID:
    """Sıfır gecikmeli HSV Renk + Histogram Re-ID Parmak İzi."""

    @staticmethod
    def extract_feature(
        frame: np.ndarray | None, bbox: tuple[float, float, float, float] | np.ndarray
    ) -> np.ndarray | None:
        if frame is None or frame.size == 0:
            return None
        h, w = frame.shape[:2]
        b = np.asarray(bbox, dtype=np.float32).reshape(-1)
        x1, y1, x2, y2 = (
            int(max(0, b[0])),
            int(max(0, b[1])),
            int(min(w, b[2])),
            int(min(h, b[3])),
        )
        if x2 - x1 < 6 or y2 - y1 < 6:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4], [0, 180, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        return hist.flatten()

    @staticmethod
    def similarity(feat1: np.ndarray | None, feat2: np.ndarray | None) -> float:
        if feat1 is None or feat2 is None:
            return 0.0
        dot = float(np.dot(feat1, feat2))
        norm = float(np.linalg.norm(feat1) * np.linalg.norm(feat2)) + 1e-6
        return float(dot / norm)


class GlobalMotionCompensator:
    """Arka plan hareketini (kamera pan/tilt sarsıntısı) hesaplayıp öteleyen sınıf."""

    def __init__(self) -> None:
        self.prev_gray: np.ndarray | None = None

    def estimate_motion(
        self, frame: np.ndarray | None, target_boxes: list[np.ndarray] | None = None
    ) -> tuple[float, float]:
        """Kareler arası (dx, dy) kamera kayma miktarını döndürür.
        
        Hareketli nesneler (target_boxes) maskelenerek optik akışın yalnızca
        statik arka plandan kilit noktaları izlemesi garanti edilir.
        """
        if frame is None or frame.size == 0:
            return 0.0, 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        if self.prev_gray is None:
            self.prev_gray = gray
            return 0.0, 0.0

        dx, dy = 0.0, 0.0
        try:
            mask = None
            if target_boxes:
                h, w = gray.shape[:2]
                mask = np.full((h, w), 255, dtype=np.uint8)
                for box in target_boxes:
                    b = np.asarray(box, dtype=np.float32).reshape(-1)
                    x1, y1, x2, y2 = int(max(0, b[0])), int(max(0, b[1])), int(min(w, b[2])), int(min(h, b[3]))
                    mx1, my1 = max(0, x1 - 10), max(0, y1 - 10)
                    mx2, my2 = min(w, x2 + 10), min(h, y2 + 10)
                    mask[my1:my2, mx1:mx2] = 0

            prev_pts = cv2.goodFeaturesToTrack(
                self.prev_gray, maxCorners=120, qualityLevel=0.01, minDistance=25, mask=mask
            )
            if prev_pts is not None and len(prev_pts) >= 6:
                curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                    self.prev_gray, gray, prev_pts, None
                )
                good_prev = prev_pts[status == 1]
                good_curr = curr_pts[status == 1]
                if len(good_prev) >= 6:
                    M, _ = cv2.estimateAffinePartial2D(good_prev, good_curr)
                    if M is not None:
                        dx = float(M[0, 2])
                        dy = float(M[1, 2])
        except Exception:
            pass

        self.prev_gray = gray
        return dx, dy


@dataclass
class TrackedTarget:
    track_id: int
    det: Detection
    age: int = 0                 # kaç karedir takipte
    misses: int = 0              # ardışık kayıp kare sayısı
    center_history: list = field(default_factory=list)
    servo_corrections: list = field(default_factory=list)
    predicted: bool = False      # bu karede kutu Kalman tahmini mi?
    feature: np.ndarray | None = None  # Re-ID HSV özellik vektörü


class TargetTracker:
    """ByteTrack + sticky kanonik ID (raw ID değişse #N sabit)."""

    def __init__(self, fps: int = 30):
        self.tracker = sv.ByteTrack(
            track_activation_threshold=config.TRACK_HIGH_CONF,
            minimum_matching_threshold=config.TRACK_MATCH_IOU,
            lost_track_buffer=config.TRACK_BUFFER,
            frame_rate=fps,
            minimum_consecutive_frames=1,
        )
        self.targets: dict[int, TrackedTarget] = {}
        self._kalmans: dict[int, TrackKalman] = {}
        self.gmc = GlobalMotionCompensator()
        self.reid = LightweightHistogramReID()
        self.lost_pool: dict[int, TrackedTarget] = {}
        self._id_map: dict[int, int] = {}

    def set_fps(self, fps: int) -> None:
        if hasattr(self.tracker, "frame_rate"):
            self.tracker.frame_rate = max(1, int(fps))

    def _kf(self, track_id: int) -> TrackKalman:
        if track_id not in self._kalmans:
            self._kalmans[track_id] = TrackKalman()
        return self._kalmans[track_id]

    def _drop(self, track_id: int) -> None:
        t = self.targets.pop(track_id, None)
        if t is not None and getattr(config, "ENABLE_REID", True) and t.feature is not None:
            self.lost_pool[track_id] = t
            if len(self.lost_pool) > 30:
                oldest_id = next(iter(self.lost_pool))
                self.lost_pool.pop(oldest_id, None)
        self._kalmans.pop(track_id, None)
        self._id_map = {
            raw_k: can_v for raw_k, can_v in self._id_map.items() if can_v != track_id
        }

    def _coast_missing(self, tid: int, dx: float = 0.0, dy: float = 0.0) -> None:
        t = self.targets[tid]
        t.misses += 1
        if t.misses > config.TRACK_BUFFER:
            self._drop(tid)
            return
        kf = self._kf(tid)
        if not kf.initialized:
            return
        cx, cy = kf.predict_only()
        cx += dx
        cy += dy
        conf = max(0.05, t.det.conf * 0.92)
        t.det = _detection_at_center(t.det, cx, cy, conf=conf)
        t.predicted = True
        t.center_history.append((cx, cy))
        if len(t.center_history) > 60:
            t.center_history.pop(0)

    def _stick_iou(self) -> float:
        return float(getattr(config, "TRACK_STICK_IOU", 0.12))

    def _stick_center_ratio(self) -> float:
        return float(getattr(config, "TRACK_STICK_CENTER_RATIO", 2.5))

    def _try_active_stick(self, det: Detection) -> int | None:
        """Yeni ByteTrack ID → mevcut kanonik ID (gevşek IoU/merkez).

        En yaşlı / en düşük ID tercih edilir; böylece #1 varken #2 açılmaz.
        """
        stick_iou = self._stick_iou()
        stick_center = self._stick_center_ratio()
        best_id: int | None = None
        best_key: tuple[int, int] | None = None  # (-age, id) → max age, min id
        for act_id, act_t in self.targets.items():
            if act_t.det.class_id != det.class_id:
                continue
            if not boxes_same_object(
                act_t.det.as_xyxy(),
                det.as_xyxy(),
                iou_threshold=stick_iou,
                center_ratio=stick_center,
            ):
                continue
            key = (-act_t.age, act_id)
            if best_key is None or key < best_key:
                best_key = key
                best_id = act_id
        return best_id

    def _try_lost_stick(self, det: Detection, feat: np.ndarray | None) -> int | None:
        """lost_pool'dan geri al — Re-ID veya sadece merkez yakınlığı."""
        max_dist = float(getattr(config, "REID_MAX_DISTANCE_PX", 220.0))
        stick_iou = self._stick_iou()
        stick_center = self._stick_center_ratio()
        best_id: int | None = None
        best_score = -1.0
        use_reid = bool(getattr(config, "ENABLE_REID", True))
        reid_thr = float(getattr(config, "REID_SIMILARITY_THRESHOLD", 0.55))

        for lost_id, lost_t in list(self.lost_pool.items()):
            if lost_t.det.class_id != det.class_id:
                continue
            dist = math.hypot(lost_t.det.cx - det.cx, lost_t.det.cy - det.cy)
            near = dist <= max_dist or boxes_same_object(
                lost_t.det.as_xyxy(),
                det.as_xyxy(),
                iou_threshold=stick_iou,
                center_ratio=stick_center,
            )
            if not near:
                continue
            score = 1.0 / (1.0 + dist)
            if use_reid and feat is not None and lost_t.feature is not None:
                sim = self.reid.similarity(feat, lost_t.feature)
                if sim >= reid_thr:
                    score += float(sim)
                elif dist > max_dist * 0.5:
                    continue
            if score > best_score:
                best_score = score
                best_id = lost_id
        return best_id

    def _resolve_canonical_id(
        self, raw_tid: int, det: Detection, feat: np.ndarray | None
    ) -> int:
        """ByteTrack raw ID → sabit kanonik ID (bir kez bağlanınca kopmaz)."""
        if raw_tid in self._id_map:
            mapped = self._id_map[raw_tid]
            if mapped in self.targets or mapped in self.lost_pool:
                if mapped in self.lost_pool and mapped not in self.targets:
                    self.targets[mapped] = self.lost_pool.pop(mapped)
                return mapped
            # Eski map çürükse yeniden çöz
            self._id_map.pop(raw_tid, None)

        matched = self._try_active_stick(det)
        if matched is not None:
            self._id_map[raw_tid] = matched
            return matched

        lost_matched = self._try_lost_stick(det, feat)
        if lost_matched is not None:
            restored = self.lost_pool.pop(lost_matched)
            self.targets[lost_matched] = restored
            self._id_map[raw_tid] = lost_matched
            return lost_matched

        self._id_map[raw_tid] = raw_tid
        return raw_tid

    def _merge_duplicate_targets(self, seen: set[int]) -> set[int]:
        """Aynı nesneye iki kanonik ID düşerse yaşlıyı tut, yeniyi yut."""
        stick_iou = self._stick_iou()
        stick_center = self._stick_center_ratio()
        ids = list(self.targets.keys())
        for i, a_id in enumerate(ids):
            if a_id not in self.targets:
                continue
            for b_id in ids[i + 1 :]:
                if b_id not in self.targets:
                    continue
                a_t, b_t = self.targets[a_id], self.targets[b_id]
                if a_t.det.class_id != b_t.det.class_id:
                    continue
                if not boxes_same_object(
                    a_t.det.as_xyxy(),
                    b_t.det.as_xyxy(),
                    iou_threshold=stick_iou,
                    center_ratio=stick_center,
                ):
                    continue
                # Yaşlı / küçük ID kalsın
                if (a_t.age, -a_id) >= (b_t.age, -b_id):
                    keep, drop = a_id, b_id
                else:
                    keep, drop = b_id, a_id
                if drop in seen and keep not in seen:
                    # Ölçüm yeni IDdeydi → yaşlıya taşı
                    self.targets[keep].det = self.targets[drop].det
                    self.targets[keep].misses = 0
                    self.targets[keep].predicted = False
                    seen.add(keep)
                    seen.discard(drop)
                for raw_k, can_v in list(self._id_map.items()):
                    if can_v == drop:
                        self._id_map[raw_k] = keep
                # Mükerrer: lost_pool'a koyma (yoksa #2 tekrar doğar)
                self.targets.pop(drop, None)
                self._kalmans.pop(drop, None)
                self.lost_pool.pop(drop, None)
        return seen

    def _suppress_overlapping(self, tracked: sv.Detections) -> sv.Detections:
        n = len(tracked)
        if n <= 1:
            return tracked

        def sort_key(i: int) -> float:
            raw_tid = _as_int(tracked.tracker_id[i]) if tracked.tracker_id is not None else -1
            can_id = self._id_map.get(raw_tid, raw_tid)
            is_active = 1.0 if can_id in self.targets else 0.0
            conf = _as_float(tracked.confidence[i])
            return is_active * 2.0 + conf

        order = sorted(range(n), key=sort_key, reverse=True)
        keep: list[int] = []
        for i in order:
            cls_i = _as_int(tracked.class_id[i])
            box_i = tracked.xyxy[i]
            if any(
                _as_int(tracked.class_id[j]) == cls_i
                and boxes_same_object(
                    box_i, tracked.xyxy[j], iou_threshold=config.TRACK_DEDUPE_IOU
                )
                for j in keep
            ):
                continue
            keep.append(i)
        return tracked[keep]

    @staticmethod
    def _to_sv(detections: list[Detection]) -> sv.Detections:
        valid_dets = [d for d in detections if d.conf >= config.TRACK_LOW_CONF]
        if not valid_dets:
            return sv.Detections.empty()
        return sv.Detections(
            xyxy=np.array([d.as_xyxy() for d in valid_dets]),
            confidence=np.array([d.conf for d in valid_dets]),
            class_id=np.array([d.class_id for d in valid_dets]),
        )

    def update(
        self, detections: list[Detection], frame: np.ndarray | None = None
    ) -> dict[int, TrackedTarget]:
        dx, dy = 0.0, 0.0
        if getattr(config, "ENABLE_GMC", True) and frame is not None:
            active_boxes = [t.det.as_xyxy() for t in self.targets.values()]
            dx, dy = self.gmc.estimate_motion(frame, target_boxes=active_boxes)
            if abs(dx) > 0.3 or abs(dy) > 0.3:
                for kf in self._kalmans.values():
                    if kf.initialized:
                        kf.kf.statePost[0, 0] += np.float32(dx)
                        kf.kf.statePost[1, 0] += np.float32(dy)

        tracked = self.tracker.update_with_detections(self._to_sv(detections))
        if len(tracked) == 0 or tracked.tracker_id is None:
            for tid in list(self.targets):
                self._coast_missing(tid, dx, dy)
            return self.targets

        tracked = self._suppress_overlapping(tracked)
        if len(tracked) == 0 or tracked.tracker_id is None:
            for tid in list(self.targets):
                self._coast_missing(tid, dx, dy)
            return self.targets

        seen: set[int] = set()
        for xyxy, conf, cls_id, raw_tid in zip(
            tracked.xyxy,
            tracked.confidence,
            tracked.class_id,
            tracked.tracker_id,
        ):
            raw_tid = _as_int(raw_tid)
            x1, y1, x2, y2 = (float(v) for v in np.asarray(xyxy).reshape(-1)[:4])
            raw = Detection(
                x1, y1, x2, y2,
                conf=_as_float(conf),
                class_id=_as_int(cls_id),
                source="track",
            )
            feat = self.reid.extract_feature(frame, (x1, y1, x2, y2))
            tid = self._resolve_canonical_id(raw_tid, raw, feat)
            seen.add(tid)
            self._kf(tid).update(raw.cx, raw.cy)
            det = raw
            fx, fy = raw.cx, raw.cy

            if tid in self.targets:
                t = self.targets[tid]
                t.det = det
                t.age += 1
                t.misses = 0
                t.predicted = False
                if feat is not None:
                    if t.feature is None:
                        t.feature = feat
                    else:
                        t.feature = 0.7 * t.feature + 0.3 * feat
            else:
                t = TrackedTarget(
                    track_id=tid, det=det, age=1, predicted=False, feature=feat
                )
                self.targets[tid] = t

            t.center_history.append((fx, fy))
            if len(t.center_history) > 60:
                t.center_history.pop(0)

        seen = self._merge_duplicate_targets(seen)

        for tid in list(self.targets):
            if tid in seen:
                continue
            # Eski davranış: örtüşen coast ID'yi düşürüyordu → #1 kaybolup #2 kalıyordu.
            # Artık coast devam; mükerrerler _merge_duplicate_targets'ta çözülür.
            self._coast_missing(tid, dx, dy)

        return self.targets

    @staticmethod
    def stability(t: TrackedTarget) -> float:
        if len(t.center_history) < 5:
            return 0.0
        pts = np.array(t.center_history[-20:])
        jitter = float(np.mean(np.std(pts, axis=0)))
        return 1.0 / (1.0 + jitter / 10.0)


class ServoKalman:
    """Servoya giden merkez — yumuşak CV Kalman (sıçramasız).

    lead_time varsayılan 0: hız gürültüsü tavan/fırlama yapıyordu.
    Çıkış kareler arası SERVO_TARGET_MAX_JUMP_PX ile sınırlanır.
    """

    def __init__(self, lead_time: float | None = None):
        self.lead_time = float(
            lead_time
            if lead_time is not None
            else getattr(config, "SERVO_KALMAN_LEAD_S", 0.0)
        )
        self._max_jump = float(getattr(config, "SERVO_TARGET_MAX_JUMP_PX", 40.0))
        self._max_speed = 600.0  # px/s — absürt vy/vx kes
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32
        )
        # Ölçüme daha çok güven; hızı az serbest bırak (Sena 0.8 çok agresifti)
        self.kf.processNoiseCov = np.diag([0.08, 0.08, 0.15, 0.15]).astype(np.float32)
        self.kf.measurementNoiseCov = np.diag([0.08, 0.08]).astype(np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self._set_dt(0.033)
        self.initialized = False
        self._last_out: tuple[float, float] | None = None

    def _set_dt(self, dt: float) -> None:
        dt = max(0.001, min(float(dt), 0.2))
        self.kf.transitionMatrix = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )

    def reset(self) -> None:
        self.kf.statePost = np.zeros((4, 1), dtype=np.float32)
        self.kf.statePre = np.zeros((4, 1), dtype=np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False
        self._last_out = None

    def _clamp_speed(self) -> None:
        sp = self.kf.statePost
        vx = float(np.clip(_as_float(sp[2, 0]), -self._max_speed, self._max_speed))
        vy = float(np.clip(_as_float(sp[3, 0]), -self._max_speed, self._max_speed))
        sp[2, 0] = np.float32(vx)
        sp[3, 0] = np.float32(vy)

    def _limit_jump(self, x: float, y: float) -> tuple[float, float]:
        if self._last_out is None:
            self._last_out = (x, y)
            return x, y
        lx, ly = self._last_out
        dx = max(-self._max_jump, min(self._max_jump, x - lx))
        dy = max(-self._max_jump, min(self._max_jump, y - ly))
        out = (lx + dx, ly + dy)
        self._last_out = out
        return out

    def update(
        self, cx: float, cy: float, dt: float = 0.033
    ) -> tuple[float, float]:
        self._set_dt(dt)
        if not self.initialized:
            self.kf.statePost = np.array(
                [[np.float32(cx)], [np.float32(cy)], [0], [0]], dtype=np.float32
            )
            self.kf.statePre = self.kf.statePost.copy()
            self.initialized = True
            return self._limit_jump(float(cx), float(cy))

        self.kf.predict()
        measurement = np.array(
            [[np.float32(cx)], [np.float32(cy)]], dtype=np.float32
        )
        estimate = self.kf.correct(measurement)
        self._clamp_speed()
        x = _as_float(estimate[0, 0])
        y = _as_float(estimate[1, 0])
        vx = _as_float(self.kf.statePost[2, 0])
        vy = _as_float(self.kf.statePost[3, 0])
        if self.lead_time > 0.0:
            x += vx * self.lead_time
            y += vy * self.lead_time
        return self._limit_jump(x, y)

    def predict_only(self, dt: float = 0.033) -> tuple[float, float]:
        if not self.initialized:
            return 0.0, 0.0
        # Miss'te son çıkışı tut — hayalet hızla tavana gitmesin
        if self._last_out is not None:
            return self._last_out
        self._set_dt(dt)
        pred = self.kf.predict()
        self._clamp_speed()
        return self._limit_jump(_as_float(pred[0, 0]), _as_float(pred[1, 0]))
