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

"""Configurable security policy and request checks for local Web UI and Jetski sidecar servers."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hmac
from http import HTTPStatus
import json
from pathlib import Path
import secrets
from typing import Any, BinaryIO
import urllib.parse

UI_TOKEN_HEADER = "X-Sidecar-Token"
UI_TOKEN_QUERY_PARAM = "token"
SIDECAR_TOKEN_ENV = "ANTIGRAVITY_SIDECAR_UI_TOKEN"
JSON_CONTENT_TYPE = "application/json"
MAX_JSON_BODY_BYTES = 10 * 1024 * 1024
_UI_TOKEN_BYTES = 32
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

DEFAULT_UI_TOKEN_HINT = (
    "Open the Web UI through its launch URL (ending with ?token=...) or from "
    "the Jetski side panel, which supplies ANTIGRAVITY_SIDECAR_UI_TOKEN."
)


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


def resolve_within_roots(
    path: str | Path, roots: Iterable[str | Path]
) -> Path | None:
    """Resolves `path` (expanding `~`, `..` and symlinks) and returns it if it is inside a root.

    Returns None when the resolved path is outside every root in `roots`, which blocks
    `../` traversal and symlinks that point outside the allowed directories.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for root in roots:
        try:
            if resolved.is_relative_to(Path(root).expanduser().resolve()):
                return resolved
        except (OSError, RuntimeError, ValueError):
            continue
    return None


@dataclass(frozen=True)
class SecurityPolicy:
    """Declarative HTTP security policy enforcing fail-closed token auth, CORS, and JSON caps."""

    expected_token: str = ""
    token_hint: str = DEFAULT_UI_TOKEN_HINT
    public_get_routes: frozenset[str] = frozenset()
    protected_get_prefixes: tuple[str, ...] = ()
    max_body_bytes: int = MAX_JSON_BODY_BYTES
    enable_cors: bool = True

    def requires_get_auth(self, path: str) -> bool:
        """Returns True when a GET request to `path` must carry the UI token."""
        if path in self.public_get_routes:
            return False
        if self.protected_get_prefixes:
            return any(path.startswith(prefix) for prefix in self.protected_get_prefixes)
        return True

    def verify_token(
        self,
        headers: Mapping[str, str],
        query: str,
        *,
        token_override: str | None = None,
    ) -> None:
        """Verifies the UI token in `headers` or `query`, failing closed when unset."""
        effective_token = self.expected_token if token_override is None else token_override
        require_ui_token(effective_token, headers, query, self.token_hint)

    def verify_get(
        self,
        path: str,
        headers: Mapping[str, str],
        query: str,
        *,
        token_override: str | None = None,
    ) -> None:
        """Enforces token authentication on `path` if required by `requires_get_auth`."""
        if self.requires_get_auth(path):
            self.verify_token(headers, query, token_override=token_override)

    def verify_post(
        self,
        headers: Mapping[str, str],
        query: str,
        rfile: BinaryIO,
        *,
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """Enforces token authentication and parses a size-capped UTF-8 JSON object body."""
        self.verify_token(headers, query, token_override=token_override)
        return read_json_body(headers, rfile, max_bytes=self.max_body_bytes)

    def cors_origin(self, headers: Mapping[str, str]) -> str | None:
        """Returns the normalized loopback `Origin` header if CORS is enabled, else None."""
        if not self.enable_cors:
            return None
        return allowed_cors_origin(headers.get("Origin"))

    def resolve_safe_path(
        self,
        path: str | Path,
        roots: Iterable[str | Path],
    ) -> Path | None:
        """Resolves `path` and returns it only if confined within one of `roots`."""
        return resolve_within_roots(path, roots)


__all__ = [
    "DEFAULT_UI_TOKEN_HINT",
    "JSON_CONTENT_TYPE",
    "MAX_JSON_BODY_BYTES",
    "RequestRejected",
    "SIDECAR_TOKEN_ENV",
    "SecurityPolicy",
    "UI_TOKEN_HEADER",
    "UI_TOKEN_QUERY_PARAM",
    "_LOOPBACK_HOSTNAMES",
    "allowed_cors_origin",
    "mint_ui_token",
    "read_json_body",
    "require_ui_token",
    "resolve_within_roots",
]
