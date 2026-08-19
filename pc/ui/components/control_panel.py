"""
Kontrol Paneli - v4 compact
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QSlider, QButtonGroup, QSizePolicy, QDoubleSpinBox
)
from PySide6.QtCore import Qt, Signal, Slot
from pc.ui.styles import Styles

# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _sep():
    f = QFrame(); f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("background:rgba(255,255,255,0.07); border:none;")
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

def _pid_spin(val=0.0):
    sb = QDoubleSpinBox()
    # Operatör ekrandan ayarlar; adım 0.002 (eski 0.005 çok kaba geliyordu)
    sb.setRange(0.0, 10.0)
    sb.setSingleStep(0.002)
    sb.setValue(val)
    sb.setDecimals(3)
    sb.setFixedHeight(26)
    sb.setFocusPolicy(Qt.ClickFocus)  # WASD sürekli odak çalmasın
    sb.setStyleSheet("""
        QDoubleSpinBox {
            background:rgba(255,255,255,0.05); color:#00d4ff;
            font-size:11px; font-weight:700;
            border:1px solid rgba(0,212,255,0.3); border-radius:4px;
            padding:1px 4px;
        }
        QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{
            width:14px; background:rgba(0,212,255,0.15); border:none;
        }
    """)
    return sb

# ── Servo Widget ──────────────────────────────────────────────────────────────

_AXIS_LABEL_ACTIVE = ("color:#c8cdd6;font-size:11px;font-weight:600;"
                      "background:transparent;border:none;")
_AXIS_LABEL_PASSIVE = ("color:#6b7280;font-size:11px;font-weight:600;"
                       "background:transparent;border:none;")

# PID — preset: en_iyi_dikey (pan iyi + tilt yumuşak/frenli)
_DEFAULT_KP = 0.034
_DEFAULT_KI = 0.0
_DEFAULT_KD = 0.010
_PID_PRESET_NAME = "en_iyi_dikey"


class ServoControlWidget(QFrame):
    """Eksen göstergeleri + ayarlanabilir PID katsayıları (KTR 4.3).

    KTR: "Azimuth ve Elevation göstergeleri ... anlık açıları operatöre görsel
    olarak sunmaktadır. Mevcut sürümde bu göstergeler yalnızca durum izleme
    amacıyla kullanılmakta olup doğrudan komut girişine izin vermemektedir."
    Bu yüzden kaydırıcılar fare/klavye ile sürüklenemez: yönelim komutu MANUEL
    modda klavye ile, otonom modda PID ile üretilir; göstergeler yalnızca
    sonucu yansıtır.
    """

    servo_changed = Signal(int, int)
    pid_changed = Signal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent; border:none;")
        L = QVBoxLayout(self)
        L.setSpacing(5); L.setContentsMargins(0,0,0,0)

        # Azimuth + Elevation
        for attr, txt, lo, hi in [("x","Azimuth",-180,180),("y","Elevation",-90,90)]:
            row = QHBoxLayout(); row.setSpacing(4)
            lbl = QLabel(txt)
            lbl.setStyleSheet(_AXIS_LABEL_ACTIVE)
            lbl.setFixedWidth(54)
            row.addWidget(lbl)
            if attr == "x": self.x_label = lbl
            else:           self.y_label = lbl
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            # Elevation home: -10°
            sl.setValue(-10 if attr == "y" else 0)
            sl.setStyleSheet(Styles.SLIDER)
            # Gösterge: doğrudan komut girişi kapalı. `setEnabled(False)`
            # kullanılmadı çünkü o kaydırıcıyı soluklaştırıp okunmaz hâle
            # getiriyor; burada yalnızca kullanıcı girişi engelleniyor,
            # programatik `setValue` çalışmaya devam ediyor.
            sl.setFocusPolicy(Qt.NoFocus)
            sl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            row.addWidget(sl, stretch=1)
            val = QLabel("0°")
            val.setStyleSheet("color:#00d4ff;font-size:11px;font-weight:700;"
                              "min-width:30px;background:transparent;border:none;")
            val.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
            row.addWidget(val)
            L.addLayout(row)
            if attr=="x": self.x_slider,self.x_value = sl,val
            else:          self.y_slider,self.y_value = sl,val

        self.x_slider.valueChanged.connect(lambda v:(self.x_value.setText(f"{v}°"),self._emit()))
        self.y_slider.valueChanged.connect(lambda v:(self.y_value.setText(f"{v}°"),self._emit()))

        # PID — 3 sütun yan yana (preset: en_iyi_dikey)
        pid_hdr = QLabel(f"PID · {_PID_PRESET_NAME}")
        pid_hdr.setStyleSheet(
            "color:#9aa4b2;font-size:10px;font-weight:700;"
            "background:transparent;border:none;"
        )
        pid_hdr.setFixedHeight(14)
        L.addWidget(pid_hdr)
        pid_row = QHBoxLayout(); pid_row.setSpacing(6)
        for lbl_txt, attr, default in [("P","pid_p",_DEFAULT_KP),
                                       ("I","pid_i",_DEFAULT_KI),
                                       ("D","pid_d",_DEFAULT_KD)]:
            col = QVBoxLayout(); col.setSpacing(1)
            l = QLabel(lbl_txt)
            l.setAlignment(Qt.AlignCenter)
            l.setStyleSheet("color:#9aa4b2;font-size:10px;font-weight:700;"
                            "background:transparent;border:none;")
            l.setFixedHeight(14)
            col.addWidget(l)
            sb = _pid_spin(default)
            sb.valueChanged.connect(self._emit_pid)
            col.addWidget(sb)
            pid_row.addLayout(col)
            setattr(self, attr, sb)
        L.addLayout(pid_row)

    def _emit(self): self.servo_changed.emit(self.x_slider.value(),self.y_slider.value())
    def reset_position(self): self.x_slider.setValue(0); self.y_slider.setValue(0)
    def get_position(self): return (self.x_slider.value(),self.y_slider.value())
    def get_pid(self): return (self.pid_p.value(),self.pid_i.value(),self.pid_d.value())

    def _emit_pid(self, *_args):
        self.pid_changed.emit(self.pid_p.value(), self.pid_i.value(), self.pid_d.value())

    @Slot(float, float, float)
    def set_pid(self, kp: float, ki: float, kd: float) -> None:
        """Katsayıları dışarıdan yaz (RPi'nin bildirdiği değerle hizalamak için)."""
        for spin, value in ((self.pid_p, kp), (self.pid_i, ki), (self.pid_d, kd)):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def set_manual_input_active(self, active: bool) -> None:
        """Klavye ile yönelimin şu an mümkün olup olmadığını görsel olarak belirt.

        Widget'ın tamamı `setEnabled(False)` ile kapatılmıyor: o, PID
        kutularını da soluklaştırıp devre dışı bırakırdı — oysa KTR PID
        ayarını kipten bağımsız olarak operatöre bırakıyor.
        """
        style = _AXIS_LABEL_ACTIVE if active else _AXIS_LABEL_PASSIVE
        self.x_label.setStyleSheet(style)
        self.y_label.setStyleSheet(style)

    def nudge(self, dx: int, dy: int) -> None:
        """Klavye adımı: göstergeleri sınırlar içinde kaydır.

        Gösterge değeri değişince `servo_changed` yayılır ve komut RPi'ye
        gider; yani klavye tek girdi yolu olsa da ekranla donanım aynı
        değerde kalır.
        """
        self.x_slider.setValue(max(self.x_slider.minimum(),
                                   min(self.x_slider.maximum(),
                                       self.x_slider.value() + dx)))
        self.y_slider.setValue(max(self.y_slider.minimum(),
                                   min(self.y_slider.maximum(),
                                       self.y_slider.value() + dy)))

    @Slot(int,int)
    def set_position(self,x,y):
        self.x_slider.blockSignals(True); self.y_slider.blockSignals(True)
        self.x_slider.setValue(x); self.y_slider.setValue(y)
        self.x_value.setText(f"{x}°"); self.y_value.setText(f"{y}°")
        self.x_slider.blockSignals(False); self.y_slider.blockSignals(False)

# ── Kontrol Paneli ────────────────────────────────────────────────────────────

class ControlPanel(QFrame):
    mode_changed  = Signal(str)
    fire_command  = Signal()
    servo_command = Signal(int,int)
    pid_command   = Signal(float,float,float)
    system_start  = Signal()
    system_stop   = Signal()
    system_reset  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fire_unlocked = False
        self._emergency_active = False
        self._is_friendly_target = None

        self.setStyleSheet("QFrame{background:rgba(22,30,48,0.95);"
                           "border:1px solid rgba(255,255,255,0.07);border-radius:14px;}")
        self.setFixedWidth(302)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        L = QVBoxLayout(self)
        L.setSpacing(5); L.setContentsMargins(10,10,10,10)

        # Başlık
        t = QLabel("KONTROL PANELİ")
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet(
            "font-family: Arial; font-size: 20px; font-weight: 800; "
            "color: #ffffff; background: transparent; padding: 6px 0px;"
        )
        L.addWidget(t)
        L.addWidget(_sep())

        # ── Sistem ────────────────────────────────────────────────────
        L.addWidget(_sec("Sistem"))
        sys_row = QHBoxLayout(); sys_row.setSpacing(4)
        self.btn_start    = QPushButton("BAŞLAT")
        self.btn_stop     = QPushButton("DURDUR")
        self.btn_reset_sys = QPushButton("RESET")
        for btn in [self.btn_start, self.btn_stop, self.btn_reset_sys]:
            btn.setFixedHeight(36)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            sys_row.addWidget(btn)
        self.btn_start.setStyleSheet(Styles.BUTTON_SUCCESS + "QPushButton{font-size:13px;font-weight:700;}")
        self.btn_stop.setStyleSheet(Styles.BUTTON_NORMAL + "QPushButton{font-size:13px;font-weight:700;}")
        self.btn_reset_sys.setStyleSheet(Styles.BUTTON_DANGER + "QPushButton{font-size:13px;font-weight:700;}")
        self.btn_stop.setEnabled(False)
        L.addLayout(sys_row)
        L.addWidget(_sep())

        # ── Çalışma Modu ──────────────────────────────────────────────
        L.addWidget(_sec("Çalışma Modu"))

        # Ana mod: MANUEL | OTONOM
        self.mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout(); mode_row.setSpacing(4)
        self.btn_manuel = QPushButton("MANUEL")
        self.btn_otonom = QPushButton("OTONOM")
        for i, btn in enumerate([self.btn_manuel, self.btn_otonom]):
            btn.setCheckable(True)
            btn.setStyleSheet(Styles.BUTTON_MODE)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setFixedHeight(34)
            mode_row.addWidget(btn)
            self.mode_group.addButton(btn, i)
        self.btn_manuel.setChecked(True)
        L.addLayout(mode_row)

        # Otonom alt modlar: 2. AŞAMA | 3. AŞAMA (başta gizli)
        self.otonom_sub_group = QButtonGroup(self)
        self.otonom_sub_row = QHBoxLayout(); self.otonom_sub_row.setSpacing(4)
        self.btn_asama2 = QPushButton("2. AŞAMA")
        self.btn_asama3 = QPushButton("3. AŞAMA")
        for i, btn in enumerate([self.btn_asama2, self.btn_asama3]):
            btn.setCheckable(True)
            btn.setStyleSheet(Styles.BUTTON_MODE)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setFixedHeight(28)
            self.otonom_sub_row.addWidget(btn)
            self.otonom_sub_group.addButton(btn, i)
        self.btn_asama2.setChecked(True)

        # Sub row'u bir widget'a sar ki gizleyebilelim
        self.otonom_sub_widget = QWidget()
        self.otonom_sub_widget.setStyleSheet("background:transparent;")
        self.otonom_sub_widget.setLayout(self.otonom_sub_row)
        self.otonom_sub_widget.setVisible(False)  # Başta gizli
        L.addWidget(self.otonom_sub_widget)
        L.addWidget(_sep())

        # ── Servo Kontrol ─────────────────────────────────────────────
        L.addWidget(_sec("Servo Kontrol"))
        self.servo_control = ServoControlWidget()
        L.addWidget(self.servo_control)
        self.btn_reset = QPushButton("SIFIRLA")
        self.btn_reset.setStyleSheet(Styles.BUTTON_NORMAL)
        self.btn_reset.setFixedHeight(28)
        L.addWidget(self.btn_reset)
        L.addWidget(_sep())

        # ── Ateş — stretch ile alta yasla ────────────────────────────
        L.addStretch(1)
        L.addWidget(_sec("Ateş Kontrolü"))
        self.btn_unlock = QPushButton("KİLİDİ AÇ")
        self.btn_unlock.setCheckable(True)
        self.btn_unlock.setStyleSheet(Styles.BUTTON_NORMAL + "QPushButton{font-size:13px;font-weight:600;}")
        self.btn_unlock.setFixedHeight(34)
        L.addWidget(self.btn_unlock)

        self.btn_fire = QPushButton("ATEŞ")
        self.btn_fire.setStyleSheet(Styles.BUTTON_DANGER)
        self.btn_fire.setEnabled(False)
        self.btn_fire.setFixedHeight(52)
        self.btn_fire.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        L.addWidget(self.btn_fire)

        # Bağlantılar
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_reset_sys.clicked.connect(self._on_reset_sys)
        self.mode_group.buttonClicked.connect(self._on_mode_changed)
        self.otonom_sub_group.buttonClicked.connect(self._on_sub_mode_changed)
        self.servo_control.servo_changed.connect(lambda x,y: self.servo_command.emit(x,y))
        self.servo_control.pid_changed.connect(
            lambda p,i,d: self.pid_command.emit(p,i,d)
        )
        self.btn_reset.clicked.connect(self.servo_control.reset_position)
        self.btn_unlock.toggled.connect(self._on_unlock_toggled)
        self.btn_fire.clicked.connect(self._on_fire_clicked)

        # Başlangıçta MANUEL kip: klavye ile yönelim mümkün.
        self.servo_control.set_manual_input_active(True)

    @property
    def is_fire_unlocked(self) -> bool:
        """Operatör ateş kilidini açtı mı?

        Otonom angajman bu bayrağa bakar: kilit kapalıyken sistem hedefi
        takip eder, kilitler, ama tetiği çekmez. İnsan onayı böylece
        otonom modda da devrede kalır.
        """
        return self._fire_unlocked and not self._emergency_active

    # ── Sistem ────────────────────────────────────────────────────────
    def _on_start(self):
        self.btn_start.setEnabled(False); self.btn_stop.setEnabled(True)
        self.system_start.emit()

    def _on_stop(self):
        self.btn_start.setEnabled(True); self.btn_stop.setEnabled(False)
        self.btn_unlock.setChecked(False)
        self.system_stop.emit()

    def _on_reset_sys(self):
        self._on_stop()
        self.servo_control.reset_position()
        self.btn_manuel.setChecked(True)
        self.otonom_sub_widget.setVisible(False)
        self.servo_control.set_manual_input_active(True)
        self.set_emergency(False)
        self.system_reset.emit()

    # ── Mod ───────────────────────────────────────────────────────────
    def _on_mode_changed(self, button):
        is_otonom = self.mode_group.id(button) == 1
        self.otonom_sub_widget.setVisible(is_otonom)
        # KTR Bölüm 6: "Manuel dışı modlarda doğrudan girişleri pasifleştiren
        # mod kilidi". Otonom kipte klavye ile yönelim kapanır.
        self.servo_control.set_manual_input_active(not is_otonom)
        if is_otonom:
            # Hangi alt aşama seçili?
            sub_id = self.otonom_sub_group.checkedId()
            mode = "ASAMA_3" if sub_id == 1 else "ASAMA_2"
        else:
            mode = "MANUEL"
        self.mode_changed.emit(mode)

    def _on_sub_mode_changed(self, button):
        sub_id = self.otonom_sub_group.id(button)
        mode = "ASAMA_3" if sub_id == 1 else "ASAMA_2"
        self.mode_changed.emit(mode)

    # ── Kilit / Ateş ──────────────────────────────────────────────────
    def _on_unlock_toggled(self, checked):
        if checked and self._emergency_active:
            # Güvenli duruşta kilit açılamaz; programatik bir setChecked
            # gelse bile burada geri alınır.
            self.btn_unlock.setChecked(False)
            return
        self._fire_unlocked = checked
        if checked:
            self.btn_unlock.setText("KİLİT AÇIK ✓")
            self.btn_unlock.setStyleSheet(Styles.BUTTON_SUCCESS)
            if self._is_friendly_target is not True:
                self.btn_fire.setEnabled(True)
        else:
            self.btn_unlock.setText("KİLİDİ AÇ")
            self.btn_unlock.setStyleSheet(Styles.BUTTON_NORMAL + "QPushButton{font-size:13px;font-weight:600;}")
            self.btn_fire.setEnabled(False)

    def _on_fire_clicked(self):
        if self._emergency_active or self._is_friendly_target is True: return
        if self._fire_unlocked:
            self.fire_command.emit()
            self.btn_unlock.setChecked(False)

    # ── Dışarıdan ─────────────────────────────────────────────────────
    @Slot(str)
    def set_mode(self, mode):
        m = mode.upper()
        if m == "MANUEL":
            self.btn_manuel.setChecked(True)
            self.otonom_sub_widget.setVisible(False)
            self.servo_control.set_manual_input_active(True)
        elif m in ("ASAMA_2", "ASAMA_3"):
            self.btn_otonom.setChecked(True)
            self.otonom_sub_widget.setVisible(True)
            self.servo_control.set_manual_input_active(False)
            if m == "ASAMA_2": self.btn_asama2.setChecked(True)
            else:               self.btn_asama3.setChecked(True)

    @Slot(bool)
    def set_emergency(self, active: bool) -> None:
        """Güvenli duruş (FAIL_SAFE) kilidi.

        KTR Bölüm 6 çok katmanlı emniyet istiyor; güvenli duruşta ateş yolunun
        arayüzde de kapanması gerekiyor. Daha önce bu bayrak hiç kimse
        tarafından açılmadığı için, açık kalmış bir ateş kilidi güvenli duruşta
        da tıklanabilir kalıyordu.
        """
        self._emergency_active = bool(active)
        if self._emergency_active:
            self._fire_unlocked = False
            self.btn_unlock.setChecked(False)
            self.btn_unlock.setEnabled(False)
        else:
            self.btn_unlock.setEnabled(self._is_friendly_target is not True)
        self.btn_fire.setEnabled(False)
        self.btn_fire.setText(self._fire_idle_text())

    def _fire_idle_text(self) -> str:
        """ATEŞ düğmesinin boştaki etiketi: kilidin gerekçesini gösterir."""
        if self._emergency_active:
            return "⛔ GÜVENLİ DURUŞ"
        if self._is_friendly_target is True:
            return "🛡️ DOST HEDEF"
        return "ATEŞ"

    @Slot(bool)
    def set_friendly_target(self, friendly):
        self._is_friendly_target = friendly
        if friendly:
            self.btn_fire.setEnabled(False)
            self.btn_unlock.setEnabled(False)
            self.btn_unlock.setChecked(False)
        else:
            self.btn_unlock.setEnabled(not self._emergency_active)
            if self._fire_unlocked and not self._emergency_active:
                self.btn_fire.setEnabled(True)
        self.btn_fire.setText(self._fire_idle_text())

    @Slot()
    def clear_target_lock(self):
        self._is_friendly_target = None
        self.btn_fire.setText(self._fire_idle_text())
        if not self._emergency_active:
            self.btn_unlock.setEnabled(True)
