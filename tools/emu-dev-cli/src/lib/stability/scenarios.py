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

"""Modular user-experience stability scenarios: dev-loop, snapshot-stress, and ui-control."""

from dataclasses import dataclass, field
import logging
import os
import re
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.perf.adb import AdbDevice
from lib.perf.workloads.display import JANKY_FRAMES_RE
from lib.perf.workloads.snapshot import _ensure_valid_console_auth_token
from lib.perf_probe_client import (
    PERF_PROBE_PACKAGE,
    PerfProbeSession,
    open_emulator_grpc_channel,
    resolve_perf_probe_apk,
)
from lib.perf_telemetry import take_proc_snapshot
from lib.stability.catalog import ScenarioName
from lib.stability.health import (
    compute_cpu_pct_between_snapshots,
    inspect_rgb888_frame,
    query_system_server_pid,
)

logger = logging.getLogger(__name__)

STABILITY_SNAPSHOT_TAG: str = "emu_stability_snap"
DEV_LOOP_TRANSFER_BYTES: int = 4 * 1024 * 1024
MS_PER_SECOND: float = 1000.0
SNAPSHOT_TIMEOUT_SEC: float = 75.0
RGB_PROBE_WIDTH: int = 64
RGB_PROBE_HEIGHT: int = 64
PNG_FORENSIC_WIDTH: int = 360
PNG_FORENSIC_HEIGHT: int = 640

GrpcChannelFactory = Callable[[str, Optional[int], Optional[int]], Any]
ControllerStubFactory = Callable[[Any], Any]


@dataclass
class StabilityScenarioContext:
    """Shared per-session dependencies passed into each stability scenario cycle."""

    adb: AdbDevice
    emu_pid: Optional[int] = None
    grpc_port: Optional[int] = None
    idle_settle_sec: float = 6.0
    probe_session: Optional[PerfProbeSession] = None
    grpc_channel_factory: GrpcChannelFactory = open_emulator_grpc_channel
    controller_stub_factory: Optional[ControllerStubFactory] = None
    sleep_fn: Callable[[float], None] = time.sleep
    snapshot_created: bool = False


@dataclass
class ScenarioCycleOutcome:
    """Metrics and invariant observations returned by a single scenario execution."""

    adb_flaps: int = 0
    adb_ping_ms: float = 0.0
    touch_latency_p95_ms: Optional[float] = None
    idle_cpu_pct: Optional[float] = None
    expected_system_server_pid: Optional[int] = None
    frame_non_black: Optional[bool] = None
    frame_pixel_std: Optional[float] = None
    jank_pct: Optional[float] = None
    notes: List[str] = field(default_factory=list)


def measure_adb_ping_ms(adb: AdbDevice) -> Tuple[float, int]:
    """Measures round-trip `adb shell echo ok` latency and counts transient retry flaps."""
    t0 = time.perf_counter()
    res = adb.shell(["echo", "ok"], timeout=10.0)
    if res.ok and "ok" in res.stdout:
        return round((time.perf_counter() - t0) * MS_PER_SECOND, 2), 0

    # Attempt single recovery before letting caller classify hard failure
    adb.wait_for_device(timeout=10.0)
    t1 = time.perf_counter()
    retry = adb.shell(["echo", "ok"], timeout=10.0)
    if not retry.ok:
        raise RuntimeError(
            f"ADB ping failed on {adb.serial} after retry: {retry.stderr.strip() or retry.stdout.strip()}"
        )
    return round((time.perf_counter() - t1) * MS_PER_SECOND, 2), 1


def _build_controller_stub(ctx: StabilityScenarioContext, channel: Any) -> Any:
    if ctx.controller_stub_factory is not None:
        return ctx.controller_stub_factory(channel)
    from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub

    return EmulatorControllerStub(channel)


