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

"""Shared defaults and argparse options for the `flakiness` subcommands."""

import argparse
from collections.abc import Sequence

from lib.ath_api import SUPPORTED_TARGET_CHOICES, SUPPORTED_TEST_IDENTIFIER_CHOICES

DEFAULT_TARGET = "emulator_linux_x64"
DEFAULT_BRANCH = "git_emu-main-next"
DEFAULT_COMPONENT_ID = "1016880"
DEFAULT_AI_MODEL = "Gemini Next"
ALL_TEST_IDENTIFIERS = "all"
ALL_TARGETS = "all"
ATH_LOOKBACK_DAYS = 7
ATH_WINDOW = 100

MODE_ALL = "all"
MODE_PRESUBMIT = "presubmit"
MODE_POSTSUBMIT = "postsubmit"
MODE_CHOICES = (MODE_ALL, MODE_PRESUBMIT, MODE_POSTSUBMIT)
WINDOW_CHOICES = (100, 250)

TARGET_OR_ALL_HELP = "Target platform matrix or 'all' (default: emulator_linux_x64)"
TARGET_HELP = "Target platform matrix (default: emulator_linux_x64)"
TEST_IDENTIFIER_HELP = (
    "Test identifier filter: 'all' (default), 'android_emulator/ets' ('ets'), "
    "'android_emulator/release' ('release'), or 'android_emulator/unit_tests' "
    "('unit_tests')"
)
POSTSUBMIT_MODE_HELP = (
    "Run type filter: 'postsubmit' (default), 'presubmit', or 'all' (both pre and post)"
)
TOKEN_HELP = "Explicit OAuth2 token with androidbuild.internal scope"
TIMEOUT_HELP = (
    "Execution timeout limit in seconds (default: dynamically calculated based "
    "on target platform and iteration count)"
)


def add_target_option(
    parser: argparse.ArgumentParser,
    *,
    default: str = DEFAULT_TARGET,
    choices: Sequence[str] = SUPPORTED_TARGET_CHOICES,
    help_text: str = TARGET_OR_ALL_HELP,
) -> None:
    """Adds `--target`."""
    parser.add_argument(
        "--target", type=str, default=default, choices=choices, help=help_text
    )


def add_test_identifier_option(
    parser: argparse.ArgumentParser,
    *,
    default: str | None = ALL_TEST_IDENTIFIERS,
    help_text: str = TEST_IDENTIFIER_HELP,
) -> None:
    """Adds `--config` and its `--test-id` / `--suite` aliases."""
    parser.add_argument(
        "--config",
        "--test-id",
        "--suite",
        dest="config",
        type=str,
        default=default,
        choices=SUPPORTED_TEST_IDENTIFIER_CHOICES,
        help=help_text,
    )


def add_branch_option(parser: argparse.ArgumentParser) -> None:
    """Adds `--branch`."""
    parser.add_argument(
        "--branch",
        type=str,
        default=DEFAULT_BRANCH,
        help=f"Target branch filter (default: {DEFAULT_BRANCH})",
    )


def add_days_option(
    parser: argparse.ArgumentParser,
    *,
    default: int | None = None,
    help_text: str = "Historical window in days (default: 7 for ATH, 20 for studio-*)",
) -> None:
    """Adds `--days`."""
    parser.add_argument("--days", type=int, default=default, help=help_text)


def add_mode_option(
    parser: argparse.ArgumentParser,
    *,
    default: str = MODE_POSTSUBMIT,
    help_text: str = POSTSUBMIT_MODE_HELP,
) -> None:
    """Adds `--mode`."""
    parser.add_argument(
        "--mode", type=str, default=default, choices=MODE_CHOICES, help=help_text
    )


def add_min_flake_rate_option(
    parser: argparse.ArgumentParser, *, help_text: str
) -> None:
    """Adds `--min-flake-rate`."""
    parser.add_argument(
        "--min-flake-rate",
        "--min_flake_rate",
        dest="min_flake_rate",
        type=float,
        default=None,
        help=help_text,
    )


def add_studio_filter_options(parser: argparse.ArgumentParser) -> None:
    """Adds the Studio / TestActions `--dir` and `--window` filters."""
    parser.add_argument(
        "--dir",
        "--directory",
        dest="directory",
        type=str,
        default=None,
        help=(
            "Bazel package prefix filter for Studio / TestActions queries "
            "(default: //tools/vendor/google/cli/android-device-hub/electron "
            "for studio-* targets)"
        ),
    )
    parser.add_argument(
        "--window",
        type=int,
        choices=WINDOW_CHOICES,
        default=None,
        help=(
            "Rolling run window for Studio / TestActions flake rate threshold "
            "(default: 250 for Android Device Hub, 100 otherwise)"
        ),
    )


def add_f1_options(parser: argparse.ArgumentParser) -> None:
    """Adds `--accounting-group` and `--print-sql` for F1 TestActions queries."""
    parser.add_argument(
        "--accounting-group",
        "--accounting_group",
        dest="accounting_group",
        type=str,
        default=None,
        help="Optional F1 MDB accounting group (--f1_sql_accounting_group)",
    )
    parser.add_argument(
        "--print-sql",
        "--print_sql",
        dest="print_sql",
        action="store_true",
        help="Print the generated F1 SQL query for Studio targets and exit without executing",
    )


def add_token_option(
    parser: argparse.ArgumentParser, *, help_text: str = TOKEN_HELP
) -> None:
    """Adds `--token`."""
    parser.add_argument("--token", type=str, default=None, help=help_text)


def add_timeout_option(parser: argparse.ArgumentParser) -> None:
    """Adds the Bazel stress-test `--timeout`."""
    parser.add_argument("--timeout", type=int, default=None, help=TIMEOUT_HELP)
