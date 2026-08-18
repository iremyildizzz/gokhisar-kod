"""Angajman ve hareket güvenliği kuralları — KTR 4.2.2.7 ve 6. bölüm.

Buradaki değerler atış güvenliğini belirlediği için sözleşmenin en hassas
kısmı. RPi bunları angajman kapısı olarak, PC ise operatöre menzil durumu
göstermek ve simülatörü gerçeğe sadık tutmak için kullanır.
"""

#: Servo açı aralığı (derece). Hem RPi'nin PID kenetlemesi hem STM32'nin
#: darbe dönüşümü bu aralığı varsayar.
#:
#: KTR 4.1.1 yatay eksen için 270° vaat ediyor, ama `rpi5/fire_control/main.py`
#: içindeki `Limits` de `stm32f411`'in `SERVO_PAN_MAX_CDEG`'i de iki ekseni
#: 0-180'e kırpıyor. Sözleşme kodun gerçeğini yazar, raporun vaadini değil:
#: 270°'ye geçilecekse burası, RPi kenetlemesi ve STM32 darbe haritası
#: birlikte değişmelidir.
SERVO_MIN_ANGLE: float = 0.0
SERVO_MAX_ANGLE: float = 180.0
SERVO_CENTER_ANGLE: float = 90.0

#: Yasaklı açı bölgeleri: (pan_min, pan_max, tilt_min, tilt_max).
#: KTR Bölüm 6: bu bölgelerde hem namlu hareketi hem atış komutu engellenmeli.
#:
#: Uyarı: `rpi5/fire_control` bu kapıyı **henüz uygulamıyor** (yalnızca 0-180
#: kenetlemesi var). Sözleşme tanımı burada duruyor çünkü arayüzün "KRİTİK
#: BÖLGE" uyarısı ve simülatör bunu kullanıyor; atış kontrol tarafı kapıyı
#: eklediğinde aynı değerleri okuması yeterli.
FORBIDDEN_ZONES: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 20.0, 0.0, 180.0),      # sol güvenlik bölgesi
    (160.0, 180.0, 0.0, 180.0),   # sağ güvenlik bölgesi
    (0.0, 180.0, 150.0, 180.0),   # aşağı — operatör tarafı
)

#: Sınıf bazlı güvenli angajman mesafeleri, santimetre: class_id -> (min, max).
#: KTR 4.2.2.7: "Her hedef sınıfı için sistemde önceden tanımlanmış güvenli
#: angajman mesafeleri bulunmaktadır."
#:
#: Değerler `rpi5/fire_control/engagement.py`'deki `ENGAGE_RANGE_M` tablosunun
#: santimetre karşılığıdır. Ayrıştıkları anda operatör "menzilde" görürken
#: sistem ateş etmez (ya da tersi) — bu yüzden `tests/test_contract.py` iki
#: tabloyu karşılaştırıyor. Balon bilinçli olarak yok: balon bir hedef değil,
#: hedefin işaretidir ve atış kontrol onu `balon_not_engageable` ile reddeder.
SAFE_ENGAGE_DISTANCES_CM: dict[int, tuple[int, int]] = {
    0: (500, 1500),    # füze
    1: (500, 1500),    # helikopter
    2: (0, 1500),      # İHA
    3: (1000, 1500),   # savaş uçağı
}

#: Hedefin güvenli mesafede kesintisiz kalması gereken süre (saniye).
#: Tek bir LiDAR okumasına güvenip ateş etmemek için.
ENGAGE_STABLE_SECONDS: float = 1.0

#: Operatöre gösterilen menzil bantları (metre) — KTR 4.3 Sistem Durumu paneli.
RANGE_BANDS_M: tuple[int, ...] = (5, 10, 15)


def in_forbidden_zone(pan: float, tilt: float) -> bool:
    """Bu pan/tilt bileşimi yasaklı bir sektörde mi."""
    return any(
        p1 <= pan <= p2 and t1 <= tilt <= t2
        for p1, p2, t1, t2 in FORBIDDEN_ZONES
    )


def clamp_angle(angle: float) -> float:
    """Açıyı servo aralığına kenetle."""
    return max(SERVO_MIN_ANGLE, min(SERVO_MAX_ANGLE, angle))


def is_safe_distance(class_id: int, distance_cm: float | None) -> bool:
    """Bu sınıf için ölçülen mesafe güvenli angajman aralığında mı.

    Mesafe bilinmiyorsa (LiDAR okuyamadı) güvenli sayılmaz — bilgi yokluğunda
    ateş etmemek doğru varsayılan.
    """
    if distance_cm is None:
        return False
    limits = SAFE_ENGAGE_DISTANCES_CM.get(class_id)
    if limits is None:
        return False
    low, high = limits
    return low <= distance_cm <= high
