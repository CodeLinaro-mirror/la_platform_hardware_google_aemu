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
import logging
from pathlib import Path
import re
import sys
from typing import Any

from commands.crash.advisor import (
    CrashAdvisorRunner,
    create_crash_bug_tracker,
    ensure_crashadvisor_imports,
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
    parse_crash_id,
    resolve_crash_work_dir,
)
from core.command import Command, CommandResult
from core.context import (
    ExecutionContext,
    SERVICE_BUGANIZER,
    SERVICE_F1_SQL,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import CommandValidationError, ExecutionError
from lib.output import print_status

logger = logging.getLogger(__name__)


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


def _extract_crash_info_from_sandbox(
    sandbox_dir: str, rca_content: str = ""
) -> dict[str, Any]:
    """Extracts crash metadata (version, os, build_id, custom_keys, crash_location) from sandbox or RCA."""
    info: dict[str, Any] = {
        "os": [],
        "versions": [],
        "build_ids": [],
        "crash_location": "",
        "custom_keys": {},
    }
    metadata_path = Path(sandbox_dir) / "metadata.json"
    if metadata_path.exists():
        try:
            meta_data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(meta_data, dict):
                meta_data = {}
            proto = (
                meta_data.get("report_proto")
                if isinstance(meta_data, dict)
                else {}
            ) or meta_data
            if not isinstance(proto, dict):
                proto = {}
            prod = proto.get("product", {}) if isinstance(proto, dict) else {}
            raw_ver = (
                prod.get("version") if isinstance(prod, dict) else None
            ) or meta_data.get("version") or meta_data.get("versions") or proto.get("versions") or ""
            if isinstance(raw_ver, list):
                info["versions"].extend([str(v).strip() for v in raw_ver if v])
            elif raw_ver:
                info["versions"].append(str(raw_ver).strip())

            raw_os = (
                proto.get("operatingSystem")
                or meta_data.get("os_name")
                or meta_data.get("os")
                or ""
            )
            if isinstance(raw_os, list):
                for item in raw_os:
                    val = item.get("name") if isinstance(item, dict) else item
                    name = str(val or "").strip()
                    if name and name not in info["os"]:
                        info["os"].append(name)
            elif isinstance(raw_os, dict):
                os_name = str(raw_os.get("name") or "").strip()
                if os_name and os_name not in info["os"]:
                    info["os"].append(os_name)
            else:
                os_name = str(raw_os or "").strip()
                if os_name and os_name not in info["os"]:
                    info["os"].append(os_name)

            raw_bid = meta_data.get("build_id") or meta_data.get("build_ids") or proto.get("buildIds") or ""
            if isinstance(raw_bid, list):
                info["build_ids"].extend([str(b).strip() for b in raw_bid if b])
            elif raw_bid:
                info["build_ids"].append(str(raw_bid).strip())

            loc = meta_data.get("crash_location") or proto.get("crashLocation") or ""
            if loc:
                info["crash_location"] = str(loc).strip()

            for raw_keys in (proto.get("customKeys"), meta_data.get("custom_keys")):
                if isinstance(raw_keys, list):
                    for item in raw_keys:
                        if isinstance(item, dict) and "key" in item:
                            val = item.get("value")
                            info["custom_keys"][str(item["key"])] = str(val if val is not None else "")
                elif isinstance(raw_keys, dict):
                    for k, v in raw_keys.items():
                        info["custom_keys"][str(k)] = str(v if v is not None else "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed parsing %s: %s", metadata_path, exc)

    if rca_content:
        if not info["versions"]:
            m_ver = re.search(
                r"(?:Version|Build Version)[:\s*]+`?([0-9]+\.[0-9]+[^\s`\)]*)`?",
                rca_content,
                re.IGNORECASE,
            )
            if m_ver:
                info["versions"].append(m_ver.group(1))
        if not info["os"]:
            m_os = re.search(
                r"(?:Host OS|Operating System)[:\s*]+`?([^`\n]+)`?",
                rca_content,
                re.IGNORECASE,
            )
            if m_os:
                info["os"].append(m_os.group(1).strip())
        if not info["build_ids"]:
            m_bid = re.search(
                r"(?:Build ID|Build IDs)[:\s*]+`?([0-9]{7,})`?",
                rca_content,
                re.IGNORECASE,
            )
            if m_bid:
                info["build_ids"].append(m_bid.group(1))

    return info


def _build_default_bug_title(
    signature: str, crash_info: dict[str, Any]
) -> str:
    """Builds a standard, informative Buganizer title for crash bugs."""
    platform = crash_info["os"][0] if crash_info.get("os") else "Emulator"
    loc = crash_info.get("crash_location", "").strip()
    if loc:
        return f"[Crash][{platform}] {signature}: {loc}"
    return f"[Crash][{platform}] {signature}"


def _parse_issue_metadata_from_rca(rca_content: str) -> dict[str, Any]:
    """Extracts explicit title or bug ID metadata embedded in RCA Markdown."""
    meta: dict[str, Any] = {}
    m_title = re.search(r"^#\s*Title:\s*(.+)$", rca_content, re.MULTILINE)
    if m_title:
        meta["title"] = m_title.group(1).strip()
    m_bug = re.search(r"(?:Bug|Issue):\s*b/(\d+)", rca_content, re.IGNORECASE)
    if m_bug:
        meta["bug_id"] = m_bug.group(1).strip()
    return meta


def _generate_rca_comment(
    rca_content: str,
    signature: str,
    crash_id: str,
    crash_info: dict[str, Any],
) -> str:
    """Formats the RCA comment posted to Buganizer."""
    header_lines = [
        "### Automated Crash Root Cause Analysis (RCA)",
        f"- **Crash Report ID**: `{crash_id}` ([go/crash/{crash_id}](https://crash.corp.google.com/{crash_id}))",
        f"- **Crash Signature**: `{signature}`",
    ]
    if crash_info.get("versions"):
        header_lines.append(f"- **Versions**: {', '.join(crash_info['versions'])}")
    if crash_info.get("build_ids"):
        header_lines.append(
            f"- **Android Build IDs**: {', '.join(crash_info['build_ids'])}"
        )
    if crash_info.get("os"):
        header_lines.append(f"- **OS**: {', '.join(crash_info['os'])}")
    header = "\n".join(header_lines)
    return f"{header}\n\n---\n\n{rca_content}"


class CrashFileBugCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash file-bug`."""

    name = "file-bug"
    help_text = (
        "Run RCA analysis (or use --from-rca), create/update a Buganizer issue, and link it in go/crash"
    )
    help = help_text
    description = (
        "Creates or updates a Buganizer issue for a crash and links the bug URL to the crash "
        "signature in go/crash. Pass --from-rca <file.md> when an agent has already written "
        "the RCA to file directly without spawning a nested AI session."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
        parser.add_argument(
            "crash_id",
            help="Crash ID (e.g. 05d8356e2f800000), go/crash URL, or stable_signature (when used with --from-rca)",
        )
        parser.add_argument(
            "--from-rca",
            dest="from_rca",
            help="Path to an existing Markdown RCA file; files/updates Buganizer and links go/crash directly without spawning a nested AI session",
        )
        parser.add_argument(
            "--title",
            help="Descriptive Buganizer issue title. If omitted, will be extracted from RCA (# Title: ...) or derived from faulting frame",
        )
        parser.add_argument(
            "--signature",
            help="Explicit stable_signature (optional override when using --from-rca)",
        )
        parser.add_argument(
            "--bug-id",
            dest="bug_id",
            help="Explicit existing Buganizer issue ID to attach the RCA comment to (optional with --from-rca)",
        )
        parser.add_argument(
            "--token", help="OAuth2 token for Buganizer & Crash API"
        )
        parser.add_argument(
            "--qa",
            action="store_true",
            help="Run against QA Issue Tracker endpoint",
        )
        parser.add_argument(
            "--no-link-crash",
            action="store_true",
            help="Skip automatically linking the Buganizer issue to the crash signature in go/crash",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass duplicate comment suppression and force updating the Buganizer issue",
        )
        parser.add_argument(
            "--work-dir",
            dest="work_dir",
            help="Optional explicit work directory override",
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
        if not raw_id:
            raise CommandValidationError(
                "Crash ID or signature cannot be empty for 'crash file-bug'.",
                remediation="Pass a 16-hex Crash ID, go/crash URL, or stable_signature.",
            )
        from_rca = getattr(args, "from_rca", None)
        if from_rca:
            rca_path = Path(str(from_rca)).expanduser()
            if not rca_path.is_file():
                raise CommandValidationError(
                    f"RCA markdown file not found at '{rca_path}'.",
                    remediation="Pass a valid path to an existing non-empty Markdown file via --from-rca.",
                )
            if not rca_path.read_text(encoding="utf-8").strip():
                raise CommandValidationError(
                    f"RCA markdown file '{rca_path}' is empty.",
                    remediation="Ensure the RCA markdown file contains a root-cause analysis summary before running 'crash file-bug --from-rca'.",
                )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        raw_crash_arg = str(args.crash_id).strip()
        crash_id = parse_crash_id(raw_crash_arg)
        explicit_work_dir = getattr(args, "work_dir", None)
        work_dir = resolve_crash_work_dir(
            ctx.config.cache_dir, crash_id, explicit_work_dir
        )
        sandbox_dir = str(work_dir)

        buganizer = ctx.services.get(SERVICE_BUGANIZER)
        f1_client = ctx.services.get(SERVICE_F1_SQL)
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)
        qa = bool(getattr(args, "qa", False))
        no_link_crash = bool(getattr(args, "no_link_crash", False))
        token = acquire_auth_token(getattr(args, "token", None))

        from_rca = getattr(args, "from_rca", None)
        if from_rca:
            return self._execute_from_rca(
                args=args,
                from_rca=from_rca,
                crash_id=crash_id,
                raw_crash_arg=raw_crash_arg,
                sandbox_dir=sandbox_dir,
                buganizer=buganizer,
                f1_client=f1_client,
                runner=runner,
                token=token,
                qa=qa,
                no_link_crash=no_link_crash,
            )

        flags = ["--enable-buganizer", "--auto-run"]
        if qa:
            flags.append("--qa")
        if getattr(args, "verbose", False) or ctx.config.verbose:
            flags.append("--verbose")
        if getattr(args, "title", None):
            flags.extend(["--title", str(args.title)])
        if getattr(args, "force", False):
            flags.append("--force")

        advisor_runner = CrashAdvisorRunner()
        advisor_runner.run(ctx, crash_id, work_dir, flags=flags, token=token, check=True)
        payload = {
            "status": "success",
            "action": "crash file-bug",
            "crash_id": crash_id,
            "sandbox_dir": sandbox_dir,
        }
        return CommandResult(
            data=payload,
            action="crash file-bug",
            summary=f"Filed/updated Buganizer issue for Crash ID {crash_id}.",
        )

    def _execute_from_rca(
        self,
        *,
        args: argparse.Namespace,
        from_rca: Any,
        crash_id: str,
        raw_crash_arg: str,
        sandbox_dir: str,
        buganizer: Any,
        f1_client: Any,
        runner: Any,
        token: str | None,
        qa: bool,
        no_link_crash: bool,
    ) -> CommandResult[dict[str, Any]]:
        rca_path = Path(str(from_rca)).expanduser().resolve()
        rca_content = rca_path.read_text(encoding="utf-8").strip()
        stable_sig = str(getattr(args, "signature", None) or "").strip()
        if not stable_sig:
            stable_sig = _extract_signature_from_sandbox(sandbox_dir)
        if not stable_sig:
            if is_crash_report_id(raw_crash_arg):
                stable_sig = (
                    resolve_signature_from_report_id(
                        crash_id, f1_client=f1_client, runner=runner
                    )
                    or ""
                )
            else:
                stable_sig = raw_crash_arg
        if not stable_sig:
            raise ExecutionError(
                f"Could not determine stable_signature for '{raw_crash_arg}'.",
                remediation="Pass --signature '<stable_signature>' explicitly when invoking 'crash file-bug --from-rca'.",
            )

        bug_id_str, issue_action = self._create_or_update_bug(
            args=args,
            stable_sig=stable_sig,
            rca_content=rca_content,
            sandbox_dir=sandbox_dir,
            buganizer=buganizer,
            token=token,
            qa=qa,
        )
        bug_url = f"https://b.corp.google.com/issues/{bug_id_str}"
        linked_in_crash = False
        if not no_link_crash and not qa:
            linked_in_crash, _ = update_crash_annotation(
                stable_signature=stable_sig,
                bug_url=bug_url,
                runner=runner,
            )

        payload: dict[str, Any] = {
            "status": "success",
            "action": "crash file-bug",
            "issue_action": issue_action,
            "crash_id": crash_id,
            "stable_signature": stable_sig,
            "bug_id": bug_id_str,
            "bug_url": bug_url,
            "rca_file": str(rca_path),
            "linked_in_crash": linked_in_crash,
        }
        return CommandResult(
            data=payload,
            action="crash file-bug",
            summary=f"Buganizer issue b/{bug_id_str} ({issue_action}) for signature '{stable_sig}'.",
        )

    def _create_or_update_bug(
        self,
        *,
        args: argparse.Namespace,
        stable_sig: str,
        rca_content: str,
        sandbox_dir: str,
        buganizer: Any,
        token: str | None,
        qa: bool,
    ) -> tuple[str, str]:
        default_component_id = "29601"
        explicit_bug_id = getattr(args, "bug_id", None)
        if buganizer is not None and hasattr(buganizer, "create_bug"):
            component_id = (
                getattr(args, "component_id", default_component_id)
                or default_component_id
            )
            if explicit_bug_id:
                bug_id_str = str(explicit_bug_id).removeprefix("b/").strip()
                buganizer.add_comment(bug_id_str, rca_content)
                return bug_id_str, "updated"
            title = (
                str(getattr(args, "title", None) or "").strip()
                or f"[Crash] {stable_sig}"
            )
            bug_id_str = str(
                buganizer.create_bug(
                    title=title,
                    description=rca_content,
                    component_id=component_id,
                )
            )
            return bug_id_str, "created"

        client = create_crash_bug_tracker(
            token=token,
            qa_mode=qa,
            verbose=bool(getattr(args, "verbose", False)),
        )
        crash_info = _extract_crash_info_from_sandbox(sandbox_dir, rca_content)
        clean_ver = crash_info["versions"][0] if crash_info.get("versions") else ""
        if explicit_bug_id:
            raw_id = str(explicit_bug_id).removeprefix("b/").strip()
            bug_id = int(raw_id) if raw_id.isdigit() else 0
            if bug_id <= 0:
                raise ExecutionError(
                    f"Invalid bug ID '{explicit_bug_id}': expected a positive numeric issue ID.",
                    remediation="Pass a valid Buganizer issue ID (e.g. '12345678') or shortlink ('b/12345678') via --bug-id.",
                )
            if hasattr(client, "ensure_crash_hotlists"):
                client.ensure_crash_hotlists(bug_id, version=clean_ver)
            client.update_issue_comment(bug_id, rca_content)
            return str(bug_id), "updated"

        existing_issue = client.search_issue_by_signature(stable_sig)
        if existing_issue:
            raw_id = (
                str(existing_issue.get("id") or existing_issue.get("issueId") or "")
                .removeprefix("b/")
                .strip()
            )
            bug_id = int(raw_id) if raw_id.isdigit() else 0
            if getattr(args, "force", False):
                client.update_issue_comment(bug_id, rca_content)
                return str(bug_id), "updated"
            return str(bug_id), "skipped_duplicate"

        created = client.create_issue(
            stable_sig, rca_content, title=getattr(args, "title", None)
        )
        if isinstance(created, dict):
            raw_id = (
                str(created.get("id") or created.get("issueId") or "")
                .removeprefix("b/")
                .strip()
            )
        else:
            raw_id = str(created or "").removeprefix("b/").strip()
        bug_id = int(raw_id) if raw_id.isdigit() else 0
        bug_id_str = str(bug_id)
        if bug_id > 0 and hasattr(client, "ensure_crash_hotlists"):
            client.ensure_crash_hotlists(bug_id_str, version=clean_ver)
        return bug_id_str, "created"


def register_file_bug_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `file-bug` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    return CrashFileBugCommand().register(crash_subparsers)
