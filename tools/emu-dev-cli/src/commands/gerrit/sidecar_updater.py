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

"""Upstream Gerrit directory update detector and dirty-worktree-safe installer for Fast Gerrit UI."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

from commands.source_directory import (
    get_all_source_directories,
    get_source_directory,
)
from install.installer import get_release_package_directory
from lib.bazel import find_bazel_cmd

DEFAULT_UPSTREAM_REMOTE_CANDIDATES: tuple[str, ...] = ("goog", "aosp", "origin")
DEFAULT_AEMU_UPSTREAM_BRANCH: str = "studio-main"
UPSTREAM_FETCH_TTL_SEC: float = 90.0
GIT_QUICK_CMD_TIMEOUT_SEC: int = 10
GIT_FETCH_TIMEOUT_SEC: int = 15
GIT_SYNC_TIMEOUT_SEC: int = 30
BAZEL_INSTALL_TIMEOUT_SEC: int = 300
MAX_PENDING_COMMITS_PREVIEW: int = 10
AEMU_CLI_INSTALL_BAZEL_TARGET: str = "@aemu//tools/emu-dev-cli:emu-dev-cli"

GERRIT_REPO_WATCH_PATHS: tuple[str, ...] = (
    "tools/emu-dev-cli/src/commands/gerrit",
    "tools/emu-dev-cli/src/templates/gerrit_ui.html",
    "tools/emu-dev-cli/src/lib/gerrit_api.py",
    "tools/emu-dev-cli/src/lib/web_security.py",
    "tools/emu-dev-cli/harness/plugin/sidecars/aemu_gerrit_ui",
)

_IGNORED_COMPILED_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


def compute_git_blob_sha1(content: bytes) -> str:
    """Computes Git's canonical `blob <size>\\0` SHA-1 hex digest for `content`."""
    header = f"blob {len(content)}\x00".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _is_watched_asset_file(filename: str) -> bool:
    """Returns True when `filename` is a source/template file rather than bytecode cache."""
    return not filename.startswith(".") and not filename.endswith(_IGNORED_COMPILED_SUFFIXES)


def _collect_dir_blobs(
    root_dir: Path, repo_prefix: str, out: dict[str, str]
) -> None:
    """Walks `root_dir` and records Git blob SHA-1 digests keyed by `repo_prefix/<rel_path>`."""
    if not root_dir.is_dir():
        return
    for cur_root, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for fname in sorted(filenames):
            if not _is_watched_asset_file(fname):
                continue
            fpath = Path(cur_root) / fname
            rel_posix = fpath.relative_to(root_dir).as_posix()
            try:
                out[f"{repo_prefix}/{rel_posix}"] = compute_git_blob_sha1(fpath.read_bytes())
            except OSError:
                continue


def _resolve_installed_sidecar_dir(lib_dir: Path) -> Path:
    """Resolves the installed `sidecars/aemu_gerrit_ui` directory inside or adjacent to `lib_dir`."""
    nested = lib_dir / "harness" / "plugin" / "sidecars" / "aemu_gerrit_ui"
    if nested.is_dir():
        return nested
    return lib_dir.parent / "harness" / "plugin" / "sidecars" / "aemu_gerrit_ui"


def collect_installed_gerrit_blobs(lib_dir: Path) -> dict[str, str]:
    """Computes `{repo_rel_path: git_blob_sha1}` for the installed sidecar/CLI bundle."""
    if not lib_dir.is_dir():
        return {}
    blobs: dict[str, str] = {}
    _collect_dir_blobs(
        lib_dir / "commands" / "gerrit",
        "tools/emu-dev-cli/src/commands/gerrit",
        blobs,
    )
    _collect_dir_blobs(
        _resolve_installed_sidecar_dir(lib_dir),
        "tools/emu-dev-cli/harness/plugin/sidecars/aemu_gerrit_ui",
        blobs,
    )
    single_files = (
        (lib_dir / "templates" / "gerrit_ui.html", "tools/emu-dev-cli/src/templates/gerrit_ui.html"),
        (lib_dir / "lib" / "gerrit_api.py", "tools/emu-dev-cli/src/lib/gerrit_api.py"),
        (lib_dir / "lib" / "web_security.py", "tools/emu-dev-cli/src/lib/web_security.py"),
    )
    for local_file, repo_rel in single_files:
        try:
            if local_file.is_file():
                blobs[repo_rel] = compute_git_blob_sha1(local_file.read_bytes())
        except OSError:
            continue
    return blobs


