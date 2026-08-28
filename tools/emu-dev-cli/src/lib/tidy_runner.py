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

"""Clang-tidy runner and fix application logic for emu-dev-cli."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from lib.tidy_types import TidyDiagnostic, TidyReplacement
from lib.tidy_parser import parse_fixes_yaml


def filter_diagnostics_by_level(
    diagnostics: List[TidyDiagnostic], max_level: int, checks: Optional[str] = None
) -> List[TidyDiagnostic]:
    """Filters diagnostics to only include those at or below max_level and matching the checks."""
    filtered = [d for d in diagnostics if d.level <= max_level]
    if checks:
        import fnmatch

        allowed_checks = [c.strip() for c in checks.split(",")]
        res = []
        for d in filtered:
            if any(fnmatch.fnmatch(d.name, c) for c in allowed_checks):
                res.append(d)
        return res
    return filtered


def apply_yaml_fixes(
    yaml_path: str,
    cwd: Optional[Path] = None,
    max_level: int = 1,
    checks: Optional[str] = None,
) -> int:
    """Applies fixes from a clang-tidy YAML file up to max_level, sorting in reverse offset order."""
    diags = parse_fixes_yaml(yaml_path)
    diags = filter_diagnostics_by_level(diags, max_level, checks)

    fixes_by_file: Dict[Path, List[TidyReplacement]] = {}
    for d in diags:
        for r in d.replacements:
            p = Path(r.file_path)
            if not p.is_absolute():
                base_p = cwd / p if cwd else p
                if not base_p.exists() and cwd:
                    cand_gf = cwd / "hardware" / "generic" / "goldfish" / p
                    cand_aemu = cwd / "hardware" / "google" / "aemu" / p
                    if cand_gf.exists():
                        base_p = cand_gf
                    elif cand_aemu.exists():
                        base_p = cand_aemu
                p = base_p
            fixes_by_file.setdefault(p, []).append(r)

    total_applied = 0
    for file_path, reps in fixes_by_file.items():
        if not file_path.exists():
            logging.warning("Skipping non-existent file: %s", file_path)
            continue

        with open(file_path, "rb") as f:
            content = f.read()

        # Sort in reverse offset order to prevent offset shifts
        sorted_reps = sorted(reps, key=lambda x: (-x.offset, -x.length))
        for rep in sorted_reps:
            prefix = content[: rep.offset]
            suffix = content[rep.offset + rep.length :]
            replacement_bytes = rep.replacement_text.encode("utf-8")
            content = prefix + replacement_bytes + suffix
            total_applied += 1

        with open(file_path, "wb") as f:
            f.write(content)

    return total_applied
