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

"""Hermetic unit tests for `workflows.common.git_commit.verifier_cli`."""

import contextlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.git import FakeGitService
from core.process import ProcessResult
from workflows.common.git_commit import verifier_cli
from workflows.common.git_commit.verifier_cli import (
    CHECK_CHANGE_ID_PRESENT,
    CHECK_CLEAN_TREE,
    CHECK_FOOTER_VALID,
    VerifierContext,
    get_base_commit,
    main as verifier_main,
    run_check_strategies,
    validate_git_repository,
    verify_amend,
    verify_commit,
    verify_proposed_msg,
)

VALID_COMMIT_MSG = (
    "feat(cli): Add unified verifier CLI runner\n\n"
    "Consolidate commit and amend workflow verifiers into a single\n"
    "strategy-driven runner over GitService for Phase 15.\n\n"
    "Bug: 579918998\n"
    "Test: bazel test //tools/emu-dev-cli:all\n"
    "Change-Id: I1111111111111111111111111111111111111111\n"
)
OTHER_VALID_MSG = VALID_COMMIT_MSG.replace(
    "Add unified verifier CLI runner", "Refine unified verifier CLI runner"
)
CHANGE_ID = "I1111111111111111111111111111111111111111"
OTHER_CHANGE_ID = "I2222222222222222222222222222222222222222"
INVALID_UTF8 = b"\xff\xfe\xfa not utf-8"
ROOT_SHA = "a" * 40
BASE_SHA = "b" * 40
FAILED_LOG = ProcessResult(
    args=("git", "log"), returncode=128, stderr="fatal: simulated failure\n"
)


