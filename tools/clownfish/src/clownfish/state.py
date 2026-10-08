# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unified thread-safe reactive state store for Clownfish v1 and v2 gRPC services."""

import copy
import math
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from clownfish.presets import DevicePreset, get_preset


@dataclass
class DisplayRecord:
    """Mutable runtime state for a single physical or virtual display."""

    display_id: int
    width: int
    height: int
    dpi: int
    flags: int = 0
    circular: bool = False
    refresh_rate_hz: float = 60.0
    rotation_deg: int = 0  # 0, 90, 180, 270
    rotation_locked: bool = False
    power_mode: int = 1  # 1=ON, 2=DOZE, 3=DOZE_SUSPEND, 4=OFF
    brightness_percent: int = 80


@dataclass
class TouchPoint:
    """Active touch/pointer contact point in normalized [0.0, 1.0] coordinates."""

    pointer_id: int
    x_norm: float
    y_norm: float
    pressure: float = 1.0
    display_id: int = 0
    tool: str = "touch"
    timestamp_ns: int = field(default_factory=time.monotonic_ns)


@dataclass
class SnapshotEntry:
    """Saved VM snapshot shared across v1 SnapshotService and v2 HypervisorService."""

    snapshot_id: str
    display_name: str
    description: str
    creation_time_s: int
    size_bytes: int
    screenshot_png: bytes
    state_snapshot: dict[str, Any]
    rotation_skin: int = 0
    folded: bool = False
    imported: bool = False
    quickboot_target: bool = False


@dataclass
class RecordingEntry:
    """Screen recording session metadata for v1 ScreenRecording service."""

    file_name: str
    width: int
    height: int
    fps: int
    bit_rate: int
    time_limit: int
    display_id: int
    state: int  # 1=STARTING, 2=RECORDING, 3=STOPPING, 4=STOPPED
    start_time_s: int


@dataclass
class CallEntry:
    """Voice call state for v1 EmulatorController and v2 TelephonyService."""

    call_id: str
    caller_phone_number: str
    status: int  # 1=INCOMING, 2=ACTIVE, 3=HELD, 4=DIALING, 5=DISCONNECTED


