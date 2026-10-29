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

"""Process execution abstraction and hermetic FakeProcessRunner for emu-dev-cli."""

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import os
import subprocess
import time

from core.errors import (
    ExitCode,
    SubprocessError,
    UsageError,
    redact_command_args,
)
from core.events import EventBus, SubprocessInvoked

LOGGER = logging.getLogger(__name__)

DEFAULT_SUBPROCESS_TIMEOUT_SEC: float = 300.0
TIMEOUT_EXIT_CODE: int = 124
COMMAND_NOT_FOUND_EXIT_CODE: int = 127
PERMISSION_DENIED_EXIT_CODE: int = 126
MAX_ERROR_STDERR_CHARS: int = 400


def _normalize_command_args(
    args: Sequence[str | os.PathLike[str]],
) -> tuple[str, ...]:
    """Validates and converts command arguments to a non-empty tuple of strings."""
    if isinstance(args, (str, bytes)):
        raise UsageError(
            "Subprocess command must be a sequence of arguments, not a raw string.",
            remediation="Pass a list or tuple of arguments such as ['git', 'status'] with shell=False.",
        )
    normalized = tuple(os.fspath(arg) for arg in args)
    if not normalized or not normalized[0].strip():
        raise UsageError(
            "Subprocess command sequence cannot be empty.",
            remediation="Provide a valid executable name as the first element of args.",
        )
    return normalized


def _normalize_cwd(cwd: str | os.PathLike[str] | None) -> str | None:
    """Converts a working directory path to string if provided."""
    if cwd is None:
        return None
    return os.fspath(cwd)


