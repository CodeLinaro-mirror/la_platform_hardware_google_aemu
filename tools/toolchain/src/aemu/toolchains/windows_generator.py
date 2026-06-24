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
import json
import logging
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Any, Callable

from aemu.toolchains.toolchain_generator import ToolchainGenerator
from aemu.toolchains.mingw_to_msvc_lib import convert_mingw_to_msvc_lib


class VisualStudioNotFoundException(Exception):
    """Raised when Visual Studio is not found."""

    pass


class VisualStudioMissingVarException(Exception):
    """Raised when a required Visual Studio environment variable is missing."""

    pass


class VisualStudioNativeWorkloadNotFoundException(Exception):
    """Raised when the Visual Studio native workload is not found."""

    pass


class WindowsToWindowsGenerator(ToolchainGenerator):
    """A toolchain generator for building on Windows for Windows."""

    COMPAT_ARCHIVE = "@aemu//windows:compat"

    def __init__(
        self, aosp: Path, dest: Path, prefix: str, versions: Dict[str, str] = None
    ) -> None:
        """Initializes a WindowsToWindowsGenerator object.

        Args:
            aosp: The path to the AOSP source tree.
            dest: The destination directory for the toolchain.
            prefix: The prefix for the toolchain binaries.
        """
        super().__init__(aosp, dest, prefix, versions)
        self.target_arch = "x86_64"
        self.env: Dict[str, str] = {}
        for key in os.environ:
            self.env[key.upper()] = os.environ[key]
        logging.info("Starting environment: %s", self.env)

        self.mingw_dir = (
            self.aosp
            / "prebuilts"
            / "gcc"
            / "linux-x86"
            / "host"
            / "x86_64-w64-mingw32-4.8"
        )
        self.rust_bin_dir = (
            self.aosp
            / "prebuilts"
            / "rust"
            / f"{self.host()}-x86"
            / self.rust_version()
            / "bin"
        )
        self.compat_path = (
            (self.aosp / "hardware" / "google" / "aemu" / "windows" / "includes")
            .absolute()
            .as_posix()
        )

    def tool_exe_extension(self) -> str:
        return ".exe"

    def initialize(self) -> None:
        """Initializes the toolchain generator."""
        if hasattr(self, "initialized"):
            return
        self._load_visual_studio_env()
        logging.info("Loaded visual studio env: %s", self.env)

        if self.compat_lib:
            assert(self.bazel == None)
            self.compat_lib_dir = self.compat_lib.resolve().parent
        else:
            assert(self.bazel != None)
            self.bazel.build_target(self.COMPAT_ARCHIVE)
            self.compat_lib_dir = self.bazel.get_archive(self.COMPAT_ARCHIVE).parent

        self.initialized = True

    def _load_visual_studio_env(self) -> None:
        """Loads the Visual Studio environment variables."""
        vs = self._visual_studio()
        logging.info("Loading environment from %s", vs)
        env_lines = subprocess.check_output(
            [str(vs), "&&", "set"], encoding="utf-8"
        ).splitlines()
        for line in env_lines:
            if "=" in line:
                key, val = line.split("=", 1)
                # Variables in windows are case insensitive, but not in python dict!
                self.env[key.upper()] = val

        if "VSINSTALLDIR" not in self.env:
            raise VisualStudioMissingVarException(
                f"Missing VSINSTALLDIR in environment, got {self.env}"
            )

        if "VCTOOLSINSTALLDIR" not in self.env:
            raise VisualStudioMissingVarException(
                f"Missing VCTOOLSINSTALLDIR in environment, got {self.env}"
            )

    def _get_toolchain_config(self) -> str:
        """Returns the toolchain configuration."""
        result = super()._get_toolchain_config()
        result += "\n"
        result += "[host_machine]\n"
        result += "system = 'windows'\n"
        result += "cpu_family = 'x86_64'\n"
        result += "cpu = 'x86_64'\n"
        result += "endian = 'little'\n"
        return result

    def _visual_studio(self) -> Path:
        """Finds the visual studio installation

        Raises:
            VisualStudioNotFoundException: When visual studio was not found
            VisualStudioNativeWorkloadNotFoundException: When the native workload was not found

        Returns:
            Path: The path to the vcvars64.bat file.
        """
        prgrfiles = Path(os.getenv("ProgramFiles(x86)", "C:\\Program Files (x86)"))
        res = subprocess.check_output(
            [
                str(
                    prgrfiles / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
                ),
                "-requires",
                "Microsoft.VisualStudio.Workload.NativeDesktop",
                "-sort",
                "-format",
                "json",
                "-utf8",
            ],
            encoding="utf-8",
        )
        vsresult = json.loads(res)
        if len(vsresult) == 0:
            raise VisualStudioNativeWorkloadNotFoundException(
                f"No visual studio with the native desktop load available in {res}"
            )

        for install in vsresult:
            logging.debug("Considering %s", install["displayName"])
            candidates = list(Path(install["installationPath"]).glob("**/vcvars64.bat"))

            if len(candidates) > 0:
                return candidates[0].absolute()

        # Oh oh, no visual studio..
        raise VisualStudioNotFoundException(
            f"Unable to detect a visual studio installation with the native desktop workload from {res}."
        )

    def gen_script(
        self, name: str, location: Path, cmd_generator_fn: Callable[[], Tuple[str, str]]
    ) -> None:
        """Generates a script.

        Args:
            name: The name of the script.
            location: The path to the script.
            cmd_generator_fn: A function that returns the command and extra arguments.
        """
        run, extra = cmd_generator_fn()
        if not run:
            logging.info("Skipping tool %s because it is empty", name)
            return

        # The C/C++ compiler wrappers need to be Python scripts mapped as list definitions in aosp-cl.ini.
        # This completely bypasses the cmd.exe 8,191-character limit on Windows (CreateProcess supports 32k),
        # which is easily exceeded when Meson passes long lists of absolute include directories.
        if name in ["ninja", "cc", "c++", "clang", "clang++", "gcc", "g++"]:
            py_script = location.with_suffix(".py")
            logging.info("Generating Python compiler wrapper %s", py_script)
            self.toolchain_map[name] = [sys.executable.replace("\\", "/"), py_script.absolute().as_posix()]

            import shlex
            tokens = shlex.split(str(run), posix=False)
            target_exe = tokens[0].strip('"')
            target_extra = [x.strip('"') for x in tokens[1:]]

            if extra:
                target_extra.extend([x.strip('"') for x in shlex.split(extra, posix=False)])

            env_sets = []
            if "LIB" in self.env:
                env_sets.append(f'os.environ["LIB"] = r"{self.env["LIB"]}"')
            if "INCLUDE" in self.env:
                env_sets.append(f'os.environ["INCLUDE"] = r"{self.env["INCLUDE"]}"')

            env_section = "\n".join(env_sets) + "\n" if env_sets else ""

            script = f"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Auto-generated by {Path(__file__).resolve()}, DO NOT EDIT!!
