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

"""Tidy management and automated remediation commands for emu-dev-cli."""

import argparse
from typing import Optional

from .add_cmd import handle_tidy_add
from .check_cmd import handle_tidy_check
from .fix_cmd import handle_tidy_fix

__all__ = [
    "register_parser",
    "handle_tidy_check",
    "handle_tidy_fix",
    "handle_tidy_add",
]

_LEVEL_EXPLANATION = """
Blast-Radius Levels Explained:
  • Level 1 / safe    [Intra-Statement / Syntax]
    - Zero API risk; pure mechanical modernizations (modernize-use-nullptr,
      google-readability-casting, explicit constructors, loop-convert, etc.).
    - Applied deterministically from compiler AST replacements.

  • Level 2 / local   [Function & Class Body Scope]
    - Renames identifiers whose visibility cannot escape the enclosing function or class.
    - Touches function parameters, local variables, and private member fields.

  • Level 3 / types   [Package Scope & Header Consumers]
    - Renames type definitions across headers and packages.
    - Touches structs, classes, enums, and typedef aliases.

  • Level 4 / public  [Cross-Package & Global API Scope]
    - Renames public API interfaces, exported functions, methods, and global constants.
    - May affect all callers, downstream dependencies, and unit tests across the workspace.
"""

_CHECK_EPILOG = f"""{_LEVEL_EXPLANATION}
Examples:
  # Scan target for safe Level 1 issues
  emu-dev-cli tidy check @goldfish//emulator/libs/async:tidy --level safe

  # Scan only files modified in your current git diff
  emu-dev-cli tidy check --diff --level local

  # Output structured JSON for automation/tools
  emu-dev-cli --json tidy check @goldfish//emulator/libs/async:tidy --level all
"""

_FIX_EPILOG = f"""{_LEVEL_EXPLANATION}
Examples:
  # Fix Level 1 modernizations deterministically
  emu-dev-cli tidy fix @goldfish//emulator/libs/async:tidy --level safe

  # Fix local variables with fast verification and test runner gate
  emu-dev-cli tidy fix @goldfish//emulator/libs/async:tidy --level local --test @goldfish//emulator/libs/async:async_test

  # Transaction recovery and inspection
  emu-dev-cli tidy fix --status
  emu-dev-cli tidy fix --continue
  emu-dev-cli tidy fix --abort
"""

_AGENT_HELP_EPILOG = f"""
Agent Guidance & Usage Patterns:
{_LEVEL_EXPLANATION}
  * Subcommand Workflows & Flags:
    - JSON Output:    Pass `--json` to `emu-dev-cli` BEFORE the subcommand to get structured parsing (e.g. `emu-dev-cli --json tidy check ...`).
    - Model Tier:     Use `--model=pro` during `emu-dev-cli tidy fix` for complex preprocessor/macro heavy code.
    - Escape Hatches: If a `fix` transaction fails to compile, it pauses. Use `emu-dev-cli tidy fix --continue` after manual edits, `emu-dev-cli tidy fix --status` to inspect state, or `emu-dev-cli tidy fix --abort` to roll back to the anchor commit.

  * Common Invocations:
    - View options:   emu-dev-cli tidy <subcommand> --help (e.g. `emu-dev-cli tidy fix --help`)
    - Check target:   emu-dev-cli tidy check @goldfish//emulator/libs/async:tidy --level safe
    - Check git diff: emu-dev-cli tidy check --diff
    - Auto-remediate: emu-dev-cli tidy fix @goldfish//emulator/libs/async:tidy --level safe --test @goldfish//emulator/libs/async:async_test
    - Check status:   emu-dev-cli tidy fix --status
    - Rollback fix:   emu-dev-cli tidy fix --abort
    - Add tidy test:  emu-dev-cli tidy add emulator/libs/async --target async
"""

LEVEL_CHOICES = [
    "safe",
    "local",
    "types",
    "public",
    "1",
    "2",
    "3",
    "4",
    "all",
]


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Registers the tidy top-level command and its subcommands."""
    tidy_parser = subparsers.add_parser(
        "tidy",
        help="Clang-tidy analysis, progressive remediation, and rule generation",
        description="Inspect, level-up, and automatically remediate clang-tidy diagnostics across emulator modules.",
        epilog=_AGENT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    tidy_subparsers = tidy_parser.add_subparsers(
        dest="tidy_subcommand", help="Available tidy subcommands"
    )

    # 1. emu-dev-cli tidy check
    check_parser = tidy_subparsers.add_parser(
        "check",
        help="Scan and check clang-tidy diagnostics for a target or git diff",
        description="Scan targets or modified files and output diagnostic tables or structured JSON.",
        epilog=_CHECK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Bazel tidy report or library target to analyze (e.g. @goldfish//emulator/libs/async:tidy)",
    )
    check_parser.add_argument(
        "--diff",
        action="store_true",
        help="Filter checks strictly to files modified in current git diff",
    )
    check_parser.add_argument(
        "--file",
        type=str,
        help="Path to a specific source file to filter diagnostics for",
    )
    check_parser.add_argument(
        "--level",
        default="4",
        choices=LEVEL_CHOICES,
        metavar="LEVEL",
        help="Severity level to scan: 4/public/all (full scan for presubmit confidence, default), 3/types, 2/local, 1/safe",
    )

    check_parser.add_argument(
        "--checks",
        type=str,
        help="Custom clang-tidy checks filter string (e.g. 'modernize-*,misc-*')",
    )
    check_parser.set_defaults(func=handle_tidy_check)

    # 2. emu-dev-cli tidy fix
    fix_parser = tidy_subparsers.add_parser(
        "fix",
        help="Run multi-tier closed-loop automated remediation and test verification",
        description="Apply deterministic compiler fixes, semantic renames, and AI repairs with fastbuild verification.",
        epilog=_FIX_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    fix_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Bazel tidy target to remediate (e.g. @goldfish//emulator/libs/async:tidy)",
    )
    fix_parser.add_argument(
        "--level",
        default="4",
        choices=LEVEL_CHOICES,
        metavar="LEVEL",
        help="Check level to remediate: safe/1 (localized), local/2 (local renames), types/3 (types), public/4 (full)",
    )
    fix_parser.add_argument(
        "--checks",
        type=str,
        help="Custom clang-tidy checks filter string (e.g. 'modernize-*,misc-*')",
    )

    fix_parser.add_argument(
        "--model",
        type=str,
        default="auto",
        choices=["auto", "pro", "flash", "flash_lite"],
        help="AI model tier to use: 'auto' (escalate from flash to pro), 'pro', or 'flash'",
    )
    fix_parser.add_argument(
        "--continue",
        dest="continue_tx",
        action="store_true",
        help="Resume an in-flight refactoring transaction from the active Git log state",
    )
    fix_parser.add_argument(
        "--abort",
        action="store_true",
        help="Abort the current transaction and roll back working tree to the baseline anchor commit",
    )
    fix_parser.add_argument(
        "--status",
        action="store_true",
        help="Inspect and display active in-flight refactoring transaction state",
    )
    fix_parser.add_argument(
        "--test",
        type=str,
        help="Optional Bazel test target to run for regression verification (e.g. @goldfish//emulator/libs/async:async_test)",
    )
    fix_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview proposed fixes without applying modifications to files",
    )
    fix_parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if uncommitted git changes exist in the working tree",
    )
    fix_parser.add_argument(
        "--upload",
        action="store_true",
        help="Create a clean git commit and upload to Gerrit (repo upload . --cbr -y)",
    )
    fix_parser.add_argument(
        "--bug",
        type=str,
        help="Buganizer issue ID to attach to the commit message (Bug: <id>)",
    )
    fix_parser.set_defaults(func=handle_tidy_fix)

    # 3. emu-dev-cli tidy add
    add_parser = tidy_subparsers.add_parser(
        "add",
        help="Inject or configure clang_tidy_test rules in BUILD.bazel",
        description="Automatically inspect a BUILD.bazel file or package directory and append a valid clang_tidy_test declaration. If --target is omitted, all cc_library, cc_binary, and cc_test targets in the package are included automatically.",
        epilog="Examples:\n  # Auto-discover and add all C++ targets in package\n  emu-dev-cli tidy add emulator/libs/sockets\n\n  # Add specific target(s)\n  emu-dev-cli tidy add emulator/libs/sockets --target sockets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_parser.add_argument(
        "path",
        type=str,
        help="Path to the target BUILD.bazel file or package directory to update",
    )
    add_parser.add_argument(
        "--target",
        type=str,
        action="append",
        dest="targets",
        help="Specific target name(s) to include in clang_tidy_test (defaults to all C++ targets in package if omitted)",
    )
    add_parser.set_defaults(func=handle_tidy_add)
