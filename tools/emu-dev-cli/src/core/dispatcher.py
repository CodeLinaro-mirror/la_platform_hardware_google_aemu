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

"""Central CLI dispatcher and non-exiting OutputRenderer for emu-dev-cli."""

import argparse
from collections.abc import Mapping
import inspect
import json
import logging
import sys
import time
from typing import Any, TextIO

from core.command import (
    COMMAND_INSTANCE_ATTR,
    Command,
    CommandResult,
    LegacyCommandAdapter,
    META_KEY_ALREADY_RENDERED,
    STATUS_ERROR,
    STATUS_SUCCESS,
)
from core.context import ExecutionContext
from core.errors import EmuCliError, ExitCode, UsageError
from core.events import StepCompleted, StepStarted
from lib.output import ensure_utf8_stdio

LOGGER = logging.getLogger(__name__)

_REDUNDANT_TABLE_KEYS: frozenset[str] = frozenset({
    "nodes",
    "devices",
    "serials",
    "total_nodes",
    "action",
})


class OutputRenderer:
    """Renders CommandResult and EmuCliError payloads without calling sys.exit()."""

    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr

    @property
    def stdout(self) -> TextIO:
        """Returns the active stdout stream."""
        return self._stdout if self._stdout is not None else sys.stdout

    @property
    def stderr(self) -> TextIO:
        """Returns the active stderr stream."""
        return self._stderr if self._stderr is not None else sys.stderr

    def render_payload(
        self,
        payload: dict[str, Any],
        *,
        json_mode: bool = False,
        is_error: bool = False,
    ) -> None:
        """Renders a dictionary payload in JSON or human-readable Markdown format."""
        ensure_utf8_stdio()
        target_stream = self.stderr if is_error else self.stdout
        if json_mode:
            serialized = json.dumps(payload, indent=2, ensure_ascii=False)
            print(serialized, file=target_stream)
            return

        status_symbol = "❌ ERROR" if is_error else "✅ SUCCESS"
        summary_text = payload.get(
            "summary", payload.get("error_message", payload.get("action", "done"))
        )
        print(f"\n{status_symbol}: {summary_text}", file=target_stream)

        has_table = "markdown_table" in payload
        for key, val in payload.items():
            if key in ("summary", "status"):
                continue
            if key == "markdown_table":
                print(f"\n{val}\n", file=target_stream)
            elif key == "proposed_bug_markdown":
                print(
                    f"\n--- Proposed Bug Report (Markdown Preview) ---\n\n{val}\n"
                    "-------------------------------------------------\n",
                    file=target_stream,
                )
            elif has_table and key in _REDUNDANT_TABLE_KEYS:
                continue
            elif isinstance(val, list):
                print(f"  • {key}: [{len(val)} items]", file=target_stream)
            elif isinstance(val, dict):
                continue
            else:
                print(f"  • {key}: {val}", file=target_stream)
        print(file=target_stream)

    def render_result(
        self, result: CommandResult[Any], *, json_mode: bool = False
    ) -> None:
        """Renders a structured CommandResult if not already rendered."""
        if result.already_rendered:
            return
        self.render_payload(
            result.to_payload(),
            json_mode=json_mode,
            is_error=not result.ok,
        )

    def render_error(
        self,
        error: EmuCliError,
        *,
        action: str | None = None,
        json_mode: bool = False,
    ) -> None:
        """Renders a typed EmuCliError to stderr or stdout according to mode."""
        self.render_payload(
            error.to_dict(action=action),
            json_mode=json_mode,
            is_error=True,
        )


def _resolve_command(args: argparse.Namespace) -> Command[Any, Any]:
    """Extracts a Command instance from `args` or adapts a legacy `args.func` handler."""
    bound_cmd = getattr(args, COMMAND_INSTANCE_ATTR, None)
    if isinstance(bound_cmd, Command):
        return bound_cmd

    legacy_func = getattr(args, "func", None)
    subcommand_name = str(getattr(args, "subcommand", None) or "cli")
    if callable(legacy_func):
        return LegacyCommandAdapter(name=subcommand_name, handler_fn=legacy_func)

    raise UsageError(
        f"No command handler is registered for subcommand '{subcommand_name}'.",
        remediation="Run 'emu-dev-cli --help' to list valid subcommands.",
    )


