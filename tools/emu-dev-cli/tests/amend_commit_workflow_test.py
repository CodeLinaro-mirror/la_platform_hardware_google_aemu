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
    validate_commit_message,
)
from workflows.core.yaml_runner import (
    init_workflow_session,
    load_yaml,
)


class TestAmendCommitWorkflowIntegration(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("amend-commit")
        self.assertIsNotNone(target, "amend-commit workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.workflow_dir = os.path.dirname(self.workflow_yaml)
        self.verifier_py = os.path.join(self.workflow_dir, "verify_amend.py")
        self.extract_change_id_py = os.path.join(self.workflow_dir, "extract_change_id.py")
        self.get_base_commit_py = os.path.join(self.workflow_dir, "get_base_commit.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
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

    def test_change_id_extraction_and_validation(self):
        cid = "Iabcdef0123456789abcdef0123456789abcdef01"
        valid_msg = (
            "feat: Awesome commit\n\n"
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
        msg_no_cid = "feat: Commit\n\nBug: 1\nTest: N/A\n"
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
            "Bug: 12345678\n"
            "Test: Presubmit\n"
            f"Change-Id: {cid}\n"
        )
        amended_msg = (
            "feat: Amended commit\n\n"
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


if __name__ == "__main__":
    unittest.main()
