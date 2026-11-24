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

"""Public exports for `lib.stability`."""

from lib.stability.catalog import (
    DEFAULT_GPU_MODE,
    DEFAULT_SCENARIO_SPEC,
    DEFAULT_TIER,
    SCENARIO_CATALOG,
    TIER_CATALOG,
    ScenarioName,
    StabilityRunConfig,
    StabilityThresholds,
    StabilityTier,
    StabilityVerdict,
    resolve_stability_scenarios,
    resolve_tier_and_duration,
    select_cycle_scenario,
)
from lib.stability.forensics import (
    ForensicBundleInfo,
    collect_forensic_bundle,
    default_forensics_root,
    discover_recent_minidumps,
)
from lib.stability.health import (
    CycleHealthSample,
    GuestHealthSnapshot,
    HostProcessSample,
    StabilityEvaluation,
    StabilityFinding,
    compute_cpu_pct_between_snapshots,
    compute_linear_slope_per_min,
    compute_rgb888_frame_digest,
    evaluate_stability_samples,
    inspect_guest_health,
    inspect_rgb888_frame,
    parse_dropbox_lines,
    query_system_server_pid,
    sample_host_process_health,
)
from lib.stability.render import (
    append_stability_history,
    compare_stability_reports,
    default_stability_history_path,
    expand_safe_path,
    format_clickable_file_link,
    format_file_uri,
    format_osc8_link,
    format_stability_history_markdown,
    load_stability_history,
    render_stability_comparison_markdown,
    render_stability_report_markdown,
    supports_osc8_hyperlinks,
)
from lib.stability.scenarios import (
    ScenarioCycleOutcome,
    StabilityScenarioContext,
    dispatch_stability_scenario,
    run_dev_loop_cycle,
    run_snapshot_stress_cycle,
    run_ui_control_cycle,
)
from lib.stability.session import (
    build_stability_launch_command,
    load_stability_report_json,
    run_managed_stability_session,
    run_stability_session_against_device,
)

__all__ = [
    "CycleHealthSample",
    "DEFAULT_GPU_MODE",
    "DEFAULT_SCENARIO_SPEC",
    "DEFAULT_TIER",
    "ForensicBundleInfo",
    "GuestHealthSnapshot",
    "HostProcessSample",
    "SCENARIO_CATALOG",
    "ScenarioCycleOutcome",
    "ScenarioName",
    "StabilityEvaluation",
    "StabilityFinding",
    "StabilityRunConfig",
    "StabilityScenarioContext",
    "StabilityThresholds",
    "StabilityTier",
    "StabilityVerdict",
    "TIER_CATALOG",
    "append_stability_history",
    "build_stability_launch_command",
    "collect_forensic_bundle",
    "compare_stability_reports",
    "compute_cpu_pct_between_snapshots",
    "compute_linear_slope_per_min",
    "compute_rgb888_frame_digest",
    "default_forensics_root",
    "default_stability_history_path",
    "discover_recent_minidumps",
    "dispatch_stability_scenario",
    "evaluate_stability_samples",
    "expand_safe_path",
    "format_clickable_file_link",
    "format_file_uri",
    "format_osc8_link",
    "format_stability_history_markdown",
    "inspect_guest_health",
    "inspect_rgb888_frame",
    "load_stability_history",
    "load_stability_report_json",
    "parse_dropbox_lines",
    "query_system_server_pid",
    "render_stability_comparison_markdown",
    "render_stability_report_markdown",
    "resolve_stability_scenarios",
    "resolve_tier_and_duration",
    "run_dev_loop_cycle",
    "run_managed_stability_session",
    "run_snapshot_stress_cycle",
    "run_stability_session_against_device",
    "run_ui_control_cycle",
    "sample_host_process_health",
    "select_cycle_scenario",
    "supports_osc8_hyperlinks",
]
