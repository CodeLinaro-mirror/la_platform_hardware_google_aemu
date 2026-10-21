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

"""Per-CL iteration feedback history and per-agent timing metrics for Review Swarm."""

from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from commands.harness.review_aggregation import (
    SHORT_ID_DISPLAY_LEN,
    normalize_finding_file,
)
from commands.harness.review_targets import (
    COMMIT_MSG_FILE,
    atomic_write_text,
    parse_unified_diff_hunk_entries,
    sanitize_target_id,
    serialize_changed_lines,
)
from lib.gerrit_api import COMMIT_CHANGE_ID_RE

HISTORY_SUBDIR: str = "history"
METRICS_JSONL_FILENAME: str = "metrics.jsonl"
METRICS_REPORT_FILENAME: str = "review_swarm_metrics.json"
INCREMENTAL_PASS_MODEL: str = "flash"
MAX_STORED_ITERATIONS: int = 10
RECENT_METRICS_DEFAULT_LIMIT: int = 5
DURATION_ROUND_DIGITS: int = 3
SPEEDUP_ROUND_DIGITS: int = 2
JSONL_FILE_MODE: int = 0o644


def extract_change_key(target_manifest: Dict[str, Any]) -> str:
    """Derives a stable cross-patchset cache key from `Change-Id` or CL number."""
    commit_msg = str(target_manifest.get("commit_message") or "")
    cid_match = COMMIT_CHANGE_ID_RE.search(commit_msg)
    if cid_match:
        return cid_match.group(1)
    target_type = str(target_manifest.get("target_type") or "commit")
    target_id = str(target_manifest.get("target_id") or "HEAD")
    return f"{sanitize_target_id(target_type)}_{sanitize_target_id(target_id)}"


def load_iteration_history(change_key: str, cache_root: Path) -> Dict[str, Any]:
    """Loads persisted pass-by-pass specialist feedback for `change_key`."""
    history_path = cache_root / HISTORY_SUBDIR / f"{change_key}.json"
    if not history_path.is_file():
        return {"change_key": change_key, "iterations": []}
    try:
        parsed = json.loads(history_path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return {"change_key": change_key, "iterations": []}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("iterations"), list):
        return {"change_key": change_key, "iterations": []}
    return parsed


