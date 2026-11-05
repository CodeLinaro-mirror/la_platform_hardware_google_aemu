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

"""Review checks that `gerrit stack pre-upload` runs on a commit before it is uploaded.

Each check only looks at lines the change adds, so legacy code around a change never
fails the audit. Errors block the upload; warnings are heuristics a reviewer would raise
and only block with `--strict`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import ast
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Any

from commands.harness.code_guardrails import BAZEL_BUILD_FILENAMES, CPP_EXTENSIONS
from commands.harness.constants import (
    LINKSHARED_TRUE_PATTERN,
    RAW_CC_BINARY_BLOCK_PATTERN,
    RAW_FSTREAM_PATTERN,
)
from commands.harness.goldfish_libs import REUSABLE_LIBS_CATALOG
from workflows.common.git_commit.commit_verifier import (
    SEMANTIC_SUBJECT_PATTERN,
    extract_all_change_ids,
    validate_commit_message,
)

COMMIT_MSG_PATH = "/COMMIT_MSG"
MIN_SPECIFIC_MESSAGE_WORDS = 4
ROOT_CAUSE_LOOKBACK_LINES = 3
MAX_STATEMENT_LINES = 6
MAX_SNIPPET_CHARS = 80
CC_BINARY_MANIFEST_BUG = "b/572839247"

CHECK_COMMIT_MESSAGE = "commit-message"
CHECK_CC_BINARY_MANIFEST = "cc-binary-manifest"
CHECK_ERROR_MESSAGES = "error-messages"
CHECK_LIBRARY_REUSE = "library-reuse"


class Severity(str, Enum):
    """How a finding affects the upload: errors block it, warnings only advise."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One problem found in a commit, with the fix a reviewer would ask for."""

    check: str
    severity: Severity
    message: str
    remediation: str
    path: str = ""
    line: int = 0

    @property
    def location(self) -> str:
        """`path:line`, `path`, or an empty string when the finding is commit-wide."""
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path

    def to_dict(self) -> dict[str, Any]:
        """Serializes the finding into a JSON-compatible dictionary."""
        return {
            "check": self.check,
            "severity": self.severity.value,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class ChangedFile:
    """A file touched by the audited change: its added lines and its new content."""

    path: str
    added_lines: Mapping[int, str]
    content: str = ""

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.path).suffix.lower()

    @property
    def is_cpp(self) -> bool:
        return self.suffix in CPP_EXTENSIONS

    @property
    def is_python(self) -> bool:
        return self.suffix == ".py"

    @property
    def is_test(self) -> bool:
        """True for unit test sources, where terse error text is acceptable."""
        name = PurePosixPath(self.path).name.lower()
        parts = PurePosixPath(self.path).parts
        return (
            "_test." in name
            or "_unittest." in name
            or name.startswith("test_")
            or any(p in ("tests", "testing") for p in parts)
        )

    def content_lines(self) -> list[str]:
        return self.content.splitlines()


@dataclass(frozen=True)
class PreUploadTarget:
    """One unit the audit inspects: a commit, or the uncommitted working tree."""

    label: str
    commit: str
    commit_message: str | None
    files: tuple[ChangedFile, ...]
    is_goldfish: bool

    @property
    def subject(self) -> str:
        message = (self.commit_message or "").strip()
        return message.splitlines()[0] if message else ""


class PreUploadCheck(ABC):
    """Strategy interface for one pre-upload review check."""

    name: str = ""
    title: str = ""

    def skip_reason(self, target: PreUploadTarget) -> str:
        """Returns why the check cannot run on `target`, or an empty string."""
        del target
        return ""

    @abstractmethod
    def run(self, target: PreUploadTarget) -> list[Finding]:
        """Returns the findings for `target`."""


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_SNIPPET_CHARS:
        return collapsed[: MAX_SNIPPET_CHARS - 3] + "..."
    return collapsed


# Words that end like past tense, gerunds, or third person but are imperative verbs.
_IMPERATIVE_EXCEPTIONS = frozenset({
    "alias", "bias", "bleed", "breed", "bring", "embed", "exceed", "feed", "need",
    "ping", "proceed", "seed", "shed", "speed", "string", "succeed",
})


