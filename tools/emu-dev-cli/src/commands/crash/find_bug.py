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
from pathlib import Path
import re
import subprocess
import sys

from commands.crash.advisor import ensure_crashadvisor_imports, run_crashadvisor_bazel
from commands.crash.utils import (
    acquire_auth_token,
    extract_top_fault_frame,
    parse_crash_id,
)
from lib.output import print_result


def run_find_bug(args: argparse.Namespace) -> None:
    """Finds existing Buganizer issues using exact signatures and stack fingerprint analysis.

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

    # Tier 1: Signature Match
    if stable_sig and stable_sig != "Unknown":
        clean_sig = re.sub(r"[^\w::]", " ", stable_sig).strip()
        try:
            issue_t1 = client.search_issue_by_signature(clean_sig or stable_sig)
            if issue_t1:
                candidates.append(
                    {
                        "confidence": "HIGH (Signature Match)",
                        "issue": issue_t1,
                    }
                )
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Buganizer signature search notice: {e}\n")

    # Tier 2: Top Fault Function Match
    if top_func:
        try:
            queries = [f'componentid:{component_id} status:open "{top_func}"']
            for q in queries:
                if client.cli_binary and not client.token:
                    cmd = [client.cli_binary, "search", q, "--format=json"]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode == 0 and res.stdout.strip():
                        issues = json.loads(res.stdout)
                        if issues:
                            issue_t2 = issues[0]
                            issue_t2_id = issue_t2.get("issueId")
                            if not any(
                                c["issue"].get("issueId") == issue_t2_id
                                for c in candidates
                            ):
                                candidates.append(
                                    {
                                        "confidence": "MEDIUM (Top Stack Frame Function)",
                                        "issue": issue_t2,
                                    }
                                )
                                break
        except Exception:
            pass

    if json_mode:
        print_result(
            {
                "status": "success",
                "action": "crash find-bug",
                "crash_id": crash_id,
                "stable_signature": stable_sig,
                "top_fault_function": top_func,
                "top_fault_file": top_file,
                "candidates_found": len(candidates),
                "candidates": [
                    {
                        "confidence": c["confidence"],
                        "issue_id": c["issue"].get("issueId"),
                        "title": c["issue"].get("issueState", {}).get("title")
                        or c["issue"].get("title"),
                        "status": c["issue"].get("issueState", {}).get("status")
                        or c["issue"].get("status"),
                        "assignee": c["issue"]
                        .get("issueState", {})
                        .get("assignee", {})
                        .get("emailAddress"),
                        "url": f"https://b.corp.google.com/issues/{c['issue'].get('issueId')}",
                    }
                    for c in candidates
                ],
            },
            json_mode=True,
        )
    else:
        print(f"\n🔍 Crash ID: {crash_id}")
        print(f"📌 Primary Signature: {stable_sig}")
        if top_func:
            print(f"🎯 Fault Frame: {top_func} ({top_file or 'unknown file'})")
        print()

        if not candidates:
            print("🟢 No matching existing issues found in Buganizer Component 29601.")
        else:
            print(f"🐛 Found {len(candidates)} Candidate Buganizer Issue(s):\n")
            for idx, cand in enumerate(candidates, 1):
                iss = cand["issue"]
                iss_id = iss.get("issueId")
                state = iss.get("issueState", {})
                title = state.get("title") or iss.get("title", "Untitled")
                status = state.get("status") or iss.get("status", "UNKNOWN")
                assignee = state.get("assignee", {}).get("emailAddress", "Unassigned")
                print(f"  {idx}. [{cand['confidence']}]")
                print(f"     • Issue: b/{iss_id}")
                print(f"     • Title: {title}")
                print(f"     • Status: {status} (Assignee: {assignee})")
                print(f"     • URL: https://b.corp.google.com/issues/{iss_id}\n")


def register_find_bug_parser(crash_subparsers) -> None:
    """Registers the `find-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    find_bug_parser = crash_subparsers.add_parser(
        "find-bug",
        help="Search Buganizer for existing/duplicate bugs matching crash signature and stack fingerprint",
        description="Searches Buganizer issue tracker for existing or duplicate bugs matching the crash signature, normalized stack fingerprint, and faulting function extracted from CrashAdvisor minidumps.",
    )
    find_bug_parser.add_argument(
        "crash_id",
        help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL (Note: expects a Crash ID, NOT a Buganizer bug number)",
    )
    find_bug_parser.add_argument(
        "--token", help="OAuth2 token for Buganizer & Crash API"
    )
    find_bug_parser.add_argument(
        "--component-id",
        type=int,
        default=29601,
        help="Buganizer Component ID (default: 29601)",
    )
    find_bug_parser.set_defaults(func=run_find_bug)
