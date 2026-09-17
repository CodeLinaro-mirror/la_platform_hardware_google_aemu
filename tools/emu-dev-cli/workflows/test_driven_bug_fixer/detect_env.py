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

"""Helper script to detect repository environment using ~/.android/emu-dev-cli.json.

Resolves repository roots, target git paths, and build commands for:
- emu-main-dev: CMake incremental build in external/qemu
- emu-main-next: Bazel build in goldfish emulator workspace
"""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Dict, Optional


def get_config_file_path() -> str:
    """Resolves the config file path dynamically (respects EMU_DEV_CLI_CONFIG_PATH)."""
    return os.environ.get(
        "EMU_DEV_CLI_CONFIG_PATH",
        os.path.expanduser("~/.android/emu-dev-cli.json"),
    )


def load_config() -> Dict[str, Dict[str, str]]:
    """Loads the emu-dev-cli configuration file."""
    cfg_path = get_config_file_path()
    if not os.path.exists(cfg_path):
        return {"source_directories": {}}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"source_directories": {}}


def _is_subpath_or_equal(path: Path, base_path: Path) -> bool:
    try:
        path.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


def detect_environment(start_path: Path, branch_hint: Optional[str] = None) -> Dict[str, str]:
    """Detects repository environment using emu-dev-cli.json source_directories,

    with fallback to codebase marker inspection.
    """
    curr = start_path.resolve()
    config = load_config()
    source_dirs = config.get("source_directories", {})

    # 1. Check if branch_hint is specified
    if branch_hint and branch_hint in source_dirs and source_dirs[branch_hint]:
        b_root = Path(source_dirs[branch_hint]).resolve()
        if branch_hint == "emu-main-dev":
            return {
                "env-type": "emu-main-dev",
                "repo-root": str(b_root),
                "repo-dir": str(b_root / "external" / "qemu"),
                "compile-cmd": "cd external/qemu && python3 android/build/python/cmake.py --task Compile",
            }
        elif branch_hint == "emu-main-next":
            return {
                "env-type": "emu-main-next",
                "repo-root": str(b_root),
                "repo-dir": str(b_root),
                "compile-cmd": "bazel build @goldfish//emulator/launcher:launch_emulator",
            }

    # 2. Check if current working path is inside any registered source directory in emu-dev-cli.json
    dev_dir = source_dirs.get("emu-main-dev")
    if dev_dir and _is_subpath_or_equal(curr, Path(dev_dir)):
        b_root = Path(dev_dir).resolve()
        return {
            "env-type": "emu-main-dev",
            "repo-root": str(b_root),
            "repo-dir": str(b_root / "external" / "qemu"),
            "compile-cmd": "cd external/qemu && python3 android/build/python/cmake.py --task Compile",
        }

    next_dir = source_dirs.get("emu-main-next")
    if next_dir and _is_subpath_or_equal(curr, Path(next_dir)):
        b_root = Path(next_dir).resolve()
        return {
            "env-type": "emu-main-next",
            "repo-root": str(b_root),
            "repo-dir": str(b_root),
            "compile-cmd": "bazel build @goldfish//emulator/launcher:launch_emulator",
        }

    # 3. Fallback to filesystem marker inspection (for test environments or unregistered repos)
    candidates = [curr] + list(curr.parents)
    for p in candidates:
        # emu-main-dev root check: external/qemu/android/build/python/cmake.py
        if (p / "external" / "qemu" / "android" / "build" / "python" / "cmake.py").is_file():
            return {
                "env-type": "emu-main-dev",
                "repo-root": str(p),
                "repo-dir": str(p / "external" / "qemu"),
                "compile-cmd": "cd external/qemu && python3 android/build/python/cmake.py --task Compile",
            }

        # Subdirectory inside external/qemu
        if (p / "android" / "build" / "python" / "cmake.py").is_file() and p.name == "qemu":
            repo_root = p.parent.parent if p.parent.name == "external" else p
            return {
                "env-type": "emu-main-dev",
                "repo-root": str(repo_root),
                "repo-dir": str(p),
                "compile-cmd": "cd external/qemu && python3 android/build/python/cmake.py --task Compile",
            }

        # emu-main-next check: MODULE.bazel or WORKSPACE with goldfish
        if (p / "MODULE.bazel").is_file() or (p / "WORKSPACE").is_file():
            if (p / "hardware" / "generic" / "goldfish").is_dir() or (p / "hardware" / "google" / "aemu").is_dir():
                return {
                    "env-type": "emu-main-next",
                    "repo-root": str(p),
                    "repo-dir": str(p),
                    "compile-cmd": "bazel build @goldfish//emulator/launcher:launch_emulator",
                }

    # 4. Generic git checkout (for testing/mocking)
    for p in candidates:
        if (p / ".git").exists():
            return {
                "env-type": "generic",
                "repo-root": str(p),
                "repo-dir": str(p),
                "compile-cmd": "true",
            }

    return {
        "env-type": "unknown",
        "repo-root": str(curr),
        "repo-dir": str(curr),
        "compile-cmd": "true",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect repo root, compile command, and target git directory using emu-dev-cli.json."
    )
    parser.add_argument(
        "--target",
        choices=["repo-root", "compile-cmd", "repo-dir", "env-type"],
        required=True,
        help="Target metadata property to output.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Base working directory to detect from.",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Optional branch name hint (e.g. emu-main-dev, emu-main-next).",
    )
    parser.add_argument(
        "--compile-cmd-override",
        default="",
        help="Optional override for compile-cmd.",
    )
    args = parser.parse_args()

    start_path = Path(args.cwd) if args.cwd and args.cwd.strip() else Path.cwd()
    env_info = detect_environment(start_path, branch_hint=args.branch)

    if args.target == "compile-cmd" and args.compile_cmd_override and args.compile_cmd_override.strip():
        print(args.compile_cmd_override.strip())
        return 0

    val = env_info.get(args.target, "")
    print(val)
    return 0


if __name__ == "__main__":
    sys.exit(main())
