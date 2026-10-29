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

"""Command for 'emu-dev-cli flakiness list'."""

import argparse
from collections.abc import Callable
import logging
from typing import Any

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import UsageError
from lib.ath_api import (
    SUPPORTED_TARGET_PLATFORMS,
    FlakyTestRecord,
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
from lib.output import format_markdown_table
from . import options
from .common import (
    ProgressCallback,
    is_json_mode,
    query_errors_as_execution_errors,
    sql_preview_result,
)
from .progress import make_progress_callback

logger = logging.getLogger(__name__)

AthFlakyQuery = Callable[..., list[FlakyTestRecord]]

ACTION = "flakiness list"
DEFAULT_MIN_FLAKE_RATE = 5.0
MAX_FLAKE_RATE = 100.0
ANY_FLAKE_LABEL = ">0%"
NO_RECORDS_TABLE = "\n*No flaky tests detected matching query criteria.*"
STUDIO_HEADERS = [
    "#",
    "Test Target",
    "Target Platform",
    "L100 Flake Rate",
    "L250 Flake Rate",
    "Latest Build",
    "Latest Failed Build",
]
ATH_HEADERS = [
    "#",
    "Test Target",
    "Test Identifier",
    "Target Platform",
    "Flake Rate",
    "Runs",
]

LIST_DESCRIPTION = """
Query Flaky Emulator Test Targets (`emu-dev-cli flakiness list`)

Queries Android Test Hub (ATH) / AnTS invocations over a historical time window and aggregates test failure rates into a formatted Markdown table (or raw JSON with --json).

Sample Invocations:
  # List all flaky tests on TSAN with flake rate >= 10% across the last 7 days:
  emu-dev-cli flakiness list --target emulator_linux_x64_tsan --mode all --min-flake-rate 10.0 --days 7

  # List flaky tests across all 5 target platforms combined:
  emu-dev-cli flakiness list --target all --mode all --min-flake-rate 5.0

  # List Android Device Hub test flake rates from Studio TestActions (studio-flakes):
  emu-dev-cli flakiness list --target studio-all --min-flake-rate 0

  # Output machine-readable JSON for agentic parsing:
  emu-dev-cli --json flakiness list --target emulator_linux_x64_tsan

  # Supply explicit OAuth2 token (for macOS workstations):
  emu-dev-cli flakiness list --token "$TOKEN" --target emulator_linux_x64_tsan
"""


def validate_query_window(args: argparse.Namespace) -> None:
    """Checks `--days`, `--min-flake-rate` and `--window` (shared with triage)."""
    raw_days = getattr(args, "days", None)
    if raw_days is not None and int(raw_days) <= 0:
        raise UsageError(
            f"Historical window (--days) must be positive (got {raw_days}).",
            remediation="Pass a positive integer to --days (e.g. --days 7).",
        )
    min_rate = getattr(args, "min_flake_rate", None)
    if min_rate is not None and not 0.0 <= float(min_rate) <= MAX_FLAKE_RATE:
        raise UsageError(
            f"Minimum flake rate (--min-flake-rate) must be between 0.0 and 100.0 (got {min_rate}).",
            remediation="Pass a percentage between 0.0 and 100.0 to --min-flake-rate.",
        )
    window = getattr(args, "window", None)
    if window is not None and int(window) not in options.WINDOW_CHOICES:
        raise UsageError(
            f"Rolling window (--window) must be 100 or 250 (got {window}).",
            remediation="Pass --window 100 or --window 250.",
        )


def _studio_rows(records: list[FlakyTestRecord]) -> list[list[Any]]:
    return [
        [
            idx,
            f"`{r.test_identifier}`",
            f"`{r.target}`",
            f"**{(r.l100_flake_rate_pct or 0.0):.1f}%** ({r.l100_failed or 0}/{r.l100_total or 0})",
            f"**{(r.l250_flake_rate_pct or 0.0):.1f}%** ({r.l250_failed or 0}/{r.l250_total or 0})",
            f"`{r.latest_build_id or '-'}`",
            f"`{r.latest_failed_build_id or '-'}`",
        ]
        for idx, r in enumerate(records, 1)
    ]


def _ath_rows(records: list[FlakyTestRecord]) -> list[list[Any]]:
    return [
        [
            idx,
            f"`{r.test_identifier}`",
            f"`{r.config_name}`",
            f"`{r.target}`",
            f"**{r.flake_rate_pct:.1f}%**",
            f"{r.failed_runs}/{r.total_runs}",
        ]
        for idx, r in enumerate(records, 1)
    ]


class FlakinessListCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Lists flaky tests from Android Test Hub or Studio F1 TestActions."""

    name = "list"
    help_text = "Query Android Test Hub for flaky test targets under specified target platforms"
    description = LIST_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(self, *, ath_query: AthFlakyQuery | None = None) -> None:
        self._ath_query = ath_query

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        options.add_target_option(parser)
        options.add_test_identifier_option(parser)
        options.add_branch_option(parser)
        options.add_min_flake_rate_option(
            parser,
            help_text=(
                "Minimum failure/flake rate percentage threshold (default: any "
                "flake >0%% for Android Device Hub, 5.0 for other targets)"
            ),
        )
        options.add_days_option(parser)
        options.add_mode_option(parser)
        options.add_studio_filter_options(parser)
        options.add_f1_options(parser)
        options.add_token_option(parser)

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        validate_query_window(args)
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        target = str(getattr(args, "target", options.DEFAULT_TARGET))
        branch = getattr(args, "branch", None)
        directory = getattr(args, "directory", None)
        mode = str(getattr(args, "mode", options.MODE_POSTSUBMIT))
        studio_mode = is_studio_query(target=target, branch=branch, directory=directory)
        raw_days = getattr(args, "days", None)
        default_days = DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else options.ATH_LOOKBACK_DAYS
        days = int(raw_days) if raw_days is not None else default_days
        raw_min_flake_rate = getattr(args, "min_flake_rate", None)
        if studio_mode:
            min_flake_rate, min_failed_runs = resolve_studio_min_flake_threshold(
                raw_min_flake_rate,
                default_non_adh_rate=DEFAULT_MIN_FLAKE_RATE,
                directory_or_target=directory,
            )
            window = resolve_studio_window(
                getattr(args, "window", None), directory_or_target=directory
            )
        else:
            min_flake_rate = (
                DEFAULT_MIN_FLAKE_RATE if raw_min_flake_rate is None else float(raw_min_flake_rate)
            )
            min_failed_runs = 0
            window = options.ATH_WINDOW
        logger.info(
            "Executing flakiness list (target=%s, mode=%s, days=%s, studio_mode=%s, "
            "min_flake_rate=%s, min_failed_runs=%s, window=%s)",
            target, mode, days, studio_mode, min_flake_rate, min_failed_runs, window,
        )

        json_mode = is_json_mode(args, ctx)
        if studio_mode and getattr(args, "print_sql", False):
            sql = build_studio_flakes_sql(
                targets=resolve_studio_targets(target),
                branches=resolve_studio_branches(branch),
                directory_prefix=normalize_target_prefix(directory),
                days=days,
                mode=mode,
            )
            return sql_preview_result(
                sql,
                action=ACTION,
                summary="Generated Studio flakes F1 SQL query",
                json_mode=json_mode,
            )

        progress = make_progress_callback(json_mode)
        config_name = str(getattr(args, "config", options.ALL_TEST_IDENTIFIERS))
        if studio_mode:
            records = query_studio_flaky_tests(
                target=target,
                directory=directory,
                branch=str(branch or DEFAULT_STUDIO_BRANCH),
                min_flake_rate=min_flake_rate,
                min_failed_runs=min_failed_runs,
                days=days,
                mode=mode,
                window=window,
                accounting_group=getattr(args, "accounting_group", None),
                progress_callback=progress,
                f1_client=ctx.f1_sql_client,
            )
            rows = _studio_rows(records)
            headers = STUDIO_HEADERS
        else:
            records = self._query_ath(
                args,
                target=target,
                config_name=config_name,
                branch=str(branch or options.DEFAULT_BRANCH),
                min_flake_rate=min_flake_rate,
                days=days,
                mode=mode,
                progress=progress,
            )
            rows = _ath_rows(records)
            headers = ATH_HEADERS

        table = format_markdown_table(headers, rows) if rows else NO_RECORDS_TABLE
        any_flake = studio_mode and min_failed_runs == 1 and min_flake_rate == 0.0
        rate_label = ANY_FLAKE_LABEL if any_flake else f"{min_flake_rate}%"
        summary = (
            f"Found {len(records)} flaky test targets matching criteria "
            f"(min flake rate: {rate_label})"
        )
        data: dict[str, Any] = {
            "summary": summary,
            "target": target,
            "config": normalize_target_prefix(directory) if studio_mode else config_name,
            "records_count": len(records),
            "markdown_table": table,
            "records": [r.__dict__ for r in records],
        }
        if studio_mode:
            data["source"] = "f1_test_actions"
            data["directory"] = normalize_target_prefix(directory)
            data["window"] = window
            data["days"] = days
            data["dashboard_url"] = STUDIO_FLAKES_DASHBOARD_URL
        return CommandResult(
            data=data, action=ACTION, summary=summary, markdown_table=table
        )

    def _query_ath(
        self,
        args: argparse.Namespace,
        *,
        target: str,
        config_name: str,
        branch: str,
        min_flake_rate: float,
        days: int,
        mode: str,
        progress: ProgressCallback,
    ) -> list[FlakyTestRecord]:
        targets = SUPPORTED_TARGET_PLATFORMS if target == options.ALL_TARGETS else [target]
        ath_query = self._ath_query or query_ath_flaky_tests
        records: list[FlakyTestRecord] = []
        for platform in targets:
            with query_errors_as_execution_errors(f"Android Test Hub ({platform})"):
                records.extend(
                    ath_query(
                        target=platform,
                        config_name=config_name,
                        branch=branch,
                        min_flake_rate=min_flake_rate,
                        days=days,
                        mode=mode,
                        token=getattr(args, "token", None),
                        progress_callback=progress,
                    )
                )
        records.sort(
            key=lambda r: (r.failed_runs, r.flake_rate_pct, r.total_runs),
            reverse=True,
        )
        return records
