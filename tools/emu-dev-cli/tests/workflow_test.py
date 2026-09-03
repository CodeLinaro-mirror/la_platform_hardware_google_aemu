"""Unit tests for emu-dev-cli workflow command dispatcher."""

import io
import os
import sys
import unittest

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
    run_workflow_command,
)


class WorkflowCommandTest(unittest.TestCase):

    def test_exact_workflow_name_matching(self):
        # Exact match succeeds
        self.assertIsNotNone(find_workflow_main("hello-world-example"))
        # Non-matching variations are not normalized
        self.assertIsNone(find_workflow_main("hello_world_example"))
        self.assertIsNone(find_workflow_main("hello-world"))
        self.assertIsNone(find_workflow_main("hello"))

    def test_list_available_workflows(self):
        workflows = list_available_workflows()
        self.assertIn("hello-world-example", workflows)
        wf = workflows["hello-world-example"]
        self.assertEqual(wf["name"], "hello-world-example")
        self.assertTrue(os.path.isfile(wf["target_path"]))
        self.assertTrue(wf["target_path"].endswith(("workflow.yaml", "workflow.yml")))

    def test_find_workflow_main(self):
        main_py = find_workflow_main("hello-world-example")
        self.assertIsNotNone(main_py)
        self.assertTrue(os.path.isfile(main_py))

    def test_find_nonexistent_workflow(self):
        self.assertIsNone(find_workflow_main("nonexistent_workflow_xyz"))

    def test_workflow_list_cli(self):
        class Args:
            workflow_cmd = "list"
            workflow_args = []
            json = False

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            run_workflow_command(Args())
            output = sys.stdout.getvalue()
            self.assertIn("Available workflows", output)
            self.assertIn("hello-world-example", output)
            self.assertNotIn("hello_world_example", output)
        finally:
            sys.stdout = old_stdout


    def test_build_cmd_args_prevents_command_injection(self):
        from workflows.core.yaml_runner import build_cmd_args
        malicious_context = {
            "hello-file": "/tmp/victim; rm -rf /",
            "state_id": "1234`id`",
            "safe_dir": "/tmp/normal_dir",
        }
        args = build_cmd_args("grep -qi '^Name:' {hello-file}", malicious_context)
        self.assertEqual(args, ["grep", "-qi", "^Name:", "/tmp/victim; rm -rf /"])

        args2 = build_cmd_args("echo {state_id}", malicious_context)
        self.assertEqual(args2, ["echo", "1234`id`"])

    def test_build_cmd_args_prevents_multi_pass_injection(self):
        from workflows.core.yaml_runner import build_cmd_args
        malicious_context = {
            "a": " {b} ",
            "b": "$(id)",
        }
        args = build_cmd_args("echo {a}", malicious_context)
        self.assertEqual(args, ["echo", " {b} "])

    def test_build_cmd_args_list_specification(self):
        from workflows.core.yaml_runner import build_cmd_args
        ctx = {"file": "/tmp/test file.txt"}
        args = build_cmd_args(["cat", "{file}"], ctx)
        self.assertEqual(args, ["cat", "/tmp/test file.txt"])


    def test_workflow_init_with_args(self):
        import tempfile
        import re
        from workflows.core.yaml_runner import run_yaml_workflow, load_state_yaml

        workflow_yaml_content = """name: test-args-wf
description: Test workflow with arguments
args:
  - name: bug-number
    help: Buganizer bug number
    required: true
init:
  echo_bug:
    cmd: echo processed-{bug-number}
steps:
  - step: 0
    prompt: Working on bug {bug-number}
"""
        with tempfile.TemporaryDirectory() as td:
            wf_path = os.path.join(td, "workflow.yaml")
            with open(wf_path, "w") as f:
                f.write(workflow_yaml_content)

            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                # 1. Missing arg exits with error
                old_stderr = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    with self.assertRaises(SystemExit) as cm:
                        run_yaml_workflow(wf_path, ["init"])
                    self.assertNotEqual(cm.exception.code, 0)
                finally:
                    sys.stderr = old_stderr

                # 2. Providing arg succeeds and normalizes b/ prefix
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    ret = run_yaml_workflow(wf_path, ["init", "b/553514805"])
                    self.assertEqual(ret, 0)
                    out = sys.stdout.getvalue()
                    match = re.search(r"--state=([a-zA-Z0-9]+)", out)
                    self.assertIsNotNone(match)
                    state_id = match.group(1)
                finally:
                    sys.stdout = old_stdout

                state_file = os.path.join(state_dir, "test-args-wf", f"STATE-{state_id}.yaml")
                self.assertTrue(os.path.isfile(state_file))
                st = load_state_yaml(state_file)
                self.assertEqual(st["metadata"]["bug-number"], "553514805")
                self.assertNotIn("bug", st["metadata"])
                self.assertNotIn("bug_number", st["metadata"])
                self.assertEqual(st["metadata"]["echo_bug"], "processed-553514805")
                self.assertIn("cache-dir", st["metadata"])
                self.assertTrue(os.path.isdir(st["metadata"]["cache-dir"]))
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)


    def test_workflow_init_creates_generic_cache_dir(self):
        import tempfile
        import re
        from workflows.core.yaml_runner import run_yaml_workflow, load_state_yaml

        workflow_yaml = """name: test-generic-cache
description: Test generic cache directory creation
steps:
  - step: 0
    prompt: Use cache at {cache-dir}
"""
        with tempfile.TemporaryDirectory() as td:
            wf_path = os.path.join(td, "workflow.yaml")
            with open(wf_path, "w") as f:
                f.write(workflow_yaml)

            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    ret = run_yaml_workflow(wf_path, ["init"])
                    self.assertEqual(ret, 0)
                    out = sys.stdout.getvalue()
                    match = re.search(r"--state=([a-zA-Z0-9]+)", out)
                    self.assertIsNotNone(match)
                    state_id = match.group(1)
                finally:
                    sys.stdout = old_stdout

                state_file = os.path.join(state_dir, "test-generic-cache", f"STATE-{state_id}.yaml")
                self.assertTrue(os.path.isfile(state_file))
                st = load_state_yaml(state_file)
                self.assertIn("cache-dir", st["metadata"])
                cache_dir_path = st["metadata"]["cache-dir"]
                self.assertTrue(os.path.isdir(cache_dir_path))
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)


if __name__ == "__main__":
    unittest.main()
