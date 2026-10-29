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

"""Presubmit status extraction and TreeHugger target inspection for Fast Gerrit UI."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
import urllib.parse

DEFAULT_STUBBY_BIN = "/usr/bin/stubby"
PRESUBMIT_READ_BLADE_TARGET = "blade:android-presubmit-prod"
PRESUBMIT_LIST_RPC = (
    "google.internal.android.engprod.proto.presubmit.v1.AndroidPresubmit.ListPresubmitRuns"
)
PRESUBMIT_RPC_TIMEOUT_SEC = 15
ANDROID_BUILD_HOST = "android-build.corp.google.com"
COMPLETED_TARGETS_CACHE_TTL_SEC = 120.0
ACTIVE_TARGETS_CACHE_TTL_SEC = 5.0

MINUTES_PER_HOUR = 60.0
HOURS_PER_DAY = 24.0
MICROSECONDS_PER_SECOND = 1_000_000

_PRESUBMIT_STARTED_RE = re.compile(r"##\s*Presubmit started:\s*(L\d+)", re.IGNORECASE)
_PRESUBMIT_FINISHED_RE = re.compile(
    r"##\s*Presubmit (?:finished|failed|skipped|aborted):\s*([^\n]+)",
    re.IGNORECASE,
)
_CHECKS_TAB_LINK_RE = re.compile(r"\[Checks tab\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_EXPLAINER_LINK_RE = re.compile(r"\[Treehugger Explainer\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_VALID_BUILD_ID_RE = re.compile(r"^(P|E|L|T)?(\d+)$", re.IGNORECASE)

_BUILD_PREFIX_TO_TYPE: dict[str, str] = {
    "": "submitted",
    "p": "pending",
    "e": "external",
    "t": "train",
    "l": "local",
}

_RUN_STATE_MAP: dict[str, tuple[str, str]] = {
    "0": ("unknown", "UNKNOWN"),
    "UNKNOWN_REQUEST_STATE": ("unknown", "UNKNOWN"),
    "1": ("queued", "QUEUED"),
    "QUEUED": ("queued", "QUEUED"),
    "2": ("running", "PROCESSING"),
    "PROCESSING": ("running", "PROCESSING"),
    "3": ("running", "LAUNCHED"),
    "LAUNCHED": ("running", "LAUNCHED"),
    "4": ("failed", "INFRA_ERROR"),
    "INFRA_ERROR": ("failed", "INFRA_ERROR"),
    "5": ("obsolete", "DUPLICATED"),
    "DUPLICATED": ("obsolete", "DUPLICATED"),
    "6": ("failed", "CLIENT_ERROR"),
    "CLIENT_ERROR": ("failed", "CLIENT_ERROR"),
    "7": ("obsolete", "OBSOLETE"),
    "OBSOLETE": ("obsolete", "OBSOLETE"),
    "8": ("passed", "PASS"),
    "PASS": ("passed", "PASS"),
    "9": ("failed", "FAIL"),
    "FAIL": ("failed", "FAIL"),
    "10": ("running", "RUNNING"),
    "RUNNING": ("running", "RUNNING"),
    "11": ("failed", "TIMED_OUT"),
    "TIMED_OUT": ("failed", "TIMED_OUT"),
    "12": ("warning", "PASS_WITH_WARNING"),
    "PASS_WITH_WARNING": ("warning", "PASS_WITH_WARNING"),
    "13": ("queued", "REQUEST_RECEIVED"),
    "REQUEST_RECEIVED": ("queued", "REQUEST_RECEIVED"),
    "14": ("queued", "REQUEST_VERIFYING"),
    "REQUEST_VERIFYING": ("queued", "REQUEST_VERIFYING"),
    "15": ("queued", "THROTTLED"),
    "THROTTLED": ("queued", "THROTTLED"),
}

_REQ_TYPE_MAP: dict[str, str] = {
    "1": "build",
    "BUILD": "build",
    "2": "test",
    "TEST": "test",
    "3": "test_module",
    "TEST_RESULT": "test_module",
    "4": "preflight",
    "PRE_FLIGHT": "preflight",
}

_REQ_STATUS_MAP: dict[str, tuple[str, str]] = {
    "0": ("queued", "PENDING"),
    "UNKNOWN_REQUIREMENT_STATUS": ("queued", "PENDING"),
    "1": ("passed", "PASS"),
    "PASS": ("passed", "PASS"),
    "2": ("failed", "FAIL"),
    "FAIL": ("failed", "FAIL"),
    "3": ("running", "RUNNING"),
    "RUNNING": ("running", "RUNNING"),
    "4": ("failed", "INFRA_ERROR"),
    "INFRA_ERROR": ("failed", "INFRA_ERROR"),
    "5": ("skipped", "SKIPPED"),
    "SKIPPED": ("skipped", "SKIPPED"),
    "6": ("skipped", "OBSOLETE"),
    "OBSOLETE": ("skipped", "OBSOLETE"),
    "7": ("failed", "TIMED_OUT"),
    "TIMED_OUT": ("failed", "TIMED_OUT"),
    "8": ("skipped", "DEPENDENCY_FAILED"),
    "DEPENDENCY_FAILED": ("skipped", "DEPENDENCY_FAILED"),
    "9": ("passed", "CACHED"),
    "CACHED": ("passed", "CACHED"),
    "10": ("queued", "SUSPENDED"),
    "SUSPENDED": ("queued", "SUSPENDED"),
}

_BLOCKING_LEVEL_MAP: dict[str, str] = {
    "1": "BLOCKING",
    "BLOCKING": "BLOCKING",
    "2": "WARN",
    "WARN": "WARN",
    "3": "NON_BLOCKING",
    "NON_BLOCKING": "NON_BLOCKING",
}

_STATUS_SORT_ORDER: dict[str, int] = {
    "failed": 0,
    "warning": 1,
    "running": 2,
    "queued": 3,
    "passed": 4,
    "skipped": 5,
}

_KIND_SORT_ORDER: dict[str, int] = {
    "preflight": 0,
    "build": 1,
    "test": 2,
    "test_module": 3,
}


def format_relative_age(hours_elapsed: float) -> str:
    """Formats `hours_elapsed` into a compact relative age string (`just now`, `8m ago`, `2h ago`, `1.4d ago`)."""
    safe_hours = max(0.0, float(hours_elapsed))
    minutes = safe_hours * MINUTES_PER_HOUR
    if minutes < 1.0:
        return "just now"
    if safe_hours < 1.0:
        return f"{max(1, int(round(minutes)))}m ago"
    if safe_hours < HOURS_PER_DAY:
        return f"{max(1, int(round(safe_hours)))}h ago"
    return f"{safe_hours / HOURS_PER_DAY:.1f}d ago"


def format_duration_seconds(seconds: float | None) -> str:
    """Formats a duration in seconds as `42s`, `8m 15s`, or `1h 12m`."""
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    mins, secs = divmod(total, 60)
    if mins < 60:
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    hours, rem_mins = divmod(mins, 60)
    return f"{hours}h {rem_mins}m"


def _scan_patchset_presubmit_messages(
    messages: list[dict[str, Any]], cur_ps: int
) -> dict[str, Any]:
    """Scans Gerrit change messages on `cur_ps` for TreeHugger workplan IDs, summaries, and links."""
    workplan_id = ""
    summary = ""
    checks_url = ""
    explainer_url = ""
    started_idx = -1
    finished_idx = -1

    for idx, msg_obj in enumerate(messages):
        ps_num = int(msg_obj.get("_revision_number", 0) or 0)
        if ps_num != cur_ps:
            continue
        text = str(msg_obj.get("message") or "")
        started_match = _PRESUBMIT_STARTED_RE.search(text)
        if started_match:
            workplan_id = started_match.group(1)
            started_idx = idx
        finished_match = _PRESUBMIT_FINISHED_RE.search(text)
        if finished_match:
            summary = finished_match.group(1).strip().rstrip(".")
            finished_idx = idx
        checks_match = _CHECKS_TAB_LINK_RE.search(text)
        if checks_match:
            checks_url = checks_match.group(1)
        explainer_match = _EXPLAINER_LINK_RE.search(text)
        if explainer_match:
            explainer_url = explainer_match.group(1)

    return {
        "workplan_id": workplan_id,
        "summary": summary,
        "checks_url": checks_url,
        "explainer_url": explainer_url,
        "has_active_run": started_idx >= 0 and finished_idx < started_idx,
    }


def extract_presubmit_status_from_change(
    change: dict[str, Any],
    pv_score: int,
    pr_enabled: bool,
    cur_ps: int,
) -> dict[str, Any]:
    """Derives canonical presubmit status, badge label, workplan ID, and Checks URLs for a CL."""
    num = int(change.get("_number", 0) or 0)
    project = str(change.get("project") or "")
    msg_meta = _scan_patchset_presubmit_messages(change.get("messages") or [], cur_ps)
    workplan_id = msg_meta["workplan_id"]
    default_checks = (
        f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{num}/{cur_ps}?tab=checks"
        if project and num
        else ""
    )
    checks_tab_url = msg_meta["checks_url"] or default_checks
    workplan_url = (
        f"https://{ANDROID_BUILD_HOST}/workplan/{workplan_id}" if workplan_id else ""
    )

    if pr_enabled:
        status, label = ("running", "⏳ PV Running") if msg_meta["has_active_run"] else ("queued", "⏳ PV Queued")
    elif msg_meta["has_active_run"] and pv_score == 0:
        status, label = "running", "⏳ PV Running"
    elif pv_score >= 2:
        status, label = "passed", f"✓ PV +{pv_score}"
    elif pv_score == 1:
        status, label = "warning", "⚠ PV +1"
    elif pv_score < 0:
        status, label = "failed", f"✗ PV {pv_score}"
    else:
        status, label = "not_started", "⏸ PV 0"

    return {
        "presubmit_status": status,
        "presubmit_label": label,
        "presubmit_summary": msg_meta["summary"],
        "workplan_id": workplan_id,
        "workplan_url": workplan_url,
        "checks_tab_url": checks_tab_url,
        "explainer_url": msg_meta["explainer_url"],
    }


def resolve_gerrit_presubmit_hostname(gerrit_host: str) -> str:
    """Converts a Gerrit host URL into the short hostname expected by `AndroidPresubmit`."""
    raw = (gerrit_host or "").strip()
    if "://" in raw:
        host_only = urllib.parse.urlparse(raw).hostname or raw
    else:
        host_only = raw.split("/", 1)[0]
    first_label = host_only.split(".", 1)[0].strip().lower()
    if first_label.endswith("-review"):
        first_label = first_label[: -len("-review")]
    return first_label or "googleplex-android"


def convert_atp_test_name(raw_test_name: str) -> str:
    """Shortens ATP v2 test identifiers (`v2/<team>/<suite>`) to match Gerrit Checks display."""
    cleaned = (raw_test_name or "").strip()
    parts = cleaned.split("/", 2)
    if len(parts) == 3 and parts[0] == "v2":
        if parts[1] == "android-code-coverage-team":
            return "Coverage"
        return parts[2]
    return cleaned


def _resolve_stubby_bin(explicit_bin: str | None = None) -> str | None:
    """Locates the `stubby` CLI binary on gLinux / Cloudtop."""
    if explicit_bin:
        return explicit_bin if Path(explicit_bin).is_file() else shutil.which(explicit_bin)
    if Path(DEFAULT_STUBBY_BIN).is_file():
        return DEFAULT_STUBBY_BIN
    return shutil.which("stubby")


def _extract_primary_worknode_id(req: dict[str, Any]) -> str:
    """Returns the primary `worknode_id` (`L...:N...`) from `requirement_worknode_infos`."""
    infos = req.get("requirement_worknode_infos") or []
    for info in infos:
        if info.get("primary") and info.get("worknode_id"):
            return str(info["worknode_id"])
    for info in infos:
        if info.get("worknode_id"):
            return str(info["worknode_id"])
    return ""


def _build_worknode_url(workplan_id: str, worknode_id: str) -> str:
    """Builds the Android Build Workplan URL focused on `worknode_id`."""
    wp = workplan_id or (worknode_id.split(":", 1)[0] if ":" in worknode_id else "")
    if not wp:
        return ""
    if worknode_id:
        encoded = urllib.parse.urlencode({"activeNode": worknode_id})
        return f"https://{ANDROID_BUILD_HOST}/workplan/{wp}?{encoded}"
    return f"https://{ANDROID_BUILD_HOST}/workplan/{wp}"


def _build_urls_for_build_req(
    build_id: str,
    target: str,
    raw_status: str,
    workplan_id: str,
    worknode_id: str,
) -> tuple[str, str, str]:
    """Returns `(primary_url, secondary_url, secondary_label)` for a `BUILD` requirement."""
    match = _VALID_BUILD_ID_RE.fullmatch((build_id or "").strip())
    if not match or not target:
        return _build_worknode_url(workplan_id, worknode_id), "", ""
    prefix = (match.group(1) or "").lower()
    build_type = _BUILD_PREFIX_TO_TYPE.get(prefix, "submitted")
    details_url = f"https://{ANDROID_BUILD_HOST}/builds/build-details/{build_id}/targets/{target}"
    if raw_status in ("FAIL", "INFRA_ERROR", "TIMED_OUT"):
        err_log_url = (
            f"https://{ANDROID_BUILD_HOST}/builds/{build_type}/{build_id}/"
            f"{target}/latest/view/logs/build_error.log"
        )
        return err_log_url, details_url, "Build Details"
    node_url = _build_worknode_url(workplan_id, worknode_id)
    return details_url, node_url, ("Workplan" if node_url else "")


def _build_urls_for_test_req(
    ants_id: str,
    blocking_name: str,
    raw_status: str,
    workplan_id: str,
    worknode_id: str,
) -> tuple[str, str, str]:
    """Returns `(primary_url, secondary_url, secondary_label)` for a `TEST` requirement."""
    node_url = _build_worknode_url(workplan_id, worknode_id)
    if not ants_id:
        return node_url, "", ""
    params: dict[str, str] = {"invocationId": ants_id}
    if blocking_name == "NON_BLOCKING":
        params["blocking"] = "blocking,nonBlocking" if raw_status == "PASS" else "nonBlocking"
        params["status"] = "none"
    elif blocking_name == "WARN":
        params["blocking"] = "nonBlocking,warning" if raw_status == "PASS" else "warning"
        params["status"] = "none"
    else:
        params["blocking"] = "blocking"
        params["status"] = "none"
    test_url = f"https://{ANDROID_BUILD_HOST}/builds/tests/view?{urllib.parse.urlencode(params)}"
    return test_url, node_url, ("Workplan" if node_url else "")


def _extract_req_identity(
    kind: str, ident: dict[str, Any]
) -> tuple[str, str, str, str]:
    """Returns `(name, subtitle, branch, build_target)` for a `PresubmitRequirement`."""
    if kind == "build":
        b_id = ident.get("build_requirement_identifier") or {}
        target = str(b_id.get("target") or "Build Target")
        branch = str(b_id.get("branch") or "")
        return target, branch, branch, target
    if kind == "test":
        t_id = ident.get("test_requirement_identifier") or {}
        suite = convert_atp_test_name(str(t_id.get("atp_test_name") or "ATP Test"))
        b_target = str(t_id.get("build_target") or "")
        branch = str(t_id.get("branch") or "")
        sub = " · ".join(p for p in (b_target, branch) if p)
        return suite, sub, branch, b_target
    if kind == "preflight":
        pf_id = ident.get("pre_flight_requirement_identifier") or {}
        task_name = str(pf_id.get("task_name") or "Presubmit Preflight")
        return task_name, "Preflight Check", "", ""
    tr_id = ident.get("test_result_requirement_identifier") or {}
    module = str((tr_id.get("test_identifier") or {}).get("module") or "Test Module")
    return module, "Test Module", "", ""


def _compute_req_duration(
    req: dict[str, Any], norm_status: str, cached: bool, now_us: int
) -> tuple[float | None, str]:
    """Computes elapsed or total seconds for a presubmit requirement."""
    if cached or norm_status in ("skipped", "queued"):
        return None, ""
    create_us = int(req.get("create_timestamp_micro", 0) or 0)
    update_us = int(req.get("update_timestamp_micro", 0) or 0)
    if create_us <= 0:
        return None, ""
    end_us = now_us if norm_status == "running" else update_us
    if end_us <= create_us:
        return None, ""
    sec = round((end_us - create_us) / MICROSECONDS_PER_SECOND, 1)
    return sec, format_duration_seconds(sec)


def _resolve_req_urls_and_artifact(
    kind: str,
    build_id: str,
    ants_id: str,
    build_target: str,
    blocking_name: str,
    raw_status: str,
    workplan_id: str,
    worknode_id: str,
) -> tuple[str, str, str, str]:
    """Returns `(primary_url, secondary_url, secondary_label, artifact_id)` for a requirement."""
    if kind == "build":
        p_url, s_url, s_lbl = _build_urls_for_build_req(
            build_id, build_target, raw_status, workplan_id, worknode_id
        )
        return p_url, s_url, s_lbl, build_id
    if kind in ("test", "test_module"):
        p_url, s_url, s_lbl = _build_urls_for_test_req(
            ants_id, blocking_name, raw_status, workplan_id, worknode_id
        )
        return p_url, s_url, s_lbl, ants_id
    return _build_worknode_url(workplan_id, worknode_id), "", "", ""


def format_presubmit_requirement(
    req: dict[str, Any], workplan_id: str, now_us: int
) -> dict[str, Any]:
    """Formats one raw `PresubmitRequirement` proto JSON dict into a UI target row."""
    kind = _REQ_TYPE_MAP.get(str(req.get("type", "")), "build")
    norm_status, raw_status = _REQ_STATUS_MAP.get(str(req.get("status", "0")), ("queued", "PENDING"))
    blocking_name = _BLOCKING_LEVEL_MAP.get(str(req.get("blocking_level", "1")), "BLOCKING")
    cached = raw_status == "CACHED"
    if norm_status == "failed" and blocking_name in ("WARN", "NON_BLOCKING"):
        norm_status = "warning"

    name, subtitle, branch, build_target = _extract_req_identity(
        kind, req.get("requirement_identifier") or {}
    )
    worknode_id = _extract_primary_worknode_id(req)
    build_id = str(req.get("latest_build_id") or "")
    ants_id = str(req.get("ants_invocation_id") or "")
    primary_url, secondary_url, secondary_label, artifact_id = _resolve_req_urls_and_artifact(
        kind, build_id, ants_id, build_target, blocking_name, raw_status, workplan_id, worknode_id
    )
    duration_sec, duration_display = _compute_req_duration(req, norm_status, cached, now_us)
    return {
        "id": str(req.get("presubmit_requirement_id") or ""),
        "kind": kind,
        "name": name,
        "subtitle": subtitle,
        "branch": branch,
        "build_target": build_target,
        "status": norm_status,
        "raw_status": raw_status,
        "cached": cached,
        "waiting_on_build": bool(kind == "test" and norm_status == "running" and not ants_id),
        "blocking": blocking_name == "BLOCKING" and norm_status != "skipped",
        "blocking_level": blocking_name,
        "artifact_id": artifact_id,
        "worknode_id": worknode_id,
        "duration_sec": duration_sec,
        "duration_display": duration_display,
        "url": primary_url,
        "secondary_url": secondary_url,
        "secondary_label": secondary_label,
    }


def _select_primary_presubmit_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Chooses the most recent non-obsolete `PresubmitRun` from `ListPresubmitRunsResponse`."""
    if not runs:
        return None

    def _sort_key(run: dict[str, Any]) -> tuple[int, int, int]:
        state_norm, _ = _RUN_STATE_MAP.get(str(run.get("presubmit_run_state", "0")), ("unknown", "UNKNOWN"))
        not_obsolete = 0 if state_norm == "obsolete" else 1
        attempt = int(run.get("workplan_attempt", 0) or 0)
        created = int(run.get("create_timestamp_micro", 0) or 0)
        return (not_obsolete, attempt, created)

    return max(runs, key=_sort_key)


