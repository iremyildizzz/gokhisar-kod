"""Model sınıf kimlikleri ile `pc/config.py` sınıf uzayı arasındaki eşleme.

Neden gerekli?
--------------
Arayüz/backend deposundaki eğitilmiş ağırlık dosyası (`models/best.pt`) beş
sınıf içerir::

    0: helikopter   1: iha   2: jet   3: mini-micro-iha   4: rocket

Görüntü işleme reposunun `pc/config.py` dosyası ise bambaşka bir sıralama
bekler::

    0: fuze   1: helikopter   2: iha   3: ucak   4: balon
    BALLOON_CLASS_ID = 4      MODEL_CLASS_IDS = {0, 1, 2, 3}

İki uzay doğrudan bağlanırsa modelin "rocket" tespitleri balon sanılır;
`TargetMatcher` her roketi bir balonla eşleştirmeye çalışır, IFF ve angajman
zinciri baştan bozulur. `pc/config.py` değiştirilmeyeceği için dönüşüm burada,
tespit çıktısı boru hattına girmeden önce yapılır.

Eşleme, sınıf kimliklerine değil **adlara** bakar. Model yeniden eğitilip sınıf
sırası değişse bile adlar aynı kaldığı sürece eşleme kendiliğinden doğru kalır;
tanınmayan bir ad çıkarsa sessizce yanlış eşlenmek yerine raporlanır.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from pc.integration import bootstrap  # noqa: F401  (sys.path kurulumu)
from shared.classes import DISPLAY_NAMES, TargetClass

import config as vision_config

# Sistem sınıf kimlikleri. Sözleşme `shared/classes.py`de tanımlı; buradaki
# adlar yalnızca okunabilirlik için. `pc/config.py` ile aynı olduklarını
# `tests/test_contract.py` doğrular.
FUZE = int(TargetClass.FUZE)
HELIKOPTER = int(TargetClass.HELIKOPTER)
IHA = int(TargetClass.IHA)
UCAK = int(TargetClass.UCAK)
BALON = int(TargetClass.BALON)

# Normalize edilmiş model sınıf adı -> config sınıf kimliği.
_ALIASES: dict[str, int] = {
    # Füze / roket
    "fuze": FUZE, "roket": FUZE, "rocket": FUZE, "missile": FUZE,
    "balistik fuze": FUZE, "ballistic missile": FUZE, "cruise missile": FUZE,
    # Helikopter
    "helikopter": HELIKOPTER, "helicopter": HELIKOPTER, "heli": HELIKOPTER,
    # İHA (mini/micro İHA da operasyonel olarak İHA sınıfıdır)
    "iha": IHA, "uav": IHA, "drone": IHA, "dron": IHA,
    "mini micro iha": IHA, "minimicroiha": IHA,
    "quadcopter": IHA, "multirotor": IHA,
    # Uçak
    "ucak": UCAK, "jet": UCAK, "plane": UCAK, "aircraft": UCAK,
    "savas ucagi": UCAK, "fighter": UCAK, "warplane": UCAK,
    # Balon
    "balon": BALON, "balloon": BALON, "balloons": BALON,
}

# Model sınıf adına özel, operatöre gösterilecek ad. Burada olmayan adlar için
# config sınıfının genel adı kullanılır — böylece "mini-micro-iha" arayüzde
# yalnızca "İHA" diye görünmez, ayrımı korunur.
_DISPLAY_OVERRIDES: dict[str, str] = {
    "mini micro iha": "Mini/Micro İHA",
    "minimicroiha": "Mini/Micro İHA",
    "jet": "Savaş Uçağı (Jet)",
    "rocket": "Balistik Füze",
    "roket": "Balistik Füze",
}

# Sistem sınıf kimliği -> operatör arayüzünde gösterilecek ad. Sözleşmeden
# gelir; `_DISPLAY_OVERRIDES` yalnızca modelin daha ince ayrımlarını
# ("mini-micro-iha") korumak için bunun üstüne biner.
CONFIG_DISPLAY_NAMES: dict[int, str] = dict(DISPLAY_NAMES)


def normalize(name: str) -> str:
    """Sınıf adını eşleme anahtarına çevir.

    Türkçe karakterler ASCII karşılıklarına indirgenir, ayraçlar boşluğa
    dönüşür. "Savaş Uçağı", "savas_ucagi" ve "SAVAS-UCAGI" aynı anahtara düşer.
    """
    lowered = name.strip().lower()
    # Türkçe'ye özgü, unicode ayrıştırmasının ASCII'ye indirgeyemediği harfler.
    for src, dst in (("ı", "i"), ("ş", "s"), ("ğ", "g"),
                     ("ü", "u"), ("ö", "o"), ("ç", "c")):
        lowered = lowered.replace(src, dst)
    decomposed = unicodedata.normalize("NFKD", lowered)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    for separator in ("_", "-", "/", "\\", "."):
        ascii_only = ascii_only.replace(separator, " ")
    return " ".join(ascii_only.split())


@dataclass(frozen=True)
class MappedClass:
    """Bir model sınıfının boru hattı ve arayüz karşılıkları."""

    model_id: int
    model_name: str
    config_id: int
    display_name: str


class ClassMap:
    """Model sınıf uzayını `pc/config.py` sınıf uzayına çeviren adaptör."""

    def __init__(self, model_names: dict[int, str] | None = None):
        self._entries: dict[int, MappedClass] = {}
        self._unmapped: list[str] = []
        self._model_names: dict[int, str] = dict(model_names or {})

        if not self._model_names:
            # Model adları okunamadıysa (ör. henüz yüklenmemişse) config'in
            # kendi sıralamasını birebir kabul ederiz.
            self._model_names = dict(vision_config.CLASS_NAMES)

        for model_id, raw_name in self._model_names.items():
            key = normalize(str(raw_name))
            config_id = _ALIASES.get(key)
            if config_id is None:
                self._unmapped.append(str(raw_name))
                continue
            self._entries[int(model_id)] = MappedClass(
                model_id=int(model_id),
                model_name=str(raw_name),
                config_id=config_id,
                display_name=_DISPLAY_OVERRIDES.get(
                    key, CONFIG_DISPLAY_NAMES.get(config_id, str(raw_name))),
            )

    # ---------- sorgular ----------
    def to_config_id(self, model_class_id: int) -> int | None:
        """Model sınıf kimliğini config kimliğine çevir; eşlenemezse ``None``.

        ``None`` dönen tespitler boru hattına alınmaz. Sessizce 0'a düşürmek,
        tanınmayan bir nesnenin "füze" sanılmasına yol açardı.
        """
        entry = self._entries.get(int(model_class_id))
        return entry.config_id if entry else None

    def display_name(self, model_class_id: int) -> str:
        entry = self._entries.get(int(model_class_id))
        if entry:
            return entry.display_name
        return str(self._model_names.get(int(model_class_id), model_class_id))

    def display_name_for_config_id(self, config_id: int) -> str:
        for entry in self._entries.values():
            if entry.config_id == config_id:
                return entry.display_name
        return CONFIG_DISPLAY_NAMES.get(int(config_id), str(config_id))

    @property
    def has_balloon_class(self) -> bool:
        """Model balonu kendi başına tespit edebiliyor mu?

        Edemiyorsa balon adayları yalnızca HSV boru hattından gelir; bu, mevcut
        `best.pt` için geçerli durumdur.
        """
        return any(e.config_id == BALON for e in self._entries.values())

    @property
    def unmapped_names(self) -> list[str]:
        """Eşlenemeyen model sınıf adları — operatöre uyarı olarak gösterilir."""
        return list(self._unmapped)

    def describe(self) -> str:
        parts = [f"{e.model_id}:{e.model_name}->{e.config_id}"
                 for e in sorted(self._entries.values(), key=lambda x: x.model_id)]
        text = "Sınıf eşlemesi " + ", ".join(parts) if parts else "Sınıf eşlemesi boş"
        if self._unmapped:
            text += f" | eşlenemeyen: {', '.join(self._unmapped)}"
        if not self.has_balloon_class:
            text += " | balon sınıfı modelde yok, HSV tespitine düşülüyor"
        return text
