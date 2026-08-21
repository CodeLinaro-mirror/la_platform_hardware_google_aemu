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

import sys
from typing import List
from lib.emulator import calculate_mesh_ports
from lib.mesh import (
    check_device_boot_completed,
    find_netsim_binary,
    find_netsim_device_by_name,
    get_connected_adb_devices,
    get_device_avd_name,
    get_netsim_devices,
    summarize_netsim_chips,
    teardown_mesh,
    wait_for_mesh_ready,
)
from lib.output import format_markdown_table, print_result


def _resolve_target_serials(
    args, allow_auto_adb: bool = False
) -> List[str]:
    """Resolves list of target serials from --serials, --all, or (--prefix, --count, --base-port)."""
    raw_serials = getattr(args, "serials", None)
    if raw_serials:
        return [s.strip() for s in raw_serials.split(",") if s.strip()]

    if getattr(args, "all", False):
        return get_connected_adb_devices()

    if allow_auto_adb and not getattr(args, "prefix", None):
        connected = get_connected_adb_devices()
        if connected:
            return connected

    count = getattr(args, "count", 2) or 2
    base_port = getattr(args, "base_port", 5554) or 5554
    ports = calculate_mesh_ports(base_port=base_port, count=count)
    return [p["serial"] for p in ports]


def register_parser(subparsers):
    mesh_parser = subparsers.add_parser(
        "mesh", help="Inspect and monitor emulator mesh instances and radio networks"
    )
    mesh_parser.set_defaults(
        func=lambda args: mesh_parser.print_help() or sys.exit(0)
    )
    mesh_subparsers = mesh_parser.add_subparsers(
        dest="mesh_cmd", help="Available mesh operations"
    )

    # emu-dev-cli mesh wait-ready ...
    wait_parser = mesh_subparsers.add_parser(
        "wait-ready",
        help="Wait until all mesh nodes finish booting and verify Netsim radio connectivity",
    )
    wait_parser.add_argument(
        "--serials",
        type=str,
        default=None,
        help="Comma-separated list of target serials (e.g. 'emulator-5554,emulator-5556')",
    )
    wait_parser.add_argument(
        "--prefix",
        type=str,
        default="medium_phone",
        help="Prefix of the AVD mesh instances (used to compute serials if --serials is omitted)",
    )
    wait_parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of mesh instances to wait for (default: 2)",
    )
    wait_parser.add_argument(
        "--base-port",
        type=int,
        default=5554,
        help="Base console port (default: 5554)",
    )
    wait_parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout in seconds to wait for boot completion (default: 180s)",
    )
    wait_parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds between boot status checks (default: 2.0s)",
    )
    wait_parser.add_argument(
        "--no-unlock",
        action="store_true",
        help="Skip automatic keyguard dismissal and screen wake",
    )
    wait_parser.add_argument(
        "--no-netsim-check",
        action="store_true",
        help="Skip Netsim radio registration verification",
    )
    wait_parser.add_argument(
        "--emulator-dir",
        type=str,
        default=None,
        help="Directory containing extracted prebuilt emulator to locate netsim binary",
    )
    wait_parser.set_defaults(parser=wait_parser, func=run_wait_ready)

    # emu-dev-cli mesh status ...
    status_parser = mesh_subparsers.add_parser(
        "status",
        help="Inspect immediate boot status, keyguard, and Netsim radio chips across mesh nodes",
    )
    status_parser.add_argument(
        "--serials",
        type=str,
        default=None,
        help="Comma-separated list of target serials (auto-discovers all online emulators if omitted)",
    )
    status_parser.add_argument(
        "--emulator-dir",
        type=str,
        default=None,
        help="Directory containing extracted prebuilt emulator to locate netsim binary",
    )
    status_parser.set_defaults(parser=status_parser, func=run_mesh_status)

    # emu-dev-cli mesh teardown / stop ...
    for cmd_name in ("teardown", "stop"):
        td_parser = mesh_subparsers.add_parser(
            cmd_name,
            help="Gracefully stop mesh emulator instances and reset Netsim RF simulation",
        )
        td_parser.add_argument(
            "--serials",
            type=str,
            default=None,
            help="Comma-separated list of target serials (auto-discovers all online emulators if omitted)",
        )
        td_parser.add_argument(
            "--prefix",
            type=str,
            default=None,
            help="Prefix of the AVD mesh instances (used to compute serials if --serials is omitted)",
        )
        td_parser.add_argument(
            "--count",
            type=int,
            default=2,
            help="Number of mesh instances to stop (default: 2)",
        )
        td_parser.add_argument(
            "--base-port",
            type=int,
            default=5554,
            help="Base console port (default: 5554)",
        )
        td_parser.add_argument(
            "--all",
            action="store_true",
            help="Stop all connected emulator instances recognized by adb",
        )
        td_parser.add_argument(
            "--no-netsim-reset",
            action="store_true",
            help="Skip resetting Netsim RF device scene",
        )
        td_parser.add_argument(
            "--emulator-dir",
            type=str,
            default=None,
            help="Directory containing extracted prebuilt emulator to locate netsim binary",
        )
        td_parser.set_defaults(parser=td_parser, func=run_mesh_teardown)


