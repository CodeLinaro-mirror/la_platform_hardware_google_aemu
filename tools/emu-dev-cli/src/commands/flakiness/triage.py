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

"""Subcommand handler for 'emu-dev-cli flakiness triage'."""

from pathlib import Path
import re
import sys
from typing import Optional

from lib.ai_patch_generator import (
    format_bug_body_with_ai_patch,
    generate_ai_patch_for_test,
)
import logging
from lib.ath_api import (
    SUPPORTED_TARGET_PLATFORMS,
    infer_test_identifier_for_target,
    query_ath_flaky_tests,
    sanitize_bazel_target,
)
from lib.buganizer import BuganizerClient, BuganizerIssuePayload
from lib.f1_test_actions import (
    DEFAULT_STUDIO_BRANCH,
    DEFAULT_STUDIO_COMPONENT_ID,
    DEFAULT_STUDIO_DIR_PREFIX,
    DEFAULT_STUDIO_LOOKBACK_DAYS,
    build_studio_flakes_sql,
    is_android_device_hub_target,
    is_studio_query,
    normalize_target_prefix,
    query_studio_flaky_tests,
    resolve_studio_branches,
    resolve_studio_min_flake_threshold,
    resolve_studio_targets,
    resolve_studio_window,
)
from lib.output import format_markdown_table, print_result

logger = logging.getLogger(__name__)