def capture_grpc_rgb_frame(
    ctx: StabilityScenarioContext,
    width: int = RGB_PROBE_WIDTH,
    height: int = RGB_PROBE_HEIGHT,
) -> Optional[Tuple[bool, float]]:
    """Captures a single `RGB888` screenshot via gRPC and returns `(is_non_black, pixel_std)`."""
    channel = None
    try:
        from aemu.proto.emulator_controller_pb2 import ImageFormat

        channel = ctx.grpc_channel_factory(ctx.adb.serial, ctx.emu_pid, ctx.grpc_port)
        stub = _build_controller_stub(ctx, channel)
        img = stub.getScreenshot(
            ImageFormat(format=ImageFormat.RGB888, width=width, height=height),
            timeout=10.0,
        )
        if not img or not img.image:
            return False, 0.0
        return inspect_rgb888_frame(img.image)
    except Exception as exc:  # noqa: BLE001
        logger.debug("gRPC RGB screenshot probe failed on %s: %s", ctx.adb.serial, exc)
        return None
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass


def capture_grpc_png_bytes(
    serial: str,
    emu_pid: Optional[int] = None,
    grpc_port: Optional[int] = None,
    channel_factory: GrpcChannelFactory = open_emulator_grpc_channel,
) -> Optional[bytes]:
    """Captures a PNG screenshot over `EmulatorController.getScreenshot` for forensic archiving."""
    channel = None
    try:
        from aemu.proto.emulator_controller_pb2 import ImageFormat
        from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub

        channel = channel_factory(serial, emu_pid, grpc_port)
        stub = EmulatorControllerStub(channel)
        img = stub.getScreenshot(
            ImageFormat(
                format=ImageFormat.PNG,
                width=PNG_FORENSIC_WIDTH,
                height=PNG_FORENSIC_HEIGHT,
            ),
            timeout=10.0,
        )
        return bytes(img.image) if (img and img.image) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Forensic PNG screenshot capture failed on %s: %s", serial, exc)
        return None
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass


def _query_gfxinfo_jank_pct(adb: AdbDevice) -> Optional[float]:
    res = adb.shell(["dumpsys", "gfxinfo"], timeout=12.0)
    if not res.ok or not res.stdout:
        return None
    match = JANKY_FRAMES_RE.search(res.stdout)
    return float(match.group(1)) if match else None


def _measure_idle_settle_cpu(
    ctx: StabilityScenarioContext,
) -> Optional[float]:
    if ctx.idle_settle_sec <= 0 or not ctx.emu_pid:
        return None
    half_wait = max(0.2, ctx.idle_settle_sec * 0.4)
    measure_window = max(0.2, ctx.idle_settle_sec - half_wait)
    ctx.sleep_fn(half_wait)
    snap_before = take_proc_snapshot(ctx.emu_pid)
    ctx.sleep_fn(measure_window)
    snap_after = take_proc_snapshot(ctx.emu_pid)
    if snap_before is None or snap_after is None:
        return None
    return compute_cpu_pct_between_snapshots(snap_before, snap_after)


