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
import threading
import time
from typing import Any
import urllib.parse
import webbrowser

from commands.gerrit.ai_analysis import (
    build_ai_analysis_prompt,
    parse_ai_analysis_from_conversation,
)
from commands.gerrit.presubmit_targets import (
    ACTIVE_TARGETS_CACHE_TTL_SEC,
    COMPLETED_TARGETS_CACHE_TTL_SEC,
    extract_presubmit_status_from_change,
    format_relative_age,
    query_presubmit_targets_via_stubby,
)
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
DIFF_BUNDLE_SUBTASK_WORKERS = 4
DEFAULT_MAX_DIFF_LINES_PER_FILE = 500
SESSION_PRESUBMIT_LAG_SEC = 25.0
FAST_QUEUE_LIMIT = 10
FULL_QUEUE_LIMIT = 100
RAW_QUEUE_CACHE_TTL_SEC = 45.0
RAW_QUEUE_STALE_MAX_SEC = 300.0
PREWARM_COOLDOWN_SEC = 45.0
DIFF_BUNDLE_CACHE_MAX_ENTRIES = 128

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

DEFAULT_GCHAT_CLI_BIN = "/google/bin/releases/gemini-agents-gchat/gchat"
GCHAT_DM_TIMEOUT_SEC = 25
MAX_CHAT_DM_LENGTH = 4000
_GOOGLE_CORP_EMAIL_RE = re.compile(r"^[a-z0-9._+-]+@google\.com$")

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
_SIMPLE_LDAP_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]*$")

_LABEL_SHORT_NAMES: dict[str, str] = {
    "Code-Review": "CR",
    "Presubmit-Verified": "PV",
    "Presubmit-Ready": "PR",
    "Autosubmit": "AS",
    "Performance": "Perf",
    "Open-Source-Licensing": "OSL",
    "Presubmit-Verified-Together": "PVT",
    "API-Review": "API",
    "Owners-Override": "OO",
}

CODE_SEARCH_BASE_HOST = "https://source.corp.google.com/h"
DEFAULT_CODE_SEARCH_GOB_HOST = "googleplex-android"
GERRIT_MAGIC_FILES: frozenset[str] = frozenset({
    "/COMMIT_MSG",
    "/MERGE_LIST",
    "/PATCHSET_LEVEL",
})


def _extract_gob_host_name(gerrit_host: str) -> str:
    """Extracts the short Git-on-Borg host name (e.g. `googleplex-android`) from a Gerrit host."""
    raw = (gerrit_host or "").strip().lower()
    if "://" in raw:
        raw = urllib.parse.urlparse(raw).netloc or raw
    subdomain = raw.split(".", 1)[0] if raw else ""
    if subdomain.endswith("-review"):
        subdomain = subdomain[: -len("-review")]
    return subdomain or DEFAULT_CODE_SEARCH_GOB_HOST


def _resolve_googleplex_android_superproject(
    clean_project: str, clean_branch: str
) -> tuple[str, str, str] | None:
    """Maps an Android GoB `(project, branch)` pair to `(superproject_repo, ref, mount_path)`."""
    if clean_project == "platform/hardware/generic/goldfish":
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/generic/goldfish")

    if clean_project == "platform/external/qemu":
        if clean_branch.startswith("emu-main-next"):
            return ("platform/superproject/emu-main-next", "emu-main-next", "third_party/qemu")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "external/qemu")
        return ("platform/superproject/emu-main-dev", "emu-main-dev", "external/qemu")

    if clean_project == "platform/hardware/google/aemu":
        if clean_branch == "emu-main-dev":
            return ("platform/superproject/emu-main-dev", "emu-main-dev", "hardware/google/aemu")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "hardware/google/aemu")
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/google/aemu")

    if clean_project == "platform/hardware/google/gfxstream":
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "hardware/google/gfxstream")
        if clean_branch == "emu-main-dev":
            return ("platform/superproject/emu-main-dev", "emu-main-dev", "hardware/google/gfxstream")
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/google/gfxstream")

    if clean_project == "platform/build/bazel":
        if clean_branch == "studio-main":
            return ("platform/superproject/base", "studio-main", "build/bazel")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "build/bazel")
        return ("platform/superproject/emu-main-next", "emu-main-next", "build/bazel")

    if not clean_project.startswith("platform/"):
        return None

    subpath = clean_project[len("platform/") :]
    branch_superprojects: dict[str, tuple[str, str]] = {
        "emu-main-next": ("platform/superproject/emu-main-next", "emu-main-next"),
        "emu-main-dev": ("platform/superproject/emu-main-dev", "emu-main-dev"),
        "studio-main": ("platform/superproject/base", "studio-main"),
        "main": ("platform/superproject/main", "main"),
    }
    sp_entry = branch_superprojects.get(clean_branch)
    if sp_entry is None:
        return None
    return (sp_entry[0], sp_entry[1], subpath)


def resolve_code_search_base_url(
    project: str, branch: str, gerrit_host: str = DEFAULT_GERRIT_HOST
) -> str:
    """Resolves the Code Search base prefix URL for a Gerrit `(project, branch)` pair."""
    clean_project = (project or "").strip().strip("/")
    if not clean_project:
        return ""
    clean_branch = (branch or "").strip()
    if clean_branch.startswith("refs/heads/"):
        clean_branch = clean_branch[len("refs/heads/") :]
    clean_branch = clean_branch or "main"
    gob_host = _extract_gob_host_name(gerrit_host)

    if gob_host == DEFAULT_CODE_SEARCH_GOB_HOST:
        sp = _resolve_googleplex_android_superproject(clean_project, clean_branch)
        if sp is not None:
            sp_repo, sp_ref, mount_path = sp
            return f"{CODE_SEARCH_BASE_HOST}/{gob_host}/{sp_repo}/+/{sp_ref}:{mount_path}"

    encoded_branch = urllib.parse.quote(clean_branch, safe="/._-")
    return f"{CODE_SEARCH_BASE_HOST}/{gob_host}/{clean_project}/+/{encoded_branch}:"


def build_code_search_file_url(
    project: str,
    branch: str,
    file_path: str,
    line: int | None = None,
    gerrit_host: str = DEFAULT_GERRIT_HOST,
) -> str:
    """Builds a full `source.corp.google.com` Code Search URL for `file_path` (with optional line)."""
    raw_path = (file_path or "").strip()
    if not raw_path or raw_path in GERRIT_MAGIC_FILES:
        return ""
    rel_path = raw_path.lstrip("/")
    if not rel_path or rel_path == "dev/null":
        return ""
    base_url = resolve_code_search_base_url(project, branch, gerrit_host=gerrit_host)
    if not base_url:
        return ""
    encoded_path = "/".join(urllib.parse.quote(seg, safe="") for seg in rel_path.split("/") if seg)
    if not encoded_path:
        return ""
    url = f"{base_url}{encoded_path}" if base_url.endswith(":") else f"{base_url}/{encoded_path}"
    if line is not None and int(line) > 0:
        url = f"{url};l={int(line)}"
    return url


def format_repo_display_path(project: str) -> str:
    """Formats a Gerrit project name into its concise repo path (e.g. `hardware/generic/goldfish`)."""
    clean = (project or "").strip().strip("/")
    if clean.startswith("platform/"):
        return clean[len("platform/") :]
    return clean


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


def build_viewer_gerrit_query(
    viewer_email: str, scope: str = "reviewer", author: str = ""
) -> str:
    """Builds the Gerrit search query for the active `viewer_email`, `scope`, and optional `author`."""
    viewer = (viewer_email or "").strip().lower()
    norm_scope = (scope or "reviewer").strip().lower()
    norm_author = (author or "").strip()
    if norm_scope == "owner" and viewer:
        base_q = f"status:open -is:wip owner:{viewer}"
    elif norm_scope == "team" or not viewer:
        base_q = DEFAULT_EMULATOR_GERRIT_QUERY
    else:
        base_q = f"status:open -is:wip reviewer:{viewer} -owner:{viewer}"
    if norm_author and norm_scope != "owner":
        author_q = norm_author if "@" in norm_author else f"{norm_author}@google.com"
        return f"{base_q} owner:{author_q}"
    return base_q


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


def _account_lookup_key(acct: dict[str, Any]) -> str:
    """Returns a stable normalized key (`email` or `id:<account_id>`) for a Gerrit account dict."""
    email = (acct.get("email") or "").strip().lower()
    if email:
        return email
    acct_id = acct.get("_account_id")
    if acct_id is not None:
        return f"id:{acct_id}"
    return (acct.get("display_name") or acct.get("name") or "").strip().lower()


