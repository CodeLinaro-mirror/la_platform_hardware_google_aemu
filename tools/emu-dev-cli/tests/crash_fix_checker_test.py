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

"""Unit tests for commands.crash.fix_checker module."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.fix_checker import (
    FixStatusResult,
    check_buganizer_fixed_status,
    check_git_history_fixes,
    evaluate_crash_fix_status,
    extract_crash_version_info,
    format_agent_version_guardrail_prompt,
)


class CrashFixCheckerTest(unittest.TestCase):
    """Tests for crash fix detection and older repository build awareness."""

    def test_extract_crash_version_info_hyphenated(self):
        """Tests extracting Build ID from product version string with build suffix."""
        meta = {
            "report_proto": {
                "product": {
                    "Name": "Android Emulator",
                    "Version": "34.2.1-15953806",
                },
                "os": {"Name": "Linux", "Version": "6.6.0"},
                "cpu": {"Architecture": "amd64"},
                "crashTime": "2026-07-20T10:00:00Z",
                "productdata": [{"Key": "git_branch", "Value": "emu-main-next"}],
            }
        }
        info = extract_crash_version_info(meta)
        self.assertEqual(info["build_id"], "15953806")
        self.assertEqual(info["version_str"], "34.2.1-15953806")
        self.assertEqual(info["product_name"], "Android Emulator")
        self.assertEqual(info["os_name"], "Linux")
        self.assertEqual(info["architecture"], "amd64")

    def test_extract_crash_version_info_fallback(self):
        """Tests extracting version when product info is partial or plain build ID."""
        meta = {
            "report_proto": {
                "product": {"Version": "15900000"},
            }
        }
        info = extract_crash_version_info(meta)
        self.assertEqual(info["build_id"], "15900000")
        self.assertEqual(info["version_str"], "15900000")

    def test_check_buganizer_fixed_status_finds_fixed(self):
        """Tests detecting Buganizer issues with FIXED or VERIFIED status."""
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "12345678",
            "issueState": {
                "title": "Crash in FrameBuffer",
                "status": "FIXED",
                "assignee": {"emailAddress": "dev@google.com"},
            },
        }

        is_fixed, issues, reason = check_buganizer_fixed_status(
            mock_client, stable_signature="android::FrameBuffer::post"
        )
        self.assertTrue(is_fixed)
        self.assertEqual(len(issues), 1)
        self.assertIn("FIXED", reason)
        self.assertIn("12345678", reason)

    def test_check_buganizer_fixed_status_open(self):
        """Tests detecting Buganizer issues with ASSIGNED or OPEN status."""
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "87654321",
            "issueState": {
                "title": "Open crash issue",
                "status": "ASSIGNED",
                "assignee": {"emailAddress": "triage@google.com"},
            },
        }

        is_fixed, issues, reason = check_buganizer_fixed_status(
            mock_client, stable_signature="android::FrameBuffer::post"
        )
        self.assertFalse(is_fixed)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["status"], "ASSIGNED")

    @patch("subprocess.run")
    def test_check_git_history_fixes(self, mock_subproc):
        """Tests checking local git log for recent fixes and commits."""
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout="a1b2c3d Fix null pointer crash in FrameBuffer::post\n9e8d7c6 Refactor rendering pipeline\n",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            target_file = Path(tmp_dir) / "android" / "FrameBuffer.cpp"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(
                "void post(Buffer* buf) { if (!buf) return; }", encoding="utf-8"
            )

            has_fix, commits, summary = check_git_history_fixes(
                source_dir=tmp_dir,
                target_file="android/FrameBuffer.cpp",
                target_func="post",
                issue_id="12345678",
            )
            self.assertTrue(has_fix)
            self.assertGreater(len(commits), 0)
            self.assertIn("Fix null pointer", summary)

    def test_evaluate_crash_fix_status_full(self):
        """Tests overall fix status evaluation and older build warning generation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_path = Path(tmp_dir) / "metadata.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "report_proto": {
                            "product": {"Version": "34.2.1-15953806"},
                            "stableSignature": "android::FrameBuffer::post",
                        }
                    }
                ),
                encoding="utf-8",
            )

            mock_client = MagicMock()
            mock_client.search_issue_by_signature.return_value = {
                "issueId": "12345678",
                "issueState": {
                    "title": "Crash in FrameBuffer",
                    "status": "FIXED",
                    "assignee": {"emailAddress": "alice@google.com"},
                },
            }

            res = evaluate_crash_fix_status(
                crash_id="05d8356e2f800000",
                metadata_path=meta_path,
                action_data={
                    "target_file": "android/FrameBuffer.cpp",
                    "target_function": "post",
                    "remediation_summary": "Null-check pointer",
                },
                buganizer_client=mock_client,
            )

            self.assertTrue(res.is_already_fixed)
            self.assertEqual(res.build_id, "15953806")
            self.assertEqual(res.fix_status, "FIXED")
            self.assertTrue(res.is_older_build)
            self.assertIn("older version", res.older_version_warning.lower())
            self.assertIn("Inspect git log", res.agent_version_notice)

    def test_format_agent_version_guardrail_prompt(self):
        """Tests that the guardrail prompt instructs the agent not to re-fix already fixed code."""
        fix_res = FixStatusResult(
            is_already_fixed=True,
            fix_status="FIXED",
            fix_reason="Buganizer issue b/12345678 is FIXED",
            build_id="15953806",
            version_str="34.2.1-15953806",
            is_older_build=True,
            older_version_warning="Crash captured on older build 15953806",
            fixed_issues=[{"issue_id": "12345678", "status": "FIXED"}],
            relevant_commits=["abc1234 Fix null dereference"],
            agent_version_notice="",
        )

        prompt = format_agent_version_guardrail_prompt(
            crash_id="05d8356e2f800000",
            rca_path="/tmp/rca_summary.md",
            target_file="android/FrameBuffer.cpp",
            local_file_path="/work/emu-main-next/android/FrameBuffer.cpp",
            target_func="post",
            remediation="Null-check pointer",
            fix_status=fix_res,
        )

        self.assertIn("15953806", prompt)
        self.assertIn("older version", prompt.lower())
        self.assertIn("git log", prompt.lower())
        self.assertIn("ALREADY FIXED", prompt)
        self.assertIn("Do NOT introduce redundant", prompt)


if __name__ == "__main__":
    unittest.main()
