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

"""Unit tests for the `review-duplication` workflow."""

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_EMU_DEV_CLI_DIR = os.path.dirname(_TESTS_DIR)
_WORKFLOWS_DIR = os.path.join(_EMU_DEV_CLI_DIR, "workflows")
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")
for p in (_SRC_DIR, _EMU_DEV_CLI_DIR, _WORKFLOWS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from commands.workflow import (
    find_workflow_main,
    find_workflow_target,
    list_available_workflows,
)
from workflows.core.yaml_runner import (
    execute_workflow_step,
    init_workflow_session,
    load_yaml,
)


class TestReviewDuplicationWorkflowIntegration(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("review-duplication")
        self.assertIsNotNone(target, "review-duplication workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("review-duplication", workflows)
        wf = workflows["review-duplication"]
        self.assertEqual(wf["name"], "review-duplication")
        self.assertEqual(wf["target_type"], "yaml")
        self.assertTrue(os.path.isfile(wf["target_path"]))

        target = find_workflow_target("review-duplication")
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "yaml")
        self.assertEqual(target[1], self.workflow_yaml)

    def test_yaml_specification(self):
        spec = load_yaml(self.workflow_yaml)
        self.assertEqual(spec.get("name"), "review-duplication")
        self.assertTrue(spec.get("description"))

        # Verify arguments
        arg_names = [arg["name"] for arg in spec.get("args", [])]
        self.assertIn("commit", arg_names)
        self.assertIn("repo", arg_names)

        # No verification scripts or report file generation
        self.assertNotIn("init", spec)

        steps = spec.get("steps", [])
        self.assertGreaterEqual(len(steps), 1)

        step0 = steps[0]
        self.assertEqual(step0.get("step"), 0)
        self.assertIn("remove duplicate code", step0.get("prompt", "").lower())
        self.assertIn("code reuse", step0.get("prompt", "").lower())
        self.assertIn("amend", step0.get("prompt", "").lower())

        # No validation / verifier command
        self.assertIsNone(step0.get("verifier_cmd"))

    def test_workflow_session_initialization_and_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = tmpdir
            try:
                state_id = init_workflow_session(
                    spec_path=self.workflow_yaml,
                    args_input=["--commit", "HEAD~1", "--repo", tmpdir],
                )
                self.assertIsNotNone(state_id)
                from workflows.core.yaml_runner import get_state_base_dir
                state_file = get_state_base_dir() / "review-duplication" / f"STATE-{state_id}.yaml"
                self.assertTrue(state_file.is_file())
                state = load_yaml(state_file)
                self.assertEqual(state.get("state"), "INIT")
                self.assertEqual(state.get("step"), 0)
                metadata = state.get("metadata", {})
                self.assertEqual(metadata.get("commit"), "HEAD~1")
                self.assertEqual(os.path.realpath(metadata.get("repo")), os.path.realpath(tmpdir))

                # Execute step and verify prompt is emitted without any validation failure
                f = io.StringIO()
                with patch("sys.stdout", f):
                    rc = execute_workflow_step(self.workflow_yaml, state_id)

                self.assertEqual(rc, 0)
                output = f.getvalue()
                self.assertIn("Please check commit 'HEAD~1'", output)
                self.assertIn("remove duplicate code", output.lower())

                # State should be DONE since no verifier_cmd is required
                updated_state = load_yaml(state_file)
                self.assertEqual(updated_state.get("state"), "DONE")
            finally:
                os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)


if __name__ == "__main__":
    unittest.main()
