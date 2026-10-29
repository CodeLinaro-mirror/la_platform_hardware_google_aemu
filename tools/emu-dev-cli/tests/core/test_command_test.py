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

"""Hermetic unit tests for core.command (Command, CommandResult, LegacyCommandAdapter)."""

import argparse
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.command import Command, CommandResult, LegacyCommandAdapter
from core.context import ExecutionContext
from core.errors import ExitCode, UsageError


class SampleCheckCommand(Command[argparse.Namespace, dict[str, str]]):
    """Concrete Command implementation for unit testing."""

    name = "sample-check"
    aliases = ("sc",)
    help_text = "Runs a sample check command"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--target", required=True)

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        if not args.target.strip():
            raise UsageError(
                "Target flag '--target' cannot be blank.",
                remediation="Pass a non-empty target name such as --target emulator_linux_x64.",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, str]]:
        proc = ctx.process_runner.run(["git", "rev-parse", "HEAD"], check=True)
        return CommandResult(
            data={"target": args.target, "commit": proc.stdout.strip()},
            action="sample-check",
            summary=f"Checked {args.target}",
            markdown_table="| Target | Commit |\n| --- | --- |",
            metadata={"host": ctx.config.host_platform, "_internal": "hidden"},
        )


class CommandTest(unittest.TestCase):
    """Tests Command registration, CommandResult payload serialization, and LegacyCommandAdapter."""

    def test_command_registration_and_execution_with_fake_context(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        cmd = SampleCheckCommand()
        cmd.register(subparsers)

        args = parser.parse_args(["sample-check", "--target", "目标_🚀_é"])
        ctx = ExecutionContext.for_testing()
        ctx.fake_process_runner.queue_response(returncode=0, stdout="deadbeef1234\n")

        cmd.validate(args, ctx)
        result = cmd.execute(args, ctx)
        self.assertTrue(result.ok)
        payload = result.to_payload()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "sample-check")
        self.assertEqual(payload["target"], "目标_🚀_é")
        self.assertEqual(payload["commit"], "deadbeef1234")
        self.assertEqual(payload["meta"], {"host": "linux-x64"})
        self.assertNotIn("_internal", payload["meta"])

    def test_command_without_name_raises_usage_error(self) -> None:
        class UnnamedCommand(Command[argparse.Namespace, None]):
            def configure_parser(self, parser: argparse.ArgumentParser) -> None:
                pass

            def execute(
                self, args: argparse.Namespace, ctx: ExecutionContext
            ) -> CommandResult[None]:
                del args, ctx
                return CommandResult(data=None)

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        with self.assertRaises(UsageError):
            UnnamedCommand().register(subparsers)

    def test_legacy_command_adapter_handles_sys_exit_and_dict_returns(self) -> None:
        ctx = ExecutionContext.for_testing()

        def _legacy_exiting_handler(args: argparse.Namespace) -> None:
            if getattr(args, "fail_loop", False):
                sys.exit(int(ExitCode.RETRY_OR_LOOP))
            sys.exit(0)

        adapter = LegacyCommandAdapter("legacy-workflow", _legacy_exiting_handler)
        ok_res = adapter.execute(argparse.Namespace(fail_loop=False), ctx)
        self.assertTrue(ok_res.ok)
        self.assertTrue(ok_res.already_rendered)
        self.assertEqual(ok_res.exit_code, 0)

        loop_res = adapter.execute(argparse.Namespace(fail_loop=True), ctx)
        self.assertFalse(loop_res.ok)
        self.assertTrue(loop_res.already_rendered)
        self.assertEqual(loop_res.exit_code, int(ExitCode.RETRY_OR_LOOP))

        dict_adapter = LegacyCommandAdapter(
            "legacy-dict",
            lambda _args: {"summary": "Legacy dict ok", "items": ["a", "b"]},
        )
        dict_res = dict_adapter.execute(argparse.Namespace(), ctx)
        self.assertTrue(dict_res.ok)
        self.assertFalse(dict_res.already_rendered)
        self.assertEqual(dict_res.to_payload()["items"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
