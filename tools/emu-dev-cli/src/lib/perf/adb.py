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

"""ADB device transport abstraction backed by ProcessRunner."""

from collections.abc import Callable, Sequence
import subprocess
import sys
from typing import Any, List, Optional

from core.process import ProcessResult, ProcessRunner, SubprocessRunner
from lib.perf.catalog import DEFAULT_ADB_BIN

DEFAULT_ADB_TIMEOUT_SEC: float = 180.0
DEFAULT_ADB_DEVICES_TIMEOUT_SEC: float = 15.0
DEFAULT_SHELL_TIMEOUT_SEC: float = 30.0
DEFAULT_GETPROP_TIMEOUT_SEC: float = 5.0
DEFAULT_EMU_TIMEOUT_SEC: float = 60.0
DEFAULT_WAIT_FOR_DEVICE_TIMEOUT_SEC: float = 15.0
DEFAULT_DISCONNECT_TIMEOUT_SEC: float = 3.0
DEFAULT_DROP_CACHES_TIMEOUT_SEC: float = 15.0

DROP_CACHES_SHELL_CMD: str = (
    "sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true"
)


def _adb_cmd(
    serial: str,
    args: List[str],
    adb_bin: str = DEFAULT_ADB_BIN,
    timeout: float = DEFAULT_ADB_TIMEOUT_SEC,
    process_runner: Optional[ProcessRunner] = None,
) -> subprocess.CompletedProcess:
    """Executes `adb -s <serial> <args>` via ProcessRunner and returns a CompletedProcess."""
    runner = process_runner or SubprocessRunner()
    cmd = [adb_bin, "-s", serial] + list(args)
    res = runner.run(cmd, check=False, timeout_sec=timeout)
    stderr_text = res.stderr
    if res.timed_out and not stderr_text:
        stderr_text = f"Command {cmd} timed out after {timeout} seconds"
    return subprocess.CompletedProcess(
        cmd,
        returncode=res.returncode,
        stdout=res.stdout,
        stderr=stderr_text,
    )


def _resolve_patched_adb_cmd() -> Optional[Callable[..., Any]]:  # type: ignore[name-defined]
    """Returns `lib.perf_runner._adb_cmd` if a caller patched it at runtime."""
    pr_mod = sys.modules.get("lib.perf_runner")
    if pr_mod is None:
        return None
    fn = getattr(pr_mod, "_adb_cmd", None)
    if fn is not None and fn is not _adb_cmd:
        return fn
    return None


