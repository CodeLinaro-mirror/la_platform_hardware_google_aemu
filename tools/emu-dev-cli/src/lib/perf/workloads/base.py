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

"""Base Strategy and Template Method classes for performance benchmark workloads."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional

from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import (
    DEFAULT_ADB_BIN,
    DEFAULT_ITERATIONS,
    DEFAULT_PROFILER_NAME,
    DEFAULT_WARMUP,
)
from lib.perf.stats import is_cv_converged, summarize_samples
from lib.perf_probe_client import PerfProbeSession
from lib.perf_telemetry import WorkloadProfilerSession

LOGGER = logging.getLogger(__name__)

MIN_ADAPTIVE_ITERATIONS: int = 3
MAX_ADAPTIVE_EXTRA_ITERATIONS: int = 2
DEFAULT_PROFILER_ADB_TIMEOUT_SEC: float = 30.0


@dataclass
class WorkloadContext:
    """Execution context passed to every `WorkloadStrategy.execute()` invocation."""

    adb: AdbDevice
    serial: str = ""
    emu_pid: Optional[int] = None
    iterations: int = DEFAULT_ITERATIONS
    warmup: int = DEFAULT_WARMUP
    profiles: List[str] = field(
        default_factory=lambda: [DEFAULT_PROFILER_NAME]
    )
    probe_session: Optional[PerfProbeSession] = None
    boot_metrics: Optional[Dict[str, Any]] = None
    grpc_port: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.serial and self.adb is not None:
            self.serial = self.adb.serial
        if self.profiles is None:
            self.profiles = [DEFAULT_PROFILER_NAME]

    @classmethod
    def from_device(
        cls,
        serial: str,
        emu_pid: Optional[int] = None,
        *,
        iterations: int = DEFAULT_ITERATIONS,
        warmup: int = DEFAULT_WARMUP,
        profiles: Optional[List[str]] = None,
        adb_bin: str = DEFAULT_ADB_BIN,
        process_runner: Optional[ProcessRunner] = None,
        boot_metrics: Optional[Dict[str, Any]] = None,
    ) -> "WorkloadContext":
        """Constructs a WorkloadContext backed by an AdbDevice for `serial`."""
        adb = AdbDevice(serial, adb_bin=adb_bin, process_runner=process_runner)
        return cls(
            adb=adb,
            serial=serial,
            emu_pid=emu_pid,
            iterations=iterations,
            warmup=warmup,
            profiles=list(profiles or [DEFAULT_PROFILER_NAME]),
            boot_metrics=boot_metrics,
        )


class WorkloadStrategy(ABC):
    """Strategy interface for executing a named emulator performance workload."""

    @abstractmethod
    def matches(self, workload_name: str) -> bool:
        """Returns True if this strategy handles `workload_name`."""

    @abstractmethod
    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        """Executes `workload_name` against `ctx` and returns the summarized metric dict."""

    def execute_for_device(
        self,
        workload_name: str,
        serial: str,
        emu_pid: Optional[int],
        **ctx_kwargs: Any,
    ) -> Dict[str, Any]:
        """Constructs a `WorkloadContext.from_device(...)` and executes this strategy."""
        ctx = WorkloadContext.from_device(serial, emu_pid, **ctx_kwargs)
        return self.execute(workload_name, ctx)


class MeasuredWorkloadTemplate(WorkloadStrategy, ABC):
    """Template Method base class for iterative benchmark workloads.

    Standardizes the multi-iteration measurement lifecycle across ADB transfer,
    PerfProbe, and Quickboot snapshot workloads:
      1. `setup_workload`
      2. Untimed warmup iterations (`warmup_count`)
      3. Measured iteration loop with optional `drop_caches`, `WorkloadProfilerSession`
         instrumentation, and adaptive CV<=8% convergence early stopping
      4. `summarize_samples` statistical and telemetry aggregation
      5. Guaranteed `teardown_workload` cleanup in `finally:`
    """

    def setup_workload(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Any:
        """Prepares host/guest state prior to warmup and returns workload state."""
        del workload_name, ctx
        return None

    def warmup_count(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> int:
        """Returns the number of untimed warmup iterations to run before measurement."""
        del workload_name, state
        return max(0, ctx.warmup)

    def target_iterations(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> int:
        """Returns the base number of measured iterations."""
        del workload_name, state
        return max(1, ctx.iterations)

    def allow_adaptive_extra_iterations(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> bool:
        """Returns True if up to `MAX_ADAPTIVE_EXTRA_ITERATIONS` may run when CV > 8%."""
        del workload_name, ctx, state
        return True

    def should_drop_caches_before_iteration(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> bool:
        """Returns True if guest page/slab caches should be dropped before each measured iteration."""
        del workload_name, ctx, state
        return False

    @abstractmethod
    def run_single_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: Any,
        *,
        is_warmup: bool = False,
    ) -> Dict[str, float]:
        """Executes one iteration of the workload and returns sample metrics."""

    def convergence_metric_key(
        self, sample: Dict[str, float], state: Any
    ) -> Optional[str]:
        """Returns the sample key used to evaluate adaptive CV convergence."""
        del state
        if "wall_mib_s" in sample:
            return "wall_mib_s"
        return next(iter(sample.keys()), None)

    def total_mib_per_iteration(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> float:
        """Returns the payload size in MiB per iteration for `cpu_ms_per_mib` calculation."""
        del workload_name, ctx, state
        return 0.0

    def teardown_workload(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> None:
        """Cleans up temporary host/guest artifacts after measurement completes or fails."""
        del workload_name, ctx, state

    def _maybe_drop_caches(
        self, workload_name: str, ctx: WorkloadContext, state: Any
    ) -> None:
        if not self.should_drop_caches_before_iteration(
            workload_name, ctx, state
        ):
            return
        try:
            ctx.adb.drop_caches(timeout=15.0)
        except Exception as exc:
            LOGGER.debug(
                "Ignored error dropping guest caches for %s: %s",
                workload_name,
                exc,
            )

    def _should_stop_early(
        self,
        samples: List[Dict[str, float]],
        sample: Dict[str, float],
        state: Any,
    ) -> bool:
        metric_key = self.convergence_metric_key(sample, state)
        if not metric_key:
            return False
        vals = [
            float(s[metric_key])
            for s in samples
            if isinstance(s.get(metric_key), (int, float))
        ]
        return is_cv_converged(vals)

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        """Template Method orchestrating setup, warmup, measured loop, aggregation, and teardown."""
        state = self.setup_workload(workload_name, ctx)
        try:
            for _ in range(self.warmup_count(workload_name, ctx, state)):
                self.run_single_iteration(
                    workload_name, ctx, state, is_warmup=True
                )

            samples: List[Dict[str, float]] = []
            telemetries: List[Any] = []
            target_iters = self.target_iterations(workload_name, ctx, state)
            max_extra = (
                MAX_ADAPTIVE_EXTRA_ITERATIONS
                if (
                    self.allow_adaptive_extra_iterations(
                        workload_name, ctx, state
                    )
                    and target_iters >= MIN_ADAPTIVE_ITERATIONS
                )
                else 0
            )
            adb_fn = ctx.adb.as_adb_fn(
                default_timeout=DEFAULT_PROFILER_ADB_TIMEOUT_SEC
            )

            idx = 0
            while idx < target_iters + max_extra:
                self._maybe_drop_caches(workload_name, ctx, state)
                with WorkloadProfilerSession(
                    pid=ctx.emu_pid,
                    profiles=ctx.profiles,
                    adb_fn=adb_fn,
                ) as prof:
                    sample = self.run_single_iteration(
                        workload_name, ctx, state, is_warmup=False
                    )

                samples.append(sample)
                telemetries.append(prof.telemetry)
                idx += 1

                if (
                    target_iters <= idx < target_iters + max_extra
                    and self._should_stop_early(samples, sample, state)
                ):
                    break

            return summarize_samples(
                samples,
                telemetries,
                total_mib_per_iter=self.total_mib_per_iteration(
                    workload_name, ctx, state
                ),
            )
        finally:
            self.teardown_workload(workload_name, ctx, state)
