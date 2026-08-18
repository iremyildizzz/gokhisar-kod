"""Küçük Hedef Tespiti + Yedek Balon Algılama (HSV boru hattı).

Arka planın siyah, balonların kırmızı olması bilgisinden yararlanır:
HSV dönüşümü -> kırmızı için çift eşikli maskeleme -> morfolojik
temizleme -> kontur analizi -> dairesellik filtresi. Balon adayının
etrafında piksel boyutuna bağlı dinamik ROI üretir; bu ROI, YOLO'da
yeniden değerlendirilmek üzere tespit modülüne verilir.
"""
import cv2
import numpy as np

import config
from detection.yolo_detector import Detection


class HsvBalloonDetector:
    def __init__(self, trigger_frame_threshold: int = 30,
                 lidar_min_m: float = 10.0,
                 lidar_max_m: float = 15.0):
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.trigger_frame_threshold = trigger_frame_threshold
        self.mismatch_frame_count = 0
        self.lidar_min_m = lidar_min_m
        self.lidar_max_m = lidar_max_m
        self._prev_gray: np.ndarray | None = None

    def detect_motion(self, frame: np.ndarray,
                      threshold: int = 25,
                      min_motion_pixels: int = 250,
                      is_turret_moving: bool = False) -> bool:
        """Görüntüde hareketlilik tespiti (Kamera/Taret hareketi telafili - Ego-Motion Compensation).

        1. Taret hızlı dönüyorsa (is_turret_moving=True) kameradan doğan yanlış tetiklemeyi önlemek için False döner.
        2. Kamera hareketinden doğan arka plan kaymasını engellemek için iki kare arasındaki küresel
           dönüşüm (Affine matrisi) hesaplanır ve önceki kare güncel kareye hizalanır (warpAffine).
        3. Arka plan kayması kompanze edildikten sonra gerçek bağımsız nesne hareketi absdiff ile hesaplanır.
        """
        if frame is None or is_turret_moving:
            return False

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_blur = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray_blur
            return False

        h, w = gray.shape

        # 1. Aşama: Köşe noktalarını bul ve optik akış ile kameranın kaymasını hesapla
        prev_pts = cv2.goodFeaturesToTrack(self._prev_gray, maxCorners=100, qualityLevel=0.01, minDistance=10)

        aligned_prev = self._prev_gray
        if prev_pts is not None and len(prev_pts) >= 10:
            next_pts, status, _ = cv2.calcOpticalFlowPyrLK(self._prev_gray, gray_blur, prev_pts, None)
            if next_pts is not None and status is not None:
                good_prev = prev_pts[status == 1]
                good_next = next_pts[status == 1]

                if len(good_prev) >= 8:
                    # Kameranın arka plan kayma/dönme matrisini kestir (Ego-motion estimation)
                    M, inliers = cv2.estimateAffinePartial2D(good_prev, good_next)
                    if M is not None:
                        # Önceki kareyi güncel kamera açısına hizala
                        aligned_prev = cv2.warpAffine(self._prev_gray, M, (w, h))

        self._prev_gray = gray_blur

        # 2. Aşama: Arka plan hareketi temizlenmiş kare farkı (Foreground Object Motion)
        diff = cv2.absdiff(aligned_prev, gray_blur)
        thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)

        return int(cv2.countNonZero(thresh)) >= min_motion_pixels

    def check_lidar_and_motion(self, frame: np.ndarray,
                               lidar_distance_m: float | None,
                               is_turret_moving: bool = False) -> bool:
        """LIDAR verisi 10 - 15 metre arasında ise VE (kamera hareketi telafili) görüntüde hareketlilik varsa True döner."""
        if lidar_distance_m is None:
            return False
        if not (self.lidar_min_m <= lidar_distance_m <= self.lidar_max_m):
            return False
        return self.detect_motion(frame, is_turret_moving=is_turret_moving)

    def update_condition(self, num_objects: int, num_balloons: int,
                         frame: np.ndarray | None = None,
                         lidar_distance_m: float | None = None,
                         is_turret_moving: bool = False) -> bool:
        """Küçük hedef tespit (HSV) algoritmasının çalışması için iki şartı günceller ve denetler:

        1. Şart: 30 frame boyunca nesne var ama nesne sayısı ile balon sayısı eşit değilse.
        2. Şart: LIDAR mesafesi 10-15 metre arasında VE kamera hareketi telafili görüntüde hareketlilik varsa.
        """
        # 1. Şart: 30 frame nesne-balon dengesizliği
        if num_objects > 0 and num_objects != num_balloons:
            self.mismatch_frame_count += 1
        else:
            self.mismatch_frame_count = 0

        frame_mismatch_triggered = self.mismatch_frame_count >= self.trigger_frame_threshold

        # 2. Şart: LIDAR 10-15m ve görüntüde hareketlilik (kamera hareketi telafili)
        lidar_motion_triggered = False
        if frame is not None and lidar_distance_m is not None:
            lidar_motion_triggered = self.check_lidar_and_motion(frame, lidar_distance_m, is_turret_moving=is_turret_moving)

        return frame_mismatch_triggered or lidar_motion_triggered

    def reset_condition(self) -> None:
        """Sayaç durumunu ve hareket geçmişini sıfırlar."""
        self.mismatch_frame_count = 0
        self._prev_gray = None

    def should_trigger(self, num_objects: int, num_balloons: int,
                       frame: np.ndarray | None = None,
                       lidar_distance_m: float | None = None,
                       is_turret_moving: bool = False) -> bool:
        """Tetiklenme şartının karşılanıp karşılanmadığını günceller ve döndürür."""
        return self.update_condition(num_objects, num_balloons, frame=frame, lidar_distance_m=lidar_distance_m, is_turret_moving=is_turret_moving)

    # ---------- ortak HSV boru hattı ----------
    def _red_mask(self, frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        m1 = cv2.inRange(hsv, config.HSV_RED_LOWER_1, config.HSV_RED_UPPER_1)
        m2 = cv2.inRange(hsv, config.HSV_RED_LOWER_2, config.HSV_RED_UPPER_2)
        mask = cv2.bitwise_or(m1, m2)
        # morfolojik temizleme
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel)
        return mask

    def _candidates(self, mask: np.ndarray) -> list[tuple[float, float, float]]:
        """Kontur analizi + dairesellik filtresi.
        Dönüş: (cx, cy, yarıçap) listesi."""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < config.MIN_CONTOUR_AREA:
                continue
            perim = cv2.arcLength(cnt, True)
            if perim == 0:
                continue
            circularity = 4 * np.pi * area / (perim * perim)
            if circularity < config.MIN_CIRCULARITY:
                continue
            (cx, cy), r = cv2.minEnclosingCircle(cnt)
            out.append((cx, cy, r))
        return out

    # ---------- küçük hedef tespiti ----------
    def detect(self, frame: np.ndarray,
               num_objects: int | None = None,
               num_balloons: int | None = None,
               lidar_distance_m: float | None = None,
               force: bool = False) -> list[Detection]:
        """HSV tabanlı balon adayları; merkez+yarıçaptan ortak sınır
        kutusu formatına dönüştürülür.

        num_objects ve num_balloons veya lidar_distance_m belirtilirse (ve force=False ise):
        - 30 frame nesne-balon dengesizliği VEYA
        - 10-15m LIDAR mesafesi + görüntüde hareketlilik
        şartlarından en az biri sağlandığında tespit yapılır.
        Parametreler belirtilmezse veya force=True ise doğrudan tespit yapılır.
        """
        if not force and (num_objects is not None and num_balloons is not None or lidar_distance_m is not None):
            n_obj = num_objects if num_objects is not None else 0
            n_bal = num_balloons if num_balloons is not None else 0
            if not self.update_condition(n_obj, n_bal, frame=frame, lidar_distance_m=lidar_distance_m):
                return []

        mask = self._red_mask(frame)
        detections = []
        for cx, cy, r in self._candidates(mask):
            detections.append(
                Detection(cx - r, cy - r, cx + r, cy + r,
                          conf=0.4,                     # sabit ön güven
                          class_id=config.BALLOON_CLASS_ID,
                          source="hsv")
            )
        return detections

    # ---------- dinamik ROI üretimi ----------
    @staticmethod
    def dynamic_roi(det: Detection, frame_shape) -> tuple[int, int, int, int]:
        """Balon piksel boyutuna bağlı, kare sınırlarına kırpılmış ROI.
        Mesafeye bağlı ölçek değişimlerine doğal uyum sağlar."""
        h, w = frame_shape[:2]
        size = max(det.w, det.h) * config.ROI_SCALE_FACTOR
        x1 = int(max(0, det.cx - size / 2))
        y1 = int(max(0, det.cy - size / 2))
        x2 = int(min(w, det.cx + size / 2))
        y2 = int(min(h, det.cy + size / 2))
        return x1, y1, x2, y2

    @staticmethod
    def upper_roi(det: Detection, frame_shape) -> tuple[int, int, int, int]:
        """Balonun üstündeki bölge (maket doğrulaması için)."""
        h, w = frame_shape[:2]
        span = max(det.w, det.h) * config.ROI_SCALE_FACTOR
        x1 = int(max(0, det.cx - span / 2))
        x2 = int(min(w, det.cx + span / 2))
        y2 = int(max(0, det.y1))
        y1 = int(max(0, det.y1 - span))
        return x1, y1, x2, y2

    @staticmethod
    def lower_roi(det: Detection, frame_shape) -> tuple[int, int, int, int]:
        """Nesnenin altındaki bölge (altındaki balonu aramak için)."""
        h, w = frame_shape[:2]
        ratio = config.MATCH_REGION_BASE_RATIO
        if det.h < config.SMALL_TARGET_PX_HEIGHT:
            shrink = (config.SMALL_TARGET_PX_HEIGHT - det.h) / config.SMALL_TARGET_PX_HEIGHT
            ratio += shrink * config.MATCH_REGION_EXTEND_STEP * 4
        depth = det.h * ratio
        x1 = int(max(0, det.x1))
        y1 = int(max(0, det.y2))
        x2 = int(min(w, det.x2))
        y2 = int(min(h, det.y2 + depth))
        return x1, y1, x2, y2

    def detect_in_roi(self, frame: np.ndarray, roi: tuple[int, int, int, int]) -> list[Detection]:
        """Kırpılmış ROI alanında HSV tabanlı balon tespiti."""
        x1, y1, x2, y2 = roi
        if x2 <= x1 or y2 <= y1:
            return []
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return []

        mask = self._red_mask(crop)
        dets = []
        for cx, cy, r in self._candidates(mask):
            abs_cx = x1 + cx
            abs_cy = y1 + cy
            dets.append(
                Detection(abs_cx - r, abs_cy - r, abs_cx + r, abs_cy + r,
                          conf=0.4,
                          class_id=config.BALLOON_CLASS_ID,
                          source="hsv")
            )
        return dets

    def detect_under_object(self, frame: np.ndarray, model: Detection, yolo_detector=None, remap_fn=None) -> list[Detection]:
        """Tespit edilen nesnenin altındaki bölgede balon arama:
        1. Önce nesnenin altındaki ROI'de YOLO modeli ile balon arar.
        2. Model balon bulamazsa, HSV tabanlı renk/kontur çözümünü uygular.
        """
        roi = self.lower_roi(model, frame.shape)
        if yolo_detector is not None:
            roi_dets = yolo_detector.detect_in_roi(frame, roi)
            if remap_fn is not None:
                roi_dets = remap_fn(roi_dets)
            balloons_in_roi = [d for d in roi_dets if d.class_id == config.BALLOON_CLASS_ID]
            if balloons_in_roi:
                return balloons_in_roi

        # Model bulamazsa HSV çözümü ile alt bölgede balon ara
        return self.detect_in_roi(frame, roi)

    # ---------- yedek algılama ----------
    def detect_backup(self, frame: np.ndarray) -> list[Detection]:
        """Sistem performans düşüşünde devreye giren yedek mekanizma:
        aynı HSV boru hattı + boyut/şekil/oran geometrik filtreleri."""
        mask = self._red_mask(frame)
        detections = []
        for cx, cy, r in self._candidates(mask):
            d = 2 * r
            # geometrik filtreler: makul boyut ve en-boy oranı (daire ~1)
            if d < 4 or d > min(frame.shape[:2]) * 0.5:
                continue
            detections.append(
                Detection(cx - r, cy - r, cx + r, cy + r,
                          conf=0.3,
                          class_id=config.BALLOON_CLASS_ID,
                          source="hsv")
            )
        return detections
