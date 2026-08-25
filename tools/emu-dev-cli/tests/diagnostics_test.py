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

"""Unit tests for DiagnosticSession and DiagnosticsConfig."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import pytest

from lib.diagnostics import DiagnosticSession, DiagnosticsConfig


def test_diagnostics_config_defaults():
    config = DiagnosticsConfig()
    assert config.output_dir is None
    assert config.capture_logcat is True
    assert config.capture_pcap is True
    assert config.capture_netsim_logs is True
    assert config.capture_ui is True
    assert config.clear_logcat_on_start is True
    assert config.save_on_success is False


def test_diagnostic_session_init_single_and_multi_device(tmp_path):
    # Single device as string
    session_single = DiagnosticSession("emulator-5554", test_name="My Test")
    assert session_single.devices == ["emulator-5554"]
    assert "my_test" in session_single.session_dir

    # Multi device as list
    custom_dir = str(tmp_path / "custom_diag")
    session_multi = DiagnosticSession(
        ["emulator-5554", "emulator-5556"],
        test_name="Mesh BLE",
        config=DiagnosticsConfig(output_dir=custom_dir),
    )
    assert session_multi.devices == ["emulator-5554", "emulator-5556"]
    assert session_multi.session_dir == os.path.abspath(custom_dir)


@patch("subprocess.run")
def test_diagnostic_session_start(mock_run, tmp_path):
    mock_run.return_value = MagicMock(return_value=0, stdout=b"", stderr=b"")
    custom_dir = str(tmp_path / "diag_start")

    config = DiagnosticsConfig(
        output_dir=custom_dir,
        adb_path="/mock/adb",
        netsim_bin="/mock/netsim",
        clear_logcat_on_start=True,
        capture_pcap=True,
    )
    session = DiagnosticSession(["emulator-5554", "emulator-5556"], "start_test", config=config)
    session.start()

    assert session.is_active is True
    assert session.start_time > 0

    # Verify logcat -c called for both devices
    expected_calls = [
        ["/mock/adb", "-s", "emulator-5554", "logcat", "-c"],
        ["/mock/adb", "-s", "emulator-5556", "logcat", "-c"],
        ["/mock/netsim", "capture", "patch", "on"],
    ]
    actual_cmds = [call.args[0] for call in mock_run.call_args_list]
    for exp in expected_calls:
        assert exp in actual_cmds


@patch("subprocess.run")
def test_diagnostic_session_collect_bundle(mock_run, tmp_path):
    def fake_subprocess_run(cmd, *args, **kwargs):
        if "logcat" in cmd:
            return MagicMock(returncode=0, stdout="[LOGCAT SAMPLE DATA]\n", stderr="")
        elif "screencap" in cmd:
            return MagicMock(returncode=0, stdout=b"\x89PNG\r\nFakeImageData", stderr=b"")
        elif "uiautomator" in cmd:
            return MagicMock(
                returncode=0,
                stdout='<hierarchy><node text="Pass" resource-id="pass_btn"/></hierarchy>',
                stderr="",
            )
        elif "capture" in cmd and "get" in cmd:
            # Simulate netsim writing a .pcap file into the directory
            out_dir = cmd[cmd.index("-o") + 1]
            pcap_path = os.path.join(out_dir, "mesh-node-1_BLUETOOTH.pcap")
            with open(pcap_path, "wb") as f:
                f.write(b"PCAPDATA")
            return MagicMock(returncode=0, stdout="Downloaded pcap", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_subprocess_run

    custom_dir = str(tmp_path / "diag_bundle")
    config = DiagnosticsConfig(
        output_dir=custom_dir,
        adb_path="/mock/adb",
        netsim_bin="/mock/netsim",
        capture_netsim_logs=False,
    )
    session = DiagnosticSession(["emulator-5554", "emulator-5556"], "bundle_test", config=config)
    session.start()

    bundle = session.collect_bundle(status="FAILED", error="Assertion failed: packet count mismatch")

    assert os.path.isdir(custom_dir)
    assert bundle["status"] == "FAILED"
    assert "emulator-5554_logcat.log" in [os.path.basename(f) for f in bundle["captured_files"]]
    assert "emulator-5556_logcat.log" in [os.path.basename(f) for f in bundle["captured_files"]]
    assert "mesh-node-1_BLUETOOTH.pcap" in [os.path.basename(f) for f in bundle["captured_files"]]
    assert "emulator-5554_screenshot.png" in [os.path.basename(f) for f in bundle["captured_files"]]
    assert "emulator-5554_ui.xml" in [os.path.basename(f) for f in bundle["captured_files"]]
    assert "summary.json" in [os.path.basename(f) for f in bundle["captured_files"]]

    # Inspect summary.json content
    summary_path = os.path.join(custom_dir, "summary.json")
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["test_name"] == "bundle_test"
    assert data["status"] == "FAILED"
    assert data["error"] == "Assertion failed: packet count mismatch"
    assert data["devices"] == ["emulator-5554", "emulator-5556"]


@patch("subprocess.run")
def test_diagnostic_session_context_manager_on_exception(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="log", stderr="")

    custom_dir = str(tmp_path / "ctx_fail")
    config = DiagnosticsConfig(
        output_dir=custom_dir,
        adb_path="/mock/adb",
        netsim_bin="/mock/netsim",
        capture_netsim_logs=False,
    )

    with pytest.raises(RuntimeError, match="DUT disconnected"):
        with DiagnosticSession("emulator-5554", "ctx_test", config=config):
            raise RuntimeError("DUT disconnected")

    summary_path = os.path.join(custom_dir, "summary.json")
    assert os.path.isfile(summary_path)
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "FAILED"
    assert "DUT disconnected" in data["error"]


@patch("subprocess.run")
def test_diagnostic_session_context_manager_on_success(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="log", stderr="")

    # Default: save_on_success=False -> no bundle
    dir_nosave = str(tmp_path / "ctx_nosave")
    config_nosave = DiagnosticsConfig(output_dir=dir_nosave, save_on_success=False)
    with DiagnosticSession("emulator-5554", "pass_test", config=config_nosave):
        pass
    assert not os.path.exists(os.path.join(dir_nosave, "summary.json"))

    # Configured: save_on_success=True -> bundle saved with status PASSED
    dir_save = str(tmp_path / "ctx_save")
    config_save = DiagnosticsConfig(
        output_dir=dir_save,
        save_on_success=True,
        adb_path="/mock/adb",
        netsim_bin="/mock/netsim",
        capture_netsim_logs=False,
    )
    with DiagnosticSession("emulator-5554", "pass_test", config=config_save):
        pass
    summary_path = os.path.join(dir_save, "summary.json")
    assert os.path.isfile(summary_path)
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "PASSED"