def _transfer_temp_adb_payload(adb: AdbDevice, cycle_index: int) -> None:
    fd_src, local_src = tempfile.mkstemp(prefix="emu_stab_push_", suffix=".bin")
    os.close(fd_src)
    fd_dst, local_dst = tempfile.mkstemp(prefix="emu_stab_pull_", suffix=".bin")
    os.close(fd_dst)
    remote_path = f"/data/local/tmp/emu_stab_payload_{os.getpid()}_{cycle_index}.bin"
    try:
        pattern = (f"stability-cycle-{cycle_index}-测试_🚀_é\n".encode("utf-8")) * (
            DEV_LOOP_TRANSFER_BYTES // 36 + 1
        )
        with open(local_src, "wb") as f:
            f.write(pattern[:DEV_LOOP_TRANSFER_BYTES])

        push_res = adb.push(local_src, remote_path, timeout=45.0)
        if not push_res.ok:
            raise RuntimeError(
                f"ADB push failed during dev-loop cycle {cycle_index}: {push_res.stderr.strip()}"
            )
        pull_res = adb.pull(remote_path, local_dst, timeout=45.0)
        if not pull_res.ok:
            raise RuntimeError(
                f"ADB pull failed during dev-loop cycle {cycle_index}: {pull_res.stderr.strip()}"
            )
    finally:
        adb.shell(["rm", "-f", remote_path], timeout=10.0)
        for path in (local_src, local_dst):
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def run_dev_loop_cycle(
    ctx: StabilityScenarioContext,
    cycle_index: int,
) -> ScenarioCycleOutcome:
    """Executes one `dev-loop` cycle: APK churn, ADB file transfer, logcat, UI input, and idle settle."""
    ping_ms, flaps = measure_adb_ping_ms(ctx.adb)
    notes: List[str] = []

    ctx.adb.shell(["input", "keyevent", "KEYCODE_WAKEUP"], timeout=10.0)
    ctx.adb.shell(["wm", "dismiss-keyguard"], timeout=10.0)
    ctx.adb.shell(["dumpsys", "gfxinfo", "--reset"], timeout=10.0)

    touch_p95: Optional[float] = None
    probe = ctx.probe_session
    if probe is None:
        try:
            apk_path = resolve_perf_probe_apk()
            probe = PerfProbeSession(
                serial=ctx.adb.serial,
                adb_fn=ctx.adb.as_adb_fn(default_timeout=60.0),
                emu_pid=ctx.emu_pid,
                grpc_port=ctx.grpc_port,
                apk_path=apk_path,
            )
            ctx.probe_session = probe
        except FileNotFoundError:
            probe = None

    if probe is not None:
        probe.ensure_installed_and_ready()
        t2p = probe.run_touch_to_photon(samples=4)
        touch_p95 = float(t2p.get("touch_to_photon_ms_p95") or t2p.get("duration_ms") or 0.0)
        probe.run_swipe_120hz_stress(events=60)
        if cycle_index % 2 == 1:
            ctx.adb.run(["uninstall", PERF_PROBE_PACKAGE], timeout=25.0)
            probe._installed = False
            notes.append("uninstalled_perf_probe_apk")
    else:
        # Fallback gesture + latency measurement if perf_probe.apk is absent
        t_inp = time.perf_counter()
        swipe_res = ctx.adb.shell(
            ["input", "swipe", "300", "900", "300", "250", "250"], timeout=15.0
        )
        if not swipe_res.ok:
            raise RuntimeError(
                f"Fallback UI swipe failed on {ctx.adb.serial}: {swipe_res.stderr.strip()}"
            )
        touch_p95 = round((time.perf_counter() - t_inp) * MS_PER_SECOND, 2)
        notes.append("adb_input_fallback")

    _transfer_temp_adb_payload(ctx.adb, cycle_index)
    ctx.adb.run(["logcat", "-d", "-t", "80"], timeout=15.0)

    frame_probe = capture_grpc_rgb_frame(ctx)
    frame_ok = frame_probe[0] if frame_probe is not None else None
    frame_std = frame_probe[1] if frame_probe is not None else None
    jank_pct = _query_gfxinfo_jank_pct(ctx.adb)
    idle_cpu = _measure_idle_settle_cpu(ctx)

    return ScenarioCycleOutcome(
        adb_flaps=flaps,
        adb_ping_ms=ping_ms,
        touch_latency_p95_ms=touch_p95,
        idle_cpu_pct=idle_cpu,
        frame_non_black=frame_ok,
        frame_pixel_std=frame_std,
        jank_pct=jank_pct,
        notes=notes,
    )


