"""Görüntü işleme modüllerinin içe aktarılabilmesi için sys.path kurulumu.

Neden gerekli
-------------
`pc/vision/` altındaki dosyalar yukarı akış (senadubus) deposundan geliyor ve
kendi aralarında **düz** isimlerle konuşuyorlar::

    import config
    from detection.yolo_detector import Detection
    from tracking.tracker import TrackedTarget

Bu satırların çalışması için `pc/config.py`'nin bulunduğu dizin ile paket
dizinlerinin bulunduğu dizinin `sys.path` üzerinde olması gerekir. Alternatif,
bu 23 satırı `pc.vision.detection...` biçimine çevirmek olurdu — ama o zaman
görüntü işleme kodunu değiştirmiş olurduk ve yukarı akıştan gelecek her commit
çakışırdı. Yol kurulumu, o değişikliğin bedelini ödemeden aynı sonucu veriyor.

Kurulan kökler
--------------
``PROJECT_ROOT``
    ``pc``, ``shared``, ``tools`` paketleri buradan görünür.
``PC_ROOT`` (``pc/``)
    ``import config`` buradan çözülür — diyagramdaki ``pc/config.py``.
``VISION_ROOT`` (``pc/vision/``)
    ``detection``, ``tracking``, ``iff``, ``lifecycle``, ``validation``,
    ``evaluation``, ``comms`` buradan çözülür.

Dikkat: `pc/` yolda olduğu için `pc/ui` hem ``pc.ui`` hem ``ui`` olarak
çözülebilir. Aynı modülün iki ayrı kimlikle yüklenip iki ayrı duruma sahip
olmaması için proje genelinde **daima ``pc.`` önekli biçim** kullanılır; düz
biçim yalnızca görüntü işleme kodunun kendi iç importlarına aittir.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PC_ROOT = PROJECT_ROOT / "pc"
VISION_ROOT = PC_ROOT / "vision"
MODELS_DIR = PROJECT_ROOT / "models"


def install() -> None:
    """Gerekli kökleri `sys.path`e ekle. Birden fazla çağrı zararsızdır."""
    for directory in (PROJECT_ROOT, PC_ROOT, VISION_ROOT):
        entry = str(directory)
        if entry not in sys.path:
            sys.path.insert(0, entry)
