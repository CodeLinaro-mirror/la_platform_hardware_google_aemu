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

import os
from pathlib import Path
import re
import sys


def verify_rating_file(filepath: Path) -> int:
    if not filepath.is_file():
        print(f"Error: Rating file does not exist: '{filepath}'", file=sys.stderr)
        return 1

    try:
        content = filepath.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"Error reading file '{filepath}': {e}", file=sys.stderr)
        return 1

    if not content:
        print(f"Error: Rating file '{filepath}' is empty.", file=sys.stderr)
        return 1

    lines = content.splitlines()

    # Look for rating line matching: Rating: x.x / 10 or Rating: x / 10
    rating_pattern = re.compile(r"^\s*Rating:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10\s*$", re.IGNORECASE)
    rating_val = None
    summary_lines = []

    for line in lines:
        stripped = line.strip()
        m = rating_pattern.match(stripped)
        if m:
            try:
                score = float(m.group(1))
                if 0.0 <= score <= 10.0:
                    rating_val = score
                else:
                    print(
                        f"Error: Rating value {score} is out of range [0.0, 10.0].",
                        file=sys.stderr,
                    )
                    return 1
            except ValueError:
                pass
        else:
            summary_lines.append(line)

    if rating_val is None:
        print(
            "Error: Rating file is missing a valid rating line (expected 'Rating: x.x / 10' or 'Rating: x / 10').",
            file=sys.stderr,
        )
        return 1

    summary_text = " ".join(summary_lines).strip()
    words = [w for w in re.split(r"\s+", summary_text) if w]

    # Validate that there is a substantive summary paragraph (at least 15 words)
    if len(words) < 15:
        print(
            f"Error: Summary is too brief ({len(words)} words found; minimum 15 words required for pros and cons).",
            file=sys.stderr,
        )
        return 1

    print(f"Rating verified successfully: {rating_val:.1f} / 10 with {len(words)} word review summary.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 verify_joke_rating.py <path-to-rating-file>", file=sys.stderr)
        sys.exit(1)

    target_file = Path(sys.argv[1]).resolve()
    sys.exit(verify_rating_file(target_file))
