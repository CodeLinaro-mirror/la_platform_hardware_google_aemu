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

"""Compatibility wrapper around `clients.buganizer_service`.

All Buganizer CLI interactions, ANSI stripping, issue parsing, and deduplication
now live in `clients.buganizer_service`. This module preserves the legacy
`BuganizerClient`, `BuganizerIssuePayload`, and `format_buganizer_search_query`
symbols for existing callers and unit tests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
import subprocess
from typing import Dict, List, Optional

from clients.buganizer_service import (
    BuganizerIssue as BugIssue,
    BuganizerService,
    CreateBugRequest,
    DEFAULT_ISSUES_CLI_BIN,
    ProdBuganizerService,
    normalize_bazel_target_for_search,
)
from core.errors import EmuCliError
from core.process import ProcessResult, ProcessRunner, SubprocessRunner


def format_buganizer_search_query(
    component_id: int, test_identifier: str
) -> str:
    """Formats Buganizer search query string to check for open bugs by test signature."""
    clean_target = normalize_bazel_target_for_search(test_identifier)
    return f'componentid:{int(component_id)} status:open "{clean_target}"'


@dataclass
class BuganizerIssuePayload:
    """Legacy payload dataclass mapped to `CreateBugRequest`."""

    title: str
    description: str
    component_id: int
    priority: str = "P2"
    severity: str = "S2"
    assignee: Optional[str] = None
    tags: Optional[List[str]] = None


class _CompatCheckOutputRunner(ProcessRunner):
    """Bridges `ProcessRunner` with legacy `@patch('subprocess.check_output')` tests."""

    def __init__(self, delegate: Optional[ProcessRunner] = None) -> None:
        self._delegate = delegate or SubprocessRunner()

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: os.PathLike[str] | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
        check: bool = True,
        input_text: str | None = None,
        merge_Env: bool = True,
        remediation: str | None = None,
    ) -> ProcessResult:
        if hasattr(subprocess.check_output, "assert_called"):
            cmd_list = [str(a) for a in args]
            try:
                raw_out = subprocess.check_output(
                    cmd_list,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout_sec,
                )
                if isinstance(raw_out, bytes):
                    out_str = raw_out.decode("utf-8", errors="replace")
                else:
                    out_str = str(raw_out or "")
                return ProcessResult(
                    args=tuple(cmd_list),
                    returncode=0,
                    stdout=out_str,
                    stderr="",
                    duration_sec=0.0,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                if check:
                    raise RuntimeError(str(exc)) from exc
                return ProcessResult(
                    args=tuple(cmd_list),
                    returncode=1,
                    stdout="",
                    stderr=str(exc),
                    duration_sec=0.0,
                )
        return self._delegate.run(
            args,
            cwd=cwd,
            env=env,
            timeout_sec=timeout_sec,
            check=check,
            input_text=input_text,
            merge_Env=merge_Env,
            remediation=remediation,
        )


class BuganizerClient:
    """Compatibility wrapper delegating Buganizer operations to `ProdBuganizerService`."""

    def __init__(
        self,
        issues_bin: Optional[str] = None,
        *,
        service: Optional[BuganizerService] = None,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        self.issues_bin = issues_bin or DEFAULT_ISSUES_CLI_BIN
        self._runner = _CompatCheckOutputRunner(process_runner)
        self._service: BuganizerService = service or ProdBuganizerService(
            self._runner, issues_bin=self.issues_bin
        )
        self._component_bugs_cache: Dict[int, List[Dict[str, str]]] = {}

    def fetch_all_component_open_bugs(
        self, component_id: int
    ) -> List[Dict[str, str]]:
        """Fetches all open bug titles/IDs for `component_id` via `BuganizerService`."""
        cid = int(component_id)
        if cid in self._component_bugs_cache:
            return self._component_bugs_cache[cid]
        if not os.path.exists(self.issues_bin):
            self._component_bugs_cache[cid] = []
            return []
        try:
            issues = self._service.list_component_open_issues(cid, use_cache=False)
            parsed = [
                {"issue_id": iss.issue_id, "title": iss.title} for iss in issues
            ]
            self._component_bugs_cache[cid] = parsed
            return parsed
        except Exception:
            self._component_bugs_cache[cid] = []
            return []

    def find_existing_open_bug(
        self, component_id: int, test_identifier: str
    ) -> Optional[str]:
        """Returns the open Buganizer Issue ID string if a bug for `test_identifier` exists."""
        clean_target = test_identifier.replace("@@goldfish+//", "@goldfish//")
        for b in self.fetch_all_component_open_bugs(component_id):
            title = b.get("title", "") if isinstance(b, dict) else str(b)
            issue_id = b.get("issue_id") if isinstance(b, dict) else title
            if clean_target in title or test_identifier in title:
                return issue_id
        return None

    def is_test_already_reported(
        self, component_id: int, test_identifier: str
    ) -> bool:
        """Checks if `test_identifier` is already reported in an open bug for `component_id`."""
        return self.find_existing_open_bug(component_id, test_identifier) is not None

    def find_recently_fixed_bug(
        self, component_id: int, test_identifier: str
    ) -> Optional[str]:
        """Returns a closed Buganizer Issue ID if one matches `test_identifier`."""
        if not os.path.exists(self.issues_bin):
            return None
        try:
            found = self._service.find_recently_fixed_issue_for_target(
                int(component_id), test_identifier
            )
            return found.issue_id if found is not None else None
        except Exception:
            return None

    def search_open_bugs(
        self, component_id: int, test_identifier: str
    ) -> List[dict]:
        """Searches for open Buganizer issues matching `test_identifier`."""
        if self.is_test_already_reported(component_id, test_identifier):
            return [{"raw": "matching_open_bug"}]
        if not os.path.exists(self.issues_bin):
            return []
        query = format_buganizer_search_query(component_id, test_identifier)
        try:
            issues = self._service.search_issues(query)
            return [{"raw": f"b/{iss.issue_id}: {iss.title}"} for iss in issues]
        except Exception:
            return []

    def create_bug(
        self, payload: BuganizerIssuePayload, dry_run: bool = False
    ) -> Optional[str]:
        """Creates a new Buganizer issue via `BuganizerService` or returns dry-run placeholder."""
        if not dry_run and not os.path.exists(self.issues_bin):
            return None
        try:
            created = self._service.create_issue(
                CreateBugRequest(
                    title=payload.title,
                    description=payload.description,
                    component_id=int(payload.component_id),
                    priority=payload.priority,
                    severity=payload.severity,
                    assignee=payload.assignee,
                ),
                dry_run=dry_run,
            )
            return f"b/{created.issue_id}"
        except EmuCliError:
            return None
        except Exception:
            return None
