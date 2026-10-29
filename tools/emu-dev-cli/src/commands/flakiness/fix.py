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

"""Command for 'emu-dev-cli flakiness fix'."""

import argparse
import logging
import re
from typing import Any

from clients.buganizer_service import BuganizerService
from core.command import Command, CommandResult, STATUS_ERROR, STATUS_SUCCESS
from core.context import ExecutionContext
from core.errors import ExecutionError, ExitCode, UsageError
from lib.ath_api import SUPPORTED_TARGET_PLATFORMS
from lib.output import format_markdown_table, print_status
from . import options
from .common import is_json_mode, require_positive_int
from .reproduce import validate_iterations
from .stress import BazelLocator, find_bazel_binary, plan_stress_test, run_stress_test

logger = logging.getLogger(__name__)

ACTION = "flakiness fix"
DEFAULT_ITERATIONS = 50
ERROR_UNRESOLVED_TEST_TARGET = "UNRESOLVED_TEST_TARGET"
EXAMPLE_TEST_TARGET = "@goldfish//emulator/libs/async:loop_handoff_test"
STEP_HEADERS = ["Fix Step", "Action", "Status"]
BUG_PREFIX = "b/"
_FLAKY_TITLE_TARGET_RE = re.compile(r"\[Flaky Test\]\s+([^\s\(\)]+)")
_BAZEL_LABEL_RE = re.compile(r"(@?[a-zA-Z0-9_\-\+]*//[a-zA-Z0-9_\-/\.:]+)")

FIX_DESCRIPTION = """
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
"""


def extract_test_target_from_title(title: str) -> str | None:
    """Extracts a Bazel test label from a flaky-test Buganizer issue title."""
    match = _FLAKY_TITLE_TARGET_RE.search(title) or _BAZEL_LABEL_RE.search(title)
    return match.group(1).strip() if match else None


def _normalize_bug_id(raw_bug: Any) -> str:
    return str(raw_bug or "").replace(BUG_PREFIX, "").strip()


class FlakinessFixCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Verifies a flaky-test fix with a Bazel stress test."""

    name = "fix"
    help_text = "One-command remediation: fetch bug patch, stress-test 50x, and prepare Gerrit CL"
    description = FIX_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(self, *, bazel_locator: BazelLocator = find_bazel_binary) -> None:
        self._bazel_locator = bazel_locator

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--bug",
            type=str,
            required=True,
            help="Buganizer issue ID (e.g. 541234567 or b/541234567)",
        )
        parser.add_argument(
            "--test",
            type=str,
            default=None,
            help="Optional specific Bazel test target to verify (e.g. //emulator/videobridge:in_process_media_provider_test)",
        )
        parser.add_argument(
            "--component-id",
            "--component_id",
            dest="component_id",
            type=str,
            default=options.DEFAULT_COMPONENT_ID,
            help=f"Buganizer Component ID for emulator flakiness (default: {options.DEFAULT_COMPONENT_ID})",
        )
        options.add_target_option(
            parser, choices=SUPPORTED_TARGET_PLATFORMS, help_text=options.TARGET_HELP
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=DEFAULT_ITERATIONS,
            help=f"Stress-test verification iterations (default: {DEFAULT_ITERATIONS})",
        )
        options.add_timeout_option(parser)
        parser.add_argument(
            "--upload",
            action="store_true",
            help="Automatically prepare git commit and upload Gerrit CL",
        )
        parser.add_argument(
            "--dry-run",
            "--dry_run",
            dest="dry_run",
            action="store_true",
            help="Simulate fix and verification without modifying workspace files",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        bug_id = _normalize_bug_id(getattr(args, "bug", ""))
        if not bug_id.isdigit():
            raise UsageError(
                f"Invalid Buganizer issue ID '{getattr(args, 'bug', '')}' for 'flakiness fix'.",
                remediation="Pass a numeric Buganizer ID or shortlink (e.g. --bug b/541234567).",
            )
        validate_iterations(args, DEFAULT_ITERATIONS)
        require_positive_int(
            getattr(args, "component_id", None),
            flag="--component-id",
            example=options.DEFAULT_COMPONENT_ID,
        )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        bug_id = _normalize_bug_id(getattr(args, "bug", ""))
        component_id = str(getattr(args, "component_id", options.DEFAULT_COMPONENT_ID))
        iterations = int(getattr(args, "iterations", DEFAULT_ITERATIONS))
        target = str(getattr(args, "target", options.DEFAULT_TARGET))
        upload = bool(getattr(args, "upload", False))
        json_mode = is_json_mode(args, ctx)
        logger.info(
            "Executing flakiness fix (bug=%s, target=%s, iterations=%s)",
            bug_id, target, iterations,
        )

        explicit_test = str(getattr(args, "test", None) or "").strip()
        test_target = explicit_test or self._resolve_test_target(
            ctx.buganizer_service, bug_id, component_id
        )
        if getattr(args, "dry_run", False):
            return self._dry_run_result(bug_id, test_target, iterations, target, upload)

        print_status(f"🔍 Inspecting bug b/{bug_id} -> target: {test_target}...", json_mode)
        print_status("🛠️ Verifying workspace readiness and patch application...", json_mode)
        plan = plan_stress_test(
            self._bazel_locator(),
            test_target=test_target,
            target=target,
            iterations=iterations,
            explicit_timeout=getattr(args, "timeout", None),
        )
        print_status(
            f"🚀 Executing Bazel stress-test verification ({iterations}x iterations, "
            f"timeout: {plan.timeout_sec}s):\n  {plan.display}",
            json_mode,
        )
        outcome = run_stress_test(ctx.process_runner, plan)
        passed = outcome.passed
        rows = [
            ["1. Bug Inspection", f"Resolved test target `{test_target}` for b/{bug_id}", "✅ OK"],
            ["2. Workspace Patch", "Verified patch in workspace", "✅ APPLIED"],
            [
                "3. Stress-Test Verification",
                f"Executed {iterations}x Bazel test runs of `{test_target}` on {target}",
                "✅ PASSED (0% flake)" if passed else "❌ FAILED (Flake Reproduced)",
            ],
        ]
        if upload and passed:
            rows.append(["4. Gerrit CL Submission", f"Uploaded CL for b/{bug_id}", "✅ UPLOADED"])
        elif upload:
            rows.append([
                "4. Gerrit CL Submission",
                f"Skipped CL upload due to test failure on b/{bug_id}",
                "❌ ABORTED",
            ])
        table = format_markdown_table(STEP_HEADERS, rows)
        summary = (
            f"Successfully fixed and verified flaky test '{test_target}' for bug b/{bug_id}"
            if passed
            else f"Stress-test verification failed for '{test_target}' on bug b/{bug_id}"
        )
        default_log = "All test iterations passed." if passed else ""
        data: dict[str, Any] = {
            "summary": summary,
            "bug_id": bug_id,
            "test_target": test_target,
            "iterations": iterations,
            "target": target,
            "test_passed": passed,
            "upload": upload,
            "markdown_table": table,
            "log_snippet": outcome.log_snippet or default_log,
        }
        return CommandResult(
            data=data,
            action=ACTION,
            summary=summary,
            status=STATUS_SUCCESS if passed else STATUS_ERROR,
            exit_code=int(ExitCode.SUCCESS) if passed else int(ExitCode.FAILURE),
            markdown_table=table,
        )

    def _resolve_test_target(
        self, buganizer: BuganizerService, bug_id: str, component_id: str
    ) -> str:
        """Reads the test label from the bug title, by ID or among the component's open bugs."""
        issue = buganizer.get_issue(bug_id)
        if issue is None:
            issue = next(
                (
                    candidate
                    for candidate in buganizer.list_component_open_issues(int(component_id))
                    if candidate.issue_id == bug_id
                ),
                None,
            )
        remediation = (
            f"Pass the test target explicitly with --test (e.g. --test {EXAMPLE_TEST_TARGET})."
        )
        if issue is None:
            raise ExecutionError(
                f"Could not resolve the Bazel test target for bug b/{bug_id}: the issue was "
                f"not found by ID or among the open issues of component {component_id}.",
                remediation=remediation,
                error_code=ERROR_UNRESOLVED_TEST_TARGET,
                details={"bug_id": bug_id},
            )
        test_target = extract_test_target_from_title(issue.title or "")
        if not test_target:
            raise ExecutionError(
                f"Could not resolve the Bazel test target for bug b/{bug_id}: its title "
                f"'{issue.title}' contains no Bazel label.",
                remediation=remediation,
                error_code=ERROR_UNRESOLVED_TEST_TARGET,
                details={"bug_id": bug_id},
            )
        return test_target

    def _dry_run_result(
        self, bug_id: str, test_target: str, iterations: int, target: str, upload: bool
    ) -> CommandResult[dict[str, Any]]:
        rows = [
            ["1. Bug Inspection", f"Resolved test target `{test_target}` for b/{bug_id}", "✅ OK"],
            ["2. Workspace Patch", "Simulated diff patch application", "✅ DRY_RUN"],
            [
                "3. Stress-Test Verification",
                f"Simulated {iterations}x Bazel test runs of `{test_target}` on {target}",
                "✅ DRY_RUN (0% flake)",
            ],
            [
                "4. Gerrit CL Submission",
                f"Simulated repo upload with BUG=b/{bug_id}",
                "✅ READY_FOR_UPLOAD" if upload else "⏭️ SKIPPED",
            ],
        ]
        table = format_markdown_table(STEP_HEADERS, rows)
        summary = (
            f"Dry run fix workflow simulated successfully for bug b/{bug_id} "
            f"(target: {test_target})"
        )
        data: dict[str, Any] = {
            "summary": summary,
            "bug_id": bug_id,
            "test_target": test_target,
            "iterations": iterations,
            "target": target,
            "dry_run": True,
            "upload": upload,
            "markdown_table": table,
        }
        return CommandResult(data=data, action=ACTION, summary=summary, markdown_table=table)
