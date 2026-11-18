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

"""Markdown scorecard rendering, A/B stability comparison, and longitudinal history."""

import json
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from lib.output import format_markdown_table
from lib.stability.catalog import StabilityVerdict

logger = logging.getLogger(__name__)

HISTORY_FILENAME: str = "stability_history.jsonl"


def expand_safe_path(raw_path: str) -> str:
    """Expands `~` safely across Windows/POSIX sandboxes and returns an absolute path."""
    text = str(raw_path or "").strip()
    if text.startswith("~"):
        home = (
            os.environ.get("USERPROFILE")
            or os.environ.get("HOME")
            or tempfile.gettempdir()
        )
        suffix = text[1:].lstrip("/\\")
        text = os.path.join(home, suffix) if suffix else home
    return os.path.abspath(text)


def format_file_uri(raw_path: str) -> str:
    """Converts a Windows or POSIX `raw_path` to an absolute, terminal-clickable `file:///...` URI."""
    text = str(raw_path or "").strip()
    if text.startswith("file://"):
        return text.replace("(", "%28").replace(")", "%29")
    if not text.startswith("~"):
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            return (
                PureWindowsPath(text)
                .as_uri()
                .replace("(", "%28")
                .replace(")", "%29")
            )
        if text.startswith("/") and not text.startswith("//"):
            return (
                PurePosixPath(text)
                .as_uri()
                .replace("(", "%28")
                .replace(")", "%29")
            )
    abs_path = expand_safe_path(text)
    return Path(abs_path).as_uri().replace("(", "%28").replace(")", "%29")


def supports_osc8_hyperlinks(stream: Any = None) -> bool:
    """Returns True if `stream` (default `sys.stdout`) is an interactive terminal supporting ANSI OSC 8 hyperlinks."""
    if os.environ.get("NO_COLOR") or os.environ.get("EMU_DEV_CLI_NO_OSC8"):
        return False
    force = os.environ.get("FORCE_HYPERLINK") or os.environ.get(
        "EMU_DEV_CLI_OSC8"
    )
    if force and str(force).strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    target = sys.stdout if stream is None else stream
    return bool(getattr(target, "isatty", lambda: False)())


def format_osc8_link(uri: str, label: Optional[str] = None) -> str:
    """Formats `uri` as an ECMA-48 / ANSI OSC 8 terminal hyperlink (`\\x1b]8;;URI\\x1b\\\\LABEL\\x1b]8;;\\x1b\\\\`)."""
    display = label if label is not None else uri
    return f"\x1b]8;;{uri}\x1b\\{display}\x1b]8;;\x1b\\"


def format_clickable_file_link(
    raw_path: str,
    label: Optional[str] = None,
    *,
    osc8: bool = False,
) -> str:
    """Formats `raw_path` as either an ANSI OSC 8 terminal hyperlink (`osc8=True`) or Markdown `[file:///...](file:///...)` link."""
    uri = format_file_uri(raw_path)
    linked = format_osc8_link(uri, uri) if osc8 else f"[{uri}]({uri})"
    if label:
        return f"{label}: {linked}"
    return linked


