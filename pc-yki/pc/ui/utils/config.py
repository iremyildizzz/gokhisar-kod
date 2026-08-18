"""
Cross-platform konfigürasyon ve yardımcı fonksiyonlar
Linux/Windows uyumluluğu için os.path kullanılıyor
"""

import os
import sys
import platform
from pathlib import Path

from shared import protocol


class SystemConfig:
    """
    Sistem konfigürasyonu - Cross-platform uyumluluk
    
    Neden bu yapı?
    - Windows ve Linux'ta dosya yolları farklı (ters bölü vs bölü)
    - Seri port isimleri farklı (COM3 vs /dev/ttyUSB0)
    - os.path ve pathlib kullanarak platform bağımsız çalışıyoruz
    """
    
    # Platform tespiti
    IS_WINDOWS = platform.system() == "Windows"
    IS_LINUX = platform.system() == "Linux"
    IS_MAC = platform.system() == "Darwin"
    
    # Depo kökü: pc/ui/utils/config.py -> utils -> ui -> pc -> kök
    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    # Ağ sabitleri bilerek burada tutulmuyor: port ve adres PC ile RPi'nin
    # üzerinde anlaşması gereken sözleşme maddeleri, tek yerde (shared/) durur.

    # Seri port ayarları (opsiyonel - doğrudan USB bağlantı için)
    @classmethod
    def get_serial_port(cls) -> str:
        """
        Platform'a göre varsayılan seri port döndürür
        
        Neden?
        - Windows: COM3, COM4 gibi isimler kullanır
        - Linux: /dev/ttyUSB0, /dev/ttyACM0 gibi isimler kullanır
        """
        if cls.IS_WINDOWS:
            return "COM3"
        elif cls.IS_LINUX:
            return "/dev/ttyUSB0"
        elif cls.IS_MAC:
            return "/dev/tty.usbserial"
        return "/dev/ttyUSB0"
    
    @classmethod
    def get_platform_info(cls) -> dict:
        """Sistem bilgilerini döndürür - debug için kullanışlı"""
        return {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python_version": sys.version,
            "project_root": str(cls.PROJECT_ROOT),
            "serial_port": cls.get_serial_port()
        }


class NetworkConfig:
    """
    Ağ iletişim konfigürasyonu.

    Adres ve port değerleri artık burada tanımlanmıyor, `shared/protocol.py`den
    okunuyor: bunlar PC'ye özgü tercihler değil, RPi ile paylaşılan sözleşme
    maddeleri. Daha önce aynı port numarası hem burada hem `pc/config.py`de hem
    atış kontrol yazılımında bağımsız literaller olarak duruyordu.
    """

    # Raspberry Pi bağlantı ayarları
    RPI_HOST = os.environ.get("GOKHISAR_RPI_HOST", protocol.DEFAULT_RPI_HOST)

    # Port numaraları
    UDP_VIDEO_PORT = int(os.environ.get("GOKHISAR_UDP_VIDEO_PORT",
                                        str(protocol.VIDEO_PORT)))
    TCP_COMMAND_PORT = int(os.environ.get("GOKHISAR_RPI_PORT",
                                          str(protocol.COMMAND_PORT)))

    # Timeout ayarları (saniye)
    TCP_TIMEOUT = 5.0
    UDP_TIMEOUT = 1.0
    
    # Buffer boyutları
    TCP_BUFFER_SIZE = 1024
    UDP_BUFFER_SIZE = 65535    # Video frame'leri için büyük buffer

    # ---------------------------------------------------------------
    # GStreamer Video Akışı Ayarları
    # ---------------------------------------------------------------
    # Kullanıcının manuel olarak çalıştırdığı pipeline'ın eşdeğeri:
    #   gst-launch-1.0 udpsrc port=5000 \
    #       caps="application/x-rtp, media=video, encoding-name=JPEG, payload=26" \
    #       ! rtpjpegdepay ! decodebin ! autovideosink
    #
    # Bizim pipeline'ımız autovideosink yerine "fdsink fd=1" kullanır;
    # bu sayede depay edilmiş JPEG kareleri subprocess.stdout üzerinden Python
    # tarafına akar. decodebin ve videoconvert'e ihtiyacımız yok çünkü Qt
    # tarafında QImage zaten JPEG decode edebiliyor (cv2.imdecode aracılığı ile).
    # Windows'ta VS Code/venv PATH'e GStreamer eklemeyebiliyor; bilinen yolu dene.
    GST_BIN = (
        r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe"
        if os.name == "nt"
        and os.path.isfile(
            r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin\gst-launch-1.0.exe"
        )
        else "gst-launch-1.0"
    )
    GST_RTP_CAPS = (
        "application/x-rtp, media=video, encoding-name=JPEG, payload=26"
    )


