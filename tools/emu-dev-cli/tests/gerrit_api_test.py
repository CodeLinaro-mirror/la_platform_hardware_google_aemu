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

"""Unit tests for Gerrit REST API client and Git workspace helpers."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lib.gerrit_api import (
    GerritClient,
    GerritCommentMessage,
    GerritCommentThread,
    GerritReplyInput,
    extract_source_snippet,
    parse_cl_identifier,
    resolve_project_repo_path,
    strip_xssi_prefix,
)


class GerritApiTest(unittest.TestCase):
    """Tests observable contracts of GerritClient and CL resolution helpers."""

    def test_parse_cl_identifier_from_urls_and_ids(self) -> None:
        self.assertEqual(
            parse_cl_identifier(
                "https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/42659931?tab=comments"
            ),
            "42659931",
        )
        self.assertEqual(
            parse_cl_identifier(
                "https://googleplex-android-review.git.corp.google.com/c/platform/build/bazel/+/42629139/3"
            ),
            "42629139",
        )
        self.assertEqual(parse_cl_identifier("42659931"), "42659931")
        self.assertEqual(
            parse_cl_identifier("I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1"),
            "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
        )

    @mock.patch("subprocess.run")
    def test_parse_cl_identifier_from_head(self, mock_run: mock.MagicMock) -> None:
        mock_run.return_value = mock.MagicMock(
            returncode=0,
            stdout=(
                "feat(emu-dev-cli): add gerrit workflow\n\n"
                "Bug: 123456\n"
                "Test: unit tests\n"
                "Change-Id: I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1\n"
            ),
        )
        cid = parse_cl_identifier("HEAD", cwd=Path("."))
        self.assertEqual(cid, "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1")

    def test_strip_xssi_prefix_handles_gerrit_and_gosso_headers(self) -> None:
        raw_gerrit = ")]}'\n{\"_number\": 42659931}"
        self.assertEqual(strip_xssi_prefix(raw_gerrit), '{"_number": 42659931}')

        raw_gosso = (
            "2026/10/05 05:31:48 Establishing SSO session with googleplex-android-review...\n"
            ")]}'\n"
            '{"_number": 42659931}'
        )
        self.assertEqual(strip_xssi_prefix(raw_gosso), '{"_number": 42659931}')

    def test_get_comment_threads_reconstructs_and_filters_unresolved(self) -> None:
        client = GerritClient(token="fake-token")
        fake_comments = {
            "tools/emu-dev-cli/src/lib/resultstore_api.py": [
                {
                    "id": "root_resolved",
                    "patch_set": 2,
                    "line": 120,
                    "unresolved": True,
                    "updated": "2026-10-03 04:08:17.000000000",
                    "author": {"name": "groupagent-android-review", "email": "bot@google.com"},
                    "tag": "cl_review_agent",
                    "is_ai": True,
                    "message": "[Gerrit Review Agent] First issue",
                },
                {
                    "id": "reply_resolved",
                    "in_reply_to": "root_resolved",
                    "patch_set": 2,
                    "line": 120,
                    "unresolved": False,
                    "updated": "2026-10-05 01:37:49.000000000",
                    "author": {"name": "Erwin Jansen", "email": "jansene@google.com"},
                    "message": "Done",
                },
                {
                    "id": "root_unresolved",
                    "patch_set": 4,
                    "line": 170,
                    "range": {"start_line": 168, "end_line": 170},
                    "unresolved": True,
                    "updated": "2026-10-05 01:50:04.000000000",
                    "author": {"name": "groupagent-android-review", "email": "bot@google.com"},
                    "tag": "cl_review_agent",
                    "is_ai": True,
                    "commit_id": "52c6796fbd9fa882034a0ae9bc30b105d09909a3",
                    "message": "[Gerrit Review Agent] Python 3.8 Type Subscriptability",
                },
            ]
        }

        with mock.patch.object(
            client, "_request_json", side_effect=[fake_comments, {}]
        ):
            unresolved_threads = client.get_comment_threads(
                "42659931", include_resolved=False
            )
            self.assertEqual(len(unresolved_threads), 1)
            t = unresolved_threads[0]
            self.assertEqual(t.root_id, "root_unresolved")
            self.assertEqual(t.leaf_id, "root_unresolved")
            self.assertEqual(t.line, 170)
            self.assertEqual(t.patch_set, 4)
            self.assertTrue(t.unresolved)
            self.assertTrue(t.is_ai)

        with mock.patch.object(
            client, "_request_json", side_effect=[fake_comments, {}]
        ):
            all_threads = client.get_comment_threads(
                "42659931", include_resolved=True
            )
            self.assertEqual(len(all_threads), 2)
            resolved_t = [th for th in all_threads if th.root_id == "root_resolved"][0]
            self.assertFalse(resolved_t.unresolved)
            self.assertEqual(resolved_t.leaf_id, "reply_resolved")
            self.assertEqual(len(resolved_t.messages), 2)

    def test_post_review_replies_batches_by_patchset(self) -> None:
        client = GerritClient(token="fake-token")
        replies = [
            GerritReplyInput(
                comment_id="c_ps2",
                file_path="src/foo.py",
                line=42,
                patch_set=2,
                message="Done in foo.py",
                resolve=True,
            ),
            GerritReplyInput(
                comment_id="c_ps3",
                file_path="src/bar.py",
                line=99,
                patch_set=3,
                message="Fixed bar.py",
                resolve=True,
            ),
        ]

        with mock.patch.object(client, "_request_json", return_value={}) as mock_req:
            client.post_review_replies(
                "42659931",
                replies=replies,
                top_message="Addressed review comments.",
            )
            self.assertEqual(mock_req.call_count, 3)
            calls = mock_req.call_args_list
            self.assertEqual(calls[0][0][0], "/a/changes/42659931/revisions/2/review")
            self.assertEqual(
                calls[0][1]["data"]["comments"]["src/foo.py"][0]["in_reply_to"],
                "c_ps2",
            )
            self.assertFalse(
                calls[0][1]["data"]["comments"]["src/foo.py"][0]["unresolved"]
            )
            self.assertEqual(calls[1][0][0], "/a/changes/42659931/revisions/3/review")
            self.assertEqual(
                calls[2][0][0], "/a/changes/42659931/revisions/current/review"
            )
            self.assertNotIn("labels", calls[2][1]["data"])

    def test_gerrit_client_blocks_code_review_voting_and_submit(self) -> None:
        client = GerritClient(token="fake-token")
        with self.assertRaises(PermissionError):
            client._request_json(
                "/a/changes/42659931/revisions/current/review",
                method="POST",
                data={"labels": {"Code-Review": 2}},
            )
        with self.assertRaises(PermissionError):
            client._request_json(
                "/a/changes/42659931/submit",
                method="POST",
                data={},
            )

    @mock.patch("urllib.request.urlopen")
    def test_gerrit_client_normalizes_corp_host_to_googlesource_for_rest_api(
        self, mock_urlopen: mock.MagicMock
    ) -> None:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = b")]}'\n{}"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = GerritClient(
            host="https://googleplex-android-review.git.corp.google.com",
            token="fake-token",
        )
        client._request_json(
            "/a/changes/42713016/revisions/2/review",
            method="POST",
            data={"comments": {}},
        )
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url,
            "https://googleplex-android-review.googlesource.com/a/changes/42713016/revisions/2/review",
        )
        self.assertEqual(req.get_method(), "POST")

    def test_wait_for_review_update_detects_new_comments_and_ai_completion(self) -> None:
        client = GerritClient(token="fake-token")
        fake_thread = GerritCommentThread(
            root_id="new_c1",
            leaf_id="new_c1",
            file_path="src/a.py",
            line=10,
            start_line=10,
            end_line=10,
            patch_set=4,
            commit_id="abc",
            unresolved=True,
            is_ai=True,
            author_name="groupagent-android-review",
            author_email="bot@google.com",
            updated="2026-10-05 02:00:00",
            messages=[
                GerritCommentMessage(
                    comment_id="new_c1",
                    author_name="groupagent-android-review",
                    author_email="bot@google.com",
                    patch_set=4,
                    updated="2026-10-05 02:00:00",
                    message="New issue found",
                    unresolved=True,
                    is_ai=True,
                )
            ],
        )

        detail_initial = {
            "_number": 42659931,
            "current_revision": "rev4",
            "revisions": {"rev4": {"_number": 4}},
            "messages": [],
            "labels": {},
        }
        clock_ticks = iter([0.0, 5.0, 10.0, 15.0])

        with mock.patch.object(
            client,
            "get_change_detail",
            side_effect=[detail_initial, detail_initial],
        ), mock.patch.object(
            client,
            "get_comment_threads",
            side_effect=[[], [fake_thread]],
        ):
            res = client.wait_for_review_update(
                "42659931",
                baseline_patchset=4,
                known_comment_ids=set(),
                timeout=timedelta(seconds=60),
                poll_interval=timedelta(seconds=5),
                sleep_fn=lambda _: None,
                clock_fn=lambda: next(clock_ticks),
            )
            self.assertEqual(res["status"], "new_comments")
            self.assertEqual(len(res["new_threads"]), 1)

    def test_wait_for_review_update_waits_for_ai_when_sticky_cr2_present(self) -> None:
        client = GerritClient(token="fake-token")
        detail_sticky_cr2_no_ai = {
            "_number": 42713016,
            "current_revision": "rev2",
            "revisions": {"rev2": {"_number": 2}},
            "messages": [
                {
                    "_revision_number": 1,
                    "author": {"name": "groupagent-android-emulator"},
                    "message": "PS1 review",
                }
            ],
            "labels": {"Code-Review": {"approved": {"name": "Reviewer"}}},
        }
        detail_sticky_cr2_with_ai = {
            "_number": 42713016,
            "current_revision": "rev2",
            "revisions": {"rev2": {"_number": 2}},
            "messages": [
                {
                    "_revision_number": 2,
                    "author": {"name": "groupagent-android-emulator"},
                    "message": "PS2 review",
                }
            ],
            "labels": {"Code-Review": {"approved": {"name": "Reviewer"}}},
        }
        clock_ticks = iter([0.0, 5.0, 10.0, 15.0])

        with mock.patch.object(
            client,
            "get_change_detail",
            side_effect=[detail_sticky_cr2_no_ai, detail_sticky_cr2_with_ai],
        ) as mock_detail, mock.patch.object(
            client,
            "get_comment_threads",
            side_effect=[[], []],
        ):
            res = client.wait_for_review_update(
                "42713016",
                baseline_patchset=2,
                known_comment_ids=set(),
                timeout=timedelta(seconds=60),
                poll_interval=timedelta(seconds=5),
                wait_for_ai=True,
                wait_for_approval=False,
                sleep_fn=lambda _: None,
                clock_fn=lambda: next(clock_ticks),
            )
            self.assertEqual(mock_detail.call_count, 2)
            self.assertIn(res["status"], ("approved", "ai_review_clean"))

    def test_resolve_project_repo_path_maps_platform_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            aemu_dir = ws / "hardware" / "google" / "aemu"
            aemu_dir.mkdir(parents=True)
            resolved = resolve_project_repo_path(
                "platform/hardware/google/aemu", workspace_root=ws
            )
            self.assertEqual(resolved, aemu_dir.resolve())

    def test_extract_source_snippet_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir = root / "repo"
            repo_dir.mkdir()
            secret_file = root / "secret.txt"
            secret_file.write_text("TOP_SECRET\n", encoding="utf-8")
            valid_file = repo_dir / "main.py"
            valid_file.write_text("print('ok')\n", encoding="utf-8")

            self.assertEqual(
                extract_source_snippet(repo_dir, "../secret.txt", line=1),
                "",
            )
            self.assertIn(
                "print('ok')",
                extract_source_snippet(repo_dir, "main.py", line=1),
            )


if __name__ == "__main__":
    unittest.main()

