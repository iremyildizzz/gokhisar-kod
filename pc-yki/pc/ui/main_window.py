"""
Ana Pencere (MainWindow) - GÖKHİSAR Yer Kontrol İstasyonu

Tüm UI bileşenlerini bir araya getiren ana pencere.
Worker thread'ler burada başlatılır ve signal/slot bağlantıları yapılır.

Mimari:
- Sol: Video görüntüleme
- Sağ üst: Durum paneli
- Sağ alt: Kontrol paneli
- Alt: Log paneli

Neden bu yapı?
- Operatör tek ekrandan tüm sistemi görebilir
- Kritik kontroller sağ tarafta (sağ el kullanımı için optimize)
- Log paneli detaylı bilgi için alt kısımda
"""

import time
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QStatusBar, QSizePolicy, QStackedWidget, QLabel, QPushButton, QFrame,
    QDoubleSpinBox
)
from PySide6.QtCore import Qt, Slot, QTimer, QEvent
from PySide6.QtGui import QGuiApplication  # Boyutlandırma görevi için eklendi

from shared import protocol

from pc.ui.utils.config import UIConfig, SystemConfig
from pc.ui.styles import Styles
from pc.ui.components.video_display import VideoDisplay
from pc.ui.components.status_panel import StatusPanel
from pc.ui.components.control_panel import ControlPanel
from pc.ui.components.log_panel import LogPanel
from pc.ui.workers.gstreamer_video_worker import GStreamerVideoWorker
from pc.ui.workers.target_simulator import TargetSimulator

# Entegrasyon katmanı: görüntü işleme boru hattı ve RPi komut kanalı.
from pc.integration import bootstrap  # noqa: F401  (sys.path: import config)
from pc.integration.settings import Settings
from pc.integration.system_state import SystemState, SystemStateMachine
from pc.ui.workers.rpi_link_worker import RpiLinkWorker
from pc.ui.workers.vision_worker import VisionWorker

import config

# Servo kaydırıcıları ile RPi'nin açı uzayı arasındaki dönüşüm.
# Kaydırıcılar merkezi 0 kabul eder (Azimut ±180°, Elevasyon ±90°);
# `PanTiltController` ise 0–180° arası çalışır ve 90° merkezdedir.
# Azimut ikiye bölünür ki kaydırıcının tamamı kullanılabilsin.
# Başlangıç: Elevation UI -10° → tilt 80°.
_PAN_CENTER = 90.0
_TILT_CENTER = 90.0
_AZIMUTH_SCALE = 0.5
_ELEVATION_HOME_UI = int(getattr(config, "SERVO_ELEVATION_HOME_UI", -10))
_PAN_HOME_UI = 0

# KTR 4.3.2: gösterge adımı. Azimut ×0.5 → gerçek pan ≈ 1°.
# Shift = 1 birim (daha ince).
_KEYBOARD_STEP = 2
# Ok tuşları ve WASD birlikte destekleniyor: operatörün eli hangi taraftaysa
# oradan sürebilsin. Değerler (dx, dy) gösterge adımıdır; ekranda yukarı,
# tilt göstergesinde artı yön kabul edildi.
_KEYBOARD_BINDINGS = {
    Qt.Key_Left:  (-1, 0),
    Qt.Key_A:     (-1, 0),
    Qt.Key_Right: (1, 0),
    Qt.Key_D:     (1, 0),
    Qt.Key_Up:    (0, 1),
    Qt.Key_W:     (0, 1),
    Qt.Key_Down:  (0, -1),
    Qt.Key_S:     (0, -1),
}


