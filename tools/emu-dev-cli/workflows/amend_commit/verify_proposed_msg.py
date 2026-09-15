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

"""Verifier for proposed commit message in amend-commit workflow."""

import argparse
import os
from pathlib import Path
import sys

_CUR_DIR = Path(__file__).resolve().parent
_GIT_COMMIT_DIR = str((_CUR_DIR.parent / "common" / "git_commit").resolve())
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import (
        extract_all_change_ids,
        find_commit_by_change_id,
        validate_commit_message_file,
    )
except ImportError:
    from workflows.common.git_commit import (
        extract_all_change_ids,
        find_commit_by_change_id,
        validate_commit_message_file,
    )


def verify_proposed_msg(
    filepath: str,
    expected_change_id: str = "NONE",
    current_file: str = "",
    base_commit_file: str = "",
    cwd: str = None,
) -> int:
    """Verifies that the proposed commit message file satisfies Gerrit rules and preserves Change-Id."""
    change_id = expected_change_id if expected_change_id and expected_change_id != "NONE" else None

    if current_file:
        current_path = Path(current_file).resolve()
        if not current_path.is_file():
            print(f"Error: Current commit message file '{current_file}' does not exist.", file=sys.stderr)
            return 1
        current_text = current_path.read_text(encoding="utf-8")
        if not current_text.strip():
            print(
                f"Error: Current commit message file '{current_file}' is empty. Please copy the current commit message into this file.",
                file=sys.stderr,
            )
            return 1
        cids = extract_all_change_ids(current_text)
        if not cids:
            print(
                f"Error: No Gerrit Change-Id found in current commit message file '{current_file}'.",
                file=sys.stderr,
            )
            return 1
        if len(cids) > 1:
            print(
                f"Error: Multiple 'Change-Id' lines found in current commit message file '{current_file}'.",
                file=sys.stderr,
            )
            return 1
        change_id = cids[0]

        if base_commit_file:
            base_hash = find_commit_by_change_id(change_id, cwd=cwd)
            if base_hash:
                b_path = Path(base_commit_file).resolve()
                b_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                b_path.write_text(base_hash.strip() + "\n", encoding="utf-8")

    errors = validate_commit_message_file(filepath, expected_change_id=change_id)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    print("Proposed commit message verified successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify proposed commit message file for amend-commit workflow."
    )
    parser.add_argument("proposed_msg_file", help="Path to the proposed commit message file.")
    parser.add_argument(
        "expected_change_id",
        nargs="?",
        default="NONE",
        help="Expected Gerrit Change-Id to preserve.",
    )
    parser.add_argument(
        "--current-file",
        default="",
        help="Path to the current commit message file to extract Change-Id from.",
    )
    parser.add_argument(
        "--base-commit-file",
        default="",
        help="Path to save resolved base commit hash.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for git operations.",
    )
    args = parser.parse_args()

    return verify_proposed_msg(
        filepath=args.proposed_msg_file,
        expected_change_id=args.expected_change_id,
        current_file=args.current_file,
        base_commit_file=args.base_commit_file,
        cwd=args.cwd,
    )


if __name__ == "__main__":
    sys.exit(main())
