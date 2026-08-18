"""
Modern Arayüz Stil Tanımlamaları - DÜZELTİLMİŞ
Glassmorphism & Gradient Tema - Layout sorunları giderildi
"""

from pc.ui.utils.config import UIConfig


class Colors:
    BG_PRIMARY = "#061824"
    BG_SECONDARY = "#071e38"
    BG_TERTIARY = "#1a233a"
    GLASS_BG = "rgba(26, 35, 58, 0.72)"
    GLASS_BORDER = "rgba(0, 212, 255, 0.12)"
    NEON_CYAN = "#00d4ff"
    NEON_GREEN = "#34d399"
    NEON_BLUE = "#00d4ff"
    NEON_PURPLE = "#7c5cff"
    NEON_PINK = "#db2777"
    NEON_RED = "#ff4d4d"
    NEON_ORANGE = "#fb923c"
    NEON_YELLOW = "#fbbf24"
    GRADIENT_START = "#071e38"
    GRADIENT_END = "#040e1a"
    TEXT_PRIMARY = "#e6eaf2"
    TEXT_SECONDARY = "#9aa4b2"
    TEXT_MUTED = "#6b7280"
    SUCCESS = "#10b981"
    WARNING = "#f59e0b"
    DANGER = "#ef4444"
    INFO = "#3b82f6"


