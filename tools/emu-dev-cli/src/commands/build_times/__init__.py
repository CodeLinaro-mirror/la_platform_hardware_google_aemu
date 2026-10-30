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

"""Build Time & Bazel Profile Analysis Command Suite ('emu-dev-cli build-times')."""

import argparse
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any

from clients.artifact_service import (
    ArtifactService,
    ProdArtifactService,
)
from core.command import Command, CommandGroup, CommandResult, bind_command
from core.context import ExecutionContext
from core.errors import EmuCliError, ExecutionError, UsageError
from lib.build_profile_analyzer import (
    SUPPORTED_BUILD_TARGETS,
    BuildProfileMetrics,
    analyze_single_build,
    annotate_regressions,
    compare_build_profiles,
    fetch_target_history,
    format_delta,
    format_duration,
    resolve_build_targets,
)
from lib.output import format_markdown_table

__all__ = [
    "BuildTimesCommand",
    "BuildTimesDiagnoseCommand",
    "BuildTimesHistoryCommand",
    "BuildTimesInspectCommand",
    "register_parser",
]

logger = logging.getLogger(__name__)

DEFAULT_BRANCH = "git_emu-main-next"
DEFAULT_INSPECT_TARGET = "emulator_linux_x64"
DEFAULT_TREND_TARGET = "emulator_mac_aarch64"
DEFAULT_TOP_N = 10
DEFAULT_LIMIT = 10
MIN_BUILDS_TO_DIAGNOSE = 2
MAX_INSPECT_WORKERS = 8
SOURCE_DIR_BRANCH = "emu-main-next"
MAX_DIAGNOSIS_ROWS = 8
MAX_COMMITS_PER_REPO = 5
HISTORY_SECTION_SEPARATOR = "\n\n---\n\n"

MULTI_TARGET_HEADERS = [
    "Target",
    "Total CI",
    "Queue Wait",
    "Total Bazel Wall",
    "Stage Breakdown",
    "Crit Path",
    "Actions",
    "Machine",
    "#1 Critical Path Bottleneck",
]
HISTORY_HEADERS = [
    "Build ID",
    "Created (UTC)",
    "Total CI",
    "Queue Wait",
    "Total Bazel Wall",
    "Stage Breakdown",
    "Crit Path",
    "Machine",
    "#1 Critical Path Bottleneck",
    "Assessment",
]

TokenServiceFactory = Callable[[ExecutionContext, str], ArtifactService]
BuildAnalyzer = Callable[..., BuildProfileMetrics]


def _truncate(text: str, max_len: int = 68) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_stage_summary_inline(m: BuildProfileMetrics) -> str:
    """Formats a concise inline summary of per-stage Bazel wall times for multi-row tables."""
    if not m.stages:
        return format_duration(m.bazel_wall_sec) if m.profile_available else "N/A"
    return " / ".join(
        f"`{s.stage_id}`: {format_duration(s.bazel_wall_sec)}" for s in m.stages
    )


def _bottleneck_cell(m: BuildProfileMetrics, max_len: int) -> str:
    """Formats the slowest critical path action of a build for a table cell."""
    top_cp = m.top_critical_bottleneck
    if not top_cp:
        return "N/A"
    stage_prefix = f"[{top_cp.stage_id}] " if top_cp.stage_id else ""
    return (
        f"`{format_duration(top_cp.duration_sec)}` "
        f"{stage_prefix}{_truncate(top_cp.name, max_len)}"
    )


