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

"""`emu-dev-cli gerrit stack sync`: collision detection, rebase validation, and stack rebase."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from commands.gerrit.stack.status import (
    CHANGE_ABANDONED,
    CHANGE_MERGED,
    CHANGE_OPEN,
    MERGEABLE_CONFLICT,
    MERGEABLE_FALSE,
    SHORT_SHA_LEN,
    STATUS_QUERY_OPTIONS,
    ChainLink,
    CollectedChain,
    GerritClientBuilder,
    StackStatusService,
    classify_mergeable,
    pick_seed_change,
    resolve_ref_change_id,
)
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import EmuCliError, ExecutionError, UsageError
from lib.gerrit_api import (
    COMMIT_CHANGE_ID_RE,
    DEFAULT_GERRIT_HOST,
    GerritClient,
    parse_cl_identifier,
)

SYNC_QUERY_OPTIONS: tuple[str, ...] = (*STATUS_QUERY_OPTIONS, "CURRENT_FILES")
DEFAULT_REMOTE: str = "origin"
PREFERRED_REMOTES: tuple[str, ...] = ("aosp", "goog", "origin")
NOTIFY_OWNER: str = "OWNER"
PSEUDO_FILE_COMMIT_MSG: str = "/COMMIT_MSG"
PSEUDO_FILE_MERGE_LIST: str = "/MERGE_LIST"
IGNORED_GERRIT_FILES: frozenset[str] = frozenset(
    {PSEUDO_FILE_COMMIT_MSG, PSEUDO_FILE_MERGE_LIST}
)

MAX_DISPLAYED_UPSTREAM_COMMITS: int = 5
MAX_DISPLAYED_COLLISION_FILES: int = 5
CHANGE_SHARD_DIGITS: int = 2

SYNC_ACTION_UP_TO_DATE: str = "up_to_date"
SYNC_ACTION_REBASED: str = "rebased"
SYNC_ACTION_WOULD_REBASE: str = "would_rebase"
SYNC_ACTION_CONFLICT: str = "conflict"
SYNC_ACTION_BLOCKED: str = "blocked"
SYNC_ACTION_MERGED: str = "merged"
SYNC_ACTION_ABANDONED: str = "abandoned"

_CONFLICT_PATH_RE = re.compile(r"Merge conflict in (?P<path>.+)$")


def _short_sha(sha: str) -> str:
    return sha[:SHORT_SHA_LEN] if sha else "?"


def _change_url(host: str, project: str, number: int) -> str:
    return f"{(host or DEFAULT_GERRIT_HOST).rstrip('/')}/c/{project}/+/{number}"


def _gerrit_change_ref(number: int, patch_set: int) -> str:
    shard = f"{number % 100:0{CHANGE_SHARD_DIGITS}d}"
    return f"refs/changes/{shard}/{number}/{patch_set}"


def _ensure_change_id_footer(message: str, change_id: str) -> str:
    """Ensures `Change-Id: <change_id>` is present in `message`."""
    if not change_id or COMMIT_CHANGE_ID_RE.search(message):
        return message
    stripped = message.rstrip()
    return f"{stripped}\n\nChange-Id: {change_id}\n"


@dataclass(frozen=True)
class UpstreamCommit:
    """One commit landed on the target branch since the stack's base commit."""

    sha: str
    subject: str

    def to_dict(self) -> dict[str, str]:
        """Serializes the upstream commit into a JSON-compatible dictionary."""
        return {"sha": self.sha, "subject": self.subject}


@dataclass(frozen=True)
class FileCollision:
    """A file modified by both upstream target-branch commits and open CLs in the stack."""

    path: str
    cl_numbers: tuple[int, ...]
    upstream_commits: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serializes the file collision into a JSON-compatible dictionary."""
        return {
            "path": self.path,
            "cl_numbers": list(self.cl_numbers),
            "upstream_commits": list(self.upstream_commits),
        }


@dataclass(frozen=True)
class ChangeSyncResult:
    """Sync, collision, and rebase outcome for a single CL in the stack."""

    position: int
    number: int
    change_id: str
    subject: str
    status: str
    url: str
    topic: str
    old_revision: str
    new_revision: str
    old_parent: str
    new_parent: str
    patch_set: int
    needs_rebase: bool
    clean_rebase: bool
    action: str
    pushed: bool
    touched_files: tuple[str, ...]
    colliding_files: tuple[str, ...]
    conflicts: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes the per-CL sync outcome into a JSON-compatible dictionary."""
        return {
            "position": self.position,
            "number": self.number,
            "change_id": self.change_id,
            "subject": self.subject,
            "status": self.status,
            "url": self.url,
            "topic": self.topic,
            "old_revision": self.old_revision,
            "new_revision": self.new_revision,
            "old_parent": self.old_parent,
            "new_parent": self.new_parent,
            "patch_set": self.patch_set,
            "needs_rebase": self.needs_rebase,
            "clean_rebase": self.clean_rebase,
            "action": self.action,
            "pushed": self.pushed,
            "touched_files": list(self.touched_files),
            "colliding_files": list(self.colliding_files),
            "conflicts": list(self.conflicts),
            "note": self.note,
        }


