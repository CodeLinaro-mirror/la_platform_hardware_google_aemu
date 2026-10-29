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
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from clients.artifact_service import (
    ArtifactMetadata,
    ArtifactService,
    BuildMetadata,
    FakeArtifactService,
)
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
from core.errors import ExecutionError, UsageError
from core.process import FakeProcessRunner, ProcessResult
from lib.build_profile_analyzer import (
    BuildProfileMetrics,
    CriticalPathItem,
    MnemonicSummary,
    StageProfileMetrics,
)


class _FakeBuildTimesArtifactService(FakeArtifactService):
    """In-memory fake ArtifactService for build-times command tests."""

    def __init__(
        self,
        trace_events: Optional[List[Dict[str, Any]]] = None,
        build_info_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self.trace_events = trace_events or [
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
        self.build_info_extra = dict(build_info_extra or {})
        self.fetch_calls: List[Dict[str, Any]] = []

    def resolve_build_id(
        self, branch: str, target: str, build_id: str | None = None
    ) -> str:
        del branch, target
        return build_id or "16612345"

    def get_build(self, build_id: str, target: str) -> Optional[BuildMetadata]:
        return BuildMetadata(
            build_id=str(build_id),
            target=target,
            branch="git_emu-main-next",
            status="COMPLETE",
            successful=True,
            machine_name="bot-测试_🚀_é-01",
            creation_timestamp="2026-10-29T10:00:00Z",
            completion_timestamp="2026-10-29T10:30:00Z",
        )

    def list_artifacts(
        self,
        build_id: str,
        target: str,
        *,
        name_regex: str | None = None,
    ) -> list[ArtifactMetadata]:
        del build_id, target, name_regex
        return [
            ArtifactMetadata(name="BUILD_INFO", size_bytes=100),
            ArtifactMetadata(name="logs/bazel/command.profile.gz", size_bytes=200),
        ]

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
        del branch, zip_entries, progress_callback
        self.fetch_calls.append(
            {
                "build_id": str(build_id),
                "target": str(target),
                "artifact_name": str(artifact),
            }
        )
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)
        out_path = dest_path / Path(artifact).name
        if artifact == "BUILD_INFO":
            data = {
                "bid": str(build_id),
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
            data.update(self.build_info_extra)
            out_path.write_text(json.dumps(data), encoding="utf-8")
        else:
            with gzip.open(out_path, "wt", encoding="utf-8") as gz:
                json.dump({"traceEvents": self.trace_events}, gz, ensure_ascii=False)
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
        del kwargs
        return self.download_artifact(build_id, target, artifact_name, dest_dir)


class BuildTimesCommandsTest(unittest.TestCase):
    """Tests Command-pattern build-times subcommands with FakeServiceFactory."""

    def test_build_times_inspect_remote_uses_injected_artifact_service_with_unicode(
        self,
    ) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
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
        self.assertIn("16612345", res.summary)

    def test_build_times_inspect_multi_target_markdown_summary(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesInspectCommand()
        args = argparse.Namespace(
            build_id="16612345",
            no_cache=True,
            branch="git_emu-main-next",
            target="linux,mac",
            top=5,
            token=None,
            json=False,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("Multi-Target Build Profile Summary", res.markdown_table)
        self.assertIn("emulator_linux_x64", res.markdown_table)
        self.assertIn("emulator_mac_aarch64", res.markdown_table)
        self.assertEqual(len(res.data["profiles"]), 2)

    def test_build_times_inspect_resolves_latest_build_id_when_omitted(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesInspectCommand()
        args = argparse.Namespace(
            build_id=None,
            no_cache=True,
            branch="git_emu-main-next",
            target="emulator_linux_x64",
            top=3,
            token=None,
            json=True,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.data["build_id"], "16612345")

    def test_build_times_inspect_fails_when_all_targets_fail(self) -> None:
        def failing_analyzer(*args: Any, **kwargs: Any) -> BuildProfileMetrics:
            del args, kwargs
            raise RuntimeError("Build analyzer failure simulation")

        cmd = BuildTimesInspectCommand(analyze_build=failing_analyzer)
        ctx = ExecutionContext.for_testing()
        args = argparse.Namespace(
            build_id="16612345",
            no_cache=True,
            branch="git_emu-main-next",
            target="emulator_linux_x64",
            top=5,
            token=None,
            json=True,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_build_times_inspect_custom_token_service_factory_is_called(self) -> None:
        called: list[str] = []

        def custom_factory(ctx: ExecutionContext, token: str) -> ArtifactService:
            del ctx
            called.append(token)
            return _FakeBuildTimesArtifactService()

        cmd = BuildTimesInspectCommand(token_service_factory=custom_factory)
        ctx = ExecutionContext.for_testing()
        args = argparse.Namespace(
            build_id="16612345",
            no_cache=True,
            branch="git_emu-main-next",
            target="emulator_linux_x64",
            top=5,
            token="secret-token-123",
            json=True,
        )
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(called, ["secret-token-123"])

    def test_build_times_history_empty(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        # No builds in fake_artifact.list_builds -> empty history
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesHistoryCommand()
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=5,
            no_cache=True,
            token=None,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("No builds found on", res.markdown_table)

    def test_build_times_history_with_builds(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="100",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
                creation_timestamp="2026-10-29T10:00:00Z",
                completion_timestamp="2026-10-29T10:20:00Z",
                machine_name="bot-01",
            )
        )
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="101",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
                creation_timestamp="2026-10-29T11:00:00Z",
                completion_timestamp="2026-10-29T11:35:00Z",
                machine_name="bot-02",
            )
        )
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesHistoryCommand()
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=10,
            no_cache=True,
            token=None,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("Build Time History", res.markdown_table)
        self.assertIn("100", res.markdown_table)
        self.assertIn("101", res.markdown_table)
        self.assertIn("emulator_linux_x64", res.data["history"])

    def test_build_times_diagnose_explicit_baseline_and_regressed(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        runner = FakeProcessRunner()
        # Mock git log for repo revision changes
        runner.register_prefix(
            ["git", "log", "--oneline"],
            ProcessResult(
                args=("git", "log"),
                returncode=0,
                stdout="abc1234 Add heavy template instantiation\ndef5678 Refactor header\n",
                stderr="",
            ),
        )
        factory = FakeServiceFactory(
            process_runner=runner,
            services={SERVICE_ARTIFACT: fake_artifact},
        )
        ctx = ExecutionContext.for_testing(factory=factory)

        def custom_analyzer(
            build_id: str, target: str, **kwargs: Any
        ) -> BuildProfileMetrics:
            del kwargs
            is_reg = str(build_id) == "200"
            wall = 500.0 if is_reg else 250.0
            return BuildProfileMetrics(
                build_id=str(build_id),
                target=target,
                branch="git_emu-main-next",
                total_ci_sec=wall + 50.0,
                queue_sec=10.0,
                sync_sec=20.0,
                build_py_sec=wall + 20.0,
                bazel_wall_sec=wall,
                phase_load_analyze_sec=30.0,
                phase_execute_sec=wall - 30.0,
                critical_path_sec=180.0 if is_reg else 100.0,
                machine_name="host-reg" if is_reg else "host-base",
                stages=[
                    StageProfileMetrics(
                        stage_id="release",
                        stage_name="Release Build",
                        artifact_name="logs/bazel/command.profile.gz",
                        bazel_wall_sec=wall,
                        critical_path_sec=180.0 if is_reg else 100.0,
                        critical_path_items=[
                            CriticalPathItem(
                                name="action '//emulator:heavy_compile'",
                                duration_sec=120.0 if is_reg else 40.0,
                                pct_of_critical_path=66.0,
                                stage_id="release",
                            )
                        ],
                        slowest_actions=[
                            {
                                "name": "action '//emulator:heavy_compile'",
                                "duration_sec": 120.0 if is_reg else 40.0,
                                "mnemonic": "CppCompile",
                                "stage_id": "release",
                            }
                        ],
                        mnemonics=[
                            MnemonicSummary(
                                mnemonic="CppCompile",
                                count=50 if is_reg else 40,
                                total_sec=300.0 if is_reg else 150.0,
                                avg_sec=6.0 if is_reg else 3.75,
                                max_sec=120.0 if is_reg else 40.0,
                                max_action_name="//emulator:heavy_compile",
                            )
                        ],
                    )
                ],
                critical_path_items=[
                    CriticalPathItem(
                        name="action '//emulator:heavy_compile'",
                        duration_sec=120.0 if is_reg else 40.0,
                        pct_of_critical_path=66.0,
                        stage_id="release",
                    )
                ],
                mnemonics=[
                    MnemonicSummary(
                        mnemonic="CppCompile",
                        count=50 if is_reg else 40,
                        total_sec=300.0 if is_reg else 150.0,
                        avg_sec=6.0 if is_reg else 3.75,
                        max_sec=120.0 if is_reg else 40.0,
                        max_action_name="//emulator:heavy_compile",
                    )
                ],
                repo_revisions={"platform/hardware/google/aemu": "def5678" if is_reg else "abc1234"},
                repo_paths={"platform/hardware/google/aemu": "hardware/google/aemu"},
                profile_available=True,
            )

        cmd = BuildTimesDiagnoseCommand(analyze_build=custom_analyzer)
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=10,
            baseline="100",
            regressed="200",
            no_cache=True,
            token=None,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("Build Time Regression Diagnosis", res.markdown_table)
        self.assertIn("Four-Layer Timing Delta", res.markdown_table)
        self.assertIn("Per-Stage Bazel Profile Comparison", res.markdown_table)
        self.assertIn("Critical Path Action Deltas", res.markdown_table)
        self.assertIn("Action Mnemonic Regressions", res.markdown_table)
        self.assertIn("Git Repository Changes", res.markdown_table)
        self.assertIn("CppCompile", res.markdown_table)

    def test_build_times_diagnose_auto_detect_from_history(self) -> None:
        def custom_analyzer(
            build_id: str, target: str, **kwargs: Any
        ) -> BuildProfileMetrics:
            del kwargs
            is_reg = str(build_id) == "102"
            wall = 600.0 if is_reg else 200.0
            return BuildProfileMetrics(
                build_id=str(build_id),
                target=target,
                branch="git_emu-main-next",
                total_ci_sec=wall + 50.0,
                bazel_wall_sec=wall,
                critical_path_sec=100.0,
                creation_time="2026-10-29T10:00:00Z",
                stages=[
                    StageProfileMetrics(
                        stage_id="release",
                        stage_name="Release",
                        artifact_name="logs/bazel/command.profile.gz",
                        bazel_wall_sec=wall,
                    )
                ],
                profile_available=True,
            )

        fake_artifact = _FakeBuildTimesArtifactService()
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="101",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="102",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesDiagnoseCommand(analyze_build=custom_analyzer)
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=5,
            baseline=None,
            regressed=None,
            no_cache=True,
            token=None,
        )
        cmd.validate(args, ctx)
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        self.assertIn("102", res.markdown_table)
        self.assertIn("101", res.markdown_table)

    def test_build_times_diagnose_fails_when_insufficient_history(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="101",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesDiagnoseCommand()
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=5,
            baseline=None,
            regressed=None,
            no_cache=True,
            token=None,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_build_times_diagnose_fails_when_baseline_equals_regressed(self) -> None:
        fake_artifact = _FakeBuildTimesArtifactService()
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="101",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        fake_artifact.seed_build(
            BuildMetadata(
                build_id="102",
                target="emulator_linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        factory = FakeServiceFactory(services={SERVICE_ARTIFACT: fake_artifact})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = BuildTimesDiagnoseCommand()
        args = argparse.Namespace(
            target="emulator_linux_x64",
            branch="git_emu-main-next",
            limit=5,
            baseline="101",
            regressed="101",
            no_cache=True,
            token=None,
        )
        with self.assertRaises(ExecutionError):
            cmd.execute(args, ctx)

    def test_build_times_validation_guards(self) -> None:
        ctx = ExecutionContext.for_testing()
        with self.assertRaises(UsageError):
            BuildTimesInspectCommand().validate(
                argparse.Namespace(
                    target="emulator_linux_x64",
                    top=0,
                ),
                ctx,
            )
        with self.assertRaises(UsageError):
            BuildTimesInspectCommand().validate(
                argparse.Namespace(
                    target="invalid_target_xyz",
                    top=5,
                ),
                ctx,
            )
        with self.assertRaises(UsageError):
            BuildTimesHistoryCommand().validate(
                argparse.Namespace(target="emulator_linux_x64", limit=0),
                ctx,
            )
        with self.assertRaises(UsageError):
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

    def test_build_times_command_group_execute(self) -> None:
        ctx = ExecutionContext.for_testing()
        cmd = BuildTimesCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)
        parsed = parser.parse_args([])
        res = cmd.execute(parsed, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
