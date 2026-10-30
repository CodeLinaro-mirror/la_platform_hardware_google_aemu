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

"""Self-update command (`emu-dev-cli update`) with git version comparison."""

import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from commands.source_directory import (
    ensure_codesearch_urls_config,
    get_source_directory,
)
from install.installer import (
    detect_default_install_path,
    get_release_package_directory,
    install_launcher_wrapper,
    install_skill,
    resolve_built_artifact,
)
from lib.bazel import find_bazel_cmd
from lib.output import ensure_utf8_stdio, print_result, print_status
from lib.version import (
    compare_versions,
    format_commit_label,
    inspect_source_version,
    read_installed_version,
    write_installed_version,
)

logger = logging.getLogger(__name__)

BAZEL_TARGET: str = "@aemu//tools/emu-dev-cli:emu-dev-cli"
ADVISOR_BAZEL_TARGET: str = "@goldfish//emulator/crashreport/tool/advisor:advisor"
CRASHREPORT_BAZEL_TARGET: str = "@goldfish//emulator/crashreport/tool:crashreport"
DEFAULT_SOURCE_BRANCH: str = "emu-main-next"
DEFAULT_WORK_ROOT: str = "/work/emu-main-next"
CLI_BUILD_REL_PATH: tuple[str, ...] = (
    "hardware",
    "google",
    "aemu",
    "tools",
    "emu-dev-cli",
    "BUILD.bazel",
)


