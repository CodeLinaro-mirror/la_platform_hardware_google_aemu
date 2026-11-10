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

"""Git-Native Transaction & Checkpointing Manager for emu-dev-cli tidy."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.git import GitService, ProdGitService
from core.process import ProcessRunner, SubprocessRunner

# Git trailer keys used for stateless crash recovery
TRAILER_TRANSACTION = "Tidy-Transaction"
TRAILER_ANCHOR = "Tidy-Anchor"
TRAILER_LEVEL = "Tidy-Level"
TRAILER_SYMBOL_OLD = "Tidy-Symbol-Old"
TRAILER_SYMBOL_NEW = "Tidy-Symbol-New"
TRAILER_STATUS = "Tidy-Status"


def resolve_git_repo_for_target(
    target: str,
    root_hint: Optional[Path] = None,
    *,
    git_service: Optional[GitService] = None,
    process_runner: Optional[ProcessRunner] = None,
) -> Path:
    """Resolves the Git repository directory corresponding to a Bazel target or workspace."""
    root = (root_hint or Path.cwd()).resolve()
    git = git_service or ProdGitService(process_runner)
    runner = process_runner or (
        git.process_runner if isinstance(git, ProdGitService) else SubprocessRunner()
    )

    if target.startswith("@goldfish//"):
        goldfish_dir = root / "hardware" / "generic" / "goldfish"
        if (goldfish_dir / ".git").exists():
            return goldfish_dir

    # Check if root itself is inside a git repo
    top = git.top_level(cwd=root)
    if top is not None:
        return top

    # Try resolving the target package path via bazel query --output=location
    base_target = (
        target[:-5]
        if target.endswith("_tidy")
        else (target[:-5] if target.endswith(":tidy") else target)
    )
    res = runner.run(
        ["bazel", "query", "--output=location", base_target],
        cwd=root,
        check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        first_line = res.stdout.strip().splitlines()[0]
        build_path = Path(first_line.split(":")[0]).resolve()
        curr = build_path.parent if build_path.is_file() else build_path
        while curr != curr.parent:
            if (curr / ".git").exists():
                return curr
            curr = curr.parent

    # Fallback: check common Android Emulator subprojects under superproject root
    for sub in [
        "hardware/generic/goldfish",
        "hardware/google/aemu",
        "external/qemu",
    ]:
        sub_dir = root / sub
        if (sub_dir / ".git").exists():
            return sub_dir

    return root


@dataclass
class ActiveTransaction:
    """Represents an in-progress refactoring transaction discovered from Git history."""

    target: str
    anchor_commit: str
    level: int
    latest_symbol_old: Optional[str] = None
    latest_symbol_new: Optional[str] = None
    status: str = "IN_PROGRESS"


class GitTransactionContext:
    """Manages atomic Git commits, trailers, and rollback for clang-tidy refactorings."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        *,
        target: Optional[str] = None,
        git_service: Optional[GitService] = None,
    ):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self._git: GitService = git_service or ProdGitService(
            default_cwd=self.workspace_root
        )
        self._anchor_commit: Optional[str] = None
        self._target: Optional[str] = target
        self._level: Optional[int] = None
        self._step_commits: List[str] = []

    @property
    def git_service(self) -> GitService:
        """Returns the underlying `GitService`."""
        return self._git

    @property
    def anchor_commit(self) -> Optional[str]:
        return self._anchor_commit

    def _run_git(self, args: List[str], check: bool = True) -> str:
        """Executes a git command via GitService and returns stripped stdout."""
        result = self._git.run_git(args, cwd=self.workspace_root, check=check)
        return result.stdout.strip()

    def is_tree_clean(self) -> bool:
        """Checks if the working tree has uncommitted changes to tracked files."""
        return self._git.is_clean(cwd=self.workspace_root, untracked=False)

    def get_current_head(self) -> str:
        """Returns the full SHA-1 hash of HEAD."""
        return self._git.head_sha(cwd=self.workspace_root, check=True)

    def start_transaction(
        self, target: str, level: int, force: bool = False
    ) -> str:
        """Begins a refactoring transaction and records the anchor commit."""
        if not force and not self.is_tree_clean():
            raise RuntimeError(
                "Working tree has uncommitted changes. Commit or stash them before running tidy fix, or pass --force."
            )
        self._target = target
        self._level = level
        self._anchor_commit = self.get_current_head()
        self._step_commits.clear()
        return self._anchor_commit

    def commit_step(
        self,
        symbol_old: str,
        symbol_new: str,
        message: str,
        status: str = "IN_PROGRESS",
    ) -> Optional[str]:
        """Stages all modified tracked files and creates an atomic step commit with trailers."""
        if not self._anchor_commit or not self._target or self._level is None:
            raise RuntimeError("Transaction has not been started.")

        self._git.add(
            cwd=self.workspace_root, update_only=True, check=True
        )

        status_out = self._git.status_porcelain(
            cwd=self.workspace_root, untracked=False
        )
        if not status_out.strip():
            return None

        trailers = [
            f"{TRAILER_TRANSACTION}: {self._target}",
            f"{TRAILER_ANCHOR}: {self._anchor_commit}",
            f"{TRAILER_LEVEL}: {self._level}",
            f"{TRAILER_SYMBOL_OLD}: {symbol_old}",
            f"{TRAILER_SYMBOL_NEW}: {symbol_new}",
            f"{TRAILER_STATUS}: {status}",
        ]
        full_msg = f"{message.strip()}\n\n" + "\n".join(trailers)
        self._git.commit(full_msg, cwd=self.workspace_root, check=True)
        commit_hash = self.get_current_head()
        self._step_commits.append(commit_hash)
        return commit_hash

    def rollback_last_step(self) -> None:
        """Rolls back the most recent step or working tree changes."""
        if not self.is_tree_clean():
            self._git.reset("HEAD", cwd=self.workspace_root, mode="hard")
        elif self._step_commits:
            self._git.reset("HEAD~1", cwd=self.workspace_root, mode="hard")
            self._step_commits.pop()

    def rollback_to_anchor(self) -> None:
        """Aborts the entire transaction and resets hard to the anchor commit."""
        if not self._anchor_commit:
            raise RuntimeError("No anchor commit recorded to rollback to.")
        self._git.reset(
            self._anchor_commit, cwd=self.workspace_root, mode="hard"
        )
        self._step_commits.clear()

    def finalize_and_squash(self, final_commit_msg: str) -> Optional[str]:
        """Squashes all intermediate step commits since anchor into a single clean commit."""
        if not self._anchor_commit:
            raise RuntimeError("No anchor commit recorded.")

        current_head = self.get_current_head()
        if current_head == self._anchor_commit and self.is_tree_clean():
            return None

        self._git.reset(
            self._anchor_commit, cwd=self.workspace_root, mode="soft"
        )
        self._git.add(
            cwd=self.workspace_root, update_only=True, check=True
        )
        self._git.commit(
            final_commit_msg.strip(), cwd=self.workspace_root, check=True
        )
        self._step_commits.clear()
        return self.get_current_head()

    @classmethod
    def get_active_transaction(
        cls,
        workspace_root: Optional[Path] = None,
        *,
        git_service: Optional[GitService] = None,
    ) -> Optional[ActiveTransaction]:
        """Inspects HEAD commit trailers to see if an interrupted transaction is active."""
        root = (workspace_root or Path.cwd()).resolve()
        git = git_service or ProdGitService(default_cwd=root)
        res = git.run_git(
            ["log", "-1", "--format=%B", "HEAD"], cwd=root, check=False
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None

        trailers: Dict[str, str] = {}
        for line in res.stdout.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                if key.startswith("Tidy-"):
                    trailers[key] = val.strip()

        if TRAILER_TRANSACTION not in trailers or TRAILER_ANCHOR not in trailers:
            return None

        status = trailers.get(TRAILER_STATUS, "IN_PROGRESS")
        if status != "IN_PROGRESS":
            return None

        return ActiveTransaction(
            target=trailers[TRAILER_TRANSACTION],
            anchor_commit=trailers[TRAILER_ANCHOR],
            level=int(trailers.get(TRAILER_LEVEL, "1")),
            latest_symbol_old=trailers.get(TRAILER_SYMBOL_OLD),
            latest_symbol_new=trailers.get(TRAILER_SYMBOL_NEW),
            status=status,
        )
