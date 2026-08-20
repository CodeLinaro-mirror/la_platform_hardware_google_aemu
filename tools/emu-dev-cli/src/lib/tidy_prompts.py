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

from lib.tidy_types import TidyDiagnostic


def _build_generic_diagnostic_prompt(diag: TidyDiagnostic) -> str:
    return f"""You are performing an automated C++ semantic refactoring in Google Goldfish Emulator.

### Objective
Fix clang-tidy diagnostic: `{diag.name}` (Level {diag.level}).

### Location
File: `{diag.file_path}` Line: `{diag.line}` Column: `{diag.column}`

### Diagnostic Details
```text
{diag.message}
```
{f'Affected Code: `{diag.affected_code}`' if diag.affected_code else ''}

### Instructions
1. Analyze the issue reported by clang-tidy at the specified location.
2. Apply the necessary structural changes to fix the bug or vulnerability.
3. Preserve all existing formatting, docstrings, and comments.
4. Do not modify unrelated code or change any functional behavior.
"""


def _build_refactor_prompt(
    symbol_old: str,
    symbol_new: str,
    level: int,
    decl_file: str,
    decl_line: int,
    compiler_error_text: str,
) -> str:
    return f"""You are performing an automated C++ semantic refactoring in Google Goldfish Emulator.

### Objective
Rename symbol: `{symbol_old}` -> `{symbol_new}`
Reason: Clang-tidy rule `readability-identifier-naming` (Level {level} Identifier Naming).

### Current State
The declaration has been updated in `{decl_file}:{decl_line}`.
Running `bazel build --keep_going` identified the following broken call sites and consumers across the codebase:

### Compiler Diagnostics
```text
{compiler_error_text}
```

### Instructions
1. Update each broken call site in the listed files from `{symbol_old}` to `{symbol_new}`.
2. Preserve all existing formatting, docstrings, and comments.
3. Do not modify unrelated code or change any functional behavior.
4. Verify your edits cover all files mentioned in the compiler errors above.
"""
