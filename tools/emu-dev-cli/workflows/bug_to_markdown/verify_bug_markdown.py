#!/usr/bin/env python3
"""Verifier script for bug_to_markdown workflow.

Validates that a valid b<bug number>.md exists in the target directory
and contains all required fields:
- Bug number: <number>
- Status: <bug status>
- Assignee: <ldap or None>
- Problem: <Summary of the problem>
- Proposed Fix: <Proposed fix, or N/A if no proposed fix in bug>
"""

import os
from pathlib import Path
import re
import sys
from typing import List, Optional, Tuple

REQUIRED_FIELDS = [
    ("Bug number", r"(?i)^\s*[-*]?\s*\*{0,2}Bug(?:\s+number)?\*{0,2}\s*:\s*(\S+)"),
    ("Status", r"(?i)^\s*[-*]?\s*\*{0,2}Status\*{0,2}\s*:\s*(.+)"),
    ("Assignee", r"(?i)^\s*[-*]?\s*\*{0,2}Assignee\*{0,2}\s*:\s*(.+)"),
    ("Problem", r"(?i)^\s*[-*]?\s*\*{0,2}Problem\*{0,2}\s*:\s*(.+)"),
    ("Proposed Fix", r"(?i)^\s*[-*]?\s*\*{0,2}Proposed\s+Fix\*{0,2}\s*:\s*(.+)"),
]


def check_file(filepath: Path, expected_bug: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Checks if a markdown file contains all required fields and matches expected bug."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    missing = []
    parsed_fields = {}
    for field_name, pattern in REQUIRED_FIELDS:
        match = re.search(pattern, content, re.MULTILINE)
        if not match or not match.group(1).strip():
            missing.append(field_name)
        else:
            parsed_fields[field_name] = match.group(1).strip()

    if missing:
        return False, missing

    if expected_bug:
        clean_expected = re.sub(r"^(?:https?://)?b(?:/)?", "", expected_bug.strip(), flags=re.I)
        actual_bug = re.sub(r"^(?:https?://)?b(?:/)?", "", parsed_fields.get("Bug number", "").strip(), flags=re.I)
        if clean_expected and actual_bug != clean_expected:
            return False, [f"Bug number mismatch (expected {clean_expected}, found {actual_bug})"]

    return True, []


def verify(target_path: str, expected_bug: Optional[str] = None) -> int:
    """Verifies target file or directory containing b<number>.md."""
    target = Path(target_path).expanduser().resolve()
    clean_bug = (
        re.sub(r"^(?:https?://)?b(?:/)?", "", expected_bug.strip(), flags=re.I)
        if expected_bug
        else None
    )

    if target.is_file():
        candidates = [target]
    elif target.is_dir():
        if clean_bug:
            specific_file = target / f"b{clean_bug}.md"
            if specific_file.is_file():
                candidates = [specific_file]
            else:
                print(
                    f"Error: Expected bug markdown file 'b{clean_bug}.md' not found in '{target}'.",
                    file=sys.stderr,
                )
                return 1
        else:
            all_files = [
                p for p in target.iterdir()
                if p.is_file() and re.match(r"^b\d+.*\.md$", p.name, re.IGNORECASE)
            ]
            all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            candidates = all_files
    else:
        print(f"Error: Target path '{target}' does not exist.", file=sys.stderr)
        return 1

    if not candidates:
        print(
            f"Error: No 'b<bug number>.md' file found in '{target}'.",
            file=sys.stderr,
        )
        return 1

    latest_file = candidates[0]
    valid, errors = check_file(latest_file, expected_bug=clean_bug)
    if not valid:
        print(
            f"Error: In '{latest_file.name}', missing or empty required field(s): {', '.join(errors)}.",
            file=sys.stderr,
        )
        return 1

    print(f"Success: '{latest_file.name}' verified with all required fields.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 verify_bug_markdown.py <file-or-directory> [bug-number]", file=sys.stderr)
        sys.exit(1)
    target_arg = sys.argv[1]
    expected_bug_arg = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(verify(target_arg, expected_bug_arg))
