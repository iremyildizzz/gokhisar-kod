from __future__ import annotations

import os
import time
import math
import socket
import threading
import statistics
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Deque, Any

import psutil


# ============================================================
# YARDIMCI FONKSIYONLAR
# ============================================================

def now_ns() -> int:
    """
    Aynı bilgisayar üzerindeki süre ölçümleri için kullanılır.
    wall-clock yerine monotonic perf_counter tercih edilir.
    """
    return time.perf_counter_ns()


def ns_to_ms(value: int) -> float:
    return value / 1_000_000.0


def safe_mean(values):
    return statistics.mean(values) if values else 0.0


def safe_stdev(values):
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def percentile(values, p: float) -> float:
    """
    Basit percentile hesabı.
    p = 95 -> p95
    """
    if not values:
        return 0.0

    data = sorted(values)

    if len(data) == 1:
        return float(data[0])

    k = (len(data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)

    if f == c:
        return float(data[int(k)])

    return (
        data[f] * (c - k)
        + data[c] * (k - f)
    )


# ============================================================
# FPS ÖLÇER
# ============================================================

class FPSMeter:
    def __init__(self, window_seconds: float = 2.0):
        self.window_seconds = window_seconds
        self.timestamps: Deque[float] = deque()
        self.total_frames = 0
        self.lock = threading.Lock()

    def tick(self):
        t = time.perf_counter()

        with self.lock:
            self.timestamps.append(t)
            self.total_frames += 1

            cutoff = t - self.window_seconds

            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()

    def fps(self) -> float:
        with self.lock:
            if len(self.timestamps) < 2:
                return 0.0

            duration = self.timestamps[-1] - self.timestamps[0]

            if duration <= 0:
                return 0.0

            return (len(self.timestamps) - 1) / duration


# ============================================================
# GECIKME İSTATISTIKLERI
# ============================================================

class LatencyMeter:
    def __init__(self, max_samples: int = 1000):
        self.values: Deque[float] = deque(maxlen=max_samples)
        self.lock = threading.Lock()

    def add_ms(self, latency_ms: float):
        with self.lock:
            self.values.append(float(latency_ms))

    def stats(self) -> Dict[str, float]:
        with self.lock:
            values = list(self.values)

        if not values:
            return {
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
                "std": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
            }

        return {
            "mean": safe_mean(values),
            "min": min(values),
            "max": max(values),
            "std": safe_stdev(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "p99": percentile(values, 99),
        }


# ============================================================
# SAYAÇ
# ============================================================

class Counter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def increment(self, amount: int = 1):
        with self.lock:
            self.value += amount

    def get(self) -> int:
        with self.lock:
            return self.value


# ============================================================
# NETWORK BANDWIDTH
# ============================================================

class NetworkMeter:
    def __init__(self, interface: Optional[str] = None):
        self.interface = interface

        self.previous_time = time.perf_counter()
        self.previous_sent = 0
        self.previous_recv = 0

        self.tx_mbps = 0.0
        self.rx_mbps = 0.0

        self._initialize()

    def _read(self):
        if self.interface:
            interfaces = psutil.net_io_counters(pernic=True)

            if self.interface not in interfaces:
                return None

            return interfaces[self.interface]

        return psutil.net_io_counters()

    def _initialize(self):
        data = self._read()

        if data:
            self.previous_sent = data.bytes_sent
            self.previous_recv = data.bytes_recv

    def update(self):
        current = self._read()

        if current is None:
            return

        current_time = time.perf_counter()
        dt = current_time - self.previous_time

        if dt <= 0:
            return

        tx_bytes = current.bytes_sent - self.previous_sent
        rx_bytes = current.bytes_recv - self.previous_recv

        self.tx_mbps = (tx_bytes * 8) / dt / 1_000_000
        self.rx_mbps = (rx_bytes * 8) / dt / 1_000_000

        self.previous_sent = current.bytes_sent
        self.previous_recv = current.bytes_recv
        self.previous_time = current_time


# ============================================================
# NVIDIA GPU
# ============================================================

class GPUMonitor:
    def __init__(self):
        self.available = False
        self.handle = None

        try:
            import pynvml

            self.nvml = pynvml
            pynvml.nvmlInit()

            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.available = True

        except Exception:
            self.available = False

    def read(self) -> Dict[str, float]:
        if not self.available:
            return {
                "gpu_usage": 0.0,
                "gpu_memory_usage": 0.0,
                "gpu_temperature": 0.0,
            }

        try:
            util = self.nvml.nvmlDeviceGetUtilizationRates(
                self.handle
            )

            memory = self.nvml.nvmlDeviceGetMemoryInfo(
                self.handle
            )

            temperature = self.nvml.nvmlDeviceGetTemperature(
                self.handle,
                self.nvml.NVML_TEMPERATURE_GPU
            )

            memory_percent = (
                memory.used / memory.total * 100
                if memory.total
                else 0.0
            )

            return {
                "gpu_usage": float(util.gpu),
                "gpu_memory_usage": memory_percent,
                "gpu_temperature": float(temperature),
            }

        except Exception:
            return {
                "gpu_usage": 0.0,
                "gpu_memory_usage": 0.0,
                "gpu_temperature": 0.0,
            }


# ============================================================
# RASPBERRY PI SICAKLIK
# ============================================================

def raspberry_pi_temperature() -> Optional[float]:
    """
    Önce Linux thermal_zone denenir.
    Olmazsa vcgencmd denenir.
    """

    thermal_path = "/sys/class/thermal/thermal_zone0/temp"

    try:
        if os.path.exists(thermal_path):
            with open(thermal_path, "r") as f:
                return float(f.read().strip()) / 1000.0
    except Exception:
        pass

    try:
        result = subprocess.check_output(
            ["vcgencmd", "measure_temp"],
            text=True,
            timeout=1
        )

        # temp=55.2'C
        value = (
            result
            .replace("temp=", "")
            .replace("'C", "")
            .strip()
        )

        return float(value)

    except Exception:
        return None


# ============================================================
# PIPELINE FRAME TAKİBİ
# ============================================================

@dataclass
class FrameTiming:
    frame_id: int

    camera_ns: Optional[int] = None
    network_rx_ns: Optional[int] = None

    preprocess_start_ns: Optional[int] = None
    preprocess_end_ns: Optional[int] = None

    inference_start_ns: Optional[int] = None
    inference_end_ns: Optional[int] = None

    postprocess_start_ns: Optional[int] = None
    postprocess_end_ns: Optional[int] = None

    gui_ns: Optional[int] = None


# ============================================================
# ANA PERFORMANCE MONITOR
# ============================================================

class PerformanceMonitor:

    def __init__(
        self,
        network_interface: Optional[str] = None
    ):

        # ---------------- FPS ----------------

        self.camera_fps = FPSMeter()
        self.network_rx_fps = FPSMeter()
        self.processing_fps = FPSMeter()
        self.gui_fps = FPSMeter()

        # ---------------- LATENCY ----------------

        self.preprocess_latency = LatencyMeter()
        self.inference_latency = LatencyMeter()
        self.postprocess_latency = LatencyMeter()

        self.frame_processing_latency = LatencyMeter()

        self.network_frame_latency = LatencyMeter()
        self.tcp_latency = LatencyMeter()

        self.lidar_age = LatencyMeter()
        self.uart_latency = LatencyMeter()

        # ---------------- FRAME COUNTERS ----------------

        self.camera_frames = Counter()
        self.received_frames = Counter()
        self.processed_frames = Counter()

        self.dropped_frames = Counter()
        self.queue_overwrites = Counter()

        # ---------------- NETWORK ----------------

        self.network = NetworkMeter(
            interface=network_interface
        )

        self.network_packets = Counter()
        self.network_packet_errors = Counter()

        # ---------------- TCP ----------------

        self.tcp_messages = Counter()
        self.tcp_errors = Counter()
        self.tcp_reconnects = Counter()

        self.last_tcp_message_time = None

        # ---------------- LiDAR ----------------

        self.lidar_samples = Counter()
        self.lidar_valid_samples = Counter()
        self.lidar_invalid_samples = Counter()
        self.lidar_timeouts = Counter()

        self.last_lidar_time = None
        self.last_lidar_distance = None

        # ---------------- UART ----------------

        self.uart_tx = Counter()
        self.uart_rx = Counter()
        self.uart_checksum_errors = Counter()
        self.uart_invalid_frames = Counter()
        self.uart_timeouts = Counter()

        self.last_uart_rx_time = None

        # ---------------- APPLICATION ----------------

        self.application_errors = Counter()

        # ---------------- GPU ----------------

        self.gpu = GPUMonitor()

        # ---------------- FRAME TIMING ----------------

        self.frames: Dict[int, FrameTiming] = {}

        self.frames_lock = threading.Lock()

        # process CPU değerinin ilk çağrısının hazırlanması
        psutil.cpu_percent(interval=None)


    # ======================================================
    # CAMERA
    # ======================================================

    def camera_frame(self, frame_id: int):

        self.camera_fps.tick()
        self.camera_frames.increment()

        with self.frames_lock:

            self.frames[frame_id] = FrameTiming(
                frame_id=frame_id,
                camera_ns=now_ns()
            )

            # Bellek şişmesini önle
            if len(self.frames) > 2000:

                oldest = sorted(self.frames.keys())[:500]

                for key in oldest:
                    self.frames.pop(key, None)


    # ======================================================
    # NETWORK VIDEO RECEIVE
    # ======================================================

    def network_frame_received(
        self,
        frame_id: Optional[int] = None
    ):

        self.network_rx_fps.tick()
        self.received_frames.increment()

        if frame_id is None:
            return

        t = now_ns()

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if frame is None:
                frame = FrameTiming(frame_id=frame_id)
                self.frames[frame_id] = frame

            frame.network_rx_ns = t

            if frame.camera_ns is not None:

                latency = ns_to_ms(
                    t - frame.camera_ns
                )

                self.network_frame_latency.add_ms(
                    latency
                )


    # ======================================================
    # PREPROCESS
    # ======================================================

    def preprocess_start(self, frame_id: int):

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if frame:
                frame.preprocess_start_ns = now_ns()

    def preprocess_end(self, frame_id: int):

        t = now_ns()

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if (
                frame
                and frame.preprocess_start_ns
            ):

                frame.preprocess_end_ns = t

                latency = ns_to_ms(
                    t - frame.preprocess_start_ns
                )

                self.preprocess_latency.add_ms(
                    latency
                )


    # ======================================================
    # INFERENCE
    # ======================================================

    def inference_start(self, frame_id: int):

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if frame:
                frame.inference_start_ns = now_ns()

    def inference_end(self, frame_id: int):

        t = now_ns()

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if (
                frame
                and frame.inference_start_ns
            ):

                frame.inference_end_ns = t

                latency = ns_to_ms(
                    t - frame.inference_start_ns
                )

                self.inference_latency.add_ms(
                    latency
                )


    # ======================================================
    # POSTPROCESS
    # ======================================================

    def postprocess_start(self, frame_id: int):

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if frame:
                frame.postprocess_start_ns = now_ns()

    def postprocess_end(self, frame_id: int):

        t = now_ns()

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if (
                frame
                and frame.postprocess_start_ns
            ):

                frame.postprocess_end_ns = t

                latency = ns_to_ms(
                    t - frame.postprocess_start_ns
                )

                self.postprocess_latency.add_ms(
                    latency
                )

                # Ağdan alınma → processing tamamlanma

                if frame.network_rx_ns:

                    total_processing = ns_to_ms(
                        t - frame.network_rx_ns
                    )

                    self.frame_processing_latency.add_ms(
                        total_processing
                    )

                self.processed_frames.increment()
                self.processing_fps.tick()


    # ======================================================
    # GUI
    # ======================================================

    def gui_frame(self, frame_id: Optional[int] = None):

        self.gui_fps.tick()

        if frame_id is None:
            return

        with self.frames_lock:

            frame = self.frames.get(frame_id)

            if frame:
                frame.gui_ns = now_ns()


    # ======================================================
    # QUEUE
    # ======================================================

    def queue_overwrite(self):

        self.queue_overwrites.increment()
        self.dropped_frames.increment()


    def frame_dropped(self):

        self.dropped_frames.increment()


    # ======================================================
    # TCP
    # ======================================================

    def tcp_message_received(
        self,
        sender_timestamp_ns: Optional[int] = None
    ):

        self.tcp_messages.increment()

        self.last_tcp_message_time = (
            time.monotonic()
        )

        # Sadece clock senkronizasyonu varsa
        # iki farklı cihaz arasında kullanılmalıdır.
        if sender_timestamp_ns is not None:

            receiver_ns = time.time_ns()

            latency_ms = (
                receiver_ns - sender_timestamp_ns
            ) / 1_000_000

            if latency_ms >= 0:
                self.tcp_latency.add_ms(
                    latency_ms
                )


    def tcp_error(self):

        self.tcp_errors.increment()


    def tcp_reconnect(self):

        self.tcp_reconnects.increment()


    # ======================================================
    # LiDAR
    # ======================================================

    def lidar_sample(
        self,
        distance: Optional[float],
        valid: bool = True
    ):

        self.lidar_samples.increment()

        self.last_lidar_time = (
            time.monotonic()
        )

        if valid:

            self.lidar_valid_samples.increment()

            self.last_lidar_distance = distance

        else:

            self.lidar_invalid_samples.increment()


    def lidar_timeout(self):

        self.lidar_timeouts.increment()


    # ======================================================
    # UART
    # ======================================================

    def uart_tx_frame(self):

        self.uart_tx.increment()


    def uart_rx_frame(self):

        self.uart_rx.increment()

        self.last_uart_rx_time = (
            time.monotonic()
        )


    def uart_checksum_error(self):

        self.uart_checksum_errors.increment()


    def uart_invalid_frame(self):

        self.uart_invalid_frames.increment()


    def uart_timeout(self):

        self.uart_timeouts.increment()


    # ======================================================
    # APP
    # ======================================================

    def application_error(self):

        self.application_errors.increment()


    # ======================================================
    # DURUM / AGE
    # ======================================================

    @staticmethod
    def _age_ms(last_time):

        if last_time is None:
            return None

        return (
            time.monotonic() - last_time
        ) * 1000


    @staticmethod
    def connection_state(
        age_ms,
        warning_ms=500,
        timeout_ms=2000
    ):

        if age_ms is None:
            return "DISCONNECTED"

        if age_ms >= timeout_ms:
            return "DISCONNECTED"

        if age_ms >= warning_ms:
            return "STALE"

        return "RECEIVING"


    # ======================================================
    # SYSTEM RESOURCE MONITOR
    # ======================================================

    def system_resources(self):

        cpu = psutil.cpu_percent(
            interval=None
        )

        ram = psutil.virtual_memory()

        try:
            process = psutil.Process(
                os.getpid()
            )

            process_cpu = process.cpu_percent(
                interval=None
            )

            process_ram_mb = (
                process.memory_info().rss
                / 1024
                / 1024
            )

        except Exception:

            process_cpu = 0.0
            process_ram_mb = 0.0

        temp = raspberry_pi_temperature()

        gpu = self.gpu.read()

        return {
            "cpu_percent": cpu,
            "ram_percent": ram.percent,
            "ram_used_mb": ram.used / 1024 / 1024,

            "process_cpu_percent": process_cpu,
            "process_ram_mb": process_ram_mb,

            "temperature_c": temp,

            **gpu
        }


    # ======================================================
    # SNAPSHOT
    # ======================================================

    def snapshot(self) -> Dict[str, Any]:

        self.network.update()

        camera_total = self.camera_frames.get()
        received_total = self.received_frames.get()

        dropped = self.dropped_frames.get()

        frame_drop_percent = (
            dropped / camera_total * 100
            if camera_total
            else 0.0
        )

        lidar_total = self.lidar_samples.get()
        lidar_valid = self.lidar_valid_samples.get()

        lidar_valid_percent = (
            lidar_valid / lidar_total * 100
            if lidar_total
            else 0.0
        )

        uart_rx = self.uart_rx.get()

        uart_errors = (
            self.uart_checksum_errors.get()
            + self.uart_invalid_frames.get()
        )

        uart_success_percent = (

            (
                uart_rx - uart_errors
            )
            / uart_rx
            * 100

            if uart_rx
            else 0.0
        )

        tcp_age = self._age_ms(
            self.last_tcp_message_time
        )

        lidar_age = self._age_ms(
            self.last_lidar_time
        )

        uart_age = self._age_ms(
            self.last_uart_rx_time
        )

        data = {

            # ---------------------------------
            # FPS
            # ---------------------------------

            "camera_fps":
                self.camera_fps.fps(),

            "received_fps":
                self.network_rx_fps.fps(),

            "processing_fps":
                self.processing_fps.fps(),

            "gui_fps":
                self.gui_fps.fps(),

            # ---------------------------------
            # FRAMES
            # ---------------------------------

            "camera_frames":
                camera_total,

            "received_frames":
                received_total,

            "processed_frames":
                self.processed_frames.get(),

            "dropped_frames":
                dropped,

            "frame_drop_percent":
                frame_drop_percent,

            "queue_overwrites":
                self.queue_overwrites.get(),

            # ---------------------------------
            # PROCESSING
            # ---------------------------------

            "preprocess":
                self.preprocess_latency.stats(),

            "inference":
                self.inference_latency.stats(),

            "postprocess":
                self.postprocess_latency.stats(),

            "frame_processing":
                self.frame_processing_latency.stats(),

            "video_latency":
                self.network_frame_latency.stats(),

            "tcp_latency":
                self.tcp_latency.stats(),

            # ---------------------------------
            # NETWORK
            # ---------------------------------

            "network_tx_mbps":
                self.network.tx_mbps,

            "network_rx_mbps":
                self.network.rx_mbps,

            # ---------------------------------
            # TCP
            # ---------------------------------

            "tcp_messages":
                self.tcp_messages.get(),

            "tcp_errors":
                self.tcp_errors.get(),

            "tcp_reconnects":
                self.tcp_reconnects.get(),

            "tcp_age_ms":
                tcp_age,

            "tcp_state":
                self.connection_state(
                    tcp_age
                ),

            # ---------------------------------
            # LiDAR
            # ---------------------------------

            "lidar_samples":
                lidar_total,

            "lidar_valid_percent":
                lidar_valid_percent,

            "lidar_invalid_samples":
                self.lidar_invalid_samples.get(),

            "lidar_timeouts":
                self.lidar_timeouts.get(),

            "lidar_distance":
                self.last_lidar_distance,

            "lidar_age_ms":
                lidar_age,

            "lidar_state":
                self.connection_state(
                    lidar_age
                ),

            # ---------------------------------
            # UART
            # ---------------------------------

            "uart_tx":
                self.uart_tx.get(),

            "uart_rx":
                uart_rx,

            "uart_checksum_errors":
                self.uart_checksum_errors.get(),

            "uart_invalid_frames":
                self.uart_invalid_frames.get(),

            "uart_timeouts":
                self.uart_timeouts.get(),

            "uart_success_percent":
                uart_success_percent,

            "uart_age_ms":
                uart_age,

            "uart_state":
                self.connection_state(
                    uart_age
                ),

            # ---------------------------------
            # SOFTWARE
            # ---------------------------------

            "application_errors":
                self.application_errors.get(),
        }

        data.update(
            self.system_resources()
        )

        return data