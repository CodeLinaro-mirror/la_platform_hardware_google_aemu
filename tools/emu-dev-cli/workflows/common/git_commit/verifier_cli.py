#!/usr/bin/env python3
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

"""Unified commit workflow verifier CLI (`verifier_cli.py`).

Consolidates the commit and amend workflow verification scripts into a single
strategy-driven runner supporting composable `--check` strategies:
  - `--check=clean_tree`
  - `--check=change_id_present`
  - `--check=footer_valid`

As well as workflow lifecycle modes (`--mode`):
  - `check`: Run one or more composable verification strategies.
  - `get-base-commit`: Print the current HEAD commit SHA (or `EMPTY`).
  - `setup-msg-files`: Initialize empty proposed/current commit message files.
  - `verify-proposed-msg`: Validate a proposed commit message file (and preserve Change-Id on amend).
  - `verify-commit`: Verify a newly created Git commit against its base commit and proposed message.
  - `verify-amend`: Verify an amended Git commit preserves its Change-Id and matches the proposed message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from core.git import GitService, ProdGitService
from workflows.common.git_commit.commit_verifier import (
    extract_all_change_ids,
    extract_change_id,
    find_commit_by_change_id,
    get_commit_message,
    get_head_commit_hash,
    is_ancestor_commit,
    messages_match,
    resolve_commit_hash,
    validate_commit_message,
    validate_commit_message_file,
)

CHECK_CLEAN_TREE = "clean_tree"
CHECK_CHANGE_ID_PRESENT = "change_id_present"
CHECK_FOOTER_VALID = "footer_valid"
ALL_CHECK_NAMES = (
    CHECK_CLEAN_TREE,
    CHECK_CHANGE_ID_PRESENT,
    CHECK_FOOTER_VALID,
)


def _git(
    git_service: Optional[GitService] = None, cwd: Optional[str] = None
) -> GitService:
    return git_service or ProdGitService(default_cwd=cwd)


def validate_git_repository(
    cwd: Optional[str], *, git_service: Optional[GitService] = None
) -> bool:
    """Validates that `cwd` exists and is a valid Git repository."""
    if not cwd:
        return True
    if not os.path.isdir(cwd):
        print(
            f"Error: Specified repository directory does not exist: '{cwd}'",
            file=sys.stderr,
        )
        return False
    res = _git(git_service, cwd=cwd).run_git(
        ["rev-parse", "--git-dir"], cwd=cwd, check=False
    )
    if res.returncode != 0:
        print(
            f"Error: Specified directory is not a Git repository: '{cwd}'",
            file=sys.stderr,
        )
        return False
    return True


@dataclass(frozen=True)
class VerifierContext:
    """Input context passed to `VerifierStrategy.verify`."""

    cwd: Optional[str] = None
    commit_ref: str = "HEAD"
    message_text: Optional[str] = None
    expected_change_id: Optional[str] = None
    include_untracked: bool = False
    git_service: GitService = field(default_factory=ProdGitService)

    def resolve_message(self) -> str:
        """Returns `message_text` if provided, otherwise reads the commit message at `commit_ref`."""
        if self.message_text is not None:
            return self.message_text
        return get_commit_message(
            self.commit_ref, cwd=self.cwd, git_service=self.git_service
        )


class VerifierStrategy(ABC):
    """Strategy interface for modular commit/worktree verification checks."""

    name: str

    @abstractmethod
    def verify(self, ctx: VerifierContext) -> list[str]:
        """Returns a list of human-readable error strings (empty when valid)."""


class CleanTreeCheckStrategy(VerifierStrategy):
    """Verifies that the Git working tree has no uncommitted modifications."""

    name = CHECK_CLEAN_TREE

    def verify(self, ctx: VerifierContext) -> list[str]:
        status_out = ctx.git_service.status_porcelain(
            cwd=ctx.cwd, untracked=ctx.include_untracked
        )
        if status_out.strip():
            return [
                "Working tree has uncommitted changes:\n"
                f"{status_out.strip()}\n"
                "Please stage and commit or amend all changes before continuing."
            ]
        return []


class ChangeIdPresentCheckStrategy(VerifierStrategy):
    """Verifies that a single valid Gerrit `Change-Id` footer is present (and matches `expected_change_id` if given)."""

    name = CHECK_CHANGE_ID_PRESENT

    def verify(self, ctx: VerifierContext) -> list[str]:
        try:
            msg = ctx.resolve_message()
        except Exception as exc:
            return [f"Could not read commit message for '{ctx.commit_ref}': {exc}"]

        change_ids = extract_all_change_ids(msg)
        if not change_ids:
            if ctx.expected_change_id and ctx.expected_change_id != "NONE":
                return [
                    f"Change-Id line is missing. Expected: 'Change-Id: {ctx.expected_change_id}'."
                ]
            return [
                "Commit message is missing a Gerrit 'Change-Id: I<40-hex>' footer line."
            ]
        if len(change_ids) > 1:
            return [
                f"Multiple 'Change-Id' lines found in commit message ({len(change_ids)}). "
                "Exactly one Change-Id line is allowed."
            ]
        if (
            ctx.expected_change_id
            and ctx.expected_change_id != "NONE"
            and change_ids[0] != ctx.expected_change_id
        ):
            return [
                f"Change-Id must not be modified. Expected: '{ctx.expected_change_id}', "
                f"but found: '{change_ids[0]}'."
            ]
        return []


class FooterValidCheckStrategy(VerifierStrategy):
    """Verifies Semantic Commit subject, body wrapping, Bug:/Test: footers, and forbidden tags."""

    name = CHECK_FOOTER_VALID

    def verify(self, ctx: VerifierContext) -> list[str]:
        try:
            msg = ctx.resolve_message()
        except Exception as exc:
            return [f"Could not read commit message for '{ctx.commit_ref}': {exc}"]
        return validate_commit_message(
            msg,
            expected_change_id=(
                ctx.expected_change_id
                if ctx.expected_change_id and ctx.expected_change_id != "NONE"
                else None
            ),
        )


STRATEGY_REGISTRY: dict[str, VerifierStrategy] = {
    CHECK_CLEAN_TREE: CleanTreeCheckStrategy(),
    CHECK_CHANGE_ID_PRESENT: ChangeIdPresentCheckStrategy(),
    CHECK_FOOTER_VALID: FooterValidCheckStrategy(),
}


def run_check_strategies(
    checks: Sequence[str],
    ctx: VerifierContext,
) -> list[str]:
    """Executes the requested check strategies in order and aggregates any errors."""
    errors: list[str] = []
    normalized: list[str] = []
    for raw in checks:
        for token in raw.split(","):
            tok = token.strip()
            if tok:
                normalized.append(tok)

    for check_name in normalized:
        strategy = STRATEGY_REGISTRY.get(check_name)
        if strategy is None:
            errors.append(
                f"Unknown verifier check strategy '{check_name}'. "
                f"Supported checks: {', '.join(ALL_CHECK_NAMES)}."
            )
            continue
        errors.extend(strategy.verify(ctx))
    return errors


def get_base_commit(
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> int:
    """Outputs the current Git HEAD commit hash or 'EMPTY' after validating `cwd`."""
    git = _git(git_service, cwd=cwd)
    if not validate_git_repository(cwd, git_service=git):
        return 1
    commit_hash = get_head_commit_hash(cwd=cwd, git_service=git)
    print(commit_hash)
    return 0


def setup_msg_files(
    first_file: str,
    second_file: Optional[str] = None,
) -> int:
    """Initializes empty proposed (and optional current) commit message files in the workflow cache."""
    for raw_path in (first_file, second_file):
        if not raw_path:
            continue
        target_path = Path(raw_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_path.write_text("", encoding="utf-8")
    print("OK")
    return 0


def verify_proposed_msg(
    filepath: str,
    expected_change_id: str = "NONE",
    current_file: str = "",
    base_commit_file: str = "",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> int:
    """Verifies that the proposed commit message file satisfies Gerrit rules (and preserves Change-Id when amending)."""
    git = _git(git_service, cwd=cwd)
    change_id = (
        expected_change_id
        if expected_change_id and expected_change_id != "NONE"
        else None
    )

    if current_file:
        current_path = Path(current_file).resolve()
        if not current_path.is_file():
            print(
                f"Error: Current commit message file '{current_file}' does not exist.",
                file=sys.stderr,
            )
            return 1
        current_text = current_path.read_text(encoding="utf-8")
        if not current_text.strip():
            print(
                f"Error: Current commit message file '{current_file}' is empty. "
                "Please copy the current commit message into this file.",
                file=sys.stderr,
            )
            return 1
        cids = extract_all_change_ids(current_text)
        if not cids:
            print(
                f"Error: No Gerrit Change-Id found in current commit message file '{current_file}'.",
                file=sys.stderr,
            )
            return 1
        if len(cids) > 1:
            print(
                f"Error: Multiple 'Change-Id' lines found in current commit message file '{current_file}'.",
                file=sys.stderr,
            )
            return 1
        change_id = cids[0]

        if base_commit_file:
            base_hash = find_commit_by_change_id(
                change_id, cwd=cwd, git_service=git
            )
            if base_hash:
                b_path = Path(base_commit_file).resolve()
                b_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                b_path.write_text(base_hash.strip() + "\n", encoding="utf-8")

    errors = validate_commit_message_file(
        filepath, expected_change_id=change_id
    )
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    print("Proposed commit message verified successfully.")
    return 0


def _verify_matches_proposed_message(
    msg: str,
    proposed_file: str,
    *,
    allow_added_change_id: bool = True,
) -> bool:
    """Verifies that the commit message in Git matches the verified proposed message file."""
    if not proposed_file:
        return True
    try:
        proposed_path = Path(proposed_file)
        if not proposed_path.is_file():
            print(
                f"Error: Proposed message file '{proposed_file}' does not exist.",
                file=sys.stderr,
            )
            return False
        proposed_text = proposed_path.read_text(encoding="utf-8")
        if not messages_match(
            msg, proposed_text, allow_added_change_id=allow_added_change_id
        ):
            print(
                f"Error: Commit message in Git does not match verified message in '{proposed_file}'.",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as exc:
        print(
            f"Error reading proposed message file '{proposed_file}': {exc}",
            file=sys.stderr,
        )
        return False


def verify_commit(
    base_commit: str = "",
    proposed_file: str = "",
    msg: str = "",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> int:
    """Verifies that a new commit exists and satisfies all commit message rules."""
    git = _git(git_service, cwd=cwd)
    if not msg:
        current_head = get_head_commit_hash(cwd=cwd, git_service=git)
        if current_head == "EMPTY":
            print(
                "Error: No commit found in repository. Please ensure a commit has been created.",
                file=sys.stderr,
            )
            return 1

        if (
            base_commit
            and base_commit != "EMPTY"
            and current_head == base_commit
        ):
            print(
                f"Error: No new commit has been created. Current HEAD matches base commit {base_commit}.",
                file=sys.stderr,
            )
            return 1

        try:
            msg = get_commit_message("HEAD", cwd=cwd, git_service=git)
        except Exception as exc:
            print(f"Error reading commit message: {exc}", file=sys.stderr)
            return 1

    ctx = VerifierContext(
        cwd=cwd,
        commit_ref="HEAD",
        message_text=msg,
        git_service=git,
    )
    errors = run_check_strategies([CHECK_FOOTER_VALID], ctx)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if not _verify_matches_proposed_message(
        msg, proposed_file, allow_added_change_id=True
    ):
        return 1

    print("Commit verification passed.")
    return 0


def _resolve_change_id_from_file(
    original_change_id: str, current_file: str
) -> str:
    """Resolves Gerrit Change-Id from current commit message file if not explicitly supplied."""
    if (not original_change_id or original_change_id == "NONE") and current_file:
        try:
            current_path = Path(current_file).resolve()
            if current_path.is_file():
                current_text = current_path.read_text(encoding="utf-8")
                cids = extract_all_change_ids(current_text)
                if cids:
                    return cids[0]
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Ignored error in _resolve_change_id_from_file: %s", exc
            )
    return original_change_id


def _resolve_base_commit_from_file(
    base_commit: str, base_commit_file: str
) -> str:
    """Resolves base commit hash from file if not explicitly supplied."""
    if (not base_commit or base_commit == "EMPTY") and base_commit_file:
        try:
            b_path = Path(base_commit_file).resolve()
            if b_path.is_file():
                return b_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Ignored error in _resolve_base_commit_from_file: %s", exc
            )
    return base_commit


def _locate_amended_commit(
    cwd: Optional[str],
    commit_ref: str,
    original_change_id: str,
    base_commit: str,
    *,
    git_service: Optional[GitService] = None,
) -> Optional[str]:
    """Locates the amended commit and verifies it was amended rather than duplicated or unmodified."""
    git = _git(git_service, cwd=cwd)
    if original_change_id and original_change_id != "NONE":
        found_commit = find_commit_by_change_id(
            original_change_id, cwd=cwd, git_service=git
        )
        if not found_commit:
            print(
                f"Error: No commit with Change-Id '{original_change_id}' found in repository.",
                file=sys.stderr,
            )
            return None

        if (
            base_commit
            and base_commit != "EMPTY"
            and found_commit == base_commit
        ):
            ref_label = (
                f"'{commit_ref}' ({base_commit[:8]})"
                if commit_ref
                else f"'{base_commit[:8]}'"
            )
            print(
                f"Error: Commit {ref_label} has not been amended yet. Commit hash is unchanged.",
                file=sys.stderr,
            )
            return None

        if (
            base_commit
            and base_commit != "EMPTY"
            and is_ancestor_commit(
                base_commit, found_commit, cwd=cwd, git_service=git
            )
        ):
            print(
                f"Error: A new commit ({found_commit[:8]}) was created on top of base commit "
                f"{base_commit[:8]} instead of amending it.",
                file=sys.stderr,
            )
            return None

        return found_commit

    resolved = resolve_commit_hash(
        commit_ref or "HEAD", cwd=cwd, git_service=git
    )
    if resolved == "EMPTY":
        print(
            f"Error: Cannot resolve commit '{commit_ref or 'HEAD'}' in repository.",
            file=sys.stderr,
        )
        return None

    if base_commit and base_commit != "EMPTY" and resolved == base_commit:
        ref_label = (
            f"'{commit_ref}' ({base_commit[:8]})"
            if commit_ref
            else f"'{base_commit[:8]}'"
        )
        print(
            f"Error: Commit {ref_label} has not been amended yet. Commit hash is unchanged.",
            file=sys.stderr,
        )
        return None

    if (
        base_commit
        and base_commit != "EMPTY"
        and is_ancestor_commit(base_commit, resolved, cwd=cwd, git_service=git)
    ):
        print(
            f"Error: A new commit ({resolved[:8]}) was created on top of base commit "
            f"{base_commit[:8]} instead of amending it.",
            file=sys.stderr,
        )
        return None

    return resolved


def _verify_commit_message_changed_from_original(
    msg: str,
    current_file: str,
    proposed_file: str,
) -> bool:
    """Verifies that the commit message has actually changed when an amended message was proposed."""
    if not current_file:
        return True
    try:
        current_path = Path(current_file).resolve()
        if current_path.is_file():
            current_text = current_path.read_text(encoding="utf-8")
            if current_text.strip() and messages_match(msg, current_text):
                if proposed_file:
                    p_path = Path(proposed_file).resolve()
                    if p_path.is_file():
                        p_text = p_path.read_text(encoding="utf-8")
                        if not messages_match(current_text, p_text):
                            print(
                                "Error: Commit has not been amended yet. Commit message still matches "
                                f"original message in '{current_file}'.",
                                file=sys.stderr,
                            )
                            return False
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Ignored error in _verify_commit_message_changed_from_original: %s",
            exc,
        )
    return True


def verify_amend(
    base_commit: str = "",
    original_change_id: str = "NONE",
    commit_ref: str = "HEAD",
    proposed_file: str = "",
    current_file: str = "",
    base_commit_file: str = "",
    msg: str = "",
    cwd: Optional[str] = None,
    *,
    git_service: Optional[GitService] = None,
) -> int:
    """Verifies that a commit was amended and that the message and Change-Id are valid."""
    git = _git(git_service, cwd=cwd)
    original_change_id = _resolve_change_id_from_file(
        original_change_id, current_file
    )
    base_commit = _resolve_base_commit_from_file(base_commit, base_commit_file)

    if not msg:
        if not validate_git_repository(cwd, git_service=git):
            return 1

        target_to_inspect = _locate_amended_commit(
            cwd=cwd,
            commit_ref=commit_ref,
            original_change_id=original_change_id,
            base_commit=base_commit,
            git_service=git,
        )
        if not target_to_inspect:
            return 1

        try:
            msg = get_commit_message(
                target_to_inspect, cwd=cwd, git_service=git
            )
        except Exception as exc:
            print(
                f"Error reading commit message for '{target_to_inspect}': {exc}",
                file=sys.stderr,
            )
            return 1

        if not _verify_commit_message_changed_from_original(
            msg, current_file, proposed_file
        ):
            return 1

    ctx = VerifierContext(
        cwd=cwd,
        commit_ref=commit_ref,
        message_text=msg,
        expected_change_id=original_change_id,
        git_service=git,
    )
    errors = run_check_strategies([CHECK_FOOTER_VALID], ctx)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if not _verify_matches_proposed_message(
        msg, proposed_file, allow_added_change_id=False
    ):
        return 1

    print("Commit amendment verification passed.")
    return 0


def _read_optional_msg_file(msg: str, msg_file: str) -> tuple[Optional[str], int]:
    """Reads `--msg-file` if supplied, returning `(message_text, exit_code)`."""
    if not msg_file:
        return msg, 0
    try:
        return Path(msg_file).read_text(encoding="utf-8"), 0
    except Exception as exc:
        print(f"Error reading message file '{msg_file}': {exc}", file=sys.stderr)
        return None, 1


def build_arg_parser() -> argparse.ArgumentParser:
    """Constructs the unified argument parser for `verifier_cli.py`."""
    parser = argparse.ArgumentParser(
        description="Unified commit workflow verifier CLI with composable strategies."
    )
    parser.add_argument(
        "--mode",
        choices=(
            "check",
            "get-base-commit",
            "setup-msg-files",
            "verify-proposed-msg",
            "verify-commit",
            "verify-amend",
        ),
        default="check",
        help="Verification lifecycle mode to execute (default: check).",
    )
    parser.add_argument(
        "--check",
        dest="checks",
        action="append",
        default=[],
        help=(
            "Verification strategy to run (repeatable or comma-separated): "
            "clean_tree, change_id_present, footer_valid."
        ),
    )
    parser.add_argument(
        "positional",
        nargs="*",
        help="Mode-specific positional arguments (file paths, base_commit, or original_change_id).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for git operations.",
    )
    parser.add_argument(
        "--commit",
        default="HEAD",
        help="Target commit reference (default: HEAD).",
    )
    parser.add_argument(
        "--current-file",
        default="",
        help="Path to current commit message file to extract Change-Id from.",
    )
    parser.add_argument(
        "--proposed-file",
        default="",
        help="Path to verified proposed commit message file to match against Git commit.",
    )
    parser.add_argument(
        "--base-commit-file",
        default="",
        help="Path to file containing resolved base commit hash before amendment.",
    )
    parser.add_argument(
        "--msg",
        default="",
        help="Direct commit message content to verify (for unit testing).",
    )
    parser.add_argument(
        "--msg-file",
        default="",
        help="Path to file containing commit message to verify (for testing).",
    )
    parser.add_argument(
        "--expected-change-id",
        default="NONE",
        help="Expected Gerrit Change-Id footer value.",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files when running --check=clean_tree.",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    git_service: Optional[GitService] = None,
) -> int:
    """Unified CLI entrypoint for `verifier_cli.py` returning an integer exit code."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    pos = list(args.positional)

    if args.mode == "get-base-commit":
        return get_base_commit(cwd=args.cwd, git_service=git_service)

    if args.mode == "setup-msg-files":
        if not pos:
            parser.error("setup-msg-files requires at least one file path.")
        first_file = pos[0]
        second_file = pos[1] if len(pos) > 1 else None
        return setup_msg_files(first_file, second_file)

    if args.mode == "verify-proposed-msg":
        filepath = pos[0] if pos else args.proposed_file
        if not filepath:
            parser.error("verify-proposed-msg requires a proposed_msg_file path.")
        expected_cid = (
            pos[1]
            if len(pos) > 1
            else (args.expected_change_id or "NONE")
        )
        return verify_proposed_msg(
            filepath=filepath,
            expected_change_id=expected_cid,
            current_file=args.current_file,
            base_commit_file=args.base_commit_file,
            cwd=args.cwd,
            git_service=git_service,
        )

    if args.mode == "verify-commit":
        base_commit = pos[0] if pos else ""
        message, err_rc = _read_optional_msg_file(args.msg, args.msg_file)
        if err_rc != 0 or message is None:
            return err_rc
        return verify_commit(
            base_commit=base_commit,
            proposed_file=args.proposed_file,
            msg=message,
            cwd=args.cwd,
            git_service=git_service,
        )

    if args.mode == "verify-amend":
        base_commit = pos[0] if pos else ""
        original_cid = (
            pos[1]
            if len(pos) > 1
            else (args.expected_change_id or "NONE")
        )
        message, err_rc = _read_optional_msg_file(args.msg, args.msg_file)
        if err_rc != 0 or message is None:
            return err_rc
        return verify_amend(
            base_commit=base_commit,
            original_change_id=original_cid,
            commit_ref=args.commit,
            proposed_file=args.proposed_file,
            current_file=args.current_file,
            base_commit_file=args.base_commit_file,
            msg=message,
            cwd=args.cwd,
            git_service=git_service,
        )

    checks = args.checks or [CHECK_CLEAN_TREE]
    message, err_rc = _read_optional_msg_file(args.msg, args.msg_file)
    if err_rc != 0:
        return err_rc
    msg_text: Optional[str] = message if (args.msg or args.msg_file) else None
    if msg_text is None and args.proposed_file:
        if not os.path.exists(args.proposed_file):
            print(
                f"Error: Message file '{args.proposed_file}' does not exist.",
                file=sys.stderr,
            )
            return 1
        msg_text = Path(args.proposed_file).read_text(encoding="utf-8")

    ctx = VerifierContext(
        cwd=args.cwd,
        commit_ref=args.commit or "HEAD",
        message_text=msg_text,
        expected_change_id=(
            args.expected_change_id
            if args.expected_change_id and args.expected_change_id != "NONE"
            else None
        ),
        include_untracked=bool(args.include_untracked),
        git_service=_git(git_service, cwd=args.cwd),
    )
    errors = run_check_strategies(checks, ctx)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1
    return 0


def run_cli_entrypoint(default_argv_prefix: Sequence[str] = ()) -> None:
    """Helper for CLI entrypoints to invoke `main()` without scattered `sys.exit` calls."""
    raise SystemExit(main([*default_argv_prefix, *sys.argv[1:]]))


if __name__ == "__main__":
    run_cli_entrypoint()
