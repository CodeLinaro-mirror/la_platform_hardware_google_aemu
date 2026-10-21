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

"""Pre-upload review-swarm gate evaluation and repository path resolution."""

import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Optional, Tuple

from commands.harness.constants import (
    GERRIT_UPLOAD_CMD_PATTERN,
    GIT_C_FLAG_PATTERN,
    GIT_COMMIT_MSG_FLAG_PATTERN,
    HELP_FLAG_PATTERN,
    REPO_DIR_CD_PATTERN,
    REPO_FLAG_PATTERN,
    REPO_UPLOAD_BRANCH_FLAG_PATTERN,
    REPO_UPLOAD_TAIL_PATTERN,
    SKIP_REVIEW_SWARM_PATTERN,
)
from commands.harness.review_swarm import (
    prepare_review_swarm_bundle,
    resolve_git_repo_dir,
)

REPO_UPLOAD_VALUE_FLAGS = frozenset({
    "-j",
    "--jobs",
    "--topic",
    "--hashtag",
    "--ht",
    "-l",
    "--label",
    "--pd",
    "--patchset-description",
    "--re",
    "--reviewers",
    "--cc",
    "--br",
    "--branch",
    "-o",
    "--push-option",
    "-D",
    "--destination",
    "--dest",
})


def _first_regex_group(match: Optional[re.Match[str]]) -> Optional[str]:
    """Returns the first non-empty captured group from a regex match, if any."""
    if not match:
        return None
    for group_val in match.groups():
        if group_val:
            return group_val
    return None


def _resolve_relative_to_base(raw_path: str, base_dir: Path) -> Path:
    """Resolves `raw_path` relative to `base_dir` unless `raw_path` is already absolute."""
    expanded = Path(raw_path).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base_dir / expanded).resolve()


def _extract_repo_upload_positional_dir(command: str, base_dir: Path) -> Optional[Path]:
    """Extracts a positional `<project>` directory from `repo upload [opts] <project>`."""
    tail_match = REPO_UPLOAD_TAIL_PATTERN.search(command)
    if not tail_match:
        return None
    try:
        tokens = [
            tok.strip("\"'")
            for tok in shlex.split(tail_match.group(1), posix=(os.name != "nt"))
        ]
    except ValueError:
        tokens = tail_match.group(1).split()

    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in REPO_UPLOAD_VALUE_FLAGS:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        resolved = _resolve_relative_to_base(tok, base_dir)
        if resolved.is_dir():
            return resolved
    return None


def _resolve_upload_repo_dir(command: str, default_cwd: Optional[Path] = None) -> Path:
    """Extracts the target Git repository path from a Gerrit upload shell command."""
    base_dir = (default_cwd or Path.cwd()).expanduser().resolve()
    branch_hint = _first_regex_group(REPO_UPLOAD_BRANCH_FLAG_PATTERN.search(command))
    cd_raw = _first_regex_group(REPO_DIR_CD_PATTERN.search(command))
    current_base = _resolve_relative_to_base(cd_raw, base_dir) if cd_raw else base_dir

    for pattern in (REPO_FLAG_PATTERN, GIT_C_FLAG_PATTERN):
        raw_dir = _first_regex_group(pattern.search(command))
        if raw_dir:
            candidate = _resolve_relative_to_base(raw_dir, current_base)
            return resolve_git_repo_dir(repo_dir=candidate, branch_hint=branch_hint)

    positional_dir = _extract_repo_upload_positional_dir(command, current_base)
    return resolve_git_repo_dir(
        repo_dir=positional_dir or current_base,
        branch_hint=branch_hint,
    )


def _read_review_payload_finding_count(
    payload_path: Path,
    target_id: str,
    script_path: str,
) -> Tuple[Optional[int], Optional[str]]:
    """Reads unresolved finding count from `payload_path`, failing closed on invalid JSON."""
    try:
        review_payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (
            None,
            "Blocked by AEMU Harness Pre-Upload Review-Swarm Gate: "
            f"Failed to read or parse review-swarm payload '{payload_path}' for commit "
            f"'{target_id}' ({exc}). Wait for `run_workflow(ScriptPath=\"{script_path}\")` "
            "to complete or re-run it to regenerate a valid JSON object before uploading.",
        )

    if not isinstance(review_payload, dict):
        return (
            None,
            "Blocked by AEMU Harness Pre-Upload Review-Swarm Gate: "
            f"Invalid review-swarm payload format in '{payload_path}' for commit '{target_id}' "
            f"(expected a JSON object, got {type(review_payload).__name__}). "
            f"Execute `run_workflow(ScriptPath=\"{script_path}\")` to regenerate a valid payload.",
        )

    comments_map = review_payload.get("comments")
    if comments_map is None:
        return 0, None
    bad_comments = (
        type(comments_map).__name__
        if not isinstance(comments_map, dict)
        else next(
            (f"key '{k}' with value type {type(v).__name__}" for k, v in comments_map.items() if not isinstance(v, list)),
            None,
        )
    )
    if bad_comments is not None:
        return (
            None,
            "Blocked by AEMU Harness Pre-Upload Review-Swarm Gate: "
            f"Invalid 'comments' field in '{payload_path}' for commit '{target_id}' "
            f"(expected a JSON object mapping file paths to comment lists, got {bad_comments}). "
            f"Execute `run_workflow(ScriptPath=\"{script_path}\")` to regenerate a valid payload.",
        )
    return sum(len(items) for items in comments_map.values()), None


