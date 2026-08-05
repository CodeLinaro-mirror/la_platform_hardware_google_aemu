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
    fetch_invocation_artifacts,
    get_android_build_token,
    get_bazel_flags_for_target,
    parse_ath_test_results,
    query_ath_flaky_tests,
    query_test_history,
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

    def test_sanitize_bazel_target(self):
        self.assertEqual(
            sanitize_bazel_target(
                "@@goldfish+//emulator/libs/async:loop_handoff_test-run1"
            ),
            "@goldfish//emulator/libs/async:loop_handoff_test",
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
            config_name="devtools/emulator",
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(
            rec.test_identifier,
            "//emulator/videobridge:in_process_media_provider_test",
        )
        self.assertEqual(rec.target, "emulator_linux_x64_asan")
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


if __name__ == "__main__":
    unittest.main()
