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
"""Unit tests for polymorphic WorkflowStep classes (State + Composite patterns)."""

from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflows.core.steps import (
    AtomicVerifierStep,
    HumanVerifyStep,
    LoopStep,
    PassiveStep,
    StepExecutionContext,
    SubWorkflowStep,
    WorkflowStepFactory,
)
from workflows.core.yaml_runner import (
    WorkflowRecursionError,
    WorkflowTargetNotFoundError,
    approve_workflow_session,
    execute_workflow_step,
    init_workflow_session,
    load_state_yaml,
    save_state_yaml,
)


class WorkflowStepFactoryTest(unittest.TestCase):
    """Tests that WorkflowStepFactory selects the right polymorphic step class."""

    def test_factory_creates_expected_step_types(self):
        self.assertIsInstance(
            WorkflowStepFactory.create_step({"human_verify": True, "prompt": "Review"}),
            HumanVerifyStep,
        )
        self.assertIsInstance(
            WorkflowStepFactory.create_step({"workflow": "child-wf"}),
            SubWorkflowStep,
        )
        self.assertIsInstance(
            WorkflowStepFactory.create_step({"prompt": "Done!"}),
            PassiveStep,
        )
        self.assertIsInstance(
            WorkflowStepFactory.create_step(
                {"prompt": "Loop", "verifier_cmd": "true", "loop_to_step": 0}
            ),
            LoopStep,
        )
        step = WorkflowStepFactory.create_step(
            {"prompt": "Verify", "verifier_cmd": "true"}
        )
        self.assertIsInstance(step, AtomicVerifierStep)
        self.assertNotIsInstance(step, LoopStep)


class WorkflowStepsExecutionTest(unittest.TestCase):
    """Tests state transitions across AtomicVerifierStep, LoopStep, HumanVerifyStep, SubWorkflowStep, and PassiveStep."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
        os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(self.base_path / "state")

    def tearDown(self):
        if self.old_env is not None:
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = self.old_env
        else:
            os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)
        self.tmp_dir.cleanup()

    def test_atomic_human_verify_and_passive_steps_with_unicode(self):
        wf_file = self.base_path / "unicode_wf.yaml"
        wf_file.write_text(
            """
name: unicode-wf
steps:
  - name: step0
    prompt: "Step 0 Unicode 测试_🚀_é"
    verifier_cmd: ["python3", "-c", "import sys; sys.exit(0)"]
  - name: step1
    human_verify: true
    prompt: "Approve Unicode barrier 测试_🚀_é"
  - name: step2
    prompt: "Final passive completion 测试_🚀_é"
""",
            encoding="utf-8",
        )
        state_id = init_workflow_session(wf_file, {})

        # 1. Initial call transitions INIT -> RUNNING and outputs step 0 prompt
        out0 = StringIO()
        with patch("sys.stdout", out0):
            rc0 = execute_workflow_step(wf_file, state_id)
        self.assertEqual(rc0, 0)
        self.assertIn("Step 0 Unicode 测试_🚀_é", out0.getvalue())

        # 2. Second call runs verifier_cmd (passes) and enters HumanVerifyStep (WAITING_HUMAN)
        out1 = StringIO()
        with patch("sys.stdout", out1):
            rc1 = execute_workflow_step(wf_file, state_id)
        self.assertEqual(rc1, 0)
        self.assertIn("HUMAN VERIFICATION REQUIRED", out1.getvalue())
        state_file = self.base_path / "state" / "unicode-wf" / f"STATE-{state_id}.yaml"
        self.assertEqual(load_state_yaml(state_file)["state"], "WAITING_HUMAN")

        # 3. Approve session and advance into PassiveStep -> DONE
        ok, _ = approve_workflow_session(
            state_id=state_id,
            workflow_name="unicode-wf",
            feedback="Looks great 🎃",
            state_base_dir=self.base_path / "state",
        )
        self.assertTrue(ok)
        out2 = StringIO()
        with patch("sys.stdout", out2):
            rc2 = execute_workflow_step(wf_file, state_id)
        self.assertEqual(rc2, 0)
        self.assertIn("Final passive completion 测试_🚀_é", out2.getvalue())
        self.assertEqual(load_state_yaml(state_file)["state"], "DONE")

    def test_loop_step_iterates_and_bounds_max_iterations(self):
        wf_file = self.base_path / "loop_wf.yaml"
        wf_file.write_text(
            """
name: loop-wf
steps:
  - name: body
    prompt: "Iteration {iteration}"
    verifier_cmd: ["python3", "-c", "import sys; sys.exit(0)"]
  - name: check_loop
    prompt: "Check loop"
    verifier_cmd: ["python3", "-c", "import sys; sys.exit(2)"]
    loop_to_step: 0
    loop_exit_code: 2
    max_iterations: 2
    loop_prompt: "Looping back!"
""",
            encoding="utf-8",
        )
        state_id = init_workflow_session(wf_file, {})
        state_file = self.base_path / "state" / "loop-wf" / f"STATE-{state_id}.yaml"

        # INIT -> RUNNING step 0
        with patch("sys.stdout", StringIO()):
            execute_workflow_step(wf_file, state_id)
        # Step 0 passes -> advances to step 1
        with patch("sys.stdout", StringIO()):
            execute_workflow_step(wf_file, state_id)
        self.assertEqual(load_state_yaml(state_file)["step"], 1)

        # Step 1 returns 2 -> loops back to step 0 with iteration=1
        out_loop = StringIO()
        with patch("sys.stdout", out_loop):
            rc = execute_workflow_step(wf_file, state_id)
        self.assertEqual(rc, 0)
        self.assertIn("Looping back!", out_loop.getvalue())
        st = load_state_yaml(state_file)
        self.assertEqual(st["step"], 0)
        self.assertEqual(st["iteration"], 1)

        # Advance to step 1 again; next loop hits max_iterations=2 -> STUCK
        with patch("sys.stdout", StringIO()):
            execute_workflow_step(wf_file, state_id)
        with patch("sys.stderr", StringIO()):
            rc_stuck = execute_workflow_step(wf_file, state_id)
        self.assertEqual(rc_stuck, 1)
        self.assertEqual(load_state_yaml(state_file)["state"], "STUCK")

    def test_sub_workflow_step_detects_missing_target_and_recursion(self):
        wf_file = self.base_path / "parent_wf.yaml"
        wf_file.write_text(
            """
name: parent-wf
steps:
  - workflow: nonexistent-child-wf
""",
            encoding="utf-8",
        )
        state_id = init_workflow_session(wf_file, {})
        with self.assertRaises(WorkflowTargetNotFoundError):
            execute_workflow_step(wf_file, state_id)

        state_file = self.base_path / "state" / "parent-wf" / f"STATE-{state_id}.yaml"
        st = load_state_yaml(state_file)
        st["metadata"]["_call_stack"] = ["a", "b", "c", "d", "e"]
        save_state_yaml(state_file, st)
        with self.assertRaises(WorkflowRecursionError):
            execute_workflow_step(wf_file, state_id)


if __name__ == "__main__":
    unittest.main()
