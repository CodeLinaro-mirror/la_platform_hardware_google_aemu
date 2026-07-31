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

"""Unit tests for lib.agent module."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.agent import AgentApiClient


class AgentApiClientTest(unittest.TestCase):
    """Tests for AgentApiClient wrapper."""

    @patch("subprocess.run")
    def test_start_conversation(self, mock_run):
        """Tests launching a new agent conversation via agentapi CLI."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Conversation ID: 1234\n"
        )
        client = AgentApiClient()
        res = client.start_conversation(
            prompt="Fix the bug",
            agent=".gemini/agents/emu_main_next_engineer.md",
            model="pro",
        )
        mock_run.assert_called_once_with(
            [
                "agentapi",
                "new-conversation",
                "--model=pro",
                "--agent=.gemini/agents/emu_main_next_engineer.md",
                "Fix the bug",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(res.returncode, 0)

    @patch("subprocess.run")
    def test_send_message(self, mock_run):
        """Tests sending a message to an existing conversation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Message sent\n")
        client = AgentApiClient()
        res = client.send_message(
            recipient_id="conv-123",
            message="Please check tests",
            title="Task update",
        )
        mock_run.assert_called_once_with(
            [
                "agentapi",
                "send-message",
                "--title=Task update",
                "conv-123",
                "Please check tests",
            ],
            capture_output=True,
            text=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
