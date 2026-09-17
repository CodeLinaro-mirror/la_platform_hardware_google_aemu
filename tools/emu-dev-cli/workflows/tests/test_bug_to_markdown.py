"""Unit tests for bug_to_markdown workflow definition."""

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import yaml

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent
SRC_DIR = TOOLS_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from commands.workflow import list_available_workflows


class TestBugToMarkdownWorkflow(unittest.TestCase):

    def setUp(self):
        self.workflow_dir = TOOLS_DIR / "workflows" / "bug_to_markdown"
        self.workflow_yaml = self.workflow_dir / "workflow.yaml"
        self.verifier_py = self.workflow_dir / "verify_bug_markdown.py"
        self.yaml_runner_py = TOOLS_DIR / "workflows" / "core" / "yaml_runner.py"

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("bug-to-markdown", workflows)
        wf = workflows["bug-to-markdown"]
        self.assertEqual(wf["name"], "bug-to-markdown")
        self.assertTrue(Path(wf["target_path"]).is_file())

    def test_verifier_script_directly(self):
        valid_sample = (
            "# Bug 999888\n"
            "- Bug number: 999888\n"
            "- Status: Assigned\n"
            "- Assignee: joshuaduong\n"
            "- Problem: Build version display issue\n"
            "- Proposed Fix: N/A\n"
        )
        with tempfile.TemporaryDirectory() as td:
            fpath = Path(td) / "b999888.md"
            fpath.write_text(valid_sample, encoding="utf-8")

            res = subprocess.run(
                [sys.executable, str(self.verifier_py), td, "999888"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("verified with all required fields", res.stdout)

    def test_complete_workflow_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_state_dir:
            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = tmp_state_dir

            # 1. No arguments prints help
            help_res = subprocess.run(
                [sys.executable, str(self.yaml_runner_py), str(self.workflow_yaml)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("usage:", help_res.stdout.lower())
            self.assertIn("init", help_res.stdout.lower())

            # 2. Init without bug argument fails
            init_fail_res = subprocess.run(
                [sys.executable, str(self.yaml_runner_py), str(self.workflow_yaml), "init"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(init_fail_res.returncode, 0)
            self.assertIn("required: bug-number", init_fail_res.stderr)

            # 3. Init with bug argument creates session
            init_res = subprocess.run(
                [sys.executable, str(self.yaml_runner_py), str(self.workflow_yaml), "init", "b/999888"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            output = init_res.stdout.strip()
            self.assertIn("Please run 'emu-dev-cli workflow bug-to-markdown --state=", output)

            match = re.search(r"--state=([a-zA-Z0-9]+)", output)
            self.assertIsNotNone(match)
            state_id = match.group(1)

            state_file = Path(tmp_state_dir) / "bug-to-markdown" / f"STATE-{state_id}.yaml"
            self.assertTrue(state_file.is_file())

            with open(state_file, "r") as f:
                s0 = yaml.safe_load(f)
            self.assertEqual(s0["state"], "INIT")
            self.assertEqual(s0["metadata"]["bug-number"], "999888")
            self.assertNotIn("bug", s0["metadata"])
            cache_dir = Path(s0["metadata"]["cache-dir"])
            self.assertTrue(cache_dir.is_dir())

            # 4. Enter Step 0
            step0_res = subprocess.run(
                [sys.executable, str(self.yaml_runner_py), str(self.workflow_yaml), f"--state={state_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please collect the bug information for bug b/999888", step0_res.stdout)
            self.assertIn("b999888.md", step0_res.stdout)

            # 5. Create valid b999888.md and advance to DONE
            target_md = cache_dir / "b999888.md"
            target_md.write_text(
                "# Bug 999888\n"
                "- Bug number: 999888\n"
                "- Status: Assigned\n"
                "- Assignee: joshuaduong\n"
                "- Problem: Build version display issue\n"
                "- Proposed Fix: N/A\n",
                encoding="utf-8",
            )

            try:
                pass_res = subprocess.run(
                    [sys.executable, str(self.yaml_runner_py), str(self.workflow_yaml), f"--state={state_id}"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )
                self.assertIn("Great job! Bug information for b/999888 has been successfully verified", pass_res.stdout)

                with open(state_file, "r") as f:
                    s_done = yaml.safe_load(f)
                self.assertEqual(s_done["state"], "DONE")
            finally:
                if target_md.is_file():
                    target_md.unlink()


if __name__ == "__main__":
    unittest.main()
