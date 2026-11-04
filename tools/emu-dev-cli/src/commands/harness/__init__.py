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

"""CLI subcommand group for the Android Emulator Jetski Harness (`emu-dev-cli harness`)."""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from commands.harness.constants import HookDecision, HookEvent
from commands.harness.guardrails import (
    discover_active_workflows,
    dispatch_hook_event,
)
from commands.harness.ledger import SessionLedgerStore, get_harness_base_dir
from commands.harness.mcp_server import run_stdio_mcp_server
from commands.harness.plugin_installer import (
    get_harness_plugin_status,
    install_harness_plugin,
    uninstall_harness_plugin,
)
from commands.harness.presets import list_harness_presets
from commands.harness.review_history import (
    build_wave_metrics,
    load_recent_swarm_metrics,
    persist_iteration_and_metrics,
)
from commands.harness.review_swarm import (
    REVIEW_SWARM_SUBDIR,
    atomic_write_text,
    filter_and_aggregate_findings,
    prepare_review_swarm_bundle,
)
from commands.harness.review_targets import sanitize_target_id
from commands.harness.terminal_title import TerminalTitleUpdater
from core.command import (
    Command,
    CommandResult,
    CommandValidationError,
    META_KEY_ALREADY_RENDERED,
    bind_command,
)
from core.context import ExecutionContext
from lib.output import ensure_utf8_stdio, print_result

LOGGER = logging.getLogger(__name__)

REPORT_MD_FILENAME: str = "review_swarm_report.md"
GERRIT_PAYLOAD_FILENAME: str = "gerrit_review_payload.json"
JSON_INDENT_SPACES: int = 2
VALID_SCOPES = frozenset({"global", "workspace"})
VALID_SWARM_MODES = frozenset({"workflow", "swarm"})


class HarnessInstallCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness install`."""

    name = "harness install"
    description = "Install the aemu-harness plugin into ~/.gemini/config/plugins/aemu-harness"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--scope", choices=["global", "workspace"], default="global",
            help="Install scope: 'global' (~/.gemini/config/plugins) or 'workspace' (<workspace>/.agent/plugins)",
        )
        parser.add_argument(
            "--global", dest="scope", action="store_const", const="global",
            help="Shorthand for --scope global (default: installs into ~/.gemini/config/plugins/aemu-harness)",
        )
        parser.add_argument(
            "--workspace", dest="scope", action="store_const", const="workspace",
            help="Shorthand for --scope workspace (installs into <workspace>/.agent/plugins/aemu-harness)",
        )
        parser.add_argument(
            "--target-dir", type=str, default=None, help="Override target plugin installation directory",
        )

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        scope = getattr(args, "scope", "global") or "global"
        if scope not in VALID_SCOPES:
            raise CommandValidationError(
                f"Invalid '--scope' value for 'harness install': expected one of "
                f"{sorted(VALID_SCOPES)}, got {scope!r}. Pass '--scope global' or '--scope workspace'."
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_install(args, render_output=False)
        if payload.get("status") == "error":
            return CommandResult.failure(
                payload.get("summary", "Failed to install aemu-harness plugin."),
                exit_code=int(payload.get("exit_code", 1)),
                data=payload,
            )
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessUninstallCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness uninstall`."""

    name = "harness uninstall"
    description = "Uninstall the aemu-harness plugin from ~/.gemini/config/plugins/aemu-harness"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--target-dir", type=str, default=None, help="Override target plugin directory")

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_uninstall(args, render_output=False)
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessStatusCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness status`."""

    name = "harness status"
    description = "Show plugin installation status, active workflows, and session ledger telemetry"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--conversation", type=str, default=None, help="Inspect ledger for a specific conversation ID")

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_status(args, render_output=False)
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessHookCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness hook`."""

    name = "harness hook"
    description = "Internal Jetski lifecycle hook entrypoint (reads JSON from stdin, writes JSON to stdout)"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--event", required=True, choices=[e.value for e in HookEvent], help="Hook lifecycle event name")

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        event = getattr(args, "event", None)
        valid_events = {e.value for e in HookEvent}
        if event not in valid_events:
            raise CommandValidationError(
                f"Invalid '--event' value for 'harness hook': expected one of "
                f"{sorted(valid_events)}, got {event!r}. Pass a valid HookEvent name."
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_hook(args)
        return CommandResult.success(
            payload,
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class HarnessClearCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness clear`."""

    name = "harness clear"
    description = "Clear stored session ledger history"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--conversation", type=str, default=None, help="Clear only the specified conversation ID")

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_clear(args, render_output=False)
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessMcpServerCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness mcp-server`."""

    name = "harness mcp-server"
    description = "Run the Stdio Model Context Protocol (MCP) server"

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del args, ctx
        run_stdio_mcp_server()
        return CommandResult.success(
            {"status": "success", "action": "harness mcp-server"},
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class HarnessPresetsCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness presets`."""

    name = "harness presets"
    description = "List multi-agent swarm and workflow presets"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--name", type=str, default=None, help="Filter by preset name (e.g. parallel-code-review)")

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_presets(args, render_output=False)
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessReviewSwarmCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for `emu-dev-cli harness review-swarm`."""

    name = "harness review-swarm"
    description = "Prepare or aggregate the 8-Specialist Single-Wave Parallel Code-Review Swarm"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--target", type=str, default=None, help="Unified Gerrit CL ID/URL/Change-Id or git commit ref")
        parser.add_argument("--cl", type=str, default=None, help="Gerrit CL number, ag/<id>, or URL")
        parser.add_argument("--commit", type=str, default="HEAD", help="Local git commit ref (default: HEAD)")
        parser.add_argument("--mode", choices=["workflow", "swarm"], default="workflow", help="Orchestration mode")
        parser.add_argument("--repo", "--repo-dir", dest="repo", type=str, default=None, help="Local git repo path")
        parser.add_argument("--output-dir", type=str, default=None, help="Bundle output directory")
        parser.add_argument("--aggregate-from", type=str, default=None, help="Raw specialist outputs JSON file")
        parser.add_argument(
            "--full",
            action="store_true",
            help="Force a full 8-specialist review pass ignoring cached iteration history",
        )

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        mode = getattr(args, "mode", "workflow") or "workflow"
        if mode not in VALID_SWARM_MODES:
            raise CommandValidationError(
                f"Invalid '--mode' value for 'harness review-swarm': expected one of "
                f"{sorted(VALID_SWARM_MODES)}, got {mode!r}. Pass '--mode workflow' or '--mode swarm'."
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        del ctx
        payload = run_harness_review_swarm(args, render_output=False)
        if payload.get("status") == "error":
            return CommandResult.failure(
                payload.get("summary", "Failed to run harness review-swarm."),
                exit_code=int(payload.get("exit_code", 1)),
                data=payload,
            )
        return CommandResult.success(payload, summary=payload.get("summary", ""))


class HarnessCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Composite Command implementation for `emu-dev-cli harness`."""

    name = "harness"
    description = "Manage the Android Emulator Custom Agent Harness (Jetski Web & CLI plugin, hooks, and ledger)"

    def __init__(self, parser: Optional[argparse.ArgumentParser] = None) -> None:
        self._parser = parser
        self._subcommands: Dict[str, Command[argparse.Namespace, Any]] = {
            "install": HarnessInstallCommand(),
            "uninstall": HarnessUninstallCommand(),
            "status": HarnessStatusCommand(),
            "hook": HarnessHookCommand(),
            "clear": HarnessClearCommand(),
            "mcp-server": HarnessMcpServerCommand(),
            "presets": HarnessPresetsCommand(),
            "review-swarm": HarnessReviewSwarmCommand(),
        }

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        action = getattr(args, "harness_action", None)
        if action in self._subcommands:
            return self._subcommands[action].validate(args, ctx)
        if self._parser is not None:
            self._parser.print_help()
        raise CommandValidationError(
            f"Unknown or missing harness action {action!r}: expected one of "
            f"{sorted(self._subcommands)}, got {action!r}. "
            "Run 'emu-dev-cli harness --help' to inspect available actions."
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        action = getattr(args, "harness_action", None)
        if action in self._subcommands:
            return self._subcommands[action].execute(args, ctx)
        if self._parser is not None:
            self._parser.print_help()
        return CommandResult.failure(
            "Unknown or missing harness action.",
            exit_code=1,
            data={"status": "error", "action": "harness", "exit_code": 1},
        )


def _register_install_parser(harness_subparsers: Any) -> Any:
    """Registers the `emu-dev-cli harness install` CLI subparser."""
    install_p = harness_subparsers.add_parser(
        "install",
        help="Install the aemu-harness plugin into ~/.gemini/config/plugins/aemu-harness",
    )
    bind_command(
        install_p,
        HarnessInstallCommand(),
        configure=True,
        legacy_func=run_harness_install,
    )
    return install_p


def _register_core_subparsers(harness_subparsers: Any) -> list[Any]:
    """Registers `uninstall`, `status`, `hook`, `clear`, `mcp-server`, and `presets` subparsers."""
    uninstall_p = harness_subparsers.add_parser(
        "uninstall", help="Uninstall the aemu-harness plugin from ~/.gemini/config/plugins/aemu-harness"
    )
    bind_command(
        uninstall_p,
        HarnessUninstallCommand(),
        configure=True,
        legacy_func=run_harness_uninstall,
    )

    status_p = harness_subparsers.add_parser(
        "status", help="Show plugin installation status, active workflows, and session ledger telemetry"
    )
    bind_command(
        status_p,
        HarnessStatusCommand(),
        configure=True,
        legacy_func=run_harness_status,
    )

    hook_p = harness_subparsers.add_parser(
        "hook", help="Internal Jetski lifecycle hook entrypoint (reads JSON from stdin, writes JSON to stdout)"
    )
    bind_command(
        hook_p,
        HarnessHookCommand(),
        configure=True,
        legacy_func=run_harness_hook,
    )

    clear_p = harness_subparsers.add_parser("clear", help="Clear stored session ledger history")
    bind_command(
        clear_p,
        HarnessClearCommand(),
        configure=True,
        legacy_func=run_harness_clear,
    )

    mcp_p = harness_subparsers.add_parser("mcp-server", help="Run the Stdio Model Context Protocol (MCP) server")
    bind_command(
        mcp_p,
        HarnessMcpServerCommand(),
        configure=True,
        legacy_func=lambda _args: run_stdio_mcp_server(),
    )

    presets_p = harness_subparsers.add_parser("presets", help="List multi-agent swarm and workflow presets")
    bind_command(
        presets_p,
        HarnessPresetsCommand(),
        configure=True,
        legacy_func=run_harness_presets,
    )
    return [uninstall_p, status_p, clear_p, presets_p]


def register_parser(subparsers: Any) -> None:
    """Registers the `emu-dev-cli harness` subcommand group."""
    harness_parser = subparsers.add_parser(
        "harness",
        help="Manage the Android Emulator Custom Agent Harness (Jetski Web & CLI plugin, hooks, and ledger)",
    )
    harness_subparsers = harness_parser.add_subparsers(dest="harness_action", help="Harness actions")
    bind_command(
        harness_parser,
        HarnessCommand(parser=harness_parser),
        configure=False,
        legacy_func=lambda args: harness_parser.print_help(),
    )

    install_p = _register_install_parser(harness_subparsers)
    core_parsers = _register_core_subparsers(harness_subparsers)
    swarm_p = _register_review_swarm_parser(harness_subparsers)
    for sub_p in (install_p, *core_parsers, swarm_p):
        sub_p.add_argument("--json", action="store_true", help="Output results in machine-readable JSON format")


def _register_review_swarm_parser(harness_subparsers: Any) -> Any:
    """Registers the `emu-dev-cli harness review-swarm` CLI subparser."""
    swarm_p = harness_subparsers.add_parser(
        "review-swarm",
        help="Prepare or aggregate the 8-Specialist Single-Wave Parallel Code-Review Swarm",
    )
    bind_command(
        swarm_p,
        HarnessReviewSwarmCommand(),
        configure=True,
        legacy_func=run_harness_review_swarm,
    )
    return swarm_p


def run_harness_install(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness install`."""
    json_mode = getattr(args, "json", False)
    scope = getattr(args, "scope", "global") or "global"
    target_dir = Path(args.target_dir) if getattr(args, "target_dir", None) else None
    report = install_harness_plugin(target_dir=target_dir, scope=scope)
    if not report.get("installed"):
        err_payload = {
            "status": "error",
            "action": "harness install",
            "summary": report.get("error", "Failed to install aemu-harness plugin"),
            "details": report,
            "exit_code": 1,
        }
        if render_output:
            print_result(
                err_payload,
                json_mode=json_mode,
                is_error=True,
            )
        return err_payload

    res_payload = {
        "status": "success",
        "action": "harness install",
        "summary": f"Installed aemu-harness plugin to {report['plugin_dir']}",
        "plugin_dir": report["plugin_dir"],
        "cli_command": report["cli_command"],
        "installed_files": report["installed_files"],
    }
    if render_output:
        print_result(
            res_payload,
            json_mode=json_mode,
        )
    return res_payload


def run_harness_uninstall(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness uninstall`."""
    json_mode = getattr(args, "json", False)
    target_dir = Path(args.target_dir) if getattr(args, "target_dir", None) else None
    report = uninstall_harness_plugin(target_dir=target_dir)
    res_payload = {
        "status": "success",
        "action": "harness uninstall",
        "summary": (
            f"Removed aemu-harness plugin from {report['plugin_dir']}"
            if report.get("removed")
            else f"No plugin directory found at {report['plugin_dir']}"
        ),
        "details": report,
    }
    if render_output:
        print_result(
            res_payload,
            json_mode=json_mode,
        )
    return res_payload


def run_harness_status(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness status`."""
    json_mode = getattr(args, "json", False)
    conv_id = getattr(args, "conversation", None)
    plugin_status = get_harness_plugin_status()
    store = SessionLedgerStore()
    active_workflows = discover_active_workflows(conversation_id=conv_id)
    ledgers = [store.load(conv_id)] if conv_id else store.list_all_ledgers()
    recent_metrics = load_recent_swarm_metrics(get_harness_base_dir() / REVIEW_SWARM_SUBDIR)

    res_payload = {
        "status": "success",
        "action": "harness status",
        "summary": (
            f"aemu-harness plugin installed={plugin_status['installed']}, "
            f"active_workflows={len(active_workflows)}, "
            f"tracked_sessions={len(ledgers)}, "
            f"recent_swarm_runs={len(recent_metrics)}"
        ),
        "plugin": plugin_status,
        "active_workflows": active_workflows,
        "recent_review_swarm_metrics": recent_metrics,
        "sessions": ledgers[:10],
    }
    if render_output:
        print_result(
            res_payload,
            json_mode=json_mode,
        )
    return res_payload


def run_harness_hook(args: argparse.Namespace) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness hook --event <event>`."""
    ensure_utf8_stdio()
    raw_stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload: Dict[str, Any] = {}
    if raw_stdin.strip():
        try:
            parsed = json.loads(raw_stdin)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    try:
        response = dispatch_hook_event(args.event, payload)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.warning(
            "Failed to dispatch harness hook event %r: %s; falling back to "
            '\'{"decision": "allow"}\'. Run \'emu-dev-cli --verbose harness status\' '
            "to inspect harness state.",
            getattr(args, "event", None),
            exc,
        )
        response = {"decision": HookDecision.ALLOW.value}

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return response


def run_harness_clear(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness clear`."""
    json_mode = getattr(args, "json", False)
    conv_id = getattr(args, "conversation", None)
    store = SessionLedgerStore()
    removed = store.clear(conversation_id=conv_id)
    TerminalTitleUpdater(base_dir=store.base_dir).clear_state(conversation_id=conv_id)
    res_payload = {
        "status": "success",
        "action": "harness clear",
        "summary": f"Cleared {removed} session ledger(s)",
        "removed": removed,
    }
    if render_output:
        print_result(
            res_payload,
            json_mode=json_mode,
        )
    return res_payload


def run_harness_presets(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness presets`."""
    json_mode = getattr(args, "json", False)
    preset_name = getattr(args, "name", None)
    presets = list_harness_presets(name=preset_name)
    res_payload = {
        "status": "success",
        "action": "harness presets",
        "summary": f"Found {len(presets)} multi-agent harness preset(s)",
        "presets": presets,
    }
    if render_output:
        print_result(
            res_payload,
            json_mode=json_mode,
        )
    return res_payload


def _persist_cli_aggregated_artifacts(
    result_payload: Dict[str, Any],
    manifest: Dict[str, Any],
    raw_outputs: list[Dict[str, Any]],
    aggregated: Dict[str, Any],
    metrics: Dict[str, Any],
) -> None:
    """Writes report/payload artifacts and persists round history and telemetry."""
    out_dir_path = Path(str(result_payload["output_dir"]))
    out_dir_path.mkdir(parents=True, exist_ok=True)
    report_md_path = out_dir_path / REPORT_MD_FILENAME
    gerrit_json_path = out_dir_path / GERRIT_PAYLOAD_FILENAME
    payload_text = (
        json.dumps(
            aggregated["gerrit_review_payload"],
            indent=JSON_INDENT_SPACES,
            ensure_ascii=False,
        )
        + "\n"
    )
    atomic_write_text(report_md_path, aggregated["markdown_report"])
    atomic_write_text(gerrit_json_path, payload_text)

    cache_root = get_harness_base_dir() / REVIEW_SWARM_SUBDIR
    safe_id = sanitize_target_id(str(result_payload.get("target_id") or "HEAD"))
    default_cache_out = cache_root / safe_id
    if out_dir_path.resolve() != default_cache_out.resolve():
        default_cache_out.mkdir(parents=True, exist_ok=True)
        atomic_write_text(default_cache_out / REPORT_MD_FILENAME, aggregated["markdown_report"])
        atomic_write_text(default_cache_out / GERRIT_PAYLOAD_FILENAME, payload_text)

    metrics_path = persist_iteration_and_metrics(
        out_dir_path, cache_root, manifest, raw_outputs, aggregated, metrics
    )
    result_payload["report_md_path"] = str(report_md_path)
    result_payload["gerrit_json_path"] = str(gerrit_json_path)
    result_payload["metrics_path"] = str(metrics_path)


def _attach_cli_aggregation(
    result_payload: Dict[str, Any],
    agg_from: str,
    changed_lines: Dict[str, Any],
) -> None:
    """Loads specialist outputs from `agg_from`, writes artifacts, and attaches results."""
    raw_outputs = json.loads(Path(agg_from).read_text(encoding="utf-8"))
    bad_desc = (
        type(raw_outputs).__name__
        if not isinstance(raw_outputs, list)
        else next(
            (f"element[{i}] of type {type(v).__name__}" for i, v in enumerate(raw_outputs) if not isinstance(v, dict)),
            None,
        )
    )
    if bad_desc is not None:
        raise ValueError(
            f"Invalid specialist outputs in '{agg_from}': expected a JSON array of "
            f"specialist result objects, got {bad_desc}. "
            "Write a top-level JSON list ('[...]') matching SPECIALIST_OUTPUT_SCHEMA and retry."
        )
    manifest_path = Path(str(result_payload.get("manifest_path") or ""))
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else result_payload
    )
    wall_clock_sec = max(
        (float(item.get("duration_sec") or 0.0) for item in raw_outputs),
        default=0.0,
    )
    metrics = build_wave_metrics(manifest, raw_outputs, wall_clock_sec)
    aggregated = filter_and_aggregate_findings(raw_outputs, changed_lines, metrics=metrics)
    if "output_dir" in result_payload:
        _persist_cli_aggregated_artifacts(
            result_payload, manifest, raw_outputs, aggregated, metrics
        )
    result_payload["aggregated"] = aggregated
    result_payload["summary"] = (
        f"Aggregated {aggregated['total_findings']} diff-bounded finding(s) | "
        f"{aggregated['reuse_audit_line']} | {aggregated['error_audit_line']}"
    )


def run_harness_review_swarm(
    args: argparse.Namespace, *, render_output: bool = True
) -> Dict[str, Any]:
    """CLI handler for `emu-dev-cli harness review-swarm`."""
    json_mode = getattr(args, "json", False)
    target = getattr(args, "target", None)
    cl = getattr(args, "cl", None)
    commit = getattr(args, "commit", "HEAD") or "HEAD"
    mode = getattr(args, "mode", "workflow") or "workflow"
    repo_dir = Path(args.repo) if getattr(args, "repo", None) else None
    out_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else None
    agg_from = getattr(args, "aggregate_from", None)
    force_full = bool(getattr(args, "full", False))
    target_desc = (cl or target or commit).split("?", 1)[0]
    stage_desc = f"prepare review swarm for '{target_desc}'"
    try:
        bundle = prepare_review_swarm_bundle(
            cl=cl, commit=commit, mode=mode, repo_dir=repo_dir,
            output_dir=out_dir, target=target, force_full=force_full,
        )
        result_payload: Dict[str, Any] = {
            "status": "success",
            "action": "harness review-swarm",
            "summary": (
                f"Prepared {bundle['specialist_count']}-specialist {bundle['review_scope']} "
                f"review swarm (round {bundle['round_number']}, {mode}) for "
                f"{bundle['target_type']} {bundle['target_id']} -> {bundle['workflow_script_path']}"
            ),
            **bundle,
        }
        if agg_from:
            stage_desc = f"aggregate specialist findings from '{agg_from}' for '{target_desc}'"
            _attach_cli_aggregation(
                result_payload, agg_from, bundle.get("changed_lines_by_file") or {}
            )
        if render_output:
            print_result(result_payload, json_mode=json_mode)
        return result_payload
    except Exception as exc:
        err_payload = {
            "status": "error",
            "action": "harness review-swarm",
            "summary": (
                f"Failed to {stage_desc}: {exc}. "
                "Verify `--cl`/`--commit`, `--repo`, and `--aggregate-from` arguments and retry."
            ),
            "exit_code": 1,
        }
        if render_output:
            print_result(
                err_payload,
                json_mode=json_mode,
                is_error=True,
            )
        return err_payload

