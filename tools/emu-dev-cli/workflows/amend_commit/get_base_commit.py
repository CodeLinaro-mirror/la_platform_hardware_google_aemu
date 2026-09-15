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

"""Helper script to retrieve base commit hash for the target commit during workflow initialization."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

_CUR_DIR = Path(__file__).resolve().parent
_GIT_COMMIT_DIR = str((_CUR_DIR.parent / "common" / "git_commit").resolve())
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import resolve_commit_hash
except ImportError:
    from workflows.common.git_commit import resolve_commit_hash


def main() -> int:
    parser = argparse.ArgumentParser(description="Output resolved Git commit hash or error.")
    parser.add_argument(
        "commit_ref",
        nargs="?",
        default="HEAD",
        help="Target commit ref/hash to resolve (default: HEAD).",
    )
    parser.add_argument("--cwd", default=None, help="Working directory for git commands.")
    args = parser.parse_args()

    if args.cwd:
        if not os.path.isdir(args.cwd):
            print(f"Error: Specified repository directory does not exist: '{args.cwd}'", file=sys.stderr)
            return 1
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                check=True,
                cwd=args.cwd,
            )
        except Exception:
            print(f"Error: Specified directory is not a Git repository: '{args.cwd}'", file=sys.stderr)
            return 1

    commit_hash = resolve_commit_hash(commit_ref=args.commit_ref, cwd=args.cwd)
    if commit_hash == "EMPTY":
        print(
            f"Error: Cannot resolve commit reference '{args.commit_ref}' in repository '{args.cwd or os.getcwd()}'.",
            file=sys.stderr,
        )
        return 1

    print(commit_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
