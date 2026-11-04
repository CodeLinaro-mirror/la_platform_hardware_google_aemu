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

"""Version inspection, persistence, and update-needed comparison for emu-dev-cli."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any

from core.git import GitService, ProdGitService
from core.process import ProcessResult
from lib.path_utils import atomic_write_text, run_git_readonly

logger = logging.getLogger(__name__)

VERSION_FILENAME: str = "version.json"
UNKNOWN_COMMIT: str = "unknown"
SHORT_COMMIT_LEN: int = 8
GIT_TIMEOUT_SECONDS: float = 15.0

AEMU_REPO_CANDIDATE_SUBDIRS: tuple[tuple[str, ...], ...] = (
    ("hardware", "google", "aemu", "tools", "emu-dev-cli"),
    ("hardware", "google", "aemu"),
    ("tools", "emu-dev-cli"),
)


def _atomic_write_json(dest_path: Path, payload: dict[str, Any]) -> None:
    """Atomically writes a UTF-8 JSON object to `dest_path`."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(dest_path, serialized)


def _has_git_metadata(directory: Path) -> bool:
    """Returns True if `directory` or any ancestor contains `.git` or `GIT_DIR` is set."""
    if os.environ.get("GIT_DIR"):
        return True
    for candidate in (directory, *directory.parents):
        git_marker = candidate / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            return True
    return False


def _run_git(
    args: list[str], cwd: Path, *, git_service: GitService | None = None
) -> ProcessResult:
    """Runs a read-only git command with UTF-8 decoding and a bounded timeout."""
    try:
        return run_git_readonly(
            args, cwd, timeout=GIT_TIMEOUT_SECONDS, git_service=git_service
        )
    except OSError as exc:
        logger.debug(
            "Git command 'git %s' failed in '%s': %s", " ".join(args), cwd, exc
        )
        return ProcessResult(
            args=("git", *args), returncode=1, stdout="", stderr=str(exc)
        )


def resolve_emu_dev_cli_git_dir(source_dir: str | os.PathLike[str]) -> Path:
    """Resolves the directory containing the aemu git checkout within `source_dir`."""
    base = Path(source_dir).resolve()
    for rel_parts in AEMU_REPO_CANDIDATE_SUBDIRS:
        candidate = base.joinpath(*rel_parts)
        if candidate.is_dir():
            return candidate
    return base


def _unknown_source_version(resolved_source: str) -> dict[str, Any]:
    """Returns a fallback version metadata dictionary when git state is unavailable."""
    return {
        "commit": UNKNOWN_COMMIT,
        "commit_short": UNKNOWN_COMMIT,
        "commit_date": "",
        "commit_subject": "",
        "dirty": False,
        "source_dir": resolved_source,
    }


