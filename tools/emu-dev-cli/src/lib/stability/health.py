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

"""Host/guest health sampling, frame freshness inspection, and 3-state SLA evaluation."""

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
import logging
import math
import os
import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.errors import ExitCode
from lib.perf.adb import AdbDevice
from lib.perf_telemetry import ProcSnapshot, take_proc_snapshot
from lib.stability.catalog import StabilityThresholds, StabilityVerdict

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

SECONDS_PER_MINUTE: float = 60.0
BYTES_PER_MIB: float = float(1024 * 1024)
RGB_CHANNELS: int = 3
BLACK_CHANNEL_MAX_THRESHOLD: int = 6
MIN_FRAME_CHANNEL_STDDEV: float = 1.0
MIN_LATENCY_BASELINE_MS: float = 5.0

DROPBOX_ANR_TAGS: Tuple[str, ...] = (
    "system_server_anr",
    "system_app_anr",
    "data_app_anr",
)
DROPBOX_TOMBSTONE_TAGS: Tuple[str, ...] = (
    "SYSTEM_TOMBSTONE",
    "system_server_crash",
    "system_server_native_crash",
)
DROPBOX_APP_CRASH_TAGS: Tuple[str, ...] = (
    "system_app_crash",
    "system_app_native_crash",
    "data_app_crash",
    "data_app_native_crash",
)


@dataclass
class HostProcessSample:
    """Point-in-time host resource metrics for the emulator process."""

    alive: bool
    rss_mib: float = 0.0
    peak_rss_mib: float = 0.0
    open_handles_or_fds: int = 0
    thread_count: int = 0
    proc_snapshot: Optional[ProcSnapshot] = None


@dataclass
class GuestHealthSnapshot:
    """Point-in-time guest OS health indicators queried over ADB."""

    adb_ok: bool
    system_server_pid: Optional[int] = None
    boot_completed: bool = False
    new_anr_entries: List[str] = field(default_factory=list)
    new_tombstone_entries: List[str] = field(default_factory=list)
    new_app_crash_entries: List[str] = field(default_factory=list)


