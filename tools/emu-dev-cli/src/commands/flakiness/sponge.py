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

"""Subcommand handler for 'emu-dev-cli flakiness sponge'."""

import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from lib.ath_api import query_test_history
from lib.output import format_markdown_table, print_result
from lib.resultstore_api import (
    ResultStoreInvocation,
    extract_uuid,
    query_resultstore_invocation,
)
from .progress import make_progress_callback

logger = logging.getLogger(__name__)


def handle_flakiness_sponge(args):
    """Handles 'emu-dev-cli flakiness sponge' subcommand.

    Queries ResultStore to inspect build/test actions, failure exit codes, error
    messages, durations, and diagnostic file URIs for a given Sponge/Fusion2 invocation.
    """
    logger.info("Executing flakiness sponge command (invocation=%s, test=%s, target=%s)", getattr(args, "invocation", None), getattr(args, "test", None), getattr(args, "target", None))
    json_mode = getattr(args, "json", False)
    cb = make_progress_callback(json_mode)

    raw_invocation = getattr(args, "invocation", None)
    test_target = getattr(args, "test", None)
    target_platform = getattr(args, "target", "emulator_linux_x64")
    latest_failures = getattr(args, "latest_failures", 0)

    invocations_to_query: List[Dict[str, Any]] = []

    if raw_invocation:
        uuid = extract_uuid(raw_invocation)
        invocations_to_query.append({
            "uuid": uuid,
            "label": f"Explicit Invocation `{uuid}`",
        })
    elif test_target and latest_failures > 0:
        if cb:
            cb(0, 100, f"Querying execution history for '{test_target}' on '{target_platform}'...")
        _, history_entries = query_test_history(
            test_target=test_target,
            target=target_platform,
            days=getattr(args, "days", 7),
            mode=getattr(args, "mode", "all"),
            limit=50,
            token=getattr(args, "token", None),
            config_name=getattr(args, "config", None),
        )
        failed_entries = [e for e in history_entries if e.status == "FAILED"][:latest_failures]
        for f in failed_entries:
            inv_uuid = extract_uuid(f.test_uri) if f.test_uri else f.invocation_id
            invocations_to_query.append({
                "uuid": inv_uuid,
                "label": f"Run at {f.timestamp} (Build `{f.build_id}`)",
            })
    else:
        sys.stderr.write("❌ Error: Must specify either --invocation <UUID/URL> or both --test <target> and --latest-failures <N>.\n")
        sys.exit(1)

    if not invocations_to_query:
        print_result({
            "summary": "No matching invocations found.",
            "results": [],
            "markdown_table": "*No invocations found to inspect.*",
        }, json_mode=json_mode)
        return

    results = []
    all_markdown_sections = []

    for idx, inv_info in enumerate(invocations_to_query, 1):
        uuid = inv_info["uuid"]
        if cb:
            cb(idx, len(invocations_to_query), f"Querying ResultStore for {uuid}...")

        inv_data = query_resultstore_invocation(
            uuid,
            target_filter=test_target,
        )

        if not inv_data:
            results.append({
                "invocation_id": uuid,
                "status": "UNAVAILABLE",
                "error": "Failed to retrieve from ResultStore RPC",
            })
            all_markdown_sections.append(f"### Invocation `{uuid}`\n\n*Unable to retrieve data from ResultStore.*")
            continue

        action_rows = []
        failed_case_rows = []
        action_headers = ["#", "Target", "Action", "Status", "Test Cases", "Description / Error", "Duration", "Log / XML Files"]
        for a_idx, action in enumerate(inv_data.actions, 1):
            status_badge = (
                f"**`{action.status}`**"
                if action.status == "FAILED"
                else f"`{action.status}`"
            )
            err_text = (
                (action.description or "").replace("\n", " ").replace("|", "\\|")
            )
            if action.error_messages:
                short_errs = [
                    (m[:120] + ("..." if len(m) > 120 else ""))
                    .replace("\n", " ")
                    .replace("|", "\\|")
                    for m in action.error_messages[:2]
                ]
                err_text += " (" + ", ".join(short_errs) + ")"

            cases_str = (
                f"{len(action.failed_test_cases)}/{action.total_test_cases} failed"
                if action.total_test_cases > 0
                else "-"
            )

            for tc in action.failed_test_cases:
                msg_short = (
                    (tc.failure_message or "-")
                    .replace("\n", " ")
                    .replace("|", "\\|")
                )
                if len(msg_short) > 160:
                    msg_short = msg_short[:157] + "..."
                failed_case_rows.append([
                    len(failed_case_rows) + 1,
                    f"`{action.target_id}`",
                    f"`{tc.class_name or '-'}`",
                    f"`{tc.case_name}`",
                    f"**`{tc.result}`**",
                    msg_short,
                ])

            file_links = []
            for f in action.files[:4]:
                file_links.append(f"`{f.uid}` ({f.length:,} bytes)")
            if len(action.files) > 4:
                file_links.append(f"*(+{len(action.files) - 4} more files)*")
            files_str = "<br>".join(file_links) if file_links else "None"

            dur_str = f"{action.duration_seconds:.2f}s" if action.duration_seconds > 0 else "-"
            action_rows.append([
                a_idx,
                f"`{action.target_id}`",
                f"`{action.action_id}`",
                status_badge,
                cases_str,
                err_text or "-",
                dur_str,
                files_str,
            ])

        summary_rows = [
            ["Invocation ID", f"`{inv_data.invocation_id}`"],
            ["Fusion2 Link", f"[Open in Fusion2]({inv_data.fusion_url})"],
            ["Sponge2 Link", f"[Open in Sponge2]({inv_data.sponge_url})"],
            ["Total Actions", f"{len(inv_data.actions)}"],
            ["Failed Actions", f"**{len(inv_data.failed_actions)}**"],
            ["Passed/Built Actions", f"{len(inv_data.passed_actions)}"],
        ]

        summary_tbl = format_markdown_table(["Property", "Value"], summary_rows)
        actions_tbl = format_markdown_table(action_headers, action_rows) if action_rows else "*No actions recorded.*"

        section_md = (
            f"### {inv_info['label']}\n\n"
            f"{summary_tbl}\n\n"
            f"#### Test & Build Actions\n\n"
            f"{actions_tbl}"
        )
        if failed_case_rows:
            failed_cases_tbl = format_markdown_table(
                ["#", "Target", "Test Class", "Test Case", "Result", "Failure Message"],
                failed_case_rows,
            )
            section_md += f"\n\n#### Failed Test Cases\n\n{failed_cases_tbl}"
        all_markdown_sections.append(section_md)

        results.append({
            "invocation_id": inv_data.invocation_id,
            "fusion_url": inv_data.fusion_url,
            "sponge_url": inv_data.sponge_url,
            "total_actions": len(inv_data.actions),
            "failed_actions_count": len(inv_data.failed_actions),
            "failed_actions": [
                {
                    "target_id": a.target_id,
                    "action_id": a.action_id,
                    "status": a.status,
                    "description": a.description,
                    "error_messages": a.error_messages,
                    "duration_seconds": a.duration_seconds,
                    "total_test_cases": a.total_test_cases,
                    "failed_test_cases": [tc.__dict__ for tc in a.failed_test_cases],
                    "files": [f.__dict__ for f in a.files],
                }
                for a in inv_data.failed_actions
            ],
            "actions": [
                {
                    "target_id": a.target_id,
                    "action_id": a.action_id,
                    "status": a.status,
                    "description": a.description,
                    "error_messages": a.error_messages,
                    "duration_seconds": a.duration_seconds,
                    "total_test_cases": a.total_test_cases,
                    "failed_test_cases": [tc.__dict__ for tc in a.failed_test_cases],
                    "files": [f.__dict__ for f in a.files],
                }
                for a in inv_data.actions
            ],
        })

    data = {
        "summary": f"Retrieved Sponge / ResultStore data for {len(results)} invocation(s)",
        "invocations_queried": len(results),
        "results": results,
        "markdown_table": "\n\n---\n\n".join(all_markdown_sections),
    }
    print_result(data, json_mode=json_mode)
