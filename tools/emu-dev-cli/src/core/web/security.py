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

"""Configurable security policy for local Web UI and Jetski sidecar servers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from lib.security import resolve_within_roots
from lib.web_security import (
    JSON_CONTENT_TYPE,
    MAX_JSON_BODY_BYTES,
    RequestRejected,
    SIDECAR_TOKEN_ENV,
    UI_TOKEN_HEADER,
    UI_TOKEN_QUERY_PARAM,
    allowed_cors_origin,
    mint_ui_token,
    read_json_body,
    require_ui_token,
)

DEFAULT_UI_TOKEN_HINT = (
    "Open the Web UI through its launch URL (ending with ?token=...) or from "
    "the Jetski side panel, which supplies ANTIGRAVITY_SIDECAR_UI_TOKEN."
)


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
    "allowed_cors_origin",
    "mint_ui_token",
    "read_json_body",
    "require_ui_token",
    "resolve_within_roots",
]
