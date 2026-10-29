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
from pathlib import Path
import sys
from typing import Any, Dict, List
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.cts import (
    CtsCommand,
    CtsRunVerifierCommand,
    fetch_from_android_build,
    register_parser,
)
from core.command import CommandValidationError
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_ARTIFACT,
)
from core.dispatcher import CliDispatcher
from core.process import FakeProcessRunner, ProcessResult


class _FakeCtsArtifactService:
    """In-memory fake ArtifactService for CTS command tests."""

    def __init__(self) -> None:
        self.fetch_calls: List[Dict[str, Any]] = []

    def fetch_artifact(
        self,
        *,
        build_id: str,
        target: str,
        artifact_name: str,
        dest_dir: str | Path,
        output_filename: str | None = None,
        **kwargs: Any,
    ) -> Path:
        del kwargs
        self.fetch_calls.append(
            {
                "build_id": build_id,
                "target": target,
                "artifact_name": artifact_name,
            }
        )
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)
        out_path = dest_path / (output_filename or Path(artifact_name).name)
        out_path.write_bytes(b"FAKE_CTS_APK")
        return out_path


class CtsCommandsTest(unittest.TestCase):
    """Tests Command-pattern CTS subcommands with FakeServiceFactory and FakeProcessRunner."""

    def test_cts_run_verifier_validate_rejects_invalid_timeout_and_bid(self) -> None:
        ctx = ExecutionContext.for_testing()
        cmd = CtsRunVerifierCommand()
        with self.assertRaises(CommandValidationError):
            cmd.validate(
                argparse.Namespace(timeout=0, bid="12345"),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            cmd.validate(
                argparse.Namespace(timeout=60, bid="not-numeric"),
                ctx,
            )

    def test_cts_fetch_from_android_build_uses_injected_artifact_service_and_runner_with_unicode(
        self,
    ) -> None:
        import tempfile

        fake_artifact = _FakeCtsArtifactService()
        fake_runner = FakeProcessRunner()
        fake_runner.register_prefix(
            ["/google/data/ro/projects/android/fetch_artifact"],
            ProcessResult(
                args=("fetch_artifact",),
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            unicode_dir = Path(tmp_dir) / "cts_测试_🚀_é"
            ok = fetch_from_android_build(
                "16654321",
                "test_suites_x86_64-trunk_staging",
                "android-cts-verifier.zip",
                str(unicode_dir),
                artifact_service=fake_artifact,
                runner=fake_runner,
            )
            self.assertTrue(ok)
            self.assertEqual(len(fake_artifact.fetch_calls), 1)
            self.assertEqual(fake_artifact.fetch_calls[0]["build_id"], "16654321")

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


if __name__ == "__main__":
    unittest.main()