def _render_single_build_markdown(m: BuildProfileMetrics, top_n: int = 10) -> str:
    lines: list[str] = []
    lines.append(f"## Build `{m.build_id}` — `{m.target}` ({m.branch})")
    lines.append("")
    if m.stages:
        stage_links = ", ".join(
            f"[`{s.artifact_name}`]({s.artifact_viewer_url})"
            if s.artifact_viewer_url
            else f"`{s.artifact_name}`"
            for s in m.stages
        )
        lines.append(f"- **Bazel Stage Profiles ({len(m.stages)})**: {stage_links}")
    else:
        lines.append(f"- **Profile Artifact**: [{m.artifact_viewer_url}]({m.artifact_viewer_url})")
    lines.append(
        f"- **Builder Host**: `{m.machine_name or 'unknown'}`"
        + (f" (Free Disk: `{m.free_disk_gb} GB`)" if m.free_disk_gb else "")
        + (f" | **Bazel**: `{m.bazel_version}`" if m.bazel_version else "")
    )
    lines.append("")

    sec_num = 1

    # 1. 4-Layer Timing Table
    lines.append(f"### {sec_num}. End-to-End CI vs. Bazel Timing Breakdown")
    sec_num += 1
    timing_headers = [
        "Total CI Time",
        "Queue Wait",
        "Repo Sync",
        "build.py Time",
        "Total Bazel Wall",
        "Total Critical Path",
        "Stages",
    ]
    timing_rows = [
        [
            f"**{format_duration(m.total_ci_sec)}**",
            format_duration(m.queue_sec),
            format_duration(m.sync_sec),
            format_duration(m.build_py_sec),
            f"**{format_duration(m.bazel_wall_sec)}**",
            f"**{format_duration(m.critical_path_sec)}**",
            str(len(m.stages)) if m.stages else ("1" if m.profile_available else "0"),
        ]
    ]
    lines.append(format_markdown_table(timing_headers, timing_rows))
    lines.append("")

    if not m.profile_available:
        lines.append("*Note: No `logs/bazel/*.profile.gz` artifacts were found for this build target.*")
        return "\n".join(lines)

    # Per-Stage Bazel Breakdown Table
    if m.stages:
        lines.append(
            f"### {sec_num}. Per-Stage Bazel Profile Breakdown (`logs/bazel/*.profile.gz`)"
        )
        sec_num += 1
        stage_headers = [
            "Stage",
            "Artifact",
            "Bazel Wall",
            "Load & Analyze",
            "Action Exec",
            "Crit Path",
            "Actions",
            "Remote Execs",
            "#1 Stage Critical Path Bottleneck",
        ]
        stage_rows = []
        for s in m.stages:
            top_cp = s.top_critical_bottleneck
            cp_str = (
                f"`{format_duration(top_cp.duration_sec)}` {_truncate(top_cp.name, 52)}"
                if top_cp
                else "N/A"
            )
            stage_rows.append([
                f"**`{s.stage_id}`** ({s.stage_name})",
                f"`{s.artifact_name}`",
                f"**{format_duration(s.bazel_wall_sec)}**",
                format_duration(s.phase_load_analyze_sec),
                format_duration(s.phase_execute_sec),
                f"**{format_duration(s.critical_path_sec)}**",
                f"{s.total_actions:,}",
                f"{s.remote_executions:,}",
                cp_str,
            ])
        if len(m.stages) > 1:
            stage_rows.append([
                "**Total (All Stages)**",
                f"`{len(m.stages)} profiles`",
                f"**{format_duration(m.bazel_wall_sec)}**",
                format_duration(m.phase_load_analyze_sec),
                format_duration(m.phase_execute_sec),
                f"**{format_duration(m.critical_path_sec)}**",
                f"**{m.total_actions:,}**",
                f"**{m.remote_executions:,}**",
                "",
            ])
        lines.append(format_markdown_table(stage_headers, stage_rows))
        lines.append("")

    # Aggregate Bazel Phase Breakdown + RBE Telemetry
    lines.append(f"### {sec_num}. Aggregate Bazel Phases & Execution Telemetry")
    sec_num += 1
    phase_headers = [
        "Launch/Extract",
        "Initialize",
        "Evaluate Targets",
        "Load & Analyze",
        "Action Execution",
        "Total Actions",
        "Remote Execs",
        "Cache Checks",
    ]
    phase_rows = [
        [
            format_duration(m.phase_launch_sec),
            format_duration(m.phase_init_sec),
            format_duration(m.phase_evaluate_sec),
            format_duration(m.phase_load_analyze_sec),
            f"**{format_duration(m.phase_execute_sec)}**",
            f"{m.total_actions:,}",
            f"{m.remote_executions:,} ({format_duration(m.remote_execution_sec)})",
            f"{m.remote_cache_checks:,}",
        ]
    ]
    lines.append(format_markdown_table(phase_headers, phase_rows))
    lines.append("")

    # Per-Stage Critical Path & Mnemonic Details (when multiple stages exist)
    if len(m.stages) > 1:
        lines.append(f"### {sec_num}. Per-Stage Critical Path & Top Mnemonics")
        sec_num += 1
        for s in m.stages:
            lines.append(
                f"#### Stage `{s.stage_id}` — {s.stage_name} "
                f"(Wall: `{format_duration(s.bazel_wall_sec)}`, "
                f"Crit Path: `{format_duration(s.critical_path_sec)}`, "
                f"Actions: `{s.total_actions:,}`)"
            )
            if s.critical_path_items:
                scp_headers = ["#", "Duration", "% of Stage Crit Path", "Critical Path Action"]
                scp_rows = []
                for idx, item in enumerate(
                    sorted(s.critical_path_items, key=lambda x: -x.duration_sec)[: min(5, top_n)],
                    1,
                ):
                    scp_rows.append([
                        idx,
                        f"**{format_duration(item.duration_sec)}**",
                        f"{item.pct_of_critical_path:.1f}%",
                        f"`{_truncate(item.name, 88)}`",
                    ])
                lines.append(format_markdown_table(scp_headers, scp_rows))
            if s.mnemonics:
                smn_summary = ", ".join(
                    f"`{mn.mnemonic}`: {format_duration(mn.total_sec)} ({mn.count:,} actions, max {format_duration(mn.max_sec)})"
                    for mn in s.mnemonics[:4]
                )
                lines.append(f"- **Top Stage Mnemonics**: {smn_summary}")
            lines.append("")

    # Aggregate Critical Path
    if m.critical_path_items:
        lines.append(
            f"### {sec_num}. Top Critical Path Components Across Stages ({format_duration(m.critical_path_sec)})"
        )
        sec_num += 1
        cp_headers = ["#", "Stage", "Duration", "% of Total Crit Path", "Critical Path Action"]
        cp_rows = []
        sorted_cp = sorted(m.critical_path_items, key=lambda x: -x.duration_sec)[:top_n]
        for idx, item in enumerate(sorted_cp, 1):
            cp_rows.append([
                idx,
                f"`{item.stage_id or 'release'}`",
                f"**{format_duration(item.duration_sec)}**",
                f"{item.pct_of_critical_path:.1f}%",
                f"`{_truncate(item.name, 85)}`",
            ])
        lines.append(format_markdown_table(cp_headers, cp_rows))
        lines.append("")

    # Top Mnemonics
    if m.mnemonics:
        lines.append(f"### {sec_num}. Top Action Mnemonics by Cumulative Time (All Stages)")
        sec_num += 1
        mn_headers = ["Mnemonic", "Count", "Cumulative Time", "Avg Time", "Max Time", "Slowest Action"]
        mn_rows = []
        for mn in m.mnemonics[:top_n]:
            mn_rows.append([
                f"`{mn.mnemonic}`",
                f"{mn.count:,}",
                f"**{format_duration(mn.total_sec)}**",
                f"{mn.avg_sec:.2f}s",
                f"{format_duration(mn.max_sec)}",
                f"`{_truncate(mn.max_action_name, 65)}`",
            ])
        lines.append(format_markdown_table(mn_headers, mn_rows))
        lines.append("")

    # Slowest Individual Actions
    if m.slowest_actions:
        lines.append(
            f"### {sec_num}. Top {min(top_n, len(m.slowest_actions))} Slowest Individual Actions"
        )
        sec_num += 1
        sa_headers = ["#", "Stage", "Duration", "Mnemonic", "Action"]
        sa_rows = []
        for idx, act in enumerate(m.slowest_actions[:top_n], 1):
            sa_rows.append([
                idx,
                f"`{act.get('stage_id') or 'release'}`",
                f"**{format_duration(act['duration_sec'])}**",
                f"`{act['mnemonic']}`",
                f"`{_truncate(act['name'], 80)}`",
            ])
        lines.append(format_markdown_table(sa_headers, sa_rows))

    return "\n".join(lines)


