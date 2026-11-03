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

"""The MCP tools: which emu-dev-cli commands agents can call, and how a call runs."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
import contextlib
from dataclasses import dataclass, field
import functools
import io
import json
from typing import Any

from commands.crash.find_bug import CrashFindBugCommand
from commands.crash.list_crashes import CrashListCrashesCommand
from commands.crash.reproduce import CrashReproduceCommand
from commands.gerrit.cmd import GerritCommand, GerritCommentsCommand, GerritReplyCommand
from commands.gerrit.ping_for_review import GerritPingForReviewCommand
from commands.gerrit.stack import (
    StackPreUploadCommand,
    StackStatusCommand,
    StackSyncCommand,
    StackWatchCommand,
)
from commands.mcp.arguments import (
    ToolArgumentParser,
    ToolParameter,
    parse_tool_arguments,
    tool_parameters,
)
from commands.perf import PerfCompareCommand
from commands.tidy import TidyCheckCommand
from core.command import Command, CommandGroup
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import UsageError

SleepFn = Callable[[float], None]

# Output is always JSON, and credentials do not belong in tool arguments.
ALWAYS_HIDDEN: frozenset[str] = frozenset({
    "json",
    "token",
    "access_token",
    "auth_token",
    "api_key",
    "password",
    "secret",
    "cookie",
})


@dataclass(frozen=True)
class ToolSpec:
    """An MCP tool that runs one emu-dev-cli command."""

    name: str
    title: str
    description: str
    command: Callable[..., Command[Any, Any]]
    # Command group whose own arguments the command reads, such as `gerrit --host`.
    group: type[CommandGroup] | None = None
    hidden: frozenset[str] = frozenset()
    # Overrides of CLI defaults, so that tools only change state when asked to.
    defaults: Mapping[str, Any] = field(default_factory=dict)
    read_only: bool = True
    destructive: bool = False
    open_world: bool = True
    # The command polls with an injectable `sleep_fn`, which lets a cancelled call stop.
    takes_sleep_fn: bool = False

    def create_command(self, sleep_fn: SleepFn | None = None) -> Command[Any, Any]:
        """Returns a new command instance for one call."""
        if self.takes_sleep_fn and sleep_fn is not None:
            return self.command(sleep_fn=sleep_fn)
        return self.command()

    def build_parser(self, command: Command[Any, Any]) -> ToolArgumentParser:
        """Returns the command's CLI parser, with the group arguments it relies on."""
        # "resolve" lets a command redefine a group argument, as `gerrit stack` does with --host.
        parser = ToolArgumentParser(prog=self.name, add_help=False, conflict_handler="resolve")
        if self.group is not None:
            self.group().configure_group_parser(parser)
        command.configure_parser(parser)
        command.bind_parser(parser)
        return parser

    @functools.cached_property
    def parameters(self) -> dict[str, ToolParameter]:
        """The CLI arguments this tool accepts, keyed by name."""
        parser = self.build_parser(self.create_command())
        return tool_parameters(parser, hidden=ALWAYS_HIDDEN | self.hidden)

    def definition(self) -> dict[str, Any]:
        """Returns the MCP `Tool` object that tools/list reports for this tool."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                name: param.schema(self.defaults.get(name, param.default))
                for name, param in self.parameters.items()
            },
            "additionalProperties": False,
        }
        required = [name for name, param in self.parameters.items() if param.required]
        if required:
            schema["required"] = required
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "openWorldHint": self.open_world,
            },
        }

    def parse_arguments(
        self, command: Command[Any, Any], arguments: Mapping[str, Any]
    ) -> argparse.Namespace:
        """Validates `arguments` with the command's parser and returns the CLI namespace."""
        given = {name: value for name, value in arguments.items() if value is not None}
        namespace = parse_tool_arguments(
            self.name, self.build_parser(command), self.parameters, {**self.defaults, **given}
        )
        # Commands that print their own output decide the format from args.json.
        namespace.json = True
        return namespace


