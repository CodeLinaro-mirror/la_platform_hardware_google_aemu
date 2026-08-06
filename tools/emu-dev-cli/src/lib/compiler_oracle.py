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

"""Compiler oracle for capturing ground-truth compilation errors during automated refactoring."""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
from typing import Dict, List, Optional, Set
from lib.path_utils import normalize_build_path

from lib.bazel import BazelRunner

logger = logging.getLogger(__name__)


@dataclass
class CompilerError:
    file_path: str
    line: int
    column: int
    message: str
    raw_snippet: str = ""
    error_kind: str = "error"
    notes: List[str] = field(default_factory=list)
    context: str = ""


@dataclass
class CompilerOracleReport:
    success: bool
    errors: List[CompilerError] = field(default_factory=list)
    raw_output: str = ""

    @property
    def affected_files(self) -> List[str]:
        seen = set()
        files = []
        for err in self.errors:
            if err.file_path and err.file_path not in seen:
                seen.add(err.file_path)
                files.append(err.file_path)
        return files

    def format_for_prompt(self, max_errors: int = 50) -> str:
        """Formats the compiler errors and diagnostic context into structured markdown."""
        if not self.errors:
            if not self.success and self.raw_output:
                snippet = self.raw_output.strip()
                if len(snippet) > 4000:
                    snippet = snippet[-4000:]
                return (
                    "Compilation or linking failed (exit code != 0), but no standard file:line errors were matched.\n\n"
                    f"```text\n{snippet}\n```"
                )
            return "No compilation errors detected."

        lines = []
        for i, err in enumerate(self.errors[:max_errors]):
            lines.append(
                f"- {err.file_path}:{err.line}:{err.column}: {err.error_kind}: {err.message}"
            )
            if err.context:
                lines.append(f"    ```cpp\n    {err.context.strip()}\n    ```")
            elif err.raw_snippet:
                lines.append(f"    ```cpp\n    {err.raw_snippet}\n    ```")
            for note in err.notes:
                lines.append(f"    * note: {note}")

        if len(self.errors) > max_errors:
            lines.append(
                f"\n... and {len(self.errors) - max_errors} additional compilation errors truncated."
            )

        return "\n".join(lines)