def _single_build_summary(m: BuildProfileMetrics) -> str:
    stages_summary = (
        " ["
        + ", ".join(f"{s.stage_id}={format_duration(s.bazel_wall_sec)}" for s in m.stages)
        + "]"
        if m.stages
        else ""
    )
    return (
        f"Build {m.build_id} ({m.target}): Total CI={format_duration(m.total_ci_sec)} "
        f"(Queue={format_duration(m.queue_sec)}, Bazel Wall={format_duration(m.bazel_wall_sec)}{stages_summary}, "
        f"Critical Path={format_duration(m.critical_path_sec)})"
    )


def _render_multi_target_markdown(
    build_id: str, profiles: Sequence[BuildProfileMetrics]
) -> str:
    rows = [
        [
            f"`{m.target}`",
            f"**{format_duration(m.total_ci_sec)}**",
            format_duration(m.queue_sec),
            f"**{format_duration(m.bazel_wall_sec)}**",
            _format_stage_summary_inline(m),
            format_duration(m.critical_path_sec),
            f"{m.total_actions:,}" if m.profile_available else "N/A",
            f"`{m.machine_name or 'N/A'}`",
            _bottleneck_cell(m, 44),
        ]
        for m in profiles
    ]
    return (
        f"## Multi-Target Build Profile Summary — Build `{build_id}`\n\n"
        + format_markdown_table(MULTI_TARGET_HEADERS, rows)
    )


def _history_row(m: BuildProfileMetrics) -> list[str]:
    created_short = m.creation_time[:16].replace("T", " ") if m.creation_time else "N/A"
    assessment = (
        f"⚠️ **SLOW** ({', '.join(m.regression_reasons)})"
        if m.is_regressed
        else "✅ Healthy"
    )
    return [
        f"`{m.build_id}`",
        created_short,
        f"**{format_duration(m.total_ci_sec)}**",
        format_duration(m.queue_sec),
        f"**{format_duration(m.bazel_wall_sec)}**",
        _format_stage_summary_inline(m),
        format_duration(m.critical_path_sec),
        f"`{m.machine_name or 'N/A'}`",
        _bottleneck_cell(m, 38),
        assessment,
    ]


