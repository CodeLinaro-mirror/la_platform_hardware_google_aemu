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

"""Parser registration module for the crash investigation subcommand group."""

import argparse
import sys
from typing import Any

from commands.crash.analyze import CrashAnalyzeCommand, register_analyze_parser
from commands.crash.autofix import CrashAutofixCommand, register_autofix_parser
from commands.crash.file_bug import CrashFileBugCommand, register_file_bug_parser
from commands.crash.find_bug import CrashFindBugCommand, register_find_bug_parser
from commands.crash.link_bug import CrashLinkBugCommand, register_link_bug_parser
from commands.crash.list_crashes import (
    CrashListCrashesCommand,
    register_list_crashes_parser,
)
from commands.crash.reproduce import (
    CrashReproduceCommand,
    register_reproduce_parser,
)
from core.command import Command, CommandResult, bind_command
from core.context import ExecutionContext
from core.errors import UsageError


class CrashCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Top-level `crash` subcommand group delegating to typed Crash subcommands."""

    name = "crash"
    help_text = (
        "Crash investigation group: production crash discovery (list-crashes), bug linking (link-bug), "
        "stack analysis (analyze), reproduction (reproduce), Buganizer deduplication (find-bug, file-bug), "
        "and AI autofixing (autofix)"
    )

    def __init__(self) -> None:
        self._subcommands: dict[str, Command[argparse.Namespace, dict[str, Any]]] = {
            "list-crashes": CrashListCrashesCommand(),
            "link-bug": CrashLinkBugCommand(),
            "find-bug": CrashFindBugCommand(),
            "file-bug": CrashFileBugCommand(),
            "autofix": CrashAutofixCommand(),
            "reproduce": CrashReproduceCommand(),
            "analyze": CrashAnalyzeCommand(),
        }

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = (
            "Automated crash discovery, investigation, Buganizer deduplication, issue filing, "
            "go/crash annotation linking, local reproduction, and AI autofixing tools group. "
            "Contains subcommands: list-crashes, link-bug, analyze, reproduce, find-bug, file-bug, autofix."
        )
        crash_subparsers = parser.add_subparsers(
            dest="crash_cmd", help="Available crash subcommands"
        )
        register_list_crashes_parser(crash_subparsers)
        register_link_bug_parser(crash_subparsers)
        register_find_bug_parser(crash_subparsers)
        register_file_bug_parser(crash_subparsers)
        register_autofix_parser(crash_subparsers)
        register_reproduce_parser(crash_subparsers)
        register_analyze_parser(crash_subparsers)

    def _resolve_subcommand(
        self, args: argparse.Namespace
    ) -> Command[argparse.Namespace, dict[str, Any]]:
        sub_name = str(getattr(args, "crash_cmd", "") or "").strip()
        sub_cmd = self._subcommands.get(sub_name)
        if sub_cmd is None:
            valid = ", ".join(self._subcommands)
            raise UsageError(
                f"Missing or unknown crash subcommand '{sub_name or '<none>'}'.",
                remediation=f"Specify one of the supported crash subcommands: {valid}.",
            )
        return sub_cmd

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        sub_cmd = self._resolve_subcommand(args)
        sub_cmd.validate(args, ctx)

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        sub_cmd = self._resolve_subcommand(args)
        return sub_cmd.execute(args, ctx)


def register_parser(subparsers) -> None:
    """Registers the `crash` subcommand parser group in emu-dev-cli.

    Args:
        subparsers: Top-level subparser group instance from main ArgumentParser.
    """
    cmd = CrashCommand()
    crash_parser = subparsers.add_parser(
        cmd.name,
        help=cmd.help_text,
    )
    crash_parser.set_defaults(
        func=lambda args: crash_parser.print_help() or sys.exit(0)
    )
    bind_command(
        crash_parser,
        cmd,
        configure=True,
        legacy_func=lambda args: crash_parser.print_help() or sys.exit(0),
    )
