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

"""Agent API client module for interacting with Antigravity subagents."""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_agentapi_binary() -> Optional[str]:
    """Resolves the executable path to agentapi, checking PATH and standard installation paths."""
    found = shutil.which("agentapi")
    if found:
        return found
    jetski_bin = Path.home() / ".gemini" / "jetski" / "bin" / "agentapi"
    if jetski_bin.exists() and os.access(jetski_bin, os.X_OK):
        return str(jetski_bin)
    return None


def verify_agentapi_available() -> str:
    """Verifies upfront that agentapi binary is available and executable and ANTIGRAVITY_LS_ADDRESS is set.

    Raises:
        RuntimeError: If agentapi is not found or ANTIGRAVITY_LS_ADDRESS is not set.
    """
    bin_path = resolve_agentapi_binary()
    if not bin_path:
        sys.stderr.write(
            "\n❌ Error: Could not locate 'agentapi' CLI tool.\n"
            "   Ensure Antigravity / Jetski agent tools are installed and 'agentapi' is available in PATH or ~/.gemini/jetski/bin/agentapi.\n"
        )
        raise RuntimeError(
            "Could not locate 'agentapi' CLI tool. Ensure Antigravity agent tools are installed and in PATH."
        )

    if not os.environ.get("ANTIGRAVITY_LS_ADDRESS"):
        sys.stderr.write(
            "\n⚠️ Notice: 'ANTIGRAVITY_LS_ADDRESS' environment variable is not set.\n"
            "   'agentapi' requires an active Jetski / Antigravity agent session to dispatch subagents.\n"
            "   To run automated AI root cause analysis, execute 'emu-dev-cli flakiness triage' from within an active Jetski session.\n"
        )
        raise RuntimeError(
            "ANTIGRAVITY_LS_ADDRESS environment variable is not set. Run 'emu-dev-cli flakiness triage' within an active Jetski session."
        )

    return bin_path


class AgentApiClient:
    """Client for programmatically dispatching conversations and messaging subagents via agentapi CLI."""

    def __init__(self, cli_binary: Optional[str] = None) -> None:
        """Initializes AgentApiClient.

        Args:
            cli_binary: The CLI command name or path to the agentapi tool (auto-resolved if None).
        """
        self.cli_binary = cli_binary or resolve_agentapi_binary() or "agentapi"

    def start_conversation(
        self,
        prompt: str,
        agent: Optional[str] = None,
        model: str = "pro",
        title: Optional[str] = None,
        profile: Optional[str] = None,
        retries: int = 3,
    ) -> subprocess.CompletedProcess:
        """Starts a new agent conversation with automatic retries on temporary RPC glitches.

        Args:
            prompt: Prompt instruction text to send to the subagent.
            agent: Optional path to markdown agent definition (e.g. '.gemini/agents/emu_main_next_engineer.md').
            model: Model tier ('pro', 'flash', 'flash_lite'). Defaults to 'pro'.
            title: Optional title for the new conversation.
            profile: Optional agent profile name.
            retries: Number of retry attempts on non-zero exit (default: 3).

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
        last_error = ""
        for attempt in range(1, retries + 1):
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res
            last_error = (res.stderr or res.stdout or f"Exit code {res.returncode}").strip()
            if attempt < retries:
                time.sleep(2.0)

        raise RuntimeError(
            f"agentapi new-conversation failed after {retries} attempts ({last_error})"
        )

    def send_message(
        self,
        recipient_id: str,
        message: str,
        title: Optional[str] = None,
        retries: int = 3,
    ) -> subprocess.CompletedProcess:
        """Sends a message to an existing conversation with automatic retries.

        Args:
            recipient_id: The conversation ID of the recipient agent.
            message: The message body to deliver.
            title: Optional title for the message notification.
            retries: Number of retry attempts on non-zero exit (default: 3).

        Returns:
            CompletedProcess execution result.
        """
        cmd = [self.cli_binary, "send-message"]
        if title:
            cmd.append(f"--title={title}")
        cmd.extend([recipient_id, message])

        logger.debug("Dispatching agentapi send_message to recipient %s", recipient_id)
        last_error = ""
        for attempt in range(1, retries + 1):
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res
            last_error = (res.stderr or res.stdout or f"Exit code {res.returncode}").strip()
            if attempt < retries:
                time.sleep(2.0)

        raise RuntimeError(
            f"agentapi send-message failed after {retries} attempts ({last_error})"
        )
