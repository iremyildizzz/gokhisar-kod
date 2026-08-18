"""HSS (Hava Savunma Sistemi) PC Ana Orkestratörü ve Boru Hattı (Pipeline).

Tüm modülleri (Tespit, Doğrulama, IFF, Takip, Önceliklendirme, Yaşam Döngüsü,
Haberleşme ve Latency Tracker) yaşam döngüsüne göre birbirine bağlar.

Gecikme Ölçümü ve Optimizasyon:
  - Kamera yakalama anından (t_capture) itibaren her kareden mikro saniye hassasiyetinde
    uçtan uca (end-to-end) gecikme verisi toplanır.
  - LatencyTracker ile her boru hattı aşamasının harcadığı süre ölçülür.
  - Gecikme eşiği aşıldığında veya YOLO yükü arttığında adaptif yedek moda (backup_mode)
    geçilerek FPS ve tepki süresi korunur.
"""
import os
import sys
import time
import cv2
import numpy as np

# Modül arama yoluna pc ve proje kök klasörünü ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import config
from detection.yolo_detector import YoloDetector, Detection
from detection.hsv_detector import HsvBalloonDetector
from validation.matcher import TargetMatcher
from iff.friend_foe import FriendFoeClassifier, IFFLabel
from tracking.tracker import TargetTracker, ServoKalman
from evaluation.prioritizer import TargetPrioritizer
from evaluation.latency_tracker import LatencyTracker
from lifecycle.state_machine import TargetLifecycleManager, TargetState
from comms.rpi_link import RpiLink


