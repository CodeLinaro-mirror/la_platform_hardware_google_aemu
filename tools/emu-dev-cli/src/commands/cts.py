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

"""CTS and CTS-Verifier testing commands ('emu-dev-cli cts')."""

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any, TextIO
import zipfile

from clients.artifact_service import (
    HttpStatusError,
    HttpTransport,
    UrllibHttpTransport,
)
from commands.source_directory import find_file_in_source_directories
from core.command import (
    Command,
    CommandGroup,
    CommandResult,
    STATUS_ERROR,
    STATUS_SUCCESS,
    bind_command,
)
from core.context import ExecutionContext
from core.errors import ExecutionError, ExitCode, UsageError
from core.process import ProcessResult, ProcessRunner
from lib.bazel import find_bazel_cmd
from lib.output import format_markdown_table, print_status

__all__ = [
    "CtsCommand",
    "CtsRunVerifierCommand",
    "discover_available_modules",
    "find_verifier_scripts_dir",
    "register_parser",
]

ARCH_ARM64 = "arm64"
ARCH_X86_64 = "x86_64"
PUBLIC_VERIFIER_RELEASE = "17_r1"
PUBLIC_VERIFIER_URLS = {
    ARCH_X86_64:
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-x86.zip",
    ARCH_ARM64:
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-arm.zip",
}

VERIFIER_REL_PATH = Path("third_party", "adt-infra", "goldfish_test", "xts", "verifier")
VERIFIER_BZL_REL_PATH = Path("third_party", "adt-infra", "goldfish_test", "xts", "verifier.bzl")
TEST_BUILDER_REL_PATH = Path("automation_dev", "test_builder.py")
SOURCE_DIR_BRANCH = "emu-main-next"

CTS_VERIFIER_ZIP = "android-cts-verifier.zip"
CTS_VERIFIER_APK = "CtsVerifier.apk"
CTS_VERIFIER_PACKAGE = "com.android.cts.verifier"
EXTRACTED_DIR_NAME = "extracted"
EXTRACTED_APK_DIR_NAME = "android-cts-verifier"
RESULTS_DIR_NAME = "cts-verifier-results"
TEST_RESULT_XML = "test_result.xml"
INSTALL_FAILED_OLDER_SDK = "INSTALL_FAILED_OLDER_SDK"
SCRIPT_PREFIX = "pass_"
SCRIPT_SUFFIX = ".py"

DEFAULT_AB_BRANCH = "trunk-release"
TARGET_TEST_SUITES_ARM64 = "test_suites_arm64"
TARGET_TEST_SUITES_X86_64 = "test_suites_x86_64"

AUTOMATION_DEV_TARGET = "@goldfish_test//xts:cts-verifier-automation-dev"
ETS_VERIFIER_SUITE = "@goldfish_test//xts:ets-verifier"
DEFAULT_TEST_BUILDER_MODULE = "screen_lock"
CLOCK_TEST_ALIASES = frozenset({"clock", "clocktest", "clock_test"})
CLOCK_TEST_SUBNAME = "ClockTest"
RBE_TEST_FLAGS = (
    "-c",
    "opt",
    "--config=remote",
    "--sandbox_debug",
    "--nocache_test_results",
    "--config=ants",
    "--config=sponge",
    "--flaky_test_attempts=8",
)

# Device settings and app-ops that let the CtsVerifier automation drive the UI.
DEVICE_SETUP_COMMANDS = (
    ("settings", "put", "global", "hidden_api_policy", "1"),
    ("appops", "set", CTS_VERIFIER_PACKAGE, "android:read_device_identifiers", "allow"),
    ("appops", "set", CTS_VERIFIER_PACKAGE, "MANAGE_EXTERNAL_STORAGE", "0"),
    ("appops", "set", CTS_VERIFIER_PACKAGE, "TURN_SCREEN_ON", "0"),
    ("am", "compat", "enable", "ALLOW_TEST_API_ACCESS", CTS_VERIFIER_PACKAGE),
)

ACTION_RUN = "run-cts-verifier"
ACTION_LIST = "list-modules"
MODE_ALL = "all"
MODE_COLLECT = "collect-test-module-names"
BANNER_WIDTH = 50
BYTES_PER_MB = 1024 * 1024
LOG_SNIPPET_CHARS = 1500

