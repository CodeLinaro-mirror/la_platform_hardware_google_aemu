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

"""Agent Dispatcher module for handling AI root cause analysis dispatches in both agent session and standalone modes."""

import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

from lib.agent import AgentApiClient, resolve_agentapi_binary

logger = logging.getLogger(__name__)


class AgentDispatcher:
    """Dispatches AI root-cause analysis investigations via subagents (agentapi) when in an active agent session,
    or directly via Jetski CLI when running in standalone mode.
    """

    def __init__(self, cli_binary: Optional[str] = None) -> None:
        """Initializes AgentDispatcher."""
        self.cli_binary = cli_binary or resolve_agentapi_binary() or "agentapi"
        self.agent_client = AgentApiClient(cli_binary=self.cli_binary)

    def is_agent_session_active(self) -> bool:
        """Returns True if running within an active Antigravity/Jetski agent session (ANTIGRAVITY_LS_ADDRESS is set)."""
        return bool(os.environ.get("ANTIGRAVITY_LS_ADDRESS"))

    def resolve_standalone_cli(self) -> Optional[str]:
        """Resolves the executable path to standalone jetski or gemini CLI for direct invocation."""
        if shutil.which("jetski"):
            return shutil.which("jetski")
        paths = [
            "/google/bin/releases/jetski-devs/tools/cli",
            "/google/bin/releases/gemini-cli/tools/gemini",
        ]
        for path in paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
        return shutil.which("gemini")

    def verify_environment(self) -> None:
        """Verifies upfront that either an active agent session is present or a standalone CLI binary is available.

        Raises:
            RuntimeError: If neither agentapi session nor standalone CLI binary is available.
        """
        if self.is_agent_session_active():
            bin_path = resolve_agentapi_binary()
            if not bin_path:
                sys.stderr.write(
                    "\n❌ Error: Active agent session detected, but 'agentapi' binary was not found.\n"
                    "   Ensure agent tools are installed in PATH or ~/.gemini/jetski/bin/agentapi.\n"
                )
                raise RuntimeError("Could not locate 'agentapi' CLI tool.")
        else:
            standalone_bin = self.resolve_standalone_cli()
            if not standalone_bin:
                sys.stderr.write(
                    "\n❌ Error: Running in standalone mode, but no 'jetski' or 'gemini' CLI tool was found.\n"
                    "   Ensure jetski or gemini CLI is installed or available at /google/bin/releases/jetski-devs/tools/cli.\n"
                )
                raise RuntimeError(
                    "Could not locate 'jetski' or 'gemini' CLI binary for standalone execution."
                )

    def dispatch_investigation(
        self,
        record: Any,
        prompt: str,
        sandbox_dir: Path,
        profile: str = "emu_main_next_engineer",
        title: str = "Flaky Test RCA",
        model: str = "Gemini Next",
        wait_timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> None:
        """Dispatches an RCA investigation prompt.

        - In agent session mode: Uses AgentApiClient (agentapi) to spawn a subagent and polls for completion.
        - In standalone mode: Invokes Jetski CLI directly and writes investigation_cmd.sh.

        Args:
            record: FlakyTestRecord being triaged.
            prompt: Investigation prompt text.
            sandbox_dir: Diagnostic output sandbox directory.
            profile: Agent profile name for agentapi subagents.
            title: Title for conversation.
            model: AI model string (default: 'Gemini Next').
            wait_timeout: Max wait time in seconds for analysis completion (default: 600s).
            poll_interval: Polling interval in seconds (default: 2.0s).

        Raises:
            RuntimeError: If dispatch fails or times out without generating a valid RCA summary.
        """
        sandbox_dir = Path(sandbox_dir)
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = sandbox_dir / "investigation_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        rca_summary_path = sandbox_dir / "rca_summary.md"

        # Check if valid cached report already exists
        if rca_summary_path.exists():
            existing_text = rca_summary_path.read_text(encoding="utf-8", errors="replace")
            if "```diff" in existing_text:
                return
            try:
                rca_summary_path.unlink()
            except OSError:
                pass

        if self.is_agent_session_active():
            # Mode A: Active Agent Session -> Dispatch Subagent via agentapi
            logger.info("Dispatching subagent via agentapi for %s (model=%s, profile=%s)", record.test_identifier, model, profile)
            sys.stderr.write(
                f"\n🤖 Active agent session detected. Dispatching subagent via agentapi to investigate {record.test_identifier}...\n"
            )
            sys.stderr.write(
                f"⏳ Waiting for AI agent to complete RCA investigation and write {rca_summary_path.name}...\n"
            )

            agentapi_model = "pro" if model in ("Gemini Next", "pro") else model
            client = AgentApiClient(cli_binary=self.cli_binary)
            client.start_conversation(
                prompt=prompt,
                model=agentapi_model,
                profile=profile,
                title=f"{title}: {record.test_identifier}",
            )

            # Poll for completion
            start_time = time.time()
            elapsed = 0.0
            agent_completed = False

            while elapsed < wait_timeout:
                if rca_summary_path.exists():
                    text = rca_summary_path.read_text(encoding="utf-8", errors="replace")
                    if "Proposed Code Patch" in text or "```diff" in text:
                        logger.info("Subagent completed RCA investigation for %s in %.1fs", record.test_identifier, elapsed)
                        sys.stderr.write(
                            f"\n✅ Subagent completed RCA investigation in {elapsed:.1f}s!\n"
                        )
                        agent_completed = True
                        break
                time.sleep(poll_interval)
                elapsed = time.time() - start_time
                sys.stderr.write(
                    f"⏳ Polling subagent progress ({elapsed:.0f}s / {wait_timeout:.0f}s)... (Analysis can take up to 10 minutes)\r"
                )
                sys.stderr.flush()
            sys.stderr.write("\n")

            if not agent_completed:
                logger.error("RCA investigation timed out for %s after %.0fs", record.test_identifier, wait_timeout)
                sys.stderr.write(
                    f"\n❌ Error: AI root-cause analysis for '{record.test_identifier}' timed out after {wait_timeout:.0f} seconds.\n"
                    f"   Prompt path: file://{prompt_path}\n"
                )
                raise RuntimeError(
                    f"AI root-cause analysis for '{record.test_identifier}' timed out after {wait_timeout:.0f} seconds."
                )

        else:
            # Mode B: Standalone Mode -> Invoke Jetski CLI Directly
            standalone_bin = self.resolve_standalone_cli()
            if not standalone_bin:
                raise RuntimeError("Standalone mode: No 'jetski' or 'gemini' CLI binary found.")

            logger.info("Executing standalone Jetski CLI binary %s for %s (model=%s)", standalone_bin, record.test_identifier, model)

            sys.stderr.write(
                f"\n🚀 Standalone mode detected (outside agent session).\n"
                f"   Invoking Jetski CLI directly ({standalone_bin})...\n"
            )

            script_path = sandbox_dir / "investigation_cmd.sh"
            cmd_args = [
                standalone_bin,
                f"--model={model}",
                "--prompt",
                prompt,
            ]

            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env bash\n")
                f.write(f"# Interactive/Standalone Jetski investigation for {record.test_identifier}\n\n")
                f.write(f'exec {shlex.quote(standalone_bin)} --model={shlex.quote(model)} --prompt-interactive "$(< {shlex.quote(str(prompt_path))})"\n')
            script_path.chmod(0o755)

            sys.stderr.write(
                f"📋 Interactive launcher created: file://{script_path}\n"
                f"⏳ Executing Jetski CLI to generate root cause analysis...\n"
            )

            res = subprocess.run(cmd_args, capture_output=True, text=True, check=False)
            if res.returncode != 0 and not rca_summary_path.exists():
                sys.stderr.write(
                    f"\n❌ Error: Standalone Jetski CLI execution failed with exit code {res.returncode}.\n"
                    f"   Stderr: {res.stderr or res.stdout}\n"
                )
                raise RuntimeError(
                    f"Standalone Jetski CLI execution failed for '{record.test_identifier}': {res.stderr or res.stdout}"
                )
