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

"""Target discovery and unified diff parsing for the 8-Specialist Review Swarm."""

from concurrent.futures import ThreadPoolExecutor
import itertools
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from commands.harness.constants import MAX_REPO_SUBPROJECT_SCAN_DEPTH
from lib.gerrit_api import (
    COMMIT_CHANGE_ID_RE,
    DEFAULT_GERRIT_HOST,
    GerritClient,
    decode_gerrit_patch,
    find_repo_workspace_root,
    parse_cl_identifier as _parse_gerrit_cl_identifier,
    unquote_git_diff_path,
)

COMMIT_MSG_FILE: str = "/COMMIT_MSG"
GIT_SUBPROCESS_TIMEOUT_SEC: float = 30.0
MAX_SAFE_TARGET_ID_LEN: int = 24
REPO_SUBPROJECT_SCAN_WORKERS: int = 16
SUBPROJECT_PRIORITY_AHEAD_OR_BRANCH: int = 2
SUBPROJECT_PRIORITY_NO_UPSTREAM: int = 1

DIFF_FILE_HEADER_RE: re.Pattern[str] = re.compile(r"^\+\+\+\s+(.+)$")
DIFF_HUNK_HEADER_RE: re.Pattern[str] = re.compile(
    r"^@@\s+-\d+(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@"
)
BUG_ID_FOOTER_RE: re.Pattern[str] = re.compile(
    r"(?im)^\s*Bug:\s*(?:b/)?([0-9]+)\b"
)


_ATOMIC_WRITE_SEQ = itertools.count()