@dataclass
class CycleHealthSample:
    """Telemetry and invariant snapshot recorded at the end of one stability cycle."""

    cycle_index: int
    elapsed_sec: float
    scenario: str
    process_alive: bool = True
    rss_mib: float = 0.0
    peak_rss_mib: float = 0.0
    open_handles_or_fds: int = 0
    thread_count: int = 0
    host_cpu_pct: float = 0.0
    idle_cpu_pct: Optional[float] = None
    adb_ok: bool = True
    adb_flaps: int = 0
    adb_ping_ms: float = 0.0
    touch_latency_p95_ms: Optional[float] = None
    system_server_pid: Optional[int] = None
    expected_system_server_pid: Optional[int] = None
    dropbox_anr_count: int = 0
    dropbox_tombstone_count: int = 0
    dropbox_app_crash_count: int = 0
    frame_non_black: Optional[bool] = None
    frame_pixel_std: Optional[float] = None
    jank_pct: Optional[float] = None
    crashpad_minidumps: int = 0
    scenario_error: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the sample for JSON output and forensic bundles."""
        return asdict(self)


@dataclass(frozen=True)
class StabilityFinding:
    """Individual invariant violation or degradation notice."""

    severity: str
    category: str
    summary: str
    metric_name: str = ""
    metric_value: Optional[float] = None
    threshold_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StabilityEvaluation:
    """Aggregated 3-state verdict, slope metrics, and findings across all cycles."""

    verdict: str
    effective_exit_code: int
    strict: bool
    cycles_completed: int
    elapsed_sec: float
    rss_slope_mib_per_min: float
    rss_net_growth_mib: float
    peak_rss_mib: float
    handle_net_growth: int
    peak_handles_or_fds: int
    baseline_latency_ms: Optional[float]
    final_latency_ms: Optional[float]
    latency_drift_ratio: Optional[float]
    max_idle_cpu_pct: Optional[float]
    total_adb_flaps: int
    total_anrs: int
    total_tombstones: int
    total_app_crashes: int
    system_server_restarts: int
    findings: List[StabilityFinding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["findings"] = [f.to_dict() for f in self.findings]
        return payload


def _count_linux_proc_fds(pid: int) -> int:
    fd_dir = f"/proc/{pid}/fd"
    if not os.path.isdir(fd_dir):
        return 0
    try:
        return len(os.listdir(fd_dir))
    except OSError:
        return 0


def _count_linux_proc_threads(pid: int) -> int:
    task_dir = f"/proc/{pid}/task"
    if not os.path.isdir(task_dir):
        return 0
    try:
        return len(os.listdir(task_dir))
    except OSError:
        return 0


def _sample_win32_process_health(pid: int) -> Optional[HostProcessSample]:
    """Queries Win32 kernel32/psapi counters when psutil is unavailable on Windows."""
    if os.name != "nt":
        return None

    process_query_limited_information = 0x1000
    process_vm_read = 0x0010
    still_active = 259

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(
        process_query_limited_information | process_vm_read, False, int(pid)
    )
    if not handle:
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
    if not handle:
        return HostProcessSample(alive=False)

    try:
        exit_code = wintypes.DWORD(0)
        if (
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            and exit_code.value != still_active
        ):
            return HostProcessSample(alive=False)

        rss_mib = 0.0
        peak_mib = 0.0
        pmc = _ProcessMemoryCounters()
        pmc.cb = ctypes.sizeof(_ProcessMemoryCounters)
        mem_ok = False
        if hasattr(kernel32, "K32GetProcessMemoryInfo"):
            mem_ok = bool(
                kernel32.K32GetProcessMemoryInfo(
                    handle, ctypes.byref(pmc), pmc.cb
                )
            )
        elif hasattr(ctypes.windll, "psapi"):  # type: ignore[attr-defined]
            mem_ok = bool(
                ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                    handle, ctypes.byref(pmc), pmc.cb
                )
            )
        if mem_ok:
            rss_mib = round(pmc.WorkingSetSize / BYTES_PER_MIB, 2)
            peak_mib = round(
                max(pmc.WorkingSetSize, pmc.PeakWorkingSetSize) / BYTES_PER_MIB,
                2,
            )

        handle_count = wintypes.DWORD(0)
        kernel32.GetProcessHandleCount(handle, ctypes.byref(handle_count))

        c_time = _FileTime()
        e_time = _FileTime()
        k_time = _FileTime()
        u_time = _FileTime()
        utime_ticks = 0
        stime_ticks = 0
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(c_time),
            ctypes.byref(e_time),
            ctypes.byref(k_time),
            ctypes.byref(u_time),
        ):
            # FILETIME is in 100ns units; 1 tick at 100Hz = 10,000,000ns = 100,000 * 100ns
            k_100ns = (k_time.dwHighDateTime << 32) | k_time.dwLowDateTime
            u_100ns = (u_time.dwHighDateTime << 32) | u_time.dwLowDateTime
            stime_ticks = int(k_100ns // 100_000)
            utime_ticks = int(u_100ns // 100_000)

        snap = ProcSnapshot(
            pid=int(pid),
            timestamp=time.monotonic(),
            utime_ticks=utime_ticks,
            stime_ticks=stime_ticks,
            rss_mib=rss_mib,
            peak_rss_mib=peak_mib,
        )
        return HostProcessSample(
            alive=True,
            rss_mib=rss_mib,
            peak_rss_mib=peak_mib,
            open_handles_or_fds=int(handle_count.value),
            thread_count=0,
            proc_snapshot=snap,
        )
    finally:
        kernel32.CloseHandle(handle)


def sample_host_process_health(
    emu_pid: Optional[int],
    popen_proc: Optional[Any] = None,
) -> HostProcessSample:
    """Samples RSS, peak RSS, open OS handles/FDs, and thread count for `emu_pid`."""
    if popen_proc is not None and popen_proc.poll() is not None:
        return HostProcessSample(alive=False)
    if not emu_pid or emu_pid <= 0:
        alive = popen_proc is None or popen_proc.poll() is None
        return HostProcessSample(alive=alive)

    snap = take_proc_snapshot(emu_pid)
    if psutil is not None:
        try:
            proc = psutil.Process(emu_pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return HostProcessSample(alive=False)
            mem = proc.memory_info()
            rss_mib = round(mem.rss / BYTES_PER_MIB, 2)
            peak_bytes = getattr(mem, "peak_wset", 0) or mem.rss
            peak_mib = round(max(rss_mib, peak_bytes / BYTES_PER_MIB), 2)
            if snap is not None and snap.peak_rss_mib > peak_mib:
                peak_mib = round(snap.peak_rss_mib, 2)

            handles = 0
            if hasattr(proc, "num_handles"):
                handles = int(proc.num_handles())
            elif hasattr(proc, "num_fds"):
                handles = int(proc.num_fds())
            else:
                handles = _count_linux_proc_fds(emu_pid)

            threads = int(proc.num_threads())
            return HostProcessSample(
                alive=True,
                rss_mib=rss_mib,
                peak_rss_mib=peak_mib,
                open_handles_or_fds=handles,
                thread_count=threads,
                proc_snapshot=snap,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("psutil inspection failed for pid %s: %s", emu_pid, exc)
            if snap is None:
                return HostProcessSample(alive=False)

    if snap is None:
        win_sample = _sample_win32_process_health(emu_pid)
        if win_sample is not None:
            return win_sample
        try:
            os.kill(emu_pid, 0)
            return HostProcessSample(alive=True)
        except OSError:
            return HostProcessSample(alive=False)

    return HostProcessSample(
        alive=True,
        rss_mib=round(snap.rss_mib, 2),
        peak_rss_mib=round(snap.peak_rss_mib, 2),
        open_handles_or_fds=_count_linux_proc_fds(emu_pid),
        thread_count=_count_linux_proc_threads(emu_pid),
        proc_snapshot=snap,
    )


def compute_cpu_pct_between_snapshots(
    before: Optional[ProcSnapshot],
    after: Optional[ProcSnapshot],
) -> float:
    """Computes host CPU utilization percentage (`100%` = 1 logical core) between two snapshots."""
    if before is None or after is None:
        return 0.0
    wall_dt = after.timestamp - before.timestamp
    if wall_dt <= 1e-3:
        return 0.0
    try:
        clk_tck = int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        clk_tck = 100
    tick_delta = max(
        0,
        (after.utime_ticks - before.utime_ticks)
        + (after.stime_ticks - before.stime_ticks),
    )
    cpu_seconds = tick_delta / float(max(1, clk_tck))
    return round((cpu_seconds / wall_dt) * 100.0, 2)


def parse_dropbox_lines(dropbox_output: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """Extracts unique `(anr_lines, tombstone_lines, app_crash_lines)` from `dumpsys dropbox`."""
    anrs: Set[str] = set()
    tombstones: Set[str] = set()
    app_crashes: Set[str] = set()
    if not dropbox_output:
        return anrs, tombstones, app_crashes

    for raw_line in dropbox_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(tag in line for tag in DROPBOX_ANR_TAGS):
            anrs.add(line)
        elif any(tag in line for tag in DROPBOX_TOMBSTONE_TAGS):
            tombstones.add(line)
        elif any(tag in line for tag in DROPBOX_APP_CRASH_TAGS):
            app_crashes.add(line)
    return anrs, tombstones, app_crashes


def query_system_server_pid(adb: AdbDevice, timeout: float = 10.0) -> Optional[int]:
    """Queries the guest `system_server` PID via `adb shell pidof system_server`."""
    res = adb.shell(["pidof", "system_server"], timeout=timeout)
    if not res.ok:
        return None
    tokens = res.stdout.strip().split()
    if not tokens:
        return None
    first = tokens[0].strip()
    return int(first) if first.isdigit() else None


def inspect_guest_health(
    adb: AdbDevice,
    seen_dropbox_lines: Set[str],
    timeout: float = 12.0,
) -> GuestHealthSnapshot:
    """Checks ADB reachability, `system_server` PID, and new DropBox crash/ANR entries."""
    boot_val = adb.getprop("sys.boot_completed", timeout=timeout)
    if boot_val != "1":
        # Retry once after a short wait-for-device in case of transient ADB hiccup
        wait_res = adb.wait_for_device(timeout=8.0)
        if not wait_res.ok:
            return GuestHealthSnapshot(adb_ok=False, boot_completed=False)
        boot_val = adb.getprop("sys.boot_completed", timeout=timeout)
        if boot_val != "1":
            return GuestHealthSnapshot(adb_ok=False, boot_completed=False)

    sys_pid = query_system_server_pid(adb, timeout=timeout)
    db_res = adb.shell(["dumpsys", "dropbox"], timeout=timeout)
    anrs, tombstones, app_crashes = parse_dropbox_lines(
        db_res.stdout if db_res.ok else ""
    )

    new_anrs = sorted(anrs - seen_dropbox_lines)
    new_tombstones = sorted(tombstones - seen_dropbox_lines)
    new_app_crashes = sorted(app_crashes - seen_dropbox_lines)
    seen_dropbox_lines.update(anrs | tombstones | app_crashes)

    return GuestHealthSnapshot(
        adb_ok=True,
        system_server_pid=sys_pid,
        boot_completed=True,
        new_anr_entries=new_anrs,
        new_tombstone_entries=new_tombstones,
        new_app_crash_entries=new_app_crashes,
    )


def inspect_rgb888_frame(rgb_bytes: bytes) -> Tuple[bool, float]:
    """Inspects an `RGB888` frame buffer and returns `(is_non_black, pixel_stddev)`.

    Samples up to 1,024 evenly spaced RGB pixels to keep overhead sub-millisecond.
    """
    total_bytes = len(rgb_bytes or b"")
    pixel_count = total_bytes // RGB_CHANNELS
    if pixel_count <= 0:
        return False, 0.0

    stride_pixels = max(1, pixel_count // 1024)
    luminance_samples: List[float] = []
    max_channel = 0

    for px_idx in range(0, pixel_count, stride_pixels):
        offset = px_idx * RGB_CHANNELS
        r = rgb_bytes[offset]
        g = rgb_bytes[offset + 1]
        b = rgb_bytes[offset + 2]
        if r > max_channel:
            max_channel = r
        if g > max_channel:
            max_channel = g
        if b > max_channel:
            max_channel = b
        luminance_samples.append(0.299 * r + 0.587 * g + 0.114 * b)

    if len(luminance_samples) < 2:
        return max_channel > BLACK_CHANNEL_MAX_THRESHOLD, 0.0

    mean_lum = statistics.fmean(luminance_samples)
    variance = sum((val - mean_lum) ** 2 for val in luminance_samples) / len(
        luminance_samples
    )
    stddev = round(math.sqrt(variance), 2)
    is_non_black = (
        max_channel > BLACK_CHANNEL_MAX_THRESHOLD
        and stddev >= MIN_FRAME_CHANNEL_STDDEV
    ) or max_channel > 24
    return is_non_black, stddev


def compute_linear_slope_per_min(
    points: Sequence[Tuple[float, float]],
) -> float:
    """Computes ordinary least-squares slope in `units / minute` from `(elapsed_sec, value)` pairs."""
    if len(points) < 2:
        return 0.0
    xs_min = [pt[0] / SECONDS_PER_MINUTE for pt in points]
    ys = [pt[1] for pt in points]
    mean_x = statistics.fmean(xs_min)
    denom = sum((x - mean_x) ** 2 for x in xs_min)
    if denom <= 1e-9:
        return 0.0
    mean_y = statistics.fmean(ys)
    numer = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs_min, ys))
    return round(numer / denom, 3)


def _select_post_warmup_samples(
    samples: Sequence[CycleHealthSample],
) -> Sequence[CycleHealthSample]:
    """Skips cycle 0 warmup transient when 3+ samples exist so cold caches don't skew leak slopes."""
    alive_samples = [s for s in samples if s.process_alive]
    if len(alive_samples) >= 3:
        return alive_samples[1:]
    return alive_samples


