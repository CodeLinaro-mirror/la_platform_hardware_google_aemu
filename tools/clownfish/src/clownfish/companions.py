# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Companion process discovery and lifecycle manager for Clownfish.

Locates and manages the native C++ companion binaries when running Clownfish
through Bazel:
- `videobridge` (`@goldfish//emulator/videobridge:videobridge`): WebRTC JSEP
  signaling, H.264/VP8 media encoding over `file://` MMAP shared memory, and
  SCTP DataChannel (`input`, `input_v2`, `sensors_v2`) forwarding.
- `grpc-web-proxy` (`@goldfish//emulator/tools/grpc-web:grpc-web-proxy`):
  Native HTTP/1.1 gRPC-Web proxy (`application/grpc-web+proto` and `+text`).
- `aquarium-ui` (`@goldfish//emulator/tools/aquarium-ui:aquarium-ui`):
  Unified web server hosting the compiled Aquarium React UI (`dist/`) and
  embedded `GrpcWebServer`.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import grpc

_logger = logging.getLogger(__name__)

_BINARY_RUNFILES_CANDIDATES: dict[str, tuple[str, ...]] = {
    "videobridge": (
        "goldfish+/emulator/videobridge/videobridge",
        "goldfish/emulator/videobridge/videobridge",
        "android_emulator/hardware/generic/goldfish/emulator/videobridge/videobridge",
        "_main/hardware/generic/goldfish/emulator/videobridge/videobridge",
        "bazel-bin/external/goldfish+/emulator/videobridge/videobridge",
    ),
    "grpc-web-proxy": (
        "goldfish+/emulator/tools/grpc-web/grpc-web-proxy",
        "goldfish/emulator/tools/grpc-web/grpc-web-proxy",
        "android_emulator/hardware/generic/goldfish/emulator/tools/grpc-web/grpc-web-proxy",
        "_main/hardware/generic/goldfish/emulator/tools/grpc-web/grpc-web-proxy",
        "bazel-bin/external/goldfish+/emulator/tools/grpc-web/grpc-web-proxy",
    ),
    "aquarium-ui": (
        "goldfish+/emulator/tools/aquarium-ui/aquarium-ui",
        "goldfish/emulator/tools/aquarium-ui/aquarium-ui",
        "android_emulator/hardware/generic/goldfish/emulator/tools/aquarium-ui/aquarium-ui",
        "_main/hardware/generic/goldfish/emulator/tools/aquarium-ui/aquarium-ui",
        "bazel-bin/external/goldfish+/emulator/tools/aquarium-ui/aquarium-ui",
    ),
}

_STATIC_DIR_CANDIDATES: tuple[str, ...] = (
    "goldfish+/emulator/ui/aquarium/dist",
    "goldfish/emulator/ui/aquarium/dist",
    "android_emulator/hardware/generic/goldfish/emulator/ui/aquarium/dist",
    "_main/hardware/generic/goldfish/emulator/ui/aquarium/dist",
    "bazel-bin/external/goldfish+/emulator/ui/aquarium/dist",
    "hardware/generic/goldfish/emulator/ui/aquarium/dist",
)


def _search_roots() -> list[Path]:
    """Returns candidate runfiles and workspace roots in priority order."""
    roots: list[Path] = []
    for env_var in ("RUNFILES_DIR", "PYTHON_RUNFILES", "TEST_SRCDIR"):
        val = os.environ.get(env_var)
        if val:
            roots.append(Path(val))

    if sys.argv and sys.argv[0]:
        argv0_runfiles = Path(f"{sys.argv[0]}.runfiles")
        if argv0_runfiles.is_dir():
            roots.append(argv0_runfiles)

    for env_var in ("BUILD_WORKSPACE_DIRECTORY", "BUILD_WORKING_DIRECTORY"):
        val = os.environ.get(env_var)
        if val:
            roots.append(Path(val))

    roots.append(Path.cwd())
    return roots


def resolve_companion_binary(
    name: str, explicit_path: str | Path | None = None
) -> Path | None:
    """Locates a companion binary (`videobridge`, `grpc-web-proxy`, `aquarium-ui`)."""
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise FileNotFoundError(
            f"Explicit {name} binary path '{explicit_path}' does not exist or is not executable. "
            f"Verify the path or omit --{name}-bin to resolve from Bazel runfiles."
        )

    candidates = _BINARY_RUNFILES_CANDIDATES.get(name, ())
    for root in _search_roots():
        for rel in candidates:
            candidate = root / rel
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
    return None


def resolve_aquarium_static_dir(
    explicit_dir: str | Path | None = None,
) -> Path | None:
    """Locates the compiled Aquarium UI `dist/` directory containing `index.html`."""
    if explicit_dir:
        p = Path(explicit_dir).expanduser().resolve()
        if (p / "index.html").is_file():
            return p
        raise FileNotFoundError(
            f"Explicit Aquarium static_dir '{explicit_dir}' does not contain index.html. "
            "Build '//emulator/ui/aquarium:dist' or omit --aquarium-static-dir."
        )

    for root in _search_roots():
        for rel in _STATIC_DIR_CANDIDATES:
            candidate = root / rel
            if (candidate / "index.html").is_file():
                return candidate.resolve()
    return None


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ensure_available_port(host: str, requested_port: int) -> int:
    if requested_port <= 0:
        return _pick_free_port()
    bind_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((bind_host, requested_port))
            return requested_port
        except OSError:
            fallback = _pick_free_port()
            _logger.warning(
                "Port %d on %s is already in use; falling back to free port %d.",
                requested_port,
                bind_host,
                fallback,
            )
            return fallback


