"""Arayüz ile görüntü işleme arasındaki köprü katmanı.

Bu paketteki hiçbir modül `pc/vision/`, `rpi/`, `stm32/` altındaki yukarı akış
kodunu değiştirmez; onları yalnızca dışarıdan sarmalar, uyarlar ve birbirine
bağlar. Görüntü işleme ekibinden yeni bir commit geldiğinde çakışma yaşamamak
için tüm uyarlama mantığı burada toplanmıştır.

Paket içe aktarılır aktarılmaz `bootstrap.install()` çağrılır; bu satır
olmadan `pc/vision/` altındaki düz import'lar (``import config``,
``from detection.yolo_detector import ...``) çözülemez.
"""

from pc.integration import bootstrap

bootstrap.install()
