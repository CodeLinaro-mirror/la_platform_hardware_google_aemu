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

"""Live progress tracking and completed result discovery for Review Swarm runs.

Bridges `emu-dev-cli harness review-swarm` / `run_review_swarm_workflow.py` and the
Fast Gerrit Web UI (`commands.gerrit.ui_server`) via `review_swarm_status.json` plus
backwards-compatible fallback to `review_swarm_metrics.json` & `gerrit_review_payload.json`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from commands.harness.ledger import get_harness_base_dir
from commands.harness.review_history import METRICS_REPORT_FILENAME
from commands.harness.review_targets import atomic_write_text, sanitize_target_id

REVIEW_SWARM_SUBDIR: str = "review-swarm"
SWARM_STATUS_FILENAME: str = "review_swarm_status.json"
GERRIT_REVIEW_PAYLOAD_FILENAME: str = "gerrit_review_payload.json"
DIFF_MANIFEST_FILENAME: str = "diff_manifest.json"
ACTIVE_SWARM_STALE_SEC: float = 600.0
RECENT_PREP_PRESERVE_SEC: float = 180.0
INLINE_CATEGORY_PREFIX_RE: re.Pattern[str] = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.DOTALL)


def _utc_now(now_dt: datetime | None = None) -> datetime:
    """Returns timezone-aware UTC `now_dt` or current UTC clock."""
    return now_dt if now_dt is not None else datetime.now(timezone.utc)


def _parse_iso_utc(raw_value: Any) -> datetime | None:
    """Parses an ISO-8601 timestamp string into a UTC-aware `datetime`."""
    text = str(raw_value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    """Reads `path` as a JSON object, returning `None` if missing or malformed."""
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _preserve_recent_started_at(
    status_path: Path, now: datetime, max_age_sec: float
) -> str | None:
    """Preserves `started_at` when an in-flight swarm was queued moments earlier."""
    existing = _read_json_dict(status_path)
    if not existing or existing.get("state") != "running":
        return None
    started_dt = _parse_iso_utc(existing.get("started_at"))
    if started_dt is None:
        return None
    age_sec = max(0.0, (now - started_dt).total_seconds())
    return str(existing["started_at"]) if age_sec <= max_age_sec else None


def write_initial_swarm_status(
    output_dir: Path,
    target_manifest: dict[str, Any],
    specialists: list[dict[str, Any]],
    phase: str = "queued",
) -> Path:
    """Writes `review_swarm_status.json` when a Review Swarm bundle is prepared."""
    now = _utc_now()
    now_iso = now.isoformat()
    status_path = output_dir / SWARM_STATUS_FILENAME
    preserved_start = _preserve_recent_started_at(status_path, now, RECENT_PREP_PRESERVE_SEC)
    effective_phase = "preparing" if preserved_start else phase
    status_doc = {
        "state": "running",
        "phase": effective_phase,
        "target_type": str(target_manifest.get("target_type") or ""),
        "target_id": str(target_manifest.get("target_id") or ""),
        "current_revision": str(target_manifest.get("current_revision") or ""),
        "subject": str(target_manifest.get("subject") or ""),
        "round_number": int(target_manifest.get("round_number") or 1),
        "review_scope": str(target_manifest.get("review_scope") or "full"),
        "total_specialists": len(specialists),
        "completed_specialists": 0,
        "started_at": preserved_start or now_iso,
        "updated_at": now_iso,
        "agents": [
            {
                "specialist": str(spec.get("name") or ""),
                "role": str(spec.get("role") or ""),
                "model": str(spec.get("model") or ""),
                "status": "queued",
                "duration_sec": None,
                "finding_count": None,
            }
            for spec in specialists
        ],
    }
    atomic_write_text(status_path, json.dumps(status_doc, indent=2, ensure_ascii=False) + "\n")
    return status_path


def write_auditing_swarm_status(
    output_dir: Path,
    target_manifest: dict[str, Any],
    specialists: list[dict[str, Any]],
) -> dict[str, Any]:
    """Marks all specialists as `running` when Wave 1 parallel execution begins."""
    now = _utc_now()
    now_iso = now.isoformat()
    status_path = output_dir / SWARM_STATUS_FILENAME
    preserved_start = _preserve_recent_started_at(status_path, now, ACTIVE_SWARM_STALE_SEC)
    status_doc: dict[str, Any] = {
        "state": "running",
        "phase": "auditing",
        "target_type": str(target_manifest.get("target_type") or ""),
        "target_id": str(target_manifest.get("target_id") or ""),
        "current_revision": str(target_manifest.get("current_revision") or ""),
        "subject": str(target_manifest.get("subject") or ""),
        "round_number": int(target_manifest.get("round_number") or 1),
        "review_scope": str(target_manifest.get("review_scope") or "full"),
        "total_specialists": len(specialists),
        "completed_specialists": 0,
        "started_at": preserved_start or now_iso,
        "updated_at": now_iso,
        "agents": [
            {
                "specialist": str(spec.get("name") or ""),
                "role": str(spec.get("role") or ""),
                "model": str(spec.get("model") or ""),
                "status": "running",
                "duration_sec": None,
                "finding_count": None,
            }
            for spec in specialists
        ],
    }
    atomic_write_text(status_path, json.dumps(status_doc, indent=2, ensure_ascii=False) + "\n")
    return status_doc


def record_specialist_completion(
    output_dir: Path,
    status_doc: dict[str, Any],
    spec_output: dict[str, Any],
) -> None:
    """Updates `review_swarm_status.json` as each individual specialist completes."""
    spec_name = str(spec_output.get("specialist") or "")
    findings = spec_output.get("findings") or []
    raw_cnt = spec_output.get("finding_count")
    finding_count = int(raw_cnt if raw_cnt is not None else len(findings))
    for entry in status_doc.get("agents") or []:
        if entry.get("specialist") == spec_name:
            entry["status"] = "done"
            entry["duration_sec"] = spec_output.get("duration_sec")
            entry["finding_count"] = finding_count
    done_cnt = sum(1 for a in (status_doc.get("agents") or []) if a.get("status") == "done")
    status_doc["completed_specialists"] = done_cnt
    status_doc["updated_at"] = _utc_now().isoformat()
    atomic_write_text(
        output_dir / SWARM_STATUS_FILENAME,
        json.dumps(status_doc, indent=2, ensure_ascii=False) + "\n",
    )


def write_completed_swarm_status(
    output_dir: Path,
    status_doc: dict[str, Any],
    aggregated: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    """Persists final Wave 2 completion state, findings, and timing telemetry."""
    now_iso = _utc_now().isoformat()
    agents = [{**item, "status": "done"} for item in (metrics.get("agents") or [])]
    completed_doc = {
        **status_doc,
        "state": "completed",
        "phase": "completed",
        "completed_at": now_iso,
        "updated_at": now_iso,
        "total_specialists": len(agents),
        "completed_specialists": len(agents),
        "wall_clock_sec": metrics.get("wall_clock_sec", 0.0),
        "parallel_speedup": metrics.get("parallel_speedup", 1.0),
        "total_findings": int(aggregated.get("total_findings") or 0),
        "reuse_audit_line": str(aggregated.get("reuse_audit_line") or ""),
        "error_audit_line": str(aggregated.get("error_audit_line") or ""),
        "findings": list(aggregated.get("findings") or []),
        "agents": agents,
    }
    atomic_write_text(
        output_dir / SWARM_STATUS_FILENAME,
        json.dumps(completed_doc, indent=2, ensure_ascii=False) + "\n",
    )


def _extract_findings_from_gerrit_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extracts structured findings from a saved `gerrit_review_payload.json`."""
    if not payload or not isinstance(payload.get("comments"), dict):
        return []
    findings: list[dict[str, Any]] = []
    for file_path, items in payload["comments"].items():
        if not isinstance(items, list):
            continue
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            findings.append(_parse_single_payload_comment(str(file_path), raw_item))
    return findings


