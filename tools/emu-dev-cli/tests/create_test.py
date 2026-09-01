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

from commands.create import find_cached_sysimg_dir, run_create_avd


class CreateTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sysimg_dir = os.path.join(self.temp_dir.name, "sysimg")
        os.makedirs(self.sysimg_dir, exist_ok=True)
        # Create dummy kernel and source.properties
        with open(os.path.join(self.sysimg_dir, "kernel-ranchu"), "w") as f:
            f.write("mock-kernel")
        with open(os.path.join(self.sysimg_dir, "source.properties"), "w") as f:
            f.write("SystemImage.Abi=x86_64\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_auto_discover_cached_sysimg(self):
        with patch("glob.glob", return_value=[self.sysimg_dir]):
            discovered = find_cached_sysimg_dir()
            self.assertEqual(discovered, self.sysimg_dir)

    def test_missing_sysimg_actionable_error(self):
        class Args:
            name = "test-phone"
            sysimg_dir = None
            profile = "medium_phone"
            list_profiles = False
            ram = None
            cores = 4
            disk_size = None
            gpu = "auto"
            force = True
            json = True

        with patch("commands.create.find_cached_sysimg_dir", return_value=None):
            with patch("commands.create.print_result") as mock_print:
                with self.assertRaises(SystemExit):
                    run_create_avd(Args())
                self.assertTrue(mock_print.called)
                payload = mock_print.call_args[0][0]
                self.assertEqual(payload["status"], "error")
                self.assertIn("suggestion", payload)
                self.assertEqual(payload["suggestion"], "emu-dev-cli fetch-build system-image --latest")


if __name__ == "__main__":
    unittest.main()
