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

"""Typed `emu-dev-cli gerrit ping-for-review` command implementation."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
import time
from typing import Any

from clients.chat_service import normalize_chat_dm_recipient
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import (
    EmuCliError,
    ExecutionError,
    RetryableError,
    UsageError,
)
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GerritClient,
    parse_cl_identifier,
)

DEFAULT_PING_TIMEOUT_SEC = 1800
DEFAULT_PING_POLL_INTERVAL_SEC = 20
REQUIRED_PRESUBMIT_VERIFIED_SCORE = 2


def _extract_patchset_number(detail: Mapping[str, Any]) -> int:
    """Extracts the current patchset number from a Gerrit change detail payload."""
    cur_rev = str(detail.get("current_revision") or "")
    revisions = detail.get("revisions")
    if isinstance(revisions, Mapping) and cur_rev in revisions:
        rev_info = revisions.get(cur_rev)
        if isinstance(rev_info, Mapping):
            return int(rev_info.get("_number", 1) or 1)
    return 1


def _format_gerrit_cl_url(
    host: str, detail: Mapping[str, Any], cl_number: int | str
) -> str:
    """Formats the canonical Gerrit review URL for `cl_number`."""
    clean_host = (host or DEFAULT_GERRIT_HOST).rstrip("/")
    project = str(detail.get("project") or "").strip().strip("/")
    if project:
        return f"{clean_host}/c/{project}/+/{cl_number}"
    return f"{clean_host}/c/{cl_number}"


def _format_score(score: int) -> str:
    """Formats a Gerrit label integer score with an explicit `+` prefix when positive."""
    return f"+{score}" if score > 0 else str(score)


@dataclass(frozen=True)
class PingArgs:
    """Validated arguments for `GerritPingForReviewCommand`."""

    cl: str = "HEAD"
    to: str = ""
    repo: str = "."
    host: str = DEFAULT_GERRIT_HOST
    timeout: int = DEFAULT_PING_TIMEOUT_SEC
    poll_interval: int = DEFAULT_PING_POLL_INTERVAL_SEC
    required_score: int = REQUIRED_PRESUBMIT_VERIFIED_SCORE
    no_wait: bool = False
    message: str = ""
    sender_email: str = ""


@dataclass(frozen=True)
class PingResult(Mapping[str, Any]):
    """Structured output returned by `GerritPingForReviewCommand`."""

    cl_number: int
    change_id: str
    subject: str
    patch_set: int
    presubmit_score: int
    recipient: str
    gerrit_url: str
    space_name: str = ""
    message_name: str = ""
    chat_url: str = ""
    message: str = ""
    polls: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serializes the ping result into a JSON-compatible dictionary."""
        return {
            "cl_number": self.cl_number,
            "change_id": self.change_id,
            "subject": self.subject,
            "patch_set": self.patch_set,
            "presubmit_score": self.presubmit_score,
            "recipient": self.recipient,
            "gerrit_url": self.gerrit_url,
            "space_name": self.space_name,
            "message_name": self.message_name,
            "chat_url": self.chat_url,
            "message": self.message,
            "polls": self.polls,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


GerritClientBuilder = Callable[[ExecutionContext, str], GerritClient]


class GerritPingForReviewCommand(Command[PingArgs, PingResult]):
    """Polls Gerrit for `Presubmit-Verified: +2` and sends an automated 1:1 Chat review ping."""

    name = "ping-for-review"
    help_text = (
        "Poll Gerrit for Presubmit-Verified: +2 and send a 1:1 Chat review ping"
    )

    def __init__(
        self,
        *,
        gerrit_client_factory: GerritClientBuilder | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gerrit_client_factory = gerrit_client_factory
        self._sleep_fn = sleep_fn
        self._clock_fn = clock_fn

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, Change-Id, Gerrit URL, or HEAD (default: HEAD)",
        )
        parser.add_argument(
            "--cl",
            dest="cl_flag",
            default=None,
            help="Explicit CL number, Change-Id, or Gerrit URL",
        )
        parser.add_argument(
            "--to",
            "--recipient",
            dest="to",
            required=True,
            help="Reviewer LDAP or '@google.com' email to notify over Google Chat DM",
        )
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git repository path when resolving HEAD (default: .)",
        )
        parser.add_argument(
            "--host",
            default=DEFAULT_GERRIT_HOST,
            help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_PING_TIMEOUT_SEC,
            help=(
                "Maximum seconds to poll for Presubmit-Verified: +2 "
                f"(default: {DEFAULT_PING_TIMEOUT_SEC})"
            ),
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=DEFAULT_PING_POLL_INTERVAL_SEC,
            help=(
                "Seconds between Gerrit poll requests "
                f"(default: {DEFAULT_PING_POLL_INTERVAL_SEC})"
            ),
        )
        parser.add_argument(
            "--no-wait",
            action="store_true",
            help="Check Presubmit-Verified: +2 once immediately without polling",
        )
        parser.add_argument(
            "--message",
            default="",
            help="Optional custom Google Chat review reminder message",
        )
        parser.add_argument(
            "--sender",
            dest="sender_email",
            default="",
            help="Optional sender '@google.com' email for self-DM validation",
        )

    def validate(
        self, args: PingArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> PingArgs:
        del ctx
        if isinstance(args, PingArgs):
            return self._validate_ping_args(args)
        return self.parse_args(args)

    def _validate_ping_args(self, args: PingArgs) -> PingArgs:
        resolved_to = normalize_chat_dm_recipient(
            args.to, sender_email=args.sender_email
        )
        if args.timeout < 0:
            raise UsageError(
                f"Invalid --timeout value ({args.timeout}): timeout must be >= 0 seconds.",
                remediation="Pass a non-negative integer to --timeout (or use --no-wait).",
            )
        if args.poll_interval < 1:
            raise UsageError(
                f"Invalid --poll-interval value ({args.poll_interval}): poll interval must be >= 1 second.",
                remediation="Pass an integer >= 1 to --poll-interval.",
            )
        return PingArgs(
            cl=str(args.cl or "HEAD").strip() or "HEAD",
            to=resolved_to,
            repo=str(args.repo or "."),
            host=str(args.host or DEFAULT_GERRIT_HOST),
            timeout=int(args.timeout),
            poll_interval=int(args.poll_interval),
            required_score=int(args.required_score),
            no_wait=bool(args.no_wait),
            message=str(args.message or ""),
            sender_email=str(args.sender_email or "").strip(),
        )

    def parse_args(self, ns: argparse.Namespace) -> PingArgs:
        raw_cl = (
            getattr(ns, "cl_flag", None)
            or getattr(ns, "cl", None)
            or "HEAD"
        )
        candidate = PingArgs(
            cl=str(raw_cl).strip() or "HEAD",
            to=str(getattr(ns, "to", "") or "").strip(),
            repo=str(getattr(ns, "repo", ".") or "."),
            host=str(getattr(ns, "host", DEFAULT_GERRIT_HOST) or DEFAULT_GERRIT_HOST),
            timeout=int(getattr(ns, "timeout", DEFAULT_PING_TIMEOUT_SEC)),
            poll_interval=int(
                getattr(ns, "poll_interval", DEFAULT_PING_POLL_INTERVAL_SEC)
            ),
            required_score=REQUIRED_PRESUBMIT_VERIFIED_SCORE,
            no_wait=bool(getattr(ns, "no_wait", False)),
            message=str(getattr(ns, "message", "") or ""),
            sender_email=str(getattr(ns, "sender_email", "") or "").strip(),
        )
        return self._validate_ping_args(candidate)

    def _create_gerrit_client(
        self, ctx: ExecutionContext, host: str
    ) -> GerritClient:
        if self._gerrit_client_factory is not None:
            return self._gerrit_client_factory(ctx, host)
        return GerritClient(
            host=host, runner=ctx.process_runner, prefer_cli=True
        )

    def execute(
        self, args: PingArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[PingResult]:
        validated = self.validate(args, ctx)
        resolved_recipient = validated.to
        try:
            cl_id = parse_cl_identifier(
                validated.cl,
                cwd=validated.repo,
                runner=ctx.process_runner,
            )
        except ValueError as exc:
            raise UsageError(
                str(exc),
                remediation=(
                    "Pass a valid Gerrit CL number, 'ag/<id>', Change-Id, or run "
                    "from a Git repository whose HEAD commit contains a Change-Id footer."
                ),
            ) from exc

        effective_timeout = (
            0 if validated.no_wait else max(0, validated.timeout)
        )
        client = self._create_gerrit_client(ctx, validated.host)
        try:
            outcome = client.wait_for_presubmit_verified(
                cl_id,
                required_score=validated.required_score,
                timeout=timedelta(seconds=effective_timeout),
                poll_interval=timedelta(
                    seconds=max(1, validated.poll_interval)
                ),
                sleep_fn=self._sleep_fn,
                clock_fn=self._clock_fn,
            )
        except EmuCliError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"Failed to query Gerrit presubmit status for '{cl_id}' on '{validated.host}': {exc}",
                remediation=(
                    "Run 'gcert' to refresh SSO credentials and verify that the CL "
                    "exists on the target Gerrit host."
                ),
            ) from exc
        finally:
            client.close()

        detail = outcome.get("detail") or {}
        cl_num = int(
            detail.get("_number", 0) or (cl_id if cl_id.isdigit() else 0)
        )
        cl_label = str(cl_num or cl_id)
        change_id = str(detail.get("change_id") or cl_id)
        subject = str(detail.get("subject") or "").strip()
        patch_set = _extract_patchset_number(detail)
        gerrit_url = _format_gerrit_cl_url(validated.host, detail, cl_label)
        pv_score = int(outcome.get("presubmit_score", 0) or 0)
        polls = int(outcome.get("polls", 1) or 1)
        status = str(outcome.get("status") or "")

        if status == "presubmit_failed":
            raise ExecutionError(
                f"Cannot ping '{resolved_recipient}' for ag/{cl_label}: "
                f"Presubmit-Verified is {_format_score(pv_score)} on PS{patch_set}.",
                remediation=(
                    f"Inspect the failing presubmit targets with "
                    f"'emu-dev-cli triage presubmit {cl_label}', upload a fix, "
                    "and re-run 'ping-for-review'."
                ),
            )
        if status != "verified":
            raise RetryableError(
                f"Cannot ping '{resolved_recipient}' for ag/{cl_label}: "
                f"Presubmit-Verified is {_format_score(pv_score)} after {polls} poll(s) "
                f"(requires +{_format_score(validated.required_score).lstrip('+')}).",
                remediation=(
                    "Wait for Treehugger/AyeAye presubmit verification to finish "
                    "or increase '--timeout' and retry."
                ),
            )

        dm_result = ctx.chat_service.send_review_ping(
            cl_number=cl_label,
            recipient=resolved_recipient,
            subject=subject,
            gerrit_url=gerrit_url,
            patch_set=patch_set,
            presubmit_score=pv_score,
            custom_message=validated.message,
            sender_email=validated.sender_email,
        )
        ping_result = PingResult(
            cl_number=cl_num,
            change_id=change_id,
            subject=subject,
            patch_set=patch_set,
            presubmit_score=pv_score,
            recipient=dm_result.recipient,
            gerrit_url=gerrit_url,
            space_name=dm_result.space_name,
            message_name=dm_result.message_name,
            chat_url=dm_result.chat_url,
            message=dm_result.message,
            polls=polls,
        )
        summary = (
            f"Sent Google Chat review ping for ag/{cl_label} "
            f"(PS{patch_set}, Presubmit-Verified: {_format_score(pv_score)}) "
            f"to {dm_result.recipient}"
        )
        if dm_result.chat_url:
            summary = f"{summary} ({dm_result.chat_url})"
        return CommandResult.success(
            ping_result,
            action=self.name,
            summary=summary,
        )
