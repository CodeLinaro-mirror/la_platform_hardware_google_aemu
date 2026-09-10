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

"""Verifier for amend-commit workflow.

Verifies that an existing commit has been amended, preserving the original
Gerrit Change-Id hash, and that its message adheres to Gerrit guidelines and
workflow requirements:
- No 'Tag=' or 'CONV='
- 'Bug: ' line (number or N/A)
- 'Test: ' line (test command or N/A)
- Exactly one Change-Id line matching the original Change-Id
"""

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Optional

_CUR_DIR = Path(__file__).resolve().parent
_GIT_COMMIT_DIR = str((_CUR_DIR.parent / "common" / "git_commit").resolve())
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import (
        extract_all_change_ids,
        find_commit_by_change_id,
        get_commit_message,
        is_ancestor_commit,
        messages_match,
        resolve_commit_hash,
        validate_commit_message,
    )
except ImportError:
    from workflows.common.git_commit import (
        extract_all_change_ids,
        find_commit_by_change_id,
        get_commit_message,
        is_ancestor_commit,
        messages_match,
        resolve_commit_hash,
        validate_commit_message,
    )


def _resolve_change_id_from_file(original_change_id: str, current_file: str) -> str:
    """Resolves Gerrit Change-Id from current commit message file if not explicitly supplied."""
    if (not original_change_id or original_change_id == "NONE") and current_file:
        try:
            current_path = Path(current_file).resolve()
            if current_path.is_file():
                current_text = current_path.read_text(encoding="utf-8")
                cids = extract_all_change_ids(current_text)
                if cids:
                    return cids[0]
        except Exception:
            pass
    return original_change_id


def _resolve_base_commit_from_file(base_commit: str, base_commit_file: str) -> str:
    """Resolves base commit hash from file if not explicitly supplied."""
    if (not base_commit or base_commit == "EMPTY") and base_commit_file:
        try:
            b_path = Path(base_commit_file).resolve()
            if b_path.is_file():
                return b_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return base_commit


def _validate_git_repository(cwd: Optional[str]) -> bool:
    """Validates that the specified directory exists and is a valid Git repository."""
    if not cwd:
        return True
    if not Path(cwd).is_dir():
        print(f"Error: Specified repository directory does not exist: '{cwd}'", file=sys.stderr)
        return False
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        return True
    except Exception:
        print(f"Error: Specified directory is not a Git repository: '{cwd}'", file=sys.stderr)
        return False


def _locate_amended_commit(
    cwd: Optional[str],
    commit_ref: str,
    original_change_id: str,
    base_commit: str,
) -> Optional[str]:
    """Locates the amended commit and verifies it was amended rather than duplicated or unmodified."""
    if original_change_id and original_change_id != "NONE":
        found_commit = find_commit_by_change_id(original_change_id, cwd=cwd)
        if not found_commit:
            print(
                f"Error: No commit with Change-Id '{original_change_id}' found in repository.",
                file=sys.stderr,
            )
            return None

        if base_commit and base_commit != "EMPTY" and found_commit == base_commit:
            ref_label = f"'{commit_ref}' ({base_commit[:8]})" if commit_ref else f"'{base_commit[:8]}'"
            print(
                f"Error: Commit {ref_label} has not been amended yet. Commit hash is unchanged.",
                file=sys.stderr,
            )
            return None

        if base_commit and base_commit != "EMPTY" and is_ancestor_commit(base_commit, found_commit, cwd=cwd):
            print(
                f"Error: A new commit ({found_commit[:8]}) was created on top of base commit {base_commit[:8]} instead of amending it.",
                file=sys.stderr,
            )
            return None

        return found_commit

    # Fallback when no Change-Id was present in the base commit
    resolved = resolve_commit_hash(commit_ref or "HEAD", cwd=cwd)
    if resolved == "EMPTY":
        print(
            f"Error: Cannot resolve commit '{commit_ref or 'HEAD'}' in repository.",
            file=sys.stderr,
        )
        return None

    if base_commit and base_commit != "EMPTY" and resolved == base_commit:
        ref_label = f"'{commit_ref}' ({base_commit[:8]})" if commit_ref else f"'{base_commit[:8]}'"
        print(
            f"Error: Commit {ref_label} has not been amended yet. Commit hash is unchanged.",
            file=sys.stderr,
        )
        return None

    if base_commit and base_commit != "EMPTY" and is_ancestor_commit(base_commit, resolved, cwd=cwd):
        print(
            f"Error: A new commit ({resolved[:8]}) was created on top of base commit {base_commit[:8]} instead of amending it.",
            file=sys.stderr,
        )
        return None

    return resolved