class Styles:

    MAIN_WINDOW = f"""
        QMainWindow {{
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 {Colors.BG_PRIMARY},
                stop:0.6 {Colors.BG_SECONDARY},
                stop:1 {Colors.GRADIENT_END}
            );
        }}
        QWidget {{
            font-family: 'Arial', 'Segoe UI', sans-serif;
        }}
    """

    PANEL = f"""
        QFrame {{
            background-color: rgba(6, 20, 42, 0.97);
            border: 1px solid rgba(0, 212, 255, 0.12);
            border-radius: 14px;
        }}
    """

    VIDEO_DISPLAY = f"""
        QLabel {{
            background-color: #000000;
            border: 1px solid rgba(0, 212, 255, 0.45);
            border-radius: 10px;
            padding: 4px;
        }}
    """

    # ── STATUS LABELS ─────────────────────────────────────────────────────────
    # DÜZELTME: font-size küçültüldü (11px), padding azaltıldı (5px 8px),
    # setFixedHeight(28) ile uyumlu olması için
    STATUS_LABEL_OK = f"""
        QLabel {{
            background-color: rgba(52, 211, 153, 0.08);
            color: {Colors.TEXT_PRIMARY};
            font-size: 10px;
            font-weight: 600;
            padding: 4px 6px;
            border: 1px solid rgba(52, 211, 153, 0.25);
            border-radius: 5px;
        }}
    """

    STATUS_LABEL_WARNING = f"""
        QLabel {{
            background-color: rgba(255, 77, 77, 0.08);
            color: {Colors.TEXT_PRIMARY};
            font-size: 10px;
            font-weight: 600;
            padding: 4px 6px;
            border: 1px solid rgba(255, 77, 77, 0.25);
            border-radius: 5px;
        }}
    """

    STATUS_LABEL_CAUTION = f"""
        QLabel {{
            background-color: rgba(251, 146, 60, 0.08);
            color: {Colors.TEXT_PRIMARY};
            font-size: 10px;
            font-weight: 600;
            padding: 4px 6px;
            border: 1px solid rgba(251, 146, 60, 0.25);
            border-radius: 5px;
        }}
    """

    # ── BAŞLIKLAR ─────────────────────────────────────────────────────────────
    TITLE_LABEL = f"""
        QLabel {{
            color: {Colors.TEXT_PRIMARY};
            font-size: 14px;
            font-weight: 700;
        }}
    """

    SUBTITLE_LABEL = f"""
        QLabel {{
            color: {Colors.TEXT_SECONDARY};
            font-size: 11px;
            font-weight: 500;
        }}
    """

    # ── BUTONLAR ─────────────────────────────────────────────────────────────
    BUTTON_NORMAL = f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.04);
            color: {Colors.TEXT_PRIMARY};
            font-size: 13px;
            font-weight: 600;
            padding: 7px 10px;
            border: 1px solid rgba(0, 212, 255, 0.10);
            border-radius: 8px;
        }}
        QPushButton:hover {{
            background-color: rgba(0, 212, 255, 0.12);
            border-color: rgba(0, 212, 255, 0.35);
        }}
        QPushButton:pressed {{
            background-color: rgba(0, 212, 255, 0.18);
        }}
        QPushButton:disabled {{
            background-color: rgba(100, 116, 139, 0.1);
            color: {Colors.TEXT_MUTED};
            border-color: rgba(100, 116, 139, 0.2);
        }}
    """

    BUTTON_DANGER = f"""
        QPushButton {{
            background-color: rgba(239, 68, 68, 0.15);
            color: {Colors.NEON_RED};
            font-size: 15px;
            font-weight: 700;
            padding: 8px 10px;
            border: 2px solid rgba(239, 68, 68, 0.5);
            border-radius: 8px;
        }}
        QPushButton:hover {{
            background-color: rgba(239, 68, 68, 0.3);
            border-color: {Colors.NEON_RED};
        }}
        QPushButton:pressed {{
            background-color: rgba(239, 68, 68, 0.45);
        }}
        QPushButton:disabled {{
            background-color: rgba(239, 68, 68, 0.05);
            color: rgba(239, 68, 68, 0.3);
            border-color: rgba(239, 68, 68, 0.15);
        }}
    """

    BUTTON_SUCCESS = f"""
        QPushButton {{
            background-color: rgba(16, 185, 129, 0.15);
            color: {Colors.NEON_GREEN};
            font-size: 11px;
            font-weight: 600;
            padding: 5px 10px;
            border: 1px solid rgba(16, 185, 129, 0.4);
            border-radius: 8px;
        }}
        QPushButton:hover {{
            background-color: rgba(16, 185, 129, 0.3);
            border-color: {Colors.NEON_GREEN};
        }}
        QPushButton:pressed {{
            background-color: rgba(16, 185, 129, 0.4);
        }}
    """

    BUTTON_MODE = f"""
        QPushButton {{
            background-color: rgba(0, 212, 255, 0.12);
            color: {Colors.TEXT_PRIMARY};
            font-size: 12px;
            font-weight: 700;
            padding: 5px 4px;
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 5px;
            min-height: 26px;
        }}
        QPushButton:checked {{
            background-color: rgba(0, 212, 255, 0.22);
            border-color: rgba(0, 212, 255, 0.55);
            color: {Colors.NEON_BLUE};
        }}
        QPushButton:hover {{
            background-color: rgba(0, 212, 255, 0.12);
            border-color: rgba(79, 131, 255, 0.30);
        }}
    """

    # ── SLIDER ───────────────────────────────────────────────────────────────
    SLIDER = f"""
        QSlider::groove:horizontal {{
            border: none;
            height: 4px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 {Colors.NEON_CYAN},
                stop:1 {Colors.NEON_BLUE}
            );
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {Colors.NEON_CYAN};
            border: 2px solid {Colors.BG_PRIMARY};
            width: 12px;
            height: 12px;
            margin: -4px 0;
            border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {Colors.TEXT_PRIMARY};
            border-color: {Colors.NEON_CYAN};
        }}
    """

    # ── GROUP BOX ─────────────────────────────────────────────────────────────
    # DÜZELTME: margin-top artırıldı (GroupBox title için yer açıldı)
    GROUP_BOX = f"""
        QGroupBox {{
            color: {Colors.TEXT_SECONDARY};
            font-size: 10px;
            font-weight: 600;
            border: 1px solid rgba(0, 212, 255, 0.10);
            border-radius: 6px;
            margin-top: 12px;
            padding: 4px 3px 4px 3px;
            background-color: rgba(255, 255, 255, 0.02);
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            top: 0px;
            padding: 0 3px;
            color: {Colors.NEON_BLUE};
            font-size: 10px;
            font-weight: 700;
        }}
    """

    LOG_AREA = f"""
        QTextEdit {{
            background-color: rgba(0, 0, 0, 0.6);
            color: {Colors.TEXT_SECONDARY};
            font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 11px;
            border: 1px solid rgba(0, 212, 255, 0.18);
            border-radius: 8px;
            padding: 8px;
            selection-background-color: rgba(79, 131, 255, 0.25);
        }}
    """

    SPLITTER = f"""
        QSplitter {{ background: transparent; }}
        QSplitter::handle {{
            background-color: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
        }}
        QSplitter::handle:horizontal {{
            width: 4px; margin: 0 4px;
        }}
        QSplitter::handle:vertical {{
            height: 4px; margin: 4px 0;
        }}
        QSplitter::handle:hover {{
            background-color: rgba(0, 212, 255, 0.35);
        }}
    """

    SCROLLBAR = f"""
        QScrollBar:vertical {{
            background: transparent; width: 6px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(255, 255, 255, 0.15);
            min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: rgba(255, 255, 255, 0.25);
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """

    STATUS_BAR = f"""
        QStatusBar {{
            background-color: rgba(17, 24, 39, 0.9);
            color: {Colors.TEXT_SECONDARY};
            font-size: 11px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding: 4px 10px;
        }}
    """

    IFF_BADGE_FRIENDLY = f"""
        QLabel {{
            background-color: rgba(16, 185, 129, 0.2);
            color: #00DD88;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 6px;
            border: 1px solid rgba(0, 220, 136, 0.5);
            border-radius: 5px;
        }}
    """

    IFF_BADGE_HOSTILE = f"""
        QLabel {{
            background-color: rgba(239, 68, 68, 0.2);
            color: #FF4444;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 6px;
            border: 1px solid rgba(255, 68, 68, 0.5);
            border-radius: 5px;
        }}
    """

    TARGET_CLASS_LABEL = f"""
        QLabel {{
            color: {Colors.TEXT_PRIMARY};
            font-size: 11px;
            font-weight: 600;
            padding: 3px 6px;
            background-color: rgba(79, 131, 255, 0.1);
            border-radius: 4px;
        }}
    """
