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

from pathlib import Path
from aemu.toolchains.factory import get_toolchain_generator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate toolchain wrappers.")
    parser.add_argument("--aosp", required=True, type=Path, help="AOSP root path")
    parser.add_argument(
        "--out", required=True, type=Path, help="Destination directory"
    )
    parser.add_argument("--target", required=True, type=str, help="Target platform")
    parser.add_argument(
        "--ninja", required=True, type=Path, help="Path to prebuilt ninja"
    )
    parser.add_argument(
        "--pkg-config", required=True, type=Path, help="Path to prebuilt pkg-config"
    )
    parser.add_argument(
        "--compat-lib", type=Path, help="Path to prebuilt compat library"
    )
    parser.add_argument(
        "--compat-include", action="append", default=[], type=Path, help="Path to compat include directory"
    )
    parser.add_argument(
        "--meson", type=Path, help="Path to prebuilt meson"
    )
    parser.add_argument(
        "--clang", type=Path, help="Path to prebuilt clang"
    )
    parser.add_argument(
        "--sysroot", type=Path, help="Path to the sysroot dir"
    )
    parser.add_argument(
        "--python", type=Path, help="Path to the python interpreter"
    )
    parser.add_argument(
        "--versions", required=True, type=Path, help="Path to tool_versions.json"
    )

    args = parser.parse_args()

    assert args.versions.exists()

    with open(
        args.versions,
        "r",
        encoding="utf-8",
    ) as f:
        versions = json.load(f)

    generator = get_toolchain_generator(
        target=args.target,
        toolchain_dir=args.out,
        prefix="",
        aosp=args.aosp,
        versions=versions,
        ninja_bin=args.ninja,
        pkg_config_bin=args.pkg_config,
        compat_lib=args.compat_lib,
        compat_includes=args.compat_include,
        meson_bin=args.meson,
        clang_bin=args.clang,
        sysroot=args.sysroot,
        python_bin=args.python,
    )

    # We only want to generate the toolchain, not the packages (and binaries wouldn't work anyway is it depends on calling Bazel).
    generator.gen_toolchain(packages=[], binaries={})


if __name__ == "__main__":
    main()