def _render_history_section(
    target: str, history: Sequence[BuildProfileMetrics], branch: str, limit: int
) -> str:
    if not history:
        return f"### `{target}`\n*No builds found on `{branch}`.*"

    baseline, regressed = annotate_regressions(list(history))
    lines = [f"## Build Time History: `{target}` (Last {len(history)} Builds on `{branch}`)"]
    if baseline:
        lines.append(
            f"- **Healthy Baseline Reference**: Build `{baseline.build_id}` "
            f"(Total CI: `{format_duration(baseline.total_ci_sec)}`, "
            f"Bazel Wall: `{format_duration(baseline.bazel_wall_sec)}` [{_format_stage_summary_inline(baseline)}], "
            f"Critical Path: `{format_duration(baseline.critical_path_sec)}`)"
        )
    if regressed:
        lines.append(
            f"- **Regressed / Slow Builds Detected**: **{len(regressed)} / {len(history)}** builds "
            f"({', '.join('`' + r.build_id + '`' for r in regressed)}). "
            f"Run `emu-dev-cli build-times diagnose --target {target} --limit {limit}` for full root-cause attribution."
        )
    else:
        lines.append("- **Status**: All recent builds are running within healthy baseline thresholds.")
    lines.append("")
    lines.append(format_markdown_table(HISTORY_HEADERS, [_history_row(m) for m in history]))
    return "\n".join(lines)


def _timing_delta_table(
    base_m: BuildProfileMetrics, reg_m: BuildProfileMetrics, comp: dict[str, Any]
) -> str:
    deltas = comp["deltas"]
    headers = [
        "Metric / Phase",
        f"Baseline ({base_m.build_id})",
        f"Regressed ({reg_m.build_id})",
        "Delta",
    ]
    rows = [
        [
            "**Total Android Build CI Time**",
            format_duration(base_m.total_ci_sec),
            f"**{format_duration(reg_m.total_ci_sec)}**",
            f"**{format_delta(deltas['total_ci_sec'], base_m.total_ci_sec)}**",
        ],
        [
            "1. Worker Queue Wait (`sync_start - creation`)",
            format_duration(base_m.queue_sec),
            format_duration(reg_m.queue_sec),
            format_delta(deltas["queue_sec"], base_m.queue_sec),
        ],
        [
            "2. Repo Sync (`sync_finish - sync_start`)",
            format_duration(base_m.sync_sec),
            format_duration(reg_m.sync_sec),
            format_delta(deltas["sync_sec"], base_m.sync_sec),
        ],
        [
            "3. `build.py` Wrapper Time (`target_finish - target_start`)",
            format_duration(base_m.build_py_sec),
            format_duration(reg_m.build_py_sec),
            format_delta(deltas["build_py_sec"], base_m.build_py_sec),
        ],
        [
            "**4. Total Bazel Profile Wall Time (All Stages)**",
            format_duration(base_m.bazel_wall_sec),
            f"**{format_duration(reg_m.bazel_wall_sec)}**",
            f"**{format_delta(deltas['bazel_wall_sec'], base_m.bazel_wall_sec)}**",
        ],
    ]
    for sc in comp.get("stage_comparisons", []):
        rows.append([
            f"   ├─ Stage `{sc['stage_id']}` ({sc['stage_name']})",
            format_duration(sc["baseline_wall_sec"]),
            f"**{format_duration(sc['regressed_wall_sec'])}**",
            format_delta(sc["delta_wall_sec"], sc["baseline_wall_sec"]),
        ])
    rows.extend([
        [
            "   ├─ Bazel Load & Analysis Phase (Total)",
            format_duration(base_m.phase_load_analyze_sec),
            format_duration(reg_m.phase_load_analyze_sec),
            format_delta(deltas["phase_load_analyze_sec"], base_m.phase_load_analyze_sec),
        ],
        [
            "   ├─ Bazel Action Execution Phase (Total)",
            format_duration(base_m.phase_execute_sec),
            format_duration(reg_m.phase_execute_sec),
            format_delta(deltas["phase_execute_sec"], base_m.phase_execute_sec),
        ],
        [
            "   └─ **Bazel Critical Path Duration (Total)**",
            format_duration(base_m.critical_path_sec),
            f"**{format_duration(reg_m.critical_path_sec)}**",
            f"**{format_delta(deltas['critical_path_sec'], base_m.critical_path_sec)}**",
        ],
        [
            "Builder Hostname (`machineName`)",
            f"`{base_m.machine_name or 'N/A'}`",
            f"`{reg_m.machine_name or 'N/A'}`",
            "Changed" if base_m.machine_name != reg_m.machine_name else "Same host",
        ],
    ])
    return format_markdown_table(headers, rows)


