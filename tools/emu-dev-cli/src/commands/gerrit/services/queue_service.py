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

"""Gerrit CL queue querying, review-enforcement tracking, and attention-set filtering."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import threading
import time
from typing import Any
import urllib.parse

from clients.buganizer_service import parse_issues_search_map
from commands.gerrit.presubmit_targets import (
    ACTIVE_TARGETS_CACHE_TTL_SEC,
    COMPLETED_TARGETS_CACHE_TTL_SEC,
    extract_presubmit_status_from_change,
    format_relative_age,
    query_presubmit_targets_via_stubby,
)
from commands.gerrit.services.diff_parser import (
    COMMIT_TYPE_SPECS,
    DIFF_FETCH_WORKERS,
    GERRIT_MAGIC_FILES,
    GerritDiffService,
    GerritServiceBase,
    RAW_QUEUE_CACHE_TTL_SEC,
    build_code_search_file_url,
    classify_commit_type,
    classify_risk_and_load,
    format_repo_display_path,
    get_gerrit_ui_template_version,
    parse_unified_diff_files,
    register_ui_hook,
    resolve_code_search_base_url,
    resolve_ui_hook,
)
from commands.harness.review_swarm_status import read_cl_swarm_status
from core.process import SubprocessRunner
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GERRIT_CLI_TIMEOUT_SEC,
    decode_gerrit_patch,
    parse_cl_identifier,
    strip_xssi_prefix,
)

register_ui_hook("query_presubmit_targets_via_stubby", query_presubmit_targets_via_stubby)
register_ui_hook("read_cl_swarm_status", read_cl_swarm_status)

FRESH_THRESHOLD_HOURS = 24.0
CLIFF_THRESHOLD_HOURS = 96.0
SILENT_ATTENTION_HOURS = 24.0
SESSION_PRESUBMIT_LAG_SEC = 25.0
FAST_QUEUE_LIMIT = 10
FULL_QUEUE_LIMIT = 100
RAW_QUEUE_STALE_MAX_SEC = 300.0
PREWARM_COOLDOWN_SEC = 45.0

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


DEFAULT_ISSUES_CLI_BIN = "/google/bin/releases/issues-cli/issues"
BUG_LOOKUP_TIMEOUT_SEC = 8


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
NON_HUMAN_OS_USERS: frozenset[str] = frozenset({"root", "nobody", "daemon", "bin", "sys"})

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
    return {
        bid: {
            "id": info["id"],
            "url": info["url"],
            "priority": info["priority"],
            "severity": info["severity"],
            "status": info["status"],
            "title": info["title"],
        }
        for bid, info in parse_issues_search_map(raw_text).items()
    }



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



def _normalize_viewer_email(raw_value: str | None) -> str:
    """Normalizes a viewer username or email (e.g. `jansene` -> `jansene@google.com`)."""
    cleaned = (raw_value or "").strip()
    if "\\" in cleaned:
        cleaned = cleaned.rsplit("\\", 1)[-1].strip()
    cleaned = cleaned.lower()
    if not cleaned:
        return ""
    if "@" not in cleaned:
        if not _SIMPLE_LDAP_RE.match(cleaned):
            return ""
        return f"{cleaned}@google.com"
    return cleaned


def _detect_git_user_email() -> str:
    """Returns `git config user.email` when it belongs to a human account."""
    try:
        res = SubprocessRunner().run(
            ["git", "config", "user.email"],
            check=False,
            timeout_sec=5,
        )
    except Exception:
        return ""
    email = (res.stdout or "").strip().lower()
    if email and not is_agent_or_bot_email(email):
        return email
    return ""


def _detect_os_user_email() -> str:
    """Resolves the logged-in OS user (`$USER`, `$USERNAME`, or `getpass.getuser()`) to `@google.com`."""
    raw_user = (os.environ.get("USER") or "").strip() or (os.environ.get("USERNAME") or "").strip()
    if not raw_user:
        try:
            raw_user = (getpass.getuser() or "").strip()
        except Exception:
            raw_user = ""
    if not raw_user:
        return ""
    if "\\" in raw_user:
        raw_user = raw_user.rsplit("\\", 1)[-1].strip()
    short_user = raw_user.split("@", 1)[0].strip().lower()
    if not short_user or short_user in NON_HUMAN_OS_USERS:
        return ""
    email = _normalize_viewer_email(raw_user)
    if not email or is_agent_or_bot_email(email):
        return ""
    return email


def _detect_capsule_owner_email() -> str:
    """Resolves the human capsule owner's `@google.com` email when running on a `cpl-` host."""
    try:
        hostname_short = socket.gethostname().split(".")[0].strip()
        if not hostname_short.startswith("cpl-"):
            return ""
        res = SubprocessRunner().run(
            ["cpl", "list"],
            check=False,
            timeout_sec=5,
        )
    except Exception:
        return ""
    for line in (res.stdout or "").splitlines():
        if hostname_short not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        owner_cand = _normalize_viewer_email(parts[3])
        if owner_cand and not is_agent_or_bot_email(owner_cand):
            return owner_cand
    return ""




