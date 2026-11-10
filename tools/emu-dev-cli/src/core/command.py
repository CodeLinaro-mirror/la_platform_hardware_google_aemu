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
from core.errors import CommandValidationError, ExitCode, UsageError

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

    @classmethod
    def success(
        cls,
        data: TResult | None = None,
        *,
        summary: str = "",
        action: str = "",
        markdown_table: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CommandResult[TResult]":
        """Constructs a successful CommandResult with exit_code=0."""
        return cls(
            data=data,
            summary=summary,
            action=action,
            status=STATUS_SUCCESS,
            exit_code=int(ExitCode.SUCCESS),
            markdown_table=markdown_table,
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls,
        summary: str,
        *,
        exit_code: int = int(ExitCode.FAILURE),
        data: Any = None,
        action: str = "",
        markdown_table: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CommandResult[Any]":
        """Constructs a failed CommandResult with status='error'."""
        return cls(
            data=data,
            summary=summary,
            action=action,
            status=STATUS_ERROR,
            exit_code=int(exit_code),
            markdown_table=markdown_table,
            metadata=metadata or {},
        )

    @property
    def ok(self) -> bool:
        """Returns True when exit_code is 0 and status is not error."""
        return (
            self.exit_code == int(ExitCode.SUCCESS)
            and self.status != STATUS_ERROR
        )

    def __int__(self) -> int:
        """Returns the integer exit code of this CommandResult."""
        return int(self.exit_code)

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
    # Long `--help` text and its argparse formatter, used by register().
    description: str = ""
    epilog: str = ""
    formatter_class: type[argparse.HelpFormatter] | None = None

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """Registers command-specific arguments on `parser`."""
        del parser

    def validate(self, args: TArgs, ctx: ExecutionContext) -> Any:
        """Optional hook to validate parsed arguments before execution."""
        del ctx
        return args

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
        if self.description:
            kwargs["description"] = self.description
        if self.epilog:
            kwargs["epilog"] = self.epilog
        if self.formatter_class is not None:
            kwargs["formatter_class"] = self.formatter_class
        elif self.epilog:
            kwargs["formatter_class"] = argparse.RawDescriptionHelpFormatter
        parser = subparsers.add_parser(self.name, **kwargs)
        self.configure_parser(parser)
        self.bind_parser(parser)
        return parser

    def bind_parser(self, parser: argparse.ArgumentParser) -> None:
        """Attaches this Command instance to `parser` defaults for CliDispatcher."""
        parser.set_defaults(**{COMMAND_INSTANCE_ATTR: self})


def bind_command(
    target: Any,
    command: Command[Any, Any],
) -> argparse.ArgumentParser:
    """Registers or binds `command` on a subparsers action or an ArgumentParser."""
    if hasattr(target, "add_parser"):
        return command.register(target)
    if isinstance(target, argparse.ArgumentParser):
        command.configure_parser(target)
        command.bind_parser(target)
        return target
    raise UsageError(
        f"Cannot bind command '{command.name}' to unsupported target type {type(target).__name__}.",
        remediation="Pass an argparse subparsers action or ArgumentParser to bind_command().",
    )


class CommandGroup(Command[argparse.Namespace, Any]):
    """A command that hands its work to one of its named subcommands.

    Running the group without a subcommand prints the group's help, the way
    the procedural `print_help()` handlers did. In JSON mode it reports a
    usage error instead, so callers never get help text where JSON belongs.
    """

    # Namespace attribute that argparse fills with the chosen subcommand name.
    subcommand_dest: str = ""
    subcommand_help: str = ""
    subcommands: Sequence[Command[Any, Any]] = ()

    def __init__(
        self, subcommands: Sequence[Command[Any, Any]] | None = None
    ) -> None:
        resolved = (
            subcommands
            if subcommands is not None
            else getattr(self, "subcommands", ())
        )
        self._subcommands = tuple(resolved)
        self.subcommands = self._subcommands
        self._by_name: dict[str, Command[Any, Any]] = {}
        for sub_cmd in self._subcommands:
            for key in (sub_cmd.name, *sub_cmd.aliases):
                self._by_name[key] = sub_cmd
        self._parser: argparse.ArgumentParser | None = None

    def configure_group_parser(self, parser: argparse.ArgumentParser) -> None:
        """Optional hook to register group-level arguments before subparsers."""
        del parser

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        self._parser = parser
        self.configure_group_parser(parser)
        subparsers = parser.add_subparsers(
            dest=self.subcommand_dest, help=self.subcommand_help
        )
        for sub_cmd in self._subcommands:
            sub_cmd.register(subparsers)

    def _usage_remediation(self) -> str:
        names = ", ".join(sub_cmd.name for sub_cmd in self._subcommands)
        return (
            f"Use one of: {names}. "
            f"Run 'emu-dev-cli {self.name} --help' for details."
        )

    def resolve_subcommand(
        self, args: argparse.Namespace
    ) -> Command[Any, Any] | None:
        """Returns the chosen subcommand, or None when none was given."""
        sub_name = str(getattr(args, self.subcommand_dest, "") or "").strip()
        if not sub_name:
            return None
        sub_cmd = self._by_name.get(sub_name)
        if sub_cmd is None:
            raise UsageError(
                f"Unknown '{self.name}' subcommand '{sub_name}'.",
                remediation=self._usage_remediation(),
            )
        return sub_cmd

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> Any:
        sub_cmd = self.resolve_subcommand(args)
        if sub_cmd is None:
            return args
        return sub_cmd.validate(args, ctx)

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult[Any]:
        sub_cmd = self.resolve_subcommand(args)
        if sub_cmd is not None:
            return sub_cmd.execute(args, ctx)  # type: ignore[arg-type]
        json_mode = (
            ctx.config.json_mode
            if ctx is not None
            else bool(getattr(args, "json", False))
        )
        if json_mode or self._parser is None:
            raise UsageError(
                f"Missing '{self.name}' subcommand.",
                remediation=self._usage_remediation(),
            )
        self._parser.print_help()
        return CommandResult.success(
            {},
            action=self.name,
            summary=self.name,
            metadata={META_KEY_ALREADY_RENDERED: True},
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
