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

"""Rule generator and injector for emu-dev-cli tidy add."""

import argparse
import logging
import os
from pathlib import Path
import re
import sys
from typing import List, Optional, Tuple

from commands.source_directory import get_source_directory

logger = logging.getLogger(__name__)


def _extract_cc_targets(content: str) -> List[str]:
    """Discovers all cc_library, cc_binary, and cc_test rule names in BUILD.bazel content."""
    targets = []
    rule_types = ["cc_library", "cc_binary", "cc_test"]
    for rtype in rule_types:
        name_pattern = re.compile(r"name\s*=\s*[\"']([^\"']+)[\"']")
        for m in re.finditer(rf"\b{rtype}\s*\(", content):
            start = m.end()
            # Constrain regex engine lookup boundary by explicitly walking parens
            # to prevent attributing names from subsequent independent targets.
            open_parens = 1
            idx = start
            while idx < len(content) and open_parens > 0:
                if content[idx] == "(":
                    open_parens += 1
                elif content[idx] == ")":
                    open_parens -= 1
                idx += 1

            name_match = name_pattern.search(content, pos=start, endpos=idx)
            if name_match:
                name = name_match.group(1).strip()
                if name and name not in targets:
                    targets.append(name)
    return targets


def _insert_load_statement(content: str, load_stmt: str) -> str:
    """Inserts load statement after top-of-file license comments."""
    if "rules:clang_tidy.bzl" in content and "clang_tidy_test" in content:
        return content

    lines = content.splitlines(keepends=True)
    insert_idx = 0

    # Skip initial license comment block and empty lines
    while insert_idx < len(lines):
        line = lines[insert_idx].strip()
        if line.startswith("#") or line == "":
            insert_idx += 1
        else:
            break

    stmt = load_stmt if load_stmt.endswith("\n") else load_stmt + "\n"
    lines.insert(insert_idx, stmt)
    return "".join(lines)


def _update_or_append_clang_tidy_rule(
    content: str, formatted_targets: List[str]
) -> Tuple[str, int]:
    """Updates existing targets list in clang_tidy_test or appends a new rule."""
    pattern = re.compile(
        r"(clang_tidy_test\s*\([^)]*targets\s*=\s*\[)([^\]]*)(\])", re.DOTALL
    )
    match = pattern.search(content)

    if match:
        prefix, existing_targets_block, suffix = match.groups()
        existing_targets = re.findall(r'"([^"]+)"|\'([^\']+)\'', existing_targets_block)
        existing_set = {t[0] or t[1] for t in existing_targets}

        new_targets_to_add = [t for t in formatted_targets if t not in existing_set]
        if not new_targets_to_add:
            return content, 0

        added_lines = "\n".join([f'        "{t}",' for t in new_targets_to_add])
        if existing_targets_block.strip():
            trimmed_block = existing_targets_block.rstrip()
            if trimmed_block and not trimmed_block.endswith(","):
                trimmed_block += ","
            updated_block = trimmed_block + "\n" + added_lines + "\n    "
        else:
            updated_block = "\n" + added_lines + "\n    "

        new_content = (
            content[: match.start()]
            + prefix
            + updated_block
            + suffix
            + content[match.end() :]
        )
        return new_content, len(new_targets_to_add)

    # Append tidy test declaration
    targets_str = "\n".join([f'        "{t}",' for t in formatted_targets])
    tidy_rule = f"""
clang_tidy_test(
    name = "tidy",
    targets = [
{targets_str}
    ],
    tidy_config_file = "//:clang_tidy_config",
)
"""
    return content.rstrip() + "\n" + tidy_rule, len(formatted_targets)


