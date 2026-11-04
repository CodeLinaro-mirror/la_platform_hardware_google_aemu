# Copyright (C) 2026 The Android Open Source Project
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

"""Modular performance benchmark runner package for `emu-dev-cli perf`."""

from lib.perf.adb import AdbDevice, _adb_cmd, detect_active_adb_serial
from lib.perf.catalog import (
    ADB_SPEED_RE,
    BUILTIN_PROFILERS,
    BUILTIN_SUITES,
    BenchmarkPlan,
    EmulatorLaunchConfig,
    MIB,
    ManagedBenchmarkConfig,
    PROBE_WORKLOADS,
    ResolvedPerfTarget,
    resolve_profiles,
    resolve_suites,
)
from lib.perf.compare import (
    compare_perf_reports,
    load_perf_report_any_format,
    resolve_workload_metric_specs,
)
from lib.perf.guest_settle import (
    stabilize_guest_os_after_first_boot,
    wait_for_guest_and_host_settled,
)
from lib.perf.render import (
    _attribute_regression_cause,
    render_comparison_commit_text,
    render_comparison_markdown,
    render_single_report_markdown,
    render_weekly_qualification_report,
)
from lib.perf.session import (
    BenchmarkSessionTemplate,
    LiveDeviceBenchmarkSession,
    ManagedEmulatorBenchmarkSession,
    _cleanup_stale_netsim,
    _find_free_console_port,
    _kill_emulator_process_tree,
    _parse_emulator_boot_completed_ms,
    _resolve_perf_avd_template,
    run_managed_emulator_benchmark,
    run_suite_against_device,
)
from lib.perf.stats import is_cv_converged, summarize_samples
from lib.perf.target_resolver import (
    _detect_default_host,
    inspect_emulator_dir_metadata,
    resolve_perf_target,
)
from lib.perf.workloads import (
    AdbTransferWorkloadStrategy,
    ColdBootWorkloadStrategy,
    DisplayWorkloadStrategy,
    INTENT_SCENARIO_SPECS,
    IdleWorkloadStrategy,
    IntentScenarioSpec,
    MeasuredWorkloadTemplate,
    ProbeWorkloadStrategy,
    SnapshotWorkloadStrategy,
    WorkloadContext,
    WorkloadStrategy,
    WorkloadStrategyRegistry,
    _create_host_payload_dir,
    _create_host_payload_file,
    _ensure_valid_console_auth_token,
    create_default_workload_registry,
    run_adb_transfer_workload,
    run_cold_boot_workload,
    run_display_workload,
    run_idle_workload,
    run_probe_workload,
    run_snapshot_workload,
)

__all__ = [
    "ADB_SPEED_RE",
    "AdbDevice",
    "AdbTransferWorkloadStrategy",
    "BUILTIN_PROFILERS",
    "BUILTIN_SUITES",
    "BenchmarkPlan",
    "BenchmarkSessionTemplate",
    "ColdBootWorkloadStrategy",
    "DisplayWorkloadStrategy",
    "EmulatorLaunchConfig",
    "INTENT_SCENARIO_SPECS",
    "IdleWorkloadStrategy",
    "IntentScenarioSpec",
    "LiveDeviceBenchmarkSession",
    "MIB",
    "ManagedBenchmarkConfig",
    "ManagedEmulatorBenchmarkSession",
    "MeasuredWorkloadTemplate",
    "PROBE_WORKLOADS",
    "ProbeWorkloadStrategy",
    "ResolvedPerfTarget",
    "SnapshotWorkloadStrategy",
    "WorkloadContext",
    "WorkloadStrategy",
    "WorkloadStrategyRegistry",
    "_adb_cmd",
    "_attribute_regression_cause",
    "_cleanup_stale_netsim",
    "_create_host_payload_dir",
    "_create_host_payload_file",
    "_detect_default_host",
    "_ensure_valid_console_auth_token",
    "_find_free_console_port",
    "_kill_emulator_process_tree",
    "_parse_emulator_boot_completed_ms",
    "_resolve_perf_avd_template",
    "compare_perf_reports",
    "create_default_workload_registry",
    "detect_active_adb_serial",
    "inspect_emulator_dir_metadata",
    "is_cv_converged",
    "load_perf_report_any_format",
    "render_comparison_commit_text",
    "render_comparison_markdown",
    "render_single_report_markdown",
    "render_weekly_qualification_report",
    "resolve_perf_target",
    "resolve_profiles",
    "resolve_suites",
    "resolve_workload_metric_specs",
    "run_adb_transfer_workload",
    "run_cold_boot_workload",
    "run_display_workload",
    "run_idle_workload",
    "run_managed_emulator_benchmark",
    "run_probe_workload",
    "run_snapshot_workload",
    "run_suite_against_device",
    "stabilize_guest_os_after_first_boot",
    "summarize_samples",
    "wait_for_guest_and_host_settled",
]
