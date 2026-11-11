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

"""A Model Context Protocol (MCP) server: newline-delimited JSON-RPC 2.0 over text streams.

The main thread reads requests and answers the quick ones (initialize, ping,
tools/list) right away. Tool calls run one at a time on a worker thread, so the
reader stays free to accept pings and cancellations while a command runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import enum
import json
import logging
import math
import threading
import time
from typing import Any, TextIO

from commands.mcp.arguments import json_type_name
from commands.mcp.tools import ToolResult, ToolSpec, run_tool
from core.context import ExecutionContext
from core.errors import redact_sensitive_text

LOGGER = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
# Newest first. `initialize` answers with the client's version when it is in this list.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_NAME = "emu-dev-cli"
SERVER_TITLE = "Android Emulator developer CLI"
# Requests a client may send before `initialize`.
_PRE_INITIALIZE_METHODS = frozenset({"initialize", "ping"})

RequestId = str | int
RequestHandler = Callable[[RequestId, Mapping[str, Any]], dict[str, Any] | None]


class ErrorCode(enum.IntEnum):
    """JSON-RPC 2.0 error codes."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """A request failure that the client receives as a JSON-RPC error response."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolCancelled(Exception):
    """Raised inside a running tool when the client cancels its request."""


class ToolCall:
    """Cancellation state of one tools/call request."""

    def __init__(self, request_id: RequestId) -> None:
        self.request_id = request_id
        self.reason = ""
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self, reason: str) -> None:
        self.reason = reason
        self._cancelled.set()

    def sleep(self, seconds: float) -> None:
        """Waits like time.sleep, but raises ToolCancelled as soon as the call is cancelled."""
        if self._cancelled.wait(max(0.0, seconds)):
            raise ToolCancelled(f"Tool call {self.request_id!r} was cancelled: {self.reason}")


