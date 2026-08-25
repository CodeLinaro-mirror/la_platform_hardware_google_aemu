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

"""Clang-tidy data models and type definitions."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class TidyReplacement:
    file_path: str
    offset: int
    length: int
    replacement_text: str
    line: int = 1
    column: int = 1


@dataclass
class TidyDiagnostic:
    name: str
    file_path: str
    line: int
    column: int
    message: str
    level: int = 1
    affected_code: str = ""
    replacements: List[TidyReplacement] = field(default_factory=list)


@dataclass
class TidyReport:
    target: str
    yaml_path: Optional[str]
    diagnostics: List[TidyDiagnostic] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.diagnostics) == 0
