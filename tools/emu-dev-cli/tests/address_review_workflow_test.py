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

"""Unit tests for the `address-review` declarative workflow and its verifiers."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lib.gerrit_api import GerritCommentMessage, GerritCommentThread
from workflows.address_review import (
    prepare_review_batch,
    verify_fixes,
    verify_upload_and_resolve,
)


def _make_sample_thread(
    comment_id: str = "c1",
    message: str = "Use from __future__ import annotations",
    patch_set: int = 4,
) -> GerritCommentThread:
    return GerritCommentThread(
        root_id=comment_id,
        leaf_id=comment_id,
        file_path="src/foo.py",
        line=10,
        start_line=10,
        end_line=10,
        patch_set=patch_set,
        commit_id="deadbeef",
        unresolved=True,
        is_ai=True,
        author_name="groupagent-android-review",
        author_email="bot@google.com",
        updated="2026-10-05 01:50:04",
        messages=[
            GerritCommentMessage(
                comment_id=comment_id,
                author_name="groupagent-android-review",
                author_email="bot@google.com",
                patch_set=patch_set,
                updated="2026-10-05 01:50:04",
                message=message,
                unresolved=True,
                is_ai=True,
            )
        ],
    )


class AddressReviewWorkflowTest(unittest.TestCase):
    """Tests `prepare_review_batch`, `verify_fixes`, and `verify_upload_and_resolve`."""

    @mock.patch("workflows.address_review.prepare_review_batch.fetch_and_checkout_cl")
    @mock.patch("workflows.address_review.prepare_review_batch.resolve_project_repo_path")
    @mock.patch("workflows.address_review.prepare_review_batch.GerritClient")
    def test_prepare_review_batch_writes_queue_and_dossier(
        self,
        mock_client_cls: mock.MagicMock,
        mock_resolve_repo: mock.MagicMock,
        mock_checkout: mock.MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            (repo_dir / "src").mkdir()
            (repo_dir / "src" / "foo.py").write_text(
                "import os\n" * 15, encoding="utf-8"
            )
            mock_resolve_repo.return_value = repo_dir
            mock_checkout.return_value = {
                "status": "success",
                "action": "already_on_change",
                "repo_path": str(repo_dir),
                "head_sha": "deadbeef",
            }

            mock_client = mock_client_cls.return_value
            mock_client.get_change_detail.return_value = {
                "_number": 42659931,
                "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
                "project": "platform/hardware/google/aemu",
                "branch": "main",
                "subject": "feat(emu-dev-cli): support new CI test identifiers",
                "current_revision": "deadbeef",
                "revisions": {"deadbeef": {"_number": 4}},
            }
            mock_client.get_comment_threads.return_value = [_make_sample_thread()]

            q_file = Path(tmp) / "queue.json"
            d_file = Path(tmp) / "dossier.md"
            r_file = Path(tmp) / "primary_repo.txt"

            rc = prepare_review_batch.prepare_batch(
                cl="42659931",
                all_open=False,
                queue_file=q_file,
                dossier_file=d_file,
                repo_out_file=r_file,
                workspace_root=repo_dir,
                checkout=True,
            )
            self.assertEqual(rc, 0)
            self.assertTrue(q_file.is_file())
            self.assertTrue(d_file.is_file())
            self.assertEqual(r_file.read_text(encoding="utf-8").strip(), str(repo_dir))

            q_data = json.loads(q_file.read_text(encoding="utf-8"))
            self.assertEqual(len(q_data["cls"]), 1)
            self.assertEqual(q_data["cls"][0]["cl_number"], 42659931)
            self.assertEqual(len(q_data["cls"][0]["unresolved_threads"]), 1)
            self.assertIn("Use from __future__ import annotations", d_file.read_text(encoding="utf-8"))

    def test_verify_fixes_enforces_staged_replies_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q_file = Path(tmp) / "queue.json"
            q_data = {
                "mode": "single",
                "cls": [
                    {
                        "cl_number": 42659931,
                        "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
                        "repo_path": str(tmp),
                        "unresolved_threads": [
                            {
                                "root_id": "c1",
                                "leaf_id": "c1",
                                "file_path": "src/foo.py",
                                "line": 10,
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

            rc_missing = verify_fixes.verify_all_fixes(
                queue_file=q_file,
                test_cmd="true",
            )
            self.assertEqual(rc_missing, 1)

            q_data["cls"][0]["unresolved_threads"][0]["staged_reply"] = (
                "Done: added future annotations."
            )
            q_file.write_text(json.dumps(q_data), encoding="utf-8")

            rc_fail_test = verify_fixes.verify_all_fixes(
                queue_file=q_file,
                test_cmd="false",
            )
            self.assertEqual(rc_fail_test, 1)

            rc_ok = verify_fixes.verify_all_fixes(
                queue_file=q_file,
                test_cmd="true",
            )
            self.assertEqual(rc_ok, 0)

    @mock.patch("workflows.address_review.verify_upload_and_resolve.upload_cl")
    @mock.patch("workflows.address_review.verify_upload_and_resolve.GerritClient")
    def test_verify_upload_and_resolve_loops_on_new_comments_and_completes_when_clean(
        self,
        mock_client_cls: mock.MagicMock,
        mock_upload: mock.MagicMock,
    ) -> None:
        mock_upload.return_value = {
            "status": "success",
            "repo_path": "/tmp/repo",
            "presubmit": True,
            "output": "Uploaded",
        }
        mock_client = mock_client_cls.return_value
        mock_client.post_review_replies.return_value = [{}]
        mock_client.get_change_detail.return_value = {
            "_number": 42659931,
            "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
            "project": "platform/hardware/google/aemu",
            "branch": "main",
            "subject": "feat: test",
            "current_revision": "rev5",
            "revisions": {"rev5": {"_number": 5}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            q_file = Path(tmp) / "queue.json"
            d_file = Path(tmp) / "dossier.md"
            q_data = {
                "mode": "single",
                "cls": [
                    {
                        "cl_number": 42659931,
                        "change_id": "I4113ef0106ca5bb1c3f8b1dc47b96be003ca0aa1",
                        "project": "platform/hardware/google/aemu",
                        "repo_path": str(tmp),
                        "patch_set": 4,
                        "unresolved_threads": [
                            {
                                "root_id": "c1",
                                "leaf_id": "c1",
                                "file_path": "src/foo.py",
                                "line": 10,
                                "patch_set": 4,
                                "staged_reply": "Done: fixed import.",
                                "resolve": True,
                                "published": False,
                            }
                        ],
                    }
                ],
            }
            q_file.write_text(json.dumps(q_data), encoding="utf-8")

            new_thread = _make_sample_thread(
                comment_id="c2",
                message="Please also add a unit test for the edge case.",
                patch_set=5,
            )
            mock_client.wait_for_review_update.return_value = {
                "status": "new_comments",
                "new_threads": [new_thread],
                "unresolved_threads": [new_thread],
                "detail": mock_client.get_change_detail.return_value,
            }

            rc_loop = verify_upload_and_resolve.upload_resolve_and_poll(
                queue_file=q_file,
                dossier_file=d_file,
                skip_upload=False,
                wait_timeout_sec=30,
                poll_interval_sec=1,
            )
            self.assertEqual(rc_loop, 2)
            refreshed_q = json.loads(q_file.read_text(encoding="utf-8"))
            self.assertEqual(
                refreshed_q["cls"][0]["unresolved_threads"][0]["leaf_id"], "c2"
            )
            self.assertIn(
                "Please also add a unit test for the edge case.",
                d_file.read_text(encoding="utf-8"),
            )

            refreshed_q["cls"][0]["unresolved_threads"][0]["staged_reply"] = (
                "Done: added edge case unit test."
            )
            q_file.write_text(json.dumps(refreshed_q), encoding="utf-8")
            mock_client.wait_for_review_update.return_value = {
                "status": "ai_review_clean",
                "new_threads": [],
                "unresolved_threads": [],
                "detail": mock_client.get_change_detail.return_value,
            }

            rc_done = verify_upload_and_resolve.upload_resolve_and_poll(
                queue_file=q_file,
                dossier_file=d_file,
                skip_upload=False,
                wait_timeout_sec=30,
                poll_interval_sec=1,
            )
            self.assertEqual(rc_done, 0)

    def test_workflow_yaml_spec_structure(self) -> None:
        from workflows.core import load_yaml

        spec_path = (
            Path(__file__).resolve().parent.parent
            / "workflows"
            / "address_review"
            / "workflow.yaml"
        )
        spec = load_yaml(spec_path)
        self.assertEqual(spec["name"], "address-review")
        steps = spec["steps"]
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[1]["workflow"], "amend-commit")
        self.assertEqual(steps[2]["loop_to_step"], 0)
        self.assertEqual(steps[2]["loop_exit_code"], 2)


if __name__ == "__main__":
    unittest.main()

