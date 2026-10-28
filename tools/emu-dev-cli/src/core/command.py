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

"""Command pattern base classes, CommandResult, and LegacyCommandAdapter."""

from abc import ABC, abstractmethod
import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from core.context import ExecutionContext
from core.errors import ExitCode, UsageError

TArgs = TypeVar("TArgs")
TResult = TypeVar("TResult")

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
META_KEY_ALREADY_RENDERED = "_already_rendered"
COMMAND_INSTANCE_ATTR = "_command_instance"


def _coerce_exit_code(code: Any) -> int:
    """Converts a SystemExit code attribute into a canonical integer exit code."""
    if code is None:
        return int(ExitCode.SUCCESS)
    if isinstance(code, int):
        return int(code)
    return int(ExitCode.FAILURE)


@dataclass(frozen=True)
class CommandResult(Generic[TResult]):
    """Structured return value produced by Command.execute()."""

    data: TResult
    summary: str = ""
    action: str = ""
    status: str = STATUS_SUCCESS
    exit_code: int = int(ExitCode.SUCCESS)
    markdown_table: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Returns True when exit_code is 0 and status is not error."""
        return (
            self.exit_code == int(ExitCode.SUCCESS)
            and self.status != STATUS_ERROR
        )

    @property
    def already_rendered(self) -> bool:
        """Returns True if a legacy command already printed its output before exiting."""
        return bool(self.metadata.get(META_KEY_ALREADY_RENDERED, False))

    def to_payload(self) -> dict[str, Any]:
        """Builds the dictionary payload consumed by OutputRenderer / print_result."""
        payload: dict[str, Any] = {
            "status": self.status,
            "action": self.action or "done",
            "summary": self.summary or self.action or "done",
        }
        if isinstance(self.data, Mapping):
            for key, val in self.data.items():
                if key in ("status", "action", "summary") and payload.get(key) not in ("", "done"):
                    continue
                payload[str(key)] = val
        elif self.data is not None:
            payload["data"] = self.data

        if self.markdown_table is not None:
            payload["markdown_table"] = self.markdown_table

        visible_meta = {
            str(k): v
            for k, v in (self.metadata or {}).items()
            if not str(k).startswith("_")
        }
        if visible_meta:
            payload["meta"] = visible_meta

        if self.exit_code != int(ExitCode.SUCCESS) or not self.ok:
            payload["exit_code"] = int(self.exit_code)
        return payload


class Command(ABC, Generic[TArgs, TResult]):
    """Abstract base class for typed, dependency-injected CLI commands."""

    name: str = ""
    aliases: Sequence[str] = ()
    help_text: str = ""

    @abstractmethod
    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """Registers command-specific arguments on `parser`."""

    def validate(self, args: TArgs, ctx: ExecutionContext) -> None:
        """Optional hook to validate parsed arguments before execution."""
        del args, ctx

    @abstractmethod
    def execute(
        self, args: TArgs, ctx: ExecutionContext
    ) -> CommandResult[TResult]:
        """Executes the command logic using services from `ctx` and returns a CommandResult."""

    def rollback(self, args: TArgs, ctx: ExecutionContext) -> None:
        """Optional cleanup hook invoked when `execute()` raises an exception."""
        del args, ctx

    def register(self, subparsers: Any) -> argparse.ArgumentParser:
        """Registers this command on `subparsers` and binds it for CliDispatcher."""
        if not self.name or not self.name.strip():
            raise UsageError(
                f"Command class {type(self).__name__} must define a non-empty 'name'.",
                remediation="Set the class attribute `name = '<subcommand>'` on the Command subclass.",
            )
        kwargs: dict[str, Any] = {"help": self.help_text}
        if self.aliases:
            kwargs["aliases"] = list(self.aliases)
        parser = subparsers.add_parser(self.name, **kwargs)
        self.configure_parser(parser)
        self.bind_parser(parser)
        return parser

    def bind_parser(self, parser: argparse.ArgumentParser) -> None:
        """Attaches this Command instance to `parser` defaults."""
        from core.dispatcher import CliDispatcher

        def _legacy_entrypoint(parsed_args: argparse.Namespace) -> int:
            return CliDispatcher().dispatch(parsed_args)

        parser.set_defaults(
            **{
                COMMAND_INSTANCE_ATTR: self,
                "func": _legacy_entrypoint,
            }
        )


class LegacyCommandAdapter(Command[argparse.Namespace, dict[str, Any]]):
    """Adapts existing procedural `func(args)` subcommands to the Command interface."""

    def __init__(
        self,
        name: str,
        handler_fn: Callable[[argparse.Namespace], Any],
        *,
        configure_fn: Callable[[argparse.ArgumentParser], None] | None = None,
        help_text: str = "",
        aliases: Sequence[str] = (),
    ) -> None:
        if not callable(handler_fn):
            raise UsageError(
                f"LegacyCommandAdapter handler for '{name}' must be callable.",
                remediation="Pass a valid function taking an argparse.Namespace argument.",
            )
        self.name = name
        self.aliases = tuple(aliases)
        self.help_text = help_text
        self._handler_fn = handler_fn
        self._configure_fn = configure_fn

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        if self._configure_fn is not None:
            self._configure_fn(parser)

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        try:
            raw_return = self._handler_fn(args)
        except SystemExit as exc:
            exit_code = _coerce_exit_code(exc.code)
            status = (
                STATUS_SUCCESS
                if exit_code == int(ExitCode.SUCCESS)
                else STATUS_ERROR
            )
            return CommandResult(
                data={},
                action=self.name,
                summary=self.name,
                status=status,
                exit_code=exit_code,
                metadata={META_KEY_ALREADY_RENDERED: True},
            )

        if isinstance(raw_return, CommandResult):
            return raw_return
        if isinstance(raw_return, Mapping):
            exit_code = int(raw_return.get("exit_code", ExitCode.SUCCESS))
            status = str(
                raw_return.get(
                    "status",
                    STATUS_SUCCESS
                    if exit_code == int(ExitCode.SUCCESS)
                    else STATUS_ERROR,
                )
            )
            return CommandResult(
                data=dict(raw_return),
                action=str(raw_return.get("action", self.name)),
                summary=str(raw_return.get("summary", self.name)),
                status=status,
                exit_code=exit_code,
            )
        if isinstance(raw_return, int):
            exit_code = int(raw_return)
            status = (
                STATUS_SUCCESS
                if exit_code == int(ExitCode.SUCCESS)
                else STATUS_ERROR
            )
            return CommandResult(
                data={},
                action=self.name,
                summary=self.name,
                status=status,
                exit_code=exit_code,
                metadata={META_KEY_ALREADY_RENDERED: True},
            )
        return CommandResult(
            data={},
            action=self.name,
            summary=self.name,
            status=STATUS_SUCCESS,
            exit_code=int(ExitCode.SUCCESS),
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
