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
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.find_bug import register_find_bug_parser, run_find_bug


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
        self.assertEqual(args.func, run_find_bug)

    @patch("commands.crash.find_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.find_bug.acquire_auth_token")
    @patch("commands.crash.find_bug.print_result")
    def test_run_find_bug_json(
        self, mock_print_res, mock_acquire_token, mock_ensure_imports
    ):
        """Tests run_find_bug handler execution in JSON output mode."""
        mock_acquire_token.return_value = "token_xyz"
        mock_api = MagicMock()
        mock_buganizer = MagicMock()
        mock_buganizer.EMULATOR_COMPONENT_ID = 29601
        mock_context = MagicMock()
        mock_client = MagicMock()
        mock_metadata = MagicMock()

        # Mock metadata object
        mock_meta_instance = MagicMock()
        mock_meta_instance.primary_signature = "android::FrameBuffer::post"
        mock_metadata.CrashMetadata.return_value = mock_meta_instance

        # Mock client search return
        mock_client_instance = MagicMock()
        mock_client_instance.search_issue_by_signature.return_value = {
            "issueId": "12345678",
            "issueState": {"title": "Crash in FrameBuffer", "status": "ASSIGNED"},
        }
        mock_buganizer.BuganizerClient.return_value = mock_client_instance

        mock_ensure_imports.return_value = {
            "buganizer": mock_buganizer,
            "context": mock_context,
            "client": mock_client,
            "api": mock_api,
            "metadata": mock_metadata,
            "symbols": MagicMock(),
            "dump": MagicMock(),
        }

        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            token="token_xyz",
            component_id=29601,
            json=True,
            verbose=False,
        )

        run_find_bug(args)
        mock_print_res.assert_called_once()
        res_arg = mock_print_res.call_args[0][0]
        self.assertEqual(res_arg["status"], "success")
        self.assertEqual(res_arg["crash_id"], "05d8356e2f800000")
        self.assertEqual(res_arg["candidates_found"], 1)

    @patch("commands.crash.find_bug.extract_top_fault_frame")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("subprocess.run")
    @patch("commands.crash.find_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.find_bug.acquire_auth_token")
    @patch("commands.crash.find_bug.print_result")
    def test_run_find_bug_deduplication(
        self,
        mock_print_res,
        mock_acquire_token,
        mock_ensure_imports,
        mock_subproc_run,
        mock_read_text,
        mock_exists,
        mock_extract_top_frame,
    ):
        """Tests that Tier 1 and Tier 2 matching the same issueId deduplicates correctly."""
        mock_acquire_token.return_value = None
        mock_exists.return_value = True
        mock_read_text.return_value = "dummy dump"
        mock_extract_top_frame.return_value = (
            "FrameBuffer::post",
            "FrameBuffer.cpp:10",
        )

        mock_buganizer = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.token = None
        mock_client_instance.cli_binary = "b"
        # Tier 1 returns issue 12345678
        mock_client_instance.search_issue_by_signature.return_value = {
            "issueId": "12345678",
            "issueState": {"title": "Crash in FrameBuffer", "status": "ASSIGNED"},
        }
        mock_buganizer.BuganizerClient.return_value = mock_client_instance

        mock_metadata = MagicMock()
        mock_meta_instance = MagicMock()
        mock_meta_instance.primary_signature = "android::FrameBuffer::post"
        mock_metadata.CrashMetadata.return_value = mock_meta_instance

        mock_ensure_imports.return_value = {
            "buganizer": mock_buganizer,
            "context": MagicMock(),
            "client": MagicMock(),
            "api": MagicMock(),
            "metadata": mock_metadata,
            "symbols": MagicMock(),
            "dump": MagicMock(),
        }

        # Subprocess search for Tier 2 returns the SAME issue 12345678
        mock_subproc_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"issueId": "12345678", "issueState": {"title": "Same issue"}}]',
        )

        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            token=None,
            component_id=29601,
            json=True,
            verbose=False,
        )

        run_find_bug(args)
        mock_print_res.assert_called_once()
        res_arg = mock_print_res.call_args[0][0]
        # Should only have 1 candidate because 12345678 was already added in Tier 1
        self.assertEqual(res_arg["candidates_found"], 1)

    @patch("commands.crash.find_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.find_bug.acquire_auth_token")
    @patch("commands.crash.find_bug.print_result")
    def test_run_find_bug_fixed_issue_and_older_build(
        self, mock_print_res, mock_acquire_token, mock_ensure_imports
    ):
        """Tests that find_bug detects already fixed issues and includes build/version metadata."""
        mock_acquire_token.return_value = "tok_123"
        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "99988877",
            "issueState": {
                "title": "Fixed Crash in FrameBuffer",
                "status": "FIXED",
                "assignee": {"emailAddress": "engineer@google.com"},
            },
        }
        mock_buganizer.BuganizerClient.return_value = mock_client

        mock_metadata = MagicMock()
        mock_meta_instance = MagicMock()
        mock_meta_instance.primary_signature = "android::FrameBuffer::post"
        mock_meta_instance.build_id = "15953806"
        mock_meta_instance.data = {
            "report_proto": {
                "product": {"Version": "34.2.1-15953806"},
                "stableSignature": "android::FrameBuffer::post",
            }
        }
        mock_metadata.CrashMetadata.return_value = mock_meta_instance

        mock_ensure_imports.return_value = {
            "buganizer": mock_buganizer,
            "context": MagicMock(),
            "client": MagicMock(),
            "api": MagicMock(),
            "metadata": mock_metadata,
            "symbols": MagicMock(),
            "dump": MagicMock(),
        }

        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            token="tok_123",
            component_id=29601,
            json=True,
            verbose=False,
        )

        run_find_bug(args)
        mock_print_res.assert_called_once()
        res = mock_print_res.call_args[0][0]
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["already_fixed"])
        self.assertEqual(res["fix_status"], "FIXED")
        self.assertEqual(res["build_id"], "15953806")
        self.assertTrue(res["is_older_build"])
        self.assertIn("older version", res["older_version_warning"].lower())
        self.assertTrue(res["candidates"][0]["is_fixed"])
        self.assertEqual(res["candidates"][0]["status"], "FIXED")


if __name__ == "__main__":
    unittest.main()