class AdbDevice:
    """Encapsulates ADB operations for a specific device serial over ProcessRunner."""

    def __init__(
        self,
        serial: str,
        *,
        adb_bin: str = DEFAULT_ADB_BIN,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        if not serial or not str(serial).strip():
            raise ValueError(
                "AdbDevice requires a non-empty device serial (e.g. 'emulator-5554')."
            )
        self.serial: str = str(serial).strip()
        self.adb_bin: str = adb_bin or DEFAULT_ADB_BIN
        self._explicit_runner: bool = process_runner is not None
        self.process_runner: ProcessRunner = process_runner or SubprocessRunner()

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = DEFAULT_ADB_TIMEOUT_SEC,
        check: bool = False,
    ) -> ProcessResult:
        """Executes `adb -s <serial> <args>` and returns a structured ProcessResult."""
        if not self._explicit_runner:
            patched_fn = _resolve_patched_adb_cmd()
            if patched_fn is not None:
                cp = patched_fn(
                    self.serial,
                    list(args),
                    adb_bin=self.adb_bin,
                    timeout=timeout,
                )
                res = ProcessResult(
                    args=(self.adb_bin, "-s", self.serial, *[str(a) for a in args]),
                    returncode=int(getattr(cp, "returncode", 0)),
                    stdout=str(getattr(cp, "stdout", "") or ""),
                    stderr=str(getattr(cp, "stderr", "") or ""),
                )
                if check:
                    res.check_returncode()
                return res

        cmd = [self.adb_bin, "-s", self.serial, *[str(a) for a in args]]
        return self.process_runner.run(
            cmd,
            timeout_sec=timeout,
            check=check,
        )

    def run_completed(
        self,
        args: Sequence[str],
        timeout: float = DEFAULT_ADB_TIMEOUT_SEC,
    ) -> subprocess.CompletedProcess:
        """Executes an ADB command and returns `subprocess.CompletedProcess` for callback compatibility."""
        if not self._explicit_runner:
            patched_fn = _resolve_patched_adb_cmd()
            if patched_fn is not None:
                cp = patched_fn(
                    self.serial,
                    list(args),
                    adb_bin=self.adb_bin,
                    timeout=timeout,
                )
                return subprocess.CompletedProcess(
                    [self.adb_bin, "-s", self.serial, *[str(a) for a in args]],
                    returncode=int(getattr(cp, "returncode", 0)),
                    stdout=str(getattr(cp, "stdout", "") or ""),
                    stderr=str(getattr(cp, "stderr", "") or ""),
                )

        res = self.run(args, timeout=timeout, check=False)
        stderr_text = res.stderr
        if res.timed_out and not stderr_text:
            stderr_text = f"Command {list(res.args)} timed out after {timeout} seconds"
        return subprocess.CompletedProcess(
            list(res.args),
            returncode=res.returncode,
            stdout=res.stdout,
            stderr=stderr_text,
        )

    def as_adb_fn(
        self, default_timeout: float = DEFAULT_SHELL_TIMEOUT_SEC
    ) -> Callable[[List[str], float], subprocess.CompletedProcess]:
        """Returns a `(args, timeout) -> CompletedProcess` callable for profiler/probe sessions."""

        def _invoke(
            args: List[str], timeout: float = default_timeout
        ) -> subprocess.CompletedProcess:
            return self.run_completed(args, timeout=timeout)

        return _invoke

    def shell(
        self,
        command: str | Sequence[str],
        *,
        timeout: float = DEFAULT_SHELL_TIMEOUT_SEC,
    ) -> ProcessResult:
        """Executes `adb -s <serial> shell ...`."""
        if isinstance(command, str):
            shell_args = ["shell", command]
        else:
            shell_args = ["shell", *[str(part) for part in command]]
        return self.run(shell_args, timeout=timeout)

    def getprop(
        self,
        prop_name: str,
        *,
        timeout: float = DEFAULT_GETPROP_TIMEOUT_SEC,
    ) -> str:
        """Reads an Android system property via `adb shell getprop <prop_name>`."""
        res = self.shell(["getprop", prop_name], timeout=timeout)
        if res.returncode != 0:
            return ""
        return res.stdout.strip()

    def push(
        self,
        local_path: str,
        remote_path: str,
        *,
        timeout: float = DEFAULT_ADB_TIMEOUT_SEC,
    ) -> ProcessResult:
        """Pushes `local_path` from host to `remote_path` on the guest."""
        return self.run(["push", local_path, remote_path], timeout=timeout)

    def pull(
        self,
        remote_path: str,
        local_path: str,
        *,
        timeout: float = DEFAULT_ADB_TIMEOUT_SEC,
    ) -> ProcessResult:
        """Pulls `remote_path` from the guest to `local_path` on the host."""
        return self.run(["pull", remote_path, local_path], timeout=timeout)

    def emu(
        self,
        emu_args: Sequence[str],
        *,
        timeout: float = DEFAULT_EMU_TIMEOUT_SEC,
    ) -> ProcessResult:
        """Executes an emulator console command via `adb -s <serial> emu ...`."""
        return self.run(["emu", *[str(a) for a in emu_args]], timeout=timeout)

    def wait_for_device(
        self, *, timeout: float = DEFAULT_WAIT_FOR_DEVICE_TIMEOUT_SEC
    ) -> ProcessResult:
        """Blocks until `adb -s <serial> wait-for-device` returns or times out."""
        return self.run(["wait-for-device"], timeout=timeout)

    def disconnect(
        self, *, timeout: float = DEFAULT_DISCONNECT_TIMEOUT_SEC
    ) -> ProcessResult:
        """Disconnects `serial` from the host ADB server."""
        return self.run(["disconnect"], timeout=timeout)

    def drop_caches(
        self, *, timeout: float = DEFAULT_DROP_CACHES_TIMEOUT_SEC
    ) -> ProcessResult:
        """Flushes guest filesystem buffers and drops reclaimable page/slab caches."""
        return self.shell(DROP_CACHES_SHELL_CMD, timeout=timeout)


def detect_active_adb_serial(
    adb_bin: str = DEFAULT_ADB_BIN,
    process_runner: Optional[ProcessRunner] = None,
) -> str:
    """Detects a single running emulator serial via `adb devices`."""
    runner = process_runner or SubprocessRunner()
    res = runner.run(
        [adb_bin, "devices"],
        check=False,
        timeout_sec=DEFAULT_ADB_DEVICES_TIMEOUT_SEC,
    )
    serials: List[str] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    emu_serials = [s for s in serials if s.startswith("emulator-")]
    if emu_serials:
        return emu_serials[0]
    if serials:
        return serials[0]
    raise RuntimeError(
        "No active ADB device found (`adb devices`). "
        "Start an emulator or pass `--emulator-dir` / `--target`."
    )
