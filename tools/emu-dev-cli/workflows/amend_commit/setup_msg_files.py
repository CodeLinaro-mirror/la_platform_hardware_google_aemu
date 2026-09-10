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

"""Sets up message files for amend-commit workflow.

Extracts the commit message of the target commit into current_msg_file,
and initializes proposed_msg_file as an empty file for the user/agent to write.
"""

import argparse
from pathlib import Path
import sys




def setup_msg_files(
    current_msg_file: str,
    proposed_msg_file: str,
) -> int:
    """Initializes empty current and proposed commit message files in workflow cache."""
    current_path = Path(current_msg_file).resolve()
    current_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    current_path.write_text("", encoding="utf-8")

    proposed_path = Path(proposed_msg_file).resolve()
    proposed_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    proposed_path.write_text("", encoding="utf-8")

    print("OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize current and proposed commit message files for amend-commit."
    )
    parser.add_argument("current_msg_file", help="Path to current commit message file.")
    parser.add_argument("proposed_msg_file", help="Path to proposed commit message file.")
    parser.add_argument("--cwd", default=None, help="Working directory (ignored).")
    parsed = parser.parse_args()

    return setup_msg_files(
        current_msg_file=parsed.current_msg_file,
        proposed_msg_file=parsed.proposed_msg_file,
    )


if __name__ == "__main__":
    sys.exit(main())
