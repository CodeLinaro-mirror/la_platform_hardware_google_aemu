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

"""Typed exception hierarchy and canonical CLI exit codes for emu-dev-cli."""

from collections.abc import Mapping, Sequence
from enum import IntEnum
import re
from typing import Any


class ExitCode(IntEnum):
    """Canonical process exit codes for emu-dev-cli commands and workflows."""

    SUCCESS = 0
    FAILURE = 1
    RETRY_OR_LOOP = 2
    AUTH_ERROR = 3
    USAGE_ERROR = 4
    INTERRUPTED = 130


ERROR_CODE_GENERAL = "EMU_CLI_ERROR"
ERROR_CODE_USAGE = "USAGE_ERROR"
ERROR_CODE_EXECUTION = "EXECUTION_ERROR"
ERROR_CODE_AUTH = "AUTH_ERROR"
ERROR_CODE_RETRYABLE = "RETRYABLE_ERROR"
ERROR_CODE_SUBPROCESS = "SUBPROCESS_ERROR"

REDACTED_PLACEHOLDER = "<REDACTED>"

_SENSITIVE_FLAG_NAMES: frozenset[str] = frozenset({
    "--token",
    "--access-token",
    "--auth-token",
    "--api-key",
    "--password",
    "--secret",
    "--cookie",
    "--header",
    "-h",
})

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api_key|apikey|authorization|cookie|bearer)",
    re.IGNORECASE,
)

_INLINE_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)((?:authorization\s*:\s*bearer|bearer)\s+|"
    r"(?:--?(?:access-?|auth-?)?token|--?api-?key|--?password|--?secret|--?cookie|"
    r"authorization)\s*[=:]\s*)([^\s,;\"']+)"
)


def redact_sensitive_text(text: str) -> str:
    """Redacts inline credentials, bearer tokens, and secret flag values from text."""
    if not text:
        return ""
    return _INLINE_SECRET_VALUE_PATTERN.sub(
        rf"\1{REDACTED_PLACEHOLDER}", str(text)
    )


def redact_command_args(args: Sequence[str]) -> tuple[str, ...]:
    """Redacts secret values following sensitive CLI flags in a command sequence."""
    sanitized: list[str] = []
    redact_next = False
    for raw_arg in args:
        arg = str(raw_arg)
        if redact_next:
            sanitized.append(REDACTED_PLACEHOLDER)
            redact_next = False
            continue
        lower_arg = arg.lower()
        if lower_arg in _SENSITIVE_FLAG_NAMES:
            sanitized.append(arg)
            redact_next = True
            continue
        if "=" in arg:
            flag_part, _ = arg.split("=", 1)
            if flag_part.lower() in _SENSITIVE_FLAG_NAMES or _SENSITIVE_KEY_PATTERN.search(flag_part):
                sanitized.append(f"{flag_part}={REDACTED_PLACEHOLDER}")
                continue
        sanitized.append(redact_sensitive_text(arg))
    return tuple(sanitized)


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Returns a copy of diagnostic details with sensitive keys and values redacted."""
    if not details:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        str_key = str(key)
        if _SENSITIVE_KEY_PATTERN.search(str_key):
            cleaned[str_key] = REDACTED_PLACEHOLDER
        elif isinstance(value, str):
            cleaned[str_key] = redact_sensitive_text(value)
        elif isinstance(value, Mapping):
            cleaned[str_key] = sanitize_details(value)
        elif isinstance(value, (list, tuple)):
            cleaned[str_key] = [
                redact_sensitive_text(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            cleaned[str_key] = value
    return cleaned


class EmuCliError(Exception):
    """Base exception for structured, actionable emu-dev-cli failures."""

    default_exit_code: int = int(ExitCode.FAILURE)
    default_error_code: str = ERROR_CODE_GENERAL

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        exit_code: int | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        clean_message = redact_sensitive_text(message)
        super().__init__(clean_message)
        self.message: str = clean_message
        self.remediation: str | None = (
            redact_sensitive_text(remediation) if remediation else None
        )
        self.exit_code: int = (
            int(exit_code) if exit_code is not None else int(self.default_exit_code)
        )
        self.error_code: str = error_code or self.default_error_code
        self.details: dict[str, Any] = sanitize_details(details)
        if cause is not None:
            self.__cause__ = cause

    def format_human(self) -> str:
        """Formats a concise, actionable human-readable message with remediation."""
        base = f"[{self.error_code}] {self.message}"
        if not self.remediation:
            return base
        return f"{base} Next step: {self.remediation}"

    def to_dict(self, action: str | None = None) -> dict[str, Any]:
        """Serializes the error into the canonical JSON envelope."""
        payload: dict[str, Any] = {
            "status": "error",
            "error_code": self.error_code,
            "error_message": self.message,
            "summary": self.message,
            "exit_code": self.exit_code,
        }
        if action:
            payload["action"] = action
        if self.remediation:
            payload["remediation"] = self.remediation
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class UsageError(EmuCliError):
    """Raised when CLI arguments, configuration values, or inputs are invalid."""

    default_exit_code = int(ExitCode.USAGE_ERROR)
    default_error_code = ERROR_CODE_USAGE


class ExecutionError(EmuCliError):
    """Raised when a command or workflow step fails during execution."""

    default_exit_code = int(ExitCode.FAILURE)
    default_error_code = ERROR_CODE_EXECUTION


class AuthError(EmuCliError):
    """Raised when authentication or authorization (LOAS, SSO, OAuth2) fails."""

    default_exit_code = int(ExitCode.AUTH_ERROR)
    default_error_code = ERROR_CODE_AUTH


class RetryableError(EmuCliError):
    """Raised when an operation or workflow step should retry or loop."""

    default_exit_code = int(ExitCode.RETRY_OR_LOOP)
    default_error_code = ERROR_CODE_RETRYABLE


class SubprocessError(ExecutionError):
    """Raised when an external subprocess exits non-zero, times out, or fails to launch."""

    default_error_code = ERROR_CODE_SUBPROCESS

    def __init__(
        self,
        message: str,
        *,
        cmd: Sequence[str] = (),
        returncode: int = int(ExitCode.FAILURE),
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        remediation: str | None = None,
        exit_code: int | None = None,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        sanitized_cmd = redact_command_args(cmd)
        clean_stdout = redact_sensitive_text(stdout)
        clean_stderr = redact_sensitive_text(stderr)
        merged_details: dict[str, Any] = {
            "command": list(sanitized_cmd),
            "returncode": int(returncode),
            "timed_out": bool(timed_out),
        }
        if clean_stderr.strip():
            merged_details["stderr"] = clean_stderr.strip()
        if details:
            merged_details.update(sanitize_details(details))

        super().__init__(
            message,
            remediation=remediation,
            exit_code=exit_code,
            error_code=self.default_error_code,
            details=merged_details,
            cause=cause,
        )
        self.cmd: tuple[str, ...] = sanitized_cmd
        self.returncode: int = int(returncode)
        self.stdout: str = clean_stdout
        self.stderr: str = clean_stderr
        self.timed_out: bool = bool(timed_out)
