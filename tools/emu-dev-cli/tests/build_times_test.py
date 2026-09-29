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
    annotate_regressions,
    compare_build_profiles,
    parse_bazel_profile,
    parse_build_info,
    resolve_build_targets,
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
            repo_revisions={"platform/hardware/generic/goldfish": "1111111111111111"},
            repo_paths={"platform/hardware/generic/goldfish": "hardware/generic/goldfish"},
            profile_available=True,
        )

        baseline, regressed = annotate_regressions([fast1, fast2, slow])
        self.assertIsNotNone(baseline)
        self.assertEqual(len(regressed), 1)
        self.assertEqual(regressed[0].build_id, "16405496")
        self.assertTrue(slow.is_regressed)

        comp = compare_build_profiles(fast1, slow, source_root=None)
        self.assertGreater(comp["deltas"]["queue_sec"], 9000.0)
        self.assertGreater(comp["deltas"]["bazel_wall_sec"], 1400.0)
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


if __name__ == "__main__":
    unittest.main()
