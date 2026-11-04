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

"""Performance benchmark workload strategies and registry."""

from lib.perf.workloads.adb_transfer import (
    AdbTransferWorkloadStrategy,
    _create_host_payload_dir,
    _create_host_payload_file,
    run_adb_transfer_workload,
)
from lib.perf.workloads.base import (
    MeasuredWorkloadTemplate,
    WorkloadContext,
    WorkloadStrategy,
)
from lib.perf.workloads.cold_boot import (
    ColdBootWorkloadStrategy,
    run_cold_boot_workload,
)
from lib.perf.workloads.display import (
    DisplayWorkloadStrategy,
    run_display_workload,
)
from lib.perf.workloads.idle import (
    IdleWorkloadStrategy,
    run_idle_workload,
)
from lib.perf.workloads.probe import (
    INTENT_SCENARIO_SPECS,
    IntentScenarioSpec,
    ProbeWorkloadStrategy,
    run_probe_workload,
)
from lib.perf.workloads.registry import (
    WorkloadStrategyRegistry,
    create_default_workload_registry,
)
from lib.perf.workloads.snapshot import (
    SnapshotWorkloadStrategy,
    _ensure_valid_console_auth_token,
    run_snapshot_workload,
)

__all__ = [
    "AdbTransferWorkloadStrategy",
    "ColdBootWorkloadStrategy",
    "DisplayWorkloadStrategy",
    "INTENT_SCENARIO_SPECS",
    "IdleWorkloadStrategy",
    "IntentScenarioSpec",
    "MeasuredWorkloadTemplate",
    "ProbeWorkloadStrategy",
    "SnapshotWorkloadStrategy",
    "WorkloadContext",
    "WorkloadStrategy",
    "WorkloadStrategyRegistry",
    "_create_host_payload_dir",
    "_create_host_payload_file",
    "_ensure_valid_console_auth_token",
    "create_default_workload_registry",
    "run_adb_transfer_workload",
    "run_cold_boot_workload",
    "run_display_workload",
    "run_idle_workload",
    "run_probe_workload",
    "run_snapshot_workload",
]
