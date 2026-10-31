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

"""Reusable fake Gerrit change builders for Gerrit service and UI tests."""

from __future__ import annotations

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