def _resolve_installer_hooks(source_dir: str):
    """Loads `(install_launcher_wrapper, install_skill)` from `source_dir` when present."""
    wrapper_fn = install_launcher_wrapper
    skill_fn = install_skill

    source_src_dir = os.path.join(
        source_dir, "hardware", "google", "aemu", "tools", "emu-dev-cli", "src"
    )
    source_installer = os.path.join(source_src_dir, "install", "installer.py")
    if not os.path.isfile(source_installer):
        return wrapper_fn, skill_fn

    try:
        if source_src_dir not in sys.path:
            sys.path.insert(0, source_src_dir)
        for pkg_name in ("commands", "install", "lib", "targets"):
            pkg_mod = sys.modules.get(pkg_name)
            pkg_dir = os.path.join(source_src_dir, pkg_name)
            if (
                pkg_mod is not None
                and hasattr(pkg_mod, "__path__")
                and os.path.isdir(pkg_dir)
                and pkg_dir not in pkg_mod.__path__
            ):
                pkg_mod.__path__.insert(0, pkg_dir)

        spec = importlib.util.spec_from_file_location(
            "_emu_dev_cli_source_installer", source_installer
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            wrapper_fn = getattr(mod, "install_launcher_wrapper", wrapper_fn)
            skill_fn = getattr(mod, "install_skill", skill_fn)
    except Exception as exc:
        logger.debug(
            "Falling back to bundled installer hooks after loading '%s' failed: %s",
            source_installer,
            exc,
        )
    return wrapper_fn, skill_fn


def _resolve_update_source_dir() -> str | None:
    """Resolves the local source workspace directory for `emu-dev-cli update`."""
    source_dir = get_source_directory(DEFAULT_SOURCE_BRANCH)
    if source_dir and os.path.exists(source_dir):
        return source_dir

    pwd = os.getcwd()
    if os.path.exists(os.path.join(pwd, *CLI_BUILD_REL_PATH)):
        return pwd
    if os.path.exists(os.path.join(DEFAULT_WORK_ROOT, *CLI_BUILD_REL_PATH)):
        return DEFAULT_WORK_ROOT
    return None


def _run_bazel_build(source_dir: str, verbose: bool, json_mode: bool) -> None:
    """Compiles emu-dev-cli and companion crash tools via Bazel, exiting on failure."""
    bazel_cmd = find_bazel_cmd(source_dir)
    print_status(
        f"🔨 Building {BAZEL_TARGET}, {ADVISOR_BAZEL_TARGET}, and "
        f"{CRASHREPORT_BAZEL_TARGET} in {source_dir}...",
        json_mode=json_mode,
    )
    cmd = [
        bazel_cmd,
        "build",
        BAZEL_TARGET,
        ADVISOR_BAZEL_TARGET,
        CRASHREPORT_BAZEL_TARGET,
    ]
    try:
        if verbose and not json_mode:
            res = subprocess.run(cmd, cwd=source_dir, check=False)
        else:
            res = subprocess.run(
                cmd,
                cwd=source_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        print_result(
            {
                "status": "error",
                "action": "update",
                "source_directory": source_dir,
                "error_message": (
                    f"Failed to invoke Bazel binary '{bazel_cmd}' in '{source_dir}': {exc}. "
                    "Verify Bazel is installed or prebuilts/bazel is synced in the workspace."
                ),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    if res.returncode != 0:
        err_msg = (
            getattr(res, "stderr", None)
            or f"Bazel build exited with code {res.returncode}"
        )
        print_result(
            {
                "status": "error",
                "action": "update",
                "source_directory": source_dir,
                "error_message": (
                    f"Failed to build {BAZEL_TARGET} in '{source_dir}' "
                    f"(exit code {res.returncode}): {err_msg}. "
                    "Run 'emu-dev-cli --verbose update' to inspect full compiler output."
                ),
                "exit_code": res.returncode,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(res.returncode)


def register_parser(subparsers):
    update_parser = subparsers.add_parser(
        "update",
        help="Rebuild emu-dev-cli from local emu-main-next source directory and re-install executable",
    )
    update_parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Destination executable path override",
    )
    update_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Rebuild and re-install even when emu-dev-cli is already up to date",
    )
    update_parser.set_defaults(func=run_update_cmd)


def run_update_cmd(args):
    ensure_utf8_stdio()
    json_mode = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)
    force = getattr(args, "force", False)
    dest_path = getattr(args, "path", None) or detect_default_install_path()

    source_dir = _resolve_update_source_dir()
    if not source_dir:
        print_result(
            {
                "status": "error",
                "action": "update",
                "error_message": (
                    f"No valid source directory found for branch '{DEFAULT_SOURCE_BRANCH}'. "
                    f"Use 'emu-dev-cli source-directory set {DEFAULT_SOURCE_BRANCH} <path>' first."
                ),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    release_dir = get_release_package_directory(dest_path)
    installed_version = read_installed_version(release_dir)
    source_version = inspect_source_version(source_dir)
    update_needed, reason = compare_versions(installed_version, source_version)

    if not update_needed:
        print_status(f"ℹ️ No update was needed: {reason}.", json_mode=json_mode)
        if not force:
            print_status("Use --force to rebuild anyway.", json_mode=json_mode)
            print_result(
                {
                    "status": "success",
                    "action": "update",
                    "update_needed": False,
                    "updated": False,
                    "summary": f"No update was needed ({reason})",
                    "source_directory": source_dir,
                    "launcher_path": str(dest_path),
                    "commit": source_version["commit"],
                    "commit_short": source_version["commit_short"],
                    "installed_version": installed_version,
                    "source_version": source_version,
                },
                json_mode=json_mode,
            )
        print_status(
            "🔄 Proceeding with forced rebuild (--force)...", json_mode=json_mode
        )
    else:
        print_status(f"ℹ️ Update is needed: {reason}.", json_mode=json_mode)

    _run_bazel_build(source_dir, verbose=verbose, json_mode=json_mode)

    built_bin = resolve_built_artifact(source_dir, BAZEL_TARGET, "emu-dev-cli")
    if not built_bin or not os.path.exists(built_bin):
        print_result(
            {
                "status": "error",
                "action": "update",
                "source_directory": source_dir,
                "built_bin": str(built_bin) if built_bin else None,
                "error_message": (
                    f"Built executable for {BAZEL_TARGET} in '{source_dir}' could not be resolved "
                    f"via Bazel cquery (resolved={built_bin!r}). Verify '{BAZEL_TARGET}' produces "
                    "an executable binary on this host."
                ),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    print_status(
        f"📦 Re-installing compiled emu-dev-cli release package from {built_bin}...",
        json_mode=json_mode,
    )
    wrapper_fn, skill_fn = _resolve_installer_hooks(source_dir)
    wrapper_fn(built_bin, dest_path, source_dir=source_dir)
    skill_files = skill_fn(source_dir=source_dir)
    ensure_codesearch_urls_config()
    updated_version = write_installed_version(
        release_dir, source_dir=source_dir, source_version=source_version
    )

    commit_label = format_commit_label(updated_version)
    print_status(
        f"✅ emu-dev-cli updated to {commit_label}.",
        json_mode=json_mode,
    )
    print_result(
        {
            "status": "success",
            "action": "update",
            "update_needed": update_needed,
            "updated": True,
            "reason": reason,
            "summary": (
                f"Successfully updated and re-installed emu-dev-cli ({commit_label}) "
                f"from {source_dir}"
            ),
            "source_directory": source_dir,
            "built_bin": built_bin,
            "launcher_path": str(dest_path),
            "commit": updated_version["commit"],
            "commit_short": updated_version["commit_short"],
            "dirty": updated_version["dirty"],
            "skill_files": skill_files,
            "version": updated_version,
        },
        json_mode=json_mode,
    )