def _diff_single_file_entries(
    path: str,
    prev_list: List[Tuple[int, str]],
    curr_list: List[Tuple[int, str]],
) -> Tuple[Set[int], str]:
    """Computes modified Commit-N line numbers and an interdiff snippet for one file."""
    matcher = difflib.SequenceMatcher(
        None,
        [op for _, op in prev_list],
        [op for _, op in curr_list],
        autojunk=False,
    )
    touched_lines: Set[int] = set()
    snippet_lines: List[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        snippet_lines.extend(f"- [prev] {prev_list[i][1]}" for i in range(i1, i2))
        snippet_lines.extend(
            f"+ [L{curr_list[j][0]}] {curr_list[j][1]}" for j in range(j1, j2)
        )
        touched_lines.update(curr_list[j][0] for j in range(j1, j2))
        if tag == "delete" and curr_list:
            anchor_idx = min(j1, len(curr_list) - 1)
            touched_lines.add(curr_list[anchor_idx][0])
    if not snippet_lines:
        return touched_lines, ""
    return touched_lines, f"--- prev/{path}\n+++ curr/{path}\n" + "\n".join(snippet_lines)


def _resolve_shifted_finding_line(
    file_path: str,
    old_line: int,
    prev_entries: Dict[str, List[Tuple[int, str]]],
    curr_entries: Dict[str, List[Tuple[int, str]]],
) -> Optional[int]:
    """Maps a prior finding line number forward across line shifts in `curr_entries`."""
    curr_map = dict(curr_entries.get(file_path) or [])
    prev_raw = dict(prev_entries.get(file_path) or {}).get(old_line)
    if not prev_raw:
        return old_line if old_line in curr_map else None
    if curr_map.get(old_line) == prev_raw:
        return old_line
    for curr_ln, curr_raw in curr_entries.get(file_path) or []:
        if curr_raw == prev_raw:
            return curr_ln
    return None


def _collect_prior_unresolved_lines(
    prev_entry: Dict[str, Any],
    prev_entries: Dict[str, List[Tuple[int, str]]],
    curr_entries: Dict[str, List[Tuple[int, str]]],
) -> Dict[str, Set[int]]:
    """Collects still-present lines that had findings in the previous pass."""
    mapped: Dict[str, Set[int]] = {}
    feedback_map = prev_entry.get("specialist_feedback") or {}
    if not isinstance(feedback_map, dict):
        return mapped
    flat_items = [
        item
        for findings in feedback_map.values()
        if isinstance(findings, list)
        for item in findings
        if isinstance(item, dict)
    ]
    for item in flat_items:
        norm_path = normalize_finding_file(str(item.get("file") or ""))
        if not norm_path or norm_path == COMMIT_MSG_FILE:
            continue
        resolved_ln = _resolve_shifted_finding_line(
            norm_path, int(item.get("line") or 0), prev_entries, curr_entries
        )
        if resolved_ln is not None:
            mapped.setdefault(norm_path, set()).add(resolved_ln)
    return mapped


def _select_prior_baseline(
    iterations: List[Dict[str, Any]],
    current_key_tuple: Tuple[str, str],
) -> Optional[Dict[str, Any]]:
    """Selects the most recent iteration preceding `current_key_tuple`."""
    for entry in reversed(iterations):
        entry_tuple = (str(entry.get("target_id") or ""), str(entry.get("current_revision") or ""))
        if entry_tuple != current_key_tuple:
            return entry
    return None


def _compute_interdiff_summary(
    prev_entry: Dict[str, Any],
    full_diff: str,
) -> Tuple[Dict[str, List[int]], str]:
    """Computes effective allowlist lines and unified interdiff text from `prev_entry`."""
    prev_entries = parse_unified_diff_hunk_entries(str(prev_entry.get("full_diff_text") or ""))
    curr_entries = parse_unified_diff_hunk_entries(full_diff)
    effective_lines = _collect_prior_unresolved_lines(prev_entry, prev_entries, curr_entries)
    interdiff_blocks: List[str] = []
    for path in sorted(set(prev_entries) | set(curr_entries)):
        touched, snippet = _diff_single_file_entries(
            path, prev_entries.get(path) or [], curr_entries.get(path) or []
        )
        if touched:
            effective_lines.setdefault(path, set()).update(touched)
        if snippet:
            interdiff_blocks.append(snippet)
    prev_short = str(prev_entry.get("target_id") or "")[:SHORT_ID_DISPLAY_LEN]
    interdiff_text = (
        "\n\n".join(interdiff_blocks)
        if interdiff_blocks
        else f"(No code diff lines changed relative to previous pass {prev_short}.)"
    )
    return serialize_changed_lines(effective_lines), interdiff_text


def apply_incremental_history(
    target_manifest: Dict[str, Any],
    cache_root: Path,
    force_full: bool = False,
) -> Dict[str, Any]:
    """Enriches `target_manifest` with round metadata, interdiff, and prior feedback."""
    enriched = dict(target_manifest)
    change_key = extract_change_key(enriched)
    full_diff = str(enriched.get("diff_text") or "")
    enriched["change_key"] = change_key
    enriched["full_diff_text"] = full_diff

    history = load_iteration_history(change_key, cache_root)
    curr_tuple = (str(enriched.get("target_id") or ""), str(enriched.get("current_revision") or ""))
    prev_entry = None if force_full else _select_prior_baseline(history["iterations"], curr_tuple)
    if prev_entry is None:
        enriched["round_number"] = 1
        enriched["review_scope"] = "full"
        enriched["prior_specialist_feedback"] = {}
        return enriched

    changed_map, interdiff_text = _compute_interdiff_summary(prev_entry, full_diff)
    enriched["round_number"] = int(prev_entry.get("round_number") or 1) + 1
    enriched["review_scope"] = "incremental"
    enriched["previous_target_id"] = str(prev_entry.get("target_id") or "")
    enriched["commit_msg_changed"] = str(prev_entry.get("commit_message") or "") != str(
        enriched.get("commit_message") or ""
    )
    enriched["prior_specialist_feedback"] = prev_entry.get("specialist_feedback") or {}
    enriched["changed_lines_by_file"] = changed_map
    enriched["diff_text"] = interdiff_text
    return enriched


def build_wave_metrics(
    manifest: Dict[str, Any],
    raw_results: List[Dict[str, Any]],
    wall_clock_sec: float,
) -> Dict[str, Any]:
    """Builds structured per-agent timing and count metrics for a completed swarm run."""
    agent_metrics: List[Dict[str, Any]] = []
    for item in raw_results:
        findings = item.get("findings") or []
        blocking_cnt = sum(
            1 for f in findings if isinstance(f, dict) and str(f.get("severity") or "") == "blocking"
        )
        raw_cnt = item.get("finding_count")
        agent_metrics.append({
            "specialist": str(item.get("specialist") or ""),
            "role": str(item.get("role") or ""),
            "model": str(item.get("model") or ""),
            "duration_sec": round(float(item.get("duration_sec") or 0.0), DURATION_ROUND_DIGITS),
            "checked_count": int(item.get("checked_count") or 0),
            "duplicate_count": int(item.get("duplicate_count") or 0),
            "finding_count": int(raw_cnt if raw_cnt is not None else len(findings)),
            "blocking_count": blocking_cnt,
        })

    sum_sec = round(sum(a["duration_sec"] for a in agent_metrics), DURATION_ROUND_DIGITS)
    wall_rounded = round(max(0.0, float(wall_clock_sec)), DURATION_ROUND_DIGITS)
    slowest = max(agent_metrics, key=lambda a: a["duration_sec"], default=None)
    speedup = round(sum_sec / wall_rounded, SPEEDUP_ROUND_DIGITS) if wall_rounded > 0 else 1.0
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "change_key": str(manifest.get("change_key") or extract_change_key(manifest)),
        "target_type": str(manifest.get("target_type") or ""),
        "target_id": str(manifest.get("target_id") or ""),
        "round_number": int(manifest.get("round_number") or 1),
        "review_scope": str(manifest.get("review_scope") or "full"),
        "previous_target_id": str(manifest.get("previous_target_id") or ""),
        "wall_clock_sec": wall_rounded,
        "sum_agent_sec": sum_sec,
        "parallel_speedup": speedup,
        "bottleneck_specialist": slowest["specialist"] if slowest else "none",
        "bottleneck_duration_sec": slowest["duration_sec"] if slowest else 0.0,
        "agents": agent_metrics,
    }


