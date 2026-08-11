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

"""Unit tests for lib.agent_dispatcher module."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.agent_dispatcher import AgentDispatcher
from lib.ath_api import FlakyTestRecord


class AgentDispatcherTest(unittest.TestCase):
    """Tests for AgentDispatcher handling subagent vs standalone execution modes."""

    def setUp(self):
        self.test_record = FlakyTestRecord(
            test_identifier="@goldfish//emulator/libs/process:process_unittests",
            module_name="process",
            target="emulator_linux_x64",
            config_name="devtools/emulator",
            total_runs=100,
            failed_runs=10,
            flake_rate_pct=10.0,
            latest_build_id="P123",
            latest_invocation_id="I456",
            latest_work_unit_id="WU789",
        )
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.sandbox_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch.dict(os.environ, {"ANTIGRAVITY_LS_ADDRESS": "localhost:32977"})
    def test_is_agent_session_active_returns_true(self):
        dispatcher = AgentDispatcher()
        self.assertTrue(dispatcher.is_agent_session_active())

    @patch.dict(os.environ, {}, clear=True)
    def test_is_agent_session_active_returns_false_standalone(self):
        dispatcher = AgentDispatcher()
        self.assertFalse(dispatcher.is_agent_session_active())

    @patch.dict(os.environ, {"ANTIGRAVITY_LS_ADDRESS": "localhost:32977"})
    @patch("lib.agent_dispatcher.resolve_agentapi_binary")
    def test_verify_environment_agent_session_ok(self, mock_resolve):
        mock_resolve.return_value = "/bin/agentapi"
        dispatcher = AgentDispatcher()
        dispatcher.verify_environment()

    @patch.dict(os.environ, {}, clear=True)
    @patch("lib.agent_dispatcher.AgentDispatcher.resolve_standalone_cli")
    def test_verify_environment_standalone_ok(self, mock_resolve):
        mock_resolve.return_value = "/bin/jetski"
        dispatcher = AgentDispatcher()
        dispatcher.verify_environment()

    @patch.dict(os.environ, {"ANTIGRAVITY_LS_ADDRESS": "localhost:32977"})
    @patch("lib.agent_dispatcher.AgentApiClient.start_conversation")
    def test_dispatch_investigation_agent_session_mode(self, mock_start):
        def create_rca(*args, **kwargs):
            rca_file = self.sandbox_dir / "rca_summary.md"
            rca_file.write_text(
                "### 🔬 Jetski RCA Analysis\n\n**Proposed Code Patch:**\n```diff\n--- a/test.cc\n+++ b/test.cc\n```\n",
                encoding="utf-8",
            )
        mock_start.side_effect = create_rca

        dispatcher = AgentDispatcher()
        dispatcher.dispatch_investigation(
            record=self.test_record,
            prompt="Test Prompt",
            sandbox_dir=self.sandbox_dir,
            wait_timeout=5.0,
        )

        mock_start.assert_called_once()
        self.assertTrue((self.sandbox_dir / "investigation_prompt.txt").exists())

    @patch.dict(os.environ, {}, clear=True)
    @patch("lib.agent_dispatcher.AgentDispatcher.resolve_standalone_cli")
    @patch("subprocess.run")
    def test_dispatch_investigation_standalone_mode(self, mock_run, mock_resolve):
        mock_resolve.return_value = "/bin/jetski"
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        dispatcher = AgentDispatcher()
        dispatcher.dispatch_investigation(
            record=self.test_record,
            prompt="Test Prompt",
            sandbox_dir=self.sandbox_dir,
        )

        script_path = self.sandbox_dir / "investigation_cmd.sh"
        self.assertTrue(script_path.exists())
        self.assertTrue((self.sandbox_dir / "investigation_prompt.txt").exists())
        mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
