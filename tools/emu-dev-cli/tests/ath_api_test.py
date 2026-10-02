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

"""Unit tests for Android Test Hub (ATH) and Android Build API integration."""

import sys
import unittest
from unittest.mock import MagicMock, patch
from lib.ath_api import (
    FlakyTestRecord,
    SUPPORTED_TARGET_PLATFORMS,
    SUPPORTED_TEST_IDENTIFIERS,
    fetch_invocation_artifacts,
    get_android_build_token,
    get_bazel_flags_for_target,
    infer_test_identifier_for_target,
    parse_ath_test_results,
    query_ath_flaky_tests,
    query_test_history,
    resolve_test_identifiers,
    sanitize_bazel_target,
)


class AthApiTest(unittest.TestCase):

    def test_supported_target_platforms(self):
        expected = {
            "emulator_linux_x64",
            "emulator_linux_x64_asan",
            "emulator_linux_x64_tsan",
            "emulator_windows_x64",
            "emulator_mac_aarch64",
        }
        self.assertEqual(set(SUPPORTED_TARGET_PLATFORMS), expected)

    def test_supported_test_identifiers_and_resolution(self):
        expected_ids = [
            "android_emulator/ets",
            "android_emulator/release",
            "android_emulator/unit_tests",
        ]
        self.assertEqual(SUPPORTED_TEST_IDENTIFIERS, expected_ids)
        self.assertEqual(resolve_test_identifiers("all"), expected_ids)
        self.assertEqual(resolve_test_identifiers("devtools/emulator"), expected_ids)
        self.assertEqual(resolve_test_identifiers("ets"), ["android_emulator/ets"])
        self.assertEqual(
            resolve_test_identifiers("release"), ["android_emulator/release"]
        )
        self.assertEqual(
            resolve_test_identifiers("unit_tests"),
            ["android_emulator/unit_tests"],
        )

    def test_infer_test_identifier_for_target(self):
        self.assertEqual(
            infer_test_identifier_for_target("@@goldfish_test+//ets:presubmit"),
            "android_emulator/ets",
        )
        self.assertEqual(
            infer_test_identifier_for_target(
                "@goldfish//emulator/launcher:can_boot_with_minigbm"
            ),
            "android_emulator/release",
        )
        self.assertEqual(
            infer_test_identifier_for_target("@goldfish_build//:sanity_checks"),
            "android_emulator/release",
        )
        self.assertEqual(
            infer_test_identifier_for_target(
                "@qemu//google/toolchain:generated_diff"
            ),
            "android_emulator/release",
        )
        self.assertEqual(
            infer_test_identifier_for_target(
                "@@goldfish+//emulator/libs/async:loop_handoff_test"
            ),
            "android_emulator/unit_tests",
        )

    def test_sanitize_bazel_target(self):
        self.assertEqual(
            sanitize_bazel_target(
                "@@goldfish+//emulator/libs/async:loop_handoff_test-run1"
            ),
            "@goldfish//emulator/libs/async:loop_handoff_test",
        )
        self.assertEqual(
            sanitize_bazel_target(
                "@@goldfish_test+//testlib/test/com/android/emulator:device_boot_test"
            ),
            "@goldfish_test//testlib/test/com/android/emulator:device_boot_test",
        )
        self.assertEqual(
            sanitize_bazel_target("@@goldfish_build+//:sanity_checks"),
            "@goldfish_build//:sanity_checks",
        )
        self.assertEqual(
            sanitize_bazel_target("@@qemu+//google/toolchain:generated_diff"),
            "@qemu//google/toolchain:generated_diff",
        )
        self.assertEqual(
            sanitize_bazel_target(
                "//emulator/videobridge:test-abcdef1234567890abcdef1234567890"
            ),
            "//emulator/videobridge:test",
        )

    def test_get_bazel_flags_for_target(self):
        # Linux x64
        cmd, flags = get_bazel_flags_for_target(
            "//emulator/videobridge:in_process_media_provider_test",
            "emulator_linux_x64",
            20,
        )
        self.assertEqual(cmd, "test")
        self.assertIn(
            "//emulator/videobridge:in_process_media_provider_test", flags
        )
        self.assertIn("--runs_per_test=20", flags)
        self.assertNotIn("--build_manual_tests", flags)

        # ETS and release targets should include --build_manual_tests
        _, ets_flags = get_bazel_flags_for_target(
            "@@goldfish_test+//ets:presubmit",
            "emulator_linux_x64",
            5,
        )
        self.assertIn("@goldfish_test//ets:presubmit", ets_flags)
        self.assertIn("--build_manual_tests", ets_flags)

        _, release_flags = get_bazel_flags_for_target(
            "@@goldfish+//emulator/launcher:can_boot_with_minigbm",
            "emulator_linux_x64",
            5,
        )
        self.assertIn("--build_manual_tests", release_flags)

        # ASAN
        _, asan_flags = get_bazel_flags_for_target(
            "//emulator/videobridge:in_process_media_provider_test",
            "emulator_linux_x64_asan",
            10,
        )
        self.assertIn("--config=asan", asan_flags)
        self.assertIn("--runs_per_test=10", asan_flags)

        # TSAN
        _, tsan_flags = get_bazel_flags_for_target(
            "//emulator/videobridge:in_process_media_provider_test",
            "emulator_linux_x64_tsan",
            5,
        )
        self.assertIn("--config=tsan", tsan_flags)

        # Windows
        _, win_flags = get_bazel_flags_for_target(
            "//emulator/videobridge:in_process_media_provider_test",
            "emulator_windows_x64",
            1,
        )
        expected_win_config = "--config=rbe-win-x64" if sys.platform.startswith("linux") else "--config=windows"
        self.assertIn(expected_win_config, win_flags)

        # Mac ARM64
        _, mac_flags = get_bazel_flags_for_target(
            "//emulator/videobridge:in_process_media_provider_test",
            "emulator_mac_aarch64",
            1,
        )
        self.assertIn("--config=macos_arm64", mac_flags)

    def test_calculate_stress_test_timeout(self):

        from lib.ath_api import calculate_stress_test_timeout

        # Explicit timeout override
        self.assertEqual(calculate_stress_test_timeout(explicit_timeout=600), 600)

        # Standard target: max(300, 180 + 1 * 12 * 1.0) -> 300
        self.assertEqual(calculate_stress_test_timeout(target="emulator_linux_x64", iterations=1), 300)

        # Standard target, 20 iterations: max(300, 180 + 20 * 12 * 1.0) -> 420
        self.assertEqual(calculate_stress_test_timeout(target="emulator_linux_x64", iterations=20), 420)

        # TSAN target, 20 iterations: max(300, 180 + 20 * 12 * 3.5) -> 1020
        self.assertEqual(calculate_stress_test_timeout(target="emulator_linux_x64_tsan", iterations=20), 1020)

        # ASAN target, 50 iterations: max(300, 180 + 50 * 12 * 2.5) -> 1680
        self.assertEqual(calculate_stress_test_timeout(target="emulator_linux_x64_asan", iterations=50), 1680)


    def test_parse_ath_test_results(self):
        raw_response = {
            "testResults": [
                {
                    "testIdentifier": "//emulator/videobridge:in_process_media_provider_test",
                    "moduleName": "videobridge",
                    "totalRuns": 100,
                    "failedRuns": 15,
                    "latestBuildId": "15900270",
                    "latestInvocationId": "I75500010177130681",
                    "latestWorkUnitId": "WU44400250003168346",
                }
            ]
        }
        records = parse_ath_test_results(
            raw_response,
            target="emulator_linux_x64_asan",
            config_name="android_emulator/unit_tests",
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(
            rec.test_identifier,
            "//emulator/videobridge:in_process_media_provider_test",
        )
        self.assertEqual(rec.target, "emulator_linux_x64_asan")
        self.assertEqual(rec.config_name, "android_emulator/unit_tests")
        self.assertEqual(rec.flake_rate_pct, 15.0)
        self.assertEqual(rec.latest_invocation_id, "I75500010177130681")

    @patch("lib.oauth.OAuthTokenManager.get_token")
    def test_get_android_build_token(self, mock_get_token):
        mock_get_token.return_value = "mock_oauth2_token"
        token = get_android_build_token()
        self.assertEqual(token, "mock_oauth2_token")

    @patch("lib.ath_api.get_android_build_token")
    def test_fetch_invocation_artifacts(self, mock_token):
        mock_token.return_value = "mock_oauth2_token"
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            res = fetch_invocation_artifacts(
                invocation_id="I75500010177130681",
                artifact_type="LOGCAT",
                out_dir=tmpdir,
            )
            self.assertEqual(res["invocation_id"], "I75500010177130681")
            self.assertEqual(res["total_files"], 1)
            self.assertTrue(res["downloaded_files"][0]["size_bytes"] > 0)

    @patch("lib.ath_api.execute_http_get")
    @patch("lib.ath_api.get_android_build_token")
    def test_fetch_ath_invocations_all_targets(self, mock_token, mock_get):
        mock_token.return_value = "mock_token"
        mock_get.return_value = {
            "invocations": [
                {
                    "invocationId": "I12345",
                    "timing": {"creationTimestamp": "9999999999999"},
                    "primaryBuild": {"buildTarget": "emulator_linux_x64"},
                }
            ]
        }
        from lib.ath_api import fetch_ath_invocations
        invs = fetch_ath_invocations(target="all", days=1, mode="presubmit")
        # Should query each of 5 supported target platforms
        self.assertEqual(mock_get.call_count, 5)
        self.assertTrue(len(invs) > 0)

    def test_resultstore_parse_test_cases(self):
        from lib.resultstore_api import _parse_test_cases_from_action_block

        sample_block = """
          id {
            invocation_id: "5212ef79-083d-4361-bf10-709d30d9d5eb"
            target_id: "@@goldfish_test+//ets:presubmit"
            action_id: "test"
          }
          test_action {
            test_suite {
              suite_name: "TestSuite"
              tests {
                test_case {
                  case_name: "testBootCompleted"
                  class_name: "com.android.emulator.BootTest"
                  result: COMPLETED
                }
              }
              tests {
                test_case {
                  case_name: "testSnapshotLoad"
                  class_name: "com.android.emulator.SnapshotTest"
                  result: COMPLETED
                  failures {
                    failure_message: "Timeout waiting for \\"snapshot\\" in tests { block }"
                    stack_trace: "java.util.concurrent.TimeoutException: snapshot in tests { nested }"
                  }
                }
              }
            }
          }
        """
        total, failed = _parse_test_cases_from_action_block(sample_block)
        self.assertEqual(total, 2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].case_name, "testSnapshotLoad")
        self.assertEqual(failed[0].class_name, "com.android.emulator.SnapshotTest")
        self.assertEqual(failed[0].result, "FAILED")
        self.assertEqual(
            failed[0].failure_message,
            'Timeout waiting for "snapshot" in tests { block }',
        )
        self.assertEqual(
            failed[0].stack_trace,
            "java.util.concurrent.TimeoutException: snapshot in tests { nested }",
        )

    @patch("lib.ath_api.execute_http_get")
    @patch("lib.ath_api.fetch_ath_invocations")
    @patch("lib.ath_api.get_android_build_token")
    def test_query_ath_flaky_tests_suite_normalization(
        self, mock_token, mock_fetch_invs, mock_http_get
    ):
        mock_token.return_value = "mock_token"
        mock_fetch_invs.return_value = [
            {
                "invocationId": "I_FAIL",
                "test": {"name": "devtools/emulator"},
                "primaryBuild": {"buildId": "1001"},
                "properties": [
                    {"name": "_bazel_test_targets_total", "value": "50"},
                    {"name": "_bazel_test_targets_failed", "value": "1"},
                ],
            },
            {
                "invocationId": "I_PASS_1",
                "test": {"name": "devtools/emulator"},
                "primaryBuild": {"buildId": "1002"},
                "properties": [
                    {"name": "_bazel_test_targets_total", "value": "50"},
                    {"name": "_bazel_test_targets_failed", "value": "0"},
                ],
            },
            {
                "invocationId": "I_PASS_2",
                "test": {"name": "devtools/emulator"},
                "primaryBuild": {"buildId": "1003"},
                "properties": [
                    {"name": "_bazel_test_targets_total", "value": "50"},
                    {"name": "_bazel_test_targets_failed", "value": "0"},
                ],
            },
            {
                "invocationId": "I_PASS_3",
                "test": {"name": "devtools/emulator"},
                "primaryBuild": {"buildId": "1004"},
                "properties": [
                    {"name": "_bazel_test_targets_total", "value": "50"},
                    {"name": "_bazel_test_targets_failed", "value": "0"},
                ],
            },
        ]
        mock_http_get.return_value = {
            "workUnits": [
                {
                    "id": "WU_FAIL",
                    "type": "BAZEL_TARGET",
                    "name": "@@goldfish_test+//ets:presubmit",
                    "state": "error",
                }
            ]
        }

        records = query_ath_flaky_tests(
            target="emulator_linux_x64",
            config_name="devtools/emulator",
            min_flake_rate=5.0,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].test_identifier, "@goldfish_test//ets:presubmit")
        self.assertEqual(records[0].config_name, "android_emulator/ets")
        self.assertEqual(records[0].total_runs, 4)
        self.assertEqual(records[0].failed_runs, 1)
        self.assertAlmostEqual(records[0].flake_rate_pct, 25.0)

    def test_parse_resultstore_textproto_nested_braces(self):
        from lib.resultstore_api import _unescape_proto_str, parse_resultstore_textproto

        self.assertEqual(_unescape_proto_str(r"path\\network\nline2"), "path\\network\nline2")

        raw_text = """
        # Top-level comment with actions { fake } and unmatched " quote
        top_description: "outer string with actions { fake_block }"
        actions {
          # Comment inside block with } and "
          id {
            invocation_id: "5212ef79-083d-4361-bf10-709d30d9d5eb"
            target_id: "@@goldfish_test+//ets:presubmit"
            action_id: "test"
          }
          status_attributes {
            status: FAILED
            description: "Failed with \\"quoted\\" actions { nested } and files { nested }"
          }
          test_action {
            test_suite {
              tests {
                test_case {
                  case_name: "testBoot"
                  class_name: "BootTest"
                  result: COMPLETED
                  failures {
                    failure_message: "Error in actions { foo } and files { bar }"
                  }
                }
              }
              error_message: "Suite error with \\"escaped\\" quotes"
            }
          }
          files {
            uid: "test.log"
            uri: "https://storage/test.log"
            length { value: 123 }
          }
        }
        """
        inv = parse_resultstore_textproto("5212ef79-083d-4361-bf10-709d30d9d5eb", raw_text)
        self.assertEqual(len(inv.actions), 1)
        self.assertEqual(
            inv.actions[0].description,
            'Failed with "quoted" actions { nested } and files { nested }',
        )
        self.assertIn('Suite error with "escaped" quotes', inv.actions[0].error_messages)
        self.assertEqual(len(inv.actions[0].files), 1)
        self.assertEqual(inv.actions[0].files[0].uid, "test.log")
        self.assertEqual(len(inv.actions[0].failed_test_cases), 1)

    @patch("lib.ath_api.execute_http_get")
    @patch("lib.ath_api.fetch_ath_invocations")
    @patch("lib.ath_api.get_android_build_token")
    def test_query_test_history_error_pagination(
        self, mock_token, mock_fetch_invs, mock_http_get
    ):
        from lib.ath_api import _matches_invocation_filters, query_test_history

        self.assertTrue(
            _matches_invocation_filters(
                {
                    "primaryBuild": {"buildTarget": "emulator_linux_x64"},
                    "schedulerState": "completed",
                    "test": {"name": "ets"},
                },
                "emulator_linux_x64",
                {"android_emulator/ets"},
            )
        )

        mock_token.return_value = "mock_token"
        mock_fetch_invs.return_value = [
            {
                "invocationId": "I_PAGINATED",
                "test": {"name": "android_emulator/unit_tests"},
                "primaryBuild": {
                    "buildId": "2001",
                    "buildTarget": "emulator_linux_x64",
                },
                "timing": {"creationTimestamp": "1700000000000"},
                "properties": [
                    {"name": "_bazel_test_targets_total", "value": "200"},
                    {"name": "_bazel_test_targets_failed", "value": "105"},
                ],
            }
        ]
        mock_http_get.side_effect = [
            {
                "workUnits": [
                    {
                        "id": "WU_NULL",
                        "type": "BAZEL_TARGET",
                        "name": None,
                    },
                    {
                        "id": "WU_OTHER",
                        "type": "BAZEL_TARGET",
                        "name": "@@aemu+//other:test",
                    },
                ],
                "nextPageToken": "page_2",
            },
            {
                "workUnits": [
                    {
                        "id": "WU_TARGET",
                        "type": "BAZEL_TARGET",
                        "name": "@@aemu+//host-common:logging_unittest",
                    }
                ],
            },
        ]

        summary, history = query_test_history(
            test_target="@aemu//host-common:logging_unittest",
            target="emulator_linux_x64",
            days=1,
            mode="presubmit",
        )
        self.assertEqual(summary["failed_runs"], 1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].status, "FAILED")
        self.assertEqual(history[0].work_unit_id, "WU_TARGET")


if __name__ == "__main__":
    unittest.main()