_ADB_CMD_TIMEOUT_SECS = 15
_APK_INSTALL_TIMEOUT_SECS = 120
_TEST_SCRIPT_TIMEOUT_SECS = 600
_BAZEL_RUN_TIMEOUT_SECS = 1800

ScriptsDirLocator = Callable[[], tuple[str | None, str | None]]


def find_verifier_scripts_dir():
    rel_path = os.path.join("third_party", "adt-infra", "goldfish_test", "xts",
                            "verifier")
    found = find_file_in_source_directories(rel_path)
    if found:
        return found, f"source-directory registry ({found})"

    # Fallback: walk relative directories from script location
    curr = os.path.abspath(__file__)
    for _ in range(7):
        curr = os.path.dirname(curr)
        candidate = os.path.join(curr, rel_path)
        if os.path.isdir(candidate):
            return candidate, f"relative repo path ({candidate})"

    return None, None


def discover_available_modules(scripts_dir):
    """
    Dynamically scans third_party/adt-infra/goldfish_test/xts/verifier/
    for pass_*.py automation scripts without hardcoding module lists.
    """
    modules = {}
    if not scripts_dir or not os.path.isdir(scripts_dir):
        return modules

    for script_name in os.listdir(scripts_dir):
        if not script_name.startswith("pass_") or not script_name.endswith(
                ".py"):
            continue
        # Base alias from filename: strip 'pass_' and '_test.py'
        base_name = script_name[len("pass_"):-len(".py")]
        if base_name.endswith("_test"):
            base_name = base_name[:-len("_test")]

        modules[base_name] = script_name

        # Create short convenient aliases (e.g. 'pass_other_tts_test.py' -> 'tts', 'pass_vibrations_has_vibrator_test.py' -> 'vibrations')
        short_parts = base_name.split("_")
        if len(short_parts) > 1:
            # Add last word (e.g., 'tts', 'vibrator', 'service') if not ambiguous
            short_alias = short_parts[-1]
            if short_alias not in modules:
                modules[short_alias] = script_name
            # If path was like 'other_battery_saver', register 'battery_saver'
            if len(short_parts) >= 3 and short_parts[0] in (
                    "other", "tiles", "projection", "sharesheet", "vibrations"):
                sub_alias = "_".join(short_parts[1:])
                if sub_alias not in modules:
                    modules[sub_alias] = script_name

    return modules


def _json_mode(args: argparse.Namespace, ctx: ExecutionContext) -> bool:
    return bool(ctx.config.json_mode or getattr(args, "json", False))


def _has_direct_only_options(args: argparse.Namespace) -> bool:
    """Options that only make sense over ADB switch the run away from Bazel."""
    return bool(
        getattr(args, "serial", None)
        or getattr(args, "build_id", None)
        or getattr(args, "target", None)
        or getattr(args, "force_download", False)
        or getattr(args, "branch", DEFAULT_AB_BRANCH) != DEFAULT_AB_BRANCH
    )


def _normalize_module_name(raw: str) -> str:
    """Strips script prefixes and suffixes so 'pass_x.py', 'run_x.sh' and 'x' compare equal."""
    return (
        raw.lower()
        .replace("pass_", "")
        .replace("run_", "")
        .replace(".py", "")
        .replace(".sh", "")
    )


def _module_label(script_file: str) -> str:
    return script_file[len(SCRIPT_PREFIX):-len(SCRIPT_SUFFIX)]


def _match_run_script(module: str, verifier_dir: Path) -> str | None:
    """Maps a module onto a run_<name>.sh launcher: exact (or '_test') match first, then substring."""
    if not verifier_dir.is_dir():
        return None
    launchers = sorted(
        p.name for p in verifier_dir.iterdir()
        if p.name.startswith("run_") and p.name.endswith(".sh")
    )
    stems = [name.replace("run_", "").replace(".sh", "") for name in launchers]
    exact = next((s for s in stems if s in (module, f"{module}_test")), None)
    return exact or next((s for s in stems if module in s), None)


def _resolve_ets_subname(
    module: str, discovered: Mapping[str, str], source_dir: Path
) -> str:
    mod = _normalize_module_name(module)
    if mod in CLOCK_TEST_ALIASES:
        return CLOCK_TEST_SUBNAME
    if mod in discovered:
        return discovered[mod].replace("pass_", "").replace(".py", "")
    return _match_run_script(mod, source_dir / VERIFIER_REL_PATH) or mod


