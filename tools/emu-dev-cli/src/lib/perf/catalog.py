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

"""Built-in benchmark suites, profiler catalog, and benchmark parameter objects."""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

MIB: int = 1024 * 1024

DEFAULT_SUITE_NAME: str = "adb"
DEFAULT_COMPARE_SUITE_SPEC: str = "adb-tmpfs,adb-disk"
DEFAULT_PROFILER_NAME: str = "proc"
CUSTOM_SUITE_NAME: str = "custom"

DEFAULT_ITERATIONS: int = 5
DEFAULT_WARMUP: int = 1
DEFAULT_BOOTS: int = 1
DEFAULT_MANAGED_SETTLE_SEC: float = 30.0
DEFAULT_LIVE_SETTLE_SEC: float = 0.0
DEFAULT_ADB_BIN: str = "adb"

PROBE_WORKLOADS = {
    "key_arrival_latency",
    "touch_to_photon",
    "swipe_120hz_stress",
    "disk_seq_128mib",
    "disk_rand_4k_fsync",
    "mem_ept_page_fault",
    "mem_stream_copy",
    "cpu_compute_scaling",
    "cpu_ipi_ping_pong",
}

BUILTIN_SUITES: Dict[str, Dict[str, Any]] = {
    "adb": {
        "description": (
            "Full Host<->Guest ADB file transfer suite (64MiB/256MiB tmpfs & disk, 100x1MiB directory)"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
            "push_64MiB_disk",
            "pull_64MiB_disk",
            "push_256MiB_disk",
            "pull_256MiB_disk",
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
        ],
    },
    "adb-quick": {
        "description": (
            "Fast transport-isolated ADB suite using guest /dev/shm tmpfs (64MiB & 256MiB push/pull)"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
        ],
    },
    "adb-tmpfs": {
        "description": (
            "Isolates ADB + virtio-vsock/pipe transport from guest block I/O using /dev/shm"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
        ],
    },
    "adb-disk": {
        "description": (
            "End-to-end ADB transfer to/from /data/local/tmp (includes virtio-blk & guest filesystem)"
        ),
        "workloads": [
            "push_64MiB_disk",
            "pull_64MiB_disk",
            "push_256MiB_disk",
            "pull_256MiB_disk",
        ],
    },
    "adb-dir": {
        "description": (
            "Multi-file 100x1MiB directory push/pull stressing per-file stat/open/close latency"
        ),
        "workloads": [
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
        ],
    },
    "input": {
        "description": (
            "Sub-millisecond v1 gRPC -> virtio-input -> PerfProbeActivity key arrival, Touch-to-Photon, and 120Hz swipe stress"
        ),
        "workloads": [
            "key_arrival_latency",
            "touch_to_photon",
            "swipe_120hz_stress",
        ],
    },
    "disk": {
        "description": (
            "Guest storage & virtio-blk / QCOW2 microbenchmarks via perf_probe.apk (sequential 128MiB & random 4K fsync IOPS)"
        ),
        "workloads": [
            "disk_seq_128mib",
            "disk_rand_4k_fsync",
        ],
    },
    "memory": {
        "description": (
            "Guest SharedMemory mmap 4KiB stride demand-paging (EPT/Stage-2 faults & TLB shootdowns) and RAM stream copy"
        ),
        "workloads": [
            "mem_ept_page_fault",
            "mem_stream_copy",
        ],
    },
    "cpu": {
        "description": (
            "Guest 1-thread vs N-vCPU compute scaling and cross-vCPU futex/IPI wakeup ping-pong via perf_probe.apk"
        ),
        "workloads": [
            "cpu_compute_scaling",
            "cpu_ipi_ping_pong",
        ],
    },
    "idle": {
        "description": (
            "Steady-state idle emulator resource footprint (Host CPU%, per-thread CPU%, VmRSS, guest loadavg)"
        ),
        "workloads": [
            "idle_steady_state",
        ],
    },
    "boot": {
        "description": (
            "Cold boot wall-clock duration and guest boot phase breakdown (ro.boottime.init/zygote, uptime)"
        ),
        "workloads": [
            "cold_boot",
        ],
    },
    "snapshot": {
        "description": (
            "Quickboot snapshot save & load latency (`adb emu avd snapshot save/load`)"
        ),
        "workloads": [
            "snapshot_save",
            "snapshot_load",
        ],
    },
    "display": {
        "description": (
            "Guest graphics (`dumpsys gfxinfo` FPS/Jank/p50/p90) & host CPU% during display rendering"
        ),
        "workloads": [
            "display_gfxinfo",
        ],
    },
    "all": {
        "description": (
            "Full emulator binary qualification scorecard (boot, idle, adb, input, disk, memory, cpu, display, snapshot)"
        ),
        "workloads": [
            "cold_boot",
            "idle_steady_state",
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
            "push_256MiB_disk",
            "pull_256MiB_disk",
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
            "key_arrival_latency",
            "touch_to_photon",
            "swipe_120hz_stress",
            "disk_seq_128mib",
            "disk_rand_4k_fsync",
            "mem_ept_page_fault",
            "mem_stream_copy",
            "cpu_compute_scaling",
            "cpu_ipi_ping_pong",
            "display_gfxinfo",
            "snapshot_save",
            "snapshot_load",
        ],
    },
}