def _stage_comparison_table(
    base_m: BuildProfileMetrics, reg_m: BuildProfileMetrics, comp: dict[str, Any]
) -> str:
    stage_comparisons = comp.get("stage_comparisons")
    if not stage_comparisons:
        return ""
    headers = [
        "Stage",
        "Artifact",
        f"Baseline Wall ({base_m.build_id})",
        f"Regressed Wall ({reg_m.build_id})",
        "Wall Delta",
        "Crit Path Delta",
        "Actions (Base → Reg)",
        "#1 Bottleneck in Regressed Stage",
    ]
    rows = [
        [
            f"**`{sc['stage_id']}`** ({sc['stage_name']})",
            f"`{sc['artifact_name']}`",
            format_duration(sc["baseline_wall_sec"]),
            f"**{format_duration(sc['regressed_wall_sec'])}**",
            f"**{format_delta(sc['delta_wall_sec'], sc['baseline_wall_sec'])}**",
            format_delta(sc["delta_crit_sec"], sc["baseline_crit_sec"]),
            f"{sc['baseline_actions']:,} → {sc['regressed_actions']:,}",
            f"`{_truncate(sc['top_bottleneck'], 55)}`" if sc.get("top_bottleneck") else "N/A",
        ]
        for sc in stage_comparisons
    ]
    return format_markdown_table(headers, rows)


def _critical_path_delta_table(
    base_m: BuildProfileMetrics, reg_m: BuildProfileMetrics, comp: dict[str, Any]
) -> str:
    regressions = comp["critical_path_regressions"]
    if not regressions:
        return ""
    headers = [
        "Stage",
        "Critical Path Action",
        f"Baseline ({base_m.build_id})",
        f"Regressed ({reg_m.build_id})",
        "Delta",
    ]
    rows = [
        [
            f"`{item.get('stage_id') or 'release'}`",
            f"`{_truncate(item['name'], 75)}`",
            format_duration(item["baseline_sec"]),
            f"**{format_duration(item['regressed_sec'])}**",
            f"**{format_delta(item['delta_sec'], item['baseline_sec'])}**",
        ]
        for item in regressions[:MAX_DIAGNOSIS_ROWS]
    ]
    return format_markdown_table(headers, rows)


def _mnemonic_regression_table(comp: dict[str, Any]) -> str:
    regressions = comp["mnemonic_regressions"]
    if not regressions:
        return ""
    headers = [
        "Mnemonic",
        "Actions (Base → Reg)",
        "Cumulative Time Delta",
        "Max Single Action (Base → Reg)",
        "Slowest Action in Regressed Build",
    ]
    rows = [
        [
            f"`{mn['mnemonic']}`",
            f"{mn['baseline_count']:,} → {mn['regressed_count']:,}",
            f"**{format_delta(mn['delta_total_sec'], mn['baseline_total_sec'])}**",
            f"{format_duration(mn['baseline_max_sec'])} → **{format_duration(mn['regressed_max_sec'])}**",
            f"`{_truncate(mn['max_action_name'], 60)}`",
        ]
        for mn in regressions[:MAX_DIAGNOSIS_ROWS]
    ]
    return format_markdown_table(headers, rows)


def _repo_changes_table(comp: dict[str, Any]) -> str:
    changed_repos = comp["changed_repos"]
    if not changed_repos:
        return ""
    rows = []
    for repo in changed_repos:
        sha_range = f"`{repo['old_sha']}..{repo['new_sha']}`"
        commits = (
            "<br>".join(f"`{_truncate(c, 65)}`" for c in repo["commits"][:MAX_COMMITS_PER_REPO])
            if repo["commits"]
            else sha_range
        )
        rows.append([f"`{repo['repo']}`", f"`{repo['path'] or 'N/A'}`", sha_range, commits])
    return format_markdown_table(
        ["Repository", "Path", "Revision Range", "Commits Landed"], rows
    )


