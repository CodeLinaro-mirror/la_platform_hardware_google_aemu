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
    extract_all_change_ids,
    extract_change_id,
    find_commit_by_change_id,
    get_head_commit_hash,
    is_ancestor_commit,
    messages_match,
    resolve_commit_hash,
    validate_commit_message,
    validate_commit_message_file,
)
from workflows.core.yaml_runner import (
    execute_workflow_step,
    init_workflow_session,
    load_state_yaml,
    load_yaml,
)


class TestAmendCommitWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("amend-commit")
        self.assertIsNotNone(target, "amend-commit workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.workflow_dir = os.path.dirname(self.workflow_yaml)
        self.verifier_py = os.path.join(self.workflow_dir, "verify_amend.py")
        self.verify_proposed_msg_py = os.path.join(self.workflow_dir, "verify_proposed_msg.py")
        self.setup_msg_files_py = os.path.join(self.workflow_dir, "setup_msg_files.py")
        self.extract_change_id_py = os.path.join(self.workflow_dir, "extract_change_id.py")
        self.get_base_commit_py = os.path.join(self.workflow_dir, "get_base_commit.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
        self.assertTrue(os.path.isfile(self.verify_proposed_msg_py))
        self.assertTrue(os.path.isfile(self.setup_msg_files_py))
        self.assertTrue(os.path.isfile(self.extract_change_id_py))
        self.assertTrue(os.path.isfile(self.get_base_commit_py))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("amend-commit", workflows)
        wf = workflows["amend-commit"]
        self.assertEqual(wf["name"], "amend-commit")
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

    def test_extract_change_id(self):
        """Tests Gerrit Change-Id extraction."""
        cid = "I9a93f0e515fc97398b896c52f6fe00d15d415bb3"
        msg = (
            f"feat: Add something\n\n"
            f"Bug: 12345678\n"
            f"Test: bazel test //...\n"
            f"Change-Id: {cid}\n"
        )
        self.assertEqual(extract_change_id(msg), cid)
        self.assertEqual(extract_all_change_ids(msg), [cid])
        self.assertIsNone(extract_change_id("feat: No change id\n\nBug: 1\nTest: N/A\n"))

        # Test extract_change_id.py script
        res = subprocess.run(
            [sys.executable, self.extract_change_id_py, "--msg", msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), cid)

    def test_validate_commit_message_change_id_preservation(self):
        """Tests Change-Id preservation validation."""
        cid1 = "I1111111111111111111111111111111111111111"
        cid2 = "I2222222222222222222222222222222222222222"

        msg_with_cid1 = (
            "feat: Awesome commit\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
            f"Change-Id: {cid1}\n"
        )
        msg_with_cid2 = (
            "feat: Awesome commit\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
            f"Change-Id: {cid2}\n"
        )
        msg_without_cid = (
            "feat: Awesome commit\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
        )

        # Expected cid1, provided cid1 -> passes
        self.assertEqual(validate_commit_message(msg_with_cid1, expected_change_id=cid1), [])

        # Expected cid1, provided cid2 -> fails with modified error
        errors = validate_commit_message(msg_with_cid2, expected_change_id=cid1)
        self.assertTrue(any("Change-Id must not be modified" in e for e in errors))

        # Expected cid1, no cid in message -> fails with missing error
        errors = validate_commit_message(msg_without_cid, expected_change_id=cid1)
        self.assertTrue(any("Change-Id line is missing" in e for e in errors))

        # Multiple Change-Id lines
        msg_duplicate_cid = (
            "feat: Awesome commit\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
            f"Change-Id: {cid1}\n"
            f"Change-Id: {cid1}\n"
        )
        errors = validate_commit_message(msg_duplicate_cid, expected_change_id=cid1)
        self.assertTrue(any("Multiple 'Change-Id' lines" in e for e in errors))

    def test_setup_msg_files_and_verify_proposed(self):
        """Tests setup_msg_files.py creates empty files and verify_proposed_msg validates both files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True)
            subprocess.run(["git", "config", "user.email", "test@google.com"], cwd=str(repo_dir), check=True)

            cid = "I9999999999999999999999999999999999999999"
            (repo_dir / "test.txt").write_text("hello", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
            orig_msg = f"feat: Base\n\nOriginal body.\n\nBug: 1234\nTest: N/A\nChange-Id: {cid}\n"
            subprocess.run(["git", "commit", "-m", orig_msg], cwd=str(repo_dir), check=True)

            cache_dir = Path(tmpdir) / "cache"
            current_file = cache_dir / "current.txt"
            proposed_file = cache_dir / "proposed.txt"

            # Run setup_msg_files.py
            res = subprocess.run(
                [sys.executable, self.setup_msg_files_py, str(current_file), str(proposed_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertTrue(current_file.is_file())
            self.assertTrue(proposed_file.is_file())
            self.assertEqual(current_file.read_text(encoding="utf-8"), "")
            self.assertEqual(proposed_file.read_text(encoding="utf-8"), "")

            # verify_proposed_msg fails if current_file is empty
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(proposed_file), "--current-file", str(current_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("empty", res.stderr)

            # Populate current_file with original commit message
            current_file.write_text(orig_msg, encoding="utf-8")

            # verify_proposed_msg fails on empty proposed file
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(proposed_file), "--current-file", str(current_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("empty", res.stderr)

            # verify_proposed_msg passes on valid proposed message with same Change-Id
            new_msg = f"feat: Updated\n\nNew body.\n\nBug: 5678\nTest: Presubmit\nChange-Id: {cid}\n"
            proposed_file.write_text(new_msg, encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(proposed_file), "--current-file", str(current_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("verified successfully", res.stdout)

    def test_verify_amend_script_direct(self):
        """Tests verify_amend.py script directly with --msg and arguments."""
        cid = "I1234567890abcdef1234567890abcdef12345678"
        valid_msg = (
            "emu-dev-cli: Fix amend bug\n\n"
            "Bug: 98765432\n"
            "Test: bazel test //...\n"
            f"Change-Id: {cid}\n"
        )
        res = subprocess.run(
            [sys.executable, self.verifier_py, "some_base_commit", cid, "--msg", valid_msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Commit amendment verification passed", res.stdout)

        # Mismatched Change-Id
        other_cid = "Iabcdef1234567890abcdef1234567890abcdef12"
        res = subprocess.run(
            [sys.executable, self.verifier_py, "some_base_commit", other_cid, "--msg", valid_msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("Change-Id must not be modified", res.stderr)

    def test_workflow_lifecycle_top_of_tree(self):
        """Tests amend-commit 2-step lifecycle for top-of-tree (HEAD) in a git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True)
            subprocess.run(["git", "config", "user.email", "test@google.com"], cwd=str(repo_dir), check=True)

            cid = "Iabcdef1234567890abcdef1234567890abcdef12"
            file1 = repo_dir / "work.txt"
            file1.write_text("v1 content", encoding="utf-8")
            subprocess.run(["git", "add", "work.txt"], cwd=str(repo_dir), check=True)
            commit_msg_v1 = (
                "feat: Work in progress\n\n"
                "Initial implementation.\n\n"
                "Bug: 12345678\n"
                "Test: Presubmit\n"
                f"Change-Id: {cid}\n"
            )
            subprocess.run(["git", "commit", "-m", commit_msg_v1], cwd=str(repo_dir), check=True)

            base_commit = get_head_commit_hash(cwd=str(repo_dir))
            self.assertNotEqual(base_commit, "EMPTY")

            # Initialize workflow session with ZERO arguments in repo_dir
            old_cwd = os.getcwd()
            try:
                os.chdir(str(repo_dir))
                state_id = init_workflow_session(self.workflow_yaml, [])
                self.assertIsNotNone(state_id)

                # Check that current-msg-file and proposed-msg-file were created empty
                state_file = Path(os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR", Path.home() / ".cache" / "emu-dev-cli" / "workflows" / "state")) / "amend-commit" / f"STATE-{state_id}.yaml"
                state_data = load_state_yaml(state_file)
                current_file = Path(state_data["metadata"]["current-msg-file"])
                proposed_file = Path(state_data["metadata"]["proposed-msg-file"])
                self.assertTrue(current_file.is_file())
                self.assertTrue(proposed_file.is_file())
                self.assertEqual(current_file.read_text(encoding="utf-8"), "")
                self.assertEqual(proposed_file.read_text(encoding="utf-8"), "")

                # First call from INIT: prints prompt and transitions to RUNNING
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "RUNNING")
                self.assertEqual(state_data["step"], 0)

                # Second call: current file is empty -> verification fails
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                # Agent populates current commit message into current_file
                current_file.write_text(commit_msg_v1, encoding="utf-8")

                # Third call: proposed file is still empty -> verification fails
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                # Agent writes valid amended message to proposed_file
                commit_msg_amended = (
                    "feat: Work completed\n\n"
                    "Final implementation.\n\n"
                    "Bug: 12345678\n"
                    "Test: Presubmit\n"
                    f"Change-Id: {cid}\n"
                )
                proposed_file.write_text(commit_msg_amended, encoding="utf-8")

                # Step 0 passes and advances to Step 1 (Amend Commit)
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["step"], 1)

                # Step 1: Commit not amended yet in Git -> verification fails
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 1)

                # Amend the commit in git matching proposed_file
                (repo_dir / "work.txt").write_text("v2 amended content", encoding="utf-8")
                subprocess.run(["git", "add", "work.txt"], cwd=str(repo_dir), check=True)
                subprocess.run(["git", "commit", "--amend", "-m", commit_msg_amended], cwd=str(repo_dir), check=True)

                # Step 1 passes and workflow marks DONE
                self.assertEqual(execute_workflow_step(self.workflow_yaml, state_id), 0)
                state_data = load_state_yaml(state_file)
                self.assertEqual(state_data["state"], "DONE")
            finally:
                os.chdir(old_cwd)

    def test_no_arguments_allowed(self):
        """Tests that amend-commit does not accept any arguments on init."""
        with self.assertRaises(SystemExit):
            init_workflow_session(self.workflow_yaml, ["some_arg"])

    def test_init_fails_on_non_git_dir(self):
        """Tests that helper fails when passed a non-git directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                [sys.executable, self.get_base_commit_py, "HEAD", "--cwd", tmpdir],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("not a Git repository", res.stderr)

    def test_step_0_prompt_asks_agent_to_fill_current_msg_file(self):
        """Tests that Step 0 outputs the prompt asking to fill current-msg-file and proposed-msg-file."""
        res = subprocess.run(
            ["emu-dev-cli", "workflow", "amend-commit", "init"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        match = re.search(r"--state=([0-9a-fA-F]+)", res.stdout)
        self.assertIsNotNone(match)
        state_id = match.group(1)

        res2 = subprocess.run(
            ["emu-dev-cli", "workflow", "amend-commit", f"--state={state_id}"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res2.returncode, 0)
        self.assertIn("current_commit_msg_", res2.stdout)
        self.assertIn("Please copy the current commit message", res2.stdout)


if __name__ == "__main__":
    unittest.main()
