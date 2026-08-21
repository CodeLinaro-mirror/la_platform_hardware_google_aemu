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
import unittest
from unittest.mock import patch

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.mesh import run_mesh_status, run_wait_ready


class MeshCmdTest(unittest.TestCase):

    def test_run_wait_ready_success(self):
        class Args:
            serials = "emulator-5554,emulator-5556"
            prefix = "bt-mesh"
            count = 2
            base_port = 5554
            timeout = 10
            poll_interval = 0.01
            no_unlock = False
            no_netsim_check = False
            emulator_dir = None
            json = True

        mock_ready_res = {
            "status": "success",
            "all_ready": True,
            "elapsed_seconds": 1.5,
            "total_nodes": 2,
            "ready_nodes": 2,
            "pending_serials": [],
            "nodes": [
                {"serial": "emulator-5554", "boot_completed": True, "screen_unlocked": True},
                {"serial": "emulator-5556", "boot_completed": True, "screen_unlocked": True},
            ],
            "netsim_active": True,
            "netsim_devices": {"devices": []},
        }

        with patch("commands.mesh.wait_for_mesh_ready", return_value=mock_ready_res):
            with patch("commands.mesh.print_result") as mock_print:
                run_wait_ready(Args())
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "success")
                self.assertTrue(payload["all_ready"])
                self.assertEqual(payload["serials"], ["emulator-5554", "emulator-5556"])

    def test_run_wait_ready_timeout(self):
        class Args:
            serials = None
            prefix = "bt-mesh"
            count = 2
            base_port = 5554
            timeout = 5
            poll_interval = 0.01
            no_unlock = True
            no_netsim_check = True
            emulator_dir = None
            json = True

        mock_timeout_res = {
            "status": "timeout",
            "all_ready": False,
            "elapsed_seconds": 5.0,
            "total_nodes": 2,
            "ready_nodes": 1,
            "pending_serials": ["emulator-5556"],
            "nodes": [
                {"serial": "emulator-5554", "boot_completed": True, "screen_unlocked": None},
                {"serial": "emulator-5556", "boot_completed": False, "screen_unlocked": None},
            ],
            "netsim_active": False,
            "netsim_devices": None,
        }

        with patch("commands.mesh.wait_for_mesh_ready", return_value=mock_timeout_res):
            with patch("commands.mesh.print_result") as mock_print:
                with self.assertRaises(SystemExit):
                    run_wait_ready(Args())
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "timeout")
                self.assertEqual(payload["pending_serials"], ["emulator-5556"])

    def test_run_mesh_status(self):
        class Args:
            serials = "emulator-5554"
            emulator_dir = None
            json = True

        with patch("commands.mesh.check_device_boot_completed", return_value=True):
            with patch("commands.mesh.get_device_avd_name", return_value="mesh-node-1"):
                with patch("commands.mesh.find_netsim_binary", return_value=None):
                    with patch("commands.mesh.print_result") as mock_print:
                        run_mesh_status(Args())
                        self.assertTrue(mock_print.called)
                        payload = mock_print.call_args[0][0]
                        self.assertEqual(payload["status"], "success")
                        self.assertEqual(payload["total_nodes"], 1)
                        self.assertEqual(payload["serials"], ["emulator-5554"])
                        self.assertEqual(payload["nodes"][0]["serial"], "emulator-5554")
                        self.assertEqual(payload["nodes"][0]["avd_name"], "mesh-node-1")
                        self.assertTrue(payload["nodes"][0]["boot_completed"])
                        self.assertIn("markdown_table", payload)

    def test_run_mesh_status_empty(self):
        class Args:
            serials = None
            emulator_dir = None
            json = True

        with patch("commands.mesh.get_connected_adb_devices", return_value=[]):
            with patch("commands.mesh.print_result") as mock_print:
                run_mesh_status(Args())
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["total_nodes"], 0)
                self.assertEqual(payload["serials"], [])


if __name__ == "__main__":
    unittest.main()
