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

"""Emulator Stability & Endurance Command Suite ('emu-dev-cli stability')."""

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from core.command import (
    Command,
    CommandGroup,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
)
from core.context import ExecutionContext, detect_host_platform
from core.errors import ExecutionError, ExitCode, UsageError
from lib.output import format_markdown_table, print_result
from lib.perf import detect_active_adb_serial, resolve_perf_target
from lib.stability import (
    DEFAULT_GPU_MODE,
    DEFAULT_SCENARIO_SPEC,
    DEFAULT_TIER,
    SCENARIO_CATALOG,
    TIER_CATALOG,
    StabilityRunConfig,
    StabilityThresholds,
    StabilityVerdict,
    compare_stability_reports,
    expand_safe_path,
    format_clickable_file_link,
    format_stability_history_markdown,
    load_stability_history,
    load_stability_report_json,
    render_stability_comparison_markdown,
    render_stability_report_markdown,
    resolve_stability_scenarios,
    resolve_tier_and_duration,
    run_managed_stability_session,
    run_stability_session_against_device,
    supports_osc8_hyperlinks,
)


def _add_shared_stability_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tier",
        type=str,
        default=DEFAULT_TIER,
        choices=sorted(TIER_CATALOG.keys()),
        help="Stability duration tier: 'smoke' (~5m), 'standard' (~30m), or 'soak' (4h)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional explicit run duration override in seconds (e.g. 120)",
    )
    parser.add_argument(
        "--max-cycles",
        dest="max_cycles",
        type=int,
        default=None,
        help="Optional cap on maximum scenario cycles to execute",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=DEFAULT_SCENARIO_SPEC,
        help="Comma-separated scenario(s): dev-loop, snapshot-stress, ui-control, or all (default: all)",
    )
    parser.add_argument(
        "--avd",
        type=str,
        default=None,
        help="Template AVD name for isolated clean provisioning (default: auto-discover)",
    )
    parser.add_argument(
        "--sysimg-dir",
        dest="sysimg_dir",
        type=str,
        default=None,
        help="Explicit system image directory for provisioning clean AVDs",
    )
    parser.add_argument(
        "--no-clean-avd",
        dest="clean_avd",
        action="store_false",
        default=True,
        help="Reuse existing AVD directly instead of creating a clean isolated clone",
    )
    parser.add_argument(
        "--gpu",
        type=str,
        default=DEFAULT_GPU_MODE,
        help="Emulator -gpu mode (default: 'auto' matching Android Studio; or 'host', 'swiftshader_indirect', 'lavapipe')",
    )
    parser.add_argument(
        "--window",
        dest="show_window",
        action="store_true",
        default=False,
        help="Launch with a visible desktop GUI window instead of embedded-style '-no-window'",
    )
    parser.add_argument(
        "--no-audio",
        dest="enable_audio",
        action="store_false",
        default=True,
        help="Disable emulator audio backend ('-no-audio')",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Treat DEGRADED resource/latency warnings as hard failures (exit code 1)",
    )
    parser.add_argument(
        "--forensics-dir",
        dest="forensics_dir",
        type=str,
        default=None,
        help="Custom root directory for forensic bundles captured on FAIL or DEGRADED",
    )
    parser.add_argument(
        "--always-bundle",
        dest="always_bundle",
        action="store_true",
        default=False,
        help="Capture a forensic bundle even when the stability verdict is PASS",
    )


def _build_run_config_from_args(args: argparse.Namespace) -> StabilityRunConfig:
    tier, duration_sec, min_cycles, idle_settle_sec = resolve_tier_and_duration(
        getattr(args, "tier", DEFAULT_TIER),
        getattr(args, "duration", None),
    )
    max_cycles = getattr(args, "max_cycles", None)
    if max_cycles is not None and max_cycles < 1:
        raise UsageError(
            f"Invalid `--max-cycles` value {max_cycles}; must be >= 1.",
            remediation="Pass a positive integer to `--max-cycles`, e.g. `--max-cycles 3`.",
        )
    scenarios = resolve_stability_scenarios(
        getattr(args, "scenario", DEFAULT_SCENARIO_SPEC)
    )
    return StabilityRunConfig(
        tier=tier,
        duration_sec=duration_sec,
        min_cycles=min_cycles,
        max_cycles=max_cycles,
        idle_settle_sec=idle_settle_sec,
        scenarios=scenarios,
        gpu_mode=getattr(args, "gpu", DEFAULT_GPU_MODE) or DEFAULT_GPU_MODE,
        show_window=bool(getattr(args, "show_window", False)),
        enable_audio=bool(getattr(args, "enable_audio", True)),
        strict=bool(getattr(args, "strict", False)),
        avd_name=getattr(args, "avd", None),
        sysimg_dir=getattr(args, "sysimg_dir", None),
        clean_avd=bool(getattr(args, "clean_avd", True)),
        forensics_dir=getattr(args, "forensics_dir", None),
        always_bundle_forensics=bool(getattr(args, "always_bundle", False)),
        label=getattr(args, "label", None),
        thresholds=StabilityThresholds(),
    )


