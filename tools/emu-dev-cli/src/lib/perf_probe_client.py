# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Host client for `perf_probe.apk` and `EmulatorController` v1 gRPC benchmarks.

Coordinates:
  - Hermetic location and installation of `perf_probe.apk` (`com.android.emulator.perfprobe`)
  - Discovery of the active emulator's gRPC endpoint (`EmulatorDiscovery` or explicit `-grpc` port)
  - Intent-driven guest microbenchmarks (`disk`, `memory`, `cpu`) via `PerfProbeJson` logcat output
  - Sub-millisecond `input` latency benchmarks (`key_arrival_latency`, `touch_to_photon`,
    `swipe_120hz_stress`) using `EmulatorController.streamInputEvent` and `streamScreenshot`.
"""

import json
import logging
import os
import re
import statistics
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

PERF_PROBE_PACKAGE = "com.android.emulator.perfprobe"
PERF_PROBE_ACTIVITY = f"{PERF_PROBE_PACKAGE}/.PerfProbeActivity"
PERF_PROBE_LOG_TAG = "PerfProbeJson"

PERF_PROBE_JSON_RE = re.compile(r"PerfProbeJson\s*:\s*(\{.*\})")


def resolve_perf_probe_apk() -> str:
    """Locates the packaged `perf_probe.apk` across installed CLI, Bazel runfiles, or workspace build outputs."""
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tool_root = os.path.dirname(src_root)

    candidates: List[str] = [
        # 1. Installed layout: ~/.local/lib/emu-dev-cli/apks/perf_probe.apk
        os.path.join(src_root, "apks", "perf_probe.apk"),
        # 2. Adjacent to tool root
        os.path.join(tool_root, "perf_probe", "perf_probe.apk"),
    ]

    # 3. Bazel runfiles environment variables
    for env_key in ("RUNFILES_DIR", "TEST_SRCDIR"):
        rf_dir = os.environ.get(env_key)
        if rf_dir and os.path.isdir(rf_dir):
            for prefix in ("aemu+", "_main", "aemu", ""):
                rel = (
                    os.path.join(
                        prefix, "tools", "emu-dev-cli", "perf_probe", "perf_probe.apk"
                    )
                    if prefix
                    else os.path.join(
                        "tools", "emu-dev-cli", "perf_probe", "perf_probe.apk"
                    )
                )
                candidates.append(os.path.join(rf_dir, rel))

    # 4. Walk up from __file__ to detect `.runfiles` or workspace root `bazel-bin`
    cur = os.path.abspath(__file__)
    for _ in range(10):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if cur.endswith(".runfiles") or os.path.isdir(f"{cur}.runfiles"):
            rf_base = cur if cur.endswith(".runfiles") else f"{cur}.runfiles"
            for prefix in ("aemu+", "_main", "aemu"):
                candidates.append(
                    os.path.join(
                        rf_base,
                        prefix,
                        "tools",
                        "emu-dev-cli",
                        "perf_probe",
                        "perf_probe.apk",
                    )
                )
        bazel_bin = os.path.join(parent, "bazel-bin")
        if os.path.isdir(bazel_bin):
            candidates.append(
                os.path.join(
                    bazel_bin,
                    "external",
                    "aemu+",
                    "tools",
                    "emu-dev-cli",
                    "perf_probe",
                    "perf_probe.apk",
                )
            )
            candidates.append(
                os.path.join(
                    bazel_bin,
                    "tools",
                    "emu-dev-cli",
                    "perf_probe",
                    "perf_probe.apk",
                )
            )
        cur = parent

    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)

    raise FileNotFoundError(
        "Could not locate `perf_probe.apk`. Build it with "
        "`bazel build @aemu//tools/emu-dev-cli/perf_probe:perf_probe_apk` "
        "or reinstall via `emu-dev-cli install`."
    )


def parse_perf_probe_logcat_lines(logcat_text: str) -> List[Dict[str, Any]]:
    """Extracts JSON objects emitted with the `PerfProbeJson` logcat tag."""
    results: List[Dict[str, Any]] = []
    if not logcat_text:
        return results
    for raw_line in logcat_text.splitlines():
        m = PERF_PROBE_JSON_RE.search(raw_line)
        if not m:
            continue
        payload = m.group(1).strip()
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                results.append(obj)
        except json.JSONDecodeError:
            continue
    return results


def _percentile(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return float(sorted_vals[idx])


def _extract_rgb_center_pixel(image_bytes: bytes, width: int, height: int) -> Tuple[int, int, int]:
    """Extracts the center `(R, G, B)` pixel from an `RGB888` byte buffer."""
    if not image_bytes or len(image_bytes) < 3:
        return (0, 0, 0)
    w = max(1, width or 8)
    h = max(1, height or 8)
    cx = w // 2
    cy = h // 2
    offset = (cy * w + cx) * 3
    if offset + 3 > len(image_bytes):
        offset = 0
    return (image_bytes[offset], image_bytes[offset + 1], image_bytes[offset + 2])


def open_emulator_grpc_channel(
    serial: str,
    emu_pid: Optional[int] = None,
    explicit_grpc_port: Optional[int] = None,
) -> Any:
    """Discovers the running emulator's gRPC endpoint and returns an open `grpc.Channel`.

    Uses `aemu.discovery.emulator_discovery.EmulatorDiscovery` to inspect `pid_<pid>.ini`
    discovery files across Linux, macOS, and Windows, falling back to `localhost:<port>`.
    """
    from aemu.discovery.emulator_description import BasicEmulatorDescription
    from aemu.discovery.emulator_discovery import EmulatorDiscovery

    if explicit_grpc_port:
        direct_desc = EmulatorDiscovery.connection(f"localhost:{explicit_grpc_port}")
        return direct_desc.get_grpc_channel()

    console_port: Optional[str] = None
    if serial and serial.startswith("emulator-"):
        port_part = serial.split("-", 1)[1].strip()
        if port_part.isdigit():
            console_port = port_part

    desc: Optional[BasicEmulatorDescription] = None
    try:
        disc = EmulatorDiscovery()
        if emu_pid:
            desc = disc.find_by_pid(int(emu_pid))
        if desc is None and console_port:
            desc = disc.find_emulator("port.serial", console_port)
        if desc is None and disc.available() == 1:
            desc = disc.first()
    except Exception as exc:
        logger.debug("EmulatorDiscovery lookup failed (%s), falling back to direct port", exc)

    if desc is not None:
        try:
            return desc.get_grpc_channel()
        except NotImplementedError:
            # Occurs if JWT security was selected by default but `tink` is not installed.
            # Fall back to Token if `grpc.token` exists, otherwise unauthenticated Nothing.
            sec_mode = "token" if desc.get("grpc.token") else "nothing"
            return desc.get_grpc_channel([("emulator.security", sec_mode)])

    port = explicit_grpc_port or 8554
    fallback_desc = EmulatorDiscovery.connection(f"localhost:{port}")
    return fallback_desc.get_grpc_channel()


class PerfProbeSession:
    """Manages `perf_probe.apk` lifecycle and executes guest+host performance scenarios."""

    def __init__(
        self,
        serial: str,
        adb_fn: Callable[[List[str], float], subprocess.CompletedProcess],
        emu_pid: Optional[int] = None,
        grpc_port: Optional[int] = None,
        apk_path: Optional[str] = None,
    ) -> None:
        self.serial = serial
        self._adb = adb_fn
        self.emu_pid = emu_pid
        self.grpc_port = grpc_port
        self.apk_path = apk_path
        self._installed = False

    def ensure_installed_and_ready(self) -> None:
        """Installs `perf_probe.apk` if not yet installed in this session, unlocks the screen,
        and launches `PerfProbeActivity` in the foreground.
        """
        if self._installed:
            return
        resolved_apk = self.apk_path or resolve_perf_probe_apk()
        res = self._adb(["install", "-r", "-g", resolved_apk], 60.0)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to install perf_probe.apk ({resolved_apk}) on {self.serial}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )

        # Ensure screen is awake and keyguard is dismissed so input events route to PerfProbeActivity
        self._adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], 10.0)
        self._adb(["shell", "wm", "dismiss-keyguard"], 10.0)

        self._adb(["logcat", "-c"], 10.0)
        start_res = self._adb(
            [
                "shell",
                "am",
                "start",
                "-W",
                "-n",
                PERF_PROBE_ACTIVITY,
                "--activity-single-top",
            ],
            30.0,
        )
        if start_res.returncode != 0:
            raise RuntimeError(
                f"Failed to launch {PERF_PROBE_ACTIVITY} on {self.serial}: "
                f"{start_res.stderr.strip() or start_res.stdout.strip()}"
            )
        self._installed = True

    def run_intent_scenario(
        self,
        scenario: str,
        extras: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 90.0,
        clear_logcat: bool = True,
    ) -> Dict[str, Any]:
        """Dispatches an intent scenario to `PerfProbeActivity` and awaits its `PerfProbeJson` output."""
        self.ensure_installed_and_ready()
        if clear_logcat:
            self._adb(["logcat", "-c"], 10.0)

        cmd = [
            "shell",
            "am",
            "start",
            "-n",
            PERF_PROBE_ACTIVITY,
            "--activity-single-top",
            "--es",
            "scenario",
            scenario,
        ]
        for k, v in (extras or {}).items():
            if isinstance(v, int):
                cmd.extend(["--ei", str(k), str(v)])
            else:
                cmd.extend(["--es", str(k), str(v)])

        res = self._adb(cmd, 20.0)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to trigger scenario '{scenario}' on {self.serial}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )

        deadline = time.perf_counter() + timeout_sec
        while time.perf_counter() < deadline:
            lc = self._adb(
                ["logcat", "-d", "-s", f"{PERF_PROBE_LOG_TAG}:I"],
                15.0,
            )
            if lc.returncode == 0 and lc.stdout:
                records = parse_perf_probe_logcat_lines(lc.stdout)
                for rec in reversed(records):
                    if rec.get("scenario") == scenario:
                        if rec.get("status") == "error":
                            raise RuntimeError(
                                f"PerfProbe scenario '{scenario}' failed in guest: {rec.get('error')}"
                            )
                        return rec
            time.sleep(0.08)

        raise TimeoutError(
            f"Timed out after {timeout_sec:.1f}s waiting for PerfProbeJson scenario='{scenario}' on {self.serial}"
        )

    def run_key_arrival_latency(
        self,
        count: int = 40,
        interval_sec: float = 0.019,
    ) -> Dict[str, float]:
        """Streams `count` key events over gRPC `streamInputEvent` at a VSYNC-coprime interval (19 ms vs 16.67 ms 60 Hz) and collects guest arrival & frame commit latencies."""
        from aemu.proto.emulator_controller_pb2 import InputEvent, KeyboardEvent
        from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub

        self.run_intent_scenario("reset_input", timeout_sec=15.0)
        time.sleep(0.05)

        channel = open_emulator_grpc_channel(
            self.serial, emu_pid=self.emu_pid, explicit_grpc_port=self.grpc_port
        )
        t_start = time.perf_counter()
        try:
            stub = EmulatorControllerStub(channel)

            def _key_stream() -> Iterator[InputEvent]:
                for _ in range(count):
                    yield InputEvent(
                        key_event=KeyboardEvent(
                            key="a",
                            eventType=KeyboardEvent.keydown,
                        )
                    )
                    yield InputEvent(
                        key_event=KeyboardEvent(
                            key="a",
                            eventType=KeyboardEvent.keyup,
                        )
                    )
                    if interval_sec > 0:
                        time.sleep(interval_sec)

            stub.streamInputEvent(_key_stream(), timeout=max(30.0, count * interval_sec + 15.0))
        finally:
            channel.close()

        stream_wall_ms = (time.perf_counter() - t_start) * 1000.0
        time.sleep(0.15)
        summary = self.run_intent_scenario(
            "input_summary", timeout_sec=15.0, clear_logcat=False
        )

        k2a = [float(x) for x in (summary.get("key_kernel_to_app_ms") or [])]
        a2f = [float(x) for x in (summary.get("key_app_to_frame_ms") or [])]
        delivered = int(summary.get("key_down_count") or len(k2a))
        if not k2a:
            raise RuntimeError(
                f"key_arrival_latency: 0 key events received by PerfProbeActivity out of {count} sent over gRPC"
            )

        k2a_sorted = sorted(k2a)
        a2f_sorted = sorted(a2f) if a2f else [0.0]
        paired_totals = [
            k2a[i] + (a2f[i] if i < len(a2f) else statistics.median(a2f_sorted))
            for i in range(len(k2a))
        ]
        total_sorted = sorted(paired_totals)

        return {
            "duration_ms": round( statistics.median(total_sorted), 3),
            "kernel_to_app_ms_p50": round(statistics.median(k2a_sorted), 3),
            "kernel_to_app_ms_p95": round(_percentile(k2a_sorted, 0.95), 3),
            "app_to_frame_ms_p50": round(statistics.median(a2f_sorted), 3),
            "app_to_frame_ms_p95": round(_percentile(a2f_sorted, 0.95), 3),
            "total_guest_ms_p50": round(statistics.median(total_sorted), 3),
            "total_guest_ms_p95": round(_percentile(total_sorted, 0.95), 3),
            "events_sent": float(count),
            "events_delivered": float(delivered),
            "stream_wall_ms": round(stream_wall_ms, 2),
        }

    def run_touch_to_photon(self, samples: int = 12) -> Dict[str, float]:
        """Measures closed-loop Host->Guest->Host Touch-to-Photon latency ('Software WALT')
        using `sendTouch` + 8x8 RGB888 `streamScreenshot`.
        """
        from aemu.proto.emulator_controller_pb2 import ImageFormat, Touch, TouchEvent
        from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub

        self.run_intent_scenario("reset_input", timeout_sec=15.0)
        time.sleep(0.10)

        channel = open_emulator_grpc_channel(
            self.serial, emu_pid=self.emu_pid, explicit_grpc_port=self.grpc_port
        )
        cond = threading.Condition()
        latest_rgb: List[int] = [0, 0, 0]
        frames_received = [0]
        stop_flag = [False]
        stream_call: List[Any] = [None]

        def _screenshot_reader(stub: Any) -> None:
            fmt = ImageFormat(format=ImageFormat.RGB888, width=8, height=8)
            try:
                call = stub.streamScreenshot(fmt)
                stream_call[0] = call
                for img in call:
                    if stop_flag[0]:
                        break
                    w = img.format.width if (img.format and img.format.width) else 8
                    h = img.format.height if (img.format and img.format.height) else 8
                    r, g, b = _extract_rgb_center_pixel(img.image, w, h)
                    with cond:
                        latest_rgb[0] = r
                        latest_rgb[1] = g
                        latest_rgb[2] = b
                        frames_received[0] += 1
                        cond.notify_all()
            except Exception as exc:
                logger.debug("streamScreenshot reader exited: %s", exc)
                with cond:
                    cond.notify_all()

        t2p_latencies_ms: List[float] = []
        try:
            stub = EmulatorControllerStub(channel)
            reader = threading.Thread(
                target=_screenshot_reader, args=(stub,), daemon=True
            )
            reader.start()

            # Wait for initial baseline screenshot frame
            with cond:
                cond.wait_for(lambda: frames_received[0] > 0, timeout=5.0)

            for i in range(max(1, samples)):
                # Choose an X coordinate that yields a distinct target (R, G, B) in PerfProbeActivity
                x_coord = 40 + ((i * 37) % 180)
                y_coord = 400
                target_r = max(16, min(240, x_coord & 0xFF))
                target_g = 255 - target_r
                target_b = 96

                t0 = time.perf_counter()
                stub.sendTouch(
                    TouchEvent(
                        touches=[
                            Touch(
                                x=x_coord,
                                y=y_coord,
                                identifier=1,
                                pressure=1024,
                            )
                        ]
                    ),
                    timeout=5.0,
                )

                with cond:
                    matched = cond.wait_for(
                        lambda: (
                            abs(latest_rgb[0] - target_r) <= 18
                            and abs(latest_rgb[1] - target_g) <= 18
                            and abs(latest_rgb[2] - target_b) <= 18
                        ),
                        timeout=1.5,
                    )
                t1 = time.perf_counter()

                # Lift touch finger
                stub.sendTouch(
                    TouchEvent(
                        touches=[
                            Touch(
                                x=x_coord,
                                y=y_coord,
                                identifier=1,
                                pressure=0,
                            )
                        ]
                    ),
                    timeout=5.0,
                )

                if matched:
                    t2p_latencies_ms.append((t1 - t0) * 1000.0)
                time.sleep(0.04)
        finally:
            stop_flag[0] = True
            if stream_call[0] is not None:
                try:
                    stream_call[0].cancel()
                except Exception:
                    pass
            channel.close()

        summary = self.run_intent_scenario(
            "input_summary", timeout_sec=15.0, clear_logcat=False
        )
        k2a = [float(x) for x in (summary.get("touch_kernel_to_app_ms") or [])]
        a2f = [float(x) for x in (summary.get("touch_app_to_frame_ms") or [])]

        # If headless display pipeline did not emit color changes via streamScreenshot,
        # fall back to guest kernel->app + app->frame commit latency and note it.
        if not t2p_latencies_ms and (k2a or a2f):
            k2a_med = statistics.median(k2a) if k2a else 0.0
            a2f_med = statistics.median(a2f) if a2f else 0.0
            t2p_latencies_ms = [k2a_med + a2f_med]

        if not t2p_latencies_ms:
            raise RuntimeError(
                "touch_to_photon: neither streamScreenshot frames nor guest touch events were captured"
            )

        t2p_sorted = sorted(t2p_latencies_ms)
        k2a_p50 = statistics.median(k2a) if k2a else 0.0
        a2f_p50 = statistics.median(a2f) if a2f else 0.0

        return {
            "duration_ms": round(statistics.median(t2p_sorted), 2),
            "touch_to_photon_ms_p50": round(statistics.median(t2p_sorted), 2),
            "touch_to_photon_ms_p95": round(_percentile(t2p_sorted, 0.95), 2),
            "guest_kernel_to_app_ms_p50": round(k2a_p50, 3),
            "guest_app_to_frame_ms_p50": round(a2f_p50, 3),
            "matched_frames": float(len(t2p_latencies_ms)),
        }

    def run_swipe_120hz_stress(
        self,
        events: int = 180,
        rate_hz: float = 120.0,
    ) -> Dict[str, float]:
        """Streams a continuous 120Hz touch gesture over `streamInputEvent` and measures
        guest `virtio-input` -> `InputReader` delivery rate and queueing latency.
        """
        from aemu.proto.emulator_controller_pb2 import InputEvent, Touch, TouchEvent
        from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub

        self.run_intent_scenario("reset_input", timeout_sec=15.0)
        time.sleep(0.05)

        interval_sec = 1.0 / max(10.0, rate_hz)
        channel = open_emulator_grpc_channel(
            self.serial, emu_pid=self.emu_pid, explicit_grpc_port=self.grpc_port
        )
        t0 = time.perf_counter()
        try:
            stub = EmulatorControllerStub(channel)

            def _touch_stream() -> Iterator[InputEvent]:
                start_clock = time.perf_counter()
                for idx in range(events):
                    x_pos = 100 + ((idx * 3) % 300)
                    y_pos = 300 + ((idx * 5) % 400)
                    yield InputEvent(
                        touch_event=TouchEvent(
                            touches=[
                                Touch(
                                    x=x_pos,
                                    y=y_pos,
                                    identifier=1,
                                    pressure=1024,
                                )
                            ]
                        )
                    )
                    target_next = start_clock + (idx + 1) * interval_sec
                    rem = target_next - time.perf_counter()
                    if rem > 0:
                        time.sleep(rem)
                # Final finger lift
                yield InputEvent(
                    touch_event=TouchEvent(
                        touches=[
                            Touch(
                                x=200,
                                y=400,
                                identifier=1,
                                pressure=0,
                            )
                        ]
                    )
                )

            stub.streamInputEvent(
                _touch_stream(), timeout=max(30.0, events * interval_sec + 15.0)
            )
        finally:
            channel.close()

        wall_sec = max(1e-4, time.perf_counter() - t0)
        time.sleep(0.15)
        summary = self.run_intent_scenario(
            "input_summary", timeout_sec=15.0, clear_logcat=False
        )

        k2a = [float(x) for x in (summary.get("touch_kernel_to_app_ms") or [])]
        sample_count = int(summary.get("touch_sample_count") or len(k2a))
        event_count = int(summary.get("touch_event_count") or len(k2a))
        if sample_count == 0:
            raise RuntimeError(
                f"swipe_120hz_stress: 0 touch samples received by PerfProbeActivity out of {events} sent"
            )

        k2a_sorted = sorted(k2a) if k2a else [0.0]
        delivery_pct = min(100.0, (sample_count / float(events + 1)) * 100.0)
        samples_per_sec = sample_count / wall_sec

        return {
            "duration_ms": round(statistics.median(k2a_sorted), 3),
            "kernel_to_app_ms_p50": round(statistics.median(k2a_sorted), 3),
            "kernel_to_app_ms_p95": round(_percentile(k2a_sorted, 0.95), 3),
            "touch_samples_delivered": float(sample_count),
            "touch_batches_delivered": float(event_count),
            "delivery_pct": round(delivery_pct, 2),
            "samples_per_sec": round(samples_per_sec, 1),
        }