class MainWindow(QMainWindow):
    """
    GÖKHİSAR Ana Pencere
    
    Sorumluluklar:
    1. UI bileşenlerini oluştur ve yerleştir
    2. Worker thread'leri başlat/durdur
    3. Signal/Slot bağlantılarını yönet
    4. Kullanıcı etkileşimlerini işle
    """
    
    def __init__(self):
        super().__init__()
        
        # Ayarlar: görüntü işleme reposundaki pc/config.py, arayüzün
        # pc/ui/utils/config.py'si ve ortam değişkenleri tek yerde birleştirilir.
        self._settings = Settings.load()

        # Worker referansları
        # Not: _udp_worker artık GStreamer tabanlı (RTP/JPEG depay yapıyor).
        # Aynı sinyal sözleşmesini koruduğumuz için tip Union halinde tutulabilir.
        self._udp_worker: Optional[GStreamerVideoWorker] = None
        self._rpi_worker: Optional[RpiLinkWorker] = None
        self._vision_worker: Optional[VisionWorker] = None
        self._target_simulator: Optional[TargetSimulator] = None

        # Sistem durumu
        self._current_mode = "MANUEL"
        self._system = SystemStateMachine()
        # Angajmanın hangi ize yapıldığını bilmeliyiz: imha değerlendirmesi
        # ve tekrar-ateş kısıtlaması iz kimliği üzerinden yürüyor.
        self._engaged_track_id: Optional[int] = None
        self._last_engage_time = 0.0
        # Angajman adayının en son görünümü. Manuel ATEŞ düğmesi hangi hedefe
        # talep göndereceğini buradan öğrenir.
        self._candidate = None
        self._last_target_info: tuple | None = None
        self._reported_track_ids: set[int] = set()
        # STM32'nin "ateşlendi" bayrağı periyodik telemetride seviye olarak
        # gelir (her 200 ms'de aynı bayrak). Kenar yakalamazsak tek ateşleme
        # onlarca log satırı ve tekrar tekrar imha sayacı sıfırlaması üretir.
        self._last_fired_flag = False
        self._last_failsafe_flag = False
        # STM açılış failsafe'ini yok say; aksi halde ilk telemetride
        # GÜVENLİ DURUŞ + clear_pending manuel'i öldürüyor.
        self._stm_ever_healthy = False
        self._last_range_reason: str | None = None
        # Klavye komutundan hemen sonra gelen telemetrinin göstergeyi geri
        # zıplatmasını engellemek için son manuel komut zamanı.
        self._last_manual_command_t = 0.0

        # UI oluştur
        self._setup_window()
        self._setup_ui()
        self._setup_status_bar()
        self._setup_connections()

        # Klavye ile yönelim, odak hangi alt widget'ta olursa olsun çalışmalı;
        # operatör az önce bir düğmeye tıkladıysa ok tuşları o düğmede
        # kaybolmasın. Uygulama seviyesindeki süzgeç bunu sağlıyor.
        app = QGuiApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
        # Başlangıç log'u
        self._log_system_info()
    
    def _setup_window(self):
        """Pencere ayarlarını yapılandır"""
        self.setWindowTitle(UIConfig.WINDOW_TITLE)
        
        # --- BOYUTLANDIRMA GÖREVE ENTEGRASYONU ---
        # Ekranın kullanılabilir yüksekliğini alıp dikeyde kilitliyoruz
        screen = QGuiApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        full_height = screen_geometry.height()
        
        # Genişliği (target_w) 1150px olarak ayarlıyoruz
        target_w = 1150
        
        # Pencereyi ekranın tam boyuna (üstten alta) ayarla
        self.resize(target_w, full_height)
        
        # Minimum Değerler: Daha fazla küçülüp butonların ezilmesini engeller
        self.setMinimumWidth(target_w)      
        self.setMinimumHeight(full_height)  # Dikeyde ekran boyundan küçük olamaz (Kilitli)
        
        # Ekranın ortasına konumlandır
        self.move(screen_geometry.x() + (screen_geometry.width() - target_w) // 2, screen_geometry.y())
        # ------------------------------------------

        self.setStyleSheet(Styles.MAIN_WINDOW)
        
        # Başlangıçta maximize edilmiş pencere (tam ekran değil, pencere başlığı görünür)
        # F11 ile gerçek tam ekrana geçilebilir
        self.showMaximized()
    
    def _setup_ui(self):
        # QStackedWidget: 0=splash, 1=ana arayüz
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Splash sayfası (index 0)
        self._stack.addWidget(self._build_splash())

        # Ana arayüz sayfası (index 1)
        central_widget = QWidget()
        self._stack.addWidget(central_widget)
        self._stack.setCurrentIndex(0)

        # Ana layout: dikey — üst satır (Sol|Orta|Sağ) + alt log
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ── Üst satır: Sol=Durum | Orta=Video | Sağ=Kontrol ──────────
        top_row = QHBoxLayout()
        top_row.setSpacing(4)

        # Sol: Sistem Durumu
        left_widget = QWidget()
        left_widget.setFixedWidth(280)
        left_widget.setStyleSheet("background:transparent;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 0, 0, 0)
        left_layout.setSpacing(4)
        self.status_panel = StatusPanel()
        self.status_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        left_layout.addWidget(self.status_panel)
        left_layout.addStretch(1)
        top_row.addWidget(left_widget, stretch=0)

        # Orta: Video
        self.video_display = VideoDisplay()
        self.video_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        top_row.addWidget(self.video_display, stretch=1)

        # Sağ: Kontrol Paneli
        right_widget = QWidget()
        right_widget.setFixedWidth(310)
        right_widget.setStyleSheet("background:transparent;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 4, 0)
        right_layout.setSpacing(4)
        self.control_panel = ControlPanel()
        self.control_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        right_layout.addWidget(self.control_panel)
        right_layout.addStretch(1)
        top_row.addWidget(right_widget, stretch=0)

        main_layout.addLayout(top_row, stretch=1)

        # ── Alt: Log paneli tam genişlik ──────────────────────────────
        self.log_panel = LogPanel()
        self.log_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.log_panel.setFixedHeight(130)
        main_layout.addWidget(self.log_panel, stretch=0)


    def _build_splash(self) -> QWidget:
        """Başlangıç / splash sayfası"""
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        L = QVBoxLayout(page)
        L.setAlignment(Qt.AlignCenter)
        L.setSpacing(0)
        L.setContentsMargins(40, 40, 40, 40)

        L.addStretch(2)

        icon = QLabel("✈")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size:64px; color:#00d4ff; background:transparent;")
        L.addWidget(icon)
        L.addSpacing(16)

        title = QLabel("GÖKHİSAR")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:56px; font-weight:800; color:#ffffff; background:transparent;")
        L.addWidget(title)
        L.addSpacing(8)

        subtitle = QLabel("YER KONTROL İSTASYONU")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size:18px; font-weight:600; color:#00d4ff; background:transparent;")
        L.addWidget(subtitle)
        L.addSpacing(8)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background:rgba(0,212,255,0.35); border:none; max-height:1px;")
        L.addWidget(line)
        L.addSpacing(8)

        version = QLabel("NT1 Hava Savunma Platformu  •  v1.0")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("font-size:12px; color:#6b7280; background:transparent;")
        L.addWidget(version)

        L.addStretch(2)

        self._splash_status = QLabel("Sistem başlatılmaya hazır...")
        self._splash_status.setAlignment(Qt.AlignCenter)
        self._splash_status.setStyleSheet("font-size:13px; color:#9aa4b2; background:transparent;")
        L.addWidget(self._splash_status)
        L.addSpacing(16)

        btn = QPushButton("  BAŞLAT  ")
        btn.setFixedHeight(52)
        btn.setFixedWidth(200)
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0,212,255,0.15);
                color: #e6eaf2;
                font-size:16px; font-weight:700;
                border: 2px solid rgba(0,212,255,0.5);
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: rgba(0,212,255,0.3);
                border-color: #00d4ff;
            }
            QPushButton:pressed { background-color: rgba(0,212,255,0.45); }
        """)
        btn.clicked.connect(self._launch_main_ui)

        wrap = QHBoxLayout()
        wrap.addStretch(); wrap.addWidget(btn); wrap.addStretch()
        L.addLayout(wrap)

        L.addStretch(1)

        footer = QLabel("Enter veya Space ile de başlatabilirsiniz")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("font-size:11px; color:#374151; background:transparent;")
        L.addWidget(footer)
        L.addSpacing(20)

        # Animasyon timer
        self._dot_count = 0
        from PySide6.QtCore import QTimer
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._animate_splash)
        self._dot_timer.start(600)

        return page

    def _animate_splash(self):
        self._dot_count = (self._dot_count + 1) % 4
        self._splash_status.setText("Sistem başlatılmaya hazır" + "." * self._dot_count)

    def _launch_main_ui(self):
        """Splash'ten ana arayüze geç"""
        self._dot_timer.stop()
        self._splash_status.setText("Başlatılıyor...")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._stack.setCurrentIndex(1))

    def _setup_status_bar(self):
        """Durum çubuğunu ayarla"""
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(Styles.STATUS_BAR)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("⚡ Sistem hazır - Bağlantı bekleniyor...")
    
    def _setup_connections(self):
        """
        Signal/Slot bağlantılarını kur
        """
        # Kontrol paneli signal'ları
        self.control_panel.mode_changed.connect(self._on_mode_changed)
        self.control_panel.fire_command.connect(self._on_fire_command)
        self.control_panel.servo_command.connect(self._on_servo_command)
        self.control_panel.pid_command.connect(self._on_pid_command)
        self.control_panel.system_start.connect(self._on_system_start)
        self.control_panel.system_stop.connect(self._on_system_stop)
        self.control_panel.system_reset.connect(self._on_system_reset) # SERVO KONTROLÜ BURADA

        # Video bileşeninin decode/işleme hatalarını log paneline yönlendir.
        # Önceden 'print' ile terminale yazılıyordu; arayüzden görülmüyordu.
        self.video_display.error_occurred.connect(self.log_panel.log_error)
    
    def _log_system_info(self):
        """Sistem bilgilerini logla"""
        info = SystemConfig.get_platform_info()
        self.log_panel.log_info(f"Platform: {info['platform']}")
        self.log_panel.log_info(f"Python: {info['python_version'].split()[0]}")
        self.log_panel.log_info(f"Seri Port: {info['serial_port']}")
        for line in self._settings.summary():
            self.log_panel.log_info(line)
        if self._settings.pipeline.weights_path is None:
            self.log_panel.log_error(
                "YOLO ağırlık dosyası bulunamadı. models/best.pt koyun veya "
                "GOKHISAR_YOLO_WEIGHTS ortam değişkenini ayarlayın."
            )
        self.log_panel.log_info(
            "MANUEL kip yönelimi: ok tuşları veya WASD "
            f"({_KEYBOARD_STEP}° adım, Shift ile ince ayar)"
        )
        self.log_panel.log_info("GÖKHİSAR Yer Kontrol İstasyonu başlatıldı")
    
    # ==================== WORKER YÖNETİMİ ====================
    
    def start_udp_worker(self, port: int = None):
        """
        Video akışı worker'ını başlat.

        İçeride RTP/JPEG depay işini GStreamer'a yaptıran bir subprocess
        worker (GStreamerVideoWorker) kullanılır; UI tarafından bakıldığında
        eski UDPVideoWorker ile aynı sinyallere sahip olduğu için bu detay
        şeffaftır (Liskov Substitution).

        Aynı zamanda VisionWorker'ı da başlatır ve frame_received sinyalini
        hem VideoDisplay'e hem de boru hattına "fan-out" eder. Böylece ekrana
        ham görüntü gösterilirken paralel olarak tespit → doğrulama → takip →
        IFF zinciri çalışır.
        """
        if self._udp_worker and self._udp_worker.isRunning():
            self.log_panel.log_warning("Video Worker zaten çalışıyor")
            return

        port = port or self._settings.video.udp_port

        # 1) Boru hattını önce başlat ki ilk kare geldiğinde hazır olsun.
        self._start_vision_worker()

        # 2) GStreamer video worker'ı kur
        self._udp_worker = GStreamerVideoWorker(port=port)
        self._udp_worker.frame_received.connect(self.video_display.update_frame_from_bytes)
        # Kareyi boru hattına da yolla. submit_frame kendi mutex'iyle korunuyor,
        # bu yüzden Qt'nin AutoConnection'ı hangi yolu seçerse seçsin güvenli.
        if self._vision_worker is not None:
            self._udp_worker.frame_received.connect(self._vision_worker.submit_frame)

        self._udp_worker.connection_status.connect(self.status_panel.set_udp_status)
        self._udp_worker.status_changed.connect(self.log_panel.log_status)
        self._udp_worker.error_occurred.connect(self.log_panel.log_error)
        self._udp_worker.start_worker()
        self.log_panel.log_info(f"GStreamer Video Worker başlatıldı (UDP {port})")

    def _start_vision_worker(self):
        """
        Görüntü işleme boru hattını idempotent olarak başlat.

        Idempotent = "birden fazla çağrılırsa zarar vermez". F5 ile bağlantı
        yenilenirken yeniden çağrılır; modeli her seferinde yeniden yüklemek
        israf olur, bu yüzden çalışan worker varsa korur.
        """
        if self._vision_worker and self._vision_worker.isRunning():
            return
        self._vision_worker = VisionWorker(
            self._settings.pipeline, stage=self._stage_from_mode(self._current_mode)
        )
        self._vision_worker.detections_ready.connect(self.video_display.set_detections)
        self._vision_worker.result_ready.connect(self._on_pipeline_result)
        self._vision_worker.status_changed.connect(self.log_panel.log_status)
        self._vision_worker.error_occurred.connect(self.log_panel.log_error)
        self._vision_worker.pipeline_loaded.connect(self._on_model_loaded)
        self._vision_worker.start_worker()
        self.log_panel.log_info("Görüntü işleme boru hattı başlatıldı")

    def stop_vision_worker(self):
        if self._vision_worker:
            self._vision_worker.stop_worker()
            self._vision_worker = None
            self.log_panel.log_info("Görüntü işleme boru hattı durduruldu")

    @Slot(bool, str)
    def _on_model_loaded(self, ok: bool, info: str):
        if ok:
            self.log_panel.log_info(f"Model: {info}")
            self.status_bar.showMessage(f"Model: {info}")
        else:
            self.log_panel.log_error(f"Model: {info}")
            self.status_bar.showMessage("Model yüklenemedi")
            # Model olmadan otonom angajman yapılamaz; sistemi güvenli duruşa
            # alıp operatörü bilgilendiriyoruz. Sessizce manuel moda düşmek
            # "sistem çalışıyor" izlenimi verirdi.
            self._enter_fail_safe("Model yüklenemedi")
    
    def stop_udp_worker(self):
        if self._udp_worker:
            self._udp_worker.stop_worker()
            self._udp_worker = None
            self.log_panel.log_info("Video Worker durduruldu")
    
    def start_tcp_worker(self, host: str = None, port: int = None):
        """RPi komut kanalını başlat (satır tabanlı JSON, `pc/vision/comms/rpi_link`)."""
        if self._rpi_worker and self._rpi_worker.isRunning():
            self.log_panel.log_warning("RPi komut kanalı zaten çalışıyor")
            return

        settings = self._settings.rpi
        if host or port:
            from dataclasses import replace
            settings = replace(settings, host=host or settings.host,
                               port=port or settings.port)

        self._rpi_worker = RpiLinkWorker(settings)
        self._rpi_worker.connection_changed.connect(self.status_panel.set_tcp_status)
        self._rpi_worker.connection_changed.connect(self._on_rpi_connection)
        self._rpi_worker.telemetry_received.connect(self._on_telemetry)
        self._rpi_worker.engagement_sent.connect(self._on_engagement_sent)
        self._rpi_worker.status_changed.connect(self.log_panel.log_status)
        self._rpi_worker.error_occurred.connect(self.log_panel.log_error)
        self._rpi_worker.start_worker()
        self.log_panel.log_info(
            f"RPi komut kanalı başlatıldı ({settings.host}:{settings.port})"
        )
        # Bağlantı kurulur kurulmaz RPi'nin kipi, aşaması ve PID katsayıları
        # arayüzde görünenle aynı olmalı.
        self._push_mode_to_rpi()
    
    def stop_tcp_worker(self):
        if self._rpi_worker:
            self._rpi_worker.stop_worker()
            self._rpi_worker = None
            self.status_panel.set_tcp_status(False)
            self.log_panel.log_info("RPi komut kanalı durduruldu")
    
    def start_target_simulator(self):
        """Hedef simülasyonunu başlat"""
        if self._target_simulator and self._target_simulator.isRunning():
            self.log_panel.log_warning("Hedef simülasyonu zaten çalışıyor")
            return
        
        self._target_simulator = TargetSimulator()
        
        # Görev odaklı signal bağlantıları
        self._target_simulator.target_detected.connect(self.log_panel.log_target_detected)
        self._target_simulator.target_lost.connect(self.log_panel.log_target_lost)
        self._target_simulator.in_range.connect(self.log_panel.log_in_range)
        self._target_simulator.out_of_range.connect(self.log_panel.log_out_of_range)
        self._target_simulator.friendly_detected.connect(self.log_panel.log_friendly)
        self._target_simulator.hostile_detected.connect(self.log_panel.log_hostile)
        self._target_simulator.engagement_started.connect(self.log_panel.log_engagement_start)
        self._target_simulator.engagement_result.connect(self.log_panel.log_engagement_result)
        self._target_simulator.track_update.connect(self.log_panel.log_track_update)
        self._target_simulator.status_changed.connect(self.log_panel.log_status)
        
        # Menzil göstergesi bağlantısı
        self._target_simulator.distance_updated.connect(self.status_panel.set_target_distance)
        
        self._target_simulator.start_worker()
        self.log_panel.log_info("🎯 Hedef simülasyonu başlatıldı")
        self.status_bar.showMessage("Hedef simülasyonu aktif")
    
    def stop_target_simulator(self):
        """Hedef simülasyonunu durdur"""
        if self._target_simulator:
            self._target_simulator.stop_worker()
            self._target_simulator = None
            self.log_panel.log_info("Hedef simülasyonu durduruldu")
            self.status_panel.clear_target_distance()
    
    def toggle_target_simulator(self):
        """Hedef simülasyonunu aç/kapat"""
        if self._target_simulator and self._target_simulator.isRunning():
            self.stop_target_simulator()
        else:
            self.start_target_simulator()
    
    # ==================== SLOT'LAR ====================
    
    @Slot()
    def _on_system_start(self):
        self.log_panel.log_info("🟢 Sistem başlatıldı")
        self.status_bar.showMessage("Sistem aktif")
        # Önceki oturumda güvenli duruşa düşülmüş olabilir; yeniden başlatmada
        # emniyet kilidi kalkar, ateş kilidi ise kapalı hâlde başlar.
        self.control_panel.set_emergency(False)
        self._last_fired_flag = False
        self._last_failsafe_flag = False
        self._stm_ever_healthy = False
        state = self._system.on_start()
        self.status_panel.set_system_state(state.value)
        self.start_udp_worker()
        self.start_tcp_worker()

    @Slot()
    def _on_system_stop(self):
        self.log_panel.log_warning("🔴 Sistem durduruldu")
        self.status_bar.showMessage("Sistem durduruldu")
        state = self._system.on_stop()
        self.status_panel.set_system_state(state.value)
        self.stop_udp_worker()
        self.stop_vision_worker()
        self.stop_tcp_worker()

    @Slot()
    def _on_system_reset(self):
        self.log_panel.log_info("🔄 Sistem sıfırlandı")
        self.status_bar.showMessage("Sistem sıfırlandı")
        self.stop_udp_worker()
        self.stop_vision_worker()
        self.stop_tcp_worker()
        state = self._system.on_reset()
        self.status_panel.set_system_state(state.value)
        self.status_panel.clear_target_info()
        self.status_panel.clear_target_distance()
        self._clear_target_data()
        self._engaged_track_id = None
        self._candidate = None
        self._reported_track_ids.clear()
        self.control_panel.set_emergency(False)
        self.status_panel.set_critical_zone_warning(False)
        self._last_fired_flag = False
        self._last_failsafe_flag = False
        self._stm_ever_healthy = False
        self._last_range_reason = None

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        self._current_mode = mode
        self.status_panel.set_mode(mode)
        self.log_panel.log_info(f"Mod değiştirildi: {mode}")
        self.status_bar.showMessage(f"Aktif Mod: {mode}")

        if self._rpi_worker:
            # Mod değişiminde bekleyen komutları atıyoruz: manuel modda
            # üretilmiş bir hedef komutunun otonoma geçince uygulanması
            # (ya da tersi) beklenmedik servo hareketi üretir.
            self._rpi_worker.clear_pending()
            self._push_mode_to_rpi()
        if self._vision_worker:
            self._vision_worker.set_stage(self._stage_from_mode(mode))

    @staticmethod
    def _stage_from_mode(mode: str) -> int:
        """Arayüz modunu IFF aşamasına çevir.

        2. Aşamada yarışma kuralları gereği renk ayrımı yoktur, tüm hedefler
        düşman sayılır. 3. Aşamada dost/düşman ayrımı renk bandına göre
        yapılır. Manuel modda ateş kararı operatöründür; en muhafazakâr
        davranan 3. Aşama kuralları uygulanır ki dost hedefte ateş kilidi
        yine de devreye girsin.
        """
        return 2 if mode.upper() == "ASAMA_2" else 3

    @staticmethod
    def _rpi_stage_from_mode(mode: str) -> int:
        """Arayüz modunu RPi'nin yarışma aşamasına çevir.

        IFF aşaması ile aynı şey değil: MANUEL kipte görüntü işleme en
        muhafazakâr IFF kuralını (3) uygularken, atış kontrol tarafında görev
        Aşama-1'dir — orada LiDAR menzil kapısı aranmaz, yönelim operatörün
        klavye komutlarıyla yapılır. Bu ayrımı karıştırmak, manuel modda
        ateşin LiDAR beklerken hiç çıkmaması demek olurdu.
        """
        m = mode.upper()
        if m == "MANUEL":
            return 1
        return 2 if m == "ASAMA_2" else 3

    def _push_mode_to_rpi(self) -> None:
        """Kip + aşama; PID yalnız otonom 2./3. aşamada."""
        if self._rpi_worker is None:
            return
        autonomous = self._current_mode != "MANUEL"
        self._rpi_worker.send_mode(
            autonomous, self._rpi_stage_from_mode(self._current_mode)
        )
        if self._current_mode in ("ASAMA_2", "ASAMA_3"):
            kp, ki, kd = self.control_panel.servo_control.get_pid()
            self._rpi_worker.send_pid(kp, ki, kd)

    def _enter_fail_safe(self, reason: str):
        """Sistemi güvenli duruşa al: hedef akışı kesilir, ateş kilitlenir."""
        state = self._system.on_fail_safe(reason)
        self.status_panel.set_system_state(state.value)
        self.log_panel.log_error(f"GÜVENLİ DURUŞ: {reason}")
        self.status_bar.showMessage(f"GÜVENLİ DURUŞ: {reason}")
        # KTR Bölüm 6: emniyet katmanları arayüzde de kapanmalı. Ateş kilidi
        # açık kalmışsa kapatılır ve ATEŞ düğmesi pasifleşir.
        self.control_panel.set_emergency(True)
        if self._rpi_worker:
            self._rpi_worker.clear_pending()

    # ---------- Boru hattı sonucu ----------
    @Slot(object)
    def _on_pipeline_result(self, result):
        """Her kare için: durumu güncelle, hedefi RPi'ye ilet, angajmanı yönet.

        Bu metot UI thread'inde çalışır ve yalnızca hafif iş yapar; ağır olan
        her şey worker'larda kaldı.
        """
        state = self._system.update(result)
        self.status_panel.set_system_state(state.value)
        self._log_track_events(result)

        candidate = result.candidate
        if candidate is None:
            if self._candidate is not None:
                self._clear_target_data()
                self._candidate = None
            return

        previous_id = self._candidate.track_id if self._candidate else None
        self._candidate = candidate
        if candidate.track_id != previous_id:
            self.log_panel.log_target_detected(
                f"#{candidate.track_id} {candidate.display_name} ({candidate.iff})"
            )

        # Panel güncellemeleri yalnızca bilgi değiştiğinde yapılıyor.
        # `set_target_info` her çağrıda kareyi yeniden çiziyor; 30 FPS'te her
        # kare için tetiklemek görüntüyü iki kez render etmek demekti.
        info = (candidate.display_name, candidate.is_friendly)
        if info != self._last_target_info:
            self._last_target_info = info
            friendly = candidate.is_friendly is True
            self.status_panel.set_target_classification(candidate.display_name, friendly)
            self.video_display.set_target_info(candidate.display_name, friendly)
            # Ateş kilidi: dost hedefte ATEŞ düğmesi kapanır. `is_friendly`
            # None ise (IFF henüz karar vermedi) dost sayılmaz; belirsizlikte
            # karar operatöründür.
            self.control_panel.set_friendly_target(friendly)

        if self._current_mode == "MANUEL" or self._system.state is SystemState.FAIL_SAFE:
            return
        self._stream_target(result, candidate)
        self._maybe_auto_engage(result, candidate)

    def _stream_target(self, result, candidate):
        """Yalnız OTONOM 2./3. — hedef merkezi RPi PID'ye."""
        if getattr(config, "TRACKING_TEST_MODE", False):
            cx, cy = candidate.center
            self.status_bar.showMessage(
                f"TRACKING TEST (otonom servo kapalı) → #{candidate.track_id} "
                f"cx={cx:.0f} cy={cy:.0f}"
            )
            return
        if self._current_mode not in ("ASAMA_2", "ASAMA_3"):
            return
        if self._rpi_worker is None:
            return
        # Nişan = kutu merkezi + AIM ofset (kilit ile aynı nokta)
        ox = float(getattr(config, "AIM_OFFSET_X_PX", 0.0))
        oy = float(getattr(config, "AIM_OFFSET_Y_PX", 0.0))
        cx = float(candidate.center[0]) + ox
        cy = float(candidate.center[1]) + oy
        t0 = time.perf_counter()
        self._rpi_worker.send_target(
            cx, cy, candidate.config_class_id, candidate.track_id, candidate.locked
        )
        # Comms süresi: bir sonraki Latency özetine yansısın diye result'a yazılamaz;
        # status bar'da ms göster.
        comms_ms = (time.perf_counter() - t0) * 1000.0
        lock_txt = "KİLİT" if candidate.locked else "takip"
        e2e = ""
        summary = getattr(result, "latency_summary", None) or {}
        if summary:
            e2e = f" | E2E {float(summary.get('end_to_end', 0)):.0f}ms"
        self.status_bar.showMessage(
            f"{lock_txt} → #{candidate.track_id} aim=({cx:.0f},{cy:.0f}) "
            f"comms={comms_ms:.1f}ms{e2e}"
        )

    def _maybe_auto_engage(self, result, candidate):
        """Kilitli düşman hedefte angajman talebini gönder.

        Üç koruma katmanı var ve hepsi birlikte sağlanmalı:
        1. Hedef kilidi (KTR 4.4.2) — boru hattı kilit koşullarını doğruladı.
        2. IFF düşman kararı — dost ya da bilinmeyen hedefe ateş edilmez.
        3. Operatörün ateş kilidini açmış olması — insan onayı devrede kalır.

        Mesafe ve yasak açı bölgesi denetimi RPi'de yapılır; burada tekrar
        edilmiyor çünkü LiDAR verisi orada, gecikmesiz.
        """
        if getattr(config, "TRACKING_TEST_MODE", False):
            return
        if not (result.locked and candidate.locked):
            return
        if candidate.is_friendly is not False:
            return
        if not self.control_panel.is_fire_unlocked:
            return

        now = time.monotonic()
        if now - self._last_engage_time < self._settings.pipeline.engage_repeat_s:
            return
        self._last_engage_time = now
        self._rpi_worker and self._rpi_worker.send_engage(
            candidate.track_id, candidate.config_class_id
        )

    def _log_track_events(self, result):
        """Yeni/kaybolan/imha edilen izleri göreve dönük mesajlara çevir."""
        for track_id in result.new_track_ids:
            if track_id in self._reported_track_ids:
                continue
            self._reported_track_ids.add(track_id)
            view = next((t for t in result.tracks if t.track_id == track_id), None)
            if view is None:
                continue
            if view.is_friendly is True:
                self.log_panel.log_friendly(f"#{track_id} {view.display_name}")
            elif view.is_friendly is False:
                self.log_panel.log_hostile(f"#{track_id} {view.display_name}")

        for track_id in result.lost_track_ids:
            self._reported_track_ids.discard(track_id)
            self.log_panel.log_target_lost(f"#{track_id}")

        for track_id in result.destroyed_track_ids:
            self.log_panel.log_engagement_result(f"#{track_id} imha doğrulandı", True)
            if track_id == self._engaged_track_id:
                self._engaged_track_id = None

    # ---------- Operatör komutları ----------
    @Slot()
    def _on_fire_command(self):
        """Manuel ATEŞ düğmesi (iki adımlı güvenlik mekanizmasının ikinci adımı).

        Atış kontrol tarafı (`rpi5/fire_control`) `engage` mesajını ARM+FIRE
        niyeti olarak yorumluyor; Aşama-1'de (manuel görev) ek bir kilit/menzil
        koşulu aramadığı için manuel ateş artık RPi'de de tamamlanıyor. Bu
        yüzden eskiden buraya yazılan "manuel modda ateş çıkmaz" uyarısı
        kaldırıldı — yanlış bilgi vermek, hiç bilgi vermemekten kötüdür.
        """
        self.log_panel.log_warning("🔥 ATEŞ KOMUTU VERİLDİ!")
        if (
            getattr(config, "TRACKING_TEST_MODE", False)
            and self._current_mode in ("ASAMA_2", "ASAMA_3")
        ):
            self.log_panel.log_warning(
                "TRACKING TEST — otonom ateş RPi'ye gönderilmedi"
            )
            return
        if self._system.state is SystemState.FAIL_SAFE:
            self.log_panel.log_error("GÜVENLİ DURUŞ - ateş komutu reddedildi")
            return
        if self._rpi_worker is None or not self._rpi_worker.isRunning():
            self.log_panel.log_error("RPi bağlantısı yok - Ateş komutu gönderilemedi!")
            return

        candidate = self._candidate
        if candidate is None:
            self.log_panel.log_error(
                "Angajman adayı yok - ateş komutu gönderilmedi"
            )
            return
        if candidate.is_friendly is True:
            self.log_panel.log_error("DOST hedef - ateş komutu reddedildi")
            return

        self._rpi_worker.send_engage(candidate.track_id, candidate.config_class_id)

    @Slot(int, int)
    def _on_servo_command(self, x: int, y: int):
        """Kaydırıcı → RPi artım (klavye ayrı yoldan gider)."""
        if self._rpi_worker is None or not self._rpi_worker.isRunning():
            self.log_panel.log_warning("RPi yok — önce BAŞLAT")
            return
        if self._current_mode != "MANUEL":
            return
        pan = _PAN_CENTER + x * _AZIMUTH_SCALE
        tilt = _TILT_CENTER + y
        self._last_manual_command_t = time.monotonic()
        self._rpi_worker.set_manual_angles(pan, tilt)
        self.status_bar.showMessage(f"Manuel kaydırıcı pan={pan:.1f} tilt={tilt:.1f}")

    @Slot(float, float, float)
    def _on_pid_command(self, kp: float, ki: float, kd: float):
        """P/I/D yalnız otonom 2./3. aşamada RPi'ye gider."""
        if getattr(config, "TRACKING_TEST_MODE", False):
            return
        if self._current_mode not in ("ASAMA_2", "ASAMA_3"):
            return
        if self._rpi_worker is None or not self._rpi_worker.isRunning():
            self.log_panel.log_warning(
                "RPi bağlantısı yok - PID katsayıları gönderilemedi"
            )
            return
        self._rpi_worker.send_pid(kp, ki, kd)
        self.log_panel.log_info(f"PID güncellendi: P={kp:.3f} I={ki:.3f} D={kd:.3f}")

    # ---------- Klavye ile manuel yönelim (KTR 4.3.2) ----------
    def _try_manual_keyboard(self, event) -> bool:
        """WASD/ok — basılı tutunca da hareket (Qt auto-repeat)."""
        if self._stack.currentIndex() != 1:
            return False
        if self._current_mode != "MANUEL":
            return False

        delta = _KEYBOARD_BINDINGS.get(event.key())
        if delta is None:
            return False

        focused = QApplication.focusWidget()
        if isinstance(focused, QDoubleSpinBox):
            if event.key() in (
                Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                Qt.Key_PageUp, Qt.Key_PageDown,
            ):
                return False

        step = 1 if event.modifiers() & Qt.ShiftModifier else _KEYBOARD_STEP
        dx_ui = delta[0] * step
        dy_ui = delta[1] * step

        sc = self.control_panel.servo_control
        x = max(sc.x_slider.minimum(), min(sc.x_slider.maximum(), sc.x_slider.value() + dx_ui))
        y = max(sc.y_slider.minimum(), min(sc.y_slider.maximum(), sc.y_slider.value() + dy_ui))
        sc.set_position(x, y)

        dx = float(dx_ui) * _AZIMUTH_SCALE
        dy = float(dy_ui)

        if self._rpi_worker is None or not self._rpi_worker.isRunning():
            if not event.isAutoRepeat():
                self.log_panel.log_warning("RPi yok — önce BAŞLAT")
            return True

        self._last_manual_command_t = time.monotonic()
        self._rpi_worker.queue_manual_delta(dx, dy)
        self.status_bar.showMessage(f"Manuel dx={dx:+.1f} dy={dy:+.1f}")
        return True

    def eventFilter(self, watched, event):
        """Alt widget odaklıyken de klavye yönelim komutlarını yakala."""
        if event.type() == QEvent.Type.KeyPress and self._try_manual_keyboard(event):
            return True
        return super().eventFilter(watched, event)

    # ---------- RPi'den gelenler ----------
    @Slot(bool)
    def _on_rpi_connection(self, connected: bool):
        if connected:
            # Göstergeleri home'a çek; fiziksel servoya ani mutlak komut YOK
            # (açılışta zıplama yapıyordu). RPi STM telemetrisiyle senkron kalır.
            self.control_panel.servo_control.set_position(
                _PAN_HOME_UI, _ELEVATION_HOME_UI
            )
            self._push_mode_to_rpi()
        else:
            self.log_panel.log_warning("RPi bağlantısı yok")

    @Slot(object)
    def _on_telemetry(self, message: dict):
        """RPi telemetrisini panellere dağıt.

        Gelen satır önce `shared.protocol.normalize_telemetry()` ile kanonik
        alanlara çevrilir. Sebep: gerçek atış kontrol servisi
        (`rpi5/fire_control`) mesafeyi metre cinsinden `lidar_m`, açıları
        `pan_deg/tilt_deg`, STM32 bayraklarını iç içe `stm` sözlüğünde
        gönderiyor; sözleşmedeki eski `telemetry()` kurucusu ise santimetre ve
        düz alanlar kullanıyor. Çeviriyi sözleşmede tek yerde tutmak, arayüzün
        iki şemayı da bilmek zorunda kalmasını önlüyor.
        """
        data = protocol.normalize_telemetry(message)

        distance_m = data.get("distance_m")
        if distance_m is not None:
            self.status_panel.set_target_distance(distance_m)

        if "in_forbidden_zone" in data:
            self.status_panel.set_critical_zone_warning(data["in_forbidden_zone"])

        # Uygulanan açılar: göstergeleri ve artım referansını gerçeğe çek.
        pan, tilt = data.get("pan"), data.get("tilt")
        if pan is not None and tilt is not None:
            self._sync_servo_indicators(pan, tilt)

        # Ateşleme onayı: seviye bayrağının yükselen kenarı (KTR 4.2.2.8
        # kapalı çevrim). Talep anı değil, STM32'nin gerçekten tetiklediği an.
        fired = bool(data.get("fired", False))
        if fired and not self._last_fired_flag:
            track_id = data.get("track_id", self._engaged_track_id)
            self.log_panel.log_warning(f"Ateşleme onaylandı: #{track_id}")
            if self._vision_worker and track_id is not None:
                self._vision_worker.notify_fired(int(track_id))
        self._last_fired_flag = fired

        failsafe = bool(data.get("failsafe", False))
        if not failsafe:
            self._stm_ever_healthy = True
        elif (
            self._stm_ever_healthy
            and failsafe
            and not self._last_failsafe_flag
        ):
            reason = data.get("reason", "Atış kontrol birimi güvenli duruşta")
            self._enter_fail_safe(str(reason))
        self._last_failsafe_flag = failsafe

        # Aşama-3 menzil kapısının gerekçesi operatöre yazılır: ateş çıkmadığında
        # "neden çıkmadı" sorusunun cevabı log'da olsun.
        reason = data.get("range_reason")
        if isinstance(reason, str) and reason != self._last_range_reason:
            self._last_range_reason = reason
            if not data.get("range_ok", True):
                self.log_panel.log_warning(f"Menzil kapısı: {reason}")

        status_text = data.get("status_text")
        if status_text:
            self.status_bar.showMessage(f"Raspberry Pi: {status_text}")

    def _sync_servo_indicators(self, pan: float, tilt: float) -> None:
        """Otonomda telemetri → gösterge. MANUEL'de dokunma (operatör hakim)."""
        if self._current_mode == "MANUEL":
            return
        if time.monotonic() - self._last_manual_command_t < 1.0:
            return
        x = int(round((pan - _PAN_CENTER) / _AZIMUTH_SCALE))
        y = int(round(tilt - _TILT_CENTER))
        self.control_panel.servo_control.set_position(x, y)
        if self._rpi_worker is not None:
            self._rpi_worker.sync_angles(pan, tilt)

    @Slot(int)
    def _on_engagement_sent(self, track_id: int):
        self._engaged_track_id = track_id
        state = self._system.on_engagement()
        self.status_panel.set_system_state(state.value)
        self.log_panel.log_engagement_start(f"#{track_id}")
        # RPi ateşleme bildirimi göndermiyorsa imha değerlendirmesi hiç
        # başlamaz; angajman talebini de ateşleme anı sayıyoruz.
        if self._vision_worker:
            self._vision_worker.notify_fired(track_id)

    def _clear_target_data(self):
        """Hedef verilerini temizle"""
        self.video_display.clear_target_box()
        self.status_panel.clear_target_info()
        self.control_panel.clear_target_lock()
        self._last_target_info = None
        self.log_panel.log_info("Hedef kaybedildi")

    # ==================== PENCERE OLAYLARI ====================
    
    def closeEvent(self, event):
        # Uygulama seviyesindeki klavye süzgeci pencereye bağlı; pencere
        # kapanırken kaldırılmazsa silinmiş bir nesneye olay yönlenebilir.
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        # Worker kapanış sırası: önce frame üretici (UDP), sonra tüketici
        # (Detection). Aksi sırada detection halen frame işlerken UDP
        # kapanırsa sorun olmaz ama tersine kapatmak en güvenlisi.
        self.stop_udp_worker()
        self.stop_vision_worker()
        self.stop_tcp_worker()
        self.stop_target_simulator()
        event.accept()
    
    def keyPressEvent(self, event):
        # Splash ekranındayken Enter/Space ile başlat
        if self._stack.currentIndex() == 0:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self._launch_main_ui()
                return
        if event.key() == Qt.Key_F11:
            if self.isFullScreen(): self.showNormal()
            else: self.showFullScreen()
        elif event.key() == Qt.Key_Escape:
            if self.isFullScreen(): self.showNormal()
        elif event.key() == Qt.Key_F5:
            self._restart_connections()
        elif event.key() == Qt.Key_F6:
            self.toggle_target_simulator()
        elif self._try_manual_keyboard(event):
            event.accept()
        else:
            super().keyPressEvent(event)
    
    def _restart_connections(self):
        self.stop_udp_worker()
        self.stop_tcp_worker()
        QTimer.singleShot(500, self.start_udp_worker)
        QTimer.singleShot(1000, self.start_tcp_worker)