def non_imperative_lead_word(subject: str) -> str:
    """Returns the subject's first description word if it reads as past tense or third person."""
    match = SEMANTIC_SUBJECT_PATTERN.match(subject.strip())
    words = match.group(3).split() if match else []
    if not words:
        return ""
    word = words[0].strip(",.:;")
    lowered = word.lower()
    if not lowered.isalpha() or lowered in _IMPERATIVE_EXCEPTIONS:
        return ""
    if lowered.endswith(("ed", "ing")):
        return word
    if lowered.endswith("s") and not lowered.endswith(("ss", "us", "is")):
        return word
    return ""


class CommitMessageCheck(PreUploadCheck):
    """Semantic commit format, required footers, and imperative mood."""

    name = CHECK_COMMIT_MESSAGE
    title = "Semantic commit message"

    def skip_reason(self, target: PreUploadTarget) -> str:
        if target.commit_message is None:
            return "no commit message to check (pass --message-file to check a draft)"
        return ""

    def run(self, target: PreUploadTarget) -> list[Finding]:
        message = target.commit_message or ""
        findings = [
            Finding(
                self.name,
                Severity.ERROR,
                error,
                "Reword the commit with `git commit --amend` to follow "
                "'<type>(<scope>): <Description>', a body, and Bug:/Test:/Change-Id: footers.",
                COMMIT_MSG_PATH,
            )
            for error in validate_commit_message(message)
        ]
        if target.commit and message.strip() and not extract_all_change_ids(message):
            findings.append(Finding(
                self.name,
                Severity.ERROR,
                "The commit message has no 'Change-Id:' footer, so Gerrit cannot match new "
                "patch sets to the CL.",
                "Install the Gerrit commit-msg hook and run `git commit --amend --no-edit`; "
                "never type a Change-Id by hand.",
                COMMIT_MSG_PATH,
            ))
        word = non_imperative_lead_word(target.subject)
        if word:
            findings.append(Finding(
                self.name,
                Severity.WARNING,
                f"The subject description starts with '{word}', which reads as past tense or "
                "third person.",
                "Use the imperative mood, as in 'Add stack status' rather than 'Added' or 'Adds'.",
                COMMIT_MSG_PATH,
                1,
            ))
        return findings


