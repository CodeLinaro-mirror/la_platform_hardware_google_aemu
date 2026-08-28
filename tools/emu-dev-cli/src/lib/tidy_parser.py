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

"""Clang-tidy YAML parser and structural handlers."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from typing import Set
from lib.path_utils import normalize_build_path

from lib.tidy_types import TidyDiagnostic, TidyReplacement


def normalize_tidy_level(level_val: Any = 4, default: int = 4) -> int:
    """Normalizes level argument from int or semantic string (e.g. 'safe', 'local', 'types', 'public') to integer 1-4."""
    if isinstance(level_val, int):
        return level_val
    if level_val is None:
        return default
    s = str(level_val).lower().strip()
    if s.isdigit():
        return int(s)
    mapping = {
        "safe": 1,
        "modernize": 1,
        "local": 2,
        "private": 2,
        "types": 3,
        "structs": 3,
        "public": 4,
        "api": 4,
        "all": 4,
    }
    return mapping.get(s, default)


SIMPLE_FIX_DIAGNOSTIC: Set[str] = {
    "abseil-faster-strsplit-delimiter",
    "abseil-string-find-str-contains",
    "bugprone-assignment-in-if-condition",
    "bugprone-implicit-widening-of-multiplication-result",
    "google-readability-casting",
    "bugprone-string-constructor",
    "bugprone-switch-missing-default-case",
    "bugprone-macro-parentheses",
    "bugprone-misplaced-widening-cast",
    "bugprone-narrowing-conversions",
    "google-build-namespaces",
    "google-build-using-namespace",
    "google-explicit-constructor",
    "google-readability-braces-around-statements",
    "google-runtime-int",
    "misc-misplaced-const",
    "misc-redundant-expression",
    "misc-unused-alias-decls",
    "misc-unused-parameters",
    "misc-unused-using-decls",
    "misc-use-anonymous-namespace",
    "misc-use-internal-linkage",
    "modernize-avoid-bind",
    "modernize-concat-nested-namespaces",
    "modernize-deprecated-headers",
    "modernize-loop-convert",
    "modernize-macro-to-enum",
    "modernize-pass-by-value",
    "modernize-raw-string-literal",
    "modernize-redundant-void-arg",
    "modernize-replace-disallow-copy-and-assign-macro",
    "modernize-return-braced-init-list",
    "modernize-type-traits",
    "modernize-unary-static-assert",
    "modernize-use-auto",
    "modernize-use-bool-literals",
    "modernize-use-default-member-init",
    "modernize-use-designated-initializers",
    "modernize-use-emplace",
    "modernize-use-equals-default",
    "modernize-use-integer-sign-comparison",
    "modernize-use-nodiscard",
    "modernize-use-nullptr",
    "modernize-use-override",
    "modernize-use-ranges",
    "modernize-use-starts-ends-with",
    "modernize-use-std-numbers",
    "modernize-use-transparent-functors",
    "modernize-use-using",
    "performance-avoid-endl",
    "performance-enum-size",
    "performance-faster-string-find",
    "performance-for-range-copy",
    "performance-inefficient-string-concatenation",
    "performance-inefficient-vector-operation",
    "performance-move-const-arg",
    "performance-no-automatic-move",
    "performance-no-int-to-ptr",
    "performance-noexcept-move-constructor",
    "performance-noexcept-swap",
    "performance-unnecessary-copy-initialization",
    "performance-unnecessary-value-param",
    "readability-avoid-const-params-in-decls",
    "readability-avoid-return-with-void-value",
    "readability-avoid-unconditional-preprocessor-if",
    "readability-const-return-type",
    "readability-container-contains",
    "readability-container-data-pointer",
    "readability-container-size-empty",
    "readability-convert-member-functions-to-static",
    "readability-delete-null-pointer",
    "readability-duplicate-include",
    "readability-else-after-return",
    "readability-enum-initial-value",
    "readability-isolate-declaration",
    "readability-make-member-function-const",
    "readability-math-missing-parentheses",
    "readability-non-const-parameter",
    "readability-qualified-auto",
    "readability-redundant-access-specifiers",
    "readability-redundant-casting",
    "readability-redundant-declaration",
    "readability-redundant-inline-specifier",
    "readability-redundant-member-init",
    "readability-redundant-smartptr-get",
    "readability-redundant-string-init",
    "readability-redundant-typename",
    "readability-simplify-boolean-expr",
    "readability-static-accessed-through-instance",
    "readability-static-definition-in-anonymous-namespace",
    "readability-string-compare",
    "readability-uppercase-literal-suffix",
    "readability-use-anyofallof",
    "readability-use-std-min-max",
}


def classify_diagnostic_level(name: str, message: str) -> int:
    """Classifies a diagnostic name into a Level (1..4)."""
    if name == "readability-identifier-naming":
        msg_lower = message.lower()
        if (
            "function" in msg_lower
            or "method" in msg_lower
            or "global constant" in msg_lower
        ):
            return 4
        if (
            "class" in msg_lower
            or "struct" in msg_lower
            or "enum" in msg_lower
            or "typedef" in msg_lower
        ):
            return 3
        return 2

    if name in SIMPLE_FIX_DIAGNOSTIC:
        return 1

    return 3


def _normalize_member_replacement(name: str, message: str, r_text: str) -> str:
    """Normalizes member variable replacements to conform to Google C++ Style.

    Google C++ style expects class members to end with a trailing underscore,
    not prefix them with m_. If clang-tidy produces an m_ prefix, this strips it,
    and appends a trailing underscore if not already present.
    """
    if (
        name == "readability-identifier-naming"
        and "member" in message.lower()
        and r_text.startswith("m_")
    ):
        stripped = r_text[2:]
        if stripped and not stripped.endswith("_"):
            stripped += "_"
        return stripped
    return r_text


def _clean_yaml_value(val: str) -> str:
    val = val.strip()
    if (val.startswith("'") and val.endswith("'")) or (
        val.startswith('"') and val.endswith('"')
    ):
        return val[1:-1]
    return val


def _parse_fixes_yaml_native(content: str) -> List[TidyDiagnostic]:
    """Fallback standard-library parser for clang-tidy YAML files when PyYAML is unavailable."""
    diagnostics: List[TidyDiagnostic] = []
    lines = content.splitlines()

    curr_diag: Optional[Dict[str, Any]] = None
    curr_msg: Dict[str, Any] = {}
    curr_reps: List[TidyReplacement] = []
    in_replacements = False
    curr_rep: Dict[str, Any] = {}

    def flush_diag():
        nonlocal curr_diag, curr_msg, curr_reps, curr_rep
        flush_rep()
        if curr_diag:
            name = curr_diag.get("DiagnosticName", "")
            msg = curr_msg.get("Message", "")
            level = classify_diagnostic_level(name, msg)
            diagnostics.append(
                TidyDiagnostic(
                    name=name,
                    message=msg,
                    file_path=normalize_build_path(curr_msg.get("FilePath", "")),
                    line=int(curr_msg.get("Line", 0)),
                    column=int(curr_msg.get("Column", 0)),
                    affected_code=curr_msg.get("AffectedLineCode", ""),
                    replacements=list(curr_reps),
                    level=level,
                )
            )
        curr_diag = None
        curr_msg = {}
        curr_reps = []

    def flush_rep():
        nonlocal curr_rep, curr_reps, curr_diag, curr_msg
        if curr_rep:
            name = curr_diag.get("DiagnosticName", "") if curr_diag else ""
            msg = curr_msg.get("Message", "") if curr_msg else ""
            r_text = curr_rep.get("ReplacementText", "")
            r_text = _normalize_member_replacement(name, msg, r_text)
            curr_reps.append(
                TidyReplacement(
                    file_path=normalize_build_path(curr_rep.get("FilePath", "")),
                    offset=int(curr_rep.get("Offset", 0)),
                    length=int(curr_rep.get("Length", 0)),
                    replacement_text=r_text,
                )
            )
            curr_rep = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue

        if stripped.startswith("- DiagnosticName:") or (
            stripped.startswith("DiagnosticName:") and not line.startswith(" ")
        ):
            flush_diag()
            val = stripped.split(":", 1)[1].strip()
            curr_diag = {"DiagnosticName": _clean_yaml_value(val)}
            curr_msg = {}
            curr_reps = []
            in_replacements = False
            continue

        if stripped.startswith("- DiagnosticMessage:") or stripped.startswith(
            "DiagnosticMessage:"
        ):
            in_replacements = False
            continue

        if stripped.startswith("Replacements:"):
            in_replacements = True
            flush_rep()
            continue

        if in_replacements:
            if stripped.startswith("- FilePath:"):
                flush_rep()
                val = stripped.split(":", 1)[1].strip()
                curr_rep = {"FilePath": _clean_yaml_value(val)}
            elif ":" in stripped:
                k, v = stripped.split(":", 1)
                k = k.strip().lstrip("- ")
                v = _clean_yaml_value(v.strip())
                curr_rep[k] = v
        else:
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                k = k.strip().lstrip("- ")
                v = _clean_yaml_value(v.strip())
                if curr_diag is not None:
                    curr_msg[k] = v

    flush_diag()
    return diagnostics


def parse_fixes_yaml(yaml_path: str) -> List[TidyDiagnostic]:
    """Parses a clang-tidy YAML report natively."""
    p = Path(yaml_path)
    if not p.exists():
        logging.warning("Fixes YAML file does not exist: %s", yaml_path)
        return []

    try:
        import yaml

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return []

        diagnostics = []
        for d in data.get("Diagnostics", []):
            msg_obj = d.get("DiagnosticMessage", {})
            name = d.get("DiagnosticName", "")
            message = msg_obj.get("Message", "")
            level = classify_diagnostic_level(name, message)

            all_reps = msg_obj.get("Replacements", [])
            if all_reps is None:
                all_reps = []
            for note in d.get("Notes", []):
                note_reps = note.get("Replacements", [])
                if note_reps:
                    all_reps.extend(note_reps)

            reps = []
            for r in all_reps:
                r_text = r.get("ReplacementText", "")
                r_text = _normalize_member_replacement(name, message, r_text)
                reps.append(
                    TidyReplacement(
                        file_path=normalize_build_path(r.get("FilePath", "")),
                        offset=r.get("Offset", 0),
                        length=r.get("Length", 0),
                        replacement_text=r_text,
                    )
                )

            diagnostics.append(
                TidyDiagnostic(
                    name=name,
                    message=message,
                    file_path=normalize_build_path(msg_obj.get("FilePath", "")),
                    line=msg_obj.get("Line", 0),
                    column=msg_obj.get("Column", 0),
                    affected_code=msg_obj.get("AffectedLineCode", ""),
                    replacements=reps,
                    level=level,
                )
            )
        return diagnostics
    except (ImportError, ModuleNotFoundError):
        return _parse_fixes_yaml_native(p.read_text(encoding="utf-8"))
