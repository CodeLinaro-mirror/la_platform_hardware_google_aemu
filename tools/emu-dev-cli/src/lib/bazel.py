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

"""Bazel discovery, build, query, and execution runner module for emu-dev-cli.

Lifts and adapts core Bazel execution, exit code taxonomy, and artifact resolution
capabilities from the AEMU build infrastructure.
"""

import enum
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Dict, Iterable, List, Optional, Type, TypeVar, Union

logger = logging.getLogger(__name__)

PLATFORM_TARGETS: Dict[str, str] = {
    "linux_x64": "@goldfish_build//platforms:linux_x64",
    "mac_aarch64": "@goldfish_build//platforms:macos_aarch64",
    "mac_x64": "@goldfish_build//platforms:macos_x64",
    "windows_x64": "@goldfish_build//platforms:windows_x64",
}

ExitCodeType = TypeVar("ExitCodeType", bound="BaseExitCode")


class BaseExitCode(enum.IntEnum):
    """Standard base exit codes for Bazel commands."""

    SUCCESS = 0
    BAD_COMMANDLINE = 2
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45


class BuildExitCode(enum.IntEnum):
    """Exit codes for `bazel build` and `bazel test` commands."""

    SUCCESS = 0
    BUILD_FAILED = 1
    BAD_COMMANDLINE = 2
    TESTS_FAILED = 3
    TESTS_NOT_FOUND = 4
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45


class QueryExitCode(enum.IntEnum):
    """Exit codes for `bazel query` and `bazel cquery` commands."""

    SUCCESS = 0
    BAD_COMMANDLINE = 2
    QUERY_PARTIAL_SUCCESS = 3
    QUERY_COMMAND_FAILED = 7
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45


def try_convert_exit_code(
    code: int, enum_cls: Type[ExitCodeType] = BaseExitCode
) -> Union[ExitCodeType, int]:
    """Attempts to convert integer return code to a typed Bazel exit code enum.

    Args:
        code: Process return code integer.
        enum_cls: Exit code IntEnum class to try converting into.

    Returns:
        Enum instance if code is a known value, otherwise the raw integer.
    """
    try:
        return enum_cls(code)
    except (ValueError, KeyError):
        return code


def format_error_msg(
    cmd: List[str],
    returncode: Union[int, enum.IntEnum],
    stderr: Optional[str] = None,
) -> str:
    """Formats a diagnostic error message with typed exit code details.

    Args:
        cmd: Executed command argument list.
        returncode: Exit code integer or enum.
        stderr: Optional stderr captured output.

    Returns:
        Formatted error message string.
    """
    code_name = (
        returncode.name if isinstance(returncode, enum.IntEnum) else str(returncode)
    )
    msg = f"Bazel command '{' '.join(cmd)}' failed with exit code {code_name}"
    if stderr:
        msg += f"\nSTDERR:\n{stderr.strip()}"
    return msg


