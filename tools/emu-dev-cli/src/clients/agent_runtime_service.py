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

"""Unified Agent Runtime service for agentapi and standalone Jetski CLI dispatch."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any

from core.errors import ExecutionError, ExitCode, SubprocessError, UsageError
from core.process import ProcessResult, ProcessRunner

LOGGER = logging.getLogger(__name__)

ENV_ANTIGRAVITY_LS_ADDRESS = "ANTIGRAVITY_LS_ADDRESS"
ENV_ANTIGRAVITY_CONVERSATION_ID = "ANTIGRAVITY_CONVERSATION_ID"
ENV_JETSKI_CONVERSATION_ID = "JETSKI_CONVERSATION_ID"

DEFAULT_AGENTAPI_CMD = "agentapi"
JETSKI_CLI_RELEASE_PATH = "/google/bin/releases/jetski-devs/tools/cli"
GEMINI_CLI_RELEASE_PATH = "/google/bin/releases/gemini-cli/tools/gemini"

MODEL_PRO = "pro"
MODEL_FLASH = "flash"
MODEL_FLASH_LITE = "flash_lite"
MODEL_AUTO = "auto"
MODEL_GEMINI_NEXT = "Gemini Next"
FAST_TIER_MODELS: frozenset[str] = frozenset({
    MODEL_AUTO,
    MODEL_FLASH,
    MODEL_FLASH_LITE,
})

MODE_AGENTAPI = "agentapi"
MODE_STANDALONE_CLI = "standalone_cli"
MODE_FAKE = "fake"

DEFAULT_DISPATCH_RETRIES: int = 3
DEFAULT_ESCALATION_RETRIES: int = 2
DEFAULT_RETRY_BACKOFF_SEC: float = 2.0
DEFAULT_AGENTAPI_TIMEOUT_SEC: float = 120.0
DEFAULT_METADATA_TIMEOUT_SEC: float = 15.0
DEFAULT_RAW_AGENTAPI_TIMEOUT_SEC: float = 30.0

_UUID_PATTERN = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)

AGENTAPI_REMEDIATION = (
    "Run inside an active Jetski / Antigravity session (ANTIGRAVITY_LS_ADDRESS set) "
    "with 'agentapi' on PATH, or install standalone 'jetski' CLI."
)


def resolve_agentapi_binary(explicit_bin: str | None = None) -> str | None:
    """Resolves the `agentapi` executable path from `explicit_bin`, PATH, or `~/.gemini/jetski/bin`."""
    if explicit_bin:
        return explicit_bin
    found = shutil.which(DEFAULT_AGENTAPI_CMD)
    if found:
        return found
    jetski_bin = Path.home() / ".gemini" / "jetski" / "bin" / DEFAULT_AGENTAPI_CMD
    if jetski_bin.is_file() and os.access(jetski_bin, os.X_OK):
        return str(jetski_bin)
    return None


def resolve_standalone_cli_binary(explicit_bin: str | None = None) -> str | None:
    """Resolves the standalone `jetski` or `gemini` CLI binary path."""
    if explicit_bin:
        return explicit_bin
    jetski_on_path = shutil.which("jetski")
    if jetski_on_path:
        return jetski_on_path
    for release_path in (JETSKI_CLI_RELEASE_PATH, GEMINI_CLI_RELEASE_PATH):
        if os.path.exists(release_path) and os.access(release_path, os.X_OK):
            return release_path
    return shutil.which("gemini")


def normalize_agentapi_model(model: str) -> str:
    """Maps display or auto model names onto canonical `agentapi --model` identifiers."""
    cleaned = (model or MODEL_PRO).strip()
    if cleaned in (MODEL_GEMINI_NEXT, MODEL_PRO, ""):
        return MODEL_PRO
    if cleaned == MODEL_AUTO:
        return MODEL_FLASH
    return cleaned


def extract_conversation_id_from_output(raw_output: str) -> str | None:
    """Extracts a conversation UUID from `agentapi` stdout or `stream-json` lines."""
    if not raw_output or not raw_output.strip():
        return None
    for line in raw_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                cid = (
                    parsed.get("conversation_id")
                    or parsed.get("conversationId")
                    or parsed.get("session_id")
                    or parsed.get("id")
                )
                if cid:
                    return str(cid).strip()
    match = _UUID_PATTERN.search(raw_output)
    return match.group(1) if match else None


def is_complete_rca_summary(text: str) -> bool:
    """Returns True if an RCA summary Markdown contains a code diff or `**Reproducible:** No`."""
    if not text:
        return False
    if "```diff" in text or "Proposed Code Patch" in text:
        return True
    for line in text.splitlines():
        if "**Reproducible:**" in line:
            val = line.split("**Reproducible:**", 1)[1].strip().lower()
            if val.startswith(("no", "false")):
                return True
    return False


@dataclass(frozen=True)
class AgentDispatchRequest:
    """Structured request for starting or resuming an AI agent conversation."""

    prompt: str
    title: str = ""
    model: str = MODEL_PRO
    agent_definition: str | None = None
    profile: str | None = None
    conversation_id: str | None = None
    cwd: str | Path | None = None
    retries: int = DEFAULT_DISPATCH_RETRIES
    timeout_sec: float = DEFAULT_AGENTAPI_TIMEOUT_SEC

    def __post_init__(self) -> None:
        if not str(self.prompt or "").strip():
            raise UsageError(
                "Agent dispatch prompt cannot be empty.",
                remediation="Provide non-empty instructions in AgentDispatchRequest.prompt.",
            )
        if int(self.retries) < 1:
            raise UsageError(
                f"Agent dispatch retries must be >= 1 (got {self.retries}).",
                remediation="Pass a positive integer for retries (e.g. retries=3).",
            )


@dataclass(frozen=True)
class AgentDispatchResult:
    """Structured outcome of an agent conversation dispatch or message operation."""

    dispatched: bool
    mode: str
    conversation_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    returncode: int = int(ExitCode.SUCCESS)
    command: tuple[str, ...] = ()
    model_used: str = ""


class AgentRuntimeService(ABC):
    """Abstract interface for dispatching and messaging AI agent sessions."""

    @abstractmethod
    def is_agent_session_active(self) -> bool:
        """Returns True if running inside an active Antigravity / Jetski session."""

    @abstractmethod
    def start_conversation(
        self, request: AgentDispatchRequest
    ) -> AgentDispatchResult:
        """Starts a new agent conversation (via `agentapi` or standalone CLI)."""

    @abstractmethod
    def send_message(
        self,
        conversation_id: str,
        message: str,
        *,
        title: str | None = None,
        retries: int = DEFAULT_DISPATCH_RETRIES,
        timeout_sec: float = DEFAULT_AGENTAPI_TIMEOUT_SEC,
    ) -> AgentDispatchResult:
        """Sends a message to an existing agent conversation."""

    @abstractmethod
    def get_conversation_metadata(
        self,
        conversation_id: str,
        *,
        timeout_sec: float = DEFAULT_METADATA_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Retrieves metadata for `conversation_id` via `agentapi get-conversation-metadata`."""

    @abstractmethod
    def dispatch_with_tier_escalation(
        self,
        prompt: str,
        *,
        title: str = "Refactor Step",
        profile: str = "emu_main_next_engineer",
        retries: int = DEFAULT_ESCALATION_RETRIES,
    ) -> AgentDispatchResult:
        """Dispatches `prompt` on fast model tier first and escalates to `pro` on failure."""

    @abstractmethod
    def invoke_agentapi_raw(
        self,
        args: Sequence[str],
        *,
        project_id: str | None = None,
        timeout_sec: float = DEFAULT_RAW_AGENTAPI_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Executes a raw `agentapi` CLI subcommand and returns its parsed JSON output."""


class ProdAgentRuntimeService(AgentRuntimeService):
    """Production AgentRuntimeService executing `agentapi` and `jetski` via ProcessRunner."""

    def __init__(
        self,
        process_runner: ProcessRunner,
        *,
        agentapi_bin: str | None = None,
        standalone_cli_bin: str | None = None,
        env: Mapping[str, str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._process_runner = process_runner
        self._agentapi_bin = agentapi_bin
        self._standalone_cli_bin = standalone_cli_bin
        self._env = env
        self._sleep_fn = sleep_fn

    def _effective_env(self) -> Mapping[str, str]:
        return self._env if self._env is not None else os.environ

    def is_agent_session_active(self) -> bool:
        return bool(self._effective_env().get(ENV_ANTIGRAVITY_LS_ADDRESS, "").strip())

    def _resolve_agentapi(self) -> str:
        resolved = resolve_agentapi_binary(self._agentapi_bin)
        if not resolved:
            raise ExecutionError(
                "Could not locate 'agentapi' CLI binary.",
                remediation=AGENTAPI_REMEDIATION,
            )
        return resolved

    def _resolve_standalone(self) -> str:
        resolved = resolve_standalone_cli_binary(self._standalone_cli_bin)
        if not resolved:
            raise ExecutionError(
                "Could not locate standalone 'jetski' or 'gemini' CLI binary.",
                remediation=AGENTAPI_REMEDIATION,
            )
        return resolved

    def _run_with_retries(
        self,
        cmd: list[str],
        *,
        cwd: str | Path | None = None,
        retries: int = DEFAULT_DISPATCH_RETRIES,
        timeout_sec: float = DEFAULT_AGENTAPI_TIMEOUT_SEC,
        operation_label: str,
    ) -> ProcessResult:
        last_result: ProcessResult | None = None
        for attempt in range(1, retries + 1):
            last_result = self._process_runner.run(
                cmd,
                cwd=cwd,
                timeout_sec=timeout_sec,
                check=False,
            )
            if last_result.ok:
                return last_result
            if attempt < retries:
                self._sleep_fn(DEFAULT_RETRY_BACKOFF_SEC)

        assert last_result is not None
        detail = (
            last_result.stderr.strip()
            or last_result.stdout.strip()
            or f"exit code {last_result.returncode}"
        )
        raise SubprocessError(
            f"{operation_label} failed after {retries} attempt(s): {detail}.",
            cmd=cmd,
            returncode=last_result.returncode,
            stdout=last_result.stdout,
            stderr=last_result.stderr,
            timed_out=last_result.timed_out,
            remediation=AGENTAPI_REMEDIATION,
        )

    def _build_agentapi_new_conversation_cmd(
        self, request: AgentDispatchRequest, model_tier: str
    ) -> list[str]:
        bin_path = self._resolve_agentapi()
        cmd = [bin_path, "new-conversation", f"--model={model_tier}"]
        if request.agent_definition:
            cmd.append(f"--agent={request.agent_definition}")
        if request.title:
            cmd.append(f"--title={request.title}")
        if request.profile:
            cmd.append(f"--profile={request.profile}")
        cmd.append(request.prompt)
        return cmd

    def _build_standalone_cmd(self, request: AgentDispatchRequest) -> list[str]:
        bin_path = self._resolve_standalone()
        cmd = [bin_path, f"--model={request.model}"]
        if request.conversation_id:
            cmd.extend(["--conversation", request.conversation_id])
        cmd.extend(["--prompt", request.prompt])
        return cmd

    def start_conversation(
        self, request: AgentDispatchRequest
    ) -> AgentDispatchResult:
        if self.is_agent_session_active():
            model_tier = normalize_agentapi_model(request.model)
            cmd = self._build_agentapi_new_conversation_cmd(request, model_tier)
            res = self._run_with_retries(
                cmd,
                cwd=request.cwd,
                retries=request.retries,
                timeout_sec=request.timeout_sec,
                operation_label="agentapi new-conversation",
            )
            return AgentDispatchResult(
                dispatched=True,
                mode=MODE_AGENTAPI,
                conversation_id=extract_conversation_id_from_output(res.stdout),
                stdout=res.stdout,
                stderr=res.stderr,
                returncode=res.returncode,
                command=res.args,
                model_used=model_tier,
            )

        cmd = self._build_standalone_cmd(request)
        res = self._run_with_retries(
            cmd,
            cwd=request.cwd,
            retries=1,
            timeout_sec=request.timeout_sec,
            operation_label="standalone jetski CLI",
        )
        return AgentDispatchResult(
            dispatched=True,
            mode=MODE_STANDALONE_CLI,
            conversation_id=(
                request.conversation_id
                or extract_conversation_id_from_output(res.stdout)
            ),
            stdout=res.stdout,
            stderr=res.stderr,
            returncode=res.returncode,
            command=res.args,
            model_used=request.model,
        )

    def send_message(
        self,
        conversation_id: str,
        message: str,
        *,
        title: str | None = None,
        retries: int = DEFAULT_DISPATCH_RETRIES,
        timeout_sec: float = DEFAULT_AGENTAPI_TIMEOUT_SEC,
    ) -> AgentDispatchResult:
        clean_cid = (conversation_id or "").strip()
        if not clean_cid:
            raise UsageError(
                "Recipient conversation_id cannot be empty when sending a message.",
                remediation="Provide a non-empty conversation UUID.",
            )
        clean_msg = (message or "").strip()
        if not clean_msg:
            raise UsageError(
                f"Message text for conversation '{clean_cid}' cannot be empty.",
                remediation="Provide a non-empty message string.",
            )

        bin_path = self._resolve_agentapi()
        cmd = [bin_path, "send-message"]
        if title:
            cmd.append(f"--title={title}")
        cmd.extend([clean_cid, clean_msg])

        res = self._run_with_retries(
            cmd,
            retries=max(1, int(retries)),
            timeout_sec=timeout_sec,
            operation_label=f"agentapi send-message to '{clean_cid}'",
        )
        return AgentDispatchResult(
            dispatched=True,
            mode=MODE_AGENTAPI,
            conversation_id=clean_cid,
            stdout=res.stdout,
            stderr=res.stderr,
            returncode=res.returncode,
            command=res.args,
        )

    def get_conversation_metadata(
        self,
        conversation_id: str,
        *,
        timeout_sec: float = DEFAULT_METADATA_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        clean_cid = (conversation_id or "").strip()
        if not clean_cid:
            raise UsageError(
                "conversation_id cannot be empty when querying conversation metadata.",
                remediation="Provide a non-empty conversation UUID.",
            )
        bin_path = self._resolve_agentapi()
        cmd = [bin_path, "get-conversation-metadata", clean_cid]
        res = self._process_runner.run(
            cmd,
            timeout_sec=timeout_sec,
            check=True,
            remediation=AGENTAPI_REMEDIATION,
        )
        raw = res.stdout.strip()
        if not raw:
            return {"conversation_id": clean_cid}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except ValueError:
            return {"conversation_id": clean_cid, "raw": raw}

    def dispatch_with_tier_escalation(
        self,
        prompt: str,
        *,
        title: str = "Refactor Step",
        profile: str = "emu_main_next_engineer",
        retries: int = DEFAULT_ESCALATION_RETRIES,
    ) -> AgentDispatchResult:
        fast_request = AgentDispatchRequest(
            prompt=prompt,
            title=title,
            model=MODEL_FLASH,
            profile=profile,
            retries=retries,
        )
        try:
            return self.start_conversation(fast_request)
        except SubprocessError as fast_err:
            if not self.is_agent_session_active():
                raise
            LOGGER.warning(
                "Fast model tier (%s) failed (%s); escalating '%s' to %s tier.",
                MODEL_FLASH,
                fast_err,
                title,
                MODEL_PRO,
            )
            pro_request = AgentDispatchRequest(
                prompt=prompt,
                title=f"{title} (Pro Escalation)",
                model=MODEL_PRO,
                profile=profile,
                retries=retries,
            )
            return self.start_conversation(pro_request)

    def invoke_agentapi_raw(
        self,
        args: Sequence[str],
        *,
        project_id: str | None = None,
        timeout_sec: float = DEFAULT_RAW_AGENTAPI_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        bin_path = resolve_agentapi_binary(self._agentapi_bin) or DEFAULT_AGENTAPI_CMD
        env = dict(self._effective_env())
        if project_id:
            env["ANTIGRAVITY_PROJECT_ID"] = project_id
        else:
            env.pop("ANTIGRAVITY_SOURCE_METADATA", None)
        res = self._process_runner.run(
            [bin_path, *args],
            env=env,
            check=False,
            timeout_sec=timeout_sec,
        )
        if res.returncode != 0:
            subcmd = args[0] if args else "command"
            detail = (
                (res.stderr or res.stdout or "").strip()
                or f"exit code {res.returncode}"
            )
            raise RuntimeError(
                f"agentapi '{subcmd}' failed (exit {res.returncode}): {detail}. "
                "Verify the Jetski agentapi binary and active language server session."
            )
        out = (res.stdout or "").strip()
        return json.loads(out) if out else {}


@dataclass(frozen=True)
class FakeSentMessage:
    """Recorded `send_message` call captured by FakeAgentRuntimeService."""

    conversation_id: str
    message: str
    title: str | None = None


class FakeAgentRuntimeService(AgentRuntimeService):
    """Hermetic in-memory AgentRuntimeService for unit testing without spawning agents."""

    def __init__(self, *, session_active: bool = True) -> None:
        self._session_active = bool(session_active)
        self._next_index = 1
        self._fail_models: set[str] = set()
        self._metadata: dict[str, dict[str, Any]] = {}
        self._raw_agentapi_responses: dict[str, dict[str, Any]] = {}
        self.started_requests: list[AgentDispatchRequest] = []
        self.sent_messages: list[FakeSentMessage] = []
        self.raw_agentapi_calls: list[tuple[tuple[str, ...], str | None]] = []

    def seed_raw_agentapi_response(
        self, subcommand: str, payload: Mapping[str, Any]
    ) -> None:
        """Seeds the JSON payload returned by `invoke_agentapi_raw` for `subcommand`."""
        self._raw_agentapi_responses[subcommand] = dict(payload)

    def set_session_active(self, active: bool) -> None:
        """Configures whether `is_agent_session_active()` returns True or False."""
        self._session_active = bool(active)

    def fail_model_tier(self, model: str) -> None:
        """Configures `start_conversation` to raise SubprocessError when `model` is requested."""
        self._fail_models.add(model)

    def seed_metadata(
        self, conversation_id: str, metadata: Mapping[str, Any]
    ) -> None:
        """Seeds metadata returned by `get_conversation_metadata(conversation_id)`."""
        self._metadata[conversation_id] = dict(metadata)

    def is_agent_session_active(self) -> bool:
        return self._session_active

    def start_conversation(
        self, request: AgentDispatchRequest
    ) -> AgentDispatchResult:
        self.started_requests.append(request)
        effective_model = (
            normalize_agentapi_model(request.model)
            if self._session_active
            else request.model
        )
        if effective_model in self._fail_models or request.model in self._fail_models:
            raise SubprocessError(
                f"Simulated failure for model tier '{effective_model}'.",
                cmd=(DEFAULT_AGENTAPI_CMD, "new-conversation"),
                returncode=int(ExitCode.FAILURE),
                remediation=AGENTAPI_REMEDIATION,
            )
        cid = request.conversation_id or f"00000000-0000-0000-0000-{self._next_index:012d}"
        self._next_index += 1
        return AgentDispatchResult(
            dispatched=True,
            mode=MODE_FAKE,
            conversation_id=cid,
            stdout=f"Started conversation {cid}",
            model_used=effective_model,
        )

    def send_message(
        self,
        conversation_id: str,
        message: str,
        *,
        title: str | None = None,
        retries: int = DEFAULT_DISPATCH_RETRIES,
        timeout_sec: float = DEFAULT_AGENTAPI_TIMEOUT_SEC,
    ) -> AgentDispatchResult:
        del retries, timeout_sec
        clean_cid = (conversation_id or "").strip()
        clean_msg = (message or "").strip()
        if not clean_cid or not clean_msg:
            raise UsageError(
                "conversation_id and message must be non-empty.",
                remediation="Pass valid non-empty strings to send_message().",
            )
        self.sent_messages.append(
            FakeSentMessage(
                conversation_id=clean_cid,
                message=clean_msg,
                title=title,
            )
        )
        return AgentDispatchResult(
            dispatched=True,
            mode=MODE_FAKE,
            conversation_id=clean_cid,
            stdout=f"Delivered message to {clean_cid}",
        )

    def get_conversation_metadata(
        self,
        conversation_id: str,
        *,
        timeout_sec: float = DEFAULT_METADATA_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        del timeout_sec
        clean_cid = (conversation_id or "").strip()
        if not clean_cid:
            raise UsageError(
                "conversation_id cannot be empty.",
                remediation="Provide a non-empty conversation UUID.",
            )
        return dict(
            self._metadata.get(clean_cid, {"conversation_id": clean_cid, "status": "IDLE"})
        )

    def dispatch_with_tier_escalation(
        self,
        prompt: str,
        *,
        title: str = "Refactor Step",
        profile: str = "emu_main_next_engineer",
        retries: int = DEFAULT_ESCALATION_RETRIES,
    ) -> AgentDispatchResult:
        fast_req = AgentDispatchRequest(
            prompt=prompt,
            title=title,
            model=MODEL_FLASH,
            profile=profile,
            retries=retries,
        )
        try:
            return self.start_conversation(fast_req)
        except SubprocessError:
            pro_req = AgentDispatchRequest(
                prompt=prompt,
                title=f"{title} (Pro Escalation)",
                model=MODEL_PRO,
                profile=profile,
                retries=retries,
            )
            return self.start_conversation(pro_req)

    def invoke_agentapi_raw(
        self,
        args: Sequence[str],
        *,
        project_id: str | None = None,
        timeout_sec: float = DEFAULT_RAW_AGENTAPI_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        del timeout_sec
        arg_tuple = tuple(str(a) for a in args)
        self.raw_agentapi_calls.append((arg_tuple, project_id))
        subcmd = arg_tuple[0] if arg_tuple else ""
        if subcmd in self._raw_agentapi_responses:
            return dict(self._raw_agentapi_responses[subcmd])
        if subcmd == "new-conversation":
            cid = f"00000000-0000-0000-0000-{self._next_index:012d}"
            self._next_index += 1
            return {"response": {"newConversation": {"conversationId": cid}}}
        if subcmd == "get-conversation-metadata" and len(arg_tuple) >= 2:
            return self.get_conversation_metadata(arg_tuple[1])
        return {"status": "ok", "subcommand": subcmd}
