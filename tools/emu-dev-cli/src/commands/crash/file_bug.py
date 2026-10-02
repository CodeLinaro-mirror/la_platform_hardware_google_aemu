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

"""RCA analysis & Buganizer issue filing subcommand module for emu-dev-cli (`crash file-bug`)."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lib.output import print_result

from commands.crash.advisor import (
    ensure_crashadvisor_imports,
    run_crashadvisor_bazel,
)
from commands.crash.link_bug import (
    is_crash_report_id,
    is_safe_signature_issue_match,
    resolve_signature_from_report_id,
    search_crash_annotations,
    update_crash_annotation,
)
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
)


def _extract_signature_from_sandbox(sandbox_dir: str) -> str:
    """Extracts stable_signature from `<sandbox_dir>/metadata.json` if present."""
    metadata_path = Path(sandbox_dir) / "metadata.json"
    if not metadata_path.exists():
        return ""
    try:
        meta_data = json.loads(metadata_path.read_text(encoding="utf-8"))
        report_proto = meta_data.get("report_proto", meta_data)
        return (
            report_proto.get("stableSignature")
            or report_proto.get("stable_signature")
            or ""
        ).strip()
    except (OSError, ValueError, TypeError):
        return ""


def _auto_link_filed_bug_to_crash(
    sandbox_dir: str,
    token: str | None,
    verbose: bool = False,
    explicit_bug_id: str | None = None,
) -> None:
    """Links the Buganizer issue created/updated by CrashAdvisor back to go/crash."""
    stable_sig = _extract_signature_from_sandbox(sandbox_dir)
    if not stable_sig:
        return

    try:
        existing_links = search_crash_annotations([stable_sig]).get(stable_sig, [])
        if existing_links:
            urls = ", ".join(b["bug_url"] for b in existing_links)
            print(f"🔗 Signature '{stable_sig}' is already linked in go/crash: {urls}")
            return

        bug_id = str(explicit_bug_id or "").removeprefix("b/").strip()
        if not bug_id:
            crash_modules = ensure_crashadvisor_imports()
            buganizer_mod = crash_modules["buganizer"]
            client = buganizer_mod.BuganizerClient(token=token, verbose=verbose)
            issue = client.search_issue_by_signature(stable_sig)
            if not is_safe_signature_issue_match(stable_sig, issue):
                return
            bug_id = str(issue.get("issueId") or issue.get("id") or "").strip()

        if not bug_id or not bug_id.isdigit() or int(bug_id) <= 0:
            return

        bug_url = f"https://b.corp.google.com/issues/{bug_id}"
        ok, detail = update_crash_annotation(
            stable_signature=stable_sig,
            bug_url=bug_url,
        )
        if ok:
            print(
                f"🔗 Linked Buganizer issue b/{bug_id} ({bug_url}) to go/crash signature '{stable_sig}'."
            )
        elif verbose:
            sys.stderr.write(
                f"⚠️ Could not auto-link b/{bug_id} to go/crash signature '{stable_sig}': {detail}\n"
            )
    except Exception as e:  # noqa: BLE001
        if verbose:
            sys.stderr.write(f"⚠️ Auto-link to go/crash skipped: {e}\n")


def _run_file_bug_from_rca(
    args: argparse.Namespace,
    crash_id: str,
    sandbox_dir: str,
    token: str | None,
) -> None:
    """Directly creates/updates a Buganizer issue from an RCA file and links it in go/crash."""
    json_mode = getattr(args, "json", False)
    qa = getattr(args, "qa", False)
    verbose = getattr(args, "verbose", False)
    no_link_crash = getattr(args, "no_link_crash", False)
    rca_file_arg = getattr(args, "from_rca", "")

    rca_path = Path(rca_file_arg).expanduser().resolve()
    if not rca_path.is_file():
        msg = f"RCA markdown file not found: {rca_path}"
        if json_mode:
            print_result(
                {"status": "error", "action": "crash file-bug", "message": msg},
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {msg}\n")
        sys.exit(1)

    rca_content = rca_path.read_text(encoding="utf-8").strip()
    if not rca_content:
        msg = f"RCA markdown file is empty: {rca_path}"
        if json_mode:
            print_result(
                {"status": "error", "action": "crash file-bug", "message": msg},
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {msg}\n")
        sys.exit(1)

    stable_sig = (getattr(args, "signature", None) or "").strip()
    if not stable_sig:
        stable_sig = _extract_signature_from_sandbox(sandbox_dir)
    if not stable_sig:
        if is_crash_report_id(args.crash_id):
            stable_sig = resolve_signature_from_report_id(crash_id) or ""
        else:
            stable_sig = args.crash_id.strip()

    if not stable_sig:
        msg = (
            f"Could not determine stable_signature for '{args.crash_id}'. "
            "Pass --signature '<stable_signature>' explicitly."
        )
        if json_mode:
            print_result(
                {"status": "error", "action": "crash file-bug", "message": msg},
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {msg}\n")
        sys.exit(1)

    try:
        crash_modules = ensure_crashadvisor_imports()
        buganizer_mod = crash_modules["buganizer"]
        client = buganizer_mod.BuganizerClient(token=token, qa_mode=qa, verbose=verbose)

        explicit_bug_id = getattr(args, "bug_id", None)
        issue_action = "created"
        if explicit_bug_id:
            bug_id = int(str(explicit_bug_id).removeprefix("b/").strip())
            client.update_issue_comment(bug_id, rca_content)
            issue_action = "updated"
        else:
            existing_issue = client.search_issue_by_signature(stable_sig)
            if is_safe_signature_issue_match(stable_sig, existing_issue):
                bug_id = int(
                    existing_issue.get("id") or existing_issue.get("issueId") or 0
                )
                if bug_id > 0:
                    client.update_issue_comment(bug_id, rca_content)
                    issue_action = "updated"
                else:
                    existing_issue = None
            else:
                existing_issue = None

            if not existing_issue:
                explicit_title = getattr(args, "title", None)
                created = client.create_issue(
                    stable_sig, rca_content, title=explicit_title
                )
                if isinstance(created, dict):
                    bug_id = int(created.get("id") or created.get("issueId") or 0)
                else:
                    bug_id = int(created)
                issue_action = "created"

        if not bug_id or bug_id <= 0:
            raise RuntimeError("Buganizer did not return a valid positive issue ID.")
    except Exception as e:  # noqa: BLE001
        msg = f"Buganizer operation failed for '{stable_sig}': {e}"
        if json_mode:
            print_result(
                {"status": "error", "action": "crash file-bug", "message": msg},
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {msg}\n")
        sys.exit(1)

    bug_url = f"https://b.corp.google.com/issues/{bug_id}"
    linked_in_crash = False
    link_detail = ""
    if not no_link_crash and not qa:
        linked_in_crash, link_detail = update_crash_annotation(
            stable_signature=stable_sig,
            bug_url=bug_url,
        )

    payload: dict[str, Any] = {
        "status": "success",
        "action": "crash file-bug",
        "issue_action": issue_action,
        "crash_id": crash_id,
        "stable_signature": stable_sig,
        "bug_id": str(bug_id),
        "bug_url": bug_url,
        "rca_file": str(rca_path),
        "linked_in_crash": linked_in_crash,
    }
    if json_mode:
        print_result(payload, json_mode=True)
        return

    print(
        f"✅ Buganizer issue b/{bug_id} ({issue_action}) for signature '{stable_sig}': {bug_url}"
    )
    if not no_link_crash and not qa:
        if linked_in_crash:
            print(f"🔗 Linked {bug_url} to go/crash signature '{stable_sig}'.")
        else:
            sys.stderr.write(f"⚠️ Could not link {bug_url} in go/crash: {link_detail}\n")


def run_file_bug(args: argparse.Namespace) -> None:
    """Runs RCA analysis and files or updates a Buganizer issue.

    Args:
        args: Parsed command line arguments containing crash_id, token, qa, verbose flags.
    """
    crash_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    sandbox_dir = get_crashadvisor_sandbox_dir(crash_id, create=True)

    if getattr(args, "from_rca", None):
        _run_file_bug_from_rca(
            args=args,
            crash_id=crash_id,
            sandbox_dir=sandbox_dir,
            token=token,
        )
        return

    cmd_args = [crash_id, "--enable-buganizer", "--auto-run", "--work-dir", sandbox_dir]
    if token:
        cmd_args.extend(["--token", token])
    if getattr(args, "qa", False):
        cmd_args.append("--qa")
    if getattr(args, "verbose", False):
        cmd_args.append("--verbose")
    if getattr(args, "title", None):
        cmd_args.extend(["--title", args.title])

    print(
        f"🚀 Filing/updating Buganizer issue for Crash ID {crash_id} via CrashAdvisor..."
    )
    try:
        run_crashadvisor_bazel(cmd_args)
    except Exception as e:
        sys.stderr.write(f"❌ Failed to run CrashAdvisor: {e}\n")
        sys.exit(1)

    if not getattr(args, "no_link_crash", False) and not getattr(args, "qa", False):
        _auto_link_filed_bug_to_crash(
            sandbox_dir=sandbox_dir,
            token=token,
            verbose=getattr(args, "verbose", False),
            explicit_bug_id=getattr(args, "bug_id", None),
        )


def register_file_bug_parser(crash_subparsers) -> None:
    """Registers the `file-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    file_bug_parser = crash_subparsers.add_parser(
        "file-bug",
        help="Run RCA analysis (or use --from-rca), create/update a Buganizer issue, and link it in go/crash",
        description=(
            "Creates or updates a Buganizer issue for a crash and links the bug URL to the crash "
            "signature in go/crash. Pass --from-rca <file.md> when an agent has already written "
            "the RCA to file directly without spawning a nested AI session."
        ),
    )
    file_bug_parser.add_argument(
        "crash_id",
        help="Crash ID (e.g. 05d8356e2f800000), go/crash URL, or stable_signature (when used with --from-rca)",
    )
    file_bug_parser.add_argument(
        "--from-rca",
        dest="from_rca",
        help="Path to an existing Markdown RCA file; files/updates Buganizer and links go/crash directly without spawning a nested AI session",
    )
    file_bug_parser.add_argument(
        "--title",
        help="Descriptive Buganizer issue title. If omitted, will be extracted from RCA (# Title: ...) or derived from faulting frame",
    )
    file_bug_parser.add_argument(
        "--signature",
        help="Explicit stable_signature (optional override when using --from-rca)",
    )
    file_bug_parser.add_argument(
        "--bug-id",
        dest="bug_id",
        help="Explicit existing Buganizer issue ID to attach the RCA comment to (optional with --from-rca)",
    )
    file_bug_parser.add_argument(
        "--token", help="OAuth2 token for Buganizer & Crash API"
    )
    file_bug_parser.add_argument(
        "--qa", action="store_true", help="Run against QA Issue Tracker endpoint"
    )
    file_bug_parser.add_argument(
        "--no-link-crash",
        action="store_true",
        help="Skip automatically linking the Buganizer issue to the crash signature in go/crash",
    )
    file_bug_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output machine-readable JSON result",
    )
    file_bug_parser.set_defaults(func=run_file_bug)