def _coerce_timeout_text(raw: str | bytes | None) -> str:
    """Decodes stdout/stderr captured on TimeoutExpired into UTF-8 text."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return raw
    return ""


def _coerce_returncode(raw_code: object) -> int:
    """Coerces subprocess returncode to int, defaulting non-int mocks to 0."""
    if isinstance(raw_code, int):
        return int(raw_code)
    return 0


def _summarize_stderr(stderr: str) -> str:
    """Returns a concise single-line preview of stderr for error messages."""
    stripped = stderr.strip()
    if not stripped:
        return ""
    if len(stripped) <= MAX_ERROR_STDERR_CHARS:
        return stripped
    return stripped[:MAX_ERROR_STDERR_CHARS] + "..."


@dataclass(frozen=True)
class ProcessResult:
    """Structured, immutable outcome of a subprocess execution."""

    args: tuple[str, ...]
    returncode: int = int(ExitCode.SUCCESS)
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    timed_out: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(str(a) for a in self.args))

    @property
    def ok(self) -> bool:
        """Returns True when the subprocess exited with code 0 without timing out."""
        return self.returncode == int(ExitCode.SUCCESS) and not self.timed_out

    def check_returncode(
        self, remediation: str | None = None
    ) -> "ProcessResult":
        """Raises SubprocessError if the process timed out or exited non-zero."""
        if self.ok:
            return self

        sanitized_cmd = " ".join(redact_command_args(self.args))
        stderr_preview = _summarize_stderr(self.stderr)
        if self.timed_out:
            msg = (
                f"Command '{sanitized_cmd}' timed out after {self.duration_sec:.2f}s "
                f"(exit code {self.returncode})."
            )
            default_fix = (
                "Retry the command or increase the timeout if the operation is expected to take longer."
            )
        else:
            msg = f"Command '{sanitized_cmd}' failed with exit code {self.returncode}."
            default_fix = (
                "Inspect the command stderr output and verify arguments and environment."
            )
        if stderr_preview:
            msg = f"{msg} Stderr: {stderr_preview}"

        raise SubprocessError(
            msg,
            cmd=self.args,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
            remediation=remediation or default_fix,
        )


@dataclass(frozen=True)
class FakeProcessCall:
    """Recorded invocation parameters captured by FakeProcessRunner."""

    args: tuple[str, ...]
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    timeout_sec: float | None = None
    check: bool = False
    input_text: str | None = None
    stream_output: bool = False


class ProcessRunner(ABC):
    """Abstract strategy interface for executing external subprocesses."""

    @abstractmethod
    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = DEFAULT_SUBPROCESS_TIMEOUT_SEC,
        timeout: float | None = None,
        check: bool = False,
        input_text: str | None = None,
        remediation: str | None = None,
        stream_output: bool = False,
    ) -> ProcessResult:
        """Executes `args` without a shell and returns a structured ProcessResult.

        With `stream_output=True` the child writes straight to this process's
        stdout/stderr (long builds, interactive tools), so the result carries
        empty stdout/stderr.
        """


class SubprocessRunner(ProcessRunner):
    """Production ProcessRunner backed by Python's subprocess.run with UTF-8 pipes."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    def _emit_event(
        self, result: ProcessResult, cwd: str | None
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            SubprocessInvoked(
                command=result.args,
                returncode=result.returncode,
                duration_sec=result.duration_sec,
                cwd=cwd,
                timed_out=result.timed_out,
            )
        )

    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = DEFAULT_SUBPROCESS_TIMEOUT_SEC,
        timeout: float | None = None,
        check: bool = False,
        input_text: str | None = None,
        remediation: str | None = None,
        stream_output: bool = False,
    ) -> ProcessResult:
        effective_timeout = timeout if timeout is not None else timeout_sec
        normalized_args = _normalize_command_args(args)
        normalized_cwd = _normalize_cwd(cwd)
        sanitized_args = redact_command_args(normalized_args)
        LOGGER.debug(
            "Executing subprocess: %s (cwd=%s, timeout=%s)",
            " ".join(sanitized_args),
            normalized_cwd,
            effective_timeout,
        )

        start_time = time.monotonic()
        try:
            completed = subprocess.run(
                list(normalized_args),
                cwd=normalized_cwd,
                env=dict(env) if env is not None else None,
                input=input_text,
                capture_output=not stream_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
                shell=False,
            )
            duration = time.monotonic() - start_time
            result = ProcessResult(
                args=normalized_args,
                returncode=_coerce_returncode(completed.returncode),
                stdout=_coerce_timeout_text(completed.stdout),
                stderr=_coerce_timeout_text(completed.stderr),
                duration_sec=duration,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            result = ProcessResult(
                args=normalized_args,
                returncode=TIMEOUT_EXIT_CODE,
                stdout=_coerce_timeout_text(exc.stdout),
                stderr=_coerce_timeout_text(exc.stderr),
                duration_sec=duration,
                timed_out=True,
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start_time
            result = ProcessResult(
                args=normalized_args,
                returncode=COMMAND_NOT_FOUND_EXIT_CODE,
                stdout="",
                stderr=f"Executable not found: {normalized_args[0]} ({exc})",
                duration_sec=duration,
                timed_out=False,
            )
            self._emit_event(result, normalized_cwd)
            if check:
                result.check_returncode(
                    remediation=remediation
                    or f"Verify that '{normalized_args[0]}' is installed and available on PATH."
                )
            return result
        except PermissionError as exc:
            duration = time.monotonic() - start_time
            result = ProcessResult(
                args=normalized_args,
                returncode=PERMISSION_DENIED_EXIT_CODE,
                stdout="",
                stderr=f"Permission denied executing {normalized_args[0]}: {exc}",
                duration_sec=duration,
                timed_out=False,
            )
            self._emit_event(result, normalized_cwd)
            if check:
                result.check_returncode(
                    remediation=remediation
                    or f"Ensure '{normalized_args[0]}' has executable permissions (chmod +x)."
                )
            return result
        except OSError as exc:
            duration = time.monotonic() - start_time
            result = ProcessResult(
                args=normalized_args,
                returncode=int(ExitCode.FAILURE),
                stdout="",
                stderr=f"OS error executing {normalized_args[0]}: {exc}",
                duration_sec=duration,
                timed_out=False,
            )
            self._emit_event(result, normalized_cwd)
            if check:
                result.check_returncode(remediation=remediation)
            return result

        self._emit_event(result, normalized_cwd)
        if check:
            result.check_returncode(remediation=remediation)
        return result


ResponseBuilder = Callable[[FakeProcessCall], ProcessResult]


class FakeProcessRunner(ProcessRunner):
    """Hermetic in-memory ProcessRunner for unit tests without @patch decorators."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        default_result: ProcessResult | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._default_result = default_result
        self._queued_results: deque[ProcessResult] = deque()
        self._prefix_rules: list[tuple[tuple[str, ...], ProcessResult | ResponseBuilder]] = []
        self.calls: list[FakeProcessCall] = []

    def queue_response(
        self,
        *,
        returncode: int = int(ExitCode.SUCCESS),
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        duration_sec: float = 0.01,
    ) -> None:
        """Queues a one-shot response returned on the next `run()` invocation."""
        self._queued_results.append(
            ProcessResult(
                args=(),
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration_sec,
                timed_out=timed_out,
            )
        )

    def register_prefix(
        self,
        prefix: Sequence[str],
        response: ProcessResult | ResponseBuilder,
    ) -> None:
        """Registers a canned ProcessResult or builder callable for commands matching `prefix`."""
        normalized_prefix = tuple(str(part) for part in prefix)
        self._prefix_rules.append((normalized_prefix, response))

    def set_default_response(
        self,
        *,
        returncode: int = int(ExitCode.SUCCESS),
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        """Configures the fallback response when no queued or prefix rule matches."""
        self._default_result = ProcessResult(
            args=(),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )

    def _resolve_result(self, call: FakeProcessCall) -> ProcessResult:
        if self._queued_results:
            template = self._queued_results.popleft()
            return ProcessResult(
                args=call.args,
                returncode=template.returncode,
                stdout=template.stdout,
                stderr=template.stderr,
                duration_sec=template.duration_sec,
                timed_out=template.timed_out,
            )

        for prefix, rule in self._prefix_rules:
            if call.args[: len(prefix)] == prefix:
                if callable(rule):
                    built = rule(call)
                    return ProcessResult(
                        args=call.args,
                        returncode=built.returncode,
                        stdout=built.stdout,
                        stderr=built.stderr,
                        duration_sec=built.duration_sec,
                        timed_out=built.timed_out,
                    )
                return ProcessResult(
                    args=call.args,
                    returncode=rule.returncode,
                    stdout=rule.stdout,
                    stderr=rule.stderr,
                    duration_sec=rule.duration_sec,
                    timed_out=rule.timed_out,
                )

        if self._default_result is not None:
            return ProcessResult(
                args=call.args,
                returncode=self._default_result.returncode,
                stdout=self._default_result.stdout,
                stderr=self._default_result.stderr,
                duration_sec=self._default_result.duration_sec,
                timed_out=self._default_result.timed_out,
            )

        return ProcessResult(args=call.args, returncode=int(ExitCode.SUCCESS))

    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = DEFAULT_SUBPROCESS_TIMEOUT_SEC,
        timeout: float | None = None,
        check: bool = False,
        input_text: str | None = None,
        remediation: str | None = None,
        stream_output: bool = False,
    ) -> ProcessResult:
        effective_timeout = timeout if timeout is not None else timeout_sec
        normalized_args = _normalize_command_args(args)
        normalized_cwd = _normalize_cwd(cwd)
        call = FakeProcessCall(
            args=normalized_args,
            cwd=normalized_cwd,
            env=dict(env) if env is not None else None,
            timeout_sec=effective_timeout,
            check=check,
            input_text=input_text,
            stream_output=stream_output,
        )
        self.calls.append(call)
        result = self._resolve_result(call)

        if self._event_bus is not None:
            self._event_bus.publish(
                SubprocessInvoked(
                    command=result.args,
                    returncode=result.returncode,
                    duration_sec=result.duration_sec,
                    cwd=normalized_cwd,
                    timed_out=result.timed_out,
                )
            )

        if check:
            result.check_returncode(remediation=remediation)
        return result
