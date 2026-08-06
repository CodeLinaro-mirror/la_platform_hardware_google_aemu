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

"""Unit tests for emu-dev-cli tidy fix."""

import argparse
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from commands.tidy.fix_cmd import handle_tidy_fix
from lib.target_resolver import resolve_dependent_targets

from lib.tidy_engine import (
    _apply_seed_declaration_rename,
    _build_refactor_prompt,
    _extract_rename_symbols,
    _find_yaml_file,
    _run_clang_format,
)
from lib.tidy_types import TidyDiagnostic, TidyReplacement


class TidyFixTest(unittest.TestCase):

    def test_resolve_dependent_targets_scoped_closure(self):
        mock_runner = MagicMock()
        query_res = MagicMock()
        query_res.returncode = 0
        query_res.stdout = "@goldfish//emulator/libs/sockets:sockets\n@goldfish//emulator/launcher:launcher\n"
        mock_runner.cquery.return_value = query_res

        # Level 1/2: returns package closure
        t1 = resolve_dependent_targets(
            mock_runner, "@goldfish//emulator/libs/sockets:tidy", level=2
        )
        self.assertEqual(t1, ["@goldfish//emulator/libs/sockets:all"])

        # Level 3/4: returns package closure + direct reverse dependencies (rdeps)
        t3 = resolve_dependent_targets(
            mock_runner, "@goldfish//emulator/libs/sockets:tidy", level=3
        )
        self.assertIn("@goldfish//emulator/libs/sockets:all", t3)
        self.assertIn("@goldfish//emulator/launcher:launcher", t3)
        self.assertNotIn("@goldfish//...", t3)

    def test_apply_seed_declaration_rename_scoped_to_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "source.cc"
            source_file.write_text(
                "int data = 1;\nint count = 2;\nvoid process(int data, int len) {\n    int data_copy = data;\n}\n",
                encoding="utf-8",
            )

            # Diagnostic points to line 3 (the parameter 'data')
            diag = TidyDiagnostic(
                name="readability-identifier-naming",
                file_path=str(source_file),
                line=3,
                column=18,
                message="invalid case style for parameter 'data'",
                level=2,
            )
            diag.replacements = [
                TidyReplacement(
                    file_path=str(source_file),
                    offset=46,
                    length=4,
                    replacement_text="raw_data",
                    line=3,
                    column=18,
                )
            ]

            success = _apply_seed_declaration_rename(
                source_file, diag, "data", "raw_data"
            )
            self.assertTrue(success)

            result_lines = source_file.read_text(encoding="utf-8").splitlines()
            # Line 1: global data must NOT be touched
            self.assertEqual(result_lines[0], "int data = 1;")
            # Line 2: count must NOT be touched
            self.assertEqual(result_lines[1], "int count = 2;")
            # Line 3: parameter data IS renamed
            self.assertEqual(result_lines[2], "void process(int raw_data, int len) {")
            # Line 4: body data is NOT touched by seed rename (left for compiler oracle + agent)
            self.assertEqual(result_lines[3], "    int data_copy = data;")

    @patch("commands.tidy.fix_cmd.BazelRunner")
    @patch("lib.tidy_runner.parse_fixes_yaml")
    def test_handle_tidy_fix_dry_run(self, mock_parse, mock_bazel):

        mock_parse.return_value = [
            TidyDiagnostic(
                name="modernize-use-nullptr",
                file_path="foo.cc",
                line=10,
                column=5,
                message="use nullptr",
                level=1,
            )
        ]

        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            args = argparse.Namespace(
                target="@goldfish//emulator/libs/async:tidy",
                target_flag=None,
                level="safe",
                test=None,
                dry_run=True,
                force=True,
                upload=False,
                bug=None,
                continue_tx=False,
                abort=False,
                status=False,
            )
            with patch("commands.tidy.fix_cmd.Path.exists", return_value=True):
                captured_stdout = StringIO()
                with patch("sys.stdout", captured_stdout):
                    handle_tidy_fix(args)
                output = captured_stdout.getvalue()
                self.assertIn("[Dry Run]", output)
                self.assertIn("Would apply fixes for 1 diagnostics", output)

    @patch("commands.tidy.fix_cmd.GitTransactionContext")
    def test_handle_tidy_fix_status(self, mock_tx_cls):
        mock_tx = MagicMock()
        mock_tx_cls.return_value = mock_tx
        from lib.git_transaction import ActiveTransaction

        mock_tx.get_active_transaction.return_value = ActiveTransaction(
            anchor_commit="1234567890abcdef",
            target="@goldfish//emulator/libs/async:tidy",
            level=2,
            latest_symbol_old="oldName",
            latest_symbol_new="old_name",
            status="IN_PROGRESS",
        )

        args = argparse.Namespace(
            target="@goldfish//emulator/libs/async:tidy",
            target_flag=None,
            level=1,
            test=None,
            dry_run=False,
            force=False,
            upload=False,
            bug=None,
            continue_tx=False,
            abort=False,
            status=True,
            json=False,
        )

        captured_stdout = StringIO()
        with patch("sys.stdout", captured_stdout):
            handle_tidy_fix(args)
        output = captured_stdout.getvalue()
        self.assertIn("Active Tidy Refactoring Transaction", output)
        self.assertIn("@goldfish//emulator/libs/async:tidy", output)
        self.assertIn("oldName -> old_name", output)

    @patch("commands.tidy.fix_cmd.GitTransactionContext")
    @patch("commands.tidy.fix_cmd.CompilerOracle")
    @patch("commands.tidy.fix_cmd.BazelRunner")
    @patch("lib.tidy_runner.parse_fixes_yaml")
    def test_handle_tidy_fix_rollback_on_verification_failure(
        self, mock_parse, mock_bazel, mock_oracle_cls, mock_tx_cls
    ):
        mock_tx = MagicMock()
        mock_tx_cls.return_value = mock_tx
        mock_oracle = MagicMock()
        mock_oracle_cls.return_value = mock_oracle

        # Initial compile check fails, agent dispatched, verify check fails
        mock_fail_report = MagicMock(
            success=False,
            errors=[MagicMock(message="broken call")],
            affected_files=["foo.cc"],
        )
        mock_oracle.run_compilation_check.return_value = mock_fail_report

        mock_parse.return_value = [
            TidyDiagnostic(
                name="readability-identifier-naming",
                file_path="foo.cc",
                line=10,
                column=5,
                message="invalid case style for parameter 'oldName' to 'old_name'",
                level=2,
            )
        ]

        args = argparse.Namespace(
            target="@goldfish//emulator/libs/async:tidy",
            target_flag=None,
            level="local",
            test=None,
            dry_run=False,
            force=True,
            upload=False,
            bug=None,
            continue_tx=False,
            abort=False,
            status=False,
            model="auto",
        )

        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tmp_yaml:
            with patch(
                "lib.tidy_engine._find_yaml_file", return_value=Path(tmp_yaml.name)
            ):
                with patch(
                    "lib.tidy_engine._apply_seed_declaration_rename", return_value=True
                ):
                    with patch(
                        "lib.tidy_engine.AgentDispatcher.dispatch_refactor_step",
                        return_value=True,
                    ):
                        with self.assertRaises(SystemExit):
                            handle_tidy_fix(args)

        mock_tx.rollback_step.assert_called_once()

    @patch("commands.tidy.fix_cmd.GitTransactionContext")
    @patch("commands.tidy.fix_cmd.CompilerOracle")
    @patch("commands.tidy.fix_cmd.BazelRunner")
    @patch("lib.tidy_runner.parse_fixes_yaml")
    def test_handle_tidy_fix_abort_on_exception(
        self, mock_parse, mock_bazel, mock_oracle_cls, mock_tx_cls
    ):
        mock_tx = MagicMock()
        mock_tx_cls.return_value = mock_tx

        # Force parse_fixes_yaml to raise an unhandled exception

        from lib.tidy_types import TidyDiagnostic

        valid_diag = TidyDiagnostic(
            name="modernize",
            file_path="foo.cc",
            line=10,
            column=5,
            message="msg",
            level=2,
        )
        mock_parse.side_effect = [
            [valid_diag],
            [valid_diag],  # First call (before loop)
            RuntimeError("Simulated JSON parsing crash"),  # Second call (inside loop)
        ]

        args = argparse.Namespace(
            target="@goldfish//emulator/libs/async:tidy",
            target_flag=None,
            level=2,
            test=None,
            dry_run=False,
            force=True,
            upload=False,
            bug=None,
            continue_tx=False,
            abort=False,
            status=False,
            model="auto",
        )

        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tmp_yaml:
            with patch(
                "lib.tidy_engine._find_yaml_file", return_value=Path(tmp_yaml.name)
            ):
                with patch(
                    "lib.tidy_engine._apply_seed_declaration_rename", return_value=True
                ):
                    with self.assertRaises(RuntimeError):
                        handle_tidy_fix(args)

        # The global exception wrapper must catch the crash and strictly roll back
        mock_tx.rollback_step.assert_called_once()


if __name__ == "__main__":
    unittest.main()
