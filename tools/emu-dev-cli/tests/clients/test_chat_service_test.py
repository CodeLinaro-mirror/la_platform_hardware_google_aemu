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

"""Hermetic unit tests for clients.chat_service (zero @patch decorators)."""

import json
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.chat_service import (
    ChatDmRequest,
    ChatDmResult,
    FakeChatNotificationService,
    MAX_CHAT_DM_LENGTH,
    ProdChatNotificationService,
    format_review_ping_message,
    is_bot_or_agent_chat_email,
    normalize_chat_dm_recipient,
    parse_gchat_dm_output,
    validate_chat_dm_message,
)
from core.errors import (
    ExecutionError,
    RetryableError,
    SubprocessError,
    UsageError,
)
from core.process import FakeProcessRunner, ProcessResult


class ChatServiceTest(unittest.TestCase):
    """Tests recipient validation, message formatting, ProdChatNotificationService, and FakeChatNotificationService."""

    def test_normalize_chat_dm_recipient_accepts_human_ldap_and_email(self) -> None:
        self.assertEqual(
            normalize_chat_dm_recipient("joshuaduong"),
            "joshuaduong@google.com",
        )
        self.assertEqual(
            normalize_chat_dm_recipient("  Jansene@google.com "),
            "jansene@google.com",
        )
        self.assertFalse(is_bot_or_agent_chat_email("corakwue@google.com"))
        self.assertTrue(
            is_bot_or_agent_chat_email(
                "groupagent-android-emulator-heavy@google.com"
            )
        )
        self.assertTrue(
            is_bot_or_agent_chat_email(
                "android-build-coastguard@system.gserviceaccount.com"
            )
        )

    def test_normalize_chat_dm_recipient_rejects_invalid_and_self_inputs(self) -> None:
        with self.assertRaisesRegex(UsageError, "recipient is empty"):
            normalize_chat_dm_recipient("   ")

        with self.assertRaisesRegex(UsageError, "multi-recipient DMs are blocked"):
            normalize_chat_dm_recipient("joshuaduong, jansene")

        with self.assertRaisesRegex(UsageError, "only human '@google.com' accounts"):
            normalize_chat_dm_recipient("treehugger@google.com")

        with self.assertRaisesRegex(
            UsageError, "does not support 1:1 DMs with yourself"
        ):
            normalize_chat_dm_recipient(
                "joshuaduong", sender_email="joshuaduong@google.com"
            )

    def test_validate_chat_dm_message_enforces_non_empty_and_length_bounds(self) -> None:
        self.assertEqual(
            validate_chat_dm_message(
                "  Ready for review_测试_🚀_é  ",
                recipient_email="joshuaduong@google.com",
                cl_number="43386500",
            ),
            "Ready for review_测试_🚀_é",
        )
        with self.assertRaisesRegex(UsageError, "message text is empty"):
            validate_chat_dm_message(
                "   ",
                recipient_email="joshuaduong@google.com",
                cl_number="43386500",
            )
        with self.assertRaisesRegex(UsageError, f"exceeds {MAX_CHAT_DM_LENGTH}"):
            validate_chat_dm_message(
                "x" * (MAX_CHAT_DM_LENGTH + 1),
                recipient_email="joshuaduong@google.com",
                cl_number="43386500",
            )

    def test_parse_gchat_dm_output_and_format_review_ping_message(self) -> None:
        raw_json = json.dumps(
            {
                "space_name": "spaces/DM_JOSH_123",
                "message_name": "spaces/DM_JOSH_123/messages/m99",
            }
        )
        space_name, message_name, chat_url = parse_gchat_dm_output(raw_json)
        self.assertEqual(space_name, "spaces/DM_JOSH_123")
        self.assertEqual(message_name, "spaces/DM_JOSH_123/messages/m99")
        self.assertEqual(chat_url, "https://chat.google.com/dm/DM_JOSH_123")
        self.assertEqual(parse_gchat_dm_output("not-json"), ("", "", ""))

        formatted = format_review_ping_message(
            cl_number="43386500",
            subject="feat(gerrit): Add ping-for-review_测试_🚀_é",
            gerrit_url="https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/43386500",
            patch_set=2,
            presubmit_score=2,
        )
        self.assertIn("[ag/43386500]", formatted)
        self.assertIn("feat(gerrit): Add ping-for-review_测试_🚀_é", formatted)
        self.assertIn("PS2, Presubmit-Verified: +2", formatted)

    def test_prod_chat_notification_service_sends_dm_via_process_runner(self) -> None:
        runner = FakeProcessRunner()
        runner.register_prefix(
            ["/usr/bin/gchat", "mutate", "send-direct-message"],
            ProcessResult(
                args=("/usr/bin/gchat",),
                returncode=0,
                stdout=json.dumps(
                    {
                        "space_name": "spaces/DM_JOSH",
                        "message_name": "spaces/DM_JOSH/messages/m1",
                    }
                ),
            ),
        )
        service = ProdChatNotificationService(
            runner,
            gchat_bin="/usr/bin/gchat",
        )
        result = service.send_review_ping(
            cl_number="43386500",
            recipient="joshuaduong",
            subject="feat: Ping_测试_🚀_é",
            patch_set=1,
            presubmit_score=2,
        )
        self.assertIsInstance(result, ChatDmResult)
        self.assertEqual(result.recipient, "joshuaduong@google.com")
        self.assertEqual(result.space_name, "spaces/DM_JOSH")
        self.assertEqual(result.chat_url, "https://chat.google.com/dm/DM_JOSH")
        self.assertEqual(result.to_dict()["status"], "ok")
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0].args
        self.assertIn("send-direct-message", cmd)
        self.assertIn("joshuaduong@google.com", cmd)

    def test_prod_chat_notification_service_handles_missing_binary_timeout_and_error(
        self,
    ) -> None:
        runner = FakeProcessRunner()
        missing_bin_service = ProdChatNotificationService(
            runner,
            binary_resolver=lambda: None,
        )
        with self.assertRaisesRegex(ExecutionError, "was not found on this host"):
            missing_bin_service.send_direct_message(
                "joshuaduong", "Hello", cl_number="43386500"
            )

        runner.queue_response(
            returncode=124,
            timed_out=True,
        )
        service = ProdChatNotificationService(runner, gchat_bin="/usr/bin/gchat")
        with self.assertRaisesRegex(RetryableError, "Timed out after 25s"):
            service.send_direct_message(
                "joshuaduong", "Hello", cl_number="43386500"
            )

        runner.queue_response(
            returncode=1,
            stderr="SSO ticket expired",
        )
        with self.assertRaisesRegex(SubprocessError, "SSO ticket expired"):
            service.send_direct_message(
                "joshuaduong", "Hello", cl_number="43386500"
            )

    def test_fake_chat_notification_service_records_sent_dms(self) -> None:
        fake = FakeChatNotificationService(default_space_id="TEST_SPACE")
        res = fake.send_review_ping(
            cl_number="43386500",
            recipient="jansene",
            subject="feat: Core_测试_🚀_é",
        )
        self.assertEqual(len(fake.sent_dms), 1)
        self.assertIsInstance(fake.sent_dms[0], ChatDmRequest)
        self.assertEqual(fake.sent_dms[0].recipient, "jansene@google.com")
        self.assertEqual(res.chat_url, "https://chat.google.com/dm/TEST_SPACE")


if __name__ == "__main__":
    unittest.main()
