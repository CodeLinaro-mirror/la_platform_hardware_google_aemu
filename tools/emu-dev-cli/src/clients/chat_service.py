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

"""Unified Google Chat notification client service (ProdChatNotificationService & FakeChatNotificationService)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
import re
import shutil
from typing import Any

from core.errors import (
    ExecutionError,
    RetryableError,
    SubprocessError,
    UsageError,
)
from core.process import ProcessResult, ProcessRunner

DEFAULT_GCHAT_CLI_BIN = "/google/bin/releases/gemini-agents-gchat/gchat"
ENV_GCHAT_CLI_BIN = "AEMU_GCHAT_CLI_BIN"
GCHAT_DM_TIMEOUT_SEC = 25.0
MAX_CHAT_DM_LENGTH = 4000
CHAT_DM_BASE_URL = "https://chat.google.com/dm"

GOOGLE_CORP_EMAIL_RE = re.compile(r"^[a-z0-9._+-]+@google\.com$")
SIMPLE_LDAP_RE = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")

BOT_OR_AGENT_CHAT_PREFIXES: tuple[str, ...] = (
    "groupagent-",
    "ayeaye-",
    "treehugger",
    "android-build-",
    "gmr-",
    "luci-",
    "automerger",
    "copybara",
)
BOT_OR_AGENT_CHAT_DOMAINS: tuple[str, ...] = (
    ".gserviceaccount.com",
    "@prod.google.com",
    "@twosync-src.google.com",
)


def is_bot_or_agent_chat_email(email: str) -> bool:
    """Returns True if `email` belongs to an automated bot, agent, or service account."""
    lower = (email or "").strip().lower()
    if not lower:
        return True
    if any(lower.startswith(prefix) for prefix in BOT_OR_AGENT_CHAT_PREFIXES):
        return True
    return any(domain in lower for domain in BOT_OR_AGENT_CHAT_DOMAINS)


def resolve_gchat_cli_binary() -> str | None:
    """Resolves the `gchat` CLI executable path from env override, `/google/bin`, or `PATH`."""
    override = (os.environ.get(ENV_GCHAT_CLI_BIN) or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    if os.path.isfile(DEFAULT_GCHAT_CLI_BIN) and os.access(
        DEFAULT_GCHAT_CLI_BIN, os.X_OK
    ):
        return DEFAULT_GCHAT_CLI_BIN
    return shutil.which("gchat")


def normalize_chat_dm_recipient(
    raw_recipient: str,
    sender_email: str = "",
) -> str:
    """Validates and normalizes a single human `@google.com` recipient for a 1:1 Google Chat DM."""
    cleaned = str(raw_recipient or "").strip().lower()
    if not cleaned:
        raise UsageError(
            "Failed to validate Google Chat DM recipient: recipient is empty.",
            remediation="Select a human '@google.com' reviewer on the CL.",
        )
    if "," in cleaned or any(ch.isspace() for ch in cleaned):
        raise UsageError(
            f"Failed to validate Google Chat DM recipient '{raw_recipient}': "
            "multi-recipient DMs are blocked to prevent creating duplicate group chats.",
            remediation="Send 1:1 DMs one reviewer at a time.",
        )
    resolved = (
        f"{cleaned}@google.com"
        if SIMPLE_LDAP_RE.fullmatch(cleaned) and not cleaned.isdigit()
        else cleaned
    )
    if not GOOGLE_CORP_EMAIL_RE.fullmatch(resolved) or is_bot_or_agent_chat_email(
        resolved
    ):
        raise UsageError(
            f"Failed to validate Google Chat DM recipient '{resolved}': "
            "only human '@google.com' accounts can receive 1:1 Chat DMs.",
            remediation="Select a human reviewer on the CL.",
        )
    if sender_email and resolved == sender_email.strip().lower():
        raise UsageError(
            f"Failed to send Google Chat DM to '{resolved}': "
            "Google Chat API does not support 1:1 DMs with yourself.",
            remediation="Choose another reviewer on the CL.",
        )
    return resolved


def validate_chat_dm_message(
    message: str,
    *,
    recipient_email: str,
    cl_number: str = "",
) -> str:
    """Validates that a Google Chat DM body is non-empty and within `MAX_CHAT_DM_LENGTH`."""
    cl_suffix = f" for ag/{cl_number}" if cl_number else ""
    msg_text = str(message or "").strip()
    if not msg_text:
        raise UsageError(
            f"Failed to send Google Chat DM to '{recipient_email}'{cl_suffix}: "
            "message text is empty.",
            remediation="Enter a non-empty review reminder message.",
        )
    if len(msg_text) > MAX_CHAT_DM_LENGTH:
        raise UsageError(
            f"Failed to send Google Chat DM to '{recipient_email}'{cl_suffix}: "
            f"message length ({len(msg_text)} chars) exceeds {MAX_CHAT_DM_LENGTH}.",
            remediation="Shorten the message and retry.",
        )
    return msg_text


def parse_gchat_dm_output(stdout_str: str) -> tuple[str, str, str]:
    """Parses `gchat mutate send-direct-message --json` stdout into `(space_name, message_name, chat_url)`."""
    raw = (stdout_str or "").strip()
    if not raw:
        return "", "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    space_name = str(payload.get("space_name") or "").strip()
    message_name = str(payload.get("message_name") or "").strip()
    space_id = (
        space_name.split("/", 1)[1]
        if space_name.startswith("spaces/")
        else space_name
    )
    chat_url = f"{CHAT_DM_BASE_URL}/{space_id}" if space_id else ""
    return space_name, message_name, chat_url


def format_review_ping_message(
    *,
    cl_number: str,
    subject: str = "",
    gerrit_url: str = "",
    patch_set: int = 1,
    presubmit_score: int = 2,
) -> str:
    """Formats a concise Google Chat review ping message with CL shortlink and summary."""
    clean_cl = str(cl_number).strip()
    link_url = (
        gerrit_url.strip()
        if gerrit_url and gerrit_url.strip()
        else f"https://googleplex-android-review.git.corp.google.com/c/{clean_cl}"
    )
    link_md = f"[ag/{clean_cl}]({link_url})"
    clean_subject = (subject or "").strip()
    subject_clause = f" ({clean_subject})" if clean_subject else ""
    score_str = f"+{presubmit_score}" if presubmit_score > 0 else str(presubmit_score)
    ps_clause = f"PS{patch_set}, " if patch_set > 0 else ""
    return (
        f"Hi! {link_md}{subject_clause} ({ps_clause}Presubmit-Verified: {score_str}) "
        "is green and ready for review when you have a moment."
    )


def execute_gchat_send_dm_command(
    runner: ProcessRunner,
    gchat_bin: str,
    recipient_email: str,
    msg_text: str,
    cl_id: str = "",
    *,
    timeout_sec: float = GCHAT_DM_TIMEOUT_SEC,
) -> ProcessResult:
    """Executes `gchat mutate send-direct-message` via `runner` and raises structured errors on failure."""
    cl_suffix = f" for ag/{cl_id}" if cl_id else ""
    cmd = [
        gchat_bin,
        "mutate",
        "send-direct-message",
        "--usernames",
        recipient_email,
        "--text",
        msg_text,
        "--json",
    ]
    proc = runner.run(
        cmd,
        check=False,
        timeout_sec=timeout_sec,
    )
    if proc.timed_out:
        timeout_label = (
            int(timeout_sec) if float(timeout_sec).is_integer() else timeout_sec
        )
        raise RetryableError(
            f"Timed out after {timeout_label}s while sending Google Chat DM to "
            f"'{recipient_email}'{cl_suffix}.",
            remediation=(
                "Run 'gcert' in your terminal and retry, or click 'Copy Text' "
                "to paste into Google Chat."
            ),
        )
    if proc.returncode != 0:
        detail = (
            proc.stderr or proc.stdout or f"exit code {proc.returncode}"
        ).strip()
        raise SubprocessError(
            f"Failed to send Google Chat DM to '{recipient_email}'{cl_suffix}: {detail}.",
            cmd=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            remediation=(
                "Run 'gcert' in your terminal and retry, or click 'Copy Text' "
                "to paste into Google Chat."
            ),
        )
    return proc


@dataclass(frozen=True)
class ChatDmRequest:
    """Value object recording a 1:1 Google Chat direct message request."""

    recipient: str
    message: str
    cl_number: str = ""
    sender_email: str = ""


@dataclass(frozen=True)
class ChatDmResult:
    """Structured result returned after sending a 1:1 Google Chat direct message."""

    recipient: str
    space_name: str = ""
    message_name: str = ""
    chat_url: str = ""
    cl_number: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes the Chat DM result to a dictionary compatible with CLI/UI JSON payloads."""
        return {
            "status": "ok",
            "cl": self.cl_number,
            "recipient": self.recipient,
            "space_name": self.space_name,
            "message_name": self.message_name,
            "chat_url": self.chat_url,
        }


