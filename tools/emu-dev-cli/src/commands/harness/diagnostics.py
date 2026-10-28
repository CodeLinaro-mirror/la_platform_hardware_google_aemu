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

"""PostToolUse failure analyzer and diagnostic enricher for the AEMU Harness.

Inspects failed `run_command` outputs (TSAN, ASAN, missing `//emulator/libs/*`
headers, Windows UTF-8 path errors) and returns a concise diagnostic block to
append via Jetski's PostToolUse `overwriteResult` contract.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from commands.harness.goldfish_libs import target_for_include

_MISSING_HEADER_MARKERS: Tuple[str, ...] = (
    "No such file or directory",
    "file not found",
    "undeclared inclusion",
)
# A header token must start at a token boundary, which keeps the scan linear on long
# `[\w./+-]` runs, and must not be followed by `:LINE`, which marks a diagnostic
# location or an `In file included from` frame rather than the missing header.
_HEADER_TOKEN_PATTERN = re.compile(r"(?<![\w./+-])[\w./+-]+\.h\b(?!:\d)")
# Jetski prefixes failed `run_command` results with this line; successful commands
# (for example `sed` or `grep` over test files) can quote failure strings verbatim.
# `[ \t]*` instead of `\s*` keeps `^` from scanning across runs of blank lines.
_FAILED_EXIT_CODE_PATTERN = re.compile(r"(?m)^[ \t]*(?:Command )?exited with code [1-9]\d*\b")


def _target_for_header(header_token: str) -> Optional[Tuple[str, str]]:
    """Maps a header path from a compiler or Bazel error to (include path, Bazel target)."""
    include_path = header_token.rsplit("include/", 1)[-1]
    target = target_for_include(include_path)
    return None if target is None else (include_path, target)


def diagnose_command_failure(
    command: str,
    result_text: str,
    error_text: str = "",
) -> Optional[str]:
    """Analyzes command output for known Android Emulator failure patterns.

    Returns:
        A formatted `[AEMU Harness Auto-Diagnostic]` hint block if a known failure
        signature is detected, or None otherwise.
    """
    combined = f"{result_text}\n{error_text}".strip()
    if not combined:
        return None

    hints: List[str] = []

    if "WARNING: ThreadSanitizer: data race" in combined:
        hints.append(
            "ThreadSanitizer data race detected: reproduce deterministically with "
            "`bazel test <target> --config=tsan --runs_per_test=50` and consult "
            "`emu-dev-cli docs playbook-threading-and-invariants` (prefer "
            "`goldfish::async::ThreadedEventLoop` or `absl::Mutex` over sleep/atomics)."
        )

    if "ERROR: AddressSanitizer:" in combined:
        hints.append(
            "AddressSanitizer memory error detected: reproduce with "
            "`bazel test <target> --config=asan --runs_per_test=20` and audit object "
            "lifetimes across async callbacks and event-loop teardown."
        )

    if any(marker in combined for marker in _MISSING_HEADER_MARKERS):
        seen_headers: Set[str] = set()
        for header_token in _HEADER_TOKEN_PATTERN.findall(combined):
            mapped = _target_for_header(header_token)
            if mapped is None or mapped[0] in seen_headers:
                continue
            include_path, bazel_target = mapped
            seen_headers.add(include_path)
            hints.append(
                f"Missing header `{include_path}`: add `{bazel_target}` to the "
                "target's `deps` in `BUILD.bazel`."
            )

    if "undeclared inclusion(s)" in combined and not hints:
        hints.append(
            "Bazel strict header check failed (`undeclared inclusion`): add the "
            "direct library providing the included header to `deps` in `BUILD.bazel`."
        )

    if not hints:
        return None

    bullet_lines = "\n".join(f"  * {h}" for h in hints)
    return f"[AEMU Harness Auto-Diagnostic]\n{bullet_lines}"


def enrich_post_tool_result(
    command: str,
    payload: Dict[str, object],
) -> Optional[Tuple[str, str]]:
    """Checks a PostToolUse payload and builds an `overwriteResult` string if applicable.

    Only failed commands (a PostToolUse `error`, or a non-zero exit code line) are
    diagnosed, so successful commands that merely print failure strings are left alone.

    Returns:
        None if no diagnostic applies, or (overwritten_result, diagnostic_summary).
    """
    raw_result = str(payload.get("result") or "")
    raw_error = str(payload.get("error") or "")
    if not raw_error.strip() and not _FAILED_EXIT_CODE_PATTERN.search(raw_result):
        return None
    diagnostic = diagnose_command_failure(command, raw_result, raw_error)
    if not diagnostic:
        return None

    if raw_result:
        overwritten = f"{raw_result.rstrip()}\n\n{diagnostic}\n"
    else:
        overwritten = f"{diagnostic}\n"
    first_line = diagnostic.splitlines()[1].strip().lstrip("* ")
    return overwritten, first_line