def run_snapshot_stress_cycle(
    ctx: StabilityScenarioContext,
    cycle_index: int,
) -> ScenarioCycleOutcome:
    """Executes one `snapshot-stress` cycle: Quickboot save & load while guest UI/IO is active."""
    _ensure_valid_console_auth_token()
    ping_ms, flaps = measure_adb_ping_ms(ctx.adb)
    notes: List[str] = []

    ctx.adb.shell(["input", "keyevent", "KEYCODE_WAKEUP"], timeout=10.0)
    ctx.adb.shell(["wm", "dismiss-keyguard"], timeout=10.0)
    ctx.adb.shell(["input", "swipe", "280", "850", "280", "300", "200"], timeout=12.0)

    saved_sys_pid = query_system_server_pid(ctx.adb, timeout=10.0)
    t_save = time.perf_counter()
    save_res = ctx.adb.emu(
        ["avd", "snapshot", "save", STABILITY_SNAPSHOT_TAG],
        timeout=SNAPSHOT_TIMEOUT_SEC,
    )
    save_ms = round((time.perf_counter() - t_save) * MS_PER_SECOND, 1)
    if not save_res.ok or "KO:" in save_res.stdout:
        raise RuntimeError(
            f"Snapshot save failed at cycle {cycle_index} on {ctx.adb.serial}: "
            f"{save_res.stdout.strip()} {save_res.stderr.strip()}".strip()
        )
    ctx.snapshot_created = True

    # Mutate guest UI before restoring snapshot
    ctx.adb.shell(["input", "swipe", "280", "300", "280", "850", "180"], timeout=12.0)

    t_load = time.perf_counter()
    load_res = ctx.adb.emu(
        ["avd", "snapshot", "load", STABILITY_SNAPSHOT_TAG],
        timeout=SNAPSHOT_TIMEOUT_SEC,
    )
    load_ms = round((time.perf_counter() - t_load) * MS_PER_SECOND, 1)
    if not load_res.ok or "KO:" in load_res.stdout:
        raise RuntimeError(
            f"Snapshot load failed at cycle {cycle_index} on {ctx.adb.serial}: "
            f"{load_res.stdout.strip()} {load_res.stderr.strip()}".strip()
        )

    wait_res = ctx.adb.wait_for_device(timeout=20.0)
    if not wait_res.ok:
        raise RuntimeError(
            f"ADB device {ctx.adb.serial} did not reconnect within 20s after snapshot load."
        )

    post_ping_ms, post_flaps = measure_adb_ping_ms(ctx.adb)
    flaps += post_flaps
    ctx.adb.shell(["input", "keyevent", "KEYCODE_WAKEUP"], timeout=10.0)
    ctx.adb.shell(["input", "swipe", "320", "780", "320", "320", "200"], timeout=12.0)

    frame_probe = capture_grpc_rgb_frame(ctx)
    frame_ok = frame_probe[0] if frame_probe is not None else None
    frame_std = frame_probe[1] if frame_probe is not None else None
    notes.append(f"save_ms={save_ms},load_ms={load_ms}")

    return ScenarioCycleOutcome(
        adb_flaps=flaps,
        adb_ping_ms=max(ping_ms, post_ping_ms),
        expected_system_server_pid=saved_sys_pid,
        frame_non_black=frame_ok,
        frame_pixel_std=frame_std,
        notes=notes,
    )


