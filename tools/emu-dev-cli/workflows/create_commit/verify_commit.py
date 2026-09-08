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

"""Verifier for create-commit workflow.

Verifies that a new commit has been created and that its message adheres to
Gerrit guidelines and workflow requirements:
- No 'Tag=' or 'CONV='
- 'Bug: ' line (number or N/A)
- 'Test: ' line (test command or N/A)
"""

import argparse
import os
from pathlib import Path
import sys

_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_GIT_COMMIT_DIR = os.path.abspath(os.path.join(_CUR_DIR, "..", "common", "git_commit"))
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import (
        get_commit_message,
        get_head_commit_hash,
        messages_match,
        validate_commit_message,
    )
except ImportError:
    from workflows.common.git_commit import (
        get_commit_message,
        get_head_commit_hash,
        messages_match,
        validate_commit_message,
    )


def verify_commit(
    base_commit: str = "",
    proposed_file: str = "",
    msg: str = "",
    cwd: str = None,
) -> int:
    """Verifies that a new commit exists and satisfies all commit message rules."""
    # If direct message is not supplied, inspect the git repository
    if not msg:
        current_head = get_head_commit_hash(cwd=cwd)
        if current_head == "EMPTY":
            print(
                "Error: No commit found in repository. Please ensure a commit has been created.",
                file=sys.stderr,
            )
            return 1

        if base_commit and base_commit != "EMPTY" and current_head == base_commit:
            print(
                f"Error: No new commit has been created. Current HEAD matches base commit {base_commit}.",
                file=sys.stderr,
            )
            return 1

        try:
            msg = get_commit_message("HEAD", cwd=cwd)
        except Exception as e:
            print(f"Error reading commit message: {e}", file=sys.stderr)
            return 1

    errors = validate_commit_message(msg)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if proposed_file:
        try:
            proposed_path = Path(proposed_file)
            if not proposed_path.is_file():
                print(f"Error: Proposed message file '{proposed_file}' does not exist.", file=sys.stderr)
                return 1
            proposed_text = proposed_path.read_text(encoding="utf-8")
            if not messages_match(msg, proposed_text, allow_added_change_id=True):
                print(
                    f"Error: Commit message in Git does not match verified message in '{proposed_file}'.",
                    file=sys.stderr,
                )
                return 1
        except Exception as e:
            print(f"Error reading proposed message file '{proposed_file}': {e}", file=sys.stderr)
            return 1

    print("Commit verification passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that a newly created commit satisfies Gerrit standards and verification rules."
    )
    parser.add_argument(
        "base_commit",
        nargs="?",
        default="",
        help="Commit hash of HEAD prior to creating the new commit.",
    )
    parser.add_argument(
        "--proposed-file",
        default="",
        help="Path to verified proposed commit message file to match against Git commit.",
    )
    parser.add_argument(
        "--msg",
        default="",
        help="Direct commit message content to verify (for unit testing).",
    )
    parser.add_argument(
        "--msg-file",
        default="",
        help="Path to file containing commit message to verify (for testing).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for git operations.",
    )
    args = parser.parse_args()

    message = args.msg
    if args.msg_file:
        try:
            message = Path(args.msg_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"Error reading message file '{args.msg_file}': {e}", file=sys.stderr)
            return 1

    return verify_commit(
        base_commit=args.base_commit,
        proposed_file=args.proposed_file,
        msg=message,
        cwd=args.cwd,
    )


if __name__ == "__main__":
    sys.exit(main())