def atomic_write_text(dest_path: Path, content: str) -> None:
    """Atomically writes UTF-8 text via a PID/thread/sequence-scoped sibling temp file."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.parent / (
        f".{dest_path.name}.{os.getpid()}.{threading.get_ident()}.{next(_ATOMIC_WRITE_SEQ)}.tmp"
    )
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def sanitize_target_id(target_id: str) -> str:
    """Normalizes a target commit SHA or CL ID into a filesystem-safe directory slug."""
    cleaned = "".join(c for c in (target_id or "") if c.isalnum() or c in ("-", "_"))
    return cleaned[:MAX_SAFE_TARGET_ID_LEN] or "head"


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Executes a UTF-8 git subprocess isolated from stdin with a bounded timeout."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_SUBPROCESS_TIMEOUT_SEC,
    )


_unquote_git_diff_path = unquote_git_diff_path


def _parse_hunk_bounds(hunk_match: re.Match[str]) -> Tuple[int, int, int]:
    """Returns `(old_rem, new_start_line, new_rem)` from a unified diff hunk header."""
    old_count = int(hunk_match.group(1)) if hunk_match.group(1) is not None else 1
    new_start = int(hunk_match.group(2))
    new_count = int(hunk_match.group(3)) if hunk_match.group(3) is not None else 1
    return old_count, new_start, new_count


def _consume_body_diff_line(
    raw_line: str,
    entries: List[Tuple[int, str]],
    curr_new_line: int,
    old_rem: int,
    new_rem: int,
) -> Tuple[int, int, int]:
    """Updates hunk counters and appends modified lines for one unified diff body line."""
    if raw_line.startswith("+") and new_rem > 0:
        entries.append((curr_new_line, raw_line))
        return curr_new_line + 1, old_rem, new_rem - 1
    if raw_line.startswith("-") and old_rem > 0:
        entries.append((max(1, curr_new_line), raw_line))
        return curr_new_line, old_rem - 1, new_rem
    if raw_line.startswith(" ") and old_rem > 0 and new_rem > 0:
        return curr_new_line + 1, old_rem - 1, new_rem - 1
    return curr_new_line, old_rem, new_rem


def parse_unified_diff_hunk_entries(diff_text: str) -> Dict[str, List[Tuple[int, str]]]:
    """Parses a unified diff into ordered `(new_line_no, raw_diff_line)` entries per file."""
    entries_by_file: Dict[str, List[Tuple[int, str]]] = {}
    current_file: Optional[str] = None
    curr_new_line, old_rem, new_rem = 0, 0, 0

    for raw_line in (diff_text or "").replace("\r\n", "\n").split("\n"):
        if old_rem == 0 and new_rem == 0 and raw_line.startswith("+++ "):
            header_match = DIFF_FILE_HEADER_RE.match(raw_line.strip())
            path_cand = _unquote_git_diff_path(header_match.group(1)) if header_match else ""
            current_file = None if path_cand in ("", "/dev/null") else path_cand
            if current_file is not None:
                entries_by_file.setdefault(current_file, [])
            continue
        hunk_match = DIFF_HUNK_HEADER_RE.match(raw_line) if (old_rem == 0 and new_rem == 0) else None
        if hunk_match:
            old_rem, curr_new_line, new_rem = _parse_hunk_bounds(hunk_match)
            if current_file is not None and new_rem == 0:
                entries_by_file[current_file].append((max(1, curr_new_line), raw_line))
            continue
        if old_rem == 0 and new_rem == 0:
            continue
        if current_file is None:
            old_rem, new_rem = 0, 0
            continue
        curr_new_line, old_rem, new_rem = _consume_body_diff_line(
            raw_line, entries_by_file[current_file], curr_new_line, old_rem, new_rem
        )

    return entries_by_file


def parse_unified_diff_changed_lines(diff_text: str) -> Dict[str, Set[int]]:
    """Parses a unified diff and maps each file path to its set of added/modified line numbers."""
    return {
        path: {lineno for lineno, _ in entries}
        for path, entries in parse_unified_diff_hunk_entries(diff_text).items()
    }


def serialize_changed_lines(
    changed_by_file: Dict[str, Set[int]],
) -> Dict[str, List[int]]:
    """Converts `Dict[str, Set[int]]` into JSON-serializable sorted lists."""
    return {path: sorted(lines) for path, lines in sorted(changed_by_file.items())}


def extract_bug_ids_from_commit_msg(commit_msg: str) -> List[str]:
    """Extracts numeric Buganizer IDs from `Bug:` footers in a commit message."""
    seen: List[str] = []
    for match in BUG_ID_FOOTER_RE.finditer(commit_msg or ""):
        bug_id = match.group(1).strip()
        if bug_id and bug_id not in seen:
            seen.append(bug_id)
    return seen


def parse_cl_identifier(cl_or_url: str) -> str:
    """Extracts a Gerrit CL number or `Change-Id` from a bare ID, `ag/<id>`, or URL."""
    return _parse_gerrit_cl_identifier(cl_or_url, allow_head=False, strict=True)


_decode_gerrit_patch = decode_gerrit_patch


def fetch_gerrit_cl_target(
    cl_or_url: str,
    gerrit_host: str = DEFAULT_GERRIT_HOST,
) -> Dict[str, Any]:
    """Fetches Gerrit CL metadata, `/COMMIT_MSG`, and unified diff via `GerritClient`."""
    cl_id = parse_cl_identifier(cl_or_url)
    client = GerritClient(host=gerrit_host)
    detail_json = client.get_change_detail(cl_id, include_files=True)
    if not detail_json.get("current_revision"):
        raise RuntimeError(
            f"Gerrit metadata response for CL '{cl_id}' on '{gerrit_host}' did not "
            "contain a valid `current_revision` object. Verify that the CL exists and "
            "run `gcert` to refresh corp credentials before retrying."
        )

    current_rev = str(detail_json.get("current_revision") or "")
    rev_info = (detail_json.get("revisions") or {}).get(current_rev) or {}
    commit_msg = str((rev_info.get("commit") or {}).get("message") or "")
    files_map = rev_info.get("files") or {}
    diff_text = _decode_gerrit_patch(
        client.get_revision_patch(cl_id, revision=current_rev)
    )

    changed_by_file = parse_unified_diff_changed_lines(diff_text)
    return {
        "target_type": "gerrit_cl",
        "target_id": cl_id,
        "project": detail_json.get("project", ""),
        "branch": detail_json.get("branch", ""),
        "subject": detail_json.get("subject", ""),
        "current_revision": current_rev,
        "commit_message": commit_msg,
        "bug_ids": extract_bug_ids_from_commit_msg(commit_msg),
        "files": sorted(k for k in files_map.keys() if k != COMMIT_MSG_FILE),
        "diff_text": diff_text,
        "changed_lines_by_file": serialize_changed_lines(changed_by_file),
    }


def _probe_git_toplevel(candidate: Path) -> Optional[Path]:
    """Returns the git working-tree root if `candidate` lies inside a git repo."""
    if not candidate.is_dir():
        return None
    check = _run_git(["rev-parse", "--show-toplevel"], candidate)
    if check.returncode == 0 and check.stdout.strip():
        return Path(check.stdout.strip()).resolve()
    return None


def _list_repo_subprojects(repo_root: Path) -> List[Path]:
    """Lists git subproject directories beneath an Android `repo` checkout root."""
    project_list = repo_root / ".repo" / "project.list"
    if project_list.is_file():
        lines = project_list.read_text(encoding="utf-8", errors="replace").splitlines()
        return [
            (repo_root / rel.strip()).resolve()
            for rel in lines
            if rel.strip() and (repo_root / rel.strip()).is_dir()
        ]

    discovered: List[Path] = []
    for root_str, dirs, _ in os.walk(repo_root):
        curr = Path(root_str)
        depth = len(curr.relative_to(repo_root).parts)
        if ".git" in dirs:
            discovered.append(curr.resolve())
            dirs[:] = []
            continue
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".") and depth < MAX_REPO_SUBPROJECT_SCAN_DEPTH
        ]
    return discovered


def _score_subproject_for_upload(
    project_dir: Path,
    branch_hint: Optional[str] = None,
) -> Optional[Tuple[int, int]]:
    """Scores a subproject by `(priority, head_commit_timestamp)` if uploadable."""
    if branch_hint:
        if branch_hint.startswith("-"):
            return None
        br_check = _run_git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_hint}"],
            project_dir,
        )
        if br_check.returncode != 0:
            return None
        ts_proc = _run_git(
            ["log", "-1", "--format=%ct", branch_hint, "--"],
            project_dir,
        )
        commit_ts = int(ts_proc.stdout.strip() or "0") if ts_proc.returncode == 0 else 0
        return (SUBPROJECT_PRIORITY_AHEAD_OR_BRANCH, commit_ts)

    sym_proc = _run_git(["symbolic-ref", "--quiet", "HEAD"], project_dir)
    if sym_proc.returncode != 0 or not sym_proc.stdout.strip():
        return None

    ahead_proc = _run_git(["rev-list", "--count", "@{upstream}..HEAD"], project_dir)
    if ahead_proc.returncode == 0:
        if int(ahead_proc.stdout.strip() or "0") <= 0:
            return None
        priority = SUBPROJECT_PRIORITY_AHEAD_OR_BRANCH
    else:
        priority = SUBPROJECT_PRIORITY_NO_UPSTREAM

    ts_proc = _run_git(["log", "-1", "--format=%ct", "HEAD"], project_dir)
    commit_ts = int(ts_proc.stdout.strip() or "0") if ts_proc.returncode == 0 else 0
    return (priority, commit_ts)


def _select_active_repo_subproject(
    repo_root: Path,
    branch_hint: Optional[str] = None,
) -> Optional[Path]:
    """Selects the active uploadable git subproject inside a `.repo` workspace."""
    projects = _list_repo_subprojects(repo_root)
    if not projects:
        return None
    max_workers = min(REPO_SUBPROJECT_SCAN_WORKERS, len(projects))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        scores = list(
            executor.map(
                lambda p: _score_subproject_for_upload(p, branch_hint=branch_hint),
                projects,
            )
        )
    best_score: Optional[Tuple[int, int]] = None
    best_project: Optional[Path] = None
    for project_dir, score in zip(projects, scores):
        if score is not None and (best_score is None or score > best_score):
            best_score = score
            best_project = project_dir
    return best_project


def resolve_git_repo_dir(
    repo_dir: Optional[Path] = None,
    branch_hint: Optional[str] = None,
) -> Path:
    """Resolves a git repo directory from a direct repo path or `.repo` workspace root."""
    start_dir = (repo_dir or Path.cwd()).expanduser().resolve()
    direct_git = _probe_git_toplevel(start_dir)
    if direct_git is not None:
        return direct_git

    ws_root = find_repo_workspace_root(start_dir)
    if ws_root is not None:
        selected = _select_active_repo_subproject(ws_root, branch_hint=branch_hint)
        if selected is not None:
            return selected
        branch_clause = (
            f"with local branch '{branch_hint}' "
            if branch_hint
            else "with an active local branch or unuploaded commits "
        )
        raise RuntimeError(
            f"No git subproject {branch_clause}was found under Android repo workspace "
            f"'{ws_root}'. Verify `--branch` / check out your topic branch first, or pass "
            "`--repo <subproject-dir>` (`repo_dir`) pointing directly to the target Git repository."
        )

    if repo_dir is not None:
        return start_dir
    return Path(__file__).resolve().parents[3]


def _run_git_or_raise(
    args: List[str],
    cwd: Path,
    action_desc: str,
    hint: str,
    require_stdout: bool = False,
) -> str:
    """Runs a git subcommand in `cwd` and raises an actionable RuntimeError on failure."""
    try:
        proc = _run_git(args, cwd)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(
            f"Failed to {action_desc} in '{cwd}' ({exc}). {hint}"
        ) from exc
    if proc.returncode != 0 or (require_stdout and not proc.stdout.strip()):
        reason = proc.stderr.strip() or f"unknown git {args[0]} failure"
        raise RuntimeError(
            f"Failed to {action_desc} in '{cwd}' (exit code {proc.returncode}: {reason}). {hint}"
        )
    return proc.stdout


def fetch_git_commit_target(
    commit_ref: str = "HEAD",
    repo_dir: Optional[Path] = None,
    branch_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Extracts commit message, touched files, and unified diff from a local git commit."""
    resolved_dir = resolve_git_repo_dir(repo_dir=repo_dir, branch_hint=branch_hint)
    target_ref = branch_hint if (commit_ref == "HEAD" and branch_hint) else commit_ref
    if not target_ref or target_ref.startswith("-"):
        raise ValueError(
            f"Invalid git commit reference '{target_ref}' in '{resolved_dir}': "
            "revision names must be non-empty and must not start with '-'. "
            "Pass a valid commit SHA, branch name, or 'HEAD' to `--commit`."
        )

    commit_sha = _run_git_or_raise(
        ["rev-parse", "--verify", "--end-of-options", f"{target_ref}^{{commit}}"],
        resolved_dir, action_desc=f"read git commit '{target_ref}'",
        hint="Verify that the commit ref exists and pass `--repo <git-repo-dir>` pointing to the target Git repository.",
        require_stdout=True,
    ).strip()
    commit_msg = _run_git_or_raise(
        ["log", "-1", "--format=%B", commit_sha, "--"],
        resolved_dir, action_desc=f"read commit message for '{commit_sha}'",
        hint="Verify repository integrity and retry.",
    )
    diff_text = _run_git_or_raise(
        ["-c", "core.quotePath=false", "show", "--format=", "--unified=3", commit_sha, "--"],
        resolved_dir, action_desc=f"extract unified diff for git commit '{commit_sha}'",
        hint="Verify repository integrity and commit ref before re-running review-swarm.",
    )
    changed_by_file = parse_unified_diff_changed_lines(diff_text)
    subject = commit_msg.strip().splitlines()[0] if commit_msg.strip() else target_ref
    return {
        "target_type": "git_commit", "target_id": commit_sha, "project": resolved_dir.name,
        "repo_dir": str(resolved_dir), "branch": branch_hint or "HEAD", "subject": subject,
        "current_revision": commit_sha, "commit_message": commit_msg,
        "bug_ids": extract_bug_ids_from_commit_msg(commit_msg),
        "files": sorted(changed_by_file.keys()), "diff_text": diff_text,
        "changed_lines_by_file": serialize_changed_lines(changed_by_file),
    }
