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

"""Bazel stress-test helpers shared by `flakiness reproduce` and `flakiness fix`."""

from collections.abc import Callable
from dataclasses import dataclass

from core.process import ProcessRunner
from lib.ath_api import calculate_stress_test_timeout, get_bazel_flags_for_target
from lib.workspace import WorkspacePathResolver

BazelLocator = Callable[[], str]

BAZEL_WORKSPACE = "emu-main-next"
BAZEL_FALLBACK_COMMAND = "bazel"
LOG_SNIPPET_CHARS = 1500


def find_bazel_binary() -> str:
    """Returns the Bazel binary for the emu-main-next workspace, or plain `bazel`."""
    found = WorkspacePathResolver(BAZEL_WORKSPACE).find_tool_binary("bazel")
    return str(found) if found else BAZEL_FALLBACK_COMMAND


@dataclass(frozen=True)
class StressPlan:
    """The Bazel command line and timeout for one stress-test run."""

    command: tuple[str, ...]
    timeout_sec: int

    @property
    def display(self) -> str:
        """Returns the command as one printable line."""
        return " ".join(self.command)


@dataclass(frozen=True)
class StressOutcome:
    """The result of one stress-test run."""

    passed: bool
    timed_out: bool
    log_snippet: str


def plan_stress_test(
    bazel_binary: str,
    *,
    test_target: str,
    target: str,
    iterations: int,
    explicit_timeout: int | None,
) -> StressPlan:
    """Builds the `bazel test` command and timeout for a target platform."""
    _, flags = get_bazel_flags_for_target(
        test_name=test_target, target=target, iterations=iterations
    )
    timeout_sec = calculate_stress_test_timeout(
        target=target, iterations=iterations, explicit_timeout=explicit_timeout
    )
    return StressPlan(command=(bazel_binary, "test", *flags), timeout_sec=timeout_sec)


def run_stress_test(runner: ProcessRunner, plan: StressPlan) -> StressOutcome:
    """Runs `plan` and keeps the tail of its output for the report."""
    result = runner.run(
        list(plan.command), check=False, timeout_sec=float(plan.timeout_sec)
    )
    # Stderr first, so Bazel's stdout test summary ends up in the kept tail.
    output = "\n".join(
        part for part in (result.stderr.rstrip(), result.stdout.rstrip()) if part
    )
    if result.timed_out:
        output = (
            f"{output}\nBazel stress test timed out after {plan.timeout_sec}s: "
            f"{plan.display}"
        ).lstrip()
    return StressOutcome(
        passed=result.ok,
        timed_out=result.timed_out,
        log_snippet=output[-LOG_SNIPPET_CHARS:],
    )
