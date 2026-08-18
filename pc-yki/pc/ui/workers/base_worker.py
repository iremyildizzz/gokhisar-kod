"""
Base Worker Sınıfı - QThread tabanlı arka plan işlemleri

Neden Worker Thread?
- PySide6'da UI thread'i (ana thread) kullanıcı etkileşimlerini yönetir
- Ağ işlemleri, görüntü işleme gibi uzun süren işlemler UI'ı dondurur
- Worker thread'ler bu işlemleri arka planda yaparak UI'ın akıcı kalmasını sağlar

Signals & Slots Mekanizması:
- Signal: Worker'dan UI'a veri göndermek için (thread-safe)
- Slot: UI'dan gelen olayları işlemek için
- Qt'nin event loop'u üzerinden çalışır, thread-safe iletişim sağlar
"""

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker


class BaseWorker(QThread):
    """
    Tüm worker'ların türediği temel sınıf
    
    Kullanım:
    1. Bu sınıftan miras al
    2. run() metodunu override et
    3. Gerekli signal'ları tanımla
    4. UI'da signal'ları slot'lara bağla
    """
    
    # Ortak signal'lar - tüm worker'lar bunları kullanabilir
    error_occurred = Signal(str)      # Hata mesajı
    status_changed = Signal(str)      # Durum güncellemesi
    progress_updated = Signal(int)    # İlerleme yüzdesi (0-100)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Thread güvenliği için mutex
        self._mutex = QMutex()
        
        # Çalışma durumu flag'i
        self._is_running = False
        
        # Worker ismi (debug için)
        self._worker_name = self.__class__.__name__
    
    @property
    def is_running(self) -> bool:
        """Thread-safe çalışma durumu kontrolü"""
        with QMutexLocker(self._mutex):
            return self._is_running
    
    def start_worker(self):
        """Worker'ı güvenli şekilde başlat"""
        with QMutexLocker(self._mutex):
            self._is_running = True
        self.start()  # QThread.start()
        self.status_changed.emit(f"{self._worker_name} başlatıldı")
    
    def stop_worker(self):
        """
        Worker'ı güvenli şekilde durdur
        
        Neden bu yöntem?
        - terminate() kullanmak tehlikeli (kaynak sızıntısı)
        - Flag ile döngüyü kırıp düzgün kapanış sağlıyoruz
        """
        with QMutexLocker(self._mutex):
            self._is_running = False
        
        # Thread'in bitmesini bekle (max 3 saniye)
        if not self.wait(3000):
            self.status_changed.emit(f"UYARI: {self._worker_name} zorla sonlandırıldı")
            self.terminate()
        else:
            self.status_changed.emit(f"{self._worker_name} durduruldu")
    
    def run(self):
        """
        Alt sınıflar bu metodu override etmeli
        
        Örnek kullanım:
        while self.is_running:
            # İşlemleri yap
            pass
        """
        raise NotImplementedError("Alt sınıf run() metodunu implement etmeli")
    
    def emit_error(self, message: str):
        """Hata signal'ı gönder"""
        self.error_occurred.emit(f"[{self._worker_name}] HATA: {message}")
    
    def emit_status(self, message: str):
        """Durum signal'ı gönder"""
        self.status_changed.emit(f"[{self._worker_name}] {message}")
