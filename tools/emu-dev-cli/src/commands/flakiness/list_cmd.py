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

import logging
from lib.ath_api import (
    SUPPORTED_TARGET_PLATFORMS,
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