def _write_optional_file(path: Optional[str], content: str) -> None:
    if not path:
        return
    abs_p = expand_safe_path(path)
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8") as f:
        f.write(content)


class StabilityListCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Lists built-in stability tiers, scenarios, and SLA thresholds."""

    name = "list"
    help_text = "List built-in stability tiers, scenarios, and 3-state SLA thresholds"

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        thresholds = StabilityThresholds().to_dict()
        payload = {
            "status": "success",
            "action": "stability list",
            "tiers": TIER_CATALOG,
            "scenarios": SCENARIO_CATALOG,
            "thresholds": thresholds,
        }
        if getattr(args, "json", False):
            return print_result(payload, json_mode=True)

        tier_headers = ["Tier (`--tier`)", "Default Duration", "Min Cycles", "Description"]
        tier_rows = [
            [
                f"`{name}`",
                f"{int(info['duration_sec'])}s ({info['duration_sec'] / 60.0:.0f}m)",
                str(info["min_cycles"]),
                info["description"],
            ]
            for name, info in TIER_CATALOG.items()
        ]

        scen_headers = ["Scenario (`--scenario`)", "Included Flows", "Description"]
        scen_rows = [
            [
                f"`{name}`",
                ", ".join(f"`{s}`" for s in info["leaf_scenarios"]),
                info["description"],
            ]
            for name, info in SCENARIO_CATALOG.items()
        ]

        md = (
            "## Built-in Stability Duration Tiers\n\n"
            + format_markdown_table(tier_headers, tier_rows)
            + "\n\n## Built-in User-Experience Stability Scenarios\n\n"
            + format_markdown_table(scen_headers, scen_rows)
        )
        print(md)
        return CommandResult.success(
            payload,
            action="stability list",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class StabilityRunCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Runs stability scenarios and SLA health checks against a managed or live emulator."""

    name = "run"
    help_text = "Run user-experience stability soak & torture checks against a managed or live emulator"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--target",
            type=str,
            default=None,
            help="Target emulator binary: 'latest:emu-main-next', 'stable', 'tree', build ID, or directory",
        )
        parser.add_argument(
            "--emulator-dir",
            dest="emulator_dir",
            type=str,
            default=None,
            help="Alias for --target when specifying a local extracted emulator directory",
        )
        parser.add_argument(
            "--serial",
            type=str,
            default=None,
            help="ADB device serial when testing an already-running emulator (e.g. emulator-5554)",
        )
        _add_shared_stability_args(parser)
        parser.add_argument(
            "--label",
            type=str,
            default=None,
            help="Custom display label for the stability report",
        )
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            default=None,
            help="Path to save full JSON stability report",
        )
        parser.add_argument(
            "--report",
            type=str,
            default=None,
            help="Path to save Markdown stability qualification scorecard",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        json_mode = bool(getattr(args, "json", False))
        runner = ctx.process_runner if ctx is not None else None
        config = _build_run_config_from_args(args)
        target_spec = getattr(args, "target", None) or getattr(
            args, "emulator_dir", None
        )

        try:
            if target_spec:
                resolved = resolve_perf_target(
                    target_spec,
                    default_host=detect_host_platform(),
                )
                if config.label:
                    resolved.label = config.label
                report = run_managed_stability_session(
                    target=resolved,
                    config=config,
                    process_runner=runner,
                )
            else:
                serial = getattr(args, "serial", None) or detect_active_adb_serial(
                    process_runner=runner
                )
                config.clean_avd = False
                report = run_stability_session_against_device(
                    serial=serial,
                    config=config,
                    process_runner=runner,
                )
        except UsageError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"Stability run failed: {exc}",
                remediation=(
                    "Verify the target emulator binary, system image/AVD configuration, and ADB connectivity."
                ),
                cause=exc,
            ) from exc

        _write_optional_file(
            getattr(args, "output", None),
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        )
        md_report = render_stability_report_markdown(report, osc8=False)
        _write_optional_file(getattr(args, "report", None), md_report)

        eval_block = report.get("evaluation") or {}
        verdict = eval_block.get("verdict", StabilityVerdict.FAIL.value)
        exit_code = int(eval_block.get("effective_exit_code", ExitCode.SUCCESS))
        summary = (
            f"Stability run '{report.get('label', '')}' finished with verdict {verdict} "
            f"({eval_block.get('cycles_completed', 0)} cycles, {float(eval_block.get('elapsed_sec', 0.0)):.1f}s)."
        )

        if json_mode:
            report_payload = dict(report)
            report_payload["status"] = "error" if exit_code != 0 else "success"
            report_payload["action"] = "stability run"
            report_payload["summary"] = summary
            report_payload["exit_code"] = exit_code
            return print_result(
                report_payload,
                json_mode=True,
                is_error=(exit_code != 0),
            )

        use_osc8 = supports_osc8_hyperlinks()
        print(
            render_stability_report_markdown(report, osc8=use_osc8)
            if use_osc8
            else md_report
        )
        if getattr(args, "output", None):
            print(
                f"Saved JSON stability report: {format_clickable_file_link(args.output, osc8=use_osc8)}"
            )
        if getattr(args, "report", None):
            print(
                f"Saved Markdown scorecard: {format_clickable_file_link(args.report, osc8=use_osc8)}"
            )

        if exit_code != 0:
            return CommandResult.failure(
                summary,
                exit_code=exit_code,
                data=report,
                action="stability run",
                metadata={META_KEY_ALREADY_RENDERED: True},
            )
        return CommandResult.success(
            report,
            summary=summary,
            action="stability run",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


def _load_or_run_stability_target(
    spec: str,
    config: StabilityRunConfig,
    runner: Any,
) -> Dict[str, Any]:
    if os.path.isfile(spec) and spec.lower().endswith(".json"):
        return load_stability_report_json(spec)
    resolved = resolve_perf_target(spec, default_host=detect_host_platform())
    return run_managed_stability_session(
        target=resolved,
        config=config,
        process_runner=runner,
    )


class StabilityCompareCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Runs or loads an A/B stability comparison between two builds or JSON reports."""

    name = "compare"
    help_text = "Compare stability, leak slopes, and UX drift across two builds or saved .json reports"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "baseline",
            type=str,
            help="Baseline target: 'stable', build ID, directory, or saved .json report",
        )
        parser.add_argument(
            "candidate",
            type=str,
            help="Candidate target: 'latest:emu-main-next', 'tree', build ID, directory, or saved .json report",
        )
        _add_shared_stability_args(parser)
        parser.add_argument(
            "--labels",
            type=str,
            default=None,
            help="Optional comma-separated display labels 'BaselineName,CandidateName'",
        )
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            default=None,
            help="Path to save A/B stability comparison JSON",
        )
        parser.add_argument(
            "--report",
            type=str,
            default=None,
            help="Path to save A/B stability comparison Markdown scorecard",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        json_mode = bool(getattr(args, "json", False))
        runner = ctx.process_runner if ctx is not None else None
        config = _build_run_config_from_args(args)

        b_lbl: Optional[str] = None
        c_lbl: Optional[str] = None
        if getattr(args, "labels", None):
            parts = [p.strip() for p in args.labels.split(",") if p.strip()]
            if len(parts) == 2:
                b_lbl, c_lbl = parts[0], parts[1]

        try:
            base_rep = _load_or_run_stability_target(
                args.baseline, config, runner
            )
            cand_rep = _load_or_run_stability_target(
                args.candidate, config, runner
            )
        except UsageError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"Stability A/B comparison failed: {exc}",
                remediation="Verify both baseline and candidate targets or saved JSON paths.",
                cause=exc,
            ) from exc

        comp = compare_stability_reports(
            base_rep,
            cand_rep,
            baseline_label=b_lbl,
            candidate_label=c_lbl,
        )
        _write_optional_file(
            getattr(args, "output", None),
            json.dumps(comp, indent=2, ensure_ascii=False) + "\n",
        )
        md = render_stability_comparison_markdown(comp, osc8=False)
        _write_optional_file(getattr(args, "report", None), md)

        overall = comp.get("overall_verdict", StabilityVerdict.FAIL.value)
        should_fail = overall == StabilityVerdict.FAIL.value or (
            config.strict and overall == StabilityVerdict.DEGRADED.value
        )
        exit_code = int(ExitCode.FAILURE) if should_fail else int(ExitCode.SUCCESS)
        summary = f"Stability comparison finished with candidate verdict {overall}."

        if json_mode:
            out = dict(comp)
            out["status"] = "error" if exit_code != 0 else "success"
            out["action"] = "stability compare"
            out["summary"] = summary
            out["exit_code"] = exit_code
            return print_result(out, json_mode=True, is_error=(exit_code != 0))

        use_osc8 = supports_osc8_hyperlinks()
        print(
            render_stability_comparison_markdown(comp, osc8=use_osc8)
            if use_osc8
            else md
        )
        if getattr(args, "output", None):
            print(
                f"Saved A/B comparison JSON: {format_clickable_file_link(args.output, osc8=use_osc8)}"
            )
        if getattr(args, "report", None):
            print(
                f"Saved A/B Markdown scorecard: {format_clickable_file_link(args.report, osc8=use_osc8)}"
            )
        if exit_code != 0:
            return CommandResult.failure(
                summary,
                exit_code=exit_code,
                data=comp,
                action="stability compare",
                metadata={META_KEY_ALREADY_RENDERED: True},
            )
        return CommandResult.success(
            comp,
            summary=summary,
            action="stability compare",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class StabilityHistoryCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Displays longitudinal stability history across runs and builds."""

    name = "history"
    help_text = "Display longitudinal stability qualification history across runs and builds"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Maximum number of recent runs to display (default: 20)",
        )
        parser.add_argument(
            "--tier",
            type=str,
            default=None,
            help="Optional filter by tier ('smoke', 'standard', 'soak')",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        entries = load_stability_history(
            limit=getattr(args, "limit", 20),
            tier_filter=getattr(args, "tier", None),
        )
        payload = {
            "status": "success",
            "action": "stability history",
            "count": len(entries),
            "history": entries,
        }
        if getattr(args, "json", False):
            return print_result(payload, json_mode=True)

        print(
            format_stability_history_markdown(
                entries, osc8=supports_osc8_hyperlinks()
            )
        )
        return CommandResult.success(
            payload,
            action="stability history",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class StabilityVerifyCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Verifies generated stability JSON and Markdown artifacts for automated workflows."""

    name = "verify"
    help_text = "Verify stability report JSON and Markdown scorecard completeness"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--report-json",
            dest="report_json",
            required=True,
            help="Path to the stability JSON artifact produced by `stability run` or `stability compare`",
        )
        parser.add_argument(
            "--report-md",
            dest="report_md",
            default=None,
            help="Optional path to the authored Markdown qualification report to verify",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        json_path = args.report_json
        if not os.path.isfile(json_path):
            raise ExecutionError(
                f"Stability report JSON does not exist yet at '{json_path}'.",
                remediation="Run `emu-dev-cli stability run` (or `compare`) with `--output <report-json>` first.",
            )
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise ExecutionError(
                f"Failed to parse stability JSON '{json_path}': {exc}",
                remediation="Re-run the stability command so a valid UTF-8 JSON report is written.",
                cause=exc,
            ) from exc

        is_single = isinstance(data, dict) and "evaluation" in data
        is_compare = isinstance(data, dict) and "comparisons" in data
        if not (is_single or is_compare):
            raise ExecutionError(
                f"File '{json_path}' is missing required stability report keys ('evaluation' or 'comparisons').",
                remediation="Ensure `--output` points to a JSON artifact generated by `emu-dev-cli stability`.",
            )

        md_path = getattr(args, "report_md", None)
        if md_path:
            if not os.path.isfile(md_path):
                raise ExecutionError(
                    f"Stability Markdown report does not exist yet at '{md_path}'.",
                    remediation="Write or pass `--report <report-md>` so the Markdown qualification report exists.",
                )
            with open(md_path, "r", encoding="utf-8") as f:
                md_text = f.read().strip()
            if len(md_text) < 80 or "Verdict" not in md_text:
                raise ExecutionError(
                    f"Stability Markdown report '{md_path}' is incomplete (missing Verdict summary).",
                    remediation="Include the full stability verdict, SLA summary table, and findings in the report.",
                )

        return CommandResult.success(
            {"verified_json": json_path, "verified_md": md_path},
            summary=f"Verified stability artifacts: {json_path}",
            action="stability verify",
        )


class StabilityCommand(CommandGroup):
    """Parent command group for `emu-dev-cli stability`."""

    name = "stability"
    help_text = "Run user-experience stability soaks, snapshot torture, gRPC stress, and leak detection"
    subcommand_dest = "stability_cmd"
    subcommands = (
        StabilityListCommand(),
        StabilityRunCommand(),
        StabilityCompareCommand(),
        StabilityHistoryCommand(),
        StabilityVerifyCommand(),
    )


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Registers the `stability` command group (`list`, `run`, `compare`, `history`, `verify`)."""
    return StabilityCommand().register(subparsers)
