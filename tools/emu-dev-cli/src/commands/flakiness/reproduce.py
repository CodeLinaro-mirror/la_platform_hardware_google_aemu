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

"""Subcommand handler for 'emu-dev-cli flakiness reproduce'."""

import argparse
import logging
import subprocess
import sys
from typing import Any

from core.command import (
    Command,
    CommandResult,
    STATUS_ERROR,
    STATUS_SUCCESS,
)
from core.context import ExecutionContext, SERVICE_PROCESS_RUNNER
from core.errors import ExitCode, UsageError
from lib.ath_api import (
    SUPPORTED_TARGET_PLATFORMS,
    calculate_stress_test_timeout,
    get_bazel_flags_for_target,
)
from lib.output import format_markdown_table, print_result
from lib.workspace import WorkspacePathResolver

logger = logging.getLogger(__name__)


def handle_flakiness_reproduce(args):
    """Handles 'emu-dev-cli flakiness reproduce' subcommand.

    Executes local Bazel stress testing with target sanitizer and iterations.
    """
    logger.info("Executing flakiness reproduce command (test=%s, iterations=%s)", args.test, args.iterations)
    json_mode = getattr(args, "json", False)

    # Resolve Bazel binary path dynamically using WorkspacePathResolver
    resolver = WorkspacePathResolver("emu-main-next")
    bazel_bin = resolver.find_tool_binary("bazel") or "bazel"

    _, flags = get_bazel_flags_for_target(
        test_name=args.test, target=args.target, iterations=args.iterations
    )
    cmd = [str(bazel_bin), "test"] + flags

    timeout_seconds = calculate_stress_test_timeout(
        target=args.target,
        iterations=args.iterations,
        explicit_timeout=getattr(args, "timeout", None),
    )

    sys.stderr.write(
        f"🚀 Executing Bazel test reproduction (timeout: {timeout_seconds}s):\n  {' '.join(cmd)}\n"
    )
    sys.stderr.flush()

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )

        success = proc.returncode == 0
        raw_output = proc.stdout
    except Exception as e:
        success = False
        raw_output = str(e)

    status_str = (
        "PASSED (No Flake Observed)" if success else "FAILED (Flake Reproduced)"
    )

    headers = ["Property", "Value"]
    rows = [
        ["Test Target", f"`{args.test}`"],
        ["Target Platform", f"`{args.target}`"],
        ["Iterations Requested", f"`{args.iterations}`"],
        ["Reproduction Status", f"**{status_str}**"],
        ["Bazel Command", f"`{' '.join(cmd)}`"],
    ]

    data = {
        "summary": f"Reproduction run complete for '{args.test}' on '{args.target}' -> {status_str}",
        "test": args.test,
        "target": args.target,
        "iterations": args.iterations,
        "status": status_str,
        "success": success,
        "markdown_table": format_markdown_table(headers, rows),
        "log_snippet": raw_output[-1500:] if raw_output else "",
    }
    print_result(data, json_mode=json_mode, is_error=not success)


class FlakinessReproduceCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli flakiness reproduce`."""

    name = "reproduce"
    help_text = "Locally reproduce a flaky test target via configured Bazel stress-test harness"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--test",
            type=str,
            required=True,
            help="Bazel test target",
        )
        parser.add_argument(
            "--target",
            type=str,
            default="emulator_linux_x64",
            choices=SUPPORTED_TARGET_PLATFORMS,
            help="Target platform matrix (default: emulator_linux_x64)",
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=20,
            help="Number of test execution iterations (default: 20)",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=None,
            help="Execution timeout limit in seconds",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        test_label = str(getattr(args, "test", "") or "").strip()
        if not test_label:
            raise UsageError(
                "Bazel test target (--test) cannot be empty for 'flakiness reproduce'.",
                remediation="Pass a non-empty Bazel test target (e.g. --test //emulator/videobridge:in_process_media_provider_test).",
            )
        iterations = int(getattr(args, "iterations", 20))
        if iterations <= 0:
            raise UsageError(
                f"Iterations (--iterations) must be positive (got {iterations}).",
                remediation="Pass a positive integer to --iterations (e.g. --iterations 20).",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        test_name = str(args.test).strip()
        target = str(getattr(args, "target", "emulator_linux_x64"))
        iterations = int(getattr(args, "iterations", 20))
        _, flags = get_bazel_flags_for_target(
            test_name=test_name, target=target, iterations=iterations
        )
        cmd = ["bazel", "test"] + flags
        timeout_seconds = calculate_stress_test_timeout(
            target=target,
            iterations=iterations,
            explicit_timeout=getattr(args, "timeout", None),
        )
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)
        res = runner.run(cmd, check=False, timeout_sec=float(timeout_seconds))
        success = res.returncode == 0
        raw_output = res.stdout or res.stderr or ""
        status_str = (
            "PASSED (No Flake Observed)"
            if success
            else "FAILED (Flake Reproduced)"
        )

        headers = ["Property", "Value"]
        rows = [
            ["Test Target", f"`{test_name}`"],
            ["Target Platform", f"`{target}`"],
            ["Iterations Requested", f"`{iterations}`"],
            ["Reproduction Status", f"**{status_str}**"],
            ["Bazel Command", f"`{' '.join(cmd)}`"],
        ]
        table = format_markdown_table(headers, rows)
        summary = f"Reproduction run complete for '{test_name}' on '{target}' -> {status_str}"
        data: dict[str, Any] = {
            "summary": summary,
            "test": test_name,
            "target": target,
            "iterations": iterations,
            "status": status_str,
            "success": success,
            "markdown_table": table,
            "log_snippet": raw_output[-1500:] if raw_output else "",
        }
        return CommandResult(
            data=data,
            action="flakiness reproduce",
            summary=summary,
            status=STATUS_SUCCESS if success else STATUS_ERROR,
            exit_code=(
                int(ExitCode.SUCCESS) if success else int(ExitCode.FAILURE)
            ),
            markdown_table=table,
        )
