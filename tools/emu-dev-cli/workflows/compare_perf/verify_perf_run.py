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

"""Step 0 verifier for `compare-perf` workflow.

Validates that `perf compare` produced a complete comparison JSON artifact
covering the requested suites and renders the 4-part Markdown scorecard.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent
_EMU_DEV_CLI_DIR = _WORKFLOW_DIR.parent.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from lib.perf_runner import (
    render_comparison_markdown,
    render_weekly_qualification_report,
    resolve_suites,
)


def _workload_matched_in_rows(workload: str, row_names: set[str]) -> bool:
    if workload in row_names:
        return True
    if any(r.startswith(workload) for r in row_names):
        return True
    if workload == "display_gfxinfo" and any(
        r.startswith("display_") for r in row_names
    ):
        return True
    return False


def extract_row_names(comparisons: Any) -> set[str]:
    """Extracts workload row names from `comp['comparisons']` (list of dicts or dict)."""
    if isinstance(comparisons, dict):
        return set(comparisons.keys())
    if isinstance(comparisons, list):
        return {
            str(r.get("workload", ""))
            for r in comparisons
            if isinstance(r, dict) and r.get("workload")
        }
    return set()


def verify_perf_run(
    comparison_json: str,
    raw_scorecard: str,
    suite: str = "all",
    report_file: str | None = None,
) -> tuple[bool, str]:
    """Verifies comparison JSON completeness and writes the raw Markdown scorecard (and optional report)."""
    if not os.path.isfile(comparison_json):
        return (
            False,
            f"Comparison JSON file does not exist yet: {comparison_json}",
        )

    try:
        with open(comparison_json, "r", encoding="utf-8") as f:
            comp = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Failed to parse comparison JSON '{comparison_json}': {exc}",
        )

    if not isinstance(comp, dict):
        return False, f"Comparison JSON '{comparison_json}' root must be a dict."

    has_labels = (
        "baseline_label" in comp and "candidate_label" in comp
    ) or ("baseline" in comp and "candidate" in comp)
    if not has_labels or "comparisons" not in comp:
        return (
            False,
            f"Comparison JSON '{comparison_json}' is missing required keys ('baseline_label', 'candidate_label', 'comparisons').",
        )

    row_names = extract_row_names(comp.get("comparisons"))
    if not row_names:
        return (
            False,
            f"Comparison JSON '{comparison_json}' has empty 'comparisons'.",
        )

    try:
        _, expected_workloads = resolve_suites(suite)
    except Exception:  # noqa: BLE001
        expected_workloads = []

    missing_workloads = [
        w
        for w in expected_workloads
        if not _workload_matched_in_rows(w, row_names)
    ]
    if missing_workloads:
        return (
            False,
            f"Comparison JSON '{comparison_json}' is missing expected workloads for suite '{suite}': "
            f"{', '.join(missing_workloads)}",
        )

    try:
        scorecard_md = render_comparison_markdown(comp)
        os.makedirs(os.path.dirname(os.path.abspath(raw_scorecard)), exist_ok=True)
        with open(raw_scorecard, "w", encoding="utf-8") as f:
            f.write(scorecard_md + "\n")
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Failed to render 4-part Markdown scorecard to '{raw_scorecard}': {exc}",
        )

    if report_file and not os.path.isfile(report_file):
        try:
            report_md = render_weekly_qualification_report(comp)
            os.makedirs(os.path.dirname(os.path.abspath(report_file)), exist_ok=True)
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report_md + "\n")
        except Exception as exc:  # noqa: BLE001
            return (
                False,
                f"Failed to render weekly qualification report to '{report_file}': {exc}",
            )

    return (
        True,
        f"Verified {len(row_names)} comparison rows in '{comparison_json}' and rendered 4-part scorecard to '{raw_scorecard}'.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Step 0 comparison JSON and generate 4-part scorecard."
    )
    parser.add_argument("--comparison-json", required=True)
    parser.add_argument("--raw-scorecard", required=True)
    parser.add_argument("--report-file", default=None)
    parser.add_argument("--suite", default="all")
    args = parser.parse_args(argv)

    ok, msg = verify_perf_run(
        comparison_json=args.comparison_json,
        raw_scorecard=args.raw_scorecard,
        suite=args.suite,
        report_file=args.report_file,
    )
    if not ok:
        print(msg, file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
