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

from commands.gerrit.handlers.ui_handler import (
    DEFAULT_GERRIT_UI_PORT,
    get_gerrit_ui_template_path,
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
    serve_gerrit_ui,
)
from commands.gerrit.services.diff_parser import GerritDiffService
from commands.gerrit.services.inline_qa_service import GerritInlineQaService
from commands.gerrit.services.queue_service import GerritQueueService
from commands.gerrit.services.vote_submit_service import GerritVoteSubmitService
from commands.gerrit.sidecar_updater import GerritSidecarUpdater
from lib.gerrit_api import DEFAULT_GERRIT_HOST


class GerritUIService(
    GerritQueueService,
    GerritVoteSubmitService,
    GerritInlineQaService,
    GerritDiffService,
):
    """Unified Gerrit UI coordinator facade composing queue, vote/submit, Q&A, and diff services."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
        sidecar_updater: GerritSidecarUpdater | None = None,
    ) -> None:
        super().__init__(
            host=host,
            default_viewer=default_viewer,
            default_query=default_query,
            sidecar_updater=sidecar_updater,
        )
        self.queue_service: GerritQueueService = self
        self.vote_submit_service: GerritVoteSubmitService = self
        self.inline_qa_service: GerritInlineQaService = self
        self.diff_service: GerritDiffService = self


__all__ = [
    "DEFAULT_GERRIT_HOST",
    "DEFAULT_GERRIT_UI_PORT",
    "GerritUIService",
    "get_gerrit_ui_template_path",
    "make_gerrit_ui_handler",
    "render_static_gerrit_ui_html",
    "serve_gerrit_ui",
]
