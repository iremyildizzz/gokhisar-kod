"""Hedef sınıf kimlikleri — PC ile RPi'nin aynı numarayı aynı şeye demesi.

Sınıf kimliği makine sınırını `target` ve `engage` mesajlarında `class_id`
alanı olarak geçiyor. RPi bu numarayla `SAFE_ENGAGE_DISTANCES` tablosuna
bakıyor, yani yanlış bir numara yanlış mesafede ateş açılması demek.

Numaralar `pc/config.py`'deki `CLASS_NAMES` ile birebir aynıdır. Dikkat: bunlar
**sistem** kimlikleridir, eğitilmiş YOLO modelinin kendi kimlikleri değil. Model
şu an bambaşka bir sıralama kullanıyor (0=helikopter, 1=iha, 2=jet,
3=mini-micro-iha, 4=rocket) ve model çıktısı `pc/integration/class_map.py`
tarafından bu uzaya çevriliyor.
"""

from enum import IntEnum


class TargetClass(IntEnum):
    """Sistem genelinde geçerli hedef sınıfı kimlikleri."""

    FUZE = 0
    HELIKOPTER = 1
    IHA = 2
    UCAK = 3
    BALON = 4


#: Angajman edilebilir maket sınıfları. Balon bir hedef değil, hedefin
#: üzerindeki işarettir; maket-balon eşleştirmesinde kullanılır.
MODEL_CLASS_IDS: frozenset[int] = frozenset(
    {TargetClass.FUZE, TargetClass.HELIKOPTER, TargetClass.IHA, TargetClass.UCAK}
)

BALLOON_CLASS_ID: int = int(TargetClass.BALON)

#: Operatöre gösterilecek Türkçe adlar. Arayüz etiketleri ve görev logu bunu
#: kullanır; ham enum adları (`FUZE`) operatör için okunaklı değil.
DISPLAY_NAMES: dict[int, str] = {
    TargetClass.FUZE: "Balistik Füze",
    TargetClass.HELIKOPTER: "Helikopter",
    TargetClass.IHA: "İHA",
    TargetClass.UCAK: "Savaş Uçağı",
    TargetClass.BALON: "Balon",
}


def display_name(class_id: int) -> str:
    """Sınıf kimliğinin operatöre gösterilecek adı; bilinmeyende ham kimlik."""
    return DISPLAY_NAMES.get(class_id, f"Sınıf {class_id}")


def is_engageable(class_id: int) -> bool:
    """Bu sınıf angajman adayı olabilir mi (balon olamaz)."""
    return class_id in MODEL_CLASS_IDS
