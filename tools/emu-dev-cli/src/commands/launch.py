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

import os
import platform
import re
import subprocess
import sys
import time
from lib.avd import get_mesh_node_names
from lib.emulator import (
    calculate_mesh_ports,
    find_cached_emulator_dir,
    is_headless_launch,
    prepare_environment,
    resolve_emulator_executable,
    spawn_emulator_process,
)
from lib.output import print_result


def get_display_owner_label(disp_num):
    sock_path = f"/tmp/.X11-unix/X{disp_num}"
    try:
        res = subprocess.run(
            ["fuser", sock_path], capture_output=True, text=True, check=False
        )
        pids = res.stdout.strip().split()
        if pids:
            pid = pids[0]
            comm_file = f"/proc/{pid}/comm"
            if os.path.exists(comm_file):
                with open(comm_file, "r", encoding="utf-8") as f:
                    proc_name = f.read().strip()
                if "Xorg" in proc_name:
                    return f"{proc_name} (Chrome Remote Desktop / Desktop Session)"
                elif "nxnode" in proc_name or "nx" in proc_name.lower():
                    return f"{proc_name} (NoMachine)"
                elif "vnc" in proc_name.lower():
                    return f"{proc_name} (VNC)"
                return f"{proc_name} (PID {pid})"
    except Exception:
        pass
    return "Active X11 Display Server"


def check_display_health(env, emu_args, json_mode=False):
    """
    Check beforehand if DISPLAY points to an active socket (unless -no-window is given).
    If a display issue is detected, print a high-signal warning with owner labels,
    but always proceed with launching the emulator.
    """
    if platform.system().lower() != "linux":
        return

    if is_headless_launch(emu_args):
        return

    current_disp = env.get("DISPLAY", "")
    disp_num = None
    if current_disp:
        m = re.match(r"^:?(\d+)(?:\.\d+)?$", current_disp.strip())
        if m:
            disp_num = m.group(1)

    socket_path = f"/tmp/.X11-unix/X{disp_num}" if disp_num is not None else None
    if socket_path and os.path.exists(socket_path):
        return  # Active display socket exists, good to go!

    # Active socket not found for current DISPLAY. Scan available /tmp/.X11-unix/X* sockets.
    active_displays = []
    if os.path.isdir("/tmp/.X11-unix"):
        try:
            for fname in os.listdir("/tmp/.X11-unix"):
                if fname.startswith("X") and fname[1:].isdigit():
                    num = int(fname[1:])
                    label = get_display_owner_label(num)
                    active_displays.append((num, f":{num}", label))
        except Exception:
            pass

    active_displays.sort(key=lambda item: item[0])
    disp_str = current_disp if current_disp else "(not set)"

    if not json_mode:
        print("⚠️  DISPLAY WARNING:")
        print(
            f"   Current DISPLAY='{disp_str}' has no active socket file at"
            f" /tmp/.X11-unix/X{disp_num or 0}."
        )
        if active_displays:
            print("   Detected active X11 display socket(s) on machine:")
            for _, d_str, owner_desc in active_displays:
                print(f"     • DISPLAY={d_str:<6} [{owner_desc}]")
            suggested_disp = active_displays[0][1]
            for num, d_str, owner_desc in active_displays:
                if (
                    "chrome" in owner_desc.lower()
                    or "xorg" in owner_desc.lower()
                    or num == 20
                ):
                    suggested_disp = d_str
                    break
            print(
                "   If Qt XCB fails to connect to display, try running: export"
                f" DISPLAY={suggested_disp}"
            )
        else:
            print("   No active X11 display sockets found in /tmp/.X11-unix/.")
            print(
                "   If launch fails with Qt XCB error, ensure your display"
                " server is running or pass '-no-window'."
            )
        print("   Proceeding to launch emulator...")
        print("-" * 50)
        sys.stdout.flush()


