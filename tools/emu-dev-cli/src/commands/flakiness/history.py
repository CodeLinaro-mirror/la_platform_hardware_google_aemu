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

"""Command for 'emu-dev-cli flakiness history'."""

import argparse
import logging
from typing import Any

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import UsageError
from lib.ath_api import TestHistoryEntry, query_test_history
from lib.f1_test_actions import (
    DEFAULT_STUDIO_LOOKBACK_DAYS,
    build_studio_history_sql,
    is_studio_query,
    resolve_studio_branches,
    resolve_studio_targets,
)
from lib.output import format_markdown_table
from . import options
from .common import HistoryQuery, fetch_test_history, is_json_mode, sql_preview_result
from .progress import make_progress_callback

logger = logging.getLogger(__name__)

ACTION = "flakiness history"
DEFAULT_HISTORY_TARGET = "emulator_linux_x64_tsan"
DEFAULT_LIMIT = 50
FAILED_STATUS = "FAILED"
HISTORY_HEADERS = [
    "#",
    "Timestamp",
    "Status",
    "Run Type",
    "Build ID",
    "Invocation ID",
    "Test Identifier",
    "Target Platform",
    "Link",
]

HISTORY_DESCRIPTION = """
Inspect Test Target Historical Run Status (`emu-dev-cli flakiness history`)

Retrieves chronological execution records for a specific test target, showing pass/fail status, timestamps, build IDs, invocation IDs, target platforms, and direct clickable links.

Sample Invocations:
  # Query execution history for hal_plug_adapter_unittests on TSAN across 7 days:
  emu-dev-cli flakiness history --test @goldfish//emulator/plugin/hal/plug:hal_plug_adapter_unittests --target emulator_linux_x64_tsan --days 7

  # Query history across all target platforms combined:
  emu-dev-cli flakiness history --test @goldfish//emulator/libs/async:loop_handoff_test --target all --days 3 --limit 25
"""


def _history_rows(entries: list[TestHistoryEntry]) -> list[list[Any]]:
    return [
        [
            idx,
            e.timestamp,
            f"**{e.status}**" if e.status == FAILED_STATUS else f"`{e.status}`",
            e.run_type,
            f"`{e.build_id or 'N/A'}`",
            f"`{e.invocation_id}`",
            f"`{e.test_identifier or 'N/A'}`",
            f"`{e.target}`",
            f"[Fusion2]({e.test_uri})" if e.test_uri else "N/A",
        ]
        for idx, e in enumerate(entries, 1)
    ]


def _summary_table(summary: dict[str, Any]) -> str:
    return format_markdown_table(
        ["Property", "Value"],
        [
            ["Test Target", f"`{summary['test_target']}`"],
            ["Test Identifier", f"`{summary.get('test_identifier', 'all')}`"],
            ["Target Platform", f"`{summary['target']}`"],
            ["Branch", f"`{summary['branch']}`"],
            ["Time Window", f"{summary['days']} days ({summary['mode']})"],
            ["Total Runs", f"{summary['total_runs']}"],
            ["Passed Runs", f"{summary['passed_runs']}"],
            ["Failed Runs", f"{summary['failed_runs']}"],
            ["Flake Rate", f"**{summary['flake_rate_pct']:.1f}%**"],
        ],
    )


class FlakinessHistoryCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Shows the run history of one test target."""

    name = "history"
    help_text = "Fetch and display the execution run history and status of an individual test target"
    description = HISTORY_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(self, *, history_query: HistoryQuery | None = None) -> None:
        self._history_query = history_query

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--test",
            type=str,
            required=True,
            help="Bazel test target (e.g. @goldfish//emulator/plugin/grpc/services:screen_recording_impl_test)",
        )
        options.add_target_option(
            parser,
            default=DEFAULT_HISTORY_TARGET,
            help_text=f"Target platform matrix or 'all' (default: {DEFAULT_HISTORY_TARGET})",
        )
        options.add_test_identifier_option(
            parser,
            default=None,
            help_text=(
                "Test identifier filter (auto-inferred from --test when omitted; "
                "e.g. 'ets', 'release', 'unit_tests', 'all')"
            ),
        )
        options.add_branch_option(parser)
        options.add_days_option(parser)
        options.add_mode_option(
            parser,
            default=options.MODE_ALL,
            help_text=(
                "Run type filter: 'all' (both pre and post), 'presubmit', or "
                "'postsubmit' (default: all)"
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"Maximum historical runs to display (default: {DEFAULT_LIMIT})",
        )
        options.add_f1_options(parser)
        options.add_token_option(parser)

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        test_label = str(getattr(args, "test", "") or "").strip()
        if not test_label:
            raise UsageError(
                "Bazel test target (--test) cannot be empty for 'flakiness history'.",
                remediation="Pass a non-empty Bazel test target (e.g. --test @goldfish//emulator/libs/async:loop_handoff_test).",
            )
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        if limit <= 0:
            raise UsageError(
                f"History limit (--limit) must be positive (got {limit}).",
                remediation="Pass a positive integer to --limit (e.g. --limit 50).",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        test_target = str(args.test).strip()
        target = str(getattr(args, "target", DEFAULT_HISTORY_TARGET))
        branch = getattr(args, "branch", None)
        studio_mode = is_studio_query(target=target, branch=branch, test_target=test_target)
        raw_days = getattr(args, "days", None)
        default_days = DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else options.ATH_LOOKBACK_DAYS
        days = int(raw_days) if raw_days is not None else default_days
        mode = str(getattr(args, "mode", options.MODE_ALL))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        json_mode = is_json_mode(args, ctx)
        logger.info(
            "Executing flakiness history (test=%s, target=%s, days=%s, studio_mode=%s)",
            test_target, target, days, studio_mode,
        )

        if studio_mode and getattr(args, "print_sql", False):
            sql = build_studio_history_sql(
                test_target=test_target,
                targets=resolve_studio_targets(target),
                branches=resolve_studio_branches(branch),
                days=days,
                mode=mode,
                limit=limit,
            )
            return sql_preview_result(
                sql,
                action=ACTION,
                summary="Generated Studio history F1 SQL query",
                json_mode=json_mode,
            )

        summary, entries = fetch_test_history(
            ctx,
            self._history_query or query_test_history,
            test_target=test_target,
            target=target,
            branch=str(branch or options.DEFAULT_BRANCH),
            days=days,
            mode=mode,
            limit=limit,
            token=getattr(args, "token", None),
            config_name=getattr(args, "config", None),
            accounting_group=getattr(args, "accounting_group", None),
            progress_callback=make_progress_callback(json_mode),
        )
        rows = _history_rows(entries)
        history_table = (
            format_markdown_table(HISTORY_HEADERS, rows)
            if rows
            else "\n*No test runs found.*"
        )
        full_markdown = (
            f"{_summary_table(summary)}\n\n### Historical Test Runs\n\n{history_table}"
        )
        summary_str = (
            f"Fetched history for '{test_target}' on target '{target}' "
            f"({summary['failed_runs']}/{summary['total_runs']} failed, "
            f"{summary['flake_rate_pct']:.1f}% flake rate)"
        )
        data: dict[str, Any] = {
            "summary": summary_str,
            "test_target": summary["test_target"],
            "test_identifier": summary.get("test_identifier", "all"),
            "target": summary["target"],
            "total_runs": summary["total_runs"],
            "passed_runs": summary["passed_runs"],
            "failed_runs": summary["failed_runs"],
            "flake_rate_pct": summary["flake_rate_pct"],
            "markdown_table": full_markdown,
            "history": [e.__dict__ for e in entries],
        }
        return CommandResult(
            data=data, action=ACTION, summary=summary_str, markdown_table=full_markdown
        )
