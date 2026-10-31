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
from testlib.workflows import WorkflowTestCase
from workflows.common.git_commit import (
    extract_change_id,
    get_head_commit_hash,
    validate_commit_message,
)
from workflows.core.yaml_runner import (
    init_workflow_session,
    load_yaml,
)


class TestAmendCommitWorkflowIntegration(WorkflowTestCase):

    def setUp(self):
        self.setup_workflow("amend-commit")
        self.verifier_py = os.path.join(self.workflow_dir, "verify_amend.py")
        self.verify_proposed_msg_py = os.path.join(self.workflow_dir, "verify_proposed_msg.py")
        self.setup_msg_files_py = os.path.join(self.workflow_dir, "setup_msg_files.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
        self.assertTrue(os.path.isfile(self.verify_proposed_msg_py))
        self.assertTrue(os.path.isfile(self.setup_msg_files_py))

    def test_workflow_discovery(self):
        self.assert_workflow_discovery("amend-commit")

    def test_no_git_commands_in_instructions(self):
        self.assert_no_git_commands_in_instructions()

    def test_semantic_commit_rules_in_workflow_prompt(self):
        self.assert_semantic_commit_rules_in_prompt()

    def test_change_id_extraction_and_validation(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        valid_msg = (
            "feat: Awesome commit\n\n"
            "Explain what and why.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        self.assertEqual(extract_change_id(valid_msg), cid)
        self.assertEqual(validate_commit_message(valid_msg, expected_change_id=cid), [])

        # Change-Id changed
        different_cid = "I0000000000000000000000000000000000000000"
        errors = validate_commit_message(valid_msg, expected_change_id=different_cid)
        self.assertTrue(any("Change-Id must not be modified" in e for e in errors))

        # Change-Id missing
        msg_no_cid = "feat: Commit\n\nExplain what and why.\n\nBug: 1\nTest: N/A\n"
        errors = validate_commit_message(msg_no_cid, expected_change_id=cid)
        self.assertTrue(any("Change-Id line is missing" in e for e in errors))

        # Multiple Change-Id lines
        msg_multi_cid = valid_msg + f"Change-Id: {cid}\n"
        errors = validate_commit_message(msg_multi_cid, expected_change_id=cid)
        self.assertTrue(any("Multiple 'Change-Id' lines" in e for e in errors))

    def test_verify_amend_cli(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        valid_msg = (
            "feat: Amended commit\n\n"
            "Explain what and why.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        res = subprocess.run(
            [sys.executable, self.verifier_py, "dummy_base", cid, "--msg", valid_msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Commit amendment verification passed", res.stdout)

        # CLI rejection on multiple Change-Id lines
        multi_msg = valid_msg + f"Change-Id: {cid}\n"
        res = subprocess.run(
            [sys.executable, self.verifier_py, "dummy_base", cid, "--commit", "HEAD~1", "--msg", multi_msg],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("Multiple 'Change-Id' lines found", res.stderr)

    def test_verify_amend_proposed_file_cli(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        valid_msg = (
            "feat: Amended commit\n\n"
            "Explain what and why.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            proposed_file = Path(tmpdir) / "proposed.txt"
            proposed_file.write_text(valid_msg, encoding="utf-8")
            res = subprocess.run(
                [sys.executable, self.verifier_py, "dummy_base", cid, "--proposed-file", str(proposed_file), "--msg", valid_msg],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("Commit amendment verification passed", res.stdout)

    def test_verify_amend_current_file_cli(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        current_msg = (
            "feat: Original commit\n\n"
            "Original body.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        amended_msg = (
            "feat: Amended commit\n\n"
            "Amended body.\n\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = Path(tmpdir) / "current.txt"
            proposed_file = Path(tmpdir) / "proposed.txt"
            current_file.write_text(current_msg, encoding="utf-8")
            proposed_file.write_text(amended_msg, encoding="utf-8")

            res = subprocess.run(
                [sys.executable, self.verifier_py, "--current-file", str(current_file), "--proposed-file", str(proposed_file), "--msg", amended_msg],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("Commit amendment verification passed", res.stdout)

    def test_setup_msg_files_and_verify_proposed(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        current_msg = (
            "feat: Original commit\n\n"
            "Original body.\n\n"
            "Bug: 12345678\n"
            "Test: N/A\n"
            f"Change-Id: {cid}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = Path(tmpdir) / "current.txt"
            proposed_file = Path(tmpdir) / "proposed.txt"

            setup_res = subprocess.run(
                [sys.executable, self.setup_msg_files_py, str(current_file), str(proposed_file)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(setup_res.stdout.strip(), "OK")
            self.assertTrue(current_file.is_file())
            self.assertTrue(proposed_file.is_file())

            current_file.write_text(current_msg, encoding="utf-8")
            proposed_file.write_text(current_msg, encoding="utf-8")

            v_res = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(proposed_file), cid, "--current-file", str(current_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(v_res.returncode, 0)

            bad_cid_msg = current_msg.replace(cid, "I0000000000000000000000000000000000000000")
            proposed_file.write_text(bad_cid_msg, encoding="utf-8")
            v_res2 = subprocess.run(
                [sys.executable, self.verify_proposed_msg_py, str(proposed_file), cid, "--current-file", str(current_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(v_res2.returncode, 1)
            self.assertIn("Change-Id must not be modified", v_res2.stderr)


if __name__ == "__main__":
    unittest.main()
