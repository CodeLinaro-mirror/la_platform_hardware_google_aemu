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

"""Warns when a goldfish commit changes concurrency-sensitive C++ code without tests.

`hardware/generic/goldfish/AGENTS.md` ("Concurrency & Lifetime Tests") requires a
test that fails without the change whenever a commit touches threads, locks,
event-loop posting, callbacks, or object lifetimes. After a successful
`git commit` or Gerrit upload in the goldfish repository, the PostToolUse hook
scans the lines that `HEAD` adds and appends a warning to the tool result when
concurrency-sensitive production code changed but no test file did.

The check is advisory: it never blocks a command, and it stays silent (fails
open) when git cannot be queried.
"""

import logging
from pathlib import Path
import re
import subprocess
from typing import Dict, List, Mapping, Optional, Tuple

from commands.harness.constants import (
    GERRIT_UPLOAD_CMD_PATTERN,
    GIT_COMMIT_MSG_FLAG_PATTERN,
    GIT_COMMIT_SEGMENT_PATTERN,
    HELP_FLAG_PATTERN,
)
from commands.harness.shell_segments import find_command_segment
from commands.harness.upload_guardrails import resolve_command_repo_dir

WARNING_HEADER = "[AEMU Harness Concurrency-Test Check]"
GOLDFISH_REPO_PARTS: Tuple[str, ...] = ("hardware", "generic", "goldfish")
GIT_TIMEOUT_SEC = 20
MAX_FILES_IN_WARNING = 5

_SOURCE_SUFFIXES: Tuple[str, ...] = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".mm")
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|testing)/"
    r"|(?:_test|_tests|_unittest|_unittests)\.[A-Za-z0-9]+$"
    r"|(?:^|/)test_[^/]+$"
)
_THIRD_PARTY_RE = re.compile(r"(?:^|/)third_party/")
_COMMENT_LINE_RE = re.compile(r"^\s*(?://|/\*|\*)")
_NEW_FILE_HEADER_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_DEV_NULL = "/dev/null"

# (label, pattern) pairs matched against added, non-comment C/C++ lines.
CONCURRENCY_SIGNALS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "mutex/lock",
        re.compile(
            r"\b(?:absl::(?:Mutex|MutexLock|ReaderMutexLock|WriterMutexLock|CondVar)"
            r"|std::(?:mutex|recursive_mutex|shared_mutex|timed_mutex|lock_guard|unique_lock"
            r"|scoped_lock|shared_lock|condition_variable(?:_any)?))\b"
        ),
    ),
    ("thread", re.compile(r"\bstd::(?:thread|jthread|async)\b")),
    ("atomic", re.compile(r"\bstd::atomic(?:_\w+)?\b")),
    (
        "notification/future",
        re.compile(
            r"\b(?:absl::(?:Notification|Barrier|BlockingCounter)"
            r"|std::(?:promise|future|shared_future|latch|barrier|counting_semaphore|binary_semaphore))\b"
        ),
    ),
    (
        "event-loop posting/timer",
        re.compile(
            r"(?:->|\.)(?:Post|PostAndWait|PostWithOptions|ScheduleDelayed|ScheduleRepeating"
            r"|CreateTimer)\s*\("
        ),
    ),
    (
        "callback registration",
        re.compile(
            r"\b(?:AddCallback|RemoveCallback|MakeScopedCallback|AddWatchedLooper"
            r"|RemoveWatchedLooper)\s*\("
        ),
    ),
    (
        "self-deletion/shared ownership",
        re.compile(r"\bdelete\s+this\b|\b(?:shared_from_this|weak_from_this)\s*\("),
    ),
    ("BQL", re.compile(r"\b(?:bql_lock|bql_unlock|ScopedVmLock|RecursiveScopedVmLock)\b")),
)


def parse_added_lines(diff_text: str) -> Dict[str, List[str]]:
    """Maps each file in a unified diff to the lines it adds, without the leading `+`.

    Deleted files (`+++ /dev/null`) are omitted. A `+++ ` line only counts as a file
    header when it directly follows a `--- ` line, so added content that happens to
    start with `++` is not mistaken for a header.
    """
    added: Dict[str, List[str]] = {}
    current: Optional[str] = None
    previous = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = None
        elif previous.startswith("--- ") and (header := _NEW_FILE_HEADER_RE.match(line)):
            path = header.group(1).strip()
            current = None if path == _DEV_NULL else path
            if current is not None:
                added.setdefault(current, [])
        elif current is not None and line.startswith("+"):
            added[current].append(line[1:])
        previous = line
    return added


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _is_production_source(path: str) -> bool:
    return (
        path.endswith(_SOURCE_SUFFIXES)
        and not _is_test_path(path)
        and not _THIRD_PARTY_RE.search(path)
    )