class Pipeline:
    def __init__(self, rpi_link: RpiLink | None = None, backup_mode: bool = False):
        self.rpi_link = rpi_link or RpiLink()
        self.backup_mode = backup_mode

        # Modül örnekleri
        self.yolo = YoloDetector(config.YOLO_MODEL_PATH)
        self.hsv = HsvBalloonDetector()
        self.matcher = TargetMatcher(self.yolo)
        self.iff = FriendFoeClassifier(stage=3)
        self.tracker = TargetTracker()
        self.servo_kalman = ServoKalman()
        self.prioritizer = TargetPrioritizer()
        self.lifecycle = TargetLifecycleManager()
        self.latency_tracker = LatencyTracker()

        # Adaptif Optimizasyon Ayarları
        self.latency_threshold_ms = 45.0  # ~22 FPS altı uyarısı ve yedek moda geçiş
        self.show_latency_hud = True

    def process(self, frame: np.ndarray, t_capture: float = 0.0) -> np.ndarray:
        """Her kare için veri akışını çalıştırır, gecikmeleri ölçer ve görselleştirir."""
        pipeline_start = time.perf_counter()
        
        # 0. Kuyruk Gecikmesi Ölçümü
        self.latency_tracker.record_queue_delay(t_capture)
        annotated = frame.copy()

        # 1. TESPİT AŞAMASI (DETECT)
        all_detections: list[Detection] = []
        
        if self.backup_mode:
            # Düşük sistem kaynağı / yüksek gecikme durumunda yalnız HSV
            with self.latency_tracker.measure("hsv_detection"):
                all_detections = self.hsv.detect_backup(frame)
        else:
            # Standart Mod: Tam kare YOLO + HSV küçük hedef tespiti
            with self.latency_tracker.measure("yolo_detection"):
                models = self.yolo.detect(frame)
                all_detections.extend(models)

            with self.latency_tracker.measure("hsv_detection"):
                balloons = self.hsv.detect(frame)
                
                # DOĞRULAMA (VALIDATE): Maket - Balon eşleştirmesi
                with self.latency_tracker.measure("matching"):
                    validated, unmatched = self.matcher.match(frame, models, balloons)
                    for val in validated:
                        all_detections.append(val.balloon_det)

        # 2. TAKİP AŞAMASI (TRACK)
        with self.latency_tracker.measure("tracking"):
            tracked_targets = self.tracker.update(all_detections)

        # 3. IFF & YAŞAM DÖNGÜSÜ & ÖNCELİKLENDİRME
        foes = []
        for tid, target in tracked_targets.items():
            self.lifecycle.on_validated(tid)
            
            # IFF Ayrımı
            with self.latency_tracker.measure("iff_classification"):
                iff_label = self.iff.classify(frame, target.det, tid)
                self.lifecycle.on_iff(tid, iff_label)

            if iff_label == IFFLabel.FOE:
                foes.append(target)

            # Çizim (Sınır Kutuları & Etiketler)
            self._draw_target(annotated, target, self.lifecycle.get(tid))

        # Önceliklendirme (EVALUATE)
        with self.latency_tracker.measure("prioritization"):
            primary_target = self.prioritizer.select(foes)

        # 4. KİLİTLENME VE HABERLEŞME (TARGET_LOCK & COMMS)
        if primary_target:
            tid = primary_target.track_id
            rec = self.lifecycle.get(tid)
            self.lifecycle.on_selected_for_lock(tid)
            
            # Kilit kontrolü
            is_locked = self.lifecycle.update_lock(rec, primary_target)

            # Servo Kalman Filtresi Güncellemesi
            smooth_cx, smooth_cy = self.servo_kalman.update(primary_target.det.cx, primary_target.det.cy)
            
            # Target Lock Vurgulaması
            lock_color = (0, 0, 255) if is_locked else (0, 255, 255)
            cv2.circle(annotated, (int(smooth_cx), int(smooth_cy)), 12, lock_color, 2)
            cv2.line(annotated, (int(smooth_cx) - 15, int(smooth_cy)), (int(smooth_cx) + 15, int(smooth_cy)), lock_color, 2)
            cv2.line(annotated, (int(smooth_cx), int(smooth_cy) - 15), (int(smooth_cx), int(smooth_cy) + 15), lock_color, 2)

            # RPi'ye Hedef Verilerini Gönder
            with self.latency_tracker.measure("comms"):
                self.rpi_link.send_target(
                    cx=smooth_cx,
                    cy=smooth_cy,
                    class_id=primary_target.det.class_id,
                    track_id=primary_target.track_id,
                    locked=is_locked
                )

        # 5. GECİKME ÖLÇÜM SONLANDIRMA & TELEMETRİ HUD ÇİZİMİ
        self.latency_tracker.record_end_to_end(t_capture, pipeline_start)

        # Adaptif Optimizasyon Kontrolü
        e2e_avg = self.latency_tracker.get_avg("end_to_end")
        if e2e_avg > self.latency_threshold_ms and not self.backup_mode:
            # Gecikme 45ms üzerindeyse sürücü uyarısı / adaptif kip uyarısı
            cv2.putText(annotated, "WARN: LATENCY SPIKE DETECTED - CONSIDER BACKUP MODE",
                        (20, annotated.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

        if self.show_latency_hud:
            annotated = self.latency_tracker.draw_hud(annotated, show_details=True)

        return annotated

    def _draw_target(self, frame: np.ndarray, target, rec):
        """Kare üzerine hedef kutusu, kimliği ve durum bilgilerini çizer."""
        det = target.det
        x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)
        
        # Renk Seçimi
        color = (200, 200, 200)  # UNKNOWN
        if rec.iff == IFFLabel.FRIEND:
            color = (255, 200, 0) # DOST (Cyan/Mavi)
        elif rec.iff == IFFLabel.FOE:
            color = (0, 0, 255)   # DÜŞMAN (Kırmızı)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"ID:{target.track_id} | {rec.iff.value} | {rec.state.name}"
        cv2.putText(frame, label, (x1, max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def run_benchmark(duration_sec: int = 5, num_frames: int = 100):
    """Boru hattının gecikmesini sentetik kareler üzerinde ölçen test fonksiyonu."""
    print("=" * 60)
    print(f"HSS Pipeline Veri Akışı Gecikme Testi Başlatılıyor ({num_frames} Kare)...")
    print("=" * 60)

    pipeline = Pipeline(backup_mode=False)
    dummy_frame = np.zeros((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)
    
    # Bazı renkli hedefler ekleyelim (HSV ve IFF testleri için)
    cv2.circle(dummy_frame, (300, 300), 25, (0, 0, 255), -1)   # Kırmızı balon
    cv2.circle(dummy_frame, (600, 400), 20, (255, 255, 0), -1) # Camgöbeği dost

    start_total = time.perf_counter()
    for i in range(num_frames):
        t_cap = time.perf_counter()
        # Küçük simüle gecikme (kuyruk beklemesi simülasyonu)
        time.sleep(0.002)
        pipeline.process(dummy_frame, t_capture=t_cap)

    total_time = time.perf_counter() - start_total
    summary = pipeline.latency_tracker.get_summary()
    bottleneck_name, bottleneck_ms = pipeline.latency_tracker.get_bottleneck()

    print("\n--- GECİKME ÖLÇÜM SONUÇLARI ---")
    print(f"Toplam Test Süresi    : {total_time:.2f} s")
    print(f"Ortalama İşleme FPS   : {summary['fps']:.2f}")
    print(f"Uçtan Uca Gecikme(E2E): {summary['end_to_end']:.2f} ms")
    print(f"Kuyruk Gecikmesi      : {summary['queue_delay']:.2f} ms")
    print(f"Toplam Pipeline Süresi : {summary['total_pipeline']:.2f} ms")
    print("-" * 40)
    print("Aşama Bazlı Gecikme Dağılımı:")
    print(f"  - YOLO Tespit       : {summary['yolo_detection']:.2f} ms")
    print(f"  - HSV Tespit        : {summary['hsv_detection']:.2f} ms")
    print(f"  - Eşleştirme        : {summary['matching']:.2f} ms")
    print(f"  - Takip (ByteTrack) : {summary['tracking']:.2f} ms")
    print(f"  - IFF Ayrımı        : {summary['iff_classification']:.2f} ms")
    print(f"  - Haberleşme (RPi)  : {summary['comms']:.2f} ms")
    print("-" * 40)
    print(f"Darboğaz Aşama        : {bottleneck_name} ({bottleneck_ms:.2f} ms)")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--benchmark":
        run_benchmark()
    else:
        from PySide6.QtWidgets import QApplication
        from gui.main_window import MainWindow

        app = QApplication(sys.argv)
        pipeline = Pipeline()
        window = MainWindow(pipeline, pipeline.rpi_link)
        window.show()
        sys.exit(app.exec())