# Wrapper to bypass cmd.exe 8,191 character limit on Windows.
import os
import subprocess
import sys

{env_section}
cmd = [r"{target_exe}", *sys.argv[1:], *{repr(target_extra)}]
sys.exit(subprocess.call(cmd))
"""
            py_script.write_text(script, encoding="utf-8")
            return

        # Generate windows version of the scripts.
        current_file = Path(__file__).resolve()
        exe = location.with_suffix(".cmd")
        logging.info("Generating %s", exe)
        self.toolchain_map[name] = exe.absolute().as_posix()

        rem = "rem "
        run, extra = cmd_generator_fn()
        params = "%*"

        script = f"""@echo off
{rem} Auto-generated by {current_file}, DO NOT EDIT!!
{run} {params} {extra}
"""
        with open(exe, "w", encoding="utf-8") as f:
            f.write(script)

        exe.chmod(0o755)

    def _generate_bazel_binary_wrapper(self, name: str, target: str) -> None:
        """Generates a wrapper for a Bazel binary.

        Args:
            name: The name of the binary.
            target: The Bazel target.
        """
        exe = self.dest / f"{self.prefix}{name}"
        self.toolchain_map[name] = exe.absolute().as_posix()

        bazel_exe = self.bazel.build_exe(target)
        script = f"""@echo off
