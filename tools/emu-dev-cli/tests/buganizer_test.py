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

"""Unit tests for BuganizerClient integration."""

import os
import unittest
from unittest.mock import MagicMock, patch
from lib.buganizer import (
    BuganizerClient,
    BuganizerIssuePayload,
    format_buganizer_search_query,
)


class BuganizerTest(unittest.TestCase):

    def test_buganizer_query_formatting(self):
        query = format_buganizer_search_query(
            component_id=1016880,
            test_identifier="//emulator/videobridge:in_process_media_provider_test",
        )
        self.assertIn("componentid:1016880", query)
        self.assertIn("status:open", query)
        self.assertIn(
            "//emulator/videobridge:in_process_media_provider_test", query
        )

    @patch("os.path.exists")
    @patch("subprocess.check_output")
    def test_fetch_all_component_open_bugs(
        self, mock_check_output, mock_exists
    ):
        mock_exists.return_value = True
        mock_check_output.return_value = (
            b"Issue 12345: [Flaky] loop_handoff_test\nIssue 67890: other_test\n"
        )
        client = BuganizerClient()
        bugs = client.fetch_all_component_open_bugs(1016880)
        self.assertEqual(len(bugs), 2)
        self.assertTrue(
            client.is_test_already_reported(1016880, "loop_handoff_test")
        )
        self.assertFalse(
            client.is_test_already_reported(1016880, "unreported_test")
        )

    @patch("os.path.exists")
    @patch("subprocess.check_output")
    def test_fetch_all_component_open_bugs_ansi_codes(
        self, mock_check_output, mock_exists
    ):
        mock_exists.return_value = True
        mock_check_output.return_value = (
            b"\x1b[32mIssue ID: 998877\x1b[0m\n\x1b[1mTitle: [Flaky] my_colored_test\x1b[0m\n---\n"
        )
        client = BuganizerClient()
        bugs = client.fetch_all_component_open_bugs(1016880)
        self.assertEqual(len(bugs), 1)
        self.assertEqual(bugs[0]["issue_id"], "998877")
        self.assertEqual(bugs[0]["title"], "[Flaky] my_colored_test")

    def test_create_bug_dry_run(self):
        client = BuganizerClient()
        payload = BuganizerIssuePayload(
            title="[Flaky Test] my_test",
            description="Test description",
            component_id=1016880,
        )
        bug_id = client.create_bug(payload, dry_run=True)
        self.assertEqual(bug_id, "b/DRY_RUN_BUG_ID")


if __name__ == "__main__":
    unittest.main()