def _evaluate_process_and_crashpad(
    samples: Sequence[CycleHealthSample],
    findings: List[StabilityFinding],
) -> None:
    dead_cycles = [s.cycle_index for s in samples if not s.process_alive]
    if dead_cycles:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="host_process",
                summary=f"Emulator host process exited unexpectedly during cycle {dead_cycles[0]}.",
                metric_name="process_alive",
                metric_value=0.0,
                threshold_value=1.0,
            )
        )
    minidumps = max((s.crashpad_minidumps for s in samples), default=0)
    if minidumps > 0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="crashpad",
                summary=f"Detected {minidumps} Crashpad minidump (.dmp) file(s) generated during the run.",
                metric_name="crashpad_minidumps",
                metric_value=float(minidumps),
                threshold_value=0.0,
            )
        )


def _evaluate_adb_and_guest(
    samples: Sequence[CycleHealthSample],
    findings: List[StabilityFinding],
) -> Tuple[int, int, int, int, int]:
    adb_drops = [s.cycle_index for s in samples if s.process_alive and not s.adb_ok]
    total_flaps = sum(s.adb_flaps for s in samples)
    if adb_drops:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="adb_connectivity",
                summary=f"Unrecoverable ADB disconnect/timeout at cycle {adb_drops[0]}.",
                metric_name="adb_ok",
                metric_value=0.0,
                threshold_value=1.0,
            )
        )
    elif total_flaps > 0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.DEGRADED.value,
                category="adb_connectivity",
                summary=f"Observed {total_flaps} transient ADB flap(s) that required retry recovery.",
                metric_name="adb_flaps",
                metric_value=float(total_flaps),
                threshold_value=0.0,
            )
        )

    restarts = 0
    baseline_pid: Optional[int] = None
    for sample in samples:
        if sample.expected_system_server_pid is not None:
            baseline_pid = sample.expected_system_server_pid
        if sample.system_server_pid is None:
            continue
        if baseline_pid is None:
            baseline_pid = sample.system_server_pid
            continue
        if sample.system_server_pid != baseline_pid:
            restarts += 1
            baseline_pid = sample.system_server_pid

    if restarts > 0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="guest_system",
                summary=f"Guest Android system_server soft-restarted {restarts} time(s) (PID changed mid-run).",
                metric_name="system_server_restarts",
                metric_value=float(restarts),
                threshold_value=0.0,
            )
        )

    total_anrs = sum(s.dropbox_anr_count for s in samples)
    total_tombstones = sum(s.dropbox_tombstone_count for s in samples)
    total_app_crashes = sum(s.dropbox_app_crash_count for s in samples)
    if total_anrs > 0 or total_tombstones > 0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="guest_system",
                summary=(
                    f"Guest recorded {total_anrs} ANR(s) and {total_tombstones} native tombstone/system crash(es) in DropBox."
                ),
                metric_name="guest_anr_or_tombstone",
                metric_value=float(total_anrs + total_tombstones),
                threshold_value=0.0,
            )
        )
    elif total_app_crashes > 0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.DEGRADED.value,
                category="guest_system",
                summary=f"Guest recorded {total_app_crashes} non-fatal app crash entry/entries in DropBox.",
                metric_name="dropbox_app_crashes",
                metric_value=float(total_app_crashes),
                threshold_value=0.0,
            )
        )

    for sample in samples:
        if sample.scenario_error:
            findings.append(
                StabilityFinding(
                    severity=StabilityVerdict.FAIL.value,
                    category="scenario_execution",
                    summary=f"Cycle {sample.cycle_index} ({sample.scenario}) failed: {sample.scenario_error}",
                )
            )
            break

    return total_flaps, total_anrs, total_tombstones, total_app_crashes, restarts


