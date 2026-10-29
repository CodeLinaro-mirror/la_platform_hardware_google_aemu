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

"""Hermetic zero-patch DI unit tests for `commands.build_times` Command classes."""

import argparse
import gzip
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.build_times import (
    BuildTimesCommand,
    BuildTimesDiagnoseCommand,
    BuildTimesHistoryCommand,
    BuildTimesInspectCommand,
    register_parser,
)
from core.command import CommandValidationError
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_ARTIFACT,
)
from core.dispatcher import CliDispatcher


class _FakeBuildTimesArtifactService:
    """In-memory fake ArtifactService for build-times command tests."""

    def __init__(self, trace_events: List[Dict[str, Any]]) -> None:
        self.trace_events = trace_events
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
        if artifact_name == "BUILD_INFO":
            out_path.write_text(
                json.dumps(
                    {
                        "bid": build_id,
                        "target": target,
                        "branch": "emu-main-next",
                        "sync_start_time": 1000.0,
                        "sync_finish_time": 1015.0,
                        "target_start_time": 1015.0,
                        "target_finish_time": 1120.0,
                        "worknode": {
                            "creationTimeMillis": 990000,
                            "Parameter": [
                                {"name": "machineName", "value": "bot-测试_🚀_é-01"}
                            ],
                        },
                        "repo-dict": {"platform/hardware/google/aemu": "deadbeef"},
                    }
                ),
                encoding="utf-8",
            )
        else:
            with gzip.open(out_path, "wt", encoding="utf-8") as gz:
                json.dump({"traceEvents": self.trace_events}, gz, ensure_ascii=False)
        return out_path


class BuildTimesCommandsTest(unittest.TestCase):
    """Tests Command-pattern build-times subcommands with FakeServiceFactory."""

    def test_build_times_inspect_remote_uses_injected_artifact_service_with_unicode(
        self,
    ) -> None:
        fake_artifact = _FakeBuildTimesArtifactService(
            trace_events=[
                {
                    "cat": "build phase marker",
                    "name": "Launch Blaze",
                    "ph": "X",
                    "ts": 0,
                    "dur": 500_000,
                },
                {
                    "cat": "critical path component",
                    "name": "action '//emulator:unicode_测试_🚀_é'",
                    "ph": "X",
                    "ts": 0,
                    "dur": 2_000_000,
                },
                {
                    "cat": "action processing",
                    "name": "CppCompile",
                    "ph": "X",
                    "ts": 500_000,
                    "dur": 3_500_000,
                    "args": {
                        "mnemonic": "CppCompile",
                        "target": "//emulator/unicode:target_测试_🚀_é",
                    },
                },
            ]
        )
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesInspectCommand()
        args = argparse.Namespace(
            build_id="16612345",
            no_cache=True,
            branch="git_emu-main-next",
            target="emulator_linux_x64",
            top=5,
            token="fake-token",
            json=True,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(fake_artifact.fetch_calls), 2)
        self.assertIn("unicode_测试_🚀_é", res.markdown_table)

    def test_build_times_validation_guards(self) -> None:
        ctx = ExecutionContext.for_testing()
        with self.assertRaises(CommandValidationError):
            BuildTimesInspectCommand().validate(
                argparse.Namespace(
                    target="emulator_linux_x64",
                    top=0,
                ),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            BuildTimesHistoryCommand().validate(
                argparse.Namespace(target="emulator_linux_x64", limit=0),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            BuildTimesDiagnoseCommand().validate(
                argparse.Namespace(
                    target="emulator_linux_x64",
                    limit=1,
                    baseline=None,
                    regressed=None,
                ),
                ctx,
            )

    def test_build_times_parser_binds_command_instances_for_dispatcher(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--json", action="store_true")
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        parsed = parser.parse_args(
            ["--json", "build-times", "inspect", "--bid", "16612345"]
        )
        dispatcher = CliDispatcher()
        resolved = dispatcher.resolve_command(parsed)
        self.assertIsInstance(resolved, BuildTimesInspectCommand)
        self.assertEqual(BuildTimesCommand().name, "build-times")


if __name__ == "__main__":
    unittest.main()