def _exercise_screen_recording_if_available(
    channel: Any,
    sleep_fn: Callable[[float], None],
    notes: List[str],
) -> None:
    try:
        from aemu.proto.screen_recording_service_pb2 import RecordingInfo
        from aemu.proto.screen_recording_service_pb2_grpc import (
            ScreenRecordingStub,
        )
    except Exception:  # noqa: BLE001
        return

    fd, webm_path = tempfile.mkstemp(prefix="emu_stab_rec_", suffix=".webm")
    os.close(fd)
    # Remove empty placeholder so the emulator creates a fresh WebM container
    try:
        os.unlink(webm_path)
    except OSError:
        pass

    started = False
    rec_stub: Any = None
    try:
        rec_stub = ScreenRecordingStub(channel)
        info = rec_stub.StartRecording(
            RecordingInfo(
                file_name=webm_path,
                width=360,
                height=640,
                fps=24,
                time_limit=10,
            ),
            timeout=8.0,
        )
        if info.state in (
            RecordingInfo.RECORDER_STATE_STARTING,
            RecordingInfo.RECORDER_STATE_RECORDING,
        ):
            started = True
            sleep_fn(1.0)
            rec_stub.StopRecording(RecordingInfo(), timeout=8.0)
            notes.append("screen_recording_ok")
    except Exception as exc:  # noqa: BLE001
        err_txt = str(exc)
        if "UNIMPLEMENTED" in err_txt or "FAILED_PRECONDITION" in err_txt:
            notes.append("screen_recording_unsupported")
        else:
            logger.debug("ScreenRecording RPC warning: %s", exc)
            notes.append("screen_recording_skipped")
    finally:
        if started and rec_stub is not None:
            try:
                rec_stub.StopRecording(RecordingInfo(), timeout=3.0)
            except Exception:  # noqa: BLE001
                pass
        if os.path.isfile(webm_path):
            try:
                os.unlink(webm_path)
            except OSError:
                pass


def _exercise_posture_transitions(
    stub: Any,
    sleep_fn: Callable[[float], None],
    notes: List[str],
) -> None:
    from aemu.proto.emulator_controller_pb2 import Posture

    try:
        stub.setPosture(
            Posture(value=Posture.POSTURE_HALF_OPENED), timeout=5.0
        )
        sleep_fn(0.15)
        stub.setPosture(Posture(value=Posture.POSTURE_OPENED), timeout=5.0)
        notes.append("foldable_posture_ok")
    except Exception as exc:  # noqa: BLE001
        err_txt = str(exc)
        if "FAILED_PRECONDITION" in err_txt or "UNIMPLEMENTED" in err_txt:
            notes.append("non_foldable_profile")
            return
        raise RuntimeError(f"Unexpected gRPC setPosture failure: {exc}") from exc