def cc_binary_calls(content: str) -> list[tuple[str, int, int, bool]]:
    """Returns `(name, first_line, last_line, linkshared)` for each `cc_binary(...)` call.

    BUILD files are parsed as Python syntax so nested `glob()`/`select()` arguments are
    handled; files that do not parse fall back to the harness guardrail's regex.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return _cc_binary_calls_by_regex(content)
    calls: list[tuple[str, int, int, bool]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "cc_binary":
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        name_node = keywords.get("name")
        name = name_node.value if isinstance(name_node, ast.Constant) else "?"
        linkshared = keywords.get("linkshared")
        is_shared = isinstance(linkshared, ast.Constant) and linkshared.value in (True, 1)
        calls.append((str(name), node.lineno, node.end_lineno or node.lineno, is_shared))
    return calls


def _cc_binary_calls_by_regex(content: str) -> list[tuple[str, int, int, bool]]:
    calls: list[tuple[str, int, int, bool]] = []
    for match in RAW_CC_BINARY_BLOCK_PATTERN.finditer(content):
        first = content.count("\n", 0, match.start()) + 1
        name = re.search(r'\bname\s*=\s*"([^"]+)"', match.group(1))
        calls.append((
            name.group(1) if name else "?",
            first,
            first + match.group(0).count("\n"),
            bool(LINKSHARED_TRUE_PATTERN.search(match.group(1))),
        ))
    return calls


class CcBinaryManifestCheck(PreUploadCheck):
    """New or edited goldfish executables must embed the Windows UTF-8 manifest."""

    name = CHECK_CC_BINARY_MANIFEST
    title = "cc_binary_with_manifest"

    def skip_reason(self, target: PreUploadTarget) -> str:
        if not target.is_goldfish:
            return "only goldfish provides //emulator/tools:cc_binary_with_manifest.bzl"
        return ""

    def run(self, target: PreUploadTarget) -> list[Finding]:
        build_files = [
            f for f in target.files if PurePosixPath(f.path).name in BAZEL_BUILD_FILENAMES
        ]
        return [finding for changed in build_files for finding in self._file_findings(changed)]

    def _file_findings(self, changed: ChangedFile) -> Iterator[Finding]:
        for name, first, last, is_shared in cc_binary_calls(changed.content):
            touched = any(first <= line <= last for line in changed.added_lines)
            if touched and not is_shared:
                yield self._finding(changed.path, first, name)

    def _finding(self, path: str, line: int, name: str) -> Finding:
        return Finding(
            self.name,
            Severity.ERROR,
            f"cc_binary '{name}' builds an executable without the Windows UTF-8 "
            "activeCodePage manifest.",
            "Use cc_binary_with_manifest, loaded from "
            "//emulator/tools:cc_binary_with_manifest.bzl. Only plugins with "
            f"linkshared = True may stay on cc_binary ({CC_BINARY_MANIFEST_BUG}).",
            path,
            line,
        )


# An identifier holding a credential, in snake_case after camelCase is split.
_SECRET_IDENTIFIER_RE = re.compile(
    r"(?:^|_)(?:token|password|passwd|secret|api_?key|authorization|auth_header|"
    r"cookie|credentials?|private_key)$"
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def is_secret_identifier(identifier: str) -> bool:
    """True for names such as `token`, `access_token_`, `mApiKey`, or `clientSecret`."""
    snake = _CAMEL_BOUNDARY_RE.sub("_", identifier).lower().strip("_")
    if snake.startswith("m_"):
        snake = snake[2:]
    return bool(_SECRET_IDENTIFIER_RE.search(snake))


_CPP_ERROR_CALL_RE = re.compile(r"\babsl::(\w+)Error\s*\(")
_CPP_UNKNOWN_CODE_RE = re.compile(r"\babsl::(?:UnknownError\s*\(|StatusCode::kUnknown\b)")
_CPP_LITERAL_ONLY_ERROR_RE = re.compile(
    r'\babsl::\w+Error\s*\(\s*((?:"(?:[^"\\\n]|\\.)*"\s*)+)\)'
)
_CPP_LOG_RE = re.compile(
    r"\b(?:LOG|VLOG|PLOG|DLOG|LOG_IF|LOG_EVERY_N)\s*\(|\b(?:derror|dwarning|dinfo)\s*\("
)
_CPP_STRING_LITERAL_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_CPP_NOT_OK_RE = re.compile(r"if\s*\(\s*!\s*([A-Za-z_]\w*)(?:\.|->)ok\(\)\s*\)")
_CPP_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")
# Uses of a secret that only reveal its size, emptiness, or Status/StatusOr state.
_CPP_HARMLESS_SECRET_USE_RE = re.compile(
    r"(?:\s*\(\s*\))?\s*(?:\.|->)\s*(?:size|length|empty|status|ok|code|message)\s*\("
)
_CPP_REDACTING_CALL_RE = re.compile(r"(?:Redact|Mask|Sanitize)\w*\s*\(\s*$")
_CPP_REDACTING_NAME_RE = re.compile(r"^(?:Redact|Mask|Sanitize)")
_CPP_IF_RE = re.compile(r"\bif\s*\(")

_EMU_CLI_ERROR_TYPES = frozenset({
    "AuthError", "EmuCliError", "ExecutionError", "RetryableError", "SubprocessError",
    "UsageError",
})
# Exceptions whose short, fixed text is idiomatic.
_MESSAGE_EXEMPT_EXCEPTIONS = frozenset({"NotImplementedError", "StopIteration", "SystemExit"})
_PY_LOGGER_NAMES = frozenset({"logging", "logger", "LOGGER", "log", "_LOGGER"})
_PY_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def _unless_mentioned(name: str, statement: str) -> str:
    """Returns `name` unless `statement` refers to it."""
    return "" if re.search(rf"\b{re.escape(name)}\b", statement) else name


def _statement_at(lines: Sequence[str], index: int) -> str:
    """Joins source lines from `index` until the statement's `;` (bounded)."""
    end = min(len(lines), index + MAX_STATEMENT_LINES)
    parts: list[str] = []
    for line in lines[index:end]:
        parts.append(line)
        if ";" in line:
            break
    return " ".join(parts)


