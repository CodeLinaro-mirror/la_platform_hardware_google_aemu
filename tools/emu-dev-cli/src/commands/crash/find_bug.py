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

import logging
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.command import Command, CommandResult, bind_command
from core.context import (
    ExecutionContext,
    SERVICE_BUGANIZER,
    SERVICE_F1_SQL,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import UsageError
from lib.output import print_result

from commands.crash.advisor import ensure_crashadvisor_imports, run_crashadvisor_bazel
from commands.crash.fix_checker import (
    evaluate_crash_fix_status,
    extract_crash_version_info,
)
from commands.crash.link_bug import search_crash_annotations
from commands.crash.utils import (
    acquire_auth_token,
    extract_top_fault_frame,
    parse_crash_id,
)
from commands.source_directory import get_source_directory

CLOSED_STATUSES = {"FIXED", "VERIFIED", "OBSOLETE", "CLOSED", "DONE"}


def run_find_bug(args: argparse.Namespace) -> None:
    """Finds existing Buganizer issues using exact signatures and stack fingerprint analysis.

    Checks whether the crash originated from an older repository build version and whether
    the bug has already been resolved or fixed in Buganizer or the local source repository.

    Args:
        args: Parsed command line arguments containing crash_id, token, component_id, json, verbose flags.
    """
    crash_modules = ensure_crashadvisor_imports()
    buganizer_mod = crash_modules["buganizer"]
    context_mod = crash_modules["context"]
    client_mod = crash_modules["client"]
    api_mod = crash_modules["api"]
    metadata_mod = crash_modules["metadata"]

    crash_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    component_id = getattr(args, "component_id", buganizer_mod.EMULATOR_COMPONENT_ID)
    json_mode = getattr(args, "json", False)

    # Ingest context & metadata
    ctx = context_mod.CrashReportContext(crash_id)
    ctx.prepare_sandbox()
    gosso = client_mod.GossoClient()
    api = api_mod.CrashApi(gosso, verbose=getattr(args, "verbose", False))

    if not json_mode:
        print(f"📥 Fetching crash metadata for ID: {crash_id}...")

    api.download_metadata(ctx.crash_id, ctx.metadata_path)
    meta = metadata_mod.CrashMetadata(ctx.metadata_path)
    stable_sig = meta.primary_signature or "Unknown"

    meta_json = getattr(meta, "data", {})
    if not meta_json and ctx.metadata_path.exists():
        try:
            with open(ctx.metadata_path, "r", encoding="utf-8") as f:
                meta_json = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).debug("Ignored error in run_find_bug: %s", exc)

    vinfo = extract_crash_version_info(meta_json)
    build_id = meta.build_id or vinfo.get("build_id", "unknown")
    version_str = vinfo.get("version_str", build_id)

    # Ensure local symbolication & stack dump exist
    txt_dump = Path(ctx.work_dir) / "crashreport.txt"
    if not txt_dump.exists():
        if not json_mode:
            print(
                f"⚙️ Running minidump symbolication & stack extraction for ID: {crash_id}..."
            )
        cmd_args = [crash_id, "--work-dir", str(ctx.work_dir)]
        if token:
            cmd_args.extend(["--token", token])
        try:
            run_crashadvisor_bazel(cmd_args)
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Symbolication execution notice: {e}\n")

    # Attempt local stack fingerprinting from crashreport.txt dump
    top_func, top_file = None, None
    if txt_dump.exists():
        try:
            top_func, top_file = extract_top_fault_frame(
                txt_dump.read_text(encoding="utf-8")
            )
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Stack fingerprint reading warning: {e}\n")

    client = buganizer_mod.BuganizerClient(
        token=token,
        component_id=component_id,
        verbose=getattr(args, "verbose", False),
    )

    candidates = []

    # Tier 0: Direct go/crash Annotation Link (blade:crash-queryapi SearchAnnotations)
    if (
        stable_sig
        and stable_sig != "Unknown"
        and re.search(r"-[0-9a-fA-F]{7,8}$", stable_sig)
    ):
        try:
            linked_bugs = search_crash_annotations([stable_sig]).get(stable_sig, [])
            for linked in linked_bugs:
                b_id = linked.get("bug_id", "").strip()
                if b_id:
                    status_val = "ASSIGNED"
                    try:
                        fetched_status = client.get_issue_status(int(b_id))
                        if isinstance(fetched_status, str) and fetched_status:
                            status_val = fetched_status
                    except Exception as exc:
                        logging.getLogger(__name__).debug("Ignored error in run_find_bug: %s", exc)
                    candidates.append(
                        {
                            "confidence": "EXACT (Linked in go/crash)",
                            "issue": {
                                "issueId": b_id,
                                "issueState": {
                                    "title": f"[Linked in go/crash] {stable_sig}",
                                    "status": status_val,
                                },
                            },
                        }
                    )
        except Exception as exc:
            logging.getLogger(__name__).debug("Ignored error in run_find_bug: %s", exc)

    # Tier 1: Signature Match
    if stable_sig and stable_sig != "Unknown":
        clean_sig = re.sub(r"[^\w::]", " ", stable_sig).strip()
        try:
            issue_t1 = client.search_issue_by_signature(clean_sig or stable_sig)
            if issue_t1:
                issue_t1_id = str(issue_t1.get("issueId") or issue_t1.get("id") or "")
                if not any(
                    str(c["issue"].get("issueId") or c["issue"].get("id"))
                    == issue_t1_id
                    for c in candidates
                ):
                    candidates.append(
                        {
                            "confidence": "HIGH (Signature Match)",
                            "issue": issue_t1,
                        }
                    )
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Buganizer signature search notice: {e}\n")

    # Tier 2: Top Fault Function Match across both open and closed issues
    if top_func:
        try:
            queries = [
                f'componentid:{component_id} "{top_func}"',
                f'componentid:{component_id} status:open "{top_func}"',
            ]
            for q in queries:
                issues = []
                if client.cli_binary and not client.token:
                    if Path(str(client.cli_binary)).name == "issues" and hasattr(
                        client, "_cli_search"
                    ):
                        issues = client._cli_search(q)
                    else:
                        cmd = [client.cli_binary, "search", q, "--format=json"]
                        res = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=10
                        )
                        if res.returncode == 0 and res.stdout.strip():
                            issues = json.loads(res.stdout)
                else:
                    found = client.search_issue_by_signature(top_func)
                    if found:
                        issues = [found]

                added_t2 = False
                for issue_t2 in issues if isinstance(issues, list) else [issues]:
                    if not isinstance(issue_t2, dict):
                        continue
                    issue_t2_id = issue_t2.get("issueId") or issue_t2.get("id")
                    if issue_t2_id and not any(
                        str(c["issue"].get("issueId") or c["issue"].get("id"))
                        == str(issue_t2_id)
                        for c in candidates
                    ):
                        candidates.append(
                            {
                                "confidence": "MEDIUM (Top Stack Frame Function)",
                                "issue": issue_t2,
                            }
                        )
                        added_t2 = True
                        break
                if added_t2:
                    break
        except Exception as exc:
            logging.getLogger(__name__).debug("Ignored error in run_find_bug: %s", exc)

    # Normalize candidate issues for fix status evaluation
    normalized_candidates = []
    for c in candidates:
        iss = c["issue"]
        iss_id = str(iss.get("issueId") or iss.get("id", ""))
        state = iss.get("issueState") or {}
        status = (state.get("status") or iss.get("status", "UNKNOWN")).upper()
        title = state.get("title") or iss.get("title", "Untitled")
        assignee = (state.get("assignee") or {}).get("emailAddress", "Unassigned")
        normalized_candidates.append(
            {
                "issue_id": iss_id,
                "status": status,
                "title": title,
                "assignee": assignee,
                "is_fixed": status in CLOSED_STATUSES,
                "confidence": c["confidence"],
                "url": f"https://b.corp.google.com/issues/{iss_id}",
            }
        )

    local_src_dir = get_source_directory("emu-main-next")
    fix_res = evaluate_crash_fix_status(
        crash_id=crash_id,
        metadata_path=ctx.metadata_path,
        candidate_issues=normalized_candidates,
        source_dir=local_src_dir,
        meta_json=meta_json,
    )

    if json_mode:
        print_result(
            {
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
                "candidates_found": len(normalized_candidates),
                "candidates": normalized_candidates,
            },
            json_mode=True,
        )
    else:
        print(f"\n🔍 Crash ID: {crash_id}")
        print(f"📦 Crash Build ID: {build_id} (Version: {version_str})")
        print(
            f"⚠️ Repository Version Context: Crash was captured from Build {build_id}. The bug may already be fixed in current HEAD."
        )
        print(f"📌 Primary Signature: {stable_sig}")
        if top_func:
            print(f"🎯 Fault Frame: {top_func} ({top_file or 'unknown file'})")
        print(
            f"🛠️ Fix Status Assessment: {'ALREADY FIXED ✅' if fix_res.is_already_fixed else 'OPEN / NEEDS VERIFICATION ⚠️'} ({fix_res.fix_reason})"
        )
        print()

        if not candidates:
            print("🟢 No matching existing issues found in Buganizer Component 29601.")
        else:
            print(
                f"🐛 Found {len(normalized_candidates)} Candidate Buganizer Issue(s):\n"
            )
            for idx, cand in enumerate(normalized_candidates, 1):
                fixed_badge = (
                    " [ALREADY FIXED / RESOLVED ✅]" if cand["is_fixed"] else ""
                )
                print(f"  {idx}. [{cand['confidence']}]")
                print(f"     • Issue: b/{cand['issue_id']}")
                print(f"     • Title: {cand['title']}")
                print(
                    f"     • Status: {cand['status']}{fixed_badge} (Assignee: {cand['assignee']})"
                )
                print(f"     • URL: {cand['url']}\n")