def find_bazel_cmd(source_dir: Optional[Union[str, Path]] = None) -> str:
    """Finds the path to the Bazel executable.

    Checks repository prebuilt Bazel binaries first, then tools/bazel wrapper script,
    then system PATH.

    Args:
        source_dir: Optional source directory root.

    Returns:
        Path string to the resolved Bazel executable.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        platform_dirs = ["linux-x86_64"]
        exe_names = ["bazel"]
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            platform_dirs = ["darwin-arm64", "darwin-x86_64", "mac-arm64", "mac-x86_64"]
        else:
            platform_dirs = ["darwin-x86_64", "mac-x86_64"]
        exe_names = ["bazel"]
    elif system == "windows":
        platform_dirs = ["windows-x86_64", "windows"]
        exe_names = ["bazel.exe", "bazel"]
    else:
        platform_dirs = ["linux-x86_64"]
        exe_names = ["bazel"]

    if source_dir:
        src_path = Path(source_dir)
        # 1. Check repository prebuilts/bazel/<platform>/bazel
        for p_dir in platform_dirs:
            for exe in exe_names:
                candidate = src_path / "prebuilts" / "bazel" / p_dir / exe
                if candidate.exists():
                    if system == "windows" or os.access(candidate, os.X_OK):
                        return str(candidate)

        # 2. Check local repo tools/bazel wrapper script
        tools_bazel = src_path / "tools" / "bazel"
        if tools_bazel.exists() and os.access(tools_bazel, os.X_OK):
            logger.debug("Found tools/bazel wrapper: %s", tools_bazel)
            return str(tools_bazel)

    # 3. Check system PATH
    system_bazel = shutil.which("bazel")
    if system_bazel:
        logger.debug("Found system Bazel executable: %s", system_bazel)
        return system_bazel

    logger.debug("Defaulting to 'bazel' command")
    return "bazel"


class BazelRunner:
    """Reusable runner for building, testing, querying, and executing Bazel targets."""

    def __init__(
        self,
        source_dir: Optional[Union[str, Path]] = None,
        bazel_binary: Optional[str] = None,
    ) -> None:
        """Initializes BazelRunner.

        Args:
            source_dir: Optional workspace source directory root.
            bazel_binary: Optional explicit bazel command/path override.
        """
        self.source_dir = Path(source_dir) if source_dir else None
        self._bazel_binary = bazel_binary
        self._cached_info: Optional[Dict[str, str]] = None

    @property
    def bazel_binary(self) -> str:
        """Resolves the Bazel command path."""
        if not self._bazel_binary:
            self._bazel_binary = find_bazel_cmd(self.source_dir)
            logger.debug("Resolved Bazel binary: %s", self._bazel_binary)
        return self._bazel_binary

    def run(
        self,
        target: str,
        args: Optional[List[str]] = None,
        cwd: Optional[Union[str, Path]] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Runs a Bazel target (`bazel run <target> -- <args>`).

        Args:
            target: Bazel target string (e.g. '@goldfish//emulator/crashreport/tool/advisor').
            args: Arguments to pass after '--'.
            cwd: Working directory to run Bazel from (defaults to self.source_dir).
            check: Whether to raise CalledProcessError if returncode != 0.

        Returns:
            CompletedProcess result.
        """
        cmd = [self.bazel_binary, "run", target]
        if args:
            cmd.append("--")
            cmd.extend(args)

        run_cwd = str(cwd or self.source_dir) if (cwd or self.source_dir) else None
        logger.debug("Executing Bazel command: %s (cwd=%s)", " ".join(cmd), run_cwd)
        return subprocess.run(cmd, cwd=run_cwd, check=check)

    def build(
        self,
        targets: List[str],
        cwd: Optional[Union[str, Path]] = None,
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        """Builds one or more Bazel targets (`bazel build <targets>`).

        Args:
            targets: List of target strings.
            cwd: Working directory to run Bazel from (defaults to self.source_dir).
            check: Whether to raise CalledProcessError if returncode != 0.
            capture_output: Whether to capture stdout/stderr.
            text: Text output mode.

        Returns:
            CompletedProcess result.
        """
        cmd = [self.bazel_binary, "build"] + targets
        run_cwd = str(cwd or self.source_dir) if (cwd or self.source_dir) else None
        return subprocess.run(
            cmd,
            cwd=run_cwd,
            check=check,
            capture_output=capture_output,
            text=text,
        )

    def cquery(
        self,
        query: str,
        invocation_flags: Optional[List[str]] = None,
        cwd: Optional[Union[str, Path]] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Executes a configurable `bazel cquery <query>`.

        Args:
            query: The Bazel target or query expression.
            invocation_flags: Optional flags (e.g. ['--output=files']).
            cwd: Working directory (defaults to self.source_dir).
            check: Whether to check returncode == 0.

        Returns:
            CompletedProcess result with captured stdout/stderr.
        """
        cmd = [self.bazel_binary, "cquery"]
        if invocation_flags:
            cmd.extend(invocation_flags)
        cmd.append(query)

        run_cwd = str(cwd or self.source_dir) if (cwd or self.source_dir) else None
        return subprocess.run(
            cmd,
            cwd=run_cwd,
            check=check,
            capture_output=True,
            text=True,
        )

    def get_info(
        self, key: Optional[str] = None, cwd: Optional[Union[str, Path]] = None
    ) -> Union[Dict[str, str], Optional[str]]:
        """Queries `bazel info` and parses output into a dictionary or returns specific key.

        Args:
            key: Optional specific info key to return (e.g. 'execution_root', 'output_base').
            cwd: Working directory (defaults to self.source_dir).

        Returns:
            Dict of all info keys, or string value for specified key.
        """
        if self._cached_info is None:
            cmd = [self.bazel_binary, "info"]
            run_cwd = str(cwd or self.source_dir) if (cwd or self.source_dir) else None
            res = subprocess.run(
                cmd, cwd=run_cwd, check=True, capture_output=True, text=True
            )
            info_dict = {}
            for line in res.stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info_dict[k.strip()] = v.strip()
            self._cached_info = info_dict

        if key:
            return self._cached_info.get(key)
        return self._cached_info

    def query_artifacts(
        self,
        targets: Iterable[str],
        cwd: Optional[Union[str, Path]] = None,
    ) -> List[Path]:
        """Resolves absolute output file Paths for target(s) dynamically via cquery and execution_root.

        Args:
            targets: Iterable of target strings (e.g. ['//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli']).
            cwd: Working directory (defaults to self.source_dir).

        Returns:
            List of Path objects pointing to built output artifacts.
        """
        target_list = list(targets)
        query = " + ".join(f"'{t}'" for t in target_list)
        res = self.cquery(
            query, invocation_flags=["--output=files"], cwd=cwd, check=True
        )

        exec_root_str = self.get_info("execution_root", cwd=cwd) or ""
        exec_root = Path(exec_root_str)

        artifacts = []
        for line in res.stdout.splitlines():
            cleaned = line.strip()
            if cleaned:
                artifacts.append(exec_root / cleaned)
        return artifacts
