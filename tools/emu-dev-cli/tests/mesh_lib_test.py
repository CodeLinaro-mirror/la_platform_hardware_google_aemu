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

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.mesh import (
    check_device_boot_completed,
    find_adb_binary,
    find_netsim_binary,
    find_netsim_device_by_name,
    get_connected_adb_devices,
    get_device_avd_name,
    get_netsim_devices,
    reset_netsim_state,
    stop_emulator_device,
    summarize_netsim_chips,
    teardown_mesh,
    unlock_device_screen,
    wait_for_mesh_ready,
)


class MeshLibTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_adb_binary(self):
        with patch("shutil.which", return_value="/usr/bin/adb"):
            self.assertEqual(find_adb_binary(), "/usr/bin/adb")

    def test_find_netsim_binary(self):
        emu_dir = os.path.join(self.temp_dir.name, "emulator")
        bin_dir = os.path.join(emu_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        netsim_path = os.path.join(bin_dir, "netsim")
        with open(netsim_path, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(netsim_path, 0o755)

        self.assertEqual(find_netsim_binary(emu_dir=emu_dir), netsim_path)

    def test_get_connected_adb_devices(self):
        mock_output = "List of devices attached\nemulator-5554\tdevice\nemulator-5556\tdevice\nemulator-5558\toffline\n"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = mock_output

        with patch("subprocess.run", return_value=mock_res):
            devices = get_connected_adb_devices()
            self.assertEqual(devices, ["emulator-5554", "emulator-5556"])

    def test_check_device_boot_completed(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "1\n"

        with patch("subprocess.run", return_value=mock_res):
            self.assertTrue(check_device_boot_completed("emulator-5554"))

        mock_res.stdout = "0\n"
        with patch("subprocess.run", return_value=mock_res):
            self.assertFalse(check_device_boot_completed("emulator-5554"))

    def test_unlock_device_screen(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        with patch("subprocess.run", return_value=mock_res) as mock_run:
            self.assertTrue(unlock_device_screen("emulator-5554"))
            self.assertEqual(mock_run.call_count, 3)

    def test_get_netsim_devices(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"devices": [{"name": "bt-mesh-1", "chips": ["ble"]}]}'

        with patch("lib.mesh.find_netsim_binary", return_value="/mock/netsim"):
            with patch("subprocess.run", return_value=mock_res):
                data = get_netsim_devices()
                self.assertIsNotNone(data)
                self.assertIn("devices", data)
                self.assertEqual(data["devices"][0]["name"], "bt-mesh-1")

    def test_wait_for_mesh_ready_success(self):
        with patch("lib.mesh.check_device_boot_completed", return_value=True):
            with patch("lib.mesh.unlock_device_screen", return_value=True):
                with patch("lib.mesh.get_netsim_devices", return_value={"devices": []}):
                    res = wait_for_mesh_ready(
                        serials=["emulator-5554", "emulator-5556"],
                        timeout=5,
                        poll_interval=0.01,
                        unlock=True,
                        verify_netsim=True,
                    )
                    self.assertEqual(res["status"], "success")
                    self.assertTrue(res["all_ready"])
                    self.assertEqual(res["total_nodes"], 2)
                    self.assertEqual(res["ready_nodes"], 2)

    def test_wait_for_mesh_ready_timeout(self):
        # First call False, always False
        with patch("lib.mesh.check_device_boot_completed", return_value=False):
            res = wait_for_mesh_ready(
                serials=["emulator-5554"],
                timeout=0.05,
                poll_interval=0.01,
                unlock=False,
                verify_netsim=False,
            )
            self.assertEqual(res["status"], "timeout")
            self.assertFalse(res["all_ready"])
            self.assertEqual(res["ready_nodes"], 0)
            self.assertEqual(res["pending_serials"], ["emulator-5554"])


    def test_get_device_avd_name(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "mesh-node-1\nOK\n"

        with patch("subprocess.run", return_value=mock_res):
            self.assertEqual(get_device_avd_name("emulator-5554"), "mesh-node-1")

    def test_summarize_netsim_chips(self):
        dev = {
            "name": "mesh-node-1",
            "chips": [
                {"kind": "WIFI"},
                {"kind": "UWB"},
                {"kind": "BLUETOOTH", "bt": {"lowEnergy": {"state": True}, "classic": {"state": True}}},
            ]
        }
        self.assertEqual(summarize_netsim_chips(dev), "Wi-Fi, UWB, BLE+Classic")
        self.assertEqual(summarize_netsim_chips(None), "None")

    def test_find_netsim_device_by_name(self):
        data = {"devices": [{"name": "node-1"}, {"name": "node-2"}]}
        self.assertEqual(find_netsim_device_by_name(data, "node-2")["name"], "node-2")
        self.assertIsNone(find_netsim_device_by_name(data, "node-3"))
        self.assertIsNone(find_netsim_device_by_name(None, "node-1"))

    def test_stop_emulator_device(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        with patch("subprocess.run", return_value=mock_res) as mock_run:
            self.assertTrue(stop_emulator_device("emulator-5554"))
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("-s", args)
            self.assertIn("emulator-5554", args)
            self.assertIn("emu", args)
            self.assertIn("kill", args)

    def test_reset_netsim_state(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        with patch("lib.mesh.find_netsim_binary", return_value="/mock/netsim"):
            with patch("subprocess.run", return_value=mock_res) as mock_run:
                self.assertTrue(reset_netsim_state())
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                self.assertIn("reset", args)

    def test_teardown_mesh(self):
        with patch("lib.mesh.stop_emulator_device", return_value=True):
            with patch("lib.mesh.get_device_avd_name", return_value="mesh-node-1"):
                with patch("lib.mesh.reset_netsim_state", return_value=True):
                    with patch("lib.mesh.find_netsim_binary", return_value="/mock/netsim"):
                        res = teardown_mesh(["emulator-5554", "emulator-5556"], reset_netsim=True)
                        self.assertEqual(res["status"], "success")
                        self.assertEqual(res["total_nodes"], 2)
                        self.assertEqual(res["total_stopped"], 2)
                        self.assertEqual(res["stopped_serials"], ["emulator-5554", "emulator-5556"])
                        self.assertTrue(res["netsim_reset"])


if __name__ == "__main__":
    unittest.main()
