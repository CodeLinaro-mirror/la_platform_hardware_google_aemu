#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
import tempfile
import logging
import shutil
import platform
import re
from typing import List, Optional

from pathlib import Path

from aemu.configure.meson_project_builder import MesonProjectBuilder
from aemu.log import configure_logging


def get_build_dir(base_dir: str) -> Path:
    """Gets the build directory.

    Args:
        base_dir: The base directory.

    Returns:
        The build directory.
    """
    return Path(base_dir) / "build"


def _post_process_config_host(path: Path, prefix: str) -> None:
    if not path.exists():
        return
    logging.info("Post-processing %s to replace prefix %s", path.as_posix(), prefix)
    content = path.read_text(encoding="utf-8")
    content = content.replace(prefix, "rel_dir")

    # On macOS, force undefine HAVE_STRCHRNUL to maintain compatibility with macOS < 15.4.
    # The SDK has it, but Meson's weak-link detection is bypassed by ld64.lld not supporting -no_weak_imports.
    if "darwin" in path.as_posix() or "mac" in path.as_posix():
        content = re.sub(r"#define HAVE_STRCHRNUL\b.*", "#undef HAVE_STRCHRNUL", content)

    path.write_text(content, encoding="utf-8")


def bazel_command(args: argparse.Namespace) -> None:
    assert args.out.exists()

    # Shared toolchain
    target = args.target
    toolchain_dir = args.prebuilt_toolchain

    if "NINJA" in os.environ:
        ninja_path = Path(os.environ["NINJA"])
        if not ninja_path.is_absolute():
            os.environ["NINJA"] = (args.aosp / ninja_path).resolve().as_posix()

    pc_dirs = [
        pkg.resolve().as_posix() + "/pc-config" for pkg in args.prebuilt_packages
    ]

    is_windows = target.startswith("windows")
    if is_windows:
        pkg_path = ";".join(pc_dirs)
    else:
        pkg_path = ":".join(pc_dirs)
    os.environ["PKG_CONFIG_PATH"] = pkg_path

    # Setup shadow build (ninja)
    temp_build = tempfile.TemporaryDirectory(prefix="shadow")
    with tempfile.TemporaryDirectory(prefix="bazel") as shadow_build_dir:
        shadow_build_dir = Path(shadow_build_dir).resolve()
        builder = MesonProjectBuilder(
            config_file=args.config,
            aosp=args.aosp,
            dest=get_build_dir(shadow_build_dir),
            toolchain_dir=toolchain_dir,
            ccache=args.ccache,
            generator=None,
            bazel_startup_options=None,
            bazel_build_options=None,
            target=target,
        )
        builder.configure_meson([])

        # Setup bazel generator build
        # Make sure there are no accidentally symlinks that cause
        # issues when trying to find dependencies
        bazel_build_dir = Path(args.out).resolve()
        builder = MesonProjectBuilder(
            config_file=args.config,
            aosp=args.aosp,
            dest=get_build_dir(bazel_build_dir),
            toolchain_dir=toolchain_dir,
            ccache=args.ccache,
            generator=None,
            bazel_startup_options=None,
            bazel_build_options=None,
            target=target,
        )

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonc") as f:
            shim_path = Path(f.name)
        builder.write_shim_file(shim_path)
        shim_file = shim_path.absolute()

        try:
            extra_args = [
                "--backend",
                "bazel",
                f"-Dbackend_shadow_build={get_build_dir(shadow_build_dir).as_posix()}",
                f"-Dbackend_shim={shim_file.as_posix()}",
            ]
            # Prevent diff in platform/linux-x86_64/config-host.h
            if target == "linux-x64":
                extra_args.append("--libdir=lib/x86_64-linux-gnu")
            elif target.startswith("windows"):
                extra_args.append("--libdir=lib")
            elif target.startswith("mac"):
                extra_args.append("--libdir=lib")
            builder.configure_meson(extra_args)
        except Exception as e:
            import traceback
            traceback.print_exc()
            exit(1)
        finally:
            # Only delete if we generated a temporary file
            shim_path.unlink()

        bazel_dir = get_build_dir(bazel_build_dir) / "bazel"
        build_file = bazel_dir / "BUILD.bazel"
        if not build_file.exists():
            logging.error("BUILD.bazel does not exist")
            exit(1)

        logging.info("BUILD.bazel exists as: %s", build_file.as_posix())
        platform_dir = bazel_dir / "platform"
        if not platform_dir.exists():
            logging.error("Platform directory %s does not exist", platform_dir.as_posix())
            exit(1)

        # TODO: we currently have 3 different target names used in different
        # places. We should standardise on one!
        suffix = {"linux-x64": "linux-x86_64", "windows-x64": "windows-x86_64", "mac-aarch64": "darwin-arm64"}.get(target)
        dest = platform_dir / f"BUILD.{suffix}"
        logging.info("Copying BUILD.bazel to %s", dest.as_posix())
        shutil.copy2(build_file, dest)

        subdirs = [p.name for p in platform_dir.iterdir() if p.is_dir()]
        if not subdirs:
            logging.error("No platform subdirectory found in %s", platform_dir.as_posix())
            exit(1)

        prefix_path = (get_build_dir(bazel_build_dir) / "release").as_posix()
        for suffix in subdirs:
            config_host = platform_dir / suffix / "config-host.h"
            _post_process_config_host(config_host, prefix_path)

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate bazel from meson")
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        default=False,
        action="store_true",
        help="Verbose logging",
    )
    parser.add_argument("--aosp", required=True, type=Path, help="AOSP root path")
    parser.add_argument("--target", required=True, type=str, help="Target platform")
    parser.add_argument(
        "--out", required=True, type=Path, help="Directory final bazel config output"
    )
    parser.add_argument(
        "--config",
        required=True,
        type=str,
        help="Path to the build-config.jsonc file for the project.",
    )
    parser.add_argument(
        "--ccache",
        dest="ccache",
        default=shutil.which("ccache") or shutil.which("sccache"),
        help="Use the given compiler cache (ccache/sccache)",
    )
    parser.add_argument(
        "--prebuilt-toolchain",
        type=Path,
        help="Path to prebuilt toolchain directory to use instead of generating it.",
    )
    parser.add_argument(
        "--prebuilt-packages",
        action="append",
        type=Path,
        default=[],
        help="Path to prebuilt packages directory. Can be specified multiple times.",
    )

    args = parser.parse_args()

    lvl = logging.DEBUG if args.verbose else logging.INFO
    configure_logging(lvl)

    bazel_command(args)


if __name__ == "__main__":
    main()
