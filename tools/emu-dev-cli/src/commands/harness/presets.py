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

"""Multi-agent swarm and workflow orchestration presets for the AEMU Harness."""

from typing import Dict, List, Optional

HARNESS_SWARM_PRESETS: List[Dict[str, str]] = [
    {
        "name": "parallel-code-review",
        "title": "8-Specialist Single-Wave Parallel Code-Review Swarm",
        "summary": (
            "Fans out 8 non-overlapping review specialists in a single wave (`run_workflow` / "
            "`swarm`) covering Commit/Bug, Duplication/Reuse, Modularity, Control Flow, "
            "Constants, Unicode/Platform, Error Messages/StatusOr, and Concurrency/Safety, "
            "then deterministically filters findings to modified diff lines."
        ),
        "prompt": (
            "Run `emu-dev-cli harness review-swarm` (or `aemu_prepare_review_swarm`) on the "
            "target CL/commit, then execute the generated `run_review_swarm_workflow.py` via "
            "`run_workflow` to fan out all 8 review specialists (`aemu-code-reviewer`) in parallel "
            "and synthesize a diff-bounded report with `Reuse audit: N abstractions checked, "
            "M duplicates` and `Error-message audit: N sites checked, M findings`."
        ),
    },
    {
        "name": "parallel-flake-hunt",
        "title": "Parallel Sanitizer & Stress Flake Hunt",
        "summary": (
            "Coordinates Sponge telemetry analysis alongside parallel TSAN and ASAN "
            "50x stress reproduction runs via `aemu-flake-hunter`."
        ),
        "prompt": (
            "Invoke `aemu-flake-hunter` to investigate the flaky test target: "
            "first inspect `emu-dev-cli flakiness history` and `sponge` logs, then "
            "attempt 50x stress reproduction under `--config=tsan` and `--config=asan` "
            "before proposing a root-cause fix."
        ),
    },
    {
        "name": "tdd-bug-fix-verified",
        "title": "TDD Bug Fix + Adversarial Code Reviewer",
        "summary": (
            "Executes `emu-dev-cli workflow test-driven-bug-fixer` and verifies the "
            "resulting test and fix commits with `aemu-code-reviewer`."
        ),
        "prompt": (
            "Run the `emu-dev-cli workflow test-driven-bug-fixer` workflow to write a "
            "failing reproduction test first and implement the fix, then invoke "
            "`aemu-code-reviewer` to audit both commits before uploading to Gerrit."
        ),
    },
]


def list_harness_presets(name: Optional[str] = None) -> List[Dict[str, str]]:
    """Returns all harness swarm presets, or filters by preset name."""
    if not name:
        return list(HARNESS_SWARM_PRESETS)
    target = name.strip().lower()
    return [p for p in HARNESS_SWARM_PRESETS if p["name"] == target]