def collect_repo_worktree_gerrit_blobs(repo_root: Path) -> dict[str, str]:
    """Computes `{repo_rel_path: git_blob_sha1}` for the live working tree in `repo_root`."""
    if not repo_root.is_dir():
        return {}
    blobs: dict[str, str] = {}
    for rel_entry in GERRIT_REPO_WATCH_PATHS:
        target = repo_root / rel_entry
        if target.is_dir():
            _collect_dir_blobs(target, rel_entry, blobs)
        elif target.is_file():
            try:
                blobs[rel_entry] = compute_git_blob_sha1(target.read_bytes())
            except OSError:
                continue
    return blobs


def resolve_default_installed_lib_dir() -> Path:
    """Resolves `~/.android/emu-dev-cli/lib` if present, else the active `src/` root."""
    try:
        rel_lib = Path(get_release_package_directory(None)) / "lib"
        if rel_lib.is_dir():
            return rel_lib
    except Exception:
        pass
    return Path(__file__).resolve().parents[2]


def _is_valid_aemu_git_repo(candidate: Path) -> bool:
    """Returns True when `candidate` is a Git worktree containing `tools/emu-dev-cli`."""
    return (candidate / ".git").exists() and (candidate / "tools" / "emu-dev-cli").is_dir()


def _candidate_workspace_roots() -> list[Path]:
    """Collects configured workspace roots from `emu-dev-cli` source-directory settings."""
    roots: list[Path] = []
    try:
        primary = get_source_directory("emu-main-next")
        if primary:
            roots.append(Path(primary))
    except Exception:
        pass
    try:
        for entry in get_all_source_directories():
            raw = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(raw, str) and raw.strip():
                roots.append(Path(raw.strip()))
    except Exception:
        pass
    here = Path(__file__).resolve()
    if len(here.parents) >= 8:
        roots.append(here.parents[7])
    if len(here.parents) >= 5:
        roots.append(here.parents[4])
    return roots


def resolve_aemu_workspace_and_repo(
    explicit_workspace: Path | None = None,
    explicit_repo: Path | None = None,
) -> tuple[Path | None, Path | None]:
    """Resolves `(workspace_root, aemu_repo_root)` for `hardware/google/aemu`."""
    if explicit_repo is not None:
        repo = explicit_repo.resolve()
        if not _is_valid_aemu_git_repo(repo):
            return None, None
        ws = explicit_workspace.resolve() if explicit_workspace else repo.parents[2]
        return ws, repo

    candidates = [explicit_workspace] if explicit_workspace else _candidate_workspace_roots()
    for raw_ws in candidates:
        if raw_ws is None:
            continue
        ws = raw_ws.resolve()
        nested_aemu = ws / "hardware" / "google" / "aemu"
        if _is_valid_aemu_git_repo(nested_aemu):
            return ws, nested_aemu
        if _is_valid_aemu_git_repo(ws):
            inferred_ws = ws.parents[2] if len(ws.parents) >= 3 and (ws.parents[2] / "MODULE.bazel").is_file() else ws
            return inferred_ws, ws
    return None, None


