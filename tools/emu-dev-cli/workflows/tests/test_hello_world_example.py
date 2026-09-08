"""Unit tests for Hello World Example workflow."""

import yaml
import os
import re
import subprocess
import sys
import unittest

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_EMU_DEV_CLI_DIR = os.path.abspath(os.path.join(_CURRENT_DIR, "../.."))
if _EMU_DEV_CLI_DIR not in sys.path:
    sys.path.insert(0, _EMU_DEV_CLI_DIR)
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.workflow import find_workflow_target, list_available_workflows


class TestHelloWorldExampleWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("hello-world-example")
        self.assertIsNotNone(target, "hello-world-example workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.yaml_runner_py = os.path.join(
            _EMU_DEV_CLI_DIR, "workflows", "core", "yaml_runner.py"
        )
        self.assertTrue(os.path.isfile(self.yaml_runner_py))
        cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        self.state_dir = os.path.join(cache_home, "emu-dev-cli", "workflows", "state", "hello-world-example")

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("hello-world-example", workflows)
        wf = workflows["hello-world-example"]
        self.assertEqual(wf["name"], "hello-world-example")
        self.assertEqual(wf["target_type"], "yaml")
        self.assertTrue(os.path.isfile(wf["target_path"]))
        self.assertTrue(wf["target_path"].endswith("workflow.yaml"))
        self.assertIn("example workflow", wf["description"].lower())

    def test_complete_step_progression_and_retries(self):
        # 0. Running with no arguments prints help
        help_res = subprocess.run(
            [sys.executable, self.yaml_runner_py, self.workflow_yaml],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("usage:", help_res.stdout.lower())
        self.assertIn("init", help_res.stdout.lower())

        # 1. Initialize workflow via 'init' subcommand
        init_res = subprocess.run(
            [sys.executable, self.yaml_runner_py, self.workflow_yaml, "init"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = init_res.stdout.strip()
        self.assertIn("Please run 'emu-dev-cli workflow hello-world-example --state=", output)

        match = re.search(r"--state=([a-zA-Z0-9]+)", output)
        self.assertIsNotNone(match, "State ID should be printed in initialization output")
        state_id = match.group(1)

        state_file = os.path.join(self.state_dir, f"STATE-{state_id}.yaml")
        self.assertTrue(os.path.isfile(state_file), f"State file {state_file} should exist")

        with open(state_file, "r") as f:
            state_data = yaml.safe_load(f)
        self.assertEqual(state_data["state"], "INIT")
        self.assertEqual(state_data["step"], 0)
        self.assertEqual(state_data["retries"], 0)
        self.assertIn("hello-file", state_data["metadata"])

        hello_file = state_data["metadata"]["hello-file"]
        self.assertTrue(os.path.isfile(hello_file), f"Hello file {hello_file} should be created")

        try:
            # 2. First call to step 0
            step0_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
            )
            step0_out = step0_res.stdout.strip()
            self.assertIn(f"Created {hello_file}", step0_out)
            self.assertIn("Please write your name", step0_out)
            self.assertIn(f'and run "emu-dev-cli workflow hello-world-example --state={state_id}" for next steps.', step0_out)

            with open(state_file, "r") as f:
                s0_data = yaml.safe_load(f)
            self.assertEqual(s0_data["state"], "RUNNING")
            self.assertEqual(s0_data["step"], 0)
            self.assertEqual(s0_data["retries"], 0)

            # 3. Fail verification (retry 1)
            fail1_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(fail1_res.returncode, 0)
            self.assertIn("attempt 1 of 3", fail1_res.stderr)
            self.assertIn(f'and run "emu-dev-cli workflow hello-world-example --state={state_id}" for next steps.', fail1_res.stderr)

            with open(state_file, "r") as f:
                s1_data = yaml.safe_load(f)
            self.assertEqual(s1_data["state"], "RUNNING")
            self.assertEqual(s1_data["step"], 0)
            self.assertEqual(s1_data["retries"], 1)

            # 4. Fail verification (retry 2)
            fail2_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(fail2_res.returncode, 0)
            self.assertIn("attempt 2 of 3", fail2_res.stderr)

            with open(state_file, "r") as f:
                s2_data = yaml.safe_load(f)
            self.assertEqual(s2_data["retries"], 2)

            # 5. Fail verification (retry 3 -> STUCK)
            fail3_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(fail3_res.returncode, 0)
            self.assertIn("Workflow is now STUCK (retries reached 3)", fail3_res.stderr)
            self.assertIn(f'and run "emu-dev-cli workflow hello-world-example --state={state_id}" to resume.', fail3_res.stderr)

            with open(state_file, "r") as f:
                stuck_data = yaml.safe_load(f)
            self.assertEqual(stuck_data["state"], "STUCK")
            self.assertEqual(stuck_data["retries"], 3)
            self.assertIn("Failed to find 'Name: <your name>'", stuck_data["stuck_reason"])

            # 6. Resolve Step 0
            with open(hello_file, "w") as f:
                f.write("Name: Test Runner\n")

            step1_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
            )
            step1_out = step1_res.stdout.strip()
            self.assertIn('Below your name on a new line, Write your best two-sentence joke as "Joke: <your-joke>"', step1_out)
            self.assertIn(f'and run "emu-dev-cli workflow hello-world-example --state={state_id}" for next steps.', step1_out)

            with open(state_file, "r") as f:
                s3_data = yaml.safe_load(f)
            self.assertEqual(s3_data["state"], "RUNNING")
            self.assertEqual(s3_data["step"], 1)
            self.assertEqual(s3_data["retries"], 0)
            self.assertEqual(s3_data["stuck_reason"], "")

            # 7. Fail step 1 verification
            fail_step1 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(fail_step1.returncode, 0)
            self.assertIn("attempt 1 of 3", fail_step1.stderr)

            # 8. Resolve Step 1
            with open(hello_file, "a") as f:
                f.write("Joke: There are 10 types of people in the world: those who understand binary, and those who don't.\n")

            step2_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
            )
            step2_out = step2_res.stdout.strip()
            self.assertEqual(step2_out, "Great joke! You are done! I will share this with the world!")

            with open(state_file, "r") as f:
                final_data = yaml.safe_load(f)
            self.assertEqual(final_data["state"], "DONE")
            self.assertEqual(final_data["step"], 2)

        finally:
            if os.path.isfile(state_file):
                os.remove(state_file)
            if os.path.isfile(hello_file):
                os.remove(hello_file)

    def test_invalid_state_id_error(self):
        res = subprocess.run(
            [sys.executable, self.yaml_runner_py, self.workflow_yaml, "--state=invalid_id_99999"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("does not exist", res.stderr)


if __name__ == "__main__":
    unittest.main()
