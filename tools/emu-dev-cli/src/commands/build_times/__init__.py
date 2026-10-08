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

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import sys
from typing import Any, Dict, List, Optional

from commands.source_directory import get_source_directory
from lib.android_build_client import AndroidBuildClient
from lib.build_profile_analyzer import (
    SUPPORTED_BUILD_TARGETS,
    TARGET_ALIASES,
    BuildProfileMetrics,
    analyze_single_build,
    annotate_regressions,
    compare_build_profiles,
    fetch_target_history,
    format_delta,
    format_duration,
    resolve_build_targets,
)
from lib.output import format_markdown_table, print_result

logger = logging.getLogger(__name__)


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


def _render_single_build_markdown(m: BuildProfileMetrics, top_n: int = 10) -> str:
    lines: List[str] = []
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


def handle_inspect(args) -> None:
    """Handles 'emu-dev-cli build-times inspect'."""
    json_mode = getattr(args, "json", False)
    branch = getattr(args, "branch", "git_emu-main-next")
    token = getattr(args, "token", None)
    use_cache = not getattr(args, "no_cache", False)
    top_n = getattr(args, "top", 10)

    try:
        targets = resolve_build_targets(getattr(args, "target", "emulator_linux_x64"))
    except ValueError as e:
        print_result({"status": "error", "error_message": str(e)}, json_mode=json_mode, is_error=True)
        sys.exit(1)

    client = AndroidBuildClient(token=token)
    build_id = getattr(args, "build_id", None)
    if not build_id:
        build_id = client.get_latest_build_id(branch, targets[0])

    results_by_tgt: Dict[str, BuildProfileMetrics] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        fut_map = {
            pool.submit(
                analyze_single_build,
                build_id=str(build_id),
                target=tgt,
                branch=branch,
                client=client,
                token=token,
                use_cache=use_cache,
            ): tgt
            for tgt in targets
        }
        for fut in as_completed(fut_map):
            tgt = fut_map[fut]
            try:
                results_by_tgt[tgt] = fut.result()
            except Exception as e:
                logger.warning("Failed inspecting %s on %s: %s", build_id, tgt, e)

    ordered = [results_by_tgt[t] for t in targets if t in results_by_tgt]

    if len(ordered) == 1:
        m = ordered[0]
        md = _render_single_build_markdown(m, top_n=top_n)
        stages_summary = (
            " [" + ", ".join(f"{s.stage_id}={format_duration(s.bazel_wall_sec)}" for s in m.stages) + "]"
            if m.stages
            else ""
        )
        summary = (
            f"Build {m.build_id} ({m.target}): Total CI={format_duration(m.total_ci_sec)} "
            f"(Queue={format_duration(m.queue_sec)}, Bazel Wall={format_duration(m.bazel_wall_sec)}{stages_summary}, "
            f"Critical Path={format_duration(m.critical_path_sec)})"
        )
    else:
        headers = [
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
        rows = []
        for m in ordered:
            top_cp = m.top_critical_bottleneck
            stage_prefix = f"[{top_cp.stage_id}] " if (top_cp and top_cp.stage_id) else ""
            bottleneck_str = (
                f"`{format_duration(top_cp.duration_sec)}` {stage_prefix}{_truncate(top_cp.name, 44)}"
                if top_cp
                else "N/A"
            )
            rows.append([
                f"`{m.target}`",
                f"**{format_duration(m.total_ci_sec)}**",
                format_duration(m.queue_sec),
                f"**{format_duration(m.bazel_wall_sec)}**",
                _format_stage_summary_inline(m),
                format_duration(m.critical_path_sec),
                f"{m.total_actions:,}" if m.profile_available else "N/A",
                f"`{m.machine_name or 'N/A'}`",
                bottleneck_str,
            ])
        md = f"## Multi-Target Build Profile Summary — Build `{build_id}`\n\n" + format_markdown_table(
            headers, rows
        )
        summary = f"Inspected build {build_id} across {len(ordered)} targets."

    print_result(
        {
            "status": "success",
            "summary": summary,
            "build_id": str(build_id),
            "targets": targets,
            "markdown_table": md,
            "profiles": [m.to_dict() for m in ordered],
        },
        json_mode=json_mode,
    )


def handle_history(args) -> None:
    """Handles 'emu-dev-cli build-times history'."""
    json_mode = getattr(args, "json", False)
    branch = getattr(args, "branch", "git_emu-main-next")
    limit = getattr(args, "limit", 10)
    token = getattr(args, "token", None)
    use_cache = not getattr(args, "no_cache", False)

    try:
        targets = resolve_build_targets(getattr(args, "target", "emulator_mac_aarch64"))
    except ValueError as e:
        print_result({"status": "error", "error_message": str(e)}, json_mode=json_mode, is_error=True)
        sys.exit(1)

    all_sections: List[str] = []
    all_records: Dict[str, List[Dict[str, Any]]] = {}

    for tgt in targets:
        history = fetch_target_history(
            target=tgt,
            branch=branch,
            limit=limit,
            token=token,
            use_cache=use_cache,
        )
        all_records[tgt] = [m.to_dict() for m in history]
        if not history:
            all_sections.append(f"### `{tgt}`\n*No builds found on `{branch}`.*")
            continue

        baseline, regressed = annotate_regressions(history)
        headers = [
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
        rows = []
        for m in history:
            created_short = m.creation_time[:16].replace("T", " ") if m.creation_time else "N/A"
            top_cp = m.top_critical_bottleneck
            stage_prefix = f"[{top_cp.stage_id}] " if (top_cp and top_cp.stage_id) else ""
            cp_str = (
                f"`{format_duration(top_cp.duration_sec)}` {stage_prefix}{_truncate(top_cp.name, 38)}"
                if top_cp
                else "N/A"
            )
            status_str = (
                f"⚠️ **SLOW** ({', '.join(m.regression_reasons)})"
                if m.is_regressed
                else "✅ Healthy"
            )
            rows.append([
                f"`{m.build_id}`",
                created_short,
                f"**{format_duration(m.total_ci_sec)}**",
                format_duration(m.queue_sec),
                f"**{format_duration(m.bazel_wall_sec)}**",
                _format_stage_summary_inline(m),
                format_duration(m.critical_path_sec),
                f"`{m.machine_name or 'N/A'}`",
                cp_str,
                status_str,
            ])

        sec = [f"## Build Time History: `{tgt}` (Last {len(history)} Builds on `{branch}`)"]
        if baseline:
            sec.append(
                f"- **Healthy Baseline Reference**: Build `{baseline.build_id}` "
                f"(Total CI: `{format_duration(baseline.total_ci_sec)}`, "
                f"Bazel Wall: `{format_duration(baseline.bazel_wall_sec)}` [{_format_stage_summary_inline(baseline)}], "
                f"Critical Path: `{format_duration(baseline.critical_path_sec)}`)"
            )
        if regressed:
            sec.append(
                f"- **Regressed / Slow Builds Detected**: **{len(regressed)} / {len(history)}** builds "
                f"({', '.join('`' + r.build_id + '`' for r in regressed)}). "
                f"Run `emu-dev-cli build-times diagnose --target {tgt} --limit {limit}` for full root-cause attribution."
            )
        else:
            sec.append("- **Status**: All recent builds are running within healthy baseline thresholds.")
        sec.append("")
        sec.append(format_markdown_table(headers, rows))
        all_sections.append("\n".join(sec))

    full_md = "\n\n---\n\n".join(all_sections)
    print_result(
        {
            "status": "success",
            "summary": f"Analyzed build time history for {', '.join(targets)} (limit={limit}).",
            "targets": targets,
            "markdown_table": full_md,
            "history": all_records,
        },
        json_mode=json_mode,
    )


def handle_diagnose(args) -> None:
    """Handles 'emu-dev-cli build-times diagnose' (and 'compare')."""
    json_mode = getattr(args, "json", False)
    branch = getattr(args, "branch", "git_emu-main-next")
    limit = getattr(args, "limit", 10)
    token = getattr(args, "token", None)
    use_cache = not getattr(args, "no_cache", False)

    try:
        targets = resolve_build_targets(getattr(args, "target", "emulator_mac_aarch64"))
    except ValueError as e:
        print_result({"status": "error", "error_message": str(e)}, json_mode=json_mode, is_error=True)
        sys.exit(1)

    target = targets[0]
    source_root = get_source_directory("emu-main-next")

    baseline_id = getattr(args, "baseline", None)
    regressed_id = getattr(args, "regressed", None)

    client = AndroidBuildClient(token=token)

    if baseline_id and regressed_id:
        base_m = analyze_single_build(
            build_id=str(baseline_id),
            target=target,
            branch=branch,
            client=client,
            token=token,
            use_cache=use_cache,
        )
        reg_m = analyze_single_build(
            build_id=str(regressed_id),
            target=target,
            branch=branch,
            client=client,
            token=token,
            use_cache=use_cache,
        )
        history = [reg_m, base_m]
    else:
        history = fetch_target_history(
            target=target,
            branch=branch,
            limit=limit,
            token=token,
            use_cache=use_cache,
        )
        if len(history) < 2:
            print_result(
                {
                    "status": "error",
                    "error_message": f"Not enough completed builds found for '{target}' to diagnose.",
                },
                json_mode=json_mode,
                is_error=True,
            )
            sys.exit(1)

        base_m, regressed_list = annotate_regressions(history)
        if baseline_id:
            base_m = next(
                (m for m in history if m.build_id == str(baseline_id)),
                None,
            ) or analyze_single_build(
                build_id=str(baseline_id),
                target=target,
                branch=branch,
                client=client,
                token=token,
                use_cache=use_cache,
            )
        if regressed_id:
            reg_m = next(
                (m for m in history if m.build_id == str(regressed_id)),
                None,
            ) or analyze_single_build(
                build_id=str(regressed_id),
                target=target,
                branch=branch,
                client=client,
                token=token,
                use_cache=use_cache,
            )
        elif regressed_list:
            # Pick the slowest regressed build by Bazel wall time (or Total CI time)
            reg_m = max(regressed_list, key=lambda m: (m.bazel_wall_sec, m.total_ci_sec))
        else:
            # Even if no build crossed the strict regression threshold, compare the slowest vs baseline
            reg_m = max(
                [m for m in history if m.build_id != base_m.build_id],
                key=lambda m: (m.bazel_wall_sec, m.total_ci_sec),
            )

    comp = compare_build_profiles(base_m, reg_m, source_root=source_root)
    deltas = comp["deltas"]

    diag_sec = 1
    lines: List[str] = []
    lines.append(
        f"## Build Time Regression Diagnosis — `{target}` (`{reg_m.build_id}` vs Baseline `{base_m.build_id}`)"
    )
    lines.append("")
    lines.append(f"### {diag_sec}. Executive Root-Cause Attribution")
    diag_sec += 1
    for cause in comp["primary_causes"]:
        lines.append(f"- {cause}")
    lines.append("")

    # 4-Layer Timing Comparison
    lines.append(
        f"### {diag_sec}. Four-Layer Timing Delta (`BUILD_INFO` + `logs/bazel/*.profile.gz`)"
    )
    diag_sec += 1
    t_headers = [
        "Metric / Phase",
        f"Baseline ({base_m.build_id})",
        f"Regressed ({reg_m.build_id})",
        "Delta",
    ]
    t_rows = [
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
        t_rows.append([
            f"   ├─ Stage `{sc['stage_id']}` ({sc['stage_name']})",
            format_duration(sc["baseline_wall_sec"]),
            f"**{format_duration(sc['regressed_wall_sec'])}**",
            format_delta(sc["delta_wall_sec"], sc["baseline_wall_sec"]),
        ])
    t_rows.extend([
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
    lines.append(format_markdown_table(t_headers, t_rows))
    lines.append("")

    # Per-Stage Comparison Table
    if comp.get("stage_comparisons"):
        lines.append(f"### {diag_sec}. Per-Stage Bazel Profile Comparison")
        diag_sec += 1
        sc_headers = [
            "Stage",
            "Artifact",
            f"Baseline Wall ({base_m.build_id})",
            f"Regressed Wall ({reg_m.build_id})",
            "Wall Delta",
            "Crit Path Delta",
            "Actions (Base → Reg)",
            "#1 Bottleneck in Regressed Stage",
        ]
        sc_rows = []
        for sc in comp["stage_comparisons"]:
            sc_rows.append([
                f"**`{sc['stage_id']}`** ({sc['stage_name']})",
                f"`{sc['artifact_name']}`",
                format_duration(sc["baseline_wall_sec"]),
                f"**{format_duration(sc['regressed_wall_sec'])}**",
                f"**{format_delta(sc['delta_wall_sec'], sc['baseline_wall_sec'])}**",
                format_delta(sc["delta_crit_sec"], sc["baseline_crit_sec"]),
                f"{sc['baseline_actions']:,} → {sc['regressed_actions']:,}",
                f"`{_truncate(sc['top_bottleneck'], 55)}`" if sc.get("top_bottleneck") else "N/A",
            ])
        lines.append(format_markdown_table(sc_headers, sc_rows))
        lines.append("")

    # Critical Path Regressions
    if comp["critical_path_regressions"]:
        lines.append(f"### {diag_sec}. Critical Path Action Deltas")
        diag_sec += 1
        cp_headers = [
            "Stage",
            "Critical Path Action",
            f"Baseline ({base_m.build_id})",
            f"Regressed ({reg_m.build_id})",
            "Delta",
        ]
        cp_rows = []
        for item in comp["critical_path_regressions"][:8]:
            cp_rows.append([
                f"`{item.get('stage_id') or 'release'}`",
                f"`{_truncate(item['name'], 75)}`",
                format_duration(item["baseline_sec"]),
                f"**{format_duration(item['regressed_sec'])}**",
                f"**{format_delta(item['delta_sec'], item['baseline_sec'])}**",
            ])
        lines.append(format_markdown_table(cp_headers, cp_rows))
        lines.append("")

    # Mnemonic Deltas
    if comp["mnemonic_regressions"]:
        lines.append(f"### {diag_sec}. Action Mnemonic Regressions")
        diag_sec += 1
        mn_headers = [
            "Mnemonic",
            "Actions (Base → Reg)",
            "Cumulative Time Delta",
            "Max Single Action (Base → Reg)",
            "Slowest Action in Regressed Build",
        ]
        mn_rows = []
        for m in comp["mnemonic_regressions"][:8]:
            mn_rows.append([
                f"`{m['mnemonic']}`",
                f"{m['baseline_count']:,} → {m['regressed_count']:,}",
                f"**{format_delta(m['delta_total_sec'], m['baseline_total_sec'])}**",
                f"{format_duration(m['baseline_max_sec'])} → **{format_duration(m['regressed_max_sec'])}**",
                f"`{_truncate(m['max_action_name'], 60)}`",
            ])
        lines.append(format_markdown_table(mn_headers, mn_rows))
        lines.append("")

    # Git Repository / Commit Diffs
    if comp["changed_repos"]:
        older_id = comp["chronology"]["older_build_id"]
        newer_id = comp["chronology"]["newer_build_id"]
        lines.append(
            f"### {diag_sec}. Git Repository Changes (`{older_id}` → `{newer_id}`)"
        )
        diag_sec += 1
        repo_headers = ["Repository", "Path", "Revision Range", "Commits Landed"]
        repo_rows = []
        for cr in comp["changed_repos"]:
            commits_str = (
                "<br>".join(f"`{_truncate(c, 65)}`" for c in cr["commits"][:5])
                if cr["commits"]
                else f"`{cr['old_sha']}..{cr['new_sha']}`"
            )
            repo_rows.append([
                f"`{cr['repo']}`",
                f"`{cr['path'] or 'N/A'}`",
                f"`{cr['old_sha']}..{cr['new_sha']}`",
                commits_str,
            ])
        lines.append(format_markdown_table(repo_headers, repo_rows))

    md_output = "\n".join(lines)
    print_result(
        {
            "status": "success",
            "summary": comp["primary_causes"][0] if comp["primary_causes"] else "Diagnosis complete.",
            "target": target,
            "markdown_table": md_output,
            "diagnosis": comp,
        },
        json_mode=json_mode,
    )


def _add_common_args(p) -> None:
    p.add_argument(
        "--target",
        "-t",
        type=str,
        default="emulator_linux_x64",
        help=(
            f"Target name, comma-separated list, alias (linux, asan, tsan, mac, windows), or 'all'. "
            f"Supported: {', '.join(SUPPORTED_BUILD_TARGETS)}"
        ),
    )
    p.add_argument(
        "--branch",
        "-b",
        type=str,
        default="git_emu-main-next",
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


def register_parser(subparsers) -> None:
    """Registers 'emu-dev-cli build-times' (and alias 'profile') subcommands."""
    for cmd_name in ("build-times", "profile"):
        parser = subparsers.add_parser(
            cmd_name,
            help="Analyze CI build times and Bazel logs/bazel/*.profile.gz stages across emu-main-next targets",
            description=(
                "Fetch and analyze BUILD_INFO and all logs/bazel/*.profile.gz stages from Android Build (go/ab) "
                "to inspect single builds, view multi-build trends, and diagnose build time regressions."
            ),
        )
        sub = parser.add_subparsers(dest="build_times_cmd", help="Build time analysis subcommands")

        # 1. inspect
        inspect_p = sub.add_parser(
            "inspect",
            help="Inspect how fast a specific build ID (or latest build) ran across all stages and view its critical path",
        )
        _add_common_args(inspect_p)
        inspect_p.add_argument(
            "--build-id",
            "--bid",
            dest="build_id",
            type=str,
            default=None,
            help="Specific Android Build ID (e.g. 16411294). Defaults to latest submitted build if omitted.",
        )
        inspect_p.add_argument(
            "--top",
            type=int,
            default=10,
            help="Number of top critical path items, mnemonics, and slowest actions to show (default: 10)",
        )
        inspect_p.set_defaults(func=handle_inspect)

        # 2. history
        history_p = sub.add_parser(
            "history",
            help="Analyze the last N builds for a target across all Bazel stages (e.g. 'What happened to the last 10 mac arm builds?')",
        )
        _add_common_args(history_p)
        history_p.set_defaults(target="emulator_mac_aarch64")
        history_p.add_argument(
            "--limit",
            "-n",
            type=int,
            default=10,
            help="Number of recent submitted builds to analyze (default: 10)",
        )
        history_p.set_defaults(func=handle_history)

        # 3. diagnose / compare
        for diag_name in ("diagnose", "compare"):
            diag_p = sub.add_parser(
                diag_name,
                help="Find out why builds became slow by comparing regressed builds against baseline across all Bazel stages",
            )
            _add_common_args(diag_p)
            diag_p.set_defaults(target="emulator_mac_aarch64")
            diag_p.add_argument(
                "--limit",
                "-n",
                type=int,
                default=10,
                help="Number of recent builds to scan when auto-detecting regressions (default: 10)",
            )
            diag_p.add_argument(
                "--baseline",
                type=str,
                default=None,
                help="Explicit fast baseline build ID (auto-detected if omitted)",
            )
            diag_p.add_argument(
                "--regressed",
                type=str,
                default=None,
                help="Explicit slow/regressed build ID to diagnose (auto-detected if omitted)",
            )
            diag_p.set_defaults(func=handle_diagnose)

        def _default_handler(args, p=parser):
            if not getattr(args, "build_times_cmd", None):
                p.print_help()
                sys.exit(0)

        parser.set_defaults(func=_default_handler)

