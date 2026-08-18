"""
Hedef Simülasyonu Worker

Test amaçlı rastgele hedefler üretir ve log paneline görev odaklı mesajlar gönderir.
Gerçek sistemde bu veriler radar/sensörlerden gelecektir.

Kullanım:
    simulator = TargetSimulator()
    simulator.target_detected.connect(log_panel.log_target_detected)
    simulator.start_worker()
"""

import random
from typing import List, Dict
from PySide6.QtCore import Signal, QTimer

from pc.ui.workers.base_worker import BaseWorker


# Menzil bantları (metre)
RANGE_CLOSE = 5       # 5m - Yakın menzil (angajman öncelikli)
RANGE_MEDIUM = 10     # 10m - Orta menzil (takipte)
RANGE_FAR = 15        # 15m - Uzak menzil (izleniyor)
RANGE_MAX = 20        # 20m - Maksimum algılama


class Target:
    """Simüle edilmiş hedef"""
    
    def __init__(self, target_id: str, is_hostile: bool = True, distance: int = None):
        self.id = target_id
        self.is_hostile = is_hostile
        self.bearing = random.randint(0, 359)
        # 5m, 10m, 15m bantlarından birinde başlat
        if distance is None:
            self.distance = random.choice([
                random.randint(3, 5),      # Yakın menzil (3-5m)
                random.randint(7, 10),     # Orta menzil (7-10m)
                random.randint(12, 15),    # Uzak menzil (12-15m)
            ])
        else:
            self.distance = distance
        self.altitude = random.randint(100, 5000)  # metre
        self.speed = random.randint(50, 500)  # km/h
        self.threat_type = random.choice(["UAV", "Cruise Missile", "Aircraft", "Drone", "Helicopter"])
        self.friendly_type = random.choice(["F-16", "Bayraktar TB2", "ATAK", "Kaan", "Hürkuş"])
        self.tracked = True
    
    @property
    def range_band(self) -> str:
        """Hangi menzil bandında olduğunu döndür"""
        if self.distance <= RANGE_CLOSE:
            return "YAKIN"
        elif self.distance <= RANGE_MEDIUM:
            return "ORTA"
        elif self.distance <= RANGE_FAR:
            return "UZAK"
        else:
            return "DIŞI"
    
    @property
    def in_engagement_range(self) -> bool:
        """Angajman menzilinde mi?"""
        return self.distance <= RANGE_CLOSE


