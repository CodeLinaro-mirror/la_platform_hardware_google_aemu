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
from commands.harness.ledger import SessionLedgerStore
from commands.harness.plugin_installer import (
    get_harness_plugin_status,
    install_harness_plugin,
    uninstall_harness_plugin,
)
from lib.output import print_result


def register_parser(subparsers: Any) -> None:
    """Registers the `emu-dev-cli harness` subcommand group."""
    harness_parser = subparsers.add_parser(
        "harness",
        help="Manage the Android Emulator Custom Agent Harness (Jetski Web & CLI plugin, hooks, and ledger)",
    )
    harness_subparsers = harness_parser.add_subparsers(
        dest="harness_action",
        help="Harness actions",
    )
    harness_parser.set_defaults(
        func=lambda args: harness_parser.print_help()
    )

    install_p = harness_subparsers.add_parser(
        "install",
        help="Install the aemu-harness plugin into ~/.gemini/config/plugins/aemu-harness",
    )
    install_p.add_argument(
        "--target-dir",
        type=str,
        default=None,
        help="Override target plugin installation directory",
    )
    install_p.set_defaults(func=run_harness_install)

    uninstall_p = harness_subparsers.add_parser(
        "uninstall",
        help="Uninstall the aemu-harness plugin from ~/.gemini/config/plugins/aemu-harness",
    )
    uninstall_p.add_argument(
        "--target-dir",
        type=str,
        default=None,
        help="Override target plugin directory",
    )
    uninstall_p.set_defaults(func=run_harness_uninstall)

    status_p = harness_subparsers.add_parser(
        "status",
        help="Show plugin installation status, active workflows, and session ledger telemetry",
    )
    status_p.add_argument(
        "--conversation",
        type=str,
        default=None,
        help="Inspect ledger for a specific Jetski conversation ID",
    )
    status_p.set_defaults(func=run_harness_status)

    hook_p = harness_subparsers.add_parser(
        "hook",
        help="Internal Jetski lifecycle hook entrypoint (reads JSON from stdin, writes JSON to stdout)",
    )
    hook_p.add_argument(
        "--event",
        required=True,
        choices=[e.value for e in HookEvent],
        help="Hook lifecycle event name",
    )
    hook_p.set_defaults(func=run_harness_hook)

    clear_p = harness_subparsers.add_parser(
        "clear",
        help="Clear stored session ledger history",
    )
    clear_p.add_argument(
        "--conversation",
        type=str,
        default=None,
        help="Clear only the specified conversation ID (clears all if omitted)",
    )
    clear_p.set_defaults(func=run_harness_clear)

    for sub_p in (install_p, uninstall_p, status_p, clear_p):
        sub_p.add_argument(
            "--json",
            action="store_true",
            help="Output results in machine-readable JSON format",
        )


def run_harness_install(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness install`."""
    json_mode = getattr(args, "json", False)
    target_dir = Path(args.target_dir) if getattr(args, "target_dir", None) else None
    report = install_harness_plugin(target_dir=target_dir)
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

    print_result(
        {
            "status": "success",
            "action": "harness status",
            "summary": (
                f"aemu-harness plugin installed={plugin_status['installed']}, "
                f"active_workflows={len(active_workflows)}, "
                f"tracked_sessions={len(ledgers)}"
            ),
            "plugin": plugin_status,
            "active_workflows": active_workflows,
            "sessions": ledgers[:10],
        },
        json_mode=json_mode,
    )


def run_harness_hook(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli harness hook --event <event>`."""
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
    sys.stdout.write(json.dumps(response) + "\n")
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
