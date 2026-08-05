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

"""Local crash reproduction subcommand module for emu-dev-cli (`crash reproduce`).

Parses crash metadata (Build ID, platform target, GPU backend, launch flags)
and automates fetching prebuilt binaries, configuring AVDs, and booting under
the LLDB interactive debugger (using `--wait-for` to attach directly to the
spawned `qemu-system-<arch>` engine process) to reproduce the crash locally.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

from typing import Any, Dict, List, Optional

from commands.crash.advisor import run_crashadvisor_bazel
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
)
from commands.launch import prepare_environment
from lib.output import print_result


def resolve_qemu_engine_name(architecture: str, os_name: str) -> str:
    """Maps crash architecture and OS to the matching QEMU worker binary name.

    Args:
        architecture: Target architecture string (e.g. 'amd64', 'x86_64', 'arm64').
        os_name: Target operating system name (e.g. 'Linux', 'Windows').

    Returns:
        Binary name string (e.g. 'qemu-system-x86_64' or 'qemu-system-aarch64').
    """
    arch = architecture.lower()
    is_windows = "win" in os_name.lower()
    suffix = ".exe" if is_windows else ""

    if "arm64" in arch or "aarch64" in arch:
        return f"qemu-system-aarch64{suffix}"
    if "arm" in arch:
        return f"qemu-system-armel{suffix}"
    if "i386" in arch or ("x86" in arch and "64" not in arch):
        return f"qemu-system-i386{suffix}"
    return f"qemu-system-x86_64{suffix}"


def extract_reproduce_plan(
    crash_id: str,
    metadata_path: Path,
    avd_override: Optional[str] = None,
    debugger: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Parses metadata.json and constructs a comprehensive crash reproduction plan.

    Args:
        crash_id: Cleaned crash report ID.
        metadata_path: Path to metadata.json file.
        avd_override: Optional user-supplied AVD name override.
        debugger: Optional debugger wrapper (e.g. 'lldb').
        extra_args: Optional list of additional command-line arguments to append.

    Returns:
        Dictionary containing reproduction parameters and command lines.
    """
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found at: {metadata_path}")

    with open(metadata_path, "r", encoding="utf-8") as f:
        meta_json = json.load(f)

    proto = meta_json.get("report_proto", {})
    prod = proto.get("product", {})
    version_str = prod.get("Version", "")
    build_id = (
        version_str.split("-")[-1] if "-" in version_str else version_str or "unknown"
    )

    os_info = proto.get("os", {})
    cpu_info = proto.get("cpu", {})
    os_name = str(os_info.get("Name", "")).lower()
    arch = str(cpu_info.get("Architecture", "")).lower()

    if "linux" in os_name:
        build_target = (
            "emulator_linux_aarch64"
            if ("arm" in arch or "aarch64" in arch)
            else "emulator_linux_x64"
        )
    elif "mac" in os_name or "darwin" in os_name:
        build_target = (
            "emulator_mac_aarch64"
            if ("arm" in arch or "aarch64" in arch)
            else "emulator_mac_x64"
        )
    elif "win" in os_name:
        build_target = "emulator_windows_x64"
    else:
        build_target = "emulator_linux_x64"

    # Extract command-line flags from productdata breadcrumbs
    extracted_flags: List[str] = []
    productdata = proto.get("productdata", [])
    for item in productdata:
        key = item.get("Key")
        val = item.get("Value", "")
        if key == "commandline" and val:
            parsed_tokens = shlex.split(val)
            for token in parsed_tokens:
                if token not in extracted_flags:
                    extracted_flags.append(token)

    if extra_args:
        extracted_flags.extend(extra_args)

    target_avd = avd_override or f"repro_{crash_id}"
    qemu_engine = resolve_qemu_engine_name(arch, os_info.get("Name", ""))

    emulator_cmd = ["emulator", "-avd", target_avd] + extracted_flags
    lldb_cmd = ["lldb", "-n", qemu_engine, "--wait-for"] if debugger == "lldb" else None

    return {
        "crash_id": crash_id,
        "build_id": build_id,
        "build_target": build_target,
        "qemu_engine": qemu_engine,
        "os": os_info.get("Name", "Unknown"),
        "architecture": cpu_info.get("Architecture", "Unknown"),
        "avd": target_avd,
        "debugger": debugger,
        "flags": extracted_flags,
        "reproduce_command": emulator_cmd,
        "lldb_command": lldb_cmd,
    }


