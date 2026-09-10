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

"""Verifier for proposed commit message in create-commit workflow."""

import argparse
import os
from pathlib import Path
import sys

_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_GIT_COMMIT_DIR = os.path.abspath(os.path.join(_CUR_DIR, "..", "common", "git_commit"))
if _GIT_COMMIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_COMMIT_DIR)

try:
    from commit_verifier import validate_commit_message_file
except ImportError:
    from workflows.common.git_commit import validate_commit_message_file


def verify_proposed_msg(filepath: str) -> int:
    """Verifies that the proposed commit message file satisfies Gerrit rules."""
    errors = validate_commit_message_file(filepath)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    print("Proposed commit message verified successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify proposed commit message file for create-commit workflow."
    )
    parser.add_argument("proposed_msg_file", help="Path to the proposed commit message file.")
    args = parser.parse_args()

    return verify_proposed_msg(filepath=args.proposed_msg_file)


if __name__ == "__main__":
    sys.exit(main())
