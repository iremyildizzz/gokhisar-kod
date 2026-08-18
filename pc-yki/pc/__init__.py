"""PC'de çalışan tek uygulama: Yer Kontrol İstasyonu.

Bu dizin, yarışma sisteminin üç fiziksel düğümünden birine karşılık gelir
(diğerleri `rpi/` ve `stm32/`). İçinde üç sorumluluk var:

``ui/``
    Operatör arayüzü ve QThread worker'ları — pencere, paneller, video
    gösterimi, ağ ve görüntü işleme iş parçacıkları.
``vision/``
    Görüntü işleme boru hattı. Yukarı akış (senadubus) deposundan geldiği gibi
    duruyor; entegrasyon kapsamında **tek satırı değiştirilmedi**.
``integration/``
    İkisini birbirine bağlayan köprü katmanı. Ayrı durmasının sebebi, yukarı
    akıştan gelecek güncellemelerin bu koda hiç değmemesi.

`config.py` görüntü işleme tarafının eşik ve sabitlerini taşır; `pc/vision/`
altındaki dosyalar ona düz `import config` ile ulaşır.
"""