def _verify_cached_review_payload(bundle: Dict[str, Any], repo_dir: Path) -> Tuple[bool, str]:
    """Evaluates the review-swarm payload status for a prepared commit bundle."""
    out_dir = Path(bundle["output_dir"])
    payload_path = out_dir / "gerrit_review_payload.json"
    script_path = bundle["workflow_script_path"]
    target_id = bundle["target_id"]
    subject = bundle.get("subject") or ""

    if not payload_path.is_file():
        return (
            False,
            "Blocked by AEMU Harness Pre-Upload Review-Swarm Gate:\n"
            f"  Local commit '{target_id}' ({subject}) in '{repo_dir}' has not been audited by the "
            "8-Specialist Parallel Code-Review Swarm before uploading to Gerrit.\n"
            f"  Auto-prepared 8-specialist review-swarm bundle at: {script_path}\n"
            f"Next step: Execute `run_workflow(ScriptPath=\"{script_path}\")` "
            "(or `aemu_prepare_review_swarm`), fix and amend any findings on HEAD, "
            "and re-run the upload command.",
        )

    finding_count, parse_err = _read_review_payload_finding_count(
        payload_path, target_id, script_path
    )
    if parse_err is not None:
        return False, parse_err

    if (finding_count or 0) > 0 and os.environ.get("AEMU_ALLOW_REVIEW_FINDINGS") != "1":
        report_path = out_dir / "review_swarm_report.md"
        return (
            False,
            "Blocked by AEMU Harness Pre-Upload Review-Swarm Gate:\n"
            f"  Local commit '{target_id}' ({subject}) has {finding_count} unresolved "
            f"review-swarm finding(s) in {report_path}.\n"
            "Next step: Address the findings, amend the commit, re-run the review-swarm on the "
            "amended HEAD commit, or set `AEMU_ALLOW_REVIEW_FINDINGS=1` to upload with open findings.",
        )

    return (
        True,
        f"Verified 8-Specialist Parallel Code-Review Swarm audit for HEAD commit '{target_id}' (0 blocking findings)",
    )


def evaluate_upload_command(
    command: str,
    review_swarm_cache_root: Optional[Path] = None,
    default_cwd: Optional[Path] = None,
) -> Optional[Tuple[bool, str]]:
    """Gates Gerrit uploads on a clean 8-Specialist Parallel Code-Review Swarm audit for `HEAD`."""
    unquoted_cmd = GIT_COMMIT_MSG_FLAG_PATTERN.sub("", command)
    if not GERRIT_UPLOAD_CMD_PATTERN.search(unquoted_cmd) or HELP_FLAG_PATTERN.search(unquoted_cmd):
        return None
    if SKIP_REVIEW_SWARM_PATTERN.search(command) or os.environ.get("AEMU_SKIP_REVIEW_SWARM") == "1":
        return (
            True,
            "Allowed Gerrit upload (pre-upload review-swarm bypassed via --skip-review-swarm / AEMU_SKIP_REVIEW_SWARM=1)",
        )

    branch_hint = _first_regex_group(REPO_UPLOAD_BRANCH_FLAG_PATTERN.search(unquoted_cmd))
    try:
        repo_dir = _resolve_upload_repo_dir(command, default_cwd=default_cwd)
        bundle = prepare_review_swarm_bundle(
            commit="HEAD",
            repo_dir=repo_dir,
            cache_root=review_swarm_cache_root,
            branch_hint=branch_hint,
        )
    except Exception as exc:
        fallback_dir = default_cwd or Path.cwd()
        return (
            False,
            f"Blocked by AEMU Harness Pre-Upload Review-Swarm Gate: "
            f"Could not inspect HEAD commit from '{fallback_dir}' ({exc}). "
            "Call MCP tool `aemu_prepare_review_swarm(target=\"HEAD\", repo_dir=\"<git-repo-path>\")` "
            "and execute `run_workflow(ScriptPath=...)` before uploading.",
        )
    return _verify_cached_review_payload(bundle, repo_dir)
