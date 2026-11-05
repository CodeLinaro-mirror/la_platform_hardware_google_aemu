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
from collections.abc import Sequence
from typing import Any

from commands.crash.analyze import CrashAnalyzeCommand
from commands.crash.autofix import CrashAutofixCommand
from commands.crash.file_bug import CrashFileBugCommand
from commands.crash.find_bug import CrashFindBugCommand
from commands.crash.link_bug import CrashLinkBugCommand
from commands.crash.list_crashes import CrashListCrashesCommand
from commands.crash.reproduce import CrashReproduceCommand
from core.command import Command, CommandGroup


class CrashCommand(CommandGroup):
    """Top-level `crash` subcommand group delegating to typed Crash subcommands."""

    name = "crash"
    help_text = (
        "Crash investigation group: production crash discovery (list-crashes), bug linking (link-bug), "
        "stack analysis (analyze), reproduction (reproduce), Buganizer deduplication (find-bug, file-bug), "
        "and AI autofixing (autofix)"
    )
    help = help_text
    description = (
        "Automated crash discovery, investigation, Buganizer deduplication, issue filing, "
        "go/crash annotation linking, local reproduction, and AI autofixing tools group. "
        "Contains subcommands: list-crashes, link-bug, analyze, reproduce, find-bug, file-bug, autofix."
    )
    subcommand_dest = "crash_cmd"
    subcommand_help = "Available crash subcommands"

    subcommand_classes = (
        CrashListCrashesCommand,
        CrashLinkBugCommand,
        CrashFindBugCommand,
        CrashFileBugCommand,
        CrashAutofixCommand,
        CrashReproduceCommand,
        CrashAnalyzeCommand,
    )

    def __init__(
        self, subcommands: Sequence[Command[Any, Any]] | None = None
    ) -> None:
        if subcommands is None:
            subcommands = tuple(cls() for cls in self.subcommand_classes)
        super().__init__(subcommands)


def register_parser(subparsers) -> argparse.ArgumentParser:
    """Registers the `crash` subcommand parser group in emu-dev-cli.

    Args:
        subparsers: Top-level subparser group instance from main ArgumentParser.
    """
    return CrashCommand().register(subparsers)
