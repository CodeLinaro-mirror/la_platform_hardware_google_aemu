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

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

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
import yaml


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))


def _print_error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _resolve_cl_or_exit(
    cl_arg: str | None, repo_dir: str | Path = "."
) -> str:
    """Resolves a CL identifier from CLI argument or local HEAD commit."""
    try:
        return parse_cl_identifier(cl_arg, cwd=repo_dir)
    except ValueError as exc:
        _print_error(str(exc))
        sys.exit(1)


def find_sessions_for_cl(
    cl_identifier: str,
    workflow_name: str = "address-review",
) -> list[dict[str, Any]]:
    """Finds cached workflow sessions matching a CL number or Change-Id."""
    normalized = parse_cl_identifier(cl_identifier)
    wf_dir = Path(get_state_base_dir()) / workflow_name
    if not wf_dir.is_dir():
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
        except Exception:
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
                except Exception:
                    pass
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


def handle_gerrit_list(args: argparse.Namespace) -> None:
    """Lists open Gerrit CLs owned by the user."""
    client = GerritClient(host=args.host)
    try:
        changes = client.list_open_changes(
            owner=args.owner,
            project=args.project,
            unresolved_only=args.unresolved_only,
            limit=args.limit,
        )
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": str(exc)})
        else:
            _print_error(str(exc))
        sys.exit(1)

    if getattr(args, "json", False):
        _print_json(
            {
                "status": "success",
                "count": len(changes),
                "changes": changes,
            }
        )
        return

    if not changes:
        print("No matching open Gerrit CLs found.")
        return

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


def handle_gerrit_comments(args: argparse.Namespace) -> None:
    """Fetches and formats review comment threads for a CL."""
    ident = _resolve_cl_or_exit(args.cl, args.repo)
    client = GerritClient(host=args.host)
    try:
        change = client.get_change_detail(ident)
        threads = client.get_comment_threads(
            str(change.get("_number", ident)),
            include_resolved=args.all_threads,
        )
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": str(exc)})
        else:
            _print_error(str(exc))
        sys.exit(1)

    repo_root = resolve_project_repo_path(
        change.get("project", ""), workspace_root=args.repo
    )
    if getattr(args, "json", False):
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
        return

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


def handle_gerrit_checkout(args: argparse.Namespace) -> None:
    """Fetches and checks out the latest patchset of a CL."""
    ident = _resolve_cl_or_exit(args.cl, args.repo)
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
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": str(exc)})
        else:
            _print_error(str(exc))
        sys.exit(1)

    if getattr(args, "json", False):
        _print_json({"status": "success", **result})
    else:
        print(
            f"Checked out CL {change.get('_number', ident)} "
            f"({result['action']}) in {result['repo_path']}"
        )


def handle_gerrit_reply(args: argparse.Namespace) -> None:
    """Stages or publishes Gerrit comment replies and resolution states."""
    if args.from_queue:
        q_path = Path(args.from_queue)
        if not q_path.is_file():
            _print_error(f"Queue file not found: {q_path}")
            sys.exit(1)
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
        if getattr(args, "json", False):
            _print_json(
                {
                    "status": "success",
                    "published_replies": published_total,
                    "queue_file": str(q_path),
                }
            )
        else:
            print(f"Published {published_total} reply(s) from {q_path}")
        return

    ident = _resolve_cl_or_exit(args.cl, args.repo)
    if args.stage_only:
        if not args.queue_file or not args.comment_id or not args.message:
            _print_error(
                "--stage-only requires --queue-file, --comment-id, and --message."
            )
            sys.exit(1)
        q_path = Path(args.queue_file)
        if not q_path.is_file():
            _print_error(f"Queue file not found: {q_path}")
            sys.exit(1)
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
            _print_error(
                f"Comment ID {args.comment_id} not found for CL {ident} in {q_path}"
            )
            sys.exit(1)
        q_path.write_text(json.dumps(q_data, indent=2), encoding="utf-8")
        if getattr(args, "json", False):
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
        return

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
            _print_error(f"Comment ID {args.comment_id} not found on CL {ident}")
            sys.exit(1)
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
    if getattr(args, "json", False):
        _print_json(
            {
                "status": "success",
                "cl": ident,
                "posted_replies": len(replies_to_post),
            }
        )
    else:
        print(f"Posted review reply to CL {ident}")


