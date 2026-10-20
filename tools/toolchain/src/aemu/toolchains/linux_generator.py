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

from aemu.toolchains.toolchain_generator import ToolchainGenerator
from pathlib import Path
from typing import Tuple, Dict


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
            compat_lib_dir = self.compat_lib.resolve().parent
        elif (self.aosp / "third_party" / "qemu" / "google" / "compat").exists():
            assert(self.bazel != None)
            self.with_compat = True
            self.bazel.build_target(self.COMPAT_ARCHIVE)
            compat_lib_dir = self.bazel.get_archive(self.COMPAT_ARCHIVE).parent

        self.linux_sys_root = (
            self.aosp
            / "prebuilts"
            / "gcc"
            / "linux-x86"
            / "host"
            / "x86_64-linux-glibc2.17-4.8"
        )
        # GCC_DIR = TOOLCHAIN_DIR / "lib" / "gcc" / "x86_64-linux" / "4.8.3"
        self.system_root = self.linux_sys_root / "sysroot"
        linux_lib_path = version_path / "lib" / "linux"
        lib_path = self.clang() / "lib" / "x86_64-unknown-linux-gnu"
        include_path = version_path / "include"

        self.cflags = (
            f"--gcc-toolchain={self.linux_sys_root} "
            f"-B{self.linux_sys_root}/lib/gcc/x86_64-linux/4.8.3/ "
            f"-L{self.linux_sys_root}/lib/gcc/x86_64-linux/4.8.3/ "
            f"-L{self.linux_sys_root}/x86_64-linux/lib64/ "
            "-fuse-ld=lld -Wl,--allow-shlib-undefined "
            f"-L{linux_lib_path} "
            f"-L{include_path} "
            f"-L{lib_path} "
            f"--sysroot={self.system_root} "
            f"-Wl,-rpath,'$ORIGIN/lib64:$ORIGIN:{self.clang() / 'lib' / 'x86_64-unknown-linux-gnu'}' "
        )

        if compat_lib_dir:
            # TODO(whollins): We should pass this in for the Qemu build (or set in json config).
            compat_isystem_path = (
                self.aosp
                / "third_party"
                / "qemu"
                / "google"
                / "compat"
                / "linux"
                / "include"
            ).absolute()
            self.cflags += f"-L{compat_lib_dir} -isystem {compat_isystem_path} "

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
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(self.system_root)
