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
"""Unit tests for Gerrit domain services (Queue, VoteSubmit, InlineQA, Diff) and UI handler."""

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from commands.gerrit.handlers.ui_handler import (
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
)
from commands.gerrit.services.diff_parser import (
    AEMU_MAP_BASE_URL,
    GerritDiffService,
    build_aemu_map_commit_url,
    build_code_search_file_url,
    classify_commit_type,
    classify_risk_and_load,
    extract_symbol_from_code_line,
    parse_unified_diff_files,
)
from commands.gerrit.services.inline_qa_service import (
    GerritInlineQaService,
    build_code_followup_prompt,
    build_code_question_prompt,
    parse_code_questions_from_transcript_lines,
)
from commands.gerrit.services.queue_service import (
    GerritQueueService,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    is_agent_or_bot_email,
    is_team_review_agent,
)
from commands.gerrit.services.vote_submit_service import (
    GerritVoteSubmitService,
    OWNER_NOTIFY_HANDLING,
)
from commands.gerrit.ui_server import GerritUIService


class GerritDiffServiceTest(unittest.TestCase):
    """Tests for GerritDiffService and diff parsing helpers."""

    def test_parse_unified_diff_files_with_unicode(self):
        diff_text = (
            "diff --git a/src/测试_🚀_é.cc b/src/测试_🚀_é.cc\n"
            "index 1111111..2222222 100644\n"
            "--- a/src/测试_🚀_é.cc\n"
            "+++ b/src/测试_🚀_é.cc\n"
            "@@ -10,2 +10,3 @@ void run()\n"
            " int x = 1;\n"
            "-int old_val = 2;\n"
            "+int new_val = 3; // 🎃_é\n"
            "+int extra = 4;\n"
        )
        files = parse_unified_diff_files(diff_text)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["file"], "src/测试_🚀_é.cc")
        self.assertEqual(files[0]["additions"], 2)
        self.assertEqual(files[0]["deletions"], 1)
        lines = files[0]["lines"]
        self.assertTrue(any("🎃_é" in r.get("text", "") for r in lines))

    def test_classify_commit_type_and_risk_and_load(self):
        commit_type = classify_commit_type(
            {"subject": "refactor(gerrit): Decompose UI monolith"}
        )
        self.assertEqual(commit_type["id"], "cleanup")

        bugfix_type = classify_commit_type(
            {"subject": "fix: Resolve crash on startup"}
        )
        self.assertEqual(bugfix_type["id"], "bugfix")

        load = classify_risk_and_load(
            {
                "subject": "docs: Update README",
                "insertions": 10,
                "deletions": 2,
                "current_revision": "r1",
                "revisions": {"r1": {"files": {"README.md": {}}}},
            }
        )
        self.assertEqual(load["tier"], "low")

        cs_url = build_code_search_file_url(
            "platform/hardware/google/aemu", "studio-main", "src/foo_test.cc", 42
        )
        self.assertIn("src/foo_test.cc", cs_url)
        self.assertIn("l=42", cs_url)

        map_url = build_aemu_map_commit_url(
            "ag/43351736",
            [
                "/COMMIT_MSG",
                "/MERGE_LIST",
                "emulator/net/async_socket_adapter.cc",
                "/emulator/net/async_socket_adapter.h",
                "emulator/net/async_socket_adapter.cc",
                "src/测试_🚀_é.cc",
            ],
        )
        self.assertEqual(
            map_url,
            f"{AEMU_MAP_BASE_URL}?ag=43351736&files="
            "emulator/net/async_socket_adapter.cc,"
            "emulator/net/async_socket_adapter.h,"
            "src/%E6%B5%8B%E8%AF%95_%F0%9F%9A%80_%C3%A9.cc",
        )
        self.assertEqual(
            build_aemu_map_commit_url(43351736, ["/COMMIT_MSG", "/dev/null"]),
            f"{AEMU_MAP_BASE_URL}?ag=43351736",
        )

    def test_extract_symbol_from_code_line(self):
        self.assertEqual(
            extract_symbol_from_code_line("void HandleRequest(int fd) {"),
            "HandleRequest",
        )
        self.assertEqual(
            extract_symbol_from_code_line("def compute_metrics(items):"),
            "compute_metrics",
        )