def _append_metrics_jsonl(jsonl_path: Path, line_text: str) -> None:
    """Appends one UTF-8 JSON line to `jsonl_path` in a single atomic `O_APPEND` write."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = line_text.encode("utf-8")
    fd = os.open(str(jsonl_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, JSONL_FILE_MODE)
    try:
        os.write(fd, payload_bytes)
    finally:
        os.close(fd)


def _extract_accepted_specialist_feedback(
    raw_results: List[Dict[str, Any]],
    aggregated: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Groups diff-accepted normalized findings by specialist for future passes."""
    specialist_feedback: Dict[str, List[Dict[str, Any]]] = {
        spec_name: []
        for res in raw_results
        if isinstance(res, dict) and (spec_name := str(res.get("specialist") or "").strip())
    }
    for finding in aggregated.get("findings") or []:
        spec_name = str(finding.get("specialist") or "").strip()
        if spec_name:
            specialist_feedback.setdefault(spec_name, []).append(finding)
    return specialist_feedback


def persist_iteration_and_metrics(
    output_dir: Path,
    cache_root: Path,
    manifest: Dict[str, Any],
    raw_results: List[Dict[str, Any]],
    aggregated: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Path:
    """Atomically writes per-run metrics, JSONL telemetry, and per-CL feedback history."""
    metrics_path = output_dir / METRICS_REPORT_FILENAME
    atomic_write_text(metrics_path, json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    _append_metrics_jsonl(
        cache_root / METRICS_JSONL_FILENAME,
        json.dumps(metrics, ensure_ascii=False) + "\n",
    )

    change_key = str(manifest.get("change_key") or extract_change_key(manifest))
    history = load_iteration_history(change_key, cache_root)
    curr_tuple = (str(manifest.get("target_id") or ""), str(manifest.get("current_revision") or ""))
    prior_entries = [
        item
        for item in history["iterations"]
        if (str(item.get("target_id") or ""), str(item.get("current_revision") or "")) != curr_tuple
    ]
    prior_entries.append({
        "recorded_at": metrics["recorded_at"],
        "round_number": int(manifest.get("round_number") or 1),
        "target_id": str(manifest.get("target_id") or ""),
        "current_revision": str(manifest.get("current_revision") or ""),
        "commit_message": str(manifest.get("commit_message") or ""),
        "full_diff_text": str(manifest.get("full_diff_text") or manifest.get("diff_text") or ""),
        "total_findings": int(aggregated.get("total_findings") or 0),
        "wall_clock_sec": metrics["wall_clock_sec"],
        "specialist_feedback": _extract_accepted_specialist_feedback(raw_results, aggregated),
    })
    history["iterations"] = prior_entries[-MAX_STORED_ITERATIONS:]
    history_path = cache_root / HISTORY_SUBDIR / f"{change_key}.json"
    atomic_write_text(history_path, json.dumps(history, indent=2, ensure_ascii=False) + "\n")
    return metrics_path


def load_recent_swarm_metrics(
    cache_root: Path,
    limit: int = RECENT_METRICS_DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    """Loads up to `limit` most recent swarm metric entries from `metrics.jsonl`."""
    jsonl_path = cache_root / METRICS_JSONL_FILENAME
    if not jsonl_path.is_file():
        return []
    raw_lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: List[Dict[str, Any]] = []
    for line in raw_lines[-max(1, limit) :]:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries
