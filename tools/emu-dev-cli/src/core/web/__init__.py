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

"""Shared stdlib-only Web UI and Jetski sidecar scaffolding (`core.web`)."""

from __future__ import annotations

from core.web.agent_proxy import (
    AgentApiInvoker,
    AgentProxy,
    ENV_CONVERSATION_ID,
    ENV_SIDECAR_CONVERSATION_ID,
    SIDECAR_METADATA_ROUTE,
    SIDECAR_NEW_CONVERSATION_ROUTE,
    SIDECAR_SEND_MESSAGE_ROUTE,
    resolve_active_conversation_id,
)
from core.web.assets import (
    ASSET_VERSION_HEX_LENGTH,
    DEFAULT_PREVIEW_MAX_BYTES,
    SIDECAR_PRELOAD_JS,
    SIDECAR_PRELOAD_SCRIPT_TAG,
    StaticAssetResolver,
    compute_asset_version,
    read_bounded_utf8_preview,
    render_html_shell,
)
from core.web.handler import (
    CONTENT_TYPE_HTML,
    CONTENT_TYPE_JS,
    CONTENT_TYPE_JSON,
    JsonRequestHandler,
)
from core.web.router import (
    RouteHandler,
    Router,
    TRUTHY_QUERY_VALUES,
    WebRequest,
    is_truthy_query_flag,
)
from core.web.security import (
    DEFAULT_UI_TOKEN_HINT,
    JSON_CONTENT_TYPE,
    MAX_JSON_BODY_BYTES,
    RequestRejected,
    SIDECAR_TOKEN_ENV,
    SecurityPolicy,
    UI_TOKEN_HEADER,
    UI_TOKEN_QUERY_PARAM,
    allowed_cors_origin,
    mint_ui_token,
    read_json_body,
    require_ui_token,
    resolve_within_roots,
)
from core.web.server import (
    DEFAULT_BROWSER_DELAY_SEC,
    DEFAULT_MAX_PORT_PROBES,
    HotReloadHandlerDispatcher,
    find_available_port,
    serve_http,
)

__all__ = [
    "ASSET_VERSION_HEX_LENGTH",
    "AgentApiInvoker",
    "AgentProxy",
    "CONTENT_TYPE_HTML",
    "CONTENT_TYPE_JS",
    "CONTENT_TYPE_JSON",
    "DEFAULT_BROWSER_DELAY_SEC",
    "DEFAULT_MAX_PORT_PROBES",
    "DEFAULT_PREVIEW_MAX_BYTES",
    "DEFAULT_UI_TOKEN_HINT",
    "ENV_CONVERSATION_ID",
    "ENV_SIDECAR_CONVERSATION_ID",
    "HotReloadHandlerDispatcher",
    "JSON_CONTENT_TYPE",
    "JsonRequestHandler",
    "MAX_JSON_BODY_BYTES",
    "RequestRejected",
    "RouteHandler",
    "Router",
    "SIDECAR_METADATA_ROUTE",
    "SIDECAR_NEW_CONVERSATION_ROUTE",
    "SIDECAR_PRELOAD_JS",
    "SIDECAR_PRELOAD_SCRIPT_TAG",
    "SIDECAR_SEND_MESSAGE_ROUTE",
    "SIDECAR_TOKEN_ENV",
    "SecurityPolicy",
    "StaticAssetResolver",
    "TRUTHY_QUERY_VALUES",
    "UI_TOKEN_HEADER",
    "UI_TOKEN_QUERY_PARAM",
    "WebRequest",
    "allowed_cors_origin",
    "compute_asset_version",
    "find_available_port",
    "is_truthy_query_flag",
    "mint_ui_token",
    "read_bounded_utf8_preview",
    "read_json_body",
    "render_html_shell",
    "require_ui_token",
    "resolve_active_conversation_id",
    "resolve_within_roots",
    "serve_http",
]
