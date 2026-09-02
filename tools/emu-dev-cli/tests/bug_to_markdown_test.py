"""Unit and integration tests for bug_to_markdown workflow."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
import yaml

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
ROOT_DIR = os.path.dirname(SRC_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from commands.workflow import (
    find_workflow_main,
    list_available_workflows,
)


class BugToMarkdownWorkflowTest(unittest.TestCase):

    def setUp(self):
        self.workflow_dir = os.path.join(
            ROOT_DIR, "workflows", "bug_to_markdown"
        )
        self.workflow_yaml = os.path.join(self.workflow_dir, "workflow.yaml")
        self.verifier_py = os.path.join(self.workflow_dir, "verify_bug_markdown.py")
        self.yaml_runner_py = os.path.join(
            ROOT_DIR, "workflows", "core", "yaml_runner.py"
        )

    def test_workflow_discovery_and_exact_matching(self):
        workflows = list_available_workflows()
        self.assertIn("bug-to-markdown", workflows)
        self.assertNotIn("bug_to_markdown", workflows)
        self.assertEqual(workflows["bug-to-markdown"]["name"], "bug-to-markdown")
        self.assertTrue(os.path.isfile(workflows["bug-to-markdown"]["target_path"]))

        # Exact match succeeds
        main_path = find_workflow_main("bug-to-markdown")
        self.assertIsNotNone(main_path)
        self.assertTrue(os.path.isfile(main_path))

        # Variations without exact match return None
        self.assertIsNone(find_workflow_main("bug_to_markdown"))
        self.assertIsNone(find_workflow_main("bug2md"))
        self.assertIsNone(find_workflow_main("bug-markdown"))

    def test_workflow_spec_validity(self):
        with open(self.workflow_yaml, "r") as f:
            spec = yaml.safe_load(f)

        self.assertEqual(spec.get("name"), "bug-to-markdown")
        self.assertIn("args", spec)
        self.assertEqual(spec["args"][0]["name"], "bug-number")
        steps = spec.get("steps", [])
        self.assertGreaterEqual(len(steps), 2)
        self.assertEqual(steps[0]["step"], 0)
        self.assertIn("verify_bug_markdown.py", steps[0]["verifier_cmd"])
        self.assertIsNone(steps[1]["verifier_cmd"])

    def test_verifier_valid_markdown(self):
        sample = (
            "# Bug 41853198\n"
            "- Bug number: 41853198\n"
            "- Status: In Progress\n"
            "- Assignee: joshuaduong\n"
            "- Problem: Insecure state storage in shared /tmp directory\n"
            "- Proposed Fix: Migrate state directory to ~/.cache\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            fpath = os.path.join(tmp_dir, "b41853198.md")
            with open(fpath, "w") as f:
                f.write(sample)

            res = subprocess.run(
                [sys.executable, self.verifier_py, tmp_dir, "41853198"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("verified with all required fields", res.stdout)

    def test_verifier_bug_mismatch(self):
        sample = (
            "# Bug 41853198\n"
            "- Bug number: 41853198\n"
            "- Status: In Progress\n"
            "- Assignee: joshuaduong\n"
            "- Problem: Insecure state storage\n"
            "- Proposed Fix: Migrate state directory\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            fpath = os.path.join(tmp_dir, "b41853198.md")
            with open(fpath, "w") as f:
                f.write(sample)

            # Specifying a different expected bug should fail
            res = subprocess.run(
                [sys.executable, self.verifier_py, tmp_dir, "99999999"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("b99999999.md' not found", res.stderr)

    def test_verifier_missing_fields_reporting(self):
        incomplete = (
            "# Incomplete Bug\n"
            "- Bug number: 12345\n"
            "- Status: Assigned\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            fpath = os.path.join(tmp_dir, "b12345.md")
            with open(fpath, "w") as f:
                f.write(incomplete)

            res = subprocess.run(
                [sys.executable, self.verifier_py, tmp_dir, "12345"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("missing or empty required field(s)", res.stderr)
            self.assertIn("Assignee", res.stderr)
            self.assertIn("Problem", res.stderr)
            self.assertIn("Proposed Fix", res.stderr)

    def test_verifier_directory_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = subprocess.run(
                [sys.executable, self.verifier_py, tmp_dir],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("No 'b<bug number>.md' file found", res.stderr)

    def test_full_workflow_execution_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_state_dir:
            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = tmp_state_dir

            # 1. No arguments prints help
            help_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("usage:", help_res.stdout.lower())
            self.assertIn("init", help_res.stdout.lower())

            # 2. Init without bug argument fails
            init_fail_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, "init"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(init_fail_res.returncode, 0)
            self.assertIn("required: bug-number", init_fail_res.stderr)

            # 3. Initialize session with bug argument
            init_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, "init", "b/987654"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            match = re.search(r"--state=([a-zA-Z0-9]+)", init_res.stdout)
            self.assertIsNotNone(match, "State ID was not returned in init output")
            state_id = match.group(1)

            state_file = os.path.join(tmp_state_dir, "bug-to-markdown", f"STATE-{state_id}.yaml")
            self.assertTrue(os.path.isfile(state_file))

            with open(state_file, "r") as f:
                state_data = yaml.safe_load(f)
            self.assertEqual(state_data["metadata"]["bug-number"], "987654")
            self.assertNotIn("bug", state_data["metadata"])
            self.assertNotIn("bug_id", state_data["metadata"])
            cache_dir = state_data["metadata"]["cache-dir"]
            self.assertTrue(os.path.isdir(cache_dir))

            # 4. Step 0 instruction prompt
            step0_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please collect the bug information for bug b/987654", step0_res.stdout)
            self.assertIn("b987654.md", step0_res.stdout)
            self.assertIn("Bug number: 987654", step0_res.stdout)
            self.assertIn("Proposed Fix:", step0_res.stdout)

            # 5. Failed verification attempt increments retries
            fail_res = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(fail_res.returncode, 1)
            self.assertIn("attempt 1 of 3", fail_res.stderr)

            # 6. Write valid markdown file and verify completion
            test_file = os.path.join(cache_dir, "b987654.md")
            with open(test_file, "w") as f:
                f.write(
                    "# Bug 987654\n"
                    "- Bug number: 987654\n"
                    "- Status: Verified\n"
                    "- Assignee: None\n"
                    "- Problem: Test case verification\n"
                    "- Proposed Fix: N/A\n"
                )

            try:
                pass_res = subprocess.run(
                    [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={state_id}"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )
                self.assertIn("successfully verified", pass_res.stdout)

                with open(state_file, "r") as f:
                    final_data = yaml.safe_load(f)
                self.assertEqual(final_data["state"], "DONE")
            finally:
                if os.path.isfile(test_file):
                    os.remove(test_file)


if __name__ == "__main__":
    unittest.main()