class EmulatorState:
    """Thread-safe reactive state store backing all v1 and v2 gRPC services.

    Strictly enforces the 'No Alien Code Under Lock' invariant: all state reads and
    mutations occur under `self._lock`, subscriber queues are snapshotted under the
    lock, and all queue notifications are dispatched outside the lock.
    """

    def __init__(
        self,
        preset: str | DevicePreset = "phone",
        avd_name: str | None = None,
        adb_port: int = 5554,
    ) -> None:
        self._lock = threading.RLock()
        self.preset: DevicePreset = get_preset(preset)
        if avd_name:
            self.preset = replace(self.preset, avd_name=avd_name, display_name=avd_name)
        self.adb_port: int = adb_port
        self.start_monotonic_ns: int = time.monotonic_ns()

        # PubSub subscriber queues keyed by topic name.
        self._subscribers: dict[str, set[queue.Queue[Any]]] = {}

        # Initialize mutable state fields.
        self._init_mutable_state()

    def _init_mutable_state(self) -> None:
        """Initializes or resets mutable device state from `self.preset`."""
        p = self.preset

        # VM & Boot State
        # v1 VmRunState: 1=RUNNING, 2=PAUSED, 4=SHUTDOWN
        # v2 VmState.State: 1=RUNNING, 2=PAUSED, 3=STOPPED, 4=RESETTING, 5=SHUTTING_DOWN, 6=SAVING_SNAPSHOT, 7=RESTORING_SNAPSHOT
        self.vm_state_v1: int = 1
        self.vm_state_v2: int = 1
        self.booted: bool = True
        self.boot_phase_v2: int = 3  # BOOT_COMPLETED
        self.boot_completed_time_ms: int = 1250

        # Frame & Animation State
        self.frame_seq: int = 1
        self.animation_angle_deg: float = 0.0
        self.animation_step: int = 0

        # Displays
        self.displays: dict[int, DisplayRecord] = {
            0: DisplayRecord(
                display_id=0,
                width=p.width,
                height=p.height,
                dpi=p.dpi,
                circular=p.circular,
                refresh_rate_hz=p.refresh_rate_hz,
            )
        }
        self.display_mode: int = 0  # 0=PHONE, 1=FOLDABLE, 2=TABLET, 3=DESKTOP
        self.brightness_targets: dict[int, int] = {
            0: 80,  # LCD
            1: 80,  # KEYBOARD
            2: 80,  # BUTTON
        }

        # Foldable Posture & Hinges
        # Posture enum (both v1 and v2): 1=CLOSED, 2=HALF_OPENED, 3=OPENED, 4=FLIPPED, 5=TENT
        self.posture: int = 3 if p.is_foldable else 0
        self.hinge_angles: list[float] = (
            list(p.initial_hinge_angles) if p.is_foldable else []
        )
        self.rollable_percents: list[float] = [0.0] * p.num_rollables

        # 3D Pose, Ambient Environment & Raw Overrides
        self.pose_rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.pose_position_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.pose_velocity_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.pose_ambient_motion: float = 0.0

        self.env_light_lux: float = 400.0
        self.env_rgbc_light: tuple[float, float, float, float] = (
            100.0,
            100.0,
            100.0,
            400.0,
        )
        self.env_proximity_cm: float = 5.0
        self.env_temperature_c: float = 25.0
        self.env_pressure_hpa: float = 1013.25
        self.env_humidity_pct: float = 45.0
        self.env_magnetic_field_ut: tuple[float, float, float] = (0.0, 5.9, -48.4)
        self.env_heart_rate_bpm: float = 72.0
        self.env_wrist_tilt: float = 0.0

        self.raw_sensor_overrides: dict[int, tuple[float, float, float]] = {}

        # Battery State
        self.battery_has_battery: bool = True
        self.battery_is_present: bool = True
        self.battery_charger_v1: int = 1  # 0=NONE, 1=AC, 2=USB, 3=WIRELESS
        self.battery_charge_level: int = 85
        self.battery_health_v1: int = (
            0  # 0=GOOD, 1=FAILED, 2=DEAD, 3=OVERVOLTAGE, 4=OVERHEATED
        )
        self.battery_status_v1: int = (
            1  # 0=UNKNOWN, 1=CHARGING, 2=DISCHARGING, 3=NOT_CHARGING, 4=FULL
        )
        self.battery_temperature_c: float = 25.0

        # Location / GPS State
        self.gps_passive_update: bool = True
        self.gps_latitude: float = 37.4220936
        self.gps_longitude: float = -122.083922
        self.gps_altitude: float = 15.0
        self.gps_speed_mps: float = 0.0
        self.gps_bearing_deg: float = 0.0
        self.gps_accuracy_m: float = 3.0
        self.gps_satellites: int = 8
        self.gps_time_us: int = int(time.time() * 1_000_000)

        # Clipboard State
        self.clipboard_text: str = ""
        self.clipboard_generation: int = 0

        # Input & Overlay State
        self.active_touches: dict[int, TouchPoint] = {}
        self.mouse_x_norm: float = 0.5
        self.mouse_y_norm: float = 0.5
        self.mouse_buttons: int = 0
        self.mouse_display_id: int = 0
        self.last_key_event: str = ""
        self.last_rotary_delta: int = 0
        self.last_fingerprint: tuple[bool, int] = (False, 0)
        self.input_history: list[dict[str, Any]] = []

        # Audio & Microphone State
        self.mic_has_input: bool = True
        self.mic_is_plugged_in: bool = True
        self.mic_is_muted: bool = False
        self.injected_audio_bytes: int = 0

        # Telephony State
        self.phone_number: str = "+16505550100"
        self.radio_powered: bool = True
        self.active_technology_v2: int = 3  # LTE
        self.signal_strength_v2: int = 4  # GOOD
        self.registration_state_v2: int = 1  # HOME_NETWORK
        self.active_calls: dict[str, CallEntry] = {}
        self.sms_messages: list[tuple[str, str]] = []

        # Camera, XR & Virtual Scene State
        self.cameras: dict[str, dict[str, Any]] = {
            "0": {
                "lens_facing": 2,
                "orientation": 90,
                "in_use": False,
                "active_source": 1,
                "last_frame": b"",
            },
            "1": {
                "lens_facing": 1,
                "orientation": 270,
                "in_use": False,
                "active_source": 1,
                "last_frame": b"",
            },
        }
        self.xr_environment: int = 0  # LIVING_ROOM_DAY
        self.xr_passthrough_coefficient: float = 0.5 if p.is_xr else 0.0
        self.virtual_scene_rotation_rad: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.virtual_scene_velocity_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.text_view_has_focus: bool = False
        self.led_indicator_on: bool = False

        # Logcat Ring Buffer
        self.logcat_entries: list[dict[str, Any]] = [
            {
                "timestamp": int(time.time() * 1000),
                "pid": 1000,
                "tid": 1000,
                "level": 4,  # INFO
                "tag": "Clownfish",
                "msg": f"Boot completed for {p.avd_name}",
            }
        ]

        # Snapshots, Long-Running Operations, Recordings & WebRTC
        self.snapshots: dict[str, SnapshotEntry] = {}
        self.loaded_imported_snapshot: bool = False
        self.operations: dict[str, Any] = {}
        self.recordings: list[RecordingEntry] = []
        self.active_recording: RecordingEntry | None = None
        self.rtc_sessions: dict[str, dict[str, Any]] = {}
        self.tracked_processes: list[tuple[int, str, int]] = []

    def monotonic_ns(self) -> int:
        """Returns current CLOCK_MONOTONIC timestamp in nanoseconds."""
        return time.monotonic_ns()

    def uptime_ms(self) -> int:
        """Returns emulator uptime in milliseconds."""
        return max(1, (time.monotonic_ns() - self.start_monotonic_ns) // 1_000_000)

    # -------------------------------------------------------------------------
    # Pub/Sub Event Bus (No Alien Code Under Lock)
    # -------------------------------------------------------------------------

    @contextmanager
    def subscribe(self, topic: str, maxsize: int = 128) -> Iterator[queue.Queue[Any]]:
        """Subscribes a thread-safe Queue to `topic` and unregisters on exit."""
        q: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.setdefault(topic, set()).add(q)
        try:
            yield q
        finally:
            with self._lock:
                subs = self._subscribers.get(topic)
                if subs is not None:
                    subs.discard(q)

    def _snapshot_subscribers_locked(self, *topics: str) -> list[queue.Queue[Any]]:
        """Returns a snapshot of subscriber queues for the given topics while holding `_lock`."""
        queues: list[queue.Queue[Any]] = []
        seen: set[int] = set()
        for t in topics:
            for q in self._subscribers.get(t, ()):
                if id(q) not in seen:
                    seen.add(id(q))
                    queues.append(q)
        return queues

    @staticmethod
    def _publish_unlocked(queues: list[queue.Queue[Any]], event: Any) -> None:
        """Publishes `event` to all `queues` outside the lock, coalescing if full."""
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    # -------------------------------------------------------------------------
    # Animation Ticker & Frame Progression
    # -------------------------------------------------------------------------

    def advance_frame(self, delta_deg: float = 12.0) -> int:
        """Advances the synthetic animation state (rotating triangle & moving block) by one tick.

        Returns the new `frame_seq` and notifies all active screenshot stream subscribers.
        """
        with self._lock:
            if self.vm_state_v1 != 1:  # Only animate while VM is RUNNING
                return self.frame_seq
            self.frame_seq += 1
            self.animation_step += 1
            self.animation_angle_deg = (self.animation_angle_deg + delta_deg) % 360.0
            seq = self.frame_seq
            frame_queues = self._snapshot_subscribers_locked("frame")

        self._publish_unlocked(frame_queues, {"type": "tick", "seq": seq})
        return seq

    # -------------------------------------------------------------------------
    # VM & System Lifecycle
    # -------------------------------------------------------------------------

    def set_vm_state_v1(self, state_v1: int) -> None:
        """Applies a v1 VmRunState transition and synchronizes v2 VmState."""
        with self._lock:
            # v1: 1=RUNNING, 2=PAUSED, 3=RESTART, 4=SHUTDOWN, 5=TERMINATE, 6=RESET, 9=START, 10=STOP
            if state_v1 in (1, 9):  # RUNNING / START
                self.vm_state_v1 = 1
                self.vm_state_v2 = 1
            elif state_v1 in (2, 10):  # PAUSED / STOP
                self.vm_state_v1 = 2
                self.vm_state_v2 = 2
            elif state_v1 in (3, 6):  # RESTART / RESET
                self.vm_state_v1 = 1
                self.vm_state_v2 = 1
                self.active_touches.clear()
            elif state_v1 in (4, 5):  # SHUTDOWN / TERMINATE
                self.vm_state_v1 = 4
                self.vm_state_v2 = 3  # STOPPED
            else:
                self.vm_state_v1 = state_v1
            vm_queues = self._snapshot_subscribers_locked("vm", "system")

        self._publish_unlocked(
            vm_queues,
            {"type": "vm_state", "v1": self.vm_state_v1, "v2": self.vm_state_v2},
        )

    def pause_vm_v2(self) -> bool:
        """Pauses the VM via v2 HypervisorService. Returns False if not currently RUNNING."""
        with self._lock:
            if self.vm_state_v2 != 1:
                return False
            self.vm_state_v1 = 2
            self.vm_state_v2 = 2
            vm_queues = self._snapshot_subscribers_locked("vm")
        self._publish_unlocked(vm_queues, {"type": "vm_state"})
        return True

    def resume_vm_v2(self) -> bool:
        """Resumes the VM via v2 HypervisorService. Returns False if not currently PAUSED."""
        with self._lock:
            if self.vm_state_v2 != 2:
                return False
            self.vm_state_v1 = 1
            self.vm_state_v2 = 1
            vm_queues = self._snapshot_subscribers_locked("vm", "frame")
        self._publish_unlocked(vm_queues, {"type": "vm_state"})
        return True

    def reset_vm_v2(self, hard: bool = False) -> None:
        """Performs a warm or cold reset of the VM."""
        with self._lock:
            if hard:
                saved_snapshots = self.snapshots
                self._init_mutable_state()
                self.snapshots = saved_snapshots
            else:
                self.vm_state_v1 = 1
                self.vm_state_v2 = 1
                self.active_touches.clear()
            vm_queues = self._snapshot_subscribers_locked("vm", "system", "frame")
        self._publish_unlocked(vm_queues, {"type": "vm_reset", "hard": hard})

    def stop_vm_v2(
        self, save_quickboot: bool = False, screenshot_png: bytes = b""
    ) -> None:
        """Stops the VM and optionally saves to the active quickboot snapshot target."""
        with self._lock:
            if save_quickboot:
                qb_id = next(
                    (sid for sid, s in self.snapshots.items() if s.quickboot_target),
                    "default_boot",
                )
                self._save_snapshot_locked(
                    qb_id, screenshot_png=screenshot_png, quickboot=True
                )
            self.vm_state_v1 = 4
            self.vm_state_v2 = 3
            vm_queues = self._snapshot_subscribers_locked("vm")
        self._publish_unlocked(vm_queues, {"type": "vm_stopped"})

    # -------------------------------------------------------------------------
    # Displays, Brightness, Rotation & Foldable Posture
    # -------------------------------------------------------------------------

    def get_effective_display_dims(self, display_id: int = 0) -> tuple[int, int]:
        """Returns effective (width, height) accounting for folded state and rotation."""
        with self._lock:
            disp = self.displays.get(display_id)
            if disp is None:
                disp = self.displays[0]
            if (
                display_id == 0 and self.preset.is_foldable and self.posture == 1
            ):  # CLOSED
                base_w, base_h = (
                    self.preset.folded_width,
                    self.preset.folded_height,
                )
            else:
                base_w, base_h = (disp.width, disp.height)
            rot_deg = (int(round(disp.rotation_deg / 90.0)) * 90) % 360
            if rot_deg in (90, 270):
                return (base_h, base_w)
            return (base_w, base_h)

    def set_display_configurations_v1(
        self, configs: list[tuple[int, int, int, int, int]]
    ) -> list[DisplayRecord]:
        """Replaces secondary display configurations from v1 `setDisplayConfigurations`."""
        with self._lock:
            new_displays: dict[int, DisplayRecord] = {0: self.displays[0]}
            for width, height, dpi, flags, disp_id in configs:
                if disp_id == 0:
                    d0 = self.displays[0]
                    d0.width = width
                    d0.height = height
                    d0.dpi = dpi
                    d0.flags = flags
                    new_displays[0] = d0
                else:
                    new_displays[disp_id] = DisplayRecord(
                        display_id=disp_id,
                        width=width,
                        height=height,
                        dpi=dpi,
                        flags=flags,
                    )
            self.displays = new_displays
            self.frame_seq += 1
            result = [copy.copy(d) for _, d in sorted(self.displays.items())]
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "displays_changed"})
        return result

    def create_display_v2(
        self,
        width: int,
        height: int,
        dpi: int,
        refresh_rate_hz: float = 60.0,
        flags: int = 0,
        display_id: int | None = None,
    ) -> DisplayRecord | None:
        """Creates a virtual secondary display for v2 `DisplayService.CreateDisplay`.

        Returns None if the maximum display capacity (`user_configurable_displays + 1`)
        has already been reached.
        """
        with self._lock:
            max_count = self.preset.user_configurable_displays + 1
            if len(self.displays) >= max_count and (
                display_id is None or display_id == 0 or display_id not in self.displays
            ):
                return None
            if display_id is None or display_id == 0:
                candidate = 1
                while candidate in self.displays:
                    candidate += 1
                display_id = candidate
            rec = DisplayRecord(
                display_id=display_id,
                width=width,
                height=height,
                dpi=dpi,
                flags=flags,
                refresh_rate_hz=refresh_rate_hz if refresh_rate_hz > 0 else 60.0,
            )
            self.displays[display_id] = rec
            self.frame_seq += 1
            out = copy.copy(rec)
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "displays_changed"})
        return out

    def update_display_v2(
        self,
        display_id: int,
        width: int | None = None,
        height: int | None = None,
        dpi: int | None = None,
        rotation_deg: int | None = None,
        rotation_locked: bool | None = None,
        power_mode: int | None = None,
        brightness_percent: int | None = None,
    ) -> DisplayRecord | None:
        """Updates an existing display for v2 `DisplayService.UpdateDisplay`."""
        with self._lock:
            disp = self.displays.get(display_id)
            if disp is None:
                return None
            if width is not None:
                disp.width = width
            if height is not None:
                disp.height = height
            if dpi is not None:
                disp.dpi = dpi
            if rotation_deg is not None:
                disp.rotation_deg = (int(round(rotation_deg / 90.0)) * 90) % 360
                # Also align pose roll if not locked
                self.pose_rotation_deg = (
                    self.pose_rotation_deg[0],
                    self.pose_rotation_deg[1],
                    float(
                        disp.rotation_deg
                        if disp.rotation_deg <= 180
                        else disp.rotation_deg - 360
                    ),
                )
            if rotation_locked is not None:
                disp.rotation_locked = rotation_locked
            if power_mode is not None:
                disp.power_mode = power_mode
            if brightness_percent is not None:
                disp.brightness_percent = brightness_percent
                if display_id == 0:
                    self.brightness_targets[0] = brightness_percent
            self.frame_seq += 1
            out = copy.copy(disp)
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "displays_changed"})
        return out

    def delete_display_v2(self, display_id: int) -> bool:
        """Deletes a virtual secondary display (`display_id > 0`)."""
        with self._lock:
            if display_id <= 0 or display_id not in self.displays:
                return False
            del self.displays[display_id]
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "displays_changed"})
        return True

    def set_display_mode(self, mode: int) -> None:
        """Updates resizable display mode (0=PHONE, 1=FOLDABLE, 2=TABLET, 3=DESKTOP)."""
        mode_dims = {
            0: (1080, 2400, 420),
            1: (2208, 1840, 420),
            2: (2560, 1600, 320),
            3: (1920, 1080, 160),
        }
        with self._lock:
            self.display_mode = mode
            if mode in mode_dims:
                w, h, dpi = mode_dims[mode]
                d0 = self.displays[0]
                d0.width = w
                d0.height = h
                d0.dpi = dpi
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "display_mode", "mode": mode})

    def set_brightness(self, target: int, value: int) -> None:
        """Updates backlight brightness (`value` in [0, 100])."""
        with self._lock:
            clamped = max(0, min(100, value))
            self.brightness_targets[target] = clamped
            if target == 0:
                self.displays[0].brightness_percent = clamped
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked(
                "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(
            queues, {"type": "brightness", "target": target, "value": clamped}
        )

    # -------------------------------------------------------------------------
    # Posture & Sensors (Cross-coupled v1 & v2)
    # -------------------------------------------------------------------------

    @staticmethod
    def _default_angle_for_posture(posture: int) -> float:
        return {
            1: 0.0,  # CLOSED
            2: 90.0,  # HALF_OPENED
            3: 180.0,  # OPENED
            4: 360.0,  # FLIPPED
            5: 300.0,  # TENT
        }.get(posture, 180.0)

    @staticmethod
    def _posture_for_angle(angle: float) -> int:
        if angle <= 30.0:
            return 1  # CLOSED
        if angle < 150.0:
            return 2  # HALF_OPENED
        if angle <= 210.0:
            return 3  # OPENED
        if angle < 330.0:
            return 5  # TENT
        return 4  # FLIPPED

    def set_posture(self, posture: int) -> bool:
        """Sets discrete foldable posture (`1..5`) and snaps hinge angles accordingly."""
        if posture not in (1, 2, 3, 4, 5):
            return False
        with self._lock:
            self.posture = posture
            default_angle = self._default_angle_for_posture(posture)
            if self.hinge_angles:
                self.hinge_angles = [default_angle for _ in self.hinge_angles]
            else:
                self.hinge_angles = [default_angle]
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked(
                "posture", "sensor", "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(queues, {"type": "posture", "posture": posture})
        return True

    def set_hinge_angle(self, hinge_id: int, angle_deg: float) -> bool:
        """Sets a specific hinge angle (`[0.0, 360.0]`) and derives the discrete posture."""
        if not math.isfinite(angle_deg) or angle_deg < 0.0 or angle_deg > 360.0:
            return False
        with self._lock:
            while len(self.hinge_angles) <= hinge_id:
                self.hinge_angles.append(180.0)
            self.hinge_angles[hinge_id] = float(angle_deg)
            if hinge_id == 0:
                self.posture = self._posture_for_angle(angle_deg)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked(
                "posture", "sensor", "displays", "notification_v1", "frame"
            )

        self._publish_unlocked(
            queues, {"type": "hinge", "hinge_id": hinge_id, "angle": angle_deg}
        )
        return True

    def set_rollable(self, rollable_id: int, extension_pct: float) -> bool:
        """Sets a rollable extension percentage (`[0.0, 100.0]`)."""
        if (
            not math.isfinite(extension_pct)
            or extension_pct < 0.0
            or extension_pct > 100.0
        ):
            return False
        with self._lock:
            while len(self.rollable_percents) <= rollable_id:
                self.rollable_percents.append(0.0)
            self.rollable_percents[rollable_id] = float(extension_pct)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("posture", "sensor", "frame")

        self._publish_unlocked(queues, {"type": "rollable", "rollable_id": rollable_id})
        return True

    def set_pose_rotation_deg(self, x: float, y: float, z: float) -> None:
        """Updates 3D device rotation in degrees and auto-rotates primary display if unlocked."""
        with self._lock:
            self.pose_rotation_deg = (float(x), float(y), float(z))
            disp0 = self.displays[0]
            if not disp0.rotation_locked:
                # Map roll angle z to coarse display rotation (0, 90, 180, 270)
                norm_z = int(round(z / 90.0) * 90) % 360
                disp0.rotation_deg = norm_z
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("sensor", "displays", "frame")

        self._publish_unlocked(queues, {"type": "pose_rotation"})

    def compute_imu_telemetry(self) -> dict[str, Any]:
        """Synthesizes frame-consistent IMU readings from 3D pose, environment, and raw overrides."""
        with self._lock:
            pitch_deg, yaw_deg, roll_deg = self.pose_rotation_deg
            pitch = math.radians(pitch_deg)
            yaw = math.radians(yaw_deg)
            roll = math.radians(roll_deg)

            # Standard gravity (9.81 m/s^2) rotated by device Euler angles.
            # At (0, 0, 0) upright portrait: (0.0, 9.81, 0.0)
            gx = -9.81 * math.sin(roll) * math.cos(pitch)
            gy = 9.81 * math.cos(roll) * math.cos(pitch)
            gz = 9.81 * math.sin(pitch)
            accel = self.raw_sensor_overrides.get(0, (gx, gy, gz))

            gyro = self.raw_sensor_overrides.get(1, (0.0, 0.0, 0.0))

            # Rotate world magnetic field vector into device frame
            bx, by, bz = self.env_magnetic_field_ut
            mx = bx * math.cos(yaw) - by * math.sin(yaw)
            my = bx * math.sin(yaw) + by * math.cos(yaw)
            mz = bz
            mag = self.raw_sensor_overrides.get(2, (mx, my, mz))

            orient = self.raw_sensor_overrides.get(3, (yaw_deg, pitch_deg, roll_deg))
            accel_uncal = self.raw_sensor_overrides.get(11, accel)
            gyro_uncal = self.raw_sensor_overrides.get(12, gyro)
            mag_uncal = self.raw_sensor_overrides.get(13, mag)

            return {
                "accelerometer": accel,
                "gyroscope": gyro,
                "magnetometer": mag,
                "orientation_degrees": orient,
                "accelerometer_uncalibrated": accel_uncal,
                "gyroscope_uncalibrated": gyro_uncal,
                "magnetometer_uncalibrated": mag_uncal,
                "active_overrides": sorted(self.raw_sensor_overrides.keys()),
            }

    def notify_sensors_changed(self) -> None:
        """Notifies sensor subscribers after mutating pose, environment, or raw overrides."""
        with self._lock:
            queues = self._snapshot_subscribers_locked("sensor")
        self._publish_unlocked(queues, {"type": "sensor_changed"})

    # -------------------------------------------------------------------------
    # Battery, Location, Clipboard, Input & Telephony
    # -------------------------------------------------------------------------

    def update_battery(
        self,
        charge_level: int | None = None,
        charger_v1: int | None = None,
        health_v1: int | None = None,
        status_v1: int | None = None,
        is_present: bool | None = None,
        temperature_c: float | None = None,
    ) -> None:
        """Updates battery state and notifies v2 `StreamBatteryState` + frame HUD."""
        with self._lock:
            if charge_level is not None:
                self.battery_charge_level = max(0, min(100, int(charge_level)))
            if charger_v1 is not None:
                self.battery_charger_v1 = charger_v1
            if health_v1 is not None:
                self.battery_health_v1 = health_v1
            if is_present is not None:
                self.battery_is_present = bool(is_present)
            if temperature_c is not None:
                self.battery_temperature_c = float(temperature_c)

            if status_v1 is not None:
                self.battery_status_v1 = status_v1
            else:
                # Auto-derive charging status when not explicitly provided
                if not self.battery_is_present:
                    self.battery_status_v1 = 3  # NOT_CHARGING
                elif self.battery_charger_v1 in (1, 2, 3):
                    self.battery_status_v1 = (
                        4 if self.battery_charge_level >= 100 else 1
                    )
                else:
                    self.battery_status_v1 = 2  # DISCHARGING

            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("battery", "frame")

        self._publish_unlocked(queues, {"type": "battery"})

    def update_location(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        altitude: float | None = None,
        speed_mps: float | None = None,
        bearing_deg: float | None = None,
        accuracy_m: float | None = None,
        satellites: int | None = None,
        time_us: int | None = None,
    ) -> None:
        """Updates WGS84 location fix and notifies `StreamLocationState` subscribers."""
        with self._lock:
            if latitude is not None:
                self.gps_latitude = float(latitude)
            if longitude is not None:
                self.gps_longitude = float(longitude)
            if altitude is not None:
                self.gps_altitude = float(altitude)
            if speed_mps is not None:
                self.gps_speed_mps = float(speed_mps)
            if bearing_deg is not None:
                self.gps_bearing_deg = float(bearing_deg)
            if accuracy_m is not None:
                self.gps_accuracy_m = float(accuracy_m)
            if satellites is not None:
                self.gps_satellites = int(satellites)
            self.gps_time_us = (
                time_us if time_us is not None else int(time.time() * 1_000_000)
            )
            queues = self._snapshot_subscribers_locked("location")

        self._publish_unlocked(queues, {"type": "location"})

    def set_clipboard(self, text: str, source_peer: str = "") -> int:
        """Updates shared host-guest clipboard text and broadcasts to subscribers."""
        with self._lock:
            self.clipboard_text = text
            self.clipboard_generation += 1
            gen = self.clipboard_generation
            queues = self._snapshot_subscribers_locked("clipboard")

        self._publish_unlocked(
            queues,
            {"text": text, "generation": gen, "source_peer": source_peer},
        )
        return gen

    def record_touch(
        self,
        pointer_id: int,
        x_norm: float,
        y_norm: float,
        pressure: float,
        display_id: int = 0,
        tool: str = "touch",
    ) -> None:
        """Records a touch or stylus contact update in normalized `[0.0, 1.0]` coordinates."""
        with self._lock:
            ts = time.monotonic_ns()
            if pressure > 0.0:
                self.active_touches[pointer_id] = TouchPoint(
                    pointer_id=pointer_id,
                    x_norm=max(0.0, min(1.0, float(x_norm))),
                    y_norm=max(0.0, min(1.0, float(y_norm))),
                    pressure=float(pressure),
                    display_id=display_id,
                    tool=tool,
                    timestamp_ns=ts,
                )
            else:
                self.active_touches.pop(pointer_id, None)
            event_rec = {
                "kind": "touch",
                "pointer_id": pointer_id,
                "x_norm": x_norm,
                "y_norm": y_norm,
                "pressure": pressure,
                "display_id": display_id,
                "tool": tool,
                "timestamp_ns": ts,
            }
            self.input_history.append(event_rec)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("frame", "input_activity")

        self._publish_unlocked(queues, event_rec)

    def record_mouse(
        self, x_norm: float, y_norm: float, buttons: int, display_id: int = 0
    ) -> None:
        """Records a mouse pointer update in normalized `[0.0, 1.0]` coordinates."""
        with self._lock:
            ts = time.monotonic_ns()
            self.mouse_x_norm = max(0.0, min(1.0, float(x_norm)))
            self.mouse_y_norm = max(0.0, min(1.0, float(y_norm)))
            self.mouse_buttons = int(buttons)
            self.mouse_display_id = display_id
            event_rec = {
                "kind": "mouse",
                "x_norm": self.mouse_x_norm,
                "y_norm": self.mouse_y_norm,
                "buttons": buttons,
                "display_id": display_id,
                "timestamp_ns": ts,
            }
            self.input_history.append(event_rec)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("frame", "input_activity")

        self._publish_unlocked(queues, event_rec)

    def record_key(self, key_label: str, keycode: int = 0, action: int = 2) -> None:
        """Records a keyboard or hardware navigation key event."""
        with self._lock:
            ts = time.monotonic_ns()
            self.last_key_event = key_label or f"keycode:{keycode}"
            event_rec = {
                "kind": "key",
                "key": key_label,
                "keycode": keycode,
                "action": action,
                "timestamp_ns": ts,
            }
            self.input_history.append(event_rec)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("frame", "input_activity")

        self._publish_unlocked(queues, event_rec)

    def record_rotary(self, delta: int) -> None:
        """Records a rotary encoder / watch crown scroll step."""
        with self._lock:
            ts = time.monotonic_ns()
            self.last_rotary_delta = int(delta)
            event_rec = {"kind": "rotary", "delta": int(delta), "timestamp_ns": ts}
            self.input_history.append(event_rec)
            self.frame_seq += 1
            queues = self._snapshot_subscribers_locked("frame", "input_activity")

        self._publish_unlocked(queues, event_rec)

    def transition_call_v2(self, number: str, action: int) -> CallEntry | None:
        """Applies voice call state transition (`1=RING, 2=DISCONNECT, 3=ACCEPT, 4=HOLD, 5=UNHOLD`)."""
        with self._lock:
            existing = self.active_calls.get(number)
            if action == 1:  # RING
                entry = CallEntry(
                    call_id=f"call-{uuid.uuid4().hex[:8]}",
                    caller_phone_number=number,
                    status=1,  # INCOMING
                )
                self.active_calls[number] = entry
                result = copy.copy(entry)
            elif existing is None:
                return None
            elif action == 3:  # ACCEPT
                if existing.status != 1:
                    return None
                existing.status = 2  # ACTIVE
                result = copy.copy(existing)
            elif action == 4:  # HOLD
                if existing.status != 2:
                    return None
                existing.status = 3  # HELD
                result = copy.copy(existing)
            elif action == 5:  # UNHOLD
                if existing.status != 3:
                    return None
                existing.status = 2  # ACTIVE
                result = copy.copy(existing)
            elif action == 2:  # DISCONNECT
                existing.status = 5  # DISCONNECTED
                result = copy.copy(existing)
                del self.active_calls[number]
            else:
                return None
            queues = self._snapshot_subscribers_locked("telephony")

        self._publish_unlocked(queues, {"type": "call", "call": result})
        return result

    def add_logcat_entry(
        self, tag: str, msg: str, level: int = 4, pid: int = 1000, tid: int = 1000
    ) -> dict[str, Any]:
        """Appends a structured logcat message and notifies active logcat streams."""
        with self._lock:
            entry = {
                "timestamp": int(time.time() * 1000),
                "pid": pid,
                "tid": tid,
                "level": level,
                "tag": tag,
                "msg": msg,
            }
            self.logcat_entries.append(entry)
            queues = self._snapshot_subscribers_locked("logcat")

        self._publish_unlocked(queues, entry)
        return entry

    def emit_notification_v1(self, event_name: str, payload: Any = None) -> None:
        """Emits a live notification event to v1 `streamNotification` subscribers."""
        with self._lock:
            queues = self._snapshot_subscribers_locked("notification_v1")
        self._publish_unlocked(queues, {"event": event_name, "payload": payload})

    # -------------------------------------------------------------------------
    # Snapshots (Unified v1 & v2 Catalog)
    # -------------------------------------------------------------------------

    def _capture_state_dict_locked(self) -> dict[str, Any]:
        return {
            "displays": copy.deepcopy(self.displays),
            "display_mode": self.display_mode,
            "brightness_targets": dict(self.brightness_targets),
            "posture": self.posture,
            "hinge_angles": list(self.hinge_angles),
            "rollable_percents": list(self.rollable_percents),
            "pose_rotation_deg": self.pose_rotation_deg,
            "pose_position_m": self.pose_position_m,
            "pose_velocity_mps": self.pose_velocity_mps,
            "env_light_lux": self.env_light_lux,
            "env_temperature_c": self.env_temperature_c,
            "battery_charge_level": self.battery_charge_level,
            "battery_charger_v1": self.battery_charger_v1,
            "battery_health_v1": self.battery_health_v1,
            "battery_status_v1": self.battery_status_v1,
            "gps_latitude": self.gps_latitude,
            "gps_longitude": self.gps_longitude,
            "gps_altitude": self.gps_altitude,
            "clipboard_text": self.clipboard_text,
        }

    def _restore_state_dict_locked(self, snap: dict[str, Any]) -> None:
        self.displays = copy.deepcopy(snap["displays"])
        self.display_mode = snap["display_mode"]
        self.brightness_targets = dict(snap["brightness_targets"])
        self.posture = snap["posture"]
        self.hinge_angles = list(snap["hinge_angles"])
        self.rollable_percents = list(snap["rollable_percents"])
        self.pose_rotation_deg = snap["pose_rotation_deg"]
        self.pose_position_m = snap["pose_position_m"]
        self.pose_velocity_mps = snap["pose_velocity_mps"]
        self.env_light_lux = snap["env_light_lux"]
        self.env_temperature_c = snap["env_temperature_c"]
        self.battery_charge_level = snap["battery_charge_level"]
        self.battery_charger_v1 = snap["battery_charger_v1"]
        self.battery_health_v1 = snap["battery_health_v1"]
        self.battery_status_v1 = snap["battery_status_v1"]
        self.gps_latitude = snap["gps_latitude"]
        self.gps_longitude = snap["gps_longitude"]
        self.gps_altitude = snap["gps_altitude"]
        self.clipboard_text = snap["clipboard_text"]
        self.frame_seq += 1

    def _save_snapshot_locked(
        self,
        snapshot_id: str,
        display_name: str = "",
        description: str = "",
        screenshot_png: bytes = b"",
        imported: bool = False,
        quickboot: bool = False,
    ) -> SnapshotEntry:
        rot_skin = (self.displays[0].rotation_deg // 90) % 4
        folded = self.preset.is_foldable and self.posture == 1
        entry = SnapshotEntry(
            snapshot_id=snapshot_id,
            display_name=display_name or snapshot_id,
            description=description or f"Snapshot {snapshot_id}",
            creation_time_s=int(time.time()),
            size_bytes=max(4096, len(screenshot_png) + 4096),
            screenshot_png=screenshot_png,
            state_snapshot=self._capture_state_dict_locked(),
            rotation_skin=rot_skin,
            folded=folded,
            imported=imported,
            quickboot_target=quickboot,
        )
        self.snapshots[snapshot_id] = entry
        return copy.copy(entry)

    def save_snapshot(
        self,
        snapshot_id: str,
        display_name: str = "",
        description: str = "",
        screenshot_png: bytes = b"",
        imported: bool = False,
    ) -> tuple[bool, str, SnapshotEntry | None]:
        """Saves current `EmulatorState` into `self.snapshots[snapshot_id]`."""
        with self._lock:
            if self.loaded_imported_snapshot and not imported:
                return (
                    False,
                    "Cannot save snapshot after loading an imported/pushed snapshot.",
                    None,
                )
            entry = self._save_snapshot_locked(
                snapshot_id=snapshot_id,
                display_name=display_name,
                description=description,
                screenshot_png=screenshot_png,
                imported=imported,
            )
            return (True, "", entry)

    def load_snapshot(self, snapshot_id: str) -> tuple[bool, str, SnapshotEntry | None]:
        """Restores `EmulatorState` from `self.snapshots[snapshot_id]`."""
        with self._lock:
            entry = self.snapshots.get(snapshot_id)
            if entry is None:
                return (False, f"Snapshot '{snapshot_id}' does not exist.", None)
            self._restore_state_dict_locked(entry.state_snapshot)
            if entry.imported:
                self.loaded_imported_snapshot = True
            out = copy.copy(entry)
            queues = self._snapshot_subscribers_locked(
                "frame",
                "displays",
                "posture",
                "sensor",
                "battery",
                "location",
                "clipboard",
            )

        self._publish_unlocked(
            queues, {"type": "snapshot_loaded", "snapshot_id": snapshot_id}
        )
        return (True, "", out)
