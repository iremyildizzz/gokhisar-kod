#!/usr/bin/env bash
# KTR uygunluk PDF'ini üretir.
# Ubuntu'da `python` komutu yoksa bile çalışır; önce proje .venv'ini kullanır.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

run_py() {
    if [[ -x "$ROOT/.venv/bin/python" ]]; then
        exec "$ROOT/.venv/bin/python" "$ROOT/tools/generate_ktr_pdf.py" "$@"
    fi
    if command -v python3 >/dev/null 2>&1; then
        exec python3 "$ROOT/tools/generate_ktr_pdf.py" "$@"
    fi
    echo "Hata: python3 bulunamadı." >&2
    echo "Kurulum: sudo apt install python3 python3-venv" >&2
    exit 127
}

run_py "$@"
