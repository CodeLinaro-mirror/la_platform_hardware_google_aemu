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

"""Unified Buganizer service with typed models and structured error handling."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any

from core.errors import ExecutionError, UsageError
from core.process import ProcessRunner

LOGGER = logging.getLogger(__name__)

DEFAULT_ISSUES_CLI_BIN = "/google/bin/releases/issues-cli/issues"
BUGANIZER_BASE_URL = "https://b.corp.google.com/issues"
DRY_RUN_BUG_ID = "DRY_RUN_BUG_ID"

DEFAULT_PRIORITY = "P2"
DEFAULT_SEVERITY = "S2"
DEFAULT_STATUS_NEW = "NEW"
DEFAULT_STATUS_ASSIGNED = "ASSIGNED"

DEFAULT_SEARCH_LIMIT: int = 50
DEFAULT_BATCH_CHUNK_SIZE: int = 80
DEFAULT_CLOSED_SEARCH_LIMIT: int = 5
DEFAULT_SEARCH_TIMEOUT_SEC: float = 15.0
DEFAULT_MUTATE_TIMEOUT_SEC: float = 20.0

DEFAULT_SEARCH_FIELDS: tuple[str, ...] = (
    "issue_id",
    "priority",
    "severity",
    "status",
    "title",
    "assignee",
)

CLOSED_ISSUE_STATUSES: frozenset[str] = frozenset({
    "FIXED",
    "VERIFIED",
    "OBSOLETE",
    "CLOSED",
    "DONE",
    "DUPLICATE",
    "WONT_FIX",
    "INFEASIBLE",
    "INTENDED_BEHAVIOR",
})

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_BUG_ID_EXTRACT_RE = re.compile(r"(?:^|b/|issues/)(\d+)(?:[/?#]|$)")
_CREATED_ISSUE_ID_RE = re.compile(r"(?:b/|Issue\s+|id[:=\s]+)?(\d{6,})", re.IGNORECASE)
_ISSUE_ID_LINE_RE = re.compile(r"^(?:Issue ID|ID):\s*(\d+)\s*$", re.IGNORECASE)
_PRIORITY_LINE_RE = re.compile(r"^Priority:\s*(P[0-4])\b", re.IGNORECASE)
_SEVERITY_LINE_RE = re.compile(r"^Severity:\s*(S[0-4])\b", re.IGNORECASE)
_STATUS_LINE_RE = re.compile(r"^Status:\s*(.+)$", re.IGNORECASE)
_ASSIGNEE_LINE_RE = re.compile(r"^Assignee:\s*(.+)$", re.IGNORECASE)
_TITLE_LINE_RE = re.compile(r"^Title:\s*(.+)$", re.IGNORECASE)
_BATCH_LINE_RE = re.compile(
    r"^-\s*Issue\s+(\d+):\s*(.+?)\s*\[([^\]]+)\]\s*\(Priority:\s*(P[0-4])",
    re.IGNORECASE,
)
_COMPACT_LINE_RE = re.compile(
    r"^(?:Issue\s+|b/)?(\d+)\s*[:\-\t]\s*(.+)$", re.IGNORECASE
)
_SIG_HASH_SUFFIX_RE = re.compile(r"-([0-9a-fA-F]{6,16})\b")
_GENERIC_WRAPPER_SIG_RE = re.compile(
    r"(?:FailWithoutStackTrace|__abort_with_payload|PthreadWaiter::Wait|"
    r"FutexWaiter::Wait|CrashReporterImpl::Die|LogMessage::Flush|"
    r"\babort\b|std::terminate)"
)

BUGANIZER_REMEDIATION = (
    "Ensure '/google/bin/releases/issues-cli/issues' is installed, your LOAS certificate "
    "is valid ('gcert'), and your account has permission to access the Buganizer component."
)


def strip_ansi(text: str) -> str:
    """Removes ANSI terminal escape sequences from `text`."""
    if not text:
        return ""
    return _ANSI_ESCAPE_RE.sub("", text)


def normalize_bug_id(bug_input: str | int) -> str:
    """Extracts and validates a numeric Buganizer issue ID from an int, shortlink, or URL."""
    cleaned = str(bug_input or "").strip()
    if cleaned.isdigit():
        return cleaned
    match = _BUG_ID_EXTRACT_RE.search(cleaned)
    if match:
        return match.group(1)
    raise UsageError(
        f"Invalid Buganizer issue identifier '{bug_input}'.",
        remediation=(
            "Provide a numeric Buganizer ID (e.g. '485291270'), shortlink ('b/485291270'), "
            "or full issue URL ('https://b.corp.google.com/issues/485291270')."
        ),
        details={"bug_input": str(bug_input)},
    )


def normalize_bug_url_and_id(bug_input: str | int) -> tuple[str, str]:
    """Normalizes a Buganizer ID, shortlink, or full URL into `(bug_id, canonical_url)`."""
    cleaned = str(bug_input or "").strip()
    if cleaned.isdigit():
        return cleaned, f"{BUGANIZER_BASE_URL}/{cleaned}"
    match = _BUG_ID_EXTRACT_RE.search(cleaned)
    if match:
        bug_id = match.group(1)
        return bug_id, f"{BUGANIZER_BASE_URL}/{bug_id}"
    if cleaned.startswith(("http://", "https://")):
        return "", cleaned
    raise UsageError(
        f"Invalid Buganizer issue identifier '{bug_input}'.",
        remediation=(
            "Provide a numeric Buganizer ID (e.g. '485291270'), shortlink ('b/485291270'), "
            "or full issue URL."
        ),
    )


def normalize_bazel_target_for_search(target_identifier: str) -> str:
    """Normalizes canonical Bazel repo prefixes (`@@goldfish+//` -> `@goldfish//`)."""
    return str(target_identifier or "").strip().replace("@@goldfish+//", "@goldfish//")


@dataclass(frozen=True)
class BuganizerIssue:
    """Immutable, typed representation of a Buganizer issue."""

    issue_id: str
    title: str
    status: str = DEFAULT_STATUS_NEW
    priority: str = DEFAULT_PRIORITY
    severity: str = DEFAULT_SEVERITY
    component_id: int = 0
    assignee: str = ""
    reporter: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        clean_id = str(self.issue_id or "").removeprefix("b/").strip()
        object.__setattr__(self, "issue_id", clean_id)
        if not self.url and clean_id:
            object.__setattr__(self, "url", f"{BUGANIZER_BASE_URL}/{clean_id}")

    @property
    def is_closed(self) -> bool:
        """Returns True if the issue status indicates resolution or closure."""
        return self.status.strip().upper() in CLOSED_ISSUE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Serializes the issue into a dictionary compatible with legacy callers."""
        return {
            "id": self.issue_id,
            "issue_id": self.issue_id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "severity": self.severity,
            "component_id": self.component_id,
            "assignee": self.assignee,
            "reporter": self.reporter,
            "url": self.url,
            "is_closed": self.is_closed,
        }


