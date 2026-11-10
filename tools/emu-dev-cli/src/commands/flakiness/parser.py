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

"""Parser registration for 'emu-dev-cli flakiness' subcommands."""

import argparse
from typing import Any

from core.command import CommandGroup
from .fetch_logs import FlakinessFetchLogsCommand
from .fix import FlakinessFixCommand
from .history import FlakinessHistoryCommand
from .list_cmd import FlakinessListCommand
from .reproduce import FlakinessReproduceCommand
from .sponge import FlakinessSpongeCommand
from .triage import FlakinessTriageCommand

FLAKINESS_DESCRIPTION = """
Automated Emulator Flakiness Analysis & Remediation Suite (`emu-dev-cli flakiness`)

A closed-loop toolsuite for developers and AI agents to query Android Test Hub (ATH) / AnTS,
inspect run histories, download diagnostic logs, locally reproduce flakes under Bazel,
trigger Jetski AI root-cause analysis, and execute one-command CL uploads.
"""


class FlakinessCommand(CommandGroup):
    """Subcommand group for flakiness analysis and remediation."""

    name = "flakiness"
    help_text = "Query, investigate, reproduce, and fix flaky emulator tests"
    description = FLAKINESS_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter
    subcommand_dest = "flakiness_cmd"
    subcommand_help = "Available flakiness subcommands"

    def __init__(self) -> None:
        super().__init__((
            FlakinessListCommand(),
            FlakinessFetchLogsCommand(),
            FlakinessTriageCommand(),
            FlakinessHistoryCommand(),
            FlakinessReproduceCommand(),
            FlakinessFixCommand(),
            FlakinessSpongeCommand(),
        ))


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Registers all flakiness CLI subcommands with argparse."""
    return FlakinessCommand().register(subparsers)
