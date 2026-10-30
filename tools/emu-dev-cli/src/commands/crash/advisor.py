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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from core.context import ExecutionContext
from core.errors import ExecutionError
from core.process import ProcessResult
from lib.bazel import find_bazel_cmd
from lib.workspace import WorkspacePathResolver

logger = logging.getLogger(__name__)

ADVISOR_REL_DIR = "hardware/generic/goldfish/emulator/crashreport/tool/advisor"
LIBS_PYTHON_REL_DIR = "hardware/google/aemu/tools/libs_python"
AEMU_REL_DIR = "hardware/google/aemu"
ADVISOR_BAZEL_TARGET = "@goldfish//emulator/crashreport/tool/advisor:advisor"
ADVISOR_RUN_TARGET = "@goldfish//emulator/crashreport/tool/advisor"
ADVISOR_BINARY_NAME = "advisor"
# hardware/google/aemu is three levels below the checkout root.
CHECKOUT_ROOT_PARENT_INDEX = 2

CHECKOUT_REMEDIATION = (
    "Register your emu-main-next checkout with "
    "'emu-dev-cli source-directory set emu-main-next /path/to/src', then retry."
)
ADVISOR_RUN_REMEDIATION = (
    "Check the CrashAdvisor output for the failing step. Pass --token (or set "
    "BUGANIZER_TOKEN) if downloads fail with an auth error. "
    + CHECKOUT_REMEDIATION
)

_resolver = WorkspacePathResolver("emu-main-next")


def ensure_crashadvisor_imports() -> Dict[str, Any]:
    """Script-friendly loader: prints the error and exits when CrashAdvisor is missing.

    Returns:
        Dictionary mapping CrashAdvisor module names ('api', 'buganizer', 'client', etc.) to imported modules.
    """
    try:
        return load_crashadvisor_modules()
    except ExecutionError as exc:
        sys.stderr.write(f"❌ ERROR: {exc.format_human()}\n")
        sys.exit(1)


def load_crashadvisor_modules() -> Dict[str, Any]:
    """Ensures crashadvisor and libs_python directories are in sys.path and returns imported modules.

    Configures fallback stubs for missing optional packages (tqdm, googleapiclient, etc.)
    if executing outside standard virtual environments.

    Returns:
        Dictionary mapping CrashAdvisor module names ('api', 'buganizer', 'client', etc.) to imported modules.

    Raises:
        ExecutionError: If the CrashAdvisor checkout or its modules cannot be found.
    """
    advisor_dir = _resolver.find_directory(ADVISOR_REL_DIR)
    if not advisor_dir or not advisor_dir.exists():
        raise ExecutionError(
            f"Could not locate CrashAdvisor at {ADVISOR_REL_DIR} in any registered source directory.",
            remediation=CHECKOUT_REMEDIATION,
        )

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

                    class _DummyTqdm:
                        def __init__(self, iterable=None, *a, **kw):
                            self.iterable = iterable

                        def __enter__(self):
                            return self

                        def __exit__(self, *a):
                            pass

                        def update(self, *a, **kw):
                            pass

                        def __iter__(self):
                            return iter(self.iterable or ())

                    stub.tqdm = _DummyTqdm
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
        raise ExecutionError(
            f"Failed to import CrashAdvisor modules from '{advisor_dir}': {e}.",
            remediation=CHECKOUT_REMEDIATION,
            cause=e,
        ) from e


def create_crash_bug_tracker(
    token: Optional[str], qa_mode: bool, verbose: bool
) -> Any:
    """Returns CrashAdvisor's BuganizerClient (crash hotlists, QA tracker, duplicate checks)."""
    buganizer_mod = load_crashadvisor_modules()["buganizer"]
    client_cls = getattr(buganizer_mod, "BuganizerClient", None)
    if client_cls is None:
        raise ExecutionError(
            "CrashAdvisor buganizer module does not expose BuganizerClient.",
            remediation=CHECKOUT_REMEDIATION,
        )
    return client_cls(
        token=token, qa_mode=qa_mode, verbose=verbose
    )


@dataclass(frozen=True)
class AdvisorLaunch:
    """Command prefix (and working directory) that starts the CrashAdvisor tool."""

    command: tuple[str, ...]
    cwd: Optional[Path] = None


def resolve_advisor_launch(
    resolver: WorkspacePathResolver = _resolver,
    find_bazel: Callable[[Optional[Path]], str] = find_bazel_cmd,
) -> AdvisorLaunch:
    """Prefers a prebuilt advisor binary, else `bazel run` from the checkout root."""
    advisor_bin = resolver.find_tool_binary(
        ADVISOR_BINARY_NAME, bazel_target=ADVISOR_BAZEL_TARGET
    )
    if advisor_bin:
        return AdvisorLaunch(command=(str(advisor_bin),))

    aemu_dir = resolver.find_directory(AEMU_REL_DIR)
    source_root = (
        aemu_dir.parents[CHECKOUT_ROOT_PARENT_INDEX] if aemu_dir else Path.cwd()
    )
    return AdvisorLaunch(
        command=(find_bazel(source_root), "run", ADVISOR_RUN_TARGET, "--"),
        cwd=source_root,
    )


class CrashAdvisorRunner:
    """Runs the CrashAdvisor tool for one crash through the context's ProcessRunner."""

    def __init__(
        self,
        *,
        resolve_launch: Callable[[], AdvisorLaunch] = resolve_advisor_launch,
    ) -> None:
        self._resolve_launch = resolve_launch

    def run(
        self,
        ctx: ExecutionContext,
        crash_id: str,
        work_dir: Path,
        *,
        flags: Sequence[str] = (),
        token: Optional[str] = None,
        check: bool = False,
    ) -> ProcessResult:
        """Runs the advisor on `crash_id` with artifacts written to `work_dir`.

        Output streams to the terminal like it always did, except in JSON
        mode, where it is captured so stdout stays pure JSON.
        """
        launch = self._resolve_launch()
        cmd = [*launch.command, crash_id, *flags, "--work-dir", str(work_dir)]
        if token:
            cmd.extend(["--token", token])
        return ctx.process_runner.run(
            cmd,
            cwd=launch.cwd,
            timeout_sec=None,
            check=check,
            remediation=ADVISOR_RUN_REMEDIATION,
            stream_output=not ctx.config.json_mode,
        )


__all__ = [
    "AdvisorLaunch",
    "CrashAdvisorRunner",
    "create_crash_bug_tracker",
    "ensure_crashadvisor_imports",
    "load_crashadvisor_modules",
    "resolve_advisor_launch",
]
