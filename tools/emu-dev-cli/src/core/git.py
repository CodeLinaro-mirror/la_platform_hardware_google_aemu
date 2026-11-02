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

"""GitService abstraction and hermetic FakeGitService over ProcessRunner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Optional

from core.errors import SubprocessError
from core.process import (
    FakeProcessRunner,
    ProcessResult,
    ProcessRunner,
    SubprocessRunner,
)

DEFAULT_GIT_TIMEOUT_SEC: float = 30.0
DEFAULT_GIT_FETCH_TIMEOUT_SEC: float = 120.0
DEFAULT_GIT_REBASE_TIMEOUT_SEC: float = 120.0
DEFAULT_GIT_PUSH_TIMEOUT_SEC: float = 120.0


@dataclass(frozen=True)
class GitCommitInfo:
    """Immutable snapshot of a Git commit for inspection and hermetic testing."""

    sha: str
    message: str
    parents: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()

    @property
    def subject(self) -> str:
        """Returns the first line of the commit message."""
        for line in self.message.splitlines():
            return line.strip()
        return ""


class GitService(ABC):
    """Abstract interface for repository Git operations."""

    @abstractmethod
    def run_git(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        check: bool = False,
        timeout_sec: float | None = DEFAULT_GIT_TIMEOUT_SEC,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        """Executes `git <args>` and returns a `ProcessResult`."""

    def is_git_repo(self, cwd: Path | str | None = None) -> bool:
        """Returns True if `cwd` is inside a Git work tree."""
        res = self.run_git(
            ["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False
        )
        return res.returncode == 0 and res.stdout.strip() == "true"

    def top_level(self, cwd: Path | str | None = None) -> Optional[Path]:
        """Returns the repository root `Path`, or `None` if not in a Git repo."""
        res = self.run_git(
            ["rev-parse", "--show-toplevel"], cwd=cwd, check=False
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None
        return Path(res.stdout.strip()).resolve()

    def status_porcelain(
        self, cwd: Path | str | None = None, *, untracked: bool = True
    ) -> str:
        """Returns `git status --porcelain` output."""
        cmd = ["status", "--porcelain"]
        if not untracked:
            cmd.append("-uno")
        res = self.run_git(cmd, cwd=cwd, check=False)
        return res.stdout if res.returncode == 0 else ""

    def is_clean(
        self, cwd: Path | str | None = None, *, untracked: bool = True
    ) -> bool:
        """Returns True when the working tree has no modified (or untracked) files."""
        cmd = ["status", "--porcelain"]
        if not untracked:
            cmd.append("-uno")
        res = self.run_git(cmd, cwd=cwd, check=False)
        return res.returncode == 0 and not res.stdout.strip()

    def rev_parse(
        self,
        ref: str = "HEAD",
        *,
        cwd: Path | str | None = None,
        verify: bool = False,
        check: bool = False,
    ) -> str:
        """Resolves `ref` to its full SHA-1 hash (or empty string on failure when `check=False`)."""
        cmd = ["rev-parse"]
        if verify:
            cmd.append("--verify")
        cmd.append(ref)
        res = self.run_git(cmd, cwd=cwd, check=check)
        return res.stdout.strip() if res.returncode == 0 else ""

    def head_sha(
        self, cwd: Path | str | None = None, *, check: bool = False
    ) -> str:
        """Returns the current `HEAD` commit hash."""
        return self.rev_parse("HEAD", cwd=cwd, check=check)

    def commit_message(
        self, ref: str = "HEAD", *, cwd: Path | str | None = None
    ) -> str:
        """Returns the full commit message body (`%B`) for `ref`."""
        res = self.run_git(
            ["log", "-1", "--format=%B", ref], cwd=cwd, check=False
        )
        return res.stdout.strip() if res.returncode == 0 else ""

    def commit_subject(
        self, ref: str = "HEAD", *, cwd: Path | str | None = None
    ) -> str:
        """Returns the single-line commit subject (`%s`) for `ref`."""
        res = self.run_git(
            ["log", "-1", "--format=%s", ref], cwd=cwd, check=False
        )
        return res.stdout.strip() if res.returncode == 0 else ""

    def commit_parents(
        self, ref: str = "HEAD", *, cwd: Path | str | None = None
    ) -> list[str]:
        """Returns the parent commit SHAs of `ref`."""
        res = self.run_git(
            ["rev-list", "--parents", "-n", "1", ref], cwd=cwd, check=False
        )
        if res.returncode != 0 or not res.stdout.strip():
            return []
        tokens = res.stdout.strip().split()
        return tokens[1:] if len(tokens) > 1 else []

    def diff_names(
        self,
        base_ref: str = "HEAD",
        target_ref: str | None = None,
        *,
        cwd: Path | str | None = None,
        cached: bool = False,
    ) -> list[str]:
        """Returns the list of file paths modified between refs or in the index."""
        cmd = ["diff", "--name-only"]
        if cached:
            cmd.append("--cached")
        if base_ref:
            cmd.append(base_ref)
        if target_ref:
            cmd.append(target_ref)
        res = self.run_git(cmd, cwd=cwd, check=False)
        if res.returncode != 0:
            return []
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def add(
        self,
        paths: Sequence[str] | None = None,
        *,
        cwd: Path | str | None = None,
        update_only: bool = False,
        all_files: bool = False,
        check: bool = True,
    ) -> ProcessResult:
        """Stages files in the Git index."""
        cmd = ["add"]
        if update_only:
            cmd.append("-u")
        elif all_files:
            cmd.append("-A")
        if paths:
            cmd.extend(paths)
        return self.run_git(cmd, cwd=cwd, check=check)

    def commit(
        self,
        message: str | None = None,
        *,
        message_file: Path | str | None = None,
        cwd: Path | str | None = None,
        amend: bool = False,
        no_edit: bool = False,
        allow_empty: bool = False,
        check: bool = True,
    ) -> ProcessResult:
        """Creates or amends a Git commit."""
        cmd = ["commit"]
        if amend:
            cmd.append("--amend")
        if no_edit:
            cmd.append("--no-edit")
        if allow_empty:
            cmd.append("--allow-empty")
        if message is not None:
            cmd.extend(["-m", message])
        if message_file is not None:
            cmd.extend(["-F", str(message_file)])
        return self.run_git(cmd, cwd=cwd, check=check)

    def reset(
        self,
        ref: str = "HEAD",
        *,
        cwd: Path | str | None = None,
        mode: str = "hard",
        check: bool = True,
    ) -> ProcessResult:
        """Resets the current HEAD to `ref` using `--hard`, `--soft`, or `--mixed`."""
        if mode not in ("hard", "soft", "mixed"):
            raise ValueError(f"Unsupported git reset mode: {mode}")
        return self.run_git(
            ["reset", f"--{mode}", ref], cwd=cwd, check=check
        )

    def log_oneline(
        self, revision_range: str, *, cwd: Path | str | None = None
    ) -> list[str]:
        """Returns `--oneline` log entries for `revision_range`."""
        res = self.run_git(
            ["log", "--oneline", revision_range], cwd=cwd, check=False
        )
        if res.returncode != 0:
            return []
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def find_commit_by_grep(
        self,
        pattern: str,
        *,
        cwd: Path | str | None = None,
        revision_range: str | None = None,
        max_count: int = 1,
    ) -> str:
        """Finds the latest commit SHA whose commit message matches `pattern`."""
        cmd = [
            "log",
            f"--grep={pattern}",
            "-n",
            str(max_count),
            "--format=%H",
        ]
        if revision_range:
            cmd.append(revision_range)
        res = self.run_git(cmd, cwd=cwd, check=False)
        return res.stdout.strip() if res.returncode == 0 else ""

    def is_ancestor(
        self,
        ancestor_ref: str,
        descendant_ref: str = "HEAD",
        *,
        cwd: Path | str | None = None,
    ) -> bool:
        """Returns True if `ancestor_ref` is an ancestor of `descendant_ref`."""
        res = self.run_git(
            ["merge-base", "--is-ancestor", ancestor_ref, descendant_ref],
            cwd=cwd,
            check=False,
        )
        return res.returncode == 0

    def remotes(self, cwd: Path | str | None = None) -> list[str]:
        """Returns configured Git remote names."""
        res = self.run_git(["remote"], cwd=cwd, check=False)
        if res.returncode != 0:
            return []
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def fetch(
        self,
        remote: str,
        refspec: str,
        *,
        cwd: Path | str | None = None,
        timeout_sec: float = DEFAULT_GIT_FETCH_TIMEOUT_SEC,
        check: bool = False,
    ) -> ProcessResult:
        """Fetches `refspec` from `remote`."""
        return self.run_git(
            ["fetch", remote, refspec],
            cwd=cwd,
            check=check,
            timeout_sec=timeout_sec,
        )

    def cherry_pick(
        self,
        ref: str = "FETCH_HEAD",
        *,
        cwd: Path | str | None = None,
        timeout_sec: float = 60.0,
        check: bool = False,
    ) -> ProcessResult:
        """Cherry-picks `ref` onto the current HEAD."""
        return self.run_git(
            ["cherry-pick", ref],
            cwd=cwd,
            check=check,
            timeout_sec=timeout_sec,
        )

    def abort_cherry_pick(
        self, cwd: Path | str | None = None
    ) -> ProcessResult:
        """Aborts an in-progress cherry-pick."""
        return self.run_git(["cherry-pick", "--abort"], cwd=cwd, check=False)

    def checkout_branch(
        self,
        branch: str,
        start_point: str = "FETCH_HEAD",
        *,
        cwd: Path | str | None = None,
        force: bool = True,
        check: bool = False,
    ) -> ProcessResult:
        """Creates or resets `branch` at `start_point` (`git checkout -B`)."""
        flag = "-B" if force else "-b"
        return self.run_git(
            ["checkout", flag, branch, start_point], cwd=cwd, check=check
        )

    def set_upstream(
        self, upstream: str, *, cwd: Path | str | None = None
    ) -> ProcessResult:
        """Configures the upstream tracking branch for the current branch."""
        return self.run_git(
            ["branch", f"--set-upstream-to={upstream}"],
            cwd=cwd,
            check=False,
        )

    def set_config(
        self,
        key: str,
        value: str,
        *,
        cwd: Path | str | None = None,
        check: bool = True,
    ) -> ProcessResult:
        """Sets a local repository `git config` key."""
        return self.run_git(["config", key, value], cwd=cwd, check=check)

    def get_config(
        self, key: str, *, cwd: Path | str | None = None
    ) -> str:
        """Reads a repository `git config --get` value."""
        res = self.run_git(["config", "--get", key], cwd=cwd, check=False)
        return res.stdout.strip() if res.returncode == 0 else ""

    def current_branch(self, cwd: Path | str | None = None) -> str:
        """Returns the current checked-out branch name, or empty string if detached."""
        res = self.run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, check=False
        )
        if res.returncode != 0:
            return ""
        name = res.stdout.strip()
        return "" if name == "HEAD" else name

    def merge_tree_check(
        self,
        base_ref: str,
        branch_ref: str,
        *,
        merge_base: str | None = None,
        cwd: Path | str | None = None,
    ) -> ProcessResult:
        """Performs a non-destructive 3-way merge check via `git merge-tree --write-tree`."""
        cmd = ["merge-tree", "--write-tree"]
        if merge_base:
            cmd.append(f"--merge-base={merge_base}")
        cmd.extend([base_ref, branch_ref])
        return self.run_git(cmd, cwd=cwd, check=False)

    def rebase_onto(
        self,
        new_base: str,
        upstream: str,
        branch: str | None = None,
        *,
        cwd: Path | str | None = None,
        timeout_sec: float = DEFAULT_GIT_REBASE_TIMEOUT_SEC,
        check: bool = False,
    ) -> ProcessResult:
        """Rebases commits from `upstream..branch` onto `new_base`."""
        cmd = ["rebase", "--onto", new_base, upstream]
        if branch:
            cmd.append(branch)
        return self.run_git(
            cmd, cwd=cwd, check=check, timeout_sec=timeout_sec
        )

    def abort_rebase(self, cwd: Path | str | None = None) -> ProcessResult:
        """Aborts an in-progress `git rebase`."""
        return self.run_git(["rebase", "--abort"], cwd=cwd, check=False)

    def push_gerrit(
        self,
        remote: str,
        ref: str,
        target_branch: str,
        *,
        topic: str | None = None,
        notify: str = "OWNER",
        presubmit_ready: bool = False,
        extra_options: Sequence[str] = (),
        cwd: Path | str | None = None,
        timeout_sec: float = DEFAULT_GIT_PUSH_TIMEOUT_SEC,
        check: bool = False,
    ) -> ProcessResult:
        """Pushes `ref` to `refs/for/<target_branch>` with Gerrit push options."""
        cmd = ["push", remote, f"{ref}:refs/for/{target_branch}"]
        options: list[str] = []
        if notify:
            options.append(f"notify={notify}")
        options.append("nokeycheck")
        if topic:
            options.append(f"topic={topic}")
        if presubmit_ready:
            options.append("l=Presubmit-Ready+1")
        for opt in extra_options:
            if opt and opt not in options:
                options.append(opt)
        for opt in options:
            cmd.extend(["-o", opt])
        return self.run_git(
            cmd, cwd=cwd, check=check, timeout_sec=timeout_sec
        )


class ProdGitService(GitService):
    """Production `GitService` backed by a `ProcessRunner`."""

    def __init__(
        self,
        process_runner: ProcessRunner | None = None,
        *,
        default_cwd: Path | str | None = None,
    ) -> None:
        self._runner: ProcessRunner = process_runner or SubprocessRunner()
        self._default_cwd: Path | str | None = default_cwd

    @property
    def process_runner(self) -> ProcessRunner:
        """Returns the underlying `ProcessRunner`."""
        return self._runner

    def run_git(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        check: bool = False,
        timeout_sec: float | None = DEFAULT_GIT_TIMEOUT_SEC,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        target_cwd = cwd if cwd is not None else self._default_cwd
        return self._runner.run(
            ["git", *args],
            cwd=target_cwd,
            env=env,
            timeout_sec=timeout_sec,
            check=check,
        )


@dataclass
class FakeGitService(GitService):
    """Hermetic in-memory `GitService` test double with zero subprocess execution."""

    inside_work_tree: bool = True
    repo_root: Path = field(default_factory=lambda: Path("/fake/repo"))
    status_output: str = ""
    commits: list[GitCommitInfo] = field(default_factory=list)
    refs: dict[str, str] = field(default_factory=dict)
    remotes_list: list[str] = field(default_factory=lambda: ["aosp"])
    config_values: dict[str, str] = field(default_factory=dict)
    ancestors: set[tuple[str, str]] = field(default_factory=set)
    staged_files: list[str] = field(default_factory=list)
    diff_files: dict[str, list[str]] = field(default_factory=dict)
    current_branch_name: str = "studio-main"
    rebase_conflicts: set[str] = field(default_factory=set)
    merge_tree_conflicts: set[tuple[str, str]] = field(default_factory=set)
    pushed_refs: list[tuple[str, str, tuple[str, ...]]] = field(
        default_factory=list
    )
    push_returncode: int = 0
    push_stderr: str = ""
    recorded_commands: list[tuple[str, ...]] = field(default_factory=list)
    process_runner: FakeProcessRunner = field(
        default_factory=FakeProcessRunner
    )

    def seed_commit(
        self,
        sha: str,
        message: str,
        *,
        parents: Sequence[str] = (),
        changed_files: Sequence[str] = (),
        update_head: bool = True,
    ) -> GitCommitInfo:
        """Appends a commit to the in-memory commit history and optionally updates HEAD."""
        parent_tuple = tuple(parents)
        if not parent_tuple and self.commits:
            parent_tuple = (self.commits[-1].sha,)
        info = GitCommitInfo(
            sha=sha,
            message=message,
            parents=parent_tuple,
            changed_files=tuple(changed_files),
        )
        self.commits.append(info)
        self.refs[sha] = sha
        if update_head:
            if "HEAD" in self.refs:
                self.refs["HEAD~1"] = self.refs["HEAD"]
            self.refs["HEAD"] = sha
            if parent_tuple:
                self.refs["HEAD~1"] = parent_tuple[0]
                self.ancestors.add((parent_tuple[0], sha))
                self.ancestors.add((parent_tuple[0], "HEAD"))
        return info

    def set_dirty(self, porcelain: str = " M src/modified.py\n") -> None:
        """Marks the fake working tree as dirty with the given porcelain status."""
        self.status_output = porcelain

    def set_clean(self) -> None:
        """Marks the fake working tree as clean."""
        self.status_output = ""

    def _resolve_commit(self, ref: str) -> Optional[GitCommitInfo]:
        sha = self.refs.get(ref, ref)
        for commit in reversed(self.commits):
            if commit.sha == sha or commit.sha.startswith(sha):
                return commit
        if ref == "HEAD" and self.commits:
            return self.commits[-1]
        if ref in ("HEAD~1", "HEAD^") and len(self.commits) >= 2:
            return self.commits[-2]
        return None

    def _log_range_commits(self, revision_range: str) -> list[GitCommitInfo]:
        if ".." not in revision_range:
            return list(reversed(self.commits))
        base_ref, tip_ref = revision_range.split("..", 1)
        base_commit = self._resolve_commit(base_ref)
        tip_commit = self._resolve_commit(tip_ref)
        base_sha = base_commit.sha if base_commit else self.refs.get(base_ref, base_ref)
        if tip_commit is None:
            return []
        collected: list[GitCommitInfo] = []
        current: Optional[GitCommitInfo] = tip_commit
        visited: set[str] = set()
        while current is not None and current.sha not in visited:
            if current.sha == base_sha or current.sha.startswith(base_sha):
                break
            visited.add(current.sha)
            collected.append(current)
            next_parent = current.parents[0] if current.parents else ""
            current = self._resolve_commit(next_parent) if next_parent else None
        return collected

    def _diff_name_only_files(self, ref_args: Sequence[str]) -> list[str]:
        key = "..".join(ref_args) if ref_args else "HEAD"
        if key in self.diff_files:
            return list(self.diff_files[key])
        if len(ref_args) == 2:
            range_commits = self._log_range_commits(f"{ref_args[0]}..{ref_args[1]}")
            seen: list[str] = []
            for commit in reversed(range_commits):
                for path in commit.changed_files:
                    if path not in seen:
                        seen.append(path)
            if seen:
                return seen
        if ref_args:
            commit = self._resolve_commit(ref_args[-1])
            return (
                list(commit.changed_files)
                if commit is not None
                else list(self.staged_files)
            )
        return list(self.staged_files)

    def _handle_merge_tree(self, args: Sequence[str]) -> tuple[int, str, str]:
        positional = [a for a in args[1:] if not a.startswith("-")]
        base_ref = positional[-2] if len(positional) >= 2 else ""
        branch_ref = positional[-1] if positional else ""
        base_sha = self.refs.get(base_ref, base_ref)
        branch_sha = self.refs.get(branch_ref, branch_ref)
        has_conflict = (
            (base_ref, branch_ref) in self.merge_tree_conflicts
            or (base_sha, branch_sha) in self.merge_tree_conflicts
            or branch_ref in self.rebase_conflicts
            or branch_sha in self.rebase_conflicts
        )
        if has_conflict:
            return (
                1,
                "CONFLICT (content): Merge conflict in fake_file.py\n",
                "Auto-merging failed\n",
            )
        return (0, f"{'d' * 40}\n", "")

    def _handle_rebase(self, args: Sequence[str]) -> tuple[int, str, str]:
        if "--abort" in args:
            self.status_output = ""
            return (0, "", "")
        if "--onto" not in args:
            return (0, "", "")
        idx = list(args).index("--onto")
        new_base = args[idx + 1] if idx + 1 < len(args) else "HEAD"
        upstream = args[idx + 2] if idx + 2 < len(args) else "HEAD~1"
        branch = args[idx + 3] if idx + 3 < len(args) else "HEAD"
        target_commit = self._resolve_commit(branch)
        new_base_commit = self._resolve_commit(new_base)
        new_base_sha = (
            new_base_commit.sha
            if new_base_commit is not None
            else self.refs.get(new_base, new_base)
        )
        target_sha = target_commit.sha if target_commit is not None else branch
        if (
            branch in self.rebase_conflicts
            or target_sha in self.rebase_conflicts
            or (new_base, branch) in self.merge_tree_conflicts
            or (new_base_sha, branch) in self.merge_tree_conflicts
            or (new_base_sha, target_sha) in self.merge_tree_conflicts
        ):
            self.status_output = "UU fake_conflict.py\n"
            return (
                1,
                "CONFLICT (content): Merge conflict in fake_conflict.py\n",
                f"error: could not apply {target_sha[:7]} (upstream {upstream})\n",
            )
        rebased_sha = f"{len(self.commits) + 101:040x}"
        message = target_commit.message if target_commit is not None else ""
        changed = target_commit.changed_files if target_commit is not None else ()
        self.seed_commit(
            rebased_sha,
            message,
            parents=(new_base_sha,),
            changed_files=changed,
            update_head=True,
        )
        self.ancestors.add((new_base_sha, rebased_sha))
        if branch != "HEAD" and not re.fullmatch(r"[0-9a-fA-F]{7,40}", branch):
            self.refs[branch] = rebased_sha
            self.current_branch_name = branch
        return (0, f"Successfully rebased and updated {branch}.\n", "")

    def _handle_checkout(self, args: Sequence[str]) -> tuple[int, str, str]:
        if "-B" in args or "-b" in args:
            flag = "-B" if "-B" in args else "-b"
            idx = list(args).index(flag)
            branch = args[idx + 1] if idx + 1 < len(args) else "main"
            start_point = args[idx + 2] if idx + 2 < len(args) else "HEAD"
            commit = self._resolve_commit(start_point)
            sha = (
                commit.sha
                if commit is not None
                else self.refs.get(start_point, start_point)
            )
            self.refs[branch] = sha
            self.refs["HEAD"] = sha
            self.current_branch_name = branch
            return (0, f"Switched to and reset branch '{branch}'\n", "")
        target = args[-1] if len(args) > 1 else "HEAD"
        commit = self._resolve_commit(target)
        if commit is not None:
            self.refs["HEAD"] = commit.sha
        if target in self.refs and not re.fullmatch(r"[0-9a-fA-F]{7,40}", target):
            self.current_branch_name = target
        return (0, "", "")

    def _handle_push(self, args: Sequence[str]) -> tuple[int, str, str]:
        options: list[str] = []
        positional: list[str] = []
        skip_next = False
        for i, arg in enumerate(args[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if arg == "-o" and i + 1 < len(args):
                options.append(args[i + 1])
                skip_next = True
            elif arg.startswith("-o"):
                options.append(arg[2:])
            elif not arg.startswith("-"):
                positional.append(arg)
        remote = positional[0] if positional else "origin"
        refspec = positional[1] if len(positional) > 1 else "HEAD"
        self.pushed_refs.append((remote, refspec, tuple(options)))
        if self.push_returncode != 0:
            return (
                self.push_returncode,
                "",
                self.push_stderr or "fatal: remote rejected push\n",
            )
        return (0, "", f"To {remote}\n * [new reference] {refspec}\n")

    def run_git(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        check: bool = False,
        timeout_sec: float | None = DEFAULT_GIT_TIMEOUT_SEC,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        cmd_tuple = ("git", *(str(a) for a in args))
        self.recorded_commands.append(cmd_tuple)

        # Allow explicit queued/prefix overrides in self.process_runner if configured.
        if self.process_runner._queued_results or any(
            cmd_tuple[: len(p)] == p for p, _ in self.process_runner._prefix_rules
        ):
            return self.process_runner.run(
                cmd_tuple, cwd=cwd, env=env, timeout_sec=timeout_sec, check=check
            )

        subcmd = args[0] if args else ""
        stdout = ""
        stderr = ""
        returncode = 0

        if subcmd == "rev-parse":
            if "--is-inside-work-tree" in args:
                if self.inside_work_tree:
                    stdout = "true\n"
                else:
                    returncode = 128
                    stderr = "fatal: not a git repository\n"
            elif "--show-toplevel" in args:
                if self.inside_work_tree:
                    stdout = f"{self.repo_root}\n"
                else:
                    returncode = 128
                    stderr = "fatal: not a git repository\n"
            elif "--git-dir" in args:
                if self.inside_work_tree:
                    stdout = ".git\n"
                else:
                    returncode = 128
                    stderr = "fatal: not a git repository\n"
            elif "--abbrev-ref" in args:
                stdout = f"{self.current_branch_name or 'HEAD'}\n"
            else:
                ref = args[-1].removesuffix("^{commit}")
                commit = self._resolve_commit(ref)
                if commit is not None:
                    stdout = f"{commit.sha}\n"
                elif ref in self.refs:
                    stdout = f"{self.refs[ref]}\n"
                else:
                    returncode = 128
                    stderr = f"fatal: Needed a single revision ({ref})\n"
        elif subcmd == "status" and "--porcelain" in args:
            if "-uno" in args:
                lines = [
                    ln
                    for ln in self.status_output.splitlines()
                    if not ln.startswith("??")
                ]
                stdout = ("\n".join(lines) + "\n") if lines else ""
            else:
                stdout = self.status_output
        elif subcmd == "log":
            grep_arg = next(
                (a.split("=", 1)[1] for a in args if a.startswith("--grep=")),
                None,
            )
            if grep_arg is not None:
                is_fixed = "-F" in args or "--fixed-strings" in args
                matched = next(
                    (
                        c.sha
                        for c in reversed(self.commits)
                        if (
                            grep_arg in c.message
                            if is_fixed
                            else bool(
                                re.search(grep_arg, c.message, re.MULTILINE)
                            )
                        )
                    ),
                    "",
                )
                stdout = f"{matched}\n" if matched else ""
            elif "--oneline" in args:
                range_arg = next(
                    (a for a in args[1:] if not a.startswith("-")), ""
                )
                range_commits = self._log_range_commits(range_arg)
                lines = [
                    f"{c.sha[:7] if re.fullmatch(r'[0-9a-fA-F]{40}', c.sha) else c.sha} {c.subject}"
                    for c in range_commits
                ]
                stdout = ("\n".join(lines) + "\n") if lines else ""
            else:
                ref = args[-1] if not args[-1].startswith("-") else "HEAD"
                commit = self._resolve_commit(ref)
                if commit is None:
                    returncode = 128
                    stderr = f"fatal: bad revision '{ref}'\n"
                elif "--format=%s" in args:
                    stdout = f"{commit.subject}\n"
                else:
                    stdout = f"{commit.message}\n"
        elif subcmd == "rev-list" and "--parents" in args:
            ref = args[-1]
            commit = self._resolve_commit(ref)
            if commit is None:
                returncode = 128
                stderr = f"fatal: bad revision '{ref}'\n"
            else:
                tokens = [commit.sha, *commit.parents]
                stdout = " ".join(tokens) + "\n"
        elif subcmd == "diff" and "--name-only" in args:
            ref_args = [a for a in args[1:] if not a.startswith("-")]
            files = self._diff_name_only_files(ref_args)
            stdout = ("\n".join(files) + "\n") if files else ""
        elif subcmd == "merge-tree":
            returncode, stdout, stderr = self._handle_merge_tree(args)
        elif subcmd == "rebase":
            returncode, stdout, stderr = self._handle_rebase(args)
        elif subcmd == "checkout":
            returncode, stdout, stderr = self._handle_checkout(args)
        elif subcmd == "push":
            returncode, stdout, stderr = self._handle_push(args)
        elif subcmd == "add":
            paths = [a for a in args[1:] if not a.startswith("-")]
            self.staged_files.extend(paths)
            if self.status_output:
                updated_lines = []
                for ln in self.status_output.splitlines():
                    if ln.startswith(" ") and len(ln) > 2:
                        updated_lines.append(f"{ln[1]}  {ln[3:]}")
                    else:
                        updated_lines.append(ln)
                self.status_output = "\n".join(updated_lines) + "\n"
        elif subcmd == "commit":
            msg = ""
            if "-m" in args:
                idx = list(args).index("-m")
                if idx + 1 < len(args):
                    msg = args[idx + 1]
            elif "-F" in args:
                idx = list(args).index("-F")
                if idx + 1 < len(args):
                    msg_path = Path(args[idx + 1])
                    if msg_path.is_file():
                        msg = msg_path.read_text(encoding="utf-8")
            elif "--no-edit" in args and self.commits:
                msg = self.commits[-1].message
            amend = "--amend" in args
            new_sha = f"{len(self.commits) + 1:040x}"
            parents: Sequence[str] = ()
            if amend and self.commits:
                prev = self.commits.pop()
                parents = prev.parents
                changed = tuple(self.staged_files) or prev.changed_files
            else:
                changed = tuple(self.staged_files)
            self.seed_commit(
                new_sha, msg, parents=parents, changed_files=changed
            )
            self.staged_files.clear()
            self.status_output = ""
        elif subcmd == "reset":
            ref = args[-1] if not args[-1].startswith("-") else "HEAD"
            commit = self._resolve_commit(ref)
            if commit is not None:
                self.refs["HEAD"] = commit.sha
            if "--hard" in args:
                self.status_output = ""
                self.staged_files.clear()
        elif subcmd == "merge-base" and "--is-ancestor" in args:
            anc, desc = args[-2], args[-1]
            anc_sha = self.refs.get(anc, anc)
            desc_sha = self.refs.get(desc, desc)
            is_anc = (
                anc_sha == desc_sha
                or (anc, desc) in self.ancestors
                or (anc_sha, desc_sha) in self.ancestors
                or (anc_sha, desc) in self.ancestors
            )
            returncode = 0 if is_anc else 1
        elif subcmd == "remote":
            stdout = ("\n".join(self.remotes_list) + "\n") if self.remotes_list else ""
        elif subcmd == "config":
            if "--get" in args:
                key = args[-1]
                if key in self.config_values:
                    stdout = f"{self.config_values[key]}\n"
                else:
                    returncode = 1
            elif len(args) >= 3:
                self.config_values[args[1]] = args[2]

        result = ProcessResult(
            args=cmd_tuple,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if check and returncode != 0:
            raise SubprocessError(
                f"Git command {' '.join(cmd_tuple)} failed with exit code {returncode}.",
                cmd=cmd_tuple,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return result
