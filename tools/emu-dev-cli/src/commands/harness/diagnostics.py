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

from typing import Dict, List, Optional, Tuple

GOLDFISH_HEADER_TO_TARGET: Tuple[Tuple[str, str], ...] = (
    ("goldfish/file/file.h", "//emulator/libs/file"),
    ("android/goldfish/ini_file.h", "//emulator/libs/ini_file"),
    ("goldfish/http/web_server.h", "//emulator/libs/http"),
    ("goldfish/network/http_client.h", "//emulator/libs/network:http_client"),
    ("goldfish/network/resolve.h", "//emulator/libs/network"),
    ("goldfish/async/", "//emulator/libs/async"),
    ("android/base/system.h", "//emulator/libs/system"),
    ("android/base/testing/test_system.h", "//emulator/libs/system:test-headers"),
)


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

    for header_fragment, bazel_target in GOLDFISH_HEADER_TO_TARGET:
        if header_fragment in combined and (
            "No such file or directory" in combined
            or "file not found" in combined
            or "undeclared inclusion" in combined
        ):
            hints.append(
                f"Missing header `{header_fragment}`: add `{bazel_target}` to the "
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

    Returns:
        None if no diagnostic applies, or (overwritten_result, diagnostic_summary).
    """
    raw_result = str(payload.get("result") or "")
    raw_error = str(payload.get("error") or "")
    diagnostic = diagnose_command_failure(command, raw_result, raw_error)
    if not diagnostic:
        return None

    if raw_result:
        overwritten = f"{raw_result.rstrip()}\n\n{diagnostic}\n"
    else:
        overwritten = f"{diagnostic}\n"
    first_line = diagnostic.splitlines()[1].strip().lstrip("* ")
    return overwritten, first_line
