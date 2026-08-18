"""pytest ile otomatize testler (rapor: iteratif prototipleme + pytest)."""
import numpy as np
import pytest

import config
from detection.yolo_detector import Detection
from detection.hsv_detector import HsvBalloonDetector
from iff.friend_foe import FriendFoeClassifier, IFFLabel
from lifecycle.state_machine import (TargetLifecycleManager, TargetState,
                                     TargetRecord)
from tracking.tracker import TargetTracker, TrackedTarget


def make_frame(color=(0, 0, 0)):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def draw_circle(frame, center, r, bgr):
    import cv2
    cv2.circle(frame, center, r, bgr, -1)
    return frame


# ---------------- HSV tespit ----------------
def test_hsv_detects_red_balloon_on_black():
    frame = draw_circle(make_frame(), (640, 360), 30, (0, 0, 255))
    dets = HsvBalloonDetector().detect(frame)
    assert len(dets) == 1
    assert abs(dets[0].cx - 640) < 3 and abs(dets[0].cy - 360) < 3


def test_hsv_ignores_non_circular_shape():
    import cv2
    frame = make_frame()
    cv2.rectangle(frame, (100, 100), (400, 130), (0, 0, 255), -1)  # ince serit
    dets = HsvBalloonDetector().detect(frame)
    assert len(dets) == 0


def test_hsv_trigger_requires_30_mismatch_frames():
    detector = HsvBalloonDetector(trigger_frame_threshold=30)
    frame = draw_circle(make_frame(), (640, 360), 30, (0, 0, 255))

    # 29 frame boyunca nesne var (1) ama eşleşen balon yok (0)
    for _ in range(29):
        dets = detector.detect(frame, num_objects=1, num_balloons=0)
        assert len(dets) == 0

    # 30. frame'de şart sağlanır ve tespit çalışır
    dets = detector.detect(frame, num_objects=1, num_balloons=0)
    assert len(dets) == 1

    # Nesne sayısı ile balon sayısı eşitlendiğinde (1 vs 1) sayaç sıfırlanır
    assert detector.update_condition(num_objects=1, num_balloons=1) is False
    assert detector.mismatch_frame_count == 0


# ---------------- IFF zamansal oylama ----------------
def test_iff_requires_multiple_consistent_frames():
    clf = FriendFoeClassifier(stage=3)
    frame = draw_circle(make_frame(), (640, 360), 40, (0, 0, 255))
    det = Detection(600, 320, 680, 400, 0.9, 0)
    # tek kare yeterli degil
    assert clf.classify(frame, det, track_id=1) is IFFLabel.UNKNOWN
    # tutarli kareler sonrasi dusman dogrulanir
    for _ in range(config.IFF_VOTE_MIN_FRAMES):
        label = clf.classify(frame, det, track_id=1)
    assert label is IFFLabel.FOE


def test_iff_stage2_all_foe():
    clf = FriendFoeClassifier(stage=2)
    det = Detection(0, 0, 10, 10, 0.9, 0)
    assert clf.classify(make_frame(), det, track_id=5) is IFFLabel.FOE


# ---------------- takip / tekilleştirme ----------------
def _iou(a: Detection, b: Detection) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    union = a.area + b.area - intersection
    return intersection / union if union > 0 else 0.0


def _dedupe(detections: list[Detection]) -> list[Detection]:
    from tracking.tracker import detections_same_object
    kept: list[Detection] = []
    for det in sorted(detections, key=lambda d: d.conf, reverse=True):
        if any(detections_same_object(other, det) for other in kept):
            continue
        kept.append(det)
    return kept


def test_dedupe_removes_offset_duplicate_balloons():
    """Kaydırmalı çift YOLO kutusu tek tespitte birleşmeli."""
    balloon = config.BALLOON_CLASS_ID
    centered = Detection(500, 300, 600, 400, 0.71, balloon, source="yolo")
    shifted = Detection(550, 300, 650, 400, 0.66, balloon, source="yolo")
    kept = _dedupe([centered, shifted])
    assert len(kept) == 1
    assert kept[0].conf == 0.71


def test_dedupe_removes_phantom_background_box():
    """Arka plana kaymış hayalet kutu (düşük IoU) birleştirilmeli."""
    balloon = config.BALLOON_CLASS_ID
    accurate = Detection(400, 280, 520, 400, 0.66, balloon, source="yolo")
    phantom = Detection(500, 200, 620, 320, 0.71, balloon, source="yolo")
    kept = _dedupe([accurate, phantom])
    assert len(kept) == 1
    assert kept[0].conf == 0.71


def test_dedupe_keeps_different_classes_with_overlap():
    """Maket ve altındaki balon birlikte kalmalı."""
    model = Detection(500, 250, 600, 350, 0.9, 0, source="yolo")
    balloon = Detection(520, 340, 580, 400, 0.7, config.BALLOON_CLASS_ID, source="yolo")
    kept = _dedupe([model, balloon])
    assert len(kept) == 2


def test_tracker_suppresses_overlapping_same_class_tracks():
    """ByteTrack çıktısındaki çift kimlik tekilleştirilmeli."""
    tracker = TargetTracker()
    balloon = config.BALLOON_CLASS_ID
    dets = [
        Detection(500, 300, 600, 400, 0.71, balloon, source="yolo"),
        Detection(550, 300, 650, 400, 0.66, balloon, source="yolo"),
    ]
    for _ in range(3):
        active = tracker.update(dets)
    assert len(active) == 1


def test_tracker_suppresses_phantom_background_tracks():
    """Hayalet kutu senaryosunda tek iz kalmalı."""
    tracker = TargetTracker()
    balloon = config.BALLOON_CLASS_ID
    dets = [
        Detection(400, 280, 520, 400, 0.66, balloon, source="yolo"),
        Detection(500, 200, 620, 320, 0.71, balloon, source="yolo"),
    ]
    for _ in range(3):
        active = tracker.update(dets)
    assert len(active) == 1


# ---------------- imha değerlendirme (üç koşul) ----------------
def test_destroy_requires_all_three_conditions():
    mgr = TargetLifecycleManager()
    rec = TargetRecord(track_id=1, state=TargetState.TARGET_LOCK, fired=True)

    # hedef hala takipte (yuksek guven) -> imha YOK, TRACK'e doner
    alive = TrackedTarget(track_id=1,
                          det=Detection(0, 0, 50, 50, 0.9, 0), misses=0)
    assert mgr.evaluate_destroyed(rec, alive) is False
    assert rec.state is TargetState.TRACK

    # takip zinciri sonlandi (target=None) -> uc kosul saglanir
    rec.fired = True
    assert mgr.evaluate_destroyed(rec, None) is True
    assert rec.state is TargetState.DESTROYED


# ---------------- PID ----------------
def test_pid_converges_toward_center(monkeypatch):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../rpi"))
    from pid_controller import PanTiltController

    ctrl = PanTiltController()
    target_x, target_y = 900.0, 500.0   # merkezden sapmis hedef
    pan0, tilt0 = ctrl.pan_angle, ctrl.tilt_angle
    for _ in range(50):
        ctrl.step(target_x, target_y)
    # hata pozitif (hedef sagda/asagida) -> acilar artmali
    assert ctrl.pan_angle > pan0
    assert ctrl.tilt_angle > tilt0
