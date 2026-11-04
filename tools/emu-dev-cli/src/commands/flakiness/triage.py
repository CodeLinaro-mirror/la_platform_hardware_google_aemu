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

"""Command for 'emu-dev-cli flakiness triage'."""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from clients.buganizer_service import BuganizerService, CreateBugRequest
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import EmuCliError, ExecutionError
from lib.agent_dispatcher import AgentDispatcher
from lib.ai_patch_generator import (
    AIPatchResult,
    format_bug_body_with_ai_patch,
    generate_ai_patch_for_test,
)
from lib.ath_api import (
    FlakyTestRecord,
    infer_test_identifier_for_target,
    query_ath_flaky_tests,
    sanitize_bazel_target,
)
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
from lib.output import format_markdown_table, print_status
from lib.workspace import get_flakiness_sandbox_dir
from . import options
from .common import (
    AthFlakyQuery,
    ProgressCallback,
    ath_platforms,
    is_json_mode,
    query_ath_platforms,
    require_positive_int,
    sql_preview_result,
)
from .list_cmd import validate_query_window
from .progress import make_progress_callback

logger = logging.getLogger(__name__)

PatchGenerator = Callable[..., AIPatchResult]
AgentCheck = Callable[[], None]
SandboxDirFactory = Callable[[str], Path]

ACTION = "flakiness triage"
DEFAULT_MIN_FLAKE_RATE = 10.0
ADH_ASSIGNEE = "groupagent-android-emulator@google.com"
BUG_PRIORITY = "P2"
BUG_SEVERITY = "S2"
STATUS_EXISTING = "EXISTING_BUG_OPEN"
STATUS_FIXED = "ALREADY_FIXED_BUG"
STATUS_NEW = "NEW_FLAKY_TEST"
TRIAGE_SANDBOX = "triage"
NO_TRIAGE_TABLE = "\n*No flaky tests required triage.*"
TRIAGE_HEADERS = [
    "#",
    "Test Target",
    "Target Platform",
    "Flake Rate",
    "Triage Status",
    "Bug ID / Report File",
]
AGENT_REMEDIATION = (
    "Run inside a Jetski / Antigravity agent session with 'agentapi' on PATH, or "
    "install the standalone jetski CLI (/google/bin/releases/jetski-devs/tools/cli)."
)
BUGANIZER_REMEDIATION = (
    "Check that the issues CLI (/google/bin/releases/issues-cli/issues) works for "
    "you, or rerun without --auto-file to only write local reports."
)

TRIAGE_DESCRIPTION = """
Automated Flakiness Triage and AI Remediation Generator (`emu-dev-cli flakiness triage`)

Performs end-to-end triage of flaky emulator test targets across CI pipelines:
  1. Queries Android Test Hub (ATH) for flaky tests matching failure rate threshold and target platform.
  2. Cross-checks open Buganizer issues under Component 1016880 to deduplicate existing reports.
  3. Downloads diagnostic logcats/host logs into a local investigation sandbox directory.
  4. Triggers Jetski / Antigravity AI root-cause analysis to construct proposed synchronization diffs.
  5. Generates markdown report artifacts locally or files Buganizer issues directly with `--auto-file`.

Sample Invocations:
  # Triage a specific test target in dry-run mode (outputs local markdown report & proposed diff):
  emu-dev-cli flakiness triage --test @goldfish//emulator/libs/process:process_unittests --dry-run

  # Triage all flaky tests on TSAN with failure rate >= 10% across 7 days:
  emu-dev-cli flakiness triage --target emulator_linux_x64_tsan --min-flake-rate 10.0 --days 7

  # Automatically file Buganizer issues for all new unhandled flaky tests:
  emu-dev-cli flakiness triage --target emulator_linux_x64 --min-flake-rate 5.0 --auto-file

  # Specify explicit OAuth2 token (for macOS workstations):
  emu-dev-cli flakiness triage --test @goldfish//emulator/libs/async:loop_handoff_test --token "$TOKEN"
"""


def verify_agent_environment() -> None:
    """Fails fast when neither an agent session nor a standalone jetski CLI is available."""
    AgentDispatcher().verify_environment()


