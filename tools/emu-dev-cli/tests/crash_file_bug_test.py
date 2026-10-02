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

"""Unit tests for commands.crash.file_bug module."""

import argparse
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

from commands.crash.file_bug import (
    _auto_link_filed_bug_to_crash,
    register_file_bug_parser,
    run_file_bug,
)


class CrashFileBugTest(unittest.TestCase):
    """Tests for crash file-bug parser registration and execution handler."""

    def test_register_file_bug_parser(self):
        """Tests ArgumentParser registration for `file-bug` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_file_bug_parser(subparsers)

        args = parser.parse_args(
            [
                "file-bug",
                "05d8356e2f800000",
                "--token",
                "tok_123",
                "--qa",
                "--from-rca",
                "/tmp/rca.md",
                "--signature",
                "sig-123",
                "--title",
                "[Crash][macOS] Launcher: port failure",
                "--bug-id",
                "b/567125517",
            ]
        )
        self.assertEqual(args.crash_cmd, "file-bug")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertEqual(args.token, "tok_123")
        self.assertTrue(args.qa)
        self.assertEqual(args.from_rca, "/tmp/rca.md")
        self.assertEqual(args.signature, "sig-123")
        self.assertEqual(args.title, "[Crash][macOS] Launcher: port failure")
        self.assertEqual(args.bug_id, "b/567125517")
        self.assertEqual(args.func, run_file_bug)

    @patch("commands.crash.file_bug._auto_link_filed_bug_to_crash")
    @patch("commands.crash.file_bug.run_crashadvisor_bazel")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_success(
        self, mock_acquire_token, mock_run_bazel, mock_auto_link
    ):
        """Tests run_file_bug handler delegating to CrashAdvisor via Bazel and skipping auto-link on --qa."""
        mock_acquire_token.return_value = "token_abc"
        mock_run_bazel.return_value = MagicMock(returncode=0)

        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            token="token_abc",
            qa=True,
            verbose=False,
        )

        run_file_bug(args)
        mock_run_bazel.assert_called_once()
        mock_auto_link.assert_not_called()
        call_args = mock_run_bazel.call_args[0][0]
        self.assertEqual(call_args[0], "05d8356e2f800000")
        self.assertIn("--enable-buganizer", call_args)
        self.assertIn("--auto-run", call_args)
        self.assertIn("--work-dir", call_args)
        self.assertIn("--token", call_args)
        self.assertIn("--qa", call_args)

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_from_rca_creates_and_links(
        self, mock_acquire_token, mock_ensure_imports, mock_update_anno
    ):
        """Tests that --from-rca creates a Buganizer issue directly and links it in go/crash without Bazel."""
        mock_acquire_token.return_value = "tok_direct"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = None
        mock_client.create_issue.return_value = {"issueId": "568999111"}
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text(
                "## Root Cause\nNull pointer in Rutabaga.", encoding="utf-8"
            )

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="virtio_gpu_rutabaga_load-e709a6ba",
                bug_id=None,
                token="tok_direct",
                qa=False,
                verbose=False,
                json=False,
            )
            run_file_bug(args)

        mock_client.create_issue.assert_called_once_with(
            "virtio_gpu_rutabaga_load-e709a6ba",
            "## Root Cause\nNull pointer in Rutabaga.",
            title=None,
        )
        mock_update_anno.assert_called_once_with(
            stable_signature="virtio_gpu_rutabaga_load-e709a6ba",
            bug_url="https://b.corp.google.com/issues/568999111",
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_from_rca_with_custom_title(
        self, mock_acquire_token, mock_ensure_imports, mock_update_anno
    ):
        """Tests that --from-rca passes explicit --title to create_issue."""
        mock_acquire_token.return_value = "tok_direct"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = None
        mock_client.create_issue.return_value = {"issueId": "568999222"}
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nOOM in Rutabaga.", encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="virtio_gpu_rutabaga_load-e709a6ba",
                title="[Crash][Linux] Rutabaga: OOM during texture load",
                bug_id=None,
                token="tok_direct",
                qa=False,
                verbose=False,
                json=False,
            )
            run_file_bug(args)

        mock_client.create_issue.assert_called_once_with(
            "virtio_gpu_rutabaga_load-e709a6ba",
            "## Root Cause\nOOM in Rutabaga.",
            title="[Crash][Linux] Rutabaga: OOM during texture load",
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug. search_crash_annotations".replace(" ", ""))
    def test_auto_link_filed_bug_to_crash(
        self, mock_search_anno, mock_ensure_imports, mock_update_anno
    ):
        """Tests that _auto_link_filed_bug_to_crash links newly filed Buganizer issue to go/crash."""
        mock_search_anno.return_value = {}
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {"issueId": "567125517"}
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "metadata.json").write_text(
                '{"report_proto": {"stableSignature": "PthreadWaiter::Wait-a030f826"}}',
                encoding="utf-8",
            )
            _auto_link_filed_bug_to_crash(tmpdir, token="tok_xyz", verbose=False)

        mock_update_anno.assert_called_once_with(
            stable_signature="PthreadWaiter::Wait-a030f826",
            bug_url="https://b.corp.google.com/issues/567125517",
        )


if __name__ == "__main__":
    unittest.main()
