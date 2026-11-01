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

import argparse

import os
import sys

MIN_PYTHON = (3, 10)
if sys.version_info < MIN_PYTHON:
    sys.exit(
        f"Error: emu-dev-cli requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
        f"(matching the Bazel hermetic runtime).\n"
        f"Detected Python {sys.version.split()[0]} at {sys.executable}.\n"
        "Please install or switch to Python 3.10+ (e.g. via 'brew install python@3.11' or pyenv)."
    )

REAL_FILE = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(REAL_FILE)
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
_path_dirs = [os.path.join(SCRIPT_DIR, "lib"), SCRIPT_DIR]
if os.path.isdir(os.path.join(ROOT_DIR, "workflows")):
    _path_dirs.insert(0, ROOT_DIR)
for _dir in _path_dirs:
    if _dir in sys.path:
        sys.path.remove(_dir)
    if os.path.exists(_dir):
        sys.path.insert(0, _dir)

from commands import (
    build_times,
    crash,
    create,
    cts,
    docs,
    fetch_build,
    flakiness,
    gerrit,
    harness,
    init_cmd,
    launch,
    mesh,
    perf,
    sbom,
    screencast,
    source_directory,
    tidy,
    update_cmd,
    update_prebuilt,
    workflow,
)
from core.command import COMMAND_INSTANCE_ATTR
from core.context import detect_host_platform
from core.dispatcher import CliDispatcher
from install import installer
from lib.logging_config import setup_logging
from lib.output import ensure_utf8_stdio, print_result
from lib.version import UNKNOWN_COMMIT, format_version_display, read_installed_version


def detect_default_host():
    return detect_host_platform()


def _handle_version_flag(args) -> None:
    """Prints installed emu-dev-cli version metadata and exits."""
    ensure_utf8_stdio()
    release_dir = installer.get_release_package_directory(None)
    installed = read_installed_version(release_dir)
    if getattr(args, "json", False):
        payload = {
            "status": "success",
            "action": "version",
            "installed": installed is not None,
            "release_directory": release_dir,
        }
        if installed:
            payload.update(installed)
            payload["summary"] = (
                f"emu-dev-cli {installed.get('commit_short', UNKNOWN_COMMIT)}"
            )
        else:
            payload["commit"] = UNKNOWN_COMMIT
            payload["commit_short"] = UNKNOWN_COMMIT
            payload["summary"] = "No installed version record found"
        print_result(payload, json_mode=True)
    else:
        print(format_version_display(installed))
    sys.exit(0)


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="emu-dev-cli", description="Android Emulator Developer Assistant CLI"
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show installed emu-dev-cli version information and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in machine-readable JSON format",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose sub-command output and DEBUG logging",
    )
    parser.add_argument(
        "--log-file",
        help="Path to write execution log trace file for debugging",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    default_host = detect_default_host()

    # Register subcommands
    build_times.register_parser(subparsers)
    crash.register_parser(subparsers)
    create.register_parser(subparsers)
    cts.register_parser(subparsers)
    docs.register_parser(subparsers)
    fetch_build.register_parser(subparsers, default_host)
    flakiness.register_parser(subparsers)
    gerrit.register_parser(subparsers)
    harness.register_parser(subparsers)
    init_cmd.register_parser(subparsers)
    installer.register_parser(subparsers)
    launch.register_parser(subparsers)
    mesh.register_parser(subparsers)
    perf.register_parser(subparsers)
    sbom.register_parser(subparsers)
    screencast.register_parser(subparsers)
    source_directory.register_parser(subparsers)
    tidy.register_parser(subparsers)
    update_cmd.register_parser(subparsers)
    update_prebuilt.register_parser(subparsers, default_host)
    workflow.register_parser(subparsers)

    args = parser.parse_args()

    setup_logging(
        verbose=getattr(args, "verbose", False),
        log_file=getattr(args, "log_file", None),
    )

    if getattr(args, "version", False):
        _handle_version_flag(args)

    if not args.subcommand or not hasattr(args, COMMAND_INSTANCE_ATTR):
        parser.print_help()
        sys.exit(1)

    exit_code = CliDispatcher().dispatch(args)
    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
