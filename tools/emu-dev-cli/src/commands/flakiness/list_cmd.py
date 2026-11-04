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

"""Subcommand handler for 'emu-dev-cli flakiness list'."""

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
    SUPPORTED_TARGET_PLATFORMS,
    SUPPORTED_TEST_IDENTIFIER_CHOICES,
    query_ath_flaky_tests,
)
from lib.f1_test_actions import (
    DEFAULT_STUDIO_BRANCH,
    DEFAULT_STUDIO_LOOKBACK_DAYS,
    STUDIO_FLAKES_DASHBOARD_URL,
    build_studio_flakes_sql,
    is_studio_query,
    normalize_target_prefix,
    query_studio_flaky_tests,
    resolve_studio_branches,
    resolve_studio_min_flake_threshold,
    resolve_studio_targets,
    resolve_studio_window,
)
from lib.output import format_markdown_table, print_result
from .progress import make_progress_callback

logger = logging.getLogger(__name__)


def handle_flakiness_list(args):
    """Handles 'emu-dev-cli flakiness list' subcommand.

    Queries Android Test Hub (or F1 TestActions for Studio targets) for flaky tests.
    """
    studio_mode = is_studio_query(
        target=args.target,
        branch=getattr(args, "branch", None),
        directory=getattr(args, "directory", None),
    )
    raw_days = getattr(args, "days", None)
    days = (
        raw_days
        if raw_days is not None
        else (DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else 7)
    )
    mode = getattr(args, "mode", "postsubmit")
    directory = getattr(args, "directory", None)
    raw_min_flake_rate = getattr(args, "min_flake_rate", None)
    if studio_mode:
        min_flake_rate, min_failed_runs = resolve_studio_min_flake_threshold(
            raw_min_flake_rate,
            default_non_adh_rate=5.0,
            directory_or_target=directory,
        )
        window = resolve_studio_window(
            getattr(args, "window", None),
            directory_or_target=directory,
        )
    else:
        min_flake_rate = 5.0 if raw_min_flake_rate is None else raw_min_flake_rate
        min_failed_runs = 0
        window = 100
    logger.info(
        "Executing flakiness list command (target=%s, mode=%s, days=%s, studio_mode=%s, min_flake_rate=%s, min_failed_runs=%s, window=%s)",
        args.target,
        mode,
        days,
        studio_mode,
        min_flake_rate,
        min_failed_runs,
        window,
    )
    json_mode = getattr(args, "json", False)
    cb = make_progress_callback(json_mode)

    if studio_mode and getattr(args, "print_sql", False):
        sql = build_studio_flakes_sql(
            targets=resolve_studio_targets(args.target),
            branches=resolve_studio_branches(getattr(args, "branch", None)),
            directory_prefix=normalize_target_prefix(directory),
            days=days,
            mode=mode,
        )
        print(sql.strip())
        return

    all_records = []
    config_name = getattr(args, "config", "all")

    if studio_mode:
        all_records = query_studio_flaky_tests(
            target=args.target,
            directory=directory,
            branch=getattr(args, "branch", DEFAULT_STUDIO_BRANCH),
            min_flake_rate=min_flake_rate,
            min_failed_runs=min_failed_runs,
            days=days,
            mode=mode,
            window=window,
            accounting_group=getattr(args, "accounting_group", None),
            progress_callback=cb,
        )
    else:
        targets_to_query = (
            SUPPORTED_TARGET_PLATFORMS if args.target == "all" else [args.target]
        )
        for tgt in targets_to_query:
            recs = query_ath_flaky_tests(
                target=tgt,
                config_name=config_name,
                branch=getattr(args, "branch", "git_emu-main-next"),
                min_flake_rate=min_flake_rate,
                days=days,
                mode=getattr(args, "mode", "all"),
                token=getattr(args, "token", None),
                progress_callback=cb,
            )
            all_records.extend(recs)

        # Sort aggregated results
        all_records.sort(
            key=lambda r: (r.failed_runs, r.flake_rate_pct, r.total_runs),
            reverse=True,
        )

    if studio_mode:
        headers = [
            "#",
            "Test Target",
            "Target Platform",
            "L100 Flake Rate",
            "L250 Flake Rate",
            "Latest Build",
            "Latest Failed Build",
        ]
        rows = []
        for idx, r in enumerate(all_records, 1):
            rows.append(
                [
                    idx,
                    f"`{r.test_identifier}`",
                    f"`{r.target}`",
                    f"**{(r.l100_flake_rate_pct or 0.0):.1f}%** ({r.l100_failed or 0}/{r.l100_total or 0})",
                    f"**{(r.l250_flake_rate_pct or 0.0):.1f}%** ({r.l250_failed or 0}/{r.l250_total or 0})",
                    f"`{r.latest_build_id or '-'}`",
                    f"`{r.latest_failed_build_id or '-'}`",
                ]
            )
    else:
        headers = [
            "#",
            "Test Target",
            "Test Identifier",
            "Target Platform",
            "Flake Rate",
            "Runs",
        ]
        rows = []
        for idx, r in enumerate(all_records, 1):
            rows.append(
                [
                    idx,
                    f"`{r.test_identifier}`",
                    f"`{r.config_name}`",
                    f"`{r.target}`",
                    f"**{r.flake_rate_pct:.1f}%**",
                    f"{r.failed_runs}/{r.total_runs}",
                ]
            )

    table = (
        format_markdown_table(headers, rows)
        if rows
        else "\n*No flaky tests detected matching query criteria.*"
    )
    rate_label = (
        ">0%"
        if (studio_mode and min_failed_runs == 1 and min_flake_rate == 0.0)
        else f"{min_flake_rate}%"
    )
    data = {
        "summary": f"Found {len(all_records)} flaky test targets matching criteria (min flake rate: {rate_label})",
        "target": args.target,
        "config": (
            normalize_target_prefix(directory)
            if studio_mode
            else config_name
        ),
        "records_count": len(all_records),
        "markdown_table": table,
        "records": [r.__dict__ for r in all_records],
    }
    if studio_mode:
        data["source"] = "f1_test_actions"
        data["directory"] = normalize_target_prefix(directory)
        data["window"] = window
        data["days"] = days
        data["dashboard_url"] = STUDIO_FLAKES_DASHBOARD_URL
    print_result(data, json_mode=json_mode)


class FlakinessListCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli flakiness list`."""

    name = "list"
    help_text = "Query Android Test Hub for flaky test targets under specified target platforms"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--target",
            type=str,
            default="emulator_linux_x64",
            choices=SUPPORTED_TARGET_CHOICES,
            help="Target platform matrix or 'all' (default: emulator_linux_x64)",
        )
        parser.add_argument(
            "--config",
            "--test-id",
            "--suite",
            dest="config",
            type=str,
            default="all",
            choices=SUPPORTED_TEST_IDENTIFIER_CHOICES,
            help="Test identifier filter: 'all' (default), 'android_emulator/ets' ('ets'), 'android_emulator/release' ('release'), or 'android_emulator/unit_tests' ('unit_tests')",
        )
        parser.add_argument(
            "--branch",
            type=str,
            default="git_emu-main-next",
            help="Target branch filter (default: git_emu-main-next)",
        )
        parser.add_argument(
            "--min-flake-rate",
            "--min_flake_rate",
            dest="min_flake_rate",
            type=float,
            default=None,
            help="Minimum failure/flake rate percentage threshold (default: any flake >0%% for Android Device Hub, 5.0 for other targets)",
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
            default="postsubmit",
            choices=["all", "presubmit", "postsubmit"],
            help="Run type filter: 'postsubmit' (default), 'presubmit', or 'all' (both pre and post)",
        )
        parser.add_argument(
            "--dir",
            "--directory",
            dest="directory",
            type=str,
            default=None,
            help="Bazel package prefix filter for Studio / TestActions queries",
        )
        parser.add_argument(
            "--window",
            type=int,
            choices=[100, 250],
            default=None,
            help="Rolling run window for Studio / TestActions flake rate threshold (default: 250 for Android Device Hub, 100 otherwise)",
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
        raw_days = getattr(args, "days", None)
        if raw_days is not None and int(raw_days) <= 0:
            raise UsageError(
                f"Historical window (--days) must be positive (got {raw_days}).",
                remediation="Pass a positive integer to --days (e.g. --days 7).",
            )
        min_rate = getattr(args, "min_flake_rate", None)
        if min_rate is not None and (float(min_rate) < 0.0 or float(min_rate) > 100.0):
            raise UsageError(
                f"Minimum flake rate (--min-flake-rate) must be between 0.0 and 100.0 (got {min_rate}).",
                remediation="Pass a percentage between 0.0 and 100.0 to --min-flake-rate.",
            )
        window = getattr(args, "window", None)
        if window is not None and int(window) not in (100, 250):
            raise UsageError(
                f"Rolling window (--window) must be 100 or 250 (got {window}).",
                remediation="Pass --window 100 or --window 250.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        target = str(getattr(args, "target", "emulator_linux_x64"))
        branch = getattr(args, "branch", None)
        directory = getattr(args, "directory", None)
        studio_mode = is_studio_query(
            target=target,
            branch=branch,
            directory=directory,
        )
        raw_days = getattr(args, "days", None)
        days = (
            int(raw_days)
            if raw_days is not None
            else (DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else 7)
        )
        mode = str(getattr(args, "mode", "postsubmit"))
        raw_min_flake_rate = getattr(args, "min_flake_rate", None)
        if studio_mode:
            min_flake_rate, min_failed_runs = resolve_studio_min_flake_threshold(
                raw_min_flake_rate,
                default_non_adh_rate=5.0,
                directory_or_target=directory,
            )
            window = resolve_studio_window(
                getattr(args, "window", None),
                directory_or_target=directory,
            )
        else:
            min_flake_rate = (
                5.0 if raw_min_flake_rate is None else float(raw_min_flake_rate)
            )
            min_failed_runs = 0
            window = 100

        json_mode = bool(ctx.config.json_mode or getattr(args, "json", False))
        if studio_mode and getattr(args, "print_sql", False):
            sql = build_studio_flakes_sql(
                targets=resolve_studio_targets(target),
                branches=resolve_studio_branches(branch),
                directory_prefix=normalize_target_prefix(directory),
                days=days,
                mode=mode,
            )
            if not json_mode:
                print(sql.strip())
            return CommandResult(
                data={"sql": sql.strip()},
                action="flakiness list",
                summary="Generated Studio flakes F1 SQL query",
                metadata={META_KEY_ALREADY_RENDERED: not json_mode},
            )

        all_records = []
        config_name = str(getattr(args, "config", "all"))
        if studio_mode:
            f1_client = ctx.services.get(SERVICE_F1_SQL)
            all_records = query_studio_flaky_tests(
                target=target,
                directory=directory,
                branch=str(branch or DEFAULT_STUDIO_BRANCH),
                min_flake_rate=min_flake_rate,
                min_failed_runs=min_failed_runs,
                days=days,
                mode=mode,
                window=window,
                accounting_group=getattr(args, "accounting_group", None),
                f1_client=f1_client,
            )
        else:
            targets_to_query = (
                SUPPORTED_TARGET_PLATFORMS if target == "all" else [target]
            )
            for tgt in targets_to_query:
                recs = query_ath_flaky_tests(
                    target=tgt,
                    config_name=config_name,
                    branch=str(branch or "git_emu-main-next"),
                    min_flake_rate=min_flake_rate,
                    days=days,
                    mode=mode,
                    token=getattr(args, "token", None),
                )
                all_records.extend(recs)
            all_records.sort(
                key=lambda r: (r.failed_runs, r.flake_rate_pct, r.total_runs),
                reverse=True,
            )

        if studio_mode:
            headers = [
                "#",
                "Test Target",
                "Target Platform",
                "L100 Flake Rate",
                "L250 Flake Rate",
                "Latest Build",
                "Latest Failed Build",
            ]
            rows = [
                [
                    idx,
                    f"`{r.test_identifier}`",
                    f"`{r.target}`",
                    f"**{(r.l100_flake_rate_pct or 0.0):.1f}%** ({r.l100_failed or 0}/{r.l100_total or 0})",
                    f"**{(r.l250_flake_rate_pct or 0.0):.1f}%** ({r.l250_failed or 0}/{r.l250_total or 0})",
                    f"`{r.latest_build_id or '-'}`",
                    f"`{r.latest_failed_build_id or '-'}`",
                ]
                for idx, r in enumerate(all_records, 1)
            ]
        else:
            headers = [
                "#",
                "Test Target",
                "Test Identifier",
                "Target Platform",
                "Flake Rate",
                "Runs",
            ]
            rows = [
                [
                    idx,
                    f"`{r.test_identifier}`",
                    f"`{r.config_name}`",
                    f"`{r.target}`",
                    f"**{r.flake_rate_pct:.1f}%**",
                    f"{r.failed_runs}/{r.total_runs}",
                ]
                for idx, r in enumerate(all_records, 1)
            ]

        table = (
            format_markdown_table(headers, rows)
            if rows
            else "\n*No flaky tests detected matching query criteria.*"
        )
        rate_label = (
            ">0%"
            if (studio_mode and min_failed_runs == 1 and min_flake_rate == 0.0)
            else f"{min_flake_rate}%"
        )
        summary = f"Found {len(all_records)} flaky test targets matching criteria (min flake rate: {rate_label})"
        data: dict[str, Any] = {
            "summary": summary,
            "target": target,
            "config": (
                normalize_target_prefix(directory)
                if studio_mode
                else config_name
            ),
            "records_count": len(all_records),
            "markdown_table": table,
            "records": [r.__dict__ for r in all_records],
        }
        if studio_mode:
            data["source"] = "f1_test_actions"
            data["directory"] = normalize_target_prefix(directory)
            data["window"] = window
            data["days"] = days
            data["dashboard_url"] = STUDIO_FLAKES_DASHBOARD_URL

        return CommandResult(
            data=data,
            action="flakiness list",
            summary=summary,
            markdown_table=table,
        )
