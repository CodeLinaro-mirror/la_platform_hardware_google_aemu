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

"""Hermetic unit tests for core.dispatcher (CliDispatcher and OutputRenderer)."""

import argparse
from io import StringIO
import json
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import AuthError, ExitCode, RetryableError, UsageError
from core.events import StepCompleted, StepStarted


class MutatingTestCommand(Command[argparse.Namespace, dict[str, str]]):
    """Command used to verify execution, rollback on failure, and event emission."""

    name = "mutate-repo"

    def __init__(self) -> None:
        self.rolled_back = False

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--mode", default="ok")

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        if args.mode == "bad-arg":
            raise UsageError(
                "Invalid --mode 'bad-arg'.",
                remediation="Use --mode ok, --mode auth-error, or --mode retry.",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, str]]:
        del ctx
        if args.mode == "auth-error":
            raise AuthError(
                "SSO ticket expired for googleplex-android.",
                remediation="Run 'gcert' and retry the command.",
            )
        if args.mode == "retry":
            raise RetryableError(
                "New Gerrit comments arrived on patch set 2.",
                remediation="Re-run step 0 to address new comments.",
            )
        if args.mode == "crash":
            raise RuntimeError("Unexpected low-level failure in command")
        return CommandResult(
            data={"branch": "studio-main", "note": "成功_🚀_é"},
            action="mutate-repo",
            summary="Repository mutation succeeded",
        )

    def rollback(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del args, ctx
        self.rolled_back = True


class DispatcherTest(unittest.TestCase):
    """Tests CliDispatcher across JSON/Markdown modes, error codes, rollbacks, and legacy handlers."""

    def test_dispatch_success_in_json_and_human_modes(self) -> None:
        stdout_buf = StringIO()
        stderr_buf = StringIO()
        ctx = ExecutionContext.for_testing()
        dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=stdout_buf, stderr=stderr_buf),
        )

        parser = argparse.ArgumentParser()
        parser.add_argument("--json", action="store_true")
        subparsers = parser.add_subparsers(dest="subcommand")
        cmd = MutatingTestCommand()
        cmd.register(subparsers)

        json_args = parser.parse_args(["--json", "mutate-repo", "--mode", "ok"])
        exit_code = dispatcher.dispatch(json_args)
        self.assertEqual(exit_code, int(ExitCode.SUCCESS))
        self.assertFalse(cmd.rolled_back)

        parsed = json.loads(stdout_buf.getvalue())
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["action"], "mutate-repo")
        self.assertEqual(parsed["note"], "成功_🚀_é")
        self.assertEqual(stderr_buf.getvalue(), "")

        # Verify human-readable mode
        human_stdout = StringIO()
        human_dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=human_stdout, stderr=stderr_buf),
        )
        human_args = parser.parse_args(["mutate-repo", "--mode", "ok"])
        self.assertEqual(human_dispatcher.dispatch(human_args), 0)
        self.assertIn("SUCCESS: Repository mutation succeeded", human_stdout.getvalue())
        self.assertIn("成功_🚀_é", human_stdout.getvalue())

        started = ctx.event_bus.recorded_events(StepStarted)
        completed = ctx.event_bus.recorded_events(StepCompleted)
        self.assertEqual(len(started), 2)
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0].exit_code, 0)

    def test_dispatch_maps_errors_to_canonical_exit_codes_and_triggers_rollback(self) -> None:
        ctx = ExecutionContext.for_testing()
        parser = argparse.ArgumentParser()
        parser.add_argument("--json", action="store_true")
        subparsers = parser.add_subparsers(dest="subcommand")
        cmd = MutatingTestCommand()
        cmd.register(subparsers)

        # 1. UsageError -> exit code 4
        stderr_usage = StringIO()
        dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=StringIO(), stderr=stderr_usage),
        )
        usage_args = parser.parse_args(["--json", "mutate-repo", "--mode", "bad-arg"])
        code = dispatcher.dispatch(usage_args)
        self.assertEqual(code, int(ExitCode.USAGE_ERROR))
        self.assertTrue(cmd.rolled_back)
        err_json = json.loads(stderr_usage.getvalue())
        self.assertEqual(err_json["error_code"], "USAGE_ERROR")
        self.assertEqual(err_json["exit_code"], 4)

        # 2. AuthError -> exit code 3
        cmd.rolled_back = False
        stderr_auth = StringIO()
        auth_dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=StringIO(), stderr=stderr_auth),
        )
        auth_args = parser.parse_args(["--json", "mutate-repo", "--mode", "auth-error"])
        self.assertEqual(auth_dispatcher.dispatch(auth_args), int(ExitCode.AUTH_ERROR))
        self.assertTrue(cmd.rolled_back)

        # 3. RetryableError -> exit code 2
        stderr_retry = StringIO()
        retry_dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=StringIO(), stderr=stderr_retry),
        )
        retry_args = parser.parse_args(["mutate-repo", "--mode", "retry"])
        self.assertEqual(
            retry_dispatcher.dispatch(retry_args), int(ExitCode.RETRY_OR_LOOP)
        )
        self.assertIn("ERROR: New Gerrit comments arrived", stderr_retry.getvalue())

        # 4. Unexpected RuntimeError -> exit code 1 + rollback + JSON error envelope
        cmd.rolled_back = False
        stderr_crash = StringIO()
        crash_dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=StringIO(), stderr=stderr_crash),
        )
        crash_args = parser.parse_args(["--json", "mutate-repo", "--mode", "crash"])
        self.assertEqual(
            crash_dispatcher.dispatch(crash_args), int(ExitCode.FAILURE)
        )
        self.assertTrue(cmd.rolled_back)
        crash_json = json.loads(stderr_crash.getvalue())
        self.assertEqual(crash_json["status"], "error")
        self.assertEqual(crash_json["error_type"], "RuntimeError")
        self.assertIn("Unexpected low-level failure", crash_json["error"])
        self.assertEqual(crash_json["exit_code"], 1)

    def test_dispatch_supports_legacy_func_handlers_seamlessly(self) -> None:
        ctx = ExecutionContext.for_testing()
        dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=StringIO(), stderr=StringIO()),
        )
        invoked_with: list[str] = []

        def _legacy_handler(args: argparse.Namespace) -> None:
            invoked_with.append(args.target)
            sys.exit(0)

        legacy_args = argparse.Namespace(
            subcommand="flakiness",
            target="studio-linux",
            json=False,
            func=_legacy_handler,
        )
        code = dispatcher.dispatch(legacy_args)
        self.assertEqual(code, 0)
        self.assertEqual(invoked_with, ["studio-linux"])


if __name__ == "__main__":
    unittest.main()
