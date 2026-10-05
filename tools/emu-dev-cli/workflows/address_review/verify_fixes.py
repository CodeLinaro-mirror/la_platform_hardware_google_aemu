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

"""Verifier for Step 0 of `address-review`: ensures all comments have staged replies and tests pass."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def verify_all_fixes(
    queue_file: Path | str,
    test_cmd: str = "",
) -> int:
    """Verifies that every unresolved thread has a staged reply and runs the test command."""
    q_path = Path(queue_file)
    if not q_path.is_file():
        print(f"Error: Review queue file '{q_path}' does not exist.", file=sys.stderr)
        return 1

    try:
        q_data = json.loads(q_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error reading review queue file '{q_path}': {exc}", file=sys.stderr)
        return 1

    missing_replies: list[str] = []
    primary_repo = Path.cwd()
    for cl_entry in q_data.get("cls") or []:
        cl_num = cl_entry.get("cl_number")
        repo_str = cl_entry.get("repo_path")
        if repo_str and Path(repo_str).is_dir():
            primary_repo = Path(repo_str)
        for thread in cl_entry.get("unresolved_threads") or []:
            if thread.get("published"):
                continue
            reply_msg = str(thread.get("staged_reply") or "").strip()
            if not reply_msg:
                leaf_id = thread.get("leaf_id") or thread.get("root_id")
                loc = f"{thread.get('file_path')}:{thread.get('line')}"
                missing_replies.append(
                    f"CL {cl_num} comment `{leaf_id}` ({loc}) is missing a staged reply. "
                    f"Run: emu-dev-cli gerrit reply {cl_num} --comment-id \"{leaf_id}\" "
                    f"--message \"Done: <explanation>\" --resolve --queue-file \"{q_path}\" --stage-only"
                )

    if missing_replies:
        print(
            "Error: Not all unresolved review comments have staged replies:",
            file=sys.stderr,
        )
        for item in missing_replies:
            print(f"  - {item}", file=sys.stderr)
        return 1

    clean_test_cmd = (test_cmd or "").strip()
    if clean_test_cmd:
        res = subprocess.run(
            clean_test_cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(primary_repo),
        )
        if res.returncode != 0:
            combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            tail = "\n".join(combined.splitlines()[-40:])
            print(
                f"Error: Verification command `{clean_test_cmd}` failed in '{primary_repo}' "
                f"(exit {res.returncode}):\n{tail}",
                file=sys.stderr,
            )
            return 1

    print("All review comments have staged replies and verification tests passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that all Gerrit review comments have staged replies and local tests pass."
    )
    parser.add_argument("--queue-file", required=True, help="Path to queue JSON file")
    parser.add_argument(
        "--test-cmd",
        default="",
        help="Verification command to run (e.g. mise presubmit or bazel test ...)",
    )
    args = parser.parse_args()
    return verify_all_fixes(queue_file=args.queue_file, test_cmd=args.test_cmd)


if __name__ == "__main__":
    sys.exit(main())