class TargetSimulator(BaseWorker):
    """
    Hedef simülasyonu worker'ı
    
    Signals:
    - target_detected(str, int): Hedef tespit edildi (id, bearing)
    - target_lost(str): Hedef kaybedildi
    - in_range(str, float): Menzil içine girdi
    - out_of_range(str, float): Menzil dışına çıktı
    - friendly_detected(str, str): Dost unsur (id, type)
    - hostile_detected(str, str): Düşman unsur (id, type)
    - engagement_started(str): Angajman başladı
    - engagement_result(str, bool): Angajman sonucu
    - track_update(int, str): Track sayısı güncellendi
    """
    
    # Görev odaklı signal'lar
    target_detected = Signal(str, int)      # target_id, bearing
    target_lost = Signal(str)               # target_id
    in_range = Signal(str, float)           # target_id, distance
    out_of_range = Signal(str, float)       # target_id, distance
    friendly_detected = Signal(str, str)    # target_id, unit_type
    hostile_detected = Signal(str, str)     # target_id, threat_type
    engagement_started = Signal(str)        # target_id
    engagement_result = Signal(str, bool)   # target_id, success
    track_update = Signal(int, str)         # count, status
    
    # Menzil güncellemesi (UI status panel için)
    distance_updated = Signal(float)        # en yakın hedefin mesafesi (metre)
    closest_target_changed = Signal(str, float)  # target_id, distance
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._targets: Dict[str, Target] = {}
        self._target_counter = 0
        self._timer = None
        self._event_interval = 3000  # 3 saniyede bir event (ms)
    
    def run(self):
        """Simülasyon döngüsü"""
        self.emit_status("Hedef simülasyonu başlatıldı")
        
        # İlk hedefleri oluştur (5m, 10m, 15m'de birer tane)
        self._generate_initial_targets()
        
        while self.is_running:
            # Rastgele event üret
            self._generate_random_event()
            
            # Track güncellemesi
            hostile_count = sum(1 for t in self._targets.values() if t.is_hostile)
            friendly_count = len(self._targets) - hostile_count
            self.track_update.emit(
                len(self._targets), 
                f"Düşman: {hostile_count} | Dost: {friendly_count}"
            )
            
            # En yakın hedefin mesafesini gönder
            self._emit_closest_target_distance()
            
            # Bekleme
            self.msleep(self._event_interval)
    
    def _emit_closest_target_distance(self):
        """En yakın düşman hedefin mesafesini gönder"""
        hostile_targets = [t for t in self._targets.values() if t.is_hostile]
        if hostile_targets:
            closest = min(hostile_targets, key=lambda t: t.distance)
            self.distance_updated.emit(float(closest.distance))
            self.closest_target_changed.emit(closest.id, float(closest.distance))
    
    def _generate_initial_targets(self):
        """Başlangıç hedeflerini oluştur - her menzil bandında birer tane"""
        # 5m menzilinde düşman
        self._create_new_target(is_hostile=True, distance=4)
        
        # 10m menzilinde düşman
        self._create_new_target(is_hostile=True, distance=9)
        
        # 15m menzilinde dost
        self._create_new_target(is_hostile=False, distance=14)
    
    def _create_new_target(self, is_hostile: bool = None, distance: int = None) -> Target:
        """Yeni hedef oluştur"""
        self._target_counter += 1
        
        if is_hostile is None:
            is_hostile = random.random() > 0.3  # %70 düşman
        
        prefix = "T" if is_hostile else "F"
        target_id = f"{prefix}-{self._target_counter:03d}"
        
        target = Target(target_id, is_hostile, distance)
        self._targets[target_id] = target
        
        # Tespit mesajı
        self.target_detected.emit(target_id, target.bearing)
        
        # Dost/düşman tanımlama
        if is_hostile:
            self.hostile_detected.emit(target_id, target.threat_type)
        else:
            self.friendly_detected.emit(target_id, target.friendly_type)
        
        # Menzil durumu (5m, 10m, 15m bantlarına göre)
        if target.distance <= RANGE_CLOSE:
            self.in_range.emit(target_id, float(target.distance))
        elif target.distance <= RANGE_FAR:
            self.out_of_range.emit(target_id, float(target.distance))
        
        return target
    
    def _generate_random_event(self):
        """Rastgele olay üret"""
        event_type = random.choice([
            "new_target",
            "target_lost", 
            "range_change",
            "engagement",
            "nothing"
        ])
        
        if event_type == "new_target":
            self._create_new_target()
            
        elif event_type == "target_lost" and self._targets:
            # Rastgele bir hedefi kaybet
            target_id = random.choice(list(self._targets.keys()))
            del self._targets[target_id]
            self.target_lost.emit(target_id)
            
        elif event_type == "range_change" and self._targets:
            # Rastgele bir hedefin menzil durumunu değiştir
            target_id = random.choice(list(self._targets.keys()))
            target = self._targets[target_id]
            
            old_band = target.range_band
            
            # Mesafeyi değiştir (yaklaş veya uzaklaş)
            direction = random.choice([-1, 1])  # -1: yaklaş, 1: uzaklaş
            change = random.randint(1, 3) * direction
            target.distance += change
            target.distance = max(1, min(RANGE_MAX, target.distance))
            
            new_band = target.range_band
            
            # Menzil bandı değiştiyse bildir
            if old_band != new_band:
                if target.distance <= RANGE_CLOSE:
                    self.in_range.emit(target_id, float(target.distance))
                else:
                    self.out_of_range.emit(target_id, float(target.distance))
                
        elif event_type == "engagement" and self._targets:
            # Düşman hedeflerden birine angajman (sadece 5km içindekiler)
            hostile_targets = [t for t in self._targets.values() if t.is_hostile and t.in_engagement_range]
            
            if hostile_targets:
                target = random.choice(hostile_targets)
                self.engagement_started.emit(target.id)
                
                # 1 saniye sonra sonuç
                self.msleep(1000)
                
                success = random.random() > 0.3  # %70 başarı
                self.engagement_result.emit(target.id, success)
                
                if success:
                    # Hedefi kaldır
                    del self._targets[target.id]
    
    def set_event_interval(self, interval_ms: int):
        """Event aralığını ayarla"""
        self._event_interval = max(1000, interval_ms)
    
    def get_active_targets(self) -> Dict[str, Target]:
        """Aktif hedefleri döndür"""
        return self._targets.copy()
