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
from workflows.core.yaml_runner import (
    load_state_yaml,
    load_yaml,
)
from workflows.test_driven_bug_fixer.detect_env import detect_environment
from workflows.test_driven_bug_fixer.verify_compile import verify_compile


class TestTestDrivenBugFixerWorkflow(unittest.TestCase):

    def setUp(self):
        target = find_workflow_target("test-driven-bug-fixer")
        self.assertIsNotNone(target, "test-driven-bug-fixer workflow target not found")
        self.target_type, self.workflow_yaml = target
        self.assertEqual(self.target_type, "yaml")
        self.assertTrue(os.path.isfile(self.workflow_yaml))

        self.workflow_dir = os.path.dirname(self.workflow_yaml)
        self.detect_env_py = os.path.join(self.workflow_dir, "detect_env.py")
        self.verify_compile_py = os.path.join(self.workflow_dir, "verify_compile.py")
        self.yaml_runner_py = os.path.join(_WORKFLOWS_DIR, "core", "yaml_runner.py")

        self.assertTrue(os.path.isfile(self.detect_env_py))
        self.assertTrue(os.path.isfile(self.verify_compile_py))
        self.assertTrue(os.path.isfile(self.yaml_runner_py))

    def test_workflow_discovery(self):
        workflows = list_available_workflows()
        self.assertIn("test-driven-bug-fixer", workflows)
        wf = workflows["test-driven-bug-fixer"]
        self.assertEqual(wf["name"], "test-driven-bug-fixer")
        self.assertEqual(wf["target_type"], "yaml")

    def test_detect_environment_emu_main_dev(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dev_root = Path(tmpdir).resolve()
            cmake_script = dev_root / "external" / "qemu" / "android" / "build" / "python" / "cmake.py"
            cmake_script.parent.mkdir(parents=True, exist_ok=True)
            cmake_script.write_text("#!/usr/bin/env python3\n")

            env = detect_environment(dev_root)
            self.assertEqual(env["env-type"], "emu-main-dev")
            self.assertEqual(env["repo-root"], str(dev_root))
            self.assertEqual(env["repo-dir"], str(dev_root / "external" / "qemu"))
            self.assertEqual(env["compile-cmd"], "cd external/qemu && python3 android/build/python/cmake.py --task Compile")

            # From a subfolder inside external/qemu
            sub_dir = dev_root / "external" / "qemu" / "android" / "android-ui"
            sub_dir.mkdir(parents=True, exist_ok=True)
            env_sub = detect_environment(sub_dir)
            self.assertEqual(env_sub["env-type"], "emu-main-dev")
            self.assertEqual(env_sub["repo-root"], str(dev_root))

    def test_detect_environment_emu_main_next(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            next_root = Path(tmpdir).resolve()
            (next_root / "MODULE.bazel").write_text("")
            (next_root / "hardware" / "generic" / "goldfish").mkdir(parents=True, exist_ok=True)

            env = detect_environment(next_root)
            self.assertEqual(env["env-type"], "emu-main-next")
            self.assertEqual(env["repo-root"], str(next_root))
            self.assertEqual(env["repo-dir"], str(next_root))
            self.assertEqual(env["compile-cmd"], "bazel build @goldfish//emulator/launcher:launch_emulator")

    def test_detect_environment_from_emu_dev_cli_json(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "emu-dev-cli.json"
            fake_dev = (Path(tmpdir) / "fake-dev").resolve()
            fake_next = (Path(tmpdir) / "fake-next").resolve()
            fake_dev.mkdir()
            fake_next.mkdir()

            cfg_file.write_text(json.dumps({
                "source_directories": {
                    "emu-main-dev": str(fake_dev),
                    "emu-main-next": str(fake_next)
                }
            }))

            orig_cfg = os.environ.get("EMU_DEV_CLI_CONFIG_PATH")
            try:
                os.environ["EMU_DEV_CLI_CONFIG_PATH"] = str(cfg_file)
                dev_env = detect_environment(fake_dev)
                self.assertEqual(dev_env["env-type"], "emu-main-dev")
                self.assertEqual(dev_env["repo-root"], str(fake_dev))
                self.assertEqual(dev_env["repo-dir"], str(fake_dev / "external" / "qemu"))
                self.assertEqual(dev_env["compile-cmd"], "cd external/qemu && python3 android/build/python/cmake.py --task Compile")

                next_env = detect_environment(fake_next)
                self.assertEqual(next_env["env-type"], "emu-main-next")
                self.assertEqual(next_env["repo-root"], str(fake_next))
                self.assertEqual(next_env["repo-dir"], str(fake_next))
                self.assertEqual(next_env["compile-cmd"], "bazel build @goldfish//emulator/launcher:launch_emulator")
            finally:
                if orig_cfg is not None:
                    os.environ["EMU_DEV_CLI_CONFIG_PATH"] = orig_cfg
                else:
                    os.environ.pop("EMU_DEV_CLI_CONFIG_PATH", None)

    def test_detect_env_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dev_root = Path(tmpdir).resolve()
            cmake_script = dev_root / "external" / "qemu" / "android" / "build" / "python" / "cmake.py"
            cmake_script.parent.mkdir(parents=True, exist_ok=True)
            cmake_script.write_text("#!/usr/bin/env python3\n")

            # Test target repo-root
            res = subprocess.run(
                [sys.executable, self.detect_env_py, "--target", "repo-root", "--cwd", str(dev_root)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.stdout.strip(), str(dev_root))

            # Test target compile-cmd
            res = subprocess.run(
                [sys.executable, self.detect_env_py, "--target", "compile-cmd", "--cwd", str(dev_root)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.stdout.strip(), "cd external/qemu && python3 android/build/python/cmake.py --task Compile")

            # Test override
            res = subprocess.run(
                [
                    sys.executable,
                    self.detect_env_py,
                    "--target",
                    "compile-cmd",
                    "--cwd",
                    str(dev_root),
                    "--compile-cmd-override",
                    "echo custom_compile",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.stdout.strip(), "echo custom_compile")

    def test_verify_compile_success_and_failure(self):
        # Successful compile
        rc_ok = verify_compile("python3 -c \x27print(\"ok\")\x27")
        self.assertEqual(rc_ok, 0)

        # Failing compile
        rc_fail = verify_compile("python3 -c \x27import sys; sys.exit(42)\x27")
        self.assertNotEqual(rc_fail, 0)

        # Empty command
        rc_empty = verify_compile("")
        self.assertEqual(rc_empty, 1)

        # Non-existent working directory
        rc_nodir = verify_compile("true", cwd="/non_existent_path_xyz123")
        self.assertEqual(rc_nodir, 1)

    def test_workflow_init(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            cache_dir = Path(tmpdir) / "cache"
            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(state_dir)
            env["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = str(cache_dir)

            init_res = subprocess.run(
                [
                    sys.executable,
                    self.yaml_runner_py,
                    self.workflow_yaml,
                    "init",
                    "553514805",
                ],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            m = re.search(r"--state=([a-zA-Z0-9]+)", init_res.stdout)
            self.assertIsNotNone(m, "State ID not found in init output")
            state_id = m.group(1)

            state_file = state_dir / "test-driven-bug-fixer" / f"STATE-{state_id}.yaml"
            self.assertTrue(state_file.is_file())
            state_data = load_state_yaml(state_file)
            self.assertEqual(state_data["step"], 0)
            self.assertEqual(state_data["state"], "INIT")
            self.assertEqual(state_data["metadata"]["bug-number"], "553514805")
            self.assertIn("compile-cmd", state_data["metadata"])
            self.assertIn("repo-root", state_data["metadata"])
            self.assertIn("repo-dir", state_data["metadata"])

    def test_full_pipeline_lifecycle_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            cache_dir = Path(tmpdir) / "cache"
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir()

            # Initialize a real git repository inside repo_root
            subprocess.run(["git", "init"], cwd=str(repo_root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_root), check=True, capture_output=True)
            (repo_root / "initial.txt").write_text("initial\n")
            subprocess.run(["git", "add", "initial.txt"], cwd=str(repo_root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_root), check=True, capture_output=True)

            env = os.environ.copy()
            env["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(state_dir)
            env["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = str(cache_dir)

            # 1. Initialize Pipeline Session with overrides for test repository and compilation command
            init_res = subprocess.run(
                [
                    sys.executable,
                    self.yaml_runner_py,
                    self.workflow_yaml,
                    "init",
                    "553514805",
                    "--repo",
                    str(repo_root),
                    "--compile-cmd-override",
                    "true",
                ],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            m = re.search(r"--state=([a-zA-Z0-9]+)", init_res.stdout)
            self.assertIsNotNone(m)
            pipeline_id = m.group(1)

            parent_state_file = state_dir / "test-driven-bug-fixer" / f"STATE-{pipeline_id}.yaml"
            self.assertTrue(parent_state_file.is_file())

            # 2. Step 0: Starts Step 0 (Sub-workflow bug-to-markdown)
            res0 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please collect the bug information for bug b/553514805", res0.stdout)
            self.assertIn(f"emu-dev-cli workflow test-driven-bug-fixer --state={pipeline_id}", res0.stdout)

            # Create mock bug-markdown-file in bug-to-markdown cache dir
            bug_md = cache_dir / "bug-to-markdown" / "b553514805.md"
            bug_md.parent.mkdir(parents=True, exist_ok=True)
            bug_md.write_text(
                "# Bug 553514805\n\n"
                "- Bug number: 553514805\n"
                "- Status: Assigned\n"
                "- Assignee: test_dev\n"
                "- Problem: Remote emulator version not displayed\n"
                "- Proposed Fix: Update VersionExtractor to check env var\n",
                encoding="utf-8",
            )

            # 3. Advance Step: bug-to-markdown completes, parent advances to Step 1 (create-test)
            res1 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please create a unit or integration test", res1.stdout)
            self.assertIn(f"emu-dev-cli workflow test-driven-bug-fixer --state={pipeline_id}", res1.stdout)

            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 1)

            # 4. Advance Step: Step 1 verifier runs (compile-cmd "true"), succeeds! Parent advances to Step 2 (commit-test)
            res2 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please write the proposed commit message", res2.stdout)
            self.assertIn(f"emu-dev-cli workflow test-driven-bug-fixer --state={pipeline_id}", res2.stdout)

            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 2)
            self.assertIn("_sub_workflow", p_state["metadata"])
            child_commit1_id = p_state["metadata"]["_sub_workflow"]["state_id"]

            # Step 2: Agent writes test file and proposed commit message
            (repo_root / "test_fix.py").write_text("def test_failing(): pass\n")
            subprocess.run(["git", "add", "test_fix.py"], cwd=str(repo_root), check=True)

            proposed_file1 = cache_dir / "create-commit" / f"proposed_commit_msg_{child_commit1_id}.txt"
            proposed_file1.parent.mkdir(parents=True, exist_ok=True)
            proposed_file1.write_text(
                "test: Add failing test for bug 553514805\n\n"
                "Exposes the issue where remote version is missing.\n\n"
                "Bug: 553514805\n"
                "Test: python3 test_fix.py\n",
                encoding="utf-8",
            )

            # 5. Advance Step: create-commit verifies proposed msg and prompts git commit
            res2_commit_prompt = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please create a commit for the changes in your working tree", res2_commit_prompt.stdout)

            # Create test commit in git
            subprocess.run(
                ["git", "commit", "-F", str(proposed_file1)],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
            )

            # 6. Advance Step: create-commit verifies commit, completes! Parent advances to Step 3 (apply-fix)
            res3 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("The test commit has been created", res3.stdout)
            self.assertIn("implement the fix for the bug", res3.stdout)
            self.assertIn(f"emu-dev-cli workflow test-driven-bug-fixer --state={pipeline_id}", res3.stdout)

            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 3)

            # 7. Advance Step: Step 3 verifier runs (compile-cmd "true"), succeeds! Parent advances to Step 4 (commit-fix)
            res4 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please write the proposed commit message", res4.stdout)
            self.assertIn(f"emu-dev-cli workflow test-driven-bug-fixer --state={pipeline_id}", res4.stdout)

            p_state = load_state_yaml(parent_state_file)
            self.assertEqual(p_state["step"], 4)
            self.assertIn("_sub_workflow", p_state["metadata"])
            child_commit2_id = p_state["metadata"]["_sub_workflow"]["state_id"]

            # Step 4: Agent writes fix and proposed commit message
            (repo_root / "src_fix.py").write_text("def solve(): return True\n")
            subprocess.run(["git", "add", "src_fix.py"], cwd=str(repo_root), check=True)

            proposed_file2 = cache_dir / "create-commit" / f"proposed_commit_msg_{child_commit2_id}.txt"
            proposed_file2.parent.mkdir(parents=True, exist_ok=True)
            proposed_file2.write_text(
                "fix: Resolve remote emulator version reporting\n\n"
                "Checks env var before falling back to local extractor.\n\n"
                "Bug: 553514805\n"
                "Test: python3 test_fix.py\n",
                encoding="utf-8",
            )

            # 8. Advance Step: create-commit verifies proposed msg and prompts git commit
            res4_commit_prompt = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Please create a commit for the changes in your working tree", res4_commit_prompt.stdout)

            # Create fix commit in git
            subprocess.run(
                ["git", "commit", "-F", str(proposed_file2)],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
            )

            # 9. Advance Step: create-commit verifies commit, completes!
            # Parent advances to Step 5 (summary), which is a passive completion step -> reaches DONE!
            res5 = subprocess.run(
                [sys.executable, self.yaml_runner_py, self.workflow_yaml, f"--state={pipeline_id}"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertIn("Test-driven bug fixer workflow completed successfully for bug b/553514805!", res5.stdout)
            self.assertIn("Test commit: Created and verified", res5.stdout)
            self.assertIn("Fix commit: Created and verified", res5.stdout)

            # 10. Verify final parent state
            final_p_state = load_state_yaml(parent_state_file)
            self.assertEqual(final_p_state["state"], "DONE")
            self.assertNotIn("_sub_workflow", final_p_state["metadata"])
            self.assertEqual(len(final_p_state["metadata"]["_sub_workflow_history"]), 3)
            self.assertEqual(final_p_state["metadata"]["_sub_workflow_history"][0]["name"], "bug-to-markdown")
            self.assertEqual(final_p_state["metadata"]["_sub_workflow_history"][1]["name"], "create-commit")
            self.assertEqual(final_p_state["metadata"]["_sub_workflow_history"][2]["name"], "create-commit")


if __name__ == "__main__":
    unittest.main()
