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



    def test_sub_workflow_output_mapping_and_history(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            execute_workflow_step,
            load_state_yaml,
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                # 1. Define child workflow
                child_yaml = os.path.join(td, "child_wf.yaml")
                with open(child_yaml, "w") as f:
                    f.write("""name: test-child
init:
  child_out:
    cmd: "echo generated_child_value"
steps:
  - step: 0
    prompt: Child step 0 prompt
    verifier_cmd: null
""")
                # 2. Define parent workflow
                parent_yaml = os.path.join(td, "parent_wf.yaml")
                with open(parent_yaml, "w") as f:
                    f.write(f"""name: test-parent
steps:
  - step: 0
    title: Delegate to child
    workflow:
      name: "{child_yaml}"
      outputs:
        child_out: mapped_parent_var
  - step: 1
    title: Completion
    prompt: Parent step 1 with {{mapped_parent_var}}
    verifier_cmd: null
""")
                p_id = init_workflow_session(parent_yaml, [])
                rc = execute_workflow_step(parent_yaml, p_id)
                self.assertEqual(rc, 0)

                p_state_file = os.path.join(state_dir, "test-parent", f"STATE-{p_id}.yaml")
                p_state = load_state_yaml(p_state_file)
                self.assertEqual(p_state["state"], "DONE")
                self.assertIn("test-child", p_state["metadata"])
                self.assertIn("mapped_parent_var", p_state["metadata"]["test-child"])
                self.assertEqual(p_state["metadata"]["test-child"]["mapped_parent_var"], "generated_child_value")
                self.assertNotIn("_sub_workflow", p_state["metadata"])
                self.assertEqual(len(p_state["metadata"]["_sub_workflow_history"]), 1)
                self.assertEqual(p_state["metadata"]["_sub_workflow_history"][0]["status"], "DONE")
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    def test_missing_sub_workflow_output_key_raises_error(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            execute_workflow_step,
            WorkflowOutputError,
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                child_yaml = os.path.join(td, "child_wf.yaml")
                with open(child_yaml, "w") as f:
                    f.write("""name: test-child-missing
steps:
  - step: 0
    prompt: Done
    verifier_cmd: null
""")
                parent_yaml = os.path.join(td, "parent_wf.yaml")
                with open(parent_yaml, "w") as f:
                    f.write(f"""name: test-parent-missing
steps:
  - step: 0
    workflow:
      name: "{child_yaml}"
      outputs:
        non_existent_key: mapped_key
""")
                p_id = init_workflow_session(parent_yaml, [])
                with self.assertRaises(WorkflowOutputError) as cm:
                    execute_workflow_step(parent_yaml, p_id)
                self.assertIn("non_existent_key", str(cm.exception))
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    def test_static_cycle_detection(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            WorkflowRecursionError,
        )

        with tempfile.TemporaryDirectory() as td:
            wf1 = os.path.join(td, "cycle1.yaml")
            wf2 = os.path.join(td, "cycle2.yaml")

            with open(wf1, "w") as f:
                f.write(f"""name: cycle1
steps:
  - step: 0
    workflow: "{wf2}"
""")
            with open(wf2, "w") as f:
                f.write(f"""name: cycle2
steps:
  - step: 0
    workflow: "{wf1}"
""")
            with self.assertRaises(WorkflowRecursionError) as cm:
                init_workflow_session(wf1, [])
            self.assertIn("Circular workflow dependency detected", str(cm.exception))

    def test_modular_bug_fixing_workflows_discovered(self):
        workflows = list_available_workflows()
        for expected in (
            "reproduce-bug",
            "fix-bug",
            "repo-upload",
            "human-verify",
            "custom-workflow",
        ):
            self.assertIn(expected, workflows, f"Expected workflow {expected} to be registered")

    def test_custom_instructions_in_step_and_sub_workflow(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            execute_workflow_step,
            load_state_yaml,
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                child_yaml = os.path.join(td, "child_custom.yaml")
                with open(child_yaml, "w") as f:
                    f.write("""name: child-custom
steps:
  - step: 0
    prompt: Base child prompt.
    verifier_cmd: "python3 -c \\"import sys; sys.exit(0)\\""
  - step: 1
    prompt: Child done.
    verifier_cmd: null
""")
                parent_yaml = os.path.join(td, "parent_custom.yaml")
                with open(parent_yaml, "w") as f:
                    f.write(f"""name: parent-custom
steps:
  - step: 0
    title: Run child with custom instructions
    custom_instructions: "Check Vulkan swapchain null pointer in display.cpp"
    workflow:
      name: "{child_yaml}"
  - step: 1
    prompt: Parent finished.
    verifier_cmd: null
""")
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    p_id = init_workflow_session(parent_yaml, [])
                    execute_workflow_step(parent_yaml, p_id)
                    step0_out = sys.stdout.getvalue()
                    self.assertIn("Check Vulkan swapchain null pointer in display.cpp", step0_out)
                    self.assertIn("Additional Instructions:", step0_out)
                finally:
                    sys.stdout = old_stdout
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    def test_human_verify_barrier_and_approval(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            execute_workflow_step,
            approve_workflow_session,
            load_state_yaml,
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            try:
                wf_yaml = os.path.join(td, "gated_wf.yaml")
                with open(wf_yaml, "w") as f:
                    f.write("""name: gated-wf
steps:
  - step: 0
    title: Human Verification Barrier
    human_verify: true
    custom_instructions: "Confirm reproduction before continuing."
    prompt: "Waiting for human approval."
  - step: 1
    title: Final Step
    prompt: "Proceeding after approval."
    verifier_cmd: null
""")
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    s_id = init_workflow_session(wf_yaml, [])
                    execute_workflow_step(wf_yaml, s_id)
                finally:
                    sys.stdout = old_stdout

                state_file = os.path.join(state_dir, "gated-wf", f"STATE-{s_id}.yaml")
                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "WAITING_HUMAN")
                self.assertEqual(st["step"], 0)

                # Calling execute_workflow_step before approval keeps it at step 0 in WAITING_HUMAN without burning retries
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    rc = execute_workflow_step(wf_yaml, s_id)
                finally:
                    sys.stdout = old_stdout
                self.assertEqual(rc, 0)
                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "WAITING_HUMAN")
                self.assertEqual(st["step"], 0)
                self.assertEqual(st["retries"], 0)

                # Now approve via approve_workflow_session and advance
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    ok, msg = approve_workflow_session(
                        state_id=s_id,
                        workflow_name="gated-wf",
                        feedback="Looks good, proceed with fix!",
                        state_base_dir=None,
                        advance=False,
                    )
                    rc2 = execute_workflow_step(wf_yaml, s_id)
                    approve_out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                self.assertTrue(ok)
                self.assertEqual(rc2, 0)
                self.assertIn("Looks good, proceed with fix!", approve_out)
                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "DONE")
                self.assertEqual(st["step"], 1)
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    def test_loop_to_step_and_conversation_binding_and_resume(self):
        import tempfile
        from workflows.core.yaml_runner import (
            init_workflow_session,
            execute_workflow_step,
            resume_workflow_session,
            load_state_yaml,
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")
            flag_file = os.path.join(td, "round_flag.txt")
            with open(flag_file, "w") as f:
                f.write("round1")

            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            old_conv = os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = state_dir
            os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "conv-test-1234"
            try:
                wf_yaml = os.path.join(td, "loop_wf.yaml")
                with open(wf_yaml, "w") as f:
                    f.write(f"""name: loop-wf
steps:
  - step: 0
    title: Address Feedback
    prompt: "Address comments (iteration {{iteration}})."
    verifier_cmd: "python3 -c \\"import sys; sys.exit(0)\\""
  - step: 1
    title: Upload and Check New Comments
    prompt: "Upload patchset and check for new comments."
    loop_to_step: 0
    loop_exit_code: 2
    max_iterations: 3
    loop_prompt: "New review comments arrived ({{verifier_output}})! Looping back to Step 0."
    verifier_cmd: "python3 -c \\"import pathlib, sys; p = pathlib.Path('{flag_file}'); val = p.read_text().strip(); p.write_text('round2'); sys.exit(2 if val == 'round1' else 0)\\""
  - step: 2
    title: Done
    prompt: "All review rounds complete!"
    verifier_cmd: null
""")
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    s_id = init_workflow_session(wf_yaml, [])
                    state_file = os.path.join(state_dir, "loop-wf", f"STATE-{s_id}.yaml")
                    st = load_state_yaml(state_file)
                    self.assertEqual(st["metadata"].get("conversation_id"), "conv-test-1234")

                    # Enter Step 0
                    execute_workflow_step(wf_yaml, s_id)
                    # Pass Step 0 -> advance to Step 1
                    execute_workflow_step(wf_yaml, s_id)
                    st = load_state_yaml(state_file)
                    self.assertEqual(st["step"], 1)

                    # Execute Step 1 verifier -> returns exit code 2 -> loops back to Step 0!
                    rc_loop = execute_workflow_step(wf_yaml, s_id)
                    loop_out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                self.assertEqual(rc_loop, 0)
                self.assertIn("New review comments arrived", loop_out)
                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "RUNNING")
                self.assertEqual(st["step"], 0)
                self.assertEqual(st["iteration"], 1)
                self.assertEqual(st["retries"], 0)

                # Pass Step 0 again -> advance to Step 1 -> verifier returns 0 -> advance to Step 2 (DONE)
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    execute_workflow_step(wf_yaml, s_id)
                    execute_workflow_step(wf_yaml, s_id)
                finally:
                    sys.stdout = old_stdout

                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "DONE")
                self.assertEqual(st["step"], 2)

                # Now resume the completed session for a new review round without waking external process
                ok, msg, info = resume_workflow_session(
                    state_id=s_id,
                    workflow_name="loop-wf",
                    target_step=0,
                    wake_conversation=False,
                )
                self.assertTrue(ok)
                self.assertEqual(info["conversation_id"], "conv-test-1234")
                st = load_state_yaml(state_file)
                self.assertEqual(st["state"], "RUNNING")
                self.assertEqual(st["step"], 0)
                self.assertEqual(st["iteration"], 2)
            finally:
                if old_env is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)
                if old_conv is not None:
                    os.environ["ANTIGRAVITY_CONVERSATION_ID"] = old_conv
                else:
                    os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)


if __name__ == "__main__":
    unittest.main()


