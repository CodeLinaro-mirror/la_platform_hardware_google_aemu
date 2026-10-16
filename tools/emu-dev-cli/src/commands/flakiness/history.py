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

import logging
from lib.ath_api import query_test_history
from lib.f1_test_actions import (
    DEFAULT_STUDIO_LOOKBACK_DAYS,
    build_studio_history_sql,
    is_studio_query,
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