class GerritQueueServiceTest(unittest.TestCase):
    """Tests for GerritQueueService and queue classification helpers."""

    def test_agent_detection_and_enrich_change(self):
        self.assertTrue(
            is_agent_or_bot_email("groupagent-android-emulator-heavy@google.com")
        )
        self.assertTrue(
            is_team_review_agent("groupagent-android-emulator-heavy@google.com")
        )
        self.assertFalse(is_agent_or_bot_email("jansene@google.com"))

        now = datetime(2026, 10, 28, 22, 0, 0, tzinfo=timezone.utc)
        change_obj = {
            "_number": 43351736,
            "change_id": "I1234567890abcdef1234567890abcdef12345678",
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "subject": "refactor(gerrit): Decompose UI monolith 测试_🚀_é",
            "created": "2026-10-28 12:00:00.000000000",
            "updated": "2026-10-28 21:00:00.000000000",
            "insertions": 50,
            "deletions": 20,
            "unresolved_comment_count": 0,
            "current_revision": "rev_a",
            "current_revision_number": 1,
            "owner": {
                "email": "groupagent-android-emulator-heavy@google.com",
                "name": "Archie",
            },
            "revisions": {
                "rev_a": {
                    "_number": 1,
                    "files": {"tools/emu-dev-cli/src/commands/gerrit/ui_server.py": {}},
                    "commit": {"parents": [{"commit": "rev_base"}]},
                }
            },
            "reviewers": {
                "REVIEWER": [
                    {"email": "jansene@google.com", "name": "Erwin Jansen"},
                    {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
                ]
            },
            "labels": {
                "Code-Review": {
                    "all": [
                        {
                            "email": "jansene@google.com",
                            "name": "Erwin Jansen",
                            "value": 2,
                        },
                        {
                            "email": "groupagent-android-emulator-heavy@google.com",
                            "name": "Archie",
                            "value": 1,
                        },
                    ]
                }
            },
        }
        enriched = enrich_change_for_ui(change_obj, "jansene@google.com", now_dt=now)
        self.assertTrue(enriched["is_agent_cl"])
        self.assertEqual(enriched["required_cr2_count"], 2)
        self.assertEqual(enriched["human_cr2_count"], 1)
        self.assertTrue(enriched["approved_by_viewer"])
        self.assertEqual(
            enriched["aemu_map_url"],
            "http://go/aemu-map?ag=43351736&files=tools/emu-dev-cli/src/commands/gerrit/ui_server.py",
        )

    def test_extract_bug_ids_from_change(self):
        change = {
            "subject": "fix: Handle b/485291270 in UI",
            "current_revision": "r1",
            "revisions": {
                "r1": {
                    "commit": {
                        "message": "fix: Handle bug\n\nBug: 485291270, b/123456\n"
                    }
                }
            },
        }
        self.assertEqual(extract_bug_ids_from_change(change), ["485291270", "123456"])

    def test_apply_session_approvals_updates_submittable(self):
        with patch.object(
            GerritQueueService, "_detect_auth_email", return_value="jansene@google.com"
        ):
            svc = GerritQueueService(default_viewer="jansene@google.com")
            with svc._session_lock:
                svc._session_approved_cls.add(43351736)
                svc._session_votes[43351736] = {"Code-Review": 2}
            items = [
                {
                    "number": 43351736,
                    "subject": "refactor(cli): Test Unicode 测试_🚀_é",
                    "status": "NEW",
                    "wip": False,
                    "mergeable": True,
                    "unresolved_comment_count": 0,
                    "code_review": 0,
                    "presubmit_ready": 1,
                    "presubmit_status": "PASS",
                    "has_negative_cr": False,
                    "submittable": False,
                }
            ]
            svc._apply_session_approvals(items, "jansene@google.com")
            self.assertTrue(items[0]["approved_by_viewer"])


class GerritVoteSubmitServiceTest(unittest.TestCase):
    """Tests for GerritVoteSubmitService and strict notify='OWNER' enforcement."""

    def test_owner_notify_constant(self):
        self.assertEqual(OWNER_NOTIFY_HANDLING, "OWNER")

    def test_cast_batch_vote_enforces_owner_notify(self):
        with patch.object(
            GerritVoteSubmitService,
            "_detect_auth_email",
            return_value="jansene@google.com",
        ):
            svc = GerritVoteSubmitService(default_viewer="jansene@google.com")
            with (
                patch("shutil.which", return_value="/usr/bin/gob-curl"),
                patch.object(
                    svc,
                    "_post_single_vote",
                    return_value={"cl": "43351736", "ok": True},
                ) as mock_post,
            ):
                res = svc.cast_batch_vote(
                    [43351736],
                    code_review=1,
                    presubmit_ready=1,
                    message="Verified modular services 测试_🚀_é",
                )
                self.assertEqual(res["status"], "ok")
                mock_post.assert_called_once()
                _, cid, payload_json, cr_hint = mock_post.call_args[0]
                self.assertEqual(cid, "43351736")
                self.assertEqual(cr_hint, 1)
                payload = json.loads(payload_json)
                self.assertEqual(payload["notify"], "OWNER")
                self.assertEqual(payload["labels"]["Code-Review"], 1)
                self.assertEqual(payload["labels"]["Presubmit-Ready"], 1)

    def test_post_inline_comment_enforces_owner_notify_and_unresolved(self):
        with patch.object(
            GerritVoteSubmitService,
            "_detect_auth_email",
            return_value="jansene@google.com",
        ):
            svc = GerritVoteSubmitService(default_viewer="jansene@google.com")
            with patch.object(
                svc, "_post_review_payload", return_value={"comments": {}}
            ) as mock_post:
                res = svc.post_inline_comment(
                    43351736,
                    file_path="src/测试_🚀_é.py",
                    line=42,
                    message="Please use BaseRefactoringEngine here.",
                )
                self.assertEqual(res["status"], "ok")
                mock_post.assert_called_once()
                cl_id, rev, payload = mock_post.call_args[0]
                self.assertEqual(cl_id, "43351736")
                self.assertEqual(rev, "current")
                self.assertEqual(payload["notify"], "OWNER")
                comments = payload["comments"]["src/测试_🚀_é.py"]
                self.assertEqual(len(comments), 1)
                self.assertTrue(comments[0]["unresolved"])
                self.assertEqual(comments[0]["line"], 42)


class GerritInlineQaServiceTest(unittest.TestCase):
    """Tests for GerritInlineQaService and transcript parsing."""

    def test_prompt_generation_and_transcript_parsing_with_unicode(self):
        prompt = build_code_question_prompt(
            cl_number=43351736,
            file_path="src/测试_🚀_é.py",
            start_line=10,
            end_line=12,
            question="How are services composed in 测试_🚀_é?",
            code_snippet="class GerritUIService:",
        )
        self.assertIn("43351736", prompt)
        self.assertIn("src/测试_🚀_é.py", prompt)
        self.assertIn("How are services composed in 测试_🚀_é?", prompt)

        lines = [
            json.dumps(
                {
                    "type": "USER_INPUT",
                    "step_index": 1,
                    "content": prompt,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "PLANNER_RESPONSE",
                    "step_index": 2,
                    "content": "They are composed via GerritQueueService and GerritDiffService.",
                },
                ensure_ascii=False,
            ),
        ]
        parsed = parse_code_questions_from_transcript_lines(lines, conv_id="conv-123")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["cl"], 43351736)
        self.assertEqual(parsed[0]["file_path"], "src/测试_🚀_é.py")
        self.assertIn("GerritQueueService", parsed[0]["reply"])

        followup = build_code_followup_prompt(
            cl_number=43351736,
            file_path="src/测试_🚀_é.py",
            start_line=10,
            end_line=12,
            question="And what about notify='OWNER'?",
        )
        self.assertIn("And what about notify='OWNER'?", followup)


class GerritUIServiceFacadeAndHandlerTest(unittest.TestCase):
    """Tests for the GerritUIService facade and HTTP handler factory."""

    def test_facade_composes_domain_services(self):
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="jansene@google.com"
        ):
            facade = GerritUIService(default_viewer="jansene@google.com")
            self.assertIsInstance(facade.queue_service, GerritQueueService)
            self.assertIsInstance(facade.vote_submit_service, GerritVoteSubmitService)
            self.assertIsInstance(facade.inline_qa_service, GerritInlineQaService)
            self.assertIsInstance(facade.diff_service, GerritDiffService)

            handler_cls = make_gerrit_ui_handler(facade)
            self.assertTrue(hasattr(handler_cls, "do_GET"))
            self.assertTrue(hasattr(handler_cls, "do_POST"))

            html = render_static_gerrit_ui_html({"items": [], "total": 0})
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("buildAemuMapCommitUrl", html)
            self.assertIn("openAemuMapLink", html)
            self.assertIn("aemu-map-pill", html)


if __name__ == "__main__":
    unittest.main()
