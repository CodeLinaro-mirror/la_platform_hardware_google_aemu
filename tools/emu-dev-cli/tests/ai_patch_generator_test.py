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

"""Unit tests for AI patch, Jetski sandbox, and root-cause generator."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.ai_patch_generator import (
    AIPatchResult,
    format_bug_body_with_ai_patch,
    generate_ai_patch_for_test,
)
from lib.ath_api import FlakyTestRecord


class AIPatchGeneratorTest(unittest.TestCase):

    def setUp(self):
        self.record = FlakyTestRecord(
            test_identifier="//emulator/videobridge:in_process_media_provider_test",
            module_name="videobridge",
            target="emulator_linux_x64_tsan",
            config_name="devtools/emulator",
            total_runs=100,
            failed_runs=15,
            flake_rate_pct=15.0,
            latest_build_id="15900270",
            latest_invocation_id="I75500010177130681",
            latest_work_unit_id="WU44400250003168346",
        )

    @patch("lib.ai_patch_generator.fetch_invocation_artifacts")
    def test_generate_ai_patch_for_test_jetski_sandbox(self, mock_fetch):
        mock_fetch.return_value = {
            "downloaded_files": [
                {
                    "file_name": "logcat.txt",
                    "file_path": "/tmp/logcat.txt",
                    "source": "LOCAL_STUB",
                    "is_synthetic": True,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca_summary.md"
            rca_file.write_text(
                "### 🔬 Jetski RCA Analysis\n\n"
                "**Root Cause Summary:** Data race in videobridge\n\n"
                "```diff\n"
                "--- a/videobridge.cpp\n"
                "+++ b/videobridge.cpp\n"
                "@@ -1,1 +1,1 @@\n"
                "-// Automated Fix Strategy\n"
                "+// Synchronized lock\n"
                "```\n",
                encoding="utf-8",
            )
            patch = generate_ai_patch_for_test(self.record, out_dir=tmpdir)
            self.assertEqual(
                patch.test_identifier,
                "//emulator/videobridge:in_process_media_provider_test",
            )
            self.assertIn("Data race in videobridge", patch.root_cause_summary)
            self.assertTrue(len(patch.proposed_diff) > 0)

    def test_generate_ai_patch_missing_invocation_id_raises_fatal(self):
        record_no_inv = FlakyTestRecord(
            test_identifier="//emulator/videobridge:in_process_media_provider_test",
            module_name="videobridge",
            target="emulator_linux_x64_tsan",
            config_name="devtools/emulator",
            total_runs=100,
            failed_runs=15,
            flake_rate_pct=15.0,
            latest_build_id="",
            latest_invocation_id="",
            latest_work_unit_id="",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError) as ctx:
                generate_ai_patch_for_test(record_no_inv, out_dir=tmpdir)
            self.assertIn("No valid invocation ID associated", str(ctx.exception))

    @patch("lib.ai_patch_generator.fetch_invocation_artifacts")
    def test_generate_ai_patch_reads_existing_rca_summary(self, mock_fetch):
        mock_fetch.return_value = {"downloaded_files": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca_summary.md"
            rca_file.write_text(
                "### 🔬 Jetski RCA Analysis\n\n"
                "**Root Cause Summary:** Race condition on buffer handoff\n\n"
                "```diff\n"
                "--- a/buffer.cpp\n"
                "+++ b/buffer.cpp\n"
                "@@ -10,1 +10,1 @@\n"
                "-mBuffer = NULL;\n"
                "+mBuffer.reset();\n"
                "```\n",
                encoding="utf-8",
            )

            patch = generate_ai_patch_for_test(self.record, out_dir=tmpdir)
            self.assertEqual(patch.root_cause_summary, "Race condition on buffer handoff")
            self.assertIn("mBuffer.reset()", patch.proposed_diff)

    @patch("lib.ai_patch_generator.fetch_invocation_artifacts")
    def test_format_bug_body_with_ai_patch(self, mock_fetch):
        mock_fetch.return_value = {"downloaded_files": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca_summary.md"
            rca_file.write_text(
                "### 🔬 Jetski RCA Analysis\n\n"
                "**Root Cause Summary:** Test issue\n\n"
                "```diff\n"
                "--- a/test.cpp\n"
                "+++ b/test.cpp\n"
                "```\n",
                encoding="utf-8",
            )
            patch = generate_ai_patch_for_test(self.record, out_dir=tmpdir)
            body = format_bug_body_with_ai_patch(self.record, patch)
            self.assertIn("Flakiness Alert", body)
            self.assertIn(
                "//emulator/videobridge:in_process_media_provider_test", body
            )
            self.assertIn("AI-Suggested Root Cause", body)


if __name__ == "__main__":
    unittest.main()