def register_parser(subparsers):
    launch_parser = subparsers.add_parser(
        "launch", help="Launch developer tools (emulator, mesh, cts-verifier, etc.)"
    )
    launch_parser.set_defaults(
        func=lambda args: launch_parser.print_help() or sys.exit(0)
    )
    launch_subparsers = launch_parser.add_subparsers(
        dest="launch_cmd", help="Resource type to launch"
    )

    # emu-dev-cli launch emulator ...
    emulator_parser = launch_subparsers.add_parser(
        "emulator",
        help=(
            "Launch an Android Emulator using prebuilt emulator binaries and"
            " forwarding arguments"
        ),
    )
    emulator_parser.add_argument(
        "--emulator-dir",
        type=str,
        default=None,
        help="Directory containing extracted prebuilt emulator (auto-discovered if omitted)",
    )
    emulator_parser.add_argument(
        "--detached",
        action="store_true",
        help="Launch emulator as an independent daemon background process and exit immediately",
    )
    emulator_parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Custom log file path when running with --detached (defaults to /tmp/emulator_<pid>.log)",
    )
    emulator_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved executable, library paths, and command without executing",
    )
    emulator_parser.add_argument(
        "emulator_args",
        nargs="*",
        help="Arguments passed directly to emulator (use '--' before options, e.g. -- -avd my-avd)",
    )
    emulator_parser.set_defaults(
        parser=emulator_parser, func=run_launch_emulator
    )

    # emu-dev-cli launch mesh ...
    mesh_parser = launch_subparsers.add_parser(
        "mesh",
        help="Concurrently launch a mesh of N isolated emulator instances with non-overlapping ports",
    )
    mesh_parser.add_argument(
        "--emulator-dir",
        type=str,
        default=None,
        help="Directory containing extracted prebuilt emulator (auto-discovered if omitted)",
    )
    mesh_parser.add_argument(
        "--prefix",
        type=str,
        default="medium_phone",
        help="Prefix of the AVD mesh instances (e.g. 'bt-mesh')",
    )
    mesh_parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of mesh emulator instances to launch (default: 2)",
    )
    mesh_parser.add_argument(
        "--base-port",
        type=int,
        default=5554,
        help="Base console port for the first instance (default: 5554)",
    )
    mesh_parser.add_argument(
        "--packet-streamer",
        type=str,
        default="default",
        help="Packet streamer endpoint for Netsim/Bluetooth radio mesh sync (default: 'default')",
    )
    mesh_parser.add_argument(
        "--no-window",
        action="store_true",
        help="Run emulator instances headlessly without graphical window",
    )
    mesh_parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Directory to store output logs for each mesh node (defaults to /tmp/mesh_<prefix>_<ts>)",
    )
    mesh_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved node configurations and launch commands without executing",
    )
    mesh_parser.add_argument(
        "emulator_args",
        nargs="*",
        help="Additional emulator options forwarded to all instances (use '--', e.g. -- -gpu swiftshader_indirect)",
    )
    mesh_parser.set_defaults(parser=mesh_parser, func=run_launch_mesh)


def _handle_missing_emulator_error(json_mode: bool, cmd_name: str = "emulator"):
    err_msg = (
        "No emulator directory specified (--emulator-dir) and no cached"
        " emulator was found in /tmp.\n\n💡 To download a prebuilt emulator"
        " binary, run:\n   emu-dev-cli fetch-build emulator --latest\n\n  "
        f" Then re-run launch:\n   emu-dev-cli launch {cmd_name} ...\n"
    )
    if json_mode:
        print_result(
            {
                "status": "error",
                "action": f"launch {cmd_name}",
                "error_message": (
                    "No emulator directory specified and no cached emulator"
                    " found in /tmp."
                ),
                "suggestion": "emu-dev-cli fetch-build emulator --latest",
                "exit_code": 1,
            },
            json_mode=True,
            is_error=True,
        )
    else:
        print(f"❌ Error: {err_msg}")
    sys.exit(1)