def _json_object(text: str) -> dict[str, Any] | None:
    """Returns the JSON object in `text`, skipping lines a command logged before it."""
    lines = text.split("\n")
    for start, line in enumerate(lines):
        if not line.startswith("{"):
            continue
        try:
            value = json.loads("\n".join(lines[start:]))
        except ValueError:
            continue
        return value if isinstance(value, dict) else None
    return None


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool call."""

    text: str
    is_error: bool
    structured: dict[str, Any] | None = None
    stderr: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, is_error: bool) -> ToolResult:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return cls(text=text, is_error=is_error, structured=payload)

    @classmethod
    def from_output(cls, stdout: str, stderr: str, exit_code: int) -> ToolResult:
        """Builds the result from what the command printed; failures print JSON to stderr."""
        out, err = stdout.strip(), stderr.strip()
        return cls(
            text=out or err or f"The command printed nothing and exited with code {exit_code}.",
            is_error=exit_code != 0,
            structured=_json_object(out) or _json_object(err),
            stderr=err if out else "",
        )

    def to_mcp(self) -> dict[str, Any]:
        """Returns the MCP `CallToolResult` object."""
        content = [{"type": "text", "text": self.text}]
        if self.stderr:
            content.append({"type": "text", "text": f"stderr:\n{self.stderr}"})
        result: dict[str, Any] = {"content": content, "isError": self.is_error}
        if self.structured is not None:
            result["structuredContent"] = self.structured
        return result


def run_tool(
    spec: ToolSpec,
    arguments: Mapping[str, Any],
    ctx: ExecutionContext,
    *,
    sleep_fn: SleepFn | None = None,
) -> ToolResult:
    """Runs the tool's command like `emu-dev-cli --json <command>` and returns its output."""
    command = spec.create_command(sleep_fn)
    try:
        args = spec.parse_arguments(command, arguments)
    except UsageError as err:
        return ToolResult.from_payload(err.to_dict(action=spec.name), is_error=True)
    stdout, stderr = io.StringIO(), io.StringIO()
    dispatcher = CliDispatcher(context=ctx, renderer=OutputRenderer(stdout=stdout, stderr=stderr))
    # Older commands print their JSON instead of returning it. The server runs one tool
    # call at a time, so redirecting the process-wide streams cannot mix up two calls.
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = dispatcher.dispatch(args)
    return ToolResult.from_output(stdout.getvalue(), stderr.getvalue(), exit_code)


