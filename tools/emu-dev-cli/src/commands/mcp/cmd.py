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

"""`emu-dev-cli mcp [serve]`: serves emu-dev-cli commands to AI agents over MCP."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import contextlib
import os
import sys
from typing import Any, TextIO

from commands.mcp.server import McpServer
from commands.mcp.tools import build_tool_catalog
from core.command import Command, CommandGroup, CommandResult, META_KEY_ALREADY_RENDERED
from core.context import ExecutionContext
from install.installer import get_release_package_directory
from lib.version import read_installed_version

SERVE_ACTION = "mcp serve"
INSTRUCTIONS = (
    "These tools run emu-dev-cli commands for Android Emulator development and return the "
    "JSON that `emu-dev-cli --json <command>` prints. Relative paths, including the default "
    "repo '.', resolve against the server's working directory, {cwd}; pass an absolute repo "
    "path to work in another checkout. Tools only change state when asked to: "
    "gerrit_stack_sync defaults to dry_run=true and gerrit_stack_watch to a single poll "
    "that does not rebase. Calls run one at a time."
)


@contextlib.contextmanager
def protocol_stdio() -> Iterator[tuple[TextIO, TextIO]]:
    """Yields private streams on the real stdin and stdout, and detaches fds 0 and 1 meanwhile.

    The MCP client owns both pipes. While the server runs, commands and their
    child processes read an empty stdin instead of protocol messages, and their
    output goes to stderr instead of corrupting the protocol stream.
    """
    try:
        sys.stdout.flush()
        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield sys.stdin, sys.stdout  # No real file descriptors to protect.
        return
    protocol_in, protocol_out = os.dup(stdin_fd), os.dup(stdout_fd)
    empty_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(empty_fd, stdin_fd)
    os.close(empty_fd)
    os.dup2(stderr_fd, stdout_fd)
    reader = os.fdopen(protocol_in, "r", encoding="utf-8", errors="replace", closefd=False)
    writer = os.fdopen(protocol_out, "w", encoding="utf-8", newline="\n", closefd=False)
    try:
        yield reader, writer
    finally:
        reader.close()
        writer.close()
        sys.stdout.flush()
        os.dup2(protocol_in, stdin_fd)
        os.dup2(protocol_out, stdout_fd)
        os.close(protocol_in)
        os.close(protocol_out)


def _installed_version() -> str:
    installed = read_installed_version(get_release_package_directory(None)) or {}
    return str(installed.get("commit_short") or "")


class McpServeCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Runs the MCP server on stdin and stdout until the client disconnects."""

    name = "serve"
    help_text = "Serve emu-dev-cli tools to an AI agent over MCP on stdin/stdout"
    description = (
        "Runs a Model Context Protocol server on stdin/stdout. Configure an agent to "
        "start `emu-dev-cli mcp serve`; see docs/playbooks/mcp_server.md."
    )

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self._stdin = stdin
        self._stdout = stdout

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--read-only",
            action="store_true",
            help="Only offer tools that never change Gerrit, git, or chat state",
        )

    @contextlib.contextmanager
    def _streams(self) -> Iterator[tuple[TextIO, TextIO]]:
        if self._stdin is not None and self._stdout is not None:
            yield self._stdin, self._stdout
            return
        with protocol_stdio() as streams:
            yield streams

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        tools = build_tool_catalog(read_only=bool(getattr(args, "read_only", False)))
        with self._streams() as (reader, writer):
            McpServer(
                tools,
                ctx,
                reader=reader,
                writer=writer,
                server_version=_installed_version(),
                instructions=INSTRUCTIONS.format(cwd=os.getcwd()),
            ).serve()
        # stdout belongs to the client until the end, so there is nothing to print.
        return CommandResult.success(
            {"tools": len(tools)},
            action=SERVE_ACTION,
            summary="The MCP client disconnected.",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )


class McpCommand(CommandGroup):
    """Groups the MCP commands; `emu-dev-cli mcp` alone starts the server."""

    name = "mcp"
    help_text = "Serve emu-dev-cli commands to AI agents over the Model Context Protocol"
    subcommand_dest = "mcp_command"
    subcommands = [McpServeCommand()]

    def resolve_subcommand(self, args: argparse.Namespace) -> Command[Any, Any] | None:
        return super().resolve_subcommand(args) or self.subcommands[0]
