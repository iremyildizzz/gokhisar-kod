"""Pytest ortak kurulumu — depo kökünden tek komutla tüm testleri çalıştırır.

İki test paketi iki farklı import köküne göre yazılmış:

* ``pc/vision/tests/`` — yukarı akıştan gelen görüntü işleme testleri.
  ``import config`` ve ``from detection.yolo_detector import ...`` gibi düz
  isimler kullanıyorlar; ``cd pc`` yapılarak çalıştırılmak üzere yazılmışlardı.
* ``tests/`` — entegrasyon ve sözleşme testleri. ``from pc.integration import ...``
  ile depo kökünü kök kabul ediyorlar.

Görüntü işleme dosyalarına dokunmama kararı gereği o testlerin import satırları
değiştirilmedi. Kökleri `sys.path`e eklemek aynı sonucu bedelsiz veriyor;
kurulumun tek doğru kaynağı `pc/integration/bootstrap.py`, burada yalnızca o
çağrılıyor.
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pc.integration import bootstrap  # noqa: E402  (yol kurulumundan sonra)

bootstrap.install()

# Not: `pc/vision/tests/test_modules.py::test_pid_converges_toward_center`
# artık var olmayan `rpi/pid_controller.py`'yi içe aktarıyor. Atış kontrol
# yazılımı `rpi5/fire_control/` olarak yeniden yazıldı ve PID'i orada, farklı
# bir arayüzle duruyor (`pid.PID`). Eskiden bu dosyada `rpi/` dizinini sys.path'e
# ekleyen bir yama vardı; dizin silindiği için yama ölü koda dönüştü ve
# kaldırıldı.
#
# Test bilerek kırık bırakıldı: `pc/vision/` entegrasyon kapsamında
# değiştirilmiyor ve sahte bir `pid_controller` modülü uydurmak, testi
# "geçiyor" göstermek için gerçeği taklit etmek olurdu. Kırık test, görüntü
# işleme ekibine düşen gerçek bir işi işaret ediyor.
