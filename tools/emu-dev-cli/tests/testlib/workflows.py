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

"""Shared hermetic test fixtures, fakes, and base classes for `emu-dev-cli` unit tests."""

from __future__ import annotations

from contextlib import contextmanager
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterator
import unittest
from unittest.mock import patch

from commands.workflow import (
    find_workflow_main,
    find_workflow_target,
    list_available_workflows,
)
from workflows.core.yaml_runner import (
    execute_workflow_step,
    get_state_base_dir,
    init_workflow_session,
    load_state_yaml,
    load_yaml,
)

_TESTLIB_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(_TESTLIB_DIR)
EMU_DEV_CLI_DIR = os.path.dirname(TESTS_DIR)
WORKFLOWS_DIR = os.path.join(EMU_DEV_CLI_DIR, "workflows")
SRC_DIR = os.path.join(EMU_DEV_CLI_DIR, "src")

DISALLOWED_GIT_COMMANDS_IN_PROMPTS = (
    r"\bgit\s+commit\b",
    r"\bgit\s+add\b",
    r"\bgit\s+push\b",
    r"\bgit\s+checkout\b",
    r"\bgit\s+diff\b",
    r"\bgit\s+status\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+reset\b",
)


class WorkflowTestCase(unittest.TestCase):
    """Base test case providing shared YAML workflow discovery, state isolation, and execution helpers."""

    workflow_name: str = ""
    target_type: str = ""
    workflow_yaml: str = ""
    workflow_dir: str = ""
    yaml_runner_py: str = os.path.join(WORKFLOWS_DIR, "core", "yaml_runner.py")

    def setup_workflow(self, workflow_name: str | None = None) -> tuple[str, str]:
        """Resolves and validates a YAML workflow target by name."""
        name = workflow_name or self.workflow_name
        self.assertTrue(name, "workflow_name must be provided")
        target = find_workflow_target(name)
        self.assertIsNotNone(target, f"{name} workflow target not found")
        target_type, workflow_yaml = target
        self.assertEqual(target_type, "yaml")
        self.assertTrue(os.path.isfile(workflow_yaml))
        self.workflow_name = name
        self.target_type = target_type
        self.workflow_yaml = workflow_yaml
        self.workflow_dir = os.path.dirname(workflow_yaml)
        return target_type, workflow_yaml

    def assert_workflow_discovery(self, workflow_name: str | None = None) -> dict[str, Any]:
        """Verifies that `workflow_name` is discoverable via `list_available_workflows`, `find_workflow_target`, and `find_workflow_main`."""
        name = workflow_name or self.workflow_name
        workflows = list_available_workflows()
        self.assertIn(name, workflows)
        wf = workflows[name]
        self.assertEqual(wf["name"], name)
        self.assertEqual(wf["target_type"], "yaml")
        self.assertTrue(os.path.isfile(wf["target_path"]))

        target = find_workflow_target(name)
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "yaml")
        if self.workflow_yaml:
            self.assertEqual(target[1], self.workflow_yaml)
            self.assertEqual(find_workflow_main(name), self.workflow_yaml)
        return wf

    def assert_no_git_commands_in_instructions(
        self, spec_path: str | Path | None = None
    ) -> None:
        """Verifies that workflow step prompts do not instruct the agent to run raw git mutation commands."""
        yaml_path = str(spec_path or self.workflow_yaml)
        spec = load_yaml(yaml_path)
        for step in spec.get("steps", []):
            for field_name in ("prompt", "error_prompt", "stuck_prompt", "stuck_reason"):
                val = step.get(field_name, "")
                text = " ".join(val) if isinstance(val, list) else str(val or "")
                for pattern in DISALLOWED_GIT_COMMANDS_IN_PROMPTS:
                    self.assertIsNone(
                        re.search(pattern, text, re.IGNORECASE),
                        f"Found forbidden git command matching '{pattern}' in step {step.get('step')} field '{field_name}': {text}",
                    )

    def assert_semantic_commit_rules_in_prompt(
        self, spec_path: str | Path | None = None
    ) -> None:
        """Verifies that Step 0 of a commit workflow enforces Semantic Commit Message rules."""
        yaml_path = str(spec_path or self.workflow_yaml)
        spec = load_yaml(yaml_path)
        step0_prompt = spec["steps"][0]["prompt"]
        self.assertIn("<type>[optional scope]: <description>", step0_prompt)
        self.assertIn("50 characters", step0_prompt)
        self.assertIn(
            "feat, fix, build, docs, chore, test, refactor, perf, style",
            step0_prompt,
        )
        self.assertIn("imperative mood", step0_prompt)
        self.assertIn("BREAKING CHANGE:", step0_prompt)

    @contextmanager
    def isolated_workflow_env(
        self, cwd: str | Path | None = None
    ) -> Iterator[tuple[Path, Path, Path]]:
        """Context manager providing isolated temporary state and cache directories (and optional cwd)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_root = root / "state"
            cache_root = root / "cache"
            state_root.mkdir(parents=True, exist_ok=True)
            cache_root.mkdir(parents=True, exist_ok=True)

            old_cwd = os.getcwd()
            old_state = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            old_cache = os.environ.get("EMU_DEV_CLI_WORKFLOW_CACHE_DIR")
            try:
                os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(state_root)
                os.environ["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = str(cache_root)
                if cwd is not None:
                    os.chdir(str(cwd))
                yield root, state_root, cache_root
            finally:
                os.chdir(old_cwd)
                if old_state is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_state
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)
                if old_cache is not None:
                    os.environ["EMU_DEV_CLI_WORKFLOW_CACHE_DIR"] = old_cache
                else:
                    os.environ.pop("EMU_DEV_CLI_WORKFLOW_CACHE_DIR", None)

    def run_single_step_review_workflow(
        self,
        workflow_name: str,
        *,
        commit: str = "HEAD~1",
        repo_subdir: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Initializes and runs a single-step review workflow in an isolated environment."""
        _, workflow_yaml = self.setup_workflow(workflow_name)
        with self.isolated_workflow_env() as (tmpdir, _, _):
            repo_dir = tmpdir / repo_subdir if repo_subdir else tmpdir
            repo_dir.mkdir(parents=True, exist_ok=True)
            state_id = init_workflow_session(
                spec_path=workflow_yaml,
                args_input=["--commit", commit, "--repo", str(repo_dir)],
            )
            self.assertIsNotNone(state_id)
            state_file = get_state_base_dir() / workflow_name / f"STATE-{state_id}.yaml"
            self.assertTrue(state_file.is_file())
            state = load_yaml(state_file)
            self.assertEqual(state.get("state"), "INIT")
            self.assertEqual(state.get("step"), 0)
            metadata = state.get("metadata", {})
            self.assertEqual(metadata.get("commit"), commit)
            self.assertEqual(
                os.path.realpath(metadata.get("repo")),
                os.path.realpath(str(repo_dir)),
            )

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = execute_workflow_step(workflow_yaml, state_id)
            self.assertEqual(rc, 0)
            updated_state = load_yaml(state_file)
            self.assertEqual(updated_state.get("state"), "DONE")
            return buf.getvalue(), updated_state
