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
import platform
import sys

REAL_FILE = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(REAL_FILE)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
lib_dir = os.path.join(SCRIPT_DIR, "lib")
if os.path.exists(lib_dir) and lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from commands import (
    crash,
    create,
    cts,
    docs,
    fetch_build,
    flakiness,
    init_cmd,
    launch,
    mesh,
    source_directory,
    tidy,
    update_cmd,
)
from install import installer
from lib.logging_config import setup_logging


def detect_default_host():
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        return "linux-x64"
    elif system == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    elif system == "windows":
        return "windows-x64"
    return "linux-x64"


def main():
    parser = argparse.ArgumentParser(
        prog="emu-dev-cli", description="Android Emulator Developer Assistant CLI"
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
    crash.register_parser(subparsers)
    create.register_parser(subparsers)
    cts.register_parser(subparsers)
    docs.register_parser(subparsers)
    fetch_build.register_parser(subparsers, default_host)
    flakiness.register_parser(subparsers)
    init_cmd.register_parser(subparsers)
    installer.register_parser(subparsers)
    launch.register_parser(subparsers)
    mesh.register_parser(subparsers)
    source_directory.register_parser(subparsers)
    tidy.register_parser(subparsers)
    update_cmd.register_parser(subparsers)

    args = parser.parse_args()

    setup_logging(
        verbose=getattr(args, "verbose", False),
        log_file=getattr(args, "log_file", None),
    )

    if not args.subcommand or not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