def _parse_single_payload_comment(file_path: str, raw_item: dict[str, Any]) -> dict[str, Any]:
    """Parses category, body text, and `Suggested fix:` from one Gerrit payload comment."""
    raw_msg = str(raw_item.get("message") or "").strip()
    body_part, _, fix_part = raw_msg.partition("\n\nSuggested fix:\n")
    cat_match = INLINE_CATEGORY_PREFIX_RE.match(body_part)
    category = cat_match.group(1) if cat_match else "review"
    clean_body = cat_match.group(2).strip() if cat_match else body_part
    return {
        "file": file_path,
        "line": int(raw_item.get("line") or 1),
        "category": category,
        "severity": "blocking",
        "message": clean_body,
        "suggested_fix": fix_part.strip(),
    }


def _latest_dir_mtime(swarm_dir: Path) -> float:
    """Returns the latest mtime across status, metrics, or manifest files in `swarm_dir`."""
    mtimes: list[float] = []
    for fname in (SWARM_STATUS_FILENAME, METRICS_REPORT_FILENAME, DIFF_MANIFEST_FILENAME):
        cand = swarm_dir / fname
        if cand.is_file():
            mtimes.append(cand.stat().st_mtime)
    return max(mtimes) if mtimes else swarm_dir.stat().st_mtime


