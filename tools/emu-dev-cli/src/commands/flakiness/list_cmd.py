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
from lib.output import format_markdown_table, print_result
from .progress import make_progress_callback

logger = logging.getLogger(__name__)


def handle_flakiness_list(args):
    """Handles 'emu-dev-cli flakiness list' subcommand.

    Queries Android Test Hub for flaky tests under specified target platforms.
    """
    logger.info("Executing flakiness list command (target=%s, mode=%s, days=%s)", args.target, getattr(args, "mode", "postsubmit"), getattr(args, "days", 7))
    json_mode = getattr(args, "json", False)
    cb = make_progress_callback(json_mode)

    targets_to_query = (
        SUPPORTED_TARGET_PLATFORMS if args.target == "all" else [args.target]
    )
    all_records = []

    for tgt in targets_to_query:
        recs = query_ath_flaky_tests(
            target=tgt,
            config_name=getattr(args, "config", "devtools/emulator"),
            branch=getattr(args, "branch", "git_emu-main-next"),
            min_flake_rate=getattr(args, "min_flake_rate", 5.0),
            days=getattr(args, "days", 7),
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

    headers = ["#", "Test Target", "Target Platform", "Flake Rate", "Runs"]
    rows = []
    for idx, r in enumerate(all_records, 1):
        rows.append(
            [
                idx,
                f"`{r.test_identifier}`",
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
    data = {
        "summary": f"Found {len(all_records)} flaky test targets matching criteria (min flake rate: {getattr(args, 'min_flake_rate', 5.0)}%)",
        "target": args.target,
        "records_count": len(all_records),
        "markdown_table": table,
        "records": [r.__dict__ for r in all_records],
    }
    print_result(data, json_mode=json_mode)
