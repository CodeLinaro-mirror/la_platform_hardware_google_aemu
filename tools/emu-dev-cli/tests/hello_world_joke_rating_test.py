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

from commands.workflow import find_workflow_target, list_available_workflows
from workflows.core.yaml_runner import load_state_yaml, load_yaml


class TestHelloWorldJokeRatingWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("hello-world-joke-rating")
        self.assertIsNotNone(target, "hello-world-joke-rating workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.yaml_runner_py = os.path.join(
            _EMU_DEV_CLI_DIR, "workflows", "core", "yaml_runner.py"
        )
        self.verifier_py = os.path.join(
            _WORKFLOWS_DIR, "hello_world_joke_rating", "verify_joke_rating.py"
        )
        self.assertTrue(os.path.isfile(self.verifier_py))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("hello-world-joke-rating", workflows)
        wf = workflows["hello-world-joke-rating"]
        self.assertEqual(wf["name"], "hello-world-joke-rating")
        self.assertEqual(wf["target_type"], "yaml")
        self.assertTrue(os.path.isfile(wf["target_path"]))

    def test_verifier_direct_validations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rf = Path(tmpdir) / "rating.md"

            # 1. Non-existent file
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("does not exist", res.stderr)

            # 2. Empty file
            rf.write_text("", encoding="utf-8")
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("is empty", res.stderr)

            # 3. Missing rating line
            rf.write_text("This joke is quite funny with good timing and punchline delivery.", encoding="utf-8")
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("missing a valid rating line", res.stderr)

            # 4. Out of range rating
            rf.write_text("Good joke with great delivery and witty punchline.\nRating: 15.0 / 10", encoding="utf-8")
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("out of range", res.stderr)

            # 5. Too brief summary (< 15 words)
            rf.write_text("Short review.\nRating: 8.5 / 10", encoding="utf-8")
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("too brief", res.stderr)

            # 6. Valid review and rating
            valid_review = (
                "The setup of the joke is very strong and original. On the positive side, the timing "
                "creates great suspense. However, on the negative side, the punchline is slightly predictable. "
                "Overall, a solid and amusing submission.\n\n"
                "Rating: 8.5 / 10\n"
            )
            rf.write_text(valid_review, encoding="utf-8")
            res = subprocess.run([sys.executable, self.verifier_py, str(rf)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertIn("Rating verified successfully: 8.5 / 10", res.stdout)

    def test_full_workflow_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            joke_file = Path(tmpdir) / "sample_joke.txt"
            joke_file.write_text("Why do programmers prefer dark mode? Because light attracts bugs!\n", encoding="utf-8")

            state_dir = Path(tmpdir) / "state"
            cache_dir = Path(tmpdir) / "cache"
            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(state_dir)
            env["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = str(cache_dir)

            # 1. Init
            init_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, "init", str(joke_file)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            m = re.search(r"--state=([a-zA-Z0-9]+)", init_res.stdout)
            self.assertIsNotNone(m)
            state_id = m.group(1)

            state_file = state_dir / "hello-world-joke-rating" / f"STATE-{state_id}.yaml"
            self.assertTrue(state_file.is_file())
            s0 = load_state_yaml(state_file)
            self.assertEqual(s0["state"], "INIT")
            self.assertIn("joke-file", s0["metadata"])
            self.assertIn("rating-file", s0["metadata"])
            rating_file = Path(s0["metadata"]["rating-file"])

            # 2. Step 0 Prompt
            step0_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please read the joke in", step0_res.stdout)
            self.assertIn(str(joke_file), step0_res.stdout)

            # 3. Step 0 Verify with missing file -> should fail retry
            fail_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(fail_res.returncode, 1)
            self.assertIn("Rating validation failed", fail_res.stderr)

            # 4. Write valid review and rating
            valid_review = (
                "The programmer joke presents a clever and humorous play on words. "
                "Pros: Highly relatable for software engineers and well structured. "
                "Cons: Slightly well-worn trope in developer circles. "
                "Overall, an entertaining and crisp joke.\n\n"
                "Rating: 9.0 / 10\n"
            )
            rating_file.parent.mkdir(parents=True, exist_ok=True)
            rating_file.write_text(valid_review, encoding="utf-8")

            # 5. Step 0 Verify -> advances to Step 1 (Completion)
            pass_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Joke rating verified and saved", pass_res.stdout)
            s_final = load_state_yaml(state_file)
            self.assertEqual(s_final["state"], "DONE")


if __name__ == "__main__":
    unittest.main()
