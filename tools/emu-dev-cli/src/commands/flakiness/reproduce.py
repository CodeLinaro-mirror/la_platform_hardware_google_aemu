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

"""Command for 'emu-dev-cli flakiness reproduce'."""

import argparse
import logging
from typing import Any

from core.command import Command, CommandResult, STATUS_ERROR, STATUS_SUCCESS
from core.context import ExecutionContext
from core.errors import ExitCode, UsageError
from lib.ath_api import SUPPORTED_TARGET_PLATFORMS
from lib.output import format_markdown_table, print_status
from . import options
from .common import is_json_mode
from .stress import BazelLocator, find_bazel_binary, plan_stress_test, run_stress_test

logger = logging.getLogger(__name__)

ACTION = "flakiness reproduce"
DEFAULT_ITERATIONS = 20
PASSED_STATUS = "PASSED (No Flake Observed)"
FAILED_STATUS = "FAILED (Flake Reproduced)"

REPRODUCE_DESCRIPTION = """
Locally Reproduce Flaky Tests (`emu-dev-cli flakiness reproduce`)

Locally stress-tests a Bazel test target using Bazel flags (`--runs_per_test=N`, `--config=tsan`, etc.) matching the requested target platform.

Sample Invocations:
  # Locally stress-test a test target 20x on standard Linux x64:
  emu-dev-cli flakiness reproduce --test //emulator/videobridge:in_process_media_provider_test --target emulator_linux_x64 --iterations 20

  # Locally stress-test a TSAN test target 30x:
  emu-dev-cli flakiness reproduce --test @goldfish//emulator/libs/async:loop_handoff_test --target emulator_linux_x64_tsan --iterations 30
"""


def validate_iterations(args: argparse.Namespace, default: int) -> None:
    """Checks `--iterations` (shared with fix)."""
    iterations = int(getattr(args, "iterations", default))
    if iterations <= 0:
        raise UsageError(
            f"Iterations (--iterations) must be positive (got {iterations}).",
            remediation=f"Pass a positive integer to --iterations (e.g. --iterations {default}).",
        )


class FlakinessReproduceCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Stress-tests a Bazel test target locally to reproduce a flake."""

    name = "reproduce"
    help_text = "Locally reproduce a flaky test target via configured Bazel stress-test harness"
    description = REPRODUCE_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(self, *, bazel_locator: BazelLocator = find_bazel_binary) -> None:
        self._bazel_locator = bazel_locator

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--test",
            type=str,
            required=True,
            help="Bazel test target (e.g. //emulator/videobridge:in_process_media_provider_test)",
        )
        options.add_target_option(
            parser, choices=SUPPORTED_TARGET_PLATFORMS, help_text=options.TARGET_HELP
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=DEFAULT_ITERATIONS,
            help=f"Number of test execution iterations (default: {DEFAULT_ITERATIONS})",
        )
        options.add_timeout_option(parser)

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        test_label = str(getattr(args, "test", "") or "").strip()
        if not test_label:
            raise UsageError(
                "Bazel test target (--test) cannot be empty for 'flakiness reproduce'.",
                remediation="Pass a non-empty Bazel test target (e.g. --test //emulator/videobridge:in_process_media_provider_test).",
            )
        validate_iterations(args, DEFAULT_ITERATIONS)
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        test_name = str(args.test).strip()
        target = str(getattr(args, "target", options.DEFAULT_TARGET))
        iterations = int(getattr(args, "iterations", DEFAULT_ITERATIONS))
        logger.info(
            "Executing flakiness reproduce (test=%s, iterations=%s)", test_name, iterations
        )
        plan = plan_stress_test(
            self._bazel_locator(),
            test_target=test_name,
            target=target,
            iterations=iterations,
            explicit_timeout=getattr(args, "timeout", None),
        )
        print_status(
            f"🚀 Executing Bazel test reproduction (timeout: {plan.timeout_sec}s):\n  {plan.display}",
            json_mode=is_json_mode(args, ctx),
        )
        outcome = run_stress_test(ctx.process_runner, plan)
        status_str = PASSED_STATUS if outcome.passed else FAILED_STATUS
        table = format_markdown_table(
            ["Property", "Value"],
            [
                ["Test Target", f"`{test_name}`"],
                ["Target Platform", f"`{target}`"],
                ["Iterations Requested", f"`{iterations}`"],
                ["Reproduction Status", f"**{status_str}**"],
                ["Bazel Command", f"`{plan.display}`"],
            ],
        )
        summary = f"Reproduction run complete for '{test_name}' on '{target}' -> {status_str}"
        data: dict[str, Any] = {
            "summary": summary,
            "test": test_name,
            "target": target,
            "iterations": iterations,
            "status": status_str,
            "success": outcome.passed,
            "markdown_table": table,
            "log_snippet": outcome.log_snippet,
        }
        return CommandResult(
            data=data,
            action=ACTION,
            summary=summary,
            status=STATUS_SUCCESS if outcome.passed else STATUS_ERROR,
            exit_code=int(ExitCode.SUCCESS) if outcome.passed else int(ExitCode.FAILURE),
            markdown_table=table,
        )
