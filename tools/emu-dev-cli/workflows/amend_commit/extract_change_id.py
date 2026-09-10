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

"""Helper script to extract Gerrit Change-Id from current HEAD commit during workflow initialization."""

import argparse
from pathlib import Path
import sys

_CUR_DIR = Path(__file__).resolve().parent
_GIT_COMMIT_DIR = str((_CUR_DIR.parent / "common" / "git_commit").resolve())
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import extract_change_id, get_commit_message
except ImportError:
    from workflows.common.git_commit import extract_change_id, get_commit_message


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Output Gerrit Change-Id hash from HEAD commit message or 'NONE'."
    )
    parser.add_argument(
        "commit_ref",
        nargs="?",
        default="HEAD",
        help="Commit reference to inspect (default: HEAD).",
    )
    parser.add_argument(
        "--msg",
        default="",
        help="Direct commit message to inspect (for testing).",
    )
    parser.add_argument(
        "--msg-file",
        default="",
        help="Path to file containing commit message (for testing).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for git commands.",
    )
    args = parser.parse_args()

    message = args.msg
    if args.msg_file:
        try:
            message = Path(args.msg_file).read_text(encoding="utf-8")
        except Exception:
            print("NONE")
            return 0

    if not message:
        try:
            message = get_commit_message(args.commit_ref, cwd=args.cwd)
        except Exception:
            print("NONE")
            return 0

    cid = extract_change_id(message)
    print(cid if cid else "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