def _resolve_build_file(
    raw_path_str: str, source_dir: str
) -> Tuple[Optional[Path], List[str]]:
    """Resolves raw path string to a BUILD.bazel / BUILD path across CWD, workspace root, and Bazel labels."""
    raw_str = raw_path_str.strip()

    # Normalize Bazel label target suffix if user passed "@goldfish//emulator/libs/sockets:sockets"
    if (
        ":" in raw_str
        and not raw_str.endswith(".bazel")
        and not raw_str.endswith("BUILD")
    ):
        raw_str = raw_str.split(":", 1)[0]

    # Clean Bazel repository prefixes
    clean_label = (
        raw_str.replace("@goldfish//", "").replace("@aemu//", "").replace("//", "")
    )

    candidates_to_try = [
        Path(raw_str),
        Path.cwd() / raw_str,
        Path(source_dir) / raw_str,
        Path(source_dir) / clean_label,
        Path(source_dir) / "hardware/generic/goldfish" / clean_label,
        Path(source_dir) / "hardware/google/aemu" / clean_label,
    ]

    # Deduplicate candidates while preserving order
    seen = set()
    unique_candidates_to_try = []
    for cand in candidates_to_try:
        resolved = cand.resolve() if cand.exists() else cand
        if resolved not in seen:
            seen.add(resolved)
            unique_candidates_to_try.append(cand)

    for cand in unique_candidates_to_try:
        try:
            if cand.is_file() and (cand.name == "BUILD.bazel" or cand.name == "BUILD"):
                return cand.resolve(), []
            if cand.is_dir():
                if (cand / "BUILD.bazel").is_file():
                    return (cand / "BUILD.bazel").resolve(), []
                if (cand / "BUILD").is_file():
                    return (cand / "BUILD").resolve(), []
        except Exception:
            continue

    # Collect nearest matching package suggestions across workspace
    suggestions = []
    base_name = Path(clean_label).name
    try:
        root_path = Path(source_dir)
        for d in root_path.glob(f"**/{base_name}"):
            if d.is_dir() and ((d / "BUILD.bazel").exists() or (d / "BUILD").exists()):
                rel = d.relative_to(root_path)
                suggestions.append(str(rel))
                if len(suggestions) >= 3:
                    break
    except Exception:
        pass

    return None, suggestions


def handle_tidy_add(args: argparse.Namespace) -> None:
    """Injects or configures a clang_tidy_test rule into the specified BUILD.bazel file or package directory."""
    raw_path_str = getattr(args, "path", None) or getattr(args, "build_file", None)
    if not raw_path_str:
        print(
            "Error: Target BUILD.bazel path or package directory is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    source_dir = get_source_directory("emu-main-next") or os.getcwd()
    build_path, suggestions = _resolve_build_file(raw_path_str, source_dir)

    if not build_path or not build_path.exists():
        print(
            f"❌ Error: Target BUILD.bazel file does not exist for '{raw_path_str}'.\n",
            file=sys.stderr,
        )
        print("💡 Actionable suggestions:", file=sys.stderr)
        print(
            "  1. Pass the path relative to workspace root or use Bazel label syntax:",
            file=sys.stderr,
        )
        print(
            "     • emu-dev-cli tidy add emulator/libs/sockets",
            file=sys.stderr,
        )
        print(
            "     • emu-dev-cli tidy add @goldfish//emulator/libs/sockets",
            file=sys.stderr,
        )
        if suggestions:
            print("  2. Did you mean one of these existing packages?", file=sys.stderr)
            for s in suggestions:
                print(f"     • {s}", file=sys.stderr)
        print(
            f"  3. If this is a new package, create the BUILD.bazel file first:\n     touch {raw_path_str}/BUILD.bazel\n",
            file=sys.stderr,
        )
        sys.exit(1)

    targets: List[str] = getattr(args, "targets", []) or []
    content = build_path.read_text(encoding="utf-8")

    # Auto-discover all cc_library, cc_binary, and cc_test targets if --target was omitted
    if not targets:
        auto_targets = _extract_cc_targets(content)
        if auto_targets:
            print(
                f"🔍 Discovered {len(auto_targets)} C++ target(s) in {build_path.name}: {', '.join(auto_targets)}"
            )
            targets = auto_targets
        else:
            print(
                f"⚠️ Warning: No cc_library, cc_binary, or cc_test targets found in {build_path}.",
                file=sys.stderr,
            )

    formatted_targets = [
        t if t.startswith(":") or t.startswith("//") or t.startswith("@") else f":{t}"
        for t in targets
    ]

    # 1. Ensure load statement is placed after license comments
    load_stmt = 'load("@goldfish_build//rules:clang_tidy.bzl", "clang_tidy_test")\n'
    content = _insert_load_statement(content, load_stmt)

    # 2. Update existing rule or append new rule
    new_content, count = _update_or_append_clang_tidy_rule(content, formatted_targets)
    build_path.write_text(new_content, encoding="utf-8")

    if count > 0:
        print(
            f"✅ Successfully configured clang_tidy_test rule in {build_path} (added {count} target(s))."
        )
    else:
        print(
            f"ℹ️ All specified targets are already configured in clang_tidy_test in {build_path}."
        )