def _is_request_id(value: Any) -> bool:
    # MCP request IDs are strings or integers, never null; bool is an int subclass in Python.
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _sanitize_floats(value: Any) -> Any:
    """Replaces non-finite floats (NaN, Infinity) with None so wire output stays valid JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_floats(item) for item in value]
    return value


class McpServer:
    """Serves MCP tools to one client over a pair of text streams."""

    def __init__(
        self,
        tools: Mapping[str, ToolSpec],
        ctx: ExecutionContext,
        *,
        reader: TextIO,
        writer: TextIO,
        server_version: str = "",
        instructions: str = "",
    ) -> None:
        self._tools = dict(tools)
        self._ctx = ctx
        self._reader = reader
        self._writer = writer
        self._server_version = server_version or "unknown"
        self._instructions = instructions
        self._initialized = False
        self._write_lock = threading.Lock()
        self._calls_lock = threading.Lock()
        self._calls: dict[RequestId, ToolCall] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-tool")
        self._request_handlers: dict[str, RequestHandler] = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._list_tools,
            "tools/call": self._call_tool,
        }

    def serve(self) -> None:
        """Answers messages until the input ends, then finishes the tool calls it accepted.

        If reading fails or is interrupted (Ctrl-C), outstanding calls are cancelled instead.
        """
        interrupted = True
        try:
            while line := self._reader.readline():
                self._handle_line(line)
            interrupted = False
        finally:
            self.close(cancel=interrupted)

    def close(self, *, cancel: bool = False) -> None:
        """Waits for the accepted tool calls to finish, cancelling them first if `cancel`."""
        if cancel:
            with self._calls_lock:
                calls = list(self._calls.values())
            for call in calls:
                call.cancel("the MCP server is shutting down")
        self._executor.shutdown(wait=True, cancel_futures=cancel)

    def _handle_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError as exc:
            self._send_error(None, ErrorCode.PARSE_ERROR, f"The message is not valid JSON: {exc}.")
            return
        if isinstance(message, list):
            self._send_error(
                None,
                ErrorCode.INVALID_REQUEST,
                "Batched requests are not supported; send one JSON-RPC message per line.",
            )
            return
        self._handle_message(message)

    def _handle_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            self._send_error(None, ErrorCode.INVALID_REQUEST, "A message must be a JSON object.")
            return
        request_id = message.get("id")
        reply_id = request_id if _is_request_id(request_id) else None
        method = message.get("method")
        if message.get("jsonrpc") != JSONRPC_VERSION:
            error = 'A message needs "jsonrpc": "2.0".'
            self._send_error(reply_id, ErrorCode.INVALID_REQUEST, error)
            return
        if method is None and ("result" in message or "error" in message):
            return  # A response to a server request; this server sends none.
        is_request = "id" in message
        if not isinstance(method, str) or (is_request and reply_id is None):
            self._send_error(
                reply_id,
                ErrorCode.INVALID_REQUEST,
                "A request needs a string 'method' and a string or integer 'id'.",
            )
            return
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if is_request:
                error = f"The params of '{method}' must be an object, not {json_type_name(params)}."
                self._send_error(reply_id, ErrorCode.INVALID_PARAMS, error)
            return
        if is_request:
            self._handle_request(reply_id, method, params)
        elif method == "notifications/cancelled":
            self._cancel(params)
        # Other notifications, such as notifications/initialized, need no action.

    def _handle_request(
        self, request_id: RequestId, method: str, params: Mapping[str, Any]
    ) -> None:
        handler = self._request_handlers.get(method)
        try:
            if handler is None:
                raise JsonRpcError(
                    ErrorCode.METHOD_NOT_FOUND,
                    f"Method '{method}' is not supported. This server supports: "
                    f"{', '.join(self._request_handlers)}.",
                )
            if not self._initialized and method not in _PRE_INITIALIZE_METHODS:
                raise JsonRpcError(
                    ErrorCode.INVALID_REQUEST, f"Send 'initialize' before '{method}'."
                )
            result = handler(request_id, params)
        except JsonRpcError as err:
            self._send_error(request_id, err.code, err.message)
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # A bug in one handler must not end the session.
            LOGGER.exception("MCP request '%s' failed", method)
            detail = redact_sensitive_text(str(exc)) or type(exc).__name__
            self._send_error(
                request_id, ErrorCode.INTERNAL_ERROR, f"Internal error in '{method}': {detail}"
            )
            return
        if result is not None:  # None means the response is sent later.
            self._send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result})

    def _initialize(self, request_id: RequestId, params: Mapping[str, Any]) -> dict[str, Any]:
        del request_id
        if self._initialized:
            raise JsonRpcError(ErrorCode.INVALID_REQUEST, "The session is already initialized.")
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            raise JsonRpcError(
                ErrorCode.INVALID_PARAMS,
                "'initialize' needs a protocolVersion string, "
                f'such as "{LATEST_PROTOCOL_VERSION}".',
            )
        supported = requested in SUPPORTED_PROTOCOL_VERSIONS
        version = requested if supported else LATEST_PROTOCOL_VERSION
        client = params.get("clientInfo")
        client = client if isinstance(client, dict) else {}
        LOGGER.info(
            "MCP client %s %s connected with protocol %s (asked for %s).",
            client.get("name", "unknown"),
            client.get("version", ""),
            version,
            requested,
        )
        self._initialized = True
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": SERVER_TITLE,
                "version": self._server_version,
            },
        }
        if self._instructions:
            result["instructions"] = self._instructions
        return result

    def _ping(self, request_id: RequestId, params: Mapping[str, Any]) -> dict[str, Any]:
        del request_id, params
        return {}

    def _list_tools(self, request_id: RequestId, params: Mapping[str, Any]) -> dict[str, Any]:
        del request_id
        if params.get("cursor") is not None:
            raise JsonRpcError(
                ErrorCode.INVALID_PARAMS,
                "tools/list returns every tool in one page, so there is no next cursor.",
            )
        return {"tools": [spec.definition() for spec in self._tools.values()]}

    def _call_tool(self, request_id: RequestId, params: Mapping[str, Any]) -> None:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(
                ErrorCode.INVALID_PARAMS,
                "'tools/call' needs a non-empty string 'name' parameter. "
                "Call tools/list to see the available tools.",
            )
        spec = self._tools.get(name)
        if spec is None:
            raise JsonRpcError(
                ErrorCode.INVALID_PARAMS,
                f"Unknown tool {name!r}. Call tools/list to see the available tools.",
            )
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(
                ErrorCode.INVALID_PARAMS,
                f"The arguments of tool '{name}' must be an object, "
                f"not {json_type_name(arguments)}.",
            )
        call = ToolCall(request_id)
        with self._calls_lock:
            if request_id in self._calls:
                raise JsonRpcError(
                    ErrorCode.INVALID_REQUEST,
                    f"Request ID {request_id!r} is already used by a running tools/call.",
                )
            self._calls[request_id] = call
        self._executor.submit(self._run_call, call, spec, arguments)

    def _run_call(self, call: ToolCall, spec: ToolSpec, arguments: Mapping[str, Any]) -> None:
        started = time.monotonic()
        try:
            if call.cancelled:
                return  # Cancelled while it waited for an earlier call.
            result = run_tool(spec, arguments, self._ctx, sleep_fn=call.sleep)
        except (Exception, SystemExit) as exc:  # pylint: disable=broad-exception-caught
            # run_tool reports command failures itself; this catches server bugs or sys.exit.
            LOGGER.exception("MCP tool '%s' failed", spec.name)
            detail = redact_sensitive_text(str(exc)) or type(exc).__name__
            text = f"Internal error in tool '{spec.name}': {detail}"
            result = ToolResult(text=text, is_error=True)
        finally:
            with self._calls_lock:
                self._calls.pop(call.request_id, None)
        elapsed = time.monotonic() - started
        if call.cancelled:
            # MCP: the client ignores responses to cancelled requests, so send none.
            LOGGER.info("MCP tool '%s' cancelled after %.1fs: %s", spec.name, elapsed, call.reason)
            return
        outcome = "failed" if result.is_error else "finished"
        LOGGER.info("MCP tool '%s' %s in %.1fs.", spec.name, outcome, elapsed)
        self._send({"jsonrpc": JSONRPC_VERSION, "id": call.request_id, "result": result.to_mcp()})

    def _cancel(self, params: Mapping[str, Any]) -> None:
        request_id = params.get("requestId")
        if not _is_request_id(request_id):
            return
        with self._calls_lock:
            call = self._calls.get(request_id)
        if call is not None:
            call.cancel(str(params.get("reason") or "cancelled by the client"))

    def _send_error(self, request_id: RequestId | None, code: ErrorCode, message: str) -> None:
        error = {"code": int(code), "message": message}
        self._send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error})

    def _send(self, message: dict[str, Any]) -> None:
        # ASCII-only strict JSON never contains a raw newline, NaN, or misdecoded byte.
        try:
            line = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        except ValueError:
            clean = _sanitize_floats(message)
            line = json.dumps(clean, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        with self._write_lock:
            try:
                self._writer.write(line + "\n")
                self._writer.flush()
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not send an MCP message to the client: %s", exc)
