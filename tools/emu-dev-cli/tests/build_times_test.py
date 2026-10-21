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

"""Unit tests for build-times profile analyzer and CLI commands."""

import argparse
import gzip
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from commands import build_times
from lib.build_profile_analyzer import (
    SUPPORTED_BUILD_TARGETS,
    BuildProfileMetrics,
    CriticalPathItem,
    MnemonicSummary,
    StageProfileMetrics,
    analyze_single_build,
    annotate_regressions,
    compare_build_profiles,
    discover_bazel_profile_artifacts,
    parse_bazel_profile,
    parse_build_info,
    resolve_build_targets,
    resolve_stage_metadata,
)


class BuildTimesAnalyzerTest(unittest.TestCase):
    """Tests for src/lib/build_profile_analyzer.py and src/commands/build_times."""

    def test_resolve_build_targets_all_eight_supported(self):
        all_targets = resolve_build_targets("all")
        self.assertEqual(len(all_targets), 8)
        for expected in (
            "emulator_linux_x64",
            "emulator_linux_x64_asan",
            "emulator_linux_x64_debug",
            "emulator_linux_x64_tsan",
            "emulator_mac_aarch64",
            "emulator_mac_aarch64_debug",
            "emulator_windows_x64",
            "emulator_windows_x64_debug",
        ):
            self.assertIn(expected, all_targets)
            self.assertEqual(resolve_build_targets(expected), [expected])

    def test_resolve_build_targets_aliases_and_csv(self):
        self.assertEqual(resolve_build_targets("mac"), ["emulator_mac_aarch64"])
        self.assertEqual(resolve_build_targets("mac_arm"), ["emulator_mac_aarch64"])
        self.assertEqual(
            resolve_build_targets("linux,tsan,windows"),
            ["emulator_linux_x64", "emulator_linux_x64_tsan", "emulator_windows_x64"],
        )
        with self.assertRaises(ValueError):
            resolve_build_targets("nonexistent_target")

    def test_parse_bazel_profile_gzipped(self):
        sample_profile = {
            "otherData": {
                "bazel_version": "release 9.1.1rc1",
                "build_id": "test-uuid",
            },
            "traceEvents": [
                {"cat": "build phase marker", "name": "Launch Blaze", "ph": "X", "ts": -3000000, "dur": 3000000},
                {"cat": "build phase marker", "name": "Initialize command", "ph": "i", "ts": 0},
                {"cat": "build phase marker", "name": "Evaluate target patterns", "ph": "i", "ts": 2000000},
                {
                    "cat": "build phase marker",
                    "name": "Load, analyze dependencies and build artifacts",
                    "ph": "i",
                    "ts": 5000000,
                },
                {
                    "cat": "Fetching repository",
                    "name": "@@qt_prebuilt+",
                    "ph": "X",
                    "ts": 6000000,
                    "dur": 4000000,
                },
                {
                    "cat": "action processing",
                    "name": "Compiling foo.cc",
                    "ph": "X",
                    "ts": 15000000,
                    "dur": 25000000,
                    "args": {"mnemonic": "CppCompile", "target": "//foo:bar"},
                },
                {
                    "cat": "action processing",
                    "name": "Testing @@goldfish+//emulator/libs/grpc_client:emulator_grpc_client_test",
                    "ph": "X",
                    "ts": 40000000,
                    "dur": 1800000000,
                    "args": {"mnemonic": "TestRunner", "target": "//emulator:test"},
                },
                {
                    "cat": "critical path component",
                    "name": "action 'Compiling foo.cc'",
                    "ph": "X",
                    "ts": 15000000,
                    "dur": 25000000,
                },
                {
                    "cat": "critical path component",
                    "name": "action 'Testing @@goldfish+//emulator/libs/grpc_client:emulator_grpc_client_test'",
                    "ph": "X",
                    "ts": 40000000,
                    "dur": 1800000000,
                },
                {
                    "cat": "remote action cache check",
                    "name": "check cache hit",
                    "ph": "X",
                    "ts": 16000000,
                    "dur": 500000,
                },
                {
                    "cat": "remote action execution",
                    "name": "execute remotely",
                    "ph": "X",
                    "ts": 17000000,
                    "dur": 10000000,
                },
                {
                    "cat": "build phase marker",
                    "name": "Complete build",
                    "ph": "i",
                    "ts": 1840000000,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            gz_path = os.path.join(tmp, "command.profile.gz")
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                json.dump(sample_profile, f)

            parsed = parse_bazel_profile(gz_path)
            self.assertEqual(parsed["bazel_version"], "release 9.1.1rc1")
            self.assertAlmostEqual(parsed["bazel_wall_sec"], 1843.0, places=1)
            self.assertAlmostEqual(parsed["phase_launch_sec"], 3.0, places=1)
            self.assertAlmostEqual(parsed["phase_load_analyze_sec"], 10.0, places=1)
            self.assertAlmostEqual(parsed["phase_execute_sec"], 1825.0, places=1)
            self.assertAlmostEqual(parsed["critical_path_sec"], 1825.0, places=1)
            self.assertEqual(parsed["total_actions"], 2)
            self.assertEqual(parsed["remote_cache_checks"], 1)
            self.assertEqual(parsed["remote_executions"], 1)
            self.assertEqual(parsed["mnemonics"][0].mnemonic, "TestRunner")
            self.assertAlmostEqual(parsed["mnemonics"][0].total_sec, 1800.0, places=1)

    def test_parse_build_info(self):
        sample_bi = {
            "hostname": "adt-emu-m2-mac-11",
            "branch": "git_emu-main-next",
            "sync_start_time": 1000.0,
            "sync_finish_time": 1012.5,
            "target": {
                "target_start_time": 1015.0,
                "target_finish_time": 1915.0,
            },
            "worknode": {
                "creationTimeMillis": "400000",
            },
            "repo-dict": {
                "platform/hardware/generic/goldfish": "aaaa111122223333",
                "platform/build/bazel": "bbbb444455556666",
            },
            "parsed_manifest": {
                "projects": [
                    {
                        "name": "platform/hardware/generic/goldfish",
                        "path": "hardware/generic/goldfish",
                        "revision": "aaaa111122223333",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            bi_path = os.path.join(tmp, "BUILD_INFO")
            with open(bi_path, "w", encoding="utf-8") as f:
                json.dump(sample_bi, f)

            parsed = parse_build_info(bi_path)
            self.assertEqual(parsed["hostname"], "adt-emu-m2-mac-11")
            self.assertAlmostEqual(parsed["queue_sec"], 600.0, places=1)
            self.assertAlmostEqual(parsed["sync_sec"], 12.5, places=1)
            self.assertAlmostEqual(parsed["build_py_sec"], 900.0, places=1)
            self.assertEqual(
                parsed["repo_revisions"]["platform/hardware/generic/goldfish"],
                "aaaa111122223333",
            )
            self.assertEqual(
                parsed["repo_paths"]["platform/hardware/generic/goldfish"],
                "hardware/generic/goldfish",
            )

    def test_discover_and_resolve_stage_profiles(self):
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/command.profile.gz")[:2],
            ("release", "Release Packaging & Boot Tests"),
        )
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/unit_tests.profile.gz")[:2],
            ("unit_tests", "Fast-Fail Unit Tests"),
        )
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/symbols.profile.gz")[:2],
            ("symbols", "Crashpad Symbol Upload"),
        )
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/ets.profile.gz")[:2],
            ("ets", "ETS Integration Tests"),
        )
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/custom_smoke.profile.gz")[:2],
            ("custom_smoke", "Custom Smoke"),
        )

        class FakeClient:
            def list_artifacts(self, build_id, target):
                return [
                    "BUILD_INFO",
                    "logs/bazel/ets.profile.gz",
                    "logs/bazel/jvm.out",
                    "logs/bazel/custom_smoke.profile.gz",
                    "logs/bazel/symbols.profile.gz",
                    "logs/bazel/command.profile.gz",
                    "logs/bazel/unit_tests.profile.gz",
                    "logs/bazel/clang_tidy.profile.gz",
                ]

        discovered = discover_bazel_profile_artifacts(
            FakeClient(), "16500221", "emulator_mac_aarch64"
        )
        self.assertEqual(
            discovered,
            [
                "logs/bazel/clang_tidy.profile.gz",
                "logs/bazel/command.profile.gz",
                "logs/bazel/symbols.profile.gz",
                "logs/bazel/unit_tests.profile.gz",
                "logs/bazel/ets.profile.gz",
                "logs/bazel/custom_smoke.profile.gz",
            ],
        )

    def test_analyze_single_build_multi_stage_aggregation_and_rendering(self):
        def _make_profile_dict(dur_sec: float, action_name: str, mnemonic: str):
            dur_us = int(dur_sec * 1_000_000)
            return {
                "otherData": {"bazel_version": "release 9.1.1rc1"},
                "traceEvents": [
                    {"cat": "build phase marker", "name": "Launch Blaze", "ph": "X", "ts": -1000000, "dur": 1000000},
                    {"cat": "build phase marker", "name": "Evaluate target patterns", "ph": "i", "ts": 1000000},
                    {
                        "cat": "build phase marker",
                        "name": "Load, analyze dependencies and build artifacts",
                        "ph": "i",
                        "ts": 2000000,
                    },
                    {
                        "cat": "action processing",
                        "name": action_name,
                        "ph": "X",
                        "ts": 3000000,
                        "dur": dur_us,
                        "args": {"mnemonic": mnemonic, "target": "//test:target"},
                    },
                    {
                        "cat": "critical path component",
                        "name": f"action '{action_name}'",
                        "ph": "X",
                        "ts": 3000000,
                        "dur": dur_us,
                    },
                    {
                        "cat": "build phase marker",
                        "name": "Complete build",
                        "ph": "i",
                        "ts": 3000000 + dur_us,
                    },
                ],
            }

        stage_payloads = {
            "logs/bazel/command.profile.gz": _make_profile_dict(
                270.0, "Linking libgoldfish_aarch64.dylib", "CppLink"
            ),
            "logs/bazel/unit_tests.profile.gz": _make_profile_dict(
                80.0, "Testing random_seed_test", "TestRunner"
            ),
            "logs/bazel/ets.profile.gz": _make_profile_dict(
                600.0, "Testing exclusive_presubmit", "TestRunner"
            ),
            "logs/bazel/custom_check.profile.gz": _make_profile_dict(
                20.0, "Running custom check", "Genrule"
            ),
        }

        class FakeMultiStageClient:
            def get_build(self, build_id, target):
                return {
                    "buildId": build_id,
                    "target": target,
                    "branch": "git_emu-main-next",
                    "buildAttemptStatus": "COMPLETE",
                    "successful": True,
                    "machineName": "adt-emu-m1-mac-3",
                    "creationTimestamp": "2026-10-02T13:00:00Z",
                    "completionTimestamp": "2026-10-02T13:20:00Z",
                }

            def list_artifacts(self, build_id, target):
                return ["BUILD_INFO"] + list(stage_payloads.keys())

            def download_artifact(self, build_id, target, artifact, dest_dir):
                dest_file = os.path.join(dest_dir, artifact)
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                if artifact == "BUILD_INFO":
                    with open(dest_file, "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "hostname": "adt-emu-m1-mac-3",
                                "sync_start_time": 100.0,
                                "sync_finish_time": 110.0,
                                "target": {"target_start_time": 110.0, "target_finish_time": 1150.0},
                                "worknode": {"creationTimeMillis": "80000"},
                            },
                            f,
                        )
                elif artifact in stage_payloads:
                    with gzip.open(dest_file, "wt", encoding="utf-8") as f:
                        json.dump(stage_payloads[artifact], f)
                return dest_file

        metrics = analyze_single_build(
            build_id="16500221",
            target="emulator_mac_aarch64",
            client=FakeMultiStageClient(),
            use_cache=False,
        )

        self.assertTrue(metrics.profile_available)
        self.assertEqual(len(metrics.stages), 4)
        self.assertEqual(
            [s.stage_id for s in metrics.stages],
            ["release", "unit_tests", "ets", "custom_check"],
        )
        self.assertAlmostEqual(metrics.critical_path_sec, 270.0 + 80.0 + 600.0 + 20.0, places=1)
        self.assertEqual(metrics.total_actions, 4)

        md = build_times._render_single_build_markdown(metrics)
        self.assertIn("Release Packaging & Boot Tests", md)
        self.assertIn("Fast-Fail Unit Tests", md)
        self.assertIn("ETS Integration Tests", md)
        self.assertIn("Custom Check", md)
        self.assertIn("logs/bazel/unit_tests.profile.gz", md)
        self.assertIn("logs/bazel/ets.profile.gz", md)

    def test_annotate_regressions_and_compare_profiles(self):
        fast1 = BuildProfileMetrics(
            build_id="16411294",
            target="emulator_mac_aarch64",
            machine_name="adt-emu-m2-mac-11",
            total_ci_sec=990.0,
            queue_sec=20.0,
            sync_sec=10.0,
            build_py_sec=935.0,
            bazel_wall_sec=813.0,
            critical_path_sec=457.0,
            critical_path_items=[
                CriticalPathItem("action 'Testing exclusive_presubmit'", 212.0, 46.4),
            ],
            mnemonics=[
                MnemonicSummary("TestRunner", 372, 1197.0, 3.2, 212.0, "Testing exclusive_presubmit"),
            ],
            stages=[
                StageProfileMetrics(
                    stage_id="release",
                    stage_name="Release Packaging & Boot Tests",
                    artifact_name="logs/bazel/command.profile.gz",
                    bazel_wall_sec=300.0,
                    critical_path_sec=150.0,
                ),
                StageProfileMetrics(
                    stage_id="ets",
                    stage_name="ETS Integration Tests",
                    artifact_name="logs/bazel/ets.profile.gz",
                    bazel_wall_sec=513.0,
                    critical_path_sec=307.0,
                ),
            ],
            repo_revisions={"platform/hardware/generic/goldfish": "2222222222222222"},
            repo_paths={"platform/hardware/generic/goldfish": "hardware/generic/goldfish"},
            profile_available=True,
        )
        fast2 = BuildProfileMetrics(
            build_id="16411039",
            target="emulator_mac_aarch64",
            machine_name="adt-emu-m2-mac-11",
            total_ci_sec=893.0,
            queue_sec=15.0,
            sync_sec=9.0,
            build_py_sec=850.0,
            bazel_wall_sec=790.0,
            critical_path_sec=440.0,
            stages=[
                StageProfileMetrics(
                    stage_id="release",
                    stage_name="Release Packaging & Boot Tests",
                    artifact_name="logs/bazel/command.profile.gz",
                    bazel_wall_sec=290.0,
                    critical_path_sec=140.0,
                ),
                StageProfileMetrics(
                    stage_id="ets",
                    stage_name="ETS Integration Tests",
                    artifact_name="logs/bazel/ets.profile.gz",
                    bazel_wall_sec=500.0,
                    critical_path_sec=300.0,
                ),
            ],
            profile_available=True,
        )
        slow = BuildProfileMetrics(
            build_id="16405496",
            target="emulator_mac_aarch64",
            machine_name="adt-emu-m2-mac-18",
            total_ci_sec=12883.0,
            queue_sec=10051.0,
            sync_sec=11.0,
            build_py_sec=2790.0,
            bazel_wall_sec=2241.0,
            critical_path_sec=1816.0,
            critical_path_items=[
                CriticalPathItem(
                    "action 'Testing @@goldfish+//emulator/libs/grpc_client:emulator_grpc_client_test'",
                    1802.4,
                    99.2,
                ),
            ],
            mnemonics=[
                MnemonicSummary(
                    "TestRunner",
                    373,
                    4195.0,
                    11.2,
                    1802.4,
                    "Testing @@goldfish+//emulator/libs/grpc_client:emulator_grpc_client_test",
                ),
            ],
            stages=[
                StageProfileMetrics(
                    stage_id="release",
                    stage_name="Release Packaging & Boot Tests",
                    artifact_name="logs/bazel/command.profile.gz",
                    bazel_wall_sec=310.0,
                    critical_path_sec=155.0,
                ),
                StageProfileMetrics(
                    stage_id="ets",
                    stage_name="ETS Integration Tests",
                    artifact_name="logs/bazel/ets.profile.gz",
                    bazel_wall_sec=1931.0,
                    critical_path_sec=1661.0,
                ),
            ],
            repo_revisions={"platform/hardware/generic/goldfish": "1111111111111111"},
            repo_paths={"platform/hardware/generic/goldfish": "hardware/generic/goldfish"},
            profile_available=True,
        )

        baseline, regressed = annotate_regressions([fast1, fast2, slow])
        self.assertIsNotNone(baseline)
        self.assertEqual(len(regressed), 1)
        self.assertEqual(regressed[0].build_id, "16405496")
        self.assertTrue(slow.is_regressed)
        self.assertTrue(any("ets" in r.lower() for r in slow.regression_reasons))

        comp = compare_build_profiles(fast1, slow, source_root=None)
        self.assertGreater(comp["deltas"]["queue_sec"], 9000.0)
        self.assertGreater(comp["deltas"]["bazel_wall_sec"], 1400.0)
        self.assertEqual(len(comp["stage_comparisons"]), 2)
        ets_comp = next(s for s in comp["stage_comparisons"] if s["stage_id"] == "ets")
        self.assertAlmostEqual(ets_comp["delta_wall_sec"], 1418.0, places=1)
        self.assertEqual(
            comp["critical_path_regressions"][0]["name"],
            "action 'Testing @@goldfish+//emulator/libs/grpc_client:emulator_grpc_client_test'",
        )
        self.assertEqual(comp["mnemonic_regressions"][0]["mnemonic"], "TestRunner")
        self.assertEqual(len(comp["changed_repos"]), 1)
        self.assertEqual(
            comp["changed_repos"][0]["repo"],
            "platform/hardware/generic/goldfish",
        )

    def test_review_feedback_edge_cases(self):
        # 1. Suffix stripping terminates after first matched suffix
        self.assertEqual(
            resolve_stage_metadata("logs/bazel/schema.json.profile.gz")[0],
            "schema.json",
        )

        # 2. Duplicate stage aliases (command.profile.gz and release.profile.gz) are deduplicated
        class AliasClient:
            def list_artifacts(self, build_id, target):
                return [
                    "logs/bazel/command.profile.gz",
                    "logs/bazel/release.profile.gz",
                    "logs/bazel/tidy.profile.gz",
                    "logs/bazel/clang_tidy.profile.gz",
                ]

        deduped = discover_bazel_profile_artifacts(AliasClient(), "1", "emulator_linux_x64")
        self.assertEqual(len(deduped), 2)
        self.assertEqual(
            [resolve_stage_metadata(a)[0] for a in deduped],
            ["clang_tidy", "release"],
        )

        # 3. Single-stage markdown section numbering is strictly sequential (1..6, no jump from 3 to 5)
        single_stage_metrics = BuildProfileMetrics(
            build_id="100",
            target="emulator_linux_x64",
            bazel_wall_sec=120.0,
            critical_path_sec=60.0,
            critical_path_items=[CriticalPathItem("action 'Compile a.cc'", 60.0, 100.0, "release")],
            mnemonics=[MnemonicSummary("CppCompile", 10, 100.0, 10.0, 60.0, "Compile a.cc")],
            slowest_actions=[{"duration_sec": 60.0, "mnemonic": "CppCompile", "name": "Compile a.cc", "stage_id": "release"}],
            stages=[
                StageProfileMetrics(
                    stage_id="release",
                    stage_name="Release Packaging & Boot Tests",
                    artifact_name="logs/bazel/command.profile.gz",
                    bazel_wall_sec=120.0,
                    critical_path_sec=60.0,
                )
            ],
            profile_available=True,
        )
        single_md = build_times._render_single_build_markdown(single_stage_metrics)
        for idx in range(1, 7):
            self.assertIn(f"### {idx}. ", single_md)
        self.assertNotIn("### 7. ", single_md)

        # 4. Baseline build is never marked as regressed in annotate_regressions
        b_base = BuildProfileMetrics(
            build_id="200",
            target="emulator_linux_x64",
            total_ci_sec=500.0,
            bazel_wall_sec=300.0,
            stages=[
                StageProfileMetrics("release", "Release", "logs/bazel/command.profile.gz", bazel_wall_sec=250.0),
                StageProfileMetrics("ets", "ETS", "logs/bazel/ets.profile.gz", bazel_wall_sec=200.0),
            ],
            profile_available=True,
        )
        b_other = BuildProfileMetrics(
            build_id="201",
            target="emulator_linux_x64",
            total_ci_sec=520.0,
            bazel_wall_sec=310.0,
            stages=[
                StageProfileMetrics("release", "Release", "logs/bazel/command.profile.gz", bazel_wall_sec=260.0),
                StageProfileMetrics("ets", "ETS", "logs/bazel/ets.profile.gz", bazel_wall_sec=50.0),
            ],
            profile_available=True,
        )
        chosen_base, reg_list = annotate_regressions([b_base, b_other])
        self.assertEqual(chosen_base.build_id, "200")
        self.assertFalse(chosen_base.is_regressed)
        self.assertNotIn(chosen_base, reg_list)

        # 5. Critical path comparisons key by (stage_id, name) and ignore negative deltas (speedups)
        cp_base = BuildProfileMetrics(
            build_id="300",
            target="emulator_linux_x64",
            bazel_wall_sec=300.0,
            critical_path_sec=200.0,
            critical_path_items=[
                CriticalPathItem("action 'Link lib'", 100.0, 50.0, stage_id="release"),
                CriticalPathItem("action 'Link lib'", 20.0, 10.0, stage_id="unit_tests"),
                CriticalPathItem("action 'Fast in regressed'", 90.0, 45.0, stage_id="release"),
            ],
            profile_available=True,
        )
        cp_reg = BuildProfileMetrics(
            build_id="301",
            target="emulator_linux_x64",
            bazel_wall_sec=350.0,
            critical_path_sec=220.0,
            critical_path_items=[
                # Speedup in release (95s vs 100s -> -5s delta, should NOT be in cp_regressions)
                CriticalPathItem("action 'Link lib'", 95.0, 43.0, stage_id="release"),
                # Slowdown in unit_tests (45s vs 20s -> +25s delta, matched against unit_tests stage)
                CriticalPathItem("action 'Link lib'", 45.0, 20.0, stage_id="unit_tests"),
                # Speedup (50s vs 90s -> -40s delta, should NOT be in cp_regressions)
                CriticalPathItem("action 'Fast in regressed'", 50.0, 22.0, stage_id="release"),
            ],
            profile_available=True,
        )
        cp_comp = compare_build_profiles(cp_base, cp_reg)
        self.assertEqual(len(cp_comp["critical_path_regressions"]), 1)
        only_reg = cp_comp["critical_path_regressions"][0]
        self.assertEqual(only_reg["stage_id"], "unit_tests")
        self.assertAlmostEqual(only_reg["baseline_sec"], 20.0, places=1)
        self.assertAlmostEqual(only_reg["regressed_sec"], 45.0, places=1)
        self.assertAlmostEqual(only_reg["delta_sec"], 25.0, places=1)


if __name__ == "__main__":
    unittest.main()


