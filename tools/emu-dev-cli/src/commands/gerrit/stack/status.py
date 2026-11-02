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

"""`emu-dev-cli gerrit stack status`: review, presubmit, and merge state of a CL stack."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from commands.gerrit.presubmit_targets import extract_presubmit_status_from_change
from commands.gerrit.stack.review_enforcement import (
    CODE_REVIEW_LABEL,
    PASSING_REQUIREMENT_STATUSES,
    REVIEW_ENFORCEMENT_REQUIREMENT,
    STATUS_NOT_APPLICABLE,
    STATUS_UNSATISFIED,
    ReviewEnforcementAudit,
    audit_review_enforcement,
    format_ldap,
    requirement_statuses,
)
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import EmuCliError, ExecutionError, UsageError
from lib.gerrit_api import (
    COMMIT_CHANGE_ID_RE,
    DEFAULT_GERRIT_HOST,
    GerritClient,
    extract_label_score,
    parse_cl_identifier,
)

STATUS_QUERY_OPTIONS: tuple[str, ...] = (
    "CURRENT_REVISION",
    "CURRENT_COMMIT",
    "DETAILED_LABELS",
    "DETAILED_ACCOUNTS",
    "SUBMIT_REQUIREMENTS",
    "SUBMITTABLE",
    "MESSAGES",
)
MAX_TOPIC_CHANGES = 100
# Concurrent Gerrit requests per chain; each gob-curl call takes a few seconds.
MAX_PARALLEL_REQUESTS = 8
SHORT_SHA_LEN = 7
MAX_LISTED_CONFLICTS = 3

CHANGE_OPEN = "NEW"
CHANGE_MERGED = "MERGED"
CHANGE_ABANDONED = "ABANDONED"

MERGEABLE_TRUE = "true"
MERGEABLE_FALSE = "false"
MERGEABLE_CONFLICT = "conflict"
MERGEABLE_UNKNOWN = "unknown"
MERGEABLE_NOT_APPLICABLE = "n/a"

PRESUBMIT_VERIFIED_LABEL = "Presubmit-Verified"
PRESUBMIT_READY_LABEL = "Presubmit-Ready"
LINT_LABEL = "Lint"
PERFORMANCE_LABEL = "Performance"
AUTOSUBMIT_LABEL = "Autosubmit"
PRESUBMIT_PASSED = "passed"
# Presubmit-Verified +1: passed with warnings, which still satisfies the requirement.
PRESUBMIT_WARNING = "warning"
PRESUBMIT_FAILED = "failed"
PRESUBMIT_NOT_STARTED = "not_started"

# Requirements reported through dedicated fields, so they are not repeated as "other".
_DEDICATED_REQUIREMENTS = frozenset(
    {REVIEW_ENFORCEMENT_REQUIREMENT, CODE_REVIEW_LABEL, PRESUBMIT_VERIFIED_LABEL}
)

GerritClientBuilder = Callable[[ExecutionContext, str], GerritClient]


def _signed(score: int) -> str:
    return f"+{score}" if score > 0 else str(score)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _short_sha(sha: str) -> str:
    return sha[:SHORT_SHA_LEN] if sha else "?"


def _change_url(host: str, project: str, number: int) -> str:
    return f"{(host or DEFAULT_GERRIT_HOST).rstrip('/')}/c/{project}/+/{number}"


@dataclass(frozen=True)
class ChainLink:
    """One change in a Gerrit relation chain, as reported by the `related` endpoint."""

    number: int
    change_id: str
    status: str
    commit: str
    parent_commit: str
    patch_set: int
    current_patch_set: int

    @classmethod
    def from_related(cls, entry: Mapping[str, Any]) -> ChainLink:
        """Builds a link from a RelatedChangeAndCommitInfo entry."""
        commit = entry.get("commit") or {}
        parents = commit.get("parents") or [{}]
        return cls(
            number=int(entry.get("_change_number", 0) or 0),
            change_id=str(entry.get("change_id") or ""),
            status=str(entry.get("status") or CHANGE_OPEN),
            commit=str(commit.get("commit") or ""),
            parent_commit=str(parents[0].get("commit") or ""),
            patch_set=int(entry.get("_revision_number", 0) or 0),
            current_patch_set=int(entry.get("_current_revision_number", 0) or 0),
        )

    @classmethod
    def from_change(cls, change: Mapping[str, Any]) -> ChainLink:
        """Builds a single-link chain for a change that has no related changes."""
        rev_sha = str(change.get("current_revision") or "")
        rev_info = (change.get("revisions") or {}).get(rev_sha) or {}
        parents = (rev_info.get("commit") or {}).get("parents") or [{}]
        patch_set = int(rev_info.get("_number", 0) or 0)
        return cls(
            number=int(change.get("_number", 0) or 0),
            change_id=str(change.get("change_id") or ""),
            status=str(change.get("status") or CHANGE_OPEN),
            commit=rev_sha,
            parent_commit=str(parents[0].get("commit") or ""),
            patch_set=patch_set,
            current_patch_set=patch_set,
        )


@dataclass(frozen=True)
class PresubmitProgress:
    """Treehugger presubmit state and the CI labels it reports on the current patch set."""

    status: str
    verified: int
    lint: int
    performance: int
    workplan_id: str
    build_url: str
    checks_url: str
    summary: str

    def describe(self) -> str:
        """One-line summary such as `passed (PV +2, Lint +1, Performance +2) · 27 passed`."""
        scores = (
            f"PV {_signed(self.verified)}, Lint {_signed(self.lint)}, "
            f"Performance {_signed(self.performance)}"
        )
        text = f"{self.status} ({scores})"
        return f"{text} · {self.summary}" if self.summary else text

    def to_dict(self) -> dict[str, Any]:
        """Serializes the presubmit progress into a JSON-compatible dictionary."""
        return {
            "status": self.status,
            "presubmit_verified": self.verified,
            "lint": self.lint,
            "performance": self.performance,
            "workplan_id": self.workplan_id,
            "build_url": self.build_url,
            "checks_url": self.checks_url,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class Blocker:
    """Something that keeps a CL from merging, with the action that clears it."""

    reason: str
    action: str


@dataclass(frozen=True)
class ChangeStatus:
    """Review, presubmit, and merge state of one CL in a stack."""

    position: int
    number: int
    change_id: str
    subject: str
    status: str
    url: str
    current_revision: str
    patch_set: int
    chain_patch_set: int
    parent: str
    parent_number: int
    mergeable: str
    conflicts: tuple[str, ...]
    work_in_progress: bool
    unresolved_comments: int
    autosubmit: bool
    presubmit: PresubmitProgress | None
    review: ReviewEnforcementAudit | None
    other_unsatisfied: tuple[str, ...]
    blockers: tuple[Blocker, ...]
    next_action: str
    is_target: bool = False

    @property
    def is_open(self) -> bool:
        """True while the CL is still under review (status NEW)."""
        return self.status == CHANGE_OPEN

    def to_dict(self) -> dict[str, Any]:
        """Serializes the CL state into a JSON-compatible dictionary."""
        return {
            "position": self.position,
            "number": self.number,
            "change_id": self.change_id,
            "subject": self.subject,
            "status": self.status,
            "url": self.url,
            "current_revision": self.current_revision,
            "patch_set": self.patch_set,
            "chain_patch_set": self.chain_patch_set,
            "parent": self.parent,
            "parent_number": self.parent_number or None,
            "mergeable": self.mergeable,
            "conflicts": list(self.conflicts),
            "work_in_progress": self.work_in_progress,
            "unresolved_comments": self.unresolved_comments,
            "autosubmit": self.autosubmit,
            "presubmit": self.presubmit.to_dict() if self.presubmit else None,
            "review_enforcement": self.review.to_dict() if self.review else None,
            "other_unsatisfied_requirements": list(self.other_unsatisfied),
            "blockers": [{"reason": b.reason, "action": b.action} for b in self.blockers],
            "next_action": self.next_action,
            "is_target": self.is_target,
        }


@dataclass(frozen=True)
class StackSnapshot:
    """All CLs of one relation chain, ordered from the base (position 1) to the tip."""

    project: str
    branch: str
    changes: tuple[ChangeStatus, ...]

    @property
    def open_changes(self) -> tuple[ChangeStatus, ...]:
        """CLs that are still under review."""
        return tuple(c for c in self.changes if c.is_open)

    def summary(self) -> str:
        """One-line verdict naming the first CL that blocks the stack."""
        open_changes = self.open_changes
        if not open_changes:
            return f"All {_plural(len(self.changes), 'CL')} in the stack are closed."
        blocked = next((c for c in open_changes if c.blockers), None)
        if blocked is None:
            return f"{_plural(len(open_changes), 'open CL')}, all ready to merge."
        return (
            f"{_plural(len(open_changes), 'open CL')}; first blocker ag/{blocked.number}: "
            f"{blocked.blockers[0].reason}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes the stack into a JSON-compatible dictionary."""
        return {
            "project": self.project,
            "branch": self.branch,
            "size": len(self.changes),
            "open": len(self.open_changes),
            "summary": self.summary(),
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class StackStatusReport(Mapping[str, Any]):
    """Structured output of `gerrit stack status`."""

    host: str
    query: Mapping[str, Any]
    stacks: tuple[StackSnapshot, ...]
    local: Mapping[str, Any] | None = None

    @property
    def summary(self) -> str:
        """Joins the per-stack verdicts into the command summary line."""
        return " | ".join(
            f"{s.project} ({s.branch}): {s.summary()}" for s in self.stacks
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes the report into the `--json` payload (`local` only when HEAD was resolved)."""
        payload: dict[str, Any] = {
            "host": self.host,
            "query": dict(self.query),
            "stacks": [stack.to_dict() for stack in self.stacks],
        }
        if self.local:
            payload["local"] = dict(self.local)
        return payload

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def _label_score(change: Mapping[str, Any], label: str) -> int:
    return extract_label_score((change.get("labels") or {}).get(label))


def _has_positive_vote(change: Mapping[str, Any], label: str) -> bool:
    votes = ((change.get("labels") or {}).get(label) or {}).get("all") or []
    return any(int(v.get("value", 0) or 0) > 0 for v in votes if isinstance(v, Mapping))


def build_presubmit_progress(change: Mapping[str, Any]) -> PresubmitProgress:
    """Derives Treehugger progress from labels and the current patch set's bot messages."""
    verified = _label_score(change, PRESUBMIT_VERIFIED_LABEL)
    rev_info = (change.get("revisions") or {}).get(change.get("current_revision") or "") or {}
    info = extract_presubmit_status_from_change(
        dict(change),
        verified,
        _has_positive_vote(change, PRESUBMIT_READY_LABEL),
        int(rev_info.get("_number", 0) or 0),
    )
    status = info["presubmit_status"]
    if requirement_statuses(change).get(PRESUBMIT_VERIFIED_LABEL) == "SATISFIED":
        status = PRESUBMIT_PASSED
    return PresubmitProgress(
        status=status,
        verified=verified,
        lint=_label_score(change, LINT_LABEL),
        performance=_label_score(change, PERFORMANCE_LABEL),
        workplan_id=info["workplan_id"],
        build_url=info["workplan_url"],
        checks_url=info["checks_tab_url"],
        summary=info["presubmit_summary"],
    )


def classify_mergeable(info: Mapping[str, Any] | None) -> tuple[str, tuple[str, ...]]:
    """Maps Gerrit MergeableInfo to `true`, `false`, `conflict`, or `unknown`, plus conflicting files."""
    if not info or "mergeable" not in info:
        return MERGEABLE_UNKNOWN, ()
    if info.get("mergeable"):
        return MERGEABLE_TRUE, ()
    conflicts = tuple(str(path) for path in info.get("conflicts") or [])
    return (MERGEABLE_CONFLICT if conflicts else MERGEABLE_FALSE), conflicts


def _other_unsatisfied(change: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        name
        for name, status in requirement_statuses(change).items()
        if name not in _DEDICATED_REQUIREMENTS
        and status not in PASSING_REQUIREMENT_STATUSES
        and status != STATUS_NOT_APPLICABLE
    )


@dataclass(frozen=True)
class _ChangeFacts:
    """Inputs of the blocker assessment for one open CL."""

    branch: str
    parent_link: ChainLink | None
    mergeable: str
    conflicts: tuple[str, ...]
    work_in_progress: bool
    unresolved_comments: int
    presubmit: PresubmitProgress
    review: ReviewEnforcementAudit
    other_unsatisfied: tuple[str, ...]


def _structural_blockers(facts: _ChangeFacts) -> list[Blocker]:
    """Blockers that a rebase or a state change fixes, independent of reviewers."""
    blockers: list[Blocker] = []
    parent = facts.parent_link
    if parent is not None and parent.status == CHANGE_ABANDONED:
        blockers.append(Blocker(
            f"Parent ag/{parent.number} is abandoned",
            f"Rebase onto {facts.branch} or restore ag/{parent.number}",
        ))
    elif parent is not None and parent.patch_set < parent.current_patch_set:
        blockers.append(Blocker(
            f"Based on ag/{parent.number} PS{parent.patch_set}, but its latest is PS{parent.current_patch_set}",
            f"Rebase onto ag/{parent.number} PS{parent.current_patch_set}",
        ))
    if facts.mergeable in (MERGEABLE_CONFLICT, MERGEABLE_FALSE):
        files = ", ".join(facts.conflicts[:MAX_LISTED_CONFLICTS])
        detail = f" in {files}" if files else ""
        blockers.append(Blocker(
            f"Does not merge cleanly into {facts.branch}{detail}",
            f"Rebase onto {facts.branch} and resolve the conflicts",
        ))
    if facts.work_in_progress:
        blockers.append(Blocker("Marked work in progress", "Mark the CL ready for review"))
    return blockers


def _review_blockers(review: ReviewEnforcementAudit) -> list[Blocker]:
    """Blockers that need a reviewer's vote."""
    blockers: list[Blocker] = []
    if review.blocking_voters:
        names = ", ".join(format_ldap(e) for e in review.blocking_voters)
        blockers.append(Blocker(f"Code-Review -2 from {names}", f"Address the -2 from {names}"))
    if review.code_review_satisfied:
        vote, candidates = "+1", review.pending_reviewers
    else:
        vote, candidates = "+2", (*review.plus_one_approvers, *review.pending_reviewers)
    names = " or ".join(format_ldap(e) for e in candidates)
    ask = f"Ask {names} for Code-Review {vote}" if names else f"Add a trusted reviewer for Code-Review {vote}"
    if review.untrusted_approval_suspected:
        blockers.append(Blocker(
            "Review-Enforcement is unsatisfied although two human approvals were counted",
            f"{ask}, or re-vote from a trusted device",
        ))
    elif not review.satisfied:
        blockers.append(Blocker(f"Review-Enforcement: {review.describe_missing()}", ask))
    if review.satisfied and not review.code_review_satisfied and not review.blocking_voters:
        blockers.append(Blocker("No Code-Review +2 from someone other than the uploader", ask))
    return blockers


def _presubmit_blocker(presubmit: PresubmitProgress) -> Blocker | None:
    """Returns the blocker for an unfinished or failed Treehugger run, if any."""
    if presubmit.status in (PRESUBMIT_PASSED, PRESUBMIT_WARNING):
        return None
    if presubmit.status == PRESUBMIT_FAILED:
        link = presubmit.build_url or presubmit.checks_url
        return Blocker(
            f"Presubmit failed (PV {_signed(presubmit.verified)})",
            f"Fix the failing targets: {link}" if link else "Fix the failing presubmit targets",
        )
    if presubmit.status == PRESUBMIT_NOT_STARTED:
        return Blocker("Presubmit has not run on this patch set", "Vote Presubmit-Ready +1")
    run = f" ({presubmit.workplan_id})" if presubmit.workplan_id else ""
    return Blocker(f"Presubmit is {presubmit.status}{run}", "Wait for presubmit to finish")


def _ci_blockers(facts: _ChangeFacts) -> list[Blocker]:
    """Blockers raised by presubmit, comments, and the remaining submit requirements."""
    presubmit_blocker = _presubmit_blocker(facts.presubmit)
    blockers = [presubmit_blocker] if presubmit_blocker else []
    if facts.unresolved_comments:
        blockers.append(Blocker(
            f"{facts.unresolved_comments} unresolved comment thread(s)",
            "Resolve or reply to the open threads",
        ))
    if facts.other_unsatisfied:
        names = ", ".join(facts.other_unsatisfied)
        blockers.append(Blocker(f"Unsatisfied submit requirements: {names}", f"Satisfy {names}"))
    return blockers


def assess_blockers(facts: _ChangeFacts) -> tuple[Blocker, ...]:
    """Lists everything that keeps an open CL from merging, most fundamental first."""
    return (
        *_structural_blockers(facts),
        *_review_blockers(facts.review),
        *_ci_blockers(facts),
    )


def _ready_action(autosubmit: bool, parent_link: ChainLink | None) -> str:
    if parent_link is not None and parent_link.status == CHANGE_OPEN:
        return f"Ready; it merges after ag/{parent_link.number}"
    if autosubmit:
        return "Ready; Autosubmit will merge it"
    return "Ready; vote Autosubmit +1 or submit it"


@dataclass(frozen=True)
class _OpenAssessment:
    """Review and CI state of an open CL, plus what to do next."""

    presubmit: PresubmitProgress
    review: ReviewEnforcementAudit
    other_unsatisfied: tuple[str, ...]
    blockers: tuple[Blocker, ...]
    next_action: str


def _assess_open_change(
    change: Mapping[str, Any],
    parent_link: ChainLink | None,
    mergeable: str,
    conflicts: tuple[str, ...],
) -> _OpenAssessment:
    presubmit = build_presubmit_progress(change)
    review = audit_review_enforcement(change)
    other = _other_unsatisfied(change)
    blockers = assess_blockers(_ChangeFacts(
        branch=str(change.get("branch") or ""),
        parent_link=parent_link,
        mergeable=mergeable,
        conflicts=conflicts,
        work_in_progress=bool(change.get("work_in_progress")),
        unresolved_comments=int(change.get("unresolved_comment_count", 0) or 0),
        presubmit=presubmit,
        review=review,
        other_unsatisfied=other,
    ))
    autosubmit = _has_positive_vote(change, AUTOSUBMIT_LABEL)
    next_action = blockers[0].action if blockers else _ready_action(autosubmit, parent_link)
    return _OpenAssessment(presubmit, review, other, blockers, next_action)


def build_change_status(
    *,
    position: int,
    link: ChainLink,
    parent_link: ChainLink | None,
    change: Mapping[str, Any],
    mergeable_info: Mapping[str, Any] | None,
    host: str,
    target_number: int,
) -> ChangeStatus:
    """Combines a chain link, its ChangeInfo, and MergeableInfo into one CL state."""
    branch = str(change.get("branch") or "")
    rev_sha = str(change.get("current_revision") or link.commit)
    rev_info = (change.get("revisions") or {}).get(rev_sha) or {}
    status = str(change.get("status") or link.status)
    is_open = status == CHANGE_OPEN
    mergeable, conflicts = (
        classify_mergeable(mergeable_info) if is_open else (MERGEABLE_NOT_APPLICABLE, ())
    )
    assessment = _assess_open_change(change, parent_link, mergeable, conflicts) if is_open else None
    parent = (
        f"ag/{parent_link.number} PS{parent_link.patch_set}"
        if parent_link is not None
        else f"{branch}@{_short_sha(link.parent_commit)}"
    )
    return ChangeStatus(
        position=position,
        number=link.number,
        change_id=link.change_id or str(change.get("change_id") or ""),
        subject=str(change.get("subject") or ""),
        status=status,
        url=_change_url(host, str(change.get("project") or ""), link.number),
        current_revision=rev_sha,
        patch_set=int(rev_info.get("_number", 0) or link.current_patch_set),
        chain_patch_set=link.patch_set,
        parent=parent,
        parent_number=parent_link.number if parent_link is not None else 0,
        mergeable=mergeable,
        conflicts=conflicts,
        work_in_progress=bool(change.get("work_in_progress")),
        unresolved_comments=int(change.get("unresolved_comment_count", 0) or 0),
        autosubmit=_has_positive_vote(change, AUTOSUBMIT_LABEL),
        presubmit=assessment.presubmit if assessment else None,
        review=assessment.review if assessment else None,
        other_unsatisfied=assessment.other_unsatisfied if assessment else (),
        blockers=assessment.blockers if assessment else (),
        next_action=assessment.next_action if assessment else status.capitalize(),
        is_target=link.number == target_number,
    )


class StackStatusService:
    """Collects relation chains and per-CL state from Gerrit through `GerritClient`."""

    def __init__(self, client: GerritClient, host: str) -> None:
        self._client = client
        self._host = host

    def find_changes(self, query: str, limit: int = MAX_TOPIC_CHANGES) -> list[dict[str, Any]]:
        """Runs a Gerrit search with every option the status view needs."""
        return self._client.query_changes(query, options=STATUS_QUERY_OPTIONS, limit=limit)

    def collect(self, seeds: Sequence[Mapping[str, Any]], target_number: int = 0) -> tuple[StackSnapshot, ...]:
        """Builds one snapshot per relation chain that contains at least one seed CL."""
        details = {int(s.get("_number", 0) or 0): dict(s) for s in seeds}
        covered: set[int] = set()
        stacks: list[StackSnapshot] = []
        for seed in seeds:
            number = int(seed.get("_number", 0) or 0)
            if number in covered:
                continue
            chain = self._chain_for(seed)
            covered.update(link.number for link in chain)
            mergeable = self._fetch_chain_state(chain, details)
            stacks.append(self._snapshot(chain, details, mergeable, target_number))
        return tuple(stacks)

    def _chain_for(self, seed: Mapping[str, Any]) -> list[ChainLink]:
        """Returns the chain containing `seed`, ordered base first."""
        related = self._client.get_related_changes(int(seed.get("_number", 0) or 0))
        if not related:
            return [ChainLink.from_change(seed)]
        return [ChainLink.from_related(entry) for entry in reversed(related)]

    def _query_details(self, numbers: Sequence[int]) -> list[dict[str, Any]]:
        query = " OR ".join(f"change:{number}" for number in numbers)
        return self.find_changes(query, limit=len(numbers))

    def _safe_mergeable(self, number: int) -> dict[str, Any]:
        """Returns MergeableInfo for `number`, or `{}` when the endpoint fails."""
        try:
            return self._client.get_mergeable(number)
        except Exception:
            return {}

    def _fetch_chain_state(
        self, chain: Sequence[ChainLink], details: dict[int, dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        """Fills in missing ChangeInfo and returns MergeableInfo of open CLs, fetched concurrently."""
        missing = [link.number for link in chain if link.number not in details]
        open_numbers = [link.number for link in chain if link.status == CHANGE_OPEN]
        workers = min(MAX_PARALLEL_REQUESTS, len(open_numbers) + 1)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            details_future = pool.submit(self._query_details, missing) if missing else None
            futures = {n: pool.submit(self._safe_mergeable, n) for n in open_numbers}
            fetched = details_future.result() if details_future is not None else []
            mergeable = {number: future.result() for number, future in futures.items()}
        details.update((int(change.get("_number", 0) or 0), change) for change in fetched)
        return mergeable

    def _snapshot(
        self,
        chain: Sequence[ChainLink],
        details: Mapping[int, dict[str, Any]],
        mergeable: Mapping[int, dict[str, Any]],
        target_number: int,
    ) -> StackSnapshot:
        by_commit = {link.commit: link for link in chain if link.commit}
        changes: list[ChangeStatus] = []
        for index, link in enumerate(chain):
            change = details.get(link.number)
            if change is None:
                raise ExecutionError(
                    f"Gerrit returned no details for ag/{link.number}, which is part of the "
                    f"relation chain on '{self._host}'.",
                    remediation="Check that you can open the CL in Gerrit, run `gcert`, and retry.",
                )
            parent_link = by_commit.get(link.parent_commit) or (chain[index - 1] if index else None)
            changes.append(build_change_status(
                position=index + 1,
                link=link,
                parent_link=parent_link,
                change=change,
                mergeable_info=mergeable.get(link.number),
                host=self._host,
                target_number=target_number,
            ))
        tip = details.get(chain[-1].number) or {}
        return StackSnapshot(
            project=str(tip.get("project") or ""),
            branch=str(tip.get("branch") or ""),
            changes=tuple(changes),
        )


def _render_change(change: ChangeStatus, total: int) -> list[str]:
    marker = " *" if change.is_target else ""
    lines = [
        f"{change.position}/{total} ag/{change.number}{marker} PS{change.patch_set} · "
        f"{change.status} · {change.subject}",
        f"    {change.url}",
    ]
    if not change.is_open:
        return lines
    lines.append(f"    parent: {change.parent} · mergeable: {change.mergeable}")
    if change.presubmit is not None:
        build = f" · {change.presubmit.build_url}" if change.presubmit.build_url else ""
        lines.append(f"    presubmit: {change.presubmit.describe()}{build}")
    if change.review is not None:
        lines.append(f"    review-enforcement: {change.review.describe()}")
    autosubmit = "armed" if change.autosubmit else "not set"
    lines.append(f"    autosubmit: {autosubmit} · next: {change.next_action}")
    lines.extend(f"    blocker: {b.reason}" for b in change.blockers)
    return lines


def render_stack_report(report: StackStatusReport) -> str:
    """Renders the human-readable stack view, base CL first."""
    lines: list[str] = []
    for stack in report.stacks:
        lines.append(
            f"Stack: {stack.project} ({stack.branch}) · {len(stack.changes)} CL(s), base first"
        )
        for change in stack.changes:
            lines.extend(_render_change(change, len(stack.changes)))
        lines.append("")
    local = report.local or {}
    if local and not local.get("in_sync", True):
        lines.append(str(local.get("note") or ""))
    return "\n".join(lines).rstrip()


@dataclass(frozen=True)
class StackStatusArgs:
    """Validated arguments for `StackStatusCommand`."""

    cl: str = "HEAD"
    topic: str = ""
    repo: str = "."
    host: str = DEFAULT_GERRIT_HOST


class StackStatusCommand(Command[StackStatusArgs, StackStatusReport]):
    """Shows parent/child order, patch sets, mergeability, presubmit, and approvals of a CL stack."""

    name = "status"
    help_text = "Show the review, presubmit, and merge state of every CL in a stack"

    def __init__(self, *, gerrit_client_factory: GerritClientBuilder | None = None) -> None:
        self._gerrit_client_factory = gerrit_client_factory

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, ag/<id>, Change-Id, Gerrit URL, or HEAD (default: HEAD)",
        )
        parser.add_argument(
            "--topic",
            default="",
            help="Inspect every relation chain in this Gerrit topic instead of one CL",
        )
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git repository used to resolve HEAD (default: .)",
        )
        parser.add_argument(
            "--host",
            default=argparse.SUPPRESS,
            help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON",
        )

    def validate(
        self, args: StackStatusArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> StackStatusArgs:
        del ctx
        if isinstance(args, StackStatusArgs):
            candidate = args
        else:
            candidate = StackStatusArgs(
                cl=str(getattr(args, "cl", "HEAD") or "HEAD").strip(),
                topic=str(getattr(args, "topic", "") or "").strip(),
                repo=str(getattr(args, "repo", ".") or "."),
                host=str(getattr(args, "host", DEFAULT_GERRIT_HOST) or DEFAULT_GERRIT_HOST),
            )
        if candidate.topic and candidate.cl.upper() != "HEAD":
            raise UsageError(
                f"Both a CL ('{candidate.cl}') and --topic '{candidate.topic}' were given.",
                remediation="Pass either a CL identifier or --topic, not both.",
            )
        return candidate

    def _create_client(self, ctx: ExecutionContext, host: str) -> GerritClient:
        if self._gerrit_client_factory is not None:
            return self._gerrit_client_factory(ctx, host)
        return GerritClient(host=host, runner=ctx.process_runner, prefer_cli=True)

    def _resolve_head(self, ctx: ExecutionContext, repo: str) -> tuple[str, str]:
        """Returns `(change_id, head_sha)` of the local HEAD commit through GitService."""
        git = ctx.git_service
        if not git.is_git_repo(cwd=repo):
            raise UsageError(
                f"'{repo}' is not inside a Git work tree, so HEAD cannot be resolved.",
                remediation="Run from your checkout, pass --repo <dir>, or pass a CL number or --topic.",
            )
        match = COMMIT_CHANGE_ID_RE.search(git.commit_message("HEAD", cwd=repo))
        if not match:
            raise UsageError(
                f"The HEAD commit in '{repo}' has no 'Change-Id:' footer, so it has not been uploaded.",
                remediation="Upload the commit first, or pass a CL number, ag/<id>, or --topic.",
            )
        return match.group(1), git.head_sha(cwd=repo)

    @staticmethod
    def _pick_seed(changes: list[dict[str, Any]], cl_id: str, host: str) -> dict[str, Any]:
        """Picks the one change matching `cl_id`, preferring open changes for shared Change-Ids."""
        if not changes:
            raise UsageError(
                f"No Gerrit change matches '{cl_id}' on '{host}'.",
                remediation="Check the CL number or Change-Id, or pass --host for another Gerrit host.",
            )
        open_changes = [c for c in changes if c.get("status") == CHANGE_OPEN] or changes
        if len(open_changes) > 1:
            listed = ", ".join(f"ag/{c.get('_number')} ({c.get('branch')})" for c in open_changes)
            raise UsageError(
                f"Change-Id '{cl_id}' matches {len(open_changes)} CLs: {listed}.",
                remediation="Pass the CL number of the one you want.",
            )
        return open_changes[0]

    def _resolve_seeds(
        self, args: StackStatusArgs, service: StackStatusService, ctx: ExecutionContext
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Returns `(query_info, seed_changes, local_head_sha)` for the requested CL or topic."""
        if args.topic:
            seeds = service.find_changes(f'topic:"{args.topic}"')
            if not seeds:
                raise UsageError(
                    f"No Gerrit changes are in topic '{args.topic}' on '{args.host}'.",
                    remediation="Check the topic name (it is case-sensitive) or pass a CL instead.",
                )
            return {"mode": "topic", "input": args.topic}, seeds, ""
        head_sha = ""
        if args.cl.upper() == "HEAD":
            cl_id, head_sha = self._resolve_head(ctx, args.repo)
        else:
            try:
                cl_id = parse_cl_identifier(args.cl, allow_head=False, strict=True)
            except ValueError as exc:
                raise UsageError(str(exc), remediation="Pass a CL number, ag/<id>, Gerrit URL, or Change-Id.") from exc
        seed = self._pick_seed(service.find_changes(f"change:{cl_id}"), cl_id, args.host)
        mode = "head" if head_sha else "change"
        return {"mode": mode, "input": args.cl, "resolved": cl_id}, [seed], head_sha

    @staticmethod
    def _local_sync(stacks: Sequence[StackSnapshot], change_id: str, head_sha: str, repo: str) -> dict[str, Any] | None:
        """Compares the local HEAD commit with the uploaded current patch set."""
        if not head_sha:
            return None
        match = next(
            (c for s in stacks for c in s.changes if c.change_id == change_id), None
        )
        in_sync = match is not None and match.current_revision == head_sha
        note = ""
        if match is not None and not in_sync:
            note = (
                f"Local HEAD {_short_sha(head_sha)} in '{repo}' is not the uploaded "
                f"ag/{match.number} PS{match.patch_set} ({_short_sha(match.current_revision)}); "
                "upload your local commit or rebase onto the uploaded patch set."
            )
        return {"repo": repo, "head": head_sha, "in_sync": in_sync, "note": note}

    def execute(
        self, args: StackStatusArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[StackStatusReport]:
        validated = self.validate(args, ctx)
        client = self._create_client(ctx, validated.host)
        service = StackStatusService(client, validated.host)
        try:
            query, seeds, head_sha = self._resolve_seeds(validated, service, ctx)
            target = int(seeds[0].get("_number", 0) or 0) if query["mode"] != "topic" else 0
            stacks = service.collect(seeds, target_number=target)
        except EmuCliError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"Failed to read the stack for '{validated.topic or validated.cl}' from "
                f"'{validated.host}': {exc}",
                remediation="Run `gcert` to refresh credentials and check that the CL exists.",
            ) from exc
        finally:
            client.close()
        query["host"] = validated.host
        local = self._local_sync(stacks, str(query.get("resolved") or ""), head_sha, validated.repo)
        report = StackStatusReport(host=validated.host, query=query, stacks=stacks, local=local)
        return CommandResult.success(
            report,
            action="gerrit stack status",
            summary=report.summary,
            markdown_table=None if ctx.config.json_mode else render_stack_report(report),
        )
