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

"""Base HTTP request handler providing JSON serialization, CORS, token checks, and routing."""

from __future__ import annotations

from http import HTTPStatus
import http.server
import json
import logging
from typing import Any
import urllib.parse

from core.web.router import Router, WebRequest
from core.web.security import (
    RequestRejected,
    SecurityPolicy,
    UI_TOKEN_HEADER,
    read_json_body,
)

LOGGER = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"
CONTENT_TYPE_JS = "application/javascript; charset=utf-8"


class JsonRequestHandler(http.server.BaseHTTPRequestHandler):
    """Shared BaseHTTPRequestHandler with fail-closed security, CORS, JSON helpers, and Router."""

    security_policy: SecurityPolicy = SecurityPolicy()
    router: Router | None = None
    ui_token: str | None = None
    json_indent: int | None = None
    default_cache_control: str | None = "no-store"
    unknown_route_as_json: bool = True

    def log_message(self, fmt: str, *args: Any) -> None:
        """Suppresses noisy request-polling logs on CLI stdout/stderr."""
        return

    def log_error(self, fmt: str, *args: Any) -> None:
        """Logs HTTP server errors at debug level rather than discarding them."""
        LOGGER.debug(fmt, *args)

    def _resolve_expected_token(self) -> str:
        """Returns the configured UI token from the handler class or `self.server.ui_token`."""
        if self.ui_token is not None:
            return str(self.ui_token)
        server_token = getattr(self.server, "ui_token", None)
        if server_token is not None:
            return str(server_token)
        return self.security_policy.expected_token

    def _send_cors_headers(self) -> None:
        """Appends loopback CORS headers when allowed by `security_policy`."""
        origin = self.security_policy.cors_origin(self.headers)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _require_ui_token(self, query: str) -> None:
        """Verifies the request token against `_resolve_expected_token()`."""
        self.security_policy.verify_token(
            self.headers,
            query,
            token_override=self._resolve_expected_token(),
        )

    def _require_token(self, parsed: urllib.parse.ParseResult) -> None:
        """Verifies the request token from `parsed.query` and `self.headers`."""
        self._require_ui_token(parsed.query)

    def send_raw_bytes(
        self,
        content: bytes,
        content_type: str,
        status: int = int(HTTPStatus.OK),
        *,
        cache_control: str | None = "default",
    ) -> None:
        """Writes `content` with `Referrer-Policy: no-referrer` and BrokenPipe protection."""
        effective_cache = (
            self.default_cache_control if cache_control == "default" else cache_control
        )
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Referrer-Policy", "no-referrer")
            if effective_cache:
                self.send_header("Cache-Control", effective_cache)
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    _send_raw_bytes = send_raw_bytes

    def send_json(self, payload: Any, status: int = int(HTTPStatus.OK)) -> None:
        """Serializes `payload` as UTF-8 JSON and sends it with `status`."""
        body = json.dumps(payload, indent=self.json_indent).encode("utf-8")
        self.send_raw_bytes(body, CONTENT_TYPE_JSON, status=status)

    _send_json = send_json

    def send_html(self, html: str | bytes, status: int = int(HTTPStatus.OK)) -> None:
        """Sends an HTML response body encoded as UTF-8."""
        raw = html.encode("utf-8") if isinstance(html, str) else html
        self.send_raw_bytes(raw, CONTENT_TYPE_HTML, status=status)

    def send_javascript(self, script: str | bytes, status: int = int(HTTPStatus.OK)) -> None:
        """Sends a JavaScript response body encoded as UTF-8."""
        raw = script.encode("utf-8") if isinstance(script, str) else script
        self.send_raw_bytes(raw, CONTENT_TYPE_JS, status=status)

    def read_json_body(self) -> dict[str, Any]:
        """Reads and validates a UTF-8 JSON object request body."""
        return read_json_body(
            self.headers,
            self.rfile,
            max_bytes=self.security_policy.max_body_bytes,
        )

    _read_json_body = read_json_body

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight requests for loopback origins."""
        if not self.security_policy.cors_origin(self.headers):
            self.send_response(int(HTTPStatus.FORBIDDEN))
            self.end_headers()
            return
        self.send_response(int(HTTPStatus.NO_CONTENT))
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {UI_TOKEN_HEADER}")
        self.end_headers()

    def _send_unknown_route(self, method: str, path: str) -> None:
        """Sends a 404 response for an unmatched route."""
        if self.unknown_route_as_json:
            label = f"Unknown POST endpoint '{path}'" if method == "POST" else f"Unknown endpoint '{path}'"
            self.send_json({"error": label}, status=int(HTTPStatus.NOT_FOUND))
            return
        self.send_error(int(HTTPStatus.NOT_FOUND), "Endpoint not found")

    def dispatch_get(self, request: WebRequest) -> bool:
        """Dispatches `request` via `self.router` when configured."""
        if self.router is None:
            return False
        return self.router.dispatch_get(self, request)

    def dispatch_post(self, request: WebRequest) -> bool:
        """Dispatches `request` via `self.router` when configured."""
        if self.router is None:
            return False
        return self.router.dispatch_post(self, request)

    def do_GET(self) -> None:
        """Authenticates and dispatches an HTTP GET request."""
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.security_policy.verify_get(
                parsed.path,
                self.headers,
                parsed.query,
                token_override=self._resolve_expected_token(),
            )
            req = WebRequest.from_get(parsed.path, parsed.query, self.headers)
            if not self.dispatch_get(req):
                self._send_unknown_route("GET", parsed.path)
        except RequestRejected as exc:
            self.send_json({"error": str(exc)}, status=int(exc.status))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=int(HTTPStatus.BAD_REQUEST))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=int(HTTPStatus.INTERNAL_SERVER_ERROR))

    def do_POST(self) -> None:
        """Authenticates, parses the JSON body, and dispatches an HTTP POST request."""
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self.security_policy.verify_post(
                self.headers,
                parsed.query,
                self.rfile,
                token_override=self._resolve_expected_token(),
            )
            req = WebRequest.from_post(parsed.path, parsed.query, self.headers, body)
            if not self.dispatch_post(req):
                self._send_unknown_route("POST", parsed.path)
        except RequestRejected as exc:
            self.send_json({"error": str(exc)}, status=int(exc.status))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=int(HTTPStatus.BAD_REQUEST))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=int(HTTPStatus.INTERNAL_SERVER_ERROR))


__all__ = [
    "CONTENT_TYPE_HTML",
    "CONTENT_TYPE_JS",
    "CONTENT_TYPE_JSON",
    "JsonRequestHandler",
]
