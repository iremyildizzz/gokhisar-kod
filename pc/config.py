"""Sistem geneli konfigürasyon sabitleri."""

# ---------------- YOLO ----------------
YOLO_MODEL_PATH = "yolo_modeli.pt"   # eğitilmiş model
YOLO_IMG_SIZE = 640                  # 480 küçük/uzak balonda kaçırma yapıyordu
YOLO_CONF_THRESHOLD = 0.15
YOLO_IOU_THRESHOLD = 0.45


CLASS_NAMES = {0: "fuze", 1: "helikopter", 2: "iha", 3: "ucak", 4: "balon"}
BALLOON_CLASS_ID = 4
MODEL_CLASS_IDS = {0, 1, 2, 3}

# ---------------- HSV (küçük hedef / yedek tespit) ----------------
# Kırmızı için çift eşik (Hue 0 civarı sarmal olduğu için iki aralık)
HSV_RED_LOWER_1 = (0, 120, 70)
HSV_RED_UPPER_1 = (10, 255, 255)
HSV_RED_LOWER_2 = (170, 120, 70)
HSV_RED_UPPER_2 = (180, 255, 255)

# Dost (camgöbeği/mavi) aralığı — IFF için
HSV_CYAN_LOWER = (80, 100, 70)
HSV_CYAN_UPPER = (130, 255, 255)

MIN_CIRCULARITY = 0.72        # balon adayı kabul eşiği
MIN_CONTOUR_AREA = 12         # piksel^2, çok küçük gürültüyü ele
ROI_SCALE_FACTOR = 6.0        # dinamik ROI = balon boyutu * katsayı

# ---------------- Doğrulama / Eşleştirme ----------------
MATCH_REGION_BASE_RATIO = 1.2     # maket yüksekliğine oranla eşleştirme bölgesi
MATCH_REGION_EXTEND_STEP = 0.4    # hedef küçüldükçe kademeli uzatma katsayısı
SMALL_TARGET_PX_HEIGHT = 20       # bu değerin altı "uzak hedef" sayılır (@640; eski 40@1280)

# ---------------- IFF ----------------
IFF_HISTORY_LEN = 15          # sınıflandırma geçmişi uzunluğu (kare)
IFF_VOTE_MIN_FRAMES = 5       # düşman doğrulaması için asgari tutarlı kare
HUE_RED_RANGES = [(0, 10), (170, 180)]
HUE_CYAN_RANGE = (80, 130)

# ---------------- Takip ----------------
# Sticky kanonik ID: ByteTrack raw ID değişse bile #N sabit kalsın
TRACK_HIGH_CONF = 0.20
TRACK_LOW_CONF = 0.1
TRACK_BUFFER = 90
TRACK_MATCH_IOU = 0.15
TRACK_DEDUPE_IOU = 0.55
TRACK_STICK_IOU = 0.12          # remapping — düşük eşik (ID kaybını kes)
TRACK_STICK_CENTER_RATIO = 2.5  # merkez yakınlığı ile yapıştır
DEDUPE_IOU = 0.30
DEDUPE_CENTER_RATIO = 1.1
TRACK_MAX_DRAW_MISSES = 20

# Hafif Re-ID & Kamera Hareket Dengeleme (GMC)
ENABLE_GMC = True
ENABLE_REID = True
REID_SIMILARITY_THRESHOLD = 0.55
REID_MAX_DISTANCE_PX = 240.0

# Per-track + servo Kalman — ölçüme daha çok güven
TRACK_KALMAN_PROCESS = 3e-2
TRACK_KALMAN_MEASURE = 2e-2
TRACK_CANDIDATE_MAX_MISSES = 25  # kısa miss'te aday düşmesin
CANDIDATE_HOLD_FRAMES = 20       # select None olsa bile adayı tut
SERVO_KALMAN_LEAD_S = 0.0
SERVO_TARGET_MAX_JUMP_PX = 20.0

# False: otonom 2./3. servo/PID açık.
TRACKING_TEST_MODE = False

# Servo home (gokhisar 0–180°). UI Elevation = tilt - 90 → -10° ⇒ tilt 80°.
SERVO_PAN_HOME_DEG = 90.0
SERVO_TILT_HOME_DEG = 80.0
SERVO_ELEVATION_HOME_UI = -10  # -15 fazla aşağıydı

# ---------------- Değerlendirme / Önceliklendirme (5 Ölçüt) ----------------
W_SIZE = 0.35
W_CENTER = 0.25
W_STABILITY = 0.20
W_ENGAGEMENT = 0.10
W_SERVO = 0.10
CANDIDATE_HYSTERESIS = 0.20

# ---------------- Kilitlenme (balon nişanı) ----------------
# 640 genişlikte ~aynı açısal bant (~90 px @1280)
LOCK_TOLERANCE_PX = 45
LOCK_STABLE_FRAMES = 2
AIM_OFFSET_X_PX = 0.0
AIM_OFFSET_Y_PX = 0.0

# ---------------- İmha Değerlendirme ----------------
DESTROY_MISS_FRAMES = 45      # yeniden tespit edilememe süresi (kare) — TRACK_BUFFER (30) içinde kalmalı 1.5 saniye
DESTROY_CONF_THRESHOLD = 0.2  # güven skoru eşiği

# ---------------- Haberleşme (PC -> RPi) ----------------
RPI_HOST = "xxxx.xxxx.xxxx.xxxx"
RPI_PORT = 5005

# ---------------- Kamera ----------------
# Düşük gecikme denemesi: 640x480@30 (Pi video ile aynı olmalı)
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_CENTER = (FRAME_WIDTH // 2, FRAME_HEIGHT // 2)