def _test_builder_script(module: str | None) -> str:
    raw = _normalize_module_name(module or DEFAULT_TEST_BUILDER_MODULE).replace(" ", "_")
    if not raw.endswith("_test"):
        raw += "_test"
    return f"run_{raw}.sh"


@dataclass(frozen=True)
class _BazelPlan:
    target: str
    cmd: list[str]
    banner: str


def _plan_bazel_run(
    args: argparse.Namespace,
    bazel_bin: str,
    discovered: Mapping[str, str],
    source_dir: Path,
) -> _BazelPlan:
    """Picks the Bazel target and command line for the requested CTS-Verifier mode."""
    if getattr(args, "collect_test_module_names", False):
        return _BazelPlan(
            AUTOMATION_DEV_TARGET,
            [bazel_bin, "run", AUTOMATION_DEV_TARGET, "--", "--collect_tests", "--window"],
            f"Executing hermetic CTS-Verifier test label collector via Bazel ({bazel_bin}): {AUTOMATION_DEV_TARGET}...",
        )
    module = getattr(args, "module", None)
    if getattr(args, "test_builder_mode", False):
        return _BazelPlan(
            AUTOMATION_DEV_TARGET,
            [
                bazel_bin, "run", AUTOMATION_DEV_TARGET, "--", "--dev_mode",
                "--window", "--script", _test_builder_script(module),
            ],
            f"Executing hermetic CTS-Verifier TestBuilder via Bazel ({bazel_bin}): {AUTOMATION_DEV_TARGET}...",
        )

    run_suite = bool(getattr(args, "all", False)) or not module
    target = (
        ETS_VERIFIER_SUITE
        if run_suite
        else f"{ETS_VERIFIER_SUITE}.{_resolve_ets_subname(module, discovered, source_dir)}"
    )
    window_flag = "--window" if getattr(args, "window", True) else "--no-window"
    if getattr(args, "rbe", False):
        return _BazelPlan(
            target,
            [bazel_bin, "test", *RBE_TEST_FLAGS, target],
            f"Executing hermetic ETS-Verifier test target on Remote Build Execution (RBE) via Bazel ({bazel_bin}): {target}...",
        )
    if run_suite:
        return _BazelPlan(
            target,
            [bazel_bin, "test", target, f"--test_arg={window_flag}"],
            f"Executing hermetic ETS-Verifier test suite via Bazel ({bazel_bin}): {target}...",
        )
    return _BazelPlan(
        target,
        [bazel_bin, "run", target, "--", window_flag],
        f"Executing hermetic ETS-Verifier test target via Bazel ({bazel_bin}): {target}...",
    )


def _bazel_source_dir(ctx: ExecutionContext) -> Path:
    """Uses the configured emu-main-next checkout only if it has verifier.bzl, else the cwd."""
    configured = ctx.config.get_source_directory(SOURCE_DIR_BRANCH)
    if configured and (Path(configured) / VERIFIER_BZL_REL_PATH).exists():
        return Path(configured)
    return Path.cwd()


def _require_scripts_dir(scripts_dir: Path | None) -> Path:
    if scripts_dir is None:
        raise ExecutionError(
            f"Could not locate the CTS-Verifier scripts directory ({VERIFIER_REL_PATH.as_posix()}) "
            "in any configured source directory.",
            remediation=(
                f"Register your checkout with 'emu-dev-cli source-directory set {SOURCE_DIR_BRANCH} <path>', "
                "or drop --direct to run the tests through Bazel."
            ),
        )
    return scripts_dir


def _resolve_module_script(
    module: str, scripts_dir: Path, discovered: Mapping[str, str]
) -> str:
    """Maps --module onto a pass_*.py script by alias, or by its file name inside the scripts dir."""
    name = module.lower()
    if name in discovered:
        return discovered[name]
    candidate = name if name.endswith(SCRIPT_SUFFIX) else f"{name}{SCRIPT_SUFFIX}"
    if Path(candidate).name == candidate and (scripts_dir / candidate).is_file():
        return candidate
    raise UsageError(
        f"Unknown CTS-Verifier module '{module}' (no matching pass_*.py script in {scripts_dir}).",
        remediation="Run 'emu-dev-cli cts run-cts-verifier --list-modules' to see the discovered modules.",
    )


