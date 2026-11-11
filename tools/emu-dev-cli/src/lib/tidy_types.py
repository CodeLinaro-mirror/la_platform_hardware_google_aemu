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

from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class RefactoringConfig:
    """Typed parameter object for automated clang-tidy remediation and refactoring runs."""

    target: str
    report_target: str = ""
    compilation_targets: Tuple[str, ...] = ()
    level: int = 1
    model: str = "auto"
    test_target: Optional[str] = None
    checks: Optional[str] = None
    force: bool = False
    dry_run: bool = False
    continue_tx: bool = False
    bug_id: Optional[str] = None
    upload: bool = False
    json_mode: bool = False
    emit_json_result: bool = True

    @classmethod
    def from_namespace(
        cls,
        args: Any,
        *,
        target: Optional[str] = None,
        report_target: str = "",
        compilation_targets: Optional[List[str] | Tuple[str, ...]] = None,
        level: Optional[int] = None,
        emit_json_result: bool = True,
    ) -> "RefactoringConfig":
        """Constructs a RefactoringConfig from a CLI Namespace."""
        if isinstance(args, cls):
            return args
        resolved_target = (
            target
            if target is not None
            else (
                getattr(args, "target_flag", None)
                or getattr(args, "target", None)
                or ""
            )
        )
        resolved_level = (
            int(level)
            if level is not None
            else int(getattr(args, "level", 1) or 1)
        )
        return cls(
            target=str(resolved_target),
            report_target=str(report_target),
            compilation_targets=tuple(compilation_targets or ()),
            level=resolved_level,
            model=str(getattr(args, "model", "auto") or "auto"),
            test_target=getattr(args, "test", None) or getattr(args, "test_target", None),
            checks=getattr(args, "checks", None),
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            continue_tx=bool(getattr(args, "continue_tx", False)),
            bug_id=getattr(args, "bug", None) or getattr(args, "bug_id", None),
            upload=bool(getattr(args, "upload", False)),
            json_mode=bool(getattr(args, "json", False) or getattr(args, "json_mode", False)),
            emit_json_result=bool(emit_json_result),
        )

    def with_updates(self, **changes: Any) -> "RefactoringConfig":
        """Returns a copy of this config with updated fields."""
        if "compilation_targets" in changes and not isinstance(
            changes["compilation_targets"], tuple
        ):
            changes["compilation_targets"] = tuple(changes["compilation_targets"])
        return replace(self, **changes)


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
