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

"""Subcommand handler for 'emu-dev-cli flakiness history'."""

import argparse
import logging
from typing import Any

from core.command import (
    Command,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
)
from core.context import ExecutionContext, SERVICE_F1_SQL
from core.errors import UsageError
from lib.ath_api import (
    SUPPORTED_TARGET_CHOICES,
    SUPPORTED_TEST_IDENTIFIER_CHOICES,
    query_test_history,
)
from lib.f1_test_actions import (
    DEFAULT_STUDIO_LOOKBACK_DAYS,
    build_studio_history_sql,
    is_studio_query,
    query_studio_test_history,
    resolve_studio_branches,
    resolve_studio_targets,
)
from lib.output import format_markdown_table, print_result
from .progress import make_progress_callback

logger = logging.getLogger(__name__)


def handle_flakiness_history(args):
    """Handles 'emu-dev-cli flakiness history' subcommand.

    Fetches and displays chronological test execution records with clickable
    Fusion2 links.
    """
    studio_mode = is_studio_query(
        target=args.target,
        branch=getattr(args, "branch", None),
        test_target=args.test,
    )
    raw_days = getattr(args, "days", None)
    days = (
        raw_days
        if raw_days is not None
        else (DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else 7)
    )
    mode = getattr(args, "mode", "all")
    limit = getattr(args, "limit", 50)
    logger.info(
        "Executing flakiness history command (test=%s, target=%s, days=%s, studio_mode=%s)",
        args.test,
        args.target,
        days,
        studio_mode,
    )
    json_mode = getattr(args, "json", False)
    cb = make_progress_callback(json_mode)

    if studio_mode and getattr(args, "print_sql", False):
        sql = build_studio_history_sql(
            test_target=args.test,
            targets=resolve_studio_targets(args.target),
            branches=resolve_studio_branches(getattr(args, "branch", None)),
            days=days,
            mode=mode,
            limit=limit,
        )
        print(sql.strip())
        return

    extra_kwargs = {}
    if getattr(args, "accounting_group", None):
        extra_kwargs["accounting_group"] = args.accounting_group

    summary, entries = query_test_history(
        test_target=args.test,
        target=args.target,
        branch=getattr(args, "branch", "git_emu-main-next"),
        days=days,
        mode=mode,
        limit=limit,
        token=getattr(args, "token", None),
        progress_callback=cb,
        config_name=getattr(args, "config", None),
        **extra_kwargs,
    )

    headers = [
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
    rows = []
    for idx, e in enumerate(entries, 1):
        status_fmt = (
            f"**{e.status}**" if e.status == "FAILED" else f"`{e.status}`"
        )
        link_fmt = f"[Fusion2]({e.test_uri})" if e.test_uri else "N/A"
        rows.append(
            [
                idx,
                e.timestamp,
                status_fmt,
                e.run_type,
                f"`{e.build_id or 'N/A'}`",
                f"`{e.invocation_id}`",
                f"`{getattr(e, 'test_identifier', 'N/A') or 'N/A'}`",
                f"`{e.target}`",
                link_fmt,
            ]
        )

    summary_rows = [
        ["Test Target", f"`{summary['test_target']}`"],
        ["Test Identifier", f"`{summary.get('test_identifier', 'all')}`"],
        ["Target Platform", f"`{summary['target']}`"],
        ["Branch", f"`{summary['branch']}`"],
        ["Time Window", f"{summary['days']} days ({summary['mode']})"],
        ["Total Runs", f"{summary['total_runs']}"],
        ["Passed Runs", f"{summary['passed_runs']}"],
        ["Failed Runs", f"{summary['failed_runs']}"],
        ["Flake Rate", f"**{summary['flake_rate_pct']:.1f}%**"],
    ]
    summary_table = format_markdown_table(["Property", "Value"], summary_rows)
    history_table = (
        format_markdown_table(headers, rows)
        if rows
        else "\n*No test runs found.*"
    )
    full_markdown = (
        f"{summary_table}\n\n### Historical Test Runs\n\n{history_table}"
    )

    data = {
        "summary": f"Fetched history for '{args.test}' on target '{args.target}' ({summary['failed_runs']}/{summary['total_runs']} failed, {summary['flake_rate_pct']:.1f}% flake rate)",
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
    print_result(data, json_mode=json_mode)


class FlakinessHistoryCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli flakiness history`."""

    name = "history"
    help_text = "Fetch and display the execution run history and status of an individual test target"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--test",
            type=str,
            required=True,
            help="Bazel test target",
        )
        parser.add_argument(
            "--target",
            type=str,
            default="emulator_linux_x64_tsan",
            choices=SUPPORTED_TARGET_CHOICES,
            help="Target platform matrix or 'all' (default: emulator_linux_x64_tsan)",
        )
        parser.add_argument(
            "--config",
            "--test-id",
            "--suite",
            dest="config",
            type=str,
            default=None,
            choices=SUPPORTED_TEST_IDENTIFIER_CHOICES,
            help="Test identifier filter",
        )
        parser.add_argument(
            "--branch",
            type=str,
            default="git_emu-main-next",
            help="Target branch filter (default: git_emu-main-next)",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Historical window in days (default: 7 for ATH, 20 for studio-*)",
        )
        parser.add_argument(
            "--mode",
            type=str,
            default="all",
            choices=["all", "presubmit", "postsubmit"],
            help="Run type filter",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum historical runs to display (default: 50)",
        )
        parser.add_argument(
            "--accounting-group",
            "--accounting_group",
            dest="accounting_group",
            type=str,
            default=None,
            help="Optional F1 MDB accounting group (--f1_sql_accounting_group)",
        )
        parser.add_argument(
            "--print-sql",
            "--print_sql",
            dest="print_sql",
            action="store_true",
            help="Print the generated F1 SQL query for Studio targets and exit without executing",
        )
        parser.add_argument(
            "--token",
            type=str,
            default=None,
            help="Explicit OAuth2 token with androidbuild.internal scope",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        test_label = str(getattr(args, "test", "") or "").strip()
        if not test_label:
            raise UsageError(
                "Bazel test target (--test) cannot be empty for 'flakiness history'.",
                remediation="Pass a non-empty Bazel test target (e.g. --test @goldfish//emulator/libs/async:loop_handoff_test).",
            )
        limit = int(getattr(args, "limit", 50))
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
        target = str(getattr(args, "target", "emulator_linux_x64_tsan"))
        branch = getattr(args, "branch", None)
        studio_mode = is_studio_query(
            target=target,
            branch=branch,
            test_target=test_target,
        )
        raw_days = getattr(args, "days", None)
        days = (
            int(raw_days)
            if raw_days is not None
            else (DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else 7)
        )
        mode = str(getattr(args, "mode", "all"))
        limit = int(getattr(args, "limit", 50))
        json_mode = bool(ctx.config.json_mode or getattr(args, "json", False))

        if studio_mode and getattr(args, "print_sql", False):
            sql = build_studio_history_sql(
                test_target=test_target,
                targets=resolve_studio_targets(target),
                branches=resolve_studio_branches(branch),
                days=days,
                mode=mode,
                limit=limit,
            )
            if not json_mode:
                print(sql.strip())
            return CommandResult(
                data={"sql": sql.strip()},
                action="flakiness history",
                summary="Generated Studio history F1 SQL query",
                metadata={META_KEY_ALREADY_RENDERED: not json_mode},
            )

        if studio_mode:
            f1_client = ctx.services.get(SERVICE_F1_SQL)
            summary, entries = query_studio_test_history(
                test_target=test_target,
                target=target,
                branch=str(branch or "git_studio-main"),
                days=days,
                mode=mode,
                limit=limit,
                accounting_group=getattr(args, "accounting_group", None),
                f1_client=f1_client,
            )
        else:
            summary, entries = query_test_history(
                test_target=test_target,
                target=target,
                branch=str(branch or "git_emu-main-next"),
                days=days,
                mode=mode,
                limit=limit,
                token=getattr(args, "token", None),
                config_name=getattr(args, "config", None),
            )

        headers = [
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
        rows = []
        for idx, e in enumerate(entries, 1):
            status_fmt = (
                f"**{e.status}**" if e.status == "FAILED" else f"`{e.status}`"
            )
            link_fmt = f"[Fusion2]({e.test_uri})" if e.test_uri else "N/A"
            rows.append(
                [
                    idx,
                    e.timestamp,
                    status_fmt,
                    e.run_type,
                    f"`{e.build_id or 'N/A'}`",
                    f"`{e.invocation_id}`",
                    f"`{getattr(e, 'test_identifier', 'N/A') or 'N/A'}`",
                    f"`{e.target}`",
                    link_fmt,
                ]
            )

        summary_rows = [
            ["Test Target", f"`{summary['test_target']}`"],
            ["Test Identifier", f"`{summary.get('test_identifier', 'all')}`"],
            ["Target Platform", f"`{summary['target']}`"],
            ["Branch", f"`{summary['branch']}`"],
            ["Time Window", f"{summary['days']} days ({summary['mode']})"],
            ["Total Runs", f"{summary['total_runs']}"],
            ["Passed Runs", f"{summary['passed_runs']}"],
            ["Failed Runs", f"{summary['failed_runs']}"],
            ["Flake Rate", f"**{summary['flake_rate_pct']:.1f}%**"],
        ]
        summary_table = format_markdown_table(
            ["Property", "Value"], summary_rows
        )
        history_table = (
            format_markdown_table(headers, rows)
            if rows
            else "\n*No test runs found.*"
        )
        full_markdown = (
            f"{summary_table}\n\n### Historical Test Runs\n\n{history_table}"
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
            data=data,
            action="flakiness history",
            summary=summary_str,
            markdown_table=full_markdown,
        )