@dataclass(frozen=True)
class BuganizerComment:
    """Immutable representation of a single Buganizer issue comment."""

    comment_number: int
    body: str
    author: str = ""
    timestamp: str = ""


@dataclass(frozen=True)
class CreateBugRequest:
    """Structured parameters for creating a new Buganizer issue."""

    title: str
    description: str
    component_id: int
    priority: str = DEFAULT_PRIORITY
    severity: str = DEFAULT_SEVERITY
    assignee: str | None = None
    status: str | None = None

    def __post_init__(self) -> None:
        if not str(self.title or "").strip():
            raise UsageError(
                "Buganizer issue title cannot be empty.",
                remediation="Provide a descriptive issue title.",
            )
        if int(self.component_id) <= 0:
            raise UsageError(
                f"Buganizer component_id must be a positive integer (got {self.component_id}).",
                remediation="Pass a valid positive Buganizer component ID (e.g. 1016880 or 2289192).",
            )


def is_safe_signature_issue_match(
    stable_signature: str,
    issue: BuganizerIssue | Mapping[str, Any] | None,
) -> bool:
    """Checks whether an issue returned by signature search is a safe exact match.

    Prevents false-positive matches when Buganizer tokenization strips the `-<hex_hash>`
    suffix on generic abort/log/waiter wrapper signatures.
    """
    if issue is None:
        return False
    sig = (stable_signature or "").strip()
    if not sig:
        return False

    if isinstance(issue, BuganizerIssue):
        title = issue.title.strip()
    elif isinstance(issue, Mapping):
        state = issue.get("issueState")
        state_title = state.get("title") if isinstance(state, Mapping) else None
        title = str(state_title or issue.get("title") or "").strip()
    else:
        return False

    if not title:
        return True

    sig_hash_match = _SIG_HASH_SUFFIX_RE.search(sig)
    if not sig_hash_match:
        return True

    expected_hash = sig_hash_match.group(1).lower()
    title_hashes = [m.lower() for m in _SIG_HASH_SUFFIX_RE.findall(title)]
    if title_hashes and expected_hash not in title_hashes:
        return False
    if _GENERIC_WRAPPER_SIG_RE.search(sig) and expected_hash not in title_hashes:
        return False
    return True


