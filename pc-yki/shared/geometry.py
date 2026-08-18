"""Kare geometrisi — PC'nin ürettiği koordinatların hangi uzayda olduğu.

PC, hedef merkezini piksel olarak gönderiyor; RPi bu pikseli görüntü merkezine
göre bir hataya çeviriyor (`PanTiltController.step`). İki taraf aynı kare
boyutunu varsaymazsa hata sabit bir kaymayla hesaplanır ve PID bunu asla
kapatamaz — çünkü sapma referansın kendisindedir, bir bozucu etki değildir.

Bu yüzden kare boyutu bir "ayar" değil, bir **sözleşme** maddesidir. Kamera
değişirse burası ve `pc/config.py` birlikte güncellenmelidir;
`tests/test_contract.py` ikisinin ayrıldığı anda kırılır.
"""

FRAME_WIDTH: int = 1280
FRAME_HEIGHT: int = 720
FRAME_CENTER: tuple[int, int] = (FRAME_WIDTH // 2, FRAME_HEIGHT // 2)


def center_error(cx: float, cy: float) -> tuple[float, float]:
    """Hedef merkezinin görüntü merkezine göre (x, y) hatası, piksel."""
    fx, fy = FRAME_CENTER
    return cx - fx, cy - fy
