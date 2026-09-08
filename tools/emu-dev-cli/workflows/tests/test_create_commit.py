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
_WORKFLOWS_DIR = os.path.dirname(_TESTS_DIR)
_EMU_DEV_CLI_DIR = os.path.dirname(_WORKFLOWS_DIR)
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
if _EMU_DEV_CLI_DIR not in sys.path:
    sys.path.insert(0, _EMU_DEV_CLI_DIR)
if _WORKFLOWS_DIR not in sys.path:
    sys.path.insert(0, _WORKFLOWS_DIR)

from commands.workflow import find_workflow_target, list_available_workflows
from workflows.common.git_commit import (
    extract_change_id,
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


class TestCreateCommitWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("create-commit")
        self.assertIsNotNone(target, "create-commit workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.workflow_dir = os.path.dirname(self.workflow_yaml)
        self.verifier_py = os.path.join(self.workflow_dir, "verify_commit.py")
        self.verify_proposed_msg_py = os.path.join(self.workflow_dir, "verify_proposed_msg.py")
        self.setup_msg_files_py = os.path.join(self.workflow_dir, "setup_msg_files.py")
        self.get_base_commit_py = os.path.join(self.workflow_dir, "get_base_commit.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
        self.assertTrue(os.path.isfile(self.verify_proposed_msg_py))
        self.assertTrue(os.path.isfile(self.setup_msg_files_py))
        self.assertTrue(os.path.isfile(self.get_base_commit_py))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("create-commit", workflows)
        wf = workflows["create-commit"]
        self.assertEqual(wf["name"], "create-commit")
        self.assertEqual(wf["target_type"], "yaml")
        self.assertTrue(os.path.isfile(wf["target_path"]))

    def test_no_git_commands_in_instructions(self):
        """Ensures that no specific git commands are embedded in workflow prompts."""
        spec = load_yaml(self.workflow_yaml)
        disallowed_commands = [
            r"\bgit\s+commit\b",
            r"\bgit\s+add\b",
            r"\bgit\s+push\b",
            r"\bgit\s+checkout\b",
            r"\bgit\s+diff\b",
            r"\bgit\s+status\b",
            r"\bgit\s+rebase\b",
            r"\bgit\s+reset\b",
        ]

        for step in spec.get("steps", []):
            for field in ("prompt", "error_prompt", "stuck_prompt", "stuck_reason"):
                val = step.get(field, "")
                if isinstance(val, list):
                    text = " ".join(val)
                else:
                    text = str(val or "")
                for pattern in disallowed_commands:
                    self.assertIsNone(
                        re.search(pattern, text, re.IGNORECASE),
                        f"Found forbidden git command matching '{pattern}' in step {step.get('step')} field '{field}': {text}",
                    )

    def test_validate_commit_message_tags(self):
        """Validates that TAG= and CONV= are strictly rejected."""
        valid_msg = (
            "feat: Add new awesome feature\n\n"
            "This implements a clean feature.\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
        )
        self.assertEqual(validate_commit_message(valid_msg), [])

        # Banned TAG=
        for tag in ("TAG=agy", "Tag=agy", "tag=some_tag", "TAG = agy"):
            msg_with_tag = valid_msg + f"\n{tag}\n"
            errors = validate_commit_message(msg_with_tag)
            self.assertTrue(
                any("Tag=" in e for e in errors),
                f"Failed to reject tag variation: {tag}",
            )

        # Banned CONV=
        for conv in ("CONV=12345", "Conv=abc", "conv=xyz", "CONV = 123"):
            msg_with_conv = valid_msg + f"\n{conv}\n"
            errors = validate_commit_message(msg_with_conv)
            self.assertTrue(
                any("CONV=" in e for e in errors),
                f"Failed to reject conv variation: {conv}",
            )

    def test_validate_commit_message_file(self):
        """Tests validate_commit_message_file helper."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "commit_msg.txt"

            # Empty file
            file_path.write_text("", encoding="utf-8")
            errors = validate_commit_message_file(file_path)
            self.assertTrue(any("is empty" in e for e in errors))

            # Valid file
            valid_msg = "feat: Title\n\nBody.\n\nBug: 12345678\nTest: bazel test //...\n"
            file_path.write_text(valid_msg, encoding="utf-8")
            self.assertEqual(validate_commit_message_file(file_path), [])

            # Invalid file with Tag=
            file_path.write_text(valid_msg + "TAG=agy\n", encoding="utf-8")
            errors = validate_commit_message_file(file_path)
            self.assertTrue(any("Tag=" in e for e in errors))

    def test_verify_proposed_msg_script(self):
        """Tests verify_proposed_msg.py script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            msg_file = Path(tmpdir) / "proposed.txt"

            # Empty file -> fails
            msg_file.write_text("", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(msg_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("empty", res.stderr)

            # Valid proposed message -> passes
            msg_file.write_text("feat: Valid\n\nBody.\n\nBug: 1234\nTest: N/A\n", encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(msg_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("verified successfully", res.stdout)

    def test_workflow_lifecycle_in_git_repo(self):
        """Tests full 2-step create-commit lifecycle in a simulated Git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            # Initialize git repository
            subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True)
            subprocess.run(["git", "config", "user.email", "test@google.com"], cwd=str(repo_dir), check=True)

            # Initial commit
            dummy_file = repo_dir / "initial.txt"
            dummy_file.write_text("initial content", encoding="utf-8")
            subprocess.run(["git", "add", "initial.txt"], cwd=str(repo_dir), check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit\n\nBug: 1\nTest: N/A"], cwd=str(repo_dir), check=True)

            base_commit = get_head_commit_hash(cwd=str(repo_dir))
            self.assertNotEqual(base_commit, "EMPTY")

            # Initialize workflow session with ZERO arguments in repo_dir
            old_cwd = os.getcwd()
            try:
                os.chdir(str(repo_dir))
                state_id = init_workflow_session(self.workflow_yaml, [])
                self.assertIsNotNone(state_id)

                # Check that proposed message file was initialized
                state_file = Path(os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR", Path.home() / ".cache" / "emu-dev-cli" / "workflows" / "state")) / "create-commit" / f"STATE-{state_id}.yaml"
                state_data = load_state_yaml(state_file)
                proposed_file = Path(state_data["metadata"]["proposed-msg-file"])
                self.assertTrue(proposed_file.is_file())
                self.assertEqual(proposed_file.read_text(encoding="utf-8"), "")

                # First call from INIT: prints prompt and transitions to RUNNING
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "RUNNING")
                self.assertEqual(state_data["step"], 0)

                # Second call: proposed message file is empty -> verification fails
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                # Write valid proposed message to proposed_file
                commit_msg = (
                    "feat: Add new feature\n\n"
                    "Detailed description of feature.\n\n"
                    "Bug: 87654321\n"
                    "Test: bazel test //...\n"
                )
                proposed_file.write_text(commit_msg, encoding="utf-8")

                # Step 0 should now pass and advance to Step 1 (printing Step 1 prompt)
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["step"], 1)

                # Step 1: Commit not created yet -> verification fails
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                # Create commit in git matching proposed file
                file2 = repo_dir / "feature.txt"
                file2.write_text("new feature", encoding="utf-8")
                subprocess.run(["git", "add", "feature.txt"], cwd=str(repo_dir), check=True)
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(repo_dir), check=True)

                # Step 1 should now pass and complete the workflow
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "DONE")
            finally:
                os.chdir(old_cwd)

    def test_no_arguments_allowed(self):
        """Tests that create-commit does not accept any arguments on init."""
        with self.assertRaises(SystemExit):
            init_workflow_session(self.workflow_yaml, ["some_arg"])

    def test_init_fails_on_non_git_dir(self):
        """Tests that workflow init fails when passed a non-git directory."""
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
