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

import os
from pathlib import Path
import re
import subprocess
from typing import List, Optional


# Regular expression to extract Gerrit Change-Id
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

    # 1. Forbidden Tags Check (No Tag= or CONV=)
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


def get_head_commit_hash(cwd: Optional[str] = None) -> str:
    """Returns the current HEAD commit hash, or 'EMPTY' if repository has no commits."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        return res.stdout.strip()
    except Exception:
        return "EMPTY"


def get_commit_message(commit_ref: str = "HEAD", cwd: Optional[str] = None) -> str:
    """Returns the full commit message for the specified commit reference."""
    res = subprocess.run(
        ["git", "log", "-1", "--format=%B", commit_ref],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    return res.stdout.strip()


def get_commit_parents(commit_ref: str = "HEAD", cwd: Optional[str] = None) -> List[str]:
    """Returns a list of parent commit hashes for the specified commit reference."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%P", commit_ref],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        output = res.stdout.strip()
        return output.split() if output else []
    except Exception:
        return []


def resolve_commit_hash(commit_ref: str = "HEAD", cwd: Optional[str] = None) -> str:
    """Resolves a commit reference (hash, branch, tag, rev) to its 40-character SHA hash.

    Returns 'EMPTY' if the repository or commit cannot be resolved.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit_ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        return res.stdout.strip()
    except Exception:
        return "EMPTY"


def find_commit_by_change_id(change_id: str, cwd: Optional[str] = None) -> Optional[str]:
    """Finds the commit hash containing the specified Change-Id in HEAD history or all refs."""
    if not change_id or change_id == "NONE":
        return None
    try:
        res = subprocess.run(
            ["git", "log", "-1", f"--grep=^Change-Id: {change_id}", "--format=%H", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        commit_hash = res.stdout.strip()
        if commit_hash:
            return commit_hash
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["git", "log", "-1", f"--grep=^Change-Id: {change_id}", "--format=%H", "--all"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        commit_hash = res.stdout.strip()
        if commit_hash:
            return commit_hash
    except Exception:
        pass

    return None


def is_ancestor_commit(ancestor: str, descendant: str, cwd: Optional[str] = None) -> bool:
    """Returns True if 'ancestor' is an ancestor of 'descendant' in the Git commit graph."""
    if not ancestor or not descendant or ancestor == descendant:
        return False
    try:
        res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            cwd=cwd,
        )
        return res.returncode == 0
    except Exception:
        return False