def handle_gerrit_upload(args: argparse.Namespace) -> None:
    """Uploads current branch to Gerrit via repo upload."""
    try:
        result = upload_cl(
            Path(args.repo).resolve(),
            presubmit=not args.no_presubmit,
        )
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": str(exc)})
        else:
            _print_error(str(exc))
        sys.exit(1)

    if getattr(args, "json", False):
        _print_json({"status": "success", **result})
    else:
        print(f"Uploaded patchset from {result['repo_path']}")


def handle_gerrit_wait(args: argparse.Namespace) -> None:
    """Polls Gerrit for new review feedback or approval."""
    ident = _resolve_cl_or_exit(args.cl, args.repo)
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
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": str(exc)})
        else:
            _print_error(str(exc))
        sys.exit(1)

    unresolved = outcome.get("unresolved_threads") or []
    detail = outcome.get("detail") or {}
    cl_num = int(detail.get("_number", 0) or (ident if ident.isdigit() else 0))
    if getattr(args, "json", False):
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
        return

    print(
        f"CL {cl_num} (PS {baseline_ps}) wait result: "
        f"{outcome['status']} (unresolved={len(unresolved)})"
    )


def handle_gerrit_resume(args: argparse.Namespace) -> None:
    """Resumes an `address-review` workflow session and wakes its Jetski conversation."""
    state_id = args.state
    workflow_name = "address-review"
    ident = ""
    if not state_id:
        ident = _resolve_cl_or_exit(args.cl, args.repo)
        sessions = find_sessions_for_cl(ident, workflow_name=workflow_name)
        if not sessions:
            msg = (
                f"No existing address-review workflow session found for CL {ident}. "
                f"Start one with: emu-dev-cli workflow address-review init --cl {ident}"
            )
            if getattr(args, "json", False):
                _print_json({"status": "error", "error": msg})
            else:
                _print_error(msg)
            sys.exit(1)
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
        if getattr(args, "json", False):
            _print_json({"status": "error", "error": msg})
        else:
            _print_error(msg)
        sys.exit(1)

    if getattr(args, "json", False):
        _print_json({"status": "success", "message": msg, **info})
    else:
        print(
            f"Resumed session {info.get('state_id', state_id)} "
            f"(conversation={info.get('conversation_id') or 'none'}, "
            f"wake_status={info.get('wake_status', 'none')})"
        )


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Registers the `gerrit` subcommand and its sub-parsers."""
    gerrit_parser = subparsers.add_parser(
        "gerrit",
        help="Inspect, checkout, reply to, wait on, and resume Gerrit CL reviews",
    )
    gerrit_parser.add_argument(
        "--host",
        default=DEFAULT_GERRIT_HOST,
        help=f"Gerrit host URL (default: {DEFAULT_GERRIT_HOST})",
    )
    gerrit_sub = gerrit_parser.add_subparsers(
        dest="gerrit_command", help="Gerrit review actions"
    )

    list_p = gerrit_sub.add_parser("list", help="List open Gerrit CLs")
    list_p.add_argument(
        "--owner", default="self", help="CL owner (default: self)"
    )
    list_p.add_argument(
        "--project", default=None, help="Optional Gerrit project filter"
    )
    list_p.add_argument(
        "--unresolved-only",
        action="store_true",
        help="Only show CLs that have unresolved review comments",
    )
    list_p.add_argument(
        "--limit", type=int, default=50, help="Maximum CLs to fetch (default: 50)"
    )
    list_p.set_defaults(func=handle_gerrit_list)

    comments_p = gerrit_sub.add_parser(
        "comments", help="Fetch and format comment threads for a CL"
    )
    comments_p.add_argument(
        "cl",
        nargs="?",
        default="HEAD",
        help="CL number, Change-Id, Gerrit URL, or HEAD (default: HEAD)",
    )
    comments_p.add_argument(
        "--repo",
        default=".",
        help="Local Git workspace root (default: current directory)",
    )
    comments_p.add_argument(
        "--all-threads",
        action="store_true",
        help="Include resolved threads in addition to unresolved threads",
    )
    comments_p.add_argument(
        "--context-lines",
        type=int,
        default=5,
        help="Number of local source lines to include around each comment",
    )
    comments_p.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the Markdown dossier to",
    )
    comments_p.set_defaults(func=handle_gerrit_comments)

    checkout_p = gerrit_sub.add_parser(
        "checkout", help="Fetch and checkout the latest patchset of a CL"
    )
    checkout_p.add_argument(
        "cl", help="CL number, Change-Id, or Gerrit URL"
    )
    checkout_p.add_argument(
        "--repo",
        default=".",
        help="Local Git workspace root (default: current directory)",
    )
    checkout_p.add_argument(
        "--mode",
        choices=["auto", "cherry-pick", "checkout"],
        default="auto",
        help="Checkout mode (default: auto)",
    )
    checkout_p.set_defaults(func=handle_gerrit_checkout)

    reply_p = gerrit_sub.add_parser(
        "reply",
        help="Stage or publish Gerrit comment replies and mark threads resolved",
    )
    reply_p.add_argument(
        "cl",
        nargs="?",
        default="HEAD",
        help="CL number, Change-Id, Gerrit URL, or HEAD",
    )
    reply_p.add_argument(
        "--repo", default=".", help="Local Git repository path"
    )
    reply_p.add_argument(
        "--comment-id",
        default=None,
        help="Target Gerrit comment ID (root or leaf ID)",
    )
    reply_p.add_argument(
        "--message",
        default=None,
        help="Reply message text (or top-level review summary)",
    )
    reply_p.add_argument(
        "--resolve",
        action="store_true",
        default=True,
        help="Mark the comment thread resolved (default: True)",
    )
    reply_p.add_argument(
        "--unresolve",
        action="store_true",
        help="Leave the comment thread unresolved instead of marking it resolved",
    )
    reply_p.add_argument(
        "--stage-only",
        action="store_true",
        help="Stage the reply in --queue-file without posting to Gerrit yet",
    )
    reply_p.add_argument(
        "--queue-file",
        default=None,
        help="Path to the workflow queue JSON file when using --stage-only",
    )
    reply_p.add_argument(
        "--from-queue",
        default=None,
        help="Publish all staged replies from the specified workflow queue JSON file",
    )
    reply_p.set_defaults(func=handle_gerrit_reply)

    upload_p = gerrit_sub.add_parser(
        "upload", help="Upload current branch to Gerrit via repo upload"
    )
    upload_p.add_argument(
        "--repo", default=".", help="Local Git repository path (default: .)"
    )
    upload_p.add_argument(
        "--no-presubmit",
        action="store_true",
        help="Omit --label Presubmit-Ready+1 when uploading",
    )
    upload_p.set_defaults(func=handle_gerrit_upload)

    wait_p = gerrit_sub.add_parser(
        "wait", help="Poll Gerrit for new review comments or approval"
    )
    wait_p.add_argument(
        "cl",
        nargs="?",
        default="HEAD",
        help="CL number, Change-Id, Gerrit URL, or HEAD",
    )
    wait_p.add_argument(
        "--repo", default=".", help="Local Git repository path"
    )
    wait_p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Maximum seconds to poll Gerrit (default: 300)",
    )
    wait_p.add_argument(
        "--poll-interval",
        type=int,
        default=15,
        help="Seconds between poll requests (default: 15)",
    )
    wait_p.add_argument(
        "--baseline-patchset",
        type=int,
        default=None,
        help="Patchset number to watch for AI reviewer completion",
    )
    wait_p.add_argument(
        "--require-cr2",
        action="store_true",
        help="Wait until Code-Review+2 is granted",
    )
    wait_p.add_argument(
        "--require-presubmit",
        action="store_true",
        help="Wait until Presubmit-Verified+1/+2 is granted",
    )
    wait_p.set_defaults(func=handle_gerrit_wait)

    resume_p = gerrit_sub.add_parser(
        "resume",
        help="Resume an address-review workflow session and wake its Jetski conversation",
    )
    resume_p.add_argument(
        "cl",
        nargs="?",
        default="HEAD",
        help="CL number, Change-Id, Gerrit URL, or HEAD",
    )
    resume_p.add_argument(
        "--repo", default=".", help="Local Git repository path"
    )
    resume_p.add_argument(
        "--state",
        default=None,
        help="Explicit workflow state ID to resume",
    )
    resume_p.add_argument(
        "--prompt",
        default=None,
        help="Custom wakeup prompt to send to the Jetski conversation",
    )
    resume_p.add_argument(
        "--no-wake",
        action="store_true",
        help="Reset workflow state without sending a message to Jetski",
    )
    resume_p.set_defaults(func=handle_gerrit_resume)

    return gerrit_parser
