"""Veri Akışı Gecikme Ölçüm ve Performans Analiz Modülü.

Kamera görüntüsünün alınmasından (t_capture) başlayarak tüm boru hattı
aşamalarını (YOLO, HSV, Eşleştirme, Takip, IFF, Önceliklendirme, Haberleşme)
yüksek çözünürlüklü zamanlayıcı (time.perf_counter) ile ölçer.

Elde edilen veriler ile:
  1. Uçtan uca (End-to-End) gecikme süresi hesaplanır.
  2. Kare bazlı ve hareketli ortalama (EMA / rolling) gecikme istatistikleri tutulur.
  3. Gecikme darboğazları (bottleneck) tespit edilir.
  4. Görüntü üzerine telemetry HUD katmanı çizilir.
  5. Yüksek gecikme durumunda otomatik optimizasyon uyarısı üretilir.
"""
from contextlib import contextmanager
import time
from collections import deque
import cv2
import numpy as np


class LatencyTracker:
    """Veri akışı gecikmesini mikro saniye hassasiyetinde ölçen ve analiz eden sınıf."""

    def __init__(self, buffer_size: int = 30):
        self.buffer_size = buffer_size
        self.reset()

    def reset(self):
        """İstatistik sayaçlarını sıfırlar."""
        self.stage_times: dict[str, float] = {}
        self.history: dict[str, deque] = {
            "queue_delay": deque(maxlen=self.buffer_size),
            "yolo_detection": deque(maxlen=self.buffer_size),
            "hsv_detection": deque(maxlen=self.buffer_size),
            "matching": deque(maxlen=self.buffer_size),
            "tracking": deque(maxlen=self.buffer_size),
            "iff_classification": deque(maxlen=self.buffer_size),
            "prioritization": deque(maxlen=self.buffer_size),
            "comms": deque(maxlen=self.buffer_size),
            "total_pipeline": deque(maxlen=self.buffer_size),
            "end_to_end": deque(maxlen=self.buffer_size),
        }
        self.last_frame_time = time.perf_counter()
        self.fps_history = deque(maxlen=self.buffer_size)

    @contextmanager
    def measure(self, stage_name: str):
        """Boru hattı aşamasının çalışma süresini ölçen context manager.

        Kullanım:
            with tracker.measure("yolo_detection"):
                yolo.detect(frame)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.stage_times[stage_name] = elapsed_ms
            if stage_name in self.history:
                self.history[stage_name].append(elapsed_ms)

    def record_queue_delay(self, t_capture: float):
        """Kare yakalanma anı ile boru hattına girme anı arasındaki kuyruk gecikmesi."""
        if t_capture > 0:
            delay_ms = (time.perf_counter() - t_capture) * 1000.0
            self.stage_times["queue_delay"] = delay_ms
            self.history["queue_delay"].append(delay_ms)
        else:
            self.stage_times["queue_delay"] = 0.0

    def record_end_to_end(self, t_capture: float, pipeline_start: float):
        """Uçtan uca (capture -> output) gecikmeyi ve boru hattı süresini kaydeder."""
        now = time.perf_counter()
        total_pipe_ms = (now - pipeline_start) * 1000.0
        self.stage_times["total_pipeline"] = total_pipe_ms
        self.history["total_pipeline"].append(total_pipe_ms)

        if t_capture > 0:
            e2e_ms = (now - t_capture) * 1000.0
            self.stage_times["end_to_end"] = e2e_ms
            self.history["end_to_end"].append(e2e_ms)
        else:
            self.stage_times["end_to_end"] = total_pipe_ms
            self.history["end_to_end"].append(total_pipe_ms)

        # FPS hesaplama
        frame_delta = now - self.last_frame_time
        self.last_frame_time = now
        if frame_delta > 0:
            self.fps_history.append(1.0 / frame_delta)

    def get_avg(self, stage_name: str) -> float:
        """Belirtilen aşama için hareketli ortalama gecikmeyi (ms) döner."""
        hist = self.history.get(stage_name)
        if not hist:
            return 0.0
        return float(np.mean(hist))

    def get_current_fps(self) -> float:
        """Ortalama işleme FPS değerini döner."""
        if not self.fps_history:
            return 0.0
        return float(np.mean(self.fps_history))

    def get_summary(self) -> dict[str, float]:
        """Tüm aşamaların ortalama gecikme özetini döner."""
        summary = {k: self.get_avg(k) for k in self.history}
        summary["fps"] = self.get_current_fps()
        return summary

    def get_bottleneck(self) -> tuple[str, float]:
        """En çok zaman alan işlem aşamasını (darboğaz) bulur."""
        stages = ["yolo_detection", "hsv_detection", "matching", "tracking", "iff_classification", "comms"]
        max_stage = "yolo_detection"
        max_time = 0.0
        for s in stages:
            avg_t = self.get_avg(s)
            if avg_t > max_time:
                max_time = avg_t
                max_stage = s
        return max_stage, max_time

    def draw_hud(self, frame: np.ndarray, show_details: bool = True) -> np.ndarray:
        """Görüntü üzerine modern telemetry latency HUD panelini çizer."""
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        e2e_avg = self.get_avg("end_to_end")
        pipe_avg = self.get_avg("total_pipeline")
        queue_avg = self.get_avg("queue_delay")
        fps = self.get_current_fps()
        bottleneck_name, bottleneck_ms = self.get_bottleneck()

        # Durum rengi (yeşil: <33ms [30FPS], sarı: <60ms, kırmızı: >=60ms)
        if e2e_avg < 33.0:
            status_color = (0, 230, 100)   # Yeşil
            status_text = "IDEAL"
        elif e2e_avg < 60.0:
            status_color = (0, 215, 255)   # Sarı / Amber
            status_text = "ACCEPTABLE"
        else:
            status_color = (50, 50, 255)   # Kırmızı
            status_text = "HIGH LATENCY"

        # Şeffaf HUD Paneli
        panel_w = 340
        panel_h = 175 if show_details else 65
        overlay = annotated.copy()
        cv2.rectangle(overlay, (10, 10), (10 + panel_w, 10 + panel_h), (20, 24, 30), -1)
        cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)
        cv2.rectangle(annotated, (10, 10), (10 + panel_w, 10 + panel_h), status_color, 1)

        # Başlık ve Genel İstatistikler
        cv2.putText(annotated, f"LATENCY MONITOR [{status_text}]", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, status_color, 1, cv2.LINE_AA)
        
        cv2.putText(annotated, f"FPS: {fps:.1f} | E2E: {e2e_avg:.1f}ms | Pipe: {pipe_avg:.1f}ms",
                    (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        if show_details:
            cv2.line(annotated, (20, 62), (10 + panel_w - 10, 62), (80, 80, 80), 1)
            
            yolo_ms = self.get_avg("yolo_detection")
            hsv_ms = self.get_avg("hsv_detection")
            match_ms = self.get_avg("matching")
            track_ms = self.get_avg("tracking")
            comms_ms = self.get_avg("comms")

            cv2.putText(annotated, f"Queue: {queue_avg:.1f}ms  YOLO: {yolo_ms:.1f}ms",
                        (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(annotated, f"HSV:   {hsv_ms:.1f}ms  Match: {match_ms:.1f}ms",
                        (20, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(annotated, f"Track: {track_ms:.1f}ms  Comms: {comms_ms:.1f}ms",
                        (20, 116), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
            
            cv2.line(annotated, (20, 126), (10 + panel_w - 10, 126), (80, 80, 80), 1)
            cv2.putText(annotated, f"Bottleneck: {bottleneck_name} ({bottleneck_ms:.1f}ms)",
                        (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 200, 255), 1, cv2.LINE_AA)

        return annotated

    @staticmethod
    def draw_hud_from_summary(
        frame: np.ndarray, summary: dict[str, float], show_details: bool = True
    ) -> np.ndarray:
        """get_summary() sözlüğünden HUD çizer (UI thread için)."""
        tracker = LatencyTracker(buffer_size=1)
        # Ortalama yerine anlık özet değerlerini history'ye tek örnek olarak koy
        for key, value in summary.items():
            if key == "fps":
                continue
            if key in tracker.history:
                tracker.history[key].append(float(value))
        fps = float(summary.get("fps", 0.0) or 0.0)
        if fps > 0:
            tracker.fps_history.append(fps)
        return tracker.draw_hud(frame, show_details=show_details)