class ModelConfig:
    """
    Görüntü işleme / nesne tespiti modeli ayarları.

    Tasarım kararı: model-spesifik ayarları NetworkConfig veya UIConfig
    içine değil, ayrı bir sınıfa koyuyoruz. Tek sorumluluk ilkesi (SRP):
    "ağ" ile "model" iki ayrı bağlamdır; ileride farklı modele geçmek
    isteseniz sadece bu sınıfı değiştirirsiniz.
    """

    # YOLOv8 .pt dosyası — proje köküne göreli yol
    WEIGHTS_PATH = str(SystemConfig.PROJECT_ROOT / "models" / "best.pt")

    # Inference giriş çözünürlüğü. Eğitim 640x640 yapıldığı için
    # değiştirmemek en doğru; küçültürseniz hız artar ama küçük hedefler
    # kaçırılır.
    IMG_SIZE = 640

    # Confidence threshold — bu skorun altındaki tahminler atılır.
    # Düşük (0.10) = çok algılama (false positive ↑)
    # Yüksek (0.50) = az algılama, daha emin (false negative ↑)
    CONF_THRESHOLD = 0.30

    # IoU (Intersection over Union) threshold for Non-Maximum Suppression.
    # Aynı nesne için birden fazla bbox üretilirse aralarında üst üste binme
    # bu eşiğin üstündeyse zayıf olanı silinir.
    IOU_THRESHOLD = 0.45

    # "auto" / "cpu" / "cuda" / "cuda:0"
    # auto: CUDA varsa GPU, yoksa CPU. ultralytics bunu kendi tespit eder.
    DEVICE = "auto"

    # Aynı anda inference için tutulan max kuyruk uzunluğu.
    # 1 = en yeni frame, eskiler atılır (low-latency tracking için ideal).
    MAX_QUEUE_SIZE = 1

    # Sınıf adlarına özel ekran rengi (BGR yerine RGB; QPainter RGB kullanır).
    # Bilinmeyen sınıflar default renge düşer.
    CLASS_COLORS = {
        "iha":            (255, 215, 0),    # Altın sarısı
        "mini-micro-iha": (255, 140, 0),    # Turuncu
        "jet":            (255,  60, 60),   # Kırmızımsı
        "helikopter":     (  0, 200, 255),  # Cyan
        "rocket":         (255,   0, 255),  # Magenta
    }
    DEFAULT_COLOR = (0, 255, 0)             # Yeşil — bilinmeyen sınıf


class UIConfig:
    """
    Arayüz konfigürasyonu
    Renkler, boyutlar ve stil ayarları
    """
    
    # Pencere boyutları
    WINDOW_WIDTH = 1280
    WINDOW_HEIGHT = 800
    WINDOW_TITLE = "GÖKHİSAR - Yer Kontrol İstasyonu"
    
    # Renkler (Askeri tema)
    COLOR_BG_DARK = "#1a1a2e"
    COLOR_BG_PANEL = "#16213e"
    COLOR_ACCENT_GREEN = "#00ff00"
    COLOR_ACCENT_RED = "#ff0000"
    COLOR_WARNING_YELLOW = "#ffff00"
    COLOR_TEXT_PRIMARY = "#ffffff"
    COLOR_TEXT_SECONDARY = "#aaaaaa"
    
    # Font boyutları
    FONT_SIZE_LARGE = 24
    FONT_SIZE_MEDIUM = 16
    FONT_SIZE_SMALL = 12


# Singleton erişim için
system_config = SystemConfig()
network_config = NetworkConfig()
ui_config = UIConfig()
model_config = ModelConfig()
