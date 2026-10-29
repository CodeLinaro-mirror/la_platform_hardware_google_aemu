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

"""Hermetic zero-patch DI unit tests for `commands.harness` Command classes."""

import argparse
from pathlib import Path
import sys
import tempfile
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.harness import (
    HarnessClearCommand,
    HarnessCommand,
    HarnessHookCommand,
    HarnessInstallCommand,
    HarnessMcpServerCommand,
    HarnessPresetsCommand,
    HarnessReviewSwarmCommand,
    HarnessStatusCommand,
    HarnessUninstallCommand,
    register_parser,
)
from core.command import CommandValidationError
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer


class HarnessCommandsTest(unittest.TestCase):
    """Tests Command-pattern harness subcommands with ExecutionContext and CliDispatcher."""

    def test_harness_install_and_uninstall_in_unicode_temp_dir(self) -> None:
        ctx = ExecutionContext.for_testing()
        install_cmd = HarnessInstallCommand()
        uninstall_cmd = HarnessUninstallCommand()

        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir) / "plugin_测试_🚀_é"
            install_args = argparse.Namespace(
                scope="workspace",
                target_dir=str(target_dir),
                json=True,
            )
            validated = install_cmd.validate(install_args, ctx)
            install_res = install_cmd.execute(validated, ctx)
            self.assertTrue(install_res.ok)
            self.assertEqual(install_res.exit_code, 0)
            self.assertTrue((target_dir / "plugin.json").is_file())

            uninstall_args = argparse.Namespace(
                target_dir=str(target_dir),
                json=True,
            )
            uninstall_res = uninstall_cmd.execute(uninstall_args, ctx)
            self.assertTrue(uninstall_res.ok)
            self.assertEqual(uninstall_res.exit_code, 0)
            self.assertFalse(target_dir.exists())

    def test_harness_presets_and_validation_guards(self) -> None:
        ctx = ExecutionContext.for_testing()
        presets_cmd = HarnessPresetsCommand()
        presets_res = presets_cmd.execute(
            argparse.Namespace(name="parallel-code-review", json=True),
            ctx,
        )
        self.assertTrue(presets_res.ok)
        self.assertEqual(presets_res.exit_code, 0)

        with self.assertRaises(CommandValidationError):
            HarnessInstallCommand().validate(
                argparse.Namespace(scope="invalid-scope"),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            HarnessHookCommand().validate(
                argparse.Namespace(event="invalid-event"),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            HarnessReviewSwarmCommand().validate(
                argparse.Namespace(mode="invalid-mode"),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            HarnessCommand().validate(
                argparse.Namespace(harness_action=None),
                ctx,
            )

    def test_harness_parser_binds_command_instances_for_dispatcher(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        dispatcher = CliDispatcher()
        for sub_argv, expected_cls in (
            (["harness", "install", "--workspace"], HarnessInstallCommand),
            (["harness", "uninstall"], HarnessUninstallCommand),
            (["harness", "status"], HarnessStatusCommand),
            (["harness", "hook", "--event", "pre-tool"], HarnessHookCommand),
            (["harness", "clear"], HarnessClearCommand),
            (["harness", "mcp-server"], HarnessMcpServerCommand),
            (["harness", "presets"], HarnessPresetsCommand),
            (["harness", "review-swarm", "--commit", "HEAD"], HarnessReviewSwarmCommand),
        ):
            parsed = parser.parse_args(sub_argv)
            resolved = dispatcher.resolve_command(parsed)
            self.assertIsInstance(resolved, expected_cls)

    def test_harness_hook_and_mcp_server_set_already_rendered(self) -> None:
        import io
        import json
        from unittest.mock import patch

        ctx = ExecutionContext.for_testing()
        fake_stdin = io.StringIO('{"conversationId": "conv_测试_🚀_é"}')
        fake_stdout = io.StringIO()
        fake_stderr = io.StringIO()
        dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=fake_stdout, stderr=fake_stderr),
        )

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)
        parsed_hook = parser.parse_args(["harness", "hook", "--event", "pre-tool"])

        with patch("sys.stdin", fake_stdin), patch("sys.stdout", fake_stdout):
            hook_res = HarnessHookCommand().execute(parsed_hook, ctx)
            self.assertTrue(hook_res.already_rendered)
            fake_stdin.seek(0)
            composite_res = HarnessCommand().execute(parsed_hook, ctx)
            self.assertTrue(composite_res.already_rendered)
            fake_stdout.seek(0)
            fake_stdout.truncate(0)
            fake_stdin.seek(0)
            exit_code = dispatcher.dispatch(parsed_hook, ctx=ctx)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(fake_stdout.getvalue()), {"decision": "allow"})

        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", io.StringIO()):
            mcp_res = HarnessMcpServerCommand().execute(argparse.Namespace(), ctx)
            self.assertTrue(mcp_res.already_rendered)


if __name__ == "__main__":
    unittest.main()
