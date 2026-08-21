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
from unittest.mock import patch

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.emulator import (
    calculate_mesh_ports,
    find_cached_emulator_dir,
    is_headless_launch,
    prepare_environment,
    resolve_emulator_executable,
)


class EmulatorLibTest(unittest.TestCase):

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

    def test_calculate_mesh_ports_success(self):
        ports = calculate_mesh_ports(base_port=5554, count=3)
        self.assertEqual(len(ports), 3)
        self.assertEqual(ports[0], {
            "index": 1,
            "console_port": 5554,
            "adb_port": 5555,
            "serial": "emulator-5554",
        })
        self.assertEqual(ports[1], {
            "index": 2,
            "console_port": 5556,
            "adb_port": 5557,
            "serial": "emulator-5556",
        })
        self.assertEqual(ports[2], {
            "index": 3,
            "console_port": 5558,
            "adb_port": 5559,
            "serial": "emulator-5558",
        })

    def test_calculate_mesh_ports_invalid_odd_base(self):
        with self.assertRaises(ValueError):
            calculate_mesh_ports(base_port=5555, count=2)

    def test_calculate_mesh_ports_out_of_range(self):
        with self.assertRaises(ValueError):
            calculate_mesh_ports(base_port=5550, count=1)
        with self.assertRaises(ValueError):
            calculate_mesh_ports(base_port=5680, count=3)

    def test_is_headless_launch(self):
        self.assertTrue(is_headless_launch(["-avd", "foo", "-no-window"]))
        self.assertTrue(is_headless_launch(["-headless"]))
        self.assertFalse(is_headless_launch(["-avd", "foo"]))

    def test_resolve_emulator_executable_direct(self):
        bin_path, emu_d = resolve_emulator_executable(self.emu_dir)
        self.assertEqual(bin_path, self.emu_bin)
        self.assertEqual(emu_d, self.emu_dir)

    def test_find_cached_emulator_dir(self):
        with patch("glob.glob", return_value=[self.emu_dir]):
            found = find_cached_emulator_dir()
            self.assertEqual(found, self.emu_dir)

    def test_prepare_environment(self):
        env = prepare_environment(self.emu_dir)
        self.assertIsInstance(env, dict)


if __name__ == "__main__":
    unittest.main()
