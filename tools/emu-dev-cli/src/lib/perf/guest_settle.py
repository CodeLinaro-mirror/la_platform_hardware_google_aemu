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

"""Guest Android OS first-boot stabilization and host/guest CPU quiescence helpers."""

import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from core.poller import poll_until
from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf_telemetry import sample_host_cpu_pct

LOGGER = logging.getLogger(__name__)

DEFAULT_STABILIZE_TIMEOUT_SEC = 90.0
BOOTANIM_WAIT_CAP_SEC = 30.0
MIN_DRAIN_WINDOW_SEC = 20.0
DRAIN_WINDOW_FRACTION = 0.75
GUEST_CPU_QUIESCENT_MAX_BUSY_PCT = 15.0
REQUIRED_GUEST_QUIET_STREAK = 2
DRAIN_POLL_INTERVAL_SEC = 1.5

DEFAULT_SETTLE_HOST_CPU_PCT = 65.0
DEFAULT_BOOT_TIMEOUT_SEC = 120.0
BOOT_POLL_INTERVAL_SEC = 0.25
MIN_SETTLE_WAIT_CAP_SEC = 10.0
MAX_SETTLE_WAIT_FLOOR_SEC = 120.0
REQUIRED_HOST_QUIET_STREAK = 3
LOG_TAIL_LINES = 15

BOOTANIM_AND_PM_CHECK_CMD = (
    "getprop init.svc.bootanim; getprop dev.bootcomplete; "
    "pm path android >/dev/null 2>&1 && echo PM_OK"
)

FIRST_BOOT_QUIESCE_SHELL_CMD: str = (
    "svc wifi disable 2>/dev/null || true; "
    "svc data disable 2>/dev/null || true; "
    "settings put global airplane_mode_on 1 2>/dev/null || true; "
    "settings put global verifier_verify_adb_installs 0 2>/dev/null || true; "
    "settings put global package_verifier_enable 0 2>/dev/null || true; "
    "settings put global window_animation_scale 0.0; "
    "settings put global transition_animation_scale 0.0; "
    "settings put global animator_duration_scale 0.0; "
    "settings put global stay_on_while_plugged_in 7; "
    "svc power stayon true 2>/dev/null || true; "
    "wm dismiss-keyguard 2>/dev/null || true; "
    "cmd jobscheduler cancel android 2>/dev/null || true; "
    "pm disable-user --user 0 com.android.vending 2>/dev/null || true; "
    "am force-stop com.android.vending 2>/dev/null || true; "
    "am force-stop com.google.android.partnersetup 2>/dev/null || true; "
    "am force-stop com.google.android.apps.restore 2>/dev/null || true; "
    "killall -9 dex2oat dex2oat32 dex2oat64 profman 2>/dev/null || true"
)

GUEST_BUSY_PROBE_SHELL_CMD: str = (
    "head -n 1 /proc/stat 2>/dev/null; "
    "ps -A -o NAME 2>/dev/null | grep -E '^(dex2oat|dex2oat32|dex2oat64|profman|oatdump|artd)$' || true; "
    "dumpsys activity broadcasts 2>/dev/null | grep -E 'Active Ordered Broadcast.*BOOT_COMPLETED' || true"
)

CANCEL_RESPAWNED_DEXOPT_CMD: str = (
    "cmd jobscheduler cancel android 2>/dev/null || true; "
    "killall -9 dex2oat dex2oat32 dex2oat64 profman 2>/dev/null || true"
)


def _compute_guest_cpu_busy_pct(
    stat_line: str,
    prev_total: int,
    prev_idle: int,
    last_busy_pct: float,
) -> Tuple[int, int, float, bool]:
    """Computes guest CPU busy percentage from a `/proc/stat` `cpu ...` line."""
    if not stat_line:
        return prev_total, prev_idle, last_busy_pct, True
    parts = stat_line.split()[1:]
    try:
        nums = [int(x) for x in parts]
    except ValueError as exc:
        LOGGER.debug("Ignored malformed /proc/stat line: %s", exc)
        return prev_total, prev_idle, last_busy_pct, True

    total_jiffies = sum(nums)
    idle_jiffies = nums[3] + (nums[4] if len(nums) > 4 else 0)
    busy_pct = last_busy_pct
    cpu_quiescent = True
    if prev_total > 0 and total_jiffies > prev_total:
        d_total = total_jiffies - prev_total
        d_idle = idle_jiffies - prev_idle
        busy_pct = max(0.0, (1.0 - (d_idle / d_total)) * 100.0)
        if busy_pct > GUEST_CPU_QUIESCENT_MAX_BUSY_PCT:
            cpu_quiescent = False
    return total_jiffies, idle_jiffies, busy_pct, cpu_quiescent


