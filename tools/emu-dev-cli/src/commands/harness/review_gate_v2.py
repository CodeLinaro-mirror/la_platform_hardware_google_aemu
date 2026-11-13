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

"""Gate v2 enhancements for Review Swarm: Stage 0 checks, context hydration, and Stage 2 verifier.

Implements the 3-stage architecture validated on the 13-week Gerrit review benchmark:
  - Stage 0 (`run_deterministic_stage0`): zero-token deterministic checks for commit tags,
    `cc_binary_with_manifest`, `BUILD.bazel` syntax/missing inputs, raw `std::fstream`,
    missing `goldfish/...` headers, and undeclared `goldfish_*` C symbols.
  - Stage 1 Context Hydration (`build_enclosing_file_context`) and Severity Normalization
    (`normalize_stage1_outputs`): provides fair per-file enclosing source windows and paired
    headers so specialists do not hallucinate missing guards, and caps style/hygiene categories
    at `warning`/`nit`.
  - Stage 2 Refutation Verifier (`build_refutation_verifier_prompt`, `apply_verifier_decisions`):
    verifies candidate `blocking` findings against enclosing source context before they can block
    a Gerrit upload.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from typing import Any

from commands.harness.review_targets import parse_unified_diff_hunk_entries

CONTEXT_WINDOW_RADIUS: int = 45
MAX_ENCLOSING_CHARS_PER_FILE: int = 12000
MAX_ENCLOSING_TOTAL_CHARS: int = 48000
MAX_VERIFIER_CANDIDATES: int = 12
NEAR_HUNK_SNAP_DISTANCE: int = 8

BLOCKING_CAPABLE_SPECIALISTS: frozenset[str] = frozenset({
    "stage0-deterministic",
    "concurrency-and-safety-auditor",
    "unicode-and-platform-auditor",
    "control-flow-auditor",
})

BLOCKING_CAPABLE_CATEGORIES: frozenset[str] = frozenset({
    "concurrency",
    "unicode",
    "logic",
    "contracts",
    "build",
})

SEVERITY_RANK: dict[str, int] = {
    "blocking": 0,
    "warning": 1,
    "nit": 2,
}

VERIFIER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "candidate_index": {"type": "INTEGER"},
                    "action": {
                        "type": "STRING",
                        "enum": ["keep_blocking", "downgrade_warning", "drop"],
                    },
                    "reason": {"type": "STRING"},
                },
                "required": ["candidate_index", "action", "reason"],
            },
        },
    },
    "required": ["decisions"],
}

_FORBIDDEN_TAG_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:CONV|TAG)\s*=", re.IGNORECASE | re.MULTILINE
)
_BUG_FOOTER_RE: re.Pattern[str] = re.compile(
    r"^\s*Bug:\s*\S+", re.IGNORECASE | re.MULTILINE
)
_TEST_FOOTER_RE: re.Pattern[str] = re.compile(
    r"^\s*Test:\s*\S+", re.IGNORECASE | re.MULTILINE
)
_RAW_FSTREAM_RE: re.Pattern[str] = re.compile(
    r"\bstd::(?:i|o)?fstream\b"
)
_GOLDFISH_INCLUDE_RE: re.Pattern[str] = re.compile(
    r'^\s*#\s*include\s*"(goldfish/[^"]+)"'
)
_GOLDFISH_CALL_RE: re.Pattern[str] = re.compile(
    r"\b(goldfish_[a-z0-9_]+)\s*\("
)
_REFERENCED_TYPES_RE: re.Pattern[str] = re.compile(
    r"<([A-Z][A-Za-z0-9_]+)>"
)


def _resolve_repo_file(repo_root: Path, rel_path: str) -> Path | None:
    """Resolves `rel_path` against `repo_root`, stripping repo-mount prefixes when needed."""
    direct = repo_root / rel_path
    if direct.is_file():
        return direct
    for prefix in ("hardware/generic/goldfish/", "hardware/google/aemu/"):
        if rel_path.startswith(prefix):
            stripped = repo_root / rel_path[len(prefix) :]
            if stripped.is_file():
                return stripped
        else:
            prefixed = repo_root / prefix / rel_path
            if prefixed.is_file():
                return prefixed
    return None


def _resolve_goldfish_root(repo_root: Path) -> Path | None:
    """Returns the goldfish root directory if available under or at `repo_root`."""
    if (repo_root / "emulator").is_dir():
        return repo_root
    nested = repo_root / "hardware/generic/goldfish"
    if (nested / "emulator").is_dir():
        return nested
    return None


def _check_commit_message_stage0(commit_message: str) -> list[dict[str, Any]]:
    """Checks `commit_message` for forbidden tracking tags and missing footers."""
    if not commit_message.strip():
        return []
    findings: list[dict[str, Any]] = []
    if _FORBIDDEN_TAG_RE.search(commit_message):
        findings.append({
            "file": "COMMIT_MSG",
            "line": 1,
            "severity": "blocking",
            "category": "commit",
            "message": (
                "Commit message contains forbidden tracking tag (`CONV=` or `TAG=`), "
                "which violates the repository commit policy."
            ),
            "suggested_fix": "Remove `CONV=` and `TAG=` lines from the commit message.",
        })
    if not _BUG_FOOTER_RE.search(commit_message) or not _TEST_FOOTER_RE.search(commit_message):
        findings.append({
            "file": "COMMIT_MSG",
            "line": 1,
            "severity": "warning",
            "category": "commit",
            "message": "Commit message is missing required `Bug:` or `Test:` footer.",
            "suggested_fix": "Add `Bug: <id|N/A>` and `Test: <command>` footers.",
        })
    return findings


def _check_bazel_file_stage0(
    file_path: str,
    entries: list[tuple[int, str]],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Checks a modified `BUILD.bazel`/`BUILD` file for manifest rules, syntax, and missing inputs."""
    findings: list[dict[str, Any]] = []
    added_entries = [(ln, txt[1:]) for ln, txt in entries if txt.startswith("+")]
    for ln, text in added_entries:
        if re.search(r"^\s*cc_binary\s*\(", text) and "emulator/" in file_path:
            hunk_blob = "\n".join(t for _, t in entries)
            if "linkshared" not in hunk_blob and "cc_binary_with_manifest" not in hunk_blob:
                findings.append({
                    "file": file_path,
                    "line": ln,
                    "severity": "blocking",
                    "category": "build",
                    "message": (
                        "Executable C++ binary uses raw `cc_binary` instead of "
                        "`cc_binary_with_manifest`, omitting the Windows UTF-8 activeCodePage "
                        "manifest (b/572839247)."
                    ),
                    "suggested_fix": (
                        'Load and use `cc_binary_with_manifest` from '
                        '`//emulator/tools:cc_binary_with_manifest.bzl`.'
                    ),
                })

    full_path = _resolve_repo_file(repo_root, file_path)
    if full_path is None:
        return findings
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    default_ln = added_entries[0][0] if added_entries else (entries[0][0] if entries else 1)
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        err_ln = exc.lineno or default_ln
        findings.append({
            "file": file_path,
            "line": err_ln,
            "severity": "blocking",
            "category": "build",
            "message": (
                f"`{file_path}` contains a Starlark syntax error at line {err_ln} "
                f"({exc.msg}), breaking `bazel build`/`bazel test`."
            ),
            "suggested_fix": "Fix the unbalanced brackets, quotes, or commas in BUILD.bazel.",
        })
        return findings

    build_dir = full_path.parent
    changed_line_nums = {ln for ln, _ in entries}
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg not in {"srcs", "hdrs", "textual_hdrs"}:
            continue
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                continue
            src_name = elt.value
            if src_name.startswith((":", "//", "@")) or "/" in src_name:
                continue
            if not (build_dir / src_name).exists():
                target_ln = elt.lineno if elt.lineno in changed_line_nums else default_ln
                findings.append({
                    "file": file_path,
                    "line": target_ln,
                    "severity": "blocking",
                    "category": "build",
                    "message": (
                        f"`{file_path}` references `{src_name}` in `{node.arg}`, "
                        f"but `{src_name}` does not exist in `{build_dir.name}/`."
                    ),
                    "suggested_fix": (
                        f"Remove `{src_name}` from `{node.arg}`, add the missing file, "
                        "or replace it with the target's dependency label."
                    ),
                })
    return findings