def _render_diagnosis_markdown(
    target: str,
    base_m: BuildProfileMetrics,
    reg_m: BuildProfileMetrics,
    comp: dict[str, Any],
) -> str:
    chronology = comp["chronology"]
    sections = [
        (
            "Executive Root-Cause Attribution",
            "\n".join(f"- {cause}" for cause in comp["primary_causes"]),
        ),
        (
            "Four-Layer Timing Delta (`BUILD_INFO` + `logs/bazel/*.profile.gz`)",
            _timing_delta_table(base_m, reg_m, comp),
        ),
        ("Per-Stage Bazel Profile Comparison", _stage_comparison_table(base_m, reg_m, comp)),
        ("Critical Path Action Deltas", _critical_path_delta_table(base_m, reg_m, comp)),
        ("Action Mnemonic Regressions", _mnemonic_regression_table(comp)),
        (
            f"Git Repository Changes (`{chronology['older_build_id']}` → `{chronology['newer_build_id']}`)",
            _repo_changes_table(comp),
        ),
    ]
    lines = [
        f"## Build Time Regression Diagnosis — `{target}` "
        f"(`{reg_m.build_id}` vs Baseline `{base_m.build_id}`)",
        "",
    ]
    number = 1
    for title, body in sections:
        if not body:
            continue
        lines.extend([f"### {number}. {title}", body, ""])
        number += 1
    return "\n".join(lines).rstrip("\n")


def _resolve_targets(raw_target: str) -> list[str]:
    try:
        return resolve_build_targets(raw_target)
    except ValueError as exc:
        raise UsageError(
            str(exc),
            remediation=(
                "Pass --target with a supported target, an alias, a comma-separated list, "
                f"or 'all' (supported: {', '.join(SUPPORTED_BUILD_TARGETS)})."
            ),
        ) from exc


def _slowness(m: BuildProfileMetrics) -> tuple[float, float]:
    return (m.bazel_wall_sec, m.total_ci_sec)


def _find_build(
    history: Sequence[BuildProfileMetrics], build_id: str
) -> BuildProfileMetrics | None:
    return next((m for m in history if m.build_id == str(build_id)), None)


def _artifact_service_for_token(ctx: ExecutionContext, token: str) -> ArtifactService:
    """Builds the production ArtifactService that authenticates REST calls with `--token`."""
    return ProdArtifactService(ctx.process_runner, event_bus=ctx.event_bus, token=token)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--target",
        "-t",
        type=str,
        default=DEFAULT_INSPECT_TARGET,
        help=(
            f"Target name, comma-separated list, alias (linux, asan, tsan, mac, windows), or 'all'. "
            f"Supported: {', '.join(SUPPORTED_BUILD_TARGETS)}"
        ),
    )
    p.add_argument(
        "--branch",
        "-b",
        type=str,
        default=DEFAULT_BRANCH,
        help="Android Build branch (default: git_emu-main-next)",
    )
    p.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token for Android Build API",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local ~/.cache/emu-dev-cli/build-profiles cache and re-parse artifacts",
    )


