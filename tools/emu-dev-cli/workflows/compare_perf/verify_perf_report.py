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

"""Step 1 verifier for `compare-perf` workflow.

Ensures the Weekly Performance Qualification Report exists, includes all required
analysis sections, has no unresolved template placeholders, and covers every
workload measured in the comparison JSON.
"""

import argparse
import json
import os
import re
import sys

REQUIRED_SECTIONS = [
    "Executive Summary",
    "Scorecard",
    "Telemetry",
    "Regressions",
]

PLACEHOLDER_RE = re.compile(
    r"<(?:summary|baseline|candidate|root_cause|insert|todo|placeholder)[^>]*>",
    re.IGNORECASE,
)


def verify_perf_report(
    comparison_json: str,
    report_file: str,
) -> tuple[bool, str]:
    """Verifies the authored weekly performance qualification report."""
    if not os.path.isfile(report_file):
        return False, f"Weekly report file does not exist yet: {report_file}"

    with open(report_file, "r", encoding="utf-8") as f:
        content = f.read()

    if len(content.strip()) < 200:
        return (
            False,
            f"Weekly report file '{report_file}' is too short ({len(content.strip())} chars).",
        )

    missing_sections = [
        sec for sec in REQUIRED_SECTIONS if sec.lower() not in content.lower()
    ]
    if missing_sections:
        return (
            False,
            f"Weekly report '{report_file}' is missing required sections: {', '.join(missing_sections)}.",
        )

    m_ph = PLACEHOLDER_RE.search(content)
    if m_ph:
        return (
            False,
            f"Weekly report '{report_file}' contains unresolved placeholder '{m_ph.group(0)}'.",
        )

    if os.path.isfile(comparison_json):
        try:
            with open(comparison_json, "r", encoding="utf-8") as f:
                comp = json.load(f)
            comparisons = comp.get("comparisons") or []
            if isinstance(comparisons, dict):
                workloads = list(comparisons.keys())
            else:
                workloads = [
                    str(r.get("workload", ""))
                    for r in comparisons
                    if isinstance(r, dict) and r.get("workload")
                ]
            missing_workloads = [w for w in workloads if w not in content]
            if missing_workloads:
                return (
                    False,
                    f"Weekly report '{report_file}' omitted {len(missing_workloads)} measured workload(s) "
                    f"from '{comparison_json}': {', '.join(missing_workloads)}. "
                    f"Please include the complete 4-part scorecard tables so all numbers are visible.",
                )
        except Exception as exc:  # noqa: BLE001
            return (
                False,
                f"Failed to cross-check workloads against '{comparison_json}': {exc}",
            )

    return True, f"Weekly performance qualification report verified at '{report_file}'."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Step 1 weekly performance qualification report."
    )
    parser.add_argument("--comparison-json", required=True)
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args(argv)

    ok, msg = verify_perf_report(
        comparison_json=args.comparison_json,
        report_file=args.report_file,
    )
    if not ok:
        print(msg, file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
