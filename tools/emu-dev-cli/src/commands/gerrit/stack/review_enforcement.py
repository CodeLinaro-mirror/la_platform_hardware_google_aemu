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

"""Audits Gerrit Review-Enforcement: who approved a CL and whose vote is still missing.

Review-Enforcement (go/gerrit-onboarding#review-enforcement) needs two trusted Googlers
to approve a change. A human uploader counts as one approval, and every other human
who votes Code-Review +1 or +2 counts as one more. Agent and bot accounts never count.
Gerrit decides who is trusted, so its requirement status is the source of truth and the
vote counting here only explains it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from commands.gerrit.services.queue_service import is_agent_or_bot_email

REVIEW_ENFORCEMENT_REQUIREMENT = "Review-Enforcement"
CODE_REVIEW_LABEL = "Code-Review"
REQUIRED_TRUSTED_APPROVALS = 2
CODE_REVIEW_MAX = 2
CODE_REVIEW_MIN = -2

STATUS_SATISFIED = "SATISFIED"
STATUS_UNSATISFIED = "UNSATISFIED"
STATUS_OVERRIDDEN = "OVERRIDDEN"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNKNOWN = "UNKNOWN"
PASSING_REQUIREMENT_STATUSES = frozenset(
    {STATUS_SATISFIED, STATUS_OVERRIDDEN, STATUS_NOT_APPLICABLE}
)

UNTRUSTED_APPROVAL_NOTE = (
    "Gerrit still reports Review-Enforcement UNSATISFIED although two human "
    "approvals were counted, so Gerrit does not trust one of them (for example a "
    "vote cast from an untrusted device). See go/gerrit-onboarding#review-enforcement."
)


def format_ldap(email: str) -> str:
    """Formats an account email as a short `ldap@` handle for human-readable output."""
    return f"{email.split('@', 1)[0]}@" if email else "unknown"


def requirement_statuses(change: Mapping[str, Any]) -> dict[str, str]:
    """Maps each submit requirement name on `change` to its upper-case status."""
    statuses: dict[str, str] = {}
    for req in change.get("submit_requirements") or []:
        if isinstance(req, Mapping) and req.get("name"):
            statuses[str(req["name"])] = str(req.get("status") or STATUS_UNKNOWN).upper()
    return statuses


def current_uploader_email(change: Mapping[str, Any]) -> str:
    """Returns the email of the account that uploaded the current patch set."""
    revisions = change.get("revisions") or {}
    rev_info = revisions.get(change.get("current_revision") or "") or {}
    uploader = rev_info.get("uploader") or change.get("owner") or {}
    return str(uploader.get("email") or "").strip().lower()


def _code_review_votes(change: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Returns `(email, value)` for every account with a non-zero Code-Review vote."""
    label = (change.get("labels") or {}).get(CODE_REVIEW_LABEL) or {}
    votes: list[tuple[str, int]] = []
    for vote in label.get("all") or []:
        if not isinstance(vote, Mapping):
            continue
        value = int(vote.get("value", 0) or 0)
        if value:
            votes.append((str(vote.get("email") or "").strip().lower(), value))
    return votes


def _is_human(email: str) -> bool:
    return bool(email) and not is_agent_or_bot_email(email)


def _pending_human_reviewers(
    change: Mapping[str, Any], uploader: str, approvers: tuple[str, ...]
) -> tuple[str, ...]:
    """Returns human reviewers who could still add an approval, in Gerrit's order."""
    pending: list[str] = []
    for reviewer in (change.get("reviewers") or {}).get("REVIEWER") or []:
        email = str(reviewer.get("email") or "").strip().lower()
        if _is_human(email) and email != uploader and email not in approvers and email not in pending:
            pending.append(email)
    return tuple(pending)


