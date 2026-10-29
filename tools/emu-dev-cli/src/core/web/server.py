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

"""Port discovery, HTTP server lifecycle management, and hot-reload sidecar dispatch."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import http.server
import importlib
import logging
from pathlib import Path
import socket
import sys
import threading
from typing import Any
import webbrowser

from core.web.security import mint_ui_token

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_PORT_PROBES: int = 50
DEFAULT_BROWSER_DELAY_SEC: float = 0.0


def find_available_port(
    host: str = "127.0.0.1",
    start_port: int = 8080,
    max_attempts: int = DEFAULT_MAX_PORT_PROBES,
    *,
    fallback_to_start: bool = False,
    server_label: str = "Web UI server",
) -> int:
    """Finds the first available TCP port on `host` starting at `start_port`."""
    for offset in range(max_attempts):
        candidate = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError as exc:
                LOGGER.debug("Port %d on %s unavailable: %s", candidate, host, exc)
                continue
    if fallback_to_start:
        return start_port
    end_port = start_port + max_attempts - 1
    raise RuntimeError(
        f"Could not bind {server_label} on '{host}' across ports {start_port}..{end_port}. "
        f"Pass a different --port or stop existing processes listening on {start_port}..{end_port}."
    )


def _open_browser_url(
    url: str,
    opener: Callable[[str], Any],
    delay_sec: float = DEFAULT_BROWSER_DELAY_SEC,
) -> None:
    """Opens `url` immediately or after `delay_sec`, ignoring headless browser errors."""
    def _launch() -> None:
        try:
            opener(url)
        except Exception as exc:
            LOGGER.debug("Ignored browser launch error for %s: %s", url, exc)

    if delay_sec > 0.0:
        threading.Timer(delay_sec, _launch).start()
    else:
        _launch()


def serve_http(
    handler_or_factory: type[http.server.BaseHTTPRequestHandler]
    | Callable[[str], type[http.server.BaseHTTPRequestHandler]],
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    ui_token: str | None = None,
    open_browser: bool = True,
    browser_delay_sec: float = DEFAULT_BROWSER_DELAY_SEC,
    block: bool = True,
    banner_fn: Callable[[str, int], str] | None = None,
    shutdown_message: str = "\nServer stopped.",
    port_finder: Callable[[str, int], int] | None = None,
    server_cls: type[http.server.HTTPServer] = http.server.ThreadingHTTPServer,
    browser_opener: Callable[[str], Any] = webbrowser.open,
) -> tuple[http.server.HTTPServer, int]:
    """Starts a local ThreadingHTTPServer with a minted UI token and optional browser launch."""
    effective_port_finder = port_finder or find_available_port
    bound_port = effective_port_finder(host, port)
    effective_token = ui_token or mint_ui_token()

    if isinstance(handler_or_factory, type) and issubclass(
        handler_or_factory, http.server.BaseHTTPRequestHandler
    ):
        handler_cls = handler_or_factory
    else:
        handler_cls = handler_or_factory(effective_token)

    httpd = server_cls((host, bound_port), handler_cls)
    httpd.ui_token = effective_token  # type: ignore[attr-defined]

    url = f"http://{host}:{bound_port}/?token={effective_token}"
    if banner_fn is not None:
        print(banner_fn(url, bound_port))

    if open_browser:
        _open_browser_url(url, browser_opener, delay_sec=browser_delay_sec)

    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            if shutdown_message:
                print(shutdown_message)
        finally:
            httpd.server_close()

    return httpd, bound_port


class HotReloadHandlerDispatcher:
    """Thread-safe request dispatcher that reloads watched modules when file mtimes change."""

    def __init__(
        self,
        *,
        module_names: Sequence[str],
        handler_builder: Callable[[], type[http.server.BaseHTTPRequestHandler]],
        extra_paths_fn: Callable[[], Sequence[Path]] | None = None,
    ) -> None:
        self._module_names = tuple(module_names)
        self._handler_builder = handler_builder
        self._extra_paths_fn = extra_paths_fn
        self._lock = threading.Lock()
        self._mtime_snapshot = self._compute_mtime_snapshot()
        self._handler_cls = self._handler_builder()

    def _compute_mtime_snapshot(self) -> tuple[float, ...]:
        mtimes: list[float] = []
        for mod_name in self._module_names:
            mod = sys.modules.get(mod_name)
            mod_file = getattr(mod, "__file__", None) if mod is not None else None
            path = Path(mod_file) if mod_file else None
            mtimes.append(path.stat().st_mtime if path and path.is_file() else 0.0)
        if self._extra_paths_fn is not None:
            for extra_path in self._extra_paths_fn():
                mtimes.append(extra_path.stat().st_mtime if extra_path.is_file() else 0.0)
        return tuple(mtimes)

    def _reload_if_changed(self) -> None:
        if self._compute_mtime_snapshot() == self._mtime_snapshot:
            return
        with self._lock:
            current = self._compute_mtime_snapshot()
            if current == self._mtime_snapshot:
                return
            for mod_name in self._module_names:
                mod = sys.modules.get(mod_name)
                if mod is not None:
                    importlib.reload(mod)
            self._mtime_snapshot = self._compute_mtime_snapshot()
            self._handler_cls = self._handler_builder()

    def __call__(self, request: Any, client_address: Any, server_obj: Any) -> Any:
        try:
            self._reload_if_changed()
        except Exception as exc:
            LOGGER.debug("Hot reload failed, keeping previous handler: %s", exc)
        handler_cls = self._handler_cls
        return handler_cls(request, client_address, server_obj)  # type: ignore[misc]


__all__ = [
    "DEFAULT_BROWSER_DELAY_SEC",
    "DEFAULT_MAX_PORT_PROBES",
    "HotReloadHandlerDispatcher",
    "find_available_port",
    "serve_http",
]
