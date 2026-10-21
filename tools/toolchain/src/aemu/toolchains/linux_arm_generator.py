# -*- coding: utf-8 -*-
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import configparser
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Tuple

from aemu.toolchains.toolchain_generator import ToolchainGenerator
from aemu.util import safe_link_tree


class LinuxToLinuxAarch64Generator(ToolchainGenerator):
    """A cross compilation toolchain configurator using clang

    It will try to use the hardcoded version if available, otherwise
    it will fallback to the default one.
    """

    GCC_VER = 10
    GCC_PREFIX = "aarch64-linux-gnu-"

    def __init__(
        self, aosp: Path, dest: Path, prefix: str, versions: Dict[str, str] = None
    ) -> None:
        """Initializes a LinuxToLinuxAarch64Generator object.

        Args:
            aosp: The path to the AOSP source tree.
            dest: The destination directory for the toolchain.
            prefix: The prefix for the toolchain binaries.
        """
        super().__init__(aosp, dest, prefix, versions)
        self.target_arch = "aarch64"

    def _fetch_toolchain(self) -> Path:
        """Fetches the toolchain and sysroot by dynamically discovering paths via Bazel queries.

        If `self.sysroot` is already provided (e.g. via hermetic Bazel rule inputs),
        its wrapper path is returned directly. Otherwise, this method automates the
        process of locating the cross-compilation sysroot via a series of Bazel queries:
        1.  **Identify the Toolchain:** It queries the `@aemu//tools/toolchain:hello` target
            to find which CC toolchain is actually being used (specifically the `cpp_link` dependency).
        2.  **Find the Sysroot Attribute:** It inspects the identified toolchain target to
            extract its `sysroot` attribute, which points to the Bazel target providing the sysroot.
        3.  **Resolve Physical Path:** It queries the location of that sysroot target to find
            the actual directory on disk (usually in the Bazel cache).
        4.  **Localize:** Finally, it copies the discovered sysroot to the toolchain's
            destination directory for use by the wrappers.

        Returns:
            Path: The path to the localized sysroot.
        """
        if self.sysroot:
            return self.wrapper_path(self.sysroot)

        # Make sure we have the sysroot.
        sysroot = Path(self.dest) / "sysroot"
        if not sysroot.exists():
            # This will pull down the sysroot if not available.
            self.bazel.build_target("@aemu//tools/toolchain:hello")

            # Get the toolchain dependency (cpp_link).
            # We use --transitions=lite to see the resolved toolchain dependency.
            # Output looks like: [toolchain dependency: cpp_link]#<target>#NoTransition
            output = self.bazel.cquery(
                "@aemu//tools/toolchain:hello",
                ["--transitions=lite", "--noimplicit_deps"],
            )
            match = re.search(r"cpp_link(?::|\]#)\s*([^\s#\(]+)", output)
            if not match:
                raise ValueError(f"Could not find toolchain dependency in cquery output: {output}")
            toolchain_target = match.group(1)

            # Get the sysroot target from the toolchain's attributes.
            # We output to jsonproto to easily parse the rule attributes.
            json_output = self.bazel.cquery(f"deps({toolchain_target})", ["--output=jsonproto"])
            data = json.loads(json_output)
            sysroot_target = None
            for result in data.get("results", []):
                for attr in result.get("target", {}).get("rule", {}).get("attribute", []):
                    if attr.get("name") == "sysroot":
                        sysroot_target = attr.get("stringValue")
                        break
                if sysroot_target:
                    break

            if not sysroot_target:
                raise ValueError(f"Could not find sysroot attribute for toolchain {toolchain_target}")

            # Get the physical location of the sysroot target on disk.
            # Bazel query --output=location returns the path to the BUILD file for the target.
            # Output looks like: /path/to/BUILD.bazel:54:8: sysroot rule ...
            location = self.bazel.query(sysroot_target, ["--output=location"])
            match = re.match(r"(.*)/BUILD(?:\.bazel)?:\d+:\d+", location)
            if not match:
                raise ValueError(f"Could not find location for sysroot target {sysroot_target} in: {location}")

            # Localize the sysroot by linking it to our destination.
            src = Path(match.group(1))
            logging.info("Linking sysroot from %s to %s", src, sysroot)
            safe_link_tree(src, sysroot)

        return sysroot

    def cc(self, clang="clang") -> Tuple[str, str]:
        """Generates the script for the C compiler."""
        cache = f"{self.ccache}" if self.ccache else ""
        toolchain = self._fetch_toolchain()
        sysroot = toolchain / "aarch64-none-linux-gnu" / "libc"

        script = (
            f"{cache} {self.clang()}/bin/{clang} "
            f"--target=aarch64-none-linux-gnu --sysroot={sysroot} "
            f"--gcc-toolchain={toolchain} -fuse-ld=lld -Wl,--allow-shlib-undefined"
        )
        extra = "-Wno-unused-command-line-argument -ldl -lm "
        return script, extra

    def cxx(self) -> Tuple[str, str]:
        """Generates the script for the C++ compiler."""
        script, extra = self.cc("clang++")
        return script, extra + "-lstdc++ "

    def write_toolchain_config(self) -> None:
        """Writes a cross-compilation toolchain configuration that can be consumed by meson."""
        super().write_toolchain_config()
        config = configparser.ConfigParser()
        config.read(self.dest / "aosp-cl.ini")
        if "properties" not in config:
            config["properties"] = {}
        config["properties"]["skip_sanity_check"] = "true"
        config["host_machine"] = {
            "system": "'linux'",
            "cpu_family": "'aarch64'",
            "cpu": "'aarch64'",
            "endian": "'little'",
        }
        with open(self.dest / "aosp-cl.ini", "w", encoding="utf-8") as f:
            f.write("# Auto generated by Android Meson Generator - do not modify\n")
            config.write(f)

    def rustc(self) -> Tuple[str, str]:
        """Gets the path to the rustc compiler."""
        return "echo <not yet implemented>", ""

    def cargo(self) -> Tuple[str, str]:
        """Gets the path to the cargo command."""
        return "echo <not yet implemented>", ""

    def strip(self) -> Tuple[str, str]:
        """Generates the script for the strip command."""
        objcopy = self.clang() / "bin" / "llvm-objcopy"
        script = "mkdir -p build/debug_info\n"
        script += "target=$(basename $1)\n"
        script += f'{objcopy} --only-keep-debug  $1 "build/debug_info/$target.debug" \n'
        script += f"{objcopy} --strip-unneeded  $1\n"
        script += f'{objcopy} --add-gnu-debuglink="build/debug_info/$target.debug" $1\n'
        script += "# EXPLICITLY DISABLED ARBITRARY ARGUMENTS: "
        return script, ""