@dataclass(frozen=True)
class _TriageScope:
    """What to triage, resolved from the CLI arguments."""

    target: str
    explicit_test: str | None
    directory: str | None
    branch: str | None
    studio_mode: bool
    days: int
    mode: str
    component_id: str
    assignee: str | None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "_TriageScope":
        target = str(getattr(args, "target", options.DEFAULT_TARGET))
        explicit_test = str(getattr(args, "test", None) or "").strip() or None
        directory = getattr(args, "directory", None)
        branch = getattr(args, "branch", None)
        studio_mode = is_studio_query(
            target=target, branch=branch, directory=directory, test_target=explicit_test
        )
        raw_days = getattr(args, "days", None)
        default_days = DEFAULT_STUDIO_LOOKBACK_DAYS if studio_mode else options.ATH_LOOKBACK_DAYS
        adh = studio_mode and is_android_device_hub_target(
            directory or explicit_test or DEFAULT_STUDIO_DIR_PREFIX
        )
        raw_component = getattr(args, "component_id", None)
        default_component = DEFAULT_STUDIO_COMPONENT_ID if adh else options.DEFAULT_COMPONENT_ID
        raw_assignee = getattr(args, "assignee", None)
        if raw_assignee is not None:
            assignee = str(raw_assignee).strip() or None
        else:
            assignee = ADH_ASSIGNEE if adh else None
        return cls(
            target=target,
            explicit_test=explicit_test,
            directory=directory,
            branch=branch,
            studio_mode=studio_mode,
            days=int(raw_days) if raw_days is not None else default_days,
            mode=str(getattr(args, "mode", options.MODE_POSTSUBMIT)),
            component_id=str(raw_component) if raw_component else default_component,
            assignee=assignee,
        )


@dataclass(frozen=True)
class _TriageRun:
    """Settings shared by every record of one triage run."""

    buganizer: BuganizerService
    component: int
    assignee: str | None
    out_dir: Path
    ai_model: str
    auto_file: bool
    dry_run: bool
    dedup_enabled: bool
    json_mode: bool


def _safe_file_stem(test_identifier: str) -> str:
    return test_identifier.replace("/", "_").replace(":", "_").replace("@", "")


def _bug_link(issue_id: str) -> str:
    return f"[b/{issue_id}](http://b/{issue_id})"


def _file_link(path: Path, label: str = "") -> str:
    return f"[{label}{path.name}](file://{path})"


def _bug_title(rec: FlakyTestRecord) -> str:
    return (
        f"[Flaky Test] {rec.test_identifier} failing on {rec.target} "
        f"({rec.flake_rate_pct:.1f}% flake rate)"
    )


def _match_explicit_test(
    records: list[FlakyTestRecord], scope: _TriageScope
) -> list[FlakyTestRecord]:
    """Keeps the records of `--test`, or fails when the data source has none."""
    wanted = sanitize_bazel_target(scope.explicit_test)
    matched = [r for r in records if sanitize_bazel_target(r.test_identifier) == wanted]
    if matched:
        return matched
    source = "F1 TestActions" if scope.studio_mode else "ATH"
    raise ExecutionError(
        f"No execution or flakiness records found in {source} for test target "
        f"'{scope.explicit_test}' on target '{scope.target}'.",
        remediation=(
            "Check the Bazel label and --target, or widen the window with --days "
            f"(searched the last {scope.days} days)."
        ),
    )


def _no_failures_result(
    scope: _TriageScope, record: FlakyTestRecord
) -> CommandResult[dict[str, Any]]:
    summary = (
        f"Skipped AI root-cause analysis for '{scope.explicit_test}': 0 failing runs "
        f"(0.0% failure rate across {record.total_runs} runs) on target '{record.target}'."
    )
    data: dict[str, Any] = {
        "summary": summary,
        "component_id": scope.component_id,
        "triaged_count": 0,
        "results": [],
    }
    return CommandResult(data=data, action=ACTION, summary=summary)


class FlakinessTriageCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Triages flaky tests: dedups against Buganizer, runs AI RCA, and files bugs."""

    name = "triage"
    help_text = "Query ATH, deduplicate open Buganizer issues, generate Jetski AI RCA patches, and file bugs"
    description = TRIAGE_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(
        self,
        *,
        ath_query: AthFlakyQuery | None = None,
        patch_generator: PatchGenerator | None = None,
        agent_check: AgentCheck | None = None,
        sandbox_dir_factory: SandboxDirFactory | None = None,
    ) -> None:
        self._ath_query = ath_query
        self._patch_generator = patch_generator
        self._agent_check = agent_check
        self._sandbox_dir_factory = sandbox_dir_factory

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        options.add_target_option(parser)
        options.add_test_identifier_option(parser)
        options.add_branch_option(parser)
        parser.add_argument(
            "--component-id",
            "--component_id",
            dest="component_id",
            type=str,
            default=None,
            help="Buganizer Component ID (default: 1016880 for emulator, 2289192 for Android Device Hub)",
        )
        parser.add_argument(
            "--assignee",
            type=str,
            default=None,
            help=(
                f"Buganizer issue assignee (default: {ADH_ASSIGNEE} for Android "
                "Device Hub, unassigned otherwise)"
            ),
        )
        options.add_min_flake_rate_option(
            parser,
            help_text=(
                "Minimum failure/flake rate percentage threshold for bug filing "
                "(default: any flake >0%% for Android Device Hub, 10.0 for other targets)"
            ),
        )
        options.add_days_option(parser)
        options.add_mode_option(parser)
        options.add_studio_filter_options(parser)
        options.add_f1_options(parser)
        parser.add_argument(
            "--ai-model",
            "--model",
            dest="ai_model",
            type=str,
            default=options.DEFAULT_AI_MODEL,
            help=f"AI model selection for root-cause analysis (default: '{options.DEFAULT_AI_MODEL}')",
        )
        parser.add_argument(
            "--auto-file",
            "--auto_file",
            dest="auto_file",
            action="store_true",
            help="Automatically file Buganizer issues for unhandled flaky tests",
        )
        parser.add_argument(
            "--test",
            type=str,
            default=None,
            help="Optional specific Bazel test target to triage (e.g. @goldfish//emulator/libs/async:loop_handoff_test)",
        )
        parser.add_argument(
            "--out-dir",
            "--out_dir",
            dest="out_dir",
            type=str,
            default=None,
            help=(
                "Directory to save generated markdown bug reports for local inspection "
                "(defaults to user temporary directory /tmp/flakiness_<user>/triage/)"
            ),
        )
        options.add_token_option(parser)
        parser.add_argument(
            "--dry-run",
            "--dry_run",
            dest="dry_run",
            action="store_true",
            help="Output proposed bug payloads without modifying Buganizer",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        validate_query_window(args)
        require_positive_int(
            getattr(args, "component_id", None),
            flag="--component-id",
            example=options.DEFAULT_COMPONENT_ID,
        )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        scope = _TriageScope.from_args(args)
        json_mode = is_json_mode(args, ctx)
        auto_file = bool(getattr(args, "auto_file", False))
        dry_run = bool(getattr(args, "dry_run", False))
        logger.info(
            "Executing flakiness triage (target=%s, test=%s, component_id=%s, "
            "studio_mode=%s, auto_file=%s, dry_run=%s)",
            scope.target, scope.explicit_test, scope.component_id,
            scope.studio_mode, auto_file, dry_run,
        )
        if scope.studio_mode and getattr(args, "print_sql", False):
            sql = build_studio_flakes_sql(
                targets=resolve_studio_targets(scope.target),
                branches=resolve_studio_branches(scope.branch),
                directory_prefix=normalize_target_prefix(scope.directory),
                days=scope.days,
                mode=scope.mode,
                test_target=scope.explicit_test,
            )
            return sql_preview_result(
                sql,
                action=ACTION,
                summary="Generated Studio flakes F1 SQL query",
                json_mode=json_mode,
            )

        self._verify_agent_environment()
        progress = make_progress_callback(json_mode)
        if scope.studio_mode:
            records = self._query_studio(args, ctx, scope, progress, json_mode)
        else:
            records = self._query_ath(args, scope, progress, json_mode)
        if scope.explicit_test:
            records = _match_explicit_test(records, scope)
            if records[0].failed_runs == 0 or records[0].flake_rate_pct == 0.0:
                return _no_failures_result(scope, records[0])

        out_dir_arg = getattr(args, "out_dir", None)
        factory_fn = self._sandbox_dir_factory or get_flakiness_sandbox_dir
        out_dir = (
            Path(out_dir_arg).expanduser().resolve()
            if out_dir_arg
            else factory_fn(TRIAGE_SANDBOX)
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        buganizer = ctx.buganizer_service
        if progress:
            progress(
                0,
                len(records),
                f"Searching Buganizer open bugs for component {scope.component_id}...",
            )
        run = _TriageRun(
            buganizer=buganizer,
            component=int(scope.component_id),
            assignee=scope.assignee,
            out_dir=out_dir,
            ai_model=str(getattr(args, "ai_model", None) or options.DEFAULT_AI_MODEL),
            auto_file=auto_file,
            dry_run=dry_run,
            dedup_enabled=self._dedup_available(
                buganizer, scope.component_id, auto_file and not dry_run, json_mode
            ),
            json_mode=json_mode,
        )

        results: list[dict[str, Any]] = []
        rows: list[list[Any]] = []
        proposals: list[str] = []
        for idx, rec in enumerate(records, 1):
            if progress:
                progress(idx, len(records), f"Triaged {rec.test_identifier}")
            result, body = self._triage_record(rec, run)
            results.append(result)
            if body:
                proposals.append(body)
            rows.append([
                idx,
                f"`{rec.test_identifier}`",
                f"`{rec.target}`",
                f"**{rec.flake_rate_pct:.1f}%**",
                f"`{result['status']}`",
                result["bug_info"],
            ])

        table = format_markdown_table(TRIAGE_HEADERS, rows) if rows else NO_TRIAGE_TABLE
        summary = (
            f"Completed triage for {len(records)} test targets under component "
            f"{scope.component_id}"
        )
        data: dict[str, Any] = {
            "summary": summary,
            "component_id": scope.component_id,
            "triaged_count": len(records),
            "report_directory": f"file://{out_dir}",
            "markdown_table": table,
            "results": results,
        }
        if len(records) == 1 and proposals and not auto_file:
            data["proposed_bug_markdown"] = proposals[0]
        return CommandResult(data=data, action=ACTION, summary=summary, markdown_table=table)

    def _verify_agent_environment(self) -> None:
        try:
            check_fn = self._agent_check or verify_agent_environment
            check_fn()
        except RuntimeError as exc:
            raise ExecutionError(
                f"No AI agent runtime is available for root-cause analysis: {exc}",
                remediation=AGENT_REMEDIATION,
                cause=exc,
            ) from exc

    def _query_studio(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        scope: _TriageScope,
        progress: ProgressCallback,
        json_mode: bool,
    ) -> list[FlakyTestRecord]:
        hint = scope.directory or scope.explicit_test
        min_flake_rate, min_failed_runs = resolve_studio_min_flake_threshold(
            getattr(args, "min_flake_rate", None),
            default_non_adh_rate=DEFAULT_MIN_FLAKE_RATE,
            directory_or_target=hint,
        )
        window = resolve_studio_window(getattr(args, "window", None), directory_or_target=hint)
        print_status(
            "🔍 Querying F1 TestActions for Studio flaky tests "
            f"(target={scope.target}, window={window})...",
            json_mode,
        )
        return query_studio_flaky_tests(
            target=scope.target,
            directory=scope.directory,
            branch=str(scope.branch or DEFAULT_STUDIO_BRANCH),
            min_flake_rate=0.0 if scope.explicit_test else min_flake_rate,
            min_failed_runs=0 if scope.explicit_test else max(1, min_failed_runs),
            days=scope.days,
            mode=scope.mode,
            window=window,
            test_target=scope.explicit_test,
            accounting_group=getattr(args, "accounting_group", None),
            progress_callback=progress,
            f1_client=ctx.f1_sql_client,
        )

    def _query_ath(
        self,
        args: argparse.Namespace,
        scope: _TriageScope,
        progress: ProgressCallback,
        json_mode: bool,
    ) -> list[FlakyTestRecord]:
        raw_config = getattr(args, "config", None)
        if scope.explicit_test:
            print_status(
                "🔍 Querying Android Test Hub (ATH) for records matching "
                f"'{scope.explicit_test}'...",
                json_mode,
            )
            # One aggregated query, so `--target all` yields a single record per test.
            platforms = [scope.target]
            config_name = (
                raw_config
                if raw_config and raw_config != options.ALL_TEST_IDENTIFIERS
                else infer_test_identifier_for_target(scope.explicit_test)
            )
            min_flake_rate = 0.0
        else:
            platforms = ath_platforms(scope.target)
            config_name = str(raw_config or options.ALL_TEST_IDENTIFIERS)
            raw_min = getattr(args, "min_flake_rate", None)
            min_flake_rate = DEFAULT_MIN_FLAKE_RATE if raw_min is None else float(raw_min)
        ath_query = self._ath_query or query_ath_flaky_tests
        return query_ath_platforms(
            ath_query,
            platforms,
            config_name=config_name,
            branch=str(scope.branch or options.DEFAULT_BRANCH),
            min_flake_rate=min_flake_rate,
            days=scope.days,
            mode=scope.mode,
            token=getattr(args, "token", None),
            progress_callback=progress,
        )

    def _dedup_available(
        self,
        buganizer: BuganizerService,
        component_id: str,
        filing: bool,
        json_mode: bool,
    ) -> bool:
        """Checks Buganizer up front; only report-only runs may continue without it."""
        try:
            buganizer.list_component_open_issues(int(component_id))
        except EmuCliError as exc:
            if filing:
                raise ExecutionError(
                    "Refusing to file bugs: searching open bugs in Buganizer component "
                    f"{component_id} failed, so duplicates cannot be detected ({exc.message}).",
                    remediation=BUGANIZER_REMEDIATION,
                    cause=exc,
                ) from exc
            print_status(
                f"⚠️ Could not search Buganizer component {component_id} for existing "
                f"bugs ({exc.message}); writing reports without deduplication.",
                json_mode,
            )
            return False
        return True

    def _triage_record(
        self, rec: FlakyTestRecord, run: _TriageRun
    ) -> tuple[dict[str, Any], str | None]:
        """Returns the result row for `rec` and the proposed bug body, if any."""
        existing = fixed = None
        if run.dedup_enabled:
            existing = run.buganizer.find_open_issue_for_target(
                run.component, rec.test_identifier
            )
            if existing is None and rec.l100_failed == 0:
                fixed = run.buganizer.find_recently_fixed_issue_for_target(
                    run.component, rec.test_identifier
                )
        safe_name = _safe_file_stem(rec.test_identifier)
        report_file = run.out_dir / f"{safe_name}.md"
        result: dict[str, Any] = {
            "test_identifier": rec.test_identifier,
            "target": rec.target,
            "flake_rate_pct": rec.flake_rate_pct,
            "status": STATUS_NEW,
            "assignee": run.assignee,
            "bug_info": "",
            "report_file": str(report_file),
            "ai_patch": None,
        }
        if existing is not None:
            result.update(status=STATUS_EXISTING, bug_info=_bug_link(existing.issue_id))
            return result, None
        if fixed is not None:
            result.update(status=STATUS_FIXED, bug_info=_bug_link(fixed.issue_id))
            return result, None

        patch = self._generate_patch(rec, run.out_dir / safe_name, run.ai_model)
        body = format_bug_body_with_ai_patch(rec, patch)
        report_file.write_text(body, encoding="utf-8")
        result["ai_patch"] = patch.__dict__
        if not (run.auto_file or run.dry_run):
            result["bug_info"] = _file_link(report_file)
            return result, body
        try:
            created = run.buganizer.create_issue(
                CreateBugRequest(
                    title=_bug_title(rec),
                    description=body,
                    component_id=run.component,
                    priority=BUG_PRIORITY,
                    severity=BUG_SEVERITY,
                    assignee=run.assignee,
                ),
                dry_run=run.dry_run,
            )
        except EmuCliError as exc:
            print_status(
                f"⚠️ Filing a bug for '{rec.test_identifier}' failed ({exc.message}); "
                f"the proposal is saved in {report_file}.",
                run.json_mode,
            )
            result.update(bug_info=_file_link(report_file, "Report: "), filing_error=exc.message)
            return result, body
        result["bug_info"] = _bug_link(created.issue_id)
        return result, body

    def _generate_patch(
        self, rec: FlakyTestRecord, sandbox: Path, model: str
    ) -> AIPatchResult:
        patch_fn = self._patch_generator or generate_ai_patch_for_test
        try:
            return patch_fn(rec, out_dir=str(sandbox), model=model)
        except RuntimeError as exc:
            raise ExecutionError(
                f"AI root-cause analysis failed for '{rec.test_identifier}' on "
                f"'{rec.target}': {exc}",
                remediation=(
                    f"Check the investigation files in {sandbox}, then rerun with "
                    f"--test {rec.test_identifier} to retry this target."
                ),
                cause=exc,
            ) from exc
