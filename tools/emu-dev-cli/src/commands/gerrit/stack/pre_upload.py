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

"""`emu-dev-cli gerrit stack pre-upload`: shift-left review checks before uploading."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from commands.gerrit.stack.pre_upload_checks import (
    CHECK_NAMES,
    DEFAULT_CHECKS,
    ChangedFile,
    Finding,
    PreUploadCheck,
    PreUploadTarget,
    Severity,
)
from commands.harness.code_guardrails import is_goldfish_root_dir
from commands.harness.review_targets import parse_unified_diff_hunk_entries
from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import ExecutionError, UsageError
from core.git import GitService

DEFAULT_STACK_BASE = "@{upstream}"
MAX_STACK_COMMITS = 20
MAX_FILE_CONTENT_BYTES = 1_000_000
SHORT_SHA_LEN = 7
WORKING_TREE_LABEL = "working tree"

MODE_STACK = "stack"
MODE_COMMIT = "commit"
MODE_WORKING_TREE = "working_tree"

_SHOW_DIFF_ARGS = ("-c", "core.quotePath=false", "show", "--format=", "--unified=3")
_WORKING_TREE_DIFF_ARGS = ("-c", "core.quotePath=false", "diff", "--unified=3", "HEAD", "--")
_UNTRACKED_ARGS = ("ls-files", "--others", "--exclude-standard", "-z")


def added_lines_by_file(diff_text: str) -> dict[str, dict[int, str]]:
    """Maps each file in a unified diff to `{new_line_number: added_text}`."""
    added: dict[str, dict[int, str]] = {}
    for path, entries in parse_unified_diff_hunk_entries(diff_text).items():
        added[path] = {line: raw[1:] for line, raw in entries if raw.startswith("+")}
    return added


class TargetCollector:
    """Reads the commits or working-tree changes to audit through `GitService`."""

    def __init__(self, git: GitService, repo_root: Path) -> None:
        self._git = git
        self._root = repo_root
        self._is_goldfish = is_goldfish_root_dir(repo_root)

    def _git_stdout(self, args: Sequence[str], action: str) -> str:
        result = self._git.run_git(list(args), cwd=self._root)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            reason = f": {detail[0]}" if detail else ""
            raise ExecutionError(
                f"git could not {action} in '{self._root}' (exit code {result.returncode}){reason}.",
                remediation="Check that the revision exists in this repository and retry.",
            )
        return result.stdout

    def resolve(self, ref: str) -> str:
        """Returns the commit SHA of `ref`, or an empty string when it does not resolve."""
        return self._git.rev_parse(f"{ref}^{{commit}}", cwd=self._root, verify=True)

    def commit_target(self, sha: str) -> PreUploadTarget:
        """Builds the audit target for one commit: its message, added lines, and new contents."""
        diff = self._git_stdout([*_SHOW_DIFF_ARGS, sha, "--"], f"show the diff of {sha}")
        files = tuple(
            ChangedFile(path, lines, self._content_at(sha, path))
            for path, lines in added_lines_by_file(diff).items()
            if lines
        )
        return PreUploadTarget(
            label=sha[:SHORT_SHA_LEN],
            commit=sha,
            commit_message=self._git.commit_message(sha, cwd=self._root),
            files=files,
            is_goldfish=self._is_goldfish,
        )

    def _content_at(self, sha: str, path: str) -> str:
        """Returns the file's text at `sha`, or "" when it is missing or too large to parse."""
        result = self._git.run_git(["show", f"{sha}:{path}"], cwd=self._root)
        if result.returncode != 0 or len(result.stdout) > MAX_FILE_CONTENT_BYTES:
            return ""
        return result.stdout

    def stack_shas(self, base: str) -> list[str]:
        """Lists the local commits in `base..HEAD`, oldest first."""
        listing = self._git_stdout(
            ["rev-list", "--reverse", "--first-parent", f"{base}..HEAD"],
            f"list the commits in {base}..HEAD",
        )
        shas = [line.strip() for line in listing.splitlines() if line.strip()]
        if len(shas) > MAX_STACK_COMMITS:
            raise UsageError(
                f"{base}..HEAD contains {len(shas)} commits, more than the "
                f"{MAX_STACK_COMMITS} a stack audit checks.",
                remediation="Pass --base <ref> closer to your work, or --commit <ref> for one commit.",
            )
        return shas

    def working_tree_target(self, message: str | None) -> PreUploadTarget:
        """Builds the audit target for staged, unstaged, and untracked changes against HEAD."""
        diff = self._git_stdout(list(_WORKING_TREE_DIFF_ARGS), "diff the working tree against HEAD")
        files = [
            ChangedFile(path, lines, self._read_working_file(path))
            for path, lines in added_lines_by_file(diff).items()
            if lines
        ]
        files.extend(self._untracked_files())
        return PreUploadTarget(
            label=WORKING_TREE_LABEL,
            commit="",
            commit_message=message,
            files=tuple(files),
            is_goldfish=self._is_goldfish,
        )

    def _read_working_file(self, path: str) -> str:
        file_path = self._root / path
        try:
            if file_path.stat().st_size > MAX_FILE_CONTENT_BYTES:
                return ""
            return file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _untracked_files(self) -> Iterator[ChangedFile]:
        listing = self._git_stdout(list(_UNTRACKED_ARGS), "list untracked files")
        for path in filter(None, listing.split("\0")):
            content = self._read_working_file(path)
            if content:
                lines = {index + 1: text for index, text in enumerate(content.splitlines())}
                yield ChangedFile(path, lines, content)


