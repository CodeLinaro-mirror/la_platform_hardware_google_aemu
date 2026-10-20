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
from pathlib import Path

from aemu.toolchains.package_config_pc import PackageConfigPc
from aemu.toolchains.toolchain_generator import ToolchainGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pkg-config package using AMC implementation.")
    parser.add_argument("--name", required=True, type=str, help="Package name")
    parser.add_argument("--bazel_label", required=True, type=str, help="bazel label")
    parser.add_argument("--version", required=True, type=str, help="Package version")
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    parser.add_argument("--archives", nargs="*", default=[], type=Path, help="Archive files")
    parser.add_argument("--includes", nargs="*", default=[], type=Path, help="Include paths")
    parser.add_argument("--headers", nargs="*", default=[], type=Path, help="Header files")
    parser.add_argument("--requires", action="append", default=[], help="Requires")
    parser.add_argument("--link-flags", default="", type=str, help="Extra link flags")
    parser.add_argument("--cflags", default="", type=str, help="Extra cflags")

    args = parser.parse_args()

    # Construct the shim dictionary as expected by PackageConfigPc
    shim = {}
    # Note these shims are currently unsupported: name, extra_vars, Libs, link_name, dll_ext
    if args.requires:
        shim["Requires"] = ", ".join(args.requires)
    if args.link_flags:
        shim["link_flags"] = args.link_flags
    if args.cflags:
        shim["cflags"] = args.cflags

    out_dir = args.out
    release_dir = out_dir / "release"
    packages_dir = out_dir / ToolchainGenerator.PACKAGES_DIR
    pc_config_dir = out_dir / ToolchainGenerator.PKGCFG_DIR

    # Convert includes list to a set as expected by PackageConfigPc
    includes_set = set(args.includes) if args.includes else None

    # Instantiate the AMC PackageConfigPc class
    cfg = PackageConfigPc(
        name=args.name,
        version=args.version,
        release_dir=release_dir,
        archives=args.archives,
        includes=includes_set,
        shim=shim,
        target=args.bazel_label,
        headers=args.headers,
    )

    # Re-use the AMC binary placement, persistence, and writing logic
    if not cfg.is_static():
        cfg.binplace(args.out)

    cfg.persist(packages_dir)
    cfg.write(pc_config_dir)


if __name__ == "__main__":
    main()
