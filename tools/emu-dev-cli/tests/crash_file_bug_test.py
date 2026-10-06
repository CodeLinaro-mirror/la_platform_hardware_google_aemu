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
    _extract_crash_info_from_sandbox,
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
            force=True,
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
        self.assertIn("--force", call_args)

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
        mock_client.ensure_crash_hotlists.assert_called_once()
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
        mock_client.clean_version_string.return_value = None
        mock_client.search_issue_by_signature.return_value = None
        mock_client.create_issue.return_value = {"issueId": "b/568999222"}
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
        mock_client.ensure_crash_hotlists.assert_called_once_with(
            568999222, version=None
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
        mock_client.search_issue_by_signature.return_value = {"issueId": "b/567125517"}
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

        # Multi-OS list (with mixed dicts, strings, and null names) and null/falsy custom key values
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

        # Explicit report_proto: null in metadata.json fallback to top-level fields
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "report_proto": None,
                "version": "37.1.5-16600000",
                "os_name": "Linux",
                "build_id": "16600000",
                "crash_location": "qemu_mutex_lock (lock.c:10)",
                "custom_keys": {"key1": "val1"},
            }
            (Path(tmpdir) / "metadata.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            info = _extract_crash_info_from_sandbox(tmpdir, "")
            self.assertEqual(info["os"], ["Linux"])
            self.assertEqual(info["versions"], ["37.1.5-16600000"])
            self.assertEqual(info["build_ids"], ["16600000"])
            self.assertEqual(info["crash_location"], "qemu_mutex_lock (lock.c:10)")
            self.assertEqual(info["custom_keys"], {"key1": "val1"})

        # Fallback to RCA markdown when metadata is missing
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
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_duplicate_no_new_info_skips_comment(
        self, mock_acquire_token, mock_ensure_imports, mock_update_anno
    ):
        """Tests that a duplicate crash report with no novel info skips commenting on Buganizer."""
        mock_acquire_token.return_value = "tok_dup"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.clean_version_string.return_value = "37.1.3"
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "567125517",
            "issueState": {
                "title": "[Crash][macOS] Launcher: port failure (sig-dup)",
                "status": "ASSIGNED",
            },
        }
        mock_client.get_issue_comments.return_value = [
            {"comment": "Initial root cause"}
        ]
        mock_client.has_new_crash_information.return_value = (False, [])
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nRepeated crash.", encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="sig-dup",
                bug_id=None,
                force=False,
                token="tok_dup",
                qa=False,
                verbose=False,
                json=False,
            )
            run_file_bug(args)

        mock_client.ensure_crash_hotlists.assert_called_once_with(
            567125517, version="37.1.3"
        )
        mock_client.update_issue_comment.assert_not_called()
        mock_client.create_issue.assert_not_called()
        mock_update_anno.assert_called_once_with(
            stable_signature="sig-dup",
            bug_url="https://b.corp.google.com/issues/567125517",
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_duplicate_with_new_info_adds_comment(
        self, mock_acquire_token, mock_ensure_imports, mock_update_anno
    ):
        """Tests that a duplicate crash report with novel info (e.g. new OS platform) updates Buganizer."""
        mock_acquire_token.return_value = "tok_dup"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.clean_version_string.return_value = "37.1.3"
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "567125517",
            "issueState": {
                "title": "[Crash][macOS] Launcher: port failure (sig-dup)",
                "status": "ASSIGNED",
            },
        }
        mock_client.get_issue_comments.return_value = [
            {"comment": "Initial macOS root cause"}
        ]
        reasons = ["Novel host OS platform: Linux (existing issue only mentions: macOS)"]
        mock_client.has_new_crash_information.return_value = (True, reasons)
        mock_client.format_new_crash_info_comment.return_value = (
            "### Additional Crash Occurrence Detected (New Information)\n- Novel OS: Linux"
        )
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nRepeated crash on Linux.", encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="sig-dup",
                bug_id=None,
                force=False,
                token="tok_dup",
                qa=False,
                verbose=False,
                json=False,
            )
            run_file_bug(args)

        mock_client.ensure_crash_hotlists.assert_called_once_with(
            567125517, version="37.1.3"
        )
        mock_client.format_new_crash_info_comment.assert_called_once()
        mock_client.update_issue_comment.assert_called_once_with(
            567125517,
            "### Additional Crash Occurrence Detected (New Information)\n- Novel OS: Linux",
        )
        mock_update_anno.assert_called_once_with(
            stable_signature="sig-dup",
            bug_url="https://b.corp.google.com/issues/567125517",
        )

    @patch("commands.crash.file_bug.update_crash_annotation")
    @patch("commands.crash.file_bug.ensure_crashadvisor_imports")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_duplicate_force_updates_comment(
        self, mock_acquire_token, mock_ensure_imports, mock_update_anno
    ):
        """Tests that --force overrides deduplication and updates the Buganizer comment."""
        mock_acquire_token.return_value = "tok_dup"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()
        mock_client.clean_version_string.return_value = "37.1.3"
        mock_client.search_issue_by_signature.return_value = {
            "issueId": "567125517",
            "issueState": {
                "title": "[Crash][macOS] Launcher: port failure (sig-dup)",
                "status": "ASSIGNED",
            },
        }
        mock_client.get_issue_comments.return_value = []
        mock_client.has_new_crash_information.return_value = (False, [])
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        with tempfile.TemporaryDirectory() as tmpdir:
            rca_file = Path(tmpdir) / "rca.md"
            rca_file.write_text("## Root Cause\nForced update content.", encoding="utf-8")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                signature="sig-dup",
                bug_id=None,
                force=True,
                token="tok_dup",
                qa=False,
                verbose=False,
                json=False,
            )
            run_file_bug(args)

        mock_client.update_issue_comment.assert_called_once_with(
            567125517, "## Root Cause\nForced update content."
        )


if __name__ == "__main__":
    unittest.main()
