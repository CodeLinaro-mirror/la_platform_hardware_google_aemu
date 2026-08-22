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

from commands.launch import run_launch_mesh


class LaunchMeshTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.emu_dir = os.path.join(self.temp_dir.name, "emulator")
        os.makedirs(self.emu_dir, exist_ok=True)
        self.emu_bin = os.path.join(self.emu_dir, "emulator")
        with open(self.emu_bin, "w") as f:
            f.write("#!/bin/sh\necho mock emulator\n")
        os.chmod(self.emu_bin, 0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_launch_mesh_dry_run(self):
        class Args:
            emulator_dir = self.emu_dir
            prefix = "bt-mesh"
            count = 2
            base_port = 5554
            packet_streamer = "default"
            no_window = True
            log_dir = os.path.join(self.temp_dir.name, "logs")
            dry_run = True
            emulator_args = ["--", "-gpu", "swiftshader_indirect"]
            json = True

        with patch("commands.launch.print_result") as mock_print:
            run_launch_mesh(Args())
            self.assertTrue(mock_print.called)
            payload = mock_print.call_args[0][0]
            self.assertEqual(payload["status"], "dry_run")
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["serials"], ["emulator-5554", "emulator-5556"])
            self.assertEqual(len(payload["nodes"]), 2)

            node1 = payload["nodes"][0]
            self.assertEqual(node1["avd_name"], "bt-mesh-1")
            self.assertEqual(node1["console_port"], 5554)
            self.assertEqual(node1["adb_port"], 5555)
            self.assertIn("-packet-streamer-endpoint", node1["command"])
            self.assertIn("default", node1["command"])
            self.assertIn("-no-window", node1["command"])
            self.assertIn("-gpu", node1["command"])

            node2 = payload["nodes"][1]
            self.assertEqual(node2["avd_name"], "bt-mesh-2")
            self.assertEqual(node2["console_port"], 5556)
            self.assertEqual(node2["adb_port"], 5557)

    def test_launch_mesh_missing_emulator_dir(self):
        class Args:
            emulator_dir = None
            prefix = "bt-mesh"
            count = 2
            base_port = 5554
            packet_streamer = "default"
            no_window = False
            log_dir = None
            dry_run = False
            emulator_args = []
            json = True

        with patch("commands.launch.find_cached_emulator_dir", return_value=None):
            with patch("commands.launch.print_result") as mock_print:
                with self.assertRaises(SystemExit):
                    run_launch_mesh(Args())
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["suggestion"], "emu-dev-cli fetch-build emulator --latest")

    def test_launch_mesh_spawning(self):
        class Args:
            emulator_dir = self.emu_dir
            prefix = "bt-mesh"
            count = 2
            base_port = 5554
            packet_streamer = "default"
            no_window = True
            log_dir = os.path.join(self.temp_dir.name, "logs")
            dry_run = False
            emulator_args = []
            json = True

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("commands.launch.spawn_emulator_process", return_value=mock_proc) as mock_spawn:
            with patch("commands.launch.print_result") as mock_print:
                run_launch_mesh(Args())
                self.assertEqual(mock_spawn.call_count, 2)
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["nodes"][0]["pid"], 12345)


if __name__ == "__main__":
    unittest.main()
