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

"""Source-code guardrails and advisories for `write_file` and `edit_file` tool calls.

Blocking guardrails (PreToolUse `deny`) enforce Android Emulator C++ and Bazel
invariants before edits hit disk:
  1. `cc_binary_with_manifest` enforcement in `goldfish` BUILD files (b/572839247).
  2. Cross-platform UTF-8 file I/O (`android::base::file` / `android/utils/file_io.h`)
     instead of raw `std::ifstream` / `std::ofstream`.
  3. TotW 181 `absl::StatusOr` variable naming (no `_or` Hungarian suffixes).
  4. `AGENTS.md` / `ARCHITECTURE.md` / `DESIGN.md` drift prevention in `goldfish`.

Non-blocking advisories are appended to the PostToolUse result of an applied edit.
They suggest a preferred pattern that code may knowingly deviate from:
  a. `//emulator/libs/grpc_utils` reactors and status translation in
     `emulator/plugin/grpc/`.
  b. `absl::MutexLock` family locks constructed from a mutex reference rather than a
     pointer in `goldfish`.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from commands import docs
from commands.harness.constants import (
    ABSEIL_BAZEL_DEP_VERSION_PATTERN,
    ABSL_MUTEX_LOCK_BY_POINTER_PATTERN,
    ABSL_MUTEX_LOCK_REFERENCE_MIN_LTS,
    CPP_COMMENT_PATTERN,
    GRPC_MANUAL_STATUS_SWITCH_PATTERN,
    GRPC_RAW_REACTOR_INHERITANCE_PATTERN,
    LINKSHARED_TRUE_PATTERN,
    RAW_CC_BINARY_BLOCK_PATTERN,
    RAW_FSTREAM_PATTERN,
    STATUSOR_HUNGARIAN_PATTERN,
)

CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
BAZEL_BUILD_FILENAMES = {"BUILD", "BUILD.bazel"}
GOLDFISH_DOC_FILENAMES = {"AGENTS.md", "ARCHITECTURE.md", "DESIGN.md"}
ADVISORY_HEADER = "[AEMU Harness Code Advisory]"
# Longest code snippet quoted in an advisory before it is truncated.
MAX_ADVISORY_SNIPPET_CHARS = 100
# Repos built in `emu-main-dev` against Abseil LTS 20240116, which has no `Mutex&` lock
# constructors, so the pointer form is the only one that compiles there.
PRE_REFERENCE_ABSEIL_REPO_MARKERS = ("/external/qemu/", "/hardware/google/aemu/")
# How many directories above the goldfish root to search for the `abseil-cpp` pin.
MAX_ABSEIL_PIN_SEARCH_PARENTS = 3


def _extract_file_path_and_content(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Extracts target file path and new text content from a file write/edit payload."""
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    args = tool_call.get("args") or payload.get("tool_input") or {}
    path_str = str(
        args.get("TargetFile")
        or args.get("Path")
        or args.get("path")
        or args.get("file_path")
        or ""
    ).strip()
    chunks = args.get("ReplacementChunks")
    chunk_text = (
        "\n".join(
            str(chunk.get("ReplacementContent") or "")
            for chunk in chunks
            if isinstance(chunk, dict)
        )
        if isinstance(chunks, list)
        else ""
    )
    content_str = str(
        args.get("CodeContent")
        or args.get("ReplacementContent")
        or chunk_text
        or args.get("Content")
        or args.get("Replacement")
        or args.get("content")
        or args.get("new_str")
        or ""
    )
    return path_str, content_str


def is_goldfish_root_dir(candidate: Path) -> bool:
    """Returns True if `candidate` is the root of a `goldfish` repository."""
    if candidate.name == "goldfish":
        return True
    return (
        (candidate / "MODULE.bazel").is_file()
        and (candidate / "emulator" / "BUILD.bazel").is_file()
    )


def find_goldfish_repo_root(start_dir: Path) -> Optional[Path]:
    """Walks up from `start_dir` to find an enclosing `goldfish` repository root."""
    resolved = start_dir.resolve()
    for anc in (resolved, *resolved.parents):
        if is_goldfish_root_dir(anc):
            return anc
    return None


def _is_goldfish_path(norm_path: str) -> bool:
    """Returns True if the normalized path belongs to the goldfish (emu-main-next) repo."""
    if "/tools/emulator/" in norm_path or "/tools/vendor/" in norm_path:
        return False
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
        if bad_var.endswith("_or"):
            clean_var = bad_var[: -len("_or")]
        elif bad_var.startswith("maybe_"):
            clean_var = bad_var[len("maybe_") :]
        else:
            clean_var = bad_var
        return (
            False,
            "Blocked by AEMU Harness TotW 181 Guardrail (go/totw/181): "
            f"Avoid Hungarian '_or' / 'maybe_' naming on StatusOr variable '{bad_var}' in '{norm_path}'. "
            f"Name the variable '{clean_var}' directly, verify '{clean_var}.ok()', and access "
            f"via '*{clean_var}' or '{clean_var}->...'.",
        )

    return None


