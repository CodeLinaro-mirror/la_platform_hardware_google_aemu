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

"""CrashAdvisor integration module for emu-dev-cli.

Provides environment discovery, import stubs, and execution helpers
for the CrashAdvisor tool component.
"""

import logging
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List

from lib.bazel import BazelRunner
from lib.workspace import WorkspacePathResolver

logger = logging.getLogger(__name__)

ADVISOR_REL_DIR = "hardware/generic/goldfish/emulator/crashreport/tool/advisor"
LIBS_PYTHON_REL_DIR = "hardware/google/aemu/tools/libs_python"
ADVISOR_BAZEL_TARGET = "@goldfish//emulator/crashreport/tool/advisor:advisor"

_resolver = WorkspacePathResolver("emu-main-next")


def ensure_crashadvisor_imports() -> Dict[str, Any]:
    """Ensures crashadvisor and libs_python directories are in sys.path and returns imported modules.

    Configures fallback stubs for missing optional packages (tqdm, googleapiclient, etc.)
    if executing outside standard virtual environments.

    Returns:
        Dictionary mapping CrashAdvisor module names ('api', 'buganizer', 'client', etc.) to imported modules.
    """
    advisor_dir = _resolver.find_directory(ADVISOR_REL_DIR)
    if not advisor_dir or not advisor_dir.exists():
        sys.stderr.write(
            f"❌ ERROR: Could not locate CrashAdvisor at {ADVISOR_REL_DIR}.\n"
            "Ensure your workspace source directory is registered via `emu-dev-cli source-directory set emu-main-next /path/to/src`.\n"
        )
        sys.exit(1)

    advisor_dir_str = str(advisor_dir)
    if advisor_dir_str not in sys.path:
        sys.path.insert(0, advisor_dir_str)

    libs_python_dir = _resolver.find_directory(LIBS_PYTHON_REL_DIR)
    if libs_python_dir and str(libs_python_dir) not in sys.path:
        sys.path.insert(0, str(libs_python_dir))

    # Provide fallback for `from python.runfiles import Runfiles` when executed outside Bazel
    if "python.runfiles" not in sys.modules:
        try:
            from python.runfiles import Runfiles
        except ImportError:
            import types

            py_mod = types.ModuleType("python")
            rf_mod = types.ModuleType("python.runfiles")

            class DummyRunfiles:
                @staticmethod
                def Create():
                    return DummyRunfiles()

                def Rlocation(self, rpath: str) -> str:
                    return str(Path(rpath).resolve())

            rf_mod.Runfiles = DummyRunfiles
            py_mod.runfiles = rf_mod
            sys.modules["python"] = py_mod
            sys.modules["python.runfiles"] = rf_mod

    # Stubs for optional heavy dependencies when running in lightweight environments
    for mod_name in [
        "tqdm",
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.http",
        "googleapiclient.errors",
        "oauth2client",
        "oauth2client.client",
    ]:
        if mod_name not in sys.modules:
            try:
                __import__(mod_name)
            except ImportError:
                import types

                stub = types.ModuleType(mod_name)
                if mod_name == "tqdm":
                    stub.tqdm = lambda x, **kw: x
                elif mod_name in ("googleapiclient", "oauth2client"):
                    stub.__path__ = []
                elif mod_name == "googleapiclient.discovery":
                    stub.build = lambda *a, **kw: None
                elif mod_name == "googleapiclient.http":
                    stub.MediaIoBaseDownload = lambda *a, **kw: None
                    stub.MediaFileUpload = lambda *a, **kw: None
                elif mod_name == "googleapiclient.errors":

                    class HttpError(Exception):
                        pass

                    stub.HttpError = HttpError
                elif mod_name == "oauth2client.client":

                    class AccessTokenCredentials:
                        def __init__(self, *a, **kw):
                            pass

                    stub.AccessTokenCredentials = AccessTokenCredentials
                sys.modules[mod_name] = stub

    try:
        import api
        import buganizer
        import client
        import context
        import dump
        import metadata
        import rca
        import symbols
        import advisor

        return {
            "api": api,
            "buganizer": buganizer,
            "client": client,
            "context": context,
            "dump": dump,
            "metadata": metadata,
            "rca": rca,
            "symbols": symbols,
            "advisor": advisor,
        }
    except ImportError as e:
        sys.stderr.write(f"❌ ERROR: Failed to import CrashAdvisor modules: {e}\n")
        sys.exit(1)


def run_crashadvisor_bazel(
    args_list: List[str], check: bool = True
) -> subprocess.CompletedProcess:
    """Executes CrashAdvisor via compiled executable binary or falls back to Bazel target.

    Args:
        args_list: Command line argument list to pass to CrashAdvisor.
        check: Whether to raise CalledProcessError on non-zero exit.

    Returns:
        CompletedProcess instance from subprocess.run execution.
    """
    advisor_bin = _resolver.find_tool_binary(
        "advisor", bazel_target=ADVISOR_BAZEL_TARGET
    )
    try:
        if advisor_bin:
            cmd = [str(advisor_bin)] + args_list
            return subprocess.run(cmd, check=check)

        src_dir = _resolver.find_directory("hardware/google/aemu")
        source_dir_str = (
            str(src_dir.parent.parent.parent) if src_dir else str(Path.cwd())
        )

        runner = BazelRunner(source_dir=source_dir_str)
        return runner.run(
            target="@goldfish//emulator/crashreport/tool/advisor",
            args=args_list,
            cwd=source_dir_str,
            check=check,
        )
    except subprocess.CalledProcessError as e:
        sys.stderr.write(
            f"⚠️ CrashAdvisor execution returned non-zero exit code {e.returncode}.\n"
        )
        if check:
            raise
        return subprocess.CompletedProcess(
            args=getattr(e, "cmd", []),
            returncode=e.returncode,
            stdout=getattr(e, "stdout", None),
            stderr=getattr(e, "stderr", None),
        )


__all__ = [
    "ensure_crashadvisor_imports",
    "run_crashadvisor_bazel",
]
