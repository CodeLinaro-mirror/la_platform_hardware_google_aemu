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
    get_head_commit_hash,
    messages_match,
    validate_commit_message,
)
from workflows.core.yaml_runner import (
    init_workflow_session,
    load_yaml,
)


class TestCreateCommitWorkflowIntegration(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("create-commit")
        self.assertIsNotNone(target, "create-commit workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.workflow_dir = os.path.dirname(self.workflow_yaml)
        self.verifier_py = os.path.join(self.workflow_dir, "verify_commit.py")
        self.get_base_commit_py = os.path.join(self.workflow_dir, "get_base_commit.py")
        self.assertTrue(os.path.isfile(self.verifier_py))
        self.assertTrue(os.path.isfile(self.get_base_commit_py))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("create-commit", workflows)
        wf = workflows["create-commit"]
        self.assertEqual(wf["name"], "create-commit")
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

    def test_commit_verifier_validations(self):
        # Empty commit message
        self.assertEqual(validate_commit_message(""), ["Commit message is empty."])

        # Message with forbidden tags
        msg_with_tags = (
            "feat: Test title\n\n"
            "TAG=agy\n"
            "CONV=12345\n"
            "Bug: 12345678\n"
            "Test: Presubmit\n"
        )
        errors = validate_commit_message(msg_with_tags)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("Tag=" in e for e in errors))
        self.assertTrue(any("CONV=" in e for e in errors))

        # Missing Bug: line
        msg_missing_bug = "feat: Test title\n\nTest: Presubmit\n"
        errors = validate_commit_message(msg_missing_bug)
        self.assertTrue(any("Bug:" in e for e in errors))

        # Missing Test: line
        msg_missing_test = "feat: Test title\n\nBug: 12345678\n"
        errors = validate_commit_message(msg_missing_test)
        self.assertTrue(any("Test:" in e for e in errors))

        # Valid messages
        for bug_val in ("Bug: 12345678", "Bug: N/A", "Bug: b/12345678"):
            for test_val in ("Test: bazel test //...", "TEST: N/A", "Test: N/A"):
                valid_msg = f"feat: Test title\n\nDescription.\n\n{bug_val}\n{test_val}\n"
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


if __name__ == "__main__":
    unittest.main()
