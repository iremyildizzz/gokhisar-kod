#!/usr/bin/env python3
"""KTR uygunluk analizini PDF olarak üretir.

Analiz verisi neden bu dosyanın içinde
--------------------------------------
Önceki sürüm veriyi depo dışındaki bir canvas dosyasından (``~/.cursor/...``)
düzenli ifadeyle ayıklıyordu. Bu, raporu üretebilmeyi tek bir makinedeki tek
bir geçici dosyaya bağlıyordu: dosya silinse rapor bir daha üretilemez, içerik
değişse hangi sürümün PDF'e girdiği izlenemezdi. Analizin kendisi de bir
mühendislik çıktısıdır, bu yüzden kaynağı artık depoda ve sürüm kontrolünde.

Çalıştırma (Ubuntu'da `python` yok; `python3` veya sarmalayıcı kullanın):
    ./tools/generate_ktr_pdf.sh
    # veya:
    .venv/bin/python tools/generate_ktr_pdf.py
    # veya (reportlab kurulu sistem python3 ile):
    python3 tools/generate_ktr_pdf.py

Önkoşul: ``pip install reportlab`` (proje .venv'sinde zaten var olmalı).
Çıktı:
    docs/KTR-uygunluk-analizi.pdf
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:
    raise SystemExit(
        "reportlab yüklü değil.\n"
        "  source .venv/bin/activate && pip install reportlab\n"
        "  veya doğrudan: ./tools/generate_ktr_pdf.sh"
    ) from None

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "KTR-uygunluk-analizi.pdf"

FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
# Helvetica Türkçe karakterleri (ş, ğ, İ) taşımıyor; gömülü DejaVu şart.
_FONT_PATHS = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

STATUS_TR = {
    "tam": "Karşılandı",
    "kismi": "Kısmi",
    "yok": "Yok",
    "aykiri": "Rapora aykırı",
    "ek": "Rapordan ileri",
}

STATUS_COLOR = {
    "tam": colors.HexColor("#1a7f37"),
    "kismi": colors.HexColor("#9a6700"),
    "yok": colors.HexColor("#cf222e"),
    "aykiri": colors.HexColor("#a40e26"),
    "ek": colors.HexColor("#0969da"),
}

TEST_OZETI = "51 test: 48 geçti · 1 atlandı (yasak sektör) · 2 kırık (pc/vision)"

# ----------------------------------------------------------------------
# Madde tabloları: (KTR referansı, raporda ne vaat edilmiş, kodda ne var, durum)
# ----------------------------------------------------------------------

GORUNTU = [
    (
        "4.2.2.1",
        "YOLOv8s; ~30.000 görüntülük 5 sınıflı veri seti. \"Bu sınıflar istenilen "
        "maketleri ve balon nesnelerini kapsamaktadır.\"",
        "models/best.pt gerçekten YOLOv8 ve 5 sınıflı, ama sınıflar helikopter / iha / "
        "jet / mini-micro-iha / rocket. Balon sınıfı modelde yok. pc/config.py 4 "
        "numaralı sınıfı \"balon\" sanıyor; boşluk class_map.py + HSV ile kapatılıyor.",
        "aykiri",
    ),
    (
        "4.2.2.1",
        "DETECT durumunda her hedef için sınıf, sınır kutusu ve güven skoru; çıktı "
        "doğrudan karara değil doğrulama/takip modüllerine giriyor",
        "yolo_detector.py Detection(x1,y1,x2,y2,conf,class_id,source) üretiyor. conf "
        "eşiği 0.35, IoU 0.45, imgsz 640. VisionPipeline zinciri DETECT → VALIDATE → "
        "IFF → TRACK → EVALUATE sırasını koruyor.",
        "tam",
    ),
    (
        "4.2.2.1",
        "Küçük hedef: HSV çift eşikli maskeleme, morfolojik temizleme, kontur analizi, "
        "dairesellik filtresi; piksel boyutuna bağlı dinamik ROI",
        "hsv_detector.py üç tetikleyici sunuyor: (1) 30 karelik nesne≠balon "
        "dengesizliği, (2) LiDAR 10–15 m + ego-motion telafili hareket, "
        "(3) detect_under_object. VisionPipeline yalnızca (1)'i çağırıyor. Artık PC "
        "LiDAR mesafesini telemetriden biliyor, ama boru hattına geçirmek "
        "VisionPipeline'ı değiştirmek demek (pc/vision dokunulmuyor) — (2) hâlâ ölü yol.",
        "kismi",
    ),
    (
        "4.2.2.1",
        "\"Sistem performans düşmesi durumlarında yedek mekanizma devreye girer\": "
        "aynı HSV hattı + geometrik filtre",
        "detect_backup() + VisionPipeline.backup_mode var, ama set_backup_mode() hiçbir "
        "yerden çağrılmıyor: yedek otomatik devreye girmiyor. "
        "pc/performance_analysis/system_performance.py (~1085 satır) yazılmış ama "
        "bağlanmamış; object_performance.py boş.",
        "kismi",
    ),
    (
        "4.2.2.2",
        "Maketin altında piksel yüksekliğine göre boyutlanan dinamik eşleştirme "
        "bölgesi; ikinci yöntem olarak balonun üst ROI'sinde YOLO yeniden çalıştırma",
        "matcher.py iki yöntemi de uyguluyor. Taban oran 1.2; maket yüksekliği 40 px "
        "altına düşünce oran (40−h)/40 × 0.4 × 4 kadar büyüyor.",
        "tam",
    ),
    (
        "4.2.2.3",
        "Aşama-2'de balonlu tüm maketler düşman; Aşama-3'te Hue medyanı ile renk "
        "ayrımı, tek kare yetmez, ardışık karelerde zamansal oylama",
        "friend_foe.py: stage=2 → doğrudan DÜŞMAN. stage=3 → doygunluğu 80 üstü "
        "piksellerin Hue medyanı, asgari 10 piksel. Geçmiş 15 kare, karar için ≥5 "
        "tutarlı kare ve rakip oydan fazla olma şartı.",
        "tam",
    ),
    (
        "4.2.2.4",
        "ByteTrack; IoU + Macar algoritması; yüksek güvenli tespitler önce, düşük "
        "güvenliler telafi turunda; ölçüm yokken yalnız tahmin adımı",
        "supervision.ByteTrack (eşik 0.5, IoU 0.8, tampon 30). TargetTracker._to_sv "
        "conf < 0.1 olanları eliyor; 0.1–0.5 aralığı ByteTrack'in düşük-güven telafi "
        "turuna kalıyor.",
        "tam",
    ),
    (
        "4.2.2.4",
        "\"Servo kontrol kararlılığını artırmak amacıyla ByteTrack'ten bağımsız ikinci "
        "bir Kalman filtresi uygulanacaktır\" (gelecek zaman)",
        "ServoKalman (4 durum / 2 ölçüm, sabit hız) yazılmış ve "
        "VisionPipeline._update_servo_estimate ile RPi'ye giden konumu fiilen "
        "üretiyor. Ölçüm yokken predict_only(). Rapor \"yapılacak\" demiş, kod çalışıyor.",
        "ek",
    ),
    (
        "4.2.2.5",
        "Öncelik puanı beş ölçütten: boyut, merkeze uzaklık, takip kararlılığı, "
        "angajman uygunluğu, servo yönelim kararlılığı",
        "5 ölçüt uygulandı — W_SIZE 0.35, W_CENTER 0.25, W_STABILITY 0.20, "
        "W_ENGAGEMENT 0.10, W_SERVO 0.10. _select_candidate yalnız FOE geçirdiği için "
        "W_ENGAGEMENT pratikte hep 1.0.",
        "tam",
    ),
    (
        "4.2.2.5",
        "\"Üçüncü aşamada LiDAR verisi mesafe doğrulaması amacıyla sürece dahil "
        "edilerek karar kararlılığı artırılır\"",
        "LiDAR mesafesi artık PC'ye geliyor (status.lidar_m) ve arayüzde gösteriliyor. "
        "Ama değerlendirme/önceliklendirme modülleri mesafeyi hâlâ görmüyor: mesafe "
        "kapısı yalnız RPi'nin ateş kararında. Bağlamak VisionPipeline imzasını "
        "değiştirmeyi gerektiriyor.",
        "kismi",
    ),
    (
        "4.2.2.6",
        "TARGET_LOCK: merkez tolerans bölgesinde kararlı tutulunca angajmana hazır; "
        "merkez, sınıf ve takip kimliği servo sistemine aktarılır",
        "state_machine.update_lock(): tolerans 25 px, kilit için 10 ardışık kararlı "
        "kare, toleransın 3 katı aşılınca TRACK'e dönüş. RpiLink.send_target(cx, cy, "
        "class_id, track_id, locked) üç bilgiyi de gönderiyor.",
        "tam",
    ),
    (
        "4.2.2.9",
        "İmha doğrulaması üç koşulun EŞ ZAMANLI sağlanmasıyla: (a) belirli süre "
        "yeniden tespit edilememe, (b) takip zincirinin sonlanması, (c) güven "
        "skorlarının eşik altında kalması",
        "miss_count biriktiriliyor, DESTROY_MISS_FRAMES=15 (TRACK_BUFFER 30 içinde), "
        "yüksek güvenle yeniden görünürse TRACK'e dönülüyor. Ama cond_miss ile "
        "cond_track_ended miss_count≥15'te örtüşüyor — üç bağımsız koşul değil. "
        "test_destroy_requires_all_three_conditions kırık.",
        "kismi",
    ),
    (
        "4.2.2.10",
        "Hedef yaşam döngüsü durum makinesi: algılama, doğrulama, takip, "
        "değerlendirme, kilitleme, imha değerlendirme ayrı durumlar (Şekil 4.10)",
        "TargetState: DETECT, VALIDATE, TRACK, EVALUATE, TARGET_LOCK, DESTROYED. "
        "Şekil 4.10 ile birebir; geçişler on_validated / on_iff / "
        "on_selected_for_lock / update_lock / evaluate_destroyed üzerinden.",
        "tam",
    ),
]

ATIS = [
    (
        "4.2.2.7",
        "RPi, PC'den gelen X-Y koordinatıyla merkez hatasını hesaplar ve PID ile "
        "minimize eder",
        "rpi5/fire_control: err = cx − W/2 pikselden optics.pixel_offset_to_deg ile "
        "dereceye çevriliyor (GS + 16 mm HFOV), sonra pan/tilt PID'i (kp 0.55, ki "
        "0.05, kd 0.08, çıkış ±4°/tur) açıyı sürüyor. Piksel yerine derece üzerinden "
        "kapatmak, kazancı kamera optiğinden bağımsız kılıyor.",
        "tam",
    ),
    (
        "4.2.2.7",
        "Yasaklı açı bölgeleri yazılımsal denetlenir, bu bölgelere karşılık gelen "
        "yönelim komutları STM32'ye iletilmez",
        "Yeni atış kontrol yazılımında sektör denetimi YOK: yalnızca 0–180° "
        "kenetlemesi (Limits) var. Önceki sürümde çalışan üç yasaklı sektör "
        "(sol 0–20°, sağ 160–180°, aşağı 150–180°) yeniden yazımda düştü. Tanım "
        "shared/engagement.py'de duruyor, kullanan yok.",
        "yok",
    ),
    (
        "Bölüm 6",
        "\"Yasaklı açılara yönelik hareket kısıtlanmış olup bu bölgelerde hem namlu "
        "hareketi hem de ATIŞ KOMUTU tamamen engellenmektedir.\"",
        "want_fire kapısında açı sektörü hiç yok; STM32 tarafında da yalnızca "
        "Servo_WasLimited() (0/180 kenetlemesi) denetleniyor. Namlu operatöre dönük "
        "bir açıda ateş edilebilir. En kritik açık madde.",
        "yok",
    ),
    (
        "4.2.2.7",
        "Her hedef sınıfı için önceden tanımlı güvenli angajman mesafeleri; ayrıca "
        "hedefin bu aralıkta belirli bir süre kararlı kalması şartı",
        "engagement.ENGAGE_RANGE_M: füze 5–15 m, helikopter 5–15 m, İHA 0–15 m, uçak "
        "10–15 m; balon reddediliyor (balon_not_engageable). Aşama-3'te menzilde "
        "kesintisiz 1.0 s kararlılık (engage_stable_s) şartı var; aralıktan çıkınca "
        "sayaç sıfırlanıyor. shared/engagement.py aynı tabloyu taşıyor, "
        "tests/test_contract.py sapmayı yakalıyor.",
        "tam",
    ),
    (
        "4.2.2.7",
        "Manuel modda taret yönelimi operatör tarafından doğrudan kontrol edilir; "
        "otonom modda PID devrede",
        "Ayrım üç katmanda: arayüz yalnız MANUEL kipte klavye girdisini kabul ediyor, "
        "_on_servo_command modu yeniden denetliyor, RPi manuel artımı işlerken PID'i "
        "sıfırlıyor ve Aşama-1'de PID'i hiç çalıştırmıyor.",
        "tam",
    ),
    (
        "4.2.2.8",
        "STM32F411, 50 Hz PWM ile pan-tilt servoları sürer; GPIO çıkışıyla MOSFET "
        "sürücüyü tetikler",
        "servo.c: TIM3 CH1/CH2 (PA6/PA7), PSC 83 → 1 MHz, ARR 19999 → tam 50 Hz; "
        "darbe 500–2500 µs açıya lineer. trigger.c ateşleme darbesini yönetiyor ve "
        "Trigger_IsBusy() ile tekrar tetiklemeyi kilitliyor. Önceki sürümde yalnızca "
        "yorum satırında olan 50 Hz iddiası artık kodda doğrulanabiliyor.",
        "tam",
    ),
    (
        "4.2.2.8 / 4.3",
        "STM32 ateşleme sonrası RPi'ye geri bildirir → RPi bunu TCP soketiyle YKİ'ye "
        "aktarır → PC imha değerlendirme sürecini başlatır (kapalı çevrim)",
        "Zincirin üç halkası da tamam: STM32 uplink telemetrisinde fired biti, RPi "
        "status.stm.fired olarak yayınlıyor, arayüz bayrağın yükselen kenarını "
        "yakalayıp VisionWorker.notify_fired() ile imha değerlendirmesini başlatıyor. "
        "Kenar yakalama şart: bayrak seviye olarak her 200 ms'de yeniden geliyor.",
        "tam",
    ),
    (
        "4.3",
        "LiDAR ölçümleri \"TCP/IP tabanlı telemetri kanalı üzerinden YKİ'ye eş zamanlı "
        "olarak iletilmektedir\"",
        "status satırı 200 ms'de bir lidar_m, range_ok, range_reason ve engage_range_m "
        "taşıyor. Arayüz mesafeyi ve menzil bantlarını bundan besliyor; menzil kapısı "
        "kapalıysa gerekçe göreve loglanıyor.",
        "tam",
    ),
    (
        "4.3",
        "RPi → STM32 binary frame protokolü, toplam 7 byte: senkronizasyon + pan + "
        "tilt + ateşleme kontrol alanı + checksum",
        "protocol.py birebir uyguluyor: SYNC 0xAA (uplink 0x55) + int16 pan_cdeg + "
        "int16 tilt_cdeg + bayrak baytı + XOR checksum = 7 byte. Bayraklar FIRE/ARM/"
        "HEARTBEAT/HOME/SAFE/ENABLE ve iki bitlik aşama. Önceki ASCII satır protokolü "
        "(~20 byte) tamamen kaldırıldı.",
        "tam",
    ),
    (
        "4.3",
        "Haberleşme kesintilerinde güvenli duruş",
        "Rapor bunu yalnızca genel ifadeyle anıyor; kod daha ileri: her çerçevede "
        "HEARTBEAT biti gidiyor, STM32 200 ms içinde çerçeve görmezse failsafe'e "
        "geçip servoyu tutuyor, tetiği kilitliyor ve durumu uplink'te bildiriyor. "
        "Arayüz bu bayrağı görünce sistemi GÜVENLİ DURUŞ'a alıyor.",
        "ek",
    ),
    (
        "4.1.1 / 4.2.2.8",
        "Taret yatay eksende 270°, düşey eksende 180° hareket eder; bunun için 270° "
        "dönüşlü motor versiyonu tercih edilmiştir",
        "rpi5 Limits pan 0–180, stm32f411 SERVO_PAN_MAX_CDEG 1800 (=180.0°). Yatay "
        "eksen yazılımda 180° ile sınırlı. 270°'ye geçiş üç yerde birlikte değişmeli "
        "(RPi kenetleme, STM32 sınırları + darbe haritası, shared/engagement.py); "
        "test_servo_range_matches_stm32 üçünü kilitliyor.",
        "aykiri",
    ),
    (
        "4.2.2.7 / 4.3",
        "TF02-PRO LiDAR, UART üzerinden RPi'ye bağlı, yaklaşık 100 Hz mesafe ölçümü",
        "lidar_tf02.py Benewake 9 baytlık çerçeveyi checksum'la doğruluyor. İki eksik: "
        "sinyal gücü (strength) okunuyor ama zayıf ölçümler elenmiyor, ve okumanın "
        "zaman damgası yok — seri hat düşerse lidar.last son değerde donar ve Aşama-3 "
        "kapısı bayat mesafeyle açılabilir.",
        "kismi",
    ),
    (
        "4.3 / 4.4.2",
        "Manuel modda operatör angajman kararını kendisi verir; Ateş butonu "
        "tetiklemeyi başlatır",
        "engage mesajı arm+fire niyeti olarak yorumlanıyor ve Aşama-1'de kilit/menzil "
        "koşulu aranmadığı için manuel ateş RPi'de tamamlanıyor. Ateş çerçevesi UART'a "
        "gerçekten yazıldıysa engage temizleniyor — yazılamadıysa talep düşmüyor.",
        "tam",
    ),
]

ARAYUZ = [
    (
        "4.3 Şekil 4.11",
        "Dört ana bölge: solda Sistem Durumu, ortada canlı video, sağda Kontrol "
        "Paneli, altta Görev Logu. Koyu tema, neon vurgulu göstergeler",
        "main_window.py birebir bu yerleşimi kuruyor: 280 px sol durum paneli, esneyen "
        "orta video, 310 px sağ kontrol paneli, alt log paneli. Ek olarak açılışta bir "
        "splash sayfası var.",
        "tam",
    ),
    (
        "4.3",
        "Bağlantı bölümünde TCP Kontrol ve UDP Video kanallarının çevrimiçi/çevrimdışı "
        "durumu LED benzeri göstergelerle",
        "İki StatusIndicator; RpiLinkWorker ve GStreamerVideoWorker sinyalleriyle "
        "gerçek zamanlı güncelleniyor.",
        "tam",
    ),
    (
        "4.3",
        "Hedef Bilgisi alanında tespit edilen hedefin varlığı, türü ve dost-düşman "
        "durumu",
        "target_label + target_type_label + iff_badge üçlüsü, boru hattının seçtiği "
        "angajman adayından besleniyor. Panel yalnız bilgi değiştiğinde yeniden "
        "çiziliyor (30 fps'te çift render'ı önlemek için).",
        "tam",
    ),
    (
        "4.3",
        "Menzil Durumu alanında hedefin mesafesi ile 5 m, 10 m ve 15 m menzil bantları",
        "DÜZELTİLDİ: panel artık gerçek veriyle besleniyor. RPi5 mesafeyi metre "
        "cinsinden lidar_m ile gönderiyor, arayüz ise santimetrelik distance_cm "
        "bekliyordu — iki şema protocol.normalize_telemetry() ile tek kanonik alana "
        "indirildi. Ölçüm gelmediğinde alan hiç yazılmıyor: \"veri yok\" ile \"0 m\" "
        "karışmıyor.",
        "tam",
    ),
    (
        "4.3",
        "Video: RPi'den UDP üzerinden RTP/JPEG akışı, GStreamer ile RTP→JPEG, OpenCV "
        "decode, QLabel'de gösterim; altta kare hızı ve çözünürlük",
        "gstreamer_video_worker.py gst-launch-1.0 udpsrc port=5000 ! rtpjpegdepay ! "
        "fdsink alt sürecini kullanıyor, stdout'tan JPEG SOI/EOI çiftleriyle kare "
        "ayrıştırıyor. FPS ve çözünürlük alt şeritte.",
        "tam",
    ),
    (
        "4.3",
        "Sistem grubunda Başlat, Durdur, Reset butonları; sistemi başlatır, durdurur, "
        "başlangıç durumuna döndürür",
        "Üçü de worker yaşam döngüsüne bağlı. Reset ayrıca hedef verisini, angajman "
        "kaydını, raporlanmış iz kimliklerini, kritik bölge uyarısını ve güvenli duruş "
        "kilidini temizliyor.",
        "tam",
    ),
    (
        "4.3",
        "Çalışma Modu grubunda Manuel / Otonom, otonom altında yarışma aşaması seçimi",
        "MANUEL | OTONOM, otonomda 2. AŞAMA | 3. AŞAMA. Seçim artık RPi'ye de gidiyor: "
        "mode mesajına stage alanı eklendi (MANUEL→1, 2. AŞAMA→2, 3. AŞAMA→3). Önceden "
        "aşama yalnız PC'deki IFF'i etkiliyordu ve RPi kendi kendine 2'de kalıyordu, "
        "yani Aşama-3 LiDAR menzil kapısına hiç ulaşılamıyordu. Not: KTR 4.3 aşamaları "
        "\"1./2. Aşama\", IFF bölümü 4.2.2.3 \"Aşama-2/Aşama-3\" diyor — rapor kendi "
        "içinde tutarsız; kod IFF bölümünü izliyor çünkü numara doğrudan IFF "
        "davranışını seçiyor.",
        "tam",
    ),
    (
        "4.3",
        "Azimuth (−180°…+180°) ve Elevation (−90°…+90°) göstergeleri; \"mevcut sürümde "
        "yalnızca durum izleme amacıyla kullanılmakta olup doğrudan komut girişine "
        "izin vermemektedir\"",
        "DÜZELTİLDİ: kaydırıcılar artık fare veya sekme ile sürüklenemiyor "
        "(WA_TransparentForMouseEvents + NoFocus), yani rapordaki kısıt birebir "
        "uygulanıyor. Değer iki kaynaktan geliyor: klavye girdisi ve RPi telemetrisi "
        "(pan_deg/tilt_deg). Telemetri, son klavye komutundan 1 sn sonra söz sahibi "
        "oluyor — RPi açıyı kenetlediyse gösterge gerçeğe oturuyor, tuşa basarken geri "
        "zıplamıyor.",
        "tam",
    ),
    (
        "4.3",
        "\"Sistemin PID katsayıları operatöre görüntülenmekte olup ayrı olarak "
        "ayarlanabilmektedir.\"",
        "DÜZELTİLDİ: kutular RPi5'in gerçek açılış katsayılarını gösteriyor "
        "(0.550 / 0.050 / 0.080), üç ondalık ve 0.005 adımla ayarlanıyor. Değişiklik "
        "type=pid JSON mesajı olarak RpiChannel üzerinden hatta çıkıyor, RPi "
        "PID.set_gains() ile uyguluyor (integral sıfırlanıyor ki eski birikim "
        "sıçrama üretmesin) ve uygulanan katsayıları telemetride geri bildiriyor. "
        "Bağlantı kurulduğunda panel değerleri RPi'ye basılıyor.",
        "tam",
    ),
    (
        "4.3.2 / 4.4.2",
        "\"MANUEL modda servo hareketleri operatör tarafından KLAVYE KOMUTLARIYLA "
        "doğrudan kontrol edilir\"",
        "DÜZELTİLDİ: ok tuşları ve WASD, 5° gösterge adımı (pan'de 2.5°), Shift ile 1 "
        "birim ince ayar. Uygulama seviyesindeki eventFilter sayesinde odak bir "
        "düğmedeyken de çalışıyor; P/I/D kutusu odaklıyken ok tuşu tareti değil "
        "katsayıyı değiştiriyor. Otonom kipte ve güvenli duruşta klavye girdisi kapalı.",
        "tam",
    ),
    (
        "4.3",
        "Ateş Kontrolü: iki adımlı güvenlik. \"Kilidi Aç\" ilk aşama, yalnız sonra "
        "\"Ateş\" etkinleşir; Ateş varsayılan olarak kilitli",
        "btn_unlock işaretlenebilir, btn_fire başta pasif, ateşten sonra kilit "
        "otomatik kapanıyor. Kilit otonom angajmanı da kapılıyor: _maybe_auto_engage "
        "is_fire_unlocked şartına bağlı, yani insan onayı otonom modda da devrede.",
        "tam",
    ),
    (
        "Bölüm 6",
        "Çok katmanlı emniyet: \"manuel dışı modlarda doğrudan girişleri pasifleştiren "
        "mod kilidi\" ve güvenli duruş",
        "DÜZELTİLDİ: güvenli duruşta açık kalmış ateş kilidi kapatılıyor, KİLİDİ AÇ ve "
        "ATEŞ düğmeleri pasifleşiyor, düğme \"GÜVENLİ DURUŞ\" yazıyor ve _on_fire_command "
        "komutu reddedip log'a hata basıyor. Önceden _emergency_active bayrağı hiç "
        "açılmıyordu; güvenli duruşta ateş düğmesi tıklanabilir kalıyordu.",
        "tam",
    ),
    (
        "4.3",
        "Görev Logu zaman damgalı [INFO] / [UYARI] / [HATA] kayıtları; TCP yokken "
        "verilen ateş komutu engellenip log'a hata olarak düşer; Temizle butonu",
        "log_panel.py 13 farklı olay tipi sunuyor (hedef tespit/kayıp, dost/düşman, "
        "menzil, angajman sonucu…). Ateş komutu RPi bağlantısı yokken, dost hedefte ve "
        "güvenli duruşta reddedilip gerekçesiyle loglanıyor. Menzil kapısının kapalı "
        "olma gerekçesi (range_reason) de artık log'a düşüyor.",
        "tam",
    ),
    (
        "4.2.2 / 4.3 / 7",
        "Arayüz donmasını önlemek için QThread tabanlı worker mimarisi ve "
        "\"latest-only\" tek yuvalı kuyruk",
        "Ortak BaseWorker üzerine GStreamerVideoWorker, VisionWorker, RpiLinkWorker, "
        "TargetSimulator. Latest-only kuyruk VisionWorker'da: yeni kare gelince "
        "bekleyen eski kare atılıyor.",
        "tam",
    ),
    (
        "4.2.2.9 / 4.3",
        "Ateşleme onayı arayüze ulaşır ve imha değerlendirmesi bu andan başlar",
        "DÜZELTİLDİ: status.stm.fired bayrağının yükselen kenarı yakalanıp "
        "VisionWorker.notify_fired() çağrılıyor. Seviye bayrağını olay sanmak, tek "
        "ateşlemenin 200 ms'de bir yeniden loglanması ve imha sayacının sürekli "
        "sıfırlanması demekti.",
        "tam",
    ),
]

GUVENLIK = [
    (
        "4.4.2",
        "Sistem sonlu durum makinesi: IDLE, SCANNING, DETECT, TRACK, EVALUATE, "
        "TARGET_LOCK, ENGAGEMENT, DESTROYED, LOST, FAIL_SAFE",
        "pc/integration/system_state.py onunu da tanımlıyor, durum paneli Türkçe "
        "karşılıklarıyla gösteriyor. Bu katman iki depoda da yoktu; entegrasyon "
        "kapsamında yazıldı. Hedef başına döngüden ayrı tutuldu: operatöre gösterilen "
        "sistem durumu ile tek bir hedefin durumu farklı şeyler.",
        "ek",
    ),
    (
        "4.4.2",
        "\"Kritik yazılım hataları, haberleşme kesintileri, güvenlik ihlalleri veya "
        "acil durdurma komutları sistemin herhangi bir durumdan FAIL_SAFE durumuna "
        "geçmesine neden olur\"",
        "on_fail_safe her durumdan geçişi destekliyor, çıkış yalnız BAŞLAT/RESET ile. "
        "Tetikleyicilerden üçü bağlı: model yüklenemedi, boru hattı durum ihlali ve "
        "artık gerçekten gelen RPi failsafe bayrağı (STM32 heartbeat zaman aşımı bu "
        "yolla arayüze ulaşıyor). Eksik kalan: PC↔RPi TCP kopması hâlâ yalnız LED'i "
        "düşürüyor, FAIL_SAFE'e sokmuyor.",
        "kismi",
    ),
    (
        "Bölüm 6",
        "\"Atış kararı doğrudan tek bir kareye bağlı olarak verilmemekte, belirli bir "
        "zaman penceresi boyunca yapılan izleme süreci üzerinden değerlendirilmektedir\"",
        "Dört zaman penceresi üst üste biniyor: IFF 15 karelik geçmişte ≥5 tutarlı "
        "kare, hedef kilidi 10 ardışık kararlı kare, RPi'de 1.0 s kesintisiz menzil "
        "kararlılığı ve 0.35° merkezleme toleransı. Vaat edilenden daha katı.",
        "tam",
    ),
    (
        "Bölüm 6",
        "Acil durdurma, yazılım çöküşünde dahi korunması için donanımsal fail-safe "
        "(Schneider XB5-AS8442 mantar buton) ile sağlanır",
        "Donanım kapsamında; yazılımda karşılığı olmaması doğru tasarım. Yazılım "
        "tarafında ikinci bir katman da var: RPi estop mesajını alınca SAFE bayraklı "
        "çerçeve gönderiyor, STM32 servoyu tutup tetiği kilitliyor. Arayüzde ayrı bir "
        "\"acil durdur\" düğmesi yok — operatörün fiziksel butona uzanması gerekiyor.",
        "kismi",
    ),
    (
        "5.1.1 Test 4",
        "\"Raspberry Pi'ye bağlı kamera üzerinden arayüze gerçek zamanlı görüntü "
        "aktarımı sağlanmış ve görüntü işleme algoritmaları bu veri akışı üzerinde "
        "test edilmiştir\"",
        "Uçtan uca akış tools/rpi_simulator.py ile deneniyor; simülatör artık gerçek "
        "RPi5 şemasını (lidar_m, pan_deg/tilt_deg, stm bayrakları) yayıyor, pid ve "
        "stage mesajlarını işliyor. Gerçek RPi donanımıyla saha testi henüz yapılmadı "
        "— rapor bunu geçmiş zamanla anlatıyor.",
        "kismi",
    ),
    (
        "Bölüm 5",
        "Sistem performansının ölçülmesi (kare hızı, gecikme, tespit başarımı)",
        "pc/performance_analysis/system_performance.py FPS, gecikme ve LiDAR yaşı "
        "ölçümlerini içeriyor ama hiçbir yerden çağrılmıyor; object_performance.py "
        "boş. Yazılmış ama bağlanmamış kod, ölçüm yapılmıyor demek.",
        "yok",
    ),
    (
        "Bölüm 9",
        "\"JSON ve checksum doğrulamalı binary paketleri birlikte kullanan hibrit "
        "haberleşme protokolü\"",
        "İddianın iki yarısı da artık doğru: PC ↔ RPi satır tabanlı JSON (iki yönlü), "
        "RPi ↔ STM32 checksum'lu 7 baytlık binary çerçeve. Önceki sürümde ikinci yarı "
        "ASCII olduğu için \"binary\" iddiası karşılanmıyordu.",
        "tam",
    ),
    (
        "Bölüm 7 / 9",
        "PyGI bağlamasından kaynaklanan kararsızlık, GStreamer'ın gst-launch-1.0 alt "
        "süreci ve SOI/EOI çerçeve ayrıştırması ile çözüldü",
        "Tarif edilen çözümün aynısı kodda: PyGI bağımlılığı yok, alt süreç "
        "stdout'undan JPEG SOI/EOI ile kare ayıklanıyor, SOI bulunmadan biriken çöp "
        "için üst sınır konmuş.",
        "tam",
    ),
]

GROUPS = [
    ("Görüntü işleme boru hattı", "KTR 4.2.2.1 – 4.2.2.10 · pc/vision/", GORUNTU),
    ("Atış kontrol yazılımı", "KTR 4.2.2.7 – 4.2.2.8 ve 4.3 · rpi5/ + stm32f411/", ATIS),
    ("Yer Kontrol İstasyonu arayüzü", "KTR 4.3 Şekil 4.11 · pc/ui/ + pc/integration/", ARAYUZ),
    ("Mod, durum, güvenlik ve test", "KTR 4.4.2, Bölüm 5, 6, 9", GUVENLIK),
]

# ----------------------------------------------------------------------
# Bu turda kapatılan boşluklar (yalnızca YKİ arayüzü kapsamı)
# ----------------------------------------------------------------------

BU_TUR = [
    (
        "PID katsayıları artık gerçekten ayarlanabiliyor",
        "Kutular ekranda duruyordu ama hiçbir yere gitmiyordu. Sözleşmeye type=pid "
        "mesajı eklendi (shared/protocol.py), gönderimi RpiChannel yapıyor "
        "(pc/vision/ değişmedi), atış kontrol tarafı PID.set_gains() ile uyguluyor. "
        "Varsayılanlar RPi5'in gerçek açılış değerlerine çekildi: operatörün "
        "gördüğü sayı donanımdaki sayı.",
    ),
    (
        "MANUEL kipte klavye ile yönelim",
        "KTR 4.3.2'nin açıkça istediği klavye kontrolü eklendi (ok tuşları/WASD, "
        "Shift ince ayar). Aynı hamlede eksen kaydırıcıları rapordaki tanıma "
        "döndürüldü: doğrudan komut girişi kapalı, yalnızca gösterge.",
    ),
    (
        "Telemetri şeması uyuşmazlığı giderildi",
        "Arayüz distance_cm/in_forbidden_zone bekliyor, RPi5 lidar_m/pan_deg/stm "
        "gönderiyordu; mesafe paneli, ateş onayı ve güvenli duruş sessizce hiç "
        "çalışmıyordu. Çeviri protocol.normalize_telemetry() ile tek noktaya alındı, "
        "iki şema da destekleniyor.",
    ),
    (
        "Aşama seçimi donanıma iletiliyor",
        "mode mesajına stage alanı eklendi. Önceden RPi aşamayı kendisi tahmin ediyor "
        "ve 3'e hiç çıkmıyordu; yani arayüzdeki \"3. AŞAMA\" düğmesi LiDAR menzil "
        "kapısını açamıyordu.",
    ),
    (
        "Güvenli duruşta ateş yolu kapanıyor",
        "control_panel'in _emergency_active bayrağı hiç açılmıyordu. Artık güvenli "
        "duruşta ateş kilidi kapanıyor, düğmeler pasifleşiyor ve komut reddi log'a "
        "yazılıyor (KTR Bölüm 6).",
    ),
    (
        "Simülatör gerçeği temsil ediyor",
        "tools/rpi_simulator.py eski şemayı yayıyordu; arayüz onunla çalışıp gerçek "
        "donanımla çalışmıyordu — yani simülatör \"çalışıyor\" hissi veren bir "
        "yanlıştı. Artık RPi5'in status şemasını yayıyor, pid/stage mesajlarını "
        "işliyor.",
    ),
    (
        "Sözleşme testleri yeni ağaca taşındı",
        "rpi/ ve stm32/ silindiği için sapma testleri kırıktı. Testler artık "
        "rpi5/fire_control ve stm32f411/Core/Inc/servo.h değerlerini okuyor; ayrıca "
        "pid mesajının karşı tarafta işlendiğini ve telemetri çevirisini doğruluyor.",
    ),
]

KRITIK = [
    (
        "Yasak sektör kapısı yeniden yazımda düştü",
        "Önceki atış kontrol yazılımı üç yasaklı sektörü denetleyip komutu düşürüyordu; "
        "yeni rpi5/fire_control'de bu kod hiç yok, yalnızca 0–180 kenetlemesi var. "
        "KTR Bölüm 6 bu bölgelerde hem hareketin hem atışın engellenmesini vaat "
        "ediyor. Bu, güvenlik yönünde bir gerileme ve listenin en acil maddesi.",
    ),
    (
        "Modelde balon sınıfı yok",
        "best.pt 5 sınıflı ama balon yok; balon tespiti tamamen HSV'ye bağlı. "
        "hsv_detector'ın LiDAR+hareket tetikleyicisi de VisionPipeline'a bağlı "
        "olmadığı için ölü yol.",
    ),
    (
        "LiDAR PC'ye geliyor ama karara girmiyor",
        "Mesafe artık arayüze ulaşıyor. Fakat önceliklendirme (4.2.2.5) ve HSV "
        "tetikleyicisi mesafeyi görmüyor; bağlamak VisionPipeline imzasını "
        "değiştirmeyi gerektiriyor ve pc/vision/ dokunulmama kuralına takılıyor.",
    ),
    (
        "İmha üç koşulu örtüşüyor + test kırık",
        "miss_count birikiyor, DESTROY_MISS_FRAMES=15. cond_miss ≈ cond_track_ended, "
        "yani üç bağımsız koşul değil. test_destroy_requires_all_three_conditions "
        "kırık durumda.",
    ),
    (
        "LiDAR okuması bayatlayabiliyor",
        "lidar_tf02.py zaman damgası tutmuyor ve zayıf sinyalleri elemiyor. Seri hat "
        "düşerse son mesafe donar; Aşama-3 ateş kapısı bayat veriyle açılabilir.",
    ),
    (
        "Performans ölçümü bağlı değil",
        "system_performance.py yazılmış ama çağrılmıyor, object_performance.py boş. "
        "KTR Bölüm 5'in ölçüm vaatleri fiilen ölçülmüyor; yedek mod da bu yüzden "
        "otomatik devreye girmiyor.",
    ),
]

ONCELIK = [
    "rpi5/fire_control'e yasak sektör kapısı: hem açı komutu hem FLAG_FIRE engeli",
    "LiDAR okumasına zaman damgası + sinyal gücü eşiği; bayat veriyle ateş kapısını kapat",
    "PC↔RPi TCP kopmasını FAIL_SAFE tetikleyicisine bağla",
    "VisionPipeline'a lidar_distance_m girişi (HSV tetikleyicisi + önceliklendirme)",
    "evaluate_destroyed üç koşulu bağımsızlaştır + pc/vision testini güncelle",
    "PerformanceMonitor'ü boru hattına bağla; set_backup_mode()'u otomatik tetikle",
    "Modele balon sınıfı ekle ya da KTR metnini gerçekle hizala",
    "270° servo kararı: RPi + STM32 + shared birlikte, ya da KTR'yi 180° yap",
    "Arayüze yazılımsal acil durdurma düğmesi (estop mesajı zaten destekliyor)",
]

EKLENENLER = [
    ("shared/ sözleşme paketi",
     "Sınıf kimlikleri, kare geometrisi, portlar, angajman kuralları ve telemetri "
     "çevirisi tek yerde; yalnızca standart kütüphane."),
    ("Sözleşme sapma testleri",
     "pc/config.py, rpi5/fire_control ve stm32f411 ile shared/ arasındaki sapmayı "
     "yakalar; kapatılmamış boşluklar atlanan test olarak görünür kalır."),
    ("pc/integration/ köprü katmanı",
     "Arayüz ile vision arasındaki tüm uyarlama mantığı; pc/vision/ tek satır "
     "değişmeden kalabilsin diye."),
    ("class_map.py", "Model sınıf uzayını pc/config.py uzayına çevirir."),
    ("Sistem seviyesi durum makinesi", "KTR 4.4.2 on durumlu sistem FSM'i."),
    ("tools/rpi_simulator.py",
     "Donanımsız uçtan uca test; gerçek RPi5 şemasını ve shared/ protokolünü kullanır."),
    ("Otonom angajmanda operatör onayı",
     "Ateş kilidi otonom modda da zorunlu — rapordan muhafazakâr sapma."),
    ("Heartbeat + failsafe zinciri",
     "STM32 200 ms çerçeve görmezse güvenli duruşa geçer ve bunu arayüze kadar "
     "bildirir; rapor bunu ayrıntılandırmıyor."),
]


def register_fonts() -> None:
    regular, bold = _FONT_PATHS
    if not regular.is_file() or not bold.is_file():
        raise SystemExit(
            "DejaVu Sans bulunamadı. Kurulum: sudo apt install fonts-dejavu-core"
        )
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_story() -> list:
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleTR", parent=styles["Title"], fontName=FONT_BOLD,
                           fontSize=20, spaceAfter=8, alignment=TA_CENTER)
    h1 = ParagraphStyle("H1TR", parent=styles["Heading1"], fontName=FONT_BOLD,
                        fontSize=14, spaceBefore=14, spaceAfter=6,
                        textColor=colors.HexColor("#1f2328"))
    h2 = ParagraphStyle("H2TR", parent=styles["Heading2"], fontName=FONT_BOLD,
                        fontSize=11, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#1f2328"))
    body = ParagraphStyle("BodyTR", parent=styles["Normal"], fontName=FONT_REGULAR,
                          fontSize=9, leading=12, alignment=TA_JUSTIFY)
    small = ParagraphStyle("SmallTR", parent=body, fontSize=8, leading=10,
                           textColor=colors.HexColor("#57606a"))
    bullet = ParagraphStyle("BulletTR", parent=body, fontSize=9, leading=12,
                            leftIndent=12, bulletIndent=0)

    all_items = [item for _, _, items in GROUPS for item in items]
    counts = Counter(item[3] for item in all_items)

    story: list = []
    story.append(Paragraph("GÖKHİSAR — KTR Uygunluk Analizi", title))
    story.append(Paragraph(
        "Kritik Tasarım Raporu (KTR_RAPOR.pdf, 30 sayfa) ile gokhisar-kod monorepo "
        "kaynak kodunun karşılaştırması<br/>"
        f"<b>Analiz tarihi:</b> {date.today().strftime('%d.%m.%Y')} · "
        f"<b>Toplam madde:</b> {len(all_items)} · <b>Test:</b> {TEST_OZETI}<br/>"
        "<b>Bu sürümde:</b> Yer Kontrol İstasyonu arayüzündeki KTR uyumsuzlukları "
        "kapatıldı; atış kontrol yazılımı rpi5/fire_control + stm32f411 olarak "
        "yeniden yazıldığı için o bölüm baştan değerlendirildi.",
        small))
    story.append(Spacer(1, 6 * mm))

    summary_data = [[STATUS_TR[key], str(counts[key])]
                    for key in ("tam", "kismi", "yok", "aykiri", "ek")]
    summary_table = Table(summary_data, colWidths=[5.5 * cm, 2 * cm], hAlign="CENTER")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fa")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
        ("FONTNAME", (1, 0), (1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Bu turda kapatılan boşluklar (YKİ arayüzü)", h1))
    story.append(Paragraph(
        "Aşağıdaki maddelerin hepsi arayüz ve köprü katmanında yapıldı; "
        "pc/vision/ tek satır değişmedi. Karşı tarafta yalnızca eksik olan "
        "pid mesajı işleyicisi eklendi — o olmadan operatörün ayarı hiçbir şeye "
        "etki etmiyordu.", small))
    story.append(Spacer(1, 3 * mm))
    for baslik, aciklama in BU_TUR:
        story.append(Paragraph(f"<b>{esc(baslik)}</b>", h2))
        story.append(Paragraph(esc(aciklama), body))

    story.append(PageBreak())

    story.append(Paragraph("Önce konuşulması gerekenler", h1))
    story.append(Paragraph(
        "Kalan açık maddeler arayüz dışında: atış kontrol yazılımı, görüntü işleme "
        "boru hattı ve donanım kapsamında.", small))
    story.append(Spacer(1, 3 * mm))
    for baslik, aciklama in KRITIK:
        story.append(Paragraph(f"<b>{esc(baslik)}</b>", h2))
        story.append(Paragraph(esc(aciklama), body))
        story.append(Spacer(1, 2 * mm))

    story.append(PageBreak())

    cell_style = ParagraphStyle("Cell", parent=body, fontSize=7.5, leading=9.5,
                                alignment=TA_LEFT)
    ref_style = ParagraphStyle("RefCell", parent=cell_style, fontName=FONT_BOLD,
                               textColor=colors.HexColor("#57606a"))
    status_style = ParagraphStyle("StatusCell", parent=cell_style, fontName=FONT_BOLD,
                                  fontSize=7.5, alignment=TA_CENTER)

    for baslik, aciklama, items in GROUPS:
        story.append(Paragraph(esc(baslik), h1))
        story.append(Paragraph(esc(aciklama), small))
        story.append(Spacer(1, 3 * mm))

        rows = [[
            Paragraph("<b>KTR</b>", ref_style),
            Paragraph("<b>Raporda ne vaat edilmiş</b>", cell_style),
            Paragraph("<b>Kodda fiilen ne var</b>", cell_style),
            Paragraph("<b>Durum</b>", status_style),
        ]]
        row_status = []
        for ref, vaat, kod, st in items:
            color = STATUS_COLOR.get(st, colors.black)
            rows.append([
                Paragraph(esc(ref), ref_style),
                Paragraph(esc(vaat), cell_style),
                Paragraph(esc(kod), cell_style),
                Paragraph(f'<font color="{color.hexval()}">{esc(STATUS_TR[st])}</font>',
                          status_style),
            ])
            row_status.append(st)

        table = Table(rows, colWidths=[2.2 * cm, 5.8 * cm, 7.5 * cm, 2.5 * cm],
                      repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#24292f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        row_bg = {
            "tam": colors.HexColor("#dafbe1"),
            "kismi": colors.HexColor("#fff8c5"),
            "yok": colors.HexColor("#ffebe9"),
            "aykiri": colors.HexColor("#ffebe9"),
            "ek": colors.HexColor("#ddf4ff"),
        }
        for i, st in enumerate(row_status, start=1):
            style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                               row_bg.get(st, colors.white)))
        table.setStyle(TableStyle(style_cmds))
        story.append(table)
        story.append(Spacer(1, 6 * mm))

    story.append(PageBreak())
    story.append(Paragraph("Mimarideki fark", h1))
    story.append(Paragraph(
        "KTR donanım mimarisini dört birim (kamera+RPi, PC, STM32, ağ) olarak "
        "anlatıyor. Kod PC tarafında arayüz (pc/ui/) ile görüntü işlemeyi "
        "(pc/vision/) tek süreçte birleştiriyor; aralarında Qt sinyalleri var, kare "
        "serileştirme yok. pc/vision/ yukarı akıştan geldiği gibi duruyor — tek satır "
        "değişmedi. Atış kontrol tarafı ise rapor yazıldıktan sonra tümüyle yeniden "
        "yazıldı: RPi ↔ STM32 hattı artık raporun tarif ettiği 7 baytlık binary "
        "çerçeveyi kullanıyor.", body))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Raporda olmayıp entegrasyonla eklenenler", h1))
    for baslik, neden in EKLENENLER:
        story.append(Paragraph(f"<b>{esc(baslik)}</b>", h2))
        story.append(Paragraph(esc(neden), body))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Öncelikli boşluklar", h1))
    for i, madde in enumerate(ONCELIK, 1):
        story.append(Paragraph(f"{i}. {esc(madde)}", bullet))

    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=colors.HexColor("#d0d7de")))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "<b>Özet:</b> Yer Kontrol İstasyonu arayüzü artık KTR 4.3'ün tarif ettiği "
        "arayüzle örtüşüyor: PID ayarı, klavye ile manuel yönelim, izleme amaçlı "
        "eksen göstergeleri, mesafe/menzil paneli, aşama iletimi, ateşleme onayı ve "
        "güvenli duruş kilidi hattın iki ucunda da çalışıyor. Atış kontrol tarafındaki "
        "yeniden yazım raporun binary protokol, kapalı çevrim ve telemetri vaatlerini "
        "karşıladı; karşılığında yasak sektör kapısı kayboldu. Kalan boşlukların "
        "merkezi artık haberleşme değil, güvenlik kapıları ve LiDAR verisinin karar "
        "süreçlerine bağlanması.", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "Değerlendirme yalnız kaynak kod okumasına ve simülatörle yapılan uçtan uca "
        "denemelere dayanır. Mekanik/elektronik tasarım (KTR 4.1, 4.2.1) ve donanım "
        "testleri (5.2) kapsam dışıdır.", small))
    return story


def main() -> None:
    register_fonts()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="GÖKHİSAR KTR Uygunluk Analizi", author="GÖKHİSAR",
    )

    def footer(canvas, doc_obj):  # noqa: ARG001
        canvas.saveState()
        canvas.setFont(FONT_REGULAR, 8)
        canvas.setFillColor(colors.HexColor("#57606a"))
        canvas.drawString(1.5 * cm, 1 * cm, "GÖKHİSAR — KTR Uygunluk Analizi")
        canvas.drawRightString(A4[0] - 1.5 * cm, 1 * cm,
                               f"Sayfa {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(build_story(), onFirstPage=footer, onLaterPages=footer)
    print(f"PDF yazıldı: {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
