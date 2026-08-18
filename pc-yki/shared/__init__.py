"""Düğümler arası sözleşme: PC, Raspberry Pi ve STM32'nin üzerinde anlaştığı her şey.

Bu paket üç fiziksel düğümün ortak dili. İçindeki her değer, en az iki makinede
aynı olmak *zorunda* olan bir değerdir: sınıf kimlikleri, kare geometrisi, port
numaraları, mesaj alan adları, güvenli angajman mesafeleri, yasaklı açı
bölgeleri.

Neden ayrı bir paket?
---------------------
Bu değerler daha önce her düğümde bağımsız birer sabit olarak duruyordu.
En tehlikelisi kare geometrisiydi: ``pc/config.py`` 1280x720 diyordu, atış
kontrol yazılımı da ayrı bir literal olarak (bugün ``--frame-w/--frame-h``
varsayılanı) 1280x720 diyordu ve ikisi birbirini tanımıyordu. Kamera çözünürlüğü değişince PC koordinatları yeni
uzayda üretmeye başlar, RPi ise hatayı hâlâ eski merkeze göre hesaplardı.
Ortaya çıkan sabit nişan kaymasını PID **düzeltemez**, çünkü hata bir bozucu
etki değil, referansın kendisindeki bir sapmadır. Sözleşmeyi tek yerde
tanımlamak bu hata sınıfını yapısal olarak ortadan kaldırır.

Bağımlılık kuralı
-----------------
**Yalnızca standart kütüphane.** numpy, opencv, torch yok. Bu paket bir
Raspberry Pi'ye kopyalanacak; oraya derin öğrenme yığınını taşımak istemiyoruz.
``Detection`` gibi numpy'a bağımlı tipler bilerek dışarıda bırakılmıştır —
onlar PC'ye özgüdür, RPi hiçbir zaman bir ``Detection`` görmez, yalnızca
``{cx, cy, class_id, track_id, locked}`` alır.

Görüntü işleme deposuyla ilişki
-------------------------------
``pc/config.py``, ``rpi5/fire_control/`` ve ``stm32f411/`` yukarı akış
dosyalarıdır ve entegrasyon kapsamında değiştirilmemiştir; bu
yüzden şu an ``shared``'dan *okumuyorlar*, kendi literallerini taşımaya devam
ediyorlar. Sözleşmenin tek gerçek kaynağı yine de burasıdır:
``tests/test_contract.py`` her iki tarafın buradaki değerlerle birebir aynı
olduğunu doğrular. Yani sapma önlenemiyor ama **anında yakalanıyor**; görüntü
işleme ekibi bir gün ``from shared...`` satırlarını kabul ederse geçiş üç
satırlık bir değişiklik olur.
"""

from shared import classes, engagement, geometry, protocol

__all__ = ["classes", "engagement", "geometry", "protocol"]
