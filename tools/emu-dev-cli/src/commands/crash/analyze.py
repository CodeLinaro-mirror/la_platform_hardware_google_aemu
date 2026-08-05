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

"""Unified crash analysis subcommand module for emu-dev-cli (`crash analyze`)."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from commands.crash.advisor import ensure_crashadvisor_imports, run_crashadvisor_bazel
from commands.crash.autofix import run_autofix
from commands.crash.file_bug import run_file_bug
from commands.crash.find_bug import run_find_bug
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    is_path_secure_user_owned,
    parse_crash_id,
)


def run_analyze(args: argparse.Namespace) -> None:
    """Unified crash analysis command handler.

    Routes to `run_file_bug` if `--file-bug` is supplied, `run_autofix` if `--autofix` is supplied,
    or executes minidump deduplication and interactive CrashAdvisor RCA.

    Args:
        args: Parsed command line arguments.
    """
    crash_id = parse_crash_id(args.crash_id)
    file_bug_flag = getattr(args, "file_bug", False)
    autofix_flag = getattr(args, "autofix", False)

    if file_bug_flag:
        run_file_bug(args)
    elif autofix_flag:
        run_autofix(args)
    else:
        # Run find-bug first for quick deduplication feedback, then run interactive/auto RCA
        run_find_bug(args)
        print("\n------------------------------------------------------------")
        print(f"🔬 Launching interactive CrashAdvisor RCA diagnostic session...")
        crash_modules = ensure_crashadvisor_imports()

        sandbox_dir = get_crashadvisor_sandbox_dir(crash_id, create=True)
        is_auto_run = getattr(args, "auto_run", False)
        token = acquire_auth_token(getattr(args, "token", None))
        cmd_args = [crash_id, "--work-dir", sandbox_dir]
        if token:
            cmd_args.extend(["--token", token])
        if is_auto_run:
            cmd_args.append("--auto-run")
        if getattr(args, "timeout", None):
            cmd_args.extend(["--timeout", args.timeout])

        run_crashadvisor_bazel(cmd_args)

        # In interactive mode (not auto-run), automatically execute investigation_cmd.sh
        if not is_auto_run:
            script_path = Path(sandbox_dir) / "investigation_cmd.sh"
            if script_path.exists():
                if not (
                    is_path_secure_user_owned(sandbox_dir, is_dir=True)
                    and is_path_secure_user_owned(str(script_path), is_dir=False)
                ):
                    current_user = os.getuid() if hasattr(os, "getuid") else "current"
                    sys.stderr.write(
                        f"⚠️ Security warning: Refusing to execute {script_path} because directory or script permissions/ownership are insecure (must be owned by user {current_user} and not writable by group/others).\n"
                    )
                else:
                    print(
                        f"\n🚀 Launching interactive AI investigation session ({script_path})...\n"
                    )
                    try:
                        script_path.chmod(0o700)
                        subprocess.run(["bash", str(script_path)])
                    except Exception as e:
                        sys.stderr.write(
                            f"⚠️ Failed to launch interactive script: {e}\n"
                        )
            else:
                sys.stderr.write(
                    f"⚠️ Investigation script not found at {script_path}\n"
                )


def _add_analyze_arguments(parser: argparse.ArgumentParser) -> None:
    """Helper to populate argument flags for analyze subcommands."""
    parser.add_argument(
        "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
    )
    parser.add_argument("--token", help="OAuth2 token")
    parser.add_argument(
        "--file-bug",
        action="store_true",
        help="Automatically file/update Buganizer issue",
    )
    parser.add_argument(
        "--autofix", action="store_true", help="Automatically attempt closed-loop fix"
    )
    parser.add_argument(
        "--auto-run", action="store_true", help="Non-interactive RCA mode"
    )
    parser.add_argument("--timeout", help="Execution timeout (e.g. 30m)")
    parser.set_defaults(func=run_analyze)


def register_analyze_parser(crash_subparsers) -> None:
    """Registers the `analyze` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    analyze_parser = crash_subparsers.add_parser(
        "analyze",
        help="Run minidump symbolication, stack fingerprinting, interactive RCA, or automated Buganizer filing and autofixing",
        description="Unified crash analysis tool. Downloads minidumps, symbolicates stack traces, computes normalized stack fingerprints, and executes interactive or automated Root Cause Analysis (RCA). Can automatically file Buganizer issues (--file-bug) and trigger autonomous AI fixes (--autofix).",
    )
    _add_analyze_arguments(analyze_parser)
