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

"""Hermetic unit tests for core.git and clients.git_service (GitService, ProdGitService, FakeGitService)."""

from pathlib import Path
import unittest

from clients.git_service import (
    FakeGitService,
    GitCommitInfo,
    GitService,
    ProdGitService,
)
from core.context import ExecutionContext
from core.errors import SubprocessError
from core.process import FakeProcessRunner, ProcessResult


class GitServiceTest(unittest.TestCase):
    """Tests ProdGitService over FakeProcessRunner and FakeGitService in-memory semantics."""

    def test_execution_context_resolves_fake_git_service(self) -> None:
        ctx = ExecutionContext.for_testing()
        self.assertIsInstance(ctx.git_service, GitService)
        self.assertIs(ctx.git_service, ctx.fake_git_service)

    def test_prod_git_service_delegates_to_process_runner(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["git", "rev-parse", "--show-toplevel"],
            ProcessResult(
                args=("git", "rev-parse", "--show-toplevel"),
                returncode=0,
                stdout="/tmp/repo_测试\n",
            ),
        )
        runner.register_prefix(
            ["git", "status", "--porcelain"],
            ProcessResult(
                args=("git", "status", "--porcelain"),
                returncode=0,
                stdout="",
            ),
        )
        git = ProdGitService(runner, default_cwd="/tmp/repo_测试")
        self.assertEqual(git.top_level(), Path("/tmp/repo_测试").resolve())
        self.assertTrue(git.is_clean())

    def test_fake_git_service_commit_lifecycle_and_ancestry(self) -> None:
        git = FakeGitService(repo_root=Path("/tmp/fake_repo"))
        self.assertTrue(git.is_git_repo())
        self.assertEqual(git.top_level(), Path("/tmp/fake_repo").resolve())

        base = git.seed_commit(
            "1111111111111111111111111111111111111111",
            "feat(core): Initial commit\n\nBug: 123\nChange-Id: I1111111111111111111111111111111111111111",
        )
        self.assertEqual(git.head_sha(), base.sha)
        self.assertEqual(git.commit_subject(), "feat(core): Initial commit")

        git.set_dirty(" M src/a.py\n?? untracked.txt\n")
        self.assertFalse(git.is_clean(untracked=True))
        self.assertFalse(git.is_clean(untracked=False))

        git.add(["src/a.py"])
        git.commit(
            "fix(core): Address issue 测试_🚀\n\nBug: 456\nChange-Id: I2222222222222222222222222222222222222222"
        )
        self.assertTrue(git.is_clean())
        head_sha = git.head_sha()
        self.assertNotEqual(head_sha, base.sha)
        self.assertEqual(git.commit_parents("HEAD"), [base.sha])
        self.assertTrue(git.is_ancestor(base.sha, head_sha))
        self.assertEqual(git.diff_names("HEAD~1", "HEAD"), ["src/a.py"])
        self.assertEqual(
            git.find_commit_by_grep("I2222222222222222222222222222222222222222"),
            head_sha,
        )

        git.set_config("push.negotiate", "false")
        self.assertEqual(git.get_config("push.negotiate"), "false")

        with self.assertRaises(SubprocessError):
            git.rev_parse("nonexistent_ref", check=True)


if __name__ == "__main__":
    unittest.main()
