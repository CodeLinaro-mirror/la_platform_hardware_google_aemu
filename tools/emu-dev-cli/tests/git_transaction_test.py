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

"""Unit tests for Git-native transaction manager in emu-dev-cli."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lib.git_transaction import (
    ActiveTransaction,
    GitTransactionContext,
    TRAILER_ANCHOR,
    TRAILER_LEVEL,
    TRAILER_SYMBOL_NEW,
    TRAILER_SYMBOL_OLD,
    TRAILER_TRANSACTION,
)


class GitTransactionTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("lib.git_transaction.subprocess.run")
    def test_start_transaction_clean_tree(self, mock_run):
        def fake_run(cmd, *args, **kwargs):
            res = MagicMock()
            if "status" in cmd:
                res.stdout = ""
                res.returncode = 0
            elif "rev-parse" in cmd:
                res.stdout = "abcdef1234567890\n"
                res.returncode = 0
            return res

        mock_run.side_effect = fake_run

        tx = GitTransactionContext(workspace_root=self.workspace)
        anchor = tx.start_transaction(
            target="@goldfish//emulator/libs/sockets:tidy", level=2
        )

        self.assertEqual(anchor, "abcdef1234567890")
        self.assertEqual(tx.anchor_commit, "abcdef1234567890")

    @patch("lib.git_transaction.subprocess.run")
    def test_start_transaction_dirty_tree_raises(self, mock_run):
        status_res = MagicMock()
        status_res.stdout = " M modified_file.cc\n"
        mock_run.return_value = status_res

        tx = GitTransactionContext(workspace_root=self.workspace)
        with self.assertRaises(RuntimeError):
            tx.start_transaction(
                target="@goldfish//emulator/libs/sockets:tidy", level=2, force=False
            )

    @patch("lib.git_transaction.subprocess.run")
    def test_commit_step_with_trailers(self, mock_run):
        tx = GitTransactionContext(workspace_root=self.workspace)
        tx._anchor_commit = "anchor123"
        tx._target = "@goldfish//emulator/libs/sockets:tidy"
        tx._level = 3

        rev_res = MagicMock()
        rev_res.stdout = "stepcommit456\n"
        mock_run.return_value = rev_res

        step_hash = tx.commit_step(
            symbol_old="socketSetOption",
            symbol_new="SocketSetOption",
            message="style: rename socketSetOption to SocketSetOption",
        )

        self.assertEqual(step_hash, "stepcommit456")
        self.assertEqual(len(tx._step_commits), 1)

        # Check git commit call args
        commit_calls = [c for c in mock_run.call_args_list if c[0][0][1] == "commit"]
        self.assertTrue(commit_calls)
        commit_msg = commit_calls[0][0][0][3]
        self.assertIn(
            "Tidy-Transaction: @goldfish//emulator/libs/sockets:tidy", commit_msg
        )
        self.assertIn("Tidy-Anchor: anchor123", commit_msg)
        self.assertIn("Tidy-Symbol-Old: socketSetOption", commit_msg)
        self.assertIn("Tidy-Symbol-New: SocketSetOption", commit_msg)

    @patch("lib.git_transaction.subprocess.run")
    def test_get_active_transaction_parsing(self, mock_run):
        log_res = MagicMock()
        log_res.returncode = 0
        log_res.stdout = (
            "style: rename symbol\n\n"
            "Tidy-Transaction: @goldfish//emulator/libs/sockets:tidy\n"
            "Tidy-Anchor: anchor999\n"
            "Tidy-Level: 4\n"
            "Tidy-Symbol-Old: socketSendAll\n"
            "Tidy-Symbol-New: SocketSendAll\n"
            "Tidy-Status: IN_PROGRESS\n"
        )
        mock_run.return_value = log_res

        active = GitTransactionContext.get_active_transaction(
            workspace_root=self.workspace
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.anchor_commit, "anchor999")
        self.assertEqual(active.target, "@goldfish//emulator/libs/sockets:tidy")
        self.assertEqual(active.level, 4)
        self.assertEqual(active.latest_symbol_old, "socketSendAll")
        self.assertEqual(active.latest_symbol_new, "SocketSendAll")

    @patch("lib.git_transaction.subprocess.run")
    def test_finalize_and_squash(self, mock_run):
        tx = GitTransactionContext(workspace_root=self.workspace)
        tx._anchor_commit = "anchor123"
        tx._step_commits = ["step1", "step2"]

        head_res = MagicMock()
        head_res.stdout = "step2\n"
        final_head_res = MagicMock()
        final_head_res.stdout = "finalatomiccommit\n"

        mock_run.side_effect = [head_res, MagicMock(), MagicMock(), final_head_res]

        final_hash = tx.finalize_and_squash(
            final_commit_msg="style(sockets): Modernize symbol and variable naming conventions"
        )

        self.assertEqual(final_hash, "finalatomiccommit")
        # Check reset --soft call
        soft_reset_calls = [c for c in mock_run.call_args_list if "--soft" in c[0][0]]
        self.assertTrue(soft_reset_calls)

    @patch("lib.git_transaction.subprocess.run")
    def test_resolve_git_repo_for_target_from_subproject(self, mock_run):
        from lib.git_transaction import resolve_git_repo_for_target

        with tempfile.TemporaryDirectory() as tmpdir:
            super_root = Path(tmpdir)
            goldfish_sub = super_root / "hardware" / "generic" / "goldfish"
            goldfish_sub.mkdir(parents=True)
            (goldfish_sub / ".git").mkdir()
            (goldfish_sub / "emulator" / "libs" / "sockets").mkdir(parents=True)
            (goldfish_sub / "emulator" / "libs" / "sockets" / "BUILD.bazel").write_text(
                "", encoding="utf-8"
            )

            def fake_run(cmd, *args, **kwargs):
                res = MagicMock()
                if cmd[0] == "git" and "rev-parse" in cmd:
                    res.returncode = 1
                    res.stdout = ""
                elif cmd[0] == "bazel" and "query" in cmd:
                    res.returncode = 0
                    res.stdout = (
                        f"{goldfish_sub}/emulator/libs/sockets/BUILD.bazel:12:1: rule"
                    )
                else:
                    res.returncode = 1
                return res

            mock_run.side_effect = fake_run

            # 1. Resolving from inside the subproject directory directly
            resolved_from_sub = resolve_git_repo_for_target(
                target="@goldfish//emulator/libs/sockets:tidy",
                root_hint=goldfish_sub / "emulator" / "libs" / "sockets",
            )
            self.assertEqual(resolved_from_sub, goldfish_sub)

            # 2. Resolving from the superproject root
            resolved_from_super = resolve_git_repo_for_target(
                target="@goldfish//emulator/libs/sockets:tidy",
                root_hint=super_root,
            )
            self.assertEqual(resolved_from_super, goldfish_sub)


if __name__ == "__main__":
    unittest.main()