def _run_git_cmd(
    repo_root: Path,
    args: list[str],
    *,
    timeout: int = GIT_QUICK_CMD_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    """Executes a non-interactive UTF-8 `git` command with unquoted Unicode paths."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", "-C", str(repo_root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out after {timeout}s running 'git {' '.join(args)}' in '{repo_root}'. "
            "Verify network/SSO connectivity by running 'gcert' in your terminal and retry."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Failed to execute 'git {' '.join(args)}' in '{repo_root}': {exc}. "
            "Ensure 'git' is installed on PATH and the repository path is accessible."
        ) from exc


def resolve_upstream_ref(
    repo_root: Path, branch: str = DEFAULT_AEMU_UPSTREAM_BRANCH
) -> tuple[str, str, str]:
    """Resolves `(remote_name, branch_name, '<remote>/<branch>')` for `hardware/google/aemu`."""
    res = _run_git_cmd(repo_root, ["remote"])
    if res.returncode != 0:
        return "", branch, ""
    remotes = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    if not remotes:
        return "", branch, ""
    for preferred in DEFAULT_UPSTREAM_REMOTE_CANDIDATES:
        if preferred in remotes:
            return preferred, branch, f"{preferred}/{branch}"
    chosen = remotes[0]
    return chosen, branch, f"{chosen}/{branch}"


def _rev_parse_short(repo_root: Path, ref: str) -> str:
    """Returns the short commit SHA for `ref`, or `''` if `ref` cannot be resolved."""
    if not ref:
        return ""
    res = _run_git_cmd(repo_root, ["rev-parse", "--verify", "--short", f"{ref}^{{commit}}"])
    return res.stdout.strip() if res.returncode == 0 else ""


def read_git_tree_blobs(repo_root: Path, ref: str) -> dict[str, str]:
    """Reads `{repo_rel_path: blob_sha1}` at `ref` across `GERRIT_REPO_WATCH_PATHS`."""
    if not ref:
        return {}
    res = _run_git_cmd(repo_root, ["ls-tree", "-r", ref, "--", *GERRIT_REPO_WATCH_PATHS])
    if res.returncode != 0:
        return {}
    blobs: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "\t" not in line:
            continue
        meta, rel_path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob" and _is_watched_asset_file(Path(rel_path).name):
            blobs[rel_path] = parts[2]
    return blobs


def list_upstream_gerrit_commits(
    repo_root: Path, upstream_ref: str
) -> tuple[int, list[dict[str, str]]]:
    """Lists upstream commits in `HEAD..<upstream_ref>` touching `GERRIT_REPO_WATCH_PATHS`."""
    if not upstream_ref:
        return 0, []
    cnt_res = _run_git_cmd(
        repo_root, ["rev-list", "--count", f"HEAD..{upstream_ref}", "--", *GERRIT_REPO_WATCH_PATHS]
    )
    if cnt_res.returncode != 0:
        return 0, []
    try:
        behind_count = int(cnt_res.stdout.strip() or "0")
    except ValueError:
        behind_count = 0
    if behind_count <= 0:
        return 0, []

    log_res = _run_git_cmd(
        repo_root,
        [
            "log",
            f"-n{MAX_PENDING_COMMITS_PREVIEW}",
            "--format=%h%x09%s",
            f"HEAD..{upstream_ref}",
            "--",
            *GERRIT_REPO_WATCH_PATHS,
        ],
    )
    commits: list[dict[str, str]] = []
    if log_res.returncode == 0:
        for raw_line in log_res.stdout.splitlines():
            if "\t" not in raw_line:
                continue
            sha_part, subj_part = raw_line.split("\t", 1)
            commits.append({"sha": sha_part.strip(), "subject": subj_part.strip()})
    return behind_count, commits


def _list_incoming_upstream_paths(repo_root: Path, upstream_ref: str) -> set[str]:
    """Returns all file paths modified or added between `HEAD` and `upstream_ref`."""
    if not upstream_ref:
        return set()
    res = _run_git_cmd(repo_root, ["diff", "--name-only", f"HEAD..{upstream_ref}"])
    if res.returncode != 0:
        return set()
    return {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}


def _parse_porcelain_entry(
    raw_line: str, incoming_paths: set[str]
) -> dict[str, str] | None:
    """Parses one `git status --porcelain` line into a dirty-file descriptor if affected by sync."""
    clean = raw_line.rstrip("\r\n")
    if len(clean) < 4:
        return None
    xy = clean[:2]
    path_str = clean[3:]
    if xy[0] in ("R", "C") and " -> " in path_str:
        path_str = path_str.split(" -> ", 1)[1]

    if xy == "??":
        if path_str in incoming_paths:
            return {"path": path_str, "kind": "untracked_conflict", "code": xy}
        return None

    if xy[1] != " ":
        return {"path": path_str, "kind": "unstaged", "code": xy}
    if xy[0] != " ":
        return {"path": path_str, "kind": "staged", "code": xy}
    return None


def inspect_worktree_dirty_state(
    repo_root: Path, incoming_paths: set[str] | None = None
) -> list[dict[str, str]]:
    """Lists unstaged, staged, or colliding untracked changes in `repo_root`."""
    res = _run_git_cmd(repo_root, ["status", "--porcelain", "--untracked-files=all"])
    if res.returncode != 0:
        return []
    incoming = incoming_paths or set()
    dirty: list[dict[str, str]] = []
    for raw_line in res.stdout.splitlines():
        parsed = _parse_porcelain_entry(raw_line, incoming)
        if parsed is not None:
            dirty.append(parsed)
    return dirty


def _diff_blob_maps(target_blobs: dict[str, str], installed_blobs: dict[str, str]) -> list[str]:
    """Returns sorted repository-relative paths whose blob SHA-1 differs between `target` and `installed`."""
    all_keys = set(target_blobs) | set(installed_blobs)
    return sorted(k for k in all_keys if target_blobs.get(k) != installed_blobs.get(k))


def _default_bazel_install_runner(workspace_root: Path, aemu_repo_root: Path) -> None:
    """Rebuilds `@aemu//tools/emu-dev-cli:emu-dev-cli` and installs the updated CLI and sidecar."""
    bazel_cmd = find_bazel_cmd(workspace_root)
    cmd = [bazel_cmd, "run", AEMU_CLI_INSTALL_BAZEL_TARGET, "--", "--json", "install"]
    try:
        res = subprocess.run(
            cmd,
            cwd=str(workspace_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BAZEL_INSTALL_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out after {BAZEL_INSTALL_TIMEOUT_SEC}s rebuilding and installing "
            f"'{AEMU_CLI_INSTALL_BAZEL_TARGET}' in '{workspace_root}'. "
            f"Run '{bazel_cmd} run {AEMU_CLI_INSTALL_BAZEL_TARGET} -- install' manually to inspect build progress."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Failed to invoke Bazel installer '{bazel_cmd}' in '{workspace_root}' "
            f"(repo '{aemu_repo_root}'): {exc}. Verify '{workspace_root}' contains a valid Bazel wrapper."
        ) from exc

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or f"exit code {res.returncode}").strip()
        tail = "\n".join(detail.splitlines()[-12:])
        raise RuntimeError(
            f"Failed to build and install '{AEMU_CLI_INSTALL_BAZEL_TARGET}' in '{workspace_root}' "
            f"(exit code {res.returncode}): {tail}. "
            f"Run '{bazel_cmd} build {AEMU_CLI_INSTALL_BAZEL_TARGET}' in '{workspace_root}' to fix any build errors."
        )


def _wipe_dirty_worktree_changes(repo_root: Path, dirty_files: list[dict[str, str]]) -> None:
    """Discards tracked staged/unstaged modifications and removes colliding untracked files."""
    reset_res = _run_git_cmd(repo_root, ["reset", "--hard", "HEAD"], timeout=GIT_SYNC_TIMEOUT_SEC)
    if reset_res.returncode != 0:
        detail = (reset_res.stderr or reset_res.stdout or f"exit code {reset_res.returncode}").strip()
        raise RuntimeError(
            f"Failed to discard local tracked changes in '{repo_root}' via 'git reset --hard HEAD': "
            f"{detail}. Inspect 'git -C {repo_root} status' and remove any `.git/index.lock` file."
        )

    repo_resolved = repo_root.resolve()
    for entry in dirty_files:
        if entry.get("kind") != "untracked_conflict":
            continue
        target_path = (repo_resolved / entry["path"]).resolve()
        if not target_path.is_relative_to(repo_resolved) or target_path == repo_resolved:
            continue
        try:
            if target_path.is_file() or target_path.is_symlink():
                target_path.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"Failed to remove untracked conflicting file '{target_path}' before update: {exc}. "
                f"Move or delete '{target_path}' manually and retry."
            ) from exc


def _advance_repo_to_upstream(repo_root: Path, upstream_ref: str) -> None:
    """Fast-forwards or rebases `repo_root` onto `upstream_ref` when `upstream_ref` has newer commits."""
    if not upstream_ref:
        return
    behind_res = _run_git_cmd(repo_root, ["rev-list", "--count", f"HEAD..{upstream_ref}"])
    if behind_res.returncode != 0 or int(behind_res.stdout.strip() or "0") <= 0:
        return

    ahead_res = _run_git_cmd(repo_root, ["rev-list", "--count", f"{upstream_ref}..HEAD"])
    local_ahead = int(ahead_res.stdout.strip() or "0") if ahead_res.returncode == 0 else 0

    if local_ahead == 0:
        ff_res = _run_git_cmd(
            repo_root, ["merge", "--ff-only", upstream_ref], timeout=GIT_SYNC_TIMEOUT_SEC
        )
        if ff_res.returncode != 0:
            detail = (ff_res.stderr or ff_res.stdout or f"exit code {ff_res.returncode}").strip()
            raise RuntimeError(
                f"Failed to fast-forward '{repo_root}' to '{upstream_ref}': {detail}. "
                f"Run 'git -C {repo_root} status' and 'git -C {repo_root} pull --rebase' to sync manually."
            )
        return

    rb_res = _run_git_cmd(repo_root, ["rebase", upstream_ref], timeout=GIT_SYNC_TIMEOUT_SEC)
    if rb_res.returncode != 0:
        _run_git_cmd(repo_root, ["rebase", "--abort"], timeout=GIT_QUICK_CMD_TIMEOUT_SEC)
        detail = (rb_res.stderr or rb_res.stdout or f"exit code {rb_res.returncode}").strip()
        raise RuntimeError(
            f"Failed to rebase {local_ahead} local commit(s) in '{repo_root}' onto '{upstream_ref}' "
            f"(aborted cleanly): {detail}. Resolve conflicts manually via 'git -C {repo_root} rebase {upstream_ref}'."
        )


class GerritSidecarUpdater:
    """Checks for newer Gerrit changes in `hardware/google/aemu` and updates the sidecar safely."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        aemu_repo_root: Path | None = None,
        installed_lib_dir: Path | None = None,
        fetch_ttl_sec: float = UPSTREAM_FETCH_TTL_SEC,
        install_runner: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._explicit_workspace = workspace_root
        self._explicit_repo = aemu_repo_root
        self._explicit_lib_dir = installed_lib_dir
        self._fetch_ttl_sec = fetch_ttl_sec
        self._install_runner = install_runner or _default_bazel_install_runner
        self._fetch_lock = threading.Lock()
        self._update_lock = threading.Lock()
        self._last_fetch_mono: float = 0.0
        self._last_fetch_warning: str = ""

    def _maybe_fetch_upstream(
        self, repo_root: Path, remote_name: str, branch_name: str, *, force: bool
    ) -> str:
        """Fetches `<remote_name> <branch_name>` when cache TTL expires or `force=True`."""
        if not remote_name:
            return ""
        with self._fetch_lock:
            now = time.monotonic()
            if not force and self._last_fetch_mono > 0.0 and (now - self._last_fetch_mono) < self._fetch_ttl_sec:
                return self._last_fetch_warning
            try:
                res = _run_git_cmd(
                    repo_root,
                    ["fetch", "--quiet", remote_name, branch_name],
                    timeout=GIT_FETCH_TIMEOUT_SEC,
                )
                self._last_fetch_mono = time.monotonic()
                if res.returncode != 0:
                    detail = (res.stderr or res.stdout or f"exit code {res.returncode}").strip()
                    self._last_fetch_warning = (
                        f"Could not fetch '{remote_name}/{branch_name}': {detail}"
                    )
                else:
                    self._last_fetch_warning = ""
            except RuntimeError as exc:
                self._last_fetch_mono = time.monotonic()
                self._last_fetch_warning = str(exc)
            return self._last_fetch_warning

    def get_update_status(
        self, *, force_fetch: bool = False, ui_version: str = ""
    ) -> dict[str, Any]:
        """Inspects whether `hardware/google/aemu` has newer Gerrit changes than the installed sidecar."""
        ws_root, repo_root = resolve_aemu_workspace_and_repo(
            self._explicit_workspace, self._explicit_repo
        )
        if repo_root is None or ws_root is None:
            return {
                "configured": False,
                "update_available": False,
                "update_reason": "unconfigured",
                "behind_count": 0,
                "pending_commits": [],
                "changed_gerrit_files": [],
                "has_unstaged_changes": False,
                "dirty_files": [],
                "ui_version": ui_version,
            }

        remote_name, branch_name, upstream_ref = resolve_upstream_ref(repo_root)
        fetch_warning = self._maybe_fetch_upstream(
            repo_root, remote_name, branch_name, force=force_fetch
        )
        head_sha = _rev_parse_short(repo_root, "HEAD")
        upstream_sha = _rev_parse_short(repo_root, upstream_ref)
        lib_dir = self._explicit_lib_dir or resolve_default_installed_lib_dir()

        head_blobs = read_git_tree_blobs(repo_root, "HEAD")
        upstream_blobs = read_git_tree_blobs(repo_root, upstream_ref) if upstream_sha else {}
        installed_blobs = collect_installed_gerrit_blobs(lib_dir)
        worktree_blobs = collect_repo_worktree_gerrit_blobs(repo_root)

        behind_count, pending_commits = (
            list_upstream_gerrit_commits(repo_root, upstream_ref) if upstream_sha else (0, [])
        )
        incoming_paths = (
            _list_incoming_upstream_paths(repo_root, upstream_ref)
            if upstream_sha and upstream_sha != head_sha
            else set()
        )
        dirty_files = inspect_worktree_dirty_state(repo_root, incoming_paths)

        upstream_has_new = behind_count > 0 and (
            not installed_blobs or upstream_blobs != installed_blobs
        )
        local_differs_installed = (
            bool(head_blobs)
            and bool(installed_blobs)
            and head_blobs != installed_blobs
            and worktree_blobs != installed_blobs
        )
        update_available = upstream_has_new or local_differs_installed
        if upstream_has_new:
            reason = "upstream_ahead"
            changed_files = _diff_blob_maps(upstream_blobs, installed_blobs)
        elif local_differs_installed:
            reason = "local_ahead_of_installed"
            changed_files = _diff_blob_maps(head_blobs, installed_blobs)
        else:
            reason = "up_to_date"
            changed_files = []

        return {
            "configured": True,
            "workspace_path": str(ws_root),
            "repo_path": str(repo_root),
            "installed_lib_path": str(lib_dir),
            "upstream_ref": upstream_ref,
            "head_sha": head_sha,
            "upstream_sha": upstream_sha,
            "update_available": update_available,
            "update_reason": reason,
            "behind_count": behind_count,
            "pending_commits": pending_commits,
            "changed_gerrit_files": changed_files,
            "has_unstaged_changes": len(dirty_files) > 0,
            "dirty_files": dirty_files,
            "fetch_warning": fetch_warning,
            "ui_version": ui_version,
        }

    def perform_update(
        self, *, force_discard_local: bool = False, ui_version_fn: Callable[[], str] | None = None
    ) -> dict[str, Any]:
        """Pulls latest `hardware/google/aemu` and reinstalls `emu-dev-cli` + Gerrit sidecar."""
        if not self._update_lock.acquire(blocking=False):
            return {
                "status": "busy",
                "error": (
                    "Cannot start Fast Gerrit sidecar update because another update is already "
                    "in progress. Wait for the active update to finish before retrying."
                ),
            }
        try:
            return self._execute_locked_update(
                force_discard_local=force_discard_local, ui_version_fn=ui_version_fn
            )
        finally:
            self._update_lock.release()

    def _execute_locked_update(
        self, *, force_discard_local: bool, ui_version_fn: Callable[[], str] | None
    ) -> dict[str, Any]:
        """Runs fetch, dirty-tree check/wipe, upstream advance, and CLI/sidecar reinstall."""
        ws_root, repo_root = resolve_aemu_workspace_and_repo(
            self._explicit_workspace, self._explicit_repo
        )
        if repo_root is None or ws_root is None:
            raise ValueError(
                "Cannot update Fast Gerrit sidecar: local 'emu-main-next' / 'hardware/google/aemu' "
                "checkout was not found. Configure it via "
                "'emu-dev-cli source-directory set emu-main-next <path>' and retry."
            )

        remote_name, branch_name, upstream_ref = resolve_upstream_ref(repo_root)
        if remote_name:
            fetch_res = _run_git_cmd(
                repo_root,
                ["fetch", "--quiet", remote_name, branch_name],
                timeout=GIT_FETCH_TIMEOUT_SEC,
            )
            self._last_fetch_mono = time.monotonic()
            if fetch_res.returncode != 0:
                detail = (fetch_res.stderr or fetch_res.stdout or f"exit code {fetch_res.returncode}").strip()
                raise RuntimeError(
                    f"Failed to fetch latest '{branch_name}' from remote '{remote_name}' in "
                    f"'{repo_root}': {detail}. Run 'gcert' in your terminal and retry."
                )
            self._last_fetch_warning = ""

        upstream_sha = _rev_parse_short(repo_root, upstream_ref)
        head_sha = _rev_parse_short(repo_root, "HEAD")
        incoming_paths = (
            _list_incoming_upstream_paths(repo_root, upstream_ref)
            if upstream_sha and upstream_sha != head_sha
            else set()
        )
        dirty_files = inspect_worktree_dirty_state(repo_root, incoming_paths)
        if dirty_files and not force_discard_local:
            return {
                "status": "dirty_worktree",
                "requires_confirmation": True,
                "has_unstaged_changes": True,
                "dirty_files": dirty_files,
                "repo_path": str(repo_root),
                "error": (
                    f"Unstaged or uncommitted local changes detected in '{repo_root}' "
                    f"({len(dirty_files)} file(s)). Updating will wipe out those local changes; "
                    "confirm discarding local changes or commit/stash them first."
                ),
            }

        discarded_paths: list[str] = []
        if dirty_files and force_discard_local:
            _wipe_dirty_worktree_changes(repo_root, dirty_files)
            discarded_paths = [d["path"] for d in dirty_files]

        if upstream_sha:
            _advance_repo_to_upstream(repo_root, upstream_ref)

        self._install_runner(ws_root, repo_root)
        next_ui_ver = ui_version_fn() if ui_version_fn else ""
        refreshed = self.get_update_status(force_fetch=False, ui_version=next_ui_ver)
        return {
            "status": "ok",
            "updated_head_sha": refreshed.get("head_sha", ""),
            "discarded_files": discarded_paths,
            "update_status": refreshed,
            "ui_version": next_ui_ver,
        }
