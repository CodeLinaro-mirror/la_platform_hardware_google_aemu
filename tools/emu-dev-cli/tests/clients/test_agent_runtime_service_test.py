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

"""Hermetic unit tests for clients.agent_runtime_service (zero @patch decorators)."""

import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.agent_runtime_service import (
    AgentDispatchRequest,
    ENV_ANTIGRAVITY_LS_ADDRESS,
    FakeAgentRuntimeService,
    MODE_AGENTAPI,
    MODE_FAKE,
    MODE_STANDALONE_CLI,
    MODEL_AUTO,
    MODEL_FLASH,
    MODEL_GEMINI_NEXT,
    MODEL_PRO,
    ProdAgentRuntimeService,
    extract_conversation_id_from_output,
    is_complete_rca_summary,
    normalize_agentapi_model,
)
from core.errors import SubprocessError, UsageError
from core.process import FakeProcessRunner


class AgentRuntimeServiceTest(unittest.TestCase):
    """Tests ProdAgentRuntimeService and FakeAgentRuntimeService without spawning agents."""

    def test_helpers_model_normalization_and_rca_validation(self) -> None:
        self.assertEqual(normalize_agentapi_model(MODEL_GEMINI_NEXT), MODEL_PRO)
        self.assertEqual(normalize_agentapi_model(MODEL_AUTO), MODEL_FLASH)
        self.assertEqual(normalize_agentapi_model("flash_lite"), "flash_lite")

        uuid_str = "123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(
            extract_conversation_id_from_output(
                f'{{"conversation_id": "{uuid_str}", "status": "RUNNING"}}\n'
            ),
            uuid_str,
        )
        self.assertEqual(
            extract_conversation_id_from_output(
                f"Started background conversation {uuid_str} successfully."
            ),
            uuid_str,
        )
        self.assertIsNone(extract_conversation_id_from_output("no id here"))

        self.assertTrue(
            is_complete_rca_summary("## Analysis\n```diff\n-old\n+new\n```")
        )
        self.assertTrue(
            is_complete_rca_summary("**Reproducible:** No (hardware flake)")
        )
        self.assertFalse(
            is_complete_rca_summary("**Reproducible:** Yes (needs patch)")
        )

    def test_prod_agent_runtime_agentapi_retries_and_send_message(self) -> None:
        runner = FakeProcessRunner()
        slept: list[float] = []
        svc = ProdAgentRuntimeService(
            runner,
            agentapi_bin="/usr/bin/agentapi",
            env={ENV_ANTIGRAVITY_LS_ADDRESS: "localhost:41234"},
            sleep_fn=slept.append,
        )
        self.assertTrue(svc.is_agent_session_active())

        # First attempt fails transiently, second attempt succeeds
        uuid_str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        runner.queue_response(returncode=1, stderr="LS busy")
        runner.queue_response(
            returncode=0,
            stdout=f"Created conversation {uuid_str}\n",
        )

        req = AgentDispatchRequest(
            prompt="Investigate crash_测试_🚀",
            title="[Autofix] Crash_测试",
            model=MODEL_GEMINI_NEXT,
            agent_definition="Duckie",
            profile="emu_main_next_engineer",
            retries=2,
        )
        res = svc.start_conversation(req)
        self.assertTrue(res.dispatched)
        self.assertEqual(res.mode, MODE_AGENTAPI)
        self.assertEqual(res.conversation_id, uuid_str)
        self.assertEqual(res.model_used, MODEL_PRO)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(slept), 1)
        self.assertIn("--agent=Duckie", runner.calls[0].args)
        self.assertIn("--profile=emu_main_next_engineer", runner.calls[0].args)

        # send_message + get_conversation_metadata
        runner.queue_response(returncode=0, stdout="Message sent")
        msg_res = svc.send_message(
            uuid_str, "Please check patchset 2_🚀", title="Update"
        )
        self.assertTrue(msg_res.dispatched)
        self.assertEqual(msg_res.conversation_id, uuid_str)

        runner.queue_response(
            returncode=0,
            stdout=f'{{"conversation_id": "{uuid_str}", "status": "COMPLETED"}}',
        )
        meta = svc.get_conversation_metadata(uuid_str)
        self.assertEqual(meta["status"], "COMPLETED")

    def test_prod_agent_runtime_standalone_fallback_and_tier_escalation(self) -> None:
        runner = FakeProcessRunner()
        standalone_svc = ProdAgentRuntimeService(
            runner,
            standalone_cli_bin="/google/bin/releases/jetski-devs/tools/cli",
            env={},
            sleep_fn=lambda _s: None,
        )
        self.assertFalse(standalone_svc.is_agent_session_active())

        runner.queue_response(
            returncode=0,
            stdout='{"session_id": "11111111-2222-3333-4444-555555555555"}\n',
        )
        res = standalone_svc.start_conversation(
            AgentDispatchRequest(prompt="Standalone prompt_测试", model=MODEL_PRO)
        )
        self.assertEqual(res.mode, MODE_STANDALONE_CLI)
        self.assertEqual(
            res.conversation_id, "11111111-2222-3333-4444-555555555555"
        )

        # Tier escalation in active session: flash fails -> escalates to pro
        active_svc = ProdAgentRuntimeService(
            runner,
            agentapi_bin="/usr/bin/agentapi",
            env={ENV_ANTIGRAVITY_LS_ADDRESS: "localhost:41234"},
            sleep_fn=lambda _s: None,
        )
        runner.queue_response(returncode=1, stderr="flash quota exhausted")
        runner.queue_response(
            returncode=0,
            stdout="Started 99999999-8888-7777-6666-555555555555",
        )
        escalated = active_svc.dispatch_with_tier_escalation(
            "Refactor module_🚀", title="Step 1", retries=1
        )
        self.assertEqual(escalated.model_used, MODEL_PRO)
        self.assertEqual(
            escalated.conversation_id,
            "99999999-8888-7777-6666-555555555555",
        )

    def test_fake_agent_runtime_service(self) -> None:
        fake = FakeAgentRuntimeService()
        fake.fail_model_tier(MODEL_FLASH)

        res = fake.dispatch_with_tier_escalation(
            "Run complexity check_测试_🚀", title="Complexity"
        )
        self.assertTrue(res.dispatched)
        self.assertEqual(res.mode, MODE_FAKE)
        self.assertEqual(res.model_used, MODEL_PRO)
        self.assertEqual(len(fake.started_requests), 2)

        assert res.conversation_id is not None
        fake.send_message(res.conversation_id, "Follow-up_é", title="Ping")
        self.assertEqual(len(fake.sent_messages), 1)
        self.assertEqual(fake.sent_messages[0].message, "Follow-up_é")

        fake.seed_metadata(res.conversation_id, {"status": "IDLE", "turns": 3})
        self.assertEqual(
            fake.get_conversation_metadata(res.conversation_id)["turns"], 3
        )

        with self.assertRaises(UsageError):
            AgentDispatchRequest(prompt="   ")
        with self.assertRaises(UsageError):
            fake.send_message("", "hello")


if __name__ == "__main__":
    unittest.main()