def inspect_source_version(
    source_dir: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    """Inspects git commit, timestamp, subject, and dirty status for `source_dir`."""
    if not source_dir:
        return _unknown_source_version("")
    source_path = Path(source_dir)
    resolved_source = str(source_path.resolve())
    if not source_path.exists():
        return _unknown_source_version(resolved_source)

    git_dir = resolve_emu_dev_cli_git_dir(source_path)
    if not _has_git_metadata(git_dir):
        return _unknown_source_version(resolved_source)

    rev_res = _run_git(["rev-parse", "HEAD"], git_dir)
    if rev_res.returncode != 0 or not isinstance(rev_res.stdout, str):
        return _unknown_source_version(resolved_source)
    commit = rev_res.stdout.strip()
    if not commit:
        return _unknown_source_version(resolved_source)

    log_res = _run_git(["log", "-1", "--format=%cI%n%s", "HEAD"], git_dir)
    log_lines = log_res.stdout.splitlines() if log_res.returncode == 0 else []
    commit_date = log_lines[0].strip() if len(log_lines) >= 1 else ""
    commit_subject = log_lines[1].strip() if len(log_lines) >= 2 else ""

    status_res = _run_git(["status", "--porcelain"], git_dir)
    dirty = status_res.returncode == 0 and bool(status_res.stdout.strip())

    return {
        "commit": commit,
        "commit_short": commit[:SHORT_COMMIT_LEN],
        "commit_date": commit_date,
        "commit_subject": commit_subject,
        "dirty": dirty,
        "source_dir": resolved_source,
    }


def get_version_file_path(release_dir: str | os.PathLike[str]) -> Path:
    """Returns the `version.json` path inside the release package directory."""
    return Path(release_dir) / VERSION_FILENAME


def build_version_record(
    source_version: dict[str, Any],
    installed_at: str | None = None,
) -> dict[str, Any]:
    """Builds a normalized `version.json` record with an ISO-8601 `installed_at` timestamp."""
    commit = str(source_version.get("commit") or UNKNOWN_COMMIT)
    default_short = (
        commit[:SHORT_COMMIT_LEN] if commit != UNKNOWN_COMMIT else UNKNOWN_COMMIT
    )
    commit_short = str(source_version.get("commit_short") or default_short)
    timestamp = installed_at or datetime.now(timezone.utc).isoformat()
    return {
        "commit": commit,
        "commit_short": commit_short,
        "commit_date": str(source_version.get("commit_date") or ""),
        "commit_subject": str(source_version.get("commit_subject") or ""),
        "dirty": bool(source_version.get("dirty", False)),
        "source_dir": str(source_version.get("source_dir") or ""),
        "installed_at": timestamp,
    }


def read_installed_version(
    release_dir: str | os.PathLike[str],
) -> dict[str, Any] | None:
    """Reads and validates `version.json` from `release_dir`, returning None if absent or invalid."""
    version_path = get_version_file_path(release_dir)
    if not version_path.is_file():
        return None
    try:
        data = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug(
            "Failed to read installed version metadata at '%s' (%s); "
            "treating installation as unversioned so 'emu-dev-cli update' refreshes it.",
            version_path,
            exc,
        )
        return None
    if not isinstance(data, dict):
        logger.debug(
            "Ignoring malformed version metadata at '%s' (expected JSON object, got %s).",
            version_path,
            type(data).__name__,
        )
        return None
    return data


def write_installed_version(
    release_dir: str | os.PathLike[str],
    source_dir: str | os.PathLike[str] | None = None,
    source_version: dict[str, Any] | None = None,
    installed_at: str | None = None,
) -> dict[str, Any]:
    """Writes `version.json` into `release_dir` and returns the persisted record."""
    inspected = (
        dict(source_version)
        if source_version is not None
        else inspect_source_version(source_dir)
    )
    record = build_version_record(inspected, installed_at=installed_at)
    version_path = get_version_file_path(release_dir)
    try:
        _atomic_write_json(version_path, record)
    except OSError as exc:
        logger.warning(
            "Failed to write emu-dev-cli version metadata to '%s' (%s). "
            "Check write permissions on '%s' and re-run 'emu-dev-cli update'.",
            version_path,
            exc,
            release_dir,
        )
    return record


def format_commit_label(version_info: dict[str, Any]) -> str:
    """Formats `{commit_short} ({commit_subject})` or `{commit_short}` when subject is absent."""
    commit_short = str(version_info.get("commit_short") or UNKNOWN_COMMIT)
    subject = str(version_info.get("commit_subject") or "").strip()
    if subject:
        return f"{commit_short} ({subject})"
    return commit_short


def compare_versions(
    installed: dict[str, Any] | None,
    source: dict[str, Any],
) -> tuple[bool, str]:
    """Compares installed `version.json` metadata against source git state.

    Returns:
        `(update_needed, reason)` explaining why an update is or is not needed.
    """
    if not installed:
        return True, "no previous version record found"

    installed_commit = str(installed.get("commit") or UNKNOWN_COMMIT)
    if installed_commit == UNKNOWN_COMMIT:
        return True, "installed version has no recorded git commit"

    source_commit = str(source.get("commit") or UNKNOWN_COMMIT)
    if source_commit == UNKNOWN_COMMIT:
        return True, "source directory has no verifiable git commit"

    installed_short = str(
        installed.get("commit_short") or installed_commit[:SHORT_COMMIT_LEN]
    )
    source_label = format_commit_label(source)
    if installed_commit != source_commit:
        return True, f"updating from {installed_short} to {source_label}"

    if bool(source.get("dirty")):
        return True, "local changes detected in source directory"

    if bool(installed.get("dirty")):
        return (
            True,
            f"installed build at {installed_short} included uncommitted local changes",
        )

    return False, f"emu-dev-cli is already at commit {source_label}"


def format_version_display(installed: dict[str, Any] | None) -> str:
    """Formats installed `version.json` metadata for `emu-dev-cli --version`."""
    if not installed:
        return (
            "emu-dev-cli (version unknown: no installed version record found; "
            "run 'emu-dev-cli update' to record version info)"
        )

    label = format_commit_label(installed)
    dirty_suffix = " [dirty]" if installed.get("dirty") else ""
    lines = [f"emu-dev-cli {label}{dirty_suffix}"]
    for key, title in (
        ("commit", "commit"),
        ("commit_date", "commit_date"),
        ("dirty", "dirty"),
        ("source_dir", "source_dir"),
        ("installed_at", "installed_at"),
    ):
        val = installed.get(key)
        if val is not None and val != "":
            lines.append(f"  {title}: {val}")
    return "\n".join(lines)
