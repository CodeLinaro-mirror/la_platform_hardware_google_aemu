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
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import check_output
from typing import Tuple, List, Dict, Any, Callable

from aemu.command import CommandLineReconstructor
from aemu.process.bazel import Bazel


def safe_symlink_dir(src: Path, dest: Path) -> None:
    if dest.is_symlink() or dest.exists():
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        dest_item = dest / item.name
        if not item.exists():
            logging.warning("Skipping dangling symlink: %s", item)
            continue

        if dest_item.is_symlink() or dest_item.exists():
            if dest_item.is_dir() and not dest_item.is_symlink():
                shutil.rmtree(dest_item)
            else:
                dest_item.unlink()

        if item.is_dir():
            safe_symlink_dir(item, dest_item)
        else:
            dest_item.symlink_to(item.resolve())


class ToolchainGenerator:
    """A class for generating toolchain wrappers."""

    PKGCFG_DIR = "pc-config"
    PACKAGES_DIR = "packages"

    def __init__(
        self, aosp: Path, dest: Path, prefix: str, versions: Dict[str, str] = None,
    ) -> None:
        """Initializes a ToolchainGenerator object.

        Args:
            aosp: The path to the AOSP source tree.
            dest: The destination directory for the toolchain.
            prefix: The prefix for the toolchain binaries.
            versions: A dictionary of toolchain versions.
        """
        self.aosp = aosp.absolute()
        # self.bazel = Bazel(self.aosp, dest)
        # This needs to be set externally now.
        self.bazel: Bazel = None
        self.env: Dict[str, str] = os.environ
        self.reconstructor = CommandLineReconstructor()
        self.command_line = self.reconstructor.get_command_string()

        self.ninja_bin = None
        self.pkg_config_bin = None
        self.compat_lib = None

        if versions:
            self.versions = versions
        else:
            toolchain_json = (
                self.aosp / "build" / "bazel" / "toolchains" / "tool_versions.json"
            )
            with open(
                toolchain_json,
                encoding="utf-8",
            ) as f:
                self.versions = json.load(f)
        self.dest: Path = dest
        self.prefix = prefix
        self.toolchain_map: Dict[str, str] = {}
        self.dest.mkdir(exist_ok=True, parents=True)
        self.ccache = shutil.which("sccache") or shutil.which("ccache")
        self.py_exe = Path(sys.executable).absolute()

        # Create pkgconfig directory.
        self.pkgconfig_directory = self.dest / ToolchainGenerator.PKGCFG_DIR
        self.pkgconfig_directory.mkdir(parents=True, exist_ok=True)

        # Create packages directory.
        self.packages_directory = self.dest / ToolchainGenerator.PACKAGES_DIR
        self.packages_directory.mkdir(parents=True, exist_ok=True)

    def version(self) -> str:
        """Returns the clang version."""
        return self.versions.get("clang", "clang-stable")

    def rust_version(self) -> str:
        """Returns the rust version."""
        return self.versions.get("rust", "1.78.0")

    def cc_version(self) -> str:
        """Returns the C compiler version."""
        logging.info("Using clang: %s", self.clang())
        result = subprocess.check_output(
            [str(self.clang() / "bin" / "clang"), "-v"],
            encoding="utf-8",
            stderr=subprocess.STDOUT,
        )
        version = result.splitlines()[0]
        match = re.match(r".*clang version (\d+).(\d+).(\d).*", version)
        if match:
            return match.group(1)

        raise ValueError(f"Could not find version string in {version}")

    def clang(self) -> Path:
        """Returns the path to the clang directory."""
        return (
            self.aosp
            / "prebuilts"
            / "clang"
            / "host"
            / f"{self.host()}-x86"
            / self.version()
        )

    def cmake(self) -> Tuple[str, str]:
        """Returns the cmake command and extra arguments."""
        cmake = (
            self.aosp / "prebuilts" / "cmake" / f"{self.host()}-x86" / "bin" / "cmake"
        )
        return (
            f"{cmake} ",
            "",
        )

    def host(self) -> str:
        """Returns the host operating system."""
        return platform.system().lower()

    def tool_exe_extension(self) -> str:
        """Returns the executable extension for the toolchain tools."""
        return ".exe" if self.host() == "windows" else ""

    def llvm_tool(self, tool_name: str) -> Tuple[str, str]:
        """Returns the path to the given llvm tool."""
        return f'"{self.clang() / "bin" / (tool_name + self.tool_exe_extension())}"', ""

    def nm(self) -> Tuple[str, str]:
        """List symbols from object files, helping developers identify defined and undefined symbols."""
        return self.llvm_tool("llvm-nm")

    def ar(self) -> Tuple[str, str]:
        """Create, modify, and extract from archives (static libraries)."""
        return self.llvm_tool("llvm-ar")

    def objdump(self) -> Tuple[str, str]:
        """Display detailed information from object files, including headers, sections, and disassembly."""
        return self.llvm_tool("llvm-objdump")

    def strings(self) -> Tuple[str, str]:
        """Print the sequences of printable characters in files, useful for finding embedded text in binaries."""
        return self.llvm_tool("llvm-strings")

    def ranlib(self) -> Tuple[str, str]:
        """Generate an index to the contents of an archive and store it in the archive."""
        return self.llvm_tool("llvm-ranlib")

    def cxx(self) -> Tuple[str, str]:
        """The Clang C++ compiler."""
        return self.llvm_tool("clang++")

    def cc(self) -> Tuple[str, str]:
        """The Clang C compiler."""
        return self.llvm_tool("clang")

    def objc(self) -> Tuple[str, str]:
        """The Clang Objective-C compiler."""
        return self.cc()

    def lld(self) -> Tuple[str, str]:
        """A generic, high-performance replacement for system linkers."""
        return self.llvm_tool("lld")

    def ld_lld(self) -> Tuple[str, str]:
        """The LLVM linker for ELF-based systems (Linux)."""
        return self.llvm_tool("ld.lld")

    def ld64_lld(self) -> Tuple[str, str]:
        """The LLVM linker for Mach-O-based systems (macOS)."""
        return self.llvm_tool("ld64.lld")

    def wasm_ld(self) -> Tuple[str, str]:
        """The LLVM linker for WebAssembly."""
        return self.llvm_tool("wasm-ld")

    def llvm_dis(self) -> Tuple[str, str]:
        """LLVM disassembler that converts LLVM bitcode into human-readable assembly."""
        return self.llvm_tool("llvm-dis")

    def llvm_link(self) -> Tuple[str, str]:
        """LLVM bitcode linker, used to combine multiple bitcode files into one."""
        return self.llvm_tool("llvm-link")

    def llvm_extract(self) -> Tuple[str, str]:
        """Extract a specific function or global variable from an LLVM bitcode file."""
        return self.llvm_tool("llvm-extract")

    def llvm_cov(self) -> Tuple[str, str]:
        """Display code coverage information by processing profile data."""
        return self.llvm_tool("llvm-cov")

    def llvm_profdata(self) -> Tuple[str, str]:
        """Tool for manipulating and merging code profile data."""
        return self.llvm_tool("llvm-profdata")

    def llvm_dwarfdump(self) -> Tuple[str, str]:
        """Dump and verify DWARF debug information in executable files."""
        return self.llvm_tool("llvm-dwarfdump")

    def llvm_as(self) -> Tuple[str, str]:
        """LLVM assembler that converts human-readable LLVM assembly into bitcode."""
        return self.llvm_tool("llvm-as")

    def llvm_size(self) -> Tuple[str, str]:
        """List the section sizes—such as code, data, and bss—and total size for object files."""
        return self.llvm_tool("llvm-size")

    def llvm_readobj(self) -> Tuple[str, str]:
        """Display low-level, format-independent information about object files."""
        return self.llvm_tool("llvm-readobj")

    def llvm_readelf(self) -> Tuple[str, str]:
        """Display information about ELF-formatted object files."""
        return self.llvm_tool("llvm-readelf")

    def llvm_objcopy(self) -> Tuple[str, str]:
        """Copy and translate object files, often used to strip symbols or change formats."""
        return self.llvm_tool("llvm-objcopy")

    def llvm_strip(self) -> Tuple[str, str]:
        """Discard symbols and other data from binary files to reduce their size."""
        return self.llvm_tool("llvm-strip")

    def llvm_dlltool(self) -> Tuple[str, str]:
        """Create Windows DLL import libraries and definition files."""
        return self.llvm_tool("llvm-dlltool")

    def llvm_addr2line(self) -> Tuple[str, str]:
        """Convert program addresses into file names and line numbers using debug information."""
        return self.llvm_tool("llvm-addr2line")

    def llvm_dwp(self) -> Tuple[str, str]:
        """Merge split DWARF (.dwo) files into a single DWARF package (.dwp) file."""
        return self.llvm_tool("llvm-dwp")

    def llvm_cxxfilt(self) -> Tuple[str, str]:
        """Demangle C++ and Java symbols, restoring human-readable names from mangled identifiers."""
        return self.llvm_tool("llvm-c++filt")

    def llvm_lib(self) -> Tuple[str, str]:
        """An MSVC-compatible tool for managing object library archives."""
        return self.llvm_tool("llvm-lib")

    def llvm_lipo(self) -> Tuple[str, str]:
        """Tool to create or operate on universal binaries (fat files)."""
        return self.llvm_tool("llvm-lipo")

    def clang_tidy(self) -> Tuple[str, str]:
        """A Clang-based C++ linter tool that provides static analysis and fixes for common coding errors."""
        return self.llvm_tool("clang-tidy")

    def clang_format(self) -> Tuple[str, str]:
        """A tool to automatically format C/C++/Java/JavaScript/Objective-C/Protobuf code."""
        return self.llvm_tool("clang-format")

    def clang_check(self) -> Tuple[str, str]:
        """Perform static analysis, syntax checks, and other diagnostics on source code."""
        return self.llvm_tool("clang-check")

    def llvm_symbolizer(self) -> Tuple[str, str]:
        """Convert addresses into source code locations (file, line, function)."""
        return self.llvm_tool("llvm-symbolizer")

    def dsymutil(self) -> Tuple[str, str]:
        """Link and manipulate archived debug symbol files (macOS .dSYM bundles)."""
        return self.llvm_tool("dsymutil")

    def lipo(self) -> Tuple[str, str]:
        """Tool to create or operate on universal binaries (fat files)."""
        return self.llvm_tool("llvm-lipo")

    def lldb(self) -> Tuple[str, str]:
        """The LLVM debugger, providing high-performance debugging for C, C++, and Objective-C."""
        return self.llvm_tool("lldb")

    def ninja(self) -> Tuple[str, str]:
        """Returns the ninja command and extra arguments."""
        if self.ninja_bin:
            assert(self.bazel == None)
            return self.aosp / self.ninja_bin, ""

        assert(self.bazel != None)
        artifacts = self.bazel.build_target("@ninja", for_host=True)
        if artifacts:
            return f"{self.aosp / artifacts[0]}", ""

        prebuilts = self.aosp / "prebuilts"
        options = [
            prebuilts / "build-tools" / f"{self.host()}-x86" / "bin",
            prebuilts / "ninja" / f"{self.host()}-x86",
            self.env.get("PATH", None),  # Path
            None,  # Default system path
        ]

        for ninja_path in options:
            ninja = shutil.which("ninja", path=ninja_path)
            if ninja:
                return f'"{ninja}"', ""

        raise FileNotFoundError(f"Ninja was not found in any of: {','.join(options)}")

    def rust_flags(self) -> List[str]:
        """Returns the rust flags."""
        return []

    def rustc(self) -> Tuple[Path, str]:
        """Returns the rustc command and extra arguments."""
        return (
            self.aosp
            / "prebuilts"
            / "rust"
            / f"{self.host()}-x86"
            / self.rust_version()
            / "bin"
            / "rustc",
            "",
        )

    def cargo(self) -> Tuple[str, str]:
        """Returns the cargo command and extra arguments."""
        rustc_bin = (
            self.aosp
            / "prebuilts"
            / "rust"
            / f"{self.host()}-x86"
            / self.rust_version()
            / "bin"
            / "rustc"
        )
        cargo_bin = (
            self.aosp
            / "prebuilts"
            / "rust"
            / f"{self.host()}-x86"
            / self.rust_version()
            / "bin"
            / "cargo"
        )

        script = f"CARGO_BUILD_RUSTC={rustc_bin}\n" f"{cargo_bin}"
        return script, ""

    def meson(self) -> Tuple[str, str]:
        """Returns the meson command and extra arguments."""
        meson_py = self.aosp / "third_party" / "meson" / "meson.py"
        exe = f"{self.py_exe} {meson_py} "
        return exe, ""

    def python3(self) -> Tuple[str, str]:
        """Returns the python3 command."""
        exe = f"{self.py_exe}"
        return exe, ""

    def git(self) -> Tuple[str, str]:
        """Returns the system git command."""
        git_path = shutil.which("git")
        if not git_path:
            raise FileNotFoundError("System git was not found on PATH.")
        return git_path, ""

    def strip(self) -> Tuple[str, str]:
        """Returns the strip command and extra arguments."""
        return "", ""

    def pkg_config(self) -> Tuple[str, str]:
        """Returns the pkg-config command and extra arguments."""
        if self.pkg_config_bin:
            # Note PKG_CONFIG_PATH not set in the script as it should be overridden in this mode.
            assert(self.bazel == None)
            return (
                f'PKG_CONFIG_LIBDIR="" '
                f"{self.aosp / self.pkg_config_bin}",
                "",
            )

        assert(self.bazel != None)
        # Build pkg-config from source for the host.
        artifacts = self.bazel.build_target("@pkg-config", for_host=True)
        return (
            f'PKG_CONFIG_PATH={self.pkgconfig_directory}  PKG_CONFIG_LIBDIR="" '
            f"{self.aosp / artifacts[0]}",
            "",
        )

    def gen_script(
        self, name: str, exe: Path, cmd_generator_fn: Callable[[], Tuple[Any, str]]
    ) -> None:
        """Generates a script.

        Args:
            name: The name of the script.
            exe: The path to the script.
            cmd_generator_fn: A function that returns the command and extra arguments.
        """
        self.toolchain_map[name] = exe.absolute().as_posix()

        logging.info("Generating %s", exe)

        rem = "#"
        run, extra = cmd_generator_fn()
        params = '"$@"'

        script = f"""#!/bin/sh
{rem} Auto-generated by amc, DO NOT EDIT!!
{rem}
{rem} Generated by: {self.command_line}
{rem}
{run} {params} {extra}
"""
        with open(exe, "w", encoding="utf-8") as f:
            f.write(script)

        exe.chmod(0o755)

    def write_toolchain_config(self) -> None:
        """Writes a toolchain configuration that can be consumed by meson."""
        config = configparser.ConfigParser()
        config.read(self.dest / "aosp-cl.ini")

        if "properties" not in config:
            config["properties"] = {}
        if "built-in options" not in config:
            config["built-in options"] = {
                "c_args": "[]",
                "cpp_args": "[]",
                "objc_args": "[]",
                "c_link_args": "[]",
                "cpp_link_args": "[]",
            }
        if "binaries" not in config:
            config["binaries"] = {}

        for name, dest in self.toolchain_map.items():
            if isinstance(dest, list):
                config["binaries"][name] = "[" + ", ".join([f"'{x}'" for x in dest]) + "]"
            else:
                config["binaries"][name] = f"'{dest}'"

        if isinstance(self.toolchain_map['cc'], list):
            config["binaries"]["c"] = "[" + ", ".join([f"'{x}'" for x in self.toolchain_map['cc']]) + "]"
        else:
            config["binaries"]["c"] = f"'{self.toolchain_map['cc']}'"

        with open(self.dest / "aosp-cl.ini", "w", encoding="utf-8") as f:
            f.write("# Auto generated by Android Meson Generator - do not modify\n")
            config.write(f)

    def link_dirs(self) -> None:
        """Setup links to libc++.so etc.."""
        clang_lib = self.clang() / "lib"
        if not clang_lib.exists():
            logging.warning("The clang lib directory: %s, does not exist.", clang_lib)
            return

        target = self.dest / "lib"
        safe_symlink_dir(clang_lib, target)

    def gen_toolchain(
        self, packages: List[Any] = [], binaries: Dict[str, str] = {}
    ) -> None:
        """Generates the toolchain.

        Args:
            packages: A list of packages to generate pkg-config files for.
            binaries: A dictionary of binaries to generate wrappers for.
        """
        cmds = {
            # Compilers
            "cc": self.cc,
            "c++": self.cxx,
            "objc": self.objc,
            "clang": self.cc,
            "clang++": self.cxx,
            "gcc": self.cc,
            "g++": self.cxx,
            "rustc": self.rustc,
            # Linkers
            "ld": self.lld,
            "lld": self.lld,
            "ld.lld": self.ld_lld,
            "ld64.lld": self.ld64_lld,
            "wasm-ld": self.wasm_ld,
            # Archive/Library Tools
            "ar": self.ar,
            "ranlib": self.ranlib,
            "lib": self.llvm_lib,
            "llvm-lib": self.llvm_lib,
            "dlltool": self.llvm_dlltool,
            "link": self.llvm_link,
            # Binary Analysis/Manipulation
            "nm": self.nm,
            "objdump": self.objdump,
            "strings": self.strings,
            "size": self.llvm_size,
            "readobj": self.llvm_readobj,
            "readelf": self.llvm_readelf,
            "objcopy": self.llvm_objcopy,
            "strip": self.strip,
            "addr2line": self.llvm_addr2line,
            "c++filt": self.llvm_cxxfilt,
            "symbolizer": self.llvm_symbolizer,
            "dsymutil": self.dsymutil,
            # LLVM Specific Tools
            "as": self.llvm_as,
            "dis": self.llvm_dis,
            "extract": self.llvm_extract,
            "dwp": self.llvm_dwp,
            # Code Coverage/Profiling
            "cov": self.llvm_cov,
            "profdata": self.llvm_profdata,
            # Build Systems/Package Managers
            "meson": self.meson,
            "ninja": self.ninja,
            "pkg-config": self.pkg_config,
            "python3": self.python3,
            "cmake": self.cmake,
            "cargo": self.cargo,
            # Static Analysis/Formatting/Linting
            "tidy": self.clang_tidy,
            "format": self.clang_format,
            "check": self.clang_check,
            # Debugging
            "lldb": self.lldb,
            "dwarfdump": self.llvm_dwarfdump,
        }
        if self.host() == "darwin":
            cmds["lipo"] = self.llvm_lipo
        if self.host() != "windows":
            cmds["git"] = self.git

        for cmd, fn in cmds.items():
            self.gen_script(cmd, self.dest / f"{self.prefix}{cmd}", fn)

        for name, target in binaries.items():
            self._generate_bazel_binary_wrapper(name, target)

        self.generate_pkg_config_files(packages)
        self.link_dirs()
        self.write_toolchain_config()

    def _generate_bazel_binary_wrapper(self, name: str, target: str) -> None:
        """Generates a wrapper for a Bazel binary.

        Args:
            name: The name of the binary.
            target: The Bazel target.
        """
        exe = self.dest / f"{self.prefix}{name}"
        self.toolchain_map[name] = exe.absolute().as_posix()
        bazel_exe = self.bazel.build_exe(target)
        script = f"""#!/bin/sh
# Auto-generated by ToolchainGenerator, DO NOT EDIT!!
#
# Generated by: {self.command_line}
#
# Bazel: {target}
{bazel_exe} "$@"
"""
        with open(exe, "w", encoding="utf-8") as f:
            f.write(script)
        exe.chmod(0o755)

    def generate_pkg_config_files(self, packages: List[Any]) -> None:
        """Generates pkg-config files for all defined dependencies.

        Args:
            packages: A list of packages to generate pkg-config files for.
        """
        for package in packages:
            package.generate_pkg_config(
                self.dest,
                self.pkgconfig_directory,
                self.packages_directory,
            )
