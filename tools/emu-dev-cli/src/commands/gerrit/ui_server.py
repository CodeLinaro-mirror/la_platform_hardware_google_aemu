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

"""Backward-compatible facade for the AEMU Fast Gerrit Web UI server (`emu-dev-cli gerrit ui`).

Delegates Gerrit CL queue enrichment, review/vote mutations (`notify="OWNER"`),
Jetski inline Q&A transcript extraction, continuous multi-commit diff parsing,
and HTTP REST endpoints to cohesive domain modules under
`commands.gerrit.services` and `commands.gerrit.handlers`.
"""

from __future__ import annotations

import getpass
import http.server
import os
from pathlib import Path
import shutil
import socket
import subprocess
import webbrowser

from commands.gerrit.ai_analysis import (
    build_ai_analysis_prompt,
    parse_ai_analysis_from_conversation,
)
from commands.gerrit.handlers.ui_handler import (
    DEFAULT_GERRIT_UI_PORT,
    MAX_PORT_PROBES,
    SIDECAR_PRELOAD_JS,
    SIDECAR_PRELOAD_SCRIPT_TAG,
    _candidate_gerrit_ui_template_paths,
    _is_truthy_query_flag,
    find_available_port,
    get_gerrit_ui_template_path,
    get_gerrit_ui_template_version,
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
    serve_gerrit_ui,
)
from commands.gerrit.presubmit_targets import (
    ACTIVE_TARGETS_CACHE_TTL_SEC,
    COMPLETED_TARGETS_CACHE_TTL_SEC,
    extract_presubmit_status_from_change,
    format_relative_age,
    query_presubmit_targets_via_stubby,
)
from commands.gerrit.services.diff_parser import (
    AEMU_MAP_BASE_URL,
    CODE_SEARCH_BASE_HOST,
    COMMIT_TYPE_SPECS,
    DEFAULT_CODE_SEARCH_GOB_HOST,
    DEFAULT_MAX_DIFF_LINES_PER_FILE,
    DIFF_BUNDLE_CACHE_MAX_ENTRIES,
    DIFF_BUNDLE_SUBTASK_WORKERS,
    DIFF_FETCH_WORKERS,
    GERRIT_MAGIC_FILES,
    GerritDiffService,
    GerritServiceBase,
    HIGH_RISK_MIN_LINES,
    LOW_RISK_MAX_LINES,
    RAW_QUEUE_CACHE_TTL_SEC,
    _C_STYLE_FUNC_RE,
    _GO_FUNC_RE,
    _HIGH_RISK_PATH_RE,
    _HIGH_RISK_SUBJECT_RE,
    _HUNK_CONTEXT_RE,
    _IDENT_CONT_CLASS,
    _IDENT_START_CLASS,
    _JS_ARROW_RE,
    _JS_FUNC_RE,
    _LOW_RISK_SUBJECT_RE,
    _NON_FUNCTION_KEYWORDS,
    _PY_DEF_OR_CLASS_RE,
    _RUST_FN_RE,
    _extract_diff_git_path,
    _extract_gob_host_name,
    _resolve_googleplex_android_superproject,
    build_aemu_map_commit_url,
    build_code_search_file_url,
    classify_commit_type,
    classify_risk_and_load,
    extract_commit_message_from_patch,
    extract_enclosing_function_from_diff_lines,
    extract_symbol_from_code_line,
    format_repo_display_path,
    parse_unified_diff_files,
    resolve_code_search_base_url,
)
from commands.gerrit.services.inline_qa_service import (
    GerritInlineQaService,
    LS_CONNECT_RPC_TIMEOUT_SEC,
    MAX_RECENT_BRAIN_DIRS,
    _CODE_FOLLOWUP_PROMPT_RE,
    _CODE_QUESTION_PROMPT_RE,
    _call_local_language_server_rpc,
    _collect_planner_reply_until_next_prompt,
    _extract_agentapi_conversation_id,
    _extract_agentapi_project_id,
    _extract_followup_question_text,
    _resolve_candidate_conversation_dirs,
    _run_sidecar_agentapi,
    build_code_followup_prompt,
    build_code_question_prompt,
    discover_transcript_code_questions,
    parse_code_questions_from_transcript_lines,
)
from commands.gerrit.services.queue_service import (
    BUG_LOOKUP_TIMEOUT_SEC,
    CLIFF_THRESHOLD_HOURS,
    DEFAULT_EMULATOR_GERRIT_QUERY,
    DEFAULT_ISSUES_CLI_BIN,
    FAST_QUEUE_LIMIT,
    FRESH_THRESHOLD_HOURS,
    FULL_QUEUE_LIMIT,
    GerritQueueService,
    NON_HUMAN_OS_USERS,
    PREWARM_COOLDOWN_SEC,
    RAW_QUEUE_STALE_MAX_SEC,
    SESSION_PRESUBMIT_LAG_SEC,
    SILENT_ATTENTION_HOURS,
    TEAM_ROSTER_EMAILS,
    _ANSI_ESCAPE_RE,
    _BUG_FOOTER_LINE_RE,
    _BUG_ID_NUMBER_RE,
    _INLINE_B_LINK_RE,
    _LABEL_SHORT_NAMES,
    _account_lookup_key,
    _append_unique_participants,
    _build_participant_entry,
    _collect_votes_by_account,
    _compute_is_submittable,
    _detect_capsule_owner_email,
    _detect_git_user_email,
    _detect_os_user_email,
    _extract_autosubmit_and_viewer_votes,
    _extract_human_involvement,
    _extract_human_votes,
    _extract_positive_label_voters,
    _normalize_viewer_email,
    _order_chain,
    build_approval_slots,
    build_viewer_gerrit_query,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    extract_reviewers_and_ccs,
    group_changes_into_stacks,
    is_agent_authored_or_uploaded,
    is_agent_or_bot_email,
    is_explicit_reviewer,
    is_team_review_agent,
    parse_gerrit_timestamp,
    parse_issues_search_output,
)
from commands.gerrit.services.vote_submit_service import (
    DEFAULT_GCHAT_CLI_BIN,
    GCHAT_DM_TIMEOUT_SEC,
    GerritVoteSubmitService,
    MAX_CHAT_DM_LENGTH,
    OWNER_NOTIFY_HANDLING,
    _GOOGLE_CORP_EMAIL_RE,
    _SIMPLE_LDAP_RE,
    _normalize_chat_dm_recipient,
    _parse_gchat_dm_output,
    _resolve_gchat_cli_bin,
    _run_gchat_send_dm_command,
)
from commands.gerrit.sidecar_updater import GerritSidecarUpdater
from commands.harness.review_swarm_status import (
    GERRIT_REVIEW_PAYLOAD_FILENAME,
    read_cl_swarm_status,
    resolve_swarm_dir_for_cl,
)
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GERRIT_CLI_TIMEOUT_SEC,
    GerritClient,
    decode_gerrit_patch,
    parse_cl_identifier,
    strip_xssi_prefix,
    unquote_git_diff_path,
)


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
    "AEMU_MAP_BASE_URL",
    "GerritDiffService",
    "GerritInlineQaService",
    "GerritQueueService",
    "GerritServiceBase",
    "GerritUIService",
    "GerritVoteSubmitService",
    "OWNER_NOTIFY_HANDLING",
    "build_aemu_map_commit_url",
    "build_approval_slots",
    "build_code_followup_prompt",
    "build_code_question_prompt",
    "build_code_search_file_url",
    "build_viewer_gerrit_query",
    "classify_commit_type",
    "classify_risk_and_load",
    "discover_transcript_code_questions",
    "enrich_change_for_ui",
    "extract_bug_ids_from_change",
    "extract_commit_message_from_patch",
    "extract_enclosing_function_from_diff_lines",
    "extract_reviewers_and_ccs",
    "extract_symbol_from_code_line",
    "find_available_port",
    "format_repo_display_path",
    "get_gerrit_ui_template_path",
    "get_gerrit_ui_template_version",
    "group_changes_into_stacks",
    "is_agent_authored_or_uploaded",
    "is_agent_or_bot_email",
    "is_explicit_reviewer",
    "is_team_review_agent",
    "make_gerrit_ui_handler",
    "parse_code_questions_from_transcript_lines",
    "parse_gerrit_timestamp",
    "parse_issues_search_output",
    "parse_unified_diff_files",
    "render_static_gerrit_ui_html",
    "resolve_code_search_base_url",
    "serve_gerrit_ui",
]
