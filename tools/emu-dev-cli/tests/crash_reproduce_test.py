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

"""Unit tests for commands.crash.reproduce module."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.reproduce import (
    extract_reproduce_plan,
    register_reproduce_parser,
    resolve_qemu_engine_name,
    run_reproduce,
)


class CrashReproduceTest(unittest.TestCase):
    """Tests for crash reproduce parser registration and execution pipeline."""

    def test_register_reproduce_parser(self):
        """Tests ArgumentParser registration for `reproduce` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_reproduce_parser(subparsers)

        args = parser.parse_args(
            ["reproduce", "05d8356e2f800000", "--lldb", "--dry-run"]
        )
        self.assertEqual(args.crash_cmd, "reproduce")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertTrue(args.lldb)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.func, run_reproduce)

    def test_resolve_qemu_engine_name(self):
        """Tests resolving QEMU engine binary name from architecture."""
        self.assertEqual(
            resolve_qemu_engine_name("amd64", "Linux"), "qemu-system-x86_64"
        )
        self.assertEqual(
            resolve_qemu_engine_name("x86_64", "Linux"), "qemu-system-x86_64"
        )
        self.assertEqual(
            resolve_qemu_engine_name("arm64", "Linux"), "qemu-system-aarch64"
        )
        self.assertEqual(
            resolve_qemu_engine_name("aarch64", "macOS"), "qemu-system-aarch64"
        )
        self.assertEqual(resolve_qemu_engine_name("arm", "Linux"), "qemu-system-armel")
        self.assertEqual(resolve_qemu_engine_name("i386", "Linux"), "qemu-system-i386")
        self.assertEqual(
            resolve_qemu_engine_name("x86_64", "Windows"), "qemu-system-x86_64.exe"
        )

    def test_extract_reproduce_plan_from_metadata(self):
        """Tests extracting reproduction parameters and flags from metadata.json."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {
                    "Name": "AndroidEmulator",
                    "Version": "35.2.1-15953806",
                },
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
                "productdata": [
                    {
                        "Key": "commandline",
                        "Value": "-gpu swiftshader_indirect -no-snapshot -no-audio",
                    },
                    {"Key": "internal-msg", "Value": "hanging thread"},
                ],
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")

            plan = extract_reproduce_plan(
                crash_id="05d8356e2f800000",
                metadata_path=meta_file,
                avd_override="test_avd",
                debugger="lldb",
                extra_args=["-verbose"],
            )

            self.assertEqual(plan["crash_id"], "05d8356e2f800000")
            self.assertEqual(plan["build_id"], "15953806")
            self.assertEqual(plan["build_target"], "emulator_linux_x64")
            self.assertEqual(plan["qemu_engine"], "qemu-system-x86_64")
            self.assertEqual(plan["avd"], "test_avd")
            self.assertEqual(plan["debugger"], "lldb")
            self.assertEqual(
                plan["lldb_command"],
                ["lldb", "-n", "qemu-system-x86_64", "--wait-for"],
            )
            self.assertIn("-gpu", plan["flags"])
            self.assertIn("swiftshader_indirect", plan["flags"])
            self.assertIn("-no-snapshot", plan["flags"])
            self.assertIn("-verbose", plan["flags"])

    @patch("commands.crash.reproduce.run_crashadvisor_bazel")
    @patch("commands.crash.reproduce.get_crashadvisor_sandbox_dir")
    def test_run_reproduce_dry_run(self, mock_get_sandbox, mock_run_bazel):
        """Tests run_reproduce in dry-run mode."""
        mock_run_bazel.return_value = MagicMock(returncode=0)

        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {
                    "Name": "AndroidEmulator",
                    "Version": "35.2.1-15953806",
                },
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
                "productdata": [
                    {"Key": "commandline", "Value": "-gpu swiftshader_indirect"}
                ],
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_get_sandbox.return_value = tmp_dir
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_avd",
                lldb=True,
                dry_run=True,
                json=False,
                extra_args=None,
            )

            with self.assertRaises(SystemExit) as cm:
                run_reproduce(args)
            self.assertEqual(cm.exception.code, 0)

    @patch("commands.crash.reproduce.run_crashadvisor_bazel")
    @patch("commands.crash.reproduce.get_crashadvisor_sandbox_dir")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch("os.killpg")
    @patch("os.getpgid")
    def test_run_reproduce_lldb_process_group_cleanup(
        self,
        mock_getpgid,
        mock_killpg,
        mock_subproc_run,
        mock_popen,
        mock_get_sandbox,
        mock_run_bazel,
    ):
        """Tests that LLDB execution cleans up the entire emulator process group."""
        mock_run_bazel.return_value = MagicMock(returncode=0)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc
        mock_getpgid.return_value = 9999

        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-15953806"},
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_get_sandbox.return_value = tmp_dir
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_avd",
                lldb=True,
                dry_run=False,
                json=False,
                extra_args=None,
            )

            run_reproduce(args)

            # Ensure Popen was started with start_new_session=True on POSIX
            if os.name != "nt":
                _, kwargs = mock_popen.call_args
                self.assertTrue(kwargs.get("start_new_session"))
                mock_killpg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
