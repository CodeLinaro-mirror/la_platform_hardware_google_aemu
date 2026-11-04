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

"""Steady-state idle emulator resource footprint workload strategy (`idle_steady_state`)."""

import logging
import time
from typing import Any, Dict, List, Optional

from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf.stats import GUEST_VIRTIO_IRQ_KEYS
from lib.perf.workloads.base import WorkloadContext, WorkloadStrategy
from lib.perf_telemetry import WorkloadProfilerSession

LOGGER = logging.getLogger(__name__)

IDLE_WORKLOAD_NAME: str = "idle_steady_state"
DEFAULT_IDLE_DURATION_SEC: float = 10.0
MIN_IDLE_DURATION_SEC: float = 1.0


class IdleWorkloadStrategy(WorkloadStrategy):
    """Strategy measuring steady-state idle emulator CPU%, per-thread CPU%, VmRSS, and guest loadavg."""

    def __init__(
        self, duration_sec: float = DEFAULT_IDLE_DURATION_SEC
    ) -> None:
        self._duration_sec = duration_sec

    def matches(self, workload_name: str) -> bool:
        return workload_name == IDLE_WORKLOAD_NAME

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        del workload_name
        adb_fn = ctx.adb.as_adb_fn(default_timeout=30.0)
        with WorkloadProfilerSession(
            pid=ctx.emu_pid, profiles=ctx.profiles, adb_fn=adb_fn
        ) as prof:
            time.sleep(max(MIN_IDLE_DURATION_SEC, self._duration_sec))

        load_res = ctx.adb.shell("cat /proc/loadavg", timeout=10.0)
        guest_load_1m = 0.0
        if load_res.returncode == 0 and load_res.stdout.strip():
            try:
                guest_load_1m = float(load_res.stdout.strip().split()[0])
            except ValueError as exc:
                LOGGER.debug("Ignored error in IdleWorkloadStrategy: %s", exc)

        t = prof.telemetry
        girqs = t.guest_irqs or {}
        return {
            "n": 1,
            "host_cpu_pct": t.host_cpu_pct,
            "host_user_cpu_pct": t.host_user_cpu_pct,
            "host_sys_cpu_pct": t.host_sys_cpu_pct,
            "vcpu_guest_pct": t.vcpu_guest_cpu_pct,
            "vcpu_kernel_exit_pct": t.vcpu_kvm_kernel_cpu_pct,
            "vcpu_userspace_exit_pct": t.vcpu_qemu_userspace_cpu_pct,
            "kvm_exits_per_sec": (
                round(t.kvm_exits.get("exits", 0) / t.wall_duration_s, 1)
                if t.kvm_exits and t.wall_duration_s > 0
                else 0.0
            ),
            "kvm_top_exit_reasons": {
                k: v for k, v in (t.kvm_exits or {}).items() if k != "exits"
            },
            "guest_virtio_irqs": {
                k: girqs[k]
                for k in GUEST_VIRTIO_IRQ_KEYS
                if girqs.get(k, 0) > 0
            },
            "guest_ipi_resched": girqs.get("reschedule_ipis", 0),
            "guest_tlb_shootdowns": girqs.get("tlb_shootdowns", 0),
            "rss_mib": t.rss_mib,
            "peak_rss_mib": t.peak_rss_mib,
            "guest_loadavg_1m": guest_load_1m,
            "top_threads_cpu_pct": t.top_threads_cpu_pct,
            "perf_stat": t.perf_stat,
            "top_symbols": t.top_symbols,
        }


def run_idle_workload(
    serial: str,
    emu_pid: Optional[int],
    duration_sec: float = DEFAULT_IDLE_DURATION_SEC,
    **ctx_kwargs: Any,
) -> Dict[str, Any]:
    """Measures steady-state idle emulator CPU%, per-thread CPU%, VmRSS, and guest loadavg."""
    return IdleWorkloadStrategy(duration_sec=duration_sec).execute_for_device(
        IDLE_WORKLOAD_NAME,
        serial,
        emu_pid,
        **ctx_kwargs,
    )
