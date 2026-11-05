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

import logging
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

from clients.artifact_service import _extract_selected_zip_entries
from commands.crash.advisor import CrashAdvisorRunner
from commands.crash.utils import (
    CrashMetadata,
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
    resolve_crash_work_dir,
)
from commands.launch import prepare_environment
from core.command import Command, CommandResult, bind_command
from core.context import (
    ExecutionContext,
    SERVICE_ARTIFACT,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import CommandValidationError, ExecutionError, UsageError
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


def _resolve_emulator_archive_name(build_target: str, build_id: str) -> str:
    """Maps an Android Build target to its platform-specific `sdk-repo-*-emulator-<bid>.zip` archive name."""
    target_lower = (build_target or "").lower()
    if "darwin_aarch64" in target_lower or "mac_aarch64" in target_lower:
        platform_slug = "darwin_aarch64"
    elif "darwin" in target_lower or "mac" in target_lower:
        platform_slug = "darwin"
    elif "win" in target_lower:
        platform_slug = "windows"
    elif "linux_aarch64" in target_lower or "linux-aarch64" in target_lower:
        platform_slug = "linux_aarch64"
    else:
        platform_slug = "linux"
    return f"sdk-repo-{platform_slug}-emulator-{build_id}.zip"


def _resolve_emulator_binary_path(extracted_dir: Path, build_target: str) -> Path:
    """Returns the extracted emulator binary path inside `extracted_dir`."""
    is_windows_target = "win" in (build_target or "").lower() or os.name == "nt"
    exe_candidate = extracted_dir / "emulator" / "emulator.exe"
    posix_candidate = extracted_dir / "emulator" / "emulator"
    if is_windows_target and exe_candidate.is_file():
        return exe_candidate
    if posix_candidate.is_file():
        return posix_candidate
    return exe_candidate if is_windows_target else posix_candidate


class CrashReproduceCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash reproduce`."""

    name = "reproduce"
    help_text = (
        "Automate fetching crashing emulator binaries and booting with LLDB attached to qemu-system process"
    )
    help = help_text
    description = (
        "Extracts crash configuration from CrashAdvisor metadata, launches the emulator, "
        "and automatically attaches LLDB to the spawned qemu-system engine using --wait-for."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
        parser.add_argument(
            "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
        )
        parser.add_argument("--token", help="OAuth2 token")
        parser.add_argument(
            "--avd", help="Target AVD profile to use (defaults to repro_<crash_id>)"
        )
        parser.add_argument(
            "--lldb",
            action="store_true",
            help="Automatically attach LLDB to the spawned qemu-system worker process via --wait-for",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Generate and display the reproduction plan without launching",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output reproduction plan as structured JSON",
        )
        parser.add_argument(
            "--work-dir",
            dest="work_dir",
            help="Optional explicit work directory containing metadata.json",
        )
        parser.add_argument(
            "--extra-args",
            nargs=argparse.REMAINDER,
            help="Additional arguments to pass to the emulator",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        target_id = parse_crash_id(raw_id) if raw_id else ""
        if not target_id:
            raise CommandValidationError(
                "Crash ID cannot be empty for 'crash reproduce'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        meta = CrashMetadata.from_args(args, ctx.config.cache_dir)
        target_id = meta.crash_id
        dry_run = bool(getattr(args, "dry_run", False))
        avd_override = getattr(args, "avd", None)
        debugger = "lldb" if getattr(args, "lldb", False) else None
        extra_args = getattr(args, "extra_args", None)

        sandbox_path = meta.work_dir
        meta_path = meta.metadata_path
        if not meta_path.is_file():
            token = acquire_auth_token(getattr(args, "token", None))
            advisor_runner = CrashAdvisorRunner()
            advisor_runner.run(
                ctx,
                target_id,
                sandbox_path,
                flags=["--auto-run"],
                token=token,
                check=False,
            )
            meta = meta.refresh()

        if not meta_path.is_file():
            raise ExecutionError(
                f"Crash metadata not found at '{meta_path}'. Cannot construct reproduction plan.",
                remediation="Verify the Crash ID exists or provide a work directory containing metadata.json via --work-dir.",
            )

        plan = extract_reproduce_plan(
            crash_id=target_id,
            metadata_path=meta_path,
            avd_override=avd_override,
            debugger=debugger,
            extra_args=extra_args,
        )

        if dry_run or ctx.config.json_mode or getattr(args, "json", False):
            return CommandResult(
                data={
                    "status": "success",
                    "action": "crash reproduce (plan)",
                    "plan": plan,
                },
                action="crash reproduce (plan)",
                summary=f"Constructed crash reproduction plan for {target_id}.",
            )

        artifact_service = ctx.services.get(SERVICE_ARTIFACT)
        build_id = str(plan["build_id"])
        build_target = str(plan["build_target"])
        emu_output_dir = ctx.config.cache_dir / f"emulator-{build_id}"
        extracted_dir = emu_output_dir / "extracted"
        emu_bin = _resolve_emulator_binary_path(extracted_dir, build_target)
        if not emu_bin.is_file() and artifact_service is not None:
            artifact_name = _resolve_emulator_archive_name(build_target, build_id)
            try:
                if hasattr(artifact_service, "download_artifact"):
                    artifact_service.download_artifact(
                        build_id=build_id,
                        target=build_target,
                        artifact=artifact_name,
                        dest_dir=extracted_dir,
                        zip_entries=("emulator/*",),
                    )
                    emu_bin = _resolve_emulator_binary_path(extracted_dir, build_target)
                    if not emu_bin.is_file():
                        for cand_zip in (extracted_dir / artifact_name, emu_output_dir / artifact_name):
                            if cand_zip.is_file():
                                _extract_selected_zip_entries(cand_zip, extracted_dir, ("emulator/*",))
                                emu_bin = _resolve_emulator_binary_path(extracted_dir, build_target)
                                break
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "Failed downloading or extracting emulator artifact '%s' for build %s (%s): %s",
                    artifact_name,
                    build_id,
                    build_target,
                    exc,
                )

        launch_cmd = list(plan["reproduce_command"])
        if emu_bin.is_file() and "emulator" in launch_cmd:
            launch_cmd[launch_cmd.index("emulator")] = str(emu_bin)

        reproduce_timeout_sec = 3600.0
        if debugger == "lldb":
            is_posix = os.name != "nt"
            popen_kwargs: dict[str, Any] = {}
            if is_posix:
                popen_kwargs["start_new_session"] = True

            emu_proc = subprocess.Popen(launch_cmd, **popen_kwargs)
            try:
                ctx.process_runner.run(
                    plan["lldb_command"],
                    check=False,
                    timeout_sec=reproduce_timeout_sec,
                )
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
            ctx.process_runner.run(
                launch_cmd, check=False, timeout_sec=reproduce_timeout_sec
            )

        return CommandResult(
            data={
                "status": "success",
                "action": "crash reproduce",
                "plan": plan,
            },
            action="crash reproduce",
            summary=f"Executed crash reproduction for {target_id}.",
        )


def register_reproduce_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `reproduce` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    return CrashReproduceCommand().register(crash_subparsers)
