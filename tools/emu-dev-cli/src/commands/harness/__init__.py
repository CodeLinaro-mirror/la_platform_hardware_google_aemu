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
from pathlib import Path
import sys
from typing import Any, Dict

from commands.harness.constants import HookEvent
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
from commands.harness.review_history import load_recent_swarm_metrics
from commands.harness.review_swarm import (
    REVIEW_SWARM_SUBDIR,
    filter_and_aggregate_findings,
    prepare_review_swarm_bundle,
)
from lib.output import ensure_utf8_stdio, print_result


def _register_install_parser(harness_subparsers: Any) -> Any:
    """Registers the `emu-dev-cli harness install` CLI subparser."""
    install_p = harness_subparsers.add_parser(
        "install",
        help="Install the aemu-harness plugin into ~/.gemini/config/plugins/aemu-harness",
    )
    install_p.add_argument(
        "--scope", choices=["global", "workspace"], default="global",
        help="Install scope: 'global' (~/.gemini/config/plugins) or 'workspace' (<workspace>/.agent/plugins)",
    )
    install_p.add_argument(
        "--global", dest="scope", action="store_const", const="global",
        help="Shorthand for --scope global (default: installs into ~/.gemini/config/plugins/aemu-harness)",
    )
    install_p.add_argument(
        "--workspace", dest="scope", action="store_const", const="workspace",
        help="Shorthand for --scope workspace (installs into <workspace>/.agent/plugins/aemu-harness)",
    )
    install_p.add_argument(
        "--target-dir", type=str, default=None, help="Override target plugin installation directory",
    )
    install_p.set_defaults(func=run_harness_install)
    return install_p


def _register_core_subparsers(harness_subparsers: Any) -> list[Any]:
    """Registers `uninstall`, `status`, `hook`, `clear`, `mcp-server`, and `presets` subparsers."""
    uninstall_p = harness_subparsers.add_parser(
        "uninstall", help="Uninstall the aemu-harness plugin from ~/.gemini/config/plugins/aemu-harness"
    )
    uninstall_p.add_argument("--target-dir", type=str, default=None, help="Override target plugin directory")
    uninstall_p.set_defaults(func=run_harness_uninstall)

    status_p = harness_subparsers.add_parser(
        "status", help="Show plugin installation status, active workflows, and session ledger telemetry"
    )
    status_p.add_argument("--conversation", type=str, default=None, help="Inspect ledger for a specific conversation ID")
    status_p.set_defaults(func=run_harness_status)

    hook_p = harness_subparsers.add_parser(
        "hook", help="Internal Jetski lifecycle hook entrypoint (reads JSON from stdin, writes JSON to stdout)"
    )
    hook_p.add_argument("--event", required=True, choices=[e.value for e in HookEvent], help="Hook lifecycle event name")
    hook_p.set_defaults(func=run_harness_hook)

    clear_p = harness_subparsers.add_parser("clear", help="Clear stored session ledger history")
    clear_p.add_argument("--conversation", type=str, default=None, help="Clear only the specified conversation ID")
    clear_p.set_defaults(func=run_harness_clear)

    mcp_p = harness_subparsers.add_parser("mcp-server", help="Run the Stdio Model Context Protocol (MCP) server")
    mcp_p.set_defaults(func=lambda _args: run_stdio_mcp_server())

    presets_p = harness_subparsers.add_parser("presets", help="List multi-agent swarm and workflow presets")
    presets_p.add_argument("--name", type=str, default=None, help="Filter by preset name (e.g. parallel-code-review)")
    presets_p.set_defaults(func=run_harness_presets)
    return [uninstall_p, status_p, clear_p, presets_p]


def register_parser(subparsers: Any) -> None:
    """Registers the `emu-dev-cli harness` subcommand group."""
    harness_parser = subparsers.add_parser(
        "harness",
        help="Manage the Android Emulator Custom Agent Harness (Jetski Web & CLI plugin, hooks, and ledger)",
    )
    harness_subparsers = harness_parser.add_subparsers(dest="harness_action", help="Harness actions")
    harness_parser.set_defaults(func=lambda args: harness_parser.print_help())

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
    swarm_p.add_argument("--target", type=str, default=None, help="Unified Gerrit CL ID/URL/Change-Id or git commit ref")
    swarm_p.add_argument("--cl", type=str, default=None, help="Gerrit CL number, ag/<id>, or URL")
    swarm_p.add_argument("--commit", type=str, default="HEAD", help="Local git commit ref (default: HEAD)")
    swarm_p.add_argument("--mode", choices=["workflow", "swarm"], default="workflow", help="Orchestration mode")
    swarm_p.add_argument("--repo", "--repo-dir", dest="repo", type=str, default=None, help="Local git repo path")
    swarm_p.add_argument("--output-dir", type=str, default=None, help="Bundle output directory")
    swarm_p.add_argument("--aggregate-from", type=str, default=None, help="Raw specialist outputs JSON file")
    swarm_p.add_argument(
        "--full",
        action="store_true",
        help="Force a full 8-specialist review pass ignoring cached iteration history",
    )
    swarm_p.set_defaults(func=run_harness_review_swarm)
    return swarm_p


