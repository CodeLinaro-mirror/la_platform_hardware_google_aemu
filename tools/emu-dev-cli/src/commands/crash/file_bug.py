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

"""RCA analysis & Buganizer issue filing subcommand module for emu-dev-cli (`crash file-bug`)."""

import argparse
import sys

from commands.crash.advisor import run_crashadvisor_bazel
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
)


def run_file_bug(args: argparse.Namespace) -> None:
    """Runs RCA analysis and files or updates a Buganizer issue.

    Args:
        args: Parsed command line arguments containing crash_id, token, qa, verbose flags.
    """
    crash_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    sandbox_dir = get_crashadvisor_sandbox_dir(crash_id, create=True)

    cmd_args = [crash_id, "--enable-buganizer", "--auto-run", "--work-dir", sandbox_dir]
    if token:
        cmd_args.extend(["--token", token])
    if getattr(args, "qa", False):
        cmd_args.append("--qa")
    if getattr(args, "verbose", False):
        cmd_args.append("--verbose")

    print(
        f"🚀 Filing/updating Buganizer issue for Crash ID {crash_id} via CrashAdvisor..."
    )
    try:
        run_crashadvisor_bazel(cmd_args)
    except Exception as e:
        sys.stderr.write(f"❌ Failed to run CrashAdvisor: {e}\n")
        sys.exit(1)


def register_file_bug_parser(crash_subparsers) -> None:
    """Registers the `file-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    file_bug_parser = crash_subparsers.add_parser(
        "file-bug",
        help="Run RCA analysis and create or update a Buganizer issue",
        description="Runs CrashAdvisor RCA analysis for a Crash ID, extracts faulting stack traces and looper execution timelines, and creates a new Buganizer issue or updates an existing one.",
    )
    file_bug_parser.add_argument(
        "crash_id",
        help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL (Note: expects a Crash ID, NOT a Buganizer bug number)",
    )
    file_bug_parser.add_argument(
        "--token", help="OAuth2 token for Buganizer & Crash API"
    )
    file_bug_parser.add_argument(
        "--qa", action="store_true", help="Run against QA Issue Tracker endpoint"
    )
    file_bug_parser.set_defaults(func=run_file_bug)
