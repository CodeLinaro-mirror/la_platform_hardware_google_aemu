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

"""Cold boot duration and guest boot phase breakdown workload strategy (`cold_boot`, `cold_boot_warm_disk`)."""

import re
from typing import Any, Dict, Optional, Tuple

from core.process import ProcessRunner
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf.guest_settle import _parse_guest_uptime_ms
from lib.perf.workloads.base import WorkloadContext, WorkloadStrategy

COLD_BOOT_WORKLOAD_NAME: str = "cold_boot"
COLD_BOOT_WARM_DISK_WORKLOAD_NAME: str = "cold_boot_warm_disk"
COLD_BOOT_WORKLOAD_NAMES: Tuple[str, ...] = (
    COLD_BOOT_WORKLOAD_NAME,
    COLD_BOOT_WARM_DISK_WORKLOAD_NAME,
)

BOOT_PROGRESS_RE = re.compile(r"\b(boot_progress_[a-z0-9_]+)\s*:\s*(\d+)")
KEY_CPU_FLAGS = ("avx", "avx2", "bmi1", "bmi2", "fma", "movbe", "aes", "pclmulqdq", "rdrand")


def _parse_boot_progress_events(events_stdout: str) -> Dict[str, float]:
    """Extracts Android `boot_progress_*` uptime timestamps (in ms) from `logcat -b events -d`."""
    out: Dict[str, float] = {}
    for line in (events_stdout or "").splitlines():
        m = BOOT_PROGRESS_RE.search(line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def _parse_guest_cpu_info(cpuinfo_stdout: str) -> Dict[str, Any]:
    """Extracts guest CPU model name and key ISA feature flags from `/proc/cpuinfo`."""
    model_name = ""
    flags_set = set()
    for line in (cpuinfo_stdout or "").splitlines():
        if ":" not in line:
            continue
        k, v = [p.strip() for p in line.split(":", 1)]
        kl = k.lower()
        if kl in ("model name", "processor", "hardware") and not model_name and not v.isdigit():
            model_name = v
        elif kl in ("flags", "features"):
            flags_set.update(v.split())
            break
    if not model_name and not flags_set:
        return {}
    present_key_flags = [f for f in KEY_CPU_FLAGS if f in flags_set]
    return {
        "model_name": model_name,
        "key_flags": present_key_flags,
        "flag_count": len(flags_set),
    }


def _compute_boot_stage_breakdown(
    duration_ms: float,
    guest_uptime_ms: float,
    init_ns: int,
    zyg_ns: int,
    boot_events: Dict[str, float],
) -> Dict[str, float]:
    """Computes the 4 additive boot stages (`host_pre_kernel_ms`, `kernel_to_init_ms`,
    `init_to_zygote_ms`, `zygote_to_complete_ms`) summing to `duration_ms`, plus `pms_scan_ms`.
    """
    init_ms = round(max(0.0, init_ns / 1e6), 2)
    raw_zyg_ms = round(max(0.0, zyg_ns / 1e6), 2)
    if raw_zyg_ms <= 0.0 and boot_events.get("boot_progress_preload_start", 0.0) > 0.0:
        raw_zyg_ms = round(boot_events["boot_progress_preload_start"], 2)
    zyg_ms = max(init_ms, raw_zyg_ms)

    enable_screen_ms = boot_events.get("boot_progress_enable_screen", 0.0)
    if guest_uptime_ms > 0.0:
        raw_guest_ms = guest_uptime_ms
    elif enable_screen_ms > 0.0:
        raw_guest_ms = enable_screen_ms
    else:
        raw_guest_ms = duration_ms

    if duration_ms > 0.0:
        effective_guest_ms = min(duration_ms, max(zyg_ms, raw_guest_ms))
        host_pre_kernel_ms = round(max(0.0, duration_ms - effective_guest_ms), 2)
    else:
        effective_guest_ms = max(zyg_ms, raw_guest_ms)
        host_pre_kernel_ms = 0.0

    kernel_to_init_ms = round(min(effective_guest_ms, init_ms), 2)
    init_to_zygote_ms = round(
        max(0.0, min(effective_guest_ms, zyg_ms) - kernel_to_init_ms), 2
    )
    zygote_to_complete_ms = round(
        max(
            0.0,
            duration_ms
            - host_pre_kernel_ms
            - kernel_to_init_ms
            - init_to_zygote_ms,
        ),
        2,
    )

    pms_start = boot_events.get("boot_progress_pms_start", 0.0)
    pms_ready = boot_events.get("boot_progress_pms_ready", 0.0)
    pms_scan_ms = (
        round(max(0.0, pms_ready - pms_start), 2)
        if (pms_start > 0.0 and pms_ready >= pms_start)
        else 0.0
    )

    return {
        "host_pre_kernel_ms": host_pre_kernel_ms,
        "kernel_to_init_ms": kernel_to_init_ms,
        "init_to_zygote_ms": init_to_zygote_ms,
        "zygote_to_complete_ms": zygote_to_complete_ms,
        "pms_scan_ms": pms_scan_ms,
    }


def _merge_boot_stage_metrics(
    merged: Dict[str, Any],
    *,
    duration_ms: float,
    stages: Dict[str, float],
    init_ns: int,
    zyg_ns: int,
    guest_cpu_info: Dict[str, Any],
) -> Dict[str, Any]:
    merged["ro_boottime_init_ns"] = init_ns
    merged["ro_boottime_zygote_ns"] = zyg_ns
    if guest_cpu_info:
        merged["guest_cpu_info"] = guest_cpu_info

    for k, val in stages.items():
        merged[k] = val
        merged[f"{k}_p50"] = val
        merged[f"{k}_mean"] = val
        merged[f"{k}_stdev"] = 0.0

    samples = list(merged.get("samples") or [])
    if samples and isinstance(samples[0], dict):
        sample0 = dict(samples[0])
    elif duration_ms > 0.0:
        sample0 = {
            "duration_ms": round(duration_ms, 2),
            "host_wall_boot_ms": round(duration_ms, 2),
        }
    else:
        sample0 = {}

    if sample0:
        sample0.update(stages)
        sample0["ro_boottime_init_ns"] = init_ns
        sample0["ro_boottime_zygote_ns"] = zyg_ns
        merged["samples"] = [sample0]

    return merged


class ColdBootWorkloadStrategy(WorkloadStrategy):
    """Strategy collecting guest boot-phase metrics and merging host cold boot duration."""

    def matches(self, workload_name: str) -> bool:
        return workload_name in COLD_BOOT_WORKLOAD_NAMES

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        del workload_name
        init_str = ctx.adb.getprop("ro.boottime.init")
        zyg_str = ctx.adb.getprop("ro.boottime.zygote")
        if not zyg_str.isdigit():
            zyg_str = ctx.adb.getprop("ro.boottime.zygote64")
        init_ns = int(init_str) if init_str.isdigit() else 0
        zyg_ns = int(zyg_str) if zyg_str.isdigit() else 0

        events_res = ctx.adb.shell(
            "logcat -b events -d 2>/dev/null || true", timeout=10.0
        )
        boot_events = (
            _parse_boot_progress_events(events_res.stdout)
            if events_res.returncode == 0
            else {}
        )

        cpuinfo_res = ctx.adb.shell(
            "cat /proc/cpuinfo 2>/dev/null || true", timeout=5.0
        )
        guest_cpu_info = (
            _parse_guest_cpu_info(cpuinfo_res.stdout)
            if cpuinfo_res.returncode == 0
            else {}
        )

        merged: Dict[str, Any] = (
            dict(ctx.boot_metrics) if ctx.boot_metrics else {"n": 1}
        )
        guest_uptime_ms = float(merged.get("guest_uptime_ms") or 0.0)
        if guest_uptime_ms <= 0.0:
            up_res = ctx.adb.shell("cat /proc/uptime", timeout=5.0)
            if up_res.returncode == 0:
                guest_uptime_ms = _parse_guest_uptime_ms(up_res.stdout)
                if guest_uptime_ms > 0.0:
                    merged["guest_uptime_ms"] = guest_uptime_ms

        duration_ms = float(
            merged.get("duration_ms_p50")
            or merged.get("host_wall_boot_ms")
            or guest_uptime_ms
            or 0.0
        )
        stages = _compute_boot_stage_breakdown(
            duration_ms=duration_ms,
            guest_uptime_ms=guest_uptime_ms,
            init_ns=init_ns,
            zyg_ns=zyg_ns,
            boot_events=boot_events,
        )
        return _merge_boot_stage_metrics(
            merged,
            duration_ms=duration_ms,
            stages=stages,
            init_ns=init_ns,
            zyg_ns=zyg_ns,
            guest_cpu_info=guest_cpu_info,
        )


def run_cold_boot_workload(
    serial: str,
    boot_metrics: Optional[Dict[str, Any]] = None,
    adb_bin: str = DEFAULT_ADB_BIN,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Queries guest boot-phase properties and merges them with optional host boot metrics."""
    ctx = WorkloadContext.from_device(
        serial,
        adb_bin=adb_bin,
        process_runner=process_runner,
        boot_metrics=boot_metrics,
    )
    return ColdBootWorkloadStrategy().execute(COLD_BOOT_WORKLOAD_NAME, ctx)
