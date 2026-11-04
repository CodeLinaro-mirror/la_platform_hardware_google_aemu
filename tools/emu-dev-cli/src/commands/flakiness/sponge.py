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

import argparse
import logging
from typing import Any

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import UsageError
from lib.ath_api import (
    SUPPORTED_TARGET_CHOICES,
    SUPPORTED_TEST_IDENTIFIER_CHOICES,
    query_test_history,
)
from lib.output import format_markdown_table
from lib.resultstore_api import (
    extract_uuid,
    query_resultstore_invocation,
)
from . import options
from .common import is_json_mode, query_errors_as_execution_errors
from .progress import make_progress_callback

logger = logging.getLogger(__name__)

ACTION = "flakiness sponge"

SPONGE_DESCRIPTION = """
Inspect Sponge & ResultStore Invocation Diagnostics (`emu-dev-cli flakiness sponge`)

Queries ResultStore via Stubby RPC to inspect build/test actions, failure exit codes, error messages,
durations, and diagnostic file URIs (test.log, test.xml) for any Sponge / Fusion2 invocation UUID or URL.

Sample Invocations:
  # Inspect invocation by UUID or full Fusion2 link:
  emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26

  # Inspect invocation and filter for a specific test target:
  emu-dev-cli flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26 --test @@goldfish+//emulator/launcher:can_boot_with_minigbm

  # Automatically inspect the latest 3 failed invocations for a flaky test on macOS:
  emu-dev-cli flakiness sponge --test @@goldfish+//emulator/launcher:can_boot_with_minigbm --target emulator_mac_aarch64 --latest-failures 3

  # Output machine-readable JSON for automated agent triage:
  emu-dev-cli --json flakiness sponge --invocation 82bbf192-5d6f-418c-bef7-fdecb58fba26
"""


class FlakinessSpongeCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli flakiness sponge`."""

    name = "sponge"
    help_text = "Inspect ResultStore / Sponge / Fusion2 invocation actions, errors, durations, and logs"
    description = SPONGE_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--invocation",
            type=str,
            default=None,
            help="Invocation UUID, full Fusion2 URL, or Sponge URL",
        )
        parser.add_argument(
            "--test",
            type=str,
            default=None,
            help="Bazel test target filter (e.g. @@goldfish+//emulator/launcher:can_boot_with_minigbm)",
        )
        options.add_target_option(
            parser,
            default="emulator_linux_x64",
            choices=SUPPORTED_TARGET_CHOICES,
            help_text=options.TARGET_HELP,
        )
        parser.add_argument(
            "--config",
            "--test-id",
            "--suite",
            dest="config",
            type=str,
            default=None,
            choices=SUPPORTED_TEST_IDENTIFIER_CHOICES,
            help="Test identifier filter when querying latest failures (auto-inferred from --test when omitted)",
        )
        parser.add_argument(
            "--latest-failures",
            "--latest_failures",
            dest="latest_failures",
            type=int,
            default=0,
            help="Automatically inspect the latest N failed invocations for the specified test target",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Historical window in days when querying latest failures (default: 7)",
        )
        parser.add_argument(
            "--mode",
            type=str,
            default="all",
            choices=["all", "presubmit", "postsubmit"],
            help="Run type filter when querying latest failures (default: all)",
        )
        options.add_token_option(
            parser,
            help_text="Explicit OAuth2 token for ATH queries",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        raw_inv = str(getattr(args, "invocation", "") or getattr(args, "invocation_id", "") or "").strip()
        test_target = str(getattr(args, "test", "") or "").strip()
        latest_failures = int(getattr(args, "latest_failures", 0) or 0)
        if not raw_inv and not (test_target and latest_failures > 0):
            raise UsageError(
                "Missing invocation target for 'flakiness sponge'.",
                remediation="Specify either --invocation <UUID/URL> or both --test <target> and --latest-failures <N>.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        logger.info(
            "Executing flakiness sponge command (invocation=%s, test=%s, target=%s)",
            getattr(args, "invocation", None),
            getattr(args, "test", None),
            getattr(args, "target", None),
        )
        json_mode = is_json_mode(args, ctx)
        cb = make_progress_callback(json_mode)
        test_target = getattr(args, "test", None)
        invocations_to_query = self._resolve_invocations(args, cb)

        if not invocations_to_query:
            summary = "No matching invocations found."
            md = "*No invocations found to inspect.*"
            data = {
                "summary": summary,
                "invocations_queried": 0,
                "results": [],
                "markdown_table": md,
            }
            return CommandResult(
                data=data,
                action=ACTION,
                summary=summary,
                markdown_table=md,
            )

        results: list[dict[str, Any]] = []
        all_markdown_sections: list[str] = []

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
                all_markdown_sections.append(
                    f"### Invocation `{uuid}`\n\n*Unable to retrieve data from ResultStore.*"
                )
                continue

            all_markdown_sections.append(self._render_sponge_markdown(inv_info, inv_data))
            results.append(self._serialize_invocation_result(inv_data))

        summary = f"Retrieved Sponge / ResultStore data for {len(results)} invocation(s)"
        md_table = "\n\n---\n\n".join(all_markdown_sections)
        data = {
            "summary": summary,
            "invocations_queried": len(results),
            "results": results,
            "markdown_table": md_table,
        }
        return CommandResult(
            data=data,
            action=ACTION,
            summary=summary,
            markdown_table=md_table,
        )

    def _resolve_invocations(
        self, args: argparse.Namespace, cb: Any
    ) -> list[dict[str, Any]]:
        raw_invocation = str(
            getattr(args, "invocation", "") or getattr(args, "invocation_id", "") or ""
        ).strip()
        test_target = getattr(args, "test", None)
        target_platform = getattr(args, "target", "emulator_linux_x64")
        latest_failures = int(getattr(args, "latest_failures", 0) or 0)

        if raw_invocation:
            uuid = extract_uuid(raw_invocation)
            return [{"uuid": uuid, "label": f"Explicit Invocation `{uuid}`"}]
        if test_target and latest_failures > 0:
            if cb:
                cb(0, 100, f"Querying execution history for '{test_target}' on '{target_platform}'...")
            with query_errors_as_execution_errors("Android Test Hub history"):
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
            return [
                {
                    "uuid": extract_uuid(f.test_uri) if f.test_uri else f.invocation_id,
                    "label": f"Run at {f.timestamp} (Build `{f.build_id}`)",
                }
                for f in failed_entries
            ]
        raise UsageError(
            "Missing invocation target for 'flakiness sponge'.",
            remediation="Specify either --invocation <UUID/URL> or both --test <target> and --latest-failures <N>.",
        )

    def _render_sponge_markdown(self, inv_info: dict[str, Any], inv_data: Any) -> str:
        action_rows = []
        failed_case_rows = []
        action_headers = [
            "#",
            "Target",
            "Action",
            "Status",
            "Test Cases",
            "Description / Error",
            "Duration",
            "Log / XML Files",
        ]
        for a_idx, action in enumerate(inv_data.actions, 1):
            status_badge = (
                f"**`{action.status}`**"
                if action.status == "FAILED"
                else f"`{action.status}`"
            )
            err_text = (action.description or "").replace("\n", " ").replace("|", "\\|")
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
                    (tc.failure_message or "-").replace("\n", " ").replace("|", "\\|")
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

            file_links = [
                f"`{f.uid}` ({f.length:,} bytes)" for f in action.files[:4]
            ]
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
        actions_tbl = (
            format_markdown_table(action_headers, action_rows)
            if action_rows
            else "*No actions recorded.*"
        )
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
        return section_md

    @staticmethod
    def _serialize_action(action: Any) -> dict[str, Any]:
        return {
            "target_id": action.target_id,
            "action_id": action.action_id,
            "status": action.status,
            "description": action.description,
            "error_messages": action.error_messages,
            "duration_seconds": action.duration_seconds,
            "total_test_cases": action.total_test_cases,
            "failed_test_cases": [tc.__dict__ for tc in action.failed_test_cases],
            "files": [f.__dict__ for f in action.files],
        }

    def _serialize_invocation_result(self, inv_data: Any) -> dict[str, Any]:
        return {
            "invocation_id": inv_data.invocation_id,
            "fusion_url": inv_data.fusion_url,
            "sponge_url": inv_data.sponge_url,
            "total_actions": len(inv_data.actions),
            "failed_actions_count": len(inv_data.failed_actions),
            "failed_actions": [self._serialize_action(a) for a in inv_data.failed_actions],
            "actions": [self._serialize_action(a) for a in inv_data.actions],
        }
