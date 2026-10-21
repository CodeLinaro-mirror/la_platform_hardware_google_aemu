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

"""Fast Multi-Commit & Agent-Aware Gerrit Web UI server (`emu-dev-cli gerrit ui`).

Provides a local HTTP backend and single-page web application tailored for
reviewing stacked CLs and agent-authored commits quickly:
- Explicit 2x Human `+2` approval slot tracking for agent-uploaded CLs.
- Default "Vetted & Ready" gate (Presubmit +1/+2 AND Agent CR+1 with 0 open comments).
- Auto-hiding CLs already `+2`'d by the active viewer.
- Time-in-Flight & "Cliff Radar" staleness tracking with 1-click Chat follow-up pings.
- Risk / Cognitive Load classification (Low / Medium / High).
- Continuous multi-commit stack diff rendering, pure-rebase detection, batch voting,
  and 1-click `aemu-harness` / workflow execution (`review-swarm`, `address-review`, etc.).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from typing import Any
import urllib.parse
import webbrowser

from commands.harness.review_swarm_status import (
    GERRIT_REVIEW_PAYLOAD_FILENAME,
    read_cl_swarm_status,
    resolve_swarm_dir_for_cl,
)
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GERRIT_CLI_TIMEOUT_SEC,
    GerritClient,
    decode_gerrit_patch,
    parse_cl_identifier,
    strip_xssi_prefix,
    unquote_git_diff_path,
)

DEFAULT_GERRIT_UI_PORT = 8085
MAX_PORT_PROBES = 50
FRESH_THRESHOLD_HOURS = 24.0
CLIFF_THRESHOLD_HOURS = 96.0
SILENT_ATTENTION_HOURS = 24.0
LOW_RISK_MAX_LINES = 60
HIGH_RISK_MIN_LINES = 250
DIFF_FETCH_WORKERS = 6
DEFAULT_MAX_DIFF_LINES_PER_FILE = 500

DEFAULT_EMULATOR_GERRIT_QUERY = (
    "status:open ("
    "project:platform/hardware/generic/goldfish OR "
    "project:platform/hardware/google/aemu OR "
    "project:platform/external/qemu OR "
    "project:platform/hardware/google/gfxstream OR "
    "project:platform/build/bazel"
    ")"
)

TEAM_ROSTER_EMAILS: tuple[str, ...] = (
    "jansene@google.com",
    "joshuaduong@google.com",
    "rkir@google.com",
    "whollins@google.com",
    "bohu@google.com",
    "jpcottin@google.com",
    "corakwue@google.com",
    "kocdemir@google.com",
    "drosen@google.com",
)

_HIGH_RISK_PATH_RE = re.compile(
    r"(\.proto$|/include/|mutex|lock|bql|thread|async|memory|dma|bar4|vulkan|webrtc)",
    re.IGNORECASE,
)
_LOW_RISK_SUBJECT_RE = re.compile(
    r"^(style|docs|chore|test)(\([^)]+\))?:|\bKISS\b",
    re.IGNORECASE,
)
_HIGH_RISK_SUBJECT_RE = re.compile(
    r"^(feat)(\([^)]+\))?:|\b(security|uaf|race|deadlock|escape)\b",
    re.IGNORECASE,
)

DEFAULT_ISSUES_CLI_BIN = "/google/bin/releases/issues-cli/issues"
BUG_LOOKUP_TIMEOUT_SEC = 8

COMMIT_TYPE_SPECS: dict[str, dict[str, str]] = {
    "bugfix": {"id": "bugfix", "label": "🐛 Bug Fixes", "short": "Bug Fix"},
    "perf": {"id": "perf", "label": "⚡ Performance", "short": "Perf"},
    "ets_test": {"id": "ets_test", "label": "🧪 ETS & Tests", "short": "ETS / Test"},
    "cleanup": {"id": "cleanup", "label": "🧹 Cleanup & Refactor", "short": "Cleanup"},
    "feature": {"id": "feature", "label": "✨ Features & UI", "short": "Feature"},
    "docs_build": {"id": "docs_build", "label": "📦 Build, Deps & Docs", "short": "Build / Docs"},
}

_BUG_FOOTER_LINE_RE = re.compile(
    r"^\s*(?:Bug|Fixes|Fixed|Issue|BUG)\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_BUG_ID_NUMBER_RE = re.compile(
    r"(?:b/|http://b/|https?://b\.corp\.google\.com/issues/|https?://issuetracker\.google\.com/issues/)?(\b\d{6,12}\b)",
    re.IGNORECASE,
)
_INLINE_B_LINK_RE = re.compile(r"\bb/(\d{6,12})\b", re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def extract_bug_ids_from_change(change: dict[str, Any]) -> list[str]:
    """Extracts deduplicated numeric Buganizer IDs from a Gerrit change's commit message and subject."""
    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    commit_msg = ((rev_obj.get("commit") or {}).get("message") or "").strip()
    subject = (change.get("subject") or "").strip()
    full_text = f"{subject}\n{commit_msg}" if commit_msg else subject

    found: list[str] = []
    seen: set[str] = set()

    for line_match in _BUG_FOOTER_LINE_RE.finditer(full_text):
        rhs = line_match.group(1)
        for num_match in _BUG_ID_NUMBER_RE.finditer(rhs):
            bug_id = num_match.group(1)
            if bug_id not in seen:
                seen.add(bug_id)
                found.append(bug_id)

    for inline_match in _INLINE_B_LINK_RE.finditer(full_text):
        bug_id = inline_match.group(1)
        if bug_id not in seen:
            seen.add(bug_id)
            found.append(bug_id)

    return found


def parse_issues_search_output(raw_text: str) -> dict[str, dict[str, str]]:
    """Parses `issues readonly search --fields issue_id,priority,severity,status,title` output."""
    cleaned = _ANSI_ESCAPE_RE.sub("", raw_text or "")
    parsed: dict[str, dict[str, str]] = {}
    cur: dict[str, str] = {}

    def flush_current() -> None:
        issue_id = cur.get("id", "").strip()
        if issue_id:
            parsed[issue_id] = {
                "id": issue_id,
                "url": f"https://b.corp.google.com/issues/{issue_id}",
                "priority": cur.get("priority", ""),
                "severity": cur.get("severity", ""),
                "status": cur.get("status", ""),
                "title": cur.get("title", ""),
            }
        cur.clear()

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if line == "---":
            flush_current()
            continue
        m_id = re.match(r"^(?:Issue ID|ID):\s*(\d+)\s*$", line, re.IGNORECASE)
        if m_id:
            if cur.get("id"):
                flush_current()
            cur["id"] = m_id.group(1)
            continue
        m_pri = re.match(r"^Priority:\s*(P[0-4])\b", line, re.IGNORECASE)
        if m_pri:
            cur["priority"] = m_pri.group(1).upper()
            continue
        m_sev = re.match(r"^Severity:\s*(S[0-4])\b", line, re.IGNORECASE)
        if m_sev:
            cur["severity"] = m_sev.group(1).upper()
            continue
        m_status = re.match(r"^Status:\s*(.+)$", line, re.IGNORECASE)
        if m_status:
            cur["status"] = m_status.group(1).strip()
            continue
        m_title = re.match(r"^Title:\s*(.+)$", line, re.IGNORECASE)
        if m_title:
            cur["title"] = m_title.group(1).strip()
            continue
        m_batch = re.match(
            r"^-\s*Issue\s+(\d+):\s*(.+?)\s*\[([^\]]+)\]\s*\(Priority:\s*(P[0-4])",
            line,
            re.IGNORECASE,
        )
        if m_batch:
            flush_current()
            bid = m_batch.group(1)
            parsed[bid] = {
                "id": bid,
                "url": f"https://b.corp.google.com/issues/{bid}",
                "priority": m_batch.group(4).upper(),
                "severity": "",
                "status": m_batch.group(3).strip(),
                "title": m_batch.group(2).strip(),
            }

    flush_current()
    return parsed


