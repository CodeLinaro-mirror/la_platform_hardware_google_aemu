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

import sys

from commands.crash.analyze import register_analyze_parser
from commands.crash.autofix import register_autofix_parser
from commands.crash.file_bug import register_file_bug_parser
from commands.crash.find_bug import register_find_bug_parser
from commands.crash.reproduce import register_reproduce_parser


def register_parser(subparsers) -> None:
    """Registers the `crash` subcommand parser group in emu-dev-cli.

    Args:
        subparsers: Top-level subparser group instance from main ArgumentParser.
    """
    crash_parser = subparsers.add_parser(
        "crash",
        help="Crash investigation group: minidump symbolication, stack analysis (analyze), reproduction (reproduce), Buganizer deduplication (find-bug, file-bug), and AI autofixing (autofix) tools",
        description="Automated crash investigation, Buganizer deduplication, issue filing, local reproduction, and AI autofixing tools group. Contains subcommands: analyze, reproduce, find-bug, file-bug, autofix.",
    )
    crash_parser.set_defaults(
        func=lambda args: crash_parser.print_help() or sys.exit(0)
    )

    crash_subparsers = crash_parser.add_subparsers(
        dest="crash_cmd", help="Available crash subcommands"
    )

    # 1. find-bug
    register_find_bug_parser(crash_subparsers)

    # 2. file-bug
    register_file_bug_parser(crash_subparsers)

    # 3. autofix
    register_autofix_parser(crash_subparsers)

    # 4. reproduce
    register_reproduce_parser(crash_subparsers)

    # 5. analyze
    register_analyze_parser(crash_subparsers)