def _resolve_serial(runner: ProcessRunner, requested: str | None) -> str:
    if requested:
        return requested
    res = runner.run(["adb", "devices"], timeout_sec=float(_ADB_CMD_TIMEOUT_SECS))
    for line in res.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    detail = (
        f"'adb devices' exited with {res.returncode}: {res.stderr.strip()}"
        if not res.ok
        else "'adb devices' listed no device in the 'device' state"
    )
    raise ExecutionError(
        f"No online ADB device or emulator found for direct CTS-Verifier execution ({detail}).",
        remediation="Launch an emulator first, or pass --serial <device_serial>.",
    )


def _detect_arch(runner: ProcessRunner, serial: str) -> str:
    res = runner.run(
        ["adb", "-s", serial, "shell", "getprop", "ro.product.cpu.abi"],
        timeout_sec=float(_ADB_CMD_TIMEOUT_SECS),
    )
    abi = res.stdout.strip().lower()
    return ARCH_ARM64 if ("arm" in abi or "aarch64" in abi) else ARCH_X86_64


def _extract_archive(archive: Path, extracted_dir: Path) -> None:
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(extracted_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExecutionError(
            f"Could not extract CTS-Verifier archive '{archive}': {exc}.",
            remediation="Re-run with --force-download to fetch a fresh copy of the archive.",
            cause=exc,
        ) from exc


def _install_verifier(
    runner: ProcessRunner, serial: str, apk: Path, json_mode: bool
) -> None:
    """Installs CtsVerifier.apk plus its companion APKs; install failures only warn, as before."""
    adb = ["adb", "-s", serial]
    print_status(f"Installing CtsVerifier.apk onto device {serial}...", json_mode=json_mode)
    res = runner.run(
        [*adb, "install", "-r", "-g", str(apk)],
        timeout_sec=float(_APK_INSTALL_TIMEOUT_SECS),
    )
    if not res.ok:
        print_status(f"⚠️  adb install warning: {res.stderr.strip()}", json_mode=json_mode)
        if INSTALL_FAILED_OLDER_SDK in res.stderr:
            print_status(
                "  Hint: DEVICE is a release build while APK targeted DEV preview. Supply matching --build-id.",
                json_mode=json_mode,
            )
    for helper in sorted(apk.parent.glob("*.apk")):
        if helper.name == CTS_VERIFIER_APK:
            continue
        print_status(
            f"Installing companion APK {helper.name} onto device {serial}...",
            json_mode=json_mode,
        )
        runner.run(
            [*adb, "install", "-r", "-g", str(helper)],
            timeout_sec=float(_APK_INSTALL_TIMEOUT_SECS),
        )


def _configure_device(runner: ProcessRunner, serial: str) -> None:
    for setting in DEVICE_SETUP_COMMANDS:
        runner.run(
            ["adb", "-s", serial, "shell", *setting],
            timeout_sec=float(_ADB_CMD_TIMEOUT_SECS),
        )


def _script_env(apk: Path, out_dir: Path, serial: str) -> dict[str, str]:
    return {
        **os.environ,
        "CTS_APK_PATH": str(apk),
        "CTS_OUTPUT_DIR": str(out_dir),
        "ANDROID_SERIAL": serial,
    }


def _find_xml_report(out_dir: Path) -> str | None:
    reports = sorted(out_dir.rglob(TEST_RESULT_XML))
    return str(reports[0]) if reports else None


def _log_snippet(res: ProcessResult) -> str:
    output = "\n".join(part for part in (res.stdout.strip(), res.stderr.strip()) if part)
    return output[-LOG_SNIPPET_CHARS:]


def _describe_exit(res: ProcessResult, timeout_sec: int) -> str:
    if res.timed_out:
        return f"timed out after {timeout_sec}s"
    return f"exit_code={res.returncode}"


def _process_result(
    data: dict[str, Any], res: ProcessResult, summary: str
) -> CommandResult[dict[str, Any]]:
    """Wraps a child process outcome, passing its exit code through like the original CLI."""
    snippet = _log_snippet(res)
    if snippet:
        data["log_snippet"] = snippet
    return CommandResult(
        data=data,
        action=ACTION_RUN,
        summary=summary,
        status=STATUS_SUCCESS if res.ok else STATUS_ERROR,
        exit_code=res.returncode,
    )


def _list_modules_result(
    scripts_dir: Path | None, origin: str | None, discovered: Mapping[str, str]
) -> CommandResult[dict[str, Any]]:
    rows = [[f"`{name}`", script] for name, script in sorted(discovered.items())]
    data: dict[str, Any] = {
        "status": STATUS_SUCCESS,
        "action": ACTION_LIST,
        "scripts_directory": str(scripts_dir) if scripts_dir else None,
        "origin": origin,
        "modules": dict(discovered),
    }
    return CommandResult(
        data=data,
        action=ACTION_LIST,
        summary=f"Discovered {len(discovered)} automated CTS-Verifier module(s).",
        markdown_table=format_markdown_table(["Module", "Script"], rows),
    )


class _DownloadProgress:
    """Writes an in-place percentage line for HttpTransport.download progress callbacks."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, downloaded: int, total: int) -> None:
        if total <= 0:
            return
        self._stream.write(
            f"\rDownloading... {downloaded * 100 / total:5.1f}% "
            f"[{downloaded / BYTES_PER_MB:.1f}MB / {total / BYTES_PER_MB:.1f}MB]"
        )
        self._stream.flush()

    def finish(self) -> None:
        self._stream.write("\nDownload complete.\n")
        self._stream.flush()


class CtsRunVerifierCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli cts run-cts-verifier`."""

    name = "run-cts-verifier"
    help_text = "Download CTS-Verifier package (public release or go/ab build ID) and execute automated tests"

    def __init__(
        self,
        *,
        http_transport: HttpTransport | None = None,
        scripts_dir_locator: ScriptsDirLocator = find_verifier_scripts_dir,
        progress_stream: TextIO | None = None,
    ) -> None:
        self._http = http_transport or UrllibHttpTransport()
        self._scripts_dir_locator = scripts_dir_locator
        self._progress_stream = progress_stream

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--build-id",
            "--bid",
            dest="build_id",
            type=str,
            default=None,
            help="Optional numeric build ID from Android Build (go/ab). If omitted, downloads latest public release from Google CDN",
        )
        parser.add_argument(
            "--module",
            type=str,
            default=None,
            help="Module name to test (run --list-modules to discover available test scripts dynamically)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run all available automated CTS-Verifier test modules sequentially",
        )
        parser.add_argument(
            "--serial",
            type=str,
            default=None,
            help="ADB device serial number (auto-detected via 'adb devices' if omitted)",
        )
        parser.add_argument(
            "--branch",
            type=str,
            default=DEFAULT_AB_BRANCH,
            help="Branch when using --build-id (default: trunk-release)",
        )
        parser.add_argument(
            "--target",
            type=str,
            default=None,
            help="Build target when using --build-id (defaults to test_suites_x86_64 or test_suites_arm64)",
        )
        parser.add_argument(
            "--force-download",
            action="store_true",
            help="Re-download and extract CTS-Verifier archive even if cached",
        )
        parser.add_argument(
            "--list-modules",
            action="store_true",
            help="Dynamically scan and list all automated CTS-Verifier test modules in repository",
        )
        parser.add_argument(
            "--bazel",
            action="store_true",
            default=True,
            help="Execute hermetic CTS-Verifier test target defined in verifier.bzl using Bazel (default)",
        )
        parser.add_argument(
            "--no-bazel",
            "--direct",
            dest="bazel",
            action="store_false",
            help="Execute tests directly against an active emulator device over ADB instead of using Bazel",
        )
        parser.add_argument(
            "--rbe",
            action="store_true",
            default=False,
            help="Execute test target on Remote Build Execution (RBE) using Bazel remote config",
        )
        parser.add_argument(
            "--window",
            action="store_true",
            default=True,
            help="Launch emulator with GUI window enabled (default via bazel run)",
        )
        parser.add_argument(
            "--no-window",
            "--headless",
            dest="window",
            action="store_false",
            help="Launch emulator in headless mode without GUI window",
        )
        parser.add_argument(
            "--test-builder-mode",
            "--test-builder",
            action="store_true",
            dest="test_builder_mode",
            help="Run module using TestBuilder interactive automation generator (automation_dev/test_builder.py)",
        )
        parser.add_argument(
            "--collect-tests",
            "--collect-test-module-names",
            "--collect-labels",
            action="store_true",
            dest="collect_test_module_names",
            help="Scroll through CtsVerifier on device and collect all test activity labels into a JSON file",
        )

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        if (
            not getattr(args, "module", None)
            and not getattr(args, "all", False)
            and not getattr(args, "list_modules", False)
            and not getattr(args, "collect_test_module_names", False)
        ):
            raise UsageError(
                "No CTS-Verifier execution mode specified.",
                remediation="Specify one of --module <name>, --all, --list-modules, or --collect-test-module-names.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        scripts_dir, origin = self._resolve_scripts_dir(ctx)
        discovered = discover_available_modules(scripts_dir)
        if getattr(args, "list_modules", False):
            return _list_modules_result(scripts_dir, origin, discovered)

        use_bazel = bool(getattr(args, "bazel", True)) and not _has_direct_only_options(args)
        if use_bazel:
            return self._run_bazel(args, ctx, discovered)
        if getattr(args, "collect_test_module_names", False):
            return self._collect_labels_direct(args, ctx, scripts_dir)
        return self._run_direct(args, ctx, _require_scripts_dir(scripts_dir), discovered)

    def _resolve_scripts_dir(
        self, ctx: ExecutionContext
    ) -> tuple[Path | None, str | None]:
        for branch_key, src_root in ctx.config.source_directories.items():
            candidate = Path(src_root) / VERIFIER_REL_PATH
            if candidate.is_dir():
                return candidate, f"context config ({branch_key}: {candidate})"
        found, origin = self._scripts_dir_locator()
        return (Path(found), origin) if found else (None, None)

    def _run_bazel(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        discovered: Mapping[str, str],
    ) -> CommandResult[dict[str, Any]]:
        json_mode = _json_mode(args, ctx)
        source_dir = _bazel_source_dir(ctx)
        plan = _plan_bazel_run(args, find_bazel_cmd(source_dir), discovered, source_dir)
        print_status(plan.banner, json_mode=json_mode)
        res = ctx.process_runner.run(
            plan.cmd,
            cwd=source_dir,
            timeout_sec=float(_BAZEL_RUN_TIMEOUT_SECS),
            stream_output=not json_mode,
        )
        data: dict[str, Any] = {
            "status": STATUS_SUCCESS if res.ok else STATUS_ERROR,
            "action": ACTION_RUN,
            "target": plan.target,
            "command": plan.cmd,
            "exit_code": res.returncode,
        }
        outcome = "passed" if res.ok else "failed"
        return _process_result(
            data,
            res,
            f"Bazel CTS-Verifier target {plan.target} {outcome} "
            f"({_describe_exit(res, _BAZEL_RUN_TIMEOUT_SECS)}).",
        )

    def _collect_labels_direct(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        scripts_dir: Path | None,
    ) -> CommandResult[dict[str, Any]]:
        json_mode = _json_mode(args, ctx)
        builder = _require_scripts_dir(scripts_dir) / TEST_BUILDER_REL_PATH
        if not builder.is_file():
            raise ExecutionError(
                f"CtsVerifier TestBuilder script not found at '{builder}'.",
                remediation="Sync third_party/adt-infra in your checkout, or drop --direct to collect labels through Bazel.",
            )
        print_status("🔍 Running CtsVerifier test label collector over ADB...", json_mode=json_mode)
        res = ctx.process_runner.run(
            [sys.executable, str(builder), "--collect-test-module-names"],
            timeout_sec=float(_TEST_SCRIPT_TIMEOUT_SECS),
            stream_output=not json_mode,
        )
        data: dict[str, Any] = {
            "status": STATUS_SUCCESS if res.ok else STATUS_ERROR,
            "action": ACTION_RUN,
            "mode": MODE_COLLECT,
            "script": str(builder),
            "exit_code": res.returncode,
        }
        return _process_result(
            data,
            res,
            f"CtsVerifier test label collection finished ({_describe_exit(res, _TEST_SCRIPT_TIMEOUT_SECS)}).",
        )

    def _run_direct(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        scripts_dir: Path,
        discovered: Mapping[str, str],
    ) -> CommandResult[dict[str, Any]]:
        run_all = bool(getattr(args, "all", False))
        scripts = (
            sorted(set(discovered.values()))
            if run_all
            else [_resolve_module_script(str(args.module), scripts_dir, discovered)]
        )
        json_mode = _json_mode(args, ctx)
        runner = ctx.process_runner
        serial = _resolve_serial(runner, getattr(args, "serial", None))
        apk = self._prepare_verifier_apk(args, ctx, _detect_arch(runner, serial), json_mode)
        _install_verifier(runner, serial, apk, json_mode)
        _configure_device(runner, serial)

        results_root = ctx.config.cache_dir / RESULTS_DIR_NAME
        if run_all:
            return self._run_all_modules(
                runner, scripts_dir, scripts, apk, serial, results_root, json_mode
            )

        module_name = str(args.module).lower()
        out_dir = results_root / module_name
        out_dir.mkdir(parents=True, exist_ok=True)
        script = scripts[0]
        print_status("-" * BANNER_WIDTH, json_mode=json_mode)
        print_status(
            f"🚀 Running CTS-Verifier module: {module_name} ({script})",
            json_mode=json_mode,
        )
        print_status("-" * BANNER_WIDTH, json_mode=json_mode)
        res = self._run_script(runner, scripts_dir, script, _script_env(apk, out_dir, serial), json_mode)
        build_id = getattr(args, "build_id", None)
        data: dict[str, Any] = {
            "status": STATUS_SUCCESS if res.ok else STATUS_ERROR,
            "action": ACTION_RUN,
            "module": module_name,
            "script": script,
            "device_serial": serial,
            "verifier_apk": str(apk),
            "source_mode": (
                f"go/ab build-id {build_id}" if build_id else f"public CDN {PUBLIC_VERIFIER_RELEASE}"
            ),
            "exit_code": res.returncode,
            "xml_report": _find_xml_report(out_dir),
        }
        return _process_result(
            data,
            res,
            f"CTS-Verifier module '{module_name}' on {serial} "
            f"{'passed' if res.ok else 'failed'} ({_describe_exit(res, _TEST_SCRIPT_TIMEOUT_SECS)}).",
        )

    def _run_all_modules(
        self,
        runner: ProcessRunner,
        scripts_dir: Path,
        scripts: Sequence[str],
        apk: Path,
        serial: str,
        results_root: Path,
        json_mode: bool,
    ) -> CommandResult[dict[str, Any]]:
        print_status("-" * BANNER_WIDTH, json_mode=json_mode)
        print_status(
            f"🚀 Running ALL {len(scripts)} CTS-Verifier automated modules...",
            json_mode=json_mode,
        )
        print_status("-" * BANNER_WIDTH, json_mode=json_mode)
        results: list[dict[str, Any]] = []
        for idx, script in enumerate(scripts, 1):
            label = _module_label(script)
            print_status(
                f"\n[{idx}/{len(scripts)}] Module: {label} ({script})", json_mode=json_mode
            )
            out_dir = results_root / label
            out_dir.mkdir(parents=True, exist_ok=True)
            res = self._run_script(
                runner, scripts_dir, script, _script_env(apk, out_dir, serial), json_mode
            )
            results.append({
                "module": label,
                "script": script,
                "passed": res.ok,
                "exit_code": res.returncode,
            })

        failed = sum(1 for r in results if not r["passed"])
        print_status("\n" + "=" * BANNER_WIDTH, json_mode=json_mode)
        print_status("📊 CTS-Verifier --all Execution Summary:", json_mode=json_mode)
        for r in results:
            status_str = "✓ PASS" if r["passed"] else "❌ FAIL"
            print_status(f"  {status_str}  {r['module']:<35} ({r['script']})", json_mode=json_mode)
        print_status("=" * BANNER_WIDTH, json_mode=json_mode)

        exit_code = int(ExitCode.FAILURE) if failed else int(ExitCode.SUCCESS)
        data: dict[str, Any] = {
            "status": STATUS_ERROR if failed else STATUS_SUCCESS,
            "action": ACTION_RUN,
            "mode": MODE_ALL,
            "total_modules": len(results),
            "failed_modules": failed,
            "exit_code": exit_code,
            "results": results,
        }
        return CommandResult(
            data=data,
            action=ACTION_RUN,
            summary=f"Ran {len(results)} CTS-Verifier module(s) on {serial}: {failed} failed.",
            status=STATUS_ERROR if failed else STATUS_SUCCESS,
            exit_code=exit_code,
        )

    def _run_script(
        self,
        runner: ProcessRunner,
        scripts_dir: Path,
        script: str,
        env: Mapping[str, str],
        json_mode: bool,
    ) -> ProcessResult:
        return runner.run(
            [sys.executable, str(scripts_dir / script)],
            cwd=scripts_dir,
            env=env,
            timeout_sec=float(_TEST_SCRIPT_TIMEOUT_SECS),
            stream_output=not json_mode,
        )

    def _prepare_verifier_apk(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        arch: str,
        json_mode: bool,
    ) -> Path:
        """Returns the cached CtsVerifier.apk, downloading and extracting the archive when needed."""
        build_id = getattr(args, "build_id", None)
        cache_dir = ctx.config.cache_dir / (
            f"cts-verifier-bid-{build_id}-{arch}"
            if build_id
            else f"cts-verifier-public-{PUBLIC_VERIFIER_RELEASE}-{arch}"
        )
        extracted_dir = cache_dir / EXTRACTED_DIR_NAME
        apk = extracted_dir / EXTRACTED_APK_DIR_NAME / CTS_VERIFIER_APK
        if getattr(args, "force_download", False) or not apk.is_file():
            extracted_dir.mkdir(parents=True, exist_ok=True)
            archive = (
                self._fetch_from_android_build(args, ctx, arch, cache_dir, json_mode)
                if build_id
                else self._download_public_release(arch, cache_dir / CTS_VERIFIER_ZIP, json_mode)
            )
            print_status(
                "Extracting CTS-Verifier package contents from archive...", json_mode=json_mode
            )
            _extract_archive(archive, extracted_dir)
        if not apk.is_file():
            raise ExecutionError(
                f"CtsVerifier.apk was not found at '{apk}' after extracting the CTS-Verifier archive.",
                remediation=(
                    f"Check that the archive contains {EXTRACTED_APK_DIR_NAME}/{CTS_VERIFIER_APK}, "
                    "then re-run with --force-download."
                ),
            )
        print_status(f"✓ CtsVerifier.apk ready at: {apk}", json_mode=json_mode)
        return apk

    def _fetch_from_android_build(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        arch: str,
        cache_dir: Path,
        json_mode: bool,
    ) -> Path:
        build_id = str(args.build_id)
        branch = str(getattr(args, "branch", DEFAULT_AB_BRANCH))
        target = getattr(args, "target", None) or (
            TARGET_TEST_SUITES_ARM64 if arch == ARCH_ARM64 else TARGET_TEST_SUITES_X86_64
        )
        print_status(
            f"Fetching CTS-Verifier from Android Build (bid={build_id}, branch={branch}, target={target})...",
            json_mode=json_mode,
        )
        return ctx.artifact_service.download_artifact(
            build_id, target, CTS_VERIFIER_ZIP, cache_dir, branch=branch
        )

    def _download_public_release(
        self, arch: str, dest_zip: Path, json_mode: bool
    ) -> Path:
        url = PUBLIC_VERIFIER_URLS[arch]
        print_status(
            f"Downloading CTS-Verifier package from:\n  {url}\n  -> {dest_zip}",
            json_mode=json_mode,
        )
        progress = None if json_mode else _DownloadProgress(self._progress_stream or sys.stdout)
        try:
            self._http.download(url, dest_zip, progress_callback=progress)
        except HttpStatusError as exc:
            raise ExecutionError(
                f"Downloading the public CTS-Verifier package from {url} failed "
                f"with HTTP {exc.status_code} ({exc.reason}).",
                remediation="Check network access to dl.google.com, or pass --build-id <id> to fetch CTS-Verifier from go/ab instead.",
                cause=exc,
            ) from exc
        if progress is not None:
            progress.finish()
        return dest_zip


class CtsCommand(CommandGroup):
    """Top-level `cts` subcommand group."""

    name = "cts"
    help_text = "CTS and CTS-Verifier testing tools for emulator verification"
    subcommand_dest = "cts_cmd"
    subcommand_help = "Available CTS subcommands"

    def __init__(self, verifier: CtsRunVerifierCommand | None = None) -> None:
        super().__init__([verifier or CtsRunVerifierCommand()])


def register_parser(subparsers: Any) -> None:
    """Registers 'emu-dev-cli cts' and its subcommands."""
    bind_command(subparsers, CtsCommand())