def run_ui_control_cycle(
    ctx: StabilityScenarioContext,
    cycle_index: int,
) -> ScenarioCycleOutcome:
    """Executes one `ui-control` cycle: concurrent gRPC frame stream, rotation, sensors, posture, and recording."""
    from google.protobuf.empty_pb2 import Empty
    from aemu.proto.emulator_controller_pb2 import (
        ImageFormat,
        ParameterValue,
        PhysicalModelValue,
        SensorValue,
    )

    del cycle_index
    ping_ms, flaps = measure_adb_ping_ms(ctx.adb)
    notes: List[str] = []

    ctx.adb.shell(["input", "keyevent", "KEYCODE_WAKEUP"], timeout=10.0)
    ctx.adb.shell(["wm", "dismiss-keyguard"], timeout=10.0)
    ctx.adb.shell(["dumpsys", "gfxinfo", "--reset"], timeout=10.0)

    channel = ctx.grpc_channel_factory(ctx.adb.serial, ctx.emu_pid, ctx.grpc_port)
    stream_frames: List[bytes] = []
    stream_seqs: List[int] = []
    stop_flag = [False]
    stream_call: List[Any] = [None]
    lock = threading.Lock()

    def _stream_reader(stub_obj: Any) -> None:
        fmt = ImageFormat(
            format=ImageFormat.RGB888,
            width=RGB_PROBE_WIDTH,
            height=RGB_PROBE_HEIGHT,
        )
        try:
            call = stub_obj.streamScreenshot(fmt)
            stream_call[0] = call
            for img in call:
                if stop_flag[0]:
                    break
                if img and img.image:
                    with lock:
                        stream_frames.append(bytes(img.image))
                        stream_seqs.append(int(getattr(img, "seq", 0)))
                if len(stream_frames) >= 16:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("ui-control streamScreenshot reader ended: %s", exc)

    try:
        stub = _build_controller_stub(ctx, channel)
        emu_status = stub.getStatus(Empty(), timeout=8.0)
        if hasattr(emu_status, "booted") and not emu_status.booted:
            notes.append("grpc_status_not_booted_flag")

        reader = threading.Thread(target=_stream_reader, args=(stub,), daemon=True)
        reader.start()

        # 1. Rotate Portrait -> Landscape -> Reverse Landscape -> Portrait
        for z_deg in (90.0, -90.0, 0.0):
            stub.setPhysicalModel(
                PhysicalModelValue(
                    target=PhysicalModelValue.ROTATION,
                    value=ParameterValue(data=[0.0, 0.0, z_deg]),
                    interpolation=PhysicalModelValue.STEP,
                ),
                timeout=5.0,
            )
            ctx.sleep_fn(0.15)

        # 2. Sensor updates (ambient light + accelerometer)
        stub.setSensor(
            SensorValue(
                target=SensorValue.LIGHT,
                value=ParameterValue(data=[350.0]),
            ),
            timeout=5.0,
        )
        stub.setSensor(
            SensorValue(
                target=SensorValue.ACCELERATION,
                value=ParameterValue(data=[0.0, 9.81, 0.2]),
            ),
            timeout=5.0,
        )

        # 3. Foldable posture transition probe
        _exercise_posture_transitions(stub, ctx.sleep_fn, notes)

        # 4. Trigger guest UI motion & screen recording
        ctx.adb.shell(
            ["input", "swipe", "300", "820", "300", "260", "220"], timeout=12.0
        )
        _exercise_screen_recording_if_available(channel, ctx.sleep_fn, notes)
    finally:
        stop_flag[0] = True
        if stream_call[0] is not None:
            try:
                stream_call[0].cancel()
            except Exception:  # noqa: BLE001
                pass
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass

    frame_ok: Optional[bool] = None
    frame_std: Optional[float] = None
    with lock:
        captured_count = len(stream_frames)
        last_bytes = stream_frames[-1] if stream_frames else None
    notes.append(f"stream_frames={captured_count}")

    if last_bytes is not None:
        frame_ok, frame_std = inspect_rgb888_frame(last_bytes)
    else:
        single_probe = capture_grpc_rgb_frame(ctx)
        if single_probe is not None:
            frame_ok, frame_std = single_probe

    jank_pct = _query_gfxinfo_jank_pct(ctx.adb)
    return ScenarioCycleOutcome(
        adb_flaps=flaps,
        adb_ping_ms=ping_ms,
        frame_non_black=frame_ok,
        frame_pixel_std=frame_std,
        jank_pct=jank_pct,
        notes=notes,
    )


def dispatch_stability_scenario(
    scenario_name: str,
    ctx: StabilityScenarioContext,
    cycle_index: int,
) -> ScenarioCycleOutcome:
    """Dispatches a single stability cycle for `scenario_name`."""
    if scenario_name == ScenarioName.DEV_LOOP.value:
        return run_dev_loop_cycle(ctx, cycle_index)
    if scenario_name == ScenarioName.SNAPSHOT_STRESS.value:
        return run_snapshot_stress_cycle(ctx, cycle_index)
    if scenario_name == ScenarioName.UI_CONTROL.value:
        return run_ui_control_cycle(ctx, cycle_index)
    raise ValueError(f"Unsupported leaf stability scenario: {scenario_name!r}")


def cleanup_stability_artifacts_on_device(ctx: StabilityScenarioContext) -> None:
    """Deletes temporary snapshots created on persistent AVDs during a session."""
    if not ctx.snapshot_created:
        return
    try:
        ctx.adb.emu(
            ["avd", "snapshot", "delete", STABILITY_SNAPSHOT_TAG], timeout=15.0
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ignored error deleting snapshot %s: %s", STABILITY_SNAPSHOT_TAG, exc)
