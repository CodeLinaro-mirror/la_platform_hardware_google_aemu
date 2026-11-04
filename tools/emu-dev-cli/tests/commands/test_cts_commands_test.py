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

"""Hermetic zero-patch DI unit tests for `commands.cts` Command classes."""

import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence
import unittest
import zipfile

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from clients.artifact_service import (
    FakeArtifactService,
    FakeHttpTransport,
    HttpStatusError,
)
from commands.cts import (
    ARCH_ARM64,
    ARCH_X86_64,
    CTS_VERIFIER_APK,
    CTS_VERIFIER_ZIP,
    CtsCommand,
    CtsRunVerifierCommand,
    discover_available_modules,
    find_verifier_scripts_dir,
    register_parser,
)
from core.command import STATUS_ERROR, STATUS_SUCCESS
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_ARTIFACT,
)
from core.dispatcher import CliDispatcher
from core.errors import ExecutionError, UsageError
from core.process import FakeProcessRunner, ProcessResult


def _make_cts_archive_bytes() -> bytes:
    """Creates a mock android-cts-verifier.zip containing CtsVerifier.apk and a companion APK."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"android-cts-verifier/{CTS_VERIFIER_APK}", b"FAKE_CTS_VERIFIER_APK")
        zf.writestr("android-cts-verifier/CtsVerifierCompanion.apk", b"FAKE_COMPANION_APK")
    return buf.getvalue()


class _FakeCtsArtifactService(FakeArtifactService):
    """In-memory fake ArtifactService for CTS command tests."""

    def __init__(self, archive_payload: bytes | None = None) -> None:
        super().__init__()
        self.payload = archive_payload if archive_payload is not None else _make_cts_archive_bytes()
        self.fetch_calls: List[Dict[str, Any]] = []

    def download_artifact(
        self,
        build_id: str,
        target: str,
        artifact: str,
        dest_dir: str | os.PathLike[str],
        *,
        branch: str | None = None,
        zip_entries: Sequence[str] | None = None,
        progress_callback: Any = None,
    ) -> Path:
        del zip_entries, progress_callback
        self.fetch_calls.append(
            {
                "build_id": str(build_id),
                "target": str(target),
                "artifact_name": str(artifact),
                "branch": branch,
            }
        )
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)
        out_path = dest_path / Path(artifact).name
        out_path.write_bytes(self.payload)
        return out_path

    def fetch_artifact(
        self,
        *,
        build_id: str,
        target: str,
        artifact_name: str,
        dest_dir: str | Path,
        **kwargs: Any,
    ) -> Path:
        return self.download_artifact(
            build_id=build_id,
            target=target,
            artifact=artifact_name,
            dest_dir=dest_dir,
            branch=kwargs.get("branch"),
        )


class CtsCommandsTest(unittest.TestCase):
    """Tests Command-pattern CTS subcommands with FakeServiceFactory and FakeProcessRunner."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name)
        # Set up a fake goldfish verifier directory structure
        self.verifier_dir = (
            self.root_path
            / "third_party"
            / "adt-infra"
            / "goldfish_test"
            / "xts"
            / "verifier"
        )
        self.verifier_dir.mkdir(parents=True, exist_ok=True)
        # Create sample test scripts
        (self.verifier_dir / "pass_audio_test.py").write_text("#!/usr/bin/env python3\npass\n")
        (self.verifier_dir / "pass_other_battery_saver_test.py").write_text("#!/usr/bin/env python3\npass\n")
        (self.verifier_dir / "run_audio_test.sh").write_text("#!/bin/bash\nexit 0\n")
        # Create automation_dev/test_builder.py inside verifier_dir
        self.automation_dev = self.verifier_dir / "automation_dev"
        self.automation_dev.mkdir(parents=True, exist_ok=True)
        (self.automation_dev / "test_builder.py").write_text("#!/usr/bin/env python3\npass\n")
        # Create verifier.bzl to mark the repo valid
        (self.verifier_dir.parent / "verifier.bzl").write_text("# bzl\n")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_context(
        self,
        runner: Optional[FakeProcessRunner] = None,
        artifact_service: Optional[Any] = None,
    ) -> ExecutionContext:
        p_runner = runner or FakeProcessRunner()
        art_service = artifact_service or _FakeCtsArtifactService()
        factory = FakeServiceFactory(
            process_runner=p_runner,
            services={SERVICE_ARTIFACT: art_service},
        )
        ctx = ExecutionContext.for_testing(factory=factory)
        ctx.config.source_directories["emu-main-next"] = str(self.root_path)
        return ctx

    def test_cts_run_verifier_validate_rejects_missing_mode(self) -> None:
        ctx = self._create_context()
        cmd = CtsRunVerifierCommand()
        with self.assertRaises(UsageError):
            cmd.validate(argparse.Namespace(module=None, all=False, list_modules=False, collect_test_module_names=False), ctx)

    def test_cts_discover_available_modules(self) -> None:
        modules = discover_available_modules(str(self.verifier_dir))
        self.assertIn("audio", modules)
        self.assertIn("other_battery_saver", modules)
        self.assertIn("battery_saver", modules)
        self.assertEqual(modules["audio"], "pass_audio_test.py")

        # Nonexistent dir returns empty dict
        self.assertEqual(discover_available_modules("/nonexistent/dir"), {})

    def test_cts_list_modules_result(self) -> None:
        ctx = self._create_context()
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(list_modules=True)
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("audio", res.data["modules"])
        self.assertIn("pass_audio_test.py", res.markdown_table)

    def test_cts_run_bazel_all(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=0, stdout="Bazel test suite passed\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=True,
            module=None,
            list_modules=False,
            collect_test_module_names=False,
            test_builder_mode=False,
            rbe=False,
            window=True,
            json=True,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("@goldfish_test//xts:ets-verifier", res.data["target"])

    def test_cts_run_bazel_single_module_clock_alias(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=0, stdout="Bazel clock test passed\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=False,
            module="clock",
            list_modules=False,
            collect_test_module_names=False,
            test_builder_mode=False,
            rbe=False,
            window=False,
            json=False,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("ClockTest", res.data["target"])

    def test_cts_run_bazel_rbe_mode(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=0, stdout="RBE test ok\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=True,
            module=None,
            list_modules=False,
            collect_test_module_names=False,
            test_builder_mode=False,
            rbe=True,
            window=True,
            json=True,
        )
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("--config=remote", res.data["command"])

    def test_cts_run_bazel_test_builder_mode(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=0, stdout="TestBuilder ok\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=False,
            module="screen_lock",
            list_modules=False,
            collect_test_module_names=False,
            test_builder_mode=True,
            rbe=False,
            window=True,
            json=True,
        )
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("--dev_mode", res.data["command"])

    def test_cts_run_bazel_collect_labels(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=0, stdout="Labels collected\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=False,
            module=None,
            list_modules=False,
            collect_test_module_names=True,
            test_builder_mode=False,
            rbe=False,
            window=True,
            json=True,
        )
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("--collect_tests", res.data["command"])

    def test_cts_run_bazel_failure_exit_code(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["bazel"],
            ProcessResult(args=("bazel",), returncode=1, stdout="Test failed\n", stderr="Error trace"),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=True,
            all=True,
            module=None,
            list_modules=False,
            collect_test_module_names=False,
            test_builder_mode=False,
            rbe=False,
            window=True,
            json=True,
        )
        res = cmd.execute(args, ctx)
        self.assertFalse(res.ok)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.status, STATUS_ERROR)

    def test_cts_collect_labels_direct(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            [sys.executable],
            ProcessResult(args=(sys.executable,), returncode=0, stdout="Collected 42 labels\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=False,
            collect_test_module_names=True,
            list_modules=False,
            module=None,
            all=False,
            json=True,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.data["mode"], "collect-test-module-names")

    def test_cts_collect_labels_direct_missing_builder_raises_error(self) -> None:
        runner = FakeProcessRunner()
        ctx = self._create_context(runner=runner)
        ctx.config.source_directories.clear()
        # Empty directory without test_builder.py
        empty_dir = self.root_path / "empty_verifier"
        empty_dir.mkdir(parents=True, exist_ok=True)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(empty_dir), "test"))
        args = argparse.Namespace(
            bazel=False,
            collect_test_module_names=True,
            list_modules=False,
            module=None,
            all=False,
            json=True,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_cts_direct_single_module_with_build_id_and_auto_serial(self) -> None:
        runner = FakeProcessRunner()
        # Mock adb devices returning emulator-5554
        runner.register_prefix(
            ["adb", "devices"],
            ProcessResult(
                args=("adb", "devices"),
                returncode=0,
                stdout="List of devices attached\nemulator-5554\tdevice\n",
                stderr="",
            ),
        )
        # Mock adb shell getprop returning x86_64
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(
                args=("adb", "shell", "getprop"),
                returncode=0,
                stdout="x86_64\n",
                stderr="",
            ),
        )
        # Mock adb install
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "install"],
            ProcessResult(args=("adb", "install"), returncode=0, stdout="Success\n", stderr=""),
        )
        # Mock adb shell settings/appops setup
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="", stderr=""),
        )
        # Mock python running pass_audio_test.py
        runner.register_prefix(
            [sys.executable, str(self.verifier_dir / "pass_audio_test.py")],
            ProcessResult(
                args=(sys.executable,),
                returncode=0,
                stdout="Audio test PASS\n",
                stderr="",
            ),
        )

        fake_artifact = _FakeCtsArtifactService()
        ctx = self._create_context(runner=runner, artifact_service=fake_artifact)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            build_id="16654321",
            branch="trunk-release",
            target=None,
            serial=None,
            force_download=True,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
            json=True,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.data["module"], "audio")
        self.assertEqual(res.data["device_serial"], "emulator-5554")
        self.assertEqual(len(fake_artifact.fetch_calls), 1)
        self.assertEqual(fake_artifact.fetch_calls[0]["build_id"], "16654321")

    def test_cts_direct_single_module_public_cdn_download(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="x86_64\n", stderr=""),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "install"],
            ProcessResult(args=("adb", "install"), returncode=0, stdout="Success\n", stderr=""),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="", stderr=""),
        )
        runner.register_prefix(
            [sys.executable, str(self.verifier_dir / "pass_audio_test.py")],
            ProcessResult(args=(sys.executable,), returncode=0, stdout="Audio PASS\n", stderr=""),
        )

        fake_http = FakeHttpTransport()
        fake_http.register_download(
            "https://dl.google.com/dl/android/cts/", _make_cts_archive_bytes()
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            build_id=None,
            serial="emulator-5554",
            force_download=True,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
            json=False,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("public CDN", res.data["source_mode"])
        self.assertTrue(any("dl.google.com" in c.url for c in fake_http.calls))

    def test_cts_direct_public_cdn_download_http_error(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="x86_64\n", stderr=""),
        )
        fake_http = FakeHttpTransport()
        fake_http.register_download(
            "https://dl.google.com/dl/android/cts/",
            HttpStatusError(404, "Not Found", body_text="Missing archive"),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            build_id=None,
            serial="emulator-5554",
            force_download=True,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
            json=True,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_cts_direct_run_all_modules(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="arm64-v8a\n", stderr=""),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "install"],
            ProcessResult(args=("adb", "install"), returncode=0, stdout="Success\n", stderr=""),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="", stderr=""),
        )
        # Mock module scripts
        runner.register_prefix(
            [sys.executable, str(self.verifier_dir / "pass_audio_test.py")],
            ProcessResult(args=(sys.executable,), returncode=0, stdout="Audio OK\n", stderr=""),
        )
        runner.register_prefix(
            [sys.executable, str(self.verifier_dir / "pass_other_battery_saver_test.py")],
            ProcessResult(args=(sys.executable,), returncode=0, stdout="Battery OK\n", stderr=""),
        )

        fake_http = FakeHttpTransport()
        fake_http.register_download("https://dl.google.com/dl/android/cts/", _make_cts_archive_bytes())
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            all=True,
            module=None,
            build_id=None,
            serial="emulator-5554",
            force_download=False,
            list_modules=False,
            collect_test_module_names=False,
            json=False,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.data["total_modules"], 2)
        self.assertEqual(res.data["failed_modules"], 0)

    def test_cts_direct_adb_install_warning(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="x86_64\n", stderr=""),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "install"],
            ProcessResult(
                args=("adb", "install"),
                returncode=1,
                stdout="",
                stderr="Failure [INSTALL_FAILED_OLDER_SDK: Failed to parse...]",
            ),
        )
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="", stderr=""),
        )
        runner.register_prefix(
            [sys.executable, str(self.verifier_dir / "pass_audio_test.py")],
            ProcessResult(args=(sys.executable,), returncode=0, stdout="Pass\n", stderr=""),
        )

        fake_http = FakeHttpTransport()
        fake_http.register_download("https://dl.google.com/dl/android/cts/", _make_cts_archive_bytes())
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            build_id=None,
            serial="emulator-5554",
            force_download=False,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
            json=False,
        )
        # Should continue and complete despite install warning
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)

    def test_cts_direct_no_online_device_raises_error(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "devices"],
            ProcessResult(args=("adb", "devices"), returncode=0, stdout="List of devices attached\n\n", stderr=""),
        )
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(scripts_dir_locator=lambda: (str(self.verifier_dir), "test"))
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            serial=None,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_cts_direct_unknown_module_raises_usage_error(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="x86_64\n", stderr=""),
        )
        fake_http = FakeHttpTransport()
        fake_http.register_download("https://dl.google.com/dl/android/cts/", _make_cts_archive_bytes())
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            module="nonexistent_module_xyz",
            serial="emulator-5554",
            all=False,
            list_modules=False,
            collect_test_module_names=False,
        )
        with self.assertRaises(UsageError):
            cmd.execute(args, ctx)

    def test_cts_direct_corrupt_zip_archive_raises_execution_error(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["adb", "-s", "emulator-5554", "shell", "getprop", "ro.product.cpu.abi"],
            ProcessResult(args=("adb", "shell"), returncode=0, stdout="x86_64\n", stderr=""),
        )
        fake_http = FakeHttpTransport()
        fake_http.register_download("https://dl.google.com/dl/android/cts/", b"NOT_A_VALID_ZIP")
        ctx = self._create_context(runner=runner)
        cmd = CtsRunVerifierCommand(
            http_transport=fake_http,
            scripts_dir_locator=lambda: (str(self.verifier_dir), "test"),
        )
        args = argparse.Namespace(
            bazel=False,
            module="audio",
            serial="emulator-5554",
            force_download=True,
            all=False,
            list_modules=False,
            collect_test_module_names=False,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_cts_parser_binds_command_instances_for_dispatcher(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--json", action="store_true")
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        parsed = parser.parse_args(
            ["--json", "cts", "run-cts-verifier", "--bid", "16654321", "--all"]
        )
        dispatcher = CliDispatcher()
        resolved = dispatcher.resolve_command(parsed)
        self.assertIsInstance(resolved, CtsRunVerifierCommand)
        self.assertEqual(CtsCommand().name, "cts")

    def test_cts_command_group_execute(self) -> None:
        ctx = self._create_context()
        cmd = CtsCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)
        parsed = parser.parse_args([])
        res = cmd.execute(parsed, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
