#!/usr/bin/env python3
import argparse
import platform
import sys
from commands import create, fetch_build, init_cmd, launch, source_directory
from install import installer


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
        prog="emu-dev-cli",
        description="Android Emulator Developer Assistant CLI"
    )
    parser.add_argument("--json", action="store_true", help="Output results in machine-readable JSON format")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose sub-command output")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    default_host = detect_default_host()

    # Register subcommands
    create.register_parser(subparsers)
    fetch_build.register_parser(subparsers, default_host)
    init_cmd.register_parser(subparsers)
    installer.register_parser(subparsers)
    launch.register_parser(subparsers)
    source_directory.register_parser(subparsers)

    args = parser.parse_args()
    if not args.subcommand or not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
