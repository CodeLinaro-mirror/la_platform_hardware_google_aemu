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

from commands.crash.reproduce import (
    CrashReproduceCommand,
    extract_reproduce_plan,
    register_reproduce_parser,
    resolve_qemu_engine_name,
)
from core.context import ExecutionContext


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
        self.assertIsInstance(
            getattr(args, "_command_instance"), CrashReproduceCommand
        )

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

    def test_run_reproduce_dry_run(self):
        """Tests run_reproduce in dry-run mode."""

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
                work_dir=tmp_dir,
            )

            ctx = ExecutionContext.for_testing()
            cmd = CrashReproduceCommand()
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(res.data["status"], "success")
            self.assertEqual(res.data["plan"]["crash_id"], "05d8356e2f800000")

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
    ):
        """Tests that LLDB execution cleans up the entire emulator process group."""
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
                work_dir=tmp_dir,
            )

            ctx = ExecutionContext.for_testing()
            cmd = CrashReproduceCommand()
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)

            # Ensure Popen was started with start_new_session=True on POSIX
            if os.name != "nt":
                _, kwargs = mock_popen.call_args
                self.assertTrue(kwargs.get("start_new_session"))
                mock_killpg.assert_called_once()

    def test_run_reproduce_non_lldb_with_binary(self):
        """Tests non-debugger execution with existing emulator binary."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-15953806"},
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")
            ctx = ExecutionContext.for_testing()
            emu_dir = ctx.config.cache_dir / "emulator-15953806" / "extracted" / "emulator"
            emu_dir.mkdir(parents=True, exist_ok=True)
            emu_bin = emu_dir / "emulator"
            emu_bin.write_text("#!/bin/sh\n", encoding="utf-8")

            cmd = CrashReproduceCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_avd",
                lldb=False,
                dry_run=False,
                json=False,
                extra_args=None,
                work_dir=tmp_dir,
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(len(ctx.process_runner.calls), 1)
            self.assertIn(str(emu_bin), ctx.process_runner.calls[0].args)

    def test_run_reproduce_downloads_binary_when_missing(self):
        """Tests downloading the emulator binary when not cached."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-99999999"},
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")
            ctx = ExecutionContext.for_testing()
            mock_art = MagicMock()
            from core.context import SERVICE_ARTIFACT
            ctx.services.register_instance(SERVICE_ARTIFACT, mock_art)

            cmd = CrashReproduceCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_avd",
                lldb=False,
                dry_run=False,
                json=False,
                extra_args=None,
                work_dir=tmp_dir,
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            mock_art.download_artifact.assert_called_once()

    def test_run_reproduce_handles_download_artifact_failure(self):
        """Tests graceful handling when downloading binary fails."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-88888888"},
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_file = Path(tmp_dir) / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")
            ctx = ExecutionContext.for_testing()
            from core.context import SERVICE_ARTIFACT
            mock_art = MagicMock()
            mock_art.download_artifact.side_effect = RuntimeError("network error")
            ctx.services.register_instance(SERVICE_ARTIFACT, mock_art)

            cmd = CrashReproduceCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_avd",
                lldb=False,
                dry_run=False,
                json=False,
                extra_args=None,
                work_dir=tmp_dir,
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)

    @patch("commands.crash.reproduce.CrashAdvisorRunner")
    def test_run_reproduce_triggers_advisor_when_metadata_missing(self, mock_runner_cls):
        """Tests that CrashAdvisorRunner is called if metadata.json is initially missing."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-15953806"},
                "os": {"Name": "Linux", "Version": "6.17.0"},
                "cpu": {"Architecture": "amd64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_file = Path(tmp_dir) / "metadata.json"
            def side_effect(*args, **kwargs):
                meta_file.write_text(json.dumps(metadata), encoding="utf-8")
            mock_runner = MagicMock()
            mock_runner.run.side_effect = side_effect
            mock_runner_cls.return_value = mock_runner

            ctx = ExecutionContext.for_testing()
            cmd = CrashReproduceCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="test-token",
                avd="repro_avd",
                lldb=False,
                dry_run=True,
                json=False,
                extra_args=None,
                work_dir=tmp_dir,
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            mock_runner.run.assert_called_once()

    def test_root_json_flag_is_not_shadowed_by_subparser(self):
        """Verifies `emu-dev-cli --json crash reproduce <id>` preserves `args.json == True`."""
        from commands import crash

        root_parser = argparse.ArgumentParser()
        root_parser.add_argument("--json", action="store_true")
        subparsers = root_parser.add_subparsers(dest="subcommand")
        crash.register_parser(subparsers)

        parsed = root_parser.parse_args(
            ["--json", "crash", "reproduce", "05d8356e2f800000"]
        )
        self.assertTrue(parsed.json)

    def test_run_reproduce_extracts_emulator_zip_entries_and_executes_binary_with_unicode(
        self,
    ):
        """Tests that `crash reproduce` extracts `emulator/*` from the platform zip and executes the binary."""
        metadata = {
            "report_proto": {
                "ReportID": "05d8356e2f800000",
                "product": {"Name": "AndroidEmulator", "Version": "35.2.1-15950000"},
                "os": {"Name": "Mac OS X", "Version": "15.1"},
                "cpu": {"Architecture": "arm64"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            unicode_work = Path(tmp_dir) / "测试_🚀_é"
            unicode_work.mkdir(parents=True)
            meta_file = unicode_work / "metadata.json"
            meta_file.write_text(json.dumps(metadata), encoding="utf-8")

            ctx = ExecutionContext.for_testing(root_dir=unicode_work)
            ctx.fake_artifact_service.seed_artifact(
                build_id="15950000",
                target="emulator_mac_aarch64",
                artifact_name="sdk-repo-darwin_aarch64-emulator-15950000.zip",
                zip_entries={
                    "emulator/emulator": "#!/bin/sh\necho ok\n",
                    "emulator/source.properties": "Pkg.Revision=35.2.1\n",
                },
            )
            cmd = CrashReproduceCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                avd="repro_测试_🚀_é",
                lldb=False,
                dry_run=False,
                json=False,
                extra_args=None,
                work_dir=str(unicode_work),
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            expected_bin = (
                unicode_work
                / "cache"
                / "emulator-15950000"
                / "extracted"
                / "emulator"
                / "emulator"
            )
            self.assertTrue(expected_bin.is_file())
            self.assertEqual(len(ctx.process_runner.calls), 1)
            self.assertEqual(ctx.process_runner.calls[0].args[0], str(expected_bin))


if __name__ == "__main__":
    unittest.main()