def stabilize_guest_os_after_first_boot(
    serial: str,
    adb_bin: str = DEFAULT_ADB_BIN,
    timeout_sec: float = DEFAULT_STABILIZE_TIMEOUT_SEC,
    adb_device: Optional[AdbDevice] = None,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Actively drains and stabilizes Android OS first-boot work before any snapshots or benchmarks run."""
    adb = adb_device or AdbDevice(
        serial, adb_bin=adb_bin, process_runner=process_runner
    )
    t0 = time.perf_counter()

    # 1. Wait for bootanim to stop and package manager to respond
    bootanim_limit = min(BOOTANIM_WAIT_CAP_SEC, timeout_sec)
    while time.perf_counter() - t0 < bootanim_limit:
        res = adb.shell(BOOTANIM_AND_PM_CHECK_CMD, timeout=10.0)
        out = res.stdout if res.returncode == 0 else ""
        if "stopped" in out and "PM_OK" in out:
            break
        time.sleep(1.0)

    # 2. Cut guest network & stop Play Store / background dexopt jobs, disable animations, keep awake
    adb.shell(FIRST_BOOT_QUIESCE_SHELL_CMD, timeout=20.0)

    # 3. Wait for `dex2oat`/`profman`/`artd` and `BOOT_COMPLETED` broadcasts to drain,
    #    and verify guest /proc/stat CPU utilization is quiescent (< 15% busy).
    drain_deadline = t0 + max(
        MIN_DRAIN_WINDOW_SEC, timeout_sec * DRAIN_WINDOW_FRACTION
    )
    quiet_guest_streak = 0
    last_guest_busy = ""
    prev_cpu_total = 0
    prev_cpu_idle = 0
    guest_cpu_busy_pct = 0.0

    while time.perf_counter() < drain_deadline:
        probe = adb.shell(GUEST_BUSY_PROBE_SHELL_CMD, timeout=10.0)
        lines = [
            ln.strip()
            for ln in (probe.stdout or "").splitlines()
            if ln.strip()
        ]
        stat_line = ""
        busy_lines: List[str] = []
        for ln in lines:
            if ln.startswith("cpu "):
                stat_line = ln
            else:
                busy_lines.append(ln)

        prev_cpu_total, prev_cpu_idle, guest_cpu_busy_pct, cpu_quiescent = (
            _compute_guest_cpu_busy_pct(
                stat_line, prev_cpu_total, prev_cpu_idle, guest_cpu_busy_pct
            )
        )

        if not busy_lines and cpu_quiescent:
            quiet_guest_streak += 1
            if quiet_guest_streak >= REQUIRED_GUEST_QUIET_STREAK:
                break
        else:
            quiet_guest_streak = 0
            if busy_lines:
                last_guest_busy = ", ".join(busy_lines[:3])
                adb.shell(CANCEL_RESPAWNED_DEXOPT_CMD, timeout=10.0)
        time.sleep(DRAIN_POLL_INTERVAL_SEC)

    # 4. Flush dirty pages and drop reclaimable caches so I/O & snapshots start from a clean slate
    adb.drop_caches(timeout=15.0)

    return {
        "stabilize_elapsed_s": round(time.perf_counter() - t0, 2),
        "last_busy_tasks": last_guest_busy,
        "guest_cpu_busy_pct": round(guest_cpu_busy_pct, 1),
    }


def _read_log_tail(log_path: Optional[str]) -> str:
    """Reads the last `LOG_TAIL_LINES` lines from `log_path` if readable."""
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-LOG_TAIL_LINES:]).strip()
    except OSError as exc:
        LOGGER.debug("Ignored error reading log tail %s: %s", log_path, exc)
        return ""


def _wait_for_host_cpu_quiescence(
    emu_pid: Optional[int],
    settle_seconds: float,
    target_host_cpu_pct: float,
) -> None:
    """Polls host emulator CPU% until `REQUIRED_HOST_QUIET_STREAK` samples fall at or below `target_host_cpu_pct`."""
    settle_start = time.perf_counter()
    min_wait = min(MIN_SETTLE_WAIT_CAP_SEC, settle_seconds)
    max_wait = (
        max(settle_seconds, MAX_SETTLE_WAIT_FLOOR_SEC)
        if emu_pid
        else settle_seconds
    )
    deadline = settle_start + max_wait
    quiet_streak = 0
    while time.perf_counter() < deadline:
        remaining = deadline - time.perf_counter()
        if not emu_pid or remaining < 2.0:
            time.sleep(min(1.0, max(0.0, remaining)))
            continue
        cpu = sample_host_cpu_pct(emu_pid, window_sec=min(2.0, remaining))
        elapsed_settle = time.perf_counter() - settle_start
        if cpu <= target_host_cpu_pct:
            quiet_streak += 1
            if quiet_streak >= REQUIRED_HOST_QUIET_STREAK and elapsed_settle >= min_wait:
                break
        else:
            quiet_streak = 0


def wait_for_guest_and_host_settled(
    serial: str,
    emu_pid: Optional[int],
    settle_seconds: float = 0.0,
    target_host_cpu_pct: float = DEFAULT_SETTLE_HOST_CPU_PCT,
    timeout_sec: float = DEFAULT_BOOT_TIMEOUT_SEC,
    adb_bin: str = DEFAULT_ADB_BIN,
    proc: Optional[subprocess.Popen] = None,
    log_path: Optional[str] = None,
    adb_device: Optional[AdbDevice] = None,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Waits for `sys.boot_completed=1`, drains first-boot Android OS tasks, and waits for host/guest CPU quiescence."""
    adb = adb_device or AdbDevice(
        serial, adb_bin=adb_bin, process_runner=process_runner
    )
    def _check_boot_completed() -> bool:
        if proc is not None and proc.poll() is not None:
            log_tail = _read_log_tail(log_path)
            raise RuntimeError(
                f"Emulator process for {serial} exited prematurely with code {proc.returncode}"
                + (f":\n{log_tail}" if log_tail else "")
                + ". Inspect the emulator log file or verify KVM/HVF acceleration."
            )
        return adb.getprop("sys.boot_completed", timeout=5.0) == "1"

    boot_outcome = poll_until(
        _check_boot_completed,
        timeout_sec=timeout_sec,
        interval_sec=BOOT_POLL_INTERVAL_SEC,
        sleep_fn=lambda s: time.sleep(s),
        clock_fn=lambda: time.perf_counter(),
        clamp_sleep_to_deadline=False,
    )
    booted = boot_outcome.succeeded

    if not booted:
        raise TimeoutError(
            f"Timed out after {timeout_sec}s waiting for {serial} sys.boot_completed=1. "
            "Check `adb devices` or increase `--settle-seconds`."
        )

    stabilize_info: Dict[str, Any] = {}
    if settle_seconds > 0:
        stabilize_info = stabilize_guest_os_after_first_boot(
            serial=serial,
            adb_bin=adb.adb_bin,
            timeout_sec=max(60.0, settle_seconds * 2.5),
            adb_device=adb,
        )
        _wait_for_host_cpu_quiescence(
            emu_pid=emu_pid,
            settle_seconds=settle_seconds,
            target_host_cpu_pct=target_host_cpu_pct,
        )

    uptime_res = adb.shell("cat /proc/uptime", timeout=10.0)
    load_res = adb.shell("cat /proc/loadavg", timeout=10.0)
    return {
        "guest_uptime": (
            uptime_res.stdout.strip() if uptime_res.returncode == 0 else ""
        ),
        "guest_loadavg": (
            load_res.stdout.strip() if load_res.returncode == 0 else ""
        ),
        **stabilize_info,
    }
