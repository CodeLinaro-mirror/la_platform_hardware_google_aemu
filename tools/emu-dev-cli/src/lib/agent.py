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

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class AgentApiClient:
    """Client for programmatically dispatching conversations and messaging subagents via agentapi CLI."""

    def __init__(self, cli_binary: str = "agentapi") -> None:
        """Initializes AgentApiClient.

        Args:
            cli_binary: The CLI command name or path to the agentapi tool (default: 'agentapi').
        """
        self.cli_binary = cli_binary

    def start_conversation(
        self,
        prompt: str,
        agent: Optional[str] = None,
        model: str = "pro",
        title: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        """Starts a new agent conversation.

        Args:
            prompt: Prompt instruction text to send to the subagent.
            agent: Optional path to markdown agent definition (e.g. '.gemini/agents/emu_main_next_engineer.md').
            model: Model tier ('pro', 'flash', 'flash_lite'). Defaults to 'pro'.
            title: Optional title for the new conversation.
            profile: Optional agent profile name.

        Returns:
            CompletedProcess execution result.
        """
        cmd = [self.cli_binary, "new-conversation", f"--model={model}"]
        if agent:
            cmd.append(f"--agent={agent}")
        if title:
            cmd.append(f"--title={title}")
        if profile:
            cmd.append(f"--profile={profile}")
        cmd.append(prompt)

        logger.debug("Dispatching agentapi start_conversation: %s", " ".join(cmd[:cmd.index(prompt)]))
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    def send_message(
        self,
        recipient_id: str,
        message: str,
        title: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        """Sends a message to an existing conversation.

        Args:
            recipient_id: The conversation ID of the recipient agent.
            message: The message body to deliver.
            title: Optional title for the message notification.

        Returns:
            CompletedProcess execution result.
        """
        cmd = [self.cli_binary, "send-message"]
        if title:
            cmd.append(f"--title={title}")
        cmd.extend([recipient_id, message])

        logger.debug("Dispatching agentapi send_message to recipient %s", recipient_id)
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