def classify_commit_type(change: dict[str, Any], has_bugs: bool = False) -> dict[str, str]:
    """Classifies a Gerrit CL into `bugfix`, `perf`, `ets_test`, `cleanup`, `feature`, or `docs_build`."""
    subject = (change.get("subject") or "").strip()
    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    file_paths = [p for p in (rev_obj.get("files") or {}).keys() if p != "/COMMIT_MSG"]

    if re.match(r"^perf(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["perf"])
    if re.match(r"^(ets|test|tests)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["ets_test"])
    if re.match(r"^(kiss|refactor|cleanup|style|chore)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["cleanup"])
    if re.match(r"^(fix|bugfix|hotfix)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    if re.match(r"^(docs|build|ci|release)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["docs_build"])
    if re.match(r"^feat(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["feature"])

    all_docs_or_build = bool(file_paths) and all(
        p.endswith((
            ".md",
            ".txt",
            "OWNERS",
            "BUILD",
            "BUILD.bazel",
            "MODULE.bazel",
            ".bzl",
            ".mk",
            ".bp",
            ".apk",
            ".prop_template",
            "source.json",
        ))
        for p in file_paths
    )
    all_tests = bool(file_paths) and all(
        p.endswith((
            "_test.cc",
            "_test.cpp",
            "_test.py",
            "_unittest.cpp",
            ".spec.ts",
            ".test.ts",
            ".test.tsx",
            "Test.kt",
            "Test.java",
        ))
        or "/ets/" in p
        or p.startswith("goldfish_test/ets/")
        for p in file_paths
    )

    if re.search(r"\b(doc|document|documentation|readme)\b", subject, re.IGNORECASE) or all_docs_or_build:
        if not re.search(r"\b(fix|enable|desktop_mode|playstore)\b", subject, re.IGNORECASE):
            return dict(COMMIT_TYPE_SPECS["docs_build"])
    if all_tests or re.search(
        r"\b(ets|e2etest|contract test|unit test|integration test|flak(?:e|y|iness))\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["ets_test"])
    if re.search(r"\b(kiss|refactor|clean\s*up|decouple|simplify|dedup)\b", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["cleanup"])
    if re.search(
        r"\b(perf(?:ormance)?|avoid extra|reuse .* buffer|pool .* buffer|zero-copy|latency|throughput|optimize)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["perf"])
    if re.search(r"\b(update gmscore|update image rev|update to \d|build lavapipe)\b", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["docs_build"])
    if re.search(
        r"\b(fix(?:es|ed|ing)?|crash|race|thread-safe|non-blocking|leak|uaf|deadlock|reject|prevent|workaround)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    if re.search(
        r"\b(add|enable|support|redesign|implement|introduce|prototype|install)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["feature"])
    if has_bugs:
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    return dict(COMMIT_TYPE_SPECS["feature"])


def parse_gerrit_timestamp(raw_ts: str | None) -> datetime | None:
    """Parses a Gerrit UTC timestamp (`YYYY-MM-DD HH:MM:SS.fffffffff`) into a timezone-aware datetime."""
    if not raw_ts:
        return None
    cleaned = str(raw_ts).strip()
    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_agent_or_bot_email(email: str | None, name: str | None = None) -> bool:
    """Returns True if the account belongs to an AI agent or automated CI/lint bot."""
    lowered_email = (email or "").strip().lower()
    lowered_name = (name or "").strip().lower()
    if not lowered_email and not lowered_name:
        return True
    agent_prefixes = (
        "groupagent-",
        "corpagent-",
        "android-build-",
        "android-crystalball",
        "treehugger",
        "ayeaye",
        "gerrit",
        "autosubmit",
    )
    if any(lowered_email.startswith(p) for p in agent_prefixes):
        return True
    if any(tok in lowered_email for tok in ("-presubmit", "crystalball", "automerger", "robot")):
        return True
    if lowered_email.endswith("@system.gserviceaccount.com"):
        return True
    return "robot" in lowered_name or "bot" in lowered_name or "presubmit" in lowered_name


def is_team_review_agent(email: str | None) -> bool:
    """Returns True if the email belongs to our Android Emulator review agent."""
    lowered = (email or "").strip().lower()
    return lowered.startswith("groupagent-android-emulator")


def is_explicit_reviewer(change: dict[str, Any], viewer_email: str) -> bool:
    """Returns True if `viewer_email` is explicitly listed in `reviewers.REVIEWER` (excluding CC-only)."""
    viewer = (viewer_email or "").strip().lower()
    if not viewer:
        return True
    reviewers_block = change.get("reviewers") or {}
    if not reviewers_block:
        return True
    explicit_reviewers = reviewers_block.get("REVIEWER") or []
    return any((r.get("email") or "").strip().lower() == viewer for r in explicit_reviewers)


def build_viewer_gerrit_query(viewer_email: str, scope: str = "reviewer") -> str:
    """Builds the Gerrit search query for the active `viewer_email` and `scope`."""
    viewer = (viewer_email or "").strip().lower()
    norm_scope = (scope or "reviewer").strip().lower()
    if norm_scope == "owner" and viewer:
        return f"status:open -is:wip owner:{viewer}"
    if norm_scope == "team" or not viewer:
        return DEFAULT_EMULATOR_GERRIT_QUERY
    return f"status:open -is:wip reviewer:{viewer} -owner:{viewer}"


def is_agent_authored_or_uploaded(change: dict[str, Any]) -> bool:
    """Determines whether a CL was authored or uploaded by an agent (requiring 2x human +2)."""
    owner = change.get("owner") or {}
    if is_agent_or_bot_email(owner.get("email"), owner.get("name")):
        return True
    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    uploader = rev_obj.get("uploader") or rev_obj.get("commit", {}).get("committer") or {}
    if uploader and is_agent_or_bot_email(uploader.get("email"), uploader.get("name")):
        return True
    for req in change.get("submit_requirements") or []:
        if req.get("name") == "Review-Enforcement" and req.get("status") == "UNSATISFIED":
            return True
    return False


def _extract_human_votes(change: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Extracts human CR+2 voters, human CR+1 voters, and whether our team agent voted CR+1."""
    cr_label = (change.get("labels") or {}).get("Code-Review") or {}
    all_votes = cr_label.get("all") or []
    human_cr2: list[dict[str, Any]] = []
    human_cr1: list[dict[str, Any]] = []
    agent_cr1 = False

    for vote in all_votes:
        val = int(vote.get("value", 0) or 0)
        email = (vote.get("email") or "").strip().lower()
        name = vote.get("name") or email.split("@", 1)[0]
        if is_team_review_agent(email) and val >= 1:
            agent_cr1 = True
        if is_agent_or_bot_email(email, name):
            continue
        entry = {"email": email, "name": name, "short": email.split("@", 1)[0], "value": val}
        if val >= 2:
            human_cr2.append(entry)
        elif val == 1:
            human_cr1.append(entry)

    return human_cr2, human_cr1, agent_cr1


def _extract_human_involvement(
    change: dict[str, Any],
    viewer_email: str,
    human_cr2: list[dict[str, Any]],
    now_dt: datetime,
) -> list[dict[str, Any]]:
    """Summarizes awareness and activity of each human reviewer attached to the CL."""
    cur_ps = int(change.get("current_revision_number", 1) or 1)
    cr2_emails = {v["email"] for v in human_cr2}
    attention_set = change.get("attention_set") or {}
    attention_by_email: dict[str, dict[str, Any]] = {}
    for acct_info in attention_set.values():
        acct = acct_info.get("account") or {}
        em = (acct.get("email") or "").strip().lower()
        if em:
            attention_by_email[em] = acct_info

    earlier_ps_active_emails: set[str] = set()
    commented_emails: set[str] = set()
    for msg in change.get("messages") or []:
        author = msg.get("author") or {}
        em = (author.get("email") or "").strip().lower()
        if not em or is_agent_or_bot_email(em, author.get("name")):
            continue
        ps_num = int(msg.get("_revision_number", 0) or 0)
        text = msg.get("message") or ""
        if "Code-Review+2" in text or "Code-Review+1" in text:
            if ps_num < cur_ps:
                earlier_ps_active_emails.add(em)
        if " comment" in text or len(text.splitlines()) > 1:
            commented_emails.add(em)

    owner_email = ((change.get("owner") or {}).get("email") or "").strip().lower()
    reviewers_raw = (change.get("reviewers") or {}).get("REVIEWER") or []
    involvement: list[dict[str, Any]] = []
    seen_emails: set[str] = set()

    for rev in reviewers_raw:
        em = (rev.get("email") or "").strip().lower()
        name = rev.get("name") or em.split("@", 1)[0]
        if not em or em == owner_email or is_agent_or_bot_email(em, name) or em in seen_emails:
            continue
        seen_emails.add(em)
        short = em.split("@", 1)[0]
        if em in cr2_emails:
            continue
        if em in earlier_ps_active_emails:
            involvement.append({
                "email": em,
                "short": short,
                "state": "active_prev_ps",
                "label": f"✓ {short} (Voted on earlier PS · needs re-stamp)",
            })
            continue
        if em in commented_emails:
            involvement.append({
                "email": em,
                "short": short,
                "state": "commented",
                "label": f"✓ {short} (Active in comments)",
            })
            continue
        att = attention_by_email.get(em)
        if att:
            att_dt = parse_gerrit_timestamp(att.get("last_update"))
            silent_h = max(0.0, (now_dt - att_dt).total_seconds() / 3600.0) if att_dt else 0.0
            silent_str = f"{silent_h / 24.0:.1f}d" if silent_h >= 24 else f"{max(1, int(silent_h))}h"
            involvement.append({
                "email": em,
                "short": short,
                "state": "silent_attention",
                "silent_hours": round(silent_h, 1),
                "label": f"! @{short} (Silent {silent_str} in Attention Set)",
            })
        else:
            involvement.append({
                "email": em,
                "short": short,
                "state": "not_in_attention",
                "label": f"@{short} (Reviewer · not in Attention Set)",
            })

    return involvement


def classify_risk_and_load(change: dict[str, Any]) -> dict[str, Any]:
    """Classifies a CL into Low, Medium, or High cognitive load / risk."""
    subject = (change.get("subject") or "").strip()
    insertions = int(change.get("insertions", 0) or 0)
    deletions = int(change.get("deletions", 0) or 0)
    total_lines = insertions + deletions

    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    files_map = rev_obj.get("files") or {}
    file_paths = [p for p in files_map.keys() if p != "/COMMIT_MSG"]

    all_docs = bool(file_paths) and all(p.endswith((".md", ".txt", "OWNERS")) for p in file_paths)
    if all_docs or subject.lower().startswith("docs"):
        return {
            "tier": "low",
            "label": f"LOW LOAD · docs · {total_lines}L",
            "total_lines": total_lines,
            "reasons": ["docs only"],
        }

    all_tests = bool(file_paths) and all(
        p.endswith(("_test.cc", "_test.cpp", "_test.py", ".spec.ts", ".test.ts"))
        for p in file_paths
    )
    if all_tests:
        return {
            "tier": "low",
            "label": f"LOW LOAD · tests · {total_lines}L",
            "total_lines": total_lines,
            "reasons": ["tests only"],
        }

    high_reasons: list[str] = []
    low_reasons: list[str] = []

    if _HIGH_RISK_SUBJECT_RE.search(subject):
        high_reasons.append("feat/security")
    if any(_HIGH_RISK_PATH_RE.search(p) for p in file_paths):
        high_reasons.append("proto/header/concurrency")
    if total_lines >= HIGH_RISK_MIN_LINES:
        high_reasons.append(f"{total_lines}L diff")

    if high_reasons:
        return {
            "tier": "high",
            "label": f"HIGH RISK · {' · '.join(high_reasons[:2])} · {total_lines}L",
            "total_lines": total_lines,
            "reasons": high_reasons,
        }

    if _LOW_RISK_SUBJECT_RE.search(subject):
        low_reasons.append("style/test/KISS")
    if total_lines <= LOW_RISK_MAX_LINES and len(file_paths) <= 2:
        low_reasons.append(f"{total_lines}L")

    if low_reasons and total_lines <= 120:
        return {
            "tier": "low",
            "label": f"LOW LOAD · {total_lines}L",
            "total_lines": total_lines,
            "reasons": low_reasons,
        }

    return {
        "tier": "medium",
        "label": f"MED LOAD · {total_lines}L",
        "total_lines": total_lines,
        "reasons": [f"{total_lines}L"],
    }


def enrich_change_for_ui(
    change: dict[str, Any],
    viewer_email: str,
    now_dt: datetime | None = None,
    bug_lookup: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Computes 2x-human approval slots, vetted gates, Cliff Radar telemetry, and risk tier."""
    now = now_dt or datetime.now(timezone.utc)
    viewer = (viewer_email or "").strip().lower()
    num = int(change.get("_number", 0) or 0)
    project = change.get("project", "")
    project_short = project.split("/")[-1] if project else ""
    subject = change.get("subject", "")
    owner = change.get("owner") or {}
    owner_email = (owner.get("email") or "").strip().lower()
    owner_short = owner_email.split("@", 1)[0] if owner_email else "unknown"

    requires_two_humans = is_agent_authored_or_uploaded(change)
    required_cr2_count = 2 if requires_two_humans else 1
    human_cr2, human_cr1, agent_cr1 = _extract_human_votes(change)
    approved_by_viewer = any(v["email"] == viewer for v in human_cr2)

    pv_label = (change.get("labels") or {}).get("Presubmit-Verified") or {}
    pv_score = 0
    for v in pv_label.get("all") or []:
        val = int(v.get("value", 0) or 0)
        if val < 0:
            pv_score = min(pv_score, val)
        elif val > pv_score and pv_score >= 0:
            pv_score = val

    unresolved_count = int(change.get("unresolved_comment_count", 0) or 0)
    presubmit_vetted = pv_score >= 1
    agent_vetted = (agent_cr1 or not requires_two_humans) and unresolved_count == 0

    involvement = _extract_human_involvement(change, viewer, human_cr2, now)
    slots: list[dict[str, str]] = []
    for voter in human_cr2[:required_cr2_count]:
        tag = "You (+2 ✓)" if voter["email"] == viewer else f"✓ {voter['short']} +2"
        slots.append({"state": "done", "text": tag})

    remaining_slots = max(0, required_cr2_count - len(human_cr2))
    if remaining_slots > 0:
        if not approved_by_viewer and owner_email != viewer:
            slots.append({"state": "needed", "text": f"→ Slot {len(slots) + 1}: Needs Your +2"})
            remaining_slots -= 1
        while remaining_slots > 0:
            slot_num = len(slots) + 1
            ord_str = "1st" if slot_num == 1 else ("2nd" if slot_num == 2 else f"{slot_num}th")
            slots.append({"state": "needed", "text": f"→ Needs {ord_str} Human +2"})
            remaining_slots -= 1

    created_dt = parse_gerrit_timestamp(change.get("created"))
    updated_dt = parse_gerrit_timestamp(change.get("updated"))
    hours_in_flight = max(0.0, (now - created_dt).total_seconds() / 3600.0) if created_dt else 0.0
    hours_idle = max(0.0, (now - updated_dt).total_seconds() / 3600.0) if updated_dt else 0.0
    mergeable = change.get("mergeable", True) is not False

    if not mergeable or hours_in_flight >= CLIFF_THRESHOLD_HOURS:
        cliff_zone = "cliff"
    elif hours_in_flight >= FRESH_THRESHOLD_HOURS:
        cliff_zone = "aging"
    else:
        cliff_zone = "fresh"

    flight_str = (
        f"{hours_in_flight / 24.0:.1f}d"
        if hours_in_flight >= 24.0
        else f"{max(1, int(round(hours_in_flight)))}h"
    )
    risk = classify_risk_and_load(change)
    bug_ids = extract_bug_ids_from_change(change)
    commit_type = classify_commit_type(change, has_bugs=bool(bug_ids))
    bugs_list: list[dict[str, str]] = []
    for bid in bug_ids:
        cached_meta = (bug_lookup or {}).get(bid)
        if cached_meta:
            bugs_list.append(dict(cached_meta))
        else:
            bugs_list.append({
                "id": bid,
                "url": f"https://b.corp.google.com/issues/{bid}",
                "priority": "",
                "severity": "",
                "status": "",
                "title": "",
            })

    other_humans = [h for h in involvement if h["email"] != viewer]
    is_orphaned = requires_two_humans and (len(human_cr2) + len(other_humans) < 2)
    silent_targets = [h["short"] for h in other_humans if h["state"] in ("silent_attention", "not_in_attention", "active_prev_ps")]
    ping_target = f"@{silent_targets[0]}" if silent_targets else "team"
    chat_ping = (
        f"Hey {ping_target}, could you take a quick look at ag/{num} "
        f"(\"{subject}\", {risk['tier']} load, presubmit+agent vetted)? "
        f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{num}"
    )

    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    commit_obj = rev_obj.get("commit") or {}
    raw_commit_msg = (commit_obj.get("message") or "").strip()
    commit_message = raw_commit_msg if raw_commit_msg else subject
    parents = commit_obj.get("parents") or []
    parent_sha = parents[0].get("commit", "") if parents else ""

    return {
        "number": num,
        "change_id": change.get("change_id", ""),
        "project": project,
        "project_short": project_short,
        "branch": change.get("branch", ""),
        "topic": change.get("topic", ""),
        "subject": subject,
        "commit_message": commit_message,
        "owner_email": owner_email,
        "owner_short": owner_short,
        "is_agent_cl": requires_two_humans,
        "required_cr2_count": required_cr2_count,
        "human_cr2_count": len(human_cr2),
        "human_cr2": human_cr2,
        "human_cr1": human_cr1,
        "approved_by_viewer": approved_by_viewer,
        "presubmit_score": pv_score,
        "presubmit_vetted": presubmit_vetted,
        "agent_vetted": agent_vetted,
        "unresolved_comment_count": unresolved_count,
        "patch_set": int(change.get("current_revision_number", 1) or 1),
        "current_revision": cur_rev,
        "parent_revision": parent_sha,
        "hours_in_flight": round(hours_in_flight, 1),
        "hours_idle": round(hours_idle, 1),
        "flight_display": flight_str,
        "mergeable": mergeable,
        "cliff_zone": cliff_zone,
        "risk": risk,
        "commit_type": commit_type,
        "bug_ids": bug_ids,
        "bugs": bugs_list,
        "approval_slots": slots,
        "human_involvement": other_humans,
        "is_orphaned": is_orphaned,
        "chat_ping": chat_ping,
        "swarm_status": read_cl_swarm_status(num, current_revision=cur_rev, now_dt=now),
        "url": f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{num}",
    }


def group_changes_into_stacks(enriched_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups enriched CLs into ordered multi-commit stacks by topic or parent-child revision links."""
    by_rev: dict[str, dict[str, Any]] = {
        c["current_revision"]: c for c in enriched_changes if c.get("current_revision")
    }
    children_of: dict[str, list[dict[str, Any]]] = {}
    for c in enriched_changes:
        p_sha = c.get("parent_revision", "")
        if p_sha and p_sha in by_rev:
            children_of.setdefault(p_sha, []).append(c)

    visited: set[int] = set()
    stacks: list[dict[str, Any]] = []

    # 1. Group explicit topics first
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for c in enriched_changes:
        topic = (c.get("topic") or "").strip()
        if topic:
            by_topic.setdefault(topic, []).append(c)

    for topic, items in by_topic.items():
        if len(items) < 2:
            continue
        ordered = _order_chain(items)
        for idx, item in enumerate(ordered, start=1):
            item["stack_index"] = idx
            item["stack_total"] = len(ordered)
            item["stack_label"] = f"topic:{topic}"
            visited.add(item["number"])
        stacks.append({
            "stack_id": f"topic:{topic}",
            "title": f"STACK · topic:{topic} ({len(ordered)} Commits · {ordered[0]['project_short']})",
            "is_stack": True,
            "commits": ordered,
        })

    # 2. Group remaining parent-child git chains
    for c in enriched_changes:
        if c["number"] in visited:
            continue
        p_sha = c.get("parent_revision", "")
        if p_sha in by_rev and by_rev[p_sha]["number"] not in visited:
            continue
        chain = [c]
        cur = c
        while cur.get("current_revision") in children_of:
            nxt_list = [
                ch for ch in children_of[cur["current_revision"]] if ch["number"] not in visited
            ]
            if not nxt_list:
                break
            nxt = nxt_list[0]
            chain.append(nxt)
            cur = nxt
        for idx, item in enumerate(chain, start=1):
            item["stack_index"] = idx
            item["stack_total"] = len(chain)
            item["stack_label"] = f"chain:{chain[0]['number']}" if len(chain) > 1 else ""
            visited.add(item["number"])
        stacks.append({
            "stack_id": f"chain:{chain[0]['number']}",
            "title": (
                f"STACK · Relation Chain ({len(chain)} Commits · {chain[0]['project_short']})"
                if len(chain) > 1
                else f"ag/{chain[0]['number']} · {chain[0]['project_short']}"
            ),
            "is_stack": len(chain) > 1,
            "commits": chain,
        })

    stacks.sort(key=lambda s: (0 if len(s["commits"]) > 1 else 1, -len(s["commits"])))
    return stacks


def _order_chain(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orders commits in a stack from base commit (1/N) to tip commit (N/N)."""
    by_rev = {c["current_revision"]: c for c in items if c.get("current_revision")}
    roots = [c for c in items if c.get("parent_revision") not in by_rev]
    if not roots:
        return sorted(items, key=lambda x: x["number"])
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    for root in sorted(roots, key=lambda x: x["number"]):
        cur: dict[str, Any] | None = root
        while cur and cur["number"] not in seen:
            ordered.append(cur)
            seen.add(cur["number"])
            cur_rev = cur.get("current_revision", "")
            nxt = next(
                (x for x in items if x.get("parent_revision") == cur_rev and x["number"] not in seen),
                None,
            )
            cur = nxt
    for item in sorted(items, key=lambda x: x["number"]):
        if item["number"] not in seen:
            ordered.append(item)
    return ordered


def _extract_diff_git_path(diff_line: str) -> str:
    """Extracts the destination relative file path from a `diff --git` header line."""
    if ' "b/' in diff_line:
        quoted = '"' + diff_line.split(' "b/', 1)[1].strip()
        return unquote_git_diff_path(f'"b/{quoted[1:]}')
    parts = diff_line.split(" b/", 1)
    return parts[1].strip() if len(parts) == 2 else diff_line


def extract_commit_message_from_patch(raw_diff: str) -> str:
    """Extracts the full commit message (subject + body + footers) from a git format-patch."""
    if not raw_diff:
        return ""
    lines = raw_diff.splitlines()
    subject_parts: list[str] = []
    in_headers = False
    in_subject = False
    body_start_idx: int | None = None

    for idx, line in enumerate(lines):
        if idx == 0 and (line.startswith("From ") or line.startswith("From:") or line.startswith("Subject:")):
            in_headers = True
        if not in_headers:
            break
        if line == "":
            body_start_idx = idx + 1
            break
        if line.startswith("Subject:"):
            in_subject = True
            val = line[len("Subject:"):].strip()
            val = re.sub(r"^\[PATCH[^\]]*\]\s*", "", val)
            subject_parts.append(val)
        elif in_subject and line.startswith((" ", "\t")):
            subject_parts.append(line.strip())
        else:
            in_subject = False

    if body_start_idx is None:
        return ""

    body_lines: list[str] = []
    for line in lines[body_start_idx:]:
        if line == "---" or line.startswith("diff --git "):
            break
        body_lines.append(line)

    subject_line = " ".join(p for p in subject_parts if p).strip()
    body_text = "\n".join(body_lines).strip()
    if subject_line and body_text:
        return f"{subject_line}\n\n{body_text}"
    return subject_line or body_text



def parse_unified_diff_files(
    raw_diff: str, max_lines_per_file: int = DEFAULT_MAX_DIFF_LINES_PER_FILE
) -> list[dict[str, Any]]:
    """Parses a unified git patch into per-file structured diff lines for UI rendering."""
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    old_ln = 0
    new_ln = 0

    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            current_file = {
                "file": _extract_diff_git_path(line),
                "lines": [],
                "additions": 0,
                "deletions": 0,
                "truncated": False,
            }
            files.append(current_file)
            continue
        if current_file is None:
            continue
        if line.startswith("+++ "):
            cand = unquote_git_diff_path(line[4:])
            if cand and cand != "/dev/null":
                current_file["file"] = cand
            continue
        if line.startswith(("index ", "--- ", "new file mode", "deleted file mode")):
            continue
        if line.startswith(("rename from ", "rename to ", "similarity index ", "Binary files ")):
            if len(current_file["lines"]) < max_lines_per_file:
                current_file["lines"].append({"type": "hunk", "old": "", "new": "", "text": line})
            continue
        if line.startswith("@@ "):
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
            if m:
                old_ln = int(m.group(1))
                new_ln = int(m.group(2))
            if len(current_file["lines"]) < max_lines_per_file:
                current_file["lines"].append({"type": "hunk", "old": "", "new": "", "text": line})
            continue

        if len(current_file["lines"]) >= max_lines_per_file:
            current_file["truncated"] = True
            continue

        if line.startswith("+"):
            current_file["additions"] += 1
            current_file["lines"].append({"type": "add", "old": "", "new": str(new_ln), "text": line})
            new_ln += 1
        elif line.startswith("-"):
            current_file["deletions"] += 1
            current_file["lines"].append({"type": "del", "old": str(old_ln), "new": "", "text": line})
            old_ln += 1
        else:
            current_file["lines"].append({"type": "ctx", "old": str(old_ln), "new": str(new_ln), "text": line})
            old_ln += 1
            new_ln += 1

    return files


class GerritUIService:
    """Backend coordinator querying Gerrit, caching diffs, and executing review/harness actions."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
    ) -> None:
        self.host = host.rstrip("/")
        self.client = GerritClient(host=self.host)
        self.auth_email = self._detect_auth_email()
        self.default_viewer = default_viewer or self._detect_viewer_email()
        self.default_query = default_query
        self._session_approved_cls: set[int] = set()
        self._bug_cache_path = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "bug_metadata_cache.json"
        )
        self._bug_cache: dict[str, dict[str, str]] = self._load_bug_cache()

    def _load_bug_cache(self) -> dict[str, dict[str, str]]:
        """Loads cached Buganizer priority/severity metadata from disk if available."""
        try:
            if self._bug_cache_path.is_file():
                raw = json.loads(self._bug_cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {
                        str(k): v
                        for k, v in raw.items()
                        if isinstance(v, dict) and v.get("id")
                    }
        except Exception:
            pass
        return {}

    def _save_bug_cache(self) -> None:
        """Persists cached Buganizer priority/severity metadata to disk."""
        try:
            self._bug_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._bug_cache_path.write_text(
                json.dumps(self._bug_cache, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def fetch_bug_metadata_batch(
        self, bug_ids: list[str], issues_bin: str = DEFAULT_ISSUES_CLI_BIN
    ) -> dict[str, dict[str, str]]:
        """Batch-fetches Buganizer `priority`, `severity`, `status`, and `title` for `bug_ids`."""
        unique_ids = [
            str(b).strip()
            for b in dict.fromkeys(bug_ids)
            if str(b).strip().isdigit()
        ]
        missing = [bid for bid in unique_ids if bid not in self._bug_cache]
        if missing and os.path.exists(issues_bin):
            query = f"id:({' | '.join(missing[:80])})"
            try:
                res = subprocess.run(
                    [
                        issues_bin,
                        "readonly",
                        "search",
                        query,
                        "--fields",
                        "issue_id,priority,severity,status,title",
                        "--limit",
                        "100",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=BUG_LOOKUP_TIMEOUT_SEC,
                )
                if res.returncode == 0 and res.stdout:
                    fetched = parse_issues_search_output(res.stdout)
                    if fetched:
                        self._bug_cache.update(fetched)
                        self._save_bug_cache()
            except Exception:
                pass
        return {
            bid: self._bug_cache.get(
                bid,
                {
                    "id": bid,
                    "url": f"https://b.corp.google.com/issues/{bid}",
                    "priority": "",
                    "severity": "",
                    "status": "",
                    "title": "",
                },
            )
            for bid in unique_ids
        }

    def _detect_auth_email(self) -> str:
        """Returns the Gerrit account email that `gob-curl` authenticates as on this host."""
        try:
            acct = self.client._request_json("/a/accounts/self")
            return (acct.get("email") or "").strip().lower()
        except Exception:
            return ""

    def _detect_viewer_email(self) -> str:
        """Detects the logged-in engineer's email from env, Gerrit `/a/accounts/self`, git config, or capsule owner."""
        env_viewer = os.environ.get("AEMU_GERRIT_VIEWER", "").strip().lower()
        if env_viewer and not is_agent_or_bot_email(env_viewer):
            return env_viewer
        if self.auth_email and not is_agent_or_bot_email(self.auth_email):
            return self.auth_email
        try:
            res = subprocess.run(
                ["git", "config", "user.email"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            email = (res.stdout or "").strip().lower()
            if email and not is_agent_or_bot_email(email):
                return email
        except Exception:
            pass
        try:
            hostname_short = socket.gethostname().split(".")[0].strip()
            if hostname_short.startswith("cpl-"):
                res = subprocess.run(
                    ["cpl", "list"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                for line in (res.stdout or "").splitlines():
                    if hostname_short in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            owner_cand = f"{parts[3].strip().lower()}@google.com"
                            if not is_agent_or_bot_email(owner_cand):
                                return owner_cand
        except Exception:
            pass
        return "joshuaduong@google.com"

    def fetch_queue(
        self,
        viewer_email: str | None = None,
        query: str | None = None,
        scope: str = "reviewer",
        limit: int = 40,
    ) -> dict[str, Any]:
        """Fetches open CLs matching the viewer's review queue (or custom `query`) and enriches them."""
        viewer = (viewer_email or self.default_viewer).strip().lower()
        norm_scope = (scope or "reviewer").strip().lower()
        explicit_query = (query or self.default_query).strip()
        q_str = explicit_query or build_viewer_gerrit_query(viewer, scope=norm_scope)
        encoded_q = urllib.parse.quote(q_str, safe=":+()")
        endpoint = (
            f"/a/changes/?q={encoded_q}&n={limit}"
            "&o=CURRENT_REVISION&o=CURRENT_COMMIT&o=CURRENT_FILES"
            "&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS&o=MESSAGES&o=SUBMIT_REQUIREMENTS"
        )
        raw_changes = self.client._request_json(endpoint)
        if not isinstance(raw_changes, list):
            raw_changes = []

        if not explicit_query and norm_scope == "reviewer" and viewer:
            raw_changes = [ch for ch in raw_changes if is_explicit_reviewer(ch, viewer)]

        all_bug_ids: list[str] = []
        for ch in raw_changes:
            all_bug_ids.extend(extract_bug_ids_from_change(ch))
        bug_lookup = self.fetch_bug_metadata_batch(all_bug_ids) if all_bug_ids else {}

        now_dt = datetime.now(timezone.utc)
        enriched = [
            enrich_change_for_ui(ch, viewer, now_dt=now_dt, bug_lookup=bug_lookup)
            for ch in raw_changes
        ]
        self._apply_session_approvals(enriched, viewer)
        stacks = group_changes_into_stacks(enriched)

        cliff_summary = {
            "fresh": [
                c
                for c in enriched
                if c["cliff_zone"] == "fresh"
                and c["presubmit_vetted"]
                and not c["approved_by_viewer"]
            ],
            "aging": [
                c
                for c in enriched
                if c["cliff_zone"] == "aging"
                and c["presubmit_vetted"]
                and not c["approved_by_viewer"]
            ],
            "cliff": [
                c
                for c in enriched
                if c["cliff_zone"] == "cliff" and not c["approved_by_viewer"]
            ],
        }
        return {
            "ui_version": get_gerrit_ui_template_version(),
            "viewer": viewer,
            "auth_email": self.auth_email,
            "can_vote_cr2": not is_agent_or_bot_email(self.auth_email) if self.auth_email else True,
            "scope": norm_scope,
            "roster": list(TEAM_ROSTER_EMAILS),
            "query": q_str,
            "total_fetched": len(enriched),
            "changes": enriched,
            "stacks": stacks,
            "cliff_radar": cliff_summary,
        }

    def _apply_session_approvals(
        self, enriched: list[dict[str, Any]], viewer_email: str
    ) -> None:
        """Applies in-memory `Code-Review +2` votes when Gerrit's secondary search index lags."""
        if not self._session_approved_cls:
            return
        active_voter = (self.auth_email or self.default_viewer or "").strip().lower()
        viewer = (viewer_email or "").strip().lower()
        if active_voter and viewer and active_voter != viewer:
            return
        short = (viewer or "you").split("@", 1)[0]
        for item in enriched:
            if int(item.get("number", 0) or 0) not in self._session_approved_cls:
                continue
            if item.get("approved_by_viewer"):
                continue
            item["approved_by_viewer"] = True
            cr2_list = item.setdefault("human_cr2", [])
            if not any((v.get("email") or "").lower() == viewer for v in cr2_list):
                cr2_list.append({"email": viewer, "name": short, "short": short, "value": 2})
                item["human_cr2_count"] = len(cr2_list)
            for slot in item.get("approval_slots") or []:
                if slot.get("state") != "done":
                    slot["state"] = "done"
                    slot["text"] = "You (+2 ✓)"
                    break

    def fetch_commit_diff_bundle(self, cl_number: str | int) -> dict[str, Any]:
        """Fetches decoded unified diff files, pure-rebase status, and comment threads for one CL."""
        cl_id = str(cl_number).strip()
        decoded_patch = decode_gerrit_patch(self.client.get_revision_patch(cl_id, "current"))

        files = parse_unified_diff_files(decoded_patch)
        commit_message = extract_commit_message_from_patch(decoded_patch)
        threads = [t.to_dict() for t in self.client.get_comment_threads(cl_id, include_resolved=False)]

        is_pure_rebase = False
        try:
            detail = self.client.get_change_detail(cl_id, include_files=False)
            cur_ps = int(detail.get("current_revision_number", 1) or 1)
            if cur_ps > 1:
                interdiff = self.client._request_json(
                    f"/a/changes/{cl_id}/revisions/current/files?base={cur_ps - 1}"
                )
                if isinstance(interdiff, dict):
                    non_msg = [k for k in interdiff.keys() if k != "/COMMIT_MSG"]
                    is_pure_rebase = len(non_msg) == 0
        except Exception:
            is_pure_rebase = False

        return {
            "number": int(cl_id) if cl_id.isdigit() else cl_id,
            "commit_message": commit_message,
            "is_pure_rebase": is_pure_rebase,
            "files": files,
            "unresolved_threads": threads,
        }

    def fetch_multi_commit_diffs(self, cl_numbers: list[str | int]) -> list[dict[str, Any]]:
        """Fetches diff bundles concurrently for a list of CLs in a stack or queue."""
        cleaned = [str(x).strip() for x in cl_numbers if str(x).strip()]
        if not cleaned:
            return []
        with ThreadPoolExecutor(max_workers=min(DIFF_FETCH_WORKERS, len(cleaned))) as pool:
            return list(pool.map(self.fetch_commit_diff_bundle, cleaned))

    def _post_single_vote(
        self,
        gob_curl: str,
        cl_id: str,
        payload_json: str,
        code_review: int,
    ) -> dict[str, Any]:
        """Posts a single `gob-curl` review payload with `notify: OWNER` for one CL."""
        url = f"{self.client.api_host}/a/changes/{cl_id}/revisions/current/review"
        res = subprocess.run(
            [
                gob_curl,
                "-s",
                "-f",
                "-S",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                payload_json,
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=GERRIT_CLI_TIMEOUT_SEC,
        )
        err_msg = (res.stderr or "").strip() if res.returncode != 0 else ""
        if (
            res.returncode != 0
            and self.auth_email
            and is_agent_or_bot_email(self.auth_email)
            and int(code_review) >= 2
        ):
            err_msg = (
                f"Capsule backend is authenticated as role account '{self.auth_email}' "
                f"(restricted to Code-Review +1). Open ag/{cl_id} in Gerrit or run the copied "
                "Cloudtop command to cast your personal +2."
            )
        return {
            "cl": cl_id,
            "ok": res.returncode == 0,
            "error": err_msg,
        }

    def cast_batch_vote(
        self,
        cl_numbers: list[str | int],
        code_review: int = 2,
        autosubmit: bool = False,
        presubmit_ready: bool = False,
        message: str = "",
    ) -> dict[str, Any]:
        """Casts labels across one or more CLs using `gob-curl` with mandatory `notify: OWNER`."""
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot cast Gerrit vote: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        labels: dict[str, int] = {"Code-Review": int(code_review)}
        if autosubmit:
            labels["Autosubmit"] = 1
        if presubmit_ready:
            labels["Presubmit-Ready"] = 1

        payload: dict[str, Any] = {
            "labels": labels,
            "notify": "OWNER",
        }
        if message.strip():
            payload["message"] = message.strip()

        cleaned_cls = [parse_cl_identifier(str(raw_cl), allow_head=False) for raw_cl in cl_numbers]
        payload_json = json.dumps(payload)
        workers = max(1, min(DIFF_FETCH_WORKERS, len(cleaned_cls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda cid: self._post_single_vote(
                        gob_curl, cid, payload_json, int(code_review)
                    ),
                    cleaned_cls,
                )
            )

        if int(code_review) >= 2:
            for r in results:
                raw_cl_num = str(r.get("cl") or "").strip()
                if r.get("ok") and raw_cl_num.isdigit():
                    self._session_approved_cls.add(int(raw_cl_num))

        all_ok = all(r["ok"] for r in results)
        cloudtop_cmd = " && ".join(
            f"gob-curl -s -X POST -H 'Content-Type: application/json' -d '{payload_json}' "
            f"'{self.client.api_host}/a/changes/{cid}/revisions/current/review' >/dev/null"
            for cid in cleaned_cls
        )
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "partial_error",
            "auth_email": self.auth_email,
            "cloudtop_cmd": cloudtop_cmd,
            "results": results,
        }
        if not all_ok:
            first_err = next((r["error"] for r in results if not r["ok"] and r.get("error")), "Gerrit vote failed")
            out["error"] = first_err
        return out

    def assign_second_human_reviewer(self, cl_number: str | int, reviewer_email: str) -> dict[str, Any]:
        """Adds a 2nd human reviewer to an agent CL with `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        reviewer = (reviewer_email or "").strip()
        if not reviewer:
            raise ValueError("Reviewer email is required to assign a 2nd human reviewer.")
        resp = self.client._request_json(
            f"/a/changes/{cl_id}/reviewers",
            method="POST",
            data={"reviewer": reviewer, "notify": "OWNER"},
        )
        return {"status": "ok", "cl": cl_id, "reviewer": reviewer, "response": resp}

    def get_swarm_statuses(self, cl_numbers: list[str | int]) -> dict[str, dict[str, Any]]:
        """Reads live or completed Review Swarm status objects from disk for `cl_numbers`."""
        now_dt = datetime.now(timezone.utc)
        statuses: dict[str, dict[str, Any]] = {}
        for raw_cl in cl_numbers:
            cleaned = str(raw_cl).strip()
            if not cleaned:
                continue
            status_obj = read_cl_swarm_status(cleaned, now_dt=now_dt)
            if status_obj is not None:
                statuses[cleaned] = status_obj
        return statuses

    def post_swarm_review(self, cl_number: str | int) -> dict[str, Any]:
        """Posts saved `gerrit_review_payload.json` comments for `cl_number` with `notify: OWNER`."""
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot post Review Swarm payload: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        swarm_dir = resolve_swarm_dir_for_cl(cl_id)
        payload_path = (swarm_dir / GERRIT_REVIEW_PAYLOAD_FILENAME) if swarm_dir else None
        if payload_path is None or not payload_path.is_file():
            raise ValueError(
                f"No completed Review Swarm payload found for ag/{cl_id} "
                f"(expected '{GERRIT_REVIEW_PAYLOAD_FILENAME}'). Run Review Swarm first."
            )
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["notify"] = "OWNER"
        post_res = self._post_single_vote(gob_curl, cl_id, json.dumps(payload), code_review=0)
        return {
            "status": "ok" if post_res["ok"] else "error",
            "cl": cl_id,
            "error": post_res.get("error", ""),
        }

    def run_harness_action(self, cl_number: str | int, action: str) -> dict[str, Any]:
        """Executes a 1-click Harness capability (`review-swarm` or `address-review`) on a CL."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cli_main = Path(__file__).resolve().parents[2] / "__main__.py"
        if action == "review-swarm":
            cmd = [sys.executable, str(cli_main), "--json", "harness", "review-swarm", "--cl", cl_id]
        elif action == "address-review":
            cmd = [sys.executable, str(cli_main), "--json", "gerrit", "resume", cl_id]
        else:
            raise ValueError(
                f"Unsupported harness action '{action}' (expected 'review-swarm' or 'address-review')."
            )
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        raw_out = (res.stdout or "").strip()
        parsed: Any = raw_out
        if raw_out:
            try:
                parsed = json.loads(raw_out)
            except json.JSONDecodeError:
                parsed = {"stdout": raw_out}
        return {
            "status": "ok" if res.returncode == 0 else "error",
            "action": action,
            "cl": cl_id,
            "exit_code": res.returncode,
            "result": parsed,
            "swarm_status": read_cl_swarm_status(cl_id),
            "stderr": (res.stderr or "").strip(),
        }


def _candidate_gerrit_ui_template_paths() -> list[Path]:
    """Builds ordered candidate paths for `templates/gerrit_ui.html`."""
    src_dir = Path(__file__).resolve().parents[2]
    home = Path.home()
    candidates = [
        src_dir / "templates" / "gerrit_ui.html",
        home / ".android" / "emu-dev-cli" / "lib" / "templates" / "gerrit_ui.html",
    ]
    cfg_file = home / ".android" / "emu-dev-cli.json"
    if not cfg_file.is_file():
        return candidates
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return candidates
    src_dirs = cfg.get("source_directories") or {}
    if not isinstance(src_dirs, dict):
        return candidates
    for root_str in src_dirs.values():
        if isinstance(root_str, str) and root_str.strip():
            root = Path(root_str.strip())
            candidates.append(
                root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html"
            )
            candidates.append(root / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html")
    return candidates


def get_gerrit_ui_template_path() -> Path:
    """Resolves the HTML template path for `emu-dev-cli gerrit ui`."""
    candidates = _candidate_gerrit_ui_template_paths()
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


def get_gerrit_ui_template_version() -> str:
    """Returns a short SHA-256 content hash of `gerrit_ui.html` for automatic UI reload detection."""
    import hashlib

    try:
        tpl_path = get_gerrit_ui_template_path()
        return hashlib.sha256(tpl_path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"



SIDECAR_PRELOAD_SCRIPT_TAG: str = '<script src="/preload.js"></script>'

SIDECAR_PRELOAD_JS: str = """(function() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  const conversationId = params.get('conversationId') || '';

  let currentWorkspaceUris = [];
  let hasInitialWorkspace = false;
  const workspaceListeners = new Set();
  let resolveInitialWorkspace;
  const initialWorkspacePromise = new Promise((resolve) => {
    resolveInitialWorkspace = resolve;
  });

  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'theme-change') {
      const vars = (event.data.payload && event.data.payload.cssVariables) || {};
      for (const [key, value] of Object.entries(vars)) {
        document.documentElement.style.setProperty(key, value);
      }
    } else if (event.data && event.data.type === 'workspace-change') {
      const payload = event.data.payload || {};
      currentWorkspaceUris = Array.isArray(payload.workspaceUris)
        ? payload.workspaceUris.slice()
        : [];
      hasInitialWorkspace = true;
      resolveInitialWorkspace();
      for (const listener of workspaceListeners) {
        try {
          listener(currentWorkspaceUris.slice());
        } catch (err) {
          console.error('workspace-change listener failed:', err);
        }
      }
    }
  });

  function secureFetch(url, options = {}) {
    let targetUrl = url;
    if (token && !/[?&]token=/.test(targetUrl)) {
      const sep = targetUrl.includes('?') ? '&' : '?';
      targetUrl = `${targetUrl}${sep}token=${encodeURIComponent(token)}`;
    }
    const headers = new Headers(options.headers || {});
    headers.set('Content-Type', 'application/json');
    if (token) {
      headers.set('X-Sidecar-Token', token);
    }
    return fetch(targetUrl, Object.assign({}, options, { headers }));
  }

  const projectIdPromise = (async () => {
    if (!conversationId) return '';
    try {
      const res = await secureFetch('/_sidecar/get-conversation-metadata', {
        method: 'POST',
        body: JSON.stringify({ conversationId })
      });
      if (!res.ok) return '';
      const data = await res.json();
      const metaEvent = (data && data.response && (data.response.conversationMetadata || data.response.conversation_metadata)) || {};
      const meta = metaEvent.metadata || {};
      return meta.projectId || meta.project_id || '';
    } catch (e) {
      return '';
    }
  })();

  async function getWorkspaceUris() {
    if (window.parent === window) {
      return [];
    }
    await initialWorkspacePromise;
    return currentWorkspaceUris.slice();
  }

  window.sidecar = {
    conversationId,
    fetch: secureFetch,
    getWorkspaceUris,
    onWorkspaceChange(listener) {
      if (typeof listener !== 'function') return () => {};
      workspaceListeners.add(listener);
      if (hasInitialWorkspace) {
        queueMicrotask(() => {
          if (workspaceListeners.has(listener)) {
            listener(currentWorkspaceUris.slice());
          }
        });
      }
      return () => { workspaceListeners.delete(listener); };
    },
    agent: {
      async sendMessage(message, convId) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/send-message', {
          method: 'POST',
          body: JSON.stringify({
            conversationId: convId || conversationId,
            message,
            projectId: resolvedProjectId
          })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async startConversation(message, title) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/new-conversation', {
          method: 'POST',
          body: JSON.stringify({ message, title, projectId: resolvedProjectId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async getConversationMetadata(convId) {
        const res = await secureFetch('/_sidecar/get-conversation-metadata', {
          method: 'POST',
          body: JSON.stringify({ conversationId: convId || conversationId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }
    },
    ui: {
      toggleAuxPane(request) {
        window.parent.postMessage({ type: 'toggle-aux-pane', payload: request }, '*');
      },
      toggleConversation(convId) {
        window.parent.postMessage({ type: 'toggle-conversation', payload: { conversationId: convId } }, '*');
      }
    }
  };
})();
"""


def render_static_gerrit_ui_html(initial_payload: dict[str, Any] | None = None) -> str:
    """Renders `gerrit_ui.html` with inlined `window.sidecar` bridge and optional JSON state."""
    tpl_path = get_gerrit_ui_template_path()
    html = tpl_path.read_text(encoding="utf-8")
    inline_preload = f"<script>\n{SIDECAR_PRELOAD_JS}\n</script>"
    html = html.replace(SIDECAR_PRELOAD_SCRIPT_TAG, inline_preload)
    html = html.replace("__GERRIT_UI_VERSION__", get_gerrit_ui_template_version())
    bootstrap_json = json.dumps(initial_payload or {}, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("__GERRIT_UI_BOOTSTRAP_JSON__", bootstrap_json)


def _run_sidecar_agentapi(args: list[str], project_id: str | None = None) -> dict[str, Any]:
    """Invokes `agentapi` for Jetski Web sidecar conversation integration."""
    agentapi_bin = shutil.which("agentapi")
    if not agentapi_bin:
        cand = Path.home() / ".gemini" / "jetski" / "bin" / "agentapi"
        if cand.is_file():
            agentapi_bin = str(cand)
        else:
            agentapi_bin = "agentapi"
    env = os.environ.copy()
    if project_id:
        env["ANTIGRAVITY_PROJECT_ID"] = project_id
    else:
        env.pop("ANTIGRAVITY_SOURCE_METADATA", None)
    res = subprocess.run(
        [agentapi_bin, *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(f"agentapi failed: {(res.stderr or '').strip()}")
    out = (res.stdout or "").strip()
    return json.loads(out) if out else {}


def make_gerrit_ui_handler(service: GerritUIService) -> type[http.server.BaseHTTPRequestHandler]:
    """Creates the HTTP request handler bound to `service`."""
    sidecar_ui_token = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN", "")

    class GerritUIHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _check_sidecar_token(self, parsed: urllib.parse.ParseResult) -> bool:
            if not sidecar_ui_token:
                return True
            if self.headers.get("X-Sidecar-Token", "") == sidecar_ui_token:
                return True
            qs = urllib.parse.parse_qs(parsed.query)
            return (qs.get("token") or [""])[0] == sidecar_ui_token

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/preload.js":
                    js = SIDECAR_PRELOAD_JS.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(js)))
                    self.end_headers()
                    self.wfile.write(js)
                    return
                if parsed.path in ("/", "/index.html"):
                    html_bytes = render_static_gerrit_ui_html().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(html_bytes)))
                    self.end_headers()
                    self.wfile.write(html_bytes)
                    return
                if parsed.path == "/api/gerrit/queue":
                    viewer = (qs.get("viewer") or [""])[0]
                    query = (qs.get("query") or [""])[0]
                    scope = (qs.get("scope") or ["reviewer"])[0]
                    data = service.fetch_queue(
                        viewer_email=viewer or None,
                        query=query or None,
                        scope=scope,
                    )
                    self._send_json(data)
                    return
                if parsed.path == "/api/gerrit/diffs":
                    raw_cls = (qs.get("cls") or [""])[0]
                    cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
                    diffs = service.fetch_multi_commit_diffs(cl_list)
                    self._send_json({"diffs": diffs})
                    return
                if parsed.path == "/api/gerrit/swarm-status":
                    raw_cls = (qs.get("cls") or [""])[0]
                    cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
                    self._send_json({"statuses": service.get_swarm_statuses(cl_list)})
                    return
                self._send_json({"error": f"Unknown endpoint '{parsed.path}'"}, status=404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if not self._check_sidecar_token(parsed):
                self._send_json({"error": "Invalid sidecar UI token"}, status=403)
                return
            try:
                body = self._read_json_body()
                if parsed.path == "/_sidecar/get-conversation-metadata":
                    conv_id = str(
                        body.get("conversationId")
                        or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                        or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                        or ""
                    ).strip()
                    if not conv_id:
                        self._send_json({"error": "Missing conversationId"}, status=400)
                        return
                    self._send_json(_run_sidecar_agentapi(["get-conversation-metadata", conv_id]))
                    return
                if parsed.path == "/_sidecar/send-message":
                    msg = str(body.get("message") or "").strip()
                    conv_id = str(
                        body.get("conversationId")
                        or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                        or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                        or ""
                    ).strip()
                    if not msg or not conv_id:
                        self._send_json({"error": "Missing message or conversationId"}, status=400)
                        return
                    args = ["send-message"]
                    if body.get("title"):
                        args.extend(["--title", str(body["title"])])
                    args.extend([conv_id, msg])
                    self._send_json(_run_sidecar_agentapi(args, project_id=body.get("projectId")))
                    return
                if parsed.path == "/_sidecar/new-conversation":
                    msg = str(body.get("message") or "").strip()
                    if not msg:
                        self._send_json({"error": "Missing message"}, status=400)
                        return
                    args = ["new-conversation"]
                    if body.get("title"):
                        args.extend(["--title", str(body["title"])])
                    args.append(msg)
                    self._send_json(_run_sidecar_agentapi(args, project_id=body.get("projectId")))
                    return
                if parsed.path == "/api/gerrit/vote":
                    res = service.cast_batch_vote(
                        cl_numbers=body.get("cls") or [],
                        code_review=int(body.get("code_review", 2)),
                        autosubmit=bool(body.get("autosubmit", False)),
                        presubmit_ready=bool(body.get("presubmit_ready", False)),
                        message=str(body.get("message", "")),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/assign-reviewer":
                    res = service.assign_second_human_reviewer(
                        cl_number=body.get("cl", ""),
                        reviewer_email=body.get("reviewer", ""),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/harness":
                    res = service.run_harness_action(
                        cl_number=body.get("cl", ""),
                        action=body.get("action", "review-swarm"),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/post-swarm-review":
                    self._send_json(service.post_swarm_review(cl_number=body.get("cl", "")))
                    return
                self._send_json({"error": f"Unknown POST endpoint '{parsed.path}'"}, status=404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

    return GerritUIHandler


def find_available_port(host: str, start_port: int) -> int:
    """Finds the first available TCP port starting at `start_port`."""
    for offset in range(MAX_PORT_PROBES):
        candidate = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(
        f"Could not bind Gerrit UI server on '{host}' across ports {start_port}..{start_port + MAX_PORT_PROBES - 1}."
    )


def serve_gerrit_ui(
    host_addr: str = "127.0.0.1",
    port: int = DEFAULT_GERRIT_UI_PORT,
    gerrit_host: str = DEFAULT_GERRIT_HOST,
    viewer: str = "",
    query: str = "",
    open_browser: bool = True,
    export_static_path: str | None = None,
) -> int:
    """Starts the local Gerrit UI server or exports a pre-populated static snapshot HTML."""
    service = GerritUIService(host=gerrit_host, default_viewer=viewer, default_query=query)
    if export_static_path:
        queue_data = service.fetch_queue(viewer_email=viewer or None, query=query or None)
        vetted_stacks = [
            st
            for st in (queue_data.get("stacks") or [])
            if any(c.get("presubmit_vetted") and not c.get("approved_by_viewer") for c in (st.get("commits") or []))
        ]
        target_cls = [
            c["number"]
            for st in vetted_stacks[:3]
            for c in (st.get("commits") or [])
        ][:8]
        if not target_cls:
            target_cls = [c["number"] for c in (queue_data.get("changes") or [])[:6]]
        queue_data["prefetched_diffs"] = service.fetch_multi_commit_diffs(target_cls)
        html_out = render_static_gerrit_ui_html(initial_payload=queue_data)
        out_file = Path(export_static_path).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(html_out, encoding="utf-8")
        print(f"Exported static Gerrit UI snapshot ({len(queue_data['changes'])} CLs) to {out_file}")
        return 0

    bound_port = find_available_port(host_addr, port)
    url = f"http://{host_addr}:{bound_port}"
    server = http.server.ThreadingHTTPServer((host_addr, bound_port), make_gerrit_ui_handler(service))
    print(f"AEMU Fast Gerrit UI running at {url} (viewer: {service.default_viewer})")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Gerrit UI server.")
    finally:
        server.server_close()
    return 0
