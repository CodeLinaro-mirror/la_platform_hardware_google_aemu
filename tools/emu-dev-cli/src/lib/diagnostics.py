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

"""Test troubleshooting and failure diagnostic collection for single and multi-device runs."""

from dataclasses import dataclass
import datetime
import getpass
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Union

from lib.mesh import find_adb_binary, find_netsim_binary


@dataclass
class DiagnosticsConfig:
    """Configuration options for test diagnostics and troubleshooting capture."""

    output_dir: Optional[str] = None
    capture_logcat: bool = True
    capture_pcap: bool = True
    capture_netsim_logs: bool = True
    capture_ui: bool = True
    clear_logcat_on_start: bool = True
    save_on_success: bool = False
    adb_path: Optional[str] = None
    netsim_bin: Optional[str] = None


class DiagnosticSession:
    """
    Session-scoped troubleshooting & failure diagnostic collector.
    Supports both single-device and multi-device mesh runs.

    Can be used as a context manager or controlled imperatively.
    """

    def __init__(
        self,
        devices: Union[str, List[str]],
        test_name: str = "test",
        config: Optional[DiagnosticsConfig] = None,
    ):
        if isinstance(devices, str):
            self.devices = [devices] if devices else []
        else:
            self.devices = list(devices)

        self.test_name = test_name
        self.config = config or DiagnosticsConfig()
        self.adb_bin = self.config.adb_path or find_adb_binary()
        self.netsim_bin = self.config.netsim_bin or find_netsim_binary()

        test_slug = re.sub(r"[^a-zA-Z0-9]+", "_", test_name).strip("_").lower() or "test"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.config.output_dir:
            self.session_dir = os.path.abspath(os.path.expanduser(self.config.output_dir))
        else:
            self.session_dir = os.path.join(
                tempfile.gettempdir(), "emu-dev-diagnostics", f"{test_slug}_{timestamp}"
            )

        self.start_time: float = 0.0
        self.start_iso: str = ""
        self.netsim_log_offsets: Dict[str, int] = {}
        self.is_active: bool = False
        self.bundle_collected: bool = False

    def start(self) -> None:
        """Initializes the diagnostic session and turns on packet/log captures."""
        self.start_time = time.time()
        self.start_iso = datetime.datetime.now().isoformat()
        self.is_active = True

        if self.config.clear_logcat_on_start and self.config.capture_logcat:
            for serial in self.devices:
                try:
                    subprocess.run(
                        [self.adb_bin, "-s", serial, "logcat", "-c"],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass

        if self.config.capture_pcap and self.netsim_bin:
            try:
                subprocess.run(
                    [self.netsim_bin, "capture", "patch", "on"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass

        if self.config.capture_netsim_logs:
            user = getpass.getuser()
            netsim_log_dir = os.path.join(tempfile.gettempdir(), f"android-{user}", "netsimd")
            for name in ["netsim_stdout.log", "netsim_stderr.log"]:
                path = os.path.join(netsim_log_dir, name)
                if os.path.isfile(path):
                    try:
                        self.netsim_log_offsets[name] = os.path.getsize(path)
                    except OSError:
                        self.netsim_log_offsets[name] = 0
                else:
                    self.netsim_log_offsets[name] = 0

    def collect_bundle(
        self,
        status: str = "FAILED",
        error: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Harvests logcats, pcaps, netsim logs, UI dumps, and generates a summary bundle."""
        os.makedirs(self.session_dir, exist_ok=True)
        captured_files: List[str] = []

        # 1. Capture ADB Logcats
        if self.config.capture_logcat:
            for serial in self.devices:
                log_file = os.path.join(self.session_dir, f"{serial}_logcat.log")
                try:
                    res = subprocess.run(
                        [self.adb_bin, "-s", serial, "logcat", "-d", "-v", "threadtime"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    with open(log_file, "w", encoding="utf-8", errors="replace") as f:
                        f.write(res.stdout)
                    captured_files.append(log_file)
                except (OSError, subprocess.SubprocessError):
                    pass

        # 2. Capture Netsim PCAPs
        if self.config.capture_pcap and self.netsim_bin:
            try:
                subprocess.run(
                    [self.netsim_bin, "capture", "get", "-o", self.session_dir],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                for entry in os.listdir(self.session_dir):
                    if entry.endswith(".pcap"):
                        captured_files.append(os.path.join(self.session_dir, entry))
            except (OSError, subprocess.SubprocessError):
                pass

        # 3. Capture Netsim Daemon Logs (sliced from start offset)
        if self.config.capture_netsim_logs:
            user = getpass.getuser()
            netsim_log_dir = os.path.join(tempfile.gettempdir(), f"android-{user}", "netsimd")
            for name in ["netsim_stdout.log", "netsim_stderr.log"]:
                src_path = os.path.join(netsim_log_dir, name)
                if os.path.isfile(src_path):
                    offset = self.netsim_log_offsets.get(name, 0)
                    dst_path = os.path.join(self.session_dir, name)
                    try:
                        with open(src_path, "r", encoding="utf-8", errors="replace") as src:
                            src.seek(offset)
                            content = src.read()
                        with open(dst_path, "w", encoding="utf-8") as dst:
                            dst.write(content)
                        captured_files.append(dst_path)
                    except OSError:
                        pass

        # 4. Capture UI Hierarchy & Screenshot
        if self.config.capture_ui:
            for serial in self.devices:
                # Screenshot
                png_file = os.path.join(self.session_dir, f"{serial}_screenshot.png")
                try:
                    res = subprocess.run(
                        [self.adb_bin, "-s", serial, "exec-out", "screencap", "-p"],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                    if res.returncode == 0 and res.stdout:
                        with open(png_file, "wb") as f:
                            if isinstance(res.stdout, str):
                                f.write(res.stdout.encode("utf-8", errors="replace"))
                            else:
                                f.write(res.stdout)
                        captured_files.append(png_file)
                except (OSError, subprocess.SubprocessError):
                    pass

                # View Hierarchy XML
                xml_file = os.path.join(self.session_dir, f"{serial}_ui.xml")
                try:
                    res = subprocess.run(
                        [self.adb_bin, "-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    if res.returncode == 0 and res.stdout:
                        with open(xml_file, "w", encoding="utf-8", errors="replace") as f:
                            f.write(res.stdout)
                        captured_files.append(xml_file)
                except (OSError, subprocess.SubprocessError):
                    pass

        duration = round(time.time() - self.start_time, 2) if self.start_time else 0.0
        summary_data = {
            "test_name": self.test_name,
            "status": status,
            "error": error,
            "devices": self.devices,
            "start_time": self.start_iso,
            "duration_seconds": duration,
            "captured_files": [os.path.basename(f) for f in captured_files],
            "details": details or {},
        }

        summary_file = os.path.join(self.session_dir, "summary.json")
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2)
            captured_files.append(summary_file)
        except OSError:
            pass

        self.bundle_collected = True
        return {
            "session_dir": self.session_dir,
            "status": status,
            "duration_seconds": duration,
            "captured_files": captured_files,
            "summary": summary_data,
        }

    def stop(self) -> None:
        """Stops diagnostic session and turns off Netsim packet capture."""
        if self.config.capture_pcap and self.netsim_bin:
            try:
                subprocess.run(
                    [self.netsim_bin, "capture", "patch", "off"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        self.is_active = False

    def __enter__(self) -> "DiagnosticSession":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self.collect_bundle(status="FAILED", error=str(exc_val))
        elif self.config.save_on_success and not self.bundle_collected:
            self.collect_bundle(status="PASSED")
        self.stop()
        return False
