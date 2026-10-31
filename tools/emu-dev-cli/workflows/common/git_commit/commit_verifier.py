#!/usr/bin/env python3
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

"""Shared Commit Verification Library for emu-dev-cli Workflows.

Provides commit message parsing, Gerrit format validation, forbidden tag detection,
and Git repository inspection helpers for `create-commit` and `amend-commit`.
"""

import logging
import os
from pathlib import Path
import re
from typing import List, Optional

from core.git import GitService, ProdGitService
from workflows.common.git_commit.gerrit_formatter import (
    detect_formatting_errors_in_string,
    parse_commit_message_string,
)


# Allowed Semantic Commit <type> values
ALLOWED_COMMIT_TYPES = (
    "feat",
    "fix",
    "build",
    "docs",
    "chore",
    "test",
    "refactor",
    "perf",
    "style",
)

# Regular expression to validate Semantic Commit subject: <type>[optional scope]: <description>
SEMANTIC_SUBJECT_PATTERN = re.compile(
    r"^(feat|fix|build|docs|chore|test|refactor|perf|style)(?:\(([^)]*)\))?:\s+(.+)$"
)

# Regular expression to extract Gerrit Change-Id
_DEFAULT_GIT_TIMEOUT_SECS = 30

CHANGE_ID_PATTERN = re.compile(r"(?im)^\s*Change-Id:\s*(I[0-9a-fA-F]{40})\b")

# Regular expression to validate Bug: line (numeric ID or N/A)
BUG_LINE_PATTERN = re.compile(r"(?im)^\s*Bug:\s*(?:b/)?(\d+|N/A|None)\s*$")

# Regular expression to validate Test: line (command or N/A)
TEST_LINE_PATTERN = re.compile(r"(?im)^\s*Test:\s*(\S.*?)\s*$")

# Forbidden tags: Tag= and CONV=
FORBIDDEN_TAG_PATTERN = re.compile(r"(?i)\bTAG\s*=")
FORBIDDEN_CONV_PATTERN = re.compile(r"(?i)\bCONV\s*=")


# Regular expression to match any Change-Id line header
CHANGE_ID_LINE_PATTERN = re.compile(r"(?im)^\s*Change-Id\s*:")


def extract_all_change_ids(msg: str) -> List[str]:
    """Extracts all Gerrit Change-Id hashes found in a commit message."""
    if not msg:
        return []
    return CHANGE_ID_PATTERN.findall(msg)


def extract_change_id(msg: str) -> Optional[str]:
    """Extracts the first Gerrit Change-Id from a commit message if present."""
    ids = extract_all_change_ids(msg)
    return ids[0] if ids else None


def validate_commit_message(
    msg: str,
    expected_change_id: Optional[str] = None,
) -> List[str]:
    """Validates a commit message against Gerrit guidelines and workflow requirements.

    Returns a list of error strings. An empty list indicates validation passed.
    """
    errors: List[str] = []

    if not msg or not msg.strip():
        return ["Commit message is empty."]

    parsed = parse_commit_message_string(msg)
    subject_str = parsed.subject.strip()

    # 1. Semantic Commit Subject Check: <type>[optional scope]: <description>
    semantic_match = SEMANTIC_SUBJECT_PATTERN.match(subject_str)
    if not semantic_match:
        errors.append(
            "Subject must follow Semantic Commit format '<type>[optional scope]: <description>' "
            f"with allowed <type>: {', '.join(ALLOWED_COMMIT_TYPES)}."
        )
    else:
        scope = semantic_match.group(2)
        description = semantic_match.group(3).strip()
        if scope is not None and not scope.strip():
            errors.append("Subject scope in '<type>(<scope>): <description>' must not be empty.")
        if not description or not description[0].isupper():
            errors.append(
                "Subject <description> must start with a capital letter "
                "(e.g. 'feat: Add feature', not 'feat: add feature')."
            )
        if description.endswith("."):
            errors.append("Subject line must not end with a period '.'.")

    # 2. Explanatory Body Check (what and why vs. how)
    if not any(line.strip() for line in parsed.body):
        errors.append(
            "Commit message is missing an explanatory body paragraph between the subject and footers "
            "(explain what and why vs. how)."
        )

    # 3. Forbidden Tags Check (No Tag= or CONV=)
    if FORBIDDEN_TAG_PATTERN.search(msg):
        errors.append("Commit message contains forbidden tag 'Tag=' ('Tag=' and 'CONV=' are not allowed).")
    if FORBIDDEN_CONV_PATTERN.search(msg):
        errors.append("Commit message contains forbidden tag 'CONV=' ('Tag=' and 'CONV=' are not allowed).")

    # 2. Bug: line check (either a bug number like 'Bug: 12345678' or 'Bug: N/A')
    if not BUG_LINE_PATTERN.search(msg):
        errors.append("Commit message is missing required 'Bug: <number>' or 'Bug: N/A' line.")

    # 3. Test: line check (test command or 'TEST: N/A')
    if not TEST_LINE_PATTERN.search(msg):
        errors.append("Commit message is missing required 'Test: <command>' or 'TEST: N/A' line.")

    # 4. Change-Id verification
    change_id_lines = CHANGE_ID_LINE_PATTERN.findall(msg)
    num_change_ids = len(change_id_lines)

    if num_change_ids > 1:
        errors.append(
            f"Multiple 'Change-Id' lines found in commit message ({num_change_ids}). "
            "Exactly one Change-Id line is allowed."
        )

    if expected_change_id and expected_change_id != "NONE":
        if num_change_ids == 0:
            errors.append(f"Change-Id line is missing. Expected: 'Change-Id: {expected_change_id}'.")
        elif num_change_ids == 1:
            found_id = extract_change_id(msg)
            if not found_id:
                errors.append(
                    f"Change-Id line format is invalid. Expected: 'Change-Id: {expected_change_id}'."
                )
            elif found_id != expected_change_id:
                errors.append(
                    f"Change-Id must not be modified. Expected: '{expected_change_id}', but found: '{found_id}'."
                )
        else:
            found_ids = extract_all_change_ids(msg)
            if expected_change_id not in found_ids:
                errors.append(
                    f"Original Change-Id is missing. Expected: 'Change-Id: {expected_change_id}'."
                )

    # 5. Gerrit format protocol validation (line length, subject length, blank lines, spaces, comment lines)
    format_errors = detect_formatting_errors_in_string(msg)
    for err in format_errors:
        if err.line is not None:
            errors.append(f"Line {err.line}: {err.message} ({err.type.value}).")
        else:
            errors.append(f"{err.message} ({err.type.value}).")

    return errors


