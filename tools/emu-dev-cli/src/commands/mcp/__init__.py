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

"""Model Context Protocol (MCP) server that exposes emu-dev-cli commands as agent tools."""

from commands.mcp.cmd import McpCommand, McpServeCommand, protocol_stdio
from commands.mcp.server import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    ErrorCode,
    McpServer,
    ToolCall,
    ToolCancelled,
)
from commands.mcp.tools import (
    ToolResult,
    ToolSpec,
    build_tool_catalog,
    default_tools,
    run_tool,
)

__all__ = [
    "ErrorCode",
    "LATEST_PROTOCOL_VERSION",
    "McpCommand",
    "McpServeCommand",
    "McpServer",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "ToolCall",
    "ToolCancelled",
    "ToolResult",
    "ToolSpec",
    "build_tool_catalog",
    "default_tools",
    "protocol_stdio",
    "run_tool",
]