def _wait_for_tcp_port(
    host: str,
    port: int,
    proc: subprocess.Popen[bytes],
    name: str,
    timeout_sec: float = 10.0,
) -> None:
    target_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Companion process '{name}' exited prematurely with code {proc.returncode} "
                f"while binding to {target_host}:{port}. Check the binary flags and logs above."
            )
        try:
            with socket.create_connection((target_host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(
        f"Timed out waiting {timeout_sec:.1f}s for '{name}' to listen on {target_host}:{port}."
    )


class CompanionManager:
    """Supervises native C++ companion processes attached to a ClownfishServer."""

    def __init__(self) -> None:
        self._processes: list[tuple[str, subprocess.Popen[bytes]]] = []
        self._temp_files: list[Path] = []
        self.videobridge_address: str | None = None
        self.grpc_web_url: str | None = None
        self.aquarium_url: str | None = None

    def start_videobridge(
        self,
        discovery_file: Path,
        max_dimension: int = 720,
        binary_path: str | Path | None = None,
        port: int = 0,
    ) -> str:
        """Starts the C++ `videobridge` process connected to `discovery_file`."""
        exe = resolve_companion_binary("videobridge", binary_path)
        if exe is None:
            raise FileNotFoundError(
                "Could not locate the 'videobridge' binary. Build or run via "
                "'bazel run @goldfish//emulator/tools/aquarium-ui:clownfish' or pass --videobridge-bin."
            )

        vb_port = _ensure_available_port("127.0.0.1", port)
        vb_address = f"127.0.0.1:{vb_port}"
        shm_path = (
            Path(tempfile.gettempdir()) / f"clownfish_shm_{os.getpid()}_{vb_port}.bin"
        )
        self._temp_files.append(shm_path)

        cmd = [
            str(exe),
            f"--discovery_file={discovery_file}",
            f"--grpc_address={vb_address}",
            f"--shared_memory_path={shm_path}",
        ]
        if max_dimension > 0:
            cmd.extend(
                [
                    f"--width={max_dimension}",
                    f"--height={max_dimension}",
                ]
            )

        _logger.info("Starting WebRTC videobridge: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)
        self._processes.append(("videobridge", proc))

        _wait_for_tcp_port("127.0.0.1", vb_port, proc, "videobridge")
        channel = grpc.insecure_channel(vb_address)
        try:
            grpc.channel_ready_future(channel).result(timeout=5.0)
        except grpc.FutureTimeoutError as err:
            raise TimeoutError(
                f"Timed out waiting 5.0s for videobridge gRPC channel at {vb_address} to become ready."
            ) from err
        finally:
            channel.close()

        self.videobridge_address = vb_address
        return vb_address

    def start_grpc_web_proxy(
        self,
        grpc_target: str,
        http_address: str = "127.0.0.1",
        http_port: int = 8888,
        binary_path: str | Path | None = None,
    ) -> str:
        """Starts the native `grpc-web-proxy` pointing at `grpc_target`."""
        exe = resolve_companion_binary("grpc-web-proxy", binary_path)
        if exe is None:
            raise FileNotFoundError(
                "Could not locate the 'grpc-web-proxy' binary. Build or run via "
                "'bazel run @goldfish//emulator/tools/aquarium-ui:clownfish' or pass --grpc-web-bin."
            )

        actual_port = _ensure_available_port(http_address, http_port)
        cmd = [
            str(exe),
            f"--http_address={http_address}",
            f"--http_port={actual_port}",
            f"--grpc_target={grpc_target}",
        ]
        _logger.info("Starting grpc-web-proxy: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)
        self._processes.append(("grpc-web-proxy", proc))

        _wait_for_tcp_port(http_address, actual_port, proc, "grpc-web-proxy")
        self.grpc_web_url = f"http://{http_address}:{actual_port}"
        return self.grpc_web_url

    def start_aquarium_ui(
        self,
        grpc_target: str,
        http_address: str = "0.0.0.0",
        http_port: int = 8080,
        binary_path: str | Path | None = None,
        static_dir: str | Path | None = None,
    ) -> str:
        """Starts the `aquarium-ui` web server + gRPC-Web proxy pointing at `grpc_target`."""
        exe = resolve_companion_binary("aquarium-ui", binary_path)
        if exe is None:
            raise FileNotFoundError(
                "Could not locate the 'aquarium-ui' binary. Build or run via "
                "'bazel run @goldfish//emulator/tools/aquarium-ui:clownfish' or pass --aquarium-bin."
            )

        resolved_static = resolve_aquarium_static_dir(static_dir)
        actual_port = _ensure_available_port(http_address, http_port)
        cmd = [
            str(exe),
            f"--http_address={http_address}",
            f"--http_port={actual_port}",
            f"--grpc_target={grpc_target}",
        ]
        if resolved_static is not None:
            cmd.append(f"--static_dir={resolved_static}")

        _logger.info("Starting aquarium-ui: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)
        self._processes.append(("aquarium-ui", proc))

        _wait_for_tcp_port(http_address, actual_port, proc, "aquarium-ui")
        display_host = (
            "localhost" if http_address in ("0.0.0.0", "127.0.0.1") else http_address
        )
        self.aquarium_url = f"http://{display_host}:{actual_port}"
        return self.aquarium_url

    def stop_all(self) -> None:
        """Terminates all managed companion processes and removes temporary files."""
        for name, proc in reversed(self._processes):
            if proc.poll() is None:
                _logger.info(
                    "Stopping companion process '%s' (pid=%d)...", name, proc.pid
                )
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
        self._processes.clear()

        for path in self._temp_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_files.clear()
        self.videobridge_address = None
        self.grpc_web_url = None
        self.aquarium_url = None