def validate_commit_message_file(
    filepath: os.PathLike,
    expected_change_id: Optional[str] = None,
) -> List[str]:
    """Reads a commit message from a file and validates it against Gerrit standards."""
    path = Path(filepath)
    if not path.is_file():
        return [f"Commit message file '{filepath}' does not exist."]
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Error reading commit message file '{filepath}': {e}"]

    if not content.strip():
        return [f"Commit message file '{filepath}' is empty. Please write the proposed commit message into this file."]

    return validate_commit_message(content, expected_change_id=expected_change_id)


def messages_match(
    git_msg: str,
    proposed_msg: str,
    allow_added_change_id: bool = True,
) -> bool:
    """Checks if a commit message in Git matches the proposed message content.

    If allow_added_change_id is True, matches if the only difference is an automatically
    generated Gerrit Change-Id added by a Git commit-msg hook.
    """
    clean_git = git_msg.strip()
    clean_proposed = proposed_msg.strip()
    if clean_git == clean_proposed:
        return True

    if allow_added_change_id:
        # Strip trailing Change-Id from git_msg if proposed_msg did not have one
        proposed_has_change_id = bool(extract_all_change_ids(clean_proposed))
        if not proposed_has_change_id:
            # Remove any trailing Change-Id: I... from clean_git
            git_without_change_id = CHANGE_ID_PATTERN.sub("", clean_git).strip()
            if git_without_change_id == clean_proposed:
                return True

    return False


def _git(git_service: Optional[GitService] = None) -> GitService:
    return git_service or ProdGitService()


def get_head_commit_hash(
    cwd: Optional[str] = None, *, git_service: Optional[GitService] = None
) -> str:
    """Returns the current HEAD commit hash, or 'EMPTY' if repository has no commits."""
    try:
        sha = _git(git_service).head_sha(cwd=cwd, check=True)
        return sha or "EMPTY"
    except Exception:
        return "EMPTY"


def get_commit_message(
    commit_ref: str = "HEAD",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> str:
    """Returns the full commit message for the specified commit reference."""
    res = _git(git_service).run_git(
        ["log", "-1", "--format=%B", commit_ref],
        cwd=cwd,
        check=True,
        timeout_sec=float(_DEFAULT_GIT_TIMEOUT_SECS),
    )
    return res.stdout.strip()


def get_commit_parents(
    commit_ref: str = "HEAD",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> List[str]:
    """Returns a list of parent commit hashes for the specified commit reference."""
    try:
        res = _git(git_service).run_git(
            ["log", "-1", "--format=%P", commit_ref],
            cwd=cwd,
            check=True,
            timeout_sec=float(_DEFAULT_GIT_TIMEOUT_SECS),
        )
        output = res.stdout.strip()
        return output.split() if output else []
    except Exception:
        return []


def resolve_commit_hash(
    commit_ref: str = "HEAD",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> str:
    """Resolves a commit reference (hash, branch, tag, rev) to its 40-character SHA hash.

    Returns 'EMPTY' if the repository or commit cannot be resolved.
    """
    try:
        res = _git(git_service).run_git(
            ["rev-parse", "--verify", f"{commit_ref}^{{commit}}"],
            cwd=cwd,
            check=True,
            timeout_sec=float(_DEFAULT_GIT_TIMEOUT_SECS),
        )
        return res.stdout.strip()
    except Exception:
        return "EMPTY"


def find_commit_by_change_id(
    change_id: str,
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> Optional[str]:
    """Finds the commit hash containing the specified Change-Id in HEAD history or all refs."""
    if not change_id or change_id == "NONE":
        return None
    git = _git(git_service)
    for rev_scope in ("HEAD", "--all"):
        try:
            res = git.run_git(
                [
                    "log",
                    "-1",
                    f"--grep=^Change-Id: {change_id}",
                    "--format=%H",
                    rev_scope,
                ],
                cwd=cwd,
                check=True,
                timeout_sec=float(_DEFAULT_GIT_TIMEOUT_SECS),
            )
            commit_hash = res.stdout.strip()
            if commit_hash:
                return commit_hash
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Ignored error in find_commit_by_change_id (%s): %s",
                rev_scope,
                exc,
            )
    return None


def is_ancestor_commit(
    ancestor: str,
    descendant: str,
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> bool:
    """Returns True if 'ancestor' is an ancestor of 'descendant' in the Git commit graph."""
    if not ancestor or not descendant or ancestor == descendant:
        return False
    try:
        return _git(git_service).is_ancestor(ancestor, descendant, cwd=cwd)
    except Exception:
        return False
