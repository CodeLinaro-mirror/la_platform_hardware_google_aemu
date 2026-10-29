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
"""Unit tests for BaseRefactoringEngine and its concrete tidy subclasses."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.errors import ExecutionError
from lib.batched_engine import BatchedRefactoringEngine
from lib.compiler_oracle import CompilerOracleReport
from lib.level1_engine import Level1RefactoringEngine
from lib.refactoring import BaseRefactoringEngine
from lib.tidy_engine import GitNativeRefactoringEngine


class DummyRefactoringEngine(BaseRefactoringEngine):
    """Concrete test subclass for exercising BaseRefactoringEngine template methods."""

    def run_pipeline(self, *args, **kwargs):
        return "pipeline_executed"


class BaseRefactoringEngineTest(unittest.TestCase):
    """Tests Template Method operations shared across all tidy engines."""

    def setUp(self):
        self.mock_runner = MagicMock()
        self.mock_oracle = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_tx = MagicMock()

    def test_all_tidy_engines_inherit_from_base_engine(self):
        self.assertTrue(issubclass(Level1RefactoringEngine, BaseRefactoringEngine))
        self.assertTrue(issubclass(GitNativeRefactoringEngine, BaseRefactoringEngine))
        self.assertTrue(issubclass(BatchedRefactoringEngine, BaseRefactoringEngine))

    def test_resolve_declaration_path_with_unicode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_dir = root / "repo"
            repo_dir.mkdir()
            unicode_rel = "src/测试_🚀_é.cc"
            fallback_file = root / unicode_rel
            fallback_file.parent.mkdir(parents=True)
            fallback_file.write_text("int x = 0;\n", encoding="utf-8")

            engine = DummyRefactoringEngine(
                self.mock_runner, self.mock_oracle, self.mock_dispatcher, root
            )
            # Falls back to source_path when not in repo_dir
            resolved = engine.resolve_declaration_path(repo_dir, unicode_rel)
            self.assertEqual(resolved, fallback_file)

            # Prefers repo_dir when file exists there
            repo_file = repo_dir / unicode_rel
            repo_file.parent.mkdir(parents=True)
            repo_file.write_text("int y = 1;\n", encoding="utf-8")
            self.assertEqual(
                engine.resolve_declaration_path(repo_dir, unicode_rel), repo_file
            )

    def test_verify_compilation_and_test_gate_rollback_on_failure(self):
        engine = DummyRefactoringEngine(
            self.mock_runner, self.mock_oracle, self.mock_dispatcher, "/mock/src"
        )
        self.mock_oracle.run_compilation_check.return_value = CompilerOracleReport(
            success=False
        )
        with self.assertRaises(ExecutionError):
            engine.verify_compilation_or_rollback(
                self.mock_tx, ["//foo:bar"], "Compile failed"
            )
        self.mock_tx.rollback_step.assert_called_once()

        self.mock_tx.reset_mock()
        self.mock_runner.test.return_value = MagicMock(returncode=1)
        with self.assertRaises(ExecutionError):
            engine.verify_test_gate_or_rollback(self.mock_tx, "//foo:bar_test")
        self.mock_tx.rollback_step.assert_called_once()

    def test_execute_with_transaction_guard_rolls_back_on_error_and_interrupt(self):
        engine = DummyRefactoringEngine(
            self.mock_runner, self.mock_oracle, self.mock_dispatcher, "/mock/src"
        )

        def _raise_runtime():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            engine.execute_with_transaction_guard(self.mock_tx, _raise_runtime)
        self.mock_tx.rollback_step.assert_called_once()

        self.mock_tx.reset_mock()

        def _raise_kb():
            raise KeyboardInterrupt()

        with self.assertRaises(ExecutionError) as cm:
            engine.execute_with_transaction_guard(self.mock_tx, _raise_kb)
        self.assertEqual(cm.exception.exit_code, 130)
        self.mock_tx.rollback_step.assert_called_once()


if __name__ == "__main__":
    unittest.main()
