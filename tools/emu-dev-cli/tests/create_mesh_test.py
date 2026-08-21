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

from commands.create import create_single_avd, run_create_avd


class CreateMeshTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sysimg_dir = os.path.join(self.temp_dir.name, "sysimg")
        os.makedirs(self.sysimg_dir, exist_ok=True)
        # Create dummy kernel and source.properties
        with open(os.path.join(self.sysimg_dir, "kernel-ranchu"), "w") as f:
            f.write("mock-kernel")
        with open(os.path.join(self.sysimg_dir, "source.properties"), "w") as f:
            f.write("SystemImage.Abi=x86_64\n")

        self.avd_root = os.path.join(self.temp_dir.name, "avd_root")
        os.makedirs(self.avd_root, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_single_avd(self):
        res = create_single_avd(
            avd_name="test-phone",
            profile_name="medium_phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
        )
        self.assertEqual(res["avd_name"], "test-phone")
        self.assertEqual(res["profile"], "medium_phone")
        self.assertTrue(os.path.exists(res["ini_file"]))
        self.assertTrue(os.path.exists(os.path.join(res["avd_dir"], "config.ini")))

        with open(res["ini_file"], "r", encoding="utf-8") as f:
            ini_content = f.read()
        self.assertIn(f"path={res['avd_dir']}", ini_content)

        with open(os.path.join(res["avd_dir"], "config.ini"), "r", encoding="utf-8") as f:
            config_content = f.read()
        self.assertIn("AvdId=test-phone", config_content)
        self.assertIn("hw.cpu.arch=x86_64", config_content)

    def test_create_single_avd_already_exists_error(self):
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

        # Passing force=True succeeds
        res = create_single_avd(
            avd_name="test-phone",
            raw_sysimg_dir=self.sysimg_dir,
            avd_root=self.avd_root,
            force=True,
        )
        self.assertEqual(res["avd_name"], "test-phone")

    def test_batch_mesh_creation(self):
        class Args:
            name = None
            prefix = "bt-mesh"
            count = 3
            sysimg_dir = self.sysimg_dir
            profile = "small_phone"
            list_profiles = False
            ram = 1024
            cores = 2
            disk_size = "4G"
            gpu = "auto"
            force = True
            json = True

        with patch("os.path.expanduser", return_value=self.temp_dir.name):
            # Intercept print_result
            with patch("commands.create.print_result") as mock_print:
                run_create_avd(Args())
                self.assertTrue(mock_print.called)
                result_payload = mock_print.call_args[0][0]
                self.assertEqual(result_payload["status"], "success")
                self.assertEqual(result_payload["action"], "create mesh")
                self.assertEqual(result_payload["count"], 3)
                self.assertEqual(
                    result_payload["avd_names"],
                    ["bt-mesh-1", "bt-mesh-2", "bt-mesh-3"],
                )


if __name__ == "__main__":
    unittest.main()
