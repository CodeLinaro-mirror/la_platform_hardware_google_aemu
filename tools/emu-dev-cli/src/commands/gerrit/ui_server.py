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

"""Coordinator facade for the AEMU Fast Gerrit Web UI server (`emu-dev-cli gerrit ui`).

Composes Gerrit CL queue enrichment, review/vote mutations (`notify="OWNER"`),
Jetski inline Q&A transcript extraction, continuous multi-commit diff parsing,
and HTTP REST endpoints from `commands.gerrit.services` and `commands.gerrit.handlers`.
"""

from __future__ import annotations

from typing import Any

from commands.gerrit.handlers.ui_handler import (
    DEFAULT_GERRIT_UI_PORT,
    get_gerrit_ui_template_path,
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
    serve_gerrit_ui,
)
from commands.gerrit.services.context import (
    CONTEXT_SHARED_ATTRS,
    GerritWorkspaceContext,
)
from commands.gerrit.services.diff_parser import (
    DiffService,
    GerritDiffService,
)
from commands.gerrit.services.inline_qa_service import (
    GerritInlineQaService,
    InlineQaService,
)
from commands.gerrit.services.queue_service import (
    GerritQueueService,
    ReviewQueueService,
    _normalize_viewer_email,
)
from commands.gerrit.services.vote_submit_service import (
    GerritVoteSubmitService,
    VoteSubmitService,
)
from commands.gerrit.sidecar_updater import GerritSidecarUpdater
from lib.gerrit_api import DEFAULT_GERRIT_HOST, GerritClient

_FACADE_OWN_ATTRS: frozenset[str] = frozenset({
    "context",
    "queue_service",
    "vote_submit_service",
    "inline_qa_service",
    "diff_service",
    "review_queue_service",
})


class GerritUIService:
    """Unified Gerrit UI coordinator facade composing queue, vote/submit, Q&A, and diff services."""

    context: GerritWorkspaceContext
    queue_service: GerritQueueService
    review_queue_service: GerritQueueService
    vote_submit_service: GerritVoteSubmitService
    inline_qa_service: GerritInlineQaService
    diff_service: GerritDiffService

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
        sidecar_updater: GerritSidecarUpdater | None = None,
        *,
        context: GerritWorkspaceContext | None = None,
        diff_service: GerritDiffService | None = None,
        vote_submit_service: GerritVoteSubmitService | None = None,
        queue_service: GerritQueueService | None = None,
        inline_qa_service: GerritInlineQaService | None = None,
    ) -> None:
        has_injected_context = context is not None
        ctx = context or GerritWorkspaceContext(
            host=host,
            default_viewer="",
            default_query=default_query,
            sidecar_updater=sidecar_updater,
            client=GerritClient(host=host.rstrip("/")),
        )
        object.__setattr__(self, "context", ctx)

        diff_svc = diff_service or GerritDiffService(context=ctx)
        queue_svc = queue_service or GerritQueueService(
            context=ctx, diff_service=diff_svc
        )
        vote_svc = vote_submit_service or GerritVoteSubmitService(
            context=ctx, queue_service=queue_svc
        )
        qa_svc = inline_qa_service or GerritInlineQaService(
            context=ctx, diff_service=diff_svc, queue_service=queue_svc
        )
        if getattr(diff_svc, "_inline_qa_service", None) is None:
            object.__setattr__(diff_svc, "_inline_qa_service", qa_svc)

        object.__setattr__(self, "diff_service", diff_svc)
        object.__setattr__(self, "queue_service", queue_svc)
        object.__setattr__(self, "review_queue_service", queue_svc)
        object.__setattr__(self, "vote_submit_service", vote_svc)
        object.__setattr__(self, "inline_qa_service", qa_svc)

        if not has_injected_context:
            if hasattr(queue_svc, "_load_bug_cache"):
                ctx._bug_cache = queue_svc._load_bug_cache()
            ctx.auth_email = self._detect_auth_email()
            if hasattr(queue_svc, "_detect_viewer_email"):
                ctx.default_viewer = (
                    _normalize_viewer_email(default_viewer)
                    or queue_svc._detect_viewer_email()
                )
            else:
                ctx.default_viewer = default_viewer.strip().lower()
        elif default_viewer and not ctx.default_viewer:
            ctx.default_viewer = _normalize_viewer_email(default_viewer)

    def _collaborators(self) -> tuple[Any, ...]:
        inst_dict = object.__getattribute__(self, "__dict__")
        return tuple(
            c
            for c in (
                inst_dict.get("queue_service"),
                inst_dict.get("vote_submit_service"),
                inst_dict.get("inline_qa_service"),
                inst_dict.get("diff_service"),
            )
            if c is not None
        )

    def _detect_auth_email(self) -> str:
        """Returns the Gerrit account email that `gob-curl` authenticates as on this host."""
        return self.context.detect_auth_email()

    def _invalidate_cl_caches(self, cl_ids: list[str] | None = None) -> None:
        """Clears cached raw queue queries and optional full diff bundle entries after a mutation."""
        self.context.invalidate_cl_caches(cl_ids)

    def _record_known_revisions(self, raw_changes: list[dict[str, Any]]) -> None:
        """Records `(current_revision, patch_set)` per CL number to accelerate diff bundle fetches."""
        self.context.record_known_revisions(raw_changes)

    def _get_known_revision(self, cl_id: str) -> tuple[str, int]:
        """Returns `(current_revision, patch_set)` if previously seen in a queue response."""
        return self.context.get_known_revision(cl_id)

    def __getattr__(self, name: str) -> Any:
        inst_dict = object.__getattribute__(self, "__dict__")
        ctx = inst_dict.get("context")
        if name in CONTEXT_SHARED_ATTRS and ctx is not None:
            return getattr(ctx, name)
        for collab in self._collaborators():
            if hasattr(collab, name):
                return getattr(collab, name)
        if ctx is not None and hasattr(ctx, name):
            return getattr(ctx, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _FACADE_OWN_ATTRS:
            object.__setattr__(self, name, value)
            return
        inst_dict = object.__getattribute__(self, "__dict__")
        ctx = inst_dict.get("context")
        if name in CONTEXT_SHARED_ATTRS and ctx is not None:
            setattr(ctx, name, value)
            return
        object.__setattr__(self, name, value)
        for collab in self._collaborators():
            if hasattr(type(collab), name) or name in collab.__dict__:
                if getattr(value, "__self__", None) is collab:
                    collab.__dict__.pop(name, None)
                else:
                    object.__setattr__(collab, name, value)

    def __delattr__(self, name: str) -> None:
        inst_dict = object.__getattribute__(self, "__dict__")
        found = name in inst_dict
        inst_dict.pop(name, None)
        for collab in self._collaborators():
            if name in collab.__dict__:
                collab.__dict__.pop(name, None)
                found = True
        if not found:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )


__all__ = [
    "DEFAULT_GERRIT_HOST",
    "DEFAULT_GERRIT_UI_PORT",
    "DiffService",
    "GerritUIService",
    "GerritWorkspaceContext",
    "InlineQaService",
    "ReviewQueueService",
    "VoteSubmitService",
    "get_gerrit_ui_template_path",
    "make_gerrit_ui_handler",
    "render_static_gerrit_ui_html",
    "serve_gerrit_ui",
]