class CompilerOracle:
    """Invokes Bazel with --keep_going to uncover all broken call sites across the build graph."""

    # Matches compiler diagnostic header: path/to/file.cc:123:45: error: message
    ERROR_PATTERN = re.compile(
        r"(?:^|\n)\s*(?P<file>[^\s:]+\.(?:cc|cpp|c|h|hpp|inc|BUILD|bzl)):(?P<line>\d+):(?P<col>\d+):\s+(?P<kind>error|fatal error):\s+(?P<msg>[^\n]+)"
    )

    NOTE_PATTERN = re.compile(
        r"(?:^|\n)\s*(?:(?P<file>[^\s:]+\.(?:cc|cpp|c|h|hpp|inc)):(?P<line>\d+):(?P<col>\d+):\s+)?note:\s+(?P<msg>[^\n]+)"
    )

    LINKER_ERROR_PATTERN = re.compile(
        r"(?:^|\n)\s*(?:(?P<file>[^\s\[\]\:]+\.(?:cc|cpp|c|o|obj))(?:.*?\:\s*))?(?:(?:ld(?:\.lld|\.gold)?|clang\+\+|gcc|lld-link|LINK)[\s\:]*)?(?:(?:error|fatal error)[\s\:]*)?(?P<msg>(?:undefined (?:symbol|reference)|unresolved external symbol)[^\n]+|\b(?:undefined symbol|undefined reference to)\b[^\n]+)",
        re.IGNORECASE,
    )

    def __init__(self, runner: Optional[BazelRunner] = None) -> None:
        self.runner = runner or BazelRunner()

    def run_compilation_check(
        self,
        targets: List[str],
        flags: Optional[List[str]] = None,
        workspace_root: Optional[Path] = None,
    ) -> CompilerOracleReport:
        """Runs a fastbuild with keep_going flag to extract ground-truth call-site errors."""
        build_flags = ["--keep_going"]
        if flags:
            build_flags.extend(flags)

        logger.debug(
            "Executing CompilerOracle check for %s with flags: %s", targets, build_flags
        )
        res = self.runner.build(targets, flags=build_flags, check=False)
        output = (res.stderr or "") + "\n" + (res.stdout or "")

        errors = self.parse_errors(
            output, workspace_root=workspace_root, returncode=res.returncode
        )
        success = res.returncode == 0 and len(errors) == 0
        return CompilerOracleReport(success=success, errors=errors, raw_output=output)

    def parse_errors(
        self,
        compiler_output: str,
        workspace_root: Optional[Path] = None,
        returncode: int = 0,
    ) -> List[CompilerError]:
        """Extracts structured CompilerError objects from raw build logs including notes, traces, and linker errors."""
        errors: List[CompilerError] = []
        root = workspace_root or Path.cwd()

        matches = list(self.ERROR_PATTERN.finditer(compiler_output))
        for i, match in enumerate(matches):
            raw_path = match.group("file")
            line = int(match.group("line"))
            col = int(match.group("col"))
            kind = match.group("kind")
            msg = match.group("msg").strip()

            clean_path = normalize_build_path(raw_path)

            # Capture diagnostic body up to the next error match
            start_pos = match.end()
            end_pos = (
                matches[i + 1].start() if i + 1 < len(matches) else len(compiler_output)
            )
            diagnostic_body = compiler_output[start_pos:end_pos]

            notes: List[str] = []
            context_lines: List[str] = []

            for line_text in diagnostic_body.splitlines():
                stripped = line_text.strip()
                if not stripped:
                    continue
                note_match = self.NOTE_PATTERN.search(line_text)
                if note_match:
                    notes.append(note_match.group("msg").strip())
                context_lines.append(line_text)

            raw_snippet = ""
            for cand_root in [
                root,
                root / "hardware" / "generic" / "goldfish",
                root / "hardware" / "google" / "aemu",
            ]:
                full_file = cand_root / clean_path
                if full_file.exists() and full_file.is_file():
                    try:
                        file_lines = full_file.read_text(
                            encoding="utf-8", errors="ignore"
                        ).splitlines()
                        if 0 < line <= len(file_lines):
                            raw_snippet = file_lines[line - 1].strip()
                            break
                    except Exception:
                        pass

            errors.append(
                CompilerError(
                    file_path=clean_path,
                    line=line,
                    column=col,
                    message=msg,
                    raw_snippet=raw_snippet,
                    error_kind=kind,
                    notes=notes,
                    context="\n".join(context_lines) if context_lines else raw_snippet,
                )
            )

        # 2. Check for Linker errors (e.g. undefined symbol / undefined reference)
        for link_match in self.LINKER_ERROR_PATTERN.finditer(compiler_output):
            linker_msg = link_match.group("msg").strip()
            raw_file = link_match.group("file")
            if raw_file:
                raw_file = normalize_build_path(raw_file)
                if raw_file.endswith(".o") or raw_file.endswith(".obj"):
                    base_path = (
                        raw_file[:-2] if raw_file.endswith(".o") else raw_file[:-4]
                    )
                    root = workspace_root or Path.cwd()
                    found_src = None
                    for ext in [".cc", ".cpp", ".c", ".cxx"]:
                        for cand_root in [
                            root,
                            root / "hardware" / "generic" / "goldfish",
                            root / "hardware" / "google" / "aemu",
                        ]:
                            if (cand_root / f"{base_path}{ext}").is_file():
                                found_src = f"{base_path}{ext}"
                                break
                        if found_src:
                            break
                    if found_src:
                        raw_file = found_src
            # Avoid duplicate if already captured
            if not any(linker_msg in e.message for e in errors):
                errors.append(
                    CompilerError(
                        file_path=raw_file or "[linker]",
                        line=0,
                        column=0,
                        message=f"{linker_msg} (Fix Required: The definition/implementation of this symbol must also be renamed consistently, or BUILD file dependencies are incorrect.)",
                        raw_snippet="Linking Phase Failed (Check function bodies outside headers).",
                        error_kind="linker error",
                    )
                )

        # 3. Fallback: If returncode != 0 and errors is still empty, capture raw stderr excerpt
        if returncode != 0 and not errors:
            failure_lines = [
                l.strip()
                for l in compiler_output.splitlines()
                if (
                    "error:" in l or "FAILED:" in l or "undefined" in l or "fatal:" in l
                )
            ]
            fallback_msg = (
                "; ".join(failure_lines[:5])
                if failure_lines
                else "Build failed with non-zero exit code but no standard error pattern was matched."
            )
            errors.append(
                CompilerError(
                    file_path="[build]",
                    line=0,
                    column=0,
                    message=fallback_msg,
                    raw_snippet="",
                    error_kind="build failure",
                    context=(
                        compiler_output[-1500:].strip()
                        if len(compiler_output) > 1500
                        else compiler_output.strip()
                    ),
                )
            )

        return errors