def leaked_cpp_secrets(statement: str) -> list[str]:
    """Returns secret-looking identifiers a C++ error or log statement prints."""
    code = _CPP_STRING_LITERAL_RE.sub('""', statement)
    leaked: list[str] = []
    for match in _CPP_IDENTIFIER_RE.finditer(code):
        name = match.group(0)
        if not is_secret_identifier(name) or name in leaked or _CPP_REDACTING_NAME_RE.match(name):
            continue
        if _CPP_HARMLESS_SECRET_USE_RE.match(code, match.end()):
            continue
        if _CPP_REDACTING_CALL_RE.search(code[: match.start()]):
            continue
        leaked.append(name)
    return leaked


class ErrorMessageCheck(PreUploadCheck):
    """go/error-messages: specific text, canonical codes, kept root causes, no secrets."""

    name = CHECK_ERROR_MESSAGES
    title = "go/error-messages"

    def run(self, target: PreUploadTarget) -> list[Finding]:
        return [finding for changed in target.files for finding in self._file_findings(changed)]

    def _file_findings(self, changed: ChangedFile) -> list[Finding]:
        if changed.is_cpp:
            return self._cpp_findings(changed)
        if changed.is_python:
            return self._python_findings(changed)
        return []

    def _finding(
        self, severity: Severity, changed: ChangedFile, line: int, message: str, remediation: str
    ) -> Finding:
        return Finding(self.name, severity, message, remediation, changed.path, line)

    def _cpp_findings(self, changed: ChangedFile) -> list[Finding]:
        lines = changed.content_lines()
        findings: list[Finding] = []
        for line_no, text in sorted(changed.added_lines.items()):
            is_error_site = bool(_CPP_ERROR_CALL_RE.search(text))
            if not (is_error_site or _CPP_LOG_RE.search(text)):
                continue
            statement = _statement_at(lines, line_no - 1) if line_no <= len(lines) else text
            findings.extend(self._cpp_secret_findings(changed, line_no, statement))
            if is_error_site:
                findings.extend(self._cpp_error_site_findings(changed, line_no, text, statement, lines))
        return findings

    def _cpp_secret_findings(self, changed: ChangedFile, line_no: int, statement: str) -> Iterator[Finding]:
        for name in leaked_cpp_secrets(statement):
            yield self._finding(
                Severity.ERROR,
                changed,
                line_no,
                f"This error or log statement prints '{name}', which looks like a credential.",
                "Leave the secret out of the message, or print a redacted form such as its length.",
            )

    def _cpp_error_site_findings(
        self, changed: ChangedFile, line_no: int, text: str, statement: str, lines: Sequence[str]
    ) -> Iterator[Finding]:
        if _CPP_UNKNOWN_CODE_RE.search(text):
            yield self._finding(
                Severity.WARNING,
                changed,
                line_no,
                "UNKNOWN is not a specific status code, so callers cannot react to the failure.",
                "Pick the canonical code for the failure class, such as NOT_FOUND, "
                "INVALID_ARGUMENT, FAILED_PRECONDITION, or UNAVAILABLE.",
            )
        literal = _CPP_LITERAL_ONLY_ERROR_RE.search(text)
        if literal and not changed.is_test and _word_count(literal.group(1)) < MIN_SPECIFIC_MESSAGE_WORDS:
            yield self._finding(
                Severity.WARNING,
                changed,
                line_no,
                f"Error text {_snippet(literal.group(1))} does not say what failed or why.",
                "Name the operation, the cause, and the offending value, and add a next step "
                "when one exists (go/error-messages).",
            )
        dropped = self._dropped_status(lines, line_no, statement)
        if dropped:
            yield self._finding(
                Severity.WARNING,
                changed,
                line_no,
                f"This error replaces the failed status '{dropped}' without its message, "
                "so the root cause is lost.",
                f"Include {dropped}.status() or {dropped}.message() in the new error, or "
                "propagate it with absl::StatusBuilder.",
            )

    @staticmethod
    def _dropped_status(lines: Sequence[str], line_no: int, statement: str) -> str:
        """Returns the variable of the guarding `if (!x.ok())` when the new error ignores it.

        Looks at the error's own line, then up to ROOT_CAUSE_LOOKBACK_LINES lines back,
        stopping at a closing brace so an error after the guarded block is not blamed.
        """
        current = lines[line_no - 1] if 0 < line_no <= len(lines) else statement
        guard = _CPP_NOT_OK_RE.search(current)
        if guard:
            after_guard = statement.split(guard.group(0), 1)[-1]
            return _unless_mentioned(guard.group(1), after_guard)
        if _CPP_IF_RE.search(current):
            return ""
        start = max(0, line_no - 1 - ROOT_CAUSE_LOOKBACK_LINES)
        for previous in reversed(lines[start : line_no - 1]):
            guard = _CPP_NOT_OK_RE.search(previous)
            if guard:
                return _unless_mentioned(guard.group(1), statement)
            if "}" in previous:
                return ""
        return ""

    def _python_findings(self, changed: ChangedFile) -> list[Finding]:
        try:
            tree = ast.parse(changed.content)
        except (SyntaxError, ValueError):
            return []
        added = set(changed.added_lines)
        nodes = list(ast.walk(tree))
        handlers = [n for n in nodes if isinstance(n, ast.ExceptHandler)]
        raises = [n for n in nodes if isinstance(n, ast.Raise) and n.lineno in added]
        log_calls = [
            n for n in nodes if isinstance(n, ast.Call) and n.lineno in added and _is_logging_call(n)
        ]
        # A raise in nested handlers is reached from each of them; key by identity to report it once.
        handler_raises = {
            id(n): n for handler in handlers for n in ast.walk(handler) if isinstance(n, ast.Raise)
        }
        findings: list[Finding] = []
        for handler in handlers:
            findings.extend(self._python_silent_handler_findings(changed, handler, added))
        for node in handler_raises.values():
            findings.extend(self._python_unchained_findings(changed, node, added))
        for node in raises:
            findings.extend(self._python_raise_findings(changed, node))
        for call in log_calls:
            findings.extend(self._python_secret_findings(changed, call, call.args))
        return findings

    def _python_silent_handler_findings(
        self, changed: ChangedFile, handler: ast.ExceptHandler, added: set[int]
    ) -> Iterator[Finding]:
        if handler.lineno in added and _is_broad(handler) and _is_silent(handler.body):
            yield self._finding(
                Severity.WARNING,
                changed,
                handler.lineno,
                "This handler catches every exception and discards it, hiding real failures.",
                "Catch the specific exception you expect, or log it with context before continuing.",
            )

    def _python_unchained_findings(
        self, changed: ChangedFile, node: ast.Raise, added: set[int]
    ) -> Iterator[Finding]:
        if node.lineno not in added or node.exc is None or node.cause is not None:
            return
        if isinstance(node.exc, ast.Name):
            return
        yield self._finding(
            Severity.WARNING,
            changed,
            node.lineno,
            "This raise inside an except block does not chain the caught exception, so "
            "the root cause is lost.",
            "Raise the new error with `from exc` (or `from None` when hiding it on purpose).",
        )

    def _python_raise_findings(self, changed: ChangedFile, node: ast.Raise) -> Iterator[Finding]:
        call = node.exc if isinstance(node.exc, ast.Call) else None
        if call is None:
            return
        name = _call_name(call)
        literal = _literal_message(call)
        checks_text = not changed.is_test and name not in _MESSAGE_EXEMPT_EXCEPTIONS
        if literal is not None and checks_text and _word_count(literal) < MIN_SPECIFIC_MESSAGE_WORDS:
            yield self._finding(
                Severity.WARNING,
                changed,
                node.lineno,
                f"Error text '{_snippet(literal)}' does not say what failed or why.",
                "Name the operation, the cause, and the offending value (go/error-messages).",
            )
        has_remediation = any(kw.arg == "remediation" for kw in call.keywords)
        if name in _EMU_CLI_ERROR_TYPES and not has_remediation and not changed.is_test:
            yield self._finding(
                Severity.WARNING,
                changed,
                node.lineno,
                f"{name} is raised without a remediation, so the user gets no next step.",
                "Pass remediation='...' with the concrete action that fixes the problem.",
            )
        values = [*call.args, *(kw.value for kw in call.keywords)]
        yield from self._python_secret_findings(changed, call, values)

    def _python_secret_findings(
        self, changed: ChangedFile, call: ast.Call, values: Sequence[ast.expr]
    ) -> Iterator[Finding]:
        for name in _python_leaked_secrets(values):
            yield self._finding(
                Severity.ERROR,
                changed,
                call.lineno,
                f"This error or log message prints '{name}', which looks like a credential.",
                "Leave the secret out of the message, or print a redacted form such as its length.",
            )


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _literal_message(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _is_logging_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _PY_LOG_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id in _PY_LOGGER_NAMES
    )


