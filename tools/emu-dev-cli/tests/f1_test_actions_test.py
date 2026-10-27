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

"""Unit tests for F1 TestActions (`studio-flakes`) integration."""

import unittest
from unittest.mock import patch

from lib.f1_test_actions import (
    DEFAULT_STUDIO_COMPONENT_ID,
    DEFAULT_STUDIO_DIR_PREFIX,
    STUDIO_TARGET_PLATFORMS,
    build_studio_flakes_sql,
    build_studio_history_sql,
    is_android_device_hub_target,
    is_studio_query,
    normalize_target_prefix,
    query_studio_flaky_tests,
    query_studio_test_history,
    resolve_studio_branches,
    resolve_studio_min_flake_threshold,
    resolve_studio_targets,
    resolve_studio_window,
)


class F1TestActionsTest(unittest.TestCase):

    def test_is_studio_query(self):
        self.assertTrue(is_studio_query(target="studio-linux"))
        self.assertTrue(is_studio_query(target="studio-all"))
        self.assertTrue(is_studio_query(branch="git_studio-main"))
        self.assertTrue(is_studio_query(branch="studio-main"))
        self.assertTrue(
            is_studio_query(
                directory="//tools/vendor/google/cli/android-device-hub/electron"
            )
        )
        self.assertTrue(
            is_studio_query(
                test_target="//tools/vendor/google/cli/android-device-hub/electron:test"
            )
        )
        self.assertFalse(
            is_studio_query(
                target="emulator_linux_x64", branch="git_emu-main-next"
            )
        )

    def test_is_android_device_hub_target(self):
        self.assertEqual(DEFAULT_STUDIO_COMPONENT_ID, "2289192")
        self.assertTrue(
            is_android_device_hub_target(
                "//tools/vendor/google/cli/android-device-hub/electron:test"
            )
        )
        self.assertTrue(
            is_android_device_hub_target(
                "studio-main/tools/vendor/google/cli/android-device-hub/electron"
            )
        )
        self.assertFalse(
            is_android_device_hub_target(
                "@goldfish//emulator/libs/async:loop_handoff_test"
            )
        )

    def test_resolve_studio_window_and_min_flake_threshold(self):
        # ADH defaults to window=250 and any flake (min_flake_rate=0.0, min_failed_runs=1)
        self.assertEqual(resolve_studio_window(None, None), 250)
        self.assertEqual(
            resolve_studio_window(
                None, "//tools/vendor/google/cli/android-device-hub/electron"
            ),
            250,
        )
        self.assertEqual(resolve_studio_window(100, None), 100)
        self.assertEqual(
            resolve_studio_window(None, "//tools/adt/idea/android:tests"), 100
        )

        self.assertEqual(
            resolve_studio_min_flake_threshold(None, 5.0, None), (0.0, 1)
        )
        self.assertEqual(
            resolve_studio_min_flake_threshold(None, 10.0, None), (0.0, 1)
        )
        self.assertEqual(
            resolve_studio_min_flake_threshold(0.0, 5.0, None), (0.0, 0)
        )
        self.assertEqual(
            resolve_studio_min_flake_threshold(
                None, 10.0, "//tools/adt/idea/android:tests"
            ),
            (10.0, 0),
        )

    def test_resolve_studio_targets_and_branches(self):
        self.assertEqual(
            resolve_studio_targets("studio-all"), STUDIO_TARGET_PLATFORMS
        )
        self.assertEqual(resolve_studio_targets("all"), STUDIO_TARGET_PLATFORMS)
        self.assertEqual(
            resolve_studio_targets("studio-linux"), ["studio-linux"]
        )
        self.assertEqual(
            resolve_studio_branches("git_studio-main"),
            ["git_studio-main", "git_studio-main-periodic"],
        )
        self.assertEqual(
            resolve_studio_branches("git_emu-main-next"),
            ["git_studio-main", "git_studio-main-periodic"],
        )
        self.assertEqual(
            resolve_studio_branches("git_studio-release"),
            ["git_studio-release"],
        )

    def test_normalize_target_prefix(self):
        self.assertEqual(
            normalize_target_prefix(None), DEFAULT_STUDIO_DIR_PREFIX
        )
        self.assertEqual(
            normalize_target_prefix(
                "studio-main/tools/vendor/google/cli/android-device-hub/electron/"
            ),
            "//tools/vendor/google/cli/android-device-hub/electron",
        )
        self.assertEqual(
            normalize_target_prefix(
                "tools/vendor/google/cli/android-device-hub/electron"
            ),
            "//tools/vendor/google/cli/android-device-hub/electron",
        )

    def test_build_studio_flakes_sql_and_injection_guard(self):
        sql = build_studio_flakes_sql(
            targets=["studio-linux", "studio-mac-arm"],
            branches=["git_studio-main", "git_studio-main-periodic"],
            directory_prefix="//tools/vendor/google/cli/android-device-hub/electron",
            days=20,
            mode="postsubmit",
        )
        self.assertIn("android_devtools_infra.TestActions", sql)
        self.assertIn("WHEN 'studio-linux-flake-reruns' THEN 'studio-linux'", sql)
        self.assertIn("NOT STARTS_WITH(ab_build_id, 'P')", sql)
        self.assertIn(
            "STARTS_WITH(target_id, '//tools/vendor/google/cli/android-device-hub/electron')",
            sql,
        )
        self.assertIn(
            "LAST_VALUE(IF(NOT passed, invocation_id, NULL) IGNORE NULLS) OVER (last250)",
            sql,
        )

        with self.assertRaises(ValueError):
            build_studio_flakes_sql(
                targets=["studio-linux"],
                branches=["git_studio-main"],
                directory_prefix="//tools/vendor/google'; DROP TABLE x; --",
            )

    def test_build_studio_history_sql(self):
        sql = build_studio_history_sql(
            test_target="//tools/vendor/google/cli/android-device-hub/electron:test",
            targets=["studio-linux"],
            branches=["git_studio-main"],
            days=14,
            mode="presubmit",
            limit=25,
        )
        self.assertIn("STARTS_WITH(ab_build_id, 'P')", sql)
        self.assertIn(
            "AND target_id = '//tools/vendor/google/cli/android-device-hub/electron:test'",
            sql,
        )
        self.assertIn("LIMIT 25;", sql)

    @patch("lib.f1_test_actions.execute_f1_jsonl_query")
    def test_query_studio_flaky_tests_windows(self, mock_exec):
        mock_exec.return_value = [
            {
                "ab_target": "studio-linux",
                "target_id": "//tools/vendor/google/cli/android-device-hub/electron:test",
                "latest_build_id": "16630659",
                "latest_invocation_id": "00906c62-ae04-474a-aacd-bed0c864a953",
                "l100_failed": 1,
                "l100_total": 100,
                "l100_flake_rate_pct": 1.0,
                "l250_failed": 1,
                "l250_total": 250,
                "l250_flake_rate_pct": 0.4,
                "latest_failed_build_id": "16630553",
                "latest_failed_invocation_id": "ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
            },
            {
                "ab_target": "studio-mac-arm",
                "target_id": "//tools/vendor/google/cli/android-device-hub/electron:package_test",
                "latest_build_id": "16630964",
                "latest_invocation_id": "866a91db-7073-480b-9f73-828d26f76e87",
                "l100_failed": 0,
                "l100_total": 100,
                "l100_flake_rate_pct": 0.0,
                "l250_failed": 68,
                "l250_total": 250,
                "l250_flake_rate_pct": 27.2,
                "latest_failed_build_id": "16555878",
                "latest_failed_invocation_id": "5c8c6d47-c090-46d2-bb11-cf50687b2085",
            },
        ]

        # Window 100 with min_flake_rate=0.5 -> only :test (1.0% on l100) matches
        recs_100 = query_studio_flaky_tests(
            target="studio-all",
            min_flake_rate=0.5,
            window=100,
        )
        self.assertEqual(len(recs_100), 1)
        self.assertEqual(
            recs_100[0].test_identifier,
            "//tools/vendor/google/cli/android-device-hub/electron:test",
        )
        self.assertEqual(recs_100[0].flake_rate_pct, 1.0)
        self.assertEqual(
            recs_100[0].latest_invocation_id,
            "ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
        )
        self.assertEqual(recs_100[0].latest_build_id, "16630659")
        self.assertEqual(recs_100[0].latest_failed_build_id, "16630553")

        # Window 250 with min_flake_rate=5.0 -> :package_test (27.2% on l250) matches
        recs_250 = query_studio_flaky_tests(
            target="studio-all",
            min_flake_rate=5.0,
            window=250,
        )
        self.assertEqual(len(recs_250), 1)
        self.assertEqual(
            recs_250[0].test_identifier,
            "//tools/vendor/google/cli/android-device-hub/electron:package_test",
        )
        self.assertEqual(recs_250[0].flake_rate_pct, 27.2)

        # Window 250 with min_flake_rate=0.0 and min_failed_runs=1 -> both rare (0.4%) and frequent (27.2%) match
        mock_exec.return_value.append(
            {
                "ab_target": "studio-linux",
                "target_id": "//tools/vendor/google/cli/android-device-hub/electron:e2e_test",
                "latest_build_id": "16630659",
                "latest_invocation_id": "00906c62-ae04-474a-aacd-bed0c864a953",
                "l100_failed": 0,
                "l100_total": 100,
                "l100_flake_rate_pct": 0.0,
                "l250_failed": 0,
                "l250_total": 250,
                "l250_flake_rate_pct": 0.0,
                "latest_failed_build_id": None,
                "latest_failed_invocation_id": None,
            }
        )
        recs_any = query_studio_flaky_tests(
            target="studio-all",
            min_flake_rate=0.0,
            min_failed_runs=1,
            window=250,
        )
        self.assertEqual(len(recs_any), 2)
        self.assertEqual(
            [r.test_identifier for r in recs_any],
            [
                "//tools/vendor/google/cli/android-device-hub/electron:package_test",
                "//tools/vendor/google/cli/android-device-hub/electron:test",
            ],
        )

    @patch("lib.f1_test_actions.execute_f1_jsonl_query")
    def test_query_studio_test_history(self, mock_exec):
        mock_exec.return_value = [
            {
                "ab_target": "studio-linux",
                "raw_ab_target": "studio-linux",
                "ab_build_id": "16630659",
                "target_id": "//tools/vendor/google/cli/android-device-hub/electron:test",
                "run_number": 1,
                "invocation_id": "00906c62-ae04-474a-aacd-bed0c864a953",
                "timestamp_utc": "2026-10-16 14:00:00",
                "passed": True,
                "failed_actions": 0,
                "total_actions": 1,
            },
            {
                "ab_target": "studio-linux",
                "raw_ab_target": "studio-linux-flake-reruns",
                "ab_build_id": "16630553",
                "target_id": "//tools/vendor/google/cli/android-device-hub/electron:test",
                "run_number": 1,
                "invocation_id": "ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
                "timestamp_utc": "2026-10-16 12:00:00",
                "passed": False,
                "failed_actions": 1,
                "total_actions": 1,
            },
        ]

        summary, entries = query_studio_test_history(
            test_target="//tools/vendor/google/cli/android-device-hub/electron:test",
            target="studio-linux",
        )
        self.assertEqual(summary["total_runs"], 2)
        self.assertEqual(summary["passed_runs"], 1)
        self.assertEqual(summary["failed_runs"], 1)
        self.assertAlmostEqual(summary["flake_rate_pct"], 50.0)
        self.assertEqual(entries[0].status, "PASSED")
        self.assertEqual(entries[1].status, "FAILED")
        self.assertEqual(entries[1].run_type, "Postsubmit (flake-rerun)")


if __name__ == "__main__":
    unittest.main()