@dataclass(frozen=True)
class StackSyncSnapshot:
    """Sync and collision report for one Gerrit relation chain."""

    project: str
    branch: str
    topic: str
    base_commit: str
    target_ref: str
    target_commit: str
    upstream_commits: tuple[UpstreamCommit, ...]
    collisions: tuple[FileCollision, ...]
    changes: tuple[ChangeSyncResult, ...]
    pushed: bool
    push_refspec: str = ""

    @property
    def has_conflicts(self) -> bool:
        """True if any open CL in the chain failed rebase validation or execution."""
        return any(
            c.action in (SYNC_ACTION_CONFLICT, SYNC_ACTION_BLOCKED)
            for c in self.changes
        )

    @property
    def rebased_count(self) -> int:
        """Number of CLs rebased (or that would be rebased in dry-run mode)."""
        return sum(
            1
            for c in self.changes
            if c.action in (SYNC_ACTION_REBASED, SYNC_ACTION_WOULD_REBASE)
        )

    def summary(self, *, dry_run: bool = False) -> str:
        """One-line summary of the stack sync outcome."""
        conflict_cl = next(
            (c for c in self.changes if c.action == SYNC_ACTION_CONFLICT), None
        )
        if conflict_cl is not None:
            return (
                f"Conflict on ag/{conflict_cl.number} ({conflict_cl.subject}); "
                f"{len(self.collisions)} file collision(s) with {self.target_ref}."
            )
        if self.rebased_count == 0:
            return f"All open CLs are already up to date with {self.target_ref}."
        if dry_run:
            return (
                f"Dry-run: {self.rebased_count} CL(s) cleanly rebase onto "
                f"{self.target_ref} ({len(self.collisions)} file overlap(s))."
            )
        push_state = "and pushed to Gerrit" if self.pushed else "locally (--no-push)"
        return f"Rebased {self.rebased_count} CL(s) onto {self.target_ref} {push_state}."

    def to_dict(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Serializes the stack sync snapshot into a JSON-compatible dictionary."""
        return {
            "project": self.project,
            "branch": self.branch,
            "topic": self.topic,
            "base_commit": self.base_commit,
            "target_ref": self.target_ref,
            "target_commit": self.target_commit,
            "upstream_behind_count": len(self.upstream_commits),
            "upstream_commits": [c.to_dict() for c in self.upstream_commits],
            "collisions": [c.to_dict() for c in self.collisions],
            "changes": [c.to_dict() for c in self.changes],
            "pushed": self.pushed,
            "push_refspec": self.push_refspec,
            "has_conflicts": self.has_conflicts,
            "rebased_count": self.rebased_count,
            "summary": self.summary(dry_run=dry_run),
        }


@dataclass(frozen=True)
class StackSyncReport(Mapping[str, Any]):
    """Structured output of `emu-dev-cli gerrit stack sync`."""

    host: str
    query: Mapping[str, Any]
    dry_run: bool
    pushed: bool
    stacks: tuple[StackSyncSnapshot, ...]

    @property
    def has_conflicts(self) -> bool:
        """True if any stack encountered rebase or validation conflicts."""
        return any(s.has_conflicts for s in self.stacks)

    @property
    def summary(self) -> str:
        """Joins the per-stack sync verdicts into the command summary line."""
        return " | ".join(
            f"{s.project} ({s.branch}): {s.summary(dry_run=self.dry_run)}"
            for s in self.stacks
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes the full report into a JSON-compatible dictionary."""
        return {
            "host": self.host,
            "query": dict(self.query),
            "dry_run": self.dry_run,
            "pushed": self.pushed,
            "has_conflicts": self.has_conflicts,
            "stacks": [s.to_dict(dry_run=self.dry_run) for s in self.stacks],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def _extract_touched_files(
    change: Mapping[str, Any],
    rev_sha: str,
    parent_sha: str,
    ctx: ExecutionContext,
    repo: str,
) -> tuple[str, ...]:
    """Returns files modified by `change` from Gerrit RevisionInfo or local `git diff`."""
    rev_info = (change.get("revisions") or {}).get(rev_sha) or {}
    files_map = rev_info.get("files")
    if isinstance(files_map, Mapping) and files_map:
        paths = [
            str(p) for p in files_map.keys() if str(p) not in IGNORED_GERRIT_FILES
        ]
        if paths:
            return tuple(sorted(paths))
    if parent_sha and rev_sha:
        diff = ctx.git_service.diff_names(parent_sha, rev_sha, cwd=repo)
        if diff:
            return tuple(diff)
    diff_single = ctx.git_service.diff_names(rev_sha, cwd=repo)
    return tuple(diff_single)


def _collect_upstream_commits(
    ctx: ExecutionContext, repo: str, base_commit: str, target_ref: str
) -> tuple[UpstreamCommit, ...]:
    """Returns commits in `<base_commit>..<target_ref>` from `git log --oneline`."""
    if not base_commit or not target_ref or base_commit == target_ref:
        return ()
    entries = ctx.git_service.log_oneline(f"{base_commit}..{target_ref}", cwd=repo)
    commits: list[UpstreamCommit] = []
    for entry in entries:
        parts = entry.split(" ", 1)
        sha = parts[0].strip()
        subject = parts[1].strip() if len(parts) > 1 else ""
        if sha:
            commits.append(UpstreamCommit(sha=sha, subject=subject))
    return tuple(commits)


def _detect_collisions(
    ctx: ExecutionContext,
    repo: str,
    base_commit: str,
    target_ref: str,
    upstream_commits: Sequence[UpstreamCommit],
    files_by_cl: Mapping[int, tuple[str, ...]],
) -> tuple[FileCollision, ...]:
    """Identifies files modified both in `<base_commit>..<target_ref>` and in open stack CLs."""
    if not base_commit or not target_ref or not upstream_commits:
        return ()
    upstream_files = set(
        ctx.git_service.diff_names(base_commit, target_ref, cwd=repo)
    )
    if not upstream_files:
        return ()
    commit_files_map: dict[str, set[str]] = {}
    for commit in upstream_commits:
        commit_files_map[commit.sha] = set(
            ctx.git_service.diff_names(commit.sha, cwd=repo)
        )
    cl_map: dict[str, list[int]] = {}
    for cl_num, paths in files_by_cl.items():
        for path in paths:
            if path in upstream_files:
                cl_map.setdefault(path, []).append(cl_num)
    collisions: list[FileCollision] = []
    for path in sorted(cl_map.keys()):
        matching_commits = tuple(
            c.sha for c in upstream_commits if path in commit_files_map.get(c.sha, set())
        )
        collisions.append(
            FileCollision(
                path=path,
                cl_numbers=tuple(sorted(cl_map[path])),
                upstream_commits=matching_commits,
            )
        )
    return tuple(collisions)


def _parse_conflict_paths(output: str) -> tuple[str, ...]:
    """Extracts conflicting file paths from `git merge-tree` or `git rebase` output."""
    paths: list[str] = []
    for line in output.splitlines():
        match = _CONFLICT_PATH_RE.search(line.strip())
        if match:
            path = match.group("path").strip()
            if path and path not in paths:
                paths.append(path)
    return tuple(paths)


@dataclass(frozen=True)
class StackSyncArgs:
    """Validated arguments for `StackSyncCommand`."""

    cl: str = "HEAD"
    topic: str = ""
    branch: str = ""
    onto: str = ""
    remote: str = ""
    repo: str = "."
    host: str = DEFAULT_GERRIT_HOST
    dry_run: bool = False
    no_push: bool = False
    no_fetch: bool = False
    presubmit: bool = False


class StackSyncService:
    """Executes collision detection, dry-run validation, stack rebasing, and Gerrit push."""

    def __init__(
        self,
        client: GerritClient,
        host: str,
        ctx: ExecutionContext,
    ) -> None:
        self._client = client
        self._host = host
        self._ctx = ctx
        self._status_service = StackStatusService(
            client, host, options=SYNC_QUERY_OPTIONS
        )

    @property
    def status_service(self) -> StackStatusService:
        """Returns the underlying `StackStatusService` used for chain discovery."""
        return self._status_service

    def resolve_remote(self, repo: str, requested_remote: str) -> str:
        """Selects the Git remote to fetch/push against."""
        if requested_remote:
            return requested_remote
        remotes = self._ctx.git_service.remotes(cwd=repo)
        for candidate in PREFERRED_REMOTES:
            if candidate in remotes:
                return candidate
        return remotes[0] if remotes else DEFAULT_REMOTE

    def sync_chains(
        self,
        chains: Sequence[CollectedChain],
        args: StackSyncArgs,
        *,
        local_branch: str = "",
    ) -> tuple[StackSyncSnapshot, ...]:
        """Runs collision detection, rebase validation/execution, and push for each chain."""
        remote = self.resolve_remote(args.repo, args.remote)
        fetched_branches: set[str] = set()
        snapshots: list[StackSyncSnapshot] = []
        for chain in chains:
            snapshots.append(
                self._sync_single_chain(
                    chain,
                    args,
                    remote=remote,
                    local_branch=local_branch,
                    fetched_branches=fetched_branches,
                )
            )
        return tuple(snapshots)

    def _sync_single_chain(
        self,
        chain: CollectedChain,
        args: StackSyncArgs,
        *,
        remote: str,
        local_branch: str,
        fetched_branches: set[str],
    ) -> StackSyncSnapshot:
        git = self._ctx.git_service
        tip_change = chain.details.get(chain.links[-1].number) or {}
        project = str(tip_change.get("project") or "")
        stack_branch = str(tip_change.get("branch") or "studio-main")
        topic = self._chain_topic(chain)
        if not args.no_fetch and stack_branch not in fetched_branches:
            git.fetch(remote, stack_branch, cwd=args.repo, check=False)
            fetched_branches.add(stack_branch)

        target_ref = args.onto or f"{remote}/{stack_branch}"
        target_commit = git.rev_parse(target_ref, cwd=args.repo, check=False) or target_ref
        base_commit = self._determine_base_commit(chain)
        upstream_commits = _collect_upstream_commits(
            self._ctx, args.repo, base_commit, target_ref
        )
        files_by_cl = self._collect_files_by_cl(chain, args.repo)
        collisions = _detect_collisions(
            self._ctx,
            args.repo,
            base_commit,
            target_ref,
            upstream_commits,
            files_by_cl,
        )
        collision_paths = {c.path for c in collisions}

        if args.dry_run:
            results = self._validate_chain_dry_run(
                chain,
                project=project,
                target_ref=target_ref,
                target_commit=target_commit,
                files_by_cl=files_by_cl,
                collision_paths=collision_paths,
                repo=args.repo,
            )
            return StackSyncSnapshot(
                project=project,
                branch=stack_branch,
                topic=topic,
                base_commit=base_commit,
                target_ref=target_ref,
                target_commit=target_commit,
                upstream_commits=upstream_commits,
                collisions=collisions,
                changes=results,
                pushed=False,
            )

        return self._execute_chain_rebase(
            chain,
            args=args,
            project=project,
            stack_branch=stack_branch,
            topic=topic,
            remote=remote,
            local_branch=local_branch,
            base_commit=base_commit,
            target_ref=target_ref,
            target_commit=target_commit,
            upstream_commits=upstream_commits,
            collisions=collisions,
            files_by_cl=files_by_cl,
            collision_paths=collision_paths,
        )

    @staticmethod
    def _chain_topic(chain: CollectedChain) -> str:
        for link in reversed(chain.links):
            change = chain.details.get(link.number) or {}
            topic = str(change.get("topic") or "").strip()
            if topic:
                return topic
        return ""

    @staticmethod
    def _determine_base_commit(chain: CollectedChain) -> str:
        for link in chain.links:
            if link.status == CHANGE_OPEN and link.parent_commit:
                return link.parent_commit
        return chain.links[0].parent_commit if chain.links else ""

    def _collect_files_by_cl(
        self, chain: CollectedChain, repo: str
    ) -> dict[int, tuple[str, ...]]:
        files_by_cl: dict[int, tuple[str, ...]] = {}
        for link in chain.links:
            if link.status != CHANGE_OPEN:
                continue
            change = chain.details.get(link.number) or {}
            rev_sha = str(change.get("current_revision") or link.commit)
            files_by_cl[link.number] = _extract_touched_files(
                change, rev_sha, link.parent_commit, self._ctx, repo
            )
        return files_by_cl

    def _closed_change_result(
        self,
        position: int,
        link: ChainLink,
        change: Mapping[str, Any],
        project: str,
    ) -> ChangeSyncResult:
        status = str(change.get("status") or link.status)
        rev_sha = str(change.get("current_revision") or link.commit)
        action = (
            SYNC_ACTION_MERGED
            if status == CHANGE_MERGED
            else SYNC_ACTION_ABANDONED
        )
        return ChangeSyncResult(
            position=position,
            number=link.number,
            change_id=link.change_id or str(change.get("change_id") or ""),
            subject=str(change.get("subject") or ""),
            status=status,
            url=_change_url(self._host, project, link.number),
            topic=str(change.get("topic") or ""),
            old_revision=rev_sha,
            new_revision=rev_sha,
            old_parent=link.parent_commit,
            new_parent=link.parent_commit,
            patch_set=link.current_patch_set,
            needs_rebase=False,
            clean_rebase=True,
            action=action,
            pushed=False,
            touched_files=(),
            colliding_files=(),
            conflicts=(),
            note=f"Skipped ({status.lower()})",
        )

    def _check_needs_rebase(
        self,
        link: ChainLink,
        parent_link: ChainLink | None,
        expected_parent_sha: str,
        mergeable_state: str,
        cascade: bool,
    ) -> bool:
        if cascade:
            return True
        if parent_link is not None and parent_link.status in (
            CHANGE_MERGED,
            CHANGE_ABANDONED,
        ):
            return link.parent_commit != expected_parent_sha
        if parent_link is not None and parent_link.patch_set < parent_link.current_patch_set:
            return True
        if mergeable_state in (MERGEABLE_CONFLICT, MERGEABLE_FALSE):
            return True
        return bool(
            expected_parent_sha and link.parent_commit != expected_parent_sha
        )

    def _validate_chain_dry_run(
        self,
        chain: CollectedChain,
        *,
        project: str,
        target_ref: str,
        target_commit: str,
        files_by_cl: Mapping[int, tuple[str, ...]],
        collision_paths: set[str],
        repo: str,
    ) -> tuple[ChangeSyncResult, ...]:
        results: list[ChangeSyncResult] = []
        expected_parent = target_commit
        cascade_rebase = False
        blocked_by: int = 0

        for idx, link in enumerate(chain.links):
            change = chain.details.get(link.number) or {}
            status = str(change.get("status") or link.status)
            if status != CHANGE_OPEN:
                results.append(
                    self._closed_change_result(idx + 1, link, change, project)
                )
                continue

            parent_link = chain.links[idx - 1] if idx > 0 else None
            rev_sha = str(change.get("current_revision") or link.commit)
            touched = files_by_cl.get(link.number, ())
            colliding = tuple(p for p in touched if p in collision_paths)
            mergeable_state, gerrit_conflicts = classify_mergeable(
                chain.mergeable.get(link.number)
            )
            needs_rebase = self._check_needs_rebase(
                link,
                parent_link,
                expected_parent,
                mergeable_state,
                cascade_rebase,
            )

            if blocked_by:
                results.append(
                    ChangeSyncResult(
                        position=idx + 1,
                        number=link.number,
                        change_id=link.change_id or str(change.get("change_id") or ""),
                        subject=str(change.get("subject") or ""),
                        status=status,
                        url=_change_url(self._host, project, link.number),
                        topic=str(change.get("topic") or ""),
                        old_revision=rev_sha,
                        new_revision=rev_sha,
                        old_parent=link.parent_commit,
                        new_parent=expected_parent,
                        patch_set=link.current_patch_set,
                        needs_rebase=True,
                        clean_rebase=False,
                        action=SYNC_ACTION_BLOCKED,
                        pushed=False,
                        touched_files=touched,
                        colliding_files=colliding,
                        conflicts=(),
                        note=f"Blocked by conflict in parent ag/{blocked_by}",
                    )
                )
                continue

            clean, conflicts, note = self._dry_run_check_single(
                rev_sha=rev_sha,
                parent_sha=link.parent_commit,
                expected_parent=expected_parent or target_ref,
                needs_rebase=needs_rebase,
                mergeable_state=mergeable_state,
                gerrit_conflicts=gerrit_conflicts,
                colliding=colliding,
                repo=repo,
            )
            if not clean:
                blocked_by = link.number
                action = SYNC_ACTION_CONFLICT
            elif needs_rebase:
                cascade_rebase = True
                action = SYNC_ACTION_WOULD_REBASE
            else:
                action = SYNC_ACTION_UP_TO_DATE

            results.append(
                ChangeSyncResult(
                    position=idx + 1,
                    number=link.number,
                    change_id=link.change_id or str(change.get("change_id") or ""),
                    subject=str(change.get("subject") or ""),
                    status=status,
                    url=_change_url(self._host, project, link.number),
                    topic=str(change.get("topic") or ""),
                    old_revision=rev_sha,
                    new_revision=rev_sha,
                    old_parent=link.parent_commit,
                    new_parent=expected_parent if needs_rebase else link.parent_commit,
                    patch_set=link.current_patch_set,
                    needs_rebase=needs_rebase,
                    clean_rebase=clean,
                    action=action,
                    pushed=False,
                    touched_files=touched,
                    colliding_files=colliding,
                    conflicts=conflicts,
                    note=note,
                )
            )
            expected_parent = rev_sha
        return tuple(results)

    def _dry_run_check_single(
        self,
        *,
        rev_sha: str,
        parent_sha: str,
        expected_parent: str,
        needs_rebase: bool,
        mergeable_state: str,
        gerrit_conflicts: tuple[str, ...],
        colliding: tuple[str, ...],
        repo: str,
    ) -> tuple[bool, tuple[str, ...], str]:
        if not needs_rebase:
            return True, (), "Already up to date"
        merge_res = self._ctx.git_service.merge_tree_check(
            expected_parent,
            rev_sha,
            merge_base=parent_sha or None,
            cwd=repo,
        )
        if merge_res.returncode != 0:
            parsed = _parse_conflict_paths(
                f"{merge_res.stdout}\n{merge_res.stderr}"
            )
            conflicts = parsed or gerrit_conflicts or colliding
            return (
                False,
                conflicts,
                f"Merge-tree detected conflict rebasing onto {_short_sha(expected_parent)}",
            )
        if mergeable_state == MERGEABLE_CONFLICT:
            conflicts = gerrit_conflicts or colliding
            return (
                False,
                conflicts,
                "Gerrit MergeableInfo reports merge conflict",
            )
        return (
            True,
            (),
            f"Cleanly rebases onto {_short_sha(expected_parent)}",
        )

    def _ensure_revision_present(
        self,
        number: int,
        patch_set: int,
        rev_sha: str,
        *,
        remote: str,
        repo: str,
        no_fetch: bool,
    ) -> None:
        git = self._ctx.git_service
        if git.rev_parse(f"{rev_sha}^{{commit}}", cwd=repo, verify=True, check=False):
            return
        if no_fetch:
            raise ExecutionError(
                f"Commit '{rev_sha}' for ag/{number} PS{patch_set} is not present in '{repo}' "
                "and --no-fetch was set.",
                remediation=f"Re-run without --no-fetch or run `git fetch {remote} {_gerrit_change_ref(number, patch_set)}`.",
            )
        fetch_res = git.fetch(
            remote, _gerrit_change_ref(number, patch_set), cwd=repo, check=False
        )
        if fetch_res.returncode != 0:
            detail = fetch_res.stderr.strip() or fetch_res.stdout.strip() or "unknown git fetch error"
            raise ExecutionError(
                f"Failed to fetch ag/{number} PS{patch_set} ({_gerrit_change_ref(number, patch_set)}) "
                f"from remote '{remote}': {detail}",
                remediation="Run `gcert`, verify remote connectivity, and retry.",
            )

    def _restore_initial_state(
        self, repo: str, initial_branch: str, initial_head: str
    ) -> None:
        git = self._ctx.git_service
        git.abort_rebase(cwd=repo)
        if initial_branch and initial_head:
            git.checkout_branch(
                initial_branch, initial_head, cwd=repo, force=True, check=False
            )
        elif initial_head:
            git.reset(initial_head, cwd=repo, mode="hard", check=False)

    def _execute_chain_rebase(
        self,
        chain: CollectedChain,
        *,
        args: StackSyncArgs,
        project: str,
        stack_branch: str,
        topic: str,
        remote: str,
        local_branch: str,
        base_commit: str,
        target_ref: str,
        target_commit: str,
        upstream_commits: tuple[UpstreamCommit, ...],
        collisions: tuple[FileCollision, ...],
        files_by_cl: Mapping[int, tuple[str, ...]],
        collision_paths: set[str],
    ) -> StackSyncSnapshot:
        git = self._ctx.git_service
        initial_branch = local_branch or git.current_branch(cwd=args.repo)
        initial_head = git.head_sha(cwd=args.repo)
        current_base = target_commit
        cascade_rebase = False
        blocked_by: int = 0
        raw_results: list[ChangeSyncResult] = []

        for idx, link in enumerate(chain.links):
            change = chain.details.get(link.number) or {}
            status = str(change.get("status") or link.status)
            if status != CHANGE_OPEN:
                raw_results.append(
                    self._closed_change_result(idx + 1, link, change, project)
                )
                continue

            parent_link = chain.links[idx - 1] if idx > 0 else None
            rev_sha = str(change.get("current_revision") or link.commit)
            change_id = link.change_id or str(change.get("change_id") or "")
            touched = files_by_cl.get(link.number, ())
            colliding = tuple(p for p in touched if p in collision_paths)
            mergeable_state, gerrit_conflicts = classify_mergeable(
                chain.mergeable.get(link.number)
            )
            needs_rebase = self._check_needs_rebase(
                link,
                parent_link,
                current_base,
                mergeable_state,
                cascade_rebase,
            )

            if blocked_by:
                raw_results.append(
                    ChangeSyncResult(
                        position=idx + 1,
                        number=link.number,
                        change_id=change_id,
                        subject=str(change.get("subject") or ""),
                        status=status,
                        url=_change_url(self._host, project, link.number),
                        topic=str(change.get("topic") or ""),
                        old_revision=rev_sha,
                        new_revision=rev_sha,
                        old_parent=link.parent_commit,
                        new_parent=current_base,
                        patch_set=link.current_patch_set,
                        needs_rebase=True,
                        clean_rebase=False,
                        action=SYNC_ACTION_BLOCKED,
                        pushed=False,
                        touched_files=touched,
                        colliding_files=colliding,
                        conflicts=(),
                        note=f"Blocked by rebase conflict in ag/{blocked_by}",
                    )
                )
                continue

            if not needs_rebase:
                raw_results.append(
                    ChangeSyncResult(
                        position=idx + 1,
                        number=link.number,
                        change_id=change_id,
                        subject=str(change.get("subject") or ""),
                        status=status,
                        url=_change_url(self._host, project, link.number),
                        topic=str(change.get("topic") or ""),
                        old_revision=rev_sha,
                        new_revision=rev_sha,
                        old_parent=link.parent_commit,
                        new_parent=link.parent_commit,
                        patch_set=link.current_patch_set,
                        needs_rebase=False,
                        clean_rebase=True,
                        action=SYNC_ACTION_UP_TO_DATE,
                        pushed=False,
                        touched_files=touched,
                        colliding_files=colliding,
                        conflicts=(),
                        note="Already up to date",
                    )
                )
                current_base = rev_sha
                continue

            self._ensure_revision_present(
                link.number,
                link.current_patch_set,
                rev_sha,
                remote=remote,
                repo=args.repo,
                no_fetch=args.no_fetch,
            )
            rebase_res = git.rebase_onto(
                current_base,
                link.parent_commit or f"{rev_sha}~1",
                rev_sha,
                cwd=args.repo,
                check=False,
            )
            if rebase_res.returncode != 0:
                parsed_conflicts = _parse_conflict_paths(
                    f"{rebase_res.stdout}\n{rebase_res.stderr}"
                )
                conflicts = parsed_conflicts or gerrit_conflicts or colliding
                self._restore_initial_state(
                    args.repo, initial_branch, initial_head
                )
                blocked_by = link.number
                raw_results.append(
                    ChangeSyncResult(
                        position=idx + 1,
                        number=link.number,
                        change_id=change_id,
                        subject=str(change.get("subject") or ""),
                        status=status,
                        url=_change_url(self._host, project, link.number),
                        topic=str(change.get("topic") or ""),
                        old_revision=rev_sha,
                        new_revision=rev_sha,
                        old_parent=link.parent_commit,
                        new_parent=current_base,
                        patch_set=link.current_patch_set,
                        needs_rebase=True,
                        clean_rebase=False,
                        action=SYNC_ACTION_CONFLICT,
                        pushed=False,
                        touched_files=touched,
                        colliding_files=colliding,
                        conflicts=conflicts,
                        note=f"Rebase onto {_short_sha(current_base)} failed with conflicts; aborted and restored {initial_branch or 'HEAD'}",
                    )
                )
                continue

            new_sha = self._preserve_change_id(args.repo, change_id)
            cascade_rebase = True
            raw_results.append(
                ChangeSyncResult(
                    position=idx + 1,
                    number=link.number,
                    change_id=change_id,
                    subject=str(change.get("subject") or ""),
                    status=status,
                    url=_change_url(self._host, project, link.number),
                    topic=str(change.get("topic") or ""),
                    old_revision=rev_sha,
                    new_revision=new_sha,
                    old_parent=link.parent_commit,
                    new_parent=current_base,
                    patch_set=link.current_patch_set,
                    needs_rebase=True,
                    clean_rebase=True,
                    action=SYNC_ACTION_REBASED,
                    pushed=False,
                    touched_files=touched,
                    colliding_files=colliding,
                    conflicts=(),
                    note=f"Rebased onto {_short_sha(current_base)} ({_short_sha(rev_sha)} -> {_short_sha(new_sha)})",
                )
            )
            current_base = new_sha

        if blocked_by:
            return StackSyncSnapshot(
                project=project,
                branch=stack_branch,
                topic=topic,
                base_commit=base_commit,
                target_ref=target_ref,
                target_commit=target_commit,
                upstream_commits=upstream_commits,
                collisions=collisions,
                changes=tuple(raw_results),
                pushed=False,
            )

        any_rebased = any(r.action == SYNC_ACTION_REBASED for r in raw_results)
        if any_rebased and initial_branch:
            git.checkout_branch(
                initial_branch, current_base, cwd=args.repo, force=True, check=False
            )

        pushed = False
        push_refspec = ""
        if any_rebased and not args.no_push:
            push_refspec = f"{current_base}:refs/for/{stack_branch}"
            self._push_rebased_stack(
                remote=remote,
                tip_sha=current_base,
                stack_branch=stack_branch,
                topic=topic,
                presubmit=args.presubmit,
                repo=args.repo,
            )
            pushed = True
            raw_results = [
                ChangeSyncResult(
                    **{**r.__dict__, "pushed": True}
                )
                if r.action == SYNC_ACTION_REBASED
                else r
                for r in raw_results
            ]

        return StackSyncSnapshot(
            project=project,
            branch=stack_branch,
            topic=topic,
            base_commit=base_commit,
            target_ref=target_ref,
            target_commit=target_commit,
            upstream_commits=upstream_commits,
            collisions=collisions,
            changes=tuple(raw_results),
            pushed=pushed,
            push_refspec=push_refspec,
        )

    def _preserve_change_id(self, repo: str, change_id: str) -> str:
        git = self._ctx.git_service
        msg = git.commit_message("HEAD", cwd=repo)
        updated_msg = _ensure_change_id_footer(msg, change_id)
        if updated_msg != msg:
            git.commit(updated_msg, cwd=repo, amend=True, check=False)
        return git.head_sha(cwd=repo)

    def _push_rebased_stack(
        self,
        *,
        remote: str,
        tip_sha: str,
        stack_branch: str,
        topic: str,
        presubmit: bool,
        repo: str,
    ) -> None:
        git = self._ctx.git_service
        git.set_config("push.negotiate", "false", cwd=repo, check=False)
        push_res = git.push_gerrit(
            remote,
            tip_sha,
            stack_branch,
            topic=topic or None,
            notify=NOTIFY_OWNER,
            presubmit_ready=presubmit,
            cwd=repo,
            check=False,
        )
        if push_res.returncode != 0:
            detail = (
                push_res.stderr.strip()
                or push_res.stdout.strip()
                or f"exit code {push_res.returncode}"
            )
            raise ExecutionError(
                f"Failed to push rebased stack tip '{_short_sha(tip_sha)}' to "
                f"'{remote}' ('refs/for/{stack_branch}'): {detail}",
                remediation=(
                    "Run `gcert`, verify Gerrit push permissions, or pass --no-push "
                    "to inspect the local rebased commits."
                ),
            )


def _render_collisions(stack: StackSyncSnapshot) -> list[str]:
    if not stack.collisions:
        return ["  collisions: none detected"]
    lines = [
        f"  collisions: {len(stack.collisions)} file overlap(s) with {stack.target_ref}"
    ]
    for col in stack.collisions[:MAX_DISPLAYED_COLLISION_FILES]:
        cls = ", ".join(f"ag/{n}" for n in col.cl_numbers)
        commits = (
            ", ".join(_short_sha(s) for s in col.upstream_commits[:3])
            or _short_sha(stack.target_commit)
        )
        lines.append(f"    - {col.path} (touched by {cls}; upstream: {commits})")
    remaining = len(stack.collisions) - MAX_DISPLAYED_COLLISION_FILES
    if remaining > 0:
        lines.append(f"    ... and {remaining} more colliding file(s)")
    return lines


def _render_change_sync(change: ChangeSyncResult, total: int) -> list[str]:
    pushed_tag = " [pushed notify=OWNER]" if change.pushed else ""
    lines = [
        f"  {change.position}/{total} ag/{change.number} PS{change.patch_set} · "
        f"{change.action}{pushed_tag} · {change.subject}",
        f"      {change.url}",
    ]
    if change.note:
        lines.append(f"      note: {change.note}")
    if change.colliding_files:
        listed = ", ".join(change.colliding_files[:MAX_DISPLAYED_COLLISION_FILES])
        lines.append(f"      overlapping upstream files: {listed}")
    if change.conflicts:
        listed = ", ".join(change.conflicts[:MAX_DISPLAYED_COLLISION_FILES])
        lines.append(f"      conflicts: {listed}")
    return lines


def render_stack_sync_report(report: StackSyncReport) -> str:
    """Renders the human-readable stack sync report."""
    mode_tag = " (DRY-RUN)" if report.dry_run else ""
    lines: list[str] = []
    for stack in report.stacks:
        lines.append(
            f"Stack Sync{mode_tag}: {stack.project} ({stack.branch}) -> {stack.target_ref} "
            f"({_short_sha(stack.target_commit)})"
        )
        lines.append(
            f"  base: {_short_sha(stack.base_commit)} · "
            f"upstream commits since base: {len(stack.upstream_commits)}"
        )
        for commit in stack.upstream_commits[:MAX_DISPLAYED_UPSTREAM_COMMITS]:
            lines.append(f"    * {_short_sha(commit.sha)} {commit.subject}")
        extra_commits = len(stack.upstream_commits) - MAX_DISPLAYED_UPSTREAM_COMMITS
        if extra_commits > 0:
            lines.append(f"    ... and {extra_commits} more upstream commit(s)")
        lines.extend(_render_collisions(stack))
        for change in stack.changes:
            lines.extend(_render_change_sync(change, len(stack.changes)))
        lines.append(f"  verdict: {stack.summary(dry_run=report.dry_run)}")
        lines.append("")
    return "\n".join(lines).rstrip()


class StackSyncCommand(Command[StackSyncArgs, StackSyncReport]):
    """Detects upstream collisions, validates rebases, and rebases/pushes a Gerrit CL stack."""

    name = "sync"
    help_text = (
        "Detect upstream collisions, rebase a CL stack onto target branch, "
        "and push updated patchsets with notify=OWNER"
    )

    def __init__(
        self, *, gerrit_client_factory: GerritClientBuilder | None = None
    ) -> None:
        self._gerrit_client_factory = gerrit_client_factory

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, ag/<id>, Change-Id, Gerrit URL, local branch, or HEAD (default: HEAD)",
        )
        parser.add_argument(
            "--topic",
            default="",
            help="Sync every relation chain in this Gerrit topic",
        )
        parser.add_argument(
            "--branch",
            default="",
            help="Local Git branch whose tip commit identifies the stack",
        )
        parser.add_argument(
            "--onto",
            default="",
            help="Target ref to rebase the stack onto (default: <remote>/<stack_branch>)",
        )
        parser.add_argument(
            "--remote",
            default="",
            help="Git remote name for fetching and pushing (default: auto-detected)",
        )
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git repository path (default: .)",
        )
        parser.add_argument(
            "--host",
            default=argparse.SUPPRESS,
            help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Check upstream collisions and mergeability without modifying refs or pushing",
        )
        parser.add_argument(
            "--no-push",
            action="store_true",
            help="Rebase commits locally without pushing new patchsets to Gerrit",
        )
        parser.add_argument(
            "--no-fetch",
            action="store_true",
            help="Skip fetching the latest target branch from the remote before syncing",
        )
        parser.add_argument(
            "--presubmit",
            action="store_true",
            help="Vote Presubmit-Ready +1 (-o l=Presubmit-Ready+1) when pushing to Gerrit",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON",
        )

    def validate(
        self, args: StackSyncArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> StackSyncArgs:
        if isinstance(args, StackSyncArgs):
            candidate = args
        else:
            candidate = StackSyncArgs(
                cl=str(getattr(args, "cl", "HEAD") or "HEAD").strip(),
                topic=str(getattr(args, "topic", "") or "").strip(),
                branch=str(getattr(args, "branch", "") or "").strip(),
                onto=str(getattr(args, "onto", "") or "").strip(),
                remote=str(getattr(args, "remote", "") or "").strip(),
                repo=str(getattr(args, "repo", ".") or "."),
                host=str(
                    getattr(args, "host", DEFAULT_GERRIT_HOST)
                    or DEFAULT_GERRIT_HOST
                ),
                dry_run=bool(getattr(args, "dry_run", False)),
                no_push=bool(getattr(args, "no_push", False)),
                no_fetch=bool(getattr(args, "no_fetch", False)),
                presubmit=bool(getattr(args, "presubmit", False)),
            )

        explicit_selectors = sum(
            [
                bool(candidate.topic),
                bool(candidate.branch),
                candidate.cl.upper() != "HEAD",
            ]
        )
        if explicit_selectors > 1:
            raise UsageError(
                f"Conflicting stack selectors were given (cl='{candidate.cl}', "
                f"topic='{candidate.topic}', branch='{candidate.branch}').",
                remediation="Pass only one of a positional CL/branch, --topic <topic>, or --branch <branch>.",
            )
        if not ctx.git_service.is_git_repo(cwd=candidate.repo):
            raise UsageError(
                f"Directory '{candidate.repo}' is not inside a Git work tree.",
                remediation="Run from a Git repository checkout or pass --repo <path>.",
            )
        if not candidate.dry_run and not ctx.git_service.is_clean(
            cwd=candidate.repo, untracked=False
        ):
            raise UsageError(
                f"Working tree in '{candidate.repo}' has uncommitted modifications.",
                remediation=(
                    "Commit or stash your local changes (`git stash`) before running "
                    "`gerrit stack sync`, or pass `--dry-run` to inspect collisions without rebasing."
                ),
            )
        return candidate

    def _create_client(self, ctx: ExecutionContext, host: str) -> GerritClient:
        if self._gerrit_client_factory is not None:
            return self._gerrit_client_factory(ctx, host)
        return GerritClient(host=host, runner=ctx.process_runner, prefer_cli=True)

    def _resolve_seeds(
        self,
        args: StackSyncArgs,
        service: StackStatusService,
        ctx: ExecutionContext,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Returns `(query_info, seed_changes, local_branch)` for the requested stack."""
        if args.topic:
            seeds = service.find_changes(f'topic:"{args.topic}"')
            if not seeds:
                raise UsageError(
                    f"No Gerrit changes were found for topic '{args.topic}' on '{args.host}'.",
                    remediation="Verify the topic name (topics are case-sensitive) or pass a CL number.",
                )
            return {"mode": "topic", "input": args.topic}, seeds, ""

        if args.branch:
            cl_id, _ = resolve_ref_change_id(ctx, args.repo, args.branch)
            seed = pick_seed_change(
                service.find_changes(f"change:{cl_id}"), cl_id, args.host
            )
            return (
                {"mode": "branch", "input": args.branch, "resolved": cl_id},
                [seed],
                args.branch,
            )

        if args.cl.upper() == "HEAD":
            cl_id, _ = resolve_ref_change_id(ctx, args.repo, "HEAD")
            seed = pick_seed_change(
                service.find_changes(f"change:{cl_id}"), cl_id, args.host
            )
            branch = ctx.git_service.current_branch(cwd=args.repo)
            return (
                {"mode": "head", "input": "HEAD", "resolved": cl_id},
                [seed],
                branch,
            )

        try:
            cl_id = parse_cl_identifier(args.cl, allow_head=False, strict=True)
            mode = "change"
            local_branch = ""
        except ValueError as exc:
            if ctx.git_service.rev_parse(
                args.cl, cwd=args.repo, verify=True, check=False
            ):
                cl_id, _ = resolve_ref_change_id(ctx, args.repo, args.cl)
                mode = "branch"
                local_branch = args.cl
            else:
                raise UsageError(
                    str(exc),
                    remediation="Pass a CL number, ag/<id>, Gerrit URL, Change-Id, local branch name, or --topic.",
                ) from exc

        seed = pick_seed_change(
            service.find_changes(f"change:{cl_id}"), cl_id, args.host
        )
        return (
            {"mode": mode, "input": args.cl, "resolved": cl_id},
            [seed],
            local_branch,
        )

    def execute(
        self, args: StackSyncArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[StackSyncReport]:
        validated = self.validate(args, ctx)
        client = self._create_client(ctx, validated.host)
        sync_service = StackSyncService(client, validated.host, ctx)
        try:
            query, seeds, local_branch = self._resolve_seeds(
                validated, sync_service.status_service, ctx
            )
            chains = sync_service.status_service.collect_chains(seeds)
            stacks = sync_service.sync_chains(
                chains, validated, local_branch=local_branch
            )
        except EmuCliError:
            raise
        except Exception as exc:
            target_desc = validated.topic or validated.branch or validated.cl
            raise ExecutionError(
                f"Failed to sync Gerrit stack for '{target_desc}' on '{validated.host}': {exc}",
                remediation="Run `gcert` to refresh credentials and verify the stack CLs exist.",
            ) from exc
        finally:
            client.close()

        query["host"] = validated.host
        any_pushed = any(s.pushed for s in stacks)
        report = StackSyncReport(
            host=validated.host,
            query=query,
            dry_run=validated.dry_run,
            pushed=any_pushed,
            stacks=stacks,
        )
        table = (
            None
            if ctx.config.json_mode
            else render_stack_sync_report(report)
        )
        if report.has_conflicts:
            return CommandResult.failure(
                report.summary,
                exit_code=1,
                data=report,
                action="gerrit stack sync",
                markdown_table=table,
                metadata={
                    "remediation": (
                        "Inspect the reported colliding files and rebase conflicts, resolve "
                        "them on the affected CL, and re-run `emu-dev-cli gerrit stack sync`."
                    )
                },
            )
        return CommandResult.success(
            report,
            action="gerrit stack sync",
            summary=report.summary,
            markdown_table=table,
        )