def _collect_votes_by_account(
    labels: dict[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Collects non-zero label votes keyed by `_account_lookup_key` and raw voter account objects."""
    priority_labels = ("Code-Review", "Presubmit-Verified", "Presubmit-Ready", "Autosubmit")
    ordered_names = [k for k in priority_labels if k in labels] + sorted(
        k for k in labels.keys() if k not in priority_labels
    )
    votes_by_key: dict[str, list[dict[str, Any]]] = {}
    voter_accounts: dict[str, dict[str, Any]] = {}
    for label_name in ordered_names:
        label_obj = labels.get(label_name) or {}
        short_lbl = _LABEL_SHORT_NAMES.get(label_name, label_name)
        for vote in label_obj.get("all") or []:
            val = int(vote.get("value", 0) or 0)
            if val == 0:
                continue
            key = _account_lookup_key(vote)
            if not key:
                continue
            voter_accounts.setdefault(key, vote)
            votes_by_key.setdefault(key, []).append(
                {
                    "label": label_name,
                    "short_label": short_lbl,
                    "value": val,
                    "formatted": f"+{val}" if val > 0 else str(val),
                }
            )
    return votes_by_key, voter_accounts


def _build_participant_entry(
    acct: dict[str, Any],
    state: str,
    *,
    owner_email: str,
    viewer_email: str,
    removable_keys: set[str] | None,
    attention_keys: set[str],
    votes_by_key: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Formats one `REVIEWER` or `CC` participant with votes, bot status, and removability."""
    key = _account_lookup_key(acct)
    email = (acct.get("email") or "").strip().lower()
    short = (
        email.split("@", 1)[0]
        if email
        else (acct.get("display_name") or acct.get("name") or "unknown").strip()
    )
    display_name = (acct.get("display_name") or acct.get("name") or short).strip()
    full_name = (acct.get("name") or display_name or short).strip()
    tags = list(acct.get("tags") or [])
    is_bot = is_agent_or_bot_email(email, full_name) or ("SERVICE_USER" in tags)
    is_owner = bool(email and email == owner_email)
    votes = [dict(v) for v in (votes_by_key.get(key) or [])]
    cr_vote = next((int(v["value"]) for v in votes if v["label"] == "Code-Review"), 0)
    is_removable = (
        (key in removable_keys or (bool(email) and email in removable_keys))
        if removable_keys is not None
        else (not is_owner)
    )
    return {
        "account_id": acct.get("_account_id"),
        "email": email,
        "short": short,
        "name": display_name,
        "full_name": full_name,
        "state": state,
        "is_bot": is_bot,
        "is_team_agent": is_team_review_agent(email),
        "is_owner": is_owner,
        "is_viewer": bool(email and email == viewer_email),
        "in_attention_set": key in attention_keys or (bool(email) and email in attention_keys),
        "removable": is_removable,
        "cr_vote": cr_vote,
        "votes": votes,
    }


def _append_unique_participants(
    raw_accounts: list[dict[str, Any]],
    state: str,
    seen_keys: set[str],
    out_list: list[dict[str, Any]],
    *,
    owner_email: str,
    viewer_email: str,
    removable_keys: set[str] | None,
    attention_keys: set[str],
    votes_by_key: dict[str, list[dict[str, Any]]],
) -> None:
    """Appends deduplicated formatted participant entries for `state` (`REVIEWER` or `CC`)."""
    for acct in raw_accounts:
        key = _account_lookup_key(acct)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        out_list.append(
            _build_participant_entry(
                acct,
                state,
                owner_email=owner_email,
                viewer_email=viewer_email,
                removable_keys=removable_keys,
                attention_keys=attention_keys,
                votes_by_key=votes_by_key,
            )
        )


def extract_reviewers_and_ccs(
    change: dict[str, Any], viewer_email: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extracts ordered `REVIEWER` and `CC` participant lists with all label votes."""
    viewer = (viewer_email or "").strip().lower()
    owner_email = ((change.get("owner") or {}).get("email") or "").strip().lower()
    votes_by_key, voter_accounts = _collect_votes_by_account(change.get("labels") or {})
    removable_keys = (
        {_account_lookup_key(r) for r in (change.get("removable_reviewers") or [])}
        if "removable_reviewers" in change
        else None
    )
    attention_keys = {
        _account_lookup_key((info or {}).get("account") or {})
        for info in (change.get("attention_set") or {}).values()
    } - {""}

    rev_block = change.get("reviewers") or {}
    seen_keys: set[str] = set()
    reviewers_out: list[dict[str, Any]] = []
    ccs_out: list[dict[str, Any]] = []
    shared_kwargs = {
        "owner_email": owner_email,
        "viewer_email": viewer,
        "removable_keys": removable_keys,
        "attention_keys": attention_keys,
        "votes_by_key": votes_by_key,
    }
    _append_unique_participants(
        rev_block.get("REVIEWER") or [], "REVIEWER", seen_keys, reviewers_out, **shared_kwargs
    )
    _append_unique_participants(
        rev_block.get("CC") or [], "CC", seen_keys, ccs_out, **shared_kwargs
    )
    _append_unique_participants(
        list(voter_accounts.values()), "REVIEWER", seen_keys, reviewers_out, **shared_kwargs
    )
    reviewers_out.sort(
        key=lambda p: (
            2 if (p["is_bot"] and not p["is_team_agent"]) else (1 if p["is_team_agent"] else 0),
            0 if p["votes"] else 1,
            -p["cr_vote"],
            0 if p["is_viewer"] else 1,
            p["short"].lower(),
        )
    )
    ccs_out.sort(key=lambda p: (1 if p["is_bot"] else 0, p["short"].lower()))
    return reviewers_out, ccs_out


def _extract_positive_label_voters(
    votes: list[dict[str, Any]], viewer: str
) -> tuple[list[str], bool]:
    """Returns `(voter_short_names, viewer_voted_positive)` for a `+1` Gerrit label."""
    voters: list[str] = []
    viewer_voted = False
    for vote in votes:
        if int(vote.get("value", 0) or 0) < 1:
            continue
        em = (vote.get("email") or "").strip().lower()
        short = em.split("@", 1)[0] if em else (vote.get("name") or "user")
        voters.append(short)
        if viewer and em == viewer:
            viewer_voted = True
    return voters, viewer_voted


def _extract_autosubmit_and_viewer_votes(
    change: dict[str, Any], viewer_email: str
) -> dict[str, Any]:
    """Extracts the viewer's current CR/Autosubmit/Presubmit-Ready votes and label state."""
    viewer = (viewer_email or "").strip().lower()
    labels = change.get("labels") or {}
    cr_all = (labels.get("Code-Review") or {}).get("all") or []
    as_all = (labels.get("Autosubmit") or {}).get("all") or []
    pr_all = (labels.get("Presubmit-Ready") or {}).get("all") or []

    viewer_cr_vote = 0
    for vote in cr_all:
        if (vote.get("email") or "").strip().lower() == viewer:
            viewer_cr_vote = int(vote.get("value", 0) or 0)
            break

    autosubmit_voters, viewer_autosubmit = _extract_positive_label_voters(as_all, viewer)
    pr_voters, viewer_pr = _extract_positive_label_voters(pr_all, viewer)

    return {
        "viewer_cr_vote": viewer_cr_vote,
        "viewer_autosubmit": viewer_autosubmit,
        "autosubmit_enabled": bool(autosubmit_voters),
        "autosubmit_voters": autosubmit_voters,
        "viewer_presubmit_ready": viewer_pr,
        "presubmit_ready_enabled": bool(pr_voters),
        "presubmit_ready_voters": pr_voters,
    }


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


def build_approval_slots(
    human_cr2: list[dict[str, Any]],
    required_cr2_count: int,
    viewer: str,
) -> list[dict[str, str]]:
    """Builds compact approval slot descriptors with a single needed-votes badge."""
    slots: list[dict[str, str]] = []
    for voter in human_cr2[:required_cr2_count]:
        tag = "You (+2 ✓)" if voter["email"] == viewer else f"✓ {voter['short']} +2"
        slots.append({"state": "done", "text": tag})

    remaining_slots = max(0, required_cr2_count - len(human_cr2))
    if remaining_slots >= 2:
        slots.append({"state": "needed", "text": "Needs two +2s"})
    elif remaining_slots == 1:
        slots.append({"state": "needed", "text": "Needs +2"})
    return slots


def _compute_is_submittable(
    *,
    human_cr2_count: int,
    required_cr2_count: int,
    presubmit_vetted: bool,
    presubmit_status: str | None,
    mergeable: bool,
    has_negative_cr: bool,
    gerrit_submittable: bool | None,
) -> bool:
    """Computes effective UI submittability combining Gerrit's `submittable` flag and human CR+2 gates."""
    heuristic = (
        human_cr2_count >= required_cr2_count
        and presubmit_vetted
        and presubmit_status not in ("running", "queued", "failed")
        and mergeable
        and not has_negative_cr
    )
    return (
        bool(gerrit_submittable and heuristic)
        if isinstance(gerrit_submittable, bool)
        else heuristic
    )


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
    owner_name = (owner.get("name") or owner_short or "").strip()
    owner_is_bot = is_agent_or_bot_email(owner_email, owner_name)

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
    reviewers_detail, ccs_detail = extract_reviewers_and_ccs(change, viewer)
    vote_meta = _extract_autosubmit_and_viewer_votes(change, viewer)
    cur_ps = int(change.get("current_revision_number", 1) or 1)
    presubmit_info = extract_presubmit_status_from_change(
        change, pv_score, vote_meta["presubmit_ready_enabled"], cur_ps
    )
    slots = build_approval_slots(human_cr2, required_cr2_count, viewer)

    created_dt = parse_gerrit_timestamp(change.get("created"))
    updated_dt = parse_gerrit_timestamp(change.get("updated"))
    hours_in_flight = max(0.0, (now - created_dt).total_seconds() / 3600.0) if created_dt else 0.0
    hours_idle = max(0.0, (now - updated_dt).total_seconds() / 3600.0) if updated_dt else 0.0
    updated_ts = round(updated_dt.timestamp(), 3) if updated_dt else 0.0
    updated_iso = updated_dt.isoformat() if updated_dt else str(change.get("updated") or "")
    updated_display = format_relative_age(hours_idle) if updated_dt else ""
    mergeable = change.get("mergeable", True) is not False
    has_negative_cr = any(int(p.get("cr_vote", 0) or 0) < 0 for p in reviewers_detail)
    raw_gerrit_submittable = change.get("submittable")
    gerrit_submittable = raw_gerrit_submittable if isinstance(raw_gerrit_submittable, bool) else None
    is_submittable = _compute_is_submittable(
        human_cr2_count=len(human_cr2),
        required_cr2_count=required_cr2_count,
        presubmit_vetted=presubmit_vetted,
        presubmit_status=presubmit_info["presubmit_status"],
        mergeable=mergeable,
        has_negative_cr=has_negative_cr,
        gerrit_submittable=gerrit_submittable,
    )

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
        f"(\"{subject}\", {risk['tier']} load, presubmit+agent verified)? "
        f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{num}"
    )

    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    commit_obj = rev_obj.get("commit") or {}
    raw_commit_msg = (commit_obj.get("message") or "").strip()
    commit_message = raw_commit_msg if raw_commit_msg else subject
    parents = commit_obj.get("parents") or []
    parent_sha = parents[0].get("commit", "") if parents else ""
    files_list = [p for p in (rev_obj.get("files") or {}).keys() if p != "/COMMIT_MSG"]

    commit_author = (commit_obj.get("author") or {}) if isinstance(commit_obj, dict) else {}
    author_email = (commit_author.get("email") or owner_email or "").strip().lower()
    author_short = author_email.split("@", 1)[0] if author_email else owner_short
    author_name = (commit_author.get("name") or owner_name or author_short or "").strip()

    uploader_obj = (rev_obj.get("uploader") or {}) if isinstance(rev_obj, dict) else {}
    uploader_email = (uploader_obj.get("email") or "").strip().lower()
    uploader_short = uploader_email.split("@", 1)[0] if uploader_email else ""
    uploader_name = (uploader_obj.get("name") or uploader_short or "").strip()

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
        "owner_name": owner_name,
        "owner_is_bot": owner_is_bot,
        "author_email": author_email,
        "author_short": author_short,
        "author_name": author_name,
        "uploader_email": uploader_email,
        "uploader_short": uploader_short,
        "uploader_name": uploader_name,
        "files": files_list,
        "is_agent_cl": requires_two_humans,
        "required_cr2_count": required_cr2_count,
        "human_cr2_count": len(human_cr2),
        "human_cr2": human_cr2,
        "human_cr1": human_cr1,
        "approved_by_viewer": approved_by_viewer,
        "is_submittable": is_submittable,
        "gerrit_submittable": gerrit_submittable,
        "viewer_cr_vote": vote_meta["viewer_cr_vote"],
        "viewer_autosubmit": vote_meta["viewer_autosubmit"],
        "autosubmit_enabled": vote_meta["autosubmit_enabled"],
        "autosubmit_voters": vote_meta["autosubmit_voters"],
        "viewer_presubmit_ready": vote_meta["viewer_presubmit_ready"],
        "presubmit_ready_enabled": vote_meta["presubmit_ready_enabled"],
        "presubmit_ready_voters": vote_meta["presubmit_ready_voters"],
        "presubmit_score": pv_score,
        "presubmit_vetted": presubmit_vetted,
        "presubmit_status": presubmit_info["presubmit_status"],
        "presubmit_label": presubmit_info["presubmit_label"],
        "presubmit_summary": presubmit_info["presubmit_summary"],
        "workplan_id": presubmit_info["workplan_id"],
        "workplan_url": presubmit_info["workplan_url"],
        "checks_tab_url": presubmit_info["checks_tab_url"],
        "explainer_url": presubmit_info["explainer_url"],
        "agent_vetted": agent_vetted,
        "unresolved_comment_count": unresolved_count,
        "patch_set": cur_ps,
        "current_revision": cur_rev,
        "parent_revision": parent_sha,
        "hours_in_flight": round(hours_in_flight, 1),
        "hours_idle": round(hours_idle, 1),
        "updated_iso": updated_iso,
        "updated_ts": updated_ts,
        "updated_display": updated_display,
        "flight_display": flight_str,
        "mergeable": mergeable,
        "cliff_zone": cliff_zone,
        "risk": risk,
        "commit_type": commit_type,
        "bug_ids": bug_ids,
        "bugs": bugs_list,
        "approval_slots": slots,
        "human_involvement": other_humans,
        "reviewers_detail": reviewers_detail,
        "ccs_detail": ccs_detail,
        "is_orphaned": is_orphaned,
        "chat_ping": chat_ping,
        "swarm_status": read_cl_swarm_status(num, current_revision=cur_rev, now_dt=now),
        "url": f"https://googleplex-android-review.git.corp.google.com/c/{project}/+/{num}",
        "code_search_base_url": resolve_code_search_base_url(project, change.get("branch", "")),
        "repo_label": format_repo_display_path(project),
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
            "title": f"STACK · topic:{topic}",
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
                "STACK · Relation Chain"
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


_CODE_QUESTION_PROMPT_RE = re.compile(
    r"Regarding Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    r"```[^\n]*\n(?P<snippet>.*?)\n```\s*"
    r"\*\*Question:\*\*\s*(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)

_CODE_FOLLOWUP_PROMPT_RE = re.compile(
    r"Follow-up on Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    r"\*\*Question:\*\*\s*(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)

_IDENT_START_CLASS = r"[A-Za-z_\u0080-\U0010ffff]"
_IDENT_CONT_CLASS = r"[\w\u0080-\U0010ffff]"
_HUNK_CONTEXT_RE = re.compile(r"^@@[^@]+@@\s*(.+?)\s*$")
_PY_DEF_OR_CLASS_RE = re.compile(
    rf"^\s*(?:async\s+def|def|class)\s+({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)"
)
_GO_FUNC_RE = re.compile(
    rf"^\s*func\s+(?:\([^)]*\)\s*)?({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)\s*[\[(]"
)
_RUST_FN_RE = re.compile(
    rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+(?:\"[^\"]*\"\s+)?)*"
    rf"fn\s+({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)"
)
_JS_FUNC_RE = re.compile(
    rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
    rf"([A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*[(<]"
)
_JS_ARROW_RE = re.compile(
    rf"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*="
    rf"\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*=>"
)
_C_STYLE_FUNC_RE = re.compile(
    rf"(?:^|[\s*&>:])(~?{_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*"
    rf"(?:::[~A-Za-z_\u0080-\U0010ffff]{_IDENT_CONT_CLASS}*)*)\s*\("
)
_NON_FUNCTION_KEYWORDS: frozenset[str] = frozenset({
    "if",
    "else",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "decltype",
    "static_assert",
    "typeof",
    "new",
    "delete",
    "throw",
    "case",
    "LOCK",
    "GUARDED_BY",
})

MAX_RECENT_BRAIN_DIRS = 15
LS_CONNECT_RPC_TIMEOUT_SEC = 10


def extract_symbol_from_code_line(raw_line: str) -> str:
    """Extracts a function, method, or class identifier from a declaration or diff hunk line."""
    line = (raw_line or "").strip()
    if not line or line.startswith(("//", "#", "/*", "*")):
        return ""
    for pattern in (_PY_DEF_OR_CLASS_RE, _GO_FUNC_RE, _RUST_FN_RE, _JS_FUNC_RE, _JS_ARROW_RE):
        m = pattern.match(line)
        if m:
            return m.group(1)
    if "=" in line.split("(", 1)[0] or line.endswith(";"):
        return ""
    m_c = _C_STYLE_FUNC_RE.search(line)
    if not m_c:
        return ""
    cand = m_c.group(1).lstrip(":")
    short_tail = cand.rsplit("::", 1)[-1]
    if cand in _NON_FUNCTION_KEYWORDS or short_tail in _NON_FUNCTION_KEYWORDS:
        return ""
    return cand


def extract_enclosing_function_from_diff_lines(
    diff_lines: list[dict[str, Any]],
    target_line: int,
    side: str = "REVISION",
) -> str:
    """Finds the nearest enclosing function/method/class name at or above `target_line` in `diff_lines`."""
    if not diff_lines:
        return ""
    ln_key = "old" if str(side).upper() == "PARENT" else "new"
    alt_key = f"{ln_key}_ln"
    hit_idx = -1
    for idx, entry in enumerate(diff_lines):
        raw_val = entry.get(ln_key)
        if raw_val in (None, ""):
            raw_val = entry.get(alt_key)
        raw_num = str(raw_val if raw_val is not None else "").strip()
        if raw_num.isdigit() and int(raw_num) == int(target_line):
            hit_idx = idx
            break
    if hit_idx < 0:
        return ""

    for idx in range(hit_idx, -1, -1):
        entry = diff_lines[idx]
        ltype = entry.get("type")
        text = str(entry.get("text") or "")
        if ltype == "hunk":
            m_hunk = _HUNK_CONTEXT_RE.match(text)
            return extract_symbol_from_code_line(m_hunk.group(1)) if m_hunk else ""
        if ltype == "del" and ln_key == "new":
            continue
        if ltype == "add" and ln_key == "old":
            continue
        code_body = text[1:] if text.startswith(("+", "-", " ")) else text
        if code_body != code_body.lstrip() and len(code_body) - len(code_body.lstrip()) > 8:
            continue
        sym = extract_symbol_from_code_line(code_body)
        if sym:
            return sym
    return ""


def build_code_question_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    code_snippet: str,
    question: str,
    *,
    side: str = "REVISION",
) -> str:
    """Builds a structured Markdown prompt for asking an agent about a highlighted code block."""
    cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
    cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
    s_ln = max(1, int(start_line or 1))
    e_ln = max(s_ln, int(end_line or s_ln))
    range_label = f"L{s_ln}" if s_ln == e_ln else f"L{s_ln}-L{e_ln}"
    side_note = " (PARENT / base)" if str(side).upper() == "PARENT" else ""
    cleaned_question = (
        (question or "").strip()
        or "Please explain this code block and check for any bugs or edge cases."
    )
    snippet = (code_snippet or "").rstrip()
    return (
        f"Regarding Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"```\n{snippet}\n```\n\n"
        f"**Question:** {cleaned_question}"
    )


def build_code_followup_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    question: str,
    *,
    side: str = "REVISION",
) -> str:
    """Builds a structured follow-up prompt within an existing inline code Q&A conversation."""
    cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
    cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
    s_ln = max(1, int(start_line or 1))
    e_ln = max(s_ln, int(end_line or s_ln))
    range_label = f"L{s_ln}" if s_ln == e_ln else f"L{s_ln}-L{e_ln}"
    side_note = " (PARENT / base)" if str(side).upper() == "PARENT" else ""
    cleaned_question = (question or "").strip()
    return (
        f"Follow-up on Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"**Question:** {cleaned_question}"
    )


def _collect_planner_reply_until_next_prompt(
    steps: list[dict[str, Any]], start_idx: int
) -> tuple[str, int]:
    """Collects `PLANNER_RESPONSE` text after `start_idx` up to the next user/question step."""
    reply_parts: list[str] = []
    last_step_index = int(steps[start_idx].get("step_index", start_idx) or start_idx)
    for next_step in steps[start_idx + 1 :]:
        next_type = str(next_step.get("type") or "")
        next_content = str(next_step.get("content") or "")
        if next_type == "USER_INPUT":
            break
        if next_type == "SYSTEM_MESSAGE" and (
            "Regarding Gerrit CL `ag/" in next_content
            or "Follow-up on Gerrit CL `ag/" in next_content
        ):
            break
        step_idx = int(next_step.get("step_index", last_step_index) or last_step_index)
        last_step_index = max(last_step_index, step_idx)
        if next_type == "PLANNER_RESPONSE" and next_content.strip():
            reply_parts.append(next_content.strip())
    return "\n\n".join(reply_parts).strip(), last_step_index


def _extract_followup_question_text(step: dict[str, Any]) -> str:
    """Extracts follow-up question text from a `USER_INPUT` or follow-up `SYSTEM_MESSAGE` step."""
    step_type = str(step.get("type") or "")
    content = str(step.get("content") or "")
    m_followup = _CODE_FOLLOWUP_PROMPT_RE.search(content)
    if m_followup:
        return (m_followup.group("question") or "").strip()
    if step_type != "USER_INPUT":
        return ""
    cleaned = re.sub(r"</?USER_REQUEST>", "", content).strip()
    if not cleaned or "Regarding Gerrit CL `ag/" in cleaned:
        return ""
    return cleaned


def parse_code_questions_from_transcript_lines(
    lines: list[str],
    conv_id: str = "",
    filter_cls: set[str] | None = None,
    ignored_steps: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Extracts inline code block Q&A threads (including multi-turn follow-ups) from JSONL transcript lines."""
    steps: list[dict[str, Any]] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                steps.append(obj)
        except Exception:
            continue

    results: list[dict[str, Any]] = []
    root_prompt_indices = [
        idx
        for idx, step in enumerate(steps)
        if str(step.get("type") or "") in ("SYSTEM_MESSAGE", "USER_INPUT")
        and "Regarding Gerrit CL `ag/" in str(step.get("content") or "")
        and _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
    ]
    is_dedicated_qa_conv = len(root_prompt_indices) == 1
    skip_steps = ignored_steps or set()

    for pos, idx in enumerate(root_prompt_indices):
        step = steps[idx]
        step_index = int(step.get("step_index", idx) or idx)
        if step_index in skip_steps:
            continue
        m = _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
        if not m:
            continue
        cl_str = m.group("cl").strip()
        if filter_cls and cl_str not in filter_cls:
            continue
        file_path = m.group("file_path").strip() or "/COMMIT_MSG"
        s_ln = max(1, int(m.group("start_line") or 1))
        e_ln = max(s_ln, int(m.group("end_line") or s_ln))
        side_note = m.group("side_note") or ""
        side = "PARENT" if "PARENT" in side_note.upper() else "REVISION"
        snippet = m.group("snippet") or ""
        first_q = (m.group("question") or "").strip()

        reply_text, end_step_idx = _collect_planner_reply_until_next_prompt(steps, idx)
        turns: list[dict[str, Any]] = [
            {
                "turn_index": 0,
                "step_index": step_index,
                "end_step_index": end_step_idx,
                "question": first_q,
                "reply": reply_text,
                "status": "answered" if reply_text else "thinking",
            }
        ]

        next_root_idx = root_prompt_indices[pos + 1] if pos + 1 < len(root_prompt_indices) else len(steps)
        if is_dedicated_qa_conv:
            for sub_idx in range(idx + 1, next_root_idx):
                sub_step = steps[sub_idx]
                sub_step_num = int(sub_step.get("step_index", sub_idx) or sub_idx)
                if sub_step_num in skip_steps:
                    continue
                followup_q = _extract_followup_question_text(sub_step)
                if not followup_q:
                    continue
                f_reply, f_end_step = _collect_planner_reply_until_next_prompt(steps, sub_idx)
                turns.append(
                    {
                        "turn_index": len(turns),
                        "step_index": sub_step_num,
                        "end_step_index": f_end_step,
                        "question": followup_q,
                        "reply": f_reply,
                        "status": "answered" if f_reply else "thinking",
                    }
                )

        latest = turns[-1]
        short_conv = (conv_id or "local")[:8]
        results.append(
            {
                "id": f"tx-q-{short_conv}-{step_index}",
                "conversation_id": conv_id,
                "step_index": step_index,
                "cl": int(cl_str) if cl_str.isdigit() else cl_str,
                "file_path": file_path,
                "line": e_ln,
                "start_line": s_ln,
                "end_line": e_ln,
                "side": side,
                "code_snippet": snippet,
                "initial_question": first_q,
                "question": latest["question"],
                "reply": latest["reply"],
                "status": latest["status"],
                "turns": turns,
                "created_at": str(step.get("created_at") or ""),
            }
        )
    return results


def _resolve_candidate_conversation_dirs(
    root: Path,
    conversation_id: str | None = None,
    extra_conversation_ids: set[str] | None = None,
    excluded_conversation_ids: set[str] | None = None,
) -> list[Path]:
    """Resolves tracked, active, and recently updated Jetski conversation directories."""
    excluded = excluded_conversation_ids or set()
    candidate_dirs: list[Path] = []
    seen: set[Path] = set()

    def _add_conv_id(cid: str) -> None:
        cleaned = (cid or "").strip()
        if not cleaned or cleaned in excluded:
            return
        d = root / cleaned
        if d.is_dir() and d not in seen:
            seen.add(d)
            candidate_dirs.append(d)

    for cid in sorted(extra_conversation_ids or set()):
        _add_conv_id(cid)
    _add_conv_id(
        str(
            conversation_id
            or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
            or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            or ""
        )
    )

    try:
        subdirs = [d for d in root.iterdir() if d.is_dir() and d.name not in excluded and d not in seen]
        subdirs.sort(
            key=lambda d: (
                (d / ".system_generated" / "logs" / "transcript_full.jsonl").stat().st_mtime
                if (d / ".system_generated" / "logs" / "transcript_full.jsonl").is_file()
                else 0.0
            ),
            reverse=True,
        )
        candidate_dirs.extend(subdirs[:MAX_RECENT_BRAIN_DIRS])
    except OSError:
        pass
    return candidate_dirs


def discover_transcript_code_questions(
    conversation_id: str | None = None,
    cl_numbers: list[str | int] | None = None,
    brain_root: Path | None = None,
    *,
    extra_conversation_ids: set[str] | None = None,
    excluded_conversation_ids: set[str] | None = None,
    ignored_steps_by_conv: dict[str, set[int]] | None = None,
) -> list[dict[str, Any]]:
    """Finds inline code questions and agent replies from Jetski conversation transcripts."""
    filter_cls = (
        {parse_cl_identifier(str(x), allow_head=False) for x in cl_numbers if str(x).strip()}
        if cl_numbers
        else None
    )
    root = brain_root or (Path.home() / ".gemini" / "jetski" / "brain")
    if not root.is_dir():
        return []

    by_key: dict[str, dict[str, Any]] = {}
    conv_dirs = _resolve_candidate_conversation_dirs(
        root,
        conversation_id=conversation_id,
        extra_conversation_ids=extra_conversation_ids,
        excluded_conversation_ids=excluded_conversation_ids,
    )
    for conv_dir in conv_dirs:
        logs_dir = conv_dir / ".system_generated" / "logs"
        tx_file = logs_dir / "transcript_full.jsonl"
        if not tx_file.is_file():
            tx_file = logs_dir / "transcript.jsonl"
        if not tx_file.is_file():
            continue
        try:
            raw_text = tx_file.read_text(encoding="utf-8", errors="replace")
            if "Regarding Gerrit CL `ag/" not in raw_text:
                continue
            items = parse_code_questions_from_transcript_lines(
                raw_text.splitlines(),
                conv_id=conv_dir.name,
                filter_cls=filter_cls,
                ignored_steps=(ignored_steps_by_conv or {}).get(conv_dir.name),
            )
            for item in items:
                dedupe_key = (
                    f"conv:{conv_dir.name}:{item['step_index']}"
                    if len(items) == 1
                    else (
                        f"{item['cl']}:{item['file_path']}:{item['start_line']}:"
                        f"{item['end_line']}:{str(item.get('initial_question') or item['question']).strip()}"
                    )
                )
                prev = by_key.get(dedupe_key)
                if prev is None or (item.get("reply") and not prev.get("reply")):
                    by_key[dedupe_key] = item
        except OSError:
            continue

    return list(by_key.values())


def _resolve_gchat_cli_bin() -> str | None:
    """Resolves the `gchat` CLI executable path from env override, `/google/bin`, or `PATH`."""
    override = (os.environ.get("AEMU_GCHAT_CLI_BIN") or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    if os.path.isfile(DEFAULT_GCHAT_CLI_BIN) and os.access(DEFAULT_GCHAT_CLI_BIN, os.X_OK):
        return DEFAULT_GCHAT_CLI_BIN
    return shutil.which("gchat")


def _normalize_chat_dm_recipient(raw_recipient: str, sender_email: str) -> str:
    """Validates and normalizes a single human `@google.com` recipient for a 1:1 Google Chat DM."""
    cleaned = str(raw_recipient or "").strip().lower()
    if not cleaned:
        raise ValueError(
            "Failed to validate Google Chat DM recipient: recipient is empty. "
            "Select a human '@google.com' reviewer on the CL."
        )
    if "," in cleaned or any(ch.isspace() for ch in cleaned):
        raise ValueError(
            f"Failed to validate Google Chat DM recipient '{raw_recipient}': "
            "multi-recipient DMs are blocked to prevent creating duplicate group chats. "
            "Send 1:1 DMs one reviewer at a time."
        )
    resolved = (
        f"{cleaned}@google.com"
        if _SIMPLE_LDAP_RE.fullmatch(cleaned) and not cleaned.isdigit()
        else cleaned
    )
    if not _GOOGLE_CORP_EMAIL_RE.fullmatch(resolved) or is_agent_or_bot_email(resolved):
        raise ValueError(
            f"Failed to validate Google Chat DM recipient '{resolved}': "
            "only human '@google.com' accounts can receive 1:1 Chat DMs. "
            "Select a human reviewer on the CL."
        )
    if sender_email and resolved == sender_email.lower():
        raise ValueError(
            f"Failed to send Google Chat DM to '{resolved}': "
            "Google Chat API does not support 1:1 DMs with yourself. "
            "Choose another reviewer on the CL."
        )
    return resolved


def _run_gchat_send_dm_command(
    gchat_bin: str, recipient_email: str, msg_text: str, cl_id: str
) -> subprocess.CompletedProcess[str]:
    """Executes `gchat mutate send-direct-message` for one verified 1:1 recipient."""
    cmd = [
        gchat_bin,
        "mutate",
        "send-direct-message",
        "--usernames",
        recipient_email,
        "--text",
        msg_text,
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GCHAT_DM_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out after {GCHAT_DM_TIMEOUT_SEC}s while sending Google Chat DM to "
            f"'{recipient_email}' for ag/{cl_id}. Run 'gcert' in your terminal and retry, "
            "or click 'Copy Text' to paste into Google Chat."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        raise RuntimeError(
            f"Failed to send Google Chat DM to '{recipient_email}' for ag/{cl_id}: {detail}. "
            "Run 'gcert' in your terminal and retry, or click 'Copy Text' to paste into Google Chat."
        )
    return proc


def _parse_gchat_dm_output(stdout_str: str) -> tuple[str, str, str]:
    """Parses `gchat mutate send-direct-message --json` stdout into `(space_name, message_name, chat_url)`."""
    raw = (stdout_str or "").strip()
    if not raw:
        return "", "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    space_name = str(payload.get("space_name") or "").strip()
    message_name = str(payload.get("message_name") or "").strip()
    space_id = space_name.split("/", 1)[1] if space_name.startswith("spaces/") else space_name
    chat_url = f"https://chat.google.com/dm/{space_id}" if space_id else ""
    return space_name, message_name, chat_url


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
        self._session_lock = threading.Lock()
        self._session_approved_cls: set[int] = set()
        self._session_votes: dict[int, dict[str, int]] = {}
        self._session_presubmit_voted_at: dict[int, float] = {}
        self._presubmit_targets_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._raw_queue_cache: dict[str, tuple[float, list[dict[str, Any]], bool]] = {}
        self._known_revisions_by_cl: dict[str, tuple[str, int]] = {}
        self._diff_revision_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._full_diff_bundle_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._last_prewarm_by_viewer: dict[str, float] = {}
        self._prewarm_in_progress = False
        self._qa_lock = threading.Lock()
        self._qa_threads: dict[str, dict[str, Any]] = {}
        self._tracked_qa_conv_ids: set[str] = set()
        self._deleted_qa_conv_ids: set[str] = set()
        self._analyzer_conv_ids: set[str] = set()
        self._ignored_steps_by_conv: dict[str, set[int]] = {}
        self._cached_project_id: str = ""
        self._next_qa_seq: int = 1
        self._ai_analysis_lock = threading.Lock()
        self._ai_analysis_state: dict[tuple[str, int], dict[str, Any]] = {}
        self._ai_analysis_gen: dict[tuple[str, int], int] = {}
        self._ai_analysis_cache_dir = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "ai_analysis"
        )
        self._bug_cache_path = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "bug_metadata_cache.json"
        )
        self._bug_cache: dict[str, dict[str, str]] = self._load_bug_cache()

    def _invalidate_cl_caches(self, cl_ids: list[str] | None = None) -> None:
        """Clears cached raw queue queries and optional full diff bundle entries after a mutation."""
        target_ids = {str(cid).strip() for cid in (cl_ids or []) if str(cid).strip()}
        with self._session_lock:
            self._raw_queue_cache.clear()
            if not target_ids:
                return
            stale_keys = [k for k in self._full_diff_bundle_cache if k[0] in target_ids]
            for key in stale_keys:
                self._full_diff_bundle_cache.pop(key, None)

    def _record_known_revisions(self, raw_changes: list[dict[str, Any]]) -> None:
        """Records `(current_revision, patch_set)` per CL number to accelerate diff bundle fetches."""
        updates: dict[str, tuple[str, int]] = {}
        for ch in raw_changes:
            num = ch.get("_number")
            if not num:
                continue
            cur_rev = str(ch.get("current_revision") or "")
            rev_obj = ((ch.get("revisions") or {}).get(cur_rev) or {}) if cur_rev else {}
            cur_ps = int(ch.get("current_revision_number", 0) or rev_obj.get("_number", 1) or 1)
            updates[str(num)] = (cur_rev, max(1, cur_ps))
        if updates:
            with self._session_lock:
                self._known_revisions_by_cl.update(updates)

    def _get_known_revision(self, cl_id: str) -> tuple[str, int]:
        """Returns `(current_revision, patch_set)` if previously seen in a queue response."""
        with self._session_lock:
            return self._known_revisions_by_cl.get(cl_id, ("", 0))

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

    def _lookup_cached_raw_queue(
        self, q_str: str, *, fast: bool
    ) -> tuple[list[dict[str, Any]], bool] | None:
        """Returns `(raw_changes_copy, is_stale)` if `q_str` has a usable full cache entry."""
        with self._session_lock:
            entry = self._raw_queue_cache.get(q_str)
        if entry is None:
            return None
        cached_at, cached_list, cached_is_full = entry
        if not cached_is_full:
            return None
        age = time.monotonic() - cached_at
        max_age = RAW_QUEUE_STALE_MAX_SEC if fast else RAW_QUEUE_CACHE_TTL_SEC
        if age >= max_age:
            return None
        return list(cached_list), age >= RAW_QUEUE_CACHE_TTL_SEC

    def _fetch_or_load_raw_queue_changes(
        self,
        q_str: str,
        *,
        limit: int,
        fast: bool,
        use_cache: bool,
    ) -> tuple[list[dict[str, Any]], bool, bool, bool]:
        """Fetches or loads raw Gerrit changes, returning `(changes, is_partial, from_cache, is_stale)`."""
        if use_cache:
            cached = self._lookup_cached_raw_queue(q_str, fast=fast)
            if cached is not None:
                return cached[0], False, True, cached[1]

        req_limit = min(limit, FAST_QUEUE_LIMIT) if fast else limit
        encoded_q = urllib.parse.quote(q_str, safe=":+()")
        endpoint = (
            f"/a/changes/?q={encoded_q}&n={req_limit}"
            "&o=CURRENT_REVISION&o=CURRENT_COMMIT&o=CURRENT_FILES"
            "&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS&o=MESSAGES&o=SUBMITTABLE"
        )
        fetched = self.client._request_json(endpoint)
        raw_list = fetched if isinstance(fetched, list) else []
        self._record_known_revisions(raw_list)
        is_partial_list = bool(
            fast
            and (
                (raw_list and raw_list[-1].get("_more_changes"))
                or len(raw_list) >= req_limit
            )
        )
        is_full_result = not is_partial_list
        with self._session_lock:
            if is_full_result or q_str not in self._raw_queue_cache:
                self._raw_queue_cache[q_str] = (time.monotonic(), list(raw_list), is_full_result)
        return raw_list, is_partial_list, False, False

    def _resolve_queue_bug_lookup(
        self, raw_changes: list[dict[str, Any]], *, fast: bool
    ) -> tuple[dict[str, dict[str, str]], bool]:
        """Returns `(bug_lookup, has_uncached_bugs)`, skipping blocking `issues` CLI when `fast=True`."""
        all_bug_ids: list[str] = []
        for ch in raw_changes:
            all_bug_ids.extend(extract_bug_ids_from_change(ch))
        if not all_bug_ids:
            return {}, False
        has_uncached = any(
            str(b).strip() not in self._bug_cache
            for b in all_bug_ids
            if str(b).strip().isdigit()
        )
        if fast:
            return dict(self._bug_cache), has_uncached
        return self.fetch_bug_metadata_batch(all_bug_ids), False

    def _collect_cached_primary_diffs(
        self, enriched: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Returns a cached diff bundle for the primary queue CL if already warm in memory."""
        if not enriched:
            return []
        cand = next(
            (c for c in enriched if c.get("presubmit_vetted") and not c.get("approved_by_viewer")),
            enriched[0],
        )
        cl_id = str(cand.get("number") or "")
        cur_rev = str(cand.get("current_revision") or "")
        if not cl_id or not cur_rev:
            return []
        with self._session_lock:
            entry = self._full_diff_bundle_cache.get((cl_id, cur_rev))
        if entry is None or (time.monotonic() - entry[0]) >= RAW_QUEUE_STALE_MAX_SEC:
            return []
        return [entry[1]]

    def fetch_queue(
        self,
        viewer_email: str | None = None,
        query: str | None = None,
        scope: str = "reviewer",
        author: str | None = None,
        limit: int = FULL_QUEUE_LIMIT,
        *,
        fast: bool = False,
        use_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetches open CLs matching the viewer's review queue (or custom `query`) and enriches them."""
        viewer = (viewer_email or self.default_viewer).strip().lower()
        norm_scope = (scope or "reviewer").strip().lower()
        norm_author = (author or "").strip()
        explicit_query = (query or self.default_query).strip()
        q_str = explicit_query or build_viewer_gerrit_query(
            viewer, scope=norm_scope, author=norm_author
        )
        raw_changes, is_partial_list, from_cache, is_stale = self._fetch_or_load_raw_queue_changes(
            q_str, limit=limit, fast=fast, use_cache=use_cache
        )

        if not explicit_query and norm_scope == "reviewer" and viewer:
            raw_changes = [ch for ch in raw_changes if is_explicit_reviewer(ch, viewer)]

        bug_lookup, has_uncached_bugs = self._resolve_queue_bug_lookup(raw_changes, fast=fast)
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
        payload: dict[str, Any] = {
            "ui_version": get_gerrit_ui_template_version(),
            "viewer": viewer,
            "auth_email": self.auth_email,
            "can_vote_cr2": not is_agent_or_bot_email(self.auth_email) if self.auth_email else True,
            "scope": norm_scope,
            "roster": list(TEAM_ROSTER_EMAILS),
            "query": q_str,
            "total_fetched": len(enriched),
            "is_partial": bool(is_partial_list or has_uncached_bugs),
            "from_cache": from_cache,
            "stale": is_stale,
            "changes": enriched,
            "stacks": stacks,
            "cliff_radar": cliff_summary,
        }
        prefetched_diffs = self._collect_cached_primary_diffs(enriched)
        if prefetched_diffs:
            payload["prefetched_diffs"] = prefetched_diffs
        return payload

    def _run_background_prewarm(self, viewer: str) -> None:
        """Warms `team`, `owner`, and `reviewer` queues and their primary diff bundles."""
        try:
            for sc in ("team", "owner", "reviewer"):
                q_payload = self.fetch_queue(
                    viewer_email=viewer, scope=sc, fast=False, use_cache=True
                )
                changes = q_payload.get("changes") or []
                if not changes:
                    continue
                cand = next(
                    (c for c in changes if c.get("presubmit_vetted") and not c.get("approved_by_viewer")),
                    changes[0],
                )
                if cand.get("number"):
                    self.fetch_commit_diff_bundle(cand["number"])
        except Exception:
            pass
        finally:
            with self._session_lock:
                self._prewarm_in_progress = False

    def schedule_background_prewarm(self, viewer_email: str | None = None) -> bool:
        """Schedules a cooldown-guarded background thread to warm sibling scopes and top diffs."""
        viewer = (viewer_email or self.default_viewer).strip().lower()
        if not viewer:
            return False
        now_mono = time.monotonic()
        with self._session_lock:
            if self._prewarm_in_progress:
                return False
            if (now_mono - self._last_prewarm_by_viewer.get(viewer, 0.0)) < PREWARM_COOLDOWN_SEC:
                return False
            self._prewarm_in_progress = True
            self._last_prewarm_by_viewer[viewer] = now_mono
        threading.Thread(
            target=self._run_background_prewarm, args=(viewer,), daemon=True
        ).start()
        return True

    @staticmethod
    def _sync_viewer_participant_vote(
        item: dict[str, Any],
        viewer: str,
        short: str,
        label_name: str,
        short_label: str,
        val: int,
    ) -> None:
        """Updates or inserts `viewer`'s vote badge in `item['reviewers_detail']`."""
        revs = item.setdefault("reviewers_detail", [])
        target = next((r for r in revs if (r.get("email") or "").lower() == viewer), None)
        if target is None and val != 0:
            target = {
                "account_id": None,
                "email": viewer,
                "short": short,
                "name": short,
                "full_name": short,
                "state": "REVIEWER",
                "is_bot": False,
                "is_team_agent": False,
                "is_owner": viewer == (item.get("owner_email") or "").lower(),
                "is_viewer": True,
                "in_attention_set": False,
                "removable": viewer != (item.get("owner_email") or "").lower(),
                "cr_vote": 0,
                "votes": [],
            }
            revs.insert(0, target)
        if target is None:
            return
        if label_name == "Code-Review":
            target["cr_vote"] = val
        votes = [v for v in (target.get("votes") or []) if v.get("label") != label_name]
        if val != 0:
            fmt = f"+{val}" if val > 0 else str(val)
            entry = {"label": label_name, "short_label": short_label, "value": val, "formatted": fmt}
            if label_name == "Code-Review":
                votes.insert(0, entry)
            else:
                votes.append(entry)
        target["votes"] = votes

    @staticmethod
    def _recompute_item_submittable(item: dict[str, Any]) -> None:
        """Recomputes `item['is_submittable']` after overlaying in-memory session votes."""
        req_count = int(item.get("required_cr2_count") or (2 if item.get("is_agent_cl") else 1))
        has_neg = any(int(p.get("cr_vote", 0) or 0) < 0 for p in (item.get("reviewers_detail") or []))
        g_sub = item.get("gerrit_submittable")
        item["is_submittable"] = _compute_is_submittable(
            human_cr2_count=len(item.get("human_cr2") or []),
            required_cr2_count=req_count,
            presubmit_vetted=bool(item.get("presubmit_vetted")),
            presubmit_status=item.get("presubmit_status"),
            mergeable=item.get("mergeable") is not False,
            has_negative_cr=has_neg,
            gerrit_submittable=g_sub if isinstance(g_sub, bool) else None,
        )

    @staticmethod
    def _sync_viewer_cr_approval(
        item: dict[str, Any], viewer: str, short: str, cr_val: int
    ) -> None:
        """Synchronizes `approved_by_viewer`, `human_cr2`, and `approval_slots` for `cr_val`."""
        item["gerrit_submittable"] = None
        if cr_val >= 2 and not item.get("approved_by_viewer"):
            item["approved_by_viewer"] = True
            cr2_list = item.setdefault("human_cr2", [])
            if not any((v.get("email") or "").lower() == viewer for v in cr2_list):
                cr2_list.append({"email": viewer, "name": short, "short": short, "value": 2})
                item["human_cr2_count"] = len(cr2_list)
            slot = next(
                (s for s in (item.get("approval_slots") or []) if s.get("state") != "done"),
                None,
            )
            if slot is not None:
                slot["state"] = "done"
                slot["text"] = "You (+2 ✓)"
            GerritUIService._recompute_item_submittable(item)
            return
        if cr_val < 2 and item.get("approved_by_viewer"):
            item["approved_by_viewer"] = False
            cr2_list = [
                v for v in (item.get("human_cr2") or []) if (v.get("email") or "").lower() != viewer
            ]
            item["human_cr2"] = cr2_list
            item["human_cr2_count"] = len(cr2_list)
        GerritUIService._recompute_item_submittable(item)

    def _apply_session_presubmit_ready_vote(
        self,
        item: dict[str, Any],
        viewer: str,
        short: str,
        pr_val: int,
        is_fresh: bool,
    ) -> None:
        """Applies recent session `Presubmit-Ready` vote while Gerrit's secondary index catches up."""
        if not is_fresh:
            return
        voters = [v for v in (item.get("presubmit_ready_voters") or []) if v != short]
        if pr_val >= 1:
            if item.get("presubmit_status") == "running":
                return
            voters.append(short)
            item["viewer_presubmit_ready"] = True
            item["presubmit_ready_voters"] = voters
            item["presubmit_ready_enabled"] = True
            item["presubmit_status"] = "queued"
            item["presubmit_label"] = "⏳ Presubmit Queued"
            self._sync_viewer_participant_vote(item, viewer, short, "Presubmit-Ready", "PR", 1)
            return
        item["viewer_presubmit_ready"] = False
        item["presubmit_ready_voters"] = voters
        item["presubmit_ready_enabled"] = bool(voters)
        if not voters and item.get("presubmit_status") == "queued":
            item["presubmit_status"] = "not_started"
            item["presubmit_label"] = "⏸ Presubmit 0"
        self._sync_viewer_participant_vote(item, viewer, short, "Presubmit-Ready", "PR", 0)

    def _apply_single_session_vote(
        self,
        item: dict[str, Any],
        viewer: str,
        short: str,
        vote_map: dict[str, int],
        pr_is_fresh: bool = False,
    ) -> None:
        """Applies cached session label votes (`Code-Review`, `Autosubmit`, `Presubmit-Ready`) to `item`."""
        if "Code-Review" in vote_map:
            cr_val = int(vote_map["Code-Review"])
            item["viewer_cr_vote"] = cr_val
            self._sync_viewer_participant_vote(item, viewer, short, "Code-Review", "CR", cr_val)
            self._sync_viewer_cr_approval(item, viewer, short, cr_val)
        if "Autosubmit" in vote_map:
            as_val = int(vote_map["Autosubmit"])
            item["viewer_autosubmit"] = as_val >= 1
            voters = [v for v in (item.get("autosubmit_voters") or []) if v != short]
            if as_val >= 1:
                voters.append(short)
            item["autosubmit_voters"] = voters
            item["autosubmit_enabled"] = bool(voters)
            self._sync_viewer_participant_vote(item, viewer, short, "Autosubmit", "AS", as_val)
        if "Presubmit-Ready" in vote_map:
            self._apply_session_presubmit_ready_vote(
                item, viewer, short, int(vote_map["Presubmit-Ready"]), pr_is_fresh
            )

    def _apply_session_approvals(
        self, enriched: list[dict[str, Any]], viewer_email: str
    ) -> None:
        """Applies in-memory session votes when Gerrit's secondary search index lags."""
        now_mono = time.monotonic()
        with self._session_lock:
            approved_snapshot = set(self._session_approved_cls)
            votes_snapshot = {k: dict(v) for k, v in self._session_votes.items()}
            pr_times = dict(self._session_presubmit_voted_at)
        if not approved_snapshot and not votes_snapshot:
            return
        active_voter = (self.auth_email or self.default_viewer or "").strip().lower()
        viewer = (viewer_email or "").strip().lower()
        if active_voter and viewer and active_voter != viewer:
            return
        short = (viewer or "you").split("@", 1)[0]
        for item in enriched:
            cl_num = int(item.get("number", 0) or 0)
            merged_votes = dict(votes_snapshot.get(cl_num) or {})
            if cl_num in approved_snapshot and "Code-Review" not in merged_votes:
                merged_votes["Code-Review"] = 2
            if merged_votes:
                pr_fresh = (now_mono - pr_times.get(cl_num, 0.0)) <= SESSION_PRESUBMIT_LAG_SEC
                self._apply_single_session_vote(item, viewer, short, merged_votes, pr_is_fresh=pr_fresh)

    def _resolve_is_pure_rebase(self, cl_id: str, known_ps: int) -> bool:
        """Checks whether the current patchset is a pure rebase, skipping `get_change_detail` when known."""
        if known_ps == 1:
            return False
        try:
            cur_ps = known_ps
            if cur_ps <= 0:
                detail = self.client.get_change_detail(cl_id, include_files=False)
                cur_ps = int(detail.get("current_revision_number", 1) or 1)
            if cur_ps <= 1:
                return False
            interdiff = self.client._request_json(
                f"/a/changes/{cl_id}/revisions/current/files?base={cur_ps - 1}"
            )
            if not isinstance(interdiff, dict):
                return False
            non_msg = [k for k in interdiff.keys() if k != "/COMMIT_MSG"]
            return len(non_msg) == 0
        except Exception:
            return False

    def _load_or_fetch_revision_diff_data(
        self, cl_id: str, known_rev: str, known_ps: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetches or reuses decoded patch + pure-rebase metadata concurrently with threads and Q&A."""
        cached_rev_data: dict[str, Any] | None = None
        if known_rev:
            with self._session_lock:
                cached_rev_data = self._diff_revision_cache.get((cl_id, known_rev))

        with ThreadPoolExecutor(max_workers=DIFF_BUNDLE_SUBTASK_WORKERS) as pool:
            threads_fut = pool.submit(
                lambda: [t.to_dict() for t in self.client.get_comment_threads(cl_id, include_resolved=False)]
            )
            qa_fut = pool.submit(self.get_code_questions, [cl_id])
            if cached_rev_data is not None:
                return (
                    cached_rev_data,
                    threads_fut.result(),
                    self._enrich_questions_with_diff_symbols(qa_fut.result(), cached_rev_data["files"]),
                )

            patch_fut = pool.submit(self.client.get_revision_patch, cl_id, "current")
            rebase_fut = pool.submit(self._resolve_is_pure_rebase, cl_id, known_ps)
            decoded_patch = decode_gerrit_patch(patch_fut.result())
            rev_data = {
                "commit_message": extract_commit_message_from_patch(decoded_patch),
                "is_pure_rebase": rebase_fut.result(),
                "files": parse_unified_diff_files(decoded_patch),
            }
            return (
                rev_data,
                threads_fut.result(),
                self._enrich_questions_with_diff_symbols(qa_fut.result(), rev_data["files"]),
            )

    def _store_diff_bundle_caches(
        self,
        cl_id: str,
        known_rev: str,
        rev_data: dict[str, Any],
        full_bundle: dict[str, Any],
    ) -> None:
        """Stores revision-immutable diff data and TTL-bounded full diff bundle when `known_rev` is known."""
        if not known_rev:
            return
        cache_key = (cl_id, known_rev)
        with self._session_lock:
            if len(self._diff_revision_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._diff_revision_cache.pop(next(iter(self._diff_revision_cache)), None)
            if len(self._full_diff_bundle_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._full_diff_bundle_cache.pop(next(iter(self._full_diff_bundle_cache)), None)
            self._diff_revision_cache[cache_key] = rev_data
            self._full_diff_bundle_cache[cache_key] = (time.monotonic(), full_bundle)

    def fetch_commit_diff_bundle(self, cl_number: str | int) -> dict[str, Any]:
        """Fetches decoded unified diff files, pure-rebase status, and comment threads for one CL."""
        cl_id = str(cl_number).strip()
        known_rev, known_ps = self._get_known_revision(cl_id)
        if known_rev:
            with self._session_lock:
                cached = self._full_diff_bundle_cache.get((cl_id, known_rev))
            if cached is not None and (time.monotonic() - cached[0]) < RAW_QUEUE_CACHE_TTL_SEC:
                return dict(cached[1])

        rev_data, threads, code_questions = self._load_or_fetch_revision_diff_data(
            cl_id, known_rev, known_ps
        )
        bundle = {
            "number": int(cl_id) if cl_id.isdigit() else cl_id,
            "commit_message": rev_data["commit_message"],
            "is_pure_rebase": rev_data["is_pure_rebase"],
            "files": rev_data["files"],
            "unresolved_threads": threads,
            "code_questions": code_questions,
        }
        self._store_diff_bundle_caches(cl_id, known_rev, rev_data, bundle)
        return bundle

    def _enrich_questions_with_diff_symbols(
        self,
        questions: list[dict[str, Any]],
        diff_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Populates `function_name` on inline Q&A items from parsed `diff_files` when missing."""
        if not questions or not diff_files:
            return questions
        lines_by_file = {
            str(f.get("file") or ""): (f.get("lines") or [])
            for f in diff_files
            if isinstance(f, dict)
        }
        for q in questions:
            if q.get("function_name"):
                continue
            fpath = str(q.get("file_path") or "")
            file_lines = lines_by_file.get(fpath) or []
            if file_lines:
                sym = extract_enclosing_function_from_diff_lines(
                    file_lines,
                    int(q.get("start_line") or q.get("line") or 1),
                    str(q.get("side") or "REVISION"),
                )
                if sym:
                    q["function_name"] = sym
        return questions

    def _merge_tracked_qa_state(
        self,
        transcript_items: list[dict[str, Any]],
        filter_cls: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Merges in-memory `self._qa_threads` metadata and pending turns with transcript items."""
        with self._qa_lock:
            threads_snapshot = [
                dict(t, turns=[dict(turn) for turn in (t.get("turns") or [])])
                for t in self._qa_threads.values()
                if not t.get("deleted")
            ]

        by_conv = {
            str(it.get("conversation_id") or ""): it
            for it in transcript_items
            if it.get("conversation_id")
        }
        merged: list[dict[str, Any]] = []
        handled_convs: set[str] = set()

        for th in threads_snapshot:
            cl_str = str(th.get("cl") or "")
            if filter_cls and cl_str not in filter_cls:
                continue
            cid = str(th.get("conversation_id") or "")
            tx_item = by_conv.get(cid) if cid else None
            if tx_item is not None:
                handled_convs.add(cid)
                combined = dict(tx_item)
                combined["thread_id"] = th["thread_id"]
                if th.get("function_name"):
                    combined["function_name"] = th["function_name"]
                tx_turns = list(tx_item.get("turns") or [])
                mem_turns = list(th.get("turns") or [])
                if len(mem_turns) > len(tx_turns):
                    tx_turns.extend(mem_turns[len(tx_turns) :])
                if tx_turns:
                    combined["turns"] = tx_turns
                    combined["question"] = tx_turns[-1]["question"]
                    combined["reply"] = tx_turns[-1]["reply"]
                    combined["status"] = tx_turns[-1]["status"]
                self._sync_cached_turns_from_transcript(th["thread_id"], tx_turns)
                merged.append(combined)
                continue
            turns = list(th.get("turns") or [])
            latest = turns[-1] if turns else {"question": "", "reply": "", "status": "thinking"}
            merged.append({
                "id": th["thread_id"],
                "thread_id": th["thread_id"],
                "conversation_id": cid,
                "cl": int(cl_str) if cl_str.isdigit() else cl_str,
                "file_path": th.get("file_path") or "/COMMIT_MSG",
                "line": th.get("end_line") or 1,
                "start_line": th.get("start_line") or 1,
                "end_line": th.get("end_line") or 1,
                "side": th.get("side") or "REVISION",
                "function_name": th.get("function_name") or "",
                "code_snippet": th.get("code_snippet") or "",
                "question": latest["question"],
                "reply": latest["reply"],
                "status": latest["status"],
                "turns": turns,
            })

        for it in transcript_items:
            cid = str(it.get("conversation_id") or "")
            if cid and cid in handled_convs:
                continue
            merged.append(it)
        return merged

    def _sync_cached_turns_from_transcript(
        self, thread_id: str, tx_turns: list[dict[str, Any]]
    ) -> None:
        """Updates cached `turns` on `thread_id` once transcript replies land on disk."""
        if not tx_turns:
            return
        with self._qa_lock:
            entry = self._qa_threads.get(thread_id)
            if entry and not entry.get("deleted"):
                entry["turns"] = [dict(t) for t in tx_turns]

    def get_code_questions(
        self,
        cl_numbers: list[str | int],
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns inline code questions and their Jetski agent replies for `cl_numbers`."""
        cleaned = [str(x).strip() for x in cl_numbers if str(x).strip()]
        filter_cls = (
            {parse_cl_identifier(x, allow_head=False) for x in cleaned}
            if cleaned
            else None
        )
        with self._qa_lock:
            tracked = set(self._tracked_qa_conv_ids)
            deleted = set(self._deleted_qa_conv_ids) | set(self._analyzer_conv_ids)
            ignored = {k: set(v) for k, v in self._ignored_steps_by_conv.items()}
        tx_items = discover_transcript_code_questions(
            conversation_id=conversation_id,
            cl_numbers=cleaned or None,
            extra_conversation_ids=tracked,
            excluded_conversation_ids=deleted,
            ignored_steps_by_conv=ignored,
        )
        return self._merge_tracked_qa_state(tx_items, filter_cls)

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

    @staticmethod
    def _build_vote_labels(
        code_review: int | None,
        autosubmit: bool | int | None,
        presubmit_ready: bool | int | None,
        *,
        allow_empty: bool = False,
    ) -> dict[str, int]:
        """Constructs Gerrit `labels` dict for `Code-Review`, `Autosubmit`, and `Presubmit-Ready`."""
        labels: dict[str, int] = {}
        if code_review is not None:
            labels["Code-Review"] = int(code_review)
        if isinstance(autosubmit, bool):
            if autosubmit:
                labels["Autosubmit"] = 1
        elif autosubmit is not None:
            labels["Autosubmit"] = int(autosubmit)
        if isinstance(presubmit_ready, bool):
            if presubmit_ready:
                labels["Presubmit-Ready"] = 1
        elif presubmit_ready is not None:
            labels["Presubmit-Ready"] = int(presubmit_ready)
        if not labels and not allow_empty:
            raise ValueError(
                "Cannot cast Gerrit vote: no label values specified "
                "(provide code_review, autosubmit, or presubmit_ready)."
            )
        return labels

    def _record_session_vote_results(
        self, results: list[dict[str, Any]], labels: dict[str, int]
    ) -> None:
        """Caches successful session votes in memory to mask Gerrit secondary-index lag."""
        cr_label = labels.get("Code-Review")
        has_pr = "Presubmit-Ready" in labels
        now_mono = time.monotonic()
        mutated_cl_ids: list[str] = []
        with self._session_lock:
            for r in results:
                raw_cl_num = str(r.get("cl") or "").strip()
                if not (r.get("ok") and raw_cl_num.isdigit()):
                    continue
                mutated_cl_ids.append(raw_cl_num)
                cl_num = int(raw_cl_num)
                self._session_votes[cl_num] = {**self._session_votes.get(cl_num, {}), **labels}
                if has_pr:
                    self._session_presubmit_voted_at[cl_num] = now_mono
                    stale_keys = [k for k in self._presubmit_targets_cache if str(k[0]) == raw_cl_num]
                    for k in stale_keys:
                        self._presubmit_targets_cache.pop(k, None)
                if cr_label is None:
                    continue
                if cr_label >= 2:
                    self._session_approved_cls.add(cl_num)
                else:
                    self._session_approved_cls.discard(cl_num)
        if mutated_cl_ids:
            self._invalidate_cl_caches(mutated_cl_ids)

    def cast_batch_vote(
        self,
        cl_numbers: list[str | int],
        code_review: int | None = 2,
        autosubmit: bool | int | None = False,
        presubmit_ready: bool | int | None = False,
        message: str = "",
    ) -> dict[str, Any]:
        """Casts labels across one or more CLs using `gob-curl` with mandatory `notify: OWNER`."""
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot cast Gerrit vote: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        clean_msg = message.strip()
        labels = self._build_vote_labels(
            code_review,
            autosubmit,
            presubmit_ready,
            allow_empty=bool(clean_msg),
        )
        cr_hint = int(labels.get("Code-Review", 0))
        payload: dict[str, Any] = {"notify": "OWNER"}
        if labels:
            payload["labels"] = labels
        if clean_msg:
            payload["message"] = clean_msg

        cleaned_cls = [parse_cl_identifier(str(raw_cl), allow_head=False) for raw_cl in cl_numbers]
        payload_json = json.dumps(payload)
        workers = max(1, min(DIFF_FETCH_WORKERS, len(cleaned_cls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda cid: self._post_single_vote(gob_curl, cid, payload_json, cr_hint),
                    cleaned_cls,
                )
            )

        if labels:
            self._record_session_vote_results(results, labels)
        elif cleaned_cls:
            self._invalidate_cl_caches(cleaned_cls)
        all_ok = all(r["ok"] for r in results)
        cloudtop_cmd = " && ".join(
            f"gob-curl -s -X POST -H 'Content-Type: application/json' -d '{payload_json}' "
            f"'{self.client.api_host}/a/changes/{cid}/revisions/current/review' >/dev/null"
            for cid in cleaned_cls
        )
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "partial_error",
            "auth_email": self.auth_email,
            "labels": labels,
            "cloudtop_cmd": cloudtop_cmd,
            "results": results,
        }
        if not all_ok:
            first_err = next((r["error"] for r in results if not r["ok"] and r.get("error")), "Gerrit vote failed")
            out["error"] = first_err
        return out

    def _post_single_submit(
        self, gob_curl: str, cl_id: str, payload_json: str
    ) -> dict[str, Any]:
        """Submits one CL (or its whole topic when `submitWholeTopic` is active) with `notify: OWNER`."""
        url = f"{self.client.api_host}/a/changes/{cl_id}/submit"
        try:
            res = subprocess.run(
                [
                    gob_curl,
                    "-s",
                    "-S",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload_json,
                    url,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=GERRIT_CLI_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return {
                "cl": cl_id,
                "ok": False,
                "status": "TIMEOUT",
                "error": (
                    f"Timed out after {GERRIT_CLI_TIMEOUT_SEC}s while submitting ag/{cl_id} via `gob-curl`. "
                    f"Check Gerrit whether ag/{cl_id} already merged and verify network/`gcert` before retrying."
                ),
            }
        except OSError as exc:
            return {
                "cl": cl_id,
                "ok": False,
                "status": "ERROR",
                "error": (
                    f"Failed to execute `{gob_curl}` while submitting ag/{cl_id}: {exc}. "
                    "Verify `gob-curl` installation and permissions on PATH."
                ),
            }
        combined_out = f"{(res.stdout or '').strip()} {(res.stderr or '').strip()}".strip()
        if "change is merged" in combined_out.lower():
            return {"cl": cl_id, "ok": True, "status": "MERGED", "error": ""}
        if res.returncode == 0:
            raw_body = strip_xssi_prefix(res.stdout or "").strip()
            if raw_body:
                try:
                    parsed_body = json.loads(raw_body)
                    if isinstance(parsed_body, dict) and str(parsed_body.get("status") or "").upper() in {
                        "MERGED",
                        "SUBMITTED",
                    }:
                        return {
                            "cl": cl_id,
                            "ok": True,
                            "status": str(parsed_body["status"]),
                            "error": "",
                        }
                except ValueError:
                    pass
        detail = combined_out or f"gob-curl exited with status {res.returncode}"
        err_msg = (
            f"Failed to submit ag/{cl_id}: {detail}. "
            f"Verify submit requirements, mergeability, and `gcert` credentials, or open ag/{cl_id} in Gerrit."
        )
        if self.auth_email and is_agent_or_bot_email(self.auth_email):
            err_msg = (
                f"Backend is authenticated as role account '{self.auth_email}' "
                f"and cannot submit ag/{cl_id} directly ({detail}). "
                "Open in Gerrit or run the copied Cloudtop command to submit."
            )
        return {"cl": cl_id, "ok": False, "status": "ERROR", "error": err_msg}

    def submit_changes(self, cl_numbers: list[str | int]) -> dict[str, Any]:
        """Submits one or more submittable CLs/topics via `gob-curl` with mandatory `notify: OWNER`."""
        if not cl_numbers:
            raise ValueError(
                "Cannot submit Gerrit change: no CL numbers specified (provide `cl` or non-empty `cls`)."
            )
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot submit Gerrit change: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        cleaned_cls: list[str] = []
        seen: set[str] = set()
        for raw_cl in cl_numbers:
            cid = parse_cl_identifier(str(raw_cl), allow_head=False)
            if cid not in seen:
                seen.add(cid)
                cleaned_cls.append(cid)

        payload_json = json.dumps({"notify": "OWNER"})
        results: list[dict[str, Any]] = []
        for cid in cleaned_cls:
            step = self._post_single_submit(gob_curl, cid, payload_json)
            results.append(step)
            if not step["ok"]:
                break

        merged_ids = [r["cl"] for r in results if r["ok"]]
        if merged_ids:
            with self._session_lock:
                self._raw_queue_cache.clear()
                for cid in merged_ids:
                    if cid.isdigit():
                        self._session_approved_cls.discard(int(cid))
                        self._session_votes.pop(int(cid), None)
            self._invalidate_cl_caches(merged_ids)

        all_ok = len(results) == len(cleaned_cls) and all(r["ok"] for r in results)
        cloudtop_cmd = " && ".join(
            f"gob-curl -s -X POST -H 'Content-Type: application/json' -d '{payload_json}' "
            f"'{self.client.api_host}/a/changes/{cid}/submit' >/dev/null"
            for cid in cleaned_cls
        )
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "error",
            "auth_email": self.auth_email,
            "submitted_cls": merged_ids,
            "cloudtop_cmd": cloudtop_cmd,
            "results": results,
        }
        if not all_ok:
            first_err = next(
                (r["error"] for r in results if not r["ok"] and r.get("error")),
                "Gerrit submit failed (verify all submit requirements and mergeability).",
            )
            out["error"] = first_err
        return out

    def assign_second_human_reviewer(self, cl_number: str | int, reviewer_email: str) -> dict[str, Any]:
        """Adds a 2nd human reviewer to an agent CL with `notify: OWNER`."""
        return self.modify_reviewer(
            cl_number=cl_number,
            action="add",
            account=reviewer_email,
            state="REVIEWER",
        )

    def fetch_single_enriched_change(
        self, cl_id: str, viewer_email: str | None = None
    ) -> dict[str, Any] | None:
        """Fetches fresh primary-storage change detail for `cl_id` and returns its UI-enriched dict."""
        viewer = (viewer_email or self.default_viewer).strip().lower()
        encoded_id = urllib.parse.quote(str(cl_id), safe="")
        endpoint = (
            f"/a/changes/{encoded_id}/detail"
            "?o=CURRENT_REVISION&o=ALL_REVISIONS&o=CURRENT_COMMIT&o=CURRENT_FILES"
            "&o=MESSAGES&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS&o=SUBMITTABLE"
        )
        detail = self.client._request_json(endpoint)
        if not isinstance(detail, dict):
            return None
        bug_ids = extract_bug_ids_from_change(detail)
        bug_lookup = self.fetch_bug_metadata_batch(bug_ids) if bug_ids else self._bug_cache
        enriched = enrich_change_for_ui(detail, viewer, bug_lookup=bug_lookup)
        self._apply_session_approvals([enriched], viewer)
        return enriched

    def _fetch_remote_reviewer_suggestions(
        self, cl_number: str | int, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Queries Gerrit `/suggest_reviewers` and normalizes account/group entries."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        enc_q = urllib.parse.quote(query, safe="@._-")
        raw_list = self.client._request_json(
            f"/a/changes/{cl_id}/suggest_reviewers?q={enc_q}&n={max(1, int(limit))}"
        )
        if not isinstance(raw_list, list):
            return []
        parsed: list[dict[str, Any]] = []
        for item in raw_list:
            acct = item.get("account") if isinstance(item, dict) else None
            grp = item.get("group") if isinstance(item, dict) else None
            if isinstance(acct, dict):
                em = (acct.get("email") or "").strip().lower()
                val = em or str(acct.get("_account_id") or "")
                short = em.split("@", 1)[0] if em else (acct.get("name") or val)
                if val:
                    parsed.append({
                        "value": val,
                        "email": em,
                        "name": (acct.get("display_name") or acct.get("name") or short).strip(),
                        "short": short,
                        "is_group": False,
                    })
            elif isinstance(grp, dict) and grp.get("name"):
                gname = str(grp["name"]).strip()
                parsed.append({
                    "value": gname,
                    "email": "",
                    "name": gname,
                    "short": gname,
                    "is_group": True,
                })
        return parsed

    def suggest_reviewers(
        self, cl_number: str | int, query: str = "", limit: int = 8
    ) -> list[dict[str, Any]]:
        """Returns reviewer/CC autocomplete candidates combining Gerrit `suggest_reviewers` and team roster."""
        q = (query or "").strip()
        q_lower = q.lower()
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        if len(q) >= 2 and str(cl_number).strip():
            try:
                for entry in self._fetch_remote_reviewer_suggestions(cl_number, q, limit):
                    dedup_key = entry["value"].lower()
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        suggestions.append(entry)
            except Exception:
                pass

        for roster_em in TEAM_ROSTER_EMAILS:
            if roster_em not in seen and (not q_lower or q_lower in roster_em):
                seen.add(roster_em)
                short = roster_em.split("@", 1)[0]
                suggestions.append({
                    "value": roster_em,
                    "email": roster_em,
                    "name": short,
                    "short": short,
                    "is_group": False,
                })
        return suggestions[: max(1, int(limit))]

    def _execute_reviewer_mutation(
        self, cl_id: str, norm_action: str, raw_acct: str, state: str
    ) -> tuple[str, str, Any]:
        """Executes the Gerrit REST reviewer add/move or delete call with `notify: OWNER`."""
        if norm_action == "remove":
            enc_acct = urllib.parse.quote(raw_acct, safe="")
            try:
                resp = self.client._request_json(
                    f"/a/changes/{cl_id}/reviewers/{enc_acct}/delete",
                    method="POST",
                    data={"notify": "OWNER"},
                )
            except RuntimeError as exc:
                if "exit code 0" not in str(exc):
                    raise
                resp = {}
            return "REMOVED", raw_acct, resp
        if norm_action == "add":
            norm_state = "CC" if str(state or "").strip().upper() == "CC" else "REVIEWER"
            resolved_acct = (
                f"{raw_acct.lower()}@google.com"
                if _SIMPLE_LDAP_RE.fullmatch(raw_acct) and not raw_acct.isdigit()
                else raw_acct
            )
            resp = self.client._request_json(
                f"/a/changes/{cl_id}/reviewers",
                method="POST",
                data={"reviewer": resolved_acct, "state": norm_state, "notify": "OWNER"},
            )
            if isinstance(resp, dict) and resp.get("error"):
                err_detail = str(resp["error"]).strip()
                raise RuntimeError(
                    f"Failed to add {norm_state} '{resolved_acct}' to CL {cl_id}: {err_detail}. "
                    "Verify the Gerrit email, LDAP, or group name and your CL permissions."
                )
            return norm_state, resolved_acct, resp
        raise ValueError(f"Unsupported reviewer action '{norm_action}' (expected 'add' or 'remove').")

    def modify_reviewer(
        self,
        cl_number: str | int,
        action: str,
        account: str | int,
        *,
        state: str = "REVIEWER",
        viewer_email: str | None = None,
    ) -> dict[str, Any]:
        """Adds, moves (`REVIEWER` <-> `CC`), or removes a reviewer/CC on `cl_number` with `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        norm_action = (action or "add").strip().lower()
        raw_acct = str(account or "").strip()
        if not raw_acct:
            raise ValueError("Reviewer or CC account email/ID is required.")
        norm_state, resolved_acct, resp = self._execute_reviewer_mutation(
            cl_id, norm_action, raw_acct, state
        )
        self._invalidate_cl_caches([cl_id])
        updated_change: dict[str, Any] | None = None
        try:
            updated_change = self.fetch_single_enriched_change(cl_id, viewer_email=viewer_email)
        except Exception:
            updated_change = None
        return {
            "status": "ok",
            "cl": cl_id,
            "action": norm_action,
            "reviewer": resolved_acct,
            "state": norm_state,
            "notify": "OWNER",
            "response": resp,
            "change": updated_change,
        }

    def _modify_single_reviewer_for_batch(
        self,
        cl_id: str,
        norm_action: str,
        raw_acct: str,
        state: str,
        viewer_email: str | None,
    ) -> dict[str, Any]:
        """Executes a reviewer/CC mutation on one CL and returns structured status."""
        try:
            res = self.modify_reviewer(
                cl_id,
                norm_action,
                raw_acct,
                state=state,
                viewer_email=viewer_email,
            )
            return {
                "cl": cl_id,
                "ok": True,
                "reviewer": res["reviewer"],
                "state": res["state"],
                "response": res["response"],
                "change": res["change"],
                "error": "",
            }
        except Exception as exc:
            return {
                "cl": cl_id,
                "ok": False,
                "reviewer": raw_acct,
                "state": (state or "REVIEWER").strip().upper(),
                "error": str(exc),
            }

    def modify_reviewers_batch(
        self,
        cl_numbers: list[str | int],
        action: str,
        account: str | int,
        *,
        state: str = "REVIEWER",
        viewer_email: str | None = None,
    ) -> dict[str, Any]:
        """Batch adds, moves (`REVIEWER` <-> `CC`), or removes a reviewer/CC across multiple CLs with `notify: OWNER`."""
        if not cl_numbers:
            raise ValueError(
                "Cannot modify reviewers in batch: no target CL numbers provided "
                "(pass non-empty 'cls' or 'cl')."
            )
        norm_action = (action or "add").strip().lower()
        if norm_action not in {"add", "remove"}:
            raise ValueError(
                f"Cannot modify reviewers in batch: unsupported action '{norm_action}' "
                "(expected 'add' or 'remove')."
            )
        raw_acct = str(account or "").strip()
        if not raw_acct:
            raise ValueError(
                "Cannot modify reviewers in batch: reviewer or CC account is empty "
                "(provide a non-empty 'account' or 'reviewer' email, LDAP, or group name)."
            )
        cleaned_cls: list[str] = []
        for raw_cl in cl_numbers:
            cid = parse_cl_identifier(str(raw_cl), allow_head=False)
            if cid not in cleaned_cls:
                cleaned_cls.append(cid)
        workers = max(1, min(DIFF_FETCH_WORKERS, len(cleaned_cls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda cid: self._modify_single_reviewer_for_batch(
                        cid, norm_action, raw_acct, state, viewer_email
                    ),
                    cleaned_cls,
                )
            )
        all_ok = all(r["ok"] for r in results)
        resolved_reviewer = next(
            (r["reviewer"] for r in results if r["ok"] and r.get("reviewer")),
            raw_acct,
        )
        norm_state = next(
            (r["state"] for r in results if r.get("state")),
            (state or "REVIEWER").strip().upper(),
        )
        updated_changes = [
            r["change"] for r in results if r["ok"] and isinstance(r.get("change"), dict)
        ]
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "partial_error",
            "cls": cleaned_cls,
            "action": norm_action,
            "reviewer": resolved_reviewer,
            "state": norm_state,
            "notify": "OWNER",
            "results": results,
            "changes": updated_changes,
        }
        if len(updated_changes) == 1 and len(cleaned_cls) == 1:
            out["change"] = updated_changes[0]
        if not all_ok:
            first_err = next(
                (r["error"] for r in results if not r["ok"] and r.get("error")),
                f"Batch reviewer {norm_action} failed on one or more CLs.",
            )
            out["error"] = first_err
        return out

    def _resolve_chat_sender_email(self) -> str:
        """Returns the authenticated human `@google.com` email running the UI server."""
        auth = (self.auth_email or "").strip().lower()
        if auth and _GOOGLE_CORP_EMAIL_RE.fullmatch(auth) and not is_agent_or_bot_email(auth):
            return auth
        os_user = (os.environ.get("USER") or "").strip().lower()
        if os_user and _SIMPLE_LDAP_RE.fullmatch(os_user):
            return f"{os_user}@google.com"
        return ""

    def send_reviewer_chat_dm(
        self,
        cl_number: str | int,
        recipient: str,
        message: str,
    ) -> dict[str, Any]:
        """Sends a 1:1 Google Chat direct message to `recipient` via `gchat` after UI confirmation."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        resolved_email = _normalize_chat_dm_recipient(recipient, self._resolve_chat_sender_email())
        msg_text = str(message or "").strip()
        if not msg_text:
            raise ValueError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                "message text is empty. Enter a non-empty review reminder message."
            )
        if len(msg_text) > MAX_CHAT_DM_LENGTH:
            raise ValueError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                f"message length ({len(msg_text)} chars) exceeds {MAX_CHAT_DM_LENGTH}. "
                "Shorten the message and retry."
            )
        gchat_bin = _resolve_gchat_cli_bin()
        if not gchat_bin:
            raise RuntimeError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                f"Google Chat CLI ('{DEFAULT_GCHAT_CLI_BIN}') was not found on this host. "
                "Run Fast Gerrit UI on gLinux/Cloudtop or click 'Copy Text' to paste into Chat."
            )
        proc = _run_gchat_send_dm_command(gchat_bin, resolved_email, msg_text, cl_id)
        space_name, message_name, chat_url = _parse_gchat_dm_output(proc.stdout)
        return {
            "status": "ok",
            "cl": cl_id,
            "recipient": resolved_email,
            "space_name": space_name,
            "message_name": message_name,
            "chat_url": chat_url,
        }

    def _resolve_change_patchset_and_project(
        self, cl_id: str, patchset: int | None, project: str
    ) -> tuple[int, str]:
        """Resolves `(patchset, project)` from explicit args or Gerrit change detail."""
        resolved_ps = int(patchset or 0)
        resolved_proj = (project or "").strip()
        if resolved_ps > 0 and resolved_proj:
            return resolved_ps, resolved_proj
        detail = self.client.get_change_detail(cl_id, include_files=False)
        if resolved_ps <= 0:
            resolved_ps = int(detail.get("current_revision_number", 1) or 1)
        if not resolved_proj:
            resolved_proj = str(detail.get("project") or "")
        return max(1, resolved_ps), resolved_proj

    def get_presubmit_targets(
        self,
        cl_number: str | int,
        patchset: int | None = None,
        project: str = "",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Fetches TreeHugger build/test targets for `(cl_number, patchset)` with TTL caching."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False, strict=True)
        if not cl_id.isdigit():
            raise ValueError(
                f"Cannot query presubmit targets for '{cl_number}': expected a numeric Gerrit CL ID "
                "(e.g. '43234462' or 'ag/43234462')."
            )
        resolved_ps, resolved_proj = self._resolve_change_patchset_and_project(
            cl_id, patchset, project
        )
        cache_key = (cl_id, resolved_ps)
        now_mono = time.monotonic()
        if not force_refresh:
            with self._session_lock:
                cached_entry = self._presubmit_targets_cache.get(cache_key)
            if cached_entry is not None:
                cached_at, payload = cached_entry
                ttl = (
                    COMPLETED_TARGETS_CACHE_TTL_SEC
                    if payload.get("run_status") in ("passed", "failed", "warning")
                    else ACTIVE_TARGETS_CACHE_TTL_SEC
                )
                if now_mono - cached_at < ttl:
                    return payload

        payload = query_presubmit_targets_via_stubby(
            cl_id=cl_id,
            patchset=resolved_ps,
            gerrit_host=self.host,
            project=resolved_proj,
        )
        with self._session_lock:
            voted_at = self._session_presubmit_voted_at.get(int(cl_id), 0.0)
            existing = self._presubmit_targets_cache.get(cache_key)
            if now_mono >= voted_at and (existing is None or existing[0] <= now_mono):
                self._presubmit_targets_cache[cache_key] = (time.monotonic(), payload)
        return payload

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
        if post_res["ok"]:
            self._invalidate_cl_caches([cl_id])
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

    def post_inline_comment(
        self,
        cl_number: str | int,
        file_path: str,
        line: int,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        side: str = "REVISION",
        unresolved: bool = True,
    ) -> dict[str, Any]:
        """Posts an inline file/line/range comment on `cl_number` with mandatory `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
        cleaned_msg = (message or "").strip()
        if not cleaned_msg:
            raise ValueError("Comment message cannot be empty.")

        target_line = max(1, int(end_line or line or 1))
        s_ln = max(1, int(start_line or target_line))
        e_ln = max(s_ln, int(end_line or target_line))
        side_norm = "PARENT" if str(side or "").strip().upper() == "PARENT" else "REVISION"

        comment_entry: dict[str, Any] = {
            "line": e_ln,
            "side": side_norm,
            "message": cleaned_msg,
            "unresolved": bool(unresolved),
        }
        if e_ln > s_ln:
            comment_entry["range"] = {
                "start_line": s_ln,
                "start_character": 0,
                "end_line": e_ln,
                "end_character": 0,
            }

        payload: dict[str, Any] = {
            "notify": "OWNER",
            "comments": {
                cleaned_file: [comment_entry],
            },
        }
        endpoint = f"/a/changes/{cl_id}/revisions/current/review"
        try:
            self.client._request_json(endpoint, method="POST", data=payload)
        except Exception as exc:
            gob_curl = shutil.which("gob-curl")
            if not gob_curl:
                raise
            post_res = self._post_single_vote(gob_curl, cl_id, json.dumps(payload), code_review=0)
            if not post_res.get("ok"):
                raise RuntimeError(post_res.get("error") or str(exc)) from exc

        self._invalidate_cl_caches([cl_id])
        author = (self.auth_email or self.default_viewer or "you").split("@")[0]
        return {
            "status": "ok",
            "cl": cl_id,
            "file_path": cleaned_file,
            "line": e_ln,
            "start_line": s_ln,
            "end_line": e_ln,
            "side": side_norm,
            "unresolved": bool(unresolved),
            "message": cleaned_msg,
            "author_name": author,
            "notify": "OWNER",
        }

    def _resolve_sidecar_project_id(self, explicit_project_id: str | None) -> str | None:
        """Resolves `projectId` from `explicit_project_id` or parent sidecar conversation metadata."""
        if explicit_project_id and explicit_project_id.strip():
            return explicit_project_id.strip()
        if self._cached_project_id:
            return self._cached_project_id
        parent_conv = (
            os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
            or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            or ""
        ).strip()
        if not parent_conv:
            return None
        try:
            meta_res = _run_sidecar_agentapi(["get-conversation-metadata", parent_conv])
            pid = _extract_agentapi_project_id(meta_res)
            if pid:
                self._cached_project_id = pid
                return pid
        except Exception:
            pass
        return None

    def _find_qa_thread_entry(
        self, thread_id: str | None, conv_id: str | None
    ) -> dict[str, Any] | None:
        """Finds an active in-memory Q&A thread by `thread_id` or `conversation_id`."""
        with self._qa_lock:
            if thread_id and thread_id in self._qa_threads:
                entry = self._qa_threads[thread_id]
                if not entry.get("deleted"):
                    return entry
            if conv_id:
                for entry in self._qa_threads.values():
                    if not entry.get("deleted") and entry.get("conversation_id") == conv_id:
                        return entry
        return None

    def _is_conversation_turn_inflight(self, conv_id: str) -> bool:
        """Returns `True` if `conv_id` already has an unanswered code question in flight."""
        if not conv_id:
            return False
        with self._qa_lock:
            deleted = set(self._deleted_qa_conv_ids)
            ignored = {k: set(v) for k, v in self._ignored_steps_by_conv.items()}
        if conv_id in deleted:
            return False
        tx_items = discover_transcript_code_questions(
            conversation_id=conv_id,
            extra_conversation_ids={conv_id},
            excluded_conversation_ids=deleted,
            ignored_steps_by_conv=ignored,
        )
        matched_tx = next((it for it in tx_items if it.get("conversation_id") == conv_id), None)
        entry = self._find_qa_thread_entry(None, conv_id)
        if matched_tx is not None:
            tx_turns = list(matched_tx.get("turns") or [])
            mem_turns = list((entry.get("turns") or []) if entry else [])
            if len(mem_turns) > len(tx_turns):
                return not bool(str(mem_turns[-1].get("reply") or "").strip())
            if entry is not None and tx_turns:
                self._sync_cached_turns_from_transcript(entry["thread_id"], tx_turns)
            return matched_tx.get("status") == "thinking"
        if not entry:
            return False
        turns = entry.get("turns") or []
        return bool(turns and not str(turns[-1].get("reply") or "").strip())

    def _dispatch_code_qa_prompt(
        self,
        *,
        target_conv_id: str,
        title: str,
        prompt: str,
        project_id: str | None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Dispatches `prompt` to `target_conv_id` or starts an isolated background conversation."""
        try:
            if target_conv_id:
                res = _run_sidecar_agentapi(
                    ["send-message", target_conv_id, prompt],
                    project_id=project_id,
                )
                return True, target_conv_id, res
            resolved_pid = self._resolve_sidecar_project_id(project_id)
            res = _run_sidecar_agentapi(
                ["new-conversation", "--title", title, prompt],
                project_id=resolved_pid,
            )
            spawned_id = _extract_agentapi_conversation_id(res)
            return True, spawned_id, res
        except Exception:
            return False, target_conv_id, {}

    def _upsert_qa_thread_turn(
        self,
        *,
        thread_id: str | None,
        conv_id: str,
        cl_id: str,
        file_path: str,
        start_line: int,
        end_line: int,
        side: str,
        function_name: str,
        code_snippet: str,
        question: str,
        is_followup: bool,
    ) -> dict[str, Any]:
        """Records a newly submitted root or follow-up turn in `self._qa_threads`."""
        with self._qa_lock:
            resolved_tid = thread_id or (f"qa-{conv_id[:12]}" if conv_id else "")
            if not resolved_tid:
                resolved_tid = f"qa-local-{self._next_qa_seq}"
                self._next_qa_seq += 1
            existing = self._qa_threads.get(resolved_tid)
            if existing is None and conv_id:
                for cand in self._qa_threads.values():
                    if not cand.get("deleted") and cand.get("conversation_id") == conv_id:
                        existing = cand
                        resolved_tid = cand["thread_id"]
                        break
            if conv_id:
                self._tracked_qa_conv_ids.add(conv_id)
                self._deleted_qa_conv_ids.discard(conv_id)

            new_turn = {
                "turn_index": len(existing["turns"]) if (existing and is_followup) else 0,
                "question": question,
                "reply": "",
                "status": "thinking",
            }
            if existing and is_followup:
                existing["conversation_id"] = conv_id or existing.get("conversation_id", "")
                if function_name:
                    existing["function_name"] = function_name
                existing["turns"].append(new_turn)
                return dict(existing, turns=[dict(t) for t in existing["turns"]])

            entry = {
                "thread_id": resolved_tid,
                "conversation_id": conv_id,
                "cl": cl_id,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "side": side,
                "function_name": function_name,
                "code_snippet": code_snippet,
                "deleted": False,
                "turns": [new_turn],
            }
            self._qa_threads[resolved_tid] = entry
            return dict(entry, turns=[dict(new_turn)])

    def ask_agent_about_code(
        self,
        cl_number: str | int,
        file_path: str,
        start_line: int,
        end_line: int,
        code_snippet: str,
        question: str,
        *,
        side: str = "REVISION",
        conversation_id: str | None = None,
        project_id: str | None = None,
        spawn_new: bool = False,
        thread_id: str | None = None,
        function_name: str = "",
    ) -> dict[str, Any]:
        """Spawns an isolated per-highlight Q&A conversation or sends a follow-up when answered."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
        s_ln = max(1, int(start_line or 1))
        e_ln = max(s_ln, int(end_line or s_ln))
        cleaned_q = (
            (question or "").strip()
            or "Please explain this code block and check for any bugs or edge cases."
        )
        target_conv = "" if spawn_new else str(conversation_id or "").strip()
        if target_conv and self._is_conversation_turn_inflight(target_conv):
            raise ValueError(
                f"Cannot submit another question in conversation '{target_conv}' on ag/{cl_id} "
                "while the previous question is still being processed by the agent. "
                "Wait for the agent's reply, or modify/delete the in-flight question."
            )

        existing_entry = self._find_qa_thread_entry(thread_id, target_conv) if target_conv else None
        is_followup = bool(target_conv and existing_entry and existing_entry.get("turns"))
        prompt = (
            build_code_followup_prompt(
                cl_number=cl_id,
                file_path=cleaned_file,
                start_line=s_ln,
                end_line=e_ln,
                question=cleaned_q,
                side=side,
            )
            if is_followup
            else build_code_question_prompt(
                cl_number=cl_id,
                file_path=cleaned_file,
                start_line=s_ln,
                end_line=e_ln,
                code_snippet=code_snippet,
                question=cleaned_q,
                side=side,
            )
        )
        short_name = Path(cleaned_file).name or cleaned_file
        loc_tag = f"{function_name}:L{s_ln}" if function_name else f"{short_name}:L{s_ln}"
        title = f"Q&A ag/{cl_id} ({loc_tag})"

        dispatched, resolved_conv, agent_res = self._dispatch_code_qa_prompt(
            target_conv_id=target_conv,
            title=title,
            prompt=prompt,
            project_id=project_id,
        )
        thread_state = self._upsert_qa_thread_turn(
            thread_id=thread_id,
            conv_id=resolved_conv,
            cl_id=cl_id,
            file_path=cleaned_file,
            start_line=s_ln,
            end_line=e_ln,
            side=side,
            function_name=function_name,
            code_snippet=code_snippet,
            question=cleaned_q,
            is_followup=is_followup,
        )
        self._invalidate_cl_caches([cl_id])
        return {
            "status": "ok",
            "cl": cl_id,
            "thread_id": thread_state["thread_id"],
            "file_path": cleaned_file,
            "start_line": s_ln,
            "end_line": e_ln,
            "function_name": thread_state.get("function_name", ""),
            "dispatched": dispatched,
            "conversation_id": resolved_conv,
            "prompt": prompt,
            "turns": thread_state["turns"],
            "agent_response": agent_res,
        }

    def _cancel_or_rewind_inflight_turn(
        self,
        conv_id: str,
        turns: list[dict[str, Any]],
        delete_entire_conv: bool,
    ) -> None:
        """Cancels an in-flight LS invocation and deletes or rewinds its latest turn step."""
        if not conv_id:
            return
        _call_local_language_server_rpc(
            "CancelCascadeInvocation",
            {"cascadeId": conv_id, "killBackgroundTasks": True},
        )
        if delete_entire_conv:
            _call_local_language_server_rpc(
                "DeleteCascadeTrajectory",
                {"cascadeId": conv_id},
            )
            with self._qa_lock:
                self._deleted_qa_conv_ids.add(conv_id)
                self._tracked_qa_conv_ids.discard(conv_id)
            return

        prev_turn = turns[-2] if len(turns) >= 2 else None
        prev_end_step = prev_turn.get("end_step_index") if prev_turn else None
        latest_step = turns[-1].get("step_index") if turns else None
        if prev_end_step is not None:
            _call_local_language_server_rpc(
                "RevertToCascadeStep",
                {
                    "cascadeId": conv_id,
                    "stepIndex": int(prev_end_step),
                    "conversationOnly": True,
                },
            )
        if latest_step is not None:
            with self._qa_lock:
                self._ignored_steps_by_conv.setdefault(conv_id, set()).add(int(latest_step))

    def modify_or_delete_code_question(
        self,
        *,
        action: str,
        cl_number: str | int,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        question: str = "",
        file_path: str = "",
        start_line: int = 1,
        end_line: int = 1,
        code_snippet: str = "",
        side: str = "REVISION",
        function_name: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Modifies or deletes the latest in-flight question in an inline Q&A conversation."""
        norm_action = (action or "").strip().lower()
        if norm_action not in ("modify", "delete"):
            raise ValueError(
                f"Invalid code-question action '{action}': expected 'modify' or 'delete'. "
                "Pass action='modify' or action='delete'."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        conv_id = str(conversation_id or "").strip()
        entry = self._find_qa_thread_entry(thread_id, conv_id)
        if not conv_id and entry:
            conv_id = str(entry.get("conversation_id") or "")

        live_questions = self.get_code_questions([cl_id], conversation_id=conv_id or None)
        matched_live = next(
            (
                q
                for q in live_questions
                if (conv_id and q.get("conversation_id") == conv_id)
                or (thread_id and q.get("thread_id") == thread_id)
            ),
            None,
        )
        turns = list(
            (matched_live.get("turns") if matched_live else None)
            or (entry.get("turns") if entry else None)
            or []
        )
        is_root_turn = len(turns) <= 1

        if norm_action == "delete":
            self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
            remaining = turns[:-1] if len(turns) > 1 else []
            with self._qa_lock:
                if entry is not None:
                    if is_root_turn:
                        entry["deleted"] = True
                        entry["turns"] = []
                    else:
                        entry["turns"] = [dict(t) for t in remaining]
            self._invalidate_cl_caches([cl_id])
            return {
                "status": "ok",
                "action": "delete",
                "cl": cl_id,
                "thread_id": entry["thread_id"] if entry else (thread_id or ""),
                "conversation_id": "" if is_root_turn else conv_id,
                "thread_deleted": is_root_turn,
                "turns": remaining,
            }

        cleaned_q = (question or "").strip()
        if not cleaned_q:
            raise ValueError(
                f"Cannot modify code question on ag/{cl_id} with an empty question string. "
                "Enter a non-empty question or delete the in-flight question instead."
            )
        self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
        resolved_file = (
            (file_path or "").strip()
            or (entry.get("file_path") if entry else "")
            or "/COMMIT_MSG"
        )
        s_ln = max(1, int(start_line or (entry.get("start_line") if entry else 1) or 1))
        e_ln = max(s_ln, int(end_line or (entry.get("end_line") if entry else s_ln) or s_ln))
        resolved_snippet = code_snippet or (entry.get("code_snippet") if entry else "") or ""
        resolved_side = side or (entry.get("side") if entry else "REVISION") or "REVISION"
        resolved_fn = function_name or (entry.get("function_name") if entry else "") or ""

        if not is_root_turn and entry is not None:
            with self._qa_lock:
                entry["turns"] = [dict(t) for t in turns[:-1]]

        ask_res = self.ask_agent_about_code(
            cl_number=cl_id,
            file_path=resolved_file,
            start_line=s_ln,
            end_line=e_ln,
            code_snippet=resolved_snippet,
            question=cleaned_q,
            side=resolved_side,
            conversation_id=None if is_root_turn else conv_id,
            project_id=project_id,
            spawn_new=is_root_turn,
            thread_id=entry["thread_id"] if entry else thread_id,
            function_name=resolved_fn,
        )
        ask_res["action"] = "modify"
        return ask_res

    def _ai_analysis_disk_path(self, cl_id: str, patchset: int) -> Path:
        """Returns the disk cache path for Intangibles AI Analysis on `(cl_id, patchset)`."""
        return self._ai_analysis_cache_dir / f"cl_{cl_id}_ps_{patchset}.json"

    def _load_persisted_ai_analysis(self, cl_id: str, patchset: int) -> dict[str, Any] | None:
        """Loads persisted Intangibles AI Analysis from disk if present."""
        cache_file = self._ai_analysis_disk_path(cl_id, patchset)
        if not cache_file.is_file():
            return None
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        conv_id = str(raw.get("conversation_id") or "").strip()
        if conv_id:
            with self._qa_lock:
                self._analyzer_conv_ids.add(conv_id)
        return raw

    def _save_persisted_ai_analysis(
        self, cl_id: str, patchset: int, entry: dict[str, Any]
    ) -> None:
        """Persists Intangibles AI Analysis state/results to disk."""
        try:
            cache_file = self._ai_analysis_disk_path(cl_id, patchset)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _delete_persisted_ai_analysis(self, cl_id: str, patchset: int) -> None:
        """Deletes persisted Intangibles AI Analysis state from disk when cancelled."""
        try:
            cache_file = self._ai_analysis_disk_path(cl_id, patchset)
            if cache_file.is_file():
                cache_file.unlink()
        except OSError:
            pass

    def _resolve_analysis_patchset(self, cl_id: str, patchset: int | None) -> int:
        """Resolves `patchset` from explicit value, known queue revisions, or Gerrit detail."""
        if patchset and int(patchset) > 0:
            return int(patchset)
        _, known_ps = self._get_known_revision(cl_id)
        if known_ps > 0:
            return known_ps
        resolved_ps, _ = self._resolve_change_patchset_and_project(cl_id, None, "")
        return resolved_ps

    def get_ai_analysis(
        self,
        cl_number: str | int,
        *,
        patchset: int | None = None,
        brain_root: Path | None = None,
    ) -> dict[str, Any]:
        """Returns Intangibles AI Analysis for `(cl_number, patchset)` without auto-triggering."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False, strict=True)
        resolved_ps = self._resolve_analysis_patchset(cl_id, patchset)
        key = (cl_id, resolved_ps)

        with self._ai_analysis_lock:
            gen = self._ai_analysis_gen.get(key, 0)
            entry = self._ai_analysis_state.get(key)
        if entry is None:
            loaded = self._load_persisted_ai_analysis(cl_id, resolved_ps)
            if loaded is not None:
                with self._ai_analysis_lock:
                    if self._ai_analysis_gen.get(key, 0) == gen and key not in self._ai_analysis_state:
                        self._ai_analysis_state[key] = loaded
                    entry = self._ai_analysis_state.get(key)

        if entry is None:
            return {
                "status": "ok",
                "cl": cl_id,
                "patch_set": resolved_ps,
                "analysis_status": "not_analyzed",
                "conversation_id": "",
                "analysis": None,
                "error": "",
            }

        status_val = str(entry.get("analysis_status") or "not_analyzed")
        conv_id = str(entry.get("conversation_id") or "").strip()
        if status_val == "thinking" and conv_id:
            parsed = parse_ai_analysis_from_conversation(
                conv_id,
                brain_root=brain_root,
            )
            if parsed is not None:
                ready_entry = {
                    "analysis_status": "ready",
                    "conversation_id": conv_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "analysis": parsed,
                    "error": "",
                }
                committed = False
                with self._ai_analysis_lock:
                    cur = self._ai_analysis_state.get(key)
                    if (
                        self._ai_analysis_gen.get(key, 0) == gen
                        and cur is not None
                        and str(cur.get("conversation_id") or "").strip() == conv_id
                    ):
                        self._ai_analysis_state[key] = ready_entry
                        entry = ready_entry
                        committed = True
                if committed:
                    self._save_persisted_ai_analysis(cl_id, resolved_ps, ready_entry)

        return {
            "status": "ok",
            "cl": cl_id,
            "patch_set": resolved_ps,
            "analysis_status": str(entry.get("analysis_status") or "not_analyzed"),
            "conversation_id": conv_id,
            "updated_at": str(entry.get("updated_at") or ""),
            "analysis": entry.get("analysis"),
            "error": str(entry.get("error") or ""),
        }

    def _cancel_ai_analysis(self, cl_id: str, patchset: int | None) -> dict[str, Any]:
        """Cancels an in-flight or persisted Intangibles AI Analysis and cleans up its trajectory."""
        resolved_ps = self._resolve_analysis_patchset(cl_id, patchset)
        key = (cl_id, resolved_ps)
        with self._ai_analysis_lock:
            self._ai_analysis_gen[key] = self._ai_analysis_gen.get(key, 0) + 1
            prev = self._ai_analysis_state.pop(key, None)
        if prev is None:
            prev = self._load_persisted_ai_analysis(cl_id, resolved_ps)
        prev_conv = str((prev or {}).get("conversation_id") or "").strip()
        self._cancel_or_rewind_inflight_turn(prev_conv, [], delete_entire_conv=True)
        self._delete_persisted_ai_analysis(cl_id, resolved_ps)
        return {
            "status": "ok",
            "cl": cl_id,
            "patch_set": resolved_ps,
            "analysis_status": "not_analyzed",
            "conversation_id": "",
            "analysis": None,
            "error": "",
        }

    def _build_ai_analysis_prompt_for_cl(
        self, cl_id: str, resolved_ps: int, resolved_proj: str, subject: str
    ) -> str:
        """Fetches diff bundle metadata and constructs the Intangibles prompt for `cl_id`."""
        bundle = self.fetch_commit_diff_bundle(cl_id)
        commit_msg = str(bundle.get("commit_message") or "")
        first_lines = commit_msg.splitlines()
        resolved_subj = (subject or "").strip() or (
            first_lines[0].strip() if first_lines else f"ag/{cl_id}"
        )
        return build_ai_analysis_prompt(
            cl_id=cl_id,
            patch_set=resolved_ps,
            subject=resolved_subj,
            project=resolved_proj,
            commit_message=commit_msg,
            diff_files=bundle.get("files") or [],
        )

    def _commit_spawned_ai_analysis(
        self,
        cl_id: str,
        resolved_ps: int,
        gen: int,
        dispatched: bool,
        spawned_conv: str,
        brain_root: Path | None,
    ) -> dict[str, Any]:
        """Commits spawned `agentapi` conversation state if `gen` is still current, else cancels it."""
        key = (cl_id, resolved_ps)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not dispatched or not spawned_conv:
            err_entry = {
                "analysis_status": "error",
                "conversation_id": "",
                "updated_at": now_iso,
                "analysis": None,
                "error": (
                    f"Failed to start Problem & Alternatives analysis for ag/{cl_id}: sidecar agentapi bridge "
                    "is unavailable. Open Fast Gerrit UI inside Jetski Sidecar and retry."
                ),
            }
            with self._ai_analysis_lock:
                if self._ai_analysis_gen.get(key) == gen:
                    self._ai_analysis_state[key] = err_entry
            return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **err_entry}

        with self._qa_lock:
            self._analyzer_conv_ids.add(spawned_conv)
        thinking_entry = {
            "analysis_status": "thinking",
            "conversation_id": spawned_conv,
            "updated_at": now_iso,
            "analysis": None,
            "error": "",
        }
        with self._ai_analysis_lock:
            is_current = self._ai_analysis_gen.get(key) == gen
            if is_current:
                self._ai_analysis_state[key] = thinking_entry
        if not is_current:
            self._cancel_or_rewind_inflight_turn(spawned_conv, [], delete_entire_conv=True)
            return self.get_ai_analysis(cl_id, patchset=resolved_ps, brain_root=brain_root)
        self._save_persisted_ai_analysis(cl_id, resolved_ps, thinking_entry)
        return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **thinking_entry}

    def _start_ai_analysis(
        self,
        cl_id: str,
        *,
        patchset: int | None,
        project: str,
        subject: str,
        project_id: str | None,
        force: bool,
        brain_root: Path | None,
    ) -> dict[str, Any]:
        """Reserves an epoch and launches an isolated `agentapi` conversation for `cl_id`."""
        resolved_ps, resolved_proj = self._resolve_change_patchset_and_project(
            cl_id, patchset, project
        )
        key = (cl_id, resolved_ps)
        if not force:
            current = self.get_ai_analysis(cl_id, patchset=resolved_ps, brain_root=brain_root)
            if current["analysis_status"] in ("ready", "thinking"):
                return current

        with self._ai_analysis_lock:
            existing = self._ai_analysis_state.get(key)
            if not force and existing and existing.get("analysis_status") in ("ready", "thinking"):
                return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **existing}
            superseded_conv = (
                str(existing.get("conversation_id") or "").strip()
                if existing and existing.get("analysis_status") == "thinking"
                else ""
            )
            gen = self._ai_analysis_gen.get(key, 0) + 1
            self._ai_analysis_gen[key] = gen
            self._ai_analysis_state[key] = {
                "analysis_status": "thinking",
                "conversation_id": "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "analysis": None,
                "error": "",
            }

        self._cancel_or_rewind_inflight_turn(superseded_conv, [], delete_entire_conv=True)
        prompt = self._build_ai_analysis_prompt_for_cl(cl_id, resolved_ps, resolved_proj, subject)
        dispatched, spawned_conv, _ = self._dispatch_code_qa_prompt(
            target_conv_id="",
            title=f"Problem & Alternatives · ag/{cl_id} (PS{resolved_ps})",
            prompt=prompt,
            project_id=project_id,
        )
        return self._commit_spawned_ai_analysis(
            cl_id, resolved_ps, gen, dispatched, spawned_conv, brain_root
        )

    def trigger_or_cancel_ai_analysis(
        self,
        cl_number: str | int,
        *,
        action: str = "analyze",
        patchset: int | None = None,
        project: str = "",
        subject: str = "",
        project_id: str | None = None,
        force: bool = False,
        brain_root: Path | None = None,
    ) -> dict[str, Any]:
        """Explicitly starts or cancels a single-CL Intangibles AI Analysis."""
        norm_action = (action or "analyze").strip().lower()
        if norm_action not in ("analyze", "cancel"):
            raise ValueError(
                f"Invalid Problem & Alternatives action '{action}': expected 'analyze' or 'cancel'. "
                "Use 'Analyze Problem & Alternatives' under Harness Actions in the toolbar."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False, strict=True)
        if norm_action == "cancel":
            return self._cancel_ai_analysis(cl_id, patchset)
        return self._start_ai_analysis(
            cl_id,
            patchset=patchset,
            project=project,
            subject=subject,
            project_id=project_id,
            force=force,
            brain_root=brain_root,
        )


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
    html = html.replace('"__GERRIT_UI_VERSION__"', json.dumps(get_gerrit_ui_template_version()))
    bootstrap_json = json.dumps(initial_payload or {}, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("__GERRIT_UI_BOOTSTRAP_JSON__", bootstrap_json)


def _extract_agentapi_conversation_id(payload: dict[str, Any]) -> str:
    """Extracts `conversationId` from `agentapi new-conversation` JSON output."""
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("conversationId") or payload.get("conversation_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    resp = payload.get("response")
    if not isinstance(resp, dict):
        return ""
    new_conv = resp.get("newConversation") or resp.get("new_conversation") or {}
    if isinstance(new_conv, dict):
        cid = new_conv.get("conversationId") or new_conv.get("conversation_id")
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
    return ""


def _extract_agentapi_project_id(payload: dict[str, Any]) -> str:
    """Extracts `projectId` from `agentapi get-conversation-metadata` JSON output."""
    if not isinstance(payload, dict):
        return ""
    resp = payload.get("response")
    if not isinstance(resp, dict):
        return ""
    meta_evt = resp.get("conversationMetadata") or resp.get("conversation_metadata") or {}
    meta = meta_evt.get("metadata") if isinstance(meta_evt, dict) else {}
    if isinstance(meta, dict):
        pid = meta.get("projectId") or meta.get("project_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    return ""


def _call_local_language_server_rpc(method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invokes a Connect JSON RPC on the local Jetski Language Server when `ANTIGRAVITY_LS_ADDRESS` is set."""
    addr = (os.environ.get("ANTIGRAVITY_LS_ADDRESS") or "").strip()
    if not addr:
        return {"skipped": True}
    csrf = (os.environ.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    base_url = addr if addr.startswith(("http://", "https://")) else f"http://{addr}"
    url = f"{base_url.rstrip('/')}/exa.language_server_pb.LanguageServerService/{method_name}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LS_CONNECT_RPC_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            return json.loads(raw) if raw else {}
    except Exception as exc:
        return {"error": str(exc)}


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


def _is_truthy_query_flag(qs: dict[str, list[str]], key: str) -> bool:
    """Returns `True` if query string parameter `key` is `"1"`, `"true"`, or `"yes"`."""
    return (qs.get(key) or [""])[0].strip().lower() in ("1", "true", "yes")


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
                    if _is_truthy_query_flag(qs, "prewarm_only"):
                        scheduled = service.schedule_background_prewarm(viewer or None)
                        self._send_json({"status": "ok", "prewarm_scheduled": scheduled})
                        return
                    query = (qs.get("query") or [""])[0]
                    scope = (qs.get("scope") or ["reviewer"])[0]
                    author = (qs.get("author") or [""])[0]
                    fast_flag = _is_truthy_query_flag(qs, "fast")
                    refresh_flag = _is_truthy_query_flag(qs, "refresh")
                    data = service.fetch_queue(
                        viewer_email=viewer or None,
                        query=query or None,
                        scope=scope,
                        author=author or None,
                        fast=fast_flag,
                        use_cache=not refresh_flag,
                    )
                    if _is_truthy_query_flag(qs, "prewarm") or not fast_flag:
                        service.schedule_background_prewarm(data.get("viewer") or viewer or None)
                    self._send_json(data)
                    return
                if parsed.path == "/api/gerrit/diffs":
                    raw_cls = (qs.get("cls") or [""])[0]
                    cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
                    diffs = service.fetch_multi_commit_diffs(cl_list)
                    self._send_json(
                        {"diffs": diffs, "ui_version": get_gerrit_ui_template_version()}
                    )
                    return
                if parsed.path == "/api/gerrit/swarm-status":
                    raw_cls = (qs.get("cls") or [""])[0]
                    cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
                    self._send_json(
                        {
                            "statuses": service.get_swarm_statuses(cl_list),
                            "ui_version": get_gerrit_ui_template_version(),
                        }
                    )
                    return
                if parsed.path == "/api/gerrit/code-questions":
                    raw_cls = (qs.get("cls") or [""])[0]
                    conv_id = (qs.get("conversationId") or [""])[0].strip() or None
                    cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
                    self._send_json(
                        {"questions": service.get_code_questions(cl_list, conversation_id=conv_id)}
                    )
                    return
                if parsed.path == "/api/gerrit/suggest-reviewers":
                    cl_num = (qs.get("cl") or [""])[0]
                    query_str = (qs.get("q") or [""])[0]
                    self._send_json(
                        {"suggestions": service.suggest_reviewers(cl_num, query=query_str)}
                    )
                    return
                if parsed.path == "/api/gerrit/presubmit-targets":
                    cl_num = (qs.get("cl") or [""])[0].strip()
                    if not cl_num:
                        self._send_json(
                            {"error": "Missing required `cl` query parameter for `/api/gerrit/presubmit-targets`."},
                            status=400,
                        )
                        return
                    raw_ps = (qs.get("ps") or [""])[0].strip()
                    ps_val = int(raw_ps) if raw_ps.isdigit() else None
                    proj = (qs.get("project") or [""])[0].strip()
                    refresh_flag = (qs.get("refresh") or [""])[0].strip().lower() in ("1", "true", "yes")
                    self._send_json(
                        service.get_presubmit_targets(
                            cl_num,
                            patchset=ps_val,
                            project=proj,
                            force_refresh=refresh_flag,
                        )
                    )
                    return
                if parsed.path == "/api/gerrit/ai-analysis":
                    cl_num = (qs.get("cl") or [""])[0].strip()
                    if not cl_num:
                        self._send_json(
                            {
                                "error": "Missing required `cl` query parameter for `/api/gerrit/ai-analysis` "
                                "(pass e.g. `?cl=43256612&ps=1`)."
                            },
                            status=400,
                        )
                        return
                    raw_ps = (qs.get("ps") or [""])[0].strip()
                    ps_val = int(raw_ps) if raw_ps.isdigit() else None
                    self._send_json(service.get_ai_analysis(cl_num, patchset=ps_val))
                    return
                self._send_json({"error": f"Unknown endpoint '{parsed.path}'"}, status=404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
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
                    if "code_review" in body:
                        cr_arg = int(body["code_review"]) if body["code_review"] is not None else None
                    elif (
                        "autosubmit" in body
                        or "presubmit_ready" in body
                        or str(body.get("message") or "").strip()
                    ):
                        cr_arg = None
                    else:
                        cr_arg = 2
                    raw_as = body.get("autosubmit", False)
                    as_arg: bool | int | None = (
                        raw_as if isinstance(raw_as, bool) or raw_as is None else int(raw_as)
                    )
                    raw_pr = body.get("presubmit_ready", False)
                    pr_arg: bool | int | None = (
                        raw_pr if isinstance(raw_pr, bool) or raw_pr is None else int(raw_pr)
                    )
                    res = service.cast_batch_vote(
                        cl_numbers=body.get("cls") or [],
                        code_review=cr_arg,
                        autosubmit=as_arg,
                        presubmit_ready=pr_arg,
                        message=str(body.get("message", "")),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/submit":
                    raw_cls = body.get("cls")
                    if not isinstance(raw_cls, list):
                        single_cl = body.get("cl")
                        raw_cls = [single_cl] if single_cl is not None and str(single_cl).strip() else []
                    res = service.submit_changes(cl_numbers=raw_cls)
                    self._send_json(res, status=200 if res.get("status") == "ok" else 409)
                    return
                if parsed.path == "/api/gerrit/assign-reviewer":
                    res = service.assign_second_human_reviewer(
                        cl_number=body.get("cl", ""),
                        reviewer_email=body.get("reviewer", ""),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/reviewer":
                    batch_cls = body.get("cls")
                    if isinstance(batch_cls, list) and len(batch_cls) > 0:
                        res = service.modify_reviewers_batch(
                            cl_numbers=batch_cls,
                            action=str(body.get("action") or "add"),
                            account=body.get("account") or body.get("reviewer") or "",
                            state=str(body.get("state") or "REVIEWER"),
                            viewer_email=str(body.get("viewer") or "").strip() or None,
                        )
                    else:
                        res = service.modify_reviewer(
                            cl_number=body.get("cl", ""),
                            action=str(body.get("action") or "add"),
                            account=body.get("account") or body.get("reviewer") or "",
                            state=str(body.get("state") or "REVIEWER"),
                            viewer_email=str(body.get("viewer") or "").strip() or None,
                        )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/chat-dm":
                    res = service.send_reviewer_chat_dm(
                        cl_number=body.get("cl", ""),
                        recipient=str(body.get("recipient") or body.get("reviewer") or ""),
                        message=str(body.get("message") or ""),
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
                if parsed.path == "/api/gerrit/comment":
                    res = service.post_inline_comment(
                        cl_number=body.get("cl", ""),
                        file_path=str(body.get("file_path") or body.get("file") or ""),
                        line=int(body.get("line") or body.get("end_line") or 1),
                        message=str(body.get("message") or ""),
                        start_line=(
                            int(body["start_line"])
                            if body.get("start_line") is not None
                            else None
                        ),
                        end_line=(
                            int(body["end_line"])
                            if body.get("end_line") is not None
                            else None
                        ),
                        side=str(body.get("side") or "REVISION"),
                        unresolved=bool(body.get("unresolved", True)),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/ask-code":
                    res = service.ask_agent_about_code(
                        cl_number=body.get("cl", ""),
                        file_path=str(body.get("file_path") or body.get("file") or ""),
                        start_line=int(body.get("start_line") or body.get("line") or 1),
                        end_line=int(body.get("end_line") or body.get("line") or 1),
                        code_snippet=str(body.get("code_snippet") or body.get("code") or ""),
                        question=str(body.get("question") or ""),
                        side=str(body.get("side") or "REVISION"),
                        conversation_id=str(body.get("conversationId") or "").strip() or None,
                        project_id=str(body.get("projectId") or "").strip() or None,
                        spawn_new=bool(body.get("spawnNew", False)),
                        thread_id=str(body.get("threadId") or "").strip() or None,
                        function_name=str(body.get("function_name") or body.get("functionName") or "").strip(),
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/code-question-action":
                    res = service.modify_or_delete_code_question(
                        action=str(body.get("action") or ""),
                        cl_number=body.get("cl", ""),
                        conversation_id=str(body.get("conversationId") or "").strip() or None,
                        thread_id=str(body.get("threadId") or "").strip() or None,
                        question=str(body.get("question") or ""),
                        file_path=str(body.get("file_path") or body.get("file") or ""),
                        start_line=int(body.get("start_line") or body.get("line") or 1),
                        end_line=int(body.get("end_line") or body.get("line") or 1),
                        code_snippet=str(body.get("code_snippet") or body.get("code") or ""),
                        side=str(body.get("side") or "REVISION"),
                        function_name=str(body.get("function_name") or body.get("functionName") or "").strip(),
                        project_id=str(body.get("projectId") or "").strip() or None,
                    )
                    self._send_json(res)
                    return
                if parsed.path == "/api/gerrit/ai-analysis":
                    raw_ps = body.get("ps")
                    ps_val = int(raw_ps) if raw_ps is not None and str(raw_ps).isdigit() else None
                    res = service.trigger_or_cancel_ai_analysis(
                        cl_number=body.get("cl", ""),
                        action=str(body.get("action") or "analyze"),
                        patchset=ps_val,
                        project=str(body.get("project") or ""),
                        subject=str(body.get("subject") or ""),
                        project_id=str(body.get("projectId") or "").strip() or None,
                        force=bool(body.get("force", False)),
                    )
                    self._send_json(res)
                    return
                self._send_json({"error": f"Unknown POST endpoint '{parsed.path}'"}, status=404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
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
