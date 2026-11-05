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

"""PerfProbe (`perf_probe.apk` + gRPC) workload strategy (`input`, `disk`, `memory`, `cpu`)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from lib.perf.adb import AdbDevice, DROP_CACHES_SHELL_CMD
from lib.perf.catalog import (
    DEFAULT_ITERATIONS,
    DEFAULT_WARMUP,
    PROBE_WORKLOADS,
)
from lib.perf.workloads.base import MeasuredWorkloadTemplate, WorkloadContext
from lib.perf_probe_client import PerfProbeSession

KEY_ARRIVAL_SAMPLE_COUNT = 40
TOUCH_TO_PHOTON_SAMPLE_COUNT = 10
SWIPE_STRESS_EVENTS = 180
SWIPE_STRESS_RATE_HZ = 120.0
DISK_SEQ_SIZE_MIB = 128
DISK_SEQ_TOTAL_MIB = 256.0
DISK_RAND_ITERATIONS = 250
MEM_EPT_SIZE_MIB = 64
MEM_EPT_ROUNDS = 4
MEM_EPT_TOTAL_MIB = 256.0
MEM_STREAM_SIZE_MIB = 32
MEM_STREAM_ITERATIONS = 32
MEM_STREAM_TOTAL_MIB = 1024.0
CPU_SCALING_DURATION_MS = 400
CPU_IPI_ITERATIONS = 3000
MIB_PER_GIB = 1024.0
MS_PER_SECOND = 1000.0


@dataclass(frozen=True)
class IntentScenarioSpec:
    """Declarative specification for an intent-driven `perf_probe.apk` scenario."""

    scenario: str
    extras: Mapping[str, Any]
    total_mib: float = 0.0
    is_disk_probe: bool = False
    field_specs: Tuple[Tuple[str, Optional[int], float], ...] = ()
    custom_extractor: Optional[Callable[[Mapping[str, Any]], Dict[str, float]]] = None

    def extract_metrics(self, rec: Mapping[str, Any]) -> Dict[str, float]:
        """Extracts and rounds sample metrics from a `PerfProbeJson` record."""
        if self.custom_extractor is not None:
            return self.custom_extractor(rec)
        out: Dict[str, float] = {}
        for key, digits, default_val in self.field_specs:
            val = float(rec.get(key, default_val))
            out[key] = round(val, digits) if digits is not None else val
        return out


def _extract_disk_seq_metrics(rec: Mapping[str, Any]) -> Dict[str, float]:
    w_mib_s = float(rec.get("write_mib_s", 0.0))
    r_mib_s = float(rec.get("read_mib_s", 0.0))
    w_ms = float(rec.get("write_ms", 0.0))
    return {
        "wall_mib_s": round(w_mib_s, 2),
        "write_mib_s": round(w_mib_s, 2),
        "read_mib_s": round(r_mib_s, 2),
        "duration_s": round(w_ms / MS_PER_SECOND, 4),
    }


def _extract_mem_stream_metrics(rec: Mapping[str, Any]) -> Dict[str, float]:
    gib_s = float(rec.get("copy_gib_s", 0.0))
    total_ms = float(rec.get("total_ms", 0.0))
    return {
        "wall_mib_s": round(gib_s * MIB_PER_GIB, 2),
        "copy_gib_s": round(gib_s, 3),
        "duration_s": round(total_ms / MS_PER_SECOND, 4),
    }


INTENT_SCENARIO_SPECS: Mapping[str, IntentScenarioSpec] = {
    "disk_seq_128mib": IntentScenarioSpec(
        scenario="disk_seq_128mib",
        extras={"size_mib": DISK_SEQ_SIZE_MIB},
        total_mib=DISK_SEQ_TOTAL_MIB,
        is_disk_probe=True,
        custom_extractor=_extract_disk_seq_metrics,
    ),
    "disk_rand_4k_fsync": IntentScenarioSpec(
        scenario="disk_rand_4k_fsync",
        extras={"iterations": DISK_RAND_ITERATIONS},
        is_disk_probe=True,
        field_specs=(
            ("iops", 2, 0.0),
            ("p50_ms", 3, 0.0),
            ("p95_ms", 3, 0.0),
        ),
    ),
    "mem_ept_page_fault": IntentScenarioSpec(
        scenario="mem_ept_page_fault",
        extras={"size_mib": MEM_EPT_SIZE_MIB, "rounds": MEM_EPT_ROUNDS},
        total_mib=MEM_EPT_TOTAL_MIB,
        field_specs=(
            ("pages_per_sec", 1, 0.0),
            ("us_per_page", 3, 0.0),
            ("total_ms", 2, 0.0),
        ),
    ),
    "mem_stream_copy": IntentScenarioSpec(
        scenario="mem_stream_copy",
        extras={
            "size_mib": MEM_STREAM_SIZE_MIB,
            "iterations": MEM_STREAM_ITERATIONS,
        },
        total_mib=MEM_STREAM_TOTAL_MIB,
        custom_extractor=_extract_mem_stream_metrics,
    ),
    "cpu_compute_scaling": IntentScenarioSpec(
        scenario="cpu_compute_scaling",
        extras={"duration_ms": CPU_SCALING_DURATION_MS},
        field_specs=(
            ("cpu_nt_mops_s", 2, 0.0),
            ("cpu_1t_mops_s", 2, 0.0),
            ("smp_scaling_ratio", 2, 0.0),
            ("vcpus", None, 1.0),
        ),
    ),
    "cpu_ipi_ping_pong": IntentScenarioSpec(
        scenario="cpu_ipi_ping_pong",
        extras={"iterations": CPU_IPI_ITERATIONS},
        field_specs=(
            ("roundtrip_us", 3, 0.0),
            ("switches_per_sec", 1, 0.0),
            ("total_ms", 2, 0.0),
        ),
    ),
}


@dataclass
class _ProbeState:
    """Prepared step callable and metadata for a single probe workload execution."""

    step_fn: Callable[[], Dict[str, float]]
    total_mib: float
    is_disk_probe: bool


class ProbeWorkloadStrategy(MeasuredWorkloadTemplate):
    """Strategy executing `perf_probe.apk` + gRPC microbenchmarks."""

    def matches(self, workload_name: str) -> bool:
        return workload_name in PROBE_WORKLOADS

    def setup_workload(
        self, workload_name: str, ctx: WorkloadContext
    ) -> _ProbeState:
        probe = ctx.probe_session
        if probe is None:
            probe = PerfProbeSession(
                serial=ctx.serial,
                adb_fn=ctx.adb.as_adb_fn(default_timeout=60.0),
                emu_pid=ctx.emu_pid,
                grpc_port=ctx.grpc_port,
            )
            ctx.probe_session = probe

        probe.ensure_installed_and_ready()

        if workload_name == "key_arrival_latency":
            return _ProbeState(
                step_fn=lambda: probe.run_key_arrival_latency(
                    count=KEY_ARRIVAL_SAMPLE_COUNT
                ),
                total_mib=0.0,
                is_disk_probe=False,
            )
        if workload_name == "touch_to_photon":
            return _ProbeState(
                step_fn=lambda: probe.run_touch_to_photon(
                    samples=TOUCH_TO_PHOTON_SAMPLE_COUNT
                ),
                total_mib=0.0,
                is_disk_probe=False,
            )
        if workload_name == "swipe_120hz_stress":
            return _ProbeState(
                step_fn=lambda: probe.run_swipe_120hz_stress(
                    events=SWIPE_STRESS_EVENTS, rate_hz=SWIPE_STRESS_RATE_HZ
                ),
                total_mib=0.0,
                is_disk_probe=False,
            )

        spec = INTENT_SCENARIO_SPECS.get(workload_name)
        if spec is None:
            valid = ", ".join(sorted(PROBE_WORKLOADS))
            raise ValueError(
                f"Unsupported probe workload '{workload_name}'. Valid probe workloads: {valid}."
            )

        def _intent_step() -> Dict[str, float]:
            rec = probe.run_intent_scenario(spec.scenario, dict(spec.extras))
            return spec.extract_metrics(rec)

        return _ProbeState(
            step_fn=_intent_step,
            total_mib=spec.total_mib,
            is_disk_probe=spec.is_disk_probe,
        )

    def warmup_count(
        self, workload_name: str, ctx: WorkloadContext, state: _ProbeState
    ) -> int:
        del workload_name, state
        return max(0, min(ctx.warmup, 1))

    def should_drop_caches_before_iteration(
        self, workload_name: str, ctx: WorkloadContext, state: _ProbeState
    ) -> bool:
        del workload_name, ctx
        return state.is_disk_probe

    def total_mib_per_iteration(
        self, workload_name: str, ctx: WorkloadContext, state: _ProbeState
    ) -> float:
        del workload_name, ctx
        return state.total_mib

    def convergence_metric_key(
        self, sample: Dict[str, float], state: _ProbeState
    ) -> Optional[str]:
        del state
        return next(iter(sample.keys()), None)

    def run_single_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: _ProbeState,
        *,
        is_warmup: bool = False,
    ) -> Dict[str, float]:
        del workload_name, ctx, is_warmup
        return state.step_fn()


class _CallbackAdbDevice(AdbDevice):
    """Adapter wrapping an optional `adb_fn(args, timeout=...)` callable as an `AdbDevice`."""

    def __init__(
        self, serial: str, adb_fn: Optional[Callable[..., Any]]
    ) -> None:
        super().__init__(serial or "emulator-5554")
        self._adb_fn = adb_fn

    def as_adb_fn(self, default_timeout: float = 30.0) -> Any:
        return self._adb_fn

    def drop_caches(self, *, timeout: float = 15.0) -> Any:
        if self._adb_fn is None:
            return None
        return self._adb_fn(
            ["shell", DROP_CACHES_SHELL_CMD],
            timeout=timeout,
        )


def run_probe_workload(
    workload_name: str,
    probe: PerfProbeSession,
    emu_pid: Optional[int],
    iterations: int = DEFAULT_ITERATIONS,
    warmup: int = DEFAULT_WARMUP,
    profiles: Optional[List[str]] = None,
    adb_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Executes a `perf_probe.apk` + gRPC workload (`input`, `disk`, `memory`, `cpu`)
    with host & guest hypervisor telemetry.
    """
    serial = getattr(probe, "serial", "") or "emulator-5554"
    adb = _CallbackAdbDevice(serial, adb_fn)
    ctx = WorkloadContext(
        serial=serial,
        adb=adb,
        emu_pid=emu_pid,
        iterations=iterations,
        warmup=warmup,
        profiles=list(profiles or ["proc"]),
        probe_session=probe,
    )
    return ProbeWorkloadStrategy().execute(workload_name, ctx)