def _is_broad(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or (
        isinstance(handler.type, ast.Name) and handler.type.id in _BROAD_EXCEPTIONS
    )


def _is_silent(body: Sequence[ast.stmt]) -> bool:
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            continue
        if isinstance(stmt, ast.Return) and (
            stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
        ):
            continue
        return False
    return True


def _expr_identifier(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    return expr.attr if isinstance(expr, ast.Attribute) else ""


def _interpolated_values(values: Sequence[ast.expr]) -> Iterator[ast.expr]:
    """Yields each value, or the `{...}` expressions of an f-string value."""
    for value in values:
        if isinstance(value, ast.JoinedStr):
            yield from (part.value for part in value.values if isinstance(part, ast.FormattedValue))
        else:
            yield value


def _python_leaked_secrets(values: Sequence[ast.expr]) -> list[str]:
    """Returns secret names interpolated into f-strings or passed straight to a message."""
    names = (_expr_identifier(candidate) for candidate in _interpolated_values(values))
    return list(dict.fromkeys(name for name in names if name and is_secret_identifier(name)))


_CATALOG_BY_NAME: dict[str, Mapping[str, str]] = {
    entry["name"]: entry for entry in REUSABLE_LIBS_CATALOG
}


def _catalog_advice(names: Sequence[str]) -> str:
    """Formats `Use <namespace> (<target>, "<header>")` advice from the goldfish catalog."""
    advice = [
        f"{entry['namespace']} ({entry['bazel_target']}, \"{entry['header']}\")"
        for entry in (_CATALOG_BY_NAME[name] for name in names)
    ]
    return "Use " + " or ".join(advice) + "."


def _catalog_package_dirs(names: Sequence[str]) -> tuple[str, ...]:
    """Returns `emulator/libs/<pkg>/` for the catalog entries, whose own code is exempt."""
    dirs = []
    for name in names:
        target = _CATALOG_BY_NAME[name]["bazel_target"]
        dirs.append(target.removeprefix("//").split(":", 1)[0].rstrip("/") + "/")
    return tuple(dirs)


@dataclass(frozen=True)
class ReuseRule:
    """A hand-rolled pattern that an existing `//emulator/libs` target already covers."""

    rule_id: str
    pattern: re.Pattern[str]
    problem: str
    catalog_names: tuple[str, ...]
    severity: Severity = Severity.WARNING
    goldfish_only: bool = True
    # Advice outside goldfish, where `//emulator/libs` does not exist.
    other_repo_remediation: str = ""
    # Extra pattern the file must also contain for the rule to apply.
    file_hint: re.Pattern[str] | None = None


REUSE_RULES: tuple[ReuseRule, ...] = (
    ReuseRule(
        "raw-fstream",
        RAW_FSTREAM_PATTERN,
        "Raw std::ifstream/std::ofstream fails on Windows paths with non-ASCII characters.",
        ("file",),
        severity=Severity.ERROR,
        goldfish_only=False,
        other_repo_remediation=(
            "Use the repository's UTF-8 file helpers, such as android_fopen from "
            "android/utils/file_io.h in external/qemu."
        ),
    ),
    ReuseRule(
        "raw-env",
        re.compile(
            r"\b(?:std::)?(?:getenv|secure_getenv|setenv|unsetenv|putenv|_wgetenv|_putenv)\s*\("
            r"|\bGetEnvironmentVariable[AW]?\s*\("
        ),
        "Reads or writes the process environment directly, which unit tests cannot fake.",
        ("system", "test_system"),
    ),
    ReuseRule(
        "raw-socket",
        re.compile(
            r"\b(?:AF_INET6?|SOCK_STREAM|SOCK_DGRAM|INADDR_ANY|WSAStartup)\b"
            r"|\bstruct\s+sockaddr(?:_in6?)?\b"
            r"|#\s*include\s*<(?:sys/socket\.h|netinet/in\.h|arpa/inet\.h|winsock2\.h|ws2tcpip\.h)>"
        ),
        "Uses raw BSD sockets instead of the shared async socket and endpoint helpers.",
        ("async", "network"),
    ),
    ReuseRule(
        "manual-http-server",
        re.compile(r'"HTTP/1\.[01] \d{3}|\bhttplib::Server\b|\bMHD_start_daemon\b|\bmg_start\s*\('),
        "Implements an HTTP server by hand.",
        ("http",),
    ),
    ReuseRule(
        "manual-http-client",
        re.compile(r"\bcurl_easy_(?:init|perform)\s*\(|\bhttplib::Client\b"),
        "Implements an HTTP client by hand.",
        ("http_client",),
    ),
    ReuseRule(
        "manual-ini-parsing",
        re.compile(r"\.find(?:_first_of)?\(\s*(?:'='|\"=\")"),
        "Splits key=value lines by hand instead of using the shared INI parser.",
        ("ini_file",),
        file_hint=re.compile(r"\.ini\b|\bini\b", re.IGNORECASE),
    ),
    ReuseRule(
        "win32-shim",
        re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b.*\b(?:_WIN32|_WIN64|_MSC_VER)\b"),
        "Adds a platform-specific #if branch that a shared cross-platform helper may already cover.",
        ("file", "system"),
    ),
)


class LibraryReuseCheck(PreUploadCheck):
    """Flags hand-rolled sockets, servers, parsers, file I/O, env access, and _WIN32 shims."""

    name = CHECK_LIBRARY_REUSE
    title = "//emulator/libs reuse"

    def run(self, target: PreUploadTarget) -> list[Finding]:
        rules = [r for r in REUSE_RULES if target.is_goldfish or not r.goldfish_only]
        cpp_files = [f for f in target.files if f.is_cpp]
        return [
            finding
            for changed in cpp_files
            for rule in rules
            for finding in self._rule_findings(rule, changed, target.is_goldfish)
        ]

    def _rule_findings(self, rule: ReuseRule, changed: ChangedFile, is_goldfish: bool) -> Iterator[Finding]:
        if is_goldfish and changed.path.startswith(_catalog_package_dirs(rule.catalog_names)):
            return
        if rule.file_hint is not None and not rule.file_hint.search(changed.content):
            return
        remediation = (
            _catalog_advice(rule.catalog_names) if is_goldfish else rule.other_repo_remediation
        )
        for line_no, text in sorted(changed.added_lines.items()):
            if text.lstrip().startswith(("//", "/*", "*")):
                continue
            if rule.pattern.search(text):
                yield Finding(
                    self.name,
                    rule.severity,
                    f"{rule.problem} Found: '{_snippet(text)}' ({rule.rule_id}).",
                    remediation,
                    changed.path,
                    line_no,
                )


DEFAULT_CHECKS: tuple[PreUploadCheck, ...] = (
    CommitMessageCheck(),
    CcBinaryManifestCheck(),
    ErrorMessageCheck(),
    LibraryReuseCheck(),
)
CHECK_NAMES: tuple[str, ...] = tuple(check.name for check in DEFAULT_CHECKS)
