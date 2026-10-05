#!/usr/bin/env python3
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

"""Unit tests for the `emu-dev-cli gerrit` command suite."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from commands import gerrit
from lib.gerrit_api import GerritCommentMessage, GerritCommentThread


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emu-dev-cli")
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand")
    gerrit.register_parser(subparsers)
    return parser


class GerritCommandTest(unittest.TestCase):
    """Tests CLI subcommands under `emu-dev-cli gerrit`."""

    @mock.patch("commands.gerrit.cmd.GerritClient")
    def test_gerrit_list_renders_table_and_json(
        self, mock_client_cls: mock.MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.list_open_changes.return_value = [
            {
                "number": 42659931,
                "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
                "project": "platform/hardware/google/aemu",
                "branch": "main",
                "subject": "feat(emu-dev-cli): support new CI test identifiers",
                "status": "NEW",
                "current_revision": "52c6796fbd9fa882034a0ae9bc30b105d09909a3",
                "patch_set": 4,
                "unresolved_comment_count": 1,
                "code_review": 0,
                "presubmit_verified": 1,
                "updated": "2026-10-05 01:50:04",
                "url": "https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/42659931",
            }
        ]

        parser = _build_parser()
        args = parser.parse_args(["--json", "gerrit", "list", "--unresolved-only"])
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args.func(args)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(payload["changes"]), 1)
        self.assertEqual(payload["changes"][0]["number"], 42659931)

    @mock.patch("commands.gerrit.cmd.GerritClient")
    def test_gerrit_comments_outputs_dossier(
        self, mock_client_cls: mock.MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.get_change_detail.return_value = {
            "_number": 42659931,
            "subject": "feat(emu-dev-cli): support new CI test identifiers",
            "project": "platform/hardware/google/aemu",
            "branch": "main",
            "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
            "current_revision": "52c6796fbd9fa882034a0ae9bc30b105d09909a3",
            "revisions": {
                "52c6796fbd9fa882034a0ae9bc30b105d09909a3": {"_number": 4}
            },
        }
        mock_client.get_comment_threads.return_value = [
            GerritCommentThread(
                root_id="c1",
                leaf_id="c1",
                file_path="src/lib/resultstore_api.py",
                line=170,
                start_line=170,
                end_line=170,
                patch_set=4,
                commit_id="52c6796",
                unresolved=True,
                is_ai=True,
                author_name="groupagent-android-review",
                author_email="bot@google.com",
                updated="2026-10-05 01:50:04",
                messages=[
                    GerritCommentMessage(
                        comment_id="c1",
                        author_name="groupagent-android-review",
                        author_email="bot@google.com",
                        patch_set=4,
                        updated="2026-10-05 01:50:04",
                        message="Use from __future__ import annotations",
                        unresolved=True,
                        is_ai=True,
                    )
                ],
            )
        ]

        parser = _build_parser()
        args = parser.parse_args(["gerrit", "comments", "42659931"])
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args.func(args)
        output = buf.getvalue()
        self.assertIn("CL 42659931", output)
        self.assertIn("Use from __future__ import annotations", output)

    @mock.patch("commands.gerrit.cmd.GerritClient")
    def test_gerrit_reply_stage_and_publish_from_queue(
        self, mock_client_cls: mock.MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.post_review_replies.return_value = [{}]

        with tempfile.TemporaryDirectory() as tmp:
            q_file = Path(tmp) / "queue.json"
            q_data = {
                "mode": "single",
                "cls": [
                    {
                        "cl_number": 42659931,
                        "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
                        "unresolved_threads": [
                            {
                                "root_id": "c1",
                                "leaf_id": "c1",
                                "file_path": "src/foo.py",
                                "line": 170,
                                "patch_set": 4,
                                "staged_reply": "",
                                "resolve": True,
                                "published": False,
                            }
                        ],
                    }
                ],
            }
            q_file.write_text(json.dumps(q_data), encoding="utf-8")

            parser = _build_parser()
            stage_args = parser.parse_args(
                [
                    "gerrit",
                    "reply",
                    "42659931",
                    "--comment-id",
                    "c1",
                    "--message",
                    "Done: added future annotations",
                    "--queue-file",
                    str(q_file),
                    "--stage-only",
                ]
            )
            with mock.patch("sys.stdout", io.StringIO()):
                stage_args.func(stage_args)

            updated = json.loads(q_file.read_text(encoding="utf-8"))
            self.assertEqual(
                updated["cls"][0]["unresolved_threads"][0]["staged_reply"],
                "Done: added future annotations",
            )
            mock_client.post_review_replies.assert_not_called()

            pub_args = parser.parse_args(
                ["gerrit", "reply", "--from-queue", str(q_file)]
            )
            with mock.patch("sys.stdout", io.StringIO()):
                pub_args.func(pub_args)

            mock_client.post_review_replies.assert_called_once()
            published_q = json.loads(q_file.read_text(encoding="utf-8"))
            self.assertTrue(
                published_q["cls"][0]["unresolved_threads"][0]["published"]
            )

    @mock.patch("commands.gerrit.cmd.GerritClient")
    def test_gerrit_wait_outputs_outcome(
        self, mock_client_cls: mock.MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.get_change_detail.return_value = {
            "_number": 42659931,
            "current_revision": "abcd",
            "revisions": {"abcd": {"_number": 5}},
        }
        mock_client.wait_for_review_update.return_value = {
            "status": "new_comments",
            "new_threads": [],
            "unresolved_threads": [
                GerritCommentThread(
                    root_id="c2",
                    leaf_id="c2",
                    file_path="src/foo.py",
                    line=10,
                    start_line=10,
                    end_line=10,
                    patch_set=5,
                    commit_id="abcd",
                    unresolved=True,
                    is_ai=True,
                    author_name="groupagent-android-review",
                    author_email="bot@google.com",
                    updated="2026-10-05 02:00:00",
                )
            ],
            "detail": {"_number": 42659931},
        }

        parser = _build_parser()
        args = parser.parse_args(
            [
                "--json",
                "gerrit",
                "wait",
                "42659931",
                "--timeout",
                "10",
                "--require-presubmit",
            ]
        )
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args.func(args)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["event"], "new_comments")
        self.assertEqual(payload["unresolved_count"], 1)
        mock_client.wait_for_review_update.assert_called_once()
        self.assertTrue(
            mock_client.wait_for_review_update.call_args.kwargs.get(
                "require_presubmit"
            )
        )

    @mock.patch("commands.gerrit.cmd.find_sessions_for_cl")
    @mock.patch("commands.gerrit.cmd.resume_workflow_session")
    def test_gerrit_resume_finds_and_resumes_session(
        self,
        mock_resume: mock.MagicMock,
        mock_find: mock.MagicMock,
    ) -> None:
        mock_find.return_value = [
            {
                "state_id": "abc12345",
                "workflow_name": "address-review",
                "conversation_id": "conv-uuid-1234",
                "status": "DONE",
            }
        ]
        mock_resume.return_value = (
            True,
            "Session resumed.",
            {
                "state_id": "abc12345",
                "workflow_name": "address-review",
                "conversation_id": "conv-uuid-1234",
                "wake_status": "agentapi_sent",
            },
        )

        parser = _build_parser()
        args = parser.parse_args(["--json", "gerrit", "resume", "42659931"])
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args.func(args)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["state_id"], "abc12345")
        self.assertEqual(payload["wake_status"], "agentapi_sent")


if __name__ == "__main__":
    unittest.main()

