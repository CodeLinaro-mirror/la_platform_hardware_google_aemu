#!/usr/bin/env python3
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

"""Unit tests for per-agent swarm timing metrics and incremental patchset history."""

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from commands.harness.mcp_server import execute_mcp_tool
from commands.harness.review_history import (
    INCREMENTAL_PASS_MODEL,
    build_wave_metrics,
    load_iteration_history,
    load_recent_swarm_metrics,
    persist_iteration_and_metrics,
)
from commands.harness.review_swarm import (
    filter_and_aggregate_findings,
    prepare_review_swarm_bundle,
)

SAMPLE_CHANGE_ID: str = "I1234567890abcdef1234567890abcdef12345678"
UNICODE_SRC_NAME: str = "DeviceHub_测试_🚀_é.kt"
UNTOUCHED_SRC_NAME: str = "UntouchedHelper.kt"
ROUND_ONE_WALL_SEC: float = 18.5
MODULARITY_DURATION_SEC: float = 18.2
CONSTANTS_DURATION_SEC: float = 4.3
MAGIC_LINE_R1: int = 3
MAGIC_LINE_R2: int = 4
EXPECTED_INCREMENTAL_SPECIALISTS: int = 7
FULL_SPECIALIST_COUNT: int = 8
GIT_TEST_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@google.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@google.com",
}


def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=GIT_TEST_ENV,
    )
    return proc.stdout.strip()


def _sample_commit_message() -> str:
    return (
        "feat(cli): Support incremental swarm review\n\n"
        "Add DeviceHub telemetry handling across patchset iterations.\n\n"
        "Bug: 574245946\n"
        "Test: bazel test @aemu//tools/emu-dev-cli:review_history_test\n"
        f"Change-Id: {SAMPLE_CHANGE_ID}\n"
    )


SAMPLE_RAW_OUTPUTS = [
    {
        "specialist": "constants-auditor",
        "role": "Named Constants Auditor",
        "model": "flash",
        "duration_sec": CONSTANTS_DURATION_SEC,
        "checked_count": 2,
        "duplicate_count": 0,
        "finding_count": 1,
        "findings": [{
            "file": UNICODE_SRC_NAME,
            "line": MAGIC_LINE_R1,
            "category": "constants",
            "severity": "warning",
            "message": "Extract magic number 42 into a named constant.",
            "suggested_fix": "MAX_RETRIES = 42",
        }],
    },
    {
        "specialist": "modularity-auditor",
        "role": "Modularity Auditor",
        "model": "inherit",
        "duration_sec": MODULARITY_DURATION_SEC,
        "checked_count": 3,
        "duplicate_count": 0,
        "finding_count": 0,
        "findings": [],
    },
]


def _seed_round1_repo_and_history(root: Path) -> tuple[Path, Path, dict]:
    repo, cache_root = root / "repo", root / "cache" / "review-swarm"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: Initial base commit")

    (repo / UNICODE_SRC_NAME).write_text("line1\nline2\nmagic = 42\n", encoding="utf-8")
    (repo / UNTOUCHED_SRC_NAME).write_text("helper = true\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", _sample_commit_message())

    r1 = prepare_review_swarm_bundle(commit="HEAD", repo_dir=repo, cache_root=cache_root)
    manifest_r1 = json.loads(Path(r1["manifest_path"]).read_text(encoding="utf-8"))
    metrics_r1 = build_wave_metrics(manifest_r1, SAMPLE_RAW_OUTPUTS, ROUND_ONE_WALL_SEC)
    agg_r1 = filter_and_aggregate_findings(
        SAMPLE_RAW_OUTPUTS, manifest_r1["changed_lines_by_file"], metrics=metrics_r1
    )
    persist_iteration_and_metrics(
        Path(r1["output_dir"]), cache_root, manifest_r1, SAMPLE_RAW_OUTPUTS, agg_r1, metrics_r1
    )
    return repo, cache_root, {"r1": r1, "metrics": metrics_r1, "agg": agg_r1}