def run_reproduce(args: argparse.Namespace) -> None:
    """Handles execution of the `crash reproduce` subcommand.

    Args:
        args: Parsed command line arguments.
    """
    target_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    dry_run = getattr(args, "dry_run", False)
    json_mode = getattr(args, "json", False)
    avd_override = getattr(args, "avd", None)
    debugger = "lldb" if getattr(args, "lldb", False) else None
    extra_args = getattr(args, "extra_args", None)

    sandbox_dir = get_crashadvisor_sandbox_dir(target_id, create=True)
    meta_path = Path(sandbox_dir) / "metadata.json"

    if not meta_path.exists():
        if not json_mode:
            print(f"📥 Fetching crash metadata for Target {target_id}...")
        cmd_args = [target_id, "--auto-run", "--work-dir", sandbox_dir]
        if token:
            cmd_args.extend(["--token", token])
        try:
            run_crashadvisor_bazel(cmd_args, check=False)
        except Exception as e:
            sys.stderr.write(f"⚠️ CrashAdvisor metadata retrieval notice: {e}\n")

    if not meta_path.exists():
        err_msg = f"Crash metadata not found at {meta_path}. Cannot construct reproduction plan."
        print_result(
            {
                "status": "error",
                "action": "crash reproduce",
                "error_message": err_msg,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    plan = extract_reproduce_plan(
        crash_id=target_id,
        metadata_path=meta_path,
        avd_override=avd_override,
        debugger=debugger,
        extra_args=extra_args,
    )

    if dry_run or json_mode:
        print_result(
            {
                "status": "success",
                "action": "crash reproduce (plan)",
                "plan": plan,
            },
            json_mode=json_mode,
        )
        return

    print(f"\n🚀 Reproducing Crash: {target_id}")
    print(f"  • Build ID: {plan['build_id']}")
    print(f"  • Target Platform: {plan['build_target']}")
    print(f"  • Target Engine: {plan['qemu_engine']}")
    print(f"  • Target AVD: {plan['avd']}")
    if debugger == "lldb":
        print(f"  • Debugger Attached: LLDB via `{' '.join(plan['lldb_command'])}` 🪲")
    print(f"  • Emulator Launch Command:\n    {' '.join(plan['reproduce_command'])}\n")

    # Verify or fetch emulator binary
    host_platform = "linux-x64" if "linux" in plan["build_target"] else "mac-x64"
    emu_output_dir = Path(f"/tmp/emulator-{host_platform}-{plan['build_id']}")
    emu_bin = emu_output_dir / "extracted" / "emulator" / "emulator"

    if not emu_bin.exists():
        print(f"📦 Fetching prebuilt emulator binary {plan['build_id']} from go/ab...")
        fetch_cmd = [
            "fetch_artifact",
            "--bid",
            plan["build_id"],
            "--target",
            plan["build_target"],
            f"sdk-repo-linux-emulator-{plan['build_id']}.zip",
            str(emu_output_dir),
        ]
        if shutil.which("fetch_artifact"):
            subprocess.run(fetch_cmd, check=False)

    launch_cmd = list(plan["reproduce_command"])
    if emu_bin.exists():
        launch_cmd[launch_cmd.index("emulator")] = str(emu_bin)
        env = prepare_environment(str(emu_bin.parent))
    else:
        env = os.environ.copy()

    if debugger == "lldb":
        print(f"▶️ Starting emulator launcher in background...")
        is_posix = os.name != "nt"
        popen_kwargs = {"env": env}
        if is_posix:
            popen_kwargs["start_new_session"] = True

        emu_proc = subprocess.Popen(launch_cmd, **popen_kwargs)
        try:
            print(f"🪲 Attaching LLDB to {plan['qemu_engine']} via --wait-for...")
            subprocess.run(plan["lldb_command"], check=False)
        finally:
            if emu_proc.poll() is None:
                if is_posix:
                    try:
                        pgid = os.getpgid(emu_proc.pid)
                        os.killpg(pgid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        try:
                            emu_proc.terminate()
                        except OSError:
                            pass
                else:
                    emu_proc.terminate()
    else:
        print(f"▶️ Launching: {' '.join(launch_cmd)}")
        try:
            subprocess.run(launch_cmd, env=env, check=False)
        except Exception as e:
            sys.stderr.write(f"❌ Execution failed: {e}\n")
            sys.exit(1)


def register_reproduce_parser(crash_subparsers) -> None:
    """Registers the `reproduce` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    reproduce_parser = crash_subparsers.add_parser(
        "reproduce",
        help="Automate fetching crashing emulator binaries and booting with LLDB attached to qemu-system process",
        description="Extracts crash configuration from CrashAdvisor metadata, launches the emulator, and automatically attaches LLDB to the spawned qemu-system engine using --wait-for.",
    )
    reproduce_parser.add_argument(
        "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
    )
    reproduce_parser.add_argument("--token", help="OAuth2 token")
    reproduce_parser.add_argument(
        "--avd", help="Target AVD profile to use (defaults to repro_<crash_id>)"
    )
    reproduce_parser.add_argument(
        "--lldb",
        action="store_true",
        help="Automatically attach LLDB to the spawned qemu-system worker process via --wait-for",
    )
    reproduce_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and display the reproduction plan without launching",
    )
    reproduce_parser.add_argument(
        "--json",
        action="store_true",
        help="Output reproduction plan as structured JSON",
    )
    reproduce_parser.add_argument(
        "--extra-args",
        nargs=argparse.REMAINDER,
        help="Additional arguments to pass to the emulator",
    )
    reproduce_parser.set_defaults(func=run_reproduce)