def _verify_commit_message_changed_from_original(
    msg: str,
    current_file: str,
    proposed_file: str,
) -> bool:
    """Verifies that the commit message has actually changed when an amended message was proposed."""
    if not current_file:
        return True
    try:
        current_path = Path(current_file).resolve()
        if current_path.is_file():
            current_text = current_path.read_text(encoding="utf-8")
            if current_text.strip() and messages_match(msg, current_text):
                if proposed_file:
                    p_path = Path(proposed_file).resolve()
                    if p_path.is_file():
                        p_text = p_path.read_text(encoding="utf-8")
                        if not messages_match(current_text, p_text):
                            print(
                                f"Error: Commit has not been amended yet. Commit message still matches original message in '{current_file}'.",
                                file=sys.stderr,
                            )
                            return False
    except Exception:
        pass
    return True


def _verify_matches_proposed_message(msg: str, proposed_file: str) -> bool:
    """Verifies that the commit message in Git matches the verified proposed message file."""
    if not proposed_file:
        return True
    try:
        proposed_path = Path(proposed_file)
        if not proposed_path.is_file():
            print(f"Error: Proposed message file '{proposed_file}' does not exist.", file=sys.stderr)
            return False
        proposed_text = proposed_path.read_text(encoding="utf-8")
        if not messages_match(msg, proposed_text):
            print(
                f"Error: Commit message in Git does not match verified message in '{proposed_file}'.",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as e:
        print(f"Error reading proposed message file '{proposed_file}': {e}", file=sys.stderr)
        return False


def verify_amend(
    base_commit: str = "",
    original_change_id: str = "NONE",
    commit_ref: str = "HEAD",
    proposed_file: str = "",
    current_file: str = "",
    base_commit_file: str = "",
    msg: str = "",
    cwd: str = None,
) -> int:
    """Verifies that a commit was amended and that the message and Change-Id are valid."""
    original_change_id = _resolve_change_id_from_file(original_change_id, current_file)
    base_commit = _resolve_base_commit_from_file(base_commit, base_commit_file)

    if not msg:
        if not _validate_git_repository(cwd):
            return 1

        target_to_inspect = _locate_amended_commit(
            cwd=cwd,
            commit_ref=commit_ref,
            original_change_id=original_change_id,
            base_commit=base_commit,
        )
        if not target_to_inspect:
            return 1

        try:
            msg = get_commit_message(target_to_inspect, cwd=cwd)
        except Exception as e:
            print(f"Error reading commit message for '{target_to_inspect}': {e}", file=sys.stderr)
            return 1

        if not _verify_commit_message_changed_from_original(msg, current_file, proposed_file):
            return 1

    errors = validate_commit_message(msg, expected_change_id=original_change_id)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if not _verify_matches_proposed_message(msg, proposed_file):
        return 1

    print("Commit amendment verification passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that an amended commit satisfies Gerrit standards and preserves Change-Id."
    )
    parser.add_argument(
        "base_commit",
        nargs="?",
        default="",
        help="Commit hash of target commit prior to amending.",
    )
    parser.add_argument(
        "original_change_id",
        nargs="?",
        default="NONE",
        help="Original Change-Id before amending.",
    )
    parser.add_argument(
        "--commit",
        default="HEAD",
        help="Target commit reference passed to init (default: HEAD).",
    )
    parser.add_argument(
        "--current-file",
        default="",
        help="Path to current commit message file to extract Change-Id from.",
    )
    parser.add_argument(
        "--proposed-file",
        default="",
        help="Path to verified proposed commit message file to match against Git commit.",
    )
    parser.add_argument(
        "--base-commit-file",
        default="",
        help="Path to file containing resolved base commit hash before amendment.",
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

    return verify_amend(
        base_commit=args.base_commit,
        original_change_id=args.original_change_id,
        commit_ref=args.commit,
        proposed_file=args.proposed_file,
        current_file=args.current_file,
        base_commit_file=args.base_commit_file,
        msg=message,
        cwd=args.cwd,
    )


if __name__ == "__main__":
    sys.exit(main())
