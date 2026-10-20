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

"""Stdio Model Context Protocol (MCP) server for the Android Emulator Harness.

Exposes structured emulator development tools (`aemu_validate_commit_message`,
`aemu_search_reusable_libs`, `aemu_get_harness_state`,
`aemu_diagnose_build_or_test_log`) over JSON-RPC 2.0 stdio transport.
"""

import json
import sys
from typing import Any, Dict, List, Optional

from commands.harness.diagnostics import diagnose_command_failure
from commands.harness.guardrails import (
    _load_commit_verifier,
    discover_active_workflows,
)
from commands.harness.ledger import SessionLedgerStore

REUSABLE_LIBS_CATALOG: List[Dict[str, str]] = [
    {
        "name": "file",
        "bazel_target": "//emulator/libs/file",
        "header": "goldfish/file/file.h",
        "namespace": "android::base::file",
        "keywords": "file io utf8 unicode path read write fopen ifstream ofstream windows",
        "summary": "Cross-platform UTF-8 file I/O helpers (ReadFile, WriteFile, path utilities) safe on Windows non-ASCII paths.",
    },
    {
        "name": "ini_file",
        "bazel_target": "//emulator/libs/ini_file",
        "header": "android/goldfish/ini_file.h",
        "namespace": "android::goldfish::IniFile",
        "keywords": "ini config parser avd config.ini key value",
        "summary": "Typed INI configuration file parser and serializer for AVD and hardware profiles.",
    },
    {
        "name": "http",
        "bazel_target": "//emulator/libs/http",
        "header": "goldfish/http/web_server.h",
        "namespace": "goldfish::http::WebServer",
        "keywords": "http server web rest endpoint test fixture mock server",
        "summary": "Embedded HTTP WebServer (`CreateWebServer`) for production endpoints and hermetic C++ unit tests.",
    },
    {
        "name": "http_client",
        "bazel_target": "//emulator/libs/network:http_client",
        "header": "goldfish/network/http_client.h",
        "namespace": "goldfish::network::HttpClient",
        "keywords": "http client curl request get post download network",
        "summary": "High-level HTTP/HTTPS client with StatusOr error propagation.",
    },
    {
        "name": "network",
        "bazel_target": "//emulator/libs/network",
        "header": "goldfish/network/resolve.h",
        "namespace": "goldfish::network",
        "keywords": "dns resolve endpoints socket address ipv4 ipv6 network",
        "summary": "Endpoint resolution (`ResolveEndpoints`) and network address utilities.",
    },
    {
        "name": "async",
        "bazel_target": "//emulator/libs/async",
        "header": "goldfish/async/threaded_event_loop.h",
        "namespace": "goldfish::async",
        "keywords": "async libuv event loop socket timer thread callback uv_loop_t",
        "summary": "LibuvEventLoop, ThreadedEventLoop, AsyncSocket, and UvErrToAbslStatus.",
    },
    {
        "name": "system",
        "bazel_target": "//emulator/libs/system",
        "header": "android/base/system.h",
        "namespace": "android::base::System",
        "keywords": "env getenv environment variable system test_system fake",
        "summary": "System environment queries (`System::Get()->EnvGet(...)`) and `TestSystem` (`//emulator/libs/system:test-headers`).",
    },
]

MCP_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "aemu_validate_commit_message",
        "description": (
            "Validate a proposed git commit message against Android Emulator Semantic "
            "Commit Message rules (<type>[scope]: <Description>, <=50 char subject, "
            "mandatory explanatory body <=72 chars, Bug:, Test:, Change-Id:)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Full commit message text to validate.",
                }
            },
            "required": ["message"],
        },
    },
    {
        "name": "aemu_search_reusable_libs",
        "description": (
            "Search canonical //emulator/libs/* targets and headers in goldfish "
            "(file I/O, INI parsing, HTTP server/client, async event loop, system/env) "
            "to prevent re-implementing existing libraries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or capability (e.g. 'http', 'file', 'ini', 'env', 'async').",
                }
            },
        },
    },
    {
        "name": "aemu_get_harness_state",
        "description": (
            "Return active emu-dev-cli workflow sessions and the per-conversation "
            "AEMU Harness Session Ledger (launched AVDs, guardrail counters, recent events)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Optional Jetski conversation ID.",
                }
            },
        },
    },
    {
        "name": "aemu_diagnose_build_or_test_log",
        "description": (
            "Analyze a Bazel, CMake, TSAN, or ASAN failure log snippet and return "
            "Android Emulator remediation commands and //emulator/libs/* dependency hints."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "log_text": {
                    "type": "string",
                    "description": "Compiler or test failure log output.",
                }
            },
            "required": ["log_text"],
        },
    },
]


def search_reusable_libs(query: str = "") -> List[Dict[str, str]]:
    """Filters the `//emulator/libs/*` catalog by keyword query."""
    cleaned = (query or "").strip().lower()
    if not cleaned:
        return list(REUSABLE_LIBS_CATALOG)

    tokens = [t for t in cleaned.split() if t]
    matches: List[Dict[str, str]] = []
    for entry in REUSABLE_LIBS_CATALOG:
        haystack = " ".join(entry.values()).lower()
        if any(tok in haystack for tok in tokens):
            matches.append(entry)
    return matches or list(REUSABLE_LIBS_CATALOG)


def execute_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Dict[str, Any]:
    """Executes one named AEMU Harness MCP tool and returns its structured result."""
    if tool_name == "aemu_validate_commit_message":
        message = str(arguments.get("message") or "")
        verifier = _load_commit_verifier()
        errors = verifier.validate_commit_message(message)
        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    if tool_name == "aemu_search_reusable_libs":
        query = str(arguments.get("query") or "")
        matches = search_reusable_libs(query)
        return {
            "query": query,
            "count": len(matches),
            "libraries": matches,
        }

    if tool_name == "aemu_get_harness_state":
        conv_id = arguments.get("conversation_id")
        ledger_store = store or SessionLedgerStore()
        return {
            "ledger": ledger_store.load(conv_id),
            "active_workflows": discover_active_workflows(conversation_id=conv_id),
        }

    if tool_name == "aemu_diagnose_build_or_test_log":
        log_text = str(arguments.get("log_text") or "")
        diag = diagnose_command_failure("bazel test", log_text)
        return {
            "detected": diag is not None,
            "diagnostic": diag or "No known sanitizer or missing-header signature matched.",
        }

    raise ValueError(f"Unknown MCP tool: {tool_name}")


def handle_mcp_jsonrpc_request(
    request: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Optional[Dict[str, Any]]:
    """Processes a single JSON-RPC 2.0 MCP message and returns a response dict (or None for notifications)."""
    method = str(request.get("method") or "")
    req_id = request.get("id")

    if method.startswith("notifications/") or req_id is None:
        return None

    params = request.get("params") or {}

    if method == "initialize":
        client_version = params.get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "aemu-harness-mcp",
                    "version": "0.1.0",
                },
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS_SCHEMA},
        }

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            tool_out = execute_mcp_tool(tool_name, arguments, store=store)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_out, indent=2),
                        }
                    ],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


def run_stdio_mcp_server() -> None:
    """Runs the newline-delimited JSON-RPC 2.0 MCP server loop over stdin/stdout."""
    store = SessionLedgerStore()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line or line.lower().startswith("content-length:"):
            continue
        try:
            request = json.loads(line)
        except Exception:
            continue
        if not isinstance(request, dict):
            continue
        response = handle_mcp_jsonrpc_request(request, store=store)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
