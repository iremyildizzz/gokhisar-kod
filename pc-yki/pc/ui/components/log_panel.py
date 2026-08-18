"""
Log Paneli Bileşeni

Sistem mesajlarını, hataları ve olayları gösteren konsol benzeri panel.
Debug ve operatör bilgilendirmesi için kullanılır.

Görev Odaklı Mesaj Tipleri:
- HEDEF: Hedef tespit edildi/kaybedildi
- MENZİL: Menzil içi/dışı durumu
- DOST: Dost unsur tespiti
- DÜŞMAN: Düşman unsur tespiti
- ANGAŽMAN: Angajman durumu
"""

from datetime import datetime

from PySide6.QtWidgets import QFrame, QVBoxLayout, QTextEdit, QLabel, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QTextCursor

from pc.ui.styles import Styles


class LogPanel(QFrame):
    """
    Log/konsol paneli
    
    Özellikler:
    - Zaman damgalı log mesajları
    - Farklı log seviyeleri (INFO, WARNING, ERROR)
    - Otomatik scroll
    - Temizle butonu
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_lines = 500  # Maksimum log satırı
        self._setup_ui()
    
    def _setup_ui(self):
        """UI oluştur"""
        self.setStyleSheet(Styles.PANEL)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 5, 8, 5)
        
        # Başlık ve kontroller
        header_layout = QHBoxLayout()
        
        title = QLabel("GÖREV LOG")
        title.setStyleSheet(Styles.SUBTITLE_LABEL)
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        self.btn_clear = QPushButton("Temizle")
        self.btn_clear.setStyleSheet(Styles.BUTTON_NORMAL)
        self.btn_clear.setMinimumWidth(120)
        header_layout.addWidget(self.btn_clear)
        
        layout.addLayout(header_layout)
        
        # Log alanı
        self.log_text = QTextEdit()
        self.log_text.setStyleSheet(Styles.LOG_AREA)
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(50)
        layout.addWidget(self.log_text)
        
        # Signal bağlantıları
        self.btn_clear.clicked.connect(self.clear)
    
    def _get_timestamp(self) -> str:
        """Zaman damgası döndür"""
        return datetime.now().strftime("%H:%M:%S")
    
    def _append_log(self, message: str, color: str = "#00ff00"):
        """Log mesajı ekle"""
        timestamp = self._get_timestamp()
        html = f'<span style="color: #888;">[{timestamp}]</span> <span style="color: {color};">{message}</span><br>'
        
        # Cursor'u sona taşı
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)
        
        # HTML ekle
        self.log_text.insertHtml(html)
        
        # Otomatik scroll
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        # Satır limiti kontrolü
        self._trim_lines()
    
    def _trim_lines(self):
        """Fazla satırları sil"""
        text = self.log_text.toPlainText()
        lines = text.split('\n')
        
        if len(lines) > self._max_lines:
            # Son N satırı tut
            self.log_text.clear()
            # Not: Bu basit implementasyon, production'da optimize edilmeli
    
    @Slot(str)
    def log_info(self, message: str):
        """Bilgi mesajı logla"""
        self._append_log(f"[INFO] {message}", "#00ff00")
    
    @Slot(str)
    def log_warning(self, message: str):
        """Uyarı mesajı logla"""
        self._append_log(f"[UYARI] {message}", "#ffff00")
    
    @Slot(str)
    def log_error(self, message: str):
        """Hata mesajı logla"""
        self._append_log(f"[HATA] {message}", "#ff0000")
    
    @Slot(str)
    def log_status(self, message: str):
        """Durum mesajı logla (worker'lardan)"""
        self._append_log(message, "#00aaff")
    
    @Slot()
    def clear(self):
        """Log'u temizle"""
        self.log_text.clear()
        self.log_info("Log temizlendi")
    
    # ==================== GÖREV ODAKLI MESAJLAR ====================
    
    @Slot(str, int)
    def log_target_detected(self, target_id: str, bearing: int = 0):
        """
        Hedef tespit edildi mesajı
        
        Args:
            target_id: Hedef tanımlayıcısı ("T-001", "UAV-12" vb.)
            bearing: Hedefin yönü (derece)
        """
        self._append_log(
            f"🎯 [HEDEF] Hedef Tespit Edildi: {target_id} | Yön: {bearing}°",
            "#ff6600"  # Turuncu
        )
    
    @Slot(str)
    def log_target_lost(self, target_id: str):
        """Hedef kaybedildi mesajı"""
        self._append_log(
            f"❌ [HEDEF] Hedef Kaybedildi: {target_id}",
            "#ff6600"
        )
    
    @Slot(str, float)
    def log_in_range(self, target_id: str, distance: float):
        """
        Hedef menzil içinde mesajı
        
        Args:
            target_id: Hedef tanımlayıcısı
            distance: Mesafe (metre)
        """
        self._append_log(
            f"✅ [MENZİL] {target_id} Menzil İçinde | Mesafe: {distance:.0f}m",
            "#00ff00"  # Yeşil
        )
    
    @Slot(str, float)
    def log_out_of_range(self, target_id: str, distance: float):
        """Hedef menzil dışında mesajı"""
        self._append_log(
            f"⚠️ [MENZİL] {target_id} Menzil Dışında | Mesafe: {distance:.0f}m",
            "#ffff00"  # Sarı
        )
    
    @Slot(str, str)
    def log_friendly(self, target_id: str, unit_type: str = ""):
        """
        Dost unsur tespit edildi mesajı
        
        Args:
            target_id: Hedef tanımlayıcısı
            unit_type: Birim tipi ("F-16", "Bayraktar" vb.)
        """
        unit_info = f" ({unit_type})" if unit_type else ""
        self._append_log(
            f"🟢 [DOST] Dost Unsur Belirlendi: {target_id}{unit_info}",
            "#00aaff"  # Mavi
        )
    
    @Slot(str, str)
    def log_hostile(self, target_id: str, threat_type: str = ""):
        """
        Düşman unsur tespit edildi mesajı
        
        Args:
            target_id: Hedef tanımlayıcısı
            threat_type: Tehdit tipi ("UAV", "Cruise Missile" vb.)
        """
        threat_info = f" ({threat_type})" if threat_type else ""
        self._append_log(
            f"🔴 [DÜŞMAN] Düşman Unsur Tespit Edildi: {target_id}{threat_info}",
            "#ff0000"  # Kırmızı
        )
    
    @Slot(str)
    def log_engagement_start(self, target_id: str):
        """Angajman başladı mesajı"""
        self._append_log(
            f"💥 [ANGAŽMAN] Angajman Başlatıldı: {target_id}",
            "#ff00ff"  # Magenta
        )
    
    @Slot(str, bool)
    def log_engagement_result(self, target_id: str, success: bool):
        """Angajman sonucu mesajı"""
        if success:
            self._append_log(
                f"✅ [ANGAŽMAN] Hedef İmha Edildi: {target_id}",
                "#00ff00"
            )
        else:
            self._append_log(
                f"❌ [ANGAŽMAN] Angajman Başarısız: {target_id}",
                "#ff0000"
            )
    
    @Slot(int, str)
    def log_track_update(self, track_count: int, status: str = ""):
        """
        Radar/track güncellemesi mesajı
        
        Args:
            track_count: Toplam takip edilen hedef sayısı
            status: Ek durum bilgisi
        """
        status_info = f" | {status}" if status else ""
        self._append_log(
            f"📡 [RADAR] Aktif Track: {track_count}{status_info}",
            "#aaaaaa"  # Gri
        )