def _is_goldfish_grpc_plugin_path(norm_path: str) -> bool:
    """Returns True if `norm_path` is under `emulator/plugin/grpc/` in goldfish."""
    if not _is_goldfish_path(norm_path):
        return False
    return (
        norm_path.startswith("emulator/plugin/grpc/")
        or "/emulator/plugin/grpc/" in norm_path
    )


def _quote_snippet(text: str) -> str:
    """Collapses whitespace in `text` and truncates it for quoting in an advisory."""
    snippet = " ".join(text.split())
    if len(snippet) > MAX_ADVISORY_SNIPPET_CHARS:
        snippet = snippet[: MAX_ADVISORY_SNIPPET_CHARS - 3] + "..."
    return snippet


def _more_suffix(count: int) -> str:
    """Returns ` (+N more)` for `count` matches beyond the quoted one."""
    return f" (+{count - 1} more)" if count > 1 else ""


def grpc_utils_advisories(norm_path: str, content: str) -> List[str]:
    """Returns non-blocking `//emulator/libs/grpc_utils` advice for an `emulator/plugin/grpc/` C++ edit."""
    suffix = Path(norm_path).suffix.lower()
    if suffix not in CPP_EXTENSIONS or not _is_goldfish_grpc_plugin_path(norm_path):
        return []

    advice: List[str] = []
    reactors = list(GRPC_RAW_REACTOR_INHERITANCE_PATTERN.finditer(content))
    if reactors:
        first = reactors[0]
        advice.append(
            f"gRPC reactor: '{first.group(1)}' subclasses raw 'grpc::{first.group(2)}'"
            f"{_more_suffix(len(reactors))}. Prefer a '//emulator/libs/grpc_utils' wrapper: "
            "'StateStreamWriter', 'UniqueEventStreamWriter', 'GenericEventStreamWriter' or "
            "'BaseEventStreamWriter' (':event-stream', "
            "'android/emulation/control/grpc_event_stream_support.h') for server streams, or "
            "'SimpleServerLambdaReader', 'SimpleServerLambdaWriter', 'SimpleServerReader', "
            "'SimpleServerWriter', 'SimpleServerBidiStream' or 'ErrorServerWriter' (':async', "
            "'android/emulation/control/simple_async_grpc.h'). See "
            "'emulator/plugin/grpc/AGENTS.md'."
        )
    status_maps = list(GRPC_MANUAL_STATUS_SWITCH_PATTERN.finditer(content))
    if status_maps:
        advice.append(
            f"gRPC status translation: '{_quote_snippet(status_maps[0].group(0))}'"
            f"{_more_suffix(len(status_maps))} maps 'absl::Status' and 'grpc::Status' by hand. "
            "Prefer 'android::emulation::control::AbslStatusToGrpcStatus' / "
            "'GrpcStatusToAbslStatus' ('//emulator/libs/grpc_utils:absl_translate', "
            "'android/emulation/control/absl_status_translate.h')."
        )
    return advice


def _strip_cpp_comments(content: str) -> str:
    """Removes C++ comments from `content`, keeping the newlines of block comments."""
    return CPP_COMMENT_PATTERN.sub(lambda m: "\n" * m.group(0).count("\n"), content)


def _pinned_abseil_lts(norm_path: str) -> Optional[int]:
    """Returns the Abseil LTS date (YYYYMMDD) pinned by the checkout containing `norm_path`.

    Reads the `abseil-cpp` `bazel_dep` from the first `MODULE.bazel` at or above the goldfish
    root that declares one (in a repo checkout, the workspace root link to
    `build/bazel/toplevel.MODULE.bazel`). Returns None when no pin is found on disk, for
    example in a standalone goldfish clone.
    """
    repo_root = find_goldfish_repo_root(Path(norm_path).parent)
    if repo_root is None or not repo_root.is_dir():
        return None
    for directory in (repo_root, *repo_root.parents[:MAX_ABSEIL_PIN_SEARCH_PARENTS]):
        try:
            module_text = (directory / "MODULE.bazel").read_text(encoding="utf-8")
        except OSError:
            continue
        pin = ABSEIL_BAZEL_DEP_VERSION_PATTERN.search(module_text)
        if pin:
            return int(pin.group(1))
    return None


