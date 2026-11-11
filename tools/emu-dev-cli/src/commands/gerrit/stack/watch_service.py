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

"""One poll of `gerrit stack watch`: read the stack, rebase after merges, ping reviewers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any

from clients.chat_service import ReviewPingItem, normalize_chat_dm_recipient
from commands.gerrit.ping_for_review import REQUIRED_PRESUBMIT_VERIFIED_SCORE
from commands.gerrit.stack.review_enforcement import format_ldap
from commands.gerrit.stack.status import (
    BLOCKER_CODE_REVIEW_VETO,
    BLOCKER_MERGE_CONFLICT,
    BLOCKER_PARENT_ABANDONED,
    BLOCKER_PRESUBMIT_FAILED,
    CHANGE_MERGED,
    CHANGE_OPEN,
    MERGEABLE_CONFLICT,
    MERGEABLE_FALSE,
    PRESUBMIT_FAILED,
    PRESUBMIT_NOT_STARTED,
    ChainLink,
    ChangeStatus,
    CollectedChain,
    StackSnapshot,
    reviewers_to_ask,
    short_sha,
)
from commands.gerrit.stack.sync import (
    SYNC_ACTION_CONFLICT,
    SYNC_ACTION_REBASED,
    StackSyncArgs,
    StackSyncService,
    StackSyncSnapshot,
    resolve_stack_seeds,
)
from core.context import ExecutionContext
from core.errors import EmuCliError, UsageError
from core.events import utc_now_iso
from lib.gerrit_api import DEFAULT_GERRIT_HOST, GerritClient
from lib.path_utils import atomic_write_text, read_json_dict

DEFAULT_WATCH_POLL_INTERVAL_SEC = 60
DEFAULT_WATCH_TIMEOUT_SEC = 7200
MAX_PING_LEDGER_ENTRIES = 500

# Outcomes of a single poll; the command adds timeout, aborted, and interrupted.
OUTCOME_COMPLETE = "complete"
OUTCOME_IN_PROGRESS = "in_progress"
OUTCOME_ATTENTION = "attention"

# Blocker kinds from `gerrit stack status` that waiting will not fix.
ATTENTION_BLOCKERS = frozenset({
    BLOCKER_PARENT_ABANDONED,
    BLOCKER_MERGE_CONFLICT,
    BLOCKER_CODE_REVIEW_VETO,
    BLOCKER_PRESUBMIT_FAILED,
})
# Presubmit states worth replacing with a rebase; a passed or running run is kept.
_REBASE_PRESUBMIT_STATES = frozenset({PRESUBMIT_FAILED, PRESUBMIT_NOT_STARTED})

EVENT_STATUS = "status"
EVENT_NEW_PATCH_SET = "new_patch_set"
EVENT_PRESUBMIT = "presubmit"
EVENT_MERGEABLE = "mergeable"
EVENT_REBASE_NEEDED = "rebase_needed"
EVENT_REBASE_DEFERRED = "rebase_deferred"
EVENT_REBASED = "rebased"
EVENT_UP_TO_DATE = "up_to_date"
EVENT_REBASE_CONFLICT = "rebase_conflict"
EVENT_REBASE_FAILED = "rebase_failed"
EVENT_PINGED = "pinged"
EVENT_PING_SKIPPED = "ping_skipped"
EVENT_PING_FAILED = "ping_failed"
EVENT_POLL_FAILED = "poll_failed"


def _cl_list(numbers: Iterable[int]) -> str:
    return ", ".join(f"ag/{number}" for number in numbers)


@dataclass(frozen=True)
class WatchEvent:
    """Something the watch noticed or did during one poll."""

    iteration: int
    kind: str
    message: str
    number: int = 0
    attention: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, str]:
        """Identity used to log a repeating event only once."""
        return (self.kind, self.number, self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the event into a JSON-compatible dictionary."""
        return {
            "iteration": self.iteration,
            "kind": self.kind,
            "number": self.number or None,
            "message": self.message,
            "attention": self.attention,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class WatchTick:
    """The stack state seen and the actions taken in one poll."""

    iteration: int
    stacks: tuple[StackSnapshot, ...]
    events: tuple[WatchEvent, ...]
    attention: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """True once every CL in every watched stack is merged or abandoned."""
        return all(not stack.open_changes for stack in self.stacks)

    @property
    def outcome(self) -> str:
        """`complete`, `attention`, or `in_progress` for this poll."""
        if self.complete:
            return OUTCOME_COMPLETE
        return OUTCOME_ATTENTION if self.attention else OUTCOME_IN_PROGRESS


@dataclass(frozen=True)
class StackWatchArgs:
    """Validated arguments for `StackWatchCommand`."""

    cl: str = "HEAD"
    topic: str = ""
    branch: str = ""
    repo: str = "."
    host: str = DEFAULT_GERRIT_HOST
    remote: str = ""
    once: bool = False
    poll_interval: int = DEFAULT_WATCH_POLL_INTERVAL_SEC
    timeout: int = DEFAULT_WATCH_TIMEOUT_SEC
    rebase: bool = True
    presubmit: bool = False
    ping: tuple[str, ...] = ()
    ping_reviewers: bool = False
    state_file: str = ""
    exit_on_attention: bool = False

    @property
    def pings_enabled(self) -> bool:
        """True when green CLs should trigger review pings."""
        return bool(self.ping) or self.ping_reviewers

    def settings(self, state_file: Path) -> dict[str, Any]:
        """The options that shaped this watch, echoed in the report."""
        return {
            "poll_interval_sec": self.poll_interval,
            "timeout_sec": 0 if self.once else self.timeout,
            "rebase": self.rebase,
            "presubmit": self.presubmit,
            "ping": list(self.ping),
            "ping_reviewers": self.ping_reviewers,
            "state_file": str(state_file),
            "exit_on_attention": self.exit_on_attention,
        }


class PingLedger:
    """Remembers which reviewers were pinged about which CLs, so reruns stay quiet."""

    def __init__(self, path: Path, entries: Mapping[str, str] | None = None) -> None:
        self.path = path
        self._entries = dict(entries or {})

    @classmethod
    def load(cls, path: Path) -> PingLedger:
        """Reads the ledger; a missing or unreadable file starts an empty one."""
        pings = (read_json_dict(path) or {}).get("pings")
        if not isinstance(pings, Mapping):
            return cls(path)
        return cls(path, {str(key): str(when) for key, when in pings.items()})

    @staticmethod
    def key(host: str, number: int, recipient: str) -> str:
        """Identifies one ping: a reviewer told about one CL."""
        return f"{host.rstrip('/')}|{number}|{recipient}"

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def record(self, keys: Sequence[str], when: str) -> None:
        """Adds `keys`, keeps the newest entries, and saves the ledger (raises OSError)."""
        for key in keys:
            self._entries[key] = when
        newest = sorted(self._entries.items(), key=lambda item: item[1], reverse=True)
        self._entries = dict(newest[:MAX_PING_LEDGER_ENTRIES])
        payload = json.dumps({"pings": self._entries}, indent=2, sort_keys=True, ensure_ascii=False)
        atomic_write_text(self.path, payload + "\n")


def _link_status(chain: CollectedChain, link: ChainLink) -> str:
    return str((chain.details.get(link.number) or {}).get("status") or link.status)


def children_of_merged_parents(chain: CollectedChain) -> list[tuple[ChainLink, ChainLink]]:
    """Returns `(child, parent)` for every open CL still based on a merged CL's patch set."""
    by_commit = {link.commit: link for link in chain.links if link.commit}
    pairs: list[tuple[ChainLink, ChainLink]] = []
    for link in chain.links:
        parent = by_commit.get(link.parent_commit)
        if parent is None or _link_status(chain, link) != CHANGE_OPEN:
            continue
        if _link_status(chain, parent) == CHANGE_MERGED:
            pairs.append((link, parent))
    return pairs


def _presubmit_state(change: ChangeStatus) -> str:
    return change.presubmit.status if change.presubmit is not None else ""


@dataclass(frozen=True)
class RebasePlan:
    """The open CLs left on a merged parent's patch set, and the CLs a rebase would push."""

    children: tuple[ChangeStatus, ...]
    parents: tuple[ChangeStatus, ...]
    tip: ChangeStatus
    owners: Mapping[int, str]
    command: str

    @property
    def keys(self) -> frozenset[tuple[int, int]]:
        """(CL, patch set) of every child, to remember what was already tried."""
        return frozenset((child.number, child.patch_set) for child in self.children)

    @property
    def merged(self) -> str:
        return _cl_list(parent.number for parent in self.parents)

    @property
    def targets(self) -> str:
        return _cl_list(child.number for child in self.children)

    @property
    def first_child(self) -> int:
        return self.children[0].number

    def keeps_presubmits(self) -> bool:
        """True when every child still merges and has a passed or running presubmit to keep."""
        return all(
            child.mergeable not in (MERGEABLE_CONFLICT, MERGEABLE_FALSE)
            and _presubmit_state(child) not in _REBASE_PRESUBMIT_STATES
            for child in self.children
        )


def _owner_email(chain: CollectedChain, number: int) -> str:
    owner = (chain.details.get(number) or {}).get("owner") or {}
    return str(owner.get("email") or "")


def plan_rebase(chain: CollectedChain, snapshot: StackSnapshot, repo: str) -> RebasePlan | None:
    """Plans the rebase of the CLs a merged parent left behind, or returns None."""
    pairs = children_of_merged_parents(chain)
    if not pairs:
        return None
    by_number = {change.number: change for change in snapshot.changes}
    parent_numbers = sorted({parent.number for _, parent in pairs})
    open_changes = snapshot.open_changes
    owners = {change.number: _owner_email(chain, change.number) for change in open_changes}
    tip = open_changes[-1]
    repo_flag = f" --repo {repo}" if repo != "." else ""
    return RebasePlan(
        children=tuple(by_number[child.number] for child, _ in pairs),
        parents=tuple(by_number[number] for number in parent_numbers),
        tip=tip,
        owners=owners,
        command=f"emu-dev-cli gerrit stack sync ag/{tip.number}{repo_flag}",
    )


def change_transitions(
    iteration: int, before: ChangeStatus, after: ChangeStatus
) -> list[WatchEvent]:
    """Describes how one CL changed since the previous poll."""
    cl = f"ag/{after.number}"
    if after.status != before.status:
        message = f"{cl} is now {after.status.lower()}"
        return [WatchEvent(iteration, EVENT_STATUS, message, after.number)]
    events: list[WatchEvent] = []
    if after.patch_set != before.patch_set:
        message = f"{cl} has a new patch set: PS{before.patch_set} -> PS{after.patch_set}"
        events.append(WatchEvent(iteration, EVENT_NEW_PATCH_SET, message, after.number))
    if _presubmit_state(after) != _presubmit_state(before):
        message = (
            f"{cl} PS{after.patch_set} presubmit: "
            f"{_presubmit_state(before)} -> {_presubmit_state(after)}"
        )
        events.append(WatchEvent(iteration, EVENT_PRESUBMIT, message, after.number))
    if after.mergeable != before.mergeable:
        message = f"{cl} mergeable: {before.mergeable} -> {after.mergeable}"
        events.append(WatchEvent(iteration, EVENT_MERGEABLE, message, after.number))
    return events


def snapshot_attention(stacks: Sequence[StackSnapshot]) -> list[str]:
    """Problems on open CLs that waiting will not fix, worded like `gerrit stack status`."""
    return [
        f"ag/{change.number}: {blocker.reason} — {blocker.action}"
        for stack in stacks
        for change in stack.open_changes
        for blocker in change.blockers
        if blocker.kind in ATTENTION_BLOCKERS
    ]


def ready_for_review_ping(change: ChangeStatus) -> bool:
    """True when the current patch set has Presubmit-Verified +2 and is waiting on reviewers."""
    review = change.review
    if change.work_in_progress or change.unresolved_comments or review is None:
        return False
    if change.presubmit is None or change.presubmit.verified < REQUIRED_PRESUBMIT_VERIFIED_SCORE:
        return False
    if review.blocking_voters:
        return False
    return not (review.satisfied and review.code_review_satisfied)


def _ping_item(change: ChangeStatus) -> ReviewPingItem:
    score = change.presubmit.verified if change.presubmit is not None else 0
    return ReviewPingItem(
        cl_number=str(change.number),
        subject=change.subject,
        gerrit_url=change.url,
        patch_set=change.patch_set,
        presubmit_score=score,
    )


def _rebase_event(
    iteration: int, result: StackSyncSnapshot, plan: RebasePlan, presubmit: bool
) -> WatchEvent:
    """Turns a `gerrit stack sync` result into one watch event."""
    details = result.to_dict()
    conflict = next((c for c in result.changes if c.action == SYNC_ACTION_CONFLICT), None)
    if conflict is not None:
        files = ", ".join(conflict.conflicts)
        where = f" with conflicts in {files}" if files else ""
        message = (
            f"Rebasing ag/{conflict.number} onto {result.target_ref} after {plan.merged} merged "
            f"failed{where}. Next: rebase it locally (`emu-dev-cli gerrit stack sync --dry-run` "
            "shows the overlap) and upload a new patch set."
        )
        return WatchEvent(
            iteration, EVENT_REBASE_CONFLICT, message, conflict.number,
            attention=True, details=details,
        )
    if not result.rebased_count:
        message = (
            f"{plan.merged} merged; {plan.targets} is already up to date with {result.target_ref}"
        )
        return WatchEvent(iteration, EVENT_UP_TO_DATE, message, plan.first_child, details=details)
    rerun = (
        "and queued presubmit" if presubmit else "; vote Presubmit-Ready +1 to rerun presubmit"
    )
    message = (
        f"{plan.merged} merged; rebased {result.rebased_count} CL(s) onto {result.target_ref} "
        f"({short_sha(result.target_commit)}), pushed them with notify=OWNER {rerun}"
    )
    return WatchEvent(iteration, EVENT_REBASED, message, plan.first_child, details=details)


class StackWatchService:
    """Runs one poll at a time: read the stack, rebase after merges, and ping reviewers."""

    def __init__(
        self,
        client: GerritClient,
        args: StackWatchArgs,
        ctx: ExecutionContext,
        ledger: PingLedger,
    ) -> None:
        self._args = args
        self._ctx = ctx
        self._ledger = ledger
        self._sync = StackSyncService(client, args.host, ctx)
        self._query: dict[str, Any] = {}
        # The CL number that later polls re-read; a Change-Id could match cherry-picks.
        self._refresh_cl = ""
        self._previous: dict[int, ChangeStatus] = {}
        # (CL, patch set) pairs already rebased or found up to date during this run.
        self._attempted_rebases: set[tuple[int, int]] = set()
        # Conflicts stay on the attention list until the CL gets a new patch set.
        self._conflicts: dict[tuple[int, int], WatchEvent] = {}
        # (CL, patch set) pairs replaced by a pushed rebase; their presubmit result is stale.
        self._superseded: set[tuple[int, int]] = set()
        self._iteration = 0

    @property
    def query(self) -> dict[str, Any]:
        """How the stack was selected, as in `gerrit stack status --json`."""
        return dict(self._query)

    @property
    def iteration(self) -> int:
        """Number of polls started so far."""
        return self._iteration

    def tick(self) -> WatchTick:
        """Runs one poll and returns what it saw and did."""
        self._iteration += 1
        status_service = self._sync.status_service
        stacks: list[StackSnapshot] = []
        events: list[WatchEvent] = []
        for chain in status_service.collect_chains(self._seeds()):
            snapshot = status_service.snapshot(chain)
            stacks.append(snapshot)
            events.extend(self._transitions(snapshot))
            events.extend(self._rebase_after_merges(chain, snapshot))
            events.extend(self._ping_green_changes(snapshot))
        self._previous = {change.number: change for stack in stacks for change in stack.changes}
        attention = [*snapshot_attention(stacks), *(e.message for e in events if e.attention)]
        return WatchTick(
            self._iteration, tuple(stacks), tuple(events), tuple(dict.fromkeys(attention))
        )

    def _event(self, kind: str, message: str, number: int = 0, **kwargs: Any) -> WatchEvent:
        return WatchEvent(self._iteration, kind, message, number, **kwargs)

    def _seeds(self) -> list[dict[str, Any]]:
        """Resolves the selector on the first poll, then re-reads the same CLs from Gerrit."""
        args = self._args
        service = self._sync.status_service
        if self._query and self._query["mode"] == "topic":
            _, seeds, _ = resolve_stack_seeds(service, self._ctx, topic=args.topic, host=args.host)
            return seeds
        if self._query:
            _, seeds, _ = resolve_stack_seeds(
                service, self._ctx, cl=self._refresh_cl, repo=args.repo, host=args.host
            )
            return seeds
        query, seeds, _ = resolve_stack_seeds(
            service,
            self._ctx,
            cl=args.cl,
            topic=args.topic,
            branch=args.branch,
            repo=args.repo,
            host=args.host,
        )
        self._query = {**query, "host": args.host}
        self._refresh_cl = str(seeds[0].get("_number") or query.get("resolved") or "")
        return seeds

    def _transitions(self, snapshot: StackSnapshot) -> list[WatchEvent]:
        events: list[WatchEvent] = []
        for change in snapshot.changes:
            before = self._previous.get(change.number)
            if before is not None:
                events.extend(change_transitions(self._iteration, before, change))
        return events

    def _rebase_after_merges(
        self, chain: CollectedChain, snapshot: StackSnapshot
    ) -> list[WatchEvent]:
        """Rebases the open CLs whose parent merged, the way `gerrit stack sync` does."""
        plan = plan_rebase(chain, snapshot, self._args.repo)
        if plan is None:
            return []
        conflicts = [self._conflicts[key] for key in plan.keys if key in self._conflicts]
        if conflicts:
            return [replace(conflicts[0], iteration=self._iteration)]
        if plan.keys <= self._attempted_rebases:
            return []
        held = self._hold_rebase(plan)
        if held is not None:
            return [held]
        return [self._run_rebase(chain, snapshot, plan)]

    def _hold_rebase(self, plan: RebasePlan) -> WatchEvent | None:
        """Explains why the watch leaves the CLs on their old base, or returns None to rebase."""
        if plan.keeps_presubmits():
            states = " and ".join(sorted({_presubmit_state(child) for child in plan.children}))
            message = (
                f"{plan.merged} merged, but {plan.targets} still merges cleanly and its presubmit "
                f"is {states}, so the watch keeps that run; run `{plan.command}` to rebase anyway"
            )
            return self._event(EVENT_REBASE_DEFERRED, message, plan.first_child)
        if not self._args.rebase:
            message = f"{plan.merged} merged; run `{plan.command}` to rebase {plan.targets}"
            return self._event(EVENT_REBASE_NEEDED, message, plan.first_child)
        blocker = self._checkout_blocker(plan)
        if blocker:
            message = (
                f"{plan.merged} merged, but the watch did not rebase {plan.targets}: {blocker}. "
                f"Run `{plan.command}` from the stack's branch to rebase it yourself"
            )
            return self._event(EVENT_REBASE_NEEDED, message, plan.first_child)
        return None

    def _checkout_blocker(self, plan: RebasePlan) -> str:
        """Explains why rebasing in the local checkout is unsafe, or returns "" when it is safe."""
        git = self._ctx.git_service
        repo = self._args.repo
        branch = git.current_branch(cwd=repo)
        if not branch:
            return f"HEAD in '{repo}' is detached; check out the stack's branch"
        if not git.is_clean(cwd=repo, untracked=False):
            return f"'{repo}' has uncommitted changes"
        head = git.head_sha(cwd=repo)
        tip = plan.tip
        if head != tip.current_revision:
            return (
                f"branch '{branch}' in '{repo}' is at {short_sha(head)}, not at the top CL "
                f"ag/{tip.number} PS{tip.patch_set} ({short_sha(tip.current_revision)})"
            )
        user = git.get_config("user.email", cwd=repo).strip().lower()
        if not user:
            return (
                f"`git config user.email` is not set in '{repo}', so the watch cannot tell "
                "which CLs are yours"
            )
        others = [
            f"ag/{number} ({format_ldap(owner)})"
            for number, owner in plan.owners.items()
            if owner.lower() != user
        ]
        if others:
            return (
                f"{', '.join(others)} belong to someone else; the watch only pushes CLs "
                f"owned by {user}"
            )
        return ""

    def _run_rebase(
        self, chain: CollectedChain, snapshot: StackSnapshot, plan: RebasePlan
    ) -> WatchEvent:
        """Runs `gerrit stack sync` on the chain and puts the branch back if it fails."""
        waiting = self._wait_for_target(snapshot, plan)
        if waiting is not None:
            return waiting
        git = self._ctx.git_service
        repo = self._args.repo
        branch = git.current_branch(cwd=repo)
        head = git.head_sha(cwd=repo)
        self._attempted_rebases |= plan.keys
        try:
            (result,) = self._sync.sync_chains([chain], self._sync_args(), local_branch=branch)
        except EmuCliError as exc:
            # Forget the attempt so a later poll can retry once the cause is fixed.
            self._attempted_rebases -= plan.keys
            self._restore_checkout(branch, head)
            message = (
                f"Rebasing {plan.targets} after {plan.merged} merged failed: {exc.message} "
                "Next: run `gcert`, check that you can push to Gerrit, and the watch retries "
                "on the next poll."
            )
            return self._event(EVENT_REBASE_FAILED, message, plan.first_child, attention=True)
        except BaseException:
            self._restore_checkout(branch, head)
            raise
        event = _rebase_event(self._iteration, result, plan, self._args.presubmit)
        if event.kind == EVENT_REBASE_CONFLICT:
            self._conflicts.update(dict.fromkeys(plan.keys, event))
        self._superseded.update(
            (change.number, change.patch_set)
            for change in result.changes
            if change.action == SYNC_ACTION_REBASED and change.pushed
        )
        return event

    def _wait_for_target(self, snapshot: StackSnapshot, plan: RebasePlan) -> WatchEvent | None:
        """Fetches the target branch and waits until it contains every merged parent."""
        git = self._ctx.git_service
        repo = self._args.repo
        remote = self._sync.resolve_remote(repo, self._args.remote)
        fetched = git.fetch(remote, snapshot.branch, cwd=repo)
        if fetched.returncode != 0:
            output = fetched.stderr.strip() or fetched.stdout.strip()
            detail = output or f"exit code {fetched.returncode}"
            message = (
                f"Could not fetch {snapshot.branch} from '{remote}' to rebase {plan.targets}: "
                f"{detail}. Next: run `gcert` and check that '{remote}' is reachable; the watch "
                "retries on the next poll."
            )
            return self._event(EVENT_REBASE_FAILED, message, plan.first_child, attention=True)
        target = f"{remote}/{snapshot.branch}"
        missing = [
            parent.number
            for parent in plan.parents
            if not git.is_ancestor(parent.current_revision, target, cwd=repo)
        ]
        if not missing:
            return None
        message = (
            f"{target} does not contain {_cl_list(missing)} yet, so the watch waits before "
            f"rebasing {plan.targets}"
        )
        return self._event(EVENT_REBASE_DEFERRED, message, plan.first_child)

    def _restore_checkout(self, branch: str, head: str) -> None:
        """Puts the branch back where it was, keeping any edits in the working tree."""
        git = self._ctx.git_service
        git.abort_rebase(cwd=self._args.repo)
        git.checkout_branch(branch, head, cwd=self._args.repo, force=True, check=False)

    def _sync_args(self) -> StackSyncArgs:
        args = self._args
        return StackSyncArgs(
            repo=args.repo, host=args.host, remote=args.remote, presubmit=args.presubmit
        )

    def _ping_green_changes(self, snapshot: StackSnapshot) -> list[WatchEvent]:
        """Pings each reviewer once per green CL, with one Chat message per reviewer."""
        if not self._args.pings_enabled:
            return []
        events: list[WatchEvent] = []
        pending: dict[str, list[ChangeStatus]] = {}
        for change in snapshot.open_changes:
            superseded = (change.number, change.patch_set) in self._superseded
            if superseded or not ready_for_review_ping(change):
                continue
            recipients, skipped = self._ping_recipients(change)
            events.extend(skipped)
            for recipient in recipients:
                pending.setdefault(recipient, []).append(change)
        events.extend(self._send_ping(recipient, changes) for recipient, changes in pending.items())
        return events

    def _ping_recipients(self, change: ChangeStatus) -> tuple[list[str], list[WatchEvent]]:
        """Returns who still needs a ping about `change`, and events for who cannot get one."""
        candidates = list(self._args.ping)
        if self._args.ping_reviewers and change.review is not None:
            candidates.extend(reviewers_to_ask(change.review)[1])
        recipients: list[str] = []
        skipped: list[WatchEvent] = []
        for email in dict.fromkeys(candidates):
            try:
                recipient = normalize_chat_dm_recipient(email)
            except UsageError as exc:
                message = f"Not pinging {email} about ag/{change.number}: {exc.message}"
                skipped.append(self._event(EVENT_PING_SKIPPED, message, change.number))
                continue
            if self._ping_key(change.number, recipient) not in self._ledger:
                recipients.append(recipient)
        return recipients, skipped

    def _ping_key(self, number: int, recipient: str) -> str:
        return PingLedger.key(self._args.host, number, recipient)

    def _send_ping(self, recipient: str, changes: Sequence[ChangeStatus]) -> WatchEvent:
        numbers = [change.number for change in changes]
        listed = _cl_list(numbers)
        try:
            result = self._ctx.chat_service.send_stack_review_ping(
                recipient=recipient, items=[_ping_item(change) for change in changes]
            )
        except EmuCliError as exc:
            message = (
                f"Could not ping {recipient} about {listed}: {exc.message} Next: run `gcert` and "
                "check that the gchat CLI works; the watch retries on the next poll."
            )
            return self._event(EVENT_PING_FAILED, message, numbers[0], attention=True)
        details = {**result.to_dict(), "cls": numbers}
        message = f"Pinged {format_ldap(result.recipient)} to review {listed}"
        try:
            self._ledger.record([self._ping_key(n, recipient) for n in numbers], utc_now_iso())
        except OSError as exc:
            message = (
                f"{message}, but could not save {self._ledger.path} ({exc}). Next: fix the file "
                "or pass --state-file; until then a later run may ping again."
            )
            return self._event(EVENT_PINGED, message, numbers[0], attention=True, details=details)
        return self._event(EVENT_PINGED, message, numbers[0], details=details)