def _evaluate_display_and_resources(
    samples: Sequence[CycleHealthSample],
    post_warmup: Sequence[CycleHealthSample],
    thresholds: StabilityThresholds,
    findings: List[StabilityFinding],
) -> Tuple[float, float, int, Optional[float], Optional[float], Optional[float], Optional[float]]:
    black_cycles = [
        s.cycle_index for s in samples if s.frame_non_black is False
    ]
    if black_cycles:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="display_freshness",
                summary=f"Display output became all-black or dead at cycle {black_cycles[0]}.",
                metric_name="frame_non_black",
                metric_value=0.0,
                threshold_value=1.0,
            )
        )

    max_jank = max(
        (s.jank_pct for s in samples if s.jank_pct is not None),
        default=None,
    )
    if max_jank is not None and max_jank > thresholds.jank_degraded_pct:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.DEGRADED.value,
                category="display_freshness",
                summary=f"Guest UI jank reached {max_jank:.1f}% (threshold <= {thresholds.jank_degraded_pct:.1f}%).",
                metric_name="jank_pct",
                metric_value=round(max_jank, 2),
                threshold_value=thresholds.jank_degraded_pct,
            )
        )

    rss_points = [(s.elapsed_sec, s.rss_mib) for s in post_warmup if s.rss_mib > 0]
    rss_slope = compute_linear_slope_per_min(rss_points)
    rss_net_growth = (
        round(rss_points[-1][1] - rss_points[0][1], 2)
        if len(rss_points) >= 2
        else 0.0
    )
    rss_span_min = (
        (rss_points[-1][0] - rss_points[0][0]) / SECONDS_PER_MINUTE
        if len(rss_points) >= 2
        else 0.0
    )

    if (
        rss_span_min >= thresholds.rss_min_elapsed_min_for_slope
        and rss_slope >= thresholds.rss_slope_fail_mib_per_min
        and rss_net_growth >= thresholds.rss_net_growth_fail_mib
    ) or rss_net_growth >= thresholds.rss_net_growth_fail_mib * 2.0:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="rss_memory",
                summary=(
                    f"Severe host RSS memory growth: +{rss_net_growth:.1f} MiB "
                    f"({rss_slope:+.2f} MiB/min; fail >= {thresholds.rss_slope_fail_mib_per_min:.1f} MiB/min)."
                ),
                metric_name="rss_slope_mib_per_min",
                metric_value=rss_slope,
                threshold_value=thresholds.rss_slope_fail_mib_per_min,
            )
        )
    elif (
        rss_span_min >= thresholds.rss_min_elapsed_min_for_slope
        and rss_slope >= thresholds.rss_slope_degraded_mib_per_min
        and rss_net_growth > 64.0
    ):
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.DEGRADED.value,
                category="rss_memory",
                summary=(
                    f"Upward host RSS memory slope: {rss_slope:+.2f} MiB/min (+{rss_net_growth:.1f} MiB post-warmup)."
                ),
                metric_name="rss_slope_mib_per_min",
                metric_value=rss_slope,
                threshold_value=thresholds.rss_slope_degraded_mib_per_min,
            )
        )

    handle_vals = [
        s.open_handles_or_fds for s in post_warmup if s.open_handles_or_fds > 0
    ]
    handle_growth = (
        int(handle_vals[-1] - handle_vals[0]) if len(handle_vals) >= 2 else 0
    )
    if handle_growth >= thresholds.handle_growth_fail:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="os_handles",
                summary=(
                    f"Severe OS handle/FD leak: +{handle_growth} net handles/FDs "
                    f"(fail >= +{thresholds.handle_growth_fail})."
                ),
                metric_name="handle_net_growth",
                metric_value=float(handle_growth),
                threshold_value=float(thresholds.handle_growth_fail),
            )
        )
    elif handle_growth >= thresholds.handle_growth_degraded:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.DEGRADED.value,
                category="os_handles",
                summary=(
                    f"Elevated OS handle/FD growth: +{handle_growth} net handles/FDs "
                    f"(warn >= +{thresholds.handle_growth_degraded})."
                ),
                metric_name="handle_net_growth",
                metric_value=float(handle_growth),
                threshold_value=float(thresholds.handle_growth_degraded),
            )
        )

    latencies = [
        s.touch_latency_p95_ms
        for s in samples
        if s.touch_latency_p95_ms is not None and s.touch_latency_p95_ms > 0
    ]
    base_lat = latencies[0] if latencies else None
    final_lat = latencies[-1] if latencies else None
    lat_ratio: Optional[float] = None
    if base_lat is not None and final_lat is not None and len(latencies) >= 2:
        denom_lat = max(MIN_LATENCY_BASELINE_MS, base_lat)
        lat_ratio = round(final_lat / denom_lat, 2)
        if lat_ratio >= thresholds.latency_ratio_fail:
            findings.append(
                StabilityFinding(
                    severity=StabilityVerdict.FAIL.value,
                    category="ux_latency",
                    summary=(
                        f"Input P95 latency regressed {lat_ratio:.2f}x from {base_lat:.1f} ms to {final_lat:.1f} ms."
                    ),
                    metric_name="latency_drift_ratio",
                    metric_value=lat_ratio,
                    threshold_value=thresholds.latency_ratio_fail,
                )
            )
        elif lat_ratio >= thresholds.latency_ratio_degraded:
            findings.append(
                StabilityFinding(
                    severity=StabilityVerdict.DEGRADED.value,
                    category="ux_latency",
                    summary=(
                        f"Input P95 latency drifted {lat_ratio:.2f}x from {base_lat:.1f} ms to {final_lat:.1f} ms."
                    ),
                    metric_name="latency_drift_ratio",
                    metric_value=lat_ratio,
                    threshold_value=thresholds.latency_ratio_degraded,
                )
            )

    idle_vals = [
        s.idle_cpu_pct for s in samples if s.idle_cpu_pct is not None
    ]
    max_idle = round(max(idle_vals), 2) if idle_vals else None
    if max_idle is not None:
        if max_idle >= thresholds.idle_cpu_fail_pct:
            findings.append(
                StabilityFinding(
                    severity=StabilityVerdict.FAIL.value,
                    category="idle_cpu",
                    summary=(
                        f"Runaway host CPU during idle settle: {max_idle:.1f}% (fail >= {thresholds.idle_cpu_fail_pct:.1f}%)."
                    ),
                    metric_name="max_idle_cpu_pct",
                    metric_value=max_idle,
                    threshold_value=thresholds.idle_cpu_fail_pct,
                )
            )
        elif max_idle >= thresholds.idle_cpu_degraded_pct:
            findings.append(
                StabilityFinding(
                    severity=StabilityVerdict.DEGRADED.value,
                    category="idle_cpu",
                    summary=(
                        f"Elevated host CPU during idle settle: {max_idle:.1f}% (warn >= {thresholds.idle_cpu_degraded_pct:.1f}%)."
                    ),
                    metric_name="max_idle_cpu_pct",
                    metric_value=max_idle,
                    threshold_value=thresholds.idle_cpu_degraded_pct,
                )
            )

    return (
        rss_slope,
        rss_net_growth,
        handle_growth,
        base_lat,
        final_lat,
        lat_ratio,
        max_idle,
    )


