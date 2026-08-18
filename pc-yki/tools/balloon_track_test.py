"""Balon modeli takip testi — YOLO + ByteTrack + Kalman.

Yeni ağırlık: models/balloon_best_052.pt (tek sınıf: Balloon).

Çalıştırma (gokhisar-kod kökünden):
    python tools/balloon_track_test.py
    python tools/balloon_track_test.py --source 0 --conf 0.25

Çıkış: q veya ESC

Yarışma arayüzü için bu script değil:
    python main.py --rpi-host 192.168.137.133 --weights models/balloon_best_052.pt
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = ROOT / "models" / "balloon_best_052.pt"

IOU_THRESHOLD = 0.50
MAX_HISTORY = 60
STABILITY_WINDOW = 20
PROCESS_NOISE = 1e-3
MEASUREMENT_NOISE = 5e-2
PRINT_EVERY_S = 0.5


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    class_id: int

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass
class TrackedTarget:
    track_id: int
    det: Detection
    age: int = 0
    misses: int = 0
    center_history: list = field(default_factory=list)
    filtered_history: list = field(default_factory=list)


class CenterKalman:
    def __init__(self) -> None:
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32
        )
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * PROCESS_NOISE
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * MEASUREMENT_NOISE
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        measurement = np.array([[np.float32(cx)], [np.float32(cy)]])
        if not self.initialized:
            self.kf.statePost = np.array([[cx], [cy], [0], [0]], dtype=np.float32)
            self.initialized = True
        self.kf.predict()
        state = self.kf.correct(measurement)
        return float(state[0, 0]), float(state[1, 0])


class TargetTracker:
    def __init__(self, model_path: Path, conf: float = 0.25) -> None:
        print(f"[INFO] Model: {model_path}")
        self.model = YOLO(str(model_path))
        self.conf = conf
        print(f"[INFO] Sınıflar: {self.model.names}  conf={self.conf}")
        self.targets: dict[int, TrackedTarget] = {}
        self.kalmans: dict[int, CenterKalman] = {}

    def update(self, frame: np.ndarray) -> dict[int, TrackedTarget]:
        results = self.model.track(
            source=frame,
            tracker="bytetrack.yaml",
            persist=True,
            conf=self.conf,
            iou=IOU_THRESHOLD,
            verbose=False,
        )
        seen: set[int] = set()
        if not results:
            self._update_missing(seen)
            return self.targets

        boxes = results[0].boxes
        if boxes is None or boxes.id is None:
            self._update_missing(seen)
            return self.targets

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        ids = boxes.id.cpu().numpy().astype(int)

        for box, c, class_id, track_id in zip(xyxy, confs, classes, ids):
            x1, y1, x2, y2 = box.tolist()
            track_id = int(track_id)
            seen.add(track_id)
            det = Detection(x1, y1, x2, y2, float(c), int(class_id))

            if track_id not in self.targets:
                self.targets[track_id] = TrackedTarget(track_id=track_id, det=det, age=1)
                self.kalmans[track_id] = CenterKalman()
            else:
                t = self.targets[track_id]
                t.det = det
                t.age += 1
                t.misses = 0

            target = self.targets[track_id]
            target.center_history.append((det.cx, det.cy))
            fx, fy = self.kalmans[track_id].update(det.cx, det.cy)
            target.filtered_history.append((fx, fy))
            target.center_history = target.center_history[-MAX_HISTORY:]
            target.filtered_history = target.filtered_history[-MAX_HISTORY:]

        self._update_missing(seen)
        return self.targets

    def _update_missing(self, seen: set[int]) -> None:
        for track_id in list(self.targets.keys()):
            if track_id in seen:
                continue
            self.targets[track_id].misses += 1
            if self.targets[track_id].misses > 60:
                del self.targets[track_id]
                self.kalmans.pop(track_id, None)

    @staticmethod
    def stability(target: TrackedTarget) -> float:
        history = target.center_history[-STABILITY_WINDOW:]
        if len(history) < 5:
            return 0.0
        points = np.array(history, dtype=np.float32)
        jitter = float(np.mean(np.std(points, axis=0)))
        return 1.0 / (1.0 + jitter / 10.0)


def create_telemetry(targets: dict[int, TrackedTarget]) -> dict:
    output: dict = {"type": "tracks", "t": time.time(), "targets": []}
    for target in targets.values():
        if target.misses != 0:
            continue
        det = target.det
        cx, cy = (
            target.filtered_history[-1]
            if target.filtered_history
            else (det.cx, det.cy)
        )
        output["targets"].append(
            {
                "track_id": target.track_id,
                "class_id": det.class_id,
                "confidence": round(det.conf, 3),
                "cx": round(cx, 1),
                "cy": round(cy, 1),
                "age": target.age,
                "stability": round(TargetTracker.stability(target), 3),
            }
        )
    return output


def draw_target(frame: np.ndarray, target: TrackedTarget) -> None:
    det = target.det
    x1, y1, x2, y2 = map(int, (det.x1, det.y1, det.x2, det.y2))
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.circle(frame, (int(det.cx), int(det.cy)), 4, (0, 0, 255), -1)
    if target.filtered_history:
        fx, fy = target.filtered_history[-1]
        cv2.circle(frame, (int(fx), int(fy)), 7, (255, 0, 255), 2)
    stab = TargetTracker.stability(target)
    text = f"ID:{target.track_id} conf:{det.conf:.2f} stab:{stab:.2f}"
    cv2.putText(
        frame, text, (x1, max(20, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Balon YOLO takip testi")
    p.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument("--source", default="0", help="Webcam index veya video yolu")
    p.add_argument("--conf", type=float, default=0.25)
    args = p.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"Model yok: {args.weights}")

    tracker = TargetTracker(args.weights, conf=args.conf)
    source: int | str = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Video açılamadı: {source}")

    prev = time.time()
    last_print = 0.0
    fps = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        targets = tracker.update(frame)
        for t in targets.values():
            if t.misses == 0:
                draw_target(frame, t)

        telem = create_telemetry(targets)
        now = time.time()
        if telem["targets"] and (now - last_print) >= PRINT_EVERY_S:
            print(json.dumps(telem, ensure_ascii=False))
            last_print = now

        dt = now - prev
        prev = now
        if dt > 0:
            fps = 0.9 * fps + 0.1 * (1.0 / dt)

        cv2.putText(
            frame,
            f"FPS:{fps:.1f} tracks:{len(telem['targets'])}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        cv2.imshow("Balloon track test", frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
