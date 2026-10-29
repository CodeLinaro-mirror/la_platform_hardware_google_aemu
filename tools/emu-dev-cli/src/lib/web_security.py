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

"""Request checks shared by the local web UI servers (`workflow ui`, `gerrit ui`).

The servers only listen on loopback, but any web page open in the same browser can
still send them requests. So every API route needs the per-launch UI token, request
bodies must be size-capped JSON (a cross-site page cannot send JSON without a CORS
preflight), and CORS headers only go to loopback origins.
"""

from __future__ import annotations

import hmac
from http import HTTPStatus
import json
import secrets
from typing import Any, BinaryIO, Mapping
import urllib.parse

UI_TOKEN_HEADER = "X-Sidecar-Token"
UI_TOKEN_QUERY_PARAM = "token"
SIDECAR_TOKEN_ENV = "ANTIGRAVITY_SIDECAR_UI_TOKEN"
JSON_CONTENT_TYPE = "application/json"
MAX_JSON_BODY_BYTES = 10 * 1024 * 1024
_UI_TOKEN_BYTES = 32
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


class RequestRejected(Exception):
    """A request failed a security or format check; `status` is the HTTP status to send."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def mint_ui_token() -> str:
    """Returns a new random UI token for one server launch."""
    return secrets.token_urlsafe(_UI_TOKEN_BYTES)


def require_ui_token(
    expected_token: str, headers: Mapping[str, str], query: str, hint: str
) -> None:
    """Raises `RequestRejected` (401) unless the request carries `expected_token`.

    The token can come from the `X-Sidecar-Token` header or the `?token=` query
    parameter and is compared in constant time. An empty `expected_token` fails
    closed, so a server started without a token rejects every API request.
    `hint` tells the user how to open the UI with a valid token.
    """
    if not expected_token:
        raise RequestRejected(
            HTTPStatus.UNAUTHORIZED,
            f"This server has no UI token configured, so it rejects all API requests. {hint}",
        )
    expected = expected_token.encode("utf-8")
    candidates = [headers.get(UI_TOKEN_HEADER) or ""]
    candidates += urllib.parse.parse_qs(query).get(UI_TOKEN_QUERY_PARAM, [])
    if not any(
        candidate and hmac.compare_digest(candidate.encode("utf-8"), expected)
        for candidate in candidates
    ):
        raise RequestRejected(HTTPStatus.UNAUTHORIZED, f"Missing or invalid UI token. {hint}")


def allowed_cors_origin(origin: str | None) -> str | None:
    """Returns the normalized `origin` if it is an http(s) loopback origin, else None."""
    if not origin:
        return None
    try:
        parsed = urllib.parse.urlsplit(origin)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or hostname not in _LOOPBACK_HOSTNAMES:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def read_json_body(
    headers: Mapping[str, str], rfile: BinaryIO, max_bytes: int = MAX_JSON_BODY_BYTES
) -> dict[str, Any]:
    """Reads and parses a JSON object request body.

    Raises:
        RequestRejected: 415 if the Content-Type is not application/json, 411 or 400
            if Content-Length is missing or malformed, 413 if the body is larger than
            `max_bytes`, and 400 if the body is not a UTF-8 JSON object.
    """
    content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != JSON_CONTENT_TYPE:
        raise RequestRejected(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported Content-Type '{content_type or 'none'}'. "
            f"Send the request body as '{JSON_CONTENT_TYPE}'.",
        )
    raw_length = headers.get("Content-Length")
    if raw_length is None:
        raise RequestRejected(
            HTTPStatus.LENGTH_REQUIRED,
            "Missing Content-Length header. Send the JSON body with an explicit length.",
        )
    try:
        length = int(raw_length)
    except ValueError:
        length = -1
    if length < 0:
        raise RequestRejected(
            HTTPStatus.BAD_REQUEST,
            f"Invalid Content-Length '{raw_length}'; expected a non-negative integer.",
        )
    if length > max_bytes:
        raise RequestRejected(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"Request body is {length} bytes; the limit is {max_bytes} bytes.",
        )
    raw_body = rfile.read(length)
    if not raw_body.strip():
        raise RequestRejected(
            HTTPStatus.BAD_REQUEST, "Request body is empty; send a JSON object such as {}."
        )
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RequestRejected(
            HTTPStatus.BAD_REQUEST, f"Request body is not valid UTF-8 JSON: {exc}."
        ) from exc
    if not isinstance(body, dict):
        raise RequestRejected(
            HTTPStatus.BAD_REQUEST,
            f"Request body must be a JSON object, got {type(body).__name__}.",
        )
    return body
