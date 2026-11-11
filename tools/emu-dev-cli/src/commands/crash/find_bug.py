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

"""Buganizer deduplication & stack fingerprint searching subcommand module for emu-dev-cli (`crash find-bug`)."""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from commands.crash.advisor import (
    CrashAdvisorRunner,
    create_crash_bug_tracker,
)
from commands.crash.fix_checker import (
    evaluate_crash_fix_status,
)
from commands.crash.link_bug import search_crash_annotations
from commands.crash.utils import (
    CrashMetadata,
    acquire_auth_token,
    extract_top_fault_frame,
    parse_crash_id,
    resolve_crash_work_dir,
)
from commands.source_directory import get_source_directory
from core.command import Command, CommandResult
from core.context import (
    ExecutionContext,
    SERVICE_BUGANIZER,
    SERVICE_F1_SQL,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import CommandValidationError

logger = logging.getLogger(__name__)

CLOSED_STATUSES = {"FIXED", "VERIFIED", "OBSOLETE", "CLOSED", "DONE"}


def _append_search_candidates(
    issues: list[Any],
    *,
    confidence: str,
    seen_ids: set[str],
    candidates: list[dict[str, Any]],
) -> None:
    """Appends deduplicated Buganizer search results to `candidates`."""
    for iss in issues:
        iss_id = (
            getattr(iss, "issue_id", None)
            or getattr(iss, "id", None)
            or (iss.get("issueId") if isinstance(iss, dict) else str(iss))
        )
        if iss_id in seen_ids:
            continue
        seen_ids.add(iss_id)
        status_upper = (
            getattr(iss, "status", None)
            or (iss.get("status") if isinstance(iss, dict) else "UNKNOWN")
        ).upper()
        candidates.append(
            {
                "issue_id": iss_id,
                "status": status_upper,
                "title": getattr(iss, "title", None)
                or (iss.get("title") if isinstance(iss, dict) else "Untitled"),
                "assignee": getattr(iss, "assignee", None)
                or (iss.get("assignee") if isinstance(iss, dict) else "Unassigned"),
                "is_fixed": status_upper in CLOSED_STATUSES,
                "confidence": confidence,
                "url": f"https://b.corp.google.com/issues/{iss_id}",
            }
        )


class CrashFindBugCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash find-bug`."""

    name = "find-bug"
    help_text = (
        "Search Buganizer for existing/duplicate bugs matching crash signature and stack fingerprint"
    )
    help = help_text
    description = (
        "Searches Buganizer issue tracker for existing or duplicate bugs matching the crash "
        "signature, normalized stack fingerprint, and faulting function extracted from "
        "CrashAdvisor minidumps."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
        parser.add_argument(
            "crash_id",
            help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL (Note: expects a Crash ID, NOT a Buganizer bug number)",
        )
        parser.add_argument(
            "--token", help="OAuth2 token for Buganizer & Crash API"
        )
        parser.add_argument(
            "--component-id",
            type=int,
            default=29601,
            help="Buganizer Component ID (default: 29601)",
        )
        parser.add_argument(
            "--work-dir",
            dest="work_dir",
            help="Optional explicit work directory containing metadata.json and crashreport.txt",
        )
        parser.add_argument(
            "--signature",
            help="Optional explicit stable_signature override",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON result",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        crash_id = parse_crash_id(raw_id) if raw_id else ""
        if not crash_id:
            raise CommandValidationError(
                "Crash ID cannot be empty for 'crash find-bug'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )
        component_id = int(getattr(args, "component_id", 29601))
        if component_id <= 0:
            raise CommandValidationError(
                f"Buganizer Component ID must be positive (got {component_id}).",
                remediation="Pass a positive integer to --component-id (default: 29601).",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        meta = CrashMetadata.from_args(
            args,
            ctx.config.cache_dir,
            extract_top_fault_frame_fn=extract_top_fault_frame,
        )
        crash_id = meta.crash_id
        component_id = int(getattr(args, "component_id", 29601))
        buganizer = ctx.services.get(SERVICE_BUGANIZER)
        f1_client = ctx.services.get(SERVICE_F1_SQL)
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)

        work_dir = meta.work_dir
        meta_path = meta.metadata_path
        txt_dump = meta.crashreport_path

        if not txt_dump.is_file() or not meta_path.is_file():
            token = acquire_auth_token(getattr(args, "token", None))
            advisor_runner = CrashAdvisorRunner()
            advisor_runner.run(
                ctx,
                crash_id,
                work_dir,
                flags=["--auto-run"],
                token=token,
                check=False,
            )
            meta = meta.refresh(
                signature_override=getattr(args, "signature", None),
                extract_top_fault_frame_fn=extract_top_fault_frame,
            )

        meta_json = meta.raw_metadata
        build_id = meta.build_id
        version_str = meta.version_str
        stable_sig = meta.stable_signature
        if not stable_sig and f1_client is not None:
            from commands.crash.link_bug import resolve_signature_from_report_id

            resolved_sig = resolve_signature_from_report_id(
                crash_id, f1_client=f1_client, runner=runner
            )
            if resolved_sig:
                stable_sig = resolved_sig
        if not stable_sig:
            stable_sig = "Unknown"

        top_func = meta.top_fault_function
        top_file = meta.top_fault_file

        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        if stable_sig and stable_sig != "Unknown":
            linked_bugs = search_crash_annotations(
                [stable_sig], runner=runner
            ).get(stable_sig, [])
            for linked in linked_bugs:
                b_id = str(linked.get("bug_id") or "").strip()
                if not b_id or b_id in seen_ids:
                    continue
                issue_obj = None
                if buganizer and hasattr(buganizer, "get_issue"):
                    try:
                        issue_obj = buganizer.get_issue(b_id, use_cache=True)
                    except TypeError:
                        issue_obj = buganizer.get_issue(b_id)
                status_val = (
                    issue_obj.status.upper()
                    if issue_obj is not None and getattr(issue_obj, "status", None)
                    else "ASSIGNED"
                )
                title_val = (
                    issue_obj.title
                    if issue_obj is not None and getattr(issue_obj, "title", None)
                    else f"[Linked in go/crash] {stable_sig}"
                )
                assignee_val = (
                    issue_obj.assignee
                    if issue_obj is not None and getattr(issue_obj, "assignee", None)
                    else "Unassigned"
                )
                seen_ids.add(b_id)
                candidates.append(
                    {
                        "issue_id": b_id,
                        "status": status_val,
                        "title": title_val,
                        "assignee": assignee_val,
                        "is_fixed": status_val in CLOSED_STATUSES,
                        "confidence": "EXACT (Linked in go/crash)",
                        "url": f"https://b.corp.google.com/issues/{b_id}",
                    }
                )

            if buganizer and hasattr(buganizer, "search_issues"):
                sig_issues = buganizer.search_issues(
                    f'componentid:{component_id} "{stable_sig}"', limit=10
                )
                _append_search_candidates(
                    sig_issues,
                    confidence="HIGH (Signature Match)",
                    seen_ids=seen_ids,
                    candidates=candidates,
                )

        if top_func and buganizer and hasattr(buganizer, "search_issues"):
            func_issues = buganizer.search_issues(
                f'componentid:{component_id} "{top_func}"', limit=10
            )
            _append_search_candidates(
                func_issues,
                confidence="MEDIUM (Top Stack Frame Function)",
                seen_ids=seen_ids,
                candidates=candidates,
            )

        local_src_dir = ctx.config.get_source_directory(
            "emu-main-next"
        ) or get_source_directory("emu-main-next")
        fix_res = evaluate_crash_fix_status(
            crash_id=crash_id,
            metadata_path=meta_path if meta_path.is_file() else None,
            candidate_issues=candidates,
            source_dir=local_src_dir,
            meta_json=meta_json,
        )

        payload: dict[str, Any] = {
            "status": "success",
            "action": "crash find-bug",
            "crash_id": crash_id,
            "build_id": build_id,
            "version_str": version_str,
            "already_fixed": fix_res.is_already_fixed,
            "fix_status": fix_res.fix_status,
            "fix_reason": fix_res.fix_reason,
            "is_older_build": fix_res.is_older_build,
            "older_version_warning": fix_res.older_version_warning,
            "stable_signature": stable_sig,
            "top_fault_function": top_func,
            "top_fault_file": top_file,
            "candidates_found": len(candidates),
            "candidates": candidates,
        }
        return CommandResult(
            data=payload,
            action="crash find-bug",
            summary=f"Found {len(candidates)} candidate Buganizer issue(s) for crash {crash_id}.",
        )


def register_find_bug_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `find-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    return CrashFindBugCommand().register(crash_subparsers)
