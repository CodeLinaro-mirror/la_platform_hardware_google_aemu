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

"""Hermetic unit tests for core.command (Command, CommandGroup, CommandResult, LegacyCommandAdapter)."""

import argparse
import contextlib
import io
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.command import (
    COMMAND_INSTANCE_ATTR,
    Command,
    CommandGroup,
    CommandResult,
    LegacyCommandAdapter,
    bind_command,
)
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher
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

    def test_register_passes_description_and_formatter(self) -> None:
        class DescribedCommand(SampleCheckCommand):
            description = "Sample Invocations:\n  emu-dev-cli sample-check --target 目标"
            formatter_class = argparse.RawDescriptionHelpFormatter

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        sub_parser = DescribedCommand().register(subparsers)

        self.assertEqual(sub_parser.description, DescribedCommand.description)
        self.assertIs(sub_parser.formatter_class, argparse.RawDescriptionHelpFormatter)
        self.assertIn("  emu-dev-cli sample-check --target 目标", sub_parser.format_help())

    def test_bind_command_sets_only_the_command_instance(self) -> None:
        parser = argparse.ArgumentParser()
        cmd = SampleCheckCommand()
        bind_command(parser, cmd)

        args = parser.parse_args(["--target", "emulator_linux_x64"])
        self.assertIs(getattr(args, COMMAND_INSTANCE_ATTR), cmd)
        self.assertFalse(hasattr(args, "func"))


class _SubAlphaCommand(Command[argparse.Namespace, dict[str, str]]):
    name = "alpha"
    aliases = ("a",)
    help_text = "Alpha subcommand"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--label", default="")

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        if args.label == "invalid":
            raise UsageError(
                "Label 'invalid' is not allowed for 'alpha'.",
                remediation="Pass another --label value.",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, str]]:
        del ctx
        return CommandResult.success({"label": args.label}, summary="alpha ran")


class _SubBetaCommand(Command[argparse.Namespace, dict[str, str]]):
    name = "beta"
    help_text = "Beta subcommand"

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, str]]:
        del args, ctx
        return CommandResult.success({"ran": "beta"}, summary="beta ran")


class _SampleGroup(CommandGroup):
    name = "sample-group"
    help_text = "Sample command group"
    subcommand_dest = "sample_cmd"
    subcommand_help = "Available sample subcommands"

    def __init__(self) -> None:
        super().__init__([_SubAlphaCommand(), _SubBetaCommand()])


class CommandGroupTest(unittest.TestCase):
    """Tests CommandGroup registration, dispatch, and the no-subcommand help path."""

    def setUp(self) -> None:
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        self.group = _SampleGroup()
        bind_command(subparsers, self.group)

    def test_leaf_parsers_bind_their_own_command_and_aliases(self) -> None:
        args = self.parser.parse_args(["sample-group", "a", "--label", "标签_🚀"])
        leaf = getattr(args, COMMAND_INSTANCE_ATTR)
        self.assertIsInstance(leaf, _SubAlphaCommand)
        self.assertIs(leaf, self.group.subcommands[0])

        ctx = ExecutionContext.for_testing()
        result = self.group.execute(args, ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"label": "标签_🚀"})

    def test_group_validate_delegates_to_the_subcommand(self) -> None:
        args = self.parser.parse_args(["sample-group", "alpha", "--label", "invalid"])
        with self.assertRaises(UsageError):
            self.group.validate(args, ExecutionContext.for_testing())

    def test_bare_group_prints_help_and_succeeds(self) -> None:
        args = self.parser.parse_args(["sample-group"])
        self.assertIs(getattr(args, COMMAND_INSTANCE_ATTR), self.group)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = CliDispatcher(context=ExecutionContext.for_testing()).dispatch(args)

        self.assertEqual(exit_code, int(ExitCode.SUCCESS))
        self.assertIn("Available sample subcommands", stdout.getvalue())
        self.assertIn("alpha", stdout.getvalue())

    def test_bare_group_in_json_mode_reports_usage_error(self) -> None:
        args = self.parser.parse_args(["--json", "sample-group"])
        ctx = ExecutionContext.for_testing(json_mode=True)
        with self.assertRaises(UsageError) as raised:
            self.group.execute(args, ctx)
        self.assertIn("alpha, beta", raised.exception.remediation)

    def test_unknown_subcommand_names_the_valid_choices(self) -> None:
        args = argparse.Namespace(sample_cmd="gamma")
        with self.assertRaises(UsageError) as raised:
            self.group.execute(args, ExecutionContext.for_testing())
        self.assertIn("'gamma'", str(raised.exception))
        self.assertIn("alpha, beta", raised.exception.remediation)


if __name__ == "__main__":
    unittest.main()