class GerritQueueService(GerritServiceBase):
    """Domain service for Gerrit CL queue fetching, attention-set filtering, and session state."""

    def fetch_commit_diff_bundle(self, cl_number: str | int) -> dict[str, Any]:
        """Delegates diff bundle lookup to `GerritDiffService`."""
        return GerritDiffService.fetch_commit_diff_bundle(self, cl_number)  # type: ignore[arg-type]

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
                res = SubprocessRunner().run(
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
                    check=False,
                    timeout_sec=BUG_LOOKUP_TIMEOUT_SEC,
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

    def _detect_viewer_email(self) -> str:
        """Detects the logged-in engineer's email from env, Gerrit, git config, OS `$USER`, or capsule owner."""
        env_viewer = _normalize_viewer_email(os.environ.get("AEMU_GERRIT_VIEWER"))
        if env_viewer and not is_agent_or_bot_email(env_viewer):
            return env_viewer
        if self.auth_email and not is_agent_or_bot_email(self.auth_email):
            return self.auth_email
        git_email = _detect_git_user_email()
        if git_email:
            return git_email
        os_user_email = _detect_os_user_email()
        if os_user_email:
            return os_user_email
        capsule_owner_email = _detect_capsule_owner_email()
        if capsule_owner_email:
            return capsule_owner_email
        return ""

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
        viewer = _normalize_viewer_email(viewer_email) or self.default_viewer
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
            GerritQueueService._recompute_item_submittable(item)
            return
        if cr_val < 2 and item.get("approved_by_viewer"):
            item["approved_by_viewer"] = False
            cr2_list = [
                v for v in (item.get("human_cr2") or []) if (v.get("email") or "").lower() != viewer
            ]
            item["human_cr2"] = cr2_list
            item["human_cr2_count"] = len(cr2_list)
        GerritQueueService._recompute_item_submittable(item)

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

        payload = resolve_ui_hook("query_presubmit_targets_via_stubby", query_presubmit_targets_via_stubby)(
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
            status_obj = resolve_ui_hook("read_cl_swarm_status", read_cl_swarm_status)(cleaned, now_dt=now_dt)
            if status_obj is not None:
                statuses[cleaned] = status_obj
        return statuses


    def run_harness_action(self, cl_number: str | int, action: str) -> dict[str, Any]:
        """Executes a 1-click Harness capability (`review-swarm` or `address-review`) on a CL."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cli_main = Path(__file__).resolve().parents[3] / "__main__.py"
        if action == "review-swarm":
            cmd = [sys.executable, str(cli_main), "--json", "harness", "review-swarm", "--cl", cl_id]
        elif action == "address-review":
            cmd = [sys.executable, str(cli_main), "--json", "gerrit", "resume", cl_id]
        else:
            raise ValueError(
                f"Unsupported harness action '{action}' (expected 'review-swarm' or 'address-review')."
            )
        res = SubprocessRunner().run(
            cmd,
            check=False,
            timeout_sec=60,
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
            "swarm_status": resolve_ui_hook("read_cl_swarm_status", read_cl_swarm_status)(cl_id),
            "stderr": (res.stderr or "").strip(),
        }


    def get_sidecar_update_status(self, *, force_fetch: bool = False) -> dict[str, Any]:
        """Returns whether newer Gerrit changes exist in `hardware/google/aemu` vs installed sidecar."""
        return self.sidecar_updater.get_update_status(
            force_fetch=force_fetch,
            ui_version=get_gerrit_ui_template_version(),
        )

    def perform_sidecar_update(self, *, force_discard_local: bool = False) -> dict[str, Any]:
        """Pulls latest `hardware/google/aemu` Gerrit changes and reinstalls CLI & sidecar."""
        return self.sidecar_updater.perform_update(
            force_discard_local=force_discard_local,
            ui_version_fn=get_gerrit_ui_template_version,
        )
