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

"""Stack trace analysis and fault frame extraction module for emu-dev-cli."""

import re
from typing import List, Optional, Tuple


class StackTraceParser:
    """Parser and normalizer for native crashing stack traces."""

    DEFAULT_IGNORE_PATTERNS: List[str] = [
        r"__GI_raise",
        r"abort",
        r"__kernel_vsyscall",
        r"pthread_cond_wait",
        r"CrashReportDumper",
        r"signal_handler",
        r"libc\.so",
        r"libpthread\.so",
    ]

    def __init__(self, ignore_patterns: Optional[List[str]] = None) -> None:
        """Initializes StackTraceParser.

        Args:
            ignore_patterns: Regex pattern list of functions/libraries to filter out.
        """
        self.ignore_patterns = ignore_patterns or list(self.DEFAULT_IGNORE_PATTERNS)

    def extract_top_fault_frame(
        self,
        crash_dump_text: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extracts top faulting function and source file from crash dump text.

        Filters out runtime / libc / signal handler frames.

        Args:
            crash_dump_text: Full text contents of crash report dump.

        Returns:
            Tuple of (function_name, file_and_line), or (None, None) if not found.
        """
        lines = crash_dump_text.splitlines()
        in_crashing_thread = False

        for line in lines:
            if "Crashing Thread" in line or (
                "Thread " in line and "crashed" in line.lower()
            ):
                in_crashing_thread = True
                continue
            if in_crashing_thread:
                if not line.strip() or line.startswith("Thread "):
                    if line.startswith("Thread ") and "crashed" not in line.lower():
                        break

                if re.search(r"#\d+\s+", line):
                    should_ignore = any(
                        re.search(pat, line) for pat in self.ignore_patterns
                    )
                    if not should_ignore:
                        func_match = re.search(
                            r"#\d+\s+(?:0x[0-9a-fA-F]+\s+(?:in\s+)?)?([^\s(\[]+)",
                            line,
                        )
                        func_name = func_match.group(1) if func_match else None

                        file_match = re.search(
                            r"(?:at|\[)\s*([a-zA-Z0-9_\-/\.]+(?:\:[0-9]+)?)", line
                        )
                        file_name = file_match.group(1) if file_match else None

                        if func_name:
                            return func_name, file_name

        return None, None


def extract_top_fault_frame(
    crash_dump_text: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Convenience helper to extract top fault frame using default StackTraceParser."""
    return StackTraceParser().extract_top_fault_frame(crash_dump_text)
