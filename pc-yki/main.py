#!/usr/bin/env python3
"""GÖKHİSAR Yer Kontrol İstasyonu — giriş noktası.

Çalıştırma:
    python main.py                       # normal
    python main.py --rpi-host 10.0.0.7   # RPi adresini geçici olarak değiştir
    python main.py --udp-port 5000       # video portu
    python main.py --weights yol/best.pt # farklı model dosyası

Komut satırı argümanları ortam değişkenlerine yazılır; `pc.integration.
settings` tüm ayarları oradan okuduğu için bu, tek bir yerde geçersiz kılma
noktası sağlar ve hiçbir modülün argüman ayrıştırmasından haberi olmasına
gerek kalmaz.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Proje kökünü sys.path'e ekle ki `python main.py` başka bir dizinden
# çağrıldığında da `pc.*` ve `shared.*` bulunabilsin. Görüntü işleme
# köklerini ekleme işini bootstrap devralır.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GÖKHİSAR Yer Kontrol İstasyonu"
    )
    parser.add_argument("--rpi-host", help="Raspberry Pi IP adresi")
    parser.add_argument("--rpi-port", type=int, help="RPi TCP komut portu")
    parser.add_argument("--udp-port", type=int, help="UDP video portu")
    parser.add_argument("--weights", help="YOLO ağırlık dosyası yolu")
    parser.add_argument("--stage", type=int, choices=(2, 3),
                        help="Başlangıç IFF aşaması (2: tümü düşman, 3: renk)")
    parser.add_argument("--no-hsv-assist", action="store_true",
                        help="HSV balon tespitini kapat (yalnızca YOLO)")
    return parser.parse_args(argv)


def apply_overrides(args: argparse.Namespace) -> None:
    """Argümanları ortam değişkenlerine aktar."""
    mapping = {
        "GOKHISAR_RPI_HOST": args.rpi_host,
        "GOKHISAR_RPI_PORT": args.rpi_port,
        "GOKHISAR_UDP_VIDEO_PORT": args.udp_port,
        "GOKHISAR_YOLO_WEIGHTS": args.weights,
    }
    for key, value in mapping.items():
        if value is not None:
            os.environ[key] = str(value)
    if args.no_hsv_assist:
        os.environ["GOKHISAR_HSV_ASSIST"] = "0"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    apply_overrides(args)

    from PySide6.QtWidgets import QApplication

    from pc.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("GOKHISAR")

    window = MainWindow()
    if args.stage is not None:
        window.control_panel.set_mode(f"ASAMA_{args.stage}")
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