def _capture(fn, *args, **kwargs):
    """Runs `fn` with stdout/stderr captured; returns `(result, stdout, stderr)`."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


class VerifierCliTest(unittest.TestCase):
    """Tests composable `--check` strategies and `--mode` actions in `verifier_cli`."""

    def test_clean_tree_strategy_detects_dirty_and_clean_states(self) -> None:
        git = FakeGitService()
        git.set_clean()
        ctx_clean = VerifierContext(git_service=git)
        self.assertEqual(run_check_strategies([CHECK_CLEAN_TREE], ctx_clean), [])

        git.set_dirty(" M src/core/git.py\n")
        ctx_dirty = VerifierContext(git_service=git)
        errors = run_check_strategies([CHECK_CLEAN_TREE], ctx_dirty)
        self.assertEqual(len(errors), 1)
        self.assertIn("uncommitted changes", errors[0])

    def test_change_id_present_and_footer_valid_strategies(self) -> None:
        git = FakeGitService()
        git.seed_commit("aaaa1111", VALID_COMMIT_MSG)

        rc = verifier_main(
            [
                "--check=clean_tree,change_id_present,footer_valid",
                "--expected-change-id=I1111111111111111111111111111111111111111",
            ],
            git_service=git,
        )
        self.assertEqual(rc, 0)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            rc_mismatch = verifier_main(
                [
                    f"--check={CHECK_CHANGE_ID_PRESENT}",
                    "--expected-change-id=I2222222222222222222222222222222222222222",
                ],
                git_service=git,
            )
        self.assertEqual(rc_mismatch, 1)
        self.assertIn("Change-Id must not be modified", stderr_buf.getvalue())

    def test_lifecycle_modes_setup_propose_commit_and_amend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            current_file = tmp / "current.txt"
            proposed_file = tmp / "proposed.txt"
            base_commit_file = tmp / "base.txt"

            git = FakeGitService(repo_root=tmp)
            base_sha = "1111111111111111111111111111111111111111"
            git.seed_commit(base_sha, VALID_COMMIT_MSG)

            self.assertEqual(
                verifier_main(
                    [
                        "--mode=setup-msg-files",
                        str(current_file),
                        str(proposed_file),
                    ],
                    git_service=git,
                ),
                0,
            )
            self.assertTrue(current_file.is_file())
            self.assertTrue(proposed_file.is_file())

            current_file.write_text(VALID_COMMIT_MSG, encoding="utf-8")
            updated_msg = VALID_COMMIT_MSG.replace(
                "Add unified verifier CLI runner",
                "Refine unified verifier CLI runner",
            )
            proposed_file.write_text(updated_msg, encoding="utf-8")

            self.assertEqual(
                verifier_main(
                    [
                        "--mode=verify-proposed-msg",
                        str(proposed_file),
                        "--current-file",
                        str(current_file),
                        "--base-commit-file",
                        str(base_commit_file),
                    ],
                    git_service=git,
                ),
                0,
            )
            self.assertEqual(
                base_commit_file.read_text(encoding="utf-8").strip(), base_sha
            )

            # Simulate amending the commit on FakeGitService
            amended_sha = "2222222222222222222222222222222222222222"
            git.commits[-1] = git.commits[-1].__class__(
                sha=amended_sha,
                message=updated_msg,
                parents=(),
            )
            git.refs["HEAD"] = amended_sha

            self.assertEqual(
                verifier_main(
                    [
                        "--mode=verify-amend",
                        "--current-file",
                        str(current_file),
                        "--proposed-file",
                        str(proposed_file),
                        "--base-commit-file",
                        str(base_commit_file),
                    ],
                    git_service=git,
                ),
                0,
            )


class VerifierCliErrorPathTest(unittest.TestCase):
    """Covers `verifier_cli` failure branches against hermetic `FakeGitService` state."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name) / "仓库_🚀"
        self.tmp.mkdir()
        self.cwd = str(self.tmp)
        self.git = FakeGitService(repo_root=self.tmp)

    def _write(self, name: str, content: str | bytes) -> str:
        path = self.tmp / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return str(path)

    def _seed_root_and_base(self) -> None:
        self.git.seed_commit(ROOT_SHA, "chore: Root\n\nRoot commit.\n")
        self.git.seed_commit(BASE_SHA, VALID_COMMIT_MSG)

    def _amend_head(self, message: str) -> None:
        self.git.run_git(["commit", "--amend", "-m", message])

    def assertFails(self, needle: str, fn, *args, **kwargs) -> None:
        rc, _, err = _capture(fn, *args, **kwargs)
        self.assertEqual(rc, 1, err)
        self.assertIn(needle, err)

    def test_validate_git_repository_rejects_missing_and_non_git_dirs(self) -> None:
        self.assertTrue(validate_git_repository(None))
        self.assertTrue(validate_git_repository(self.cwd, git_service=self.git))
        missing = str(self.tmp / "missing_目录")
        ok, _, err = _capture(validate_git_repository, missing, git_service=self.git)
        self.assertFalse(ok)
        self.assertIn(f"directory does not exist: '{missing}'", err)
        not_repo = FakeGitService(inside_work_tree=False)
        ok, _, err = _capture(validate_git_repository, self.cwd, git_service=not_repo)
        self.assertFalse(ok)
        self.assertIn("is not a Git repository", err)

    def test_strategies_report_missing_duplicate_and_unreadable_change_ids(self) -> None:
        no_cid = "feat(cli): Add runner\n\nBody text.\n\nBug: 1\n"
        two_cids = f"{no_cid}Change-Id: {CHANGE_ID}\nChange-Id: {OTHER_CHANGE_ID}\n"
        cases = (
            (no_cid, CHANGE_ID, f"Expected: 'Change-Id: {CHANGE_ID}'"),
            (no_cid, None, "missing a Gerrit 'Change-Id"),
            (two_cids, None, "Multiple 'Change-Id' lines found in commit message (2)"),
        )
        for msg, expected_cid, needle in cases:
            with self.subTest(needle=needle):
                ctx = VerifierContext(
                    message_text=msg,
                    expected_change_id=expected_cid,
                    git_service=self.git,
                )
                errors = run_check_strategies([CHECK_CHANGE_ID_PRESENT], ctx)
                self.assertEqual(len(errors), 1)
                self.assertIn(needle, errors[0])

        # The empty fake repository has no HEAD commit message to read.
        for check in (CHECK_CHANGE_ID_PRESENT, CHECK_FOOTER_VALID):
            with self.subTest(check=check):
                errors = run_check_strategies(
                    [check], VerifierContext(git_service=self.git)
                )
                self.assertEqual(len(errors), 1)
                self.assertIn("Could not read commit message for 'HEAD'", errors[0])

        ctx = VerifierContext(message_text=VALID_COMMIT_MSG, git_service=self.git)
        self.assertEqual(run_check_strategies([CHECK_FOOTER_VALID], ctx), [])

    def test_run_check_strategies_rejects_unknown_names_and_skips_blank_tokens(
        self,
    ) -> None:
        errors = run_check_strategies(
            [" clean_tree, ,bogus_check "], VerifierContext(git_service=self.git)
        )
        self.assertEqual(
            errors,
            [
                "Unknown verifier check strategy 'bogus_check'. Supported checks: "
                "clean_tree, change_id_present, footer_valid."
            ],
        )

    def test_get_base_commit_and_setup_msg_files_modes(self) -> None:
        self.git.seed_commit(ROOT_SHA, VALID_COMMIT_MSG)
        rc, out, _ = _capture(
            verifier_main,
            ["--mode=get-base-commit", "--cwd", self.cwd],
            git_service=self.git,
        )
        self.assertEqual((rc, out.strip()), (0, ROOT_SHA))
        self.assertFails(
            "does not exist",
            get_base_commit,
            str(self.tmp / "missing"),
            git_service=self.git,
        )

        single = self.tmp / "nested_中文" / "proposed.txt"
        rc, out, _ = _capture(verifier_main, ["--mode=setup-msg-files", str(single)])
        self.assertEqual((rc, out.strip()), (0, "OK"))
        self.assertEqual(single.read_text(encoding="utf-8"), "")

        for mode in ("setup-msg-files", "verify-proposed-msg"):
            with self.subTest(mode=mode), self.assertRaises(SystemExit) as cm:
                _capture(verifier_main, [f"--mode={mode}"])
            self.assertEqual(cm.exception.code, 2)

    def test_unreadable_msg_file_fails_every_message_mode(self) -> None:
        missing = str(self.tmp / "missing_msg.txt")
        for mode in ("check", "verify-commit", "verify-amend"):
            with self.subTest(mode=mode):
                self.assertFails(
                    f"Error reading message file '{missing}'",
                    verifier_main,
                    [f"--mode={mode}", "--msg-file", missing],
                    git_service=self.git,
                )

    def test_check_mode_reads_messages_from_flags_and_files(self) -> None:
        missing = str(self.tmp / "missing.txt")
        self.assertFails(
            f"Message file '{missing}' does not exist",
            verifier_main,
            ["--check=footer_valid", "--proposed-file", missing],
            git_service=self.git,
        )
        proposed = self._write("proposed.txt", VALID_COMMIT_MSG)
        rc, _, _ = _capture(
            verifier_main,
            ["--check=footer_valid", "--proposed-file", proposed],
            git_service=self.git,
        )
        self.assertEqual(rc, 0)
        self.assertFails(
            "Error:",
            verifier_main,
            ["--check=footer_valid", "--msg", "not a semantic subject"],
            git_service=self.git,
        )

        self.git.set_dirty("?? untracked_文件.txt\n")
        self.assertEqual(_capture(verifier_main, [], git_service=self.git)[0], 0)
        self.assertFails(
            "untracked_文件.txt",
            verifier_main,
            ["--include-untracked"],
            git_service=self.git,
        )

    def test_verify_proposed_msg_rejects_invalid_current_and_proposed_files(
        self,
    ) -> None:
        proposed = self._write("proposed.txt", VALID_COMMIT_MSG)
        no_cid = "feat(cli): Add runner\n\nBody text.\n"
        two_cids = f"{no_cid}\nChange-Id: {CHANGE_ID}\nChange-Id: {OTHER_CHANGE_ID}\n"
        cases = (
            ("missing.txt", None, "does not exist"),
            ("empty.txt", "  \n", "is empty"),
            ("no_cid.txt", no_cid, "No Gerrit Change-Id found"),
            ("two_cids.txt", two_cids, "Multiple 'Change-Id' lines found"),
        )
        for name, content, needle in cases:
            with self.subTest(name=name):
                current = (
                    self._write(name, content)
                    if content is not None
                    else str(self.tmp / name)
                )
                self.assertFails(
                    needle,
                    verify_proposed_msg,
                    proposed,
                    current_file=current,
                    git_service=self.git,
                )

        rc, out, _ = _capture(
            verifier_main,
            ["--mode=verify-proposed-msg", proposed, CHANGE_ID],
            git_service=self.git,
        )
        self.assertEqual(rc, 0)
        self.assertIn("verified successfully", out)
        bad = self._write("bad.txt", "not a semantic subject\n")
        self.assertFails("Error:", verify_proposed_msg, bad, git_service=self.git)

    def test_verify_commit_rejects_missing_unchanged_invalid_and_mismatched_commits(
        self,
    ) -> None:
        self.assertFails(
            "No commit found in repository", verify_commit, git_service=self.git
        )
        self.git.seed_commit(BASE_SHA, VALID_COMMIT_MSG)
        self.assertFails(
            f"Current HEAD matches base commit {BASE_SHA}",
            verify_commit,
            base_commit=BASE_SHA,
            git_service=self.git,
        )
        self.git.seed_commit("c" * 40, "not a semantic subject\n")
        self.assertFails(
            "Error:", verify_commit, base_commit=BASE_SHA, git_service=self.git
        )

        self.git.seed_commit("d" * 40, VALID_COMMIT_MSG)
        proposals = (
            (str(self.tmp / "missing.txt"), "does not exist"),
            (self._write("other.txt", OTHER_VALID_MSG), "does not match verified"),
            (self._write("binary.txt", INVALID_UTF8), "Error reading proposed"),
        )
        for proposed, needle in proposals:
            with self.subTest(needle=needle):
                self.assertFails(
                    needle,
                    verify_commit,
                    base_commit=BASE_SHA,
                    proposed_file=proposed,
                    git_service=self.git,
                )

        matching = self._write("proposed.txt", VALID_COMMIT_MSG)
        rc, out, _ = _capture(
            verifier_main,
            ["--mode=verify-commit", BASE_SHA, "--proposed-file", matching],
            git_service=self.git,
        )
        self.assertEqual(rc, 0)
        self.assertIn("Commit verification passed.", out)
        rc, _, _ = _capture(
            verifier_main,
            ["--mode=verify-commit", "--msg-file", matching],
            git_service=self.git,
        )
        self.assertEqual(rc, 0)

        self.git.process_runner.register_prefix(
            ["git", "log", "-1", "--format=%B"], FAILED_LOG
        )
        self.assertFails(
            "Error reading commit message",
            verify_commit,
            base_commit=BASE_SHA,
            git_service=self.git,
        )

    def test_verify_amend_rejects_unamended_stacked_and_unknown_commits(self) -> None:
        self._seed_root_and_base()
        self.assertFails(
            "does not exist",
            verify_amend,
            original_change_id=CHANGE_ID,
            cwd=str(self.tmp / "missing"),
            git_service=self.git,
        )
        unamended = "has not been amended yet"
        cases = (
            (OTHER_CHANGE_ID, "HEAD", f"No commit with Change-Id '{OTHER_CHANGE_ID}'"),
            (CHANGE_ID, "HEAD", f"Commit 'HEAD' ({BASE_SHA[:8]}) {unamended}"),
            (CHANGE_ID, "", f"Commit '{BASE_SHA[:8]}' {unamended}"),
            ("NONE", "HEAD", f"Commit 'HEAD' ({BASE_SHA[:8]}) {unamended}"),
            ("NONE", "", f"Commit '{BASE_SHA[:8]}' {unamended}"),
            ("NONE", "no_such_ref", "Cannot resolve commit 'no_such_ref'"),
        )
        for change_id, commit_ref, needle in cases:
            with self.subTest(change_id=change_id, commit_ref=commit_ref):
                self.assertFails(
                    needle,
                    verify_amend,
                    base_commit=BASE_SHA,
                    original_change_id=change_id,
                    commit_ref=commit_ref,
                    cwd=self.cwd,
                    git_service=self.git,
                )

        stacked = "e" * 40
        self.git.seed_commit(
            stacked, VALID_COMMIT_MSG.replace(CHANGE_ID, OTHER_CHANGE_ID)
        )
        for change_id in (OTHER_CHANGE_ID, "NONE"):
            with self.subTest(stacked_change_id=change_id):
                self.assertFails(
                    f"A new commit ({stacked[:8]}) was created on top of base "
                    f"commit {BASE_SHA[:8]}",
                    verify_amend,
                    base_commit=BASE_SHA,
                    original_change_id=change_id,
                    cwd=self.cwd,
                    git_service=self.git,
                )

    def test_verify_amend_checks_amended_message_against_current_and_proposed(
        self,
    ) -> None:
        self._seed_root_and_base()
        proposed = self._write("proposed.txt", OTHER_VALID_MSG)
        amend_kwargs = dict(
            current_file=self._write("current.txt", VALID_COMMIT_MSG),
            proposed_file=proposed,
            base_commit_file=self._write("base.txt", BASE_SHA + "\n"),
            cwd=self.cwd,
            git_service=self.git,
        )
        amendments = (
            (VALID_COMMIT_MSG, "Commit message still matches original message"),
            (VALID_COMMIT_MSG.replace("feat(cli): ", "feat(cli) "), "Error:"),
            (
                VALID_COMMIT_MSG.replace("unified verifier", "shared verifier"),
                "does not match verified message",
            ),
        )
        for message, needle in amendments:
            with self.subTest(needle=needle):
                self._amend_head(message)
                self.assertFails(needle, verify_amend, **amend_kwargs)

        self._amend_head(OTHER_VALID_MSG)
        rc, out, _ = _capture(
            verifier_main,
            [
                "--mode=verify-amend",
                BASE_SHA,
                CHANGE_ID,
                "--proposed-file",
                proposed,
                "--cwd",
                self.cwd,
            ],
            git_service=self.git,
        )
        self.assertEqual(rc, 0)
        self.assertIn("Commit amendment verification passed.", out)

        self.git.process_runner.register_prefix(
            ["git", "log", "-1", "--format=%B"], FAILED_LOG
        )
        self.assertFails(
            "Error reading commit message for", verify_amend, **amend_kwargs
        )

    def test_verify_amend_ignores_undecodable_helper_files(self) -> None:
        self._seed_root_and_base()
        binary = self._write("binary.txt", INVALID_UTF8)
        rc, out, _ = _capture(
            verify_amend,
            current_file=binary,
            base_commit_file=binary,
            cwd=self.cwd,
            git_service=self.git,
        )
        self.assertEqual(rc, 0)
        self.assertIn("Commit amendment verification passed.", out)

    def test_cli_entrypoints_exit_with_main_status(self) -> None:
        with patch.object(sys, "argv", ["verifier_cli.py", "--check=bogus_check"]):
            with self.assertRaises(SystemExit) as cm:
                _capture(verifier_cli.run_cli_entrypoint, ["--mode=check"])
            self.assertEqual(cm.exception.code, 1)
            with self.assertRaises(SystemExit) as cm:
                _capture(runpy.run_path, verifier_cli.__file__, run_name="__main__")
            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
