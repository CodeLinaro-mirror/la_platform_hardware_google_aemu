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

"""Reusable fake Gerrit change builders and clients for Gerrit command, service, and UI tests."""

from __future__ import annotations

import re
from typing import Any


def make_fake_change(
    number: int = 43351736,
    *,
    subject: str = "refactor(gerrit): Decompose UI monolith 测试_🚀_é",
    owner_email: str = "groupagent-android-emulator-heavy@google.com",
    owner_name: str = "Archie",
    project: str = "platform/hardware/google/aemu",
    branch: str = "studio-main",
    change_id: str = "I1234567890abcdef1234567890abcdef12345678",
    created: str = "2026-10-28 12:00:00.000000000",
    updated: str = "2026-10-28 21:00:00.000000000",
    insertions: int = 50,
    deletions: int = 20,
    unresolved_comment_count: int = 0,
    current_revision: str = "rev_a",
    current_revision_number: int = 1,
    files: dict[str, Any] | None = None,
    parent_commit: str = "rev_base",
    reviewers: list[dict[str, Any]] | None = None,
    code_review_votes: list[dict[str, Any]] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Constructs a realistic Gerrit change dictionary for unit testing."""
    resolved_files = (
        files
        if files is not None
        else {"tools/emu-dev-cli/src/commands/gerrit/ui_server.py": {}}
    )
    resolved_reviewers = (
        reviewers
        if reviewers is not None
        else [
            {"email": "jansene@google.com", "name": "Erwin Jansen"},
            {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
        ]
    )
    resolved_votes = (
        code_review_votes
        if code_review_votes is not None
        else [
            {
                "email": "jansene@google.com",
                "name": "Erwin Jansen",
                "value": 2,
            },
            {
                "email": "groupagent-android-emulator-heavy@google.com",
                "name": "Archie",
                "value": 1,
            },
        ]
    )
    change: dict[str, Any] = {
        "_number": number,
        "change_id": change_id,
        "project": project,
        "branch": branch,
        "subject": subject,
        "created": created,
        "updated": updated,
        "insertions": insertions,
        "deletions": deletions,
        "unresolved_comment_count": unresolved_comment_count,
        "current_revision": current_revision,
        "current_revision_number": current_revision_number,
        "owner": {
            "email": owner_email,
            "name": owner_name,
        },
        "revisions": {
            current_revision: {
                "_number": current_revision_number,
                "files": resolved_files,
                "commit": {"parents": [{"commit": parent_commit}]},
            }
        },
        "reviewers": {
            "REVIEWER": resolved_reviewers,
        },
        "labels": {
            "Code-Review": {
                "all": resolved_votes,
            }
        },
    }
    if extra_fields:
        change.update(extra_fields)
    return change


AGENT = "groupagent-android-emulator-heavy@google.com"
ERWIN = "jansene@google.com"
JOSHUA = "joshuaduong@google.com"
DENALEX = "corakwue@google.com"
PROJECT = "platform/hardware/google/aemu"
SUBJECT_UNICODE = "feat(gerrit): Add stack status 测试_🚀_é"

_REQUIREMENT_NAMES = {
    "review_enforcement": "Review-Enforcement",
    "code_review": "Code-Review",
    "presubmit_verified": "Presubmit-Verified",
    "lint": "Lint",
    "code_owners": "Code-Owners",
    "security_review": "Security-Review",
}


def change_id(number: int) -> str:
    """Returns a deterministic `I<40 hex>` Change-Id for CL `number`."""
    return f"I{number:040x}"


def submit_requirements(**statuses: str) -> list[dict[str, Any]]:
    """Builds SUBMIT_REQUIREMENTS entries, e.g. `submit_requirements(code_review="SATISFIED")`."""
    return [{"name": _REQUIREMENT_NAMES[key], "status": value} for key, value in statuses.items()]


def stack_change(
    number: int,
    *,
    uploader: str = AGENT,
    parent_commit: str = "base_sha",
    patch_set: int = 1,
    status: str = "NEW",
    votes: list[tuple[str, int]] | None = None,
    reviewers: tuple[str, ...] = (ERWIN, JOSHUA, DENALEX),
    labels: dict[str, int] | None = None,
    requirements: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    subject: str = SUBJECT_UNICODE,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds a ChangeInfo with DETAILED_LABELS, SUBMIT_REQUIREMENTS, and MESSAGES fields."""
    rev = f"sha_{number}_ps{patch_set}"
    change = make_fake_change(
        number,
        subject=subject,
        owner_email=AGENT,
        change_id=change_id(number),
        current_revision=rev,
        current_revision_number=patch_set,
        parent_commit=parent_commit,
        reviewers=[{"email": e} for e in reviewers],
        code_review_votes=[{"email": e, "value": v} for e, v in (votes or [])],
        extra_fields={"status": status, **(extra or {})},
    )
    change["revisions"][rev]["uploader"] = {"email": uploader}
    for label, value in (labels or {}).items():
        change["labels"][label] = {"all": [{"email": "bot@google.com", "value": value}]}
    change["submit_requirements"] = requirements or []
    change["messages"] = messages or []
    return change


def related_entry(
    number: int,
    *,
    patch_set: int = 1,
    current: int | None = None,
    parent_commit: str = "base_sha",
    status: str = "NEW",
) -> dict[str, Any]:
    """Builds one RelatedChangeAndCommitInfo entry of a relation chain."""
    return {
        "project": PROJECT,
        "change_id": change_id(number),
        "commit": {
            "commit": f"sha_{number}_ps{patch_set}",
            "parents": [{"commit": parent_commit}],
            "subject": "s",
        },
        "_change_number": number,
        "_revision_number": patch_set,
        "_current_revision_number": current if current is not None else patch_set,
        "status": status,
    }


class FakeStackGerritClient:
    """In-memory stand-in for GerritClient's search, related, and mergeable endpoints."""

    def __init__(
        self,
        changes: list[dict[str, Any]],
        related: dict[int, list[dict[str, Any]]],
        mergeable: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.changes = {c["_number"]: c for c in changes}
        self.related = related
        self.mergeable = mergeable or {}
        self.queries: list[str] = []
        self.related_calls: list[int] = []
        self.mergeable_calls: list[int] = []
        self.closed = False
        self.fail_with: Exception | None = None

    def query_changes(
        self, query: str, *, options: tuple[str, ...] = (), limit: int = 25
    ) -> list[dict[str, Any]]:
        if self.fail_with is not None:
            raise self.fail_with
        self.queries.append(query)
        assert "SUBMIT_REQUIREMENTS" in options and "DETAILED_LABELS" in options
        topic = re.match(r'topic:"(.+)"', query)
        if topic:
            return [c for c in self.changes.values() if c.get("topic") == topic.group(1)][:limit]
        wanted = re.findall(r"change:(\w+)", query)
        return [
            c for c in self.changes.values()
            if str(c["_number"]) in wanted or c["change_id"] in wanted
        ][:limit]

    def get_related_changes(self, change_id: int, revision: str = "current") -> list[dict[str, Any]]:
        self.related_calls.append(change_id)
        return self.related.get(change_id, [])

    def get_mergeable(self, change_id: int, revision: str = "current") -> dict[str, Any]:
        self.mergeable_calls.append(change_id)
        return self.mergeable.get(change_id, {"mergeable": True})

    def close(self) -> None:
        self.closed = True
