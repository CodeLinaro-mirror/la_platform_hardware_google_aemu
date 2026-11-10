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

"""Hermetic tests for the `emu-dev-cli mcp` server (zero @patch decorators)."""

from __future__ import annotations

import argparse
import contextlib
import functools
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from typing import Any
import unittest

TOOL_DIR = Path(__file__).resolve().parents[2]
for _extra_dir in (TOOL_DIR / "src", TOOL_DIR / "tests"):
    if str(_extra_dir) not in sys.path:
        sys.path.insert(0, str(_extra_dir))

from commands.gerrit.stack.pre_upload_checks import CHECK_NAMES
from commands.mcp import (
    LATEST_PROTOCOL_VERSION,
    ErrorCode,
    McpCommand,
    McpServeCommand,
    McpServer,
    ToolCancelled,
    ToolSpec,
    build_tool_catalog,
    default_tools,
    protocol_stdio,
)
from commands.mcp.arguments import ToolArgumentParser, build_argv, json_type_name, tool_parameters
from core.command import META_KEY_ALREADY_RENDERED, Command, CommandResult, bind_command
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import ExecutionError
from core.process import ProcessResult
from testlib.gerrit_fakes import stack_change

UNICODE_TEXT = "测试_🚀_é"
WAIT_SEC = 10.0
DEFAULT_TOOL_NAMES = {
    "gerrit_stack_status",
    "gerrit_stack_pre_upload",
    "gerrit_stack_sync",
    "gerrit_stack_watch",
    "gerrit_ping_for_review",
    "gerrit_comments",
    "gerrit_reply",
    "crash_list",
    "crash_find_bug",
    "crash_reproduce",
    "perf_compare",
    "tidy_check",
}
STATE_CHANGING_TOOL_NAMES = {
    "gerrit_stack_sync",
    "gerrit_stack_watch",
    "gerrit_ping_for_review",
    "gerrit_reply",
    "perf_compare",
}


