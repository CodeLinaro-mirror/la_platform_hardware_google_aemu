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

"""Unit tests for Git-native transaction manager in emu-dev-cli using FakeGitService."""

from pathlib import Path
import tempfile
import unittest

from core.git import FakeGitService
from core.process import FakeProcessRunner, ProcessResult
from lib.git_transaction import (
    ActiveTransaction,
    GitTransactionContext,
    TRAILER_ANCHOR,
    TRAILER_LEVEL,
    TRAILER_SYMBOL_NEW,
    TRAILER_SYMBOL_OLD,
    TRAILER_TRANSACTION,
    resolve_git_repo_for_target,
)


class GitTransactionTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.git = FakeGitService(repo_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_start_transaction_clean_tree(self):
        self.git.seed_commit("abcdef1234567890", "feat: Initial")
        self.git.set_clean()

        tx = GitTransactionContext(
            workspace_root=self.workspace, git_service=self.git
        )
        anchor = tx.start_transaction(
            target="@goldfish//emulator/libs/sockets:tidy", level=2
        )

        self.assertEqual(anchor, "abcdef1234567890")
        self.assertEqual(tx.anchor_commit, "abcdef1234567890")

    def test_start_transaction_dirty_tree_raises(self):
        self.git.seed_commit("abcdef1234567890", "feat: Initial")
        self.git.set_dirty(" M modified_file.cc\n")

        tx = GitTransactionContext(
            workspace_root=self.workspace, git_service=self.git
        )
        with self.assertRaises(RuntimeError):
            tx.start_transaction(
                target="@goldfish//emulator/libs/sockets:tidy",
                level=2,
                force=False,
            )

    def test_commit_step_with_trailers(self):
        self.git.seed_commit("anchor123", "feat: Anchor")
        self.git.status_output = " M socket.cc\n"
        tx = GitTransactionContext(
            workspace_root=self.workspace, git_service=self.git
        )
        tx._anchor_commit = "anchor123"
        tx._target = "@goldfish//emulator/libs/sockets:tidy"
        tx._level = 3

        step_hash = tx.commit_step(
            symbol_old="socketSetOption",
            symbol_new="SocketSetOption",
            message="style: rename socketSetOption to SocketSetOption",
        )

        self.assertIsNotNone(step_hash)
        self.assertEqual(len(tx._step_commits), 1)

        commit_msg = self.git.commit_message("HEAD")
        self.assertIn(
            f"{TRAILER_TRANSACTION}: @goldfish//emulator/libs/sockets:tidy",
            commit_msg,
        )
        self.assertIn(f"{TRAILER_ANCHOR}: anchor123", commit_msg)
        self.assertIn(f"{TRAILER_LEVEL}: 3", commit_msg)
        self.assertIn(f"{TRAILER_SYMBOL_OLD}: socketSetOption", commit_msg)
        self.assertIn(f"{TRAILER_SYMBOL_NEW}: SocketSetOption", commit_msg)

    def test_get_active_transaction_parsing(self):
        self.git.seed_commit(
            "step999",
            (
                "style: rename symbol\n\n"
                "Tidy-Transaction: @goldfish//emulator/libs/sockets:tidy\n"
                "Tidy-Anchor: anchor999\n"
                "Tidy-Level: 4\n"
                "Tidy-Symbol-Old: socketSendAll\n"
                "Tidy-Symbol-New: SocketSendAll\n"
                "Tidy-Status: IN_PROGRESS\n"
            ),
        )

        active = GitTransactionContext.get_active_transaction(
            workspace_root=self.workspace,
            git_service=self.git,
        )
        self.assertIsInstance(active, ActiveTransaction)
        assert active is not None
        self.assertEqual(active.anchor_commit, "anchor999")
        self.assertEqual(active.target, "@goldfish//emulator/libs/sockets:tidy")
        self.assertEqual(active.level, 4)
        self.assertEqual(active.latest_symbol_old, "socketSendAll")
        self.assertEqual(active.latest_symbol_new, "SocketSendAll")

    def test_finalize_and_squash(self):
        self.git.seed_commit("anchor123", "feat: Anchor")
        self.git.seed_commit("step1", "style: Step 1", parents=["anchor123"])
        self.git.seed_commit("step2", "style: Step 2", parents=["step1"])

        tx = GitTransactionContext(
            workspace_root=self.workspace, git_service=self.git
        )
        tx._anchor_commit = "anchor123"
        tx._step_commits = ["step1", "step2"]

        final_hash = tx.finalize_and_squash(
            final_commit_msg="style(sockets): Modernize symbol and variable naming conventions"
        )

        self.assertIsNotNone(final_hash)
        soft_reset_calls = [
            cmd for cmd in self.git.recorded_commands if "--soft" in cmd
        ]
        self.assertTrue(soft_reset_calls)

    def test_resolve_git_repo_for_target_from_subproject(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            super_root = Path(tmpdir).resolve()
            goldfish_sub = super_root / "hardware" / "generic" / "goldfish"
            goldfish_sub.mkdir(parents=True)
            (goldfish_sub / ".git").mkdir()
            (goldfish_sub / "emulator" / "libs" / "sockets").mkdir(parents=True)
            (
                goldfish_sub / "emulator" / "libs" / "sockets" / "BUILD.bazel"
            ).write_text("", encoding="utf-8")

            fake_git = FakeGitService(inside_work_tree=False)
            fake_runner = FakeProcessRunner()
            fake_runner.register_prefix(
                ["bazel", "query"],
                ProcessResult(
                    args=("bazel", "query"),
                    returncode=0,
                    stdout=f"{goldfish_sub}/emulator/libs/sockets/BUILD.bazel:12:1: rule\n",
                ),
            )

            # 1. Resolving from inside the subproject directory directly
            resolved_from_sub = resolve_git_repo_for_target(
                target="@goldfish//emulator/libs/sockets:tidy",
                root_hint=goldfish_sub / "emulator" / "libs" / "sockets",
                git_service=fake_git,
                process_runner=fake_runner,
            )
            self.assertEqual(resolved_from_sub, goldfish_sub)

            # 2. Resolving from the superproject root
            resolved_from_super = resolve_git_repo_for_target(
                target="@goldfish//emulator/libs/sockets:tidy",
                root_hint=super_root,
                git_service=fake_git,
                process_runner=fake_runner,
            )
            self.assertEqual(resolved_from_super, goldfish_sub)


if __name__ == "__main__":
    unittest.main()