def _check_cpp_file_stage0(
    file_path: str,
    entries: list[tuple[int, str]],
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Checks modified C/C++ lines for raw `std::fstream`, missing headers, and undeclared symbols."""
    findings: list[dict[str, Any]] = []
    added_entries = [(ln, txt[1:]) for ln, txt in entries if txt.startswith("+")]
    is_test = file_path.endswith(("_test.cc", "_test.cpp", "_unittest.cc"))
    if not is_test:
        for ln, text in added_entries:
            stripped = text.lstrip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            if _RAW_FSTREAM_RE.search(text):
                findings.append({
                    "file": file_path,
                    "line": ln,
                    "severity": "blocking",
                    "category": "unicode",
                    "message": (
                        "Direct use of `std::ifstream`/`std::ofstream`/`std::fstream` fails on "
                        "Windows when file paths contain non-ASCII UTF-8 characters."
                    ),
                    "suggested_fix": (
                        "Use `android::base::file` (`//emulator/libs/file`, `goldfish/file/file.h`) "
                        "or `android_fopen` (`android/utils/file_io.h`)."
                    ),
                })

    goldfish_root = _resolve_goldfish_root(repo_root)
    if goldfish_root is None:
        return findings

    included_goldfish_Texts: list[str] = []
    for ln, text in added_entries:
        inc_match = _GOLDFISH_INCLUDE_RE.match(text)
        if not inc_match:
            continue
        inc_rel = inc_match.group(1)
        candidates = list(goldfish_root.glob(f"emulator/**/include/{inc_rel}"))
        if not candidates or all(c.stat().st_size == 0 for c in candidates if c.is_file()):
            findings.append({
                "file": file_path,
                "line": ln,
                "severity": "blocking",
                "category": "build",
                "message": (
                    f'`#include "{inc_rel}"` references a missing or empty header file, '
                    "which will fail compilation or provide no declarations."
                ),
                "suggested_fix": (
                    f'Remove `#include "{inc_rel}"` or include the header that declares '
                    "the required symbols."
                ),
            })
        else:
            for cand in candidates:
                if cand.is_file():
                    included_goldfish_Texts.append(
                        cand.read_text(encoding="utf-8", errors="replace")
                    )

    full_path = _resolve_repo_file(repo_root, file_path)
    full_text = ""
    if full_path is not None:
        try:
            full_text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            full_text = ""
        for inc_match in re.finditer(
            r'^\s*#\s*include\s*"(goldfish/[^"]+)"', full_text, re.MULTILINE
        ):
            inc_rel = inc_match.group(1)
            for cand in goldfish_root.glob(f"emulator/**/include/{inc_rel}"):
                if cand.is_file() and cand.stat().st_size > 0:
                    included_goldfish_Texts.append(
                        cand.read_text(encoding="utf-8", errors="replace")
                    )

    if included_goldfish_Texts:
        combined_headers = "\n".join(included_goldfish_Texts)
        for ln, text in added_entries:
            if text.lstrip().startswith(("//", "/*", "*")):
                continue
            for call_match in _GOLDFISH_CALL_RE.finditer(text):
                fn_name = call_match.group(1)
                if fn_name not in combined_headers and f" {fn_name}(" not in full_text:
                    findings.append({
                        "file": file_path,
                        "line": ln,
                        "severity": "blocking",
                        "category": "contracts",
                        "message": (
                            f"`{fn_name}(...)` is called at `{file_path}:{ln}` but is not "
                            "declared in any included `goldfish/...` header or in this translation unit."
                        ),
                        "suggested_fix": (
                            f"Declare `{fn_name}` in the appropriate header or call the "
                            "existing API function declared in the included headers."
                        ),
                    })
    return findings


def _extract_diff_text(target_manifest: dict[str, Any]) -> str:
    """Returns the unified diff string from `target_manifest` (`diff_text` or `unified_diff`)."""
    return str(target_manifest.get("diff_text") or target_manifest.get("unified_diff") or "")


def run_deterministic_stage0(
    target_manifest: dict[str, Any],
    repo_dir: str = ".",
) -> list[dict[str, Any]]:
    """Runs fast, zero-token deterministic checks on the diff and commit message."""
    effective_repo = str(target_manifest.get("repo_dir") or repo_dir or ".")
    repo_root = Path(effective_repo).resolve()
    commit_msg = str(target_manifest.get("commit_message") or "")
    diff_text = _extract_diff_text(target_manifest)
    hunk_entries = parse_unified_diff_hunk_entries(diff_text)

    findings = _check_commit_message_stage0(commit_msg)
    for file_path, entries in hunk_entries.items():
        if file_path.endswith(("BUILD.bazel", "BUILD")):
            findings.extend(_check_bazel_file_stage0(file_path, entries, repo_root))
        if file_path.endswith((".cc", ".cpp", ".h", ".hpp", ".c")):
            findings.extend(_check_cpp_file_stage0(file_path, entries, repo_root))

    return [
        {
            "specialist": "stage0-deterministic",
            "role": "Stage 0 Deterministic Gate",
            "model": "deterministic",
            "checked_count": len(hunk_entries) + (1 if commit_msg.strip() else 0),
            "duplicate_count": 0,
            "finding_count": len(findings),
            "findings": findings,
            "duration_sec": 0.01,
        }
    ]


def _merge_line_windows(
    modified_lines: list[int],
    total_lines: int,
    radius: int = CONTEXT_WINDOW_RADIUS,
) -> list[tuple[int, int]]:
    """Merges `[ln - radius, ln + radius]` intervals around `modified_lines`."""
    windows: list[tuple[int, int]] = []
    for ln in modified_lines:
        start = max(1, ln - radius)
        end = min(total_lines, ln + radius)
        if windows and start <= windows[-1][1] + 5:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def _append_companion_headers(
    file_path: str,
    full_path: Path,
    file_lines: list[str],
    sections: list[str],
) -> None:
    """Appends paired `.h` header and referenced same-directory class headers to `sections`."""
    if not file_path.endswith((".cc", ".cpp")):
        return
    stem = full_path.with_suffix("")
    for hdr_ext in (".h", ".hpp"):
        hdr_path = stem.with_suffix(hdr_ext)
        if hdr_path.is_file():
            try:
                hdr_lines = hdr_path.read_text(encoding="utf-8", errors="replace").splitlines()
                hdr_numbered = [f"   {idx}: {l}" for idx, l in enumerate(hdr_lines[:220], 1)]
                sections.append(
                    f"### Paired Header `{hdr_path.name}` (first {min(220, len(hdr_lines))} lines)\n"
                    + "\n".join(hdr_numbered)
                )
            except OSError:
                pass
            break

    referenced_types = set(_REFERENCED_TYPES_RE.findall("\n".join(file_lines[:250])))
    for type_name in sorted(referenced_types)[:3]:
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", type_name).lower()
        cand_hdr = full_path.parent / f"{snake}.h"
        if cand_hdr.is_file() and cand_hdr.stem != stem.name:
            try:
                ch_lines = cand_hdr.read_text(encoding="utf-8", errors="replace").splitlines()
                ch_numbered = [f"   {idx}: {l}" for idx, l in enumerate(ch_lines[:120], 1)]
                sections.append(
                    f"### Referenced Type Header `{cand_hdr.name}`\n" + "\n".join(ch_numbered)
                )
            except OSError:
                pass


def build_enclosing_file_context(
    target_manifest: dict[str, Any],
    repo_dir: str = ".",
) -> str:
    """Extracts line-numbered enclosing context around modified hunks for each changed file."""
    effective_repo = str(target_manifest.get("repo_dir") or repo_dir or ".")
    repo_root = Path(effective_repo).resolve()
    diff_text = _extract_diff_text(target_manifest)
    hunk_entries = parse_unified_diff_hunk_entries(diff_text)
    if not hunk_entries:
        return "(no changed files)"

    sections: list[str] = []
    total_chars = 0
    num_files = max(1, len(hunk_entries))
    per_file_budget = min(
        MAX_ENCLOSING_CHARS_PER_FILE,
        max(3000, MAX_ENCLOSING_TOTAL_CHARS // num_files),
    )

    for file_path, entries in hunk_entries.items():
        if total_chars >= MAX_ENCLOSING_TOTAL_CHARS:
            break
        full_path = _resolve_repo_file(repo_root, file_path)
        if full_path is None:
            continue
        try:
            file_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if not file_lines:
            continue

        mod_lines = sorted({ln for ln, _ in entries if 1 <= ln <= len(file_lines)})
        if not mod_lines:
            continue
        mod_set = set(mod_lines)
        windows = _merge_line_windows(mod_lines, len(file_lines))

        file_snippets: list[str] = []
        file_chars = 0
        for start, end in windows:
            numbered = [
                f"{'+' if ln in mod_set else ' '} {ln:4d}: {file_lines[ln - 1]}"
                for ln in range(start, end + 1)
            ]
            block = f"--- {file_path} (lines {start}..{end} of {len(file_lines)}) ---\n" + "\n".join(numbered)
            if file_chars + len(block) > per_file_budget and file_snippets:
                break
            file_snippets.append(block)
            file_chars += len(block)

        if file_snippets:
            section_text = f"### Enclosing File Context: `{file_path}`\n" + "\n\n".join(file_snippets)
            sections.append(section_text)
            total_chars += len(section_text)
            _append_companion_headers(file_path, full_path, file_lines, sections)

    return "\n\n".join(sections) if sections else "(enclosing file context unavailable)"


def normalize_stage1_outputs(
    raw_results: list[dict[str, Any]],
    changed_lines_by_file: dict[str, set[int]],
) -> list[dict[str, Any]]:
    """Normalizes Stage 1 findings: caps advisory categories at `warning` and snaps near-hunk lines."""
    normalized_results: list[dict[str, Any]] = []
    for res in raw_results:
        spec_name = str(res.get("specialist") or "")
        spec_can_block = spec_name in BLOCKING_CAPABLE_SPECIALISTS
        cleaned_findings: list[dict[str, Any]] = []
        for raw_f in res.get("findings") or []:
            if not isinstance(raw_f, dict):
                continue
            f = dict(raw_f)
            sev = str(f.get("severity") or "warning").lower().strip()
            cat = str(f.get("category") or "complexity").lower().strip()
            if sev == "blocking" and not (
                spec_name == "stage0-deterministic"
                or (spec_can_block and cat in BLOCKING_CAPABLE_CATEGORIES)
            ):
                sev = "warning"
            f["severity"] = sev
            f["category"] = cat

            fpath = str(f.get("file") or "").strip()
            line_num = int(f.get("line") or 0)
            valid_lines = changed_lines_by_file.get(fpath, set())
            if fpath != "COMMIT_MSG" and valid_lines and line_num not in valid_lines:
                nearest = min(valid_lines, key=lambda valid_ln: abs(valid_ln - line_num))
                if abs(nearest - line_num) <= NEAR_HUNK_SNAP_DISTANCE:
                    f["line"] = nearest

            cleaned_findings.append(f)

        cleaned_findings.sort(
            key=lambda item: SEVERITY_RANK.get(str(item.get("severity", "nit")), 3)
        )
        normalized_results.append({
            **res,
            "findings": cleaned_findings,
        })
    return normalized_results


def collect_blocking_candidates(
    stage1_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns up to `MAX_VERIFIER_CANDIDATES` LLM `blocking` findings for Stage 2 refutation."""
    return [
        f
        for f in stage1_findings
        if f.get("severity") == "blocking" and f.get("specialist") != "stage0-deterministic"
    ][:MAX_VERIFIER_CANDIDATES]


def build_refutation_verifier_prompt(
    target_manifest: dict[str, Any],
    blocking_candidates: list[dict[str, Any]],
    enclosing_context: str,
) -> str:
    """Builds the Stage 2 Refutation Verifier prompt to audit candidate `blocking` findings."""
    candidates_json = json.dumps(
        [
            {
                "candidate_index": idx,
                "specialist": f.get("specialist"),
                "file": f.get("file"),
                "line": f.get("line"),
                "category": f.get("category"),
                "message": f.get("message"),
                "suggested_fix": f.get("suggested_fix"),
            }
            for idx, f in enumerate(blocking_candidates)
        ],
        indent=2,
        ensure_ascii=False,
    )
    diff_text = _extract_diff_text(target_manifest)
    if len(diff_text) > 45000:
        diff_text = diff_text[:45000] + "\n... [diff truncated] ..."

    return (
        "You are the **Stage 2 Refutation Verifier** for the Android Emulator Pre-Upload Review Gate.\n"
        "Your job is to protect engineers from false-positive blocking reviews while ensuring genuine "
        "severe defects remain `keep_blocking`.\n\n"
        "### Verification Rules\n"
        "For EVERY candidate blocking finding below, inspect the Enclosing File Context and Unified Diff:\n"
        "1. **`keep_blocking`**: Choose this when the finding identifies a plausible or definite runtime bug, "
        "race condition, deadlock, memory/lifetime/handle leak, missing null/bounds/status check, API or "
        "state-machine bug, cross-platform/Unicode bug, or build breakage in the changed code — unless "
        "you can point to an explicit line in the Enclosing File Context that ALREADY handles or prevents it.\n"
        "2. **`downgrade_warning`**: Choose this when the finding is a valid improvement, design/architecture "
        "concern, error-message improvement, or edge-case hardening, OR when you are uncertain whether it is "
        "a hard runtime bug. NEVER use `drop` for plausible code issues — prefer `downgrade_warning` so "
        "useful engineering feedback is preserved without blocking upload!\n"
        "3. **`drop`**: Choose this ONLY when you can prove from the Enclosing File Context or Diff that the "
        "finding is factually false (e.g., the lock/check/include it claims is missing is explicitly present "
        "in the file, or it contradicts the code on that line).\n\n"
        f"### Candidate Blocking Findings\n```json\n{candidates_json}\n```\n\n"
        f"### Enclosing File Context\n```text\n{enclosing_context}\n```\n\n"
        f"### Unified Diff\n```diff\n{diff_text}\n```\n"
    )


def apply_verifier_decisions(
    stage0_findings: list[dict[str, Any]],
    stage1_findings: list[dict[str, Any]],
    verifier_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Applies Stage 2 Verifier decisions (`keep_blocking`, `downgrade_warning`, `drop`)."""
    decisions_by_idx: dict[int, tuple[str, str]] = {}
    if isinstance(verifier_result, dict):
        for item in verifier_result.get("decisions") or []:
            if isinstance(item, dict) and isinstance(item.get("candidate_index"), int):
                decisions_by_idx[int(item["candidate_index"])] = (
                    str(item.get("action") or "keep_blocking"),
                    str(item.get("reason") or ""),
                )

    final_findings: list[dict[str, Any]] = list(stage0_findings)
    blocking_idx = 0
    for finding in stage1_findings:
        if finding.get("specialist") == "stage0-deterministic":
            continue
        if finding.get("severity") != "blocking":
            final_findings.append(finding)
            continue

        cur_idx = blocking_idx
        blocking_idx += 1
        action, _ = decisions_by_idx.get(cur_idx, ("keep_blocking", ""))
        if action == "drop":
            continue
        if action == "downgrade_warning":
            downgraded = dict(finding)
            downgraded["severity"] = "warning"
            final_findings.append(downgraded)
        else:
            final_findings.append(finding)

    return final_findings


def apply_verifier_to_specialist_outputs(
    specialist_outputs: list[dict[str, Any]],
    verifier_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Applies Stage 2 Verifier decisions directly to a list of specialist output dicts."""
    decisions_by_idx: dict[int, str] = {}
    if isinstance(verifier_result, dict):
        for item in verifier_result.get("decisions") or []:
            if isinstance(item, dict) and isinstance(item.get("candidate_index"), int):
                decisions_by_idx[int(item["candidate_index"])] = str(
                    item.get("action") or "keep_blocking"
                )

    updated_outputs: list[dict[str, Any]] = []
    blocking_idx = 0
    for output in specialist_outputs:
        spec_name = str(output.get("specialist") or "")
        if spec_name == "stage0-deterministic":
            updated_outputs.append(output)
            continue
        new_findings: list[dict[str, Any]] = []
        for raw_f in output.get("findings") or []:
            if not isinstance(raw_f, dict):
                continue
            if raw_f.get("severity") != "blocking":
                new_findings.append(raw_f)
                continue
            cur_idx = blocking_idx
            blocking_idx += 1
            action = decisions_by_idx.get(cur_idx, "keep_blocking")
            if action == "drop":
                continue
            if action == "downgrade_warning":
                downgraded = dict(raw_f)
                downgraded["severity"] = "warning"
                new_findings.append(downgraded)
            else:
                new_findings.append(raw_f)
        updated_outputs.append({
            **output,
            "finding_count": len(new_findings),
            "findings": new_findings,
        })
    return updated_outputs

