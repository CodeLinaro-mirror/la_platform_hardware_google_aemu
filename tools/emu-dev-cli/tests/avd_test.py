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

from lib.avd import (
    DEVICE_PROFILES,
    create_mesh_avds,
    create_single_avd,
    detect_arch_and_abi,
    find_actual_sysimg_dir,
    find_cached_sysimg_dir,
    get_avd_paths,
    get_mesh_node_names,
)


class AvdLibTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sysimg_dir = os.path.join(self.temp_dir.name, "sysimg")
        os.makedirs(self.sysimg_dir, exist_ok=True)
        # Create dummy kernel, userdata.img, and source.properties
        with open(os.path.join(self.sysimg_dir, "kernel-ranchu"), "w") as f:
            f.write("mock-kernel")
        with open(os.path.join(self.sysimg_dir, "userdata.img"), "w") as f:
            f.write("mock-userdata")
        with open(os.path.join(self.sysimg_dir, "source.properties"), "w") as f:
            f.write("SystemImage.Abi=x86_64\n")

        self.avd_root = os.path.join(self.temp_dir.name, "avd_root")
        os.makedirs(self.avd_root, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_actual_sysimg_dir(self):
        # Direct folder containing kernel-ranchu
        self.assertEqual(find_actual_sysimg_dir(self.sysimg_dir), self.sysimg_dir)

        # Nested folder
        nested_root = os.path.join(self.temp_dir.name, "nested_root")
        nested_sub = os.path.join(nested_root, "extracted", "x86_64")
        os.makedirs(nested_sub, exist_ok=True)
        with open(os.path.join(nested_sub, "system.img"), "w") as f:
            f.write("mock-sysimg")
        self.assertEqual(find_actual_sysimg_dir(nested_root), nested_sub)

    def test_find_cached_sysimg_dir(self):
        with patch("glob.glob", return_value=[self.sysimg_dir]):
            found = find_cached_sysimg_dir()
            self.assertEqual(found, self.sysimg_dir)

    def test_detect_arch_and_abi(self):
        arch, abi = detect_arch_and_abi(self.sysimg_dir)
        self.assertEqual(arch, "x86_64")
        self.assertEqual(abi, "x86_64")

        # Arm64 test
        arm_dir = os.path.join(self.temp_dir.name, "arm_sysimg")
        os.makedirs(arm_dir, exist_ok=True)
        with open(os.path.join(arm_dir, "source.properties"), "w") as f:
            f.write("SystemImage.Abi=arm64-v8a\n")
        arch_arm, abi_arm = detect_arch_and_abi(arm_dir)
        self.assertEqual(arch_arm, "arm64")
        self.assertEqual(abi_arm, "arm64-v8a")

    def test_get_mesh_node_names(self):
        # Single with explicit name
        self.assertEqual(get_mesh_node_names("my-phone", 1, explicit_name="custom"), ["custom"])
        # Single with prefix
        self.assertEqual(get_mesh_node_names("my-phone", 1), ["my-phone"])
        # Multiple count
        self.assertEqual(get_mesh_node_names("bt-node", 3), ["bt-node-1", "bt-node-2", "bt-node-3"])

    def test_get_avd_paths(self):
        ini_p, avd_p = get_avd_paths("test-phone", avd_root="/tmp/avds")
        self.assertEqual(ini_p, "/tmp/avds/test-phone.ini")
        self.assertEqual(avd_p, "/tmp/avds/test-phone.avd")

    def test_create_single_avd(self):
        res = create_single_avd(
            avd_name="test-phone",
            profile_name="small_phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
        )
        self.assertEqual(res["avd_name"], "test-phone")
        self.assertEqual(res["profile"], "small_phone")
        self.assertTrue(os.path.exists(res["ini_file"]))
        self.assertTrue(os.path.exists(os.path.join(res["avd_dir"], "config.ini")))
        self.assertTrue(os.path.exists(os.path.join(res["avd_dir"], "userdata.img")))

        with open(os.path.join(res["avd_dir"], "config.ini"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AvdId=test-phone", content)
        self.assertIn("hw.cpu.arch=x86_64", content)
        self.assertIn(f"hw.ramSize={DEVICE_PROFILES['small_phone']['ram']}", content)

    def test_create_single_avd_exists_error_and_force(self):
        create_single_avd(
            avd_name="test-phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
        )
        with self.assertRaises(FileExistsError):
            create_single_avd(
                avd_name="test-phone",
                raw_sysimg_dir=self.sysimg_dir,
                avd_root=self.avd_root,
                force=False,
            )

        # Force overwrite
        res = create_single_avd(
            avd_name="test-phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
            force=True,
        )
        self.assertEqual(res["avd_name"], "test-phone")

    def test_create_mesh_avds(self):
        res_list = create_mesh_avds(
            prefix="bt-mesh",
            count=2,
            profile_name="medium_phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
        )
        self.assertEqual(len(res_list), 2)
        self.assertEqual(res_list[0]["avd_name"], "bt-mesh-1")
        self.assertEqual(res_list[1]["avd_name"], "bt-mesh-2")
        self.assertTrue(os.path.exists(res_list[0]["ini_file"]))
        self.assertTrue(os.path.exists(res_list[1]["ini_file"]))


if __name__ == "__main__":
    unittest.main()