def default_stability_history_path() -> str:
    """Returns `~/.cache/emu-dev-cli/stability_history.jsonl` (or OS cache equivalent)."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if not cache_home:
        if os.name == "nt":
            local_app = (
                os.environ.get("LOCALAPPDATA")
                or (
                    os.path.join(os.environ["USERPROFILE"], "AppData", "Local")
                    if os.environ.get("USERPROFILE")
                    else None
                )
                or tempfile.gettempdir()
            )
            cache_home = os.path.join(local_app, "emu-dev-cli", "cache")
        else:
            home_dir = os.environ.get("HOME") or tempfile.gettempdir()
            cache_home = os.path.join(home_dir, ".cache", "emu-dev-cli")
    return os.path.join(cache_home, HISTORY_FILENAME)


def append_stability_history(
    report: Dict[str, Any],
    history_path: Optional[str] = None,
) -> None:
    """Appends a compact summary record of `report` to `stability_history.jsonl`."""
    path = history_path or default_stability_history_path()
    eval_block = report.get("evaluation") or {}
    entry = {
        "timestamp": report.get("timestamp", ""),
        "label": report.get("label", "unknown"),
        "build_id": (report.get("target") or {}).get("build_id"),
        "engine": (report.get("target") or {}).get("engine"),
        "tier": report.get("tier", "smoke"),
        "scenarios": report.get("scenarios", []),
        "verdict": eval_block.get("verdict", StabilityVerdict.FAIL.value),
        "cycles_completed": eval_block.get("cycles_completed", 0),
        "elapsed_sec": eval_block.get("elapsed_sec", 0.0),
        "rss_slope_mib_per_min": eval_block.get("rss_slope_mib_per_min", 0.0),
        "peak_rss_mib": eval_block.get("peak_rss_mib", 0.0),
        "handle_net_growth": eval_block.get("handle_net_growth", 0),
        "finding_count": len(eval_block.get("findings") or []),
    }
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.debug("Failed appending stability history to %s: %s", path, exc)


def load_stability_history(
    limit: int = 20,
    tier_filter: Optional[str] = None,
    history_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Loads up to `limit` most-recent stability run summaries."""
    path = history_path or default_stability_history_path()
    if not os.path.isfile(path):
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        if tier_filter and obj.get("tier") != tier_filter:
                            continue
                        records.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.debug("Failed reading stability history %s: %s", path, exc)
        return []

    return list(reversed(records[-max(1, limit) :]))


def format_stability_history_markdown(
    entries: Sequence[Dict[str, Any]],
    *,
    osc8: bool = False,
) -> str:
    """Renders longitudinal stability history entries as a Markdown table."""
    if not entries:
        return "No stability history recorded yet. Run `emu-dev-cli stability run` to record a baseline."

    headers = [
        "Timestamp (UTC)",
        "Target",
        "Tier",
        "Verdict",
        "Cycles",
        "Duration (s)",
        "RSS Slope (MiB/min)",
        "Peak RSS (MiB)",
        "Handle Δ",
    ]
    rows = []
    for item in entries:
        ts = str(item.get("timestamp", ""))[:19].replace("T", " ")
        rows.append([
            ts,
            f"`{item.get('label', 'unknown')}`",
            f"`{item.get('tier', 'smoke')}`",
            f"**{item.get('verdict', 'UNKNOWN')}**",
            str(item.get("cycles_completed", 0)),
            f"{float(item.get('elapsed_sec', 0.0)):.1f}",
            f"{float(item.get('rss_slope_mib_per_min', 0.0)):+.2f}",
            f"{float(item.get('peak_rss_mib', 0.0)):.1f}",
            f"{int(item.get('handle_net_growth', 0)):+d}",
        ])
    hist_file = default_stability_history_path()
    footer = (
        f"\n\nHistory log: {format_clickable_file_link(hist_file, osc8=osc8)}"
        if os.path.isfile(hist_file)
        else ""
    )
    return (
        "## Longitudinal Emulator Stability History\n\n"
        + format_markdown_table(headers, rows)
        + footer
    )


def _fmt_opt_float(val: Any, suffix: str = "", precision: int = 1) -> str:
    if val is None:
        return "—"
    return f"{float(val):.{precision}f}{suffix}"


