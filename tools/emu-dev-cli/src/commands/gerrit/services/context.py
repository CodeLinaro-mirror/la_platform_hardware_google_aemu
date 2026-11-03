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

"""Shared workspace and session context for AEMU Fast Gerrit UI services."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from commands.gerrit.sidecar_updater import GerritSidecarUpdater
from lib.gerrit_api import DEFAULT_GERRIT_HOST, GerritClient

CONTEXT_SHARED_ATTRS: frozenset[str] = frozenset({
    "host",
    "client",
    "sidecar_updater",
    "_session_lock",
    "_session_approved_cls",
    "_session_votes",
    "_session_presubmit_voted_at",
    "_presubmit_targets_cache",
    "_raw_queue_cache",
    "_known_revisions_by_cl",
    "_diff_revision_cache",
    "_full_diff_bundle_cache",
    "_file_content_cache",
    "_file_blame_cache",
    "_reviewed_files_cache",
    "_included_in_cache",
    "_last_prewarm_by_viewer",
    "_prewarm_in_progress",
    "_qa_lock",
    "_qa_threads",
    "_tracked_qa_conv_ids",
    "_deleted_qa_conv_ids",
    "_analyzer_conv_ids",
    "_ignored_steps_by_conv",
    "_cached_project_id",
    "_next_qa_seq",
    "_ai_analysis_lock",
    "_ai_analysis_state",
    "_ai_analysis_gen",
    "_ai_analysis_cache_dir",
    "_bug_cache_path",
    "_bug_cache",
    "auth_email",
    "default_viewer",
    "default_query",
})


class GerritWorkspaceContext:
    """Parameter object holding shared Gerrit client, caches, locks, and viewer state."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
        auth_email: str = "",
        client: GerritClient | None = None,
        sidecar_updater: GerritSidecarUpdater | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.client = client or GerritClient(host=self.host)
        self.sidecar_updater = sidecar_updater or GerritSidecarUpdater()
        self._session_lock = threading.Lock()
        self._session_approved_cls: set[int] = set()
        self._session_votes: dict[int, dict[str, int]] = {}
        self._session_presubmit_voted_at: dict[int, float] = {}
        self._presubmit_targets_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._raw_queue_cache: dict[str, tuple[float, list[dict[str, Any]], bool]] = {}
        self._known_revisions_by_cl: dict[str, tuple[str, int]] = {}
        self._diff_revision_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._full_diff_bundle_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._file_content_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._file_blame_cache: dict[tuple[str, str, str, bool], tuple[float, list[dict[str, Any]]]] = {}
        self._reviewed_files_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self._included_in_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_prewarm_by_viewer: dict[str, float] = {}
        self._prewarm_in_progress = False
        self._qa_lock = threading.Lock()
        self._qa_threads: dict[str, dict[str, Any]] = {}
        self._tracked_qa_conv_ids: set[str] = set()
        self._deleted_qa_conv_ids: set[str] = set()
        self._analyzer_conv_ids: set[str] = set()
        self._ignored_steps_by_conv: dict[str, set[int]] = {}
        self._cached_project_id: str = ""
        self._next_qa_seq: int = 1
        self._ai_analysis_lock = threading.Lock()
        self._ai_analysis_state: dict[tuple[str, int], dict[str, Any]] = {}
        self._ai_analysis_gen: dict[tuple[str, int], int] = {}
        self._ai_analysis_cache_dir = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "ai_analysis"
        )
        self._bug_cache_path = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "bug_metadata_cache.json"
        )
        self._bug_cache: dict[str, dict[str, str]] = {}
        self.auth_email = auth_email.strip().lower()
        self.default_viewer = default_viewer.strip().lower()
        self.default_query = default_query

    def detect_auth_email(self) -> str:
        """Returns the Gerrit account email that `gob-curl` authenticates as on this host."""
        try:
            acct = self.client._request_json("/a/accounts/self")
            return (acct.get("email") or "").strip().lower()
        except Exception:
            return ""

    def invalidate_cl_caches(self, cl_ids: list[str] | None = None) -> None:
        """Clears cached raw queue queries and optional full diff bundle entries after a mutation."""
        target_ids = {str(cid).strip() for cid in (cl_ids or []) if str(cid).strip()}
        with self._session_lock:
            self._raw_queue_cache.clear()
            if not target_ids:
                return
            stale_keys = [k for k in self._full_diff_bundle_cache if k[0] in target_ids]
            for key in stale_keys:
                self._full_diff_bundle_cache.pop(key, None)
            stale_rev_keys = [k for k in self._reviewed_files_cache if k[0] in target_ids]
            for key in stale_rev_keys:
                self._reviewed_files_cache.pop(key, None)
            for cid in target_ids:
                self._included_in_cache.pop(cid, None)

    _invalidate_cl_caches = invalidate_cl_caches

    def record_known_revisions(self, raw_changes: list[dict[str, Any]]) -> None:
        """Records `(current_revision, patch_set)` per CL number to accelerate diff bundle fetches."""
        updates: dict[str, tuple[str, int]] = {}
        for ch in raw_changes:
            num = ch.get("_number")
            if not num:
                continue
            cur_rev = str(ch.get("current_revision") or "")
            rev_obj = ((ch.get("revisions") or {}).get(cur_rev) or {}) if cur_rev else {}
            cur_ps = int(ch.get("current_revision_number", 0) or rev_obj.get("_number", 1) or 1)
            updates[str(num)] = (cur_rev, max(1, cur_ps))
        if updates:
            with self._session_lock:
                self._known_revisions_by_cl.update(updates)

    _record_known_revisions = record_known_revisions

    def get_known_revision(self, cl_id: str) -> tuple[str, int]:
        """Returns `(current_revision, patch_set)` if previously seen in a queue response."""
        with self._session_lock:
            return self._known_revisions_by_cl.get(cl_id, ("", 0))

    _get_known_revision = get_known_revision


__all__ = [
    "CONTEXT_SHARED_ATTRS",
    "GerritWorkspaceContext",
]
