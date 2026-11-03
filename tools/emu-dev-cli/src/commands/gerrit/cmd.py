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

"""CLI handlers for `emu-dev-cli gerrit` subcommands."""

from __future__ import annotations

import logging
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

from commands.gerrit.ping_for_review import (
    GerritPingForReviewCommand,
    PingArgs,
    PingResult,
)
from commands.gerrit.stack import GerritStackCommand
from core.command import (
    Command,
    CommandGroup,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
    bind_command,
)
from core.context import ExecutionContext
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GerritClient,
    GerritReplyInput,
    fetch_and_checkout_cl,
    parse_cl_identifier,
    render_comments_markdown,
    resolve_project_repo_path,
    upload_cl,
)
from lib.output import format_markdown_table
from workflows.core import get_state_base_dir, resume_workflow_session
try:
    import yaml
except ImportError:
    yaml = None


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))


def _print_error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _fail(msg: str, json_mode: bool = False) -> CommandResult:
    if json_mode:
        _print_json({"status": "error", "error": msg})
    else:
        _print_error(msg)
    return CommandResult.failure(
        msg,
        action="gerrit",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def _resolve_cl_or_exit(
    cl_arg: str | None, repo_dir: str | Path = ".", json_mode: bool = False
) -> str:
    """Resolves a CL identifier from CLI argument or local HEAD commit."""
    return parse_cl_identifier(cl_arg, cwd=repo_dir)


def find_sessions_for_cl(
    cl_identifier: str,
    workflow_name: str = "address-review",
) -> list[dict[str, Any]]:
    """Finds cached workflow sessions matching a CL number or Change-Id."""
    normalized = parse_cl_identifier(cl_identifier)
    wf_dir = Path(get_state_base_dir()) / workflow_name
    if yaml is None or not wf_dir.is_dir():
        return []

    matches: list[dict[str, Any]] = []
    state_files = sorted(
        list(wf_dir.glob("STATE-*.yaml")) + list(wf_dir.glob("*/STATE-*.yaml")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for state_file in state_files:
        try:
            data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logging.getLogger(__name__).debug("Ignored error in find_sessions_for_cl: %s", exc)
            continue
        state_id = data.get("state_id") or state_file.stem.replace("STATE-", "")
        meta = data.get("metadata") or {}
        args = meta.get("args") or meta
        matched = False
        if str(args.get("cl", "")) == normalized:
            matched = True
        if not matched:
            q_path_str = (
                args.get("queue-file")
                or args.get("queue_file")
                or str(state_file.parent / f"queue-{state_id}.json")
            )
            q_path = Path(q_path_str)
            if q_path.is_file():
                try:
                    q_data = json.loads(q_path.read_text(encoding="utf-8"))
                    for entry in q_data.get("cls") or []:
                        if (
                            str(entry.get("cl_number", "")) == normalized
                            or str(entry.get("change_id", "")) == normalized
                        ):
                            matched = True
                            break
                except Exception as exc:
                    logging.getLogger(__name__).debug("Ignored error in find_sessions_for_cl: %s", exc)
        if matched:
            matches.append(
                {
                    "state_id": state_id,
                    "workflow_name": data.get("workflow_name", workflow_name),
                    "status": data.get("state", data.get("status", "UNKNOWN")),
                    "current_step_index": data.get(
                        "step", data.get("current_step_index", 0)
                    ),
                    "conversation_id": meta.get("conversation_id", ""),
                    "state_file": str(state_file),
                }
            )
    return matches


def handle_gerrit_list(args: argparse.Namespace) -> CommandResult:
    """Lists open Gerrit CLs owned by the user."""
    json_mode = getattr(args, "json", False)
    client = GerritClient(host=args.host)
    try:
        changes = client.list_open_changes(
            owner=args.owner,
            project=args.project,
            unresolved_only=args.unresolved_only,
            limit=args.limit,
        )
    except Exception as exc:
        return _fail(str(exc), json_mode=json_mode)

    if json_mode:
        _print_json(
            {
                "status": "success",
                "count": len(changes),
                "changes": changes,
            }
        )
        return CommandResult.success(
            action="gerrit list",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    if not changes:
        print("No matching open Gerrit CLs found.")
        return CommandResult.success(
            action="gerrit list",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    print(f"# Open Gerrit Changes ({len(changes)})\n")
    rows: list[list[str]] = []
    for chg in changes:
        cr = chg["code_review"]
        cr_str = f"+{cr}" if cr > 0 else str(cr)
        pv = chg["presubmit_verified"]
        pv_str = f"+{pv}" if pv > 0 else str(pv)
        rows.append(
            [
                str(chg["number"]),
                f"PS{chg['patch_set']}",
                str(chg["unresolved_comment_count"]),
                cr_str,
                pv_str,
                chg["project"],
                chg["subject"][:60],
            ]
        )
    print(
        format_markdown_table(
            headers=[
                "CL",
                "PS",
                "Unresolved",
                "CR",
                "Verified",
                "Project",
                "Subject",
            ],
            rows=rows,
        )
    )
    return CommandResult.success(
        action="gerrit list",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_comments(args: argparse.Namespace) -> CommandResult:
    """Fetches and formats review comment threads for a CL."""
    json_mode = getattr(args, "json", False)
    try:
        ident = _resolve_cl_or_exit(args.cl, args.repo, json_mode=json_mode)
    except ValueError as exc:
        return _fail(str(exc), json_mode=json_mode)
    client = GerritClient(host=args.host)
    try:
        change = client.get_change_detail(ident)
        threads = client.get_comment_threads(
            str(change.get("_number", ident)),
            include_resolved=args.all_threads,
        )
    except Exception as exc:
        return _fail(str(exc), json_mode=json_mode)

    repo_root = resolve_project_repo_path(
        change.get("project", ""), workspace_root=args.repo
    )
    if json_mode:
        _print_json(
            {
                "status": "success",
                "cl_number": int(change.get("_number", 0)),
                "change_id": change.get("change_id", ""),
                "project": change.get("project", ""),
                "subject": change.get("subject", ""),
                "unresolved_only": not args.all_threads,
                "threads": [t.to_dict() for t in threads],
            }
        )
        return CommandResult.success(
            action="gerrit comments",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    markdown = render_comments_markdown(
        change,
        threads,
        repo_path=repo_root,
        context_lines=args.context_lines,
    )
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"Wrote review dossier to {out_path}")
    else:
        print(markdown)
    return CommandResult.success(
        action="gerrit comments",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_checkout(args: argparse.Namespace) -> CommandResult:
    """Fetches and checks out the latest patchset of a CL."""
    json_mode = getattr(args, "json", False)
    try:
        ident = _resolve_cl_or_exit(args.cl, args.repo, json_mode=json_mode)
    except ValueError as exc:
        return _fail(str(exc), json_mode=json_mode)
    client = GerritClient(host=args.host)
    try:
        change = client.get_change_detail(ident)
        repo_path = resolve_project_repo_path(
            change.get("project", ""), workspace_root=args.repo
        )
        result = fetch_and_checkout_cl(
            change,
            repo_path=repo_path,
            mode=args.mode,
        )
    except Exception as exc:
        return _fail(str(exc), json_mode=json_mode)

    if json_mode:
        _print_json({"status": "success", **result})
    else:
        print(
            f"Checked out CL {change.get('_number', ident)} "
            f"({result['action']}) in {result['repo_path']}"
        )
    return CommandResult.success(
        action="gerrit checkout",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_reply(args: argparse.Namespace) -> CommandResult:
    """Stages or publishes Gerrit comment replies and resolution states."""
    json_mode = getattr(args, "json", False)
    if args.from_queue:
        q_path = Path(args.from_queue)
        if not q_path.is_file():
            return _fail(f"Queue file not found: {q_path}", json_mode=json_mode)
        q_data = json.loads(q_path.read_text(encoding="utf-8"))
        client = GerritClient(host=args.host)
        published_total = 0
        for cl_entry in q_data.get("cls") or []:
            cl_num = str(cl_entry.get("cl_number") or cl_entry.get("change_id"))
            replies: list[GerritReplyInput] = []
            for thread in cl_entry.get("unresolved_threads") or []:
                if thread.get("published"):
                    continue
                msg = (thread.get("staged_reply") or "").strip() or "Done."
                comment_id = str(
                    thread.get("leaf_id") or thread.get("root_id") or ""
                )
                if not comment_id:
                    continue
                replies.append(
                    GerritReplyInput(
                        comment_id=comment_id,
                        file_path=str(thread.get("file_path", "")),
                        line=int(thread.get("line") or 0),
                        patch_set=int(thread.get("patch_set") or 1),
                        message=msg,
                        resolve=bool(thread.get("resolve", True)),
                    )
                )
            if replies:
                client.post_review_replies(
                    cl_num,
                    replies,
                    top_message=args.message or None,
                )
                for thread in cl_entry.get("unresolved_threads") or []:
                    thread["published"] = True
                published_total += len(replies)
        q_path.write_text(json.dumps(q_data, indent=2), encoding="utf-8")
        if json_mode:
            _print_json(
                {
                    "status": "success",
                    "published_replies": published_total,
                    "queue_file": str(q_path),
                }
            )
        else:
            print(f"Published {published_total} reply(s) from {q_path}")
        return CommandResult.success(
            action="gerrit reply",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    try:
        ident = _resolve_cl_or_exit(args.cl, args.repo, json_mode=json_mode)
    except ValueError as exc:
        return _fail(str(exc), json_mode=json_mode)
    if args.stage_only:
        if not args.queue_file or not args.comment_id or not args.message:
            return _fail(
                "--stage-only requires --queue-file, --comment-id, and --message.",
                json_mode=json_mode,
            )
        q_path = Path(args.queue_file)
        if not q_path.is_file():
            return _fail(f"Queue file not found: {q_path}", json_mode=json_mode)
        q_data = json.loads(q_path.read_text(encoding="utf-8"))
        matched = False
        for cl_entry in q_data.get("cls") or []:
            if str(cl_entry.get("cl_number")) != ident and str(
                cl_entry.get("change_id")
            ) != ident:
                continue
            for thread in cl_entry.get("unresolved_threads") or []:
                if args.comment_id in (
                    thread.get("root_id"),
                    thread.get("leaf_id"),
                ):
                    thread["staged_reply"] = args.message
                    thread["resolve"] = not args.unresolve
                    matched = True
        if not matched:
            return _fail(
                f"Comment ID {args.comment_id} not found for CL {ident} in {q_path}",
                json_mode=json_mode,
            )
        q_path.write_text(json.dumps(q_data, indent=2), encoding="utf-8")
        if json_mode:
            _print_json(
                {
                    "status": "success",
                    "staged": True,
                    "cl": ident,
                    "comment_id": args.comment_id,
                }
            )
        else:
            print(f"Staged reply for comment {args.comment_id} on CL {ident}")
        return CommandResult.success(
            action="gerrit reply",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    client = GerritClient(host=args.host)
    replies_to_post: list[GerritReplyInput] = []
    if args.comment_id:
        threads = client.get_comment_threads(ident, include_resolved=True)
        target_thread = None
        for thread in threads:
            if args.comment_id in (thread.root_id, thread.leaf_id):
                target_thread = thread
                break
        if target_thread is None:
            return _fail(
                f"Comment ID {args.comment_id} not found on CL {ident}",
                json_mode=json_mode,
            )
        replies_to_post.append(
            GerritReplyInput(
                comment_id=target_thread.leaf_id,
                file_path=target_thread.file_path,
                line=target_thread.line,
                patch_set=target_thread.patch_set,
                message=args.message or "Done.",
                resolve=not bool(args.unresolve),
            )
        )
    client.post_review_replies(
        ident,
        replies_to_post,
        top_message=None if args.comment_id else (args.message or None),
    )
    if json_mode:
        _print_json(
            {
                "status": "success",
                "cl": ident,
                "posted_replies": len(replies_to_post),
            }
        )
    else:
        print(f"Posted review reply to CL {ident}")
    return CommandResult.success(
        action="gerrit reply",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_upload(args: argparse.Namespace) -> CommandResult:
    """Uploads current branch to Gerrit via repo upload."""
    json_mode = getattr(args, "json", False)
    try:
        result = upload_cl(
            Path(args.repo).resolve(),
            presubmit=not args.no_presubmit,
        )
    except Exception as exc:
        return _fail(str(exc), json_mode=json_mode)

    if json_mode:
        _print_json({"status": "success", **result})
    else:
        print(f"Uploaded patchset from {result['repo_path']}")
    return CommandResult.success(
        action="gerrit upload",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_wait(args: argparse.Namespace) -> CommandResult:
    """Polls Gerrit for new review feedback or approval."""
    json_mode = getattr(args, "json", False)
    try:
        ident = _resolve_cl_or_exit(args.cl, args.repo, json_mode=json_mode)
    except ValueError as exc:
        return _fail(str(exc), json_mode=json_mode)
    client = GerritClient(host=args.host)
    try:
        baseline_ps = args.baseline_patchset
        if baseline_ps is None:
            detail = client.get_change_detail(ident)
            cur_rev = detail.get("current_revision", "")
            rev_info = (detail.get("revisions") or {}).get(cur_rev, {})
            baseline_ps = int(rev_info.get("_number", 1) or 1)

        outcome = client.wait_for_review_update(
            ident,
            baseline_patchset=baseline_ps,
            timeout=timedelta(seconds=args.timeout),
            poll_interval=timedelta(seconds=args.poll_interval),
            wait_for_ai=not args.require_cr2,
            wait_for_approval=args.require_cr2,
            require_presubmit=args.require_presubmit,
        )
    except Exception as exc:
        return _fail(str(exc), json_mode=json_mode)

    unresolved = outcome.get("unresolved_threads") or []
    detail = outcome.get("detail") or {}
    cl_num = int(detail.get("_number", 0) or (ident if ident.isdigit() else 0))
    if json_mode:
        _print_json(
            {
                "status": "success",
                "event": outcome["status"],
                "cl_number": cl_num,
                "patch_set": baseline_ps,
                "unresolved_count": len(unresolved),
                "unresolved_threads": [t.to_dict() for t in unresolved],
            }
        )
        return CommandResult.success(
            action="gerrit wait",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )

    print(
        f"CL {cl_num} (PS {baseline_ps}) wait result: "
        f"{outcome['status']} (unresolved={len(unresolved)})"
    )
    return CommandResult.success(
        action="gerrit wait",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def handle_gerrit_resume(args: argparse.Namespace) -> CommandResult:
    """Resumes an `address-review` workflow session and wakes its Jetski conversation."""
    json_mode = getattr(args, "json", False)
    state_id = args.state
    workflow_name = "address-review"
    ident = ""
    if not state_id:
        try:
            ident = _resolve_cl_or_exit(args.cl, args.repo, json_mode=json_mode)
        except ValueError as exc:
            return _fail(str(exc), json_mode=json_mode)
        sessions = find_sessions_for_cl(ident, workflow_name=workflow_name)
        if not sessions:
            msg = (
                f"No existing address-review workflow session found for CL {ident}. "
                f"Start one with: emu-dev-cli workflow address-review init --cl {ident}"
            )
            return _fail(msg, json_mode=json_mode)
        state_id = sessions[0]["state_id"]
        workflow_name = sessions[0].get("workflow_name", workflow_name)

    prompt = args.prompt or (
        f"New Gerrit review feedback has arrived on CL {ident or state_id}. "
        f"Please resume the address-review workflow (state={state_id}) at Step 0."
    )
    ok, msg, info = resume_workflow_session(
        state_id=state_id,
        workflow_name=workflow_name,
        target_step=0,
        custom_prompt=prompt,
        wake_conversation=not args.no_wake,
    )
    if not ok:
        return _fail(msg, json_mode=json_mode)

    if json_mode:
        _print_json({"status": "success", "message": msg, **info})
    else:
        print(
            f"Resumed session {info.get('state_id', state_id)} "
            f"(conversation={info.get('conversation_id') or 'none'}, "
            f"wake_status={info.get('wake_status', 'none')})"
        )
    return CommandResult.success(
        action="gerrit resume",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


class GerritListCommand(Command[argparse.Namespace, Any]):
    """Lists open Gerrit CLs owned by the user."""

    name = "list"
    help_text = "List open Gerrit CLs"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--owner", default="self", help="CL owner (default: self)"
        )
        parser.add_argument(
            "--project", default=None, help="Optional Gerrit project filter"
        )
        parser.add_argument(
            "--unresolved-only",
            action="store_true",
            help="Only show CLs that have unresolved review comments",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum CLs to fetch (default: 50)",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_list(args)


class GerritCommentsCommand(Command[argparse.Namespace, Any]):
    """Fetches and formats review comment threads for a CL."""

    name = "comments"
    help_text = "Fetch and format comment threads for a CL"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, Change-Id, Gerrit URL, or HEAD (default: HEAD)",
        )
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git workspace root (default: current directory)",
        )
        parser.add_argument(
            "--all-threads",
            action="store_true",
            help="Include resolved threads in addition to unresolved threads",
        )
        parser.add_argument(
            "--context-lines",
            type=int,
            default=5,
            help="Number of local source lines to include around each comment",
        )
        parser.add_argument(
            "--output",
            default=None,
            help="Optional file path to write the Markdown dossier to",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_comments(args)


class GerritCheckoutCommand(Command[argparse.Namespace, Any]):
    """Fetches and checks out the latest patchset of a CL."""

    name = "checkout"
    help_text = "Fetch and checkout the latest patchset of a CL"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("cl", help="CL number, Change-Id, or Gerrit URL")
        parser.add_argument(
            "--repo",
            default=".",
            help="Local Git workspace root (default: current directory)",
        )
        parser.add_argument(
            "--mode",
            choices=["auto", "cherry-pick", "checkout"],
            default="auto",
            help="Checkout mode (default: auto)",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_checkout(args)


class GerritReplyCommand(Command[argparse.Namespace, Any]):
    """Stages or publishes Gerrit comment replies and marks threads resolved."""

    name = "reply"
    help_text = (
        "Stage or publish Gerrit comment replies and mark threads resolved"
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, Change-Id, Gerrit URL, or HEAD",
        )
        parser.add_argument(
            "--repo", default=".", help="Local Git repository path"
        )
        parser.add_argument(
            "--comment-id",
            default=None,
            help="Target Gerrit comment ID (root or leaf ID)",
        )
        parser.add_argument(
            "--message",
            default=None,
            help="Reply message text (or top-level review summary)",
        )
        parser.add_argument(
            "--resolve",
            action="store_true",
            default=True,
            help="Mark the comment thread resolved (default: True)",
        )
        parser.add_argument(
            "--unresolve",
            action="store_true",
            help="Leave the comment thread unresolved instead of marking it resolved",
        )
        parser.add_argument(
            "--stage-only",
            action="store_true",
            help="Stage the reply in --queue-file without posting to Gerrit yet",
        )
        parser.add_argument(
            "--queue-file",
            default=None,
            help="Path to the workflow queue JSON file when using --stage-only",
        )
        parser.add_argument(
            "--from-queue",
            default=None,
            help="Publish all staged replies from the specified workflow queue JSON file",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_reply(args)


class GerritUploadCommand(Command[argparse.Namespace, Any]):
    """Uploads current branch to Gerrit via repo upload."""

    name = "upload"
    help_text = "Upload current branch to Gerrit via repo upload"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--repo", default=".", help="Local Git repository path (default: .)"
        )
        parser.add_argument(
            "--no-presubmit",
            action="store_true",
            help="Omit --label Presubmit-Ready+1 when uploading",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_upload(args)


class GerritWaitCommand(Command[argparse.Namespace, Any]):
    """Polls Gerrit for new review comments or approval."""

    name = "wait"
    help_text = "Poll Gerrit for new review comments or approval"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, Change-Id, Gerrit URL, or HEAD",
        )
        parser.add_argument(
            "--repo", default=".", help="Local Git repository path"
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=300,
            help="Maximum seconds to poll Gerrit (default: 300)",
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=15,
            help="Seconds between poll requests (default: 15)",
        )
        parser.add_argument(
            "--baseline-patchset",
            type=int,
            default=None,
            help="Patchset number to watch for AI reviewer completion",
        )
        parser.add_argument(
            "--require-cr2",
            action="store_true",
            help="Wait until Code-Review+2 is granted",
        )
        parser.add_argument(
            "--require-presubmit",
            action="store_true",
            help="Wait until Presubmit-Verified+1/+2 is granted",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_wait(args)


class GerritResumeCommand(Command[argparse.Namespace, Any]):
    """Resumes an address-review workflow session and wakes its Jetski conversation."""

    name = "resume"
    help_text = "Resume an address-review workflow session and wake its Jetski conversation"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "cl",
            nargs="?",
            default="HEAD",
            help="CL number, Change-Id, Gerrit URL, or HEAD",
        )
        parser.add_argument(
            "--repo", default=".", help="Local Git repository path"
        )
        parser.add_argument(
            "--state",
            default=None,
            help="Explicit workflow state ID to resume",
        )
        parser.add_argument(
            "--prompt",
            default=None,
            help="Custom wakeup prompt to send to the Jetski conversation",
        )
        parser.add_argument(
            "--no-wake",
            action="store_true",
            help="Reset workflow state without sending a message to Jetski",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_resume(args)


class GerritUiCommand(Command[argparse.Namespace, Any]):
    """Launches the fast multi-commit & agent-aware Gerrit web UI."""

    name = "ui"
    help_text = "Launch the fast multi-commit & agent-aware Gerrit web UI"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--port",
            type=int,
            default=8085,
            help="Local TCP port for the Gerrit UI server (default: 8085)",
        )
        parser.add_argument(
            "--host-addr",
            default="127.0.0.1",
            help="Local address to bind the HTTP server to (default: 127.0.0.1)",
        )
        parser.add_argument(
            "--viewer",
            default="",
            help="Reviewer email perspective (default: auto-detected from git/Gerrit)",
        )
        parser.add_argument(
            "--query",
            default="",
            help="Custom Gerrit search query (default: open CLs across emulator repos)",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Do not automatically open the browser when starting the server",
        )
        parser.add_argument(
            "--export-static",
            default=None,
            help="Export a pre-populated static HTML snapshot to the specified file path",
        )
        parser.add_argument(
            "--ui",
            dest="ui_variant",
            choices=["polygerrit", "classic"],
            default="polygerrit",
            help="UI variant to display: 'polygerrit' (default) or 'classic'",
        )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext | None = None
    ) -> CommandResult:
        return handle_gerrit_ui(args)


class GerritCommand(CommandGroup):
    """Top-level command group for Gerrit CL inspection and automation."""

    name = "gerrit"
    help_text = "Inspect, checkout, reply to, wait on, and resume Gerrit CL reviews"
    subcommand_dest = "gerrit_command"
    subcommands = [
        GerritListCommand(),
        GerritCommentsCommand(),
        GerritCheckoutCommand(),
        GerritReplyCommand(),
        GerritUploadCommand(),
        GerritWaitCommand(),
        GerritResumeCommand(),
        GerritUiCommand(),
        GerritPingForReviewCommand(),
        GerritStackCommand(),
    ]

    def configure_group_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host",
            default=DEFAULT_GERRIT_HOST,
            help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
        )


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Registers the `gerrit` subcommand and its sub-parsers."""
    return GerritCommand().register(subparsers)


def handle_gerrit_ui(args: argparse.Namespace) -> CommandResult:
    """Starts the fast multi-commit Gerrit web UI or exports a static snapshot."""
    from commands.gerrit.ui_server import serve_gerrit_ui

    serve_gerrit_ui(
        host_addr=args.host_addr,
        port=args.port,
        gerrit_host=getattr(args, "host", DEFAULT_GERRIT_HOST),
        viewer=args.viewer,
        query=args.query,
        open_browser=not args.no_browser,
        export_static_path=args.export_static,
        ui_variant=getattr(args, "ui_variant", "polygerrit"),
    )
    return CommandResult.success(
        action="gerrit ui",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )
