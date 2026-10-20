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
import sys
from lib.ath_api import (
    SUPPORTED_TARGET_CHOICES,
    SUPPORTED_TARGET_PLATFORMS,
)
from .fetch_logs import handle_flakiness_fetch_logs
from .fix import handle_flakiness_fix
from .history import handle_flakiness_history
from .list_cmd import handle_flakiness_list
from .reproduce import handle_flakiness_reproduce
from .sponge import handle_flakiness_sponge
from .triage import handle_flakiness_triage


def run_flakiness_help(args):
    """Displays help text for flakiness command family."""
    if hasattr(args, "parser"):
        args.parser.print_help()
    sys.exit(0)


def register_parser(subparsers):
    """Registers all flakiness CLI subcommands with argparse."""
    flakiness_parser = subparsers.add_parser(
        "flakiness",
        help="Query, investigate, reproduce, and fix flaky emulator tests",
        description="""
Automated Emulator Flakiness Analysis & Remediation Suite (`emu-dev-cli flakiness`)

A closed-loop toolsuite for developers and AI agents to query Android Test Hub (ATH) / AnTS,
inspect run histories, download diagnostic logs, locally reproduce flakes under Bazel,
trigger Jetski AI root-cause analysis, and execute one-command CL uploads.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    flakiness_subparsers = flakiness_parser.add_subparsers(
        dest="flakiness_cmd", help="Available flakiness subcommands"
    )
    flakiness_parser.set_defaults(
        parser=flakiness_parser, func=run_flakiness_help
    )

    # Subcommand: list
    list_parser = flakiness_subparsers.add_parser(
        "list",
        help="Query Android Test Hub for flaky test targets under specified target platforms",
        description="""
Query Flaky Emulator Test Targets (`emu-dev-cli flakiness list`)

Queries Android Test Hub (ATH) / AnTS invocations over a historical time window and aggregates test failure rates into a formatted Markdown table (or raw JSON with --json).

Sample Invocations:
  # List all flaky tests on TSAN with flake rate >= 10% across the last 7 days:
  emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

  # List flaky tests across all 5 target platforms combined:
  emu-dev-cli flakiness list --target all --mode all --min-flake-rate 5.0

  # Output machine-readable JSON for agentic parsing:
  emu-dev-cli --json flakiness list --target emulator_linux_x64_tsan

  # Supply explicit OAuth2 token (for macOS workstations):
  emu-dev-cli flakiness list --token "$TOKEN" --target emulator_linux_x64_tsan
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    list_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64",
        choices=SUPPORTED_TARGET_CHOICES,
        help="Target platform matrix or 'all' (default: emulator_linux_x64)",
    )
    list_parser.add_argument(
        "--config",
        type=str,
        default="devtools/emulator",
        help="Android Test Hub configuration name (default: devtools/emulator)",
    )
    list_parser.add_argument(
        "--branch",
        type=str,
        default="git_emu-main-next",
        help="Target branch filter (default: git_emu-main-next)",
    )
    list_parser.add_argument(
        "--min-flake-rate",
        "--min_flake_rate",
        dest="min_flake_rate",
        type=float,
        default=5.0,
        help="Minimum failure/flake rate percentage threshold (default: 5.0)",
    )
    list_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Historical window in days (default: 7)",
    )
    list_parser.add_argument(
        "--mode",
        type=str,
        default="postsubmit",
        choices=["all", "presubmit", "postsubmit"],
        help="Run type filter: 'postsubmit' (default), 'presubmit', or 'all' (both pre and post)",
    )
    list_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token with androidbuild.internal scope",
    )
    list_parser.set_defaults(func=handle_flakiness_list)

    # Subcommand: fetch-logs
    fetch_parser = flakiness_subparsers.add_parser(
        "fetch-logs",
        help="Download logcat outputs, host logs, and diagnostic traces for a given invocation ID",
        description="""
Download Invocation Diagnostic Logs (`emu-dev-cli flakiness fetch-logs`)

Downloads logcat files, host stdout/stderr logs, thread dumps, and Perfetto traces for any Android Test Hub invocation ID (e.g. I75500010177130681) into a local sandbox directory.

Sample Invocations:
  # Download logcat file for an invocation ID:
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type LOGCAT

  # Download host stdout/stderr logs into a custom output directory:
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type HOST_LOG --out-dir scratch/my_logs/

  # Download all available diagnostic artifacts (logcat, host logs, thread dump, trace):
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type ALL
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fetch_parser.add_argument(
        "--invocation-id",
        "--invocation_id",
        dest="invocation_id",
        type=str,
        required=True,
        help="Invocation ID from Android Test Hub (e.g. I75500010177130681)",
    )
    fetch_parser.add_argument(
        "--artifact-type",
        "--artifact_type",
        dest="artifact_type",
        type=str,
        default="LOGCAT",
        choices=["LOGCAT", "HOST_LOG", "PERFETTO", "THREAD_DUMP", "ALL"],
        help="Type of diagnostic artifact to download (default: LOGCAT)",
    )
    fetch_parser.add_argument(
        "--out-dir",
        "--out_dir",
        dest="out_dir",
        type=str,
        default=None,
        help="Output directory (defaults to user temporary directory /tmp/flakiness_<user>/<invocation_id>/)",
    )
    fetch_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token with androidbuild.internal scope",
    )
    fetch_parser.set_defaults(func=handle_flakiness_fetch_logs)

    # Subcommand: triage
    triage_parser = flakiness_subparsers.add_parser(
        "triage",
        help="Query ATH, deduplicate open Buganizer issues, generate Jetski AI RCA patches, and file bugs",
        description="""
Automated Flakiness Triage and AI Remediation Generator (`emu-dev-cli flakiness triage`)

Performs end-to-end triage of flaky emulator test targets across CI pipelines:
  1. Queries Android Test Hub (ATH) for flaky tests matching failure rate threshold and target platform.
  2. Cross-checks open Buganizer issues under Component 1016880 to deduplicate existing reports.
  3. Downloads diagnostic logcats/host logs into a local investigation sandbox directory.
  4. Triggers Jetski / Antigravity AI root-cause analysis to construct proposed synchronization diffs.
  5. Generates markdown report artifacts locally or files Buganizer issues directly with `--auto-file`.

Sample Invocations:
  # Triage a specific test target in dry-run mode (outputs local markdown report & proposed diff):
  emu-dev-cli flakiness triage --test @goldfish//emulator/libs/process:process_unittests --dry-run

  # Triage all flaky tests on TSAN with failure rate >= 10% across 7 days:
  emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --days 7

  # Automatically file Buganizer issues for all new unhandled flaky tests:
  emu-dev-cli flakiness triage --target emulator_linux_x64 --min-flake-rate 5.0 --auto-file

  # Specify explicit OAuth2 token (for macOS workstations):
  emu-dev-cli flakiness triage --test @goldfish//emulator/libs/async:loop_handoff_test --token "$TOKEN"
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    triage_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64",
        choices=SUPPORTED_TARGET_CHOICES,
        help="Target platform matrix or 'all' (default: emulator_linux_x64)",
    )
    triage_parser.add_argument(
        "--config",
        type=str,
        default="devtools/emulator",
        help="Android Test Hub configuration name (default: devtools/emulator)",
    )
    triage_parser.add_argument(
        "--branch",
        type=str,
        default="git_emu-main-next",
        help="Target branch filter (default: git_emu-main-next)",
    )
    triage_parser.add_argument(
        "--component-id",
        "--component_id",
        dest="component_id",
        type=str,
        default="1016880",
        help="Buganizer Component ID for emulator flakiness (default: 1016880)",
    )
    triage_parser.add_argument(
        "--min-flake-rate",
        "--min_flake_rate",
        dest="min_flake_rate",
        type=float,
        default=10.0,
        help="Minimum failure/flake rate percentage threshold for bug filing (default: 10.0)",
    )
    triage_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Historical window in days (default: 7)",
    )
    triage_parser.add_argument(
        "--mode",
        type=str,
        default="postsubmit",
        choices=["all", "presubmit", "postsubmit"],
        help="Run type filter: 'postsubmit' (default), 'presubmit', or 'all' (both pre and post)",
    )
    triage_parser.add_argument(
        "--ai-model",
        "--model",
        dest="ai_model",
        type=str,
        default="Gemini Next",
        help="AI model selection for root-cause analysis (default: 'Gemini Next')",
    )
    triage_parser.add_argument(
        "--auto-file",
        "--auto_file",
        dest="auto_file",
        action="store_true",
        help="Automatically file Buganizer issues for unhandled flaky tests",
    )
    triage_parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="Optional specific Bazel test target to triage (e.g. @goldfish//emulator/libs/async:loop_handoff_test)",
    )
    triage_parser.add_argument(
        "--out-dir",
        "--out_dir",
        dest="out_dir",
        type=str,
        default=None,
        help="Directory to save generated markdown bug reports for local inspection (defaults to user temporary directory /tmp/flakiness_<user>/triage/)",
    )
    triage_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token with androidbuild.internal scope",
    )
    triage_parser.add_argument(
        "--dry-run",
        "--dry_run",
        dest="dry_run",
        action="store_true",
        help="Output proposed bug payloads without modifying Buganizer",
    )
    triage_parser.set_defaults(func=handle_flakiness_triage)

    # Subcommand: history
    history_parser = flakiness_subparsers.add_parser(
        "history",
        help="Fetch and display the execution run history and status of an individual test target",
        description="""
Inspect Test Target Historical Run Status (`emu-dev-cli flakiness history`)

Retrieves chronological execution records for a specific test target, showing pass/fail status, timestamps, build IDs, invocation IDs, target platforms, and direct clickable links.

Sample Invocations:
  # Query execution history for hal_plug_adapter_unittests on TSAN across 7 days:
  emu-dev-cli flakiness history --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --days 7

  # Query history across all target platforms combined:
  emu-dev-cli flakiness history --test @goldfish//emulator/libs/async:loop_handoff_test --target all --days 3 --limit 25
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    history_parser.add_argument(
        "--test",
        type=str,
        required=True,
        help="Bazel test target (e.g. @goldfish//emulator/plugin/grpc/services:screen_recording_impl_test)",
    )
    history_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64_tsan",
        choices=SUPPORTED_TARGET_CHOICES,
        help="Target platform matrix or 'all' (default: emulator_linux_x64_tsan)",
    )
    history_parser.add_argument(
        "--branch",
        type=str,
        default="git_emu-main-next",
        help="Target branch filter (default: git_emu-main-next)",
    )
    history_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Historical window in days (default: 7)",
    )
    history_parser.add_argument(
        "--mode",
        type=str,
        default="all",
        choices=["all", "presubmit", "postsubmit"],
        help="Run type filter: 'all' (both pre and post), 'presubmit', or 'postsubmit' (default: all)",
    )
    history_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum historical runs to display (default: 50)",
    )
    history_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token with androidbuild.internal scope",
    )
    history_parser.set_defaults(func=handle_flakiness_history)

    # Subcommand: reproduce
    reproduce_parser = flakiness_subparsers.add_parser(
        "reproduce",
        help="Locally reproduce a flaky test target via configured Bazel stress-test harness",
        description="""
Locally Reproduce Flaky Tests (`emu-dev-cli flakiness reproduce`)

Locally stress-tests a Bazel test target using Bazel flags (`--runs_per_test=N`, `--config=tsan`, etc.) matching the requested target platform.

Sample Invocations:
  # Locally stress-test a test target 20x on standard Linux x64:
  emu-dev-cli flakiness reproduce --test //emulator/videobridge:in_process_media_provider_test --target emulator_linux_x64 --iterations 20

  # Locally stress-test a TSAN test target 30x:
  emu-dev-cli flakiness reproduce --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_linux_x64_tsan --iterations 30
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reproduce_parser.add_argument(
        "--test",
        type=str,
        required=True,
        help="Bazel test target (e.g. //emulator/videobridge:in_process_media_provider_test)",
    )
    reproduce_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64",
        choices=SUPPORTED_TARGET_PLATFORMS,
        help="Target platform matrix (default: emulator_linux_x64)",
    )
    reproduce_parser.add_argument(
        "--iterations",
        type=int,
        default=20,
        help="Number of test execution iterations (default: 20)",
    )
    reproduce_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Execution timeout limit in seconds (default: dynamically calculated based on target platform and iteration count)",
    )
    reproduce_parser.set_defaults(func=handle_flakiness_reproduce)

    # Subcommand: fix
    fix_parser = flakiness_subparsers.add_parser(
        "fix",
        help="One-command remediation: fetch bug patch, stress-test 50x, and prepare Gerrit CL",
        description="""
One-Command Flaky Test Remediation (`emu-dev-cli flakiness fix`)

Remediates a flaky test in one step:
  1. Inspects Buganizer issue metadata for test target name and AI proposed patch diff.
  2. Verifies local workspace patch application.
  3. Executes 50x Bazel stress-test verification iterations on the target platform.
  4. Automatically prepares git commit and uploads Gerrit CL (`repo upload`) if `--upload` is specified.

Sample Invocations:
  # Simulate fix workflow for Bug 123456789 in dry-run mode:
  emu-dev-cli flakiness fix --bug 123456789 --dry-run

  # Execute 50x stress-test verification for Bug 123456789 on TSAN:
  emu-dev-cli flakiness fix --bug b/123456789 --target emulator_linux_x64_tsan --iterations 50

  # Verify fix and automatically upload Gerrit CL:
  emu-dev-cli flakiness fix --bug 123456789 --iterations 50 --upload
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fix_parser.add_argument(
        "--bug",
        type=str,
        required=True,
        help="Buganizer issue ID (e.g. 541234567 or b/541234567)",
    )
    fix_parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="Optional specific Bazel test target to verify (e.g. //emulator/videobridge:in_process_media_provider_test)",
    )
    fix_parser.add_argument(
        "--component-id",
        "--component_id",
        dest="component_id",
        type=str,
        default="1016880",
        help="Buganizer Component ID for emulator flakiness (default: 1016880)",
    )
    fix_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64",
        choices=SUPPORTED_TARGET_PLATFORMS,
        help="Target platform matrix (default: emulator_linux_x64)",
    )
    fix_parser.add_argument(
        "--iterations",
        type=int,
        default=50,
        help="Stress-test verification iterations (default: 50)",
    )
    fix_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Execution timeout limit in seconds (default: dynamically calculated based on target platform and iteration count)",
    )
    fix_parser.add_argument(
        "--upload",
        action="store_true",
        help="Automatically prepare git commit and upload Gerrit CL",
    )
    fix_parser.add_argument(
        "--dry-run",
        "--dry_run",
        dest="dry_run",
        action="store_true",
        help="Simulate fix and verification without modifying workspace files",
    )
    fix_parser.set_defaults(func=handle_flakiness_fix)

    # Subcommand: sponge
    sponge_parser = flakiness_subparsers.add_parser(
        "sponge",
        help="Inspect ResultStore / Sponge / Fusion2 invocation actions, errors, durations, and logs",
        description="""
Inspect Sponge & ResultStore Invocation Diagnostics (`emu-dev-cli flakiness sponge`)

Queries ResultStore via Stubby RPC to inspect build/test actions, failure exit codes, error messages,
durations, and diagnostic file URIs (test.log, test.xml) for any Sponge / Fusion2 invocation UUID or URL.

Sample Invocations:
  # Inspect invocation by UUID or full Fusion2 link:
  emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26

  # Inspect invocation and filter for a specific test target:
  emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26 --test @@goldfish+//emulator/launcher:can_boot_with_minigbm

  # Automatically inspect the latest 3 failed invocations for a flaky test on macOS:
  emu-dev-cli flakiness sponge --test @@goldfish+//emulator/launcher:can_boot_with_minigbm --target emulator_mac_aarch64 --latest-failures 3

  # Output machine-readable JSON for automated agent triage:
  emu-dev-cli --json flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sponge_parser.add_argument(
        "--invocation",
        type=str,
        default=None,
        help="Invocation UUID, full Fusion2 URL, or Sponge URL",
    )
    sponge_parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="Bazel test target filter (e.g. @@goldfish+//emulator/launcher:can_boot_with_minigbm)",
    )
    sponge_parser.add_argument(
        "--target",
        type=str,
        default="emulator_linux_x64",
        choices=SUPPORTED_TARGET_CHOICES,
        help="Target platform matrix (default: emulator_linux_x64)",
    )
    sponge_parser.add_argument(
        "--latest-failures",
        "--latest_failures",
        dest="latest_failures",
        type=int,
        default=0,
        help="Automatically inspect the latest N failed invocations for the specified test target",
    )
    sponge_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Historical window in days when querying latest failures (default: 7)",
    )
    sponge_parser.add_argument(
        "--mode",
        type=str,
        default="all",
        choices=["all", "presubmit", "postsubmit"],
        help="Run type filter when querying latest failures (default: all)",
    )
    sponge_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 token for ATH queries",
    )
    sponge_parser.set_defaults(func=handle_flakiness_sponge)