def absl_mutex_lock_advisories(norm_path: str, content: str) -> List[str]:
    """Returns non-blocking advice for goldfish C++ edits that build MutexLock locks from `&mu`.

    Skips `external/qemu` and `aemu`, and goldfish checkouts that pin an Abseil older than
    the first LTS with the `Mutex&` constructors, where the reference form does not compile.
    """
    suffix = Path(norm_path).suffix.lower()
    if (
        suffix not in CPP_EXTENSIONS
        or not _is_goldfish_path(norm_path)
        or any(marker in norm_path for marker in PRE_REFERENCE_ABSEIL_REPO_MARKERS)
    ):
        return []

    code = _strip_cpp_comments(content)
    matches = list(ABSL_MUTEX_LOCK_BY_POINTER_PATTERN.finditer(code))
    if not matches:
        return []
    pinned_lts = _pinned_abseil_lts(norm_path)
    if pinned_lts is not None and pinned_lts < ABSL_MUTEX_LOCK_REFERENCE_MIN_LTS:
        return []

    first = matches[0]
    line_start = code.rfind("\n", 0, first.start()) + 1
    line_end = code.find("\n", first.end())
    statement = code[line_start : len(code) if line_end == -1 else line_end]
    offset = first.start() - line_start
    fixed = statement[:offset] + re.sub(r"([({]\s*)&", r"\1", statement[offset:], count=1)
    return [
        f"absl::MutexLock: '{_quote_snippet(statement)}'{_more_suffix(len(matches))} passes "
        "the mutex by pointer, which Abseil deprecated ('Use the constructor that takes a "
        f"reference instead'). Prefer the reference form: '{_quote_snippet(fixed)}'. The same "
        "applies to 'ReaderMutexLock', 'WriterMutexLock', 'ReleasableMutexLock' and the "
        "'absl::Condition' overloads; 'MutexLockMaybe' keeps its pointer."
    ]


def _extract_goldfish_rel_dir(norm_path: str) -> str:
    """Extracts the directory path relative to the goldfish repository root."""
    parent_str = Path(norm_path).parent.as_posix().strip("/")
    if parent_str == ".":
        parent_str = ""
    if "/goldfish/" in f"/{parent_str}/":
        after = f"/{parent_str}/".split("/goldfish/", 1)[1].strip("/")
        return after
    if parent_str.startswith("emulator/") or parent_str == "emulator":
        return parent_str
    return parent_str


def _locate_goldfish_roots_on_disk(fpath: Path) -> Tuple[Path, Path]:
    """Resolves (repo_root, workspace_root) for an existing path inside goldfish."""
    curr = fpath.parent.resolve()
    repo_root = find_goldfish_repo_root(curr)
    if repo_root is None:
        return curr, curr
    if (
        repo_root.parent.name == "generic"
        and repo_root.parent.parent.name == "hardware"
    ):
        return repo_root, repo_root.parent.parent.parent
    return repo_root, repo_root


