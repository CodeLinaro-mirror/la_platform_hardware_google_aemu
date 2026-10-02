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

"""Unit tests for the daily `triage-crashes` workflow (`workflows/triage_crashes/`)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT_DIR, "src")
WORKFLOW_DIR = os.path.join(ROOT_DIR, "workflows", "triage_crashes")
for p in (SRC_DIR, WORKFLOW_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import prepare_triage_batch
import verify_triage_batch
from commands.workflow import list_available_workflows


class TriageCrashesWorkflowTest(unittest.TestCase):
    """Tests for `emu-dev-cli workflow triage-crashes` scripts and discovery."""

    def test_workflow_is_discoverable(self):
        workflows = list_available_workflows()
        self.assertIn("triage-crashes", workflows)

    @patch("prepare_triage_batch.update_crash_annotation")
    @patch("prepare_triage_batch.ensure_crashadvisor_imports")
    @patch("prepare_triage_batch.acquire_auth_token")
    def test_try_auto_link_existing_buganizer_matches(
        self, mock_token, mock_ensure_imports, mock_update_anno
    ):
        mock_token.return_value = "tok_123"
        mock_update_anno.return_value = (True, "status_code: SUCCESS")

        mock_buganizer = MagicMock()
        mock_client = MagicMock()

        def fake_search(sig):
            if sig == "sig_already_in_buganizer":
                return {
                    "issueId": "567125517",
                    "issueState": {"title": "Existing crash", "status": "ASSIGNED"},
                }
            if sig == "FailWithoutStackTrace-7a37e8e9":
                return {
                    "issueId": "564510102",
                    "issueState": {
                        "title": "[Crash] absl::lts_20260526::log_internal::LogMessage::FailWithoutStackTrace-b59fb732",
                        "status": "ASSIGNED",
                    },
                }
            return None

        mock_client.search_issue_by_signature.side_effect = fake_search
        mock_buganizer.BuganizerClient.return_value = mock_client
        mock_ensure_imports.return_value = {"buganizer": mock_buganizer}

        clusters = [
            {
                "stable_signature": "sig_already_in_buganizer",
                "occurrences": 5,
                "latest_report_id": "aaaa000011112222",
            },
            {
                "stable_signature": "FailWithoutStackTrace-7a37e8e9",
                "occurrences": 1,
                "latest_report_id": "cccc000011112222",
            },
            {
                "stable_signature": "sig_needs_agent_triage",
                "occurrences": 2,
                "latest_report_id": "bbbb000011112222",
            },
        ]

        remaining, auto_linked = (
            prepare_triage_batch.try_auto_link_existing_buganizer_matches(clusters)
        )
        self.assertEqual(len(auto_linked), 1)
        self.assertEqual(auto_linked[0]["bug_ids"], ["567125517"])
        self.assertEqual(len(remaining), 2)
        self.assertEqual(
            [r["stable_signature"] for r in remaining],
            ["FailWithoutStackTrace-7a37e8e9", "sig_needs_agent_triage"],
        )
        mock_update_anno.assert_called_once_with(
            stable_signature="sig_already_in_buganizer",
            bug_url="https://b.corp.google.com/issues/567125517",
        )

    def test_render_dossier_markdown_includes_stack_frames_and_commands(self):
        remaining = [
            {
                "stable_signature": "virtio_gpu_rutabaga_load-e709a6ba",
                "occurrences": 4,
                "latest_report_id": "7ebbb53ff0400000",
                "versions": ["37.1.2-16173978"],
                "build_ids": ["16173978"],
                "os": ["Mac OS X"],
                "crash_location": "virtio_gpu_rutabaga_load (rutabaga.c:42)",
                "stack_frames": [
                    "#0: virtio_gpu_rutabaga_load (rutabaga.c:42) [qemu-system-aarch64]",
                    "#1: vmstate_load_state (vmstate.c:168) [qemu-system-aarch64]",
                ],
                "custom_keys": {
                    "looper_registrations": "1=GlobalLoop;3=QemuMainLoop;",
                },
            }
        ]
        md = prepare_triage_batch.render_dossier_markdown(
            hours=48,
            remaining=remaining,
            auto_linked=[],
            cache_dir="/tmp/triage-cache",
        )
        self.assertIn("virtio_gpu_rutabaga_load-e709a6ba", md)
        self.assertIn("#0: virtio_gpu_rutabaga_load (rutabaga.c:42)", md)
        self.assertIn('--from-rca "/tmp/triage-cache/rca-7ebbb53ff0400000.md"', md)

    @patch("verify_triage_batch.search_crash_annotations")
    def test_verify_queue_blocks_unlinked_and_passes_when_linked(
        self, mock_search_anno
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            q_path = Path(tmpdir) / "queue-1234.json"
            d_path = Path(tmpdir) / "dossier-1234.md"
            d_path.write_text("# Dossier\n", encoding="utf-8")
            q_path.write_text(
                json.dumps(
                    {
                        "hours": 48,
                        "limit": 10,
                        "remaining_signatures": ["sig_1"],
                        "remaining_crashes": [
                            {
                                "stable_signature": "sig_1",
                                "latest_report_id": "1111222233334444",
                                "occurrences": 3,
                            }
                        ],
                        "verified_links": {},
                    }
                ),
                encoding="utf-8",
            )

            # 1. Unlinked -> returns ok=False
            mock_search_anno.return_value = {}
            ok, msg, _ = verify_triage_batch.verify_queue(q_path, d_path)
            self.assertFalse(ok)
            self.assertIn("sig_1", msg)

            # 2. Linked -> returns ok=True and appends verified section to dossier
            mock_search_anno.return_value = {
                "sig_1": [
                    {
                        "bug_id": "567125517",
                        "bug_url": "https://b.corp.google.com/issues/567125517",
                    }
                ]
            }
            ok, msg, _ = verify_triage_batch.verify_queue(q_path, d_path)
            self.assertTrue(ok)
            self.assertIn(
                "Verified `go/crash` Bug Links", d_path.read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
