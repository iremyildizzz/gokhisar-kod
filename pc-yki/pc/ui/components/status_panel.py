from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Slot


def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("background: rgba(0,212,255,0.2); border: none;")
    f.setFixedHeight(1)
    return f


def _sec(text):
    l = QLabel(text)
    l.setStyleSheet(
        "font-family: Arial; font-size: 13px; font-weight: 700; "
        "color: #00d4ff; background: transparent; padding: 3px 0px;"
    )
    l.setFixedHeight(22)
    return l


S_OK   = ("background: rgba(52,211,153,0.1); color: #e6eaf2; "
          "font-size: 13px; font-weight: 600; padding: 6px 10px; "
          "border: 1px solid rgba(52,211,153,0.4); border-radius: 6px;")
S_WARN = ("background: rgba(255,77,77,0.1); color: #e6eaf2; "
          "font-size: 13px; font-weight: 600; padding: 6px 10px; "
          "border: 1px solid rgba(255,77,77,0.4); border-radius: 6px;")
S_CAUT = ("background: rgba(251,146,60,0.1); color: #e6eaf2; "
          "font-size: 13px; font-weight: 600; padding: 6px 10px; "
          "border: 1px solid rgba(251,146,60,0.4); border-radius: 6px;")
IFF_F  = ("background: rgba(16,185,129,0.2); color: #00DD88; "
          "font-size: 13px; font-weight: 700; padding: 6px 10px; "
          "border: 1px solid rgba(0,220,136,0.5); border-radius: 6px;")
IFF_H  = ("background: rgba(239,68,68,0.2); color: #FF4444; "
          "font-size: 13px; font-weight: 700; padding: 6px 10px; "
          "border: 1px solid rgba(255,68,68,0.5); border-radius: 6px;")