@dataclass(frozen=True)
class TargetResult:
    """Findings for one audited commit (or the working tree)."""

    label: str
    commit: str
    subject: str
    findings: tuple[Finding, ...]
    skipped: tuple[str, ...]

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "commit": self.commit,
            "subject": self.subject,
            "errors": self.count(Severity.ERROR),
            "warnings": self.count(Severity.WARNING),
            "skipped": list(self.skipped),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class PreUploadReport(Mapping[str, Any]):
    """Structured output of `gerrit stack pre-upload`."""

    repo: str
    mode: str
    base: str
    checks: tuple[str, ...]
    strict: bool
    targets: tuple[TargetResult, ...]

    @property
    def errors(self) -> int:
        return sum(t.count(Severity.ERROR) for t in self.targets)

    @property
    def warnings(self) -> int:
        return sum(t.count(Severity.WARNING) for t in self.targets)

    @property
    def passed(self) -> bool:
        """False when an error, or under `--strict` a warning, blocks the upload."""
        return self.errors == 0 and not (self.strict and self.warnings)

    @property
    def summary(self) -> str:
        count = len(self.targets)
        if not count:
            return (
                f"Nothing to audit: {self.base}..HEAD has no commits. "
                "Pass --commit <ref> or --working-tree to audit something else."
            )
        noun = "target" if count == 1 else "targets"
        verdict = "ready to upload" if self.passed else "fix before uploading"
        return (
            f"Pre-upload audit of {count} {noun}: {self.errors} error(s), "
            f"{self.warnings} warning(s); {verdict}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "mode": self.mode,
            "base": self.base,
            "checks": list(self.checks),
            "strict": self.strict,
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "targets": [target.to_dict() for target in self.targets],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


_SEVERITY_MARKS = {Severity.ERROR: "✗", Severity.WARNING: "⚠"}


def render_pre_upload_report(report: PreUploadReport) -> str:
    """Renders findings grouped by commit, each with its location and fix."""
    lines = [f"Checks: {', '.join(report.checks)}"]
    for target in report.targets:
        heading = f"{target.label} {target.subject}".strip()
        lines.append(f"\n{heading}")
        if not target.findings:
            lines.append("  ✓ no findings")
        for finding in target.findings:
            location = f" {finding.location}:" if finding.location else ""
            lines.append(
                f"  {_SEVERITY_MARKS[finding.severity]} [{finding.check}]{location} {finding.message}"
            )
            lines.append(f"      fix: {finding.remediation}")
        lines.extend(f"  - skipped {reason}" for reason in target.skipped)
    return "\n".join(lines)


@dataclass(frozen=True)
class PreUploadArgs:
    """Validated arguments for `StackPreUploadCommand`."""

    repo: str = "."
    commit: str = ""
    base: str = ""
    working_tree: bool = False
    message_file: str = ""
    checks: tuple[str, ...] = ()
    strict: bool = False


def run_checks(target: PreUploadTarget, checks: Sequence[PreUploadCheck]) -> TargetResult:
    """Runs every check on `target`, recording the ones that do not apply."""
    findings: list[Finding] = []
    skipped: list[str] = []
    for check in checks:
        reason = check.skip_reason(target)
        if reason:
            skipped.append(f"{check.name}: {reason}")
            continue
        findings.extend(check.run(target))
    return TargetResult(
        label=target.label,
        commit=target.commit,
        subject=target.subject,
        findings=tuple(findings),
        skipped=tuple(skipped),
    )


class StackPreUploadCommand(Command[PreUploadArgs, PreUploadReport]):
    """Audits local commits for commit-message, manifest, error-message, and reuse issues."""

    name = "pre-upload"
    help_text = "Run shift-left review checks on local commits before uploading them"

    def __init__(self, checks: Sequence[PreUploadCheck] = DEFAULT_CHECKS) -> None:
        self._checks = tuple(checks)

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--repo", default=".", help="Git repository to audit (default: .)")
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument(
            "--commit",
            default="",
            help="Audit only this commit instead of the whole local stack",
        )
        scope.add_argument(
            "--base",
            default="",
            help=f"Audit the commits in <base>..HEAD (default: {DEFAULT_STACK_BASE})",
        )
        scope.add_argument(
            "--working-tree",
            action="store_true",
            help="Audit uncommitted changes (staged, unstaged, and untracked) instead of commits",
        )
        parser.add_argument(
            "--message-file",
            default="",
            help="Draft commit message to check together with --working-tree",
        )
        parser.add_argument(
            "--check",
            dest="checks",
            action="append",
            choices=CHECK_NAMES,
            default=None,
            help="Run only this check (repeatable; default: all)",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail on warnings as well as errors",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON",
        )

    def validate(
        self, args: PreUploadArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> PreUploadArgs:
        del ctx
        if isinstance(args, PreUploadArgs):
            candidate = args
        else:
            candidate = PreUploadArgs(
                repo=str(getattr(args, "repo", ".") or "."),
                commit=str(getattr(args, "commit", "") or "").strip(),
                base=str(getattr(args, "base", "") or "").strip(),
                working_tree=bool(getattr(args, "working_tree", False)),
                message_file=str(getattr(args, "message_file", "") or "").strip(),
                checks=tuple(getattr(args, "checks", None) or ()),
                strict=bool(getattr(args, "strict", False)),
            )
        unknown = sorted(set(candidate.checks) - set(CHECK_NAMES))
        if unknown:
            raise UsageError(
                f"Unknown pre-upload check(s): {', '.join(unknown)}.",
                remediation=f"Use one of: {', '.join(CHECK_NAMES)}.",
            )
        scopes = [bool(candidate.commit), bool(candidate.base), candidate.working_tree]
        if sum(scopes) > 1:
            raise UsageError(
                "--commit, --base, and --working-tree select different targets.",
                remediation="Pass at most one of them.",
            )
        if candidate.message_file and not candidate.working_tree:
            raise UsageError(
                "--message-file only applies to --working-tree, because commits already have a message.",
                remediation="Add --working-tree, or amend the commit message instead.",
            )
        return candidate

    def _selected_checks(self, names: Sequence[str]) -> tuple[PreUploadCheck, ...]:
        if not names:
            return self._checks
        return tuple(check for check in self._checks if check.name in names)

    @staticmethod
    def _read_message_file(path: str) -> str:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(
                f"Cannot read --message-file '{path}': {exc.strerror or exc}.",
                remediation="Pass the path of a readable UTF-8 text file.",
            ) from exc

    def _targets(self, args: PreUploadArgs, collector: TargetCollector) -> tuple[str, str, list[PreUploadTarget]]:
        """Returns `(mode, base, targets)` for the requested audit scope."""
        if args.working_tree:
            message = self._read_message_file(args.message_file) if args.message_file else None
            return MODE_WORKING_TREE, "", [collector.working_tree_target(message)]
        if args.commit:
            sha = collector.resolve(args.commit)
            if not sha:
                raise UsageError(
                    f"--commit '{args.commit}' is not a commit in this repository.",
                    remediation="Pass a commit SHA, branch, or ref such as HEAD~1.",
                )
            return MODE_COMMIT, "", [collector.commit_target(sha)]
        base = args.base or DEFAULT_STACK_BASE
        if not collector.resolve(base):
            if args.base:
                raise UsageError(
                    f"--base '{args.base}' is not a commit in this repository.",
                    remediation="Pass a commit SHA, branch, or remote ref such as origin/studio-main.",
                )
            return MODE_COMMIT, "", [collector.commit_target(collector.resolve("HEAD"))]
        return MODE_STACK, base, [collector.commit_target(sha) for sha in collector.stack_shas(base)]

    def execute(
        self, args: PreUploadArgs | argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[PreUploadReport]:
        validated = self.validate(args, ctx)
        repo_root = ctx.git_service.top_level(cwd=validated.repo)
        if repo_root is None:
            raise UsageError(
                f"'{validated.repo}' is not inside a Git work tree.",
                remediation="Run from your checkout or pass --repo <git-dir>.",
            )
        checks = self._selected_checks(validated.checks)
        mode, base, targets = self._targets(validated, TargetCollector(ctx.git_service, repo_root))
        report = PreUploadReport(
            repo=str(repo_root),
            mode=mode,
            base=base,
            checks=tuple(check.name for check in checks),
            strict=validated.strict,
            targets=tuple(run_checks(target, checks) for target in targets),
        )
        rendered = None if ctx.config.json_mode else render_pre_upload_report(report)
        if report.passed:
            return CommandResult.success(
                report, action="gerrit stack pre-upload", summary=report.summary, markdown_table=rendered
            )
        return CommandResult.failure(
            report.summary, data=report, action="gerrit stack pre-upload", markdown_table=rendered
        )
