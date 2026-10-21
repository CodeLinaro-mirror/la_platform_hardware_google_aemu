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

"""Deterministic diff-boundary filtering, deduplication, and payload rendering."""

from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from commands.harness.review_targets import COMMIT_MSG_FILE

REUSE_SPECIALIST_NAME: str = "duplication-and-reuse-auditor"
ERROR_SPECIALIST_NAME: str = "error-messages-and-statusor-auditor"
SHORT_ID_DISPLAY_LEN: int = 12

SPECIALIST_FINDING_ITEM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": {"type": "string", "description": "Repository-relative file path or '/COMMIT_MSG'."},
        "line": {"type": "integer", "description": "1-based new-file line number inside the modified diff hunk."},
        "category": {
            "type": "string",
            "description": "Short specialist category slug (e.g. 'commit-msg', 'reuse', 'modularity', 'control-flow', 'constants', 'unicode', 'error-message', 'statusor', 'concurrency').",
        },
        "severity": {"type": "string", "enum": ["blocking", "warning", "nit"]},
        "message": {"type": "string", "description": "Concise 1-3 sentence explanation of the concrete risk or standard violation."},
        "suggested_fix": {"type": "string", "description": "Concrete replacement snippet or actionable fix."},
    },
    "required": ["file", "line", "category", "severity", "message", "suggested_fix"],
}

SPECIALIST_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "specialist": {"type": "string"},
        "checked_count": {"type": "integer", "description": "Number of abstractions or error sites audited by this specialist."},
        "duplicate_count": {"type": "integer", "description": "Number of duplicate abstractions found (for duplication-and-reuse-auditor)."},
        "finding_count": {"type": "integer", "description": "Number of findings reported by this specialist."},
        "findings": {"type": "array", "items": SPECIALIST_FINDING_ITEM_SCHEMA},
    },
    "required": ["specialist", "checked_count", "duplicate_count", "finding_count", "findings"],
}


