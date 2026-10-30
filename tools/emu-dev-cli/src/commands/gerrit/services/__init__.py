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

"""Domain services for AEMU Fast Gerrit UI (`emu-dev-cli gerrit ui`)."""

from commands.gerrit.services.diff_parser import (
    GerritDiffService,
    GerritServiceBase,
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
    build_code_followup_prompt,
    build_code_question_prompt,
    discover_transcript_code_questions,
    parse_code_questions_from_transcript_lines,
)
from commands.gerrit.services.queue_service import (
    GerritQueueService,
    _enrich_change_messages,
    build_approval_slots,
    build_viewer_gerrit_query,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    extract_reviewers_and_ccs,
    group_changes_into_stacks,
    is_agent_authored_or_uploaded,
    is_agent_or_bot_email,
    is_cc_reviewer,
    is_explicit_reviewer,
    is_team_review_agent,
    parse_gerrit_timestamp,
    parse_issues_search_output,
)
from commands.gerrit.services.vote_submit_service import (
    GerritVoteSubmitService,
    OWNER_NOTIFY_HANDLING,
)

__all__ = [
    "GerritDiffService",
    "GerritInlineQaService",
    "GerritQueueService",
    "GerritServiceBase",
    "GerritVoteSubmitService",
    "OWNER_NOTIFY_HANDLING",
    "_enrich_change_messages",
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
    "format_repo_display_path",
    "group_changes_into_stacks",
    "is_agent_authored_or_uploaded",
    "is_agent_or_bot_email",
    "is_cc_reviewer",
    "is_explicit_reviewer",
    "is_team_review_agent",
    "parse_code_questions_from_transcript_lines",
    "parse_gerrit_timestamp",
    "parse_issues_search_output",
    "parse_unified_diff_files",
    "resolve_code_search_base_url",
]