def default_tools() -> tuple[ToolSpec, ...]:
    """Returns every tool the server offers, in tools/list order."""
    return (
        ToolSpec(
            name="gerrit_stack_status",
            title="Gerrit stack status",
            description=(
                "Show every CL of a Gerrit relation chain or topic, parent first: patch set, "
                "mergeability, presubmit (Treehugger) results, and which reviewers "
                "Review-Enforcement still needs. Identify the stack by cl (default: the "
                "uploaded HEAD commit of repo) or by topic."
            ),
            command=StackStatusCommand,
            group=GerritCommand,
        ),
        ToolSpec(
            name="gerrit_stack_pre_upload",
            title="Pre-upload review checks",
            description=(
                "Run the local review checks (semantic commit message, cc_binary_with_manifest, "
                "go/error-messages, //emulator/libs reuse) on the local commits of repo, one "
                "commit, or uncommitted changes, before uploading. Fails when a check reports "
                "an error (or a warning with strict=true)."
            ),
            command=StackPreUploadCommand,
            open_world=False,
        ),
        ToolSpec(
            name="gerrit_stack_sync",
            title="Rebase a Gerrit stack",
            description=(
                "Check a CL stack for collisions with upstream changes, rebase it onto its "
                "target branch in repo, and push the new patch sets with notify=OWNER. "
                "Defaults to dry_run=true, which only reports collisions and mergeability; "
                "pass dry_run=false to rebase and push (needs a clean working tree)."
            ),
            command=StackSyncCommand,
            group=GerritCommand,
            defaults={"dry_run": True},
            read_only=False,
            destructive=True,
        ),
        ToolSpec(
            name="gerrit_stack_watch",
            title="Watch a Gerrit stack",
            description=(
                "Poll a CL stack until it merges: report presubmit and merge state, rebase the "
                "CLs left behind when a parent merges, and ping reviewers over Google Chat when "
                "presubmit turns green (ping, ping_reviewers). Defaults to one poll that only "
                "reports (once=true, no_rebase=true); pass once=false with a timeout to keep "
                "watching, and no_rebase=false to allow rebases."
            ),
            command=StackWatchCommand,
            group=GerritCommand,
            defaults={"once": True, "no_rebase": True},
            read_only=False,
            destructive=True,
            takes_sleep_fn=True,
        ),
        ToolSpec(
            name="gerrit_ping_for_review",
            title="Ping a reviewer",
            description=(
                "Send a reviewer a Google Chat DM asking them to review a CL once its "
                "Presubmit-Verified is +2. Checks the CL once by default; pass no_wait=false "
                "to wait up to timeout seconds for the presubmit to pass."
            ),
            command=GerritPingForReviewCommand,
            group=GerritCommand,
            hidden=frozenset({"cl_flag"}),
            defaults={"no_wait": True},
            read_only=False,
            takes_sleep_fn=True,
        ),
        ToolSpec(
            name="gerrit_comments",
            title="Gerrit review comments",
            description=(
                "List the review comment threads of a CL with file, line, author, and "
                "messages: unresolved threads only, unless all_threads=true."
            ),
            command=GerritCommentsCommand,
            group=GerritCommand,
            # These only shape the Markdown dossier, which JSON output does not include.
            hidden=frozenset({"context_lines", "output"}),
        ),
        ToolSpec(
            name="gerrit_reply",
            title="Reply on a Gerrit CL",
            description=(
                "Reply to a review comment thread (comment_id and message; the thread is "
                "resolved unless unresolve=true), or post message as a top-level comment when "
                "comment_id is omitted. Never votes or submits."
            ),
            command=GerritReplyCommand,
            group=GerritCommand,
            # `resolve` is always on, and the queue files belong to the address-review workflow.
            hidden=frozenset({"resolve", "stage_only", "queue_file", "from_queue"}),
            read_only=False,
        ),
        ToolSpec(
            name="crash_list",
            title="Production crash clusters",
            description=(
                "List production qemu-next crash clusters from crash.prod.latest (F1) with "
                "their go/crash bug links, grouped by stable signature unless raw=true. "
                "print_sql=true returns the F1 query without running it."
            ),
            command=CrashListCrashesCommand,
        ),
        ToolSpec(
            name="crash_find_bug",
            title="Find bugs for a crash",
            description=(
                "Search go/crash links and Buganizer for bugs that match a crash report's "
                "signature and top stack frame, and say whether the crash looks fixed in a "
                "newer build. Takes a crash report ID, not a bug number."
            ),
            command=CrashFindBugCommand,
        ),
        ToolSpec(
            name="crash_reproduce",
            title="Crash reproduction plan",
            description=(
                "Build the plan to reproduce a crash report locally: emulator build, AVD "
                "settings, and the emulator and LLDB command lines. Over MCP this never "
                "downloads or launches the emulator."
            ),
            command=CrashReproduceCommand,
            # Over MCP the command always returns the plan, so dry_run changes nothing.
            hidden=frozenset({"dry_run"}),
        ),
        ToolSpec(
            name="perf_compare",
            title="Compare emulator performance",
            description=(
                "Compare the performance of two emulator builds. Saved .json reports compare "
                "in seconds; go/ab build IDs, branch aliases, or local emulator directories "
                "start emulators and run the benchmark suites first, which takes many minutes."
            ),
            command=PerfCompareCommand,
            # JSON is the only output format over MCP.
            hidden=frozenset({"format"}),
            read_only=False,
        ),
        ToolSpec(
            name="tidy_check",
            title="clang-tidy diagnostics",
            description=(
                "Report clang-tidy diagnostics for a Bazel target, one source file, or the "
                "files changed in the git diff. With a target, this runs a Bazel build first, "
                "which can take minutes."
            ),
            command=TidyCheckCommand,
            open_world=False,
        ),
    )


def build_tool_catalog(
    tools: Iterable[ToolSpec] | None = None, *, read_only: bool = False
) -> dict[str, ToolSpec]:
    """Indexes tools by name, keeping only read-only tools when `read_only` is set."""
    specs = default_tools() if tools is None else tuple(tools)
    return {spec.name: spec for spec in specs if spec.read_only or not read_only}