def evaluate_stability_samples(
    samples: Sequence[CycleHealthSample],
    thresholds: Optional[StabilityThresholds] = None,
    *,
    strict: bool = False,
) -> StabilityEvaluation:
    """Evaluates all collected `CycleHealthSample` records into a 3-state `StabilityEvaluation`."""
    active_thresholds = thresholds or StabilityThresholds()
    findings: List[StabilityFinding] = []

    if not samples:
        findings.append(
            StabilityFinding(
                severity=StabilityVerdict.FAIL.value,
                category="session",
                summary="Zero stability cycles completed before evaluation.",
            )
        )
        return StabilityEvaluation(
            verdict=StabilityVerdict.FAIL.value,
            effective_exit_code=int(ExitCode.FAILURE),
            strict=strict,
            cycles_completed=0,
            elapsed_sec=0.0,
            rss_slope_mib_per_min=0.0,
            rss_net_growth_mib=0.0,
            peak_rss_mib=0.0,
            handle_net_growth=0,
            peak_handles_or_fds=0,
            baseline_latency_ms=None,
            final_latency_ms=None,
            latency_drift_ratio=None,
            max_idle_cpu_pct=None,
            total_adb_flaps=0,
            total_anrs=0,
            total_tombstones=0,
            total_app_crashes=0,
            system_server_restarts=0,
            findings=findings,
        )

    _evaluate_process_and_crashpad(samples, findings)
    flaps, anrs, tombstones, app_crashes, restarts = _evaluate_adb_and_guest(
        samples, findings
    )
    post_warmup = _select_post_warmup_samples(samples)
    (
        rss_slope,
        rss_growth,
        handle_growth,
        base_lat,
        final_lat,
        lat_ratio,
        max_idle,
    ) = _evaluate_display_and_resources(
        samples, post_warmup, active_thresholds, findings
    )

    has_fail = any(f.severity == StabilityVerdict.FAIL.value for f in findings)
    has_degraded = any(
        f.severity == StabilityVerdict.DEGRADED.value for f in findings
    )

    if has_fail:
        verdict = StabilityVerdict.FAIL.value
    elif has_degraded:
        verdict = StabilityVerdict.DEGRADED.value
    else:
        verdict = StabilityVerdict.PASS.value

    should_fail_exit = verdict == StabilityVerdict.FAIL.value or (
        strict and verdict == StabilityVerdict.DEGRADED.value
    )
    exit_code = int(ExitCode.FAILURE) if should_fail_exit else int(ExitCode.SUCCESS)

    peak_rss = round(
        max((max(s.rss_mib, s.peak_rss_mib) for s in samples), default=0.0), 2
    )
    peak_handles = max((s.open_handles_or_fds for s in samples), default=0)
    completed = sum(
        1 for s in samples if s.process_alive and s.adb_ok and not s.scenario_error
    )

    return StabilityEvaluation(
        verdict=verdict,
        effective_exit_code=exit_code,
        strict=strict,
        cycles_completed=completed,
        elapsed_sec=round(samples[-1].elapsed_sec, 2),
        rss_slope_mib_per_min=rss_slope,
        rss_net_growth_mib=rss_growth,
        peak_rss_mib=peak_rss,
        handle_net_growth=handle_growth,
        peak_handles_or_fds=peak_handles,
        baseline_latency_ms=base_lat,
        final_latency_ms=final_lat,
        latency_drift_ratio=lat_ratio,
        max_idle_cpu_pct=max_idle,
        total_adb_flaps=flaps,
        total_anrs=anrs,
        total_tombstones=tombstones,
        total_app_crashes=app_crashes,
        system_server_restarts=restarts,
        findings=findings,
    )
