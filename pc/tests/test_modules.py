import os
import sys

# Modül yollarını ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
pc_dir = os.path.dirname(current_dir)
root_dir = os.path.dirname(pc_dir)
for d in (root_dir, pc_dir):
    if d not in sys.path:
        sys.path.insert(0, d)

import time
import numpy as np
import pytest

import config
from detection.yolo_detector import Detection
from detection.hsv_detector import HsvBalloonDetector
from iff.friend_foe import FriendFoeClassifier, IFFLabel
from lifecycle.state_machine import (TargetLifecycleManager, TargetState,
                                     TargetRecord)
from tracking.tracker import TrackedTarget
from evaluation.latency_tracker import LatencyTracker


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


# ---------------- Gecikme Ölçer (LatencyTracker) ----------------
def test_latency_tracker_measurement():
    tracker = LatencyTracker()
    t_cap = time.perf_counter()
    time.sleep(0.005) # 5ms kuyruk gecikmesi simülasyonu

    tracker.record_queue_delay(t_cap)
    pipe_start = time.perf_counter()

    with tracker.measure("yolo_detection"):
        time.sleep(0.010) # 10ms yolo simülasyonu

    with tracker.measure("hsv_detection"):
        time.sleep(0.003) # 3ms hsv simülasyonu

    tracker.record_end_to_end(t_cap, pipe_start)

    summary = tracker.get_summary()
    assert summary["queue_delay"] >= 4.0
    assert summary["yolo_detection"] >= 9.0
    assert summary["hsv_detection"] >= 2.0
    assert summary["end_to_end"] >= 15.0

    bottleneck, b_ms = tracker.get_bottleneck()
    assert bottleneck == "yolo_detection"
    assert b_ms >= 9.0