def render_stability_report_markdown(
    report: Dict[str, Any],
    *,
    osc8: bool = False,
) -> str:
    """Renders a single-target stability qualification report in GitHub-style Markdown."""
    eval_data = report.get("evaluation") or {}
    verdict = eval_data.get("verdict", StabilityVerdict.FAIL.value)
    label = report.get("label", "emulator")
    tier = report.get("tier", "smoke")
    scenarios = ", ".join(f"`{s}`" for s in (report.get("scenarios") or []))
    runtime = report.get("runtime") or {}

    lines: List[str] = [
        f"# Emulator Stability Qualification Scorecard — `{label}`",
        "",
        f"- **Overall Verdict**: **{verdict}**"
        + (" *(strict mode)*" if eval_data.get("strict") else ""),
        f"- **Tier & Scenarios**: `{tier}` ({scenarios})",
        f"- **Host & Runtime**: `{report.get('host_platform', 'unknown')}` | GPU `{runtime.get('gpu_mode', 'auto')}` | Audio `{'on' if runtime.get('enable_audio', True) else 'off'}` | AVD `{report.get('avd', 'live')}`",
        f"- **Completed Cycles**: `{eval_data.get('cycles_completed', 0)}` over `{float(eval_data.get('elapsed_sec', 0.0)):.1f}s`",
        "",
        "## 1. Health & SLA Summary",
        "",
    ]

    summary_headers = ["Health Dimension", "Observed Value", "Status"]
    lat_base = eval_data.get("baseline_latency_ms")
    lat_final = eval_data.get("final_latency_ms")
    lat_ratio = eval_data.get("latency_drift_ratio")
    lat_text = (
        f"{lat_base:.1f} ms -> {lat_final:.1f} ms ({lat_ratio:.2f}x)"
        if (lat_base is not None and lat_final is not None and lat_ratio is not None)
        else (_fmt_opt_float(lat_final, " ms") if lat_final is not None else "—")
    )

    finding_cats = {
        f.get("category"): f.get("severity")
        for f in (eval_data.get("findings") or [])
        if isinstance(f, dict)
    }

    def _cat_badge(*cats: str) -> str:
        severities = [finding_cats.get(c) for c in cats if finding_cats.get(c)]
        if StabilityVerdict.FAIL.value in severities:
            return "FAIL"
        if StabilityVerdict.DEGRADED.value in severities:
            return "DEGRADED"
        return "PASS"

    summary_rows = [
        [
            "Host Process & Crashpad",
            f"Restarts=0, Minidumps={max((s.get('crashpad_minidumps', 0) for s in (report.get('samples') or [])), default=0)}",
            _cat_badge("host_process", "crashpad"),
        ],
        [
            "ADB Connectivity",
            f"Flaps={eval_data.get('total_adb_flaps', 0)}",
            _cat_badge("adb_connectivity"),
        ],
        [
            "Guest System (`system_server` & DropBox)",
            f"Soft-Restarts={eval_data.get('system_server_restarts', 0)}, ANRs={eval_data.get('total_anrs', 0)}, Tombstones={eval_data.get('total_tombstones', 0)}",
            _cat_badge("guest_system", "scenario_execution"),
        ],
        [
            "Host RSS Memory Slope",
            f"{float(eval_data.get('rss_slope_mib_per_min', 0.0)):+.2f} MiB/min (Δ {float(eval_data.get('rss_net_growth_mib', 0.0)):+.1f} MiB, Peak {float(eval_data.get('peak_rss_mib', 0.0)):.1f} MiB)",
            _cat_badge("rss_memory"),
        ],
        [
            "OS Handles / File Descriptors",
            f"Δ {int(eval_data.get('handle_net_growth', 0)):+d} (Peak {int(eval_data.get('peak_handles_or_fds', 0))})",
            _cat_badge("os_handles"),
        ],
        [
            "UX Input P95 Latency Drift",
            lat_text,
            _cat_badge("ux_latency"),
        ],
        [
            "Idle Host CPU Recovery",
            _fmt_opt_float(eval_data.get("max_idle_cpu_pct"), "%"),
            _cat_badge("idle_cpu", "display_freshness"),
        ],
    ]
    lines.append(format_markdown_table(summary_headers, summary_rows))
    lines.append("")

    samples = report.get("samples") or []
    if samples:
        lines.append("## 2. Per-Cycle Telemetry Time-Series")
        lines.append("")
        cyc_headers = [
            "Cycle",
            "Elapsed (s)",
            "Scenario",
            "RSS (MiB)",
            "Handles/FDs",
            "Host CPU%",
            "Idle CPU%",
            "ADB Ping (ms)",
            "Touch P95 (ms)",
            "Frame OK",
        ]
        cyc_rows = []
        for s in samples:
            frame_val = s.get("frame_non_black")
            frame_str = "yes" if frame_val is True else ("NO" if frame_val is False else "—")
            cyc_rows.append([
                str(s.get("cycle_index", 0)),
                f"{float(s.get('elapsed_sec', 0.0)):.1f}",
                f"`{s.get('scenario', '')}`",
                f"{float(s.get('rss_mib', 0.0)):.1f}",
                str(s.get("open_handles_or_fds", 0)),
                f"{float(s.get('host_cpu_pct', 0.0)):.1f}%",
                _fmt_opt_float(s.get("idle_cpu_pct"), "%"),
                f"{float(s.get('adb_ping_ms', 0.0)):.1f}",
                _fmt_opt_float(s.get("touch_latency_p95_ms"), ""),
                frame_str,
            ])
        lines.append(format_markdown_table(cyc_headers, cyc_rows))
        lines.append("")

    findings = eval_data.get("findings") or []
    lines.append("## 3. Findings & Actionable Diagnostics")
    lines.append("")
    if not findings:
        lines.append("- Zero stability violations or UX degradations detected across all cycles.")
    else:
        for item in findings:
            lines.append(
                f"- **[{item.get('severity', 'WARN')}] `{item.get('category', 'general')}`**: {item.get('summary', '')}"
            )
    lines.append("")

    forensics = report.get("forensics")
    if isinstance(forensics, dict) and forensics.get("bundle_dir"):
        lines.append("## 4. Forensic Bundle")
        lines.append("")
        lines.append(
            f"- **Bundle Directory**: {format_clickable_file_link(forensics['bundle_dir'], osc8=osc8)}"
        )
        for k, v in sorted((forensics.get("artifacts") or {}).items()):
            lines.append(f"  - `{k}`: {format_clickable_file_link(v, osc8=osc8)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def compare_stability_reports(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    baseline_label: Optional[str] = None,
    candidate_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds a structured A/B comparison payload between two stability reports."""
    b_lbl = baseline_label or baseline.get("label") or "baseline"
    c_lbl = candidate_label or candidate.get("label") or "candidate"
    b_eval = baseline.get("evaluation") or {}
    c_eval = candidate.get("evaluation") or {}

    verdict_rank = {
        StabilityVerdict.PASS.value: 0,
        StabilityVerdict.DEGRADED.value: 1,
        StabilityVerdict.FAIL.value: 2,
    }
    b_verdict = b_eval.get("verdict", StabilityVerdict.FAIL.value)
    c_verdict = c_eval.get("verdict", StabilityVerdict.FAIL.value)

    if c_verdict == StabilityVerdict.FAIL.value:
        overall = StabilityVerdict.FAIL.value
    elif verdict_rank.get(c_verdict, 2) > verdict_rank.get(b_verdict, 0):
        overall = StabilityVerdict.DEGRADED.value
    else:
        overall = c_verdict

    metrics = [
        ("verdict", "Verdict", b_verdict, c_verdict),
        (
            "cycles_completed",
            "Cycles Completed",
            b_eval.get("cycles_completed", 0),
            c_eval.get("cycles_completed", 0),
        ),
        (
            "rss_slope_mib_per_min",
            "Host RSS Slope (MiB/min)",
            b_eval.get("rss_slope_mib_per_min", 0.0),
            c_eval.get("rss_slope_mib_per_min", 0.0),
        ),
        (
            "peak_rss_mib",
            "Peak Host RSS (MiB)",
            b_eval.get("peak_rss_mib", 0.0),
            c_eval.get("peak_rss_mib", 0.0),
        ),
        (
            "handle_net_growth",
            "OS Handle/FD Net Growth",
            b_eval.get("handle_net_growth", 0),
            c_eval.get("handle_net_growth", 0),
        ),
        (
            "latency_drift_ratio",
            "Input P95 Latency Drift Ratio",
            b_eval.get("latency_drift_ratio"),
            c_eval.get("latency_drift_ratio"),
        ),
        (
            "max_idle_cpu_pct",
            "Max Idle Host CPU (%)",
            b_eval.get("max_idle_cpu_pct"),
            c_eval.get("max_idle_cpu_pct"),
        ),
        (
            "total_adb_flaps",
            "ADB Flaps",
            b_eval.get("total_adb_flaps", 0),
            c_eval.get("total_adb_flaps", 0),
        ),
        (
            "total_anrs",
            "Guest ANRs",
            b_eval.get("total_anrs", 0),
            c_eval.get("total_anrs", 0),
        ),
    ]

    rows = []
    for key, title, b_val, c_val in metrics:
        delta: Any = None
        if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
            delta = round(float(c_val) - float(b_val), 3)
        rows.append({
            "metric": key,
            "title": title,
            "baseline": b_val,
            "candidate": c_val,
            "delta": delta,
        })

    return {
        "schema_version": 1,
        "baseline_label": b_lbl,
        "candidate_label": c_lbl,
        "overall_verdict": overall,
        "comparisons": rows,
        "baseline": baseline,
        "candidate": candidate,
    }


def render_stability_comparison_markdown(
    comp: Dict[str, Any],
    *,
    osc8: bool = False,
) -> str:
    """Renders an A/B stability comparison dict into a Markdown scorecard."""
    b_lbl = comp.get("baseline_label", "Baseline")
    c_lbl = comp.get("candidate_label", "Candidate")
    overall = comp.get("overall_verdict", "UNKNOWN")

    headers = ["Metric", f"Baseline (`{b_lbl}`)", f"Candidate (`{c_lbl}`)", "Delta"]
    table_rows = []
    for row in comp.get("comparisons") or []:
        b_v = row.get("baseline")
        c_v = row.get("candidate")
        d_v = row.get("delta")
        b_str = "—" if b_v is None else str(b_v)
        c_str = "—" if c_v is None else str(c_v)
        d_str = "—" if d_v is None else f"{float(d_v):+.2f}"
        table_rows.append([row.get("title", row.get("metric", "")), b_str, c_str, d_str])

    lines = [
        f"# A/B Emulator Stability Comparison (`{b_lbl}` vs `{c_lbl}`)",
        "",
        f"- **Candidate Qualification Verdict**: **{overall}**",
        "",
        format_markdown_table(headers, table_rows),
        "",
    ]

    cand_findings = ((comp.get("candidate") or {}).get("evaluation") or {}).get(
        "findings"
    ) or []
    if cand_findings:
        lines.append("## Candidate Stability Findings")
        lines.append("")
        for f in cand_findings:
            lines.append(
                f"- **[{f.get('severity', 'WARN')}] `{f.get('category', '')}`**: {f.get('summary', '')}"
            )
        lines.append("")

    base_forensics = (comp.get("baseline") or {}).get("forensics")
    cand_forensics = (comp.get("candidate") or {}).get("forensics")
    if (isinstance(base_forensics, dict) and base_forensics.get("bundle_dir")) or (
        isinstance(cand_forensics, dict) and cand_forensics.get("bundle_dir")
    ):
        lines.append("## Forensic Bundles")
        lines.append("")
        for side_lbl, f_obj in ((b_lbl, base_forensics), (c_lbl, cand_forensics)):
            if not isinstance(f_obj, dict) or not f_obj.get("bundle_dir"):
                continue
            lines.append(
                f"- **`{side_lbl}`**: {format_clickable_file_link(f_obj['bundle_dir'], osc8=osc8)}"
            )
            for k, v in sorted((f_obj.get("artifacts") or {}).items()):
                lines.append(f"  - `{k}`: {format_clickable_file_link(v, osc8=osc8)}")
        lines.append("")
    return "\n".join(lines)
