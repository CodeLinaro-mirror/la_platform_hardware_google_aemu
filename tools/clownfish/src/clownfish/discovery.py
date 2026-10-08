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

"""Emulator discovery (`pid_<PID>.ini`) file advertiser for Clownfish."""

import os
import tempfile
from pathlib import Path

from aemu.discovery.emulator_discovery import get_discovery_directories

from clownfish.state import EmulatorState


class DiscoveryAdvertiser:
    """Writes and cleans up `pid_<PID>.ini` in the emulator discovery directory."""

    def __init__(
        self,
        state: EmulatorState,
        grpc_port: int,
        serial_port: int = 5554,
        discovery_dir: str | Path | None = None,
        grpc_token: str | None = None,
        pid: int | None = None,
    ) -> None:
        self.state = state
        self.grpc_port = grpc_port
        self.serial_port = serial_port
        self.grpc_token = grpc_token
        self.pid = pid if pid is not None else os.getpid()
        self._discovery_dir: Path = self._resolve_discovery_dir(discovery_dir)
        self._ini_path: Path | None = None

    @staticmethod
    def _resolve_discovery_dir(custom_dir: str | Path | None) -> Path:
        if custom_dir is not None:
            return Path(custom_dir)
        dirs = get_discovery_directories()
        if dirs:
            return Path(dirs[0])
        return Path(tempfile.gettempdir()) / "avd" / "running"

    @property
    def ini_path(self) -> Path | None:
        """Path to the active `pid_<PID>.ini` file if written."""
        return self._ini_path

    def write(self) -> Path:
        """Atomically writes `pid_<PID>.ini` into the discovery directory."""
        self._discovery_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._discovery_dir / f"pid_{self.pid}.ini"
        tmp_path = self._discovery_dir / f".pid_{self.pid}.ini.tmp"

        preset = self.state.preset
        avd_dir = self._discovery_dir / preset.avd_name
        lines = [
            f"port.serial={self.serial_port}",
            f"port.adb={self.serial_port + 1}",
            f"avd.name={preset.avd_name}",
            f"avd.id={preset.avd_id}",
            f"avd.dir={avd_dir}",
            f"cmdline=clownfish -avd {preset.avd_name} -port {self.serial_port} -grpc {self.grpc_port}",
            f"grpc.port={self.grpc_port}",
            f"emulator.version={preset.emulator_version}",
            f"emulator.build={preset.emulator_build}",
        ]
        if self.grpc_token:
            lines.append(f"grpc.token={self.grpc_token}")

        content = "\n".join(lines) + "\n"
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(target_path)
        self._ini_path = target_path
        return target_path

    def remove(self) -> None:
        """Removes the advertised `pid_<PID>.ini` file if present."""
        if self._ini_path is not None:
            try:
                self._ini_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._ini_path = None