BUILTIN_PROFILERS: Dict[str, str] = {
    "proc": (
        "Always-on zero-dependency /proc/<pid> sampler: Host CPU%, per-thread CPU% breakdown, VmRSS, CPU-ms/MiB"
    ),
    "perf-stat": (
        "Linux `perf stat -p <pid>` hardware counters: cycles, instructions, IPC, context-switches, page-faults"
    ),
    "perf-record": (
        "Linux `perf record -F 999 -g -p <pid>` callgraph profiler: extracts top C++/Rust symbol CPU% shares"
    ),
    "perfetto": (
        "Host Perfetto tracing (`--trace <file.pftrace>`) + automatic `trace_processor_shell` top-slice digest"
    ),
    "emu-perf-stat": (
        "Emulator built-in `-perf-stat <file>` periodic `EmulatorPerformanceStats` protobuf reporter"
    ),
}

ADB_SPEED_RE = re.compile(r"([\d.]+)\s+MB/s\s+\(\d+\s+bytes\s+in\s+([\d.]+)s\)")


@dataclass
class ResolvedPerfTarget:
    """Represents a resolved target for `perf run` or `perf compare`."""

    spec: str
    kind: str  # "json", "emulator_dir", "running_device"
    label: str
    emulator_dir: Optional[str] = None
    emulator_bin: Optional[str] = None
    json_path: Optional[str] = None
    build_id: Optional[str] = None
    branch: Optional[str] = None
    pkg_revision: Optional[str] = None
    engine: Optional[str] = None  # "qemu-next" or "qemu-now"


def _append_unique(target: List[str], items: Sequence[str]) -> None:
    for val in items:
        if val not in target:
            target.append(val)


def resolve_suites(suite_arg: str) -> Tuple[List[str], List[str]]:
    """Resolves a comma-separated `--suite` argument into `(suite_names, ordered_workloads)`."""
    raw_items = [
        s.strip().lower()
        for s in (suite_arg or DEFAULT_SUITE_NAME).split(",")
        if s.strip()
    ]
    if not raw_items:
        raw_items = [DEFAULT_SUITE_NAME]

    all_known_workloads = {
        w for s_info in BUILTIN_SUITES.values() for w in s_info["workloads"]
    }
    suites: List[str] = []
    workloads: List[str] = []
    for item in raw_items:
        if item in BUILTIN_SUITES:
            _append_unique(suites, [item])
            _append_unique(workloads, BUILTIN_SUITES[item]["workloads"])
            continue

        matched = next(
            (w for w in all_known_workloads if w.lower() == item),
            None,
        )
        if matched is not None:
            _append_unique(suites, [CUSTOM_SUITE_NAME])
            _append_unique(workloads, [matched])
            continue

        valid = ", ".join(sorted(BUILTIN_SUITES.keys()))
        raise ValueError(
            f"Unknown performance suite '{item}'. Valid suites: {valid}. "
            "Run 'emu-dev-cli perf list' to view all suites and workloads."
        )
    return suites, workloads


def resolve_profiles(profile_arg: Optional[str]) -> List[str]:
    """Resolves comma-separated `--profile` names, always including `proc`."""
    profiles = [DEFAULT_PROFILER_NAME]
    if not profile_arg:
        return profiles
    for item in profile_arg.split(","):
        p = item.strip().lower()
        if not p:
            continue
        if p not in BUILTIN_PROFILERS:
            valid = ", ".join(sorted(BUILTIN_PROFILERS.keys()))
            raise ValueError(
                f"Unknown profiler '{p}'. Valid profilers: {valid}. "
                "Run 'emu-dev-cli perf list' to view available profilers."
            )
        if p not in profiles:
            profiles.append(p)
    return profiles


