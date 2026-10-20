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

"""Source-code guardrails for `write_file` and `edit_file` tool calls.

Enforces Android Emulator C++ and Bazel invariants before edits hit disk:
  1. `cc_binary_with_manifest` enforcement in `goldfish` BUILD files (b/572839247).
  2. Cross-platform UTF-8 file I/O (`android::base::file` / `android/utils/file_io.h`)
     instead of raw `std::ifstream` / `std::ofstream`.
  3. TotW 181 `absl::StatusOr` variable naming (no `_or` Hungarian suffixes).
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from commands.harness.constants import (
    LINKSHARED_TRUE_PATTERN,
    RAW_CC_BINARY_BLOCK_PATTERN,
    RAW_FSTREAM_PATTERN,
    STATUSOR_HUNGARIAN_PATTERN,
)

CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
BAZEL_BUILD_FILENAMES = {"BUILD", "BUILD.bazel"}


def _extract_file_path_and_content(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Extracts target file path and new text content from a write_file/edit_file payload."""
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    args = tool_call.get("args") or payload.get("tool_input") or {}
    path_str = str(
        args.get("Path") or args.get("path") or args.get("file_path") or ""
    ).strip()
    content_str = str(
        args.get("Content")
        or args.get("Replacement")
        or args.get("content")
        or args.get("new_str")
        or ""
    )
    return path_str, content_str


def _is_goldfish_path(norm_path: str) -> bool:
    """Returns True if the normalized path belongs to the goldfish (emu-main-next) repo."""
    return (
        "/goldfish/" in norm_path
        or norm_path.startswith("emulator/")
        or "/emulator/" in norm_path
    )


def _is_emulator_cpp_repo_path(norm_path: str) -> bool:
    """Returns True if the normalized path is inside goldfish, external/qemu, or aemu."""
    if "/emulator/libs/file/" in norm_path:
        # Implementation of the UTF-8 file wrapper library itself is exempt.
        return False
    return (
        _is_goldfish_path(norm_path)
        or "/external/qemu/" in norm_path
        or "/hardware/google/aemu/" in norm_path
    )


def evaluate_bazel_cc_binary_manifest(
    norm_path: str,
    content: str,
) -> Optional[Tuple[bool, str]]:
    """Checks goldfish BUILD files for raw `cc_binary` without `linkshared = True`."""
    filename = Path(norm_path).name
    if filename not in BAZEL_BUILD_FILENAMES or not _is_goldfish_path(norm_path):
        return None

    for match in RAW_CC_BINARY_BLOCK_PATTERN.finditer(content):
        block_body = match.group(1)
        if not LINKSHARED_TRUE_PATTERN.search(block_body):
            return (
                False,
                "Blocked by AEMU Harness Bazel Guardrail (b/572839247): "
                f"Executable C++ binaries in '{norm_path}' must use 'cc_binary_with_manifest' "
                "(load('//emulator/tools:cc_binary_with_manifest.bzl', 'cc_binary_with_manifest')) "
                "instead of raw 'cc_binary' so the Windows UTF-8 activeCodePage manifest is "
                "always embedded. Only shared libraries with 'linkshared = True' may use raw 'cc_binary'.",
            )
    return None


def evaluate_cpp_utf8_and_statusor(
    norm_path: str,
    content: str,
) -> Optional[Tuple[bool, str]]:
    """Checks emulator C++ edits for raw `std::fstream` and TotW 181 `_or` suffixes."""
    suffix = Path(norm_path).suffix.lower()
    if suffix not in CPP_EXTENSIONS or not _is_emulator_cpp_repo_path(norm_path):
        return None

    if RAW_FSTREAM_PATTERN.search(content):
        return (
            False,
            "Blocked by AEMU Harness UTF-8 File I/O Guardrail: "
            f"Raw 'std::ifstream' / 'std::ofstream' in '{norm_path}' fails on Windows when "
            "file paths contain non-ASCII UTF-8 characters. Use 'android::base::file' "
            "('//emulator/libs/file', 'goldfish/file/file.h') in goldfish or "
            "'android/utils/file_io.h' ('android_fopen') in external/qemu.",
        )

    statusor_match = STATUSOR_HUNGARIAN_PATTERN.search(content)
    if statusor_match:
        bad_var = statusor_match.group(1)
        clean_var = bad_var[:-3] if bad_var.endswith("_or") else bad_var
        return (
            False,
            "Blocked by AEMU Harness TotW 181 Guardrail (go/totw/181): "
            f"Avoid Hungarian '_or' suffix on StatusOr variable '{bad_var}' in '{norm_path}'. "
            f"Name the variable '{clean_var}' directly, verify '{clean_var}.ok()', and access "
            f"via '*{clean_var}' or '{clean_var}->...'.",
        )

    return None


def evaluate_file_edit_payload(
    payload: Dict[str, Any],
) -> Optional[Tuple[bool, str]]:
    """Evaluates a `write_file` or `edit_file` PreToolUse payload against code guardrails."""
    path_str, content = _extract_file_path_and_content(payload)
    if not path_str or not content:
        return None

    norm_path = path_str.replace("\\", "/")
    for checker in (
        evaluate_bazel_cc_binary_manifest,
        evaluate_cpp_utf8_and_statusor,
    ):
        verdict = checker(norm_path, content)
        if verdict is not None:
            return verdict

    return None