def _apply_field_line(line: str, cur: dict[str, str]) -> bool:
    """Matches a `Field: Value` line from `issues` CLI output and updates `cur`."""
    m_pri = _PRIORITY_LINE_RE.match(line)
    if m_pri:
        cur["priority"] = m_pri.group(1).upper()
        return True
    m_sev = _SEVERITY_LINE_RE.match(line)
    if m_sev:
        cur["severity"] = m_sev.group(1).upper()
        return True
    m_status = _STATUS_LINE_RE.match(line)
    if m_status:
        cur["status"] = m_status.group(1).strip()
        return True
    m_assignee = _ASSIGNEE_LINE_RE.match(line)
    if m_assignee:
        cur["assignee"] = m_assignee.group(1).strip()
        return True
    m_title = _TITLE_LINE_RE.match(line)
    if m_title:
        cur["title"] = m_title.group(1).strip()
        return True
    return False


def _build_issue_from_cursor(
    cur: Mapping[str, str], *, include_defaults: bool = True
) -> BuganizerIssue | None:
    """Constructs a BuganizerIssue from accumulated cursor fields if an `id` is present."""
    issue_id = cur.get("id", "").strip()
    if not issue_id:
        return None
    default_status = DEFAULT_STATUS_NEW if include_defaults else ""
    default_priority = DEFAULT_PRIORITY if include_defaults else ""
    default_severity = DEFAULT_SEVERITY if include_defaults else ""
    return BuganizerIssue(
        issue_id=issue_id,
        title=cur.get("title", ""),
        status=cur.get("status", default_status) or default_status,
        priority=cur.get("priority", default_priority) or default_priority,
        severity=cur.get("severity", default_severity) or default_severity,
        assignee=cur.get("assignee", ""),
    )


def parse_issues_cli_output(
    raw_text: str, *, include_defaults: bool = True
) -> list[BuganizerIssue]:
    """Parses `issues readonly search` or `issues get` output into typed `BuganizerIssue` objects."""
    cleaned = strip_ansi(raw_text)
    if not cleaned.strip():
        return []

    issues: list[BuganizerIssue] = []
    cur: dict[str, str] = {}
    default_status = DEFAULT_STATUS_NEW if include_defaults else ""
    default_priority = DEFAULT_PRIORITY if include_defaults else ""
    default_severity = DEFAULT_SEVERITY if include_defaults else ""

    def _flush() -> None:
        built = _build_issue_from_cursor(cur, include_defaults=include_defaults)
        if built is not None:
            issues.append(built)
        cur.clear()

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("---"):
            _flush()
            continue

        m_id = _ISSUE_ID_LINE_RE.match(line)
        if m_id:
            if cur.get("id"):
                _flush()
            cur["id"] = m_id.group(1).strip()
            continue

        if _apply_field_line(line, cur):
            continue

        m_batch = _BATCH_LINE_RE.match(line)
        if m_batch:
            _flush()
            issues.append(
                BuganizerIssue(
                    issue_id=m_batch.group(1).strip(),
                    title=m_batch.group(2).strip(),
                    status=m_batch.group(3).strip(),
                    priority=m_batch.group(4).upper(),
                    severity=default_severity,
                )
            )
            continue

        m_compact = _COMPACT_LINE_RE.match(line)
        if m_compact:
            _flush()
            issues.append(
                BuganizerIssue(
                    issue_id=m_compact.group(1).strip(),
                    title=m_compact.group(2).strip(),
                    status=default_status,
                    priority=default_priority,
                    severity=default_severity,
                )
            )

    _flush()
    return issues


