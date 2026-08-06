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

"""Standardized CLI output formatting for Markdown and JSON responses."""

import json
import sys


def format_markdown_table(headers: list, rows: list) -> str:
    """Renders an aligned GitHub-flavored Markdown table from headers and row data."""
    if not rows:
        return "*No records found.*"

    str_headers = [str(h) for h in headers]
    str_rows = [[str(cell) for cell in row] for row in rows]

    num_cols = len(str_headers)
    # Calculate max width for each column (minimum 3 chars for standard separator '---')
    col_widths = [max(3, len(str_headers[i])) for i in range(num_cols)]
    for row in str_rows:
        for i in range(min(num_cols, len(row))):
            col_widths[i] = max(col_widths[i], len(row[i]))

    header_line = (
        "| "
        + " | ".join(
            str_headers[i].ljust(col_widths[i]) for i in range(num_cols)
        )
        + " |"
    )
    separator_line = (
        "| " + " | ".join("-" * col_widths[i] for i in range(num_cols)) + " |"
    )
    row_lines = [
        "| "
        + " | ".join(
            (row[i] if i < len(row) else "").ljust(col_widths[i])
            for i in range(num_cols)
        )
        + " |"
        for row in str_rows
    ]
    return "\n".join([header_line, separator_line] + row_lines)


def print_result(data: dict, json_mode: bool = False, is_error: bool = False):
    """Prints output in either clean JSON format or human-readable terminal format."""
    if json_mode:
        output_str = json.dumps(data, indent=2)
        if is_error:
            print(output_str, file=sys.stderr)
        else:
            print(output_str)
    else:
        status_symbol = "❌ ERROR" if is_error else "✅ SUCCESS"
        print(f"\n{status_symbol}: {data.get('summary', data.get('action', 'done'))}")
        for key, val in data.items():
            if key in ("summary", "status"):
                continue
            if key == "markdown_table":
                print(f"\n{val}\n")
            elif key == "proposed_bug_markdown":
                print(
                    f"\n--- Proposed Bug Report (Markdown Preview) ---\n\n{val}\n-------------------------------------------------\n"
                )
            elif isinstance(val, list):
                print(f"  • {key}: [{len(val)} items]")
            elif isinstance(val, dict):
                pass
            else:
                print(f"  • {key}: {val}")
        print()

    if is_error:
        sys.exit(data.get("exit_code", 1))
    else:
        sys.exit(0)
