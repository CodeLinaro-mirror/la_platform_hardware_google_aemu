#!/usr/bin/env python3
"""Verifier for analyze-build-times workflow report."""

import os
import sys


REQUIRED_KEYWORDS = [
    "Target",
    "Baseline",
    "Critical Path",
    "Root Cause",
]


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: verify_report.py <report_file>", file=sys.stderr)
        return 1
    report_path = sys.argv[1]
    if not os.path.isfile(report_path):
        print(f"Report file does not exist: {report_path}", file=sys.stderr)
        return 1
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()
    if len(content.strip()) < 50:
        print(f"Report file '{report_path}' is too short.", file=sys.stderr)
        return 1
    missing = [kw for kw in REQUIRED_KEYWORDS if kw.lower() not in content.lower()]
    if missing:
        print(
            f"Report file '{report_path}' is missing required sections/keywords: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
