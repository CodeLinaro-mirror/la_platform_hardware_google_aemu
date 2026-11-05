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

"""Ordered registry dispatching benchmark workload names to WorkloadStrategy instances."""

from collections.abc import Sequence
from typing import Any, Dict, List, Tuple

from lib.perf.workloads.adb_transfer import AdbTransferWorkloadStrategy
from lib.perf.workloads.base import WorkloadContext, WorkloadStrategy
from lib.perf.workloads.cold_boot import ColdBootWorkloadStrategy
from lib.perf.workloads.display import DisplayWorkloadStrategy
from lib.perf.workloads.idle import IdleWorkloadStrategy
from lib.perf.workloads.probe import ProbeWorkloadStrategy
from lib.perf.workloads.snapshot import SnapshotWorkloadStrategy


class WorkloadStrategyRegistry:
    """Dispatches workload names to the matching `WorkloadStrategy`."""

    def __init__(self, strategies: Sequence[WorkloadStrategy]) -> None:
        self._strategies: List[WorkloadStrategy] = list(strategies)

    @property
    def strategies(self) -> Tuple[WorkloadStrategy, ...]:
        """Returns the registered strategies in evaluation order."""
        return tuple(self._strategies)

    def register(self, strategy: WorkloadStrategy) -> None:
        """Appends `strategy` to the registry."""
        self._strategies.append(strategy)

    def resolve(self, workload_name: str) -> WorkloadStrategy:
        """Returns the first `WorkloadStrategy` matching `workload_name`."""
        for strategy in self._strategies:
            if strategy.matches(workload_name):
                return strategy
        raise ValueError(
            f"Unsupported benchmark workload '{workload_name}'. "
            "Run 'emu-dev-cli perf list' to view supported suites and workloads."
        )

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        """Resolves and executes `workload_name` against `ctx`."""
        strategy = self.resolve(workload_name)
        return strategy.execute(workload_name, ctx)


def create_default_workload_registry() -> WorkloadStrategyRegistry:
    """Creates a `WorkloadStrategyRegistry` populated with all built-in workload strategies."""
    return WorkloadStrategyRegistry(
        [
            ColdBootWorkloadStrategy(),
            IdleWorkloadStrategy(),
            SnapshotWorkloadStrategy(),
            DisplayWorkloadStrategy(),
            ProbeWorkloadStrategy(),
            AdbTransferWorkloadStrategy(),
        ]
    )