def run_harness_install(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness install`."""
    json_mode = getattr(args, "json", False)
    scope = getattr(args, "scope", "global") or "global"
    target_dir = Path(args.target_dir) if getattr(args, "target_dir", None) else None
    report = install_harness_plugin(target_dir=target_dir, scope=scope)
    if not report.get("installed"):
        print_result(
            {
                "status": "error",
                "action": "harness install",
                "summary": report.get("error", "Failed to install aemu-harness plugin"),
                "details": report,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    print_result(
        {
            "status": "success",
            "action": "harness install",
            "summary": f"Installed aemu-harness plugin to {report['plugin_dir']}",
            "plugin_dir": report["plugin_dir"],
            "cli_command": report["cli_command"],
            "installed_files": report["installed_files"],
        },
        json_mode=json_mode,
    )


def run_harness_uninstall(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness uninstall`."""
    json_mode = getattr(args, "json", False)
    target_dir = Path(args.target_dir) if getattr(args, "target_dir", None) else None
    report = uninstall_harness_plugin(target_dir=target_dir)
    print_result(
        {
            "status": "success",
            "action": "harness uninstall",
            "summary": (
                f"Removed aemu-harness plugin from {report['plugin_dir']}"
                if report.get("removed")
                else f"No plugin directory found at {report['plugin_dir']}"
            ),
            "details": report,
        },
        json_mode=json_mode,
    )


def run_harness_status(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness status`."""
    json_mode = getattr(args, "json", False)
    conv_id = getattr(args, "conversation", None)
    plugin_status = get_harness_plugin_status()
    store = SessionLedgerStore()
    active_workflows = discover_active_workflows(conversation_id=conv_id)
    ledgers = [store.load(conv_id)] if conv_id else store.list_all_ledgers()
    recent_metrics = load_recent_swarm_metrics(get_harness_base_dir() / REVIEW_SWARM_SUBDIR)

    print_result(
        {
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
        },
        json_mode=json_mode,
    )


def run_harness_hook(args: argparse.Namespace) -> None:
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

    response = dispatch_hook_event(args.event, payload)
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_harness_clear(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness clear`."""
    json_mode = getattr(args, "json", False)
    conv_id = getattr(args, "conversation", None)
    store = SessionLedgerStore()
    removed = store.clear(conversation_id=conv_id)
    print_result(
        {
            "status": "success",
            "action": "harness clear",
            "summary": f"Cleared {removed} session ledger(s)",
            "removed": removed,
        },
        json_mode=json_mode,
    )


def run_harness_presets(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness presets`."""
    json_mode = getattr(args, "json", False)
    preset_name = getattr(args, "name", None)
    presets = list_harness_presets(name=preset_name)
    print_result(
        {
            "status": "success",
            "action": "harness presets",
            "summary": f"Found {len(presets)} multi-agent harness preset(s)",
            "presets": presets,
        },
        json_mode=json_mode,
    )


def _attach_cli_aggregation(
    result_payload: Dict[str, Any],
    agg_from: str,
    changed_lines: Dict[str, Any],
) -> None:
    """Loads specialist outputs from `agg_from` and attaches filtered results."""
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
    aggregated = filter_and_aggregate_findings(raw_outputs, changed_lines)
    result_payload["aggregated"] = aggregated
    result_payload["summary"] = (
        f"Aggregated {aggregated['total_findings']} diff-bounded finding(s) | "
        f"{aggregated['reuse_audit_line']} | {aggregated['error_audit_line']}"
    )


def run_harness_review_swarm(args: argparse.Namespace) -> None:
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
        print_result(result_payload, json_mode=json_mode)
    except Exception as exc:
        print_result(
            {
                "status": "error",
                "action": "harness review-swarm",
                "summary": (
                    f"Failed to {stage_desc}: {exc}. "
                    "Verify `--cl`/`--commit`, `--repo`, and `--aggregate-from` arguments and retry."
                ),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )

