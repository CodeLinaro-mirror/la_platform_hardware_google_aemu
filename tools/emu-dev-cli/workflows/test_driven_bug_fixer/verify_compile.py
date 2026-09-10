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

"""Verifier for compilation in test-driven-bug-fixer workflow.

Verifies that the codebase compiles cleanly without build errors.
"""

import argparse
from pathlib import Path
import subprocess
import sys

# TODO: Add test execution verification once test runner commands are integrated.
# - For Step 1 (test phase): verify that the newly authored test fails against unfixed code.
# - For Step 3 (fix phase): verify that the test passes against the fixed code.


def verify_compile(cmd: str, cwd: str = None, timeout: int = 600, phase: str = "") -> int:
    """Executes the compilation command in bash and asserts clean exit code 0."""
    if not cmd or not cmd.strip():
        print("Error: No compilation command specified.", file=sys.stderr)
        return 1

    work_dir = Path(cwd).resolve() if cwd and str(cwd).strip() else Path.cwd()
    if not work_dir.is_dir():
        print(f"Error: Working directory '{work_dir}' does not exist.", file=sys.stderr)
        return 1

    phase_str = f" [{phase}]" if phase else ""
    try:
        proc = subprocess.run(
            ["/bin/bash", "-c", cmd],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"Compilation{phase_str} timed out after {timeout} seconds.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Compilation{phase_str} failed to execute: {e}", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        err_out = proc.stderr.strip() or proc.stdout.strip() or f"Exited with code {proc.returncode}"
        print(f"Compilation{phase_str} failed with exit code {proc.returncode}:\n{err_out}", file=sys.stderr)
        return proc.returncode

    print(f"Compilation{phase_str} succeeded.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that the build/compile step succeeds.")
    parser.add_argument("--cmd", required=True, help="Build command to execute.")
    parser.add_argument("--cwd", default=None, help="Working directory for compilation.")
    parser.add_argument("--timeout", type=int, default=600, help="Build timeout in seconds.")
    parser.add_argument("--phase", default="", help="Optional phase label (e.g. 'test' or 'fix').")
    args = parser.parse_args()

    return verify_compile(cmd=args.cmd, cwd=args.cwd, timeout=args.timeout, phase=args.phase)


if __name__ == "__main__":
    sys.exit(main())