rem Auto-generated by ToolchainGenerator, DO NOT EDIT!!
rem Bazel: {target}
{bazel_exe} "$@"
"""
        with open(exe, "w", encoding="utf-8") as f:
            f.write(script)
        exe.chmod(0o755)

    def cmake(self) -> Tuple[str, str]:
        """Returns the cmake command and extra arguments."""
        vs = self._visual_studio()
        cmake = (
            self.aosp
            / "prebuilts"
            / "cmake"
            / f"{self.host()}-x86"
            / "bin"
            / "cmake.exe"
        )
        # We make sure that the vs variables are loaded before launching cmake
        # This will enable cmake to derive the visual studio compiler toolchain.
        return (
            f'call "{vs}"\n' f'"{cmake}"',
            "",
        )

    def rust_link_script(self) -> Tuple[str, str]:
        """Returns the rust link script and extra arguments."""
        cl = self.clang() / "bin" / "clang.exe"

        rust_lib_dir = self.dest / "rust_libs"
        rust_lib_dir.mkdir(parents=True, exist_ok=True)
        # We need to link against mingw archives. Unfortunately that will not work
        # with our clang-cl environment, so we will patch up the .a -> .lib
        if self.mingw_dir.exists():
            convert_mingw_to_msvc_lib(
                self.mingw_dir / "x86_64-w64-mingw32" / "lib64" / "libpthread.a",
                rust_lib_dir / "pthread.lib",
                self.clang() / "bin",
            )

            convert_mingw_to_msvc_lib(
                self.mingw_dir
                / "lib"
                / "gcc"
                / "x86_64-w64-mingw32"
                / "4.8.3"
                / "libgcc_eh.a",
                rust_lib_dir / "libgcc_eh.lib",
                self.clang() / "bin",
            )
        else:
            logging.warning("MinGW directory %s not found. Skipping MinGW to MSVC library conversion.", self.mingw_dir)

        # We should have loaded the visual studio environment.
        # We are going to extract the -L paths from this.
        lib_paths = ""
        if "LIB" in self.env:
            lib_paths = '"-L' + '" "-L'.join(self.env["LIB"].split(";")) + '"'
        else:
            logging.warning(
                "Not adding paths from `LIB` to the rust linker as it is not available in the environment"
            )

        script = (
            "# Link script for cargo \n"
            f'"{cl}" '
            "--target=x86_64-pc-windows-gnu "
            f"--sysroot={self.mingw_dir}/x86_64-w64-mingw32 "
            f"-B{self.mingw_dir}/lib/gcc/x86_64-w64-mingw32/4.8.3 "
            f"-L{self.mingw_dir}/lib/gcc/x86_64-w64-mingw32/4.8.3 "
            f"-L{self.mingw_dir}/x86_64-w64-mingw32/lib64 "
            f"-L{self.clang()}/lib -Wl,-mllvm,--relocation-model=pic "
            "-fuse-ld=lld "
            f"{lib_paths} "
            f"-L{rust_lib_dir}"
        )
        return script, ""

    def rust_flags(self) -> List[str]:
        """Returns the rust flags."""
        return ["--target=x86_64-pc-windows-gnu"]

    def rustc(self) -> Tuple[str, str]:
        """Returns the rustc command and extra arguments."""
        mingw = self.mingw_dir / "x86_64-w64-mingw32"
        rustc_bin = self.rust_bin_dir / "rustc.exe"
        script = (
            "setlocal\n"
            f"set PATH={self.rust_bin_dir};{mingw}\\bin;{mingw}\\lib;\n"
            f'"{rustc_bin}"'
        )
        return script, ""

    def cargo(self) -> Tuple[str, str]:
        """Returns the cargo command and extra arguments."""
        rust_linker = (self.dest / "ld-rust.cmd").as_posix()
        mingw = self.mingw_dir / "x86_64-w64-mingw32"
        rustc_bin = self.rust_bin_dir / "rustc.exe"
        cargo_bin = self.rust_bin_dir / "cargo.exe"
        cache = f"set RUSTC_WRAPPER={self.ccache}" if self.ccache else ""
        script = (
            "setlocal\n"
            # "set CARGO_HOME=..."
            f"set PATH={self.rust_bin_dir};{mingw}\\bin;{mingw}\\lib;\n"
            f"set CARGO_BUILD_RUSTC={rustc_bin}\n"
            f"set CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER={rust_linker}\n"
            "set CARGO_BUILD_TARGET=x86_64-pc-windows-gnu\n"
            "set RUSTFLAGS=-Cdefault-linker-libraries=yes\n"
            f"{cache}\n"
            f'set CC_x86_64-pc-windows-gnu={self.dest / "cc.cmd"}\n'
            f'set HOST_CC={self.dest / "cc.cmd"}\n'
            f'set CXX_x86_64-pc-windows-gnu={self.dest / "c++.cmd"}\n'
            f'set HOST_CXX={self.dest / "c++.cmd"}\n'
            f'set AR_x86_64-pc-windows-gnu={self.dest / "ar.cmd"}\n'
            f'"{cargo_bin}"'
        )
        return script, ""

    def pkg_config(self) -> Tuple[str, str]:
        """Returns the pkg-config command and extra arguments."""
        if self.pkg_config_bin:
            assert(self.bazel == None)
            # Note PKG_CONFIG_PATH not set in the script as it should be overridden in this mode.
            return (
                f"{Path(self.aosp / self.pkg_config_bin).as_posix()}",
                "",
            )

        # Build pkg-config from source.
        artifacts = self.bazel.build_target("@pkg-config")
        pkg_exe = Path(self.aosp / artifacts[0]).as_posix()
        pkg_path = self.pkgconfig_directory.as_posix()
        return (
            f"set PKG_CONFIG_PATH={pkg_path}\n" f'"{pkg_exe}"',
            "",
        )

    def clang_cl(self) -> Tuple[str, str]:
        """Returns the clang-cl command and extra arguments."""
        self.initialize()
        cache = f'"{self.ccache}"' if self.ccache else ""
        cl = self.clang() / "bin" / "clang-cl.exe"
        flags = f""

        return (
            f'{cache} "{cl}"',
            f"{flags}",
        )

    def cc(self, clang: str = "clang") -> Tuple[str, str]:
        """Returns the C compiler command and extra arguments."""
        self.initialize()
        rust_lib_dir = self.dest / "lib"

        cache = f'"{self.ccache}"' if self.ccache else ""
        if not clang.endswith(".exe"):
            clang += ".exe"
        cl = self.clang() / "bin" / clang
        flags = (
            "-Wno-constant-conversion "
            "-Wno-macro-redefined "
            "-Wno-invalid-noreturn "
            "-Wno-bitfield-constant-conversion "
            "-Wno-int-to-void-pointer-cast "
            "-Wno-unused-command-line-argument "
            "-Wno-undef "
            "-Wno-microsoft-enum-forward-reference "
            "-Wno-microsoft-include "
            "-Wno-deprecated-declarations "
            f"-L{self.compat_lib_dir} -L{rust_lib_dir} -lcompat"
        )

        return (
            f'{cache} "{cl}" -I{self.compat_path} -march=native -target x86_64-pc-windows-msvc -fms-extensions',
            f"{flags}",
        )

    def cxx(self) -> Tuple[str, str]:
        """Returns the C++ compiler command and extra arguments."""
        return self.cc("clang++")

    def windres(self) -> Tuple[str, str]:
        """Returns the windres command and extra arguments."""
        return f'"{self.clang() / "bin" / "llvm-windres.exe"}"', ""

    def rc(self) -> Tuple[str, str]:
        """Returns the rc command and extra arguments."""
        return f'"{self.clang() / "bin" / "llvm-rc.exe"}"', ""

    def windmc(self) -> Tuple[str, str]:
        """Returns the mc command and extra arguments."""
        return self.rc()

    def lld(self) -> Tuple[str, str]:
        """Returns the lld command and extra arguments."""

        link_opts = ""
        if "WINDOWSSDKLIBVERSION" in self.env and "WINDOWSSDKDIR" in self.env:
            winsdk_lib = (
                Path(self.env["WINDOWSSDKDIR"])
                / "Lib"
                / self.env["WINDOWSSDKLIBVERSION"]
                / "um"
                / "x64"
            )
            link_opts = f"/libpath:{winsdk_lib}"
        else:
            logging.warning(
                "Missing WINDOWSSDKLIBVERSION or WINDOWSSDKDIR in environment. "
                "These will not be added to the default link path."
            )

        lld = self.clang() / "bin" / "lld-link.exe"
        return f'"{lld}" {link_opts}', ""

    def gen_toolchain(self, packages: List[Any], binaries: Dict[str, str]) -> None:
        """Generates the toolchain.

        Args:
            packages: A list of packages to generate pkg-config files for.
            binaries: A dictionary of binaries to generate wrappers for.
        """
        super().gen_toolchain(packages, binaries)
        # Generate the resource compilers, not they all point to rc, llvm-rc.
        # llvm-rc is a clean room implementation of msvc-rc, with a series of
        # extensions.
        self.gen_script("windres", self.dest / "windres", self.windres)
        self.gen_script("windmc", self.dest / "windmc", self.windmc)
        self.gen_script("rc", self.dest / "rc", self.rc)
        self.gen_script("ld-rust", self.dest / "ld-rust", self.rust_link_script)
        self.gen_script("clang-cl", self.dest / "clang-cl", self.clang_cl)

        # linkers
        self.gen_script("lld-link", self.dest / "lld-link", self.lld)
