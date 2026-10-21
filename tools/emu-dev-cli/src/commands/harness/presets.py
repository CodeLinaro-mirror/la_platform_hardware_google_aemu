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
        "title": "3-Agent Parallel Code Review Swarm",
        "summary": (
            "Spawns 3 specialized review subagents in parallel (Library Reuse Auditor, "
            "Error Message & StatusOr Auditor, and 5-Way Complexity & Unicode Auditor) "
            "and aggregates findings."
        ),
        "prompt": (
            "Run a 3-agent parallel code review on the current commit/CL by invoking "
            "`aemu-code-reviewer` across three focused angles in parallel:\n"
            "1. Library Reuse Audit (`//emulator/libs/*`, headers, test servers).\n"
            "2. Error Message & TotW 181 Audit (`go/error-messages` 4-part structure, "
            "canonical `absl::StatusCode`, no `_or` suffixes).\n"
            "3. 5-Way Code Complexity, Unicode Path Safety & Semantic Commit Audit.\n"
            "Synthesize a single deduplicated review report with `Reuse audit: N checked, "
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
