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

"""`emu-dev-cli gerrit stack watch`: keeps a CL stack moving until it merges.

Each poll reads the stack the way `gerrit stack status` does. When a parent CL
merges, the open CLs on top of it are rebased with `gerrit stack sync`. When a
CL's presubmit turns green, reviewers can be pinged over Google Chat.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import sys
import time
from typing import Any

from clients.chat_service import normalize_chat_dm_recipient
from commands.gerrit.stack.status import (
    GerritClientBuilder,
    StackSnapshot,
    StackStatusReport,
    build_gerrit_client,
    render_stack_report,
    summarize_stacks,
)
from commands.gerrit.stack.sync import validate_stack_selectors
from commands.gerrit.stack.watch_service import (
    DEFAULT_WATCH_POLL_INTERVAL_SEC,
    DEFAULT_WATCH_TIMEOUT_SEC,
    EVENT_PING_FAILED,
    EVENT_PING_SKIPPED,
    EVENT_POLL_FAILED,
    EVENT_REBASE_CONFLICT,
    EVENT_REBASE_DEFERRED,
    EVENT_REBASE_FAILED,
    EVENT_REBASE_NEEDED,
    OUTCOME_ATTENTION,
    OUTCOME_COMPLETE,
    OUTCOME_IN_PROGRESS,
    PingLedger,
    StackWatchArgs,
    StackWatchService,
    WatchEvent,
    WatchTick,
)
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import EmuCliError, ExecutionError, ExitCode, UsageError
from core.poller import poll_until
from lib.gerrit_api import DEFAULT_GERRIT_HOST

# Each poll sends a few Gerrit requests per CL, and presubmits take tens of minutes.
MIN_WATCH_POLL_INTERVAL_SEC = 10
# One flaky Gerrit request should not end a long watch; a persistent outage should.
MAX_CONSECUTIVE_POLL_FAILURES = 3
PING_LEDGER_FILENAME = "gerrit_stack_watch_pings.json"
MAX_RENDERED_EVENTS = 20
WATCH_ACTION = "gerrit stack watch"

OUTCOME_TIMEOUT = "timeout"
OUTCOME_ABORTED = "aborted"
OUTCOME_INTERRUPTED = "interrupted"

_OUTCOME_LABELS: Mapping[str, str] = {
    OUTCOME_COMPLETE: "Every CL is closed",
    OUTCOME_IN_PROGRESS: "Still in flight",
    OUTCOME_ATTENTION: "Needs attention",
    OUTCOME_TIMEOUT: "Timed out",
    OUTCOME_ABORTED: "Stopped by repeated poll failures",
    OUTCOME_INTERRUPTED: "Interrupted",
}
_OUTCOME_EXIT_CODES: Mapping[str, ExitCode] = {
    OUTCOME_COMPLETE: ExitCode.SUCCESS,
    OUTCOME_IN_PROGRESS: ExitCode.SUCCESS,
    OUTCOME_ATTENTION: ExitCode.FAILURE,
    OUTCOME_TIMEOUT: ExitCode.RETRY_OR_LOOP,
    OUTCOME_ABORTED: ExitCode.FAILURE,
    OUTCOME_INTERRUPTED: ExitCode.INTERRUPTED,
}
_OUTCOME_NEXT_STEPS: Mapping[str, str] = {
    OUTCOME_ATTENTION: (
        "Fix the items listed under 'Needs attention', then run "
        "`emu-dev-cli gerrit stack watch` again."
    ),
    OUTCOME_TIMEOUT: (
        "The stack is still open. Run `emu-dev-cli gerrit stack watch` again, or pass a "
        "larger --timeout."
    ),
    OUTCOME_ABORTED: "Run `gcert`, check that the CLs still exist, and start the watch again.",
    OUTCOME_INTERRUPTED: (
        "Run `emu-dev-cli gerrit stack watch` again to resume; pings already sent are not repeated."
    ),
}

# Events that describe a lasting state and come back on every poll; they are logged once.
_REPEATING_EVENTS = frozenset({
    EVENT_REBASE_NEEDED,
    EVENT_REBASE_DEFERRED,
    EVENT_REBASE_CONFLICT,
    EVENT_REBASE_FAILED,
    EVENT_PING_SKIPPED,
    EVENT_PING_FAILED,
})

ProgressWriter = Callable[[str], None]


def _print_progress(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class StackWatchReport(Mapping[str, Any]):
    """Structured output of `gerrit stack watch`."""

    host: str
    query: Mapping[str, Any]
    once: bool
    outcome: str
    iterations: int
    elapsed_sec: float
    settings: Mapping[str, Any]
    stacks: tuple[StackSnapshot, ...]
    events: tuple[WatchEvent, ...]
    attention: tuple[str, ...]
    note: str = ""
    next_step: str = ""

    @property
    def status_report(self) -> StackStatusReport:
        """The final stack state as a `gerrit stack status` report."""
        return StackStatusReport(host=self.host, query=self.query, stacks=self.stacks)

    @property
    def summary(self) -> str:
        """One line with the outcome and the verdict of every stack."""
        polls = "1 poll" if self.iterations == 1 else f"{self.iterations} polls"
        return f"{_OUTCOME_LABELS[self.outcome]} after {polls}: {summarize_stacks(self.stacks)}"

    def to_dict(self) -> dict[str, Any]:
        """Serializes the report into the `--json` payload."""
        return {
            "host": self.host,
            "query": dict(self.query),
            "mode": "once" if self.once else "poll",
            "outcome": self.outcome,
            "iterations": self.iterations,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "settings": dict(self.settings),
            "stacks": [stack.to_dict() for stack in self.stacks],
            "events": [event.to_dict() for event in self.events],
            "attention": list(self.attention),
            "note": self.note,
            "next_step": self.next_step,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class _WatchLoop:
    """Drives the polls, logs events, and rides out transient poll failures."""

    def __init__(
        self,
        service: StackWatchService,
        *,
        exit_on_attention: bool,
        progress: ProgressWriter | None,
    ) -> None:
        self._service = service
        self._exit_on_attention = exit_on_attention
        self._progress = progress
        self._seen: set[tuple[str, int, str]] = set()
        self._failures = 0
        self._last_good_poll = 0
        self.polls = 0
        self.events: list[WatchEvent] = []
        self.last_tick: WatchTick | None = None
        # (message, next step) once polls have failed MAX_CONSECUTIVE_POLL_FAILURES times.
        self.abort_reason: tuple[str, str] | None = None

    def poll(self) -> WatchTick:
        """Runs one poll; a failure after the first poll keeps the last known state."""
        try:
            tick = self._service.tick()
        except Exception as exc:
            if self.last_tick is None:
                raise
            tick = self._after_failure(self.last_tick, exc)
        else:
            self._failures = 0
            self._last_good_poll = tick.iteration
        self.polls += 1
        self._log(tick)
        self.last_tick = tick
        return tick

    def is_done(self, tick: WatchTick | None) -> bool:
        """Stops when every CL is closed, polls keep failing, or (opt-in) on attention."""
        return tick is not None and (
            self.abort_reason is not None
            or tick.complete
            or (self._exit_on_attention and bool(tick.attention))
        )

    def _after_failure(self, last: WatchTick, exc: Exception) -> WatchTick:
        self._failures += 1
        detail = exc.message if isinstance(exc, EmuCliError) else str(exc)
        iteration = self._service.iteration
        if self._failures >= MAX_CONSECUTIVE_POLL_FAILURES:
            cause_next = exc.remediation if isinstance(exc, EmuCliError) and exc.remediation else ""
            self.abort_reason = (
                f"Stopped after {self._failures} failed polls in a row: {detail}",
                " ".join(filter(None, (cause_next, _OUTCOME_NEXT_STEPS[OUTCOME_ABORTED]))),
            )
        message = (
            f"Poll {iteration} failed; showing the state from poll {self._last_good_poll}: {detail}"
        )
        event = WatchEvent(iteration, EVENT_POLL_FAILED, message)
        return replace(last, iteration=iteration, events=(event,))

    def _log(self, tick: WatchTick) -> None:
        fresh = [
            event
            for event in tick.events
            if event.kind not in _REPEATING_EVENTS or event.key not in self._seen
        ]
        self._seen.update(event.key for event in fresh if event.kind in _REPEATING_EVENTS)
        self.events.extend(fresh)
        if self._progress is None:
            return
        self._progress(f"[poll {tick.iteration}] {summarize_stacks(tick.stacks)}")
        for event in fresh:
            self._progress(f"  {event.kind}: {event.message}")


def render_watch_report(report: StackWatchReport) -> str:
    """Renders the final stack state, the event log, and what needs attention."""
    mode = (
        "single poll"
        if report.once
        else f"{report.iterations} poll(s) in {report.elapsed_sec:.0f}s"
    )
    lines = [f"Stack watch: {_OUTCOME_LABELS[report.outcome]} ({mode})", ""]
    lines.append(render_stack_report(report.status_report))
    if report.events:
        shown = report.events[-MAX_RENDERED_EVENTS:]
        header = "Events:" if len(shown) == len(report.events) else (
            f"Events (last {len(shown)} of {len(report.events)}; see --json for all):"
        )
        lines.extend(["", header])
        lines.extend(f"  [poll {e.iteration}] {e.kind}: {e.message}" for e in shown)
    if report.attention:
        lines.extend(["", "Needs attention:"])
        lines.extend(f"  - {item}" for item in report.attention)
    if report.note:
        lines.extend(["", report.note])
    if report.next_step:
        lines.extend(["", f"Next step: {report.next_step}"])
    return "\n".join(lines)


def _normalize_ping_recipients(values: Iterable[str]) -> tuple[str, ...]:
    """Splits comma-separated --ping values and normalizes each to a human @google.com address."""
    recipients: list[str] = []
    for name in (part.strip() for value in values for part in value.split(",")):
        if not name:
            continue
        try:
            recipients.append(normalize_chat_dm_recipient(name))
        except UsageError as exc:
            raise UsageError(
                f"Invalid --ping value '{name}': {exc.message}",
                remediation=(
                    "Pass the LDAP or @google.com address of a human, for example --ping jansene."
                ),
            ) from exc
    return tuple(dict.fromkeys(recipients))


class StackWatchCommand(Command[StackWatchArgs, StackWatchReport]):
    """Polls a CL stack, rebases it after parent merges, and pings reviewers on green presubmits."""

    name = "watch"
    help_text = (
        "Watch a CL stack: rebase it after parents merge and ping reviewers when "
        "presubmit turns green"
    )

    def __init__(
        self,
        *,
        gerrit_client_factory: GerritClientBuilder | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
        progress_fn: ProgressWriter | None = None,
    ) -> None:
        self._gerrit_client_factory = gerrit_client_factory
        self._sleep_fn = sleep_fn
        self._clock_fn = clock_fn
        self._progress_fn = progress_fn or _print_progress

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, ag/<id>, Change-Id, Gerrit URL, local branch, or HEAD (default: HEAD)",
        )
        parser.add_argument(
            "--topic", default="", help="Watch every relation chain in this Gerrit topic"
        )
        parser.add_argument(
            "--branch", default="", help="Local Git branch whose tip commit identifies the stack"
        )
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git checkout used to resolve HEAD and to rebase the stack (default: .)",
        )
        parser.add_argument(
            "--host",
            default=argparse.SUPPRESS,
            help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
        )
        parser.add_argument(
            "--remote",
            default="",
            help="Git remote to fetch from and push to when rebasing (default: auto-detected)",
        )
        parser.add_argument(
            "--once", action="store_true", help="Poll once and exit, for CI jobs and scripts"
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=DEFAULT_WATCH_POLL_INTERVAL_SEC,
            help=(
                f"Seconds between polls (default: {DEFAULT_WATCH_POLL_INTERVAL_SEC}, "
                f"minimum: {MIN_WATCH_POLL_INTERVAL_SEC})"
            ),
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_WATCH_TIMEOUT_SEC,
            help=(
                f"Seconds to keep watching before giving up (default: {DEFAULT_WATCH_TIMEOUT_SEC})"
            ),
        )
        parser.add_argument(
            "--no-rebase",
            dest="rebase",
            action="store_false",
            help="Only report CLs left behind by a merged parent; do not run `gerrit stack sync`",
        )
        parser.add_argument(
            "--presubmit",
            action="store_true",
            help="Vote Presubmit-Ready +1 on the patch sets pushed after a rebase",
        )
        parser.add_argument(
            "--ping",
            action="append",
            default=[],
            metavar="LDAP",
            help=(
                "Ping this reviewer over Google Chat when a CL turns green "
                "(repeatable or comma-separated)"
            ),
        )
        parser.add_argument(
            "--ping-reviewers",
            action="store_true",
            help=(
                "Ping the human reviewers whose vote Review-Enforcement still needs "
                "when a CL turns green"
            ),
        )
        parser.add_argument(
            "--state-file",
            default="",
            help=(
                "File that records sent pings so reruns skip them "
                f"(default: <cache dir>/{PING_LEDGER_FILENAME})"
            ),
        )
        parser.add_argument(
            "--exit-on-attention",
            action="store_true",
            help="Stop at the first failed presubmit, merge conflict, or failed rebase or ping",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON",
        )

    @staticmethod
    def _from_namespace(ns: argparse.Namespace) -> StackWatchArgs:
        return StackWatchArgs(
            cl=str(getattr(ns, "cl", "HEAD") or "HEAD").strip(),
            topic=str(getattr(ns, "topic", "") or "").strip(),
            branch=str(getattr(ns, "branch", "") or "").strip(),
            repo=str(getattr(ns, "repo", ".") or "."),
            host=str(getattr(ns, "host", DEFAULT_GERRIT_HOST) or DEFAULT_GERRIT_HOST),
            remote=str(getattr(ns, "remote", "") or "").strip(),
            once=bool(getattr(ns, "once", False)),
            poll_interval=int(getattr(ns, "poll_interval", DEFAULT_WATCH_POLL_INTERVAL_SEC)),
            timeout=int(getattr(ns, "timeout", DEFAULT_WATCH_TIMEOUT_SEC)),
            rebase=bool(getattr(ns, "rebase", True)),
            presubmit=bool(getattr(ns, "presubmit", False)),
            ping=tuple(getattr(ns, "ping", None) or ()),
            ping_reviewers=bool(getattr(ns, "ping_reviewers", False)),
            state_file=str(getattr(ns, "state_file", "") or ""),
            exit_on_attention=bool(getattr(ns, "exit_on_attention", False)),
        )

    def validate(
        self, args: StackWatchArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> StackWatchArgs:
        candidate = args if isinstance(args, StackWatchArgs) else self._from_namespace(args)
        validate_stack_selectors(candidate.cl, candidate.topic, candidate.branch)
        if candidate.poll_interval < MIN_WATCH_POLL_INTERVAL_SEC:
            raise UsageError(
                f"--poll-interval {candidate.poll_interval} is too short; the minimum is "
                f"{MIN_WATCH_POLL_INTERVAL_SEC} seconds.",
                remediation=(
                    f"Pass --poll-interval {MIN_WATCH_POLL_INTERVAL_SEC} or more "
                    f"(default: {DEFAULT_WATCH_POLL_INTERVAL_SEC})."
                ),
            )
        if candidate.timeout < 0:
            raise UsageError(
                f"--timeout {candidate.timeout} is negative.",
                remediation="Pass 0 or more seconds, or use --once to poll a single time.",
            )
        if candidate.rebase and not ctx.git_service.is_git_repo(cwd=candidate.repo):
            raise UsageError(
                f"'{candidate.repo}' is not inside a Git work tree, so the watch cannot "
                "rebase the stack there.",
                remediation=(
                    "Run from your checkout, pass --repo <dir>, or pass --no-rebase "
                    "to only monitor."
                ),
            )
        return replace(candidate, ping=_normalize_ping_recipients(candidate.ping))

    def execute(
        self, args: StackWatchArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[StackWatchReport]:
        validated = self.validate(args, ctx)
        state_file = (
            Path(validated.state_file)
            if validated.state_file
            else ctx.config.cache_dir / PING_LEDGER_FILENAME
        )
        client = build_gerrit_client(ctx, validated.host, self._gerrit_client_factory)
        service = StackWatchService(client, validated, ctx, PingLedger.load(state_file))
        show_progress = not validated.once and not ctx.config.json_mode
        loop = _WatchLoop(
            service,
            exit_on_attention=validated.exit_on_attention,
            progress=self._progress_fn if show_progress else None,
        )
        started = self._clock_fn()
        interrupted = False
        succeeded = False
        try:
            succeeded = poll_until(
                loop.poll,
                timeout_sec=0 if validated.once else validated.timeout,
                interval_sec=validated.poll_interval,
                predicate=loop.is_done,
                sleep_fn=self._sleep_fn,
                clock_fn=self._clock_fn,
            ).succeeded
        except KeyboardInterrupt:
            if loop.last_tick is None:
                raise
            interrupted = True
        except EmuCliError:
            raise
        except Exception as exc:
            target = validated.topic or validated.branch or validated.cl
            raise ExecutionError(
                f"Failed to watch the stack for '{target}' on '{validated.host}': {exc}",
                remediation="Run `gcert` to refresh credentials and check that the CLs exist.",
            ) from exc
        finally:
            client.close()
        report = self._report(
            validated,
            service,
            loop,
            outcome=self._final_outcome(validated, loop, succeeded, interrupted),
            elapsed_sec=self._clock_fn() - started,
            state_file=state_file,
        )
        return self._result(report, ctx)

    @staticmethod
    def _final_outcome(
        args: StackWatchArgs, loop: _WatchLoop, succeeded: bool, interrupted: bool
    ) -> str:
        last = loop.last_tick
        if interrupted:
            return OUTCOME_INTERRUPTED
        if loop.abort_reason is not None:
            return OUTCOME_ABORTED
        if succeeded or args.once:
            return last.outcome
        return OUTCOME_ATTENTION if last.attention else OUTCOME_TIMEOUT

    @staticmethod
    def _report(
        args: StackWatchArgs,
        service: StackWatchService,
        loop: _WatchLoop,
        *,
        outcome: str,
        elapsed_sec: float,
        state_file: Path,
    ) -> StackWatchReport:
        last = loop.last_tick
        note, next_step = "", _OUTCOME_NEXT_STEPS.get(outcome, "")
        if loop.abort_reason is not None and outcome == OUTCOME_ABORTED:
            note, next_step = loop.abort_reason
        return StackWatchReport(
            host=args.host,
            query=service.query,
            once=args.once,
            outcome=outcome,
            iterations=loop.polls,
            elapsed_sec=elapsed_sec,
            settings=args.settings(state_file),
            stacks=last.stacks,
            events=tuple(loop.events),
            attention=last.attention,
            note=note,
            next_step=next_step,
        )

    @staticmethod
    def _result(report: StackWatchReport, ctx: ExecutionContext) -> CommandResult[StackWatchReport]:
        table = None if ctx.config.json_mode else render_watch_report(report)
        exit_code = _OUTCOME_EXIT_CODES[report.outcome]
        if exit_code == ExitCode.SUCCESS:
            return CommandResult.success(
                report, action=WATCH_ACTION, summary=report.summary, markdown_table=table
            )
        return CommandResult.failure(
            report.summary,
            exit_code=int(exit_code),
            data=report,
            action=WATCH_ACTION,
            markdown_table=table,
            metadata={"remediation": report.next_step},
        )
