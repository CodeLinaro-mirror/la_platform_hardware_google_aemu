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

from commands.workflow import find_workflow_target, list_available_workflows
from workflows.core.yaml_runner import load_state_yaml


class TestHelloWorldJokePipelineWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("hello-world-joke-pipeline")
        self.assertIsNotNone(target, "hello-world-joke-pipeline workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.yaml_runner_py = os.path.join(
            _EMU_DEV_CLI_DIR, "workflows", "core", "yaml_runner.py"
        )

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("hello-world-joke-pipeline", workflows)
        wf = workflows["hello-world-joke-pipeline"]
        self.assertEqual(wf["name"], "hello-world-joke-pipeline")
        self.assertEqual(wf["target_type"], "yaml")

    def test_full_pipeline_lifecycle_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            cache_dir = Path(tmpdir) / "cache"
            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(state_dir)
            env["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = str(cache_dir)

            # 1. Initialize Pipeline Session
            init_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, "init"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            m = re.search(r"--state=([a-zA-Z0-9]+)", init_res.stdout)
            self.assertIsNotNone(m)
            pipeline_id = m.group(1)

            parent_state_file = state_dir / "hello-world-joke-pipeline" / f"STATE-{pipeline_id}.yaml"
            self.assertTrue(parent_state_file.is_file())

            # 2. First Run: Starts Step 0 (Child hello-world-example Step 0)
            res0 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            # Must prompt to write name into hello-file
            self.assertIn("Please write your name in the file", res0.stdout)
            # MUST rewrite continuation command to parent pipeline state ID
            self.assertIn(f'emu-dev-cli workflow hello-world-joke-pipeline --state={pipeline_id}', res0.stdout)

            # Load parent state and verify child tracking
            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 0)
            self.assertEqual(p_state["state"], "RUNNING")
            self.assertIn("_sub_workflow", p_state["metadata"])
            child1_id = p_state["metadata"]["_sub_workflow"]["state_id"]
            self.assertEqual(p_state["metadata"]["_sub_workflow"]["name"], "hello-world-example")

            # Locate child hello-file
            child1_state_file = state_dir / "hello-world-example" / f"STATE-{child1_id}.yaml"
            c1_state = load_state_yaml(child1_state_file)
            hello_file = Path(c1_state["metadata"]["hello-file"])
            hello_file.parent.mkdir(parents=True, exist_ok=True)

            # 3. Write Name to satisfy child Step 0
            hello_file.write_text("Name: Joshua Duong\n", encoding="utf-8")

            # 4. Advance Step: Child hello-world-example advances to Step 1 (Joke)
            res1 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Write your best two-sentence joke", res1.stdout)
            self.assertIn(f'emu-dev-cli workflow hello-world-joke-pipeline --state={pipeline_id}', res1.stdout)

            # 5. Append Joke to satisfy child Step 1
            hello_file.write_text(
                "Name: Joshua Duong\nJoke: Why do programmers prefer dark mode? Because light attracts bugs!\n",
                encoding="utf-8",
            )

            # 6. Advance Step: Child 1 finishes, parent extracts outputs and advances to Step 1 (hello-world-joke-rating)
            res2 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            # Prompt must now be for rating the joke
            self.assertIn("Please read the joke in", res2.stdout)
            self.assertIn("Write a review into", res2.stdout)
            self.assertIn(f'emu-dev-cli workflow hello-world-joke-pipeline --state={pipeline_id}', res2.stdout)

            # Parent state should now be on step 1 with joke_file output populated
            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 1)
            self.assertIn("hello-world-example", p_state["metadata"])
            self.assertIn("joke_file", p_state["metadata"]["hello-world-example"])
            self.assertEqual(p_state["metadata"]["hello-world-example"]["joke_file"], str(hello_file))

            # Check child 2 tracking
            self.assertIn("_sub_workflow", p_state["metadata"])
            child2_id = p_state["metadata"]["_sub_workflow"]["state_id"]
            self.assertEqual(p_state["metadata"]["_sub_workflow"]["name"], "hello-world-joke-rating")

            child2_state_file = state_dir / "hello-world-joke-rating" / f"STATE-{child2_id}.yaml"
            c2_state = load_state_yaml(child2_state_file)
            rating_file = Path(c2_state["metadata"]["rating-file"])
            rating_file.parent.mkdir(parents=True, exist_ok=True)

            # 7. Write review and rating to satisfy child 2 Step 0
            valid_review = (
                "The programmer joke is clever, memorable, and well paced for engineering teams. "
                "Pros: High relatability and clean punchline execution. "
                "Cons: Very well known trope in tech culture. "
                "Overall, a great lighthearted submission.\n\n"
                "Rating: 9.5 / 10\n"
            )
            rating_file.write_text(valid_review, encoding="utf-8")

            # 8. Advance Step: Child 2 completes -> Parent advances to Step 2 (Completion) -> Pipeline reaches DONE!
            res3 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Pipeline complete!", res3.stdout)
            self.assertIn(str(hello_file), res3.stdout)
            self.assertIn(str(rating_file), res3.stdout)

            # Verify final parent state
            final_p_state = load_state_yaml(parent_state_file)
            self.assertEqual(final_p_state["state"], "DONE")
            self.assertIn("hello-world-joke-rating", final_p_state["metadata"])
            self.assertIn("final_rating", final_p_state["metadata"]["hello-world-joke-rating"])
            self.assertEqual(final_p_state["metadata"]["hello-world-joke-rating"]["final_rating"], str(rating_file))
            self.assertNotIn("_sub_workflow", final_p_state["metadata"])
            self.assertEqual(len(final_p_state["metadata"]["_sub_workflow_history"]), 2)
            self.assertEqual(final_p_state["metadata"]["_sub_workflow_history"][0]["name"], "hello-world-example")
            self.assertEqual(final_p_state["metadata"]["_sub_workflow_history"][1]["name"], "hello-world-joke-rating")


if __name__ == "__main__":
    unittest.main()