def _message(method: str, params: dict[str, Any] | None = None, *, request_id: Any = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return json.dumps(message) + "\n"


def _initialize(request_id: int = 1, version: str = LATEST_PROTOCOL_VERSION) -> str:
    client = {"name": "test-client", "version": "1.0"}
    params = {"protocolVersion": version, "capabilities": {}, "clientInfo": client}
    return _message("initialize", params, request_id=request_id)


def _call(request_id: Any, name: str, arguments: Any = None) -> str:
    params: dict[str, Any] = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return _message("tools/call", params, request_id=request_id)


def _reject_non_finite(constant: str) -> Any:
    raise AssertionError(f"Non-standard JSON float constant on wire: {constant}")


def _decode(output: str) -> list[dict[str, Any]]:
    """Parses server output, checking that every protocol line is strict ASCII JSON."""
    messages = []
    for line in output.split("\n"):
        if line:
            line.encode("ascii")
            messages.append(json.loads(line, parse_constant=_reject_non_finite))
    return messages


def _by_id(messages: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {m["id"]: m for m in messages if m.get("id") is not None}


def _context(root: str) -> ExecutionContext:
    return ExecutionContext.for_testing(root)


# Fake commands that exercise one behavior each.


class EchoCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Echoes its parsed arguments back as the result data."""

    name = "echo"

    def __init__(self, runs: list[str] | None = None) -> None:
        self._runs = runs if runs is not None else []

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("word", help="Word to echo")
        parser.add_argument("--count", type=int, default=1, help="Repetitions")
        parser.add_argument("--ratio", type=float, default=0.5, help="Ratio")
        parser.add_argument("--note", default="", help="Free text")
        parser.add_argument("--loud", action="store_true", help="Shout")
        parser.add_argument("--no-color", dest="color", action="store_false", help="Disable color")
        parser.add_argument("--tag", action="append", default=[], help="Tag (repeatable)")
        # Declared before --labels to prove REMAINDER still goes last in argv.
        parser.add_argument("--extra", nargs=argparse.REMAINDER, help="Passed through")
        parser.add_argument("--labels", nargs="+", default=["base"], help="Labels")
        parser.add_argument("--mode", choices=["fast", "slow"], default="fast", help="Speed")
        parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON")
        parser.add_argument("--token", help="Credential")
        parser.add_argument("--api-key", help="API key")

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        self._runs.append(args.word)
        data = {
            "word": args.word,
            "count": args.count,
            "ratio": args.ratio,
            "note": args.note,
            "loud": args.loud,
            "color": args.color,
            "tag": args.tag,
            "labels": args.labels,
            "mode": args.mode,
            "extra": args.extra or [],
            "json_mode": ctx.config.json_mode,
        }
        return CommandResult.success(data, action="echo", summary=f"Echoed {args.word}")


class FailingCommand(Command[argparse.Namespace, dict[str, Any]]):
    name = "fail"

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        return CommandResult.failure("The build broke", data={"target": "//foo:bar"}, action="fail")


class RaisingCommand(Command[argparse.Namespace, dict[str, Any]]):
    name = "raise"

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        raise ExecutionError("Gerrit is unreachable", remediation="Run gcert and retry.")


class CrashingCommand(Command[argparse.Namespace, dict[str, Any]]):
    name = "crash"

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        raise RuntimeError("request failed with --token=s3cr3t")


class LegacyPrintingCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Prints its own JSON, the way older emu-dev-cli commands do."""

    name = "legacy"

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        print("{phase: fetch}")
        print(json.dumps({"status": "success", "subject": UNICODE_TEXT, "json": args.json}))
        print("warning: cache is stale", file=sys.stderr)
        return CommandResult.success(metadata={META_KEY_ALREADY_RENDERED: True})


class BrokenParserCommand(Command[argparse.Namespace, dict[str, Any]]):
    """A command whose parser fails to build, standing in for a bug in a tool."""

    name = "broken"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        raise RuntimeError("parser setup failed")

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        return CommandResult.success({})


class ExitingValidateCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Raises SystemExit inside validate(), which bypasses CliDispatcher's execute() catch."""

    name = "sys_exit"

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        raise SystemExit("bad flag --secret=s3cr3t")

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        return CommandResult.success({})


class NanResultCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Returns a non-finite float to verify wire output stays valid RFC 8259 JSON."""

    name = "nan_result"

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        return CommandResult.success({"delta": float("nan"), "finite": [1.5, float("inf")]})


class BrokenPipeWriter(io.TextIOBase):
    """A stdout stand-in for a client that has gone away."""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        raise BrokenPipeError("client went away")


class Probe:
    """Lets a test observe a command that runs on the server's worker thread."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.runs = 0


class BlockingCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Polls (sleeps) until the call is cancelled, like `gerrit stack watch`."""

    name = "block"

    def __init__(self, probe: Probe, *, sleep_fn: Any = time.sleep) -> None:
        self._probe = probe
        self._sleep_fn = sleep_fn

    def execute(self, args: argparse.Namespace, ctx: ExecutionContext) -> CommandResult[Any]:
        self._probe.runs += 1
        self._probe.started.set()
        try:
            self._sleep_fn(WAIT_SEC * 6)
        except ToolCancelled:
            self._probe.cancelled.set()
            raise
        return CommandResult.success({"slept": True}, action="block")


def _spec(name: str, command: Any, **kwargs: Any) -> ToolSpec:
    return ToolSpec(name=name, title=name.title(), description=f"The {name} tool.", command=command, **kwargs)


def _fake_tools(runs: list[str] | None = None) -> dict[str, ToolSpec]:
    return build_tool_catalog([
        _spec("echo", functools.partial(EchoCommand, runs)),
        _spec("fail", FailingCommand),
        _spec("raise", RaisingCommand),
        _spec("crash", CrashingCommand),
        _spec("legacy", LegacyPrintingCommand),
    ])


def run_session(
    *lines: str, tools: dict[str, ToolSpec] | None = None, ctx: ExecutionContext | None = None
) -> list[dict[str, Any]]:
    """Runs one server session over in-memory streams and returns every message it sent."""
    with tempfile.TemporaryDirectory() as tmp:
        writer = io.StringIO()
        server = McpServer(
            _fake_tools() if tools is None else tools,
            ctx or _context(tmp),
            reader=io.StringIO("".join(lines)),
            writer=writer,
            server_version="abc1234",
            instructions="Use the tools.",
        )
        server.serve()
        return _decode(writer.getvalue())


def _tool_result(messages: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    response = _by_id(messages)[request_id]
    return response["result"]


class LineFeed(io.TextIOBase):
    """A stdin stand-in whose readline() blocks until the test feeds a line."""

    _INTERRUPT = object()

    def __init__(self) -> None:
        super().__init__()
        self._lines: queue.Queue[Any] = queue.Queue()

    def feed(self, line: str) -> None:
        self._lines.put(line)

    def end(self) -> None:
        self._lines.put("")

    def interrupt(self) -> None:
        self._lines.put(self._INTERRUPT)

    def readline(self, size: int = -1) -> str:  # pylint: disable=unused-argument
        line = self._lines.get(timeout=WAIT_SEC)
        if line is self._INTERRUPT:
            raise KeyboardInterrupt
        return line


class MessageSink(io.TextIOBase):
    """A stdout stand-in that parses each message and lets the test wait for responses."""

    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self.messages: list[dict[str, Any]] = []

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self._cond:
            self.messages.extend(_decode(text))
            self._cond.notify_all()
        return len(text)

    def wait_for(self, request_id: Any) -> dict[str, Any]:
        with self._cond:
            if not self._cond.wait_for(lambda: request_id in _by_id(self.messages), WAIT_SEC):
                raise AssertionError(f"No response to request {request_id!r}: {self.messages}")
            return _by_id(self.messages)[request_id]


class LiveSession:
    """Runs a server on a background thread so the test can interleave messages."""

    def __init__(self, tools: dict[str, ToolSpec], ctx: ExecutionContext) -> None:
        self.input = LineFeed()
        self.output = MessageSink()
        self.error: BaseException | None = None
        self._server = McpServer(tools, ctx, reader=self.input, writer=self.output)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.input.feed(_initialize())
        self.output.wait_for(1)

    def _serve(self) -> None:
        try:
            self._server.serve()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            self.error = exc

    def join(self) -> None:
        self._thread.join(WAIT_SEC)
        if self._thread.is_alive():
            raise AssertionError("The MCP server did not stop.")


class McpProtocolTest(unittest.TestCase):
    """JSON-RPC framing and the MCP lifecycle."""

    def test_initialize_negotiates_the_version_and_reports_the_tools_capability(self) -> None:
        messages = run_session(_initialize(version="2025-06-18"), _message("notifications/initialized"))
        self.assertEqual(len(messages), 1)
        result = messages[0]["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"]["name"], "emu-dev-cli")
        self.assertEqual(result["serverInfo"]["version"], "abc1234")
        self.assertEqual(result["instructions"], "Use the tools.")

    def test_initialize_answers_an_unknown_version_with_the_latest(self) -> None:
        messages = run_session(_initialize(version="2099-01-01"))
        self.assertEqual(messages[0]["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_ping_works_before_and_after_initialize(self) -> None:
        messages = _by_id(run_session(_message("ping", request_id="early"), _initialize(2), _message("ping", request_id=3)))
        self.assertEqual(messages["early"]["result"], {})
        self.assertEqual(messages[3]["result"], {})

    def test_requests_before_initialize_are_rejected(self) -> None:
        error = run_session(_message("tools/list", request_id=1))[0]["error"]
        self.assertEqual(error["code"], ErrorCode.INVALID_REQUEST)
        self.assertIn("initialize", error["message"])

    def test_unknown_methods_are_not_found_even_before_initialize(self) -> None:
        # Newer clients probe with server/discover and fall back to initialize on an error.
        error = run_session(_message("server/discover", request_id=1))[0]["error"]
        self.assertEqual(error["code"], ErrorCode.METHOD_NOT_FOUND)
        self.assertIn("tools/call", error["message"])

    def test_notifications_and_client_responses_get_no_reply(self) -> None:
        messages = run_session(
            _initialize(),
            _message("notifications/initialized"),
            _message("notifications/progress", {"progressToken": 1, "progress": 1}),
            _message("notifications/cancelled", {"requestId": 404}),
            _message("vendor/unknown"),
            json.dumps({"jsonrpc": "2.0", "id": 9, "result": {}}) + "\n",
            "\n   \n",
        )
        self.assertEqual([m["id"] for m in messages], [1])

    def test_malformed_messages_get_json_rpc_errors(self) -> None:
        messages = run_session(
            "{not json\n",
            "[]\n",
            "42\n",
            json.dumps({"id": 5, "method": "ping"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": True, "method": "ping"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 10}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 6, "method": "ping", "params": []}) + "\n",
            _message("initialize", {"capabilities": {}}, request_id=7),
            _initialize(8),
            _initialize(9),
        )
        codes = [(m.get("id"), m["error"]["code"]) for m in messages if "error" in m]
        self.assertEqual(
            codes,
            [
                (None, ErrorCode.PARSE_ERROR),
                (None, ErrorCode.INVALID_REQUEST),
                (None, ErrorCode.INVALID_REQUEST),
                (5, ErrorCode.INVALID_REQUEST),
                (None, ErrorCode.INVALID_REQUEST),
                (10, ErrorCode.INVALID_REQUEST),
                (6, ErrorCode.INVALID_PARAMS),
                (7, ErrorCode.INVALID_PARAMS),
                (9, ErrorCode.INVALID_REQUEST),
            ],
        )
        self.assertIn("result", _by_id(messages)[8])


class McpToolListingTest(unittest.TestCase):
    """tools/list and the schemas derived from the CLI parsers."""

    def setUp(self) -> None:
        messages = run_session(_initialize(), _message("tools/list", request_id=2), tools=build_tool_catalog())
        tools = _tool_result(messages, 2)["tools"]
        self.tools = {tool["name"]: tool for tool in tools}

    def _props(self, name: str) -> dict[str, Any]:
        return self.tools[name]["inputSchema"]["properties"]

    def test_lists_every_default_tool_with_an_object_schema(self) -> None:
        self.assertEqual(set(self.tools), DEFAULT_TOOL_NAMES)
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                self.assertTrue(tool["title"])
                self.assertTrue(tool["description"])
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertNotIn("json", schema["properties"])
                self.assertNotIn("token", schema["properties"])
                for prop in schema["properties"].values():
                    self.assertNotEqual(prop.get("default"), argparse.SUPPRESS)
                read_only = name not in STATE_CHANGING_TOOL_NAMES
                self.assertEqual(tool["annotations"]["readOnlyHint"], read_only)

    def test_schemas_follow_the_cli_parsers(self) -> None:
        status = self._props("gerrit_stack_status")
        self.assertEqual(list(status), ["cl", "topic", "repo", "host"])
        self.assertEqual(status["cl"], {
            "type": "string",
            "description": "CL number, ag/<id>, Change-Id, Gerrit URL, or HEAD (default: HEAD)",
            "default": "HEAD",
        })
        self.assertNotIn("default", status["host"])
        checks = self._props("gerrit_stack_pre_upload")["checks"]
        self.assertEqual(checks["type"], "array")
        self.assertEqual(checks["items"], {"type": "string", "enum": list(CHECK_NAMES)})
        self.assertIn("safe", self._props("tidy_check")["level"]["enum"])
        find_bug = self.tools["crash_find_bug"]["inputSchema"]
        self.assertEqual(find_bug["required"], ["crash_id"])
        self.assertEqual(find_bug["properties"]["component_id"]["type"], "integer")
        perf = self.tools["perf_compare"]["inputSchema"]
        self.assertEqual(perf["required"], ["baseline", "candidate"])
        self.assertEqual(perf["properties"]["settle_seconds"]["type"], "number")
        self.assertEqual(self._props("crash_reproduce")["extra_args"]["type"], "array")
        ping = self.tools["gerrit_ping_for_review"]["inputSchema"]
        self.assertEqual(ping["required"], ["to"])
        self.assertNotIn("cl_flag", ping["properties"])
        self.assertEqual(self._props("gerrit_comments")["host"]["type"], "string")

    def test_state_changing_tools_only_act_when_asked(self) -> None:
        self.assertIs(self._props("gerrit_stack_sync")["dry_run"]["default"], True)
        self.assertTrue(self.tools["gerrit_stack_sync"]["annotations"]["destructiveHint"])
        watch = self._props("gerrit_stack_watch")
        self.assertIs(watch["once"]["default"], True)
        self.assertIs(watch["no_rebase"]["default"], True)
        self.assertNotIn("rebase", watch)
        self.assertTrue(self.tools["gerrit_stack_watch"]["annotations"]["destructiveHint"])
        self.assertIs(self._props("gerrit_ping_for_review")["no_wait"]["default"], True)

    def test_read_only_catalog_drops_state_changing_tools(self) -> None:
        catalog = build_tool_catalog(read_only=True)
        self.assertEqual(set(catalog), DEFAULT_TOOL_NAMES - STATE_CHANGING_TOOL_NAMES)

    def test_tools_list_has_one_page(self) -> None:
        messages = run_session(_initialize(), _message("tools/list", {"cursor": "next"}, request_id=2))
        self.assertEqual(_by_id(messages)[2]["error"]["code"], ErrorCode.INVALID_PARAMS)


class ToolArgumentTest(unittest.TestCase):
    """Mapping between JSON tool arguments and the command's argparse parser."""

    def setUp(self) -> None:
        self.spec = _spec("echo", EchoCommand)
        self.params = self.spec.parameters

    def test_schema_types_come_from_the_parser(self) -> None:
        props = self.spec.definition()["inputSchema"]["properties"]
        self.assertEqual(
            list(props),
            ["word", "count", "ratio", "note", "loud", "no_color", "tag", "extra", "labels", "mode"],
        )
        self.assertNotIn("api_key", props)
        self.assertEqual(self.spec.definition()["inputSchema"]["required"], ["word"])
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["ratio"]["type"], "number")
        self.assertEqual(props["loud"], {"type": "boolean", "description": "Shout", "default": False})
        # store_false flags keep the name their help text describes.
        self.assertEqual(props["no_color"]["description"], "Disable color")
        self.assertEqual(props["tag"]["items"], {"type": "string"})
        self.assertEqual(props["labels"]["default"], ["base"])
        self.assertEqual(props["mode"]["enum"], ["fast", "slow"])
        self.assertEqual(props["extra"]["type"], "array")

    def test_argv_puts_positionals_first_and_remainder_last(self) -> None:
        argv = build_argv("echo", self.params, {
            "extra": ["-gpu", "host"],
            "labels": ["a", "b"],
            "tag": ["a", UNICODE_TEXT],
            "no_color": True,
            "loud": False,
            "count": 3.0,
            "note": "-starts-with-dash",
            "word": UNICODE_TEXT,
        })
        self.assertEqual(argv, [
            UNICODE_TEXT,
            "--count=3",
            "--note=-starts-with-dash",
            "--no-color",
            "--tag=a",
            f"--tag={UNICODE_TEXT}",
            "--labels",
            "a",
            "b",
            "--extra",
            "-gpu",
            "host",
        ])
        args = self.spec.parse_arguments(
            EchoCommand(),
            {"word": "hi", "no_color": True, "count": None, "labels": ["a", "b"], "extra": ["-v"]},
        )
        self.assertFalse(args.color)
        self.assertEqual(args.count, 1)
        self.assertEqual(args.labels, ["a", "b"])
        self.assertEqual(args.extra, ["-v"])
        self.assertTrue(args.json)

    def test_bad_arguments_raise_usage_errors_naming_the_argument(self) -> None:
        self.assertEqual(json_type_name(None), "null")
        self.assertEqual(json_type_name(1.5), "number")
        self.assertEqual(json_type_name(object()), "object")
        cases = [
            ({"word": "a", "bogus": 1}, "has no argument 'bogus'"),
            ({}, "needs argument 'word'"),
            ({"word": "a", "loud": "yes"}, "'loud' of tool 'echo' must be true or false, not string"),
            ({"word": "a", "count": True}, "'count' of tool 'echo' must be an integer, not boolean"),
            ({"word": "a", "count": 1}, None),
            ({"word": "a", "count": [1, 2]}, "takes one integer, not an array"),
            ({"word": "a", "tag": [{"k": 1}]}, "'tag' of tool 'echo' must be a string, not object"),
            ({"word": "-rf"}, "cannot start with '-'"),
            ({"word": "a", "labels": ["-x"]}, "cannot start with '-'"),
            ({"word": "a", "count": "many"}, "invalid int value: 'many'"),
            ({"word": "a", "mode": "medium"}, "invalid choice: 'medium'"),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                result = _tool_result(run_session(_initialize(), _call(2, "echo", arguments)), 2)
                if expected is None:
                    self.assertFalse(result["isError"])
                    continue
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["error_code"], "USAGE_ERROR")
                self.assertIn(expected, result["structuredContent"]["error_message"])

    def test_tool_parameters_skip_arguments_a_tool_cannot_express(self) -> None:
        parser = ToolArgumentParser(prog="t", add_help=True)
        parser.add_argument("-v", "--verbose", action="count", default=0)
        parser.add_argument("--level", action="store_const", const="debug")
        parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
        parser.add_subparsers(dest="sub")
        self.assertEqual(tool_parameters(parser), {})


class McpToolCallTest(unittest.TestCase):
    """tools/call results for successful, failing, and older commands."""

    def test_success_returns_the_json_payload_as_text_and_structured_content(self) -> None:
        result = _tool_result(run_session(_initialize(), _call(2, "echo", {"word": UNICODE_TEXT, "tag": ["x"]})), 2)
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["summary"], f"Echoed {UNICODE_TEXT}")
        self.assertEqual(payload["word"], UNICODE_TEXT)
        self.assertEqual(payload["tag"], ["x"])
        self.assertTrue(payload["json_mode"])
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)
        self.assertEqual(len(result["content"]), 1)

    def test_failures_set_is_error_and_keep_the_error_payload(self) -> None:
        messages = run_session(_initialize(), _call(2, "fail"), _call(3, "raise"), _call(4, "crash"))
        failed = _tool_result(messages, 2)
        self.assertTrue(failed["isError"])
        self.assertEqual(failed["structuredContent"]["summary"], "The build broke")
        self.assertEqual(failed["structuredContent"]["target"], "//foo:bar")
        raised = _tool_result(messages, 3)
        self.assertTrue(raised["isError"])
        self.assertEqual(raised["structuredContent"]["error_code"], "EXECUTION_ERROR")
        self.assertEqual(raised["structuredContent"]["remediation"], "Run gcert and retry.")
        crashed = _tool_result(messages, 4)
        self.assertTrue(crashed["isError"])
        self.assertIn("<REDACTED>", crashed["structuredContent"]["error_message"])
        self.assertNotIn("s3cr3t", json.dumps(crashed))

    def test_output_printed_by_older_commands_is_captured(self) -> None:
        result = _tool_result(run_session(_initialize(), _call(2, "legacy")), 2)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], {"status": "success", "subject": UNICODE_TEXT, "json": True})
        self.assertTrue(result["content"][0]["text"].startswith("{phase: fetch}\n"))
        self.assertEqual(result["content"][1]["text"], "stderr:\nwarning: cache is stale")

    def test_internal_errors_are_reported_and_the_session_continues(self) -> None:
        def explode(**kwargs: Any) -> Command[Any, Any]:
            raise RuntimeError("factory failed with --token=s3cr3t")

        tools = build_tool_catalog([
            _spec("broken", BrokenParserCommand),
            _spec("explode", explode),
            _spec("sys_exit", ExitingValidateCommand),
            _spec("nan_result", NanResultCommand),
        ])
        with self.assertLogs("commands.mcp.server", level="ERROR") as logs:
            messages = _by_id(run_session(
                _initialize(),
                _message("tools/list", request_id=2),
                _call(3, "explode"),
                _call(5, "sys_exit"),
                _call(6, "nan_result"),
                _message("ping", request_id=4),
                tools=tools,
            ))
        self.assertEqual(len(logs.records), 3)
        self.assertEqual(messages[2]["error"]["code"], ErrorCode.INTERNAL_ERROR)
        self.assertIn("parser setup failed", messages[2]["error"]["message"])
        exploded = messages[3]["result"]
        self.assertTrue(exploded["isError"])
        self.assertIn("Internal error in tool 'explode'", exploded["content"][0]["text"])
        self.assertNotIn("s3cr3t", json.dumps(exploded))
        exited = messages[5]["result"]
        self.assertTrue(exited["isError"])
        self.assertIn("<REDACTED>", exited["content"][0]["text"])
        self.assertNotIn("s3cr3t", json.dumps(exited))
        nan_payload = messages[6]["result"]["structuredContent"]
        self.assertIsNone(nan_payload["delta"])
        self.assertEqual(nan_payload["finite"], [1.5, None])
        self.assertEqual(messages[4]["result"], {})

    def test_a_client_that_went_away_does_not_crash_the_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = McpServer(
                _fake_tools(),
                _context(tmp),
                reader=io.StringIO(_initialize() + _call(2, "echo", {"word": "x"})),
                writer=BrokenPipeWriter(),
            )
            with self.assertLogs("commands.mcp.server", level="WARNING") as logs:
                server.serve()
        self.assertIn("client went away", "\n".join(logs.output))

    def test_protocol_errors_cover_unknown_tools_and_malformed_calls(self) -> None:
        messages = _by_id(run_session(
            _initialize(),
            _call(2, "no_such_tool"),
            _call(3, "echo", []),
            _message("tools/call", {"arguments": {}}, request_id=4),
        ))
        for request_id in (2, 3, 4):
            with self.subTest(request_id=request_id):
                self.assertEqual(messages[request_id]["error"]["code"], ErrorCode.INVALID_PARAMS)
        self.assertIn("tools/list", messages[2]["error"]["message"])
        self.assertIn("array", messages[3]["error"]["message"])
        self.assertIn("'name'", messages[4]["error"]["message"])

    def test_crash_list_returns_the_f1_query(self) -> None:
        result = _tool_result(
            run_session(_initialize(), _call(2, "crash_list", {"print_sql": True, "hours": 6}), tools=build_tool_catalog()),
            2,
        )
        self.assertFalse(result["isError"])
        self.assertIn("INTERVAL 6 HOUR", result["structuredContent"]["sql"])

    def test_crash_reproduce_builds_a_plan_without_running_anything(self) -> None:
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-15953806"},
                "os": {"Name": "Linux"},
                "cpu": {"Architecture": "amd64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / UNICODE_TEXT
            work_dir.mkdir()
            (work_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            ctx = _context(tmp)
            arguments = {"crash_id": "05d8356e2f800000", "work_dir": str(work_dir), "lldb": True}
            messages = run_session(_initialize(), _call(2, "crash_reproduce", arguments), tools=build_tool_catalog(), ctx=ctx)
            result = _tool_result(messages, 2)
            self.assertFalse(result["isError"])
            plan = result["structuredContent"]["plan"]
            self.assertEqual(plan["crash_id"], "05d8356e2f800000")
            self.assertEqual(plan["build_id"], "15953806")
            self.assertEqual(ctx.fake_process_runner.calls, [])

    def test_tidy_check_reports_the_error_the_command_prints(self) -> None:
        result = _tool_result(run_session(_initialize(), _call(2, "tidy_check", {}), tools=build_tool_catalog()), 2)
        self.assertTrue(result["isError"])
        self.assertIn("Must specify either a positional target, --diff, or --file", result["content"][0]["text"])

    def test_ping_for_review_sends_a_chat_dm_through_the_fake_services(self) -> None:
        detail = stack_change(
            43386500,
            subject=f"feat(mcp): Add server {UNICODE_TEXT}",
            labels={"Presubmit-Verified": 2},
        )
        gob_curl = shutil.which("gob-curl") or "/usr/bin/gob-curl"
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _context(tmp)
            ctx.fake_process_runner.register_prefix(
                [gob_curl, "-s", "-f", "-S", "--compressed", "-X", "GET"],
                ProcessResult(args=(gob_curl,), returncode=0, stdout=")]}'\n" + json.dumps(detail)),
            )
            arguments = {"cl": "43386500", "to": "joshuaduong"}
            messages = run_session(_initialize(), _call(2, "gerrit_ping_for_review", arguments), tools=build_tool_catalog(), ctx=ctx)
            result = _tool_result(messages, 2)
            self.assertFalse(result["isError"], result)
            self.assertEqual(result["structuredContent"]["recipient"], "joshuaduong@google.com")
            sent = ctx.fake_chat_service.sent_dms
            self.assertEqual(len(sent), 1)
            self.assertIn(UNICODE_TEXT, sent[0].message)


class McpCancellationTest(unittest.TestCase):
    """notifications/cancelled, queued calls, and shutdown."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.probe = Probe()
        self.runs: list[str] = []
        self.tools = build_tool_catalog([
            _spec("block", functools.partial(BlockingCommand, self.probe), takes_sleep_fn=True),
            _spec("echo", functools.partial(EchoCommand, self.runs)),
        ])
        self.session = LiveSession(self.tools, _context(self._tmp.name))

    def _wait(self, event: threading.Event) -> None:
        self.assertTrue(event.wait(WAIT_SEC), "timed out waiting for the tool")

    def test_cancelled_call_stops_polling_and_gets_no_response(self) -> None:
        self.session.input.feed(_call(2, "block"))
        self._wait(self.probe.started)
        self.session.input.feed(_message("notifications/cancelled", {"requestId": 2, "reason": "user stopped it"}))
        self._wait(self.probe.cancelled)
        self.session.input.feed(_message("ping", request_id=3))
        self.assertEqual(self.session.output.wait_for(3)["result"], {})
        # Calls run in order, so once echo answers, the cancelled call has finished.
        self.session.input.feed(_call(4, "echo", {"word": "after"}))
        self.assertFalse(self.session.output.wait_for(4)["result"]["isError"])
        self.session.input.end()
        self.session.join()
        self.assertNotIn(2, _by_id(self.session.output.messages))

    def test_queued_call_cancelled_before_it_starts_never_runs(self) -> None:
        self.session.input.feed(_call(2, "block"))
        self._wait(self.probe.started)
        self.session.input.feed(_call(3, "echo", {"word": "queued"}))
        self.session.input.feed(_message("notifications/cancelled", {"requestId": 3}))
        self.session.input.feed(_message("notifications/cancelled", {"requestId": 2}))
        self.session.input.feed(_call(4, "echo", {"word": "last"}))
        self.session.output.wait_for(4)
        self.session.input.end()
        self.session.join()
        self.assertEqual(self.runs, ["last"])
        responses = _by_id(self.session.output.messages)
        self.assertNotIn(2, responses)
        self.assertNotIn(3, responses)

    def test_running_request_id_cannot_be_reused(self) -> None:
        self.session.input.feed(_call(2, "block"))
        self._wait(self.probe.started)
        self.session.input.feed(_call(2, "echo", {"word": "dup"}))
        self.assertEqual(self.session.output.wait_for(2)["error"]["code"], ErrorCode.INVALID_REQUEST)
        self.session.input.feed(_message("notifications/cancelled", {"requestId": 2}))
        self._wait(self.probe.cancelled)
        self.session.input.end()
        self.session.join()
        self.assertEqual(self.runs, [])

    def test_end_of_input_finishes_accepted_calls(self) -> None:
        self.session.input.feed(_call(2, "echo", {"word": "one"}))
        self.session.input.feed(_call(3, "echo", {"word": "two"}))
        self.session.input.end()
        self.session.join()
        responses = _by_id(self.session.output.messages)
        self.assertEqual(responses[3]["result"]["structuredContent"]["word"], "two")
        self.assertEqual(self.runs, ["one", "two"])

    def test_interrupt_cancels_the_running_call(self) -> None:
        self.session.input.feed(_call(2, "block"))
        self._wait(self.probe.started)
        self.session.input.interrupt()
        self.session.join()
        self.assertIsInstance(self.session.error, KeyboardInterrupt)
        self.assertTrue(self.probe.cancelled.is_set())
        self.assertNotIn(2, _by_id(self.session.output.messages))

    def test_real_polling_command_stops_when_cancelled(self) -> None:
        polled = threading.Event()
        gob_curl = shutil.which("gob-curl") or "/usr/bin/gob-curl"
        detail = stack_change(43386500, labels={"Presubmit-Verified": 0})
        ctx = _context(self._tmp.name)

        def on_poll(call: Any) -> ProcessResult:
            polled.set()
            return ProcessResult(args=call.args, returncode=0, stdout=")]}'\n" + json.dumps(detail))

        ctx.fake_process_runner.register_prefix([gob_curl], on_poll)
        session = LiveSession(build_tool_catalog(), ctx)
        session.input.feed(_call(2, "gerrit_ping_for_review", {
            "cl": "43386500",
            "to": "joshuaduong",
            "no_wait": False,
            "poll_interval": 60,
            "timeout": 120,
        }))
        self._wait(polled)
        session.input.feed(_message("notifications/cancelled", {"requestId": 2}))
        session.input.feed(_message("ping", request_id=3))
        self.assertEqual(session.output.wait_for(3)["result"], {})
        session.input.end()
        session.join()
        self.assertNotIn(2, _by_id(session.output.messages))
        self.assertEqual(ctx.fake_chat_service.sent_dms, [])


class McpCommandTest(unittest.TestCase):
    """`emu-dev-cli mcp [serve]` registration and the stdio entry point."""

    def _dispatch(self, argv: list[str], lines: list[str]) -> tuple[int, list[dict[str, Any]], str]:
        stdin, stdout, rendered = io.StringIO("".join(lines)), io.StringIO(), io.StringIO()
        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        subparsers = parser.add_subparsers(dest="subcommand")
        bind_command(subparsers, McpCommand([McpServeCommand(stdin=stdin, stdout=stdout)]))
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = CliDispatcher(context=_context(tmp), renderer=OutputRenderer(stdout=rendered, stderr=rendered))
            exit_code = dispatcher.dispatch(parser.parse_args(argv))
        return exit_code, _decode(stdout.getvalue()), rendered.getvalue()

    def test_mcp_alone_serves_every_tool(self) -> None:
        exit_code, messages, rendered = self._dispatch(["mcp"], [_initialize(), _message("tools/list", request_id=2)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(rendered, "")
        self.assertIn(os.getcwd(), messages[0]["result"]["instructions"])
        self.assertEqual({t["name"] for t in _tool_result(messages, 2)["tools"]}, DEFAULT_TOOL_NAMES)

    def test_serve_read_only_offers_only_read_only_tools(self) -> None:
        _, messages, _ = self._dispatch(["mcp", "serve", "--read-only"], [_initialize(), _message("tools/list", request_id=2)])
        names = {t["name"] for t in _tool_result(messages, 2)["tools"]}
        self.assertEqual(names, DEFAULT_TOOL_NAMES - STATE_CHANGING_TOOL_NAMES)

    def _run_python(self, args: list[str], stdin: str = "", home: str = "") -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), PYTHONDONTWRITEBYTECODE="1")
        if home:
            env["HOME"] = home
        return subprocess.run(
            [sys.executable, *args],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            env=env,
            timeout=60,
            check=False,
        )

    def test_protocol_stdio_keeps_commands_off_the_client_pipes(self) -> None:
        code = textwrap.dedent("""
            import subprocess, sys
            from commands.mcp import protocol_stdio
            with protocol_stdio() as (reader, writer):
                print("stray print")
                subprocess.run([sys.executable, "-c", "print('child output')"], check=True)
                stdin_text = sys.stdin.read()
                writer.write(f"{reader.readline().strip()} stdin={stdin_text!r}\\n")
            print("after")
        """)
        result = self._run_python(["-c", code], stdin=f"request {UNICODE_TEXT}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"request {UNICODE_TEXT} stdin=''\nafter\n")
        self.assertIn("stray print", result.stderr)
        self.assertIn("child output", result.stderr)

    def test_protocol_stdio_falls_back_when_stdout_has_no_file_descriptor(self) -> None:
        in_memory = io.StringIO()
        with contextlib.redirect_stdout(in_memory), protocol_stdio() as (_, writer):
            self.assertIs(writer, in_memory)

    def test_emu_dev_cli_mcp_serve_over_real_stdio(self) -> None:
        lines = [
            _initialize(),
            _message("notifications/initialized"),
            _message("tools/list", request_id=2),
            _call(3, "crash_list", {"print_sql": True, "days": 1}),
            _message("ping", request_id=4),
        ]
        with tempfile.TemporaryDirectory() as home:
            result = self._run_python([str(TOOL_DIR / "src" / "__main__.py"), "mcp", "serve", "--read-only"], "".join(lines), home)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = _by_id(_decode(result.stdout))
        self.assertEqual(sorted(responses), [1, 2, 3, 4])
        self.assertEqual(len(_tool_result(list(responses.values()), 2)["tools"]), len(DEFAULT_TOOL_NAMES - STATE_CHANGING_TOOL_NAMES))
        self.assertIn("INTERVAL 24 HOUR", responses[3]["result"]["structuredContent"]["sql"])
        self.assertIn("MCP client test-client", result.stderr)


if __name__ == "__main__":
    unittest.main()