def resolve_swarm_dir_for_cl(
    cl_number: str | int,
    current_revision: str = "",
    cache_root: Path | None = None,
) -> Path | None:
    """Finds the best-matching `review-swarm` bundle directory for `cl_number`."""
    root = cache_root if cache_root is not None else (get_harness_base_dir() / REVIEW_SWARM_SUBDIR)
    if not root.is_dir():
        return None
    safe_cl = sanitize_target_id(str(cl_number).strip())
    if current_revision.strip():
        exact = root / f"{safe_cl}_{sanitize_target_id(current_revision.strip())}"
        if exact.is_dir():
            return exact
    matches = [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and (entry.name == safe_cl or entry.name.startswith(f"{safe_cl}_"))
    ]
    return max(matches, key=_latest_dir_mtime) if matches else None


def _build_completed_fallback(
    swarm_dir: Path, cl_id: str, metrics: dict[str, Any]
) -> dict[str, Any]:
    """Reconstructs a normalized completed status dict from `review_swarm_metrics.json`."""
    payload = _read_json_dict(swarm_dir / GERRIT_REVIEW_PAYLOAD_FILENAME)
    findings = _extract_findings_from_gerrit_payload(payload)
    agents = [{**item, "status": "done"} for item in (metrics.get("agents") or [])]
    return {
        "state": "completed",
        "phase": "completed",
        "target_id": str(metrics.get("target_id") or cl_id),
        "round_number": int(metrics.get("round_number") or 1),
        "review_scope": str(metrics.get("review_scope") or "full"),
        "completed_at": str(metrics.get("recorded_at") or ""),
        "total_specialists": len(agents),
        "completed_specialists": len(agents),
        "wall_clock_sec": metrics.get("wall_clock_sec", 0.0),
        "parallel_speedup": metrics.get("parallel_speedup", 1.0),
        "total_findings": len(findings),
        "findings": findings,
        "agents": agents,
        "has_review_payload": (swarm_dir / GERRIT_REVIEW_PAYLOAD_FILENAME).is_file(),
    }


def read_cl_swarm_status(
    cl_number: str | int,
    current_revision: str = "",
    cache_root: Path | None = None,
    now_dt: datetime | None = None,
) -> dict[str, Any] | None:
    """Reads live or completed Review Swarm telemetry for `cl_number` from disk."""
    cl_id = str(cl_number).strip()
    swarm_dir = resolve_swarm_dir_for_cl(cl_id, current_revision=current_revision, cache_root=cache_root)
    if swarm_dir is None:
        return None
    now = _utc_now(now_dt)
    status_doc = _read_json_dict(swarm_dir / SWARM_STATUS_FILENAME)
    metrics_doc = _read_json_dict(swarm_dir / METRICS_REPORT_FILENAME)
    has_payload = (swarm_dir / GERRIT_REVIEW_PAYLOAD_FILENAME).is_file()

    if status_doc and status_doc.get("state") == "running":
        started_dt = _parse_iso_utc(status_doc.get("started_at"))
        elapsed_sec = round(max(0.0, (now - started_dt).total_seconds()), 1) if started_dt else 0.0
        if elapsed_sec <= ACTIVE_SWARM_STALE_SEC:
            return {
                **status_doc,
                "elapsed_sec": elapsed_sec,
                "has_review_payload": has_payload,
            }
    if status_doc and status_doc.get("state") == "completed":
        return {**status_doc, "has_review_payload": has_payload}
    if metrics_doc is not None:
        return _build_completed_fallback(swarm_dir, cl_id, metrics_doc)
    return None
