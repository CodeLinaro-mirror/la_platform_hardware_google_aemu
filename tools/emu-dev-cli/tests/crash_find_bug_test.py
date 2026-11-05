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

"""Unit tests for commands.crash.find_bug module."""

import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from commands.crash.find_bug import (
    CrashFindBugCommand,
    register_find_bug_parser,
)
from core.context import ExecutionContext, SERVICE_BUGANIZER
from core.errors import CommandValidationError


class CrashFindBugTest(unittest.TestCase):
    """Tests for crash find-bug parser registration and execution handler."""

    def test_register_find_bug_parser(self):
        """Tests ArgumentParser registration for `find-bug` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_find_bug_parser(subparsers)

        args = parser.parse_args(["find-bug", "05d8356e2f800000", "--token", "tok_abc"])
        self.assertEqual(args.crash_cmd, "find-bug")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertEqual(args.token, "tok_abc")
        self.assertEqual(args.component_id, 29601)
        self.assertIsInstance(
            getattr(args, "_command_instance"), CrashFindBugCommand
        )

    def test_validate_rejects_empty_id(self):
        cmd = CrashFindBugCommand()
        ctx = ExecutionContext.for_testing()
        args = argparse.Namespace(crash_id="   ", component_id=29601)
        with self.assertRaises(CommandValidationError):
            cmd.validate(args, ctx)

    def test_validate_rejects_invalid_component_id(self):
        cmd = CrashFindBugCommand()
        ctx = ExecutionContext.for_testing()
        args = argparse.Namespace(crash_id="05d8356e2f800000", component_id=-1)
        with self.assertRaises(CommandValidationError):
            cmd.validate(args, ctx)

    @patch("commands.crash.find_bug.search_crash_annotations")
    @patch("commands.crash.find_bug.CrashAdvisorRunner.run")
    @patch("commands.crash.find_bug.acquire_auth_token")
    def test_run_find_bug_json(
        self,
        mock_acquire_token,
        mock_runner_run,
        mock_search_anno,
    ):
        """Tests CrashFindBugCommand.execute in JSON output mode including Tier 0 go/crash annotations."""
        mock_acquire_token.return_value = "token_xyz"
        mock_search_anno.return_value = {
            "android::FrameBuffer::post-a030f826": [
                {
                    "bug_id": "12345678",
                    "bug_url": "https://b.corp.google.com/issues/12345678",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": {
                    "stableSignature": "android::FrameBuffer::post-a030f826",
                    "product": {"version": "37.1.3-16373346"},
                }
            }
            (Path(tmpdir) / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (Path(tmpdir) / "crashreport.txt").write_text("Dummy dump", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            cmd = CrashFindBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_xyz",
                component_id=29601,
                work_dir=tmpdir,
                signature=None,
            )

            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(res.data["status"], "success")
            self.assertEqual(res.data["crash_id"], "05d8356e2f800000")
            self.assertEqual(res.data["candidates_found"], 1)
            self.assertEqual(
                res.data["candidates"][0]["confidence"],
                "EXACT (Linked in go/crash)",
            )

    @patch("commands.crash.find_bug.extract_top_fault_frame")
    @patch("commands.crash.find_bug.search_crash_annotations")
    @patch("commands.crash.find_bug.acquire_auth_token")
    def test_run_find_bug_deduplication(
        self,
        mock_acquire_token,
        mock_search_anno,
        mock_extract_top_frame,
    ):
        """Tests that Tier 1 and Tier 2 matching the same issueId deduplicates correctly."""
        mock_acquire_token.return_value = None
        mock_extract_top_frame.return_value = (
            "FrameBuffer::post",
            "FrameBuffer.cpp:10",
        )
        mock_search_anno.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": {
                    "stableSignature": "android::FrameBuffer::post",
                    "product": {"version": "37.1.3-16373346"},
                }
            }
            (Path(tmpdir) / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (Path(tmpdir) / "crashreport.txt").write_text("dummy dump", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            # Seed the FakeBuganizerService to return the same issue for both signature and function search
            buganizer = ctx.services.get(SERVICE_BUGANIZER)
            if buganizer:
                from clients.buganizer_service import BuganizerIssue
                issue = BuganizerIssue(
                    issue_id="12345678",
                    title="Crash in FrameBuffer: android::FrameBuffer::post",
                    status="ASSIGNED",
                    component_id=29601,
                )
                buganizer.seed_issue(issue)

            cmd = CrashFindBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token=None,
                component_id=29601,
                work_dir=tmpdir,
                signature="android::FrameBuffer::post",
            )

            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            # Should only have 1 candidate because 12345678 was deduplicated
            self.assertEqual(res.data["candidates_found"], 1)

    @patch("commands.crash.find_bug.evaluate_crash_fix_status")
    @patch("commands.crash.find_bug.search_crash_annotations")
    @patch("commands.crash.find_bug.acquire_auth_token")
    def test_run_find_bug_fixed_issue_and_older_build(
        self,
        mock_acquire_token,
        mock_search_anno,
        mock_eval_fix,
    ):
        """Tests that find_bug detects already fixed issues and includes build/version metadata."""
        mock_acquire_token.return_value = "tok_123"
        mock_search_anno.return_value = {}
        mock_fix_res = MagicMock(
            is_already_fixed=True,
            fix_status="FIXED",
            fix_reason="Fixed in build 15954000",
            is_older_build=True,
            older_version_warning="Crash occurred on older version 34.2.1-15953806",
        )
        mock_eval_fix.return_value = mock_fix_res

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": {
                    "product": {"Version": "34.2.1-15953806"},
                    "stableSignature": "android::FrameBuffer::post",
                }
            }
            (Path(tmpdir) / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (Path(tmpdir) / "crashreport.txt").write_text("dump", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            buganizer = ctx.services.get(SERVICE_BUGANIZER)
            if buganizer:
                from clients.buganizer_service import BuganizerIssue
                issue = BuganizerIssue(
                    issue_id="99988877",
                    title="Fixed Crash in FrameBuffer: android::FrameBuffer::post",
                    status="FIXED",
                    assignee="engineer@google.com",
                    component_id=29601,
                )
                buganizer.seed_issue(issue)

            cmd = CrashFindBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="tok_123",
                component_id=29601,
                work_dir=tmpdir,
                signature="android::FrameBuffer::post",
            )

            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(res.data["status"], "success")
            self.assertTrue(res.data["already_fixed"])
            self.assertEqual(res.data["fix_status"], "FIXED")
            self.assertEqual(res.data["build_id"], "15953806")
            self.assertTrue(res.data["is_older_build"])
            self.assertIn("older version", res.data["older_version_warning"].lower())
            self.assertTrue(res.data["candidates"][0]["is_fixed"])
            self.assertEqual(res.data["candidates"][0]["status"], "FIXED")


if __name__ == "__main__":
    unittest.main()
