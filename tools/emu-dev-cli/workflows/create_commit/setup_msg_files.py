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

"""Sets up message files for create-commit workflow.

Initializes proposed_msg_file as an empty file for the user/agent to write.
"""

import argparse
from pathlib import Path
import sys


def setup_msg_files(proposed_msg_file: str) -> int:
    """Initializes empty proposed commit message file."""
    proposed_path = Path(proposed_msg_file).resolve()
    proposed_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    proposed_path.write_text("", encoding="utf-8")
    print("OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize proposed commit message file for create-commit."
    )
    parser.add_argument("proposed_msg_file", help="Path to initialize empty proposed commit message.")
    args = parser.parse_args()

    return setup_msg_files(proposed_msg_file=args.proposed_msg_file)


if __name__ == "__main__":
    sys.exit(main())
