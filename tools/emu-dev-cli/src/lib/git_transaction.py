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

"""Git-native transaction manager for automated progressive refactoring.

Uses Git commit trailers (Tidy-Transaction, Tidy-Anchor, Tidy-Level, Tidy-Symbol)
to store transactional state directly in the Git log without creating untracked files.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
from typing import Dict, List, Optional

from commands.source_directory import get_all_source_directories, get_source_directory

logger = logging.getLogger(__name__)

TRAILER_TRANSACTION = "Tidy-Transaction"
TRAILER_ANCHOR = "Tidy-Anchor"
TRAILER_LEVEL = "Tidy-Level"
TRAILER_SYMBOL_OLD = "Tidy-Symbol-Old"
TRAILER_SYMBOL_NEW = "Tidy-Symbol-New"
TRAILER_STATUS = "Tidy-Status"


@dataclass
class ActiveTransaction:
    anchor_commit: str
    target: str
    level: int
    latest_symbol_old: Optional[str] = None
    latest_symbol_new: Optional[str] = None
    status: str = "IN_PROGRESS"


def resolve_git_repo_for_target(
    target: Optional[str], root_hint: Optional[Path] = None
) -> Path:
    """Resolves the git repository root for a given Bazel target in repo superprojects or standalone sub-repos."""
    base = root_hint or Path(get_source_directory("emu-main-next") or os.getcwd())

    # 1. Resolve top-level Git root for base by checking .git upwards
    git_root = base
    curr = base.resolve()
    while curr != curr.parent:
        if (curr / ".git").exists():
            git_root = curr
            break
        curr = curr.parent

    if git_root == base:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=base,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                git_root = Path(res.stdout.strip())
        except Exception:
            git_root = base

    if not target:
        return git_root

    # 2. Map Bazel target natively via bazel query --output=location
    try:
        res = subprocess.run(
            ["bazel", "query", "--output=location", target],
            cwd=base,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            location = res.stdout.strip().split(":")[0]
            curr = Path(location).resolve().parent
            while curr != curr.parent:
                if (curr / ".git").exists():
                    return curr
                curr = curr.parent
    except Exception:
        pass

    return git_root


class GitTransactionContext:
    """Manages Git-native transactions with anchor tracking, step commits, and squashing."""

    def __init__(
        self, workspace_root: Optional[Path] = None, target: Optional[str] = None
    ) -> None:
        self.workspace_root = resolve_git_repo_for_target(
            target, root_hint=workspace_root
        )
        self._anchor_commit: Optional[str] = None
        self._target: Optional[str] = target
        self._level: int = 1
        self._step_commits: List[str] = []

    @property
    def anchor_commit(self) -> Optional[str]:
        return self._anchor_commit

    def is_tree_clean(self, ignore_untracked: bool = False) -> bool:
        """Returns True if the working tree has no uncommitted changes."""
        cmd = ["git", "status", "--porcelain"]
        if ignore_untracked:
            cmd.append("-uno")
        res = subprocess.run(
            cmd,
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return not bool(res.stdout.strip())

    def start_transaction(
        self, target: str, level: int = 1, force: bool = False
    ) -> str:
        """Records the baseline clean commit hash as the transaction anchor."""
        if not force and not self.is_tree_clean():
            raise RuntimeError(
                "Uncommitted changes detected in repository. Commit or stash them before starting a transaction."
            )

        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True,
        )
        self._anchor_commit = res.stdout.strip()
        self._target = target
        self._level = level
        self._step_commits = []
        logger.info(
            "Started Git transaction with anchor %s for target %s (level=%d)",
            self._anchor_commit,
            target,
            level,
        )
        return self._anchor_commit

    def commit_step(
        self,
        symbol_old: str,
        symbol_new: str,
        message: Optional[str] = None,
        status: str = "IN_PROGRESS",
    ) -> str:
        """Commits an individual verified refactoring step with structured Git trailers."""
        if not self._anchor_commit or not self._target:
            raise RuntimeError("Cannot commit step: No active transaction anchor.")

        if self.is_tree_clean(ignore_untracked=True):
            logger.info(
                "Working tree already clean for symbol %s -> %s. Skipping duplicate commit.",
                symbol_old,
                symbol_new,
            )
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()

        desc = message or f"refactor: Rename '{symbol_old}' to '{symbol_new}'"
        commit_msg = (
            f"{desc}\n\n"
            f"{TRAILER_TRANSACTION}: {self._target}\n"
            f"{TRAILER_ANCHOR}: {self._anchor_commit}\n"
            f"{TRAILER_LEVEL}: {self._level}\n"
            f"{TRAILER_SYMBOL_OLD}: {symbol_old}\n"
            f"{TRAILER_SYMBOL_NEW}: {symbol_new}\n"
            f"{TRAILER_STATUS}: {status}\n"
        )

        subprocess.run(["git", "add", "-u"], cwd=self.workspace_root, check=True)
        subprocess.run(
            ["git", "commit", "-m", commit_msg], cwd=self.workspace_root, check=True
        )

        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_hash = res.stdout.strip()
        self._step_commits.append(commit_hash)
        logger.info(
            "Committed step %s for symbol %s -> %s",
            commit_hash[:8],
            symbol_old,
            symbol_new,
        )
        return commit_hash

    def rollback_step(self) -> None:
        """Discards uncommitted working tree changes and rolls back to the latest clean commit."""
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"], cwd=self.workspace_root, check=True
        )
        logger.warning("Rolled back uncommitted changes to HEAD.")

    def abort_transaction(self, anchor_commit: Optional[str] = None) -> None:
        """Aborts the entire active transaction and rolls back to the anchor commit."""
        anchor = anchor_commit or self._anchor_commit
        if not anchor:
            active = self.get_active_transaction(workspace_root=self.workspace_root)
            if active:
                anchor = active.anchor_commit

        if not anchor:
            raise RuntimeError("No active transaction anchor found to abort.")

        subprocess.run(
            ["git", "reset", "--hard", anchor], cwd=self.workspace_root, check=True
        )
        self._anchor_commit = None
        self._step_commits = []
        logger.info(
            "Successfully aborted transaction and restored working tree to anchor %s",
            anchor,
        )

    def finalize_and_squash(
        self, final_commit_msg: str, anchor_commit: Optional[str] = None
    ) -> str:
        """Soft-resets all intermediate step commits back to the anchor and commits the final atomic changelist."""
        anchor = anchor_commit or self._anchor_commit
        if not anchor:
            active = self.get_active_transaction(workspace_root=self.workspace_root)
            if active:
                anchor = active.anchor_commit

        if not anchor:
            raise RuntimeError("No active transaction anchor found to finalize.")

        # Check if HEAD is ahead of anchor
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        if current_head == anchor:
            logger.info("HEAD is already at anchor %s. Nothing to squash.", anchor)
            return anchor

        subprocess.run(
            ["git", "reset", "--soft", anchor], cwd=self.workspace_root, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", final_commit_msg],
            cwd=self.workspace_root,
            check=True,
        )

        final_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        logger.info(
            "Successfully finalized and squashed transaction to atomic commit %s",
            final_hash,
        )
        return final_hash

    @classmethod
    def get_active_transaction(
        cls, workspace_root: Optional[Path] = None
    ) -> Optional[ActiveTransaction]:
        """Inspects the latest git commit for active Tidy-Transaction trailers."""
        root = workspace_root or Path.cwd()
        res = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%B"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return None

        body = res.stdout
        if TRAILER_TRANSACTION not in body or TRAILER_ANCHOR not in body:
            return None

        trailers: Dict[str, str] = {}
        for line in body.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                trailers[key.strip()] = val.strip()

        anchor = trailers.get(TRAILER_ANCHOR)
        target = trailers.get(TRAILER_TRANSACTION)
        if not anchor or not target:
            return None

        try:
            level = int(trailers.get(TRAILER_LEVEL, "1"))
        except ValueError:
            level = 1

        return ActiveTransaction(
            anchor_commit=anchor,
            target=target,
            level=level,
            latest_symbol_old=trailers.get(TRAILER_SYMBOL_OLD),
            latest_symbol_new=trailers.get(TRAILER_SYMBOL_NEW),
            status=trailers.get(TRAILER_STATUS, "IN_PROGRESS"),
        )