def run_wait_ready(args):
    json_mode = getattr(args, "json", False)

    try:
        serials = _resolve_target_serials(args)
    except ValueError as e:
        print_result(
            {
                "status": "error",
                "action": "mesh wait-ready",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    timeout = getattr(args, "timeout", 180)
    poll_interval = getattr(args, "poll_interval", 2.0)
    unlock = not getattr(args, "no_unlock", False)
    verify_netsim = not getattr(args, "no_netsim_check", False)
    emu_dir = getattr(args, "emulator_dir", None)

    if not json_mode:
        print(f"⏳ Waiting for {len(serials)} mesh node(s) to boot: {', '.join(serials)} (timeout {timeout}s)...")
        sys.stdout.flush()

    res = wait_for_mesh_ready(
        serials=serials,
        timeout=timeout,
        poll_interval=poll_interval,
        unlock=unlock,
        verify_netsim=verify_netsim,
        emu_dir=emu_dir,
    )

    if not res["all_ready"]:
        pending = ", ".join(res["pending_serials"])
        print_result(
            {
                "status": "timeout",
                "action": "mesh wait-ready",
                "summary": f"Timed out after {res['elapsed_seconds']}s waiting for node(s): {pending}",
                "elapsed_seconds": res["elapsed_seconds"],
                "ready_nodes": res["ready_nodes"],
                "total_nodes": res["total_nodes"],
                "pending_serials": res["pending_serials"],
                "nodes": res["nodes"],
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    table_rows = []
    for node in res["nodes"]:
        s = node["serial"]
        booted = node.get("boot_completed", False)
        avd_name = node.get("avd_name") or get_device_avd_name(s)
        radios = node.get("radios") or (
            "Active" if res.get("netsim_active") else "N/A"
        )
        table_rows.append([
            s,
            avd_name or "(unknown)",
            "🟢 Ready" if booted else "⏳ Booting",
            radios,
        ])
    table_md = format_markdown_table(
        ["Serial", "AVD Name", "Boot Status", "Netsim Radios"], table_rows
    )

    summary_msg = f"All {res['total_nodes']} mesh node(s) are booted and ready in {res['elapsed_seconds']}s"
    print_result(
        {
            "status": "success",
            "action": "mesh wait-ready",
            "summary": summary_msg,
            "all_ready": True,
            "elapsed_seconds": res["elapsed_seconds"],
            "total_nodes": res["total_nodes"],
            "serials": serials,
            "nodes": res["nodes"],
            "netsim_active": res["netsim_active"],
            "netsim_devices": res["netsim_devices"],
            "markdown_table": table_md,
        },
        json_mode=json_mode,
    )


def run_mesh_status(args):
    json_mode = getattr(args, "json", False)
    raw_serials = getattr(args, "serials", None)
    if raw_serials:
        serials = [s.strip() for s in raw_serials.split(",") if s.strip()]
    else:
        serials = get_connected_adb_devices()

    if not serials:
        print_result(
            {
                "status": "success",
                "action": "mesh status",
                "summary": "No connected ADB emulator devices found",
                "total_nodes": 0,
                "serials": [],
                "nodes": [],
                "netsim_active": False,
            },
            json_mode=json_mode,
        )
        return

    emu_dir = getattr(args, "emulator_dir", None)
    netsim_bin = find_netsim_binary(emu_dir)
    netsim_data = get_netsim_devices(netsim_bin) if netsim_bin else None

    node_statuses = []
    table_rows = []
    for s in serials:
        booted = check_device_boot_completed(s)
        avd_name = get_device_avd_name(s)
        matched_netsim = (
            find_netsim_device_by_name(netsim_data, avd_name)
            if avd_name
            else None
        )
        radio_summary = (
            summarize_netsim_chips(matched_netsim)
            if matched_netsim
            else ("Active" if netsim_data else "N/A")
        )
        node_statuses.append({
            "serial": s,
            "avd_name": avd_name,
            "boot_completed": booted,
            "radios": radio_summary,
        })
        table_rows.append([
            s,
            avd_name or "(unknown)",
            "🟢 Ready" if booted else "⏳ Booting",
            radio_summary,
        ])

    table_md = format_markdown_table(
        ["Serial", "AVD Name", "Boot Status", "Netsim Radios"], table_rows
    )

    summary_msg = f"Mesh Status ({len(serials)} node{'s' if len(serials) != 1 else ''} online)"
    print_result(
        {
            "status": "success",
            "action": "mesh status",
            "summary": summary_msg,
            "total_nodes": len(serials),
            "serials": serials,
            "nodes": node_statuses,
            "netsim_active": netsim_data is not None,
            "netsim_devices": netsim_data,
            "markdown_table": table_md,
        },
        json_mode=json_mode,
    )


def run_mesh_teardown(args):
    json_mode = getattr(args, "json", False)

    try:
        serials = _resolve_target_serials(args, allow_auto_adb=True)
    except ValueError as e:
        print_result(
            {
                "status": "error",
                "action": "mesh teardown",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    if not serials:
        print_result(
            {
                "status": "success",
                "action": "mesh teardown",
                "summary": "No mesh emulator instances found to stop",
                "total_nodes": 0,
                "total_stopped": 0,
                "stopped_serials": [],
                "netsim_reset": False,
                "nodes": [],
            },
            json_mode=json_mode,
        )
        return

    emu_dir = getattr(args, "emulator_dir", None)
    reset_netsim = not getattr(args, "no_netsim_reset", False)

    res = teardown_mesh(
        serials=serials,
        reset_netsim=reset_netsim,
        emu_dir=emu_dir,
    )

    table_rows = []
    for node in res["nodes"]:
        s = node["serial"]
        avd_name = node.get("avd_name") or "(unknown)"
        stopped = node.get("stopped", False)
        table_rows.append([
            s,
            avd_name,
            "🛑 Stopped" if stopped else "❌ Failed",
        ])

    table_md = format_markdown_table(
        ["Serial", "AVD Name", "Status"], table_rows
    )

    summary_msg = f"Tore down {res['total_stopped']}/{res['total_nodes']} mesh node(s)"
    if res.get("netsim_reset"):
        summary_msg += " & reset Netsim scene"

    print_result(
        {
            "status": "success",
            "action": "mesh teardown",
            "summary": summary_msg,
            "total_nodes": res["total_nodes"],
            "total_stopped": res["total_stopped"],
            "serials": serials,
            "stopped_serials": res["stopped_serials"],
            "netsim_reset": res["netsim_reset"],
            "nodes": res["nodes"],
            "markdown_table": table_md,
        },
        json_mode=json_mode,
    )