def parse_issues_search_map(raw_text: str) -> dict[str, dict[str, str]]:
    """Parses `issues` CLI output into a dictionary keyed by issue ID with empty-string defaults."""
    return {
        issue.issue_id: {
            "id": issue.issue_id,
            "url": issue.url,
            "title": issue.title,
            "priority": issue.priority,
            "severity": issue.severity,
            "status": issue.status,
            "assignee": issue.assignee,
        }
        for issue in parse_issues_cli_output(raw_text, include_defaults=False)
    }


def _locate_issues_binary(explicit_bin: str | None = None) -> str:
    """Resolves the `issues` CLI executable path."""
    if explicit_bin:
        return explicit_bin
    if os.path.exists(DEFAULT_ISSUES_CLI_BIN):
        return DEFAULT_ISSUES_CLI_BIN
    found = shutil.which("issues")
    return found or DEFAULT_ISSUES_CLI_BIN


class BuganizerService(ABC):
    """Abstract interface for searching, reading, creating, and updating Buganizer issues."""

    @abstractmethod
    def search_issues(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        fields: Sequence[str] = DEFAULT_SEARCH_FIELDS,
    ) -> list[BuganizerIssue]:
        """Searches Buganizer issues matching `query`."""

    @abstractmethod
    def get_issue(self, issue_id: str | int) -> BuganizerIssue | None:
        """Retrieves a single Buganizer issue by ID, or None if not found."""

    @abstractmethod
    def get_batch_issues(
        self, issue_ids: Sequence[str | int]
    ) -> dict[str, BuganizerIssue]:
        """Batch-fetches multiple Buganizer issues keyed by numeric `issue_id` string."""

    @abstractmethod
    def list_component_open_issues(
        self, component_id: int, *, use_cache: bool = True
    ) -> list[BuganizerIssue]:
        """Lists all open issues in `component_id`."""

    @abstractmethod
    def find_open_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        """Returns an open issue in `component_id` matching `target_identifier`, if any."""

    @abstractmethod
    def find_recently_fixed_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        """Returns a closed issue in `component_id` mentioning `target_identifier`, if any."""

    @abstractmethod
    def create_issue(
        self, request: CreateBugRequest, *, dry_run: bool = False
    ) -> BuganizerIssue:
        """Creates a new Buganizer issue (or returns a dry-run issue when `dry_run=True`)."""

    @abstractmethod
    def add_comment(
        self,
        issue_id: str | int,
        comment_body: str,
        *,
        dry_run: bool = False,
    ) -> BuganizerComment:
        """Appends a comment to `issue_id`."""

    @abstractmethod
    def list_comments(self, issue_id: str | int) -> list[BuganizerComment]:
        """Lists comments on `issue_id`."""

    @abstractmethod
    def update_issue_title(
        self, issue_id: str | int, new_title: str, *, dry_run: bool = False
    ) -> None:
        """Updates the title of `issue_id`."""