class _BuildTimesBaseCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Shared Android Build wiring for the build-times subcommands."""

    def __init__(
        self,
        *,
        token_service_factory: TokenServiceFactory = _artifact_service_for_token,
        analyze_build: BuildAnalyzer = analyze_single_build,
    ) -> None:
        self._token_service_factory = token_service_factory
        self._analyze_build = analyze_build

    def _client(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> ArtifactService:
        token = getattr(args, "token", None)
        if token and (
            self._token_service_factory is not _artifact_service_for_token
            or isinstance(ctx.artifact_service, ProdArtifactService)
        ):
            return self._token_service_factory(ctx, token)
        return ctx.artifact_service


class BuildTimesInspectCommand(_BuildTimesBaseCommand):
    """Command implementation for `emu-dev-cli build-times inspect`."""

    name = "inspect"
    help_text = "Inspect how fast a specific build ID (or latest build) ran across all stages and view its critical path"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        _add_common_args(parser)
        parser.add_argument(
            "--build-id",
            "--bid",
            dest="build_id",
            type=str,
            default=None,
            help="Specific Android Build ID (e.g. 16411294). Defaults to latest submitted build if omitted.",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=DEFAULT_TOP_N,
            help="Number of top critical path items, mnemonics, and slowest actions to show (default: 10)",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        _resolve_targets(str(getattr(args, "target", DEFAULT_INSPECT_TARGET)))
        top_n = int(getattr(args, "top", DEFAULT_TOP_N))
        if top_n <= 0:
            raise UsageError(
                f"Top item count (--top) must be positive (got {top_n}).",
                remediation="Pass a positive integer to --top (e.g. --top 10).",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        branch = str(getattr(args, "branch", DEFAULT_BRANCH))
        targets = _resolve_targets(str(getattr(args, "target", DEFAULT_INSPECT_TARGET)))
        client = self._client(args, ctx)
        build_id = str(
            getattr(args, "build_id", None) or client.resolve_build_id(branch, targets[0])
        )
        profiles = self._analyze_targets(
            client,
            build_id,
            targets,
            branch,
            use_cache=not getattr(args, "no_cache", False),
        )

        if len(profiles) == 1:
            md = _render_single_build_markdown(
                profiles[0], top_n=int(getattr(args, "top", DEFAULT_TOP_N))
            )
            summary = _single_build_summary(profiles[0])
        else:
            md = _render_multi_target_markdown(build_id, profiles)
            summary = f"Inspected build {build_id} across {len(profiles)} targets."

        data: dict[str, Any] = {
            "status": "success",
            "summary": summary,
            "build_id": build_id,
            "targets": targets,
            "markdown_table": md,
            "profiles": [m.to_dict() for m in profiles],
        }
        return CommandResult(
            data=data,
            action="build-times inspect",
            summary=summary,
            markdown_table=md,
        )

    def _analyze_targets(
        self,
        client: ArtifactService,
        build_id: str,
        targets: Sequence[str],
        branch: str,
        *,
        use_cache: bool,
    ) -> list[BuildProfileMetrics]:
        """Analyzes every target in parallel, skipping targets that fail unless all of them do."""
        results: dict[str, BuildProfileMetrics] = {}
        failures: dict[str, Exception] = {}
        with ThreadPoolExecutor(max_workers=min(MAX_INSPECT_WORKERS, len(targets))) as pool:
            futures = {
                pool.submit(
                    self._analyze_build,
                    build_id=build_id,
                    target=target,
                    branch=branch,
                    client=client,
                    use_cache=use_cache,
                ): target
                for target in targets
            }
            for future in as_completed(futures):
                target = futures[future]
                try:
                    results[target] = future.result()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed inspecting %s on %s: %s", build_id, target, exc)
                    failures[target] = exc

        if not results:
            first_error = next(iter(failures.values()))
            if isinstance(first_error, EmuCliError):
                raise first_error
            details = "; ".join(f"{target}: {exc}" for target, exc in failures.items())
            raise ExecutionError(
                f"Could not analyze build {build_id} for any requested target ({details}).",
                remediation="Check that the build ID exists for these targets in go/ab, then retry with --no-cache.",
                cause=first_error,
            )
        return [results[target] for target in targets if target in results]


class BuildTimesHistoryCommand(_BuildTimesBaseCommand):
    """Command implementation for `emu-dev-cli build-times history`."""

    name = "history"
    help_text = (
        "Analyze the last N builds for a target across all Bazel stages "
        "(e.g. 'What happened to the last 10 mac arm builds?')"
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        _add_common_args(parser)
        parser.set_defaults(target=DEFAULT_TREND_TARGET)
        parser.add_argument(
            "--limit",
            "-n",
            type=int,
            default=DEFAULT_LIMIT,
            help="Number of recent submitted builds to analyze (default: 10)",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        _resolve_targets(str(getattr(args, "target", DEFAULT_TREND_TARGET)))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        if limit <= 0:
            raise UsageError(
                f"History limit (--limit) must be positive (got {limit}).",
                remediation="Pass a positive integer to --limit (e.g. --limit 10).",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        branch = str(getattr(args, "branch", DEFAULT_BRANCH))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        targets = _resolve_targets(str(getattr(args, "target", DEFAULT_TREND_TARGET)))
        client = self._client(args, ctx)

        sections: list[str] = []
        records: dict[str, list[dict[str, Any]]] = {}
        for target in targets:
            history = fetch_target_history(
                target=target,
                branch=branch,
                limit=limit,
                use_cache=not getattr(args, "no_cache", False),
                client=client,
            )
            records[target] = [m.to_dict() for m in history]
            sections.append(_render_history_section(target, history, branch, limit))

        full_md = HISTORY_SECTION_SEPARATOR.join(sections)
        summary = f"Analyzed build time history for {', '.join(targets)} (limit={limit})."
        data: dict[str, Any] = {
            "status": "success",
            "summary": summary,
            "targets": targets,
            "markdown_table": full_md,
            "history": records,
        }
        return CommandResult(
            data=data,
            action="build-times history",
            summary=summary,
            markdown_table=full_md,
        )


class BuildTimesDiagnoseCommand(_BuildTimesBaseCommand):
    """Command implementation for `emu-dev-cli build-times diagnose` (and `compare`)."""

    name = "diagnose"
    aliases = ("compare",)
    help_text = "Find out why builds became slow by comparing regressed builds against baseline across all Bazel stages"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        _add_common_args(parser)
        parser.set_defaults(target=DEFAULT_TREND_TARGET)
        parser.add_argument(
            "--limit",
            "-n",
            type=int,
            default=DEFAULT_LIMIT,
            help="Number of recent builds to scan when auto-detecting regressions (default: 10)",
        )
        parser.add_argument(
            "--baseline",
            type=str,
            default=None,
            help="Explicit fast baseline build ID (auto-detected if omitted)",
        )
        parser.add_argument(
            "--regressed",
            type=str,
            default=None,
            help="Explicit slow/regressed build ID to diagnose (auto-detected if omitted)",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        _resolve_targets(str(getattr(args, "target", DEFAULT_TREND_TARGET)))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        both_ids_given = getattr(args, "baseline", None) and getattr(args, "regressed", None)
        if limit < MIN_BUILDS_TO_DIAGNOSE and not both_ids_given:
            raise UsageError(
                f"History scan limit (--limit) must be at least {MIN_BUILDS_TO_DIAGNOSE} when auto-detecting regressions (got {limit}).",
                remediation="Pass --limit >= 2 or specify both --baseline <bid> and --regressed <bid>.",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        target = _resolve_targets(str(getattr(args, "target", DEFAULT_TREND_TARGET)))[0]
        base_m, reg_m = self._select_builds(args, ctx, target)
        comp = compare_build_profiles(
            base_m,
            reg_m,
            source_root=ctx.config.get_source_directory(SOURCE_DIR_BRANCH),
            runner=ctx.process_runner,
        )
        md = _render_diagnosis_markdown(target, base_m, reg_m, comp)
        summary = comp["primary_causes"][0] if comp["primary_causes"] else "Diagnosis complete."
        data: dict[str, Any] = {
            "status": "success",
            "summary": summary,
            "target": target,
            "markdown_table": md,
            "diagnosis": comp,
        }
        return CommandResult(
            data=data,
            action="build-times diagnose",
            summary=summary,
            markdown_table=md,
        )

    def _select_builds(
        self, args: argparse.Namespace, ctx: ExecutionContext, target: str
    ) -> tuple[BuildProfileMetrics, BuildProfileMetrics]:
        """Returns (baseline, regressed): explicit IDs win, the rest is auto-detected from history."""
        branch = str(getattr(args, "branch", DEFAULT_BRANCH))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        use_cache = not getattr(args, "no_cache", False)
        baseline_id = getattr(args, "baseline", None)
        regressed_id = getattr(args, "regressed", None)
        client = self._client(args, ctx)

        def analyze(build_id: str) -> BuildProfileMetrics:
            return self._analyze_build(
                build_id=str(build_id),
                target=target,
                branch=branch,
                client=client,
                use_cache=use_cache,
            )

        if baseline_id and regressed_id:
            if str(baseline_id) == str(regressed_id):
                raise ExecutionError(
                    f"Baseline and regressed builds resolved to the same build ID ({baseline_id}).",
                    remediation="Specify distinct build IDs with --baseline and --regressed.",
                )
            return analyze(baseline_id), analyze(regressed_id)

        history = fetch_target_history(
            target=target, branch=branch, limit=limit, use_cache=use_cache, client=client
        )
        if len(history) < MIN_BUILDS_TO_DIAGNOSE:
            raise ExecutionError(
                f"Not enough completed builds found for '{target}' on '{branch}' to diagnose (found {len(history)}).",
                remediation="Increase --limit or specify explicit --baseline and --regressed build IDs.",
            )

        base_m, regressed_list = annotate_regressions(history)
        if baseline_id:
            base_m = _find_build(history, baseline_id) or analyze(baseline_id)
        if base_m is None:
            raise ExecutionError(
                f"None of the {len(history)} recent '{target}' builds on '{branch}' has timing data to use as a baseline.",
                remediation="Pass --baseline <bid> with a build that has BUILD_INFO and Bazel profiles, or increase --limit.",
            )

        if regressed_id:
            reg_m = _find_build(history, regressed_id) or analyze(regressed_id)
        elif regressed_list:
            reg_m = max(regressed_list, key=_slowness)
        else:
            # No build crossed the regression threshold: compare the slowest one against the baseline.
            reg_m = max(
                (m for m in history if m.build_id != base_m.build_id), key=_slowness
            )
        return base_m, reg_m


class BuildTimesCommand(CommandGroup):
    """Top-level `build-times` (and alias `profile`) subcommand group."""

    name = "build-times"
    aliases = ("profile",)
    help_text = "Analyze CI build times and Bazel logs/bazel/*.profile.gz stages across emu-main-next targets"
    description = (
        "Fetch and analyze BUILD_INFO and all logs/bazel/*.profile.gz stages from Android Build (go/ab) "
        "to inspect single builds, view multi-build trends, and diagnose build time regressions."
    )
    subcommand_dest = "build_times_cmd"
    subcommand_help = "Build time analysis subcommands"

    def __init__(self, subcommands: Sequence[Command[Any, Any]] | None = None) -> None:
        super().__init__(
            subcommands
            or [
                BuildTimesInspectCommand(),
                BuildTimesHistoryCommand(),
                BuildTimesDiagnoseCommand(),
            ]
        )


def register_parser(subparsers: Any) -> None:
    """Registers 'emu-dev-cli build-times' (and alias 'profile') subcommands."""
    bind_command(subparsers, BuildTimesCommand())
