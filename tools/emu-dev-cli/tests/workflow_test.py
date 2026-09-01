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
    normalize_workflow_name,
    run_workflow_command,
)


class WorkflowCommandTest(unittest.TestCase):

    def test_normalize_workflow_name(self):
        self.assertEqual(normalize_workflow_name("hello-world"), "hello_world_example")
        self.assertEqual(normalize_workflow_name("hello-world-example"), "hello_world_example")
        self.assertEqual(normalize_workflow_name("hello_world_example"), "hello_world_example")
        self.assertEqual(normalize_workflow_name("my-new-workflow"), "my_new_workflow")

    def test_list_available_workflows(self):
        workflows = list_available_workflows()
        self.assertIn("hello_world_example", workflows)
        wf = workflows["hello_world_example"]
        self.assertEqual(wf["name"], "hello_world_example")
        self.assertTrue(os.path.isfile(wf["target_path"]))
        self.assertTrue(wf["target_path"].endswith(("workflow.yaml", "workflow.yml")))

    def test_find_workflow_main(self):
        main_py = find_workflow_main("hello_world_example")
        self.assertIsNotNone(main_py)
        self.assertTrue(os.path.isfile(main_py))

        alias_main = find_workflow_main("hello-world")
        self.assertEqual(main_py, alias_main)

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
            self.assertIn("hello_world_example", output)
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


if __name__ == "__main__":
    unittest.main()