class ProdBuganizerService(BuganizerService):
    """Production BuganizerService backed by `/google/bin/releases/issues-cli/issues`."""

    def __init__(
        self,
        process_runner: ProcessRunner,
        *,
        issues_bin: str | None = None,
        cache_file: Path | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._issues_bin = _locate_issues_binary(issues_bin)
        self._cache_file = cache_file
        self._lock = threading.RLock()
        self._component_cache: dict[int, list[BuganizerIssue]] = {}
        self._issue_cache: dict[str, BuganizerIssue] = self._load_disk_cache()

    def _load_disk_cache(self) -> dict[str, BuganizerIssue]:
        if self._cache_file is None or not self._cache_file.is_file():
            return {}
        try:
            raw = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, Mapping):
            return {}
        loaded: dict[str, BuganizerIssue] = {}
        for bid, entry in raw.items():
            if isinstance(entry, Mapping):
                loaded[str(bid)] = BuganizerIssue(
                    issue_id=str(entry.get("id") or bid),
                    title=str(entry.get("title") or ""),
                    status=str(entry.get("status") or DEFAULT_STATUS_NEW),
                    priority=str(entry.get("priority") or DEFAULT_PRIORITY),
                    severity=str(entry.get("severity") or DEFAULT_SEVERITY),
                    assignee=str(entry.get("assignee") or ""),
                )
        return loaded

    def _save_disk_cache(self) -> None:
        if self._cache_file is None:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            serialized = {
                bid: issue.to_dict() for bid, issue in self._issue_cache.items()
            }
            self._cache_file.write_text(
                json.dumps(serialized, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.debug("Could not persist Buganizer cache to %s: %s", self._cache_file, exc)

    def search_issues(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        fields: Sequence[str] = DEFAULT_SEARCH_FIELDS,
    ) -> list[BuganizerIssue]:
        clean_query = (query or "").strip()
        if not clean_query:
            raise UsageError(
                "Buganizer search query cannot be empty.",
                remediation="Provide a non-empty Buganizer query such as 'componentid:1016880 status:open'.",
            )
        if limit <= 0:
            raise UsageError(
                f"Buganizer search limit must be positive (got {limit}).",
                remediation="Pass a positive integer for limit.",
            )

        cmd = [
            self._issues_bin,
            "readonly",
            "search",
            f"--query={clean_query}",
            f"--limit={int(limit)}",
        ]
        if fields:
            cmd.extend(["--fields", ",".join(fields)])

        result = self._process_runner.run(
            cmd,
            timeout_sec=DEFAULT_SEARCH_TIMEOUT_SEC,
            check=True,
            remediation=BUGANIZER_REMEDIATION,
        )
        parsed = parse_issues_cli_output(result.stdout)
        with self._lock:
            for issue in parsed:
                self._issue_cache[issue.issue_id] = issue
        return parsed

    def get_issue(self, issue_id: str | int) -> BuganizerIssue | None:
        clean_id = normalize_bug_id(issue_id)
        with self._lock:
            if clean_id in self._issue_cache:
                return self._issue_cache[clean_id]
        found = self.search_issues(f"id:{clean_id}", limit=1)
        return found[0] if found else None

    def get_batch_issues(
        self, issue_ids: Sequence[str | int]
    ) -> dict[str, BuganizerIssue]:
        unique_ids = list(
            dict.fromkeys(normalize_bug_id(raw_id) for raw_id in issue_ids)
        )
        if not unique_ids:
            return {}

        with self._lock:
            missing = [bid for bid in unique_ids if bid not in self._issue_cache]

        for start in range(0, len(missing), DEFAULT_BATCH_CHUNK_SIZE):
            chunk = missing[start : start + DEFAULT_BATCH_CHUNK_SIZE]
            query = f"id:({' | '.join(chunk)})"
            fetched = self.search_issues(query, limit=len(chunk) + 10)
            with self._lock:
                for issue in fetched:
                    self._issue_cache[issue.issue_id] = issue
                self._save_disk_cache()

        with self._lock:
            return {
                bid: self._issue_cache[bid]
                for bid in unique_ids
                if bid in self._issue_cache
            }

    def list_component_open_issues(
        self, component_id: int, *, use_cache: bool = True
    ) -> list[BuganizerIssue]:
        cid = int(component_id)
        if cid <= 0:
            raise UsageError(
                f"Buganizer component_id must be positive (got {component_id}).",
                remediation="Provide a positive integer component ID.",
            )
        with self._lock:
            if use_cache and cid in self._component_cache:
                return list(self._component_cache[cid])

        issues = self.search_issues(
            f"componentid:{cid} status:open", limit=DEFAULT_SEARCH_LIMIT
        )
        with self._lock:
            self._component_cache[cid] = list(issues)
        return issues

    def find_open_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        raw_target = (target_identifier or "").strip()
        if not raw_target:
            raise UsageError(
                "Target identifier cannot be empty when searching for open bugs.",
                remediation="Provide a non-empty test target or crash signature.",
            )
        clean_target = normalize_bazel_target_for_search(raw_target)
        open_issues = self.list_component_open_issues(component_id)
        for issue in open_issues:
            if clean_target in issue.title or raw_target in issue.title:
                return issue

        direct_matches = self.search_issues(
            f'componentid:{int(component_id)} status:open "{clean_target}"',
            limit=DEFAULT_CLOSED_SEARCH_LIMIT,
        )
        return direct_matches[0] if direct_matches else None

    def find_recently_fixed_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        raw_target = (target_identifier or "").strip()
        if not raw_target:
            raise UsageError(
                "Target identifier cannot be empty when searching for closed bugs.",
                remediation="Provide a non-empty test target or crash signature.",
            )
        clean_target = normalize_bazel_target_for_search(raw_target)
        matches = self.search_issues(
            f'componentid:{int(component_id)} status:closed "{clean_target}"',
            limit=DEFAULT_CLOSED_SEARCH_LIMIT,
        )
        return matches[0] if matches else None

    def create_issue(
        self, request: CreateBugRequest, *, dry_run: bool = False
    ) -> BuganizerIssue:
        effective_status = request.status or (
            DEFAULT_STATUS_ASSIGNED if request.assignee else DEFAULT_STATUS_NEW
        )
        if dry_run:
            return BuganizerIssue(
                issue_id=DRY_RUN_BUG_ID,
                title=request.title,
                status=effective_status,
                priority=request.priority,
                severity=request.severity,
                component_id=request.component_id,
                assignee=request.assignee or "",
            )

        with tempfile.TemporaryDirectory(prefix="emu_bug_desc_") as tmp_dir:
            desc_path = Path(tmp_dir) / "description.md"
            desc_path.write_text(request.description, encoding="utf-8")
            cmd = [
                self._issues_bin,
                "mutate",
                "create",
                f"--component_id={request.component_id}",
                f"--title={request.title}",
                f"--description_file={desc_path}",
                f"--priority={request.priority}",
                f"--severity={request.severity}",
            ]
            if request.assignee:
                cmd.extend([
                    f"--assignee={request.assignee}",
                    f"--status={effective_status}",
                ])
            result = self._process_runner.run(
                cmd,
                timeout_sec=DEFAULT_MUTATE_TIMEOUT_SEC,
                check=True,
                remediation=BUGANIZER_REMEDIATION,
            )

        created_id = _extract_created_issue_id(result.stdout)
        created = BuganizerIssue(
            issue_id=created_id,
            title=request.title,
            status=effective_status,
            priority=request.priority,
            severity=request.severity,
            component_id=request.component_id,
            assignee=request.assignee or "",
        )
        with self._lock:
            self._issue_cache[created_id] = created
            self._component_cache.pop(request.component_id, None)
        return created

    def add_comment(
        self,
        issue_id: str | int,
        comment_body: str,
        *,
        dry_run: bool = False,
    ) -> BuganizerComment:
        clean_id = normalize_bug_id(issue_id)
        clean_body = (comment_body or "").strip()
        if not clean_body:
            raise UsageError(
                f"Comment body for Buganizer issue b/{clean_id} cannot be empty.",
                remediation="Pass non-empty Markdown or text content for the comment.",
            )
        if dry_run:
            return BuganizerComment(comment_number=0, body=clean_body)

        cmd = [
            self._issues_bin,
            "mutate",
            "comment",
            clean_id,
            f"--comment={clean_body}",
        ]
        self._process_runner.run(
            cmd,
            timeout_sec=DEFAULT_MUTATE_TIMEOUT_SEC,
            check=True,
            remediation=BUGANIZER_REMEDIATION,
        )
        return BuganizerComment(comment_number=1, body=clean_body)

    def list_comments(self, issue_id: str | int) -> list[BuganizerComment]:
        clean_id = normalize_bug_id(issue_id)
        cmd = [
            self._issues_bin,
            "readonly",
            "comments",
            clean_id,
        ]
        result = self._process_runner.run(
            cmd,
            timeout_sec=DEFAULT_SEARCH_TIMEOUT_SEC,
            check=True,
            remediation=BUGANIZER_REMEDIATION,
        )
        return _parse_comments_cli_output(result.stdout)

    def update_issue_title(
        self, issue_id: str | int, new_title: str, *, dry_run: bool = False
    ) -> None:
        clean_id = normalize_bug_id(issue_id)
        clean_title = (new_title or "").strip()
        if not clean_title:
            raise UsageError(
                f"New title for Buganizer issue b/{clean_id} cannot be empty.",
                remediation="Provide a non-empty descriptive title.",
            )
        if dry_run:
            return
        cmd = [
            self._issues_bin,
            "mutate",
            "update",
            "title",
            clean_id,
            clean_title,
        ]
        self._process_runner.run(
            cmd,
            timeout_sec=DEFAULT_MUTATE_TIMEOUT_SEC,
            check=True,
            remediation=BUGANIZER_REMEDIATION,
        )
        with self._lock:
            existing = self._issue_cache.get(clean_id)
            if existing is not None:
                self._issue_cache[clean_id] = BuganizerIssue(
                    issue_id=existing.issue_id,
                    title=clean_title,
                    status=existing.status,
                    priority=existing.priority,
                    severity=existing.severity,
                    component_id=existing.component_id,
                    assignee=existing.assignee,
                    reporter=existing.reporter,
                )


def _extract_created_issue_id(stdout: str) -> str:
    """Extracts the numeric Buganizer issue ID from `issues mutate create` output."""
    cleaned = strip_ansi(stdout).strip()
    match = _CREATED_ISSUE_ID_RE.search(cleaned)
    if match:
        return match.group(1)
    raise ExecutionError(
        f"Buganizer CLI did not return a valid numeric issue ID (output: {cleaned!r}).",
        remediation="Verify that 'issues mutate create' succeeded and returned an issue number.",
    )


def _parse_comments_cli_output(raw_text: str) -> list[BuganizerComment]:
    """Parses `issues readonly comments` text output into `BuganizerComment` objects."""
    cleaned = strip_ansi(raw_text).strip()
    if not cleaned:
        return []
    blocks = [b.strip() for b in re.split(r"\n---+\n", cleaned) if b.strip()]
    comments: list[BuganizerComment] = []
    for idx, block in enumerate(blocks, start=1):
        comments.append(BuganizerComment(comment_number=idx, body=block))
    return comments


class FakeBuganizerService(BuganizerService):
    """Hermetic in-memory BuganizerService for unit testing without subprocess calls."""

    def __init__(self, *, next_issue_id: int = 900000001) -> None:
        self._next_issue_id = int(next_issue_id)
        self._issues: dict[str, BuganizerIssue] = {}
        self._comments: dict[str, list[BuganizerComment]] = {}
        self.created_requests: list[CreateBugRequest] = []
        self.search_queries: list[str] = []

    def seed_issue(
        self,
        issue: BuganizerIssue,
        *,
        comments: Sequence[BuganizerComment] | None = None,
    ) -> None:
        """Seeds an issue and optional comments into the in-memory store."""
        self._issues[issue.issue_id] = issue
        if comments:
            self._comments[issue.issue_id] = list(comments)

    def search_issues(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        fields: Sequence[str] = DEFAULT_SEARCH_FIELDS,
    ) -> list[BuganizerIssue]:
        del fields
        clean_query = (query or "").strip()
        if not clean_query:
            raise UsageError(
                "Buganizer search query cannot be empty.",
                remediation="Provide a non-empty query string.",
            )
        self.search_queries.append(clean_query)
        matched = [
            issue
            for issue in self._issues.values()
            if _fake_issue_matches_query(issue, clean_query)
        ]
        return matched[:limit]

    def get_issue(self, issue_id: str | int) -> BuganizerIssue | None:
        clean_id = normalize_bug_id(issue_id)
        return self._issues.get(clean_id)

    def get_batch_issues(
        self, issue_ids: Sequence[str | int]
    ) -> dict[str, BuganizerIssue]:
        result: dict[str, BuganizerIssue] = {}
        for raw_id in issue_ids:
            clean_id = normalize_bug_id(raw_id)
            if clean_id in self._issues:
                result[clean_id] = self._issues[clean_id]
        return result

    def list_component_open_issues(
        self, component_id: int, *, use_cache: bool = True
    ) -> list[BuganizerIssue]:
        del use_cache
        cid = int(component_id)
        return [
            issue
            for issue in self._issues.values()
            if (issue.component_id in (0, cid)) and not issue.is_closed
        ]

    def find_open_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        raw_target = (target_identifier or "").strip()
        if not raw_target:
            raise UsageError(
                "Target identifier cannot be empty.",
                remediation="Provide a non-empty target identifier.",
            )
        clean_target = normalize_bazel_target_for_search(raw_target)
        for issue in self.list_component_open_issues(component_id):
            if clean_target in issue.title or raw_target in issue.title:
                return issue
        return None

    def find_recently_fixed_issue_for_target(
        self, component_id: int, target_identifier: str
    ) -> BuganizerIssue | None:
        raw_target = (target_identifier or "").strip()
        if not raw_target:
            raise UsageError(
                "Target identifier cannot be empty.",
                remediation="Provide a non-empty target identifier.",
            )
        clean_target = normalize_bazel_target_for_search(raw_target)
        cid = int(component_id)
        for issue in self._issues.values():
            if issue.component_id not in (0, cid) or not issue.is_closed:
                continue
            if clean_target in issue.title or raw_target in issue.title:
                return issue
        return None

    def create_issue(
        self, request: CreateBugRequest, *, dry_run: bool = False
    ) -> BuganizerIssue:
        self.created_requests.append(request)
        effective_status = request.status or (
            DEFAULT_STATUS_ASSIGNED if request.assignee else DEFAULT_STATUS_NEW
        )
        if dry_run:
            return BuganizerIssue(
                issue_id=DRY_RUN_BUG_ID,
                title=request.title,
                status=effective_status,
                priority=request.priority,
                severity=request.severity,
                component_id=request.component_id,
                assignee=request.assignee or "",
            )

        issue_id = str(self._next_issue_id)
        self._next_issue_id += 1
        created = BuganizerIssue(
            issue_id=issue_id,
            title=request.title,
            status=effective_status,
            priority=request.priority,
            severity=request.severity,
            component_id=request.component_id,
            assignee=request.assignee or "",
        )
        self._issues[issue_id] = created
        self._comments[issue_id] = [
            BuganizerComment(comment_number=1, body=request.description)
        ]
        return created

    def add_comment(
        self,
        issue_id: str | int,
        comment_body: str,
        *,
        dry_run: bool = False,
    ) -> BuganizerComment:
        clean_id = normalize_bug_id(issue_id)
        clean_body = (comment_body or "").strip()
        if not clean_body:
            raise UsageError(
                f"Comment body for Buganizer issue b/{clean_id} cannot be empty.",
                remediation="Provide non-empty comment text.",
            )
        if dry_run:
            return BuganizerComment(comment_number=0, body=clean_body)
        if clean_id not in self._issues:
            raise ExecutionError(
                f"Buganizer issue b/{clean_id} does not exist in FakeBuganizerService.",
                remediation="Seed the issue via fake_buganizer.seed_issue(...) before adding comments.",
            )
        bucket = self._comments.setdefault(clean_id, [])
        comment = BuganizerComment(
            comment_number=len(bucket) + 1,
            body=clean_body,
        )
        bucket.append(comment)
        return comment

    def list_comments(self, issue_id: str | int) -> list[BuganizerComment]:
        clean_id = normalize_bug_id(issue_id)
        return list(self._comments.get(clean_id, []))

    def update_issue_title(
        self, issue_id: str | int, new_title: str, *, dry_run: bool = False
    ) -> None:
        clean_id = normalize_bug_id(issue_id)
        clean_title = (new_title or "").strip()
        if not clean_title:
            raise UsageError(
                f"New title for Buganizer issue b/{clean_id} cannot be empty.",
                remediation="Provide a non-empty descriptive title.",
            )
        if dry_run:
            return
        existing = self._issues.get(clean_id)
        if existing is None:
            raise ExecutionError(
                f"Buganizer issue b/{clean_id} does not exist in FakeBuganizerService.",
                remediation="Seed the issue via fake_buganizer.seed_issue(...) before updating its title.",
            )
        self._issues[clean_id] = BuganizerIssue(
            issue_id=existing.issue_id,
            title=clean_title,
            status=existing.status,
            priority=existing.priority,
            severity=existing.severity,
            component_id=existing.component_id,
            assignee=existing.assignee,
            reporter=existing.reporter,
        )


def _fake_issue_matches_query(issue: BuganizerIssue, query: str) -> bool:
    """Evaluates simplified Buganizer query filters against an in-memory `BuganizerIssue`."""
    lower_q = query.lower()
    if "status:open" in lower_q and issue.is_closed:
        return False
    if "status:closed" in lower_q and not issue.is_closed:
        return False

    comp_match = re.search(r"componentid:(\d+)", lower_q)
    if comp_match and issue.component_id not in (0, int(comp_match.group(1))):
        return False

    id_group_match = re.search(r"id:\(([^)]+)\)", lower_q)
    if id_group_match:
        allowed = {
            part.strip()
            for part in id_group_match.group(1).split("|")
            if part.strip()
        }
        return issue.issue_id in allowed

    single_id_match = re.search(r"\bid:(\d+)\b", lower_q)
    if single_id_match and issue.issue_id != single_id_match.group(1):
        return False

    for phrase in re.findall(r'"([^"]+)"', query):
        if phrase.lower() not in issue.title.lower():
            return False
    return True