class ReviewHistoryAndMetricsTest(unittest.TestCase):
    """Verifies per-specialist timing metrics, history persistence, and incremental passes."""

    def test_round1_metrics_persistence_and_mcp_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cache_root, seeded = _seed_round1_repo_and_history(root)
            r1, metrics_r1, agg_r1 = seeded["r1"], seeded["metrics"], seeded["agg"]
            self.assertEqual(r1["round_number"], 1)
            self.assertEqual(r1["review_scope"], "full")
            self.assertEqual(r1["specialist_count"], FULL_SPECIALIST_COUNT)
            ast.parse(Path(r1["workflow_script_path"]).read_text(encoding="utf-8"))
            self.assertIn("## Swarm Telemetry & Agent Timing", agg_r1["markdown_report"])
            self.assertIn("`modularity-auditor`", agg_r1["markdown_report"])
            self.assertEqual(metrics_r1["bottleneck_specialist"], "modularity-auditor")
            self.assertEqual(len(load_recent_swarm_metrics(cache_root)), 1)

            with mock.patch(
                "commands.harness.mcp_server.get_harness_base_dir",
                return_value=root / "cache",
            ):
                mcp_state = execute_mcp_tool("aemu_get_harness_state", {})
                self.assertEqual(len(mcp_state["recent_review_swarm_metrics"]), 1)

    def test_round2_incremental_interdiff_and_force_full_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, cache_root, _ = _seed_round1_repo_and_history(Path(tmp))
            (repo / UNICODE_SRC_NAME).write_text(
                "header = 1\nline1\nline2\nmagic = 42\n", encoding="utf-8"
            )
            _git(repo, "add", UNICODE_SRC_NAME)
            _git(repo, "commit", "--amend", "-m", _sample_commit_message())

            r2 = prepare_review_swarm_bundle(commit="HEAD", repo_dir=repo, cache_root=cache_root)
            self.assertEqual(r2["round_number"], 2)
            self.assertEqual(r2["review_scope"], "incremental")
            self.assertEqual(r2["specialist_count"], EXPECTED_INCREMENTAL_SPECIALISTS)
            self.assertTrue(all(s["model"] == INCREMENTAL_PASS_MODEL for s in r2["specialists"]))
            self.assertNotIn(UNTOUCHED_SRC_NAME, r2["changed_lines_by_file"])
            self.assertEqual(r2["changed_lines_by_file"][UNICODE_SRC_NAME], [1, MAGIC_LINE_R2])

            workers_r2 = json.loads(Path(r2["swarm_briefs_path"]).read_text(encoding="utf-8"))
            const_worker = next(w for w in workers_r2 if w["name"] == "constants-auditor")
            self.assertIn("Extract magic number 42", const_worker["brief"])

            r2_full = prepare_review_swarm_bundle(
                commit="HEAD", repo_dir=repo, cache_root=cache_root, force_full=True
            )
            self.assertEqual(r2_full["review_scope"], "full")
            self.assertEqual(r2_full["specialist_count"], FULL_SPECIALIST_COUNT)

    def test_clean_rebase_zero_diff_fast_path_skips_specialist_launches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, cache_root = root / "repo", root / "cache" / "review-swarm"
            repo.mkdir()
            _git(repo, "init")
            (repo / UNICODE_SRC_NAME).write_text("clean_const = 1\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", _sample_commit_message())

            r1 = prepare_review_swarm_bundle(commit="HEAD", repo_dir=repo, cache_root=cache_root)
            manifest_r1 = json.loads(Path(r1["manifest_path"]).read_text(encoding="utf-8"))
            metrics_r1 = build_wave_metrics(manifest_r1, [], 1.0)
            agg_r1 = filter_and_aggregate_findings([], manifest_r1["changed_lines_by_file"], metrics=metrics_r1)
            persist_iteration_and_metrics(
                Path(r1["output_dir"]), cache_root, manifest_r1, [], agg_r1, metrics_r1
            )

            _git(
                repo, "commit", "--amend", "--date=2026-10-21T16:00:00Z", "-m", _sample_commit_message()
            )
            r2 = prepare_review_swarm_bundle(commit="HEAD", repo_dir=repo, cache_root=cache_root)
            self.assertEqual(r2["round_number"], 2)
            self.assertEqual(r2["review_scope"], "incremental")
            self.assertEqual(r2["specialist_count"], 0)
            self.assertEqual(len(load_iteration_history(SAMPLE_CHANGE_ID, cache_root)["iterations"]), 1)


if __name__ == "__main__":
    unittest.main()
