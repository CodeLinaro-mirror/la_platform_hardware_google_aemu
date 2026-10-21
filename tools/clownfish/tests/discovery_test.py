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

"""Unit tests for Clownfish DiscoveryAdvertiser interoperability with aemu.discovery."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aemu.discovery.emulator_discovery import EmulatorDiscovery
from clownfish.discovery import DiscoveryAdvertiser
from clownfish.state import EmulatorState


class DiscoveryAdvertiserTest(unittest.TestCase):
    """Verifies that pid_<PID>.ini written by Clownfish is parsed by EmulatorDiscovery."""

    def test_write_and_remove_discovery_file(self) -> None:
        state = EmulatorState(preset="phone", avd_name="Clownfish_Test_AVD")
        live_pid = os.getpid()
        with tempfile.TemporaryDirectory() as tmpdir:
            advertiser = DiscoveryAdvertiser(
                state=state,
                grpc_port=18554,
                serial_port=5556,
                discovery_dir=tmpdir,
                pid=live_pid,
            )
            ini_path = advertiser.write()
            self.assertTrue(ini_path.exists())
            self.assertEqual(ini_path.name, f"pid_{live_pid}.ini")

            with mock.patch(
                "aemu.discovery.emulator_discovery.get_discovery_directories",
                return_value=[Path(tmpdir)],
            ):
                discovery = EmulatorDiscovery()
                emu = discovery.find_by_pid(live_pid)
                self.assertIsNotNone(emu)
                assert emu is not None
                self.assertEqual(emu.name(), "emulator-5556")
                self.assertEqual(emu.get("avd.name"), "Clownfish_Test_AVD")
                self.assertEqual(int(emu.get("grpc.port")), 18554)
                self.assertEqual(int(emu.get("port.serial")), 5556)
                self.assertEqual(int(emu.get("port.adb")), 5557)

            advertiser.remove()
            self.assertFalse(ini_path.exists())


if __name__ == "__main__":
    unittest.main()
