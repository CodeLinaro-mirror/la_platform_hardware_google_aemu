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

"""Emulator Performance & Profiling Command Suite ('emu-dev-cli perf')."""

import argparse
import json
import logging
import os
from typing import Any, Dict, List, Optional

from core.command import (
    Command,
    CommandGroup,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
)
from core.context import ExecutionContext, detect_host_platform
from lib.output import format_markdown_table, print_result
from lib.perf_insights import (
    DEFAULT_RUN_CACHE_DIR,
    append_perf_history,
    format_perf_history_markdown,
    load_perf_history,
)
from lib.perf import (
    BUILTIN_PROFILERS,
    BUILTIN_SUITES,
    BenchmarkPlan,
    EmulatorLaunchConfig,
    ManagedBenchmarkConfig,
    compare_perf_reports,
    detect_active_adb_serial,
    load_perf_report_any_format,
    render_comparison_commit_text,
    render_comparison_markdown,
    render_single_report_markdown,
    render_weekly_qualification_report,
    resolve_perf_target,
    resolve_profiles,
    resolve_suites,
    run_managed_emulator_benchmark,
    run_suite_against_device,
)

logger = logging.getLogger(__name__)


def _detect_host() -> str:
    return detect_host_platform()


def run_perf_list(args: Any) -> CommandResult[dict[str, Any]]:
    """Handles `emu-dev-cli perf list`."""
    json_mode = getattr(args, "json", False)
    if json_mode:
        return print_result(
            {
                "status": "success",
                "action": "perf list",
                "suites": BUILTIN_SUITES,
                "profilers": BUILTIN_PROFILERS,
            },
            json_mode=True,
        )

    suite_headers = ["Suite (`--suite`)", "Included Workloads", "Description"]
    suite_rows = [
        [
            f"`{name}`",
            ", ".join(f"`{w}`" for w in info["workloads"]),
            info["description"],
        ]
        for name, info in BUILTIN_SUITES.items()
    ]

    prof_headers = ["Profiler (`--profile`)", "Description"]
    prof_rows = [
        [f"`{name}`", desc] for name, desc in BUILTIN_PROFILERS.items()
    ]

    md = (
        "## Built-in Emulator Performance Suites\n\n"
        + format_markdown_table(suite_headers, suite_rows)
        + "\n\n## Built-in Host & Guest Profilers\n\n"
        + format_markdown_table(prof_headers, prof_rows)
    )
    print(md)
    return CommandResult.success(
        {"suites": BUILTIN_SUITES, "profilers": BUILTIN_PROFILERS},
        action="perf list",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_perf_run(args: Any) -> CommandResult[dict[str, Any]]:
    """Handles `emu-dev-cli perf run`."""
    json_mode = getattr(args, "json", False)
    try:
        suites, workloads = resolve_suites(getattr(args, "suite", "adb"))
        if getattr(args, "test", None):
            pattern = args.test.strip()
            workloads = [w for w in workloads if pattern in w]
            if not workloads:
                workloads = [pattern]

        profiles = resolve_profiles(getattr(args, "profile", None))
        iterations = int(getattr(args, "iterations", 5) or 5)
        warmup = int(getattr(args, "warmup", 1) or 1)
        boots = int(getattr(args, "boots", 1) or 1)
        del boots
        target_spec = getattr(args, "target", None) or getattr(
            args, "emulator_dir", None
        )

        if target_spec:
            resolved = resolve_perf_target(
                target_spec, default_host=_detect_host()
            )
            if getattr(args, "label", None):
                resolved.label = args.label
            config = ManagedBenchmarkConfig.from_args(
                resolved,
                args,
                suites=suites,
                workloads=workloads,
                profiles=profiles,
            )
            report = run_managed_emulator_benchmark(config=config)
        else:
            serial = getattr(args, "serial", None) or detect_active_adb_serial()
            settle_sec = (
                float(args.settle_seconds)
                if getattr(args, "settle_seconds", None) is not None
                else 0.0
            )
            label = getattr(args, "label", None) or f"live-{serial}"
            report = run_suite_against_device(
                serial=serial,
                workloads=workloads,
                iterations=iterations,
                warmup=warmup,
                settle_seconds=settle_sec,
                profiles=profiles,
                label=label,
            )
            report["suites"] = suites
            append_perf_history(report)

        out_path = getattr(args, "output", None)
        if out_path:
            os.makedirs(
                os.path.dirname(os.path.abspath(out_path)), exist_ok=True
            )
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)

        if json_mode:
            return print_result(report, json_mode=True)
        print(render_single_report_markdown(report))
        if out_path:
            print(f"*Saved JSON report to `{out_path}`.*")
        return CommandResult.success(
            report,
            action="perf run",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    except Exception as e:
        return print_result(
            {
                "status": "error",
                "action": "perf run",
                "summary": f"Performance run failed: {e}",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )


def _execute_or_load_target_for_compare(
    spec: str,
    args: Any,
    suites: List[str],
    workloads: List[str],
    profiles: List[str],
) -> Dict[str, Any]:
    """Resolves a target for `perf compare`: loads JSON directly if it is a file,
    or runs a managed benchmark if it is a build ID / branch / emulator directory.
    """
    resolved = resolve_perf_target(spec, default_host=_detect_host())
    if resolved.kind == "json" and resolved.json_path:
        return load_perf_report_any_format(resolved.json_path)

    # Check if cached run exists when --reuse-cache is set
    suite_key = "_".join(suites)
    cache_file = os.path.join(
        DEFAULT_RUN_CACHE_DIR,
        f"{resolved.build_id or resolved.label}_{suite_key}.json".replace(
            " ", "_"
        ).replace("/", "_"),
    )
    if getattr(args, "reuse_cache", False) and os.path.isfile(cache_file):
        return load_perf_report_any_format(cache_file)

    config = ManagedBenchmarkConfig.from_args(
        resolved,
        args,
        suites=suites,
        workloads=workloads,
        profiles=profiles,
    )
    report = run_managed_emulator_benchmark(config=config)
    try:
        os.makedirs(DEFAULT_RUN_CACHE_DIR, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:
        logging.getLogger(__name__).debug("Ignored error in _execute_or_load_target_for_compare: %s", exc)
    return report


def run_perf_compare(args: Any) -> CommandResult[dict[str, Any]]:
    """Handles `emu-dev-cli perf compare <baseline> <candidate>`.

    Supports comparing two saved `.json` files, two Android Build IDs (`123` vs `321`),
    branch aliases (`latest:emu-main-dev` vs `latest:emu-main-next`), or local directories.
    """
    json_mode = getattr(args, "json", False)
    try:
        suites, workloads = resolve_suites(
            getattr(args, "suite", "adb-tmpfs,adb-disk")
        )
        profiles = resolve_profiles(getattr(args, "profile", None))

        base_report = _execute_or_load_target_for_compare(
            args.baseline, args, suites, workloads, profiles
        )
        cand_report = _execute_or_load_target_for_compare(
            args.candidate, args, suites, workloads, profiles
        )

        if getattr(args, "labels", None):
            parts = [p.strip() for p in args.labels.split(",") if p.strip()]
            if len(parts) >= 2:
                base_report["label"] = parts[0]
                cand_report["label"] = parts[1]

        comp = compare_perf_reports(base_report, cand_report)

        out_path = getattr(args, "output", None)
        if out_path:
            os.makedirs(
                os.path.dirname(os.path.abspath(out_path)), exist_ok=True
            )
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(comp, f, indent=2)

        scorecard_path = getattr(args, "scorecard", None)
        if scorecard_path:
            os.makedirs(
                os.path.dirname(os.path.abspath(scorecard_path)), exist_ok=True
            )
            with open(scorecard_path, "w", encoding="utf-8") as f:
                f.write(render_comparison_markdown(comp) + "\n")

        report_path = getattr(args, "report", None)
        if report_path:
            os.makedirs(
                os.path.dirname(os.path.abspath(report_path)), exist_ok=True
            )
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(render_weekly_qualification_report(comp) + "\n")

        fmt = getattr(args, "format", "markdown")
        if json_mode or fmt == "json":
            return print_result(comp, json_mode=True)
        if fmt == "commit":
            print(render_comparison_commit_text(comp))
        elif fmt == "report" or report_path:
            print(render_weekly_qualification_report(comp))
        else:
            print(render_comparison_markdown(comp))
        return CommandResult.success(
            comp,
            action="perf compare",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    except Exception as e:
        return print_result(
            {
                "status": "error",
                "action": "perf compare",
                "summary": f"Performance comparison failed: {e}",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )


def run_perf_history(args: Any) -> CommandResult[dict[str, Any]]:
    """Handles `emu-dev-cli perf history`."""
    json_mode = getattr(args, "json", False)
    entries = load_perf_history(
        limit=int(getattr(args, "limit", 20) or 20),
        suite_filter=getattr(args, "suite", None),
    )
    if json_mode:
        return print_result(
            {"status": "success", "action": "perf history", "entries": entries},
            json_mode=True,
        )
    print(format_perf_history_markdown(entries))
    return CommandResult.success(
        {"entries": entries},
        action="perf history",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def _add_common_benchmark_arguments(
    parser: Any, default_settle_seconds: Optional[float]
) -> None:
    parser.add_argument(
        "--avd",
        type=str,
        default=None,
        help="Template AVD name for system image & hardware specs (by default, a clean isolated AVD is provisioned per run)",
    )
    parser.add_argument(
        "--sysimg-dir",
        dest="sysimg_dir",
        type=str,
        default=None,
        help="Explicit system image directory for provisioning a clean isolated benchmark AVD",
    )
    parser.add_argument(
        "--reuse-avd",
        dest="reuse_avd",
        action="store_true",
        help="Boot the existing --avd in-place instead of provisioning a fresh clean AVD per boot",
    )
    parser.add_argument(
        "--boots",
        type=int,
        default=1,
        help="Number of cold boots to aggregate across when running a managed emulator binary (default: 1)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of measured iterations per workload per boot (default: 5)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warmup iterations before measurement (default: 1)",
    )
    parser.add_argument(
        "--settle-seconds",
        dest="settle_seconds",
        type=float,
        default=default_settle_seconds,
        help="Seconds to settle guest/host CPU after boot before measuring (default: 30s managed, 0s live)",
    )


class PerfListCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Lists all built-in emulator performance suites and host/guest profilers."""

    name = "list"
    help = "List all built-in emulator performance suites and host/guest profilers"

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_perf_list(args)


class PerfRunCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Runs performance workloads and telemetry profilers."""

    name = "run"
    help = "Run performance workloads + telemetry profilers against a running or managed emulator binary"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--suite",
            type=str,
            default="adb",
            help="Comma-separated suite(s) or workload(s) to run (default: 'adb'; see 'perf list')",
        )
        parser.add_argument(
            "--test",
            type=str,
            default=None,
            help="Optional substring filter for specific workload names (e.g. 'push_256MiB_tmpfs')",
        )
        parser.add_argument(
            "--profile",
            type=str,
            default="proc",
            help="Comma-separated profiler(s) to attach: proc, perf-stat, perf-record, perfetto, emu-perf-stat",
        )
        parser.add_argument(
            "--serial",
            type=str,
            default=None,
            help="ADB device serial when benchmarking an already-running emulator (e.g. emulator-5554)",
        )
        parser.add_argument(
            "--target",
            type=str,
            default=None,
            help="Target emulator binary: go/ab build ID (e.g. 15075377), 'stable', 'latest:emu-main-next', 'local', or directory",
        )
        parser.add_argument(
            "--emulator-dir",
            dest="emulator_dir",
            type=str,
            default=None,
            help="Alias for --target when specifying a local extracted emulator directory",
        )
        _add_common_benchmark_arguments(parser, default_settle_seconds=None)
        parser.add_argument(
            "--trace",
            type=str,
            default=None,
            help="Path to write host Perfetto trace (.pftrace) when launching a managed emulator",
        )
        parser.add_argument(
            "--label",
            type=str,
            default=None,
            help="Custom label for the benchmark report",
        )
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            default=None,
            help="Path to save full JSON benchmark report",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_perf_run(args)


class PerfCompareCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Compares two builds, directories, or saved JSON reports."""

    name = "compare"
    help = "Compare two builds (e.g. 'perf compare 123 321'), directories, or saved .json reports"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "baseline",
            type=str,
            help="Baseline target: go/ab build ID, 'stable', 'latest:emu-main-dev', directory, or .json path (supports file.json:subkey)",
        )
        parser.add_argument(
            "candidate",
            type=str,
            help="Candidate target: go/ab build ID, 'latest:emu-main-next', directory, or .json path (supports file.json:subkey)",
        )
        parser.add_argument(
            "--suite",
            type=str,
            default="adb-tmpfs,adb-disk",
            help="Suites to run when comparing live builds/directories (default: 'adb-tmpfs,adb-disk')",
        )
        parser.add_argument(
            "--profile",
            type=str,
            default="proc",
            help="Profilers to attach when comparing live builds (proc, perf-stat, perf-record, perfetto)",
        )
        _add_common_benchmark_arguments(parser, default_settle_seconds=30.0)
        parser.add_argument(
            "--reuse-cache",
            dest="reuse_cache",
            action="store_true",
            help="Reuse previously cached run results in ~/.cache/emu-dev-cli/perf_runs/ if available",
        )
        parser.add_argument(
            "--labels",
            type=str,
            default=None,
            help="Optional comma-separated display labels 'BaselineName,CandidateName'",
        )
        parser.add_argument(
            "--format",
            choices=["markdown", "report", "commit", "json"],
            default="markdown",
            help="Output format: 'markdown' (4-part scorecard), 'report' (full weekly qualification report), 'commit', or 'json'",
        )
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            default=None,
            help="Optional path to save comparison JSON artifact",
        )
        parser.add_argument(
            "--scorecard",
            type=str,
            default=None,
            help="Optional path to save the 4-part Markdown scorecard artifact",
        )
        parser.add_argument(
            "--report",
            type=str,
            default=None,
            help="Optional path to save the full Weekly Performance Qualification Report Markdown artifact",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_perf_compare(args)


class PerfHistoryCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Displays longitudinal performance history across runs and builds."""

    name = "history"
    help = "Display longitudinal performance history across runs and builds"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Maximum number of recent runs to display (default: 20)",
        )
        parser.add_argument(
            "--suite",
            type=str,
            default=None,
            help="Filter history entries by suite name (e.g. 'adb')",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_perf_history(args)


class PerfCommand(CommandGroup):
    """Parent command group for `emu-dev-cli perf`."""

    name = "perf"
    help = "Profile, benchmark, and compare emulator binaries (ADB, boot, idle, snapshot, display)"
    subcommand_dest = "perf_cmd"
    subcommands = (
        PerfListCommand(),
        PerfRunCommand(),
        PerfCompareCommand(),
        PerfHistoryCommand(),
    )


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Registers the `perf` command and its subcommands (`list`, `run`, `compare`, `history`)."""
    return PerfCommand().register(subparsers)