def _signals_in_lines(lines: List[str]) -> List[str]:
    code_lines = [line for line in lines if not _COMMENT_LINE_RE.match(line)]
    return [
        label
        for label, pattern in CONCURRENCY_SIGNALS
        if any(pattern.search(line) for line in code_lines)
    ]


def find_untested_concurrency_signals(
    added_lines_by_path: Mapping[str, List[str]],
) -> Dict[str, List[str]]:
    """Returns the concurrency signals per production source file.

    Returns an empty dict when the change also touches a test file, because the
    commit then already carries (possibly concurrency-focused) test coverage.
    """
    if any(_is_test_path(path) for path in added_lines_by_path):
        return {}
    signals_by_path: Dict[str, List[str]] = {}
    for path, lines in added_lines_by_path.items():
        if not _is_production_source(path):
            continue
        signals = _signals_in_lines(lines)
        if signals:
            signals_by_path[path] = signals
    return signals_by_path


def format_warning(commit_summary: str, signals_by_path: Mapping[str, List[str]]) -> str:
    """Formats the advisory block appended to the tool result."""
    paths = sorted(signals_by_path)
    lines = [
        WARNING_HEADER,
        f"  Commit {commit_summary} changes concurrency-sensitive code but no test file:",
    ]
    lines.extend(
        f"    * {path}: {', '.join(signals_by_path[path])}"
        for path in paths[:MAX_FILES_IN_WARNING]
    )
    if len(paths) > MAX_FILES_IN_WARNING:
        lines.append(f"    * ... and {len(paths) - MAX_FILES_IN_WARNING} more file(s)")
    lines.append(
        "  Add a test that fails without this change, then amend: a TSAN stress test with a "
        "start barrier for races, both lock nesting orders for lock-order inversions, a "
        "timeout-bounded wait for deadlocks, and deterministic teardown under ASAN for "
        "use-after-free."
    )
    lines.append(
        '  See "Concurrency & Lifetime Tests" in hardware/generic/goldfish/AGENTS.md. If no '
        "test is possible, explain why in the commit message."
    )
    return "\n".join(lines)


def _run_git(repo_dir: Path, *args: str) -> Optional[str]:
    """Runs a read-only git command, returning stdout or None (logged) on failure."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).debug(
            "Concurrency-test check skipped: 'git %s' failed in '%s' (%s).", args[0], repo_dir, exc
        )
        return None
    if proc.returncode != 0:
        logging.getLogger(__name__).debug(
            "Concurrency-test check skipped: 'git %s' exited %d in '%s' (%s).",
            args[0],
            proc.returncode,
            repo_dir,
            proc.stderr.strip(),
        )
        return None
    return proc.stdout


def is_goldfish_repo(repo_dir: Path) -> bool:
    """Returns True if `repo_dir` is the `hardware/generic/goldfish` repository."""
    parts = repo_dir.resolve().parts
    return tuple(parts[-len(GOLDFISH_REPO_PARTS):]) == GOLDFISH_REPO_PARTS


def build_commit_warning(repo_dir: Path, commit: str = "HEAD") -> Optional[str]:
    """Returns a warning if `commit` changes concurrency-sensitive code without tests."""
    diff_text = _run_git(
        repo_dir, "show", "--format=", "--unified=0", "--no-color", "--no-ext-diff", commit
    )
    if diff_text is None:
        return None
    signals_by_path = find_untested_concurrency_signals(parse_added_lines(diff_text))
    if not signals_by_path:
        return None
    summary = (_run_git(repo_dir, "log", "-1", "--format=%h (%s)", commit) or commit).strip()
    return format_warning(summary, signals_by_path)


def build_concurrency_test_warning(
    command: str,
    payload: Mapping[str, object],
    default_cwd: Optional[Path] = None,
) -> Optional[str]:
    """PostToolUse entry point for successful goldfish `git commit` and upload commands.

    Returns:
        The warning block to append to the tool result, or None when the command is
        not a successful goldfish commit/upload or the commit needs no warning.
    """
    if payload.get("error"):
        return None
    command_without_messages = GIT_COMMIT_MSG_FLAG_PATTERN.sub("", command)
    if HELP_FLAG_PATTERN.search(command_without_messages):
        return None
    # Like the Semantic Commit guardrail, only a top-level `git commit` counts, so
    # quoted mentions such as `grep "git commit" docs/` do not trigger the check.
    if not (
        find_command_segment(command, GIT_COMMIT_SEGMENT_PATTERN)
        or GERRIT_UPLOAD_CMD_PATTERN.search(command_without_messages)
    ):
        return None
    try:
        repo_dir = resolve_command_repo_dir(command, default_cwd=default_cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).debug(
            "Concurrency-test check skipped: could not resolve the repository for '%s' (%s).",
            command,
            exc,
        )
        return None
    if not is_goldfish_repo(repo_dir):
        return None
    return build_commit_warning(repo_dir)
