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

"""Canonical goldfish `//emulator/libs/*` targets recommended by the AEMU Harness.

Single source of truth for the include-path to Bazel target table and the
reusable-library catalog built from it. The PostToolUse missing-header diagnostics,
the `aemu_search_reusable_libs` MCP tool, the duplication-auditor review prompt, and
the AuxPane sidecar (via the `libs_catalog.json` that `emu-dev-cli harness install`
renders) all derive their labels from here; `goldfish_libs_test` checks that the
plugin rules recommend only these labels and headers.
"""

from typing import Dict, List, Optional, Tuple

# Include-path prefixes mapped to the goldfish library that exports them. Ordered from
# most to least specific: the first matching prefix wins.
GOLDFISH_HEADER_TO_TARGET: Tuple[Tuple[str, str], ...] = (
    ("goldfish/async/testing/", "//emulator/libs/async:async_testing"),
    ("goldfish/async/libuv_event_loop.h", "//emulator/libs/async:libuv_event_loop"),
    ("goldfish/async/threaded_event_loop.h", "//emulator/libs/async:threaded_event_loop"),
    ("goldfish/async/qemu_event_loop.h", "//emulator/libs/async:qemu_event_loop"),
    ("goldfish/async/libuv_socket_factory.h", "//emulator/libs/async:libuv_sockets"),
    ("goldfish/async/libuv_process_launcher.h", "//emulator/libs/async:libuv_process_launcher"),
    ("goldfish/async/libuv_signal_handlers.h", "//emulator/libs/async:libuv_signal_handlers"),
    ("goldfish/async/launch_config.h", "//emulator/libs/async:launch_config"),
    ("goldfish/async/uv_to_absl.h", "//emulator/libs/async:uv_to_absl"),
    ("goldfish/async/errno_to_absl.h", "//emulator/libs/async:errno_to_absl"),
    ("goldfish/async/", "//emulator/libs/async:async_api"),
    ("goldfish/http/web_server.h", "//emulator/libs/http:web_server"),
    ("goldfish/http/", "//emulator/libs/http:http_server"),
    ("goldfish/network/http_client.h", "//emulator/libs/network:http_client"),
    (
        "goldfish/network/generic_netlink_message.h",
        "//emulator/libs/network:generic_netlink_message",
    ),
    ("goldfish/network/io_vector.h", "//emulator/libs/network:generic_netlink_message"),
    ("goldfish/network/iovec.h", "//emulator/libs/network:generic_netlink_message"),
    ("goldfish/network/", "//emulator/libs/network"),
    ("goldfish/file/", "//emulator/libs/file"),
    ("android/goldfish/ini_file.h", "//emulator/libs/ini_file"),
    ("android/base/testing/test_system.h", "//emulator/libs/system:test-headers"),
    ("android/base/system.h", "//emulator/libs/system"),
)


def target_for_include(include_path: str) -> Optional[str]:
    """Returns the goldfish Bazel target that exports `include_path`, or None.

    `include_path` is the path as written in an `#include`, for example
    `goldfish/http/web_server.h`.
    """
    return next(
        (target for prefix, target in GOLDFISH_HEADER_TO_TARGET if include_path.startswith(prefix)),
        None,
    )


def _catalog_entry(
    name: str,
    header: str,
    namespace: str,
    keywords: str,
    summary: str,
) -> Dict[str, str]:
    """Builds a reusable-library catalog entry whose `bazel_target` is derived from `header`."""
    bazel_target = target_for_include(header)
    if bazel_target is None:
        raise ValueError(
            f"Reusable-library catalog entry '{name}' uses header '{header}', which no target in "
            "GOLDFISH_HEADER_TO_TARGET exports. Add the header's target to that table or fix "
            "the header path."
        )
    return {
        "name": name,
        "bazel_target": bazel_target,
        "header": header,
        "namespace": namespace,
        "keywords": keywords,
        "summary": summary,
    }


# One entry per library target. Each `bazel_target` comes from the header, so a label
# fixed in GOLDFISH_HEADER_TO_TARGET is fixed in the catalog too.
REUSABLE_LIBS_CATALOG: List[Dict[str, str]] = [
    _catalog_entry(
        name="file",
        header="goldfish/file/file.h",
        namespace="android::base::file",
        keywords="file io utf8 unicode path read write fopen ifstream ofstream windows",
        summary="Cross-platform UTF-8 file I/O helpers (ReadFile, WriteFile, path utilities) safe on Windows non-ASCII paths.",
    ),
    _catalog_entry(
        name="ini_file",
        header="android/goldfish/ini_file.h",
        namespace="android::goldfish::IniFile",
        keywords="ini config parser avd config.ini key value",
        summary="Typed INI configuration file parser and serializer for AVD and hardware profiles.",
    ),
    _catalog_entry(
        name="http",
        header="goldfish/http/web_server.h",
        namespace="goldfish::http::WebServer",
        keywords="http server web rest endpoint test fixture mock server",
        summary="Embedded HTTP WebServer (`CreateWebServer`) for production endpoints and hermetic C++ unit tests.",
    ),
    _catalog_entry(
        name="http_client",
        header="goldfish/network/http_client.h",
        namespace="goldfish::network::HttpClient",
        keywords="http client curl request get post download network",
        summary="High-level HTTP/HTTPS client with StatusOr error propagation.",
    ),
    _catalog_entry(
        name="network",
        header="goldfish/network/dns_resolver.h",
        namespace="goldfish::network::ResolveEndpoints",
        keywords="dns resolve endpoints socket address ipv4 ipv6 network",
        summary="Endpoint resolution (`ResolveEndpoints`) and network address utilities.",
    ),
    _catalog_entry(
        name="async",
        header="goldfish/async/async_socket.h",
        namespace="goldfish::async",
        keywords="async event loop socket asyncsocket callback interface",
        summary="EventLoop and AsyncSocket interfaces shared by all goldfish event loops.",
    ),
    _catalog_entry(
        name="threaded_event_loop",
        header="goldfish/async/threaded_event_loop.h",
        namespace="goldfish::async::ThreadedEventLoop",
        keywords="async event loop thread background worker",
        summary="ThreadedEventLoop: runs an EventLoop implementation (e.g. LibuvEventLoop) on a dedicated thread.",
    ),
    _catalog_entry(
        name="libuv_event_loop",
        header="goldfish/async/libuv_event_loop.h",
        namespace="goldfish::async::LibuvEventLoop",
        keywords="async libuv event loop uv_loop_t timer callback",
        summary="LibuvEventLoop: the libuv-based EventLoop implementation.",
    ),
    _catalog_entry(
        name="uv_to_absl",
        header="goldfish/async/uv_to_absl.h",
        namespace="goldfish::async::UvErrToAbslStatus",
        keywords="async libuv uv error errno status absl",
        summary="UvErrToAbslStatus: converts libuv error codes to absl::Status.",
    ),
    _catalog_entry(
        name="system",
        header="android/base/system.h",
        namespace="android::base::System",
        keywords="env getenv environment variable system",
        summary="System environment queries (`System::Get()->EnvGet(...)`).",
    ),
    _catalog_entry(
        name="test_system",
        header="android/base/testing/test_system.h",
        namespace="android::base::TestSystem",
        keywords="env environment variable system test_system fake test",
        summary="TestSystem fake that overrides environment and system queries in unit tests.",
    ),
]
