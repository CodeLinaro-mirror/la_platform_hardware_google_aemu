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

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_EMU_DEV_CLI_DIR = os.path.dirname(_TESTS_DIR)
_WORKFLOWS_DIR = os.path.join(_EMU_DEV_CLI_DIR, "workflows")
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")



from commands.workflow import find_workflow_target, list_available_workflows
from testlib.git_repo import TempGitRepo
from testlib.workflows import WorkflowTestCase
from workflows.common.git_commit import (
    get_head_commit_hash,
    messages_match,
    validate_commit_message,
    validate_commit_message_file,
)
from workflows.core.yaml_runner import (
    execute_workflow_step,
    init_workflow_session,
    load_state_yaml,
    load_yaml,
)


class TestCreateCommitWorkflowIntegration(WorkflowTestCase):

    def setUp(self):
        self.setup_workflow("create-commit")
        self.verifier_py = os.path.join(self.workflow_dir, "verify_commit.py")
        self.verify_proposed_msg_py = os.path.join(self.workflow_dir, "verify_proposed_msg.py")
        self.setup_msg_files_py = os.path.join(self.workflow_dir, "setup_msg_files.py")
        self.get_base_commit_py = os.path.join(self.workflow_dir, "get_base_commit.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
        self.assertTrue(os.path.isfile(self.verify_proposed_msg_py))
        self.assertTrue(os.path.isfile(self.setup_msg_files_py))
        self.assertTrue(os.path.isfile(self.get_base_commit_py))

    def test_workflow_discovery(self):
        self.assert_workflow_discovery("create-commit")

    def test_no_git_commands_in_instructions(self):
        self.assert_no_git_commands_in_instructions()

    def test_semantic_commit_rules_in_workflow_prompt(self):
        self.assert_semantic_commit_rules_in_prompt()

    def test_commit_verifier_validations(self):
        # Empty commit message
        self.assertEqual(validate_commit_message(""), ["Commit message is empty."])

        # Message with forbidden tags
        msg_with_tags = (
            "feat: Test title\n\n"
            "Description.\n\n"
            "TAG=agy\n"
            "CONV=12345\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
        )
        errors = validate_commit_message(msg_with_tags)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("Tag=" in e for e in errors))
        self.assertTrue(any("CONV=" in e for e in errors))

        # Agent conversation and authorship trailers are rejected as well
        msg_with_agent_trailers = (
            "feat: Test title\n\n"
            "Description.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            "CONVERSATION=https://jetski.example/c/1\n"
            "AGY=1\n"
        )
        errors = validate_commit_message(msg_with_agent_trailers)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("'CONVERSATION='" in e for e in errors))
        self.assertTrue(any("forbidden trailer 'AGY='" in e for e in errors))

        # Prose that names a banned trailer is not a trailer
        msg_mentioning_trailers = (
            "feat: Test title\n\n"
            "Rejects the CONV= and AGY= trailers.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
        )
        self.assertEqual(validate_commit_message(msg_mentioning_trailers), [])

        # Missing Bug: line
        msg_missing_bug = "feat: Test title\n\nDescription.\n\nTest: Presubmit\n"
        errors = validate_commit_message(msg_missing_bug)
        self.assertTrue(any("Bug:" in e for e in errors))

        # Missing Test: line
        msg_missing_test = "feat: Test title\n\nDescription.\n\nBug: 12345678\n"
        errors = validate_commit_message(msg_missing_test)
        self.assertTrue(any("Test:" in e for e in errors))

        # Valid messages across all allowed semantic commit types
        for commit_type in ("feat", "fix", "build", "docs", "chore", "test", "refactor", "perf", "style"):
            for bug_val in ("Bug: 12345678", "Bug: N/A", "Bug: b/12345678"):
                for test_val in ("Test: bazel test //...", "TEST: N/A", "Test: N/A"):
                    valid_msg = f"{commit_type}(cli): Test title\n\nDescription.\n\n{bug_val}\n{test_val}\n"
                    self.assertEqual(validate_commit_message(valid_msg), [])

    def test_messages_match_helper(self):
        proposed = "feat: Title\n\nBody.\n\nBug: 12345678\nTest: N/A\n"
        # Exact match
        self.assertTrue(messages_match(proposed, proposed))
        # Match with Gerrit hook added Change-Id
        git_with_hook_cid = proposed + "\nChange-Id: I1234567890abcdef1234567890abcdef12345678\n"
        self.assertTrue(messages_match(git_with_hook_cid, proposed, allow_added_change_id=True))
        # Mismatch on different content
        self.assertFalse(messages_match("feat: Different", proposed))

    def test_verify_commit_cli(self):
        valid_msg = "feat: Title\n\nBody.\n\nBug: 12345678\nTest: N/A\n"
        res = subprocess.run(
            [sys.executable, self.verifier_py, "--msg", valid_msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Commit verification passed", res.stdout)

    def test_verify_commit_proposed_file_cli(self):
        valid_msg = "feat: Title\n\nBody.\n\nBug: 12345678\nTest: N/A\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            proposed_file = Path(tmpdir) / "proposed.txt"
            proposed_file.write_text(valid_msg, encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verifier_py, "--proposed-file", str(proposed_file), "--msg", valid_msg],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("Commit verification passed", res.stdout)

    def test_validate_commit_message_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "commit_msg.txt"

            file_path.write_text("", encoding="utf-8")
            errors = validate_commit_message_file(file_path)
            self.assertTrue(any("is empty" in e for e in errors))

            valid_msg = "feat: Title\n\nBody.\n\nBug: 12345678\nTest: bazel test //...\n"
            file_path.write_text(valid_msg, encoding="utf-8")
            self.assertEqual(validate_commit_message_file(file_path), [])

            file_path.write_text(valid_msg + "TAG=agy\n", encoding="utf-8")
            errors = validate_commit_message_file(file_path)
            self.assertTrue(any("Tag=" in e for e in errors))

    def test_verify_proposed_msg_script(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            msg_file = Path(tmpdir) / "proposed.txt"

            msg_file.write_text("", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(msg_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("empty", res.stderr)

            msg_file.write_text("feat: Valid\n\nBody.\n\nBug: 1234\nTest: N/A\n", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(msg_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("verified successfully", res.stdout)

    def test_workflow_lifecycle_in_git_repo(self):
        with TempGitRepo() as repo:
            base_commit = get_head_commit_hash(cwd=str(repo.path))
            self.assertNotEqual(base_commit, "EMPTY")

            with self.isolated_workflow_env(cwd=repo.path) as (_, state_root, _):
                state_id = init_workflow_session(self.workflow_yaml, [])
                self.assertIsNotNone(state_id)

                state_file = state_root / "create-commit" / f"STATE-{state_id}.yaml"
                state_data = load_state_yaml(state_file)
                proposed_file = Path(state_data["metadata"]["proposed-msg-file"])
                self.assertTrue(proposed_file.is_file())
                self.assertEqual(proposed_file.read_text(encoding="utf-8"), "")

                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "RUNNING")
                self.assertEqual(state_data["step"], 0)

                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                commit_msg = (
                    "feat: Add new feature\n\n"
                    "Detailed description of feature.\n\n"
                    "Bug: 87654321\n"
                    "Test: bazel test //...\n"
                )
                proposed_file.write_text(commit_msg, encoding="utf-8")

                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["step"], 1)

                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                repo.commit_file("feature.txt", "new feature", commit_msg)

                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "DONE")

    def test_no_arguments_allowed(self):
        with self.assertRaises(SystemExit):
            init_workflow_session(self.workflow_yaml, ["some_arg"])

    def test_init_fails_on_non_git_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                [sys.executable, self.get_base_commit_py, "--cwd", tmpdir],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("not a Git repository", res.stderr)


if __name__ == "__main__":
    unittest.main()