def normalize_finding_file(file_path: str) -> str:
    """Normalizes a finding file path to repository-relative POSIX form or `/COMMIT_MSG`."""
    cleaned = (file_path or "").strip().replace("\\", "/")
    if cleaned in ("/COMMIT_MSG", "COMMIT_MSG"):
        return COMMIT_MSG_FILE
    for prefix in ("a/", "b/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.lstrip("/")


def _normalize_raw_finding(
    raw_finding: Any,
    spec_name: str,
) -> Optional[Dict[str, Any]]:
    """Validates and normalizes a raw specialist finding dictionary."""
    if not isinstance(raw_finding, dict):
        return None
    norm_file = normalize_finding_file(str(raw_finding.get("file") or ""))
    message = str(raw_finding.get("message") or "").strip()
    if not norm_file or not message:
        return None
    raw_line = raw_finding.get("line")
    try:
        line_num = (
            1 if (norm_file == COMMIT_MSG_FILE and not raw_line) else int(raw_line)
        )
    except (TypeError, ValueError):
        return None
    if line_num < 1:
        return None
    return {
        "file": norm_file,
        "line": line_num,
        "category": str(raw_finding.get("category") or spec_name or "review").strip(),
        "severity": str(raw_finding.get("severity") or "warning").strip().lower(),
        "specialist": spec_name,
        "message": message,
        "suggested_fix": str(raw_finding.get("suggested_fix") or "").strip(),
    }


def _iter_normalized_findings(
    specialist_outputs: List[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """Yields normalized findings across all specialist output dictionaries."""
    for output in specialist_outputs or []:
        if not isinstance(output, dict):
            continue
        spec_name = str(output.get("specialist") or "").strip()
        for raw_finding in output.get("findings") or []:
            normalized = _normalize_raw_finding(raw_finding, spec_name)
            if normalized is not None:
                yield normalized


def _summarize_audit_counts(
    specialist_outputs: List[Dict[str, Any]],
    accepted_findings: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """Builds mandatory `Reuse audit` and `Error-message audit` summary lines."""
    reuse_checked = 0
    reuse_duplicates = 0
    error_sites_checked = 0
    error_findings_count = 0

    for output in specialist_outputs or []:
        if not isinstance(output, dict):
            continue
        spec_name = str(output.get("specialist") or "").strip()
        checked = max(0, int(output.get("checked_count") or 0))
        if spec_name == REUSE_SPECIALIST_NAME:
            reuse_checked += checked
            reuse_duplicates += max(0, int(output.get("duplicate_count") or 0))
        elif spec_name == ERROR_SPECIALIST_NAME:
            error_sites_checked += checked
            error_findings_count += max(0, int(output.get("finding_count") or 0))

    if reuse_duplicates == 0:
        reuse_duplicates = sum(
            1 for f in accepted_findings if f["specialist"] == REUSE_SPECIALIST_NAME
        )
    if error_findings_count == 0:
        error_findings_count = sum(
            1 for f in accepted_findings if f["specialist"] == ERROR_SPECIALIST_NAME
        )

    return (
        f"Reuse audit: {reuse_checked} abstractions checked, {reuse_duplicates} duplicates",
        f"Error-message audit: {error_sites_checked} sites checked, {error_findings_count} findings",
    )


def _render_metrics_markdown(metrics: Optional[Dict[str, Any]]) -> List[str]:
    """Formats per-specialist duration and count metrics as Markdown lines."""
    if not metrics:
        return []
    scope = metrics.get("review_scope", "full")
    round_num = metrics.get("round_number", 1)
    prev_id = metrics.get("previous_target_id") or ""
    suffix = f", interdiff from `{prev_id[:SHORT_ID_DISPLAY_LEN]}`)" if prev_id else ")"
    scope_desc = f"Round {round_num} ({scope}{suffix}"
    lines = [
        "## Swarm Telemetry & Agent Timing",
        "",
        f"- **Pass Mode**: {scope_desc}",
        f"- **Wall-Clock Duration**: `{metrics.get('wall_clock_sec', 0.0):.2f}s` "
        f"(cumulative agent time `{metrics.get('sum_agent_sec', 0.0):.2f}s`, "
        f"`{metrics.get('parallel_speedup', 1.0):.2f}x` speedup, "
        f"bottleneck `{metrics.get('bottleneck_specialist', 'none')}` at `{metrics.get('bottleneck_duration_sec', 0.0):.2f}s`)",
        "",
    ]
    agents = metrics.get("agents") or []
    if not agents:
        return lines
    lines.extend([
        "| Specialist | Model | Duration (s) | Checked | Findings |",
        "| :--- | :--- | :---: | :---: | :---: |",
    ])
    for item in agents:
        lines.append(
            f"| `{item.get('specialist', '')}` | `{item.get('model', '')}` | "
            f"{float(item.get('duration_sec', 0.0)):.2f} | "
            f"{int(item.get('checked_count', 0))} | {int(item.get('finding_count', 0))} |"
        )
    lines.append("")
    return lines


def filter_and_aggregate_findings(
    specialist_outputs: List[Dict[str, Any]],
    changed_lines_by_file: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically filters specialist findings to modified diff lines and deduplicates."""
    allowed_lines = {
        normalize_finding_file(path): {int(ln) for ln in lines}
        for path, lines in (changed_lines_by_file or {}).items()
    }
    accepted_findings: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, int, str]] = set()
    dropped_out_of_bounds, dropped_duplicates = 0, 0

    for candidate in _iter_normalized_findings(specialist_outputs):
        norm_file, line_num = candidate["file"], candidate["line"]
        if norm_file != COMMIT_MSG_FILE and line_num not in allowed_lines.get(norm_file, set()):
            dropped_out_of_bounds += 1
            continue
        dedupe_key = (norm_file, line_num, candidate["category"].lower())
        if dedupe_key in seen_keys:
            dropped_duplicates += 1
            continue
        seen_keys.add(dedupe_key)
        accepted_findings.append(candidate)

    accepted_findings.sort(key=lambda item: (item["file"], item["line"], item["category"]))
    reuse_audit_line, error_audit_line = _summarize_audit_counts(specialist_outputs, accepted_findings)
    markdown_report = render_review_swarm_markdown(
        accepted_findings, reuse_audit_line, error_audit_line,
        dropped_out_of_bounds=dropped_out_of_bounds, dropped_duplicates=dropped_duplicates, metrics=metrics,
    )
    return {
        "findings": accepted_findings, "total_findings": len(accepted_findings),
        "dropped_out_of_bounds": dropped_out_of_bounds, "dropped_duplicates": dropped_duplicates,
        "reuse_audit_line": reuse_audit_line, "error_audit_line": error_audit_line,
        "markdown_report": markdown_report,
        "gerrit_review_payload": build_gerrit_review_payload(accepted_findings, reuse_audit_line, error_audit_line),
    }


def render_review_swarm_markdown(
    findings: List[Dict[str, Any]],
    reuse_audit_line: str,
    error_audit_line: str,
    dropped_out_of_bounds: int = 0,
    dropped_duplicates: int = 0,
    metrics: Optional[Dict[str, Any]] = None,
) -> str:
    """Renders a high-signal Markdown code review report with mandatory audit summary lines."""
    lines: List[str] = [
        "# 8-Specialist Parallel Code-Review Swarm Report",
        "",
        f"- **{reuse_audit_line}**",
        f"- **{error_audit_line}**",
        f"- **Total Verified Diff-Bounded Findings**: {len(findings)} "
        f"(dropped {dropped_out_of_bounds} out-of-diff, {dropped_duplicates} duplicate)",
        "",
    ]
    lines.extend(_render_metrics_markdown(metrics))
    if not findings:
        lines.append("No blocking or high-signal findings detected in the modified lines.")
        return "\n".join(lines) + "\n"

    lines.append("## Inline Findings")
    lines.append("")
    for idx, item in enumerate(findings, start=1):
        lines.append(
            f"{idx}. **`{item['file']}:{item['line']}`** "
            f"[`{item['category']}` · `{item['severity']}`]: {item['message']}"
        )
        if item.get("suggested_fix"):
            lines.append(f"   - **Suggested fix**: `{item['suggested_fix']}`")
    lines.append("")
    return "\n".join(lines)


def build_gerrit_review_payload(
    findings: List[Dict[str, Any]],
    reuse_audit_line: str,
    error_audit_line: str,
) -> Dict[str, Any]:
    """Builds a Gerrit Revision ReviewInput JSON payload with `notify: OWNER` and `unresolved: true`."""
    comments_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for item in findings:
        body = f"[{item['category']}] {item['message']}"
        if item.get("suggested_fix"):
            body += f"\n\nSuggested fix:\n{item['suggested_fix']}"
        comments_by_file.setdefault(item["file"], []).append({
            "line": int(item["line"]),
            "message": body,
            "unresolved": True,
        })

    top_msg_lines = [
        f"8-Specialist Parallel Code-Review Swarm completed ({len(findings)} finding(s)).",
        "",
        reuse_audit_line,
        error_audit_line,
    ]
    payload: Dict[str, Any] = {
        "message": "\n".join(top_msg_lines),
        "notify": "OWNER",
        "comments": comments_by_file,
    }
    if findings:
        payload["labels"] = {"Code-Review": 0}
    return payload