def handle_flakiness_triage(args):
    """Handles 'emu-dev-cli flakiness triage' subcommand.

    Queries ATH (or F1 TestActions for Studio targets), deduplicates against open
    Buganizer issues, downloads diagnostic logs, runs Jetski AI root cause analysis,
    saves report artifacts to disk, and optionally files Buganizer issues with --auto-file.
    """
    json_mode = getattr(args, "json", False)
    target = getattr(args, "target", "emulator_linux_x64")
    explicit_test = getattr(args, "test", None)
    directory = getattr(args, "directory", None)
    studio_mode = is_studio_query(
        target=target,
        branch=getattr(args, "branch", None),
        directory=directory,
        test_target=explicit_test,
    )
    raw_days = getattr(args, "days", None)
    days = (
        raw_days
        if raw_days is not None
        else (DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else 7)
    )
    mode = getattr(args, "mode", "postsubmit")
    raw_component = getattr(args, "component_id", None)
    if raw_component:
        component_id = raw_component
    elif studio_mode and is_android_device_hub_target(
        directory or explicit_test or DEFAULT_STUDIO_DIR_PREFIX
    ):
        component_id = DEFAULT_STUDIO_COMPONENT_ID
    else:
        component_id = "1016880"

    logger.info(
        "Executing flakiness triage command (target=%s, test=%s, component_id=%s, studio_mode=%s, auto_file=%s, dry_run=%s)",
        target,
        explicit_test,
        component_id,
        studio_mode,
        getattr(args, "auto_file", False),
        getattr(args, "dry_run", False),
    )

    if studio_mode and getattr(args, "print_sql", False):
        sql = build_studio_flakes_sql(
            targets=resolve_studio_targets(target),
            branches=resolve_studio_branches(getattr(args, "branch", None)),
            directory_prefix=normalize_target_prefix(directory),
            days=days,
            mode=mode,
            test_target=explicit_test,
        )
        print(sql.strip())
        return

    # Verify upfront environment readiness (agent session or standalone jetski binary)
    from lib.agent_dispatcher import AgentDispatcher
    AgentDispatcher().verify_environment()

    flaky_records = []
    cb = getattr(args, "progress_callback", None)

    if cb is None and not json_mode:
        def console_progress_cb(current: int, total: int, msg: str):
            sys.stderr.write(f"\r⏳ [{current}/{total}] {msg}                 ")
            sys.stderr.flush()
        cb = console_progress_cb

    from lib.oauth import OAuthTokenManager

    auth_token = (
        None
        if studio_mode
        else (getattr(args, "token", None) or OAuthTokenManager().get_token())
    )

    if studio_mode:
        studio_hint = directory or explicit_test
        min_flake_rate, min_failed_runs = resolve_studio_min_flake_threshold(
            getattr(args, "min_flake_rate", None),
            default_non_adh_rate=10.0,
            directory_or_target=studio_hint,
        )
        window = resolve_studio_window(
            getattr(args, "window", None),
            directory_or_target=studio_hint,
        )
        sys.stderr.write(
            f"🔍 Querying F1 TestActions for Studio flaky tests (target={target}, window={window})...\n"
        )
        recs = query_studio_flaky_tests(
            target=target,
            directory=directory,
            branch=getattr(args, "branch", DEFAULT_STUDIO_BRANCH),
            min_flake_rate=(0.0 if explicit_test else min_flake_rate),
            min_failed_runs=(0 if explicit_test else max(1, min_failed_runs)),
            days=days,
            mode=mode,
            window=window,
            test_target=explicit_test,
            accounting_group=getattr(args, "accounting_group", None),
            progress_callback=cb,
        )
        sys.stderr.write("\n")
        if explicit_test:
            clean_explicit = sanitize_bazel_target(explicit_test)
            matched = [
                r
                for r in recs
                if sanitize_bazel_target(r.test_identifier) == clean_explicit
            ]
            if matched:
                rec = matched[0]
                if rec.failed_runs == 0 or rec.flake_rate_pct == 0.0:
                    sys.stderr.write(
                        f"\nℹ️ Notice: Test target '{explicit_test}' has 0 failing runs (0.0% failure rate across {rec.total_runs} runs) on target '{rec.target}'.\n"
                        f"   Skipping AI root-cause analysis as there are no failure instances to analyze.\n"
                    )
                    return
                flaky_records.extend(matched)
            else:
                sys.stderr.write(
                    f"\n❌ Error: No execution or flakiness records found in F1 TestActions for test target '{explicit_test}' on target '{target}'.\n"
                    f"   Analysis aborted. No synthetic or fake test data will be generated.\n"
                )
                raise RuntimeError(
                    f"No execution or flakiness records found in F1 TestActions for test target '{explicit_test}' on target '{target}'."
                )
        else:
            flaky_records.extend(recs)
    elif explicit_test:
        sys.stderr.write(f"🔍 Querying Android Test Hub (ATH) for records matching '{explicit_test}'...\n")
        raw_config = getattr(args, "config", None)
        resolved_config = (
            raw_config
            if raw_config and raw_config != "all"
            else infer_test_identifier_for_target(explicit_test)
        )
        recs = query_ath_flaky_tests(
            target=target,
            config_name=resolved_config,
            branch=getattr(args, "branch", "git_emu-main-next"),
            min_flake_rate=0.0,
            days=days,
            mode=mode,
            token=auth_token,
            progress_callback=cb,
        )
        sys.stderr.write("\n")

        clean_explicit = sanitize_bazel_target(explicit_test)
        matched = [
            r
            for r in recs
            if sanitize_bazel_target(r.test_identifier) == clean_explicit
        ]
        if matched:
            rec = matched[0]
            if rec.failed_runs == 0 or rec.flake_rate_pct == 0.0:
                sys.stderr.write(
                    f"\nℹ️ Notice: Test target '{explicit_test}' has 0 failing runs (0.0% failure rate across {rec.total_runs} runs) on target '{target}'.\n"
                    f"   Skipping AI root-cause analysis as there are no failure instances to analyze.\n"
                )
                return
            flaky_records.extend(matched)
        else:
            sys.stderr.write(
                f"\n❌ Error: No execution or flakiness records found in Android Test Hub (ATH) for test target '{explicit_test}' on target '{target}'.\n"
                f"   Analysis aborted. No synthetic or fake test data will be generated.\n"
            )
            raise RuntimeError(
                f"No execution or flakiness records found in ATH for test target '{explicit_test}' on target '{target}'."
            )

    else:
        raw_min_flake_rate = getattr(args, "min_flake_rate", None)
        ath_min_flake_rate = (
            10.0 if raw_min_flake_rate is None else raw_min_flake_rate
        )
        targets_to_query = (
            SUPPORTED_TARGET_PLATFORMS
            if target == "all"
            else [target]
        )
        for tgt in targets_to_query:
            recs = query_ath_flaky_tests(
                target=tgt,
                config_name=getattr(args, "config", "all"),
                branch=getattr(args, "branch", "git_emu-main-next"),
                min_flake_rate=ath_min_flake_rate,
                days=days,
                mode=mode,
                token=auth_token,
                progress_callback=cb,
            )
            flaky_records.extend(recs)

    bug_client = BuganizerClient()
    total_triaged = len(flaky_records)
    if cb:
        cb(
            0,
            total_triaged,
            f"Searching Buganizer open bugs for component {component_id}...",
        )

    bug_client.fetch_all_component_open_bugs(component_id)

    triage_results = []
    headers = [
        "#",
        "Test Target",
        "Target Platform",
        "Flake Rate",
        "Triage Status",
        "Bug ID / Report File",
    ]
    rows = []

    from lib.workspace import get_flakiness_sandbox_dir

    # Configure local report artifact output directory
    out_dir_str = getattr(args, "out_dir", None)
    out_dir = (
        Path(out_dir_str).resolve()
        if out_dir_str
        else get_flakiness_sandbox_dir("triage")
    )
    out_dir.mkdir(parents=True, exist_ok=True)


    single_report_preview: Optional[str] = None

    for idx, rec in enumerate(flaky_records, 1):
        if cb:
            cb(idx, total_triaged, f"Triaged {rec.test_identifier}")

        existing_bug_id = bug_client.find_existing_open_bug(
            component_id, rec.test_identifier
        )
        safe_name = (
            rec.test_identifier.replace("/", "_")
            .replace(":", "_")
            .replace("@", "")
        )
        report_file = out_dir / f"{safe_name}.md"

        if existing_bug_id:
            status = "EXISTING_BUG_OPEN"
            num_id = existing_bug_id.replace("b/", "").strip()
            bug_link = f"[b/{num_id}](http://b/{num_id})"
            ai_patch = None
        else:
            status = "NEW_FLAKY_TEST"
            ai_model = getattr(args, "ai_model", None) or getattr(args, "model", "Gemini Next")
            ai_patch = generate_ai_patch_for_test(rec, out_dir=str(out_dir / safe_name), model=ai_model)
            body = format_bug_body_with_ai_patch(rec, ai_patch)
            title = f"[Flaky Test] {rec.test_identifier} failing on {rec.target} ({rec.flake_rate_pct:.1f}% flake rate)"

            payload = BuganizerIssuePayload(
                title=title,
                description=body,
                component_id=component_id,
                priority="P2",
                severity="S2",
            )

            # Export local markdown proposal report
            report_file.write_text(body, encoding="utf-8")
            if len(flaky_records) == 1:
                single_report_preview = body

            if getattr(args, "auto_file", False) or getattr(args, "dry_run", False):
                bug_id = bug_client.create_bug(
                    payload, dry_run=getattr(args, "dry_run", False)
                )
                if bug_id:
                    m = re.search(r"(\d+)", bug_id)
                    num_id = m.group(1) if m else bug_id.replace("b/", "")
                    bug_link = f"[b/{num_id}](http://b/{num_id})"
                else:
                    bug_link = f"[Report: {report_file.name}](file://{report_file})"
            else:
                bug_link = f"[{report_file.name}](file://{report_file})"

        triage_results.append(
            {
                "test_identifier": rec.test_identifier,
                "target": rec.target,
                "flake_rate_pct": rec.flake_rate_pct,
                "status": status,
                "bug_info": bug_link,
                "report_file": str(report_file),
                "ai_patch": ai_patch.__dict__ if ai_patch else None,
            }
        )

        rows.append(
            [
                idx,
                f"`{rec.test_identifier}`",
                f"`{rec.target}`",
                f"**{rec.flake_rate_pct:.1f}%**",
                f"`{status}`",
                bug_link,
            ]
        )

    table = (
        format_markdown_table(headers, rows)
        if rows
        else "\n*No flaky tests required triage.*"
    )
    data = {
        "summary": f"Completed triage for {len(flaky_records)} test targets under component {component_id}",
        "component_id": component_id,
        "triaged_count": len(flaky_records),
        "report_directory": f"file://{out_dir}",
        "markdown_table": table,
        "results": triage_results,
    }

    if single_report_preview and not getattr(args, "auto_file", False):
        data["proposed_bug_markdown"] = single_report_preview

    print_result(data, json_mode=json_mode)
