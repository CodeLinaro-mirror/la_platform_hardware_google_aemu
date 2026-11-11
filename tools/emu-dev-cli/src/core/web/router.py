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

"""Typed HTTP request wrapper and declarative URL router for local Web UI servers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
import urllib.parse

TRUTHY_QUERY_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})


def is_truthy_query_flag(qs: Mapping[str, list[str]], key: str) -> bool:
    """Returns True when query parameter `key` is `'1'`, `'true'`, or `'yes'`."""
    values = qs.get(key)
    if not values:
        return False
    return str(values[0]).strip().lower() in TRUTHY_QUERY_VALUES


@dataclass(frozen=True)
class WebRequest:
    """Normalized HTTP request carrying query parameters, headers, and parsed JSON body."""

    method: str
    path: str
    raw_query: str = ""
    query_params: Mapping[str, list[str]] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_get(
        cls,
        path: str,
        raw_query: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> WebRequest:
        """Constructs a `WebRequest` for an HTTP GET request."""
        return cls(
            method="GET",
            path=path,
            raw_query=raw_query,
            query_params=urllib.parse.parse_qs(raw_query),
            headers=headers or {},
            body={},
        )

    @classmethod
    def from_post(
        cls,
        path: str,
        raw_query: str = "",
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> WebRequest:
        """Constructs a `WebRequest` for an HTTP POST request."""
        return cls(
            method="POST",
            path=path,
            raw_query=raw_query,
            query_params=urllib.parse.parse_qs(raw_query),
            headers=headers or {},
            body=body or {},
        )

    def query_str(self, key: str, default: str = "", *, strip: bool = False) -> str:
        """Returns the first value of query parameter `key`, or `default` when absent."""
        values = self.query_params.get(key)
        raw = str(values[0]) if values else default
        return raw.strip() if strip else raw

    def query_opt_str(self, key: str) -> str | None:
        """Returns the stripped first value of query parameter `key`, or None when empty."""
        val = self.query_str(key, "", strip=True)
        return val if val else None

    def query_bool(self, *keys: str) -> bool:
        """Returns True if any of `keys` in `query_params` has a truthy flag value."""
        return any(is_truthy_query_flag(self.query_params, k) for k in keys)

    query_flag = query_bool

    def query_int(
        self,
        key: str,
        default: int | None = None,
        *,
        digits_only: bool = False,
        min_val: int | None = None,
        max_val: int | None = None,
    ) -> int | None:
        """Parses query parameter `key` as an integer, with optional digit and range bounds."""
        raw = self.query_str(key, "", strip=True)
        if not raw:
            return default
        if digits_only and not raw.isdigit():
            return default
        try:
            parsed = int(raw)
        except ValueError:
            return default
        if min_val is not None:
            parsed = max(min_val, parsed)
        if max_val is not None:
            parsed = min(max_val, parsed)
        return parsed

    def query_csv(self, key: str) -> list[str]:
        """Splits a comma-separated query parameter `key` into non-empty stripped strings."""
        raw = self.query_str(key, "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def body_str(self, *keys: str, default: str = "", strip: bool = False) -> str:
        """Returns the first non-empty body field from `keys` as `str`, or `default`."""
        for key in keys:
            val = self.body.get(key)
            if val is not None and str(val) != "":
                text = str(val)
                return text.strip() if strip else text
        return default

    def body_opt_str(self, *keys: str) -> str | None:
        """Returns the first stripped non-empty body field from `keys`, or None."""
        val = self.body_str(*keys, default="", strip=True)
        return val if val else None

    def body_int(
        self,
        *keys: str,
        default: int | None = None,
        digits_only: bool = False,
    ) -> int | None:
        """Returns the first present body field from `keys` converted to `int`, or `default`."""
        for key in keys:
            val = self.body.get(key)
            if val is None or val == "":
                continue
            if digits_only and not str(val).isdigit():
                return default
            try:
                return int(val)
            except (TypeError, ValueError):
                return default
        return default

    def body_bool(self, *keys: str, default: bool = False) -> bool:
        """Returns the boolean value of the first truthy or explicit key in `keys`."""
        found_any = False
        for key in keys:
            if key in self.body:
                found_any = True
                if bool(self.body.get(key)):
                    return True
        return False if found_any else default

    def body_list(
        self,
        key: str,
        *,
        fallback_single_key: str | None = None,
    ) -> list[Any]:
        """Returns `self.body[key]` if it is a list, or wraps `fallback_single_key` if set."""
        raw = self.body.get(key)
        if isinstance(raw, list):
            return raw
        if fallback_single_key is not None:
            single = self.body.get(fallback_single_key)
            if single is not None and str(single).strip():
                return [single]
        return []


RouteHandler = Callable[[Any, WebRequest], None]


class Router:
    """Declarative table mapping HTTP GET and POST paths to handler methods or functions."""

    def __init__(self) -> None:
        self._get_routes: dict[str, RouteHandler] = {}
        self._post_routes: dict[str, RouteHandler] = {}

    def add_get(self, path_or_paths: str | Iterable[str], handler: RouteHandler) -> Router:
        """Registers `handler` for one or more HTTP GET paths."""
        paths = (path_or_paths,) if isinstance(path_or_paths, str) else tuple(path_or_paths)
        for path in paths:
            self._get_routes[path] = handler
        return self

    def add_post(self, path_or_paths: str | Iterable[str], handler: RouteHandler) -> Router:
        """Registers `handler` for one or more HTTP POST paths."""
        paths = (path_or_paths,) if isinstance(path_or_paths, str) else tuple(path_or_paths)
        for path in paths:
            self._post_routes[path] = handler
        return self

    def resolve_get(self, path: str) -> RouteHandler | None:
        """Returns the registered GET route handler for `path`, or None."""
        return self._get_routes.get(path)

    def resolve_post(self, path: str) -> RouteHandler | None:
        """Returns the registered POST route handler for `path`, or None."""
        return self._post_routes.get(path)

    def dispatch_get(self, handler_instance: Any, request: WebRequest) -> bool:
        """Dispatches `request` to the matching GET route; returns True if matched."""
        route_fn = self._get_routes.get(request.path)
        if route_fn is None:
            return False
        route_fn(handler_instance, request)
        return True

    def dispatch_post(self, handler_instance: Any, request: WebRequest) -> bool:
        """Dispatches `request` to the matching POST route; returns True if matched."""
        route_fn = self._post_routes.get(request.path)
        if route_fn is None:
            return False
        route_fn(handler_instance, request)
        return True


__all__ = [
    "RouteHandler",
    "Router",
    "TRUTHY_QUERY_VALUES",
    "WebRequest",
    "is_truthy_query_flag",
]