def evaluate_goldfish_doc_drift(
    norm_path: str,
    content: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[bool, str]]:
    """Checks edits to AGENTS.md, ARCHITECTURE.md, and DESIGN.md in goldfish for drift."""
    filename = Path(norm_path).name
    if filename not in GOLDFISH_DOC_FILENAMES or not _is_goldfish_path(norm_path):
        return None

    fpath = Path(norm_path)
    prospective_text = content
    if payload and fpath.is_file():
        tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
        args = tool_call.get("args") or payload.get("tool_input") or {}
        target_str = args.get("Target") or args.get("old_str")
        if isinstance(target_str, str) and target_str:
            try:
                existing_text = fpath.read_text(encoding="utf-8")
                if target_str in existing_text:
                    prospective_text = existing_text.replace(target_str, content)
            except OSError:
                pass

    norm_expected = _extract_goldfish_rel_dir(norm_path)
    ws_expected = f"hardware/generic/goldfish/{norm_expected}".strip("/")
    valid_locations = {
        norm_expected,
        ws_expected,
        f"{norm_expected}/{filename}".strip("/"),
        f"{ws_expected}/{filename}".strip("/"),
    }

    # 1. Check **Location:** and **Scope:** headers
    loc_match = docs._LOCATION_RE.search(prospective_text)
    if loc_match:
        declared = loc_match.group(1).strip()
        if declared.strip("/") not in valid_locations:
            expected_disp = f"{norm_expected}/" if norm_expected else "hardware/generic/goldfish"
            return (
                False,
                "Blocked by AEMU Harness Documentation Drift Guardrail: "
                f"Declared **Location:** `{declared}` in '{norm_path}' does not match "
                f"actual directory `{expected_disp}`.",
            )

    scope_match = docs._SCOPE_RE.search(prospective_text)
    if scope_match:
        declared_scope = scope_match.group(1).strip()
        norm_scope = re.sub(r"/\*+$", "", declared_scope).strip("/")
        if norm_scope not in valid_locations:
            expected_scope = f"{ws_expected}/**" if norm_expected else "hardware/generic/goldfish/**"
            return (
                False,
                "Blocked by AEMU Harness Documentation Drift Guardrail: "
                f"Declared **Scope:** `{declared_scope}` in '{norm_path}' does not match "
                f"actual directory `{expected_scope}`.",
            )

    if not fpath.parent.is_dir():
        return None

    repo_root, workspace_root = _locate_goldfish_roots_on_disk(fpath)

    # 2. Check relative Markdown links against disk
    non_code_text = re.sub(r"```.*?```", "", prospective_text, flags=re.DOTALL)
    non_code_text = re.sub(
        r"`[^`\n]*\[[^\]]*\]\([^)]*\)[^`\n]*`", "", non_code_text
    )
    for match in docs._MARKDOWN_LINK_RE.finditer(non_code_text):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith(
            (
                "http://",
                "https://",
                "mailto:",
                "file://",
                "conversation://",
                "#",
                "b/",
                "go/",
                "g3doc/",
                "crbug/",
                "cs/",
                "//cs/",
            )
        ):
            continue
        if "<" in raw_target or ">" in raw_target:
            continue
        target_path_str = raw_target.split("#", 1)[0].strip()
        if not target_path_str:
            continue

        candidates = [
            fpath.parent / target_path_str,
            repo_root / target_path_str.lstrip("/"),
            workspace_root / target_path_str.lstrip("/"),
        ]
        if not any(c.exists() for c in candidates):
            return (
                False,
                "Blocked by AEMU Harness Documentation Drift Guardrail: "
                f"Broken relative link `{raw_target}` in '{norm_path}'. "
                "Verify the target file path exists before referencing it.",
            )

    # 3. Check Bazel package labels and target names when inside a goldfish tree
    if (repo_root / "emulator").is_dir() or (fpath.parent / "BUILD.bazel").is_file():
        for match in docs._BAZEL_LABEL_RE.finditer(prospective_text):
            label = match.group(1)
            pkg_dir = docs._resolve_bazel_pkg_dir(label, repo_root, workspace_root)
            if pkg_dir is None:
                continue
            build_file = pkg_dir / "BUILD.bazel"
            if not build_file.is_file():
                build_file = pkg_dir / "BUILD"
            if not build_file.is_file():
                return (
                    False,
                    "Blocked by AEMU Harness Documentation Drift Guardrail: "
                    f"Bazel label `{label}` in '{norm_path}' points to a directory without BUILD.bazel ({pkg_dir}).",
                )
            if ":" in label:
                target_name = label.split(":", 1)[1].strip()
                try:
                    build_text = build_file.read_text(encoding="utf-8")
                except OSError:
                    build_text = ""
                if not docs._bazel_target_exists_in_build_file(target_name, build_text):
                    return (
                        False,
                        "Blocked by AEMU Harness Documentation Drift Guardrail: "
                        f"Bazel target `{label}` in '{norm_path}' was not found in '{build_file}'.",
                    )

    # 4. Prevent stripping the AGENTS.md cross-link from a co-located ARCHITECTURE.md
    if (
        filename == "ARCHITECTURE.md"
        and (fpath.parent / "AGENTS.md").is_file()
        and "AGENTS.md" not in prospective_text
    ):
        return (
            False,
            "Blocked by AEMU Harness Documentation Drift Guardrail: "
            f"'{norm_path}' has a co-located 'AGENTS.md' playbook and must retain a link to 'AGENTS.md'.",
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

    doc_verdict = evaluate_goldfish_doc_drift(norm_path, content, payload=payload)
    if doc_verdict is not None:
        return doc_verdict

    return None


# Non-blocking checks: each returns zero or more one-line suggestions for an edit.
ADVISORY_CHECKERS = (grpc_utils_advisories, absl_mutex_lock_advisories)


def build_file_edit_advisory(payload: Dict[str, Any]) -> Optional[str]:
    """Builds the non-blocking advisory block for a `write_file` / `edit_file` payload.

    Returns None when no advisory applies. Each checker contributes at most one line per
    pattern, so an edit that touches many legacy sites still yields one short note.
    """
    path_str, content = _extract_file_path_and_content(payload)
    if not path_str or not content:
        return None
    norm_path = path_str.replace("\\", "/")
    advice = [line for checker in ADVISORY_CHECKERS for line in checker(norm_path, content)]
    if not advice:
        return None
    lines = [f"{ADVISORY_HEADER} Non-blocking: the edit was applied. Suggestions for '{norm_path}':"]
    lines.extend(f"  * {line}" for line in advice)
    lines.append(
        "  Follow them, or keep the code as written if it has to differ, and say why in a "
        "comment or the commit message."
    )
    return "\n".join(lines)
