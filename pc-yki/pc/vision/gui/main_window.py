"""Operatör Arayüzü (PySide6) — QThread worker + latest-only kuyruk.

Yüksek hızlı video üretimi ile nesne tespiti arasındaki denge, tek
yuvalı "latest-only" kuyruk ile sağlanır: kuyruk doluysa eski kare
atılır, işleme her zaman en güncel kare girer. Böylece arayüz
kesintisiz kalır ve gecikme birikmez.
"""
import queue
import sys

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QVBoxLayout, QWidget)

import config


class LatestOnlyQueue:
    """Tek yuvalı kuyruk: put her zaman en güncel öğeyi bırakır."""

    def __init__(self):
        self._q = queue.Queue(maxsize=1)

    def put(self, item):
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()      # eskiyi at
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)  # yeniyi koy
            except queue.Full:
                pass

    def get(self, timeout=1.0):
        return self._q.get(timeout=timeout)


class CaptureWorker(QThread):
    """Kamera karelerini üretir; işleme kuyruğuna latest-only yazar."""
    frame_ready = Signal(np.ndarray)

    def __init__(self, frame_queue: LatestOnlyQueue, source=0):
        super().__init__()
        self.frame_queue = frame_queue
        self.source = source
        self.running = True

    def run(self):
        cap = cv2.VideoCapture(self.source)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
        while self.running:
            ok, frame = cap.read()
            if not ok:
                continue
            self.frame_queue.put(frame)
            self.frame_ready.emit(frame)
        cap.release()

    def stop(self):
        self.running = False
        self.wait()


class PipelineWorker(QThread):
    """Tespit/takip boru hattını arayüzden bağımsız çalıştırır."""
    result_ready = Signal(np.ndarray)   # üzeri çizilmiş kare

    def __init__(self, frame_queue: LatestOnlyQueue, pipeline):
        super().__init__()
        self.frame_queue = frame_queue
        self.pipeline = pipeline        # main.Pipeline örneği
        self.running = True

    def run(self):
        while self.running:
            try:
                frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            annotated = self.pipeline.process(frame)
            self.result_ready.emit(annotated)

    def stop(self):
        self.running = False
        self.wait()


class MainWindow(QMainWindow):
    def __init__(self, pipeline, rpi_link):
        super().__init__()
        self.setWindowTitle("HSS - Yer Kontrol İstasyonu")
        self.rpi_link = rpi_link
        self.autonomous = False

        self.video_label = QLabel(alignment=Qt.AlignCenter)
        self.video_label.setMinimumSize(960, 540)

        self.mode_btn = QPushButton("Mod: MANUEL")
        self.mode_btn.clicked.connect(self.toggle_mode)
        self.status_label = QLabel("Durum: HAZIR")

        top = QHBoxLayout()
        top.addWidget(self.mode_btn)
        top.addWidget(self.status_label)
        top.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.video_label)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # worker mimarisi
        self.frame_queue = LatestOnlyQueue()
        self.capture = CaptureWorker(self.frame_queue)
        self.worker = PipelineWorker(self.frame_queue, pipeline)
        self.worker.result_ready.connect(self.show_frame)
        self.capture.start()
        self.worker.start()

    def toggle_mode(self):
        self.autonomous = not self.autonomous
        self.mode_btn.setText("Mod: OTONOM" if self.autonomous else "Mod: MANUEL")
        self.rpi_link.send_mode(self.autonomous)

    def keyPressEvent(self, e):
        """Manuel mod: ok tuşlarıyla taret yönelimi."""
        if self.autonomous:
            return
        step = 5.0
        keymap = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                  Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
        if e.key() in keymap:
            dx, dy = keymap[e.key()]
            self.rpi_link.send_manual(dx, dy)

    def show_frame(self, frame: np.ndarray):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(img).scaled(
            self.video_label.size(), Qt.KeepAspectRatio,
            Qt.SmoothTransformation))

    def closeEvent(self, e):
        self.capture.stop()
        self.worker.stop()
        super().closeEvent(e)
