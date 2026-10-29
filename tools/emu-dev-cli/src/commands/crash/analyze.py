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
import sys
from typing import Any

from commands.crash.advisor import CrashAdvisorRunner
from commands.crash.autofix import CrashAutofixCommand
from commands.crash.file_bug import CrashFileBugCommand
from commands.crash.find_bug import CrashFindBugCommand
from commands.crash.utils import (
    acquire_auth_token,
    is_path_secure_user_owned,
    parse_crash_id,
    resolve_crash_work_dir,
)
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import CommandValidationError, UsageError
from lib.output import print_status


class CrashAnalyzeCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash analyze`."""

    name = "analyze"
    help_text = (
        "Run minidump symbolication, stack fingerprinting, interactive RCA, "
        "or automated Buganizer filing and autofixing"
    )
    help = help_text
    description = (
        "Unified crash analysis tool. Downloads minidumps, symbolicates stack traces, "
        "computes normalized stack fingerprints, and executes interactive or automated "
        "Root Cause Analysis (RCA). Can automatically file Buganizer issues (--file-bug) "
        "and trigger autonomous AI fixes (--autofix)."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
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
            "--autofix",
            action="store_true",
            help="Automatically attempt closed-loop fix",
        )
        parser.add_argument(
            "--auto-run", action="store_true", help="Non-interactive RCA mode"
        )
        parser.add_argument("--timeout", help="Execution timeout (e.g. 30m)")
        parser.add_argument(
            "--work-dir",
            dest="work_dir",
            help="Optional explicit work directory override",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON result",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        if not raw_id or not parse_crash_id(raw_id):
            raise CommandValidationError(
                "Crash ID cannot be empty for 'crash analyze'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )
        if getattr(args, "file_bug", False):
            CrashFileBugCommand().validate(args, ctx)
        elif getattr(args, "autofix", False):
            CrashAutofixCommand().validate(args, ctx)
        else:
            CrashFindBugCommand().validate(args, ctx)
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        if getattr(args, "file_bug", False):
            return CrashFileBugCommand().execute(args, ctx)
        if getattr(args, "autofix", False):
            return CrashAutofixCommand().execute(args, ctx)

        find_result = CrashFindBugCommand().execute(args, ctx)
        crash_id = parse_crash_id(str(args.crash_id))
        explicit_work_dir = getattr(args, "work_dir", None)
        work_dir = resolve_crash_work_dir(
            ctx.config.cache_dir, crash_id, explicit_work_dir
        )

        token = acquire_auth_token(getattr(args, "token", None))
        is_auto_run = bool(getattr(args, "auto_run", False))

        flags: list[str] = []
        if is_auto_run:
            flags.append("--auto-run")
        if getattr(args, "timeout", None):
            flags.extend(["--timeout", str(args.timeout)])

        advisor_runner = CrashAdvisorRunner()
        advisor_runner.run(ctx, crash_id, work_dir, flags=flags, token=token)

        # In interactive mode (not auto-run), automatically execute investigation_cmd.sh
        if not is_auto_run:
            self._launch_interactive_session(ctx, work_dir)

        data = dict(find_result.data)
        data["action"] = "crash analyze"
        data["sandbox_dir"] = str(work_dir)
        return CommandResult(
            data=data,
            action="crash analyze",
            summary=f"Completed crash analysis for {crash_id}.",
        )

    def _launch_interactive_session(
        self, ctx: ExecutionContext, work_dir: Path
    ) -> None:
        script_path = work_dir / "investigation_cmd.sh"
        if not script_path.exists():
            sys.stderr.write(f"⚠️ Investigation script not found at {script_path}\n")
            return
        if not (
            is_path_secure_user_owned(str(work_dir), is_dir=True)
            and is_path_secure_user_owned(str(script_path), is_dir=False)
        ):
            current_user = os.getuid() if hasattr(os, "getuid") else "current"
            sys.stderr.write(
                f"⚠️ Security warning: Refusing to execute {script_path} because directory or script permissions/ownership are insecure (must be owned by user {current_user} and not writable by group/others).\n"
            )
            return
        print_status(
            f"\n🚀 Launching interactive AI investigation session ({script_path})...\n",
            json_mode=ctx.config.json_mode,
        )
        try:
            script_path.chmod(0o700)
            ctx.process_runner.run(
                ["bash", str(script_path)],
                timeout_sec=3600.0,
                stream_output=True,
            )
        except Exception as e:
            sys.stderr.write(
                f"⚠️ Failed to launch interactive script: {e}\n"
            )


def register_analyze_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `analyze` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    return CrashAnalyzeCommand().register(crash_subparsers)
