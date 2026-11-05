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
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from commands.crash.file_bug import (
    CrashFileBugCommand,
    _extract_crash_info_from_sandbox,
    register_file_bug_parser,
)
from core.context import ExecutionContext
from core.errors import CommandValidationError


class CrashFileBugTest(unittest.TestCase):
    """Tests for crash file-bug parser registration and Command execution."""

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
                "--force",
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
        self.assertTrue(args.force)
        self.assertIsInstance(
            getattr(args, "_command_instance"), CrashFileBugCommand
        )

    def test_validate_rejects_empty_crash_id(self):
        ctx = ExecutionContext.for_testing()
        cmd = CrashFileBugCommand()
        args = argparse.Namespace(crash_id="   ", from_rca=None)
        with self.assertRaises(CommandValidationError):
            cmd.validate(args, ctx)

    @patch("commands.crash.file_bug.CrashAdvisorRunner.run")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_file_bug_advisor(
        self, mock_acquire_token, mock_runner_run
    ):
        """Tests CrashFileBugCommand execution delegating to CrashAdvisor."""
        mock_acquire_token.return_value = "token_abc"

        ctx = ExecutionContext.for_testing()
        cmd = CrashFileBugCommand()
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            from_rca=None,
            token="token_abc",
            qa=True,
            verbose=True,
            force=True,
            work_dir=None,
            title="[Crash] Test Title",
            no_link_crash=False,
        )

        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        mock_runner_run.assert_called_once()
        call_kwargs = mock_runner_run.call_args[1]
        self.assertIn("--enable-buganizer", call_kwargs["flags"])
        self.assertIn("--auto-run", call_kwargs["flags"])
        self.assertIn("--qa", call_kwargs["flags"])
        self.assertIn("--force", call_kwargs["flags"])
        self.assertIn("--verbose", call_kwargs["flags"])
        self.assertIn("--title", call_kwargs["flags"])
        self.assertEqual(call_kwargs["token"], "token_abc")

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.create_crash_bug_tracker")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_from_rca_explicit_bug_id(
        self, mock_acquire_token, mock_create_tracker, mock_update_anno
    ):
        """Tests that --from-rca with explicit --bug-id updates the bug directly."""
        mock_acquire_token.return_value = "tok_direct"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_client = MagicMock()
        mock_create_tracker.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nExplicit bug update.", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            cmd = CrashFileBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="virtio_gpu_rutabaga_load-e709a6ba",
                bug_id="b/567123456",
                title=None,
                token="tok_direct",
                qa=False,
                verbose=False,
                force=False,
                no_link_crash=False,
                work_dir=tmpdir,
            )
            res = cmd.execute(args, ctx)

        self.assertTrue(res.ok)
        self.assertEqual(res.data["bug_id"], "567123456")
        self.assertEqual(res.data["issue_action"], "updated")
        mock_client.update_issue_comment.assert_called_once_with(
            567123456, "## Root Cause\nExplicit bug update."
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.create_crash_bug_tracker")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_from_rca_creates_and_links(
        self, mock_acquire_token, mock_create_tracker, mock_update_anno
    ):
        """Tests that --from-rca creates a Buganizer issue directly and links it in go/crash."""
        mock_acquire_token.return_value = "tok_direct"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = None
        mock_client.create_issue.return_value = {"issueId": "568999111"}
        mock_create_tracker.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text(
                "## Root Cause\nNull pointer in Rutabaga.", encoding="utf-8"
            )

            ctx = ExecutionContext.for_testing()
            cmd = CrashFileBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="virtio_gpu_rutabaga_load-e709a6ba",
                bug_id=None,
                title=None,
                token="tok_direct",
                qa=False,
                verbose=False,
                force=False,
                no_link_crash=False,
                work_dir=tmpdir,
            )
            res = cmd.execute(args, ctx)

        self.assertTrue(res.ok)
        self.assertEqual(res.data["bug_id"], "568999111")
        self.assertEqual(res.data["issue_action"], "created")
        mock_update_anno.assert_called_once_with(
            stable_signature="virtio_gpu_rutabaga_load-e709a6ba",
            bug_url="https://b.corp.google.com/issues/568999111",
            runner=ctx.process_runner,
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.create_crash_bug_tracker")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_from_rca_with_custom_title(
        self, mock_acquire_token, mock_create_tracker, mock_update_anno
    ):
        """Tests that --from-rca passes explicit --title to create_issue."""
        mock_acquire_token.return_value = "tok_direct"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = None
        mock_client.create_issue.return_value = {"issueId": "568999222"}
        mock_create_tracker.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nOOM in Rutabaga.", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            cmd = CrashFileBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="virtio_gpu_rutabaga_load-e709a6ba",
                title="[Crash][Linux] Rutabaga: OOM during texture load",
                bug_id=None,
                token="tok_direct",
                qa=False,
                verbose=False,
                force=False,
                no_link_crash=False,
                work_dir=tmpdir,
            )
            res = cmd.execute(args, ctx)

        self.assertTrue(res.ok)
        self.assertEqual(res.data["bug_id"], "568999222")

    def test_extract_crash_info_from_sandbox(self):
        """Tests extracting structured crash information from sandbox metadata and RCA markdown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": {
                    "operatingSystem": "Mac OS X",
                    "versions": ["37.1.3-16373346"],
                    "buildIds": ["16373346"],
                    "crashLocation": "virtio_gpu_rutabaga_load (rutabaga.c:42)",
                    "customKeys": [
                        {"key": "internal-msg", "value": "assertion failed"},
                    ],
                }
            }
            (Path(tmpdir) / "metadata.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            info = _extract_crash_info_from_sandbox(tmpdir, "Some RCA text")
            self.assertEqual(info["os"], ["Mac OS X"])
            self.assertEqual(info["versions"], ["37.1.3-16373346"])
            self.assertEqual(info["build_ids"], ["16373346"])
            self.assertEqual(
                info["crash_location"], "virtio_gpu_rutabaga_load (rutabaga.c:42)"
            )
            self.assertEqual(
                info["custom_keys"], {"internal-msg": "assertion failed"}
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": {
                    "operatingSystem": [
                        {"name": None},
                        None,
                        {"name": "Linux"},
                        "Mac OS X",
                        {"name": "Windows"},
                    ],
                    "versions": ["37.1.3-16373346"],
                    "customKeys": [
                        {"key": "internal-msg", "value": None},
                        {"key": "list_annotations", "value": "crumb_123"},
                        {"key": "exit_code", "value": 0},
                    ],
                },
                "custom_keys": {
                    "legacy_null_key": None,
                    "flag_disabled": False,
                },
            }
            (Path(tmpdir) / "metadata.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            info = _extract_crash_info_from_sandbox(tmpdir, "")
            self.assertEqual(info["os"], ["Linux", "Mac OS X", "Windows"])
            self.assertEqual(info["custom_keys"]["internal-msg"], "")
            self.assertEqual(info["custom_keys"]["list_annotations"], "crumb_123")
            self.assertEqual(info["custom_keys"]["exit_code"], "0")
            self.assertEqual(info["custom_keys"]["flag_disabled"], "False")

        with tempfile.TemporaryDirectory() as empty_tmpdir:
            rca_text = (
                "## Analysis\n"
                "- **Host OS:** Linux\n"
                "- **Version:** 37.1.4-16500000\n"
                "- **Build ID:** 16500000\n"
            )
            info = _extract_crash_info_from_sandbox(empty_tmpdir, rca_text)
            self.assertEqual(info["os"], ["Linux"])
            self.assertEqual(info["versions"], ["37.1.4-16500000"])
            self.assertEqual(info["build_ids"], ["16500000"])

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.create_crash_bug_tracker")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_from_rca_duplicate_skips_comment(
        self, mock_acquire_token, mock_create_tracker, mock_update_anno
    ):
        """Tests that a duplicate crash report with no force flag skips comment."""
        mock_acquire_token.return_value = "tok_dup"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "567125517",
            "issueState": {
                "title": "[Crash][macOS] Launcher: port failure (sig-dup)",
                "status": "ASSIGNED",
            },
        }
        mock_create_tracker.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nRepeated crash.", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            cmd = CrashFileBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="sig-dup",
                bug_id=None,
                title=None,
                force=False,
                token="tok_dup",
                qa=False,
                verbose=False,
                no_link_crash=False,
                work_dir=tmpdir,
            )
            res = cmd.execute(args, ctx)

        self.assertTrue(res.ok)
        self.assertEqual(res.data["issue_action"], "skipped_duplicate")
        self.assertEqual(res.data["bug_id"], "567125517")
        mock_client.update_issue_comment.assert_not_called()

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.create_crash_bug_tracker")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_execute_from_rca_duplicate_force_updates_comment(
        self, mock_acquire_token, mock_create_tracker, mock_update_anno
    ):
        """Tests that --force overrides deduplication and updates the Buganizer comment."""
        mock_acquire_token.return_value = "tok_dup"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_client = MagicMock()
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "567125517",
            "issueState": {
                "title": "[Crash][macOS] Launcher: port failure (sig-dup)",
                "status": "ASSIGNED",
            },
        }
        mock_create_tracker.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nForced update content.", encoding="utf-8")

            ctx = ExecutionContext.for_testing()
            cmd = CrashFileBugCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="sig-dup",
                bug_id=None,
                title=None,
                force=True,
                token="tok_dup",
                qa=False,
                verbose=False,
                no_link_crash=False,
                work_dir=tmpdir,
            )
            res = cmd.execute(args, ctx)

        self.assertTrue(res.ok)
        self.assertEqual(res.data["issue_action"], "updated")
        mock_client.update_issue_comment.assert_called_once_with(
            567125517, "## Root Cause\nForced update content."
        )


if __name__ == "__main__":
    unittest.main()