def _summarize_target_counts(targets: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregates status counts across formatted presubmit targets."""
    counts = {
        "total": len(targets),
        "passed": 0,
        "cached": 0,
        "running": 0,
        "queued": 0,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
    }
    for t in targets:
        st = t["status"]
        if st in counts:
            counts[st] += 1
        if t.get("cached"):
            counts["cached"] += 1
    return counts


def _empty_presubmit_targets_payload(
    cl_id: str, patchset: int, checks_tab_url: str
) -> dict[str, Any]:
    """Builds the empty response payload when no TreeHugger run exists yet for `(cl_id, patchset)`."""
    return {
        "status": "ok",
        "cl": int(cl_id) if str(cl_id).isdigit() else cl_id,
        "patch_set": patchset,
        "run_status": "not_started",
        "raw_run_state": "NONE",
        "workplan_id": "",
        "workplan_url": "",
        "presubmit_run_id": "",
        "attempt": 0,
        "total_runs": 0,
        "checks_tab_url": checks_tab_url,
        "counts": _summarize_target_counts([]),
        "targets": [],
    }


def parse_list_presubmit_runs_json(
    raw_stdout: str,
    cl_id: str,
    patchset: int,
    checks_tab_url: str,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    """Parses `AndroidPresubmit.ListPresubmitRuns` JSON output into structured UI targets."""
    now = now_dt or datetime.now(timezone.utc)
    now_us = int(now.timestamp() * MICROSECONDS_PER_SECOND)
    text = (raw_stdout or "").strip()
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse TreeHugger presubmit JSON for ag/{cl_id} (PS{patchset}): "
            f"{exc.msg} at line {exc.lineno} column {exc.colno} ({len(text)} bytes). "
            f"Run `gcert` and retry, or open {checks_tab_url}."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Failed to parse TreeHugger presubmit JSON for ag/{cl_id} (PS{patchset}): "
            f"expected JSON object, got {type(data).__name__}. "
            f"Retry with refresh or open {checks_tab_url}."
        )
    runs = data.get("presubmit_runs") or []
    chosen = _select_primary_presubmit_run(runs)
    if chosen is None:
        return _empty_presubmit_targets_payload(cl_id, patchset, checks_tab_url)

    workplan_id = str(chosen.get("workplan_id") or "")
    run_status, raw_run_state = _RUN_STATE_MAP.get(
        str(chosen.get("presubmit_run_state", "0")), ("unknown", "UNKNOWN")
    )
    targets = [
        format_presubmit_requirement(r, workplan_id, now_us)
        for r in (chosen.get("requirements") or [])
    ]
    targets.sort(
        key=lambda t: (
            _STATUS_SORT_ORDER.get(t["status"], 9),
            1 if t["cached"] else 0,
            _KIND_SORT_ORDER.get(t["kind"], 9),
            t["name"].lower(),
            t["subtitle"].lower(),
        )
    )
    counts = _summarize_target_counts(targets)
    if (
        run_status == "running"
        and counts["running"] == 0
        and counts["queued"] == 0
        and counts["total"] > 0
        and counts["failed"] == 0
        and counts["warning"] == 0
    ):
        run_status = "passed"
    return {
        "status": "ok",
        "cl": int(cl_id) if str(cl_id).isdigit() else cl_id,
        "patch_set": patchset,
        "run_status": run_status,
        "raw_run_state": raw_run_state,
        "workplan_id": workplan_id,
        "workplan_url": f"https://{ANDROID_BUILD_HOST}/workplan/{workplan_id}" if workplan_id else "",
        "presubmit_run_id": str(chosen.get("presubmit_run_id") or ""),
        "attempt": int(chosen.get("workplan_attempt", 1) or 1),
        "total_runs": len(runs),
        "checks_tab_url": checks_tab_url,
        "counts": counts,
        "targets": targets,
    }


def query_presubmit_targets_via_stubby(
    cl_id: str,
    patchset: int,
    gerrit_host: str,
    project: str = "",
    stubby_bin: str | None = None,
) -> dict[str, Any]:
    """Queries `blade:android-presubmit-prod` via `stubby` for all build/test targets on `cl_id`."""
    resolved_stubby = _resolve_stubby_bin(stubby_bin)
    checks_url = (
        f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{cl_id}/{patchset}?tab=checks"
        if project
        else f"https://googleplex-android-review.git.corp.google.com/q/{cl_id}"
    )
    if not resolved_stubby:
        raise RuntimeError(
            f"Failed to query TreeHugger presubmit targets for ag/{cl_id} (PS{patchset}): "
            f"`stubby` CLI ('{DEFAULT_STUBBY_BIN}') was not found on this machine. "
            f"Run Fast Gerrit UI on gLinux/Cloudtop or open the Gerrit Checks tab ({checks_url})."
        )

    short_host = resolve_gerrit_presubmit_hostname(gerrit_host)
    req_proto = (
        f'change_spec: {{ gerrit_change: {{ hostname: "{short_host}", '
        f"change_number: {int(cl_id)}, patchset: {int(patchset)} }} }}, "
        "optional_fields: { presubmit_requirements: true }"
    )
    try:
        proc = subprocess.run(
            [
                resolved_stubby,
                "call",
                "--output_json",
                PRESUBMIT_READ_BLADE_TARGET,
                PRESUBMIT_LIST_RPC,
                req_proto,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=PRESUBMIT_RPC_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Failed to query TreeHugger presubmit targets for ag/{cl_id} (PS{patchset}): "
            f"`stubby` RPC to '{PRESUBMIT_READ_BLADE_TARGET}' timed out after "
            f"{PRESUBMIT_RPC_TIMEOUT_SEC}s. Run `gcert` and retry, or open {checks_url}."
        ) from exc

    if proc.returncode != 0:
        err_detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        raise RuntimeError(
            f"Failed to query TreeHugger presubmit targets for ag/{cl_id} (PS{patchset}): "
            f"{err_detail}. Run `gcert` to refresh LOAS credentials or open {checks_url}."
        )
    return parse_list_presubmit_runs_json(proc.stdout, cl_id, patchset, checks_url)
