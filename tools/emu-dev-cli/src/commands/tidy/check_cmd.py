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

"""Diagnostic scanner and table renderer for emu-dev-cli tidy check."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional

from commands.source_directory import get_source_directory
from lib.bazel import BazelRunner, BuildExitCode
from lib.output import format_markdown_table
from lib.tidy_types import TidyDiagnostic
from lib.tidy_parser import normalize_tidy_level, parse_fixes_yaml
from lib.tidy_runner import filter_diagnostics_by_level
from lib.target_resolver import resolve_report_targets

_resolve_report_targets = resolve_report_targets


def handle_tidy_check(args: argparse.Namespace) -> None:
    """Scans and checks clang-tidy diagnostics for a given target, file, or git diff."""
    target = getattr(args, "target_flag", None) or getattr(args, "target", None)
    filter_diff = getattr(args, "diff", False)
    file_filter = getattr(args, "file", None)
    level = normalize_tidy_level(getattr(args, "level", 4))

    is_json = getattr(args, "json", False)

    if not target and not filter_diff and not file_filter:
        print(
            "Error: Must specify either a positional target, --diff, or --file",
            file=sys.stderr,
        )
        sys.exit(1)

    source_dir = get_source_directory("emu-main-next") or os.getcwd()
    runner = BazelRunner(source_dir=source_dir)
    diagnostics: List[TidyDiagnostic] = []

    if target:
        report_targets = _resolve_report_targets(runner, target)
        logging.info(f"Building tidy report for {len(report_targets)} targets...")
        res = runner.build(
            report_targets,
            flags=["--@goldfish_build//:clang_tidy_enabled=true", "--keep_going"],
            check=False,
            capture_output=True,
        )
        if res.returncode != 0:
            logging.warning("Report build failed with exit code: %s", res.returncode)
            if res.stderr:
                logging.warning("Build logs:\n%s", res.stderr)

        for report_target in report_targets:
            clean_target = (
                report_target.lstrip("@")
                .replace("goldfish//", "")
                .replace("android_emulator//", "")
                .lstrip("/")
            )
            if ":" in clean_target:
                pkg, rname = clean_target.split(":", 1)
                bases = [Path.cwd()]
                if source_dir:
                    bases.append(Path(source_dir))
                found_yaml = False
                for base in bases:
                    for cand in [
                        base
                        / f"bazel-bin/external/goldfish+/{pkg}/{rname}.final_fixes.yaml",
                        base
                        / f"bazel-bin/external/android_emulator+/{pkg}/{rname}.final_fixes.yaml",
                        base / f"bazel-bin/{pkg}/{rname}.final_fixes.yaml",
                    ]:
                        if cand.exists():
                            diagnostics.extend(parse_fixes_yaml(str(cand)))
                            found_yaml = True
                            break
                    if found_yaml:
                        break

    else:
        # If no target provided, attempt to load all previously built YAML reports
        bases = [Path.cwd()]
        if source_dir:
            bases.append(Path(source_dir))
        found_any = False
        for base in bases:
            try:
                for cand in base.rglob("*.final_fixes.yaml"):
                    diagnostics.extend(parse_fixes_yaml(str(cand)))
                    found_any = True
            except Exception as e:
                logging.debug("Error globbing final_fixes: %s", e)
        if not found_any:
            logging.warning(
                "No existing tidy diagnostic reports found. Supply a target to explicitly build them."
            )

    diagnostics = filter_diagnostics_by_level(
        diagnostics, level, getattr(args, "checks", None)
    )

    if file_filter:
        diagnostics = [d for d in diagnostics if file_filter in d.file_path]

    if filter_diff:
        try:
            diff_res = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            modified_files = set(diff_res.stdout.strip().splitlines())
            diagnostics = [
                d for d in diagnostics if any(m in d.file_path for m in modified_files)
            ]
        except Exception as e:
            logging.warning("Failed to query git diff: %s", e)

    if is_json:
        data = [
            {
                "name": d.name,
                "file_path": d.file_path,
                "line": d.line,
                "column": d.column,
                "level": d.level,
                "message": d.message,
                "replacements_count": len(d.replacements),
            }
            for d in diagnostics
        ]
        print(
            json.dumps(
                {"target": target, "total": len(data), "diagnostics": data}, indent=2
            )
        )
        return

    if not diagnostics:
        print(
            f"\n✨ Target is clean! Zero clang-tidy diagnostics found for Level {level}."
        )
        return

    headers = ["Level", "Check Name", "Location", "Message"]
    rows = []
    for d in diagnostics:
        loc = f"{os.path.basename(d.file_path)}:{d.line}:{d.column}"
        rows.append(
            [
                f"L{d.level}",
                d.name,
                loc,
                d.message[:60] + ("..." if len(d.message) > 60 else ""),
            ]
        )

    print(f"\n### Clang-Tidy Diagnostics (Level {level}) - {len(diagnostics)} found\n")
    print(format_markdown_table(headers, rows))