def _coerce_raw_result(
    action_name: str, raw_return: Any
) -> CommandResult[Any]:
    """Converts a raw return value from Command.execute into a CommandResult."""
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
            action=str(raw_return.get("action", action_name)),
            summary=str(raw_return.get("summary", action_name)),
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
            action=action_name,
            summary=action_name,
            status=status,
            exit_code=exit_code,
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    return CommandResult(
        data={},
        action=action_name,
        summary=action_name,
        status=STATUS_SUCCESS,
        exit_code=int(ExitCode.SUCCESS),
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def _safe_rollback(
    command: Command[Any, Any],
    args: argparse.Namespace,
    ctx: ExecutionContext,
    error: BaseException | None = None,
) -> None:
    """Invokes `command.rollback(args, ctx[, error])` while isolating rollback exceptions."""
    try:
        sig = inspect.signature(command.rollback)
        if len(sig.parameters) >= 3:
            command.rollback(args, ctx, error)  # type: ignore[call-arg]
        else:
            command.rollback(args, ctx)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.warning(
            "Rollback failed for command '%s': %s",
            command.name,
            exc,
        )


class CliDispatcher:
    """Executes parsed CLI commands with dependency injection, events, and error mapping."""

    def __init__(
        self,
        *,
        context: ExecutionContext | None = None,
        renderer: OutputRenderer | None = None,
    ) -> None:
        self._context = context
        self._renderer = renderer or OutputRenderer()
        self._registered_commands: dict[str, Command[Any, Any]] = {}

    @property
    def renderer(self) -> OutputRenderer:
        """Returns the OutputRenderer used by this dispatcher."""
        return self._renderer

    def register_command(self, command: Command[Any, Any]) -> None:
        """Registers a Command instance by its `name` and `aliases`."""
        if command.name:
            self._registered_commands[command.name] = command
        for alias in getattr(command, "aliases", ()) or ():
            self._registered_commands[str(alias)] = command

    def resolve_command(self, args: argparse.Namespace) -> Command[Any, Any]:
        """Resolves the Command instance bound to `args` or registered by subcommand name."""
        bound_cmd = getattr(args, COMMAND_INSTANCE_ATTR, None)
        if isinstance(bound_cmd, Command):
            return bound_cmd
        subcommand_name = str(getattr(args, "subcommand", None) or "")
        if subcommand_name in self._registered_commands:
            return self._registered_commands[subcommand_name]
        return _resolve_command(args)

    def dispatch(
        self,
        args: argparse.Namespace,
        *,
        ctx: ExecutionContext | None = None,
    ) -> int:
        """Executes the command bound to `args` and returns a canonical integer exit code."""
        base_ctx = ctx or self._context or ExecutionContext.create_default()
        active_ctx = base_ctx.with_flags(
            json_mode=bool(getattr(args, "json", base_ctx.config.json_mode)),
            verbose=bool(getattr(args, "verbose", base_ctx.config.verbose)),
            log_file=getattr(args, "log_file", base_ctx.config.log_file),
        )
        json_mode = active_ctx.config.json_mode
        step_name = str(getattr(args, "subcommand", None) or "cli")
        start_time = time.monotonic()

        try:
            command = self.resolve_command(args)
            step_name = command.name or step_name
        except EmuCliError as err:
            self._renderer.render_error(err, action=step_name, json_mode=json_mode)
            return err.exit_code

        active_ctx.event_bus.publish(StepStarted(step_name=step_name))
        try:
            validated_args = command.validate(args, active_ctx)
            effective_args = (
                validated_args if validated_args is not None else args
            )
            try:
                raw_result = command.execute(effective_args, active_ctx)
                result = _coerce_raw_result(step_name, raw_result)
            except SystemExit as sys_exit:
                code = (
                    int(ExitCode.SUCCESS)
                    if sys_exit.code is None
                    else (
                        int(sys_exit.code)
                        if isinstance(sys_exit.code, int)
                        else int(ExitCode.FAILURE)
                    )
                )
                result = CommandResult(
                    data={},
                    action=step_name,
                    summary=step_name,
                    status=(
                        STATUS_SUCCESS
                        if code == int(ExitCode.SUCCESS)
                        else STATUS_ERROR
                    ),
                    exit_code=code,
                    metadata={META_KEY_ALREADY_RENDERED: True},
                )
            self._renderer.render_result(result, json_mode=json_mode)
            duration = time.monotonic() - start_time
            active_ctx.event_bus.publish(
                StepCompleted(
                    step_name=step_name,
                    status=result.status,
                    duration_sec=duration,
                    exit_code=result.exit_code,
                )
            )
            return int(result.exit_code)
        except EmuCliError as err:
            duration = time.monotonic() - start_time
            _safe_rollback(command, args, active_ctx, err)
            self._renderer.render_error(err, action=step_name, json_mode=json_mode)
            active_ctx.event_bus.publish(
                StepCompleted(
                    step_name=step_name,
                    status=STATUS_ERROR,
                    duration_sec=duration,
                    exit_code=err.exit_code,
                )
            )
            return int(err.exit_code)
        except KeyboardInterrupt as intr:
            duration = time.monotonic() - start_time
            _safe_rollback(command, args, active_ctx, intr)
            active_ctx.event_bus.publish(
                StepCompleted(
                    step_name=step_name,
                    status="interrupted",
                    duration_sec=duration,
                    exit_code=int(ExitCode.INTERRUPTED),
                )
            )
            return int(ExitCode.INTERRUPTED)
