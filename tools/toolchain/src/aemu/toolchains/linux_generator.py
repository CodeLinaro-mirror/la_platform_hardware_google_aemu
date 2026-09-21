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

from pathlib import Path
from typing import Tuple, Dict

from aemu.toolchains.toolchain_generator import ToolchainGenerator, safe_symlink_dir


class LinuxToLinuxGenerator(ToolchainGenerator):
    """A toolchain generator for building on Linux for Linux."""

    COMPAT_ARCHIVE = "@qemu//google/compat/linux:compat"

    def __init__(
        self, aosp: Path, dest: Path, prefix: str, versions: Dict[str, str] = None
    ) -> None:
        """Initializes a LinuxToLinuxGenerator object.

        Args:
            aosp: The path to the AOSP source tree.
            dest: The destination directory for the toolchain.
            prefix: The prefix for the toolchain binaries.
        """
        super().__init__(aosp, dest, prefix, versions)
        self.target_arch = "x86_64"

    def initialize(self) -> None:
        """Initializes the toolchain generator."""
        if hasattr(self, "initialized"):
            return

        version_path = (self.clang() / "lib" / "clang" / self.cc_version()).absolute()

        if self.compat_lib:
            assert(self.bazel == None)
            self.with_compat = True
            compat_lib_dir = self.wrapper_path(self.compat_lib).parent
        elif self.bazel != None and (self.aosp / "third_party" / "qemu" / "google" / "compat").exists():
            self.with_compat = True
            self.bazel.build_target(self.COMPAT_ARCHIVE)
            compat_lib_dir = self.bazel.get_archive(self.COMPAT_ARCHIVE).parent
        else:
            self.with_compat = False
            compat_lib_dir = None

        # Resolve wrapper paths for sandboxed execution
        self.resolved_sysroot = self.wrapper_path(self.sysroot) if self.sysroot else (
            self.aosp
            / "prebuilts"
            / "gcc"
            / "linux-x86"
            / "host"
            / "x86_64-linux-glibc2.17-4.8"
            / "sysroot"
        )
        gcc_toolchain = self.resolved_sysroot.parent

        linux_lib_path = version_path / "lib" / "linux"
        lib_path = self.clang() / "lib" / "x86_64-unknown-linux-gnu"
        include_path = version_path / "include"

        self.cflags = (
            f"--gcc-toolchain={gcc_toolchain} "
            f"-B{gcc_toolchain}/lib/gcc/x86_64-linux/4.8.3/ "
            f"-L{gcc_toolchain}/lib/gcc/x86_64-linux/4.8.3/ "
            f"-L{gcc_toolchain}/x86_64-linux/lib64/ "
            "-fuse-ld=lld -Wl,--allow-shlib-undefined "
            f"-L{linux_lib_path} "
            f"-L{include_path} "
            f"-L{lib_path} "
            f"--sysroot={self.resolved_sysroot} "
            f"-Wl,-rpath,'$ORIGIN/lib64:$ORIGIN':{lib_path} "
        )

        if compat_lib_dir:
            compat_isystem = " ".join([f"-isystem {self.wrapper_path(p)}" for p in self.compat_includes])
            self.cflags += f"-L{compat_lib_dir} {compat_isystem} "

        self.initialized = True

    def strip(self) -> Tuple[str, str]:
        """Generates the script for the strip command."""
        objcopy = self.clang() / "bin" / "llvm-objcopy"
        script = "target=$(basename $1)\n"
        script += f'{objcopy} --only-keep-debug  $1 "build/debug_info/$target.debug" \n'
        script += f"{objcopy} --strip-unneeded  $1\n"
        script += f'{objcopy} --add-gnu-debuglink="build/debug_info/$target.debug" $1\n'
        script += "# EXPLICITLY DISABLED ARBITRARY ARGUMENTS: "
        return script, ""

    def cc(self) -> Tuple[str, str]:
        """Generates the script for the C compiler."""
        self.initialize()
        cache = f"{self.ccache}" if self.ccache else ""
        script = (
            f"{cache} {self.clang()}/bin/clang " "-m64 -march=x86-64 " f"{self.cflags} "
        )
        extra = "-Wno-unused-command-line-argument -lc++ -ldl "
        if self.with_compat:
            extra += "-lcompat "
        return script, extra

    def cxx(self) -> Tuple[str, str]:
        """Generates the script for the C++ compiler."""
        self.initialize()
        cache = f"{self.ccache}" if self.ccache else ""
        script = (
            f"{cache} {self.clang()}/bin/clang++ "
            "-m64 -march=x86-64 -stdlib=libc++ "
            f"{self.cflags} "
        )

        extra = "-Wno-unused-command-line-argument -lc++ -ldl "
        if self.with_compat:
            extra += "-lcompat "
        return script, extra

    def link_dirs(self) -> None:
        """Setup links to libc++.so etc.."""
        super().link_dirs()
        target = self.dest / "sysroot"
        safe_symlink_dir(self.resolved_sysroot, target)
