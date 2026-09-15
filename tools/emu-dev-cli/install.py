#!/usr/bin/env python3
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

"""Installation script for emu-dev-cli developer helper and agent skills."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


def find_source_root(start_dir: Path) -> Path:
    """Finds the root of the repo checkout containing hardware/google/aemu/tools/emu-dev-cli."""
    cur = start_dir.resolve()
    for parent in [cur] + list(cur.parents):
        if (parent / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli").exists():
            return parent
        if (parent / ".repo").exists():
            return parent
    if len(cur.parents) >= 5:
        return cur.parents[4]
    return cur


_SCRIPT_DIR = Path(__file__).resolve().parent
_SOURCE_ROOT = find_source_root(_SCRIPT_DIR)
_EMU_DEV_CLI_SRC = _SCRIPT_DIR / "src"
if _EMU_DEV_CLI_SRC.exists():
    sys.path.insert(0, str(_EMU_DEV_CLI_SRC))

try:
    from install import installer
    from commands.update_cmd import find_bazel_cmd
    from commands.source_directory import ensure_codesearch_urls_config
except ImportError:
    installer = None
    find_bazel_cmd = None
    ensure_codesearch_urls_config = None


def run_install(argv=None):
    parser = argparse.ArgumentParser(
        description="Build and install emu-dev-cli developer helper and skill."
    )
    parser.add_argument(
        "--install-path",
        "--install-emu-dev-cli",
        dest="install_path",
        nargs="?",
        const="",
        default=None,
        help="Install path for emu-dev-cli executable (defaults to auto-detected path).",
    )
    parser.add_argument(
        "--no-skill",
        "--skip-skill",
        action="store_true",
        dest="skip_skill",
        help="Skip installing the emu_dev_cli agent skill.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        "--force",
        action="store_true",
        dest="force_yes",
        help="Skip interactive confirmation prompts.",
    )
    # Deprecated flags accepted for backwards compatibility
    parser.add_argument(
        "--level",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--emu-dev-cli-only",
        "--only-emu-dev-cli",
        action="store_true",
        dest="emu_dev_cli_only",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--disable-agents-skills",
        "--disable-all",
        action="store_true",
        dest="disable_agents_skills",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    source_root = _SOURCE_ROOT

    print("-" * 50)
    print(f"Setting up emu-dev-cli at: {source_root}")
    print("-" * 50)

    if not installer:
        sys.stderr.write(f"ERROR: Could not import emu-dev-cli installer modules from: {_EMU_DEV_CLI_SRC}\n")
        return 1

    default_install_path = installer.detect_default_install_path()

    if args.install_path is not None and args.install_path != "":
        install_path_str = args.install_path
    elif args.install_path == "":
        install_path_str = default_install_path
    elif args.force_yes or not sys.stdin.isatty():
        install_path_str = default_install_path
    else:
        print("\nInstall emu-dev-cli developer helper globally?")
        try:
            prompt = f"Specify installation binary path [{default_install_path}] (or press Enter to accept): "
            user_input = input(prompt).strip()
            install_path_str = user_input if user_input else default_install_path
        except EOFError:
            install_path_str = default_install_path

    print("\nBuilding emu-dev-cli with Bazel...")
    bazel_cmd = find_bazel_cmd(str(source_root)) if find_bazel_cmd else "bazel"
    targets = [
        "@aemu//tools/emu-dev-cli:emu-dev-cli",
        "@goldfish//emulator/crashreport/tool/advisor:advisor",
        "@goldfish//emulator/crashreport/tool:crashreport",
    ]
    res = subprocess.run([bazel_cmd, "build"] + targets, cwd=source_root, check=False)
    if res.returncode != 0:
        print("Retrying build with primary emu-dev-cli target...")
        res = subprocess.run([bazel_cmd, "build", "@aemu//tools/emu-dev-cli:emu-dev-cli"], cwd=source_root, check=False)
        if res.returncode != 0:
            sys.stderr.write("  ! Error: Bazel build failed for emu-dev-cli.\n")
            return res.returncode

    built_bin = None
    if hasattr(installer, "resolve_built_artifact"):
        resolved = installer.resolve_built_artifact(
            str(source_root), "@aemu//tools/emu-dev-cli:emu-dev-cli", "emu-dev-cli"
        )
        if resolved and os.path.exists(resolved):
            built_bin = Path(resolved)

    if not built_bin or not built_bin.exists():
        sys.stderr.write("  ! Error: Could not locate built emu-dev-cli artifact via Bazel cquery.\n")
        return 1

    dest_path = Path(install_path_str)
    final_installed = None
    try:
        installer.install_launcher_wrapper(str(built_bin), str(dest_path), source_dir=str(source_root))
        final_installed = dest_path
        print(f"  + Installed emu-dev-cli global launcher to {dest_path}")
    except PermissionError:
        print(f"  + Permission denied for {dest_path}. Attempting to escalate with sudo...")
        if installer.install_launcher_with_sudo(str(built_bin), str(dest_path)):
            final_installed = dest_path
            print(f"  + Installed emu-dev-cli global launcher to {dest_path} (via sudo)")
        else:
            fallback_path = Path(installer.get_fallback_install_path())
            installer.install_launcher_wrapper(str(built_bin), str(fallback_path))
            final_installed = fallback_path
            print(f"  ! Sudo escalation failed; installed to fallback location: {fallback_path}")

    if not args.skip_skill:
        skill_files = installer.install_skill(source_dir=str(source_root))
        for sf in skill_files:
            print(f"  + Installed skill: {sf}")

    if ensure_codesearch_urls_config:
        try:
            ensure_codesearch_urls_config()
        except Exception:
            pass

    if final_installed:
        installer.print_path_instructions(final_installed)

    print("-" * 50)
    print(f"Done. emu-dev-cli installed to {final_installed or dest_path}")
    print("-" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(run_install())