@dataclass(frozen=True)
class ReviewEnforcementAudit:
    """Review-Enforcement and Code-Review approval state of one CL."""

    requirement_status: str
    code_review_status: str
    uploader: str
    uploader_counts: bool
    approvers: tuple[str, ...]
    has_non_uploader_max: bool
    blocking_voters: tuple[str, ...]
    pending_reviewers: tuple[str, ...]
    # Approvers who voted +1 and could raise it to the +2 that Code-Review needs.
    plus_one_approvers: tuple[str, ...] = ()

    @property
    def approvals(self) -> int:
        """Number of approvals counted toward REQUIRED_TRUSTED_APPROVALS."""
        return int(self.uploader_counts) + len(self.approvers)

    @property
    def missing_approvals(self) -> int:
        """Approvals still needed before Review-Enforcement can pass."""
        return max(0, REQUIRED_TRUSTED_APPROVALS - self.approvals)

    @property
    def satisfied(self) -> bool:
        """True when Gerrit reports the requirement as passing (or, without data, when the count passes)."""
        if self.requirement_status != STATUS_UNKNOWN:
            return self.requirement_status in PASSING_REQUIREMENT_STATUSES
        return self.missing_approvals == 0 and not self.blocking_voters

    @property
    def code_review_satisfied(self) -> bool:
        """True when the Code-Review requirement passes (a non-uploader +2 and no -2)."""
        if self.code_review_status != STATUS_UNKNOWN:
            return self.code_review_status in PASSING_REQUIREMENT_STATUSES
        return self.has_non_uploader_max and not self.blocking_voters

    @property
    def untrusted_approval_suspected(self) -> bool:
        """True when enough human approvals were counted but Gerrit still rejects the requirement."""
        return self.requirement_status == STATUS_UNSATISFIED and self.missing_approvals == 0

    def describe(self) -> str:
        """One-line summary such as `1/2 approvals (uploader jansene@); waiting on joshuaduong@`."""
        sources = [f"uploader {format_ldap(self.uploader)}"] if self.uploader_counts else []
        sources.extend(format_ldap(email) for email in self.approvers)
        counted = f"{self.approvals}/{REQUIRED_TRUSTED_APPROVALS} approvals"
        text = f"{counted} ({', '.join(sources)})" if sources else counted
        if self.satisfied:
            return f"{self.requirement_status}: {text}"
        if self.untrusted_approval_suspected:
            return f"{self.requirement_status}: {text}; Gerrit does not trust one of them"
        return f"{self.requirement_status}: {text}; {self.describe_missing()}"

    def describe_missing(self) -> str:
        """Names whose vote would unblock the CL, or says to add reviewers when nobody is left."""
        needed = self.missing_approvals
        noun = "approval" if needed == 1 else "approvals"
        if not self.pending_reviewers:
            return f"needs {needed} more trusted human {noun}; add a reviewer"
        names = " or ".join(format_ldap(email) for email in self.pending_reviewers)
        return f"needs {needed} more trusted human {noun} from {names}"

    def to_dict(self) -> dict[str, Any]:
        """Serializes the audit into a JSON-compatible dictionary."""
        return {
            "requirement_status": self.requirement_status,
            "satisfied": self.satisfied,
            "approvals": self.approvals,
            "required_approvals": REQUIRED_TRUSTED_APPROVALS,
            "missing_approvals": self.missing_approvals,
            "uploader": self.uploader,
            "uploader_counts": self.uploader_counts,
            "approvers": list(self.approvers),
            "pending_reviewers": list(self.pending_reviewers),
            "plus_one_approvers": list(self.plus_one_approvers),
            "code_review_status": self.code_review_status,
            "code_review_satisfied": self.code_review_satisfied,
            "has_non_uploader_max": self.has_non_uploader_max,
            "blocking_voters": list(self.blocking_voters),
            "untrusted_approval_suspected": self.untrusted_approval_suspected,
        }


def audit_review_enforcement(change: Mapping[str, Any]) -> ReviewEnforcementAudit:
    """Builds the Review-Enforcement audit for a Gerrit ChangeInfo with DETAILED_LABELS."""
    statuses = requirement_statuses(change)
    uploader = current_uploader_email(change)
    votes = _code_review_votes(change)
    positive = [
        (email, value)
        for email, value in votes
        if value > 0 and _is_human(email) and email != uploader
    ]
    approvers = tuple(dict.fromkeys(email for email, _ in positive))
    return ReviewEnforcementAudit(
        requirement_status=statuses.get(REVIEW_ENFORCEMENT_REQUIREMENT, STATUS_UNKNOWN),
        code_review_status=statuses.get(CODE_REVIEW_LABEL, STATUS_UNKNOWN),
        uploader=uploader,
        uploader_counts=_is_human(uploader),
        approvers=approvers,
        has_non_uploader_max=any(
            value >= CODE_REVIEW_MAX and email != uploader for email, value in votes
        ),
        blocking_voters=tuple(email for email, value in votes if value <= CODE_REVIEW_MIN),
        pending_reviewers=_pending_human_reviewers(change, uploader, approvers),
        plus_one_approvers=tuple(
            dict.fromkeys(email for email, value in positive if value < CODE_REVIEW_MAX)
        ),
    )