class ChatNotificationService(ABC):
    """Abstract interface for sending 1:1 Google Chat direct messages and CL review pings."""

    @abstractmethod
    def send_direct_message(
        self,
        recipient: str,
        message: str,
        *,
        cl_number: str = "",
        sender_email: str = "",
    ) -> ChatDmResult:
        """Validates `recipient` and `message` and sends a 1:1 Google Chat direct message."""

    def send_review_ping(
        self,
        *,
        cl_number: str,
        recipient: str,
        subject: str = "",
        gerrit_url: str = "",
        patch_set: int = 1,
        presubmit_score: int = 2,
        custom_message: str = "",
        sender_email: str = "",
    ) -> ChatDmResult:
        """Formats and sends a CL review readiness ping to `recipient` over Google Chat DM."""
        body = (
            custom_message.strip()
            if custom_message and custom_message.strip()
            else format_review_ping_message(
                cl_number=cl_number,
                subject=subject,
                gerrit_url=gerrit_url,
                patch_set=patch_set,
                presubmit_score=presubmit_score,
            )
        )
        return self.send_direct_message(
            recipient,
            body,
            cl_number=str(cl_number).strip(),
            sender_email=sender_email,
        )


class ProdChatNotificationService(ChatNotificationService):
    """Production Google Chat client invoking the `gchat` CLI via ProcessRunner."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        gchat_bin: str | None = None,
        binary_resolver: Callable[[], str | None] | None = None,
        timeout_sec: float = GCHAT_DM_TIMEOUT_SEC,
    ) -> None:
        self._runner = runner
        self._gchat_bin = gchat_bin
        self._binary_resolver = binary_resolver or resolve_gchat_cli_binary
        self._timeout_sec = timeout_sec

    def _resolve_binary(self, recipient_email: str, cl_number: str) -> str:
        """Resolves the `gchat` binary path or raises ExecutionError."""
        resolved = self._gchat_bin or self._binary_resolver()
        if resolved:
            return resolved
        cl_suffix = f" for ag/{cl_number}" if cl_number else ""
        raise ExecutionError(
            f"Failed to send Google Chat DM to '{recipient_email}'{cl_suffix}: "
            f"Google Chat CLI ('{DEFAULT_GCHAT_CLI_BIN}') was not found on this host.",
            remediation=(
                "Run Fast Gerrit UI on gLinux/Cloudtop or click 'Copy Text' "
                "to paste into Chat."
            ),
        )

    def send_direct_message(
        self,
        recipient: str,
        message: str,
        *,
        cl_number: str = "",
        sender_email: str = "",
    ) -> ChatDmResult:
        clean_cl = str(cl_number or "").strip()
        resolved_email = normalize_chat_dm_recipient(
            recipient, sender_email=sender_email
        )
        msg_text = validate_chat_dm_message(
            message,
            recipient_email=resolved_email,
            cl_number=clean_cl,
        )
        gchat_bin = self._resolve_binary(resolved_email, clean_cl)
        proc = execute_gchat_send_dm_command(
            self._runner,
            gchat_bin,
            resolved_email,
            msg_text,
            clean_cl,
            timeout_sec=self._timeout_sec,
        )
        space_name, message_name, chat_url = parse_gchat_dm_output(proc.stdout)
        return ChatDmResult(
            recipient=resolved_email,
            space_name=space_name,
            message_name=message_name,
            chat_url=chat_url,
            cl_number=clean_cl,
            message=msg_text,
        )


class FakeChatNotificationService(ChatNotificationService):
    """In-memory FakeChatNotificationService for hermetic unit tests."""

    def __init__(
        self,
        *,
        default_space_id: str = "FAKE_DM_SPACE",
        failure_error: Exception | None = None,
    ) -> None:
        self.default_space_id = default_space_id
        self.failure_error = failure_error
        self.sent_dms: list[ChatDmRequest] = []

    def send_direct_message(
        self,
        recipient: str,
        message: str,
        *,
        cl_number: str = "",
        sender_email: str = "",
    ) -> ChatDmResult:
        clean_cl = str(cl_number or "").strip()
        resolved_email = normalize_chat_dm_recipient(
            recipient, sender_email=sender_email
        )
        msg_text = validate_chat_dm_message(
            message,
            recipient_email=resolved_email,
            cl_number=clean_cl,
        )
        if self.failure_error is not None:
            raise self.failure_error
        request = ChatDmRequest(
            recipient=resolved_email,
            message=msg_text,
            cl_number=clean_cl,
            sender_email=sender_email,
        )
        self.sent_dms.append(request)
        msg_index = len(self.sent_dms)
        space_name = f"spaces/{self.default_space_id}"
        message_name = f"{space_name}/messages/msg-{msg_index}"
        chat_url = f"{CHAT_DM_BASE_URL}/{self.default_space_id}"
        return ChatDmResult(
            recipient=resolved_email,
            space_name=space_name,
            message_name=message_name,
            chat_url=chat_url,
            cl_number=clean_cl,
            message=msg_text,
        )
