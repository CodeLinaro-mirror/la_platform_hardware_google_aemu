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

"""Hermetic unit tests for clients.buganizer_service (zero @patch decorators)."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.buganizer_service import (
    BuganizerComment,
    BuganizerIssue,
    CreateBugRequest,
    DRY_RUN_BUG_ID,
    FakeBuganizerService,
    ProdBuganizerService,
    is_safe_signature_issue_match,
    normalize_bazel_target_for_search,
    normalize_bug_id,
    normalize_bug_url_and_id,
    parse_issues_cli_output,
)
from core.errors import ExecutionError, SubprocessError, UsageError
from core.process import FakeProcessRunner


class BuganizerServiceTest(unittest.TestCase):
    """Tests Buganizer parsing, signature safety, ProdBuganizerService, and FakeBuganizerService."""

    def test_normalize_bug_id_and_url(self) -> None:
        self.assertEqual(normalize_bug_id(485291270), "485291270")
        self.assertEqual(normalize_bug_id("b/485291270"), "485291270")
        self.assertEqual(
            normalize_bug_id("https://b.corp.google.com/issues/485291270?pli=1"),
            "485291270",
        )
        bug_id, url = normalize_bug_url_and_id("b/485291270")
        self.assertEqual(bug_id, "485291270")
        self.assertEqual(url, "https://b.corp.google.com/issues/485291270")
        self.assertEqual(
            normalize_bazel_target_for_search("@@goldfish+//emulator/net:test"),
            "@goldfish//emulator/net:test",
        )

        with self.assertRaises(UsageError):
            normalize_bug_id("not-a-bug-id")

    def test_parse_issues_cli_output_across_all_formats(self) -> None:
        multi_block = (
            "\x1b[32mIssue ID: 485291270\x1b[0m\n"
            "Priority: P1\n"
            "Severity: S1\n"
            "Status: ASSIGNED\n"
            "Assignee: jansene@google.com\n"
            "Title: [Crash] Vulkan QueueSubmit_测试_🚀-deadbeef\n"
            "---\n"
            "- Issue 485291271: Flaky test //emulator:foo [FIXED] (Priority: P2)\n"
            "485291272: Compact line issue title\n"
        )
        parsed = parse_issues_cli_output(multi_block)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0].issue_id, "485291270")
        self.assertEqual(parsed[0].priority, "P1")
        self.assertEqual(parsed[0].severity, "S1")
        self.assertEqual(parsed[0].assignee, "jansene@google.com")
        self.assertEqual(
            parsed[0].title, "[Crash] Vulkan QueueSubmit_测试_🚀-deadbeef"
        )
        self.assertFalse(parsed[0].is_closed)

        self.assertEqual(parsed[1].issue_id, "485291271")
        self.assertTrue(parsed[1].is_closed)
        self.assertEqual(parsed[2].issue_id, "485291272")
        self.assertEqual(parsed[2].title, "Compact line issue title")

    def test_is_safe_signature_issue_match_prevents_hash_collision(self) -> None:
        issue_matching = BuganizerIssue(
            issue_id="1001",
            title="[Crash Report] abort::FailWithoutStackTrace-a1b2c3d4 in emulator",
        )
        issue_mismatched_hash = BuganizerIssue(
            issue_id="1002",
            title="[Crash Report] abort::FailWithoutStackTrace-99887766 in emulator",
        )
        issue_missing_hash_on_wrapper = BuganizerIssue(
            issue_id="1003",
            title="[Crash Report] abort::FailWithoutStackTrace in emulator",
        )

        self.assertTrue(
            is_safe_signature_issue_match(
                "abort::FailWithoutStackTrace-a1b2c3d4", issue_matching
            )
        )
        self.assertFalse(
            is_safe_signature_issue_match(
                "abort::FailWithoutStackTrace-a1b2c3d4", issue_mismatched_hash
            )
        )
        self.assertFalse(
            is_safe_signature_issue_match(
                "abort::FailWithoutStackTrace-a1b2c3d4",
                issue_missing_hash_on_wrapper,
            )
        )

    def test_prod_buganizer_service_operations_and_error_propagation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emu_bug_cache_") as tmp_dir:
            cache_file = Path(tmp_dir) / "bug_cache.json"
            runner = FakeProcessRunner()
            svc = ProdBuganizerService(
                runner,
                issues_bin="/google/bin/releases/issues-cli/issues",
                cache_file=cache_file,
            )

            # 1. Batch fetch populates memory + disk cache
            runner.queue_response(
                stdout=(
                    "Issue ID: 485291270\n"
                    "Priority: P2\n"
                    "Status: NEW\n"
                    "Title: Crash in VkDecoder_测试_🚀\n"
                )
            )
            batch = svc.get_batch_issues(["b/485291270"])
            self.assertIn("485291270", batch)
            self.assertEqual(
                batch["485291270"].title, "Crash in VkDecoder_测试_🚀"
            )
            self.assertTrue(cache_file.is_file())

            # Subsequent get_issue hits cache without invoking subprocess
            cached = svc.get_issue("485291270")
            self.assertIsNotNone(cached)
            self.assertEqual(len(runner.calls), 1)

            # 2. Create issue (dry_run vs real)
            req = CreateBugRequest(
                title="[Crash] New issue_测试_🚀",
                description="Detailed stack trace_é",
                component_id=1016880,
                priority="P1",
                severity="S1",
                assignee="joshuaduong@google.com",
            )
            dry_created = svc.create_issue(req, dry_run=True)
            self.assertEqual(dry_created.issue_id, DRY_RUN_BUG_ID)
            self.assertEqual(len(runner.calls), 1)

            runner.queue_response(stdout="Created issue b/499999999\n")
            real_created = svc.create_issue(req, dry_run=False)
            self.assertEqual(real_created.issue_id, "499999999")
            self.assertEqual(real_created.status, "ASSIGNED")

            # 3. Add comment & update title
            runner.queue_response(stdout="Comment added\n")
            comment = svc.add_comment("499999999", "Root cause identified_🚀")
            self.assertEqual(comment.body, "Root cause identified_🚀")

            runner.queue_response(stdout="Title updated\n")
            svc.update_issue_title("499999999", "[Crash] Updated title_测试")
            updated = svc.get_issue("499999999")
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.title, "[Crash] Updated title_测试")

            # 4. Subprocess failure raises SubprocessError instead of returning []
            runner.queue_response(
                returncode=1, stderr="PERMISSION_DENIED: LOAS expired"
            )
            with self.assertRaises(SubprocessError):
                svc.search_issues("componentid:1016880 status:open")

    def test_fake_buganizer_service_end_to_end(self) -> None:
        fake = FakeBuganizerService(next_issue_id=500000001)
        fake.seed_issue(
            BuganizerIssue(
                issue_id="400000001",
                title="[Flaky Test] @goldfish//emulator/test:unit_test_测试",
                status="NEW",
                component_id=1016880,
            ),
            comments=[BuganizerComment(comment_number=1, body="Initial report")],
        )
        fake.seed_issue(
            BuganizerIssue(
                issue_id="400000002",
                title="[Fixed Test] @goldfish//emulator/test:old_test",
                status="FIXED",
                component_id=1016880,
            )
        )

        open_match = fake.find_open_issue_for_target(
            1016880, "@@goldfish+//emulator/test:unit_test_测试"
        )
        self.assertIsNotNone(open_match)
        assert open_match is not None
        self.assertEqual(open_match.issue_id, "400000001")

        fixed_match = fake.find_recently_fixed_issue_for_target(
            1016880, "@@goldfish+//emulator/test:old_test"
        )
        self.assertIsNotNone(fixed_match)
        assert fixed_match is not None
        self.assertEqual(fixed_match.issue_id, "400000002")

        created = fake.create_issue(
            CreateBugRequest(
                title="New bug_🚀",
                description="Body",
                component_id=1016880,
            )
        )
        self.assertEqual(created.issue_id, "500000001")
        fake.add_comment("500000001", "Follow-up comment")
        self.assertEqual(len(fake.list_comments("500000001")), 2)

        with self.assertRaises(ExecutionError):
            fake.add_comment("999999999", "Missing bug")


if __name__ == "__main__":
    unittest.main()
