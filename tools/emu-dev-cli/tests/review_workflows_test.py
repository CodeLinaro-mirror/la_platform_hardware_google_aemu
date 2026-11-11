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

"""Consolidated parameterized integration tests for all `review-*` workflows."""

from __future__ import annotations

from dataclasses import dataclass
import unittest

from testlib.workflows import WorkflowTestCase
from workflows.core.yaml_runner import load_yaml


@dataclass(frozen=True)
class ReviewWorkflowCase:
    """Specification and expected prompt assertions for a `review-*` workflow."""

    name: str
    expected_spec_tokens: tuple[str, ...]
    expected_output_tokens: tuple[str, ...]
    repo_subdir: str | None = None


REVIEW_WORKFLOW_CASES: tuple[ReviewWorkflowCase, ...] = (
    ReviewWorkflowCase(
        name="review-constants",
        expected_spec_tokens=("magic numbers", "named constants", "enums", "amend"),
        expected_output_tokens=("magic numbers", "named constants"),
    ),
    ReviewWorkflowCase(
        name="review-control-flow",
        expected_spec_tokens=("control flow", "guard clauses", "nesting", "amend"),
        expected_output_tokens=("control flow", "guard clauses"),
    ),
    ReviewWorkflowCase(
        name="review-duplication",
        expected_spec_tokens=("remove duplicate code", "code reuse", "amend"),
        expected_output_tokens=("remove duplicate code",),
    ),
    ReviewWorkflowCase(
        name="review-modularity",
        expected_spec_tokens=("monolithic", "modular", "single responsibility", "amend"),
        expected_output_tokens=("monolithic behavior",),
    ),
    ReviewWorkflowCase(
        name="review-unicode",
        expected_spec_tokens=(
            "unicode",
            "emu-main-next",
            "emu-main-dev",
            "goldfish/file/file.h",
            "cc_binary_with_manifest",
            "android/utils/file_io.h",
            "win32unicodestring",
            "non-ascii",
            "unit test",
            "amend",
        ),
        expected_output_tokens=("täst_中文_🚀", "emu-main-next", "emu-main-dev"),
        repo_subdir="täst_中文_🚀",
    ),
)


class TestReviewWorkflowsIntegration(WorkflowTestCase):
    """Parameterized tests covering discovery, YAML spec, and session execution for all `review-*` workflows."""

    def test_all_review_workflows_discovery(self) -> None:
        for case in REVIEW_WORKFLOW_CASES:
            with self.subTest(workflow=case.name):
                self.setup_workflow(case.name)
                self.assert_workflow_discovery(case.name)

    def test_all_review_workflows_yaml_specifications(self) -> None:
        for case in REVIEW_WORKFLOW_CASES:
            with self.subTest(workflow=case.name):
                _, workflow_yaml = self.setup_workflow(case.name)
                spec = load_yaml(workflow_yaml)
                self.assertEqual(spec.get("name"), case.name)
                self.assertTrue(spec.get("description"))

                arg_names = [arg["name"] for arg in spec.get("args", [])]
                self.assertIn("commit", arg_names)
                self.assertIn("repo", arg_names)
                self.assertNotIn("init", spec)

                steps = spec.get("steps", [])
                self.assertGreaterEqual(len(steps), 1)
                step0 = steps[0]
                self.assertEqual(step0.get("step"), 0)
                self.assertIsNone(step0.get("verifier_cmd"))

                prompt_lower = step0.get("prompt", "").lower()
                for token in case.expected_spec_tokens:
                    self.assertIn(token.lower(), prompt_lower)

    def test_all_review_workflows_session_initialization_and_execution(self) -> None:
        for case in REVIEW_WORKFLOW_CASES:
            with self.subTest(workflow=case.name):
                output, updated_state = self.run_single_step_review_workflow(
                    case.name,
                    commit="HEAD~1",
                    repo_subdir=case.repo_subdir,
                )
                self.assertEqual(updated_state.get("state"), "DONE")
                self.assertIn("Please check commit 'HEAD~1'", output)
                output_lower = output.lower()
                for token in case.expected_output_tokens:
                    self.assertIn(token.lower(), output_lower)


if __name__ == "__main__":
    unittest.main()