@dataclass
class BenchmarkPlan:
    """Parameter object encapsulating workload selection, iteration counts, and profilers."""

    suites: List[str]
    workloads: List[str]
    iterations: int = DEFAULT_ITERATIONS
    warmup: int = DEFAULT_WARMUP
    boots: int = DEFAULT_BOOTS
    settle_seconds: float = DEFAULT_MANAGED_SETTLE_SEC
    profiles: List[str] = field(default_factory=lambda: [DEFAULT_PROFILER_NAME])

    @classmethod
    def from_args(
        cls,
        args: Any,
        *,
        default_suite: str = DEFAULT_SUITE_NAME,
        default_settle_seconds: float = DEFAULT_MANAGED_SETTLE_SEC,
        suites: Optional[List[str]] = None,
        workloads: Optional[List[str]] = None,
        profiles: Optional[List[str]] = None,
    ) -> "BenchmarkPlan":
        """Builds a BenchmarkPlan from parsed CLI arguments."""
        if suites is None or workloads is None:
            resolved_suites, resolved_workloads = resolve_suites(
                getattr(args, "suite", default_suite) or default_suite
            )
            suites = suites if suites is not None else resolved_suites
            workloads = workloads if workloads is not None else resolved_workloads

        test_filter = getattr(args, "test", None)
        if test_filter:
            pattern = str(test_filter).strip()
            filtered = [w for w in workloads if pattern in w]
            workloads = filtered if filtered else [pattern]

        resolved_profiles = (
            profiles
            if profiles is not None
            else resolve_profiles(getattr(args, "profile", None))
        )
        raw_settle = getattr(args, "settle_seconds", None)
        settle_sec = (
            float(raw_settle)
            if raw_settle is not None
            else float(default_settle_seconds)
        )
        return cls(
            suites=list(suites),
            workloads=list(workloads),
            iterations=int(getattr(args, "iterations", DEFAULT_ITERATIONS) or DEFAULT_ITERATIONS),
            warmup=int(getattr(args, "warmup", DEFAULT_WARMUP) or DEFAULT_WARMUP),
            boots=int(getattr(args, "boots", DEFAULT_BOOTS) or DEFAULT_BOOTS),
            settle_seconds=settle_sec,
            profiles=list(resolved_profiles),
        )


@dataclass
class EmulatorLaunchConfig:
    """Parameter object encapsulating managed emulator binary, AVD provisioning, and boot options."""

    target: ResolvedPerfTarget
    avd_name: Optional[str] = None
    boots: int = DEFAULT_BOOTS
    clean_avd: bool = True
    sysimg_dir: Optional[str] = None
    trace_path: Optional[str] = None
    adb_bin: str = DEFAULT_ADB_BIN

    @classmethod
    def from_args(
        cls,
        target: ResolvedPerfTarget,
        args: Any,
        *,
        adb_bin: str = DEFAULT_ADB_BIN,
    ) -> "EmulatorLaunchConfig":
        """Builds an EmulatorLaunchConfig from a resolved target and parsed CLI arguments."""
        return cls(
            target=target,
            avd_name=getattr(args, "avd", None),
            boots=int(getattr(args, "boots", DEFAULT_BOOTS) or DEFAULT_BOOTS),
            clean_avd=not bool(getattr(args, "reuse_avd", False)),
            sysimg_dir=getattr(args, "sysimg_dir", None),
            trace_path=getattr(args, "trace", None),
            adb_bin=adb_bin,
        )


@dataclass
class ManagedBenchmarkConfig:
    """Composite parameter object pairing `EmulatorLaunchConfig` and `BenchmarkPlan`."""

    launch: EmulatorLaunchConfig
    plan: BenchmarkPlan

    @classmethod
    def from_args(
        cls,
        target: ResolvedPerfTarget,
        args: Any,
        *,
        default_suite: str = DEFAULT_SUITE_NAME,
        default_settle_seconds: float = DEFAULT_MANAGED_SETTLE_SEC,
        suites: Optional[List[str]] = None,
        workloads: Optional[List[str]] = None,
        profiles: Optional[List[str]] = None,
        adb_bin: str = DEFAULT_ADB_BIN,
    ) -> "ManagedBenchmarkConfig":
        """Constructs a ManagedBenchmarkConfig directly from a resolved target and CLI arguments."""
        return cls(
            launch=EmulatorLaunchConfig.from_args(target, args, adb_bin=adb_bin),
            plan=BenchmarkPlan.from_args(
                args,
                default_suite=default_suite,
                default_settle_seconds=default_settle_seconds,
                suites=suites,
                workloads=workloads,
                profiles=profiles,
            ),
        )