class StatusIndicator(QFrame):
    def __init__(self, label_text, parent=None):
        super().__init__(parent)
        self._is_ok = False
        self.setFixedHeight(36)
        self.setStyleSheet(
            "QFrame { background: rgba(255,77,77,0.07); "
            "border: 1px solid rgba(255,77,77,0.25); border-radius: 6px; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(8)

        self._icon = QLabel("○")
        self._icon.setFixedWidth(16)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("font-size: 12px; color: #ff4d4d; background: transparent; border: none;")
        row.addWidget(self._icon)

        self._name = QLabel(label_text)
        self._name.setStyleSheet("font-family: Arial; font-size: 13px; font-weight: 600; color: #c8cdd6; background: transparent; border: none;")
        row.addWidget(self._name, stretch=1)

        self._status = QLabel("OFFLINE")
        self._status.setFixedWidth(58)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("font-size: 11px; font-weight: 700; color: #ff4d4d; background: transparent; border: none;")
        row.addWidget(self._status)

    def set_status(self, ok: bool):
        self._is_ok = ok
        if ok:
            self._icon.setText("●")
            self._icon.setStyleSheet("font-size: 12px; color: #34d399; background: transparent; border: none;")
            self._status.setText("ONLINE")
            self._status.setStyleSheet("font-size: 11px; font-weight: 700; color: #34d399; background: transparent; border: none;")
            self.setStyleSheet(
                "QFrame { background: rgba(52,211,153,0.07); "
                "border: 1px solid rgba(52,211,153,0.3); border-radius: 6px; }"
            )
        else:
            self._icon.setText("○")
            self._icon.setStyleSheet("font-size: 12px; color: #ff4d4d; background: transparent; border: none;")
            self._status.setText("OFFLINE")
            self._status.setStyleSheet("font-size: 11px; font-weight: 700; color: #ff4d4d; background: transparent; border: none;")
            self.setStyleSheet(
                "QFrame { background: rgba(255,77,77,0.07); "
                "border: 1px solid rgba(255,77,77,0.25); border-radius: 6px; }"
            )


class StatusPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background: rgba(6,20,42,0.98); "
            "border: 1px solid rgba(0,212,255,0.15); border-radius: 14px; }"
        )
        self.setFixedWidth(272)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        L = QVBoxLayout(self)
        L.setSpacing(7)
        L.setContentsMargins(12, 14, 12, 14)

        # Baslik
        title = QLabel("SİSTEM DURUMU")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-family: Arial; font-size: 20px; font-weight: 800; color: #ffffff; "
            "background: transparent; padding: 6px 0px;"
        )
        L.addWidget(title)
        L.addWidget(_sep())

        # Bağlantı
        L.addWidget(_sec("Bağlantı"))
        self.tcp_indicator = StatusIndicator("TCP Kontrol")
        self.udp_indicator = StatusIndicator("UDP Video")
        L.addWidget(self.tcp_indicator)
        L.addWidget(self.udp_indicator)
        L.addWidget(_sep())

        # Çalışma Modu
        L.addWidget(_sec("Çalışma Modu"))
        self.mode_label = QLabel("MANUEL")
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setFixedHeight(34)
        self.mode_label.setStyleSheet(S_OK)
        L.addWidget(self.mode_label)

        # KTR 4.4.2'deki sistem durum makinesinin anlık hâli. Mod, operatörün
        # ne istediğini; bu etiket, sistemin fiilen ne yaptığını gösterir.
        # İkisi ayrı bilgidir: OTONOM modda olup hiçbir hedef görmemek
        # (SCANNING) ile hedefe kilitlenmiş olmak (TARGET_LOCK) çok farklı
        # durumlardır.
        self.state_label = QLabel("BEKLEMEDE")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setFixedHeight(30)
        self.state_label.setStyleSheet(S_CAUT)
        L.addWidget(self.state_label)
        L.addWidget(_sep())

        # Hedef Bilgisi
        L.addWidget(_sec("Hedef Bilgisi"))
        self.target_label = QLabel("HEDEF YOK")
        self.target_label.setAlignment(Qt.AlignCenter)
        self.target_label.setFixedHeight(34)
        self.target_label.setStyleSheet(S_CAUT)
        L.addWidget(self.target_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.target_icon = QLabel("●")
        self.target_icon.setStyleSheet("color: #00d4ff; font-size: 16px; background: transparent; border: none;")
        self.target_icon.setFixedWidth(20)
        self.target_icon.setAlignment(Qt.AlignCenter)
        row.addWidget(self.target_icon)
        self.target_type_label = QLabel("Tür: —")
        self.target_type_label.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #e6eaf2; "
            "background: rgba(0,212,255,0.08); padding: 5px 8px; border-radius: 5px;"
        )
        row.addWidget(self.target_type_label, stretch=1)
        L.addLayout(row)

        self.iff_badge = QLabel("BİLİNMİYOR")
        self.iff_badge.setAlignment(Qt.AlignCenter)
        self.iff_badge.setFixedHeight(34)
        self.iff_badge.setStyleSheet(S_CAUT)
        L.addWidget(self.iff_badge)
        L.addWidget(_sep())

        # Menzil
        L.addWidget(_sec("Menzil Durumu"))
        self.distance_label = QLabel("Mesafe: -")
        self.distance_label.setAlignment(Qt.AlignCenter)
        self.distance_label.setFixedHeight(36)
        self.distance_label.setStyleSheet(
            "color: #34d399; font-size: 15px; font-weight: bold; "
            "background: rgba(0,0,0,0.4); border: 1px solid rgba(52,211,153,0.2); "
            "border-radius: 6px; padding: 4px;"
        )
        L.addWidget(self.distance_label)

        bands = QHBoxLayout()
        bands.setSpacing(4)
        bs = ("color: #666; background: rgba(40,40,60,0.9); border: 1px solid #2a3a50; "
              "border-radius: 4px; font-size: 11px; font-weight: 600; padding: 3px 4px;")
        self.range_5m  = QLabel("5m")
        self.range_10m = QLabel("10m")
        self.range_15m = QLabel("15m")
        for lbl in [self.range_5m, self.range_10m, self.range_15m]:
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedHeight(22)
            lbl.setStyleSheet(bs)
            bands.addWidget(lbl)
        L.addLayout(bands)

        self.range_status_label = QLabel("Bekleniyor...")
        self.range_status_label.setAlignment(Qt.AlignCenter)
        self.range_status_label.setStyleSheet("color: #9aa4b2; font-size: 11px; background: transparent;")
        L.addWidget(self.range_status_label)

        self.critical_warning = QLabel("KRİTİK BÖLGE")
        self.critical_warning.setAlignment(Qt.AlignCenter)
        self.critical_warning.setStyleSheet(S_WARN)
        self.critical_warning.setVisible(False)
        L.addWidget(self.critical_warning)

    @Slot(str)
    def set_mode(self, mode):
        d = {"MANUEL": "MANUEL", "ASAMA_2": "2. AŞAMA", "ASAMA_3": "3. AŞAMA"}
        self.mode_label.setText(d.get(mode.upper(), mode.upper()))

    # Sistem durumlarının Türkçe karşılıkları ve önem seviyeleri. Renk,
    # durumun aciliyetini taşır: yeşil normal, turuncu dikkat, kırmızı kritik.
    _STATE_TEXT = {
        "IDLE": ("BEKLEMEDE", S_CAUT),
        "SCANNING": ("TARAMA", S_OK),
        "DETECT": ("TESPİT", S_OK),
        "TRACK": ("TAKİP", S_OK),
        "EVALUATE": ("DEĞERLENDİRME", S_CAUT),
        "TARGET_LOCK": ("HEDEF KİLİDİ", S_WARN),
        "ENGAGEMENT": ("ANGAJMAN", S_WARN),
        "DESTROYED": ("İMHA EDİLDİ", S_OK),
        "LOST": ("HEDEF KAYIP", S_CAUT),
        "FAIL_SAFE": ("GÜVENLİ DURUŞ", S_WARN),
    }

    @Slot(str)
    def set_system_state(self, state: str):
        text, style = self._STATE_TEXT.get(state.upper(), (state.upper(), S_CAUT))
        self.state_label.setText(text)
        self.state_label.setStyleSheet(style)

    @Slot(bool)
    def set_tcp_status(self, v): self.tcp_indicator.set_status(v)

    @Slot(bool)
    def set_udp_status(self, v): self.udp_indicator.set_status(v)

    @Slot(str, str)
    def set_target_info(self, status, target_type=""):
        self.target_label.setText(status)
        self.target_type_label.setText(f"Tur: {target_type}" if target_type else "Tür: —")
        icons = {"Balistik Fuze": "↑", "IHA": "✈", "Helikopter": "🚁", "Savas Ucagi": "✈", "Mini/Micro IHA": "⚡"}
        self.target_icon.setText(icons.get(target_type, "●"))
        if "DÜŞMAN" in target_type.upper(): self.target_label.setStyleSheet(S_WARN)
        elif "DOST" in target_type.upper(): self.target_label.setStyleSheet(S_OK)
        else: self.target_label.setStyleSheet(S_CAUT)

    @Slot(str, bool)
    def set_target_classification(self, cls, friendly):
        self.target_label.setText("HEDEF TESPİT")
        self.target_label.setStyleSheet(S_OK if friendly else S_WARN)
        self.target_type_label.setText(f"Tur: {cls}")
        self.iff_badge.setText("DOST" if friendly else "DÜŞMAN")
        self.iff_badge.setStyleSheet(IFF_F if friendly else IFF_H)

    @Slot()
    def clear_target_info(self):
        self.target_label.setText("HEDEF YOK")
        self.target_label.setStyleSheet(S_CAUT)
        self.target_type_label.setText("Tür: —")
        self.target_icon.setText("●")
        self.iff_badge.setText("BİLİNMİYOR")
        self.iff_badge.setStyleSheet(S_CAUT)

    @Slot(bool)
    def set_critical_zone_warning(self, v): self.critical_warning.setVisible(v)

    @Slot(float)
    def set_target_distance(self, d):
        self.distance_label.setText(f"Mesafe: {d:.1f} m")
        self._bands(d)
        if d <= 5:
            self.range_status_label.setText("YAKIN — Angajman")
            self.range_status_label.setStyleSheet("color: #ff4444; font-size: 11px; font-weight: bold; background: transparent;")
        elif d <= 10:
            self.range_status_label.setText("ORTA — Takipte")
            self.range_status_label.setStyleSheet("color: #ff8800; font-size: 11px; font-weight: bold; background: transparent;")
        elif d <= 15:
            self.range_status_label.setText("UZAK — İzleniyor")
            self.range_status_label.setStyleSheet("color: #ffcc00; font-size: 11px; background: transparent;")
        else:
            self.range_status_label.setText("Menzil Dışı")
            self.range_status_label.setStyleSheet("color: #555; font-size: 11px; background: transparent;")

    def _bands(self, d):
        i = "color: #666; background: rgba(40,40,60,0.9); border: 1px solid #2a3a50; border-radius: 4px; font-size: 11px; padding: 3px 4px;"
        r = "color: #fff; background: #bb2020; border: 2px solid #ff4444; border-radius: 4px; font-size: 11px; font-weight: 700; padding: 3px 4px;"
        o = "color: #fff; background: #994400; border: 2px solid #ff8800; border-radius: 4px; font-size: 11px; font-weight: 700; padding: 3px 4px;"
        y = "color: #111; background: #bb9900; border: 2px solid #ffcc00; border-radius: 4px; font-size: 11px; font-weight: 700; padding: 3px 4px;"
        self.range_5m.setStyleSheet(i)
        self.range_10m.setStyleSheet(i)
        self.range_15m.setStyleSheet(i)
        if d <= 5:   self.range_5m.setStyleSheet(r)
        elif d <= 10: self.range_10m.setStyleSheet(o)
        elif d <= 15: self.range_15m.setStyleSheet(y)

    @Slot()
    def clear_target_distance(self):
        self.distance_label.setText("Mesafe: -")
        self.distance_label.setStyleSheet(
            "color: #34d399; font-size: 15px; font-weight: bold; "
            "background: rgba(0,0,0,0.4); border: 1px solid rgba(52,211,153,0.2); "
            "border-radius: 6px; padding: 4px;"
        )
        self.range_status_label.setText("Bekleniyor...")
        self.range_status_label.setStyleSheet("color: #9aa4b2; font-size: 11px; background: transparent;")
        i = "color: #666; background: rgba(40,40,60,0.9); border: 1px solid #2a3a50; border-radius: 4px; font-size: 11px; padding: 3px 4px;"
        self.range_5m.setStyleSheet(i)
        self.range_10m.setStyleSheet(i)
        self.range_15m.setStyleSheet(i)