def _resolve_find_bug_work_dir(
    args: argparse.Namespace, ctx: ExecutionContext, crash_id: str
) -> Path:
    """Resolves the crash work directory from `--work-dir`, `ctx.config.cache_dir`, or default sandbox."""
    explicit_dir = getattr(args, "work_dir", None)
    if explicit_dir:
        return Path(str(explicit_dir)).expanduser()
    candidate_cache = ctx.config.cache_dir / "crashadvisor" / crash_id
    if candidate_cache.exists():
        return candidate_cache
    from commands.crash.utils import get_crashadvisor_sandbox_dir

    return Path(get_crashadvisor_sandbox_dir(crash_id, create=True))


class CrashFindBugCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash find-bug`."""

    name = "find-bug"
    help_text = "Search Buganizer for existing/duplicate bugs matching crash signature and stack fingerprint"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = (
            "Searches Buganizer issue tracker for existing or duplicate bugs matching the crash "
            "signature, normalized stack fingerprint, and faulting function extracted from "
            "CrashAdvisor minidumps."
        )
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

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        crash_id = parse_crash_id(raw_id) if raw_id else ""
        if not crash_id:
            raise UsageError(
                "Crash ID cannot be empty for 'crash find-bug'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )
        component_id = int(getattr(args, "component_id", 29601))
        if component_id <= 0:
            raise UsageError(
                f"Buganizer Component ID must be positive (got {component_id}).",
                remediation="Pass a positive integer to --component-id (default: 29601).",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        crash_id = parse_crash_id(str(args.crash_id))
        component_id = int(getattr(args, "component_id", 29601))
        buganizer = ctx.services.get(SERVICE_BUGANIZER)
        f1_client = ctx.services.get(SERVICE_F1_SQL)
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)

        work_dir = _resolve_find_bug_work_dir(args, ctx, crash_id)
        meta_path = work_dir / "metadata.json"
        txt_dump = work_dir / "crashreport.txt"

        meta_json: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                parsed_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(parsed_meta, dict):
                    meta_json = parsed_meta
            except (OSError, ValueError):
                meta_json = {}

        vinfo = extract_crash_version_info(meta_json)
        build_id = str(vinfo.get("build_id") or "unknown")
        version_str = str(vinfo.get("version_str") or build_id)

        proto = meta_json.get("report_proto", meta_json)
        if not isinstance(proto, dict):
            proto = {}
        stable_sig = (
            str(getattr(args, "signature", None) or "").strip()
            or str(
                proto.get("stableSignature")
                or proto.get("stable_signature")
                or meta_json.get("stable_signature")
                or ""
            ).strip()
        )
        if not stable_sig and f1_client is not None:
            from commands.crash.link_bug import resolve_signature_from_report_id

            resolved_sig = resolve_signature_from_report_id(
                crash_id, f1_client=f1_client, runner=runner
            )
            if resolved_sig:
                stable_sig = resolved_sig
        if not stable_sig:
            stable_sig = "Unknown"

        top_func: str | None = None
        top_file: str | None = None
        if txt_dump.is_file():
            try:
                top_func, top_file = extract_top_fault_frame(
                    txt_dump.read_text(encoding="utf-8")
                )
            except OSError:
                top_func, top_file = None, None

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
                issue_obj = buganizer.get_issue(b_id, use_cache=True)
                status_val = (
                    issue_obj.status.upper()
                    if issue_obj is not None and issue_obj.status
                    else "ASSIGNED"
                )
                title_val = (
                    issue_obj.title
                    if issue_obj is not None and issue_obj.title
                    else f"[Linked in go/crash] {stable_sig}"
                )
                assignee_val = (
                    issue_obj.assignee
                    if issue_obj is not None and issue_obj.assignee
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

            sig_issues = buganizer.search_issues(
                f'componentid:{component_id} "{stable_sig}"', limit=10
            )
            for iss in sig_issues:
                if iss.issue_id in seen_ids:
                    continue
                seen_ids.add(iss.issue_id)
                status_upper = (iss.status or "UNKNOWN").upper()
                candidates.append(
                    {
                        "issue_id": iss.issue_id,
                        "status": status_upper,
                        "title": iss.title or "Untitled",
                        "assignee": iss.assignee or "Unassigned",
                        "is_fixed": status_upper in CLOSED_STATUSES,
                        "confidence": "HIGH (Signature Match)",
                        "url": f"https://b.corp.google.com/issues/{iss.issue_id}",
                    }
                )

        if top_func:
            func_issues = buganizer.search_issues(
                f'componentid:{component_id} "{top_func}"', limit=10
            )
            for iss in func_issues:
                if iss.issue_id in seen_ids:
                    continue
                seen_ids.add(iss.issue_id)
                status_upper = (iss.status or "UNKNOWN").upper()
                candidates.append(
                    {
                        "issue_id": iss.issue_id,
                        "status": status_upper,
                        "title": iss.title or "Untitled",
                        "assignee": iss.assignee or "Unassigned",
                        "is_fixed": status_upper in CLOSED_STATUSES,
                        "confidence": "MEDIUM (Top Stack Frame Function)",
                        "url": f"https://b.corp.google.com/issues/{iss.issue_id}",
                    }
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


def register_find_bug_parser(crash_subparsers) -> None:
    """Registers the `find-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    bind_command(
        crash_subparsers,
        CrashFindBugCommand(),
        legacy_func=run_find_bug,
    )