def run_launch_emulator(args):
    json_mode = getattr(args, "json", False)
    emu_args = getattr(args, "emulator_args", []) or []

    if emu_args and emu_args[0] == "--":
        emu_args = emu_args[1:]

    try:
        emu_bin, emu_dir = resolve_emulator_executable(args.emulator_dir)
    except (ValueError, FileNotFoundError) as e:
        if not args.emulator_dir and not find_cached_emulator_dir():
            _handle_missing_emulator_error(json_mode, "emulator")
        print_result(
            {
                "status": "error",
                "action": "launch emulator",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    env = prepare_environment(emu_dir)
    check_display_health(env, emu_args, json_mode=json_mode)

    full_cmd = [emu_bin] + emu_args

    if args.dry_run:
        print_result(
            {
                "status": "dry_run",
                "action": "launch emulator",
                "emulator_executable": emu_bin,
                "emulator_dir": emu_dir,
                "detached_mode": args.detached,
                "display": env.get("DISPLAY", ""),
                "ld_library_path": env.get("LD_LIBRARY_PATH", ""),
                "full_command": full_cmd,
            },
            json_mode=json_mode,
        )
        return

    # Detached daemon launch mode
    if args.detached:
        log_file_path = args.log_file
        if not log_file_path:
            ts = int(time.time())
            log_file_path = f"/tmp/emulator_{ts}.log"
        else:
            log_file_path = os.path.abspath(os.path.expanduser(log_file_path))

        proc = spawn_emulator_process(
            full_cmd, emu_dir, env, log_file_path=log_file_path, detached=True
        )

        print_result(
            {
                "status": "success",
                "action": "launch emulator (detached)",
                "summary": (
                    f"Started emulator daemon (PID {proc.pid}) on"
                    f" DISPLAY={env.get('DISPLAY')}"
                ),
                "pid": proc.pid,
                "display": env.get("DISPLAY", ""),
                "log_file": log_file_path,
                "emulator_executable": emu_bin,
                "emulator_dir": emu_dir,
                "full_command": full_cmd,
            },
            json_mode=json_mode,
        )
        return

    # Foreground interactive launch mode
    if not json_mode:
        print(f"🚀 Launching Emulator: {emu_bin}")
        print(f"   Using prebuilt directory: {emu_dir}")
        print(f"   Display: DISPLAY={env.get('DISPLAY', '(none)')}")
        if emu_args:
            print(f"   Arguments: {' '.join(emu_args)}")
        print("-" * 50)
        sys.stdout.flush()

    try:
        res = subprocess.run(full_cmd, env=env, cwd=emu_dir, check=False)
        sys.exit(res.returncode)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as ex:
        print_result(
            {
                "status": "error",
                "action": "launch emulator",
                "error_message": f"Failed to execute emulator: {ex}",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)


def run_launch_mesh(args):
    json_mode = getattr(args, "json", False)
    count = getattr(args, "count", 2) or 2
    prefix = getattr(args, "prefix", "medium_phone") or "medium_phone"
    base_port = getattr(args, "base_port", 5554) or 5554
    packet_streamer = getattr(args, "packet_streamer", "default")
    no_window = getattr(args, "no_window", False)
    dry_run = getattr(args, "dry_run", False)

    extra_args = getattr(args, "emulator_args", []) or []
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    try:
        port_assignments = calculate_mesh_ports(
            base_port=base_port, count=count
        )
    except ValueError as e:
        print_result(
            {
                "status": "error",
                "action": "launch mesh",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    try:
        emu_bin, emu_dir = resolve_emulator_executable(args.emulator_dir)
    except (ValueError, FileNotFoundError) as e:
        if not args.emulator_dir and not find_cached_emulator_dir():
            _handle_missing_emulator_error(json_mode, "mesh")
        print_result(
            {
                "status": "error",
                "action": "launch mesh",
                "error_message": str(e),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    env = prepare_environment(emu_dir)
    check_display_health(env, extra_args + (["-no-window"] if no_window else []), json_mode=json_mode)

    avd_names = get_mesh_node_names(prefix, count)

    ts = int(time.time())
    log_dir = args.log_dir or f"/tmp/mesh_{prefix}_{ts}"
    log_dir = os.path.abspath(os.path.expanduser(log_dir))
    if not dry_run:
        os.makedirs(log_dir, exist_ok=True)

    node_results = []
    for i, port_info in enumerate(port_assignments):
        avd_name = avd_names[i]
        c_port = port_info["console_port"]
        a_port = port_info["adb_port"]
        serial = port_info["serial"]

        node_cmd = [
            emu_bin,
            "-avd",
            avd_name,
            "-ports",
            f"{c_port},{a_port}",
        ]
        if packet_streamer:
            node_cmd.extend(["-packet-streamer-endpoint", packet_streamer])
        if no_window and "-no-window" not in extra_args and "-headless" not in extra_args:
            node_cmd.append("-no-window")
        if extra_args:
            node_cmd.extend(extra_args)

        log_file = os.path.join(log_dir, f"{avd_name}_{c_port}.log")

        if dry_run:
            node_results.append({
                "index": port_info["index"],
                "avd_name": avd_name,
                "serial": serial,
                "console_port": c_port,
                "adb_port": a_port,
                "log_file": log_file,
                "command": node_cmd,
            })
        else:
            proc = spawn_emulator_process(
                node_cmd, emu_dir, env, log_file_path=log_file, detached=True
            )
            node_results.append({
                "index": port_info["index"],
                "avd_name": avd_name,
                "serial": serial,
                "console_port": c_port,
                "adb_port": a_port,
                "pid": proc.pid,
                "log_file": log_file,
                "command": node_cmd,
            })

    summary_msg = (
        f"{'Planned' if dry_run else 'Launched'} mesh of {count} emulator"
        f" instances with prefix '{prefix}' on ports {base_port}..{port_assignments[-1]['console_port']}"
    )

    result_payload = {
        "status": "dry_run" if dry_run else "success",
        "action": "launch mesh",
        "summary": summary_msg,
        "count": count,
        "prefix": prefix,
        "packet_streamer_endpoint": packet_streamer,
        "nodes": node_results,
        "serials": [n["serial"] for n in node_results],
        "log_directory": log_dir,
        "emulator_executable": emu_bin,
        "emulator_dir": emu_dir,
    }
    print_result(result_payload, json_mode=json_mode)
