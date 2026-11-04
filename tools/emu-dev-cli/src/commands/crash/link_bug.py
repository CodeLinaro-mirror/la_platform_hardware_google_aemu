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

"""Associate or unlink a Buganizer issue with a crash signature in go/crash (`crash link-bug`).

Uses `QueryAPIService.UpdateAnnotation` and `QueryAPIService.SearchAnnotations` on
`blade:crash-queryapi` via `stubby` to maintain real-time bug associations in `go/crash`.
"""

import argparse
import json
import re
import shutil
import sys
from typing import Any

from clients.buganizer_service import (
    is_safe_signature_issue_match as _service_is_safe_signature_issue_match,
    normalize_bug_url_and_id as _service_normalize_bug_url_and_id,
)
from core.command import Command, CommandResult, bind_command
from core.context import (
    ExecutionContext,
    SERVICE_F1_SQL,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import CommandValidationError, ExecutionError, UsageError
from core.process import ProcessRunner, SubprocessRunner
from lib.output import print_result

from commands.crash.utils import parse_crash_id

DEFAULT_PRODUCT_NAME = "AndroidEmulator"
DEFAULT_SIGNATURE_TYPE = "Stable"
CRASH_QUERYAPI_TARGET = "blade:crash-queryapi"
DEFAULT_F1_SQL_BIN = "/google/bin/releases/f1-team/public/f1-sql"

_HEX_REPORT_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_ANNOTATION_BLOCK_RE = re.compile(r"annotations\s*\{(.*?)\n\}", re.DOTALL)
_STACK_SIG_RE = re.compile(r'StackSignature:\s*"((?:[^"\\]|\\.)*)"')
_BUG_INFO_BLOCK_RE = re.compile(r"BugInfo\s*\{(.*?)\}", re.DOTALL)
_BUG_ID_FIELD_RE = re.compile(r"BugID:\s*(\d+)")
_BUG_URL_FIELD_RE = re.compile(r'BugUrl:\s*"((?:[^"\\]|\\.)*)"')


def is_safe_signature_issue_match(
    stable_signature: str, issue: dict[str, Any] | None
) -> bool:
    """Checks whether a Buganizer issue returned by signature search is a safe exact match."""
    return _service_is_safe_signature_issue_match(stable_signature, issue)


def normalize_bug_url_and_id(bug_input: str) -> tuple[str, str]:
    """Normalizes a Buganizer ID, shortlink, or full URL into (bug_id, canonical_url)."""
    try:
        return _service_normalize_bug_url_and_id(bug_input)
    except UsageError as exc:
        raise ValueError(exc.message) from exc


def parse_search_annotations_output(
    raw_text: str,
) -> dict[str, list[dict[str, str]]]:
    """Parses textproto output from `QueryAPIService.SearchAnnotations`.

    Args:
        raw_text: Raw stdout from `stubby call blade:crash-queryapi QueryAPIService.SearchAnnotations`.

    Returns:
        Dictionary mapping `StackSignature` to a list of `{"bug_id": str, "bug_url": str}` dicts.
    """
    result: dict[str, list[dict[str, str]]] = {}
    for match in _ANNOTATION_BLOCK_RE.finditer(raw_text):
        block = match.group(1)
        sig_match = _STACK_SIG_RE.search(block)
        if not sig_match:
            continue
        sig = sig_match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        bugs: list[dict[str, str]] = []
        for b_match in _BUG_INFO_BLOCK_RE.finditer(block):
            b_block = b_match.group(1)
            id_m = _BUG_ID_FIELD_RE.search(b_block)
            url_m = _BUG_URL_FIELD_RE.search(b_block)
            bug_id = id_m.group(1).strip() if id_m else ""
            bug_url = url_m.group(1).strip() if url_m else ""
            if bug_id and not bug_url:
                bug_url = f"https://b.corp.google.com/issues/{bug_id}"
            if bug_id or bug_url:
                bugs.append({"bug_id": bug_id, "bug_url": bug_url})
        if bugs:
            result.setdefault(sig, []).extend(bugs)
    return result


def search_crash_annotations(
    signatures: list[str],
    product_name: str = DEFAULT_PRODUCT_NAME,
    signature_type: str = DEFAULT_SIGNATURE_TYPE,
    stubby_bin: str | None = None,
    runner: ProcessRunner | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Queries live `blade:crash-queryapi` `QueryAPIService.SearchAnnotations` for bug links.

    Args:
        signatures: List of `stable_signature` strings to query.
        product_name: Crash product name (default: 'AndroidEmulator').
        signature_type: Signature type (default: 'Stable').
        stubby_bin: Optional explicit path to `stubby` binary.
        runner: Optional injected ProcessRunner for hermetic command execution.

    Returns:
        Mapping from `stable_signature` to list of `{"bug_id": str, "bug_url": str}`.
    """
    clean_sigs = [s.strip() for s in signatures if s and s.strip()]
    if not clean_sigs:
        return {}

    resolved_stubby = stubby_bin or (
        "stubby" if runner is not None else shutil.which("stubby")
    )
    if not resolved_stubby:
        return {}

    sig_entries = ", ".join(json.dumps(sig) for sig in clean_sigs)
    request_proto = (
        "parameters: {\n"
        f"  product_name: {json.dumps(product_name)}\n"
        f"  signature_type: {json.dumps(signature_type)}\n"
        f"  stack_signatures: [{sig_entries}]\n"
        "}"
    )
    cmd = [
        resolved_stubby,
        "call",
        CRASH_QUERYAPI_TARGET,
        "QueryAPIService.SearchAnnotations",
        request_proto,
    ]

    proc_runner = runner or SubprocessRunner()
    try:
        res = proc_runner.run(cmd, check=False, timeout_sec=15.0)
        if res.returncode != 0 or not isinstance(res.stdout, str):
            return {}
        return parse_search_annotations_output(res.stdout)
    except Exception:
        return {}


def update_crash_annotation(
    stable_signature: str,
    bug_url: str,
    unlink: bool = False,
    product_name: str = DEFAULT_PRODUCT_NAME,
    signature_type: str = DEFAULT_SIGNATURE_TYPE,
    stubby_bin: str | None = None,
    runner: ProcessRunner | None = None,
) -> tuple[bool, str]:
    """Associates or removes a bug URL on a `stable_signature` in `go/crash`.

    Calls `QueryAPIService.UpdateAnnotation` on `blade:crash-queryapi`.

    Args:
        stable_signature: Target `stable_signature` string in `go/crash`.
        bug_url: Canonical Buganizer URL to attach or remove.
        unlink: If True, removes `bug_url` instead of adding it.
        product_name: Crash product name (default: 'AndroidEmulator').
        signature_type: Signature type (default: 'Stable').
        stubby_bin: Optional explicit path to `stubby` binary.
        runner: Optional injected ProcessRunner for hermetic command execution.

    Returns:
        Tuple of (success: bool, raw_output_or_error: str).
    """
    resolved_stubby = stubby_bin or (
        "stubby" if runner is not None else shutil.which("stubby")
    )
    if not resolved_stubby:
        return False, "stubby binary not found on PATH"

    url_field = "remove_bug_urls" if unlink else "add_bug_urls"
    request_proto = (
        f"product: {json.dumps(product_name)}\n"
        f"signature_type: {json.dumps(signature_type)}\n"
        f"stack_signature: {json.dumps(stable_signature)}\n"
        f"{url_field}: {json.dumps(bug_url)}\n"
    )
    cmd = [
        resolved_stubby,
        "call",
        CRASH_QUERYAPI_TARGET,
        "QueryAPIService.UpdateAnnotation",
        request_proto,
    ]

    proc_runner = runner or SubprocessRunner()
    try:
        res = proc_runner.run(cmd, check=False, timeout_sec=15.0)
        stdout_str = res.stdout if isinstance(res.stdout, str) else ""
        stderr_str = res.stderr if isinstance(res.stderr, str) else ""
        if res.returncode == 0 and "status_code: SUCCESS" in stdout_str:
            return True, stdout_str.strip()
        return False, (
            stderr_str.strip() or stdout_str.strip() or f"Exit code {res.returncode}"
        )
    except Exception as exc:
        return False, str(exc)


def is_crash_report_id(target: str) -> bool:
    """Returns True if `target` is a go/crash URL or a 16-character hex ReportID."""
    raw = target.strip()
    if "crash.corp.google.com/" in raw or "go/crash/" in raw:
        cleaned = parse_crash_id(raw)
        return bool(_HEX_REPORT_ID_RE.match(cleaned))
    return bool(_HEX_REPORT_ID_RE.match(raw))


def resolve_signature_from_report_id(
    report_id: str,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
    product_name: str = DEFAULT_PRODUCT_NAME,
    f1_client: Any = None,
    runner: ProcessRunner | None = None,
) -> str | None:
    """Resolves `stable_signature` for a 16-hex Crash `ReportID` via F1 SQL.

    Args:
        report_id: 16-character hex Crash ReportID.
        f1_sql_bin: Path to `f1-sql` executable.
        product_name: Crash product name (default: 'AndroidEmulator').
        f1_client: Optional injected F1SqlClient.
        runner: Optional injected ProcessRunner.

    Returns:
        The `stable_signature` string if found, else None.
    """
    clean_id = parse_crash_id(report_id)
    if not _HEX_REPORT_ID_RE.match(clean_id) or "'" in product_name:
        return None

    sql = (
        "SELECT stable_signature FROM crash.prod.latest "
        f"WHERE product_name = '{product_name}' AND ReportID = '{clean_id}' LIMIT 1;"
    )
    if f1_client is not None:
        if hasattr(f1_client, "query_csv"):
            rows = f1_client.query_csv(sql)
        else:
            rows = f1_client.execute_sql(sql)
        for row in rows:
            val = str(row.get("stable_signature") or "").strip()
            if val and val != "NULL":
                return val
        return None

    proc_runner = runner or SubprocessRunner()
    try:
        res_run = proc_runner.run(
            [f1_sql_bin, "--csv_output=true"],
            input_text=sql,
            check=False,
            timeout_sec=60.0,
        )
        if res_run.returncode != 0 or not isinstance(res_run.stdout, str):
            return None
        raw_lines = res_run.stdout.splitlines()
        header_idx = next(
            (
                i
                for i, line in enumerate(raw_lines)
                if line.strip().strip('"') == "stable_signature"
            ),
            None,
        )
        if header_idx is None:
            return None
        for line in raw_lines[header_idx + 1 :]:
            val = line.strip().strip('"').replace('""', '"')
            if val and val != "NULL":
                return val
        return None
    except Exception:
        return None


class CrashLinkBugCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash link-bug`."""

    name = "link-bug"
    help_text = (
        "Associate (or unlink) a Buganizer issue URL with a crash signature in go/crash"
    )
    help = help_text
    description = (
        "Associates or removes a Buganizer issue URL on a crash signature in go/crash "
        "via QueryAPIService.UpdateAnnotation on blade:crash-queryapi. Accepts either a "
        "stable_signature or a 16-hex crash ReportID."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
        parser.add_argument(
            "target",
            help="Crash stable_signature (e.g. 'virtio_gpu_rutabaga_load-e709a6ba') or 16-hex crash ReportID",
        )
        parser.add_argument(
            "bug",
            help="Buganizer issue ID (e.g. '567125517', 'b/567125517') or full URL",
        )
        parser.add_argument(
            "--unlink",
            action="store_true",
            help="Remove the bug URL association from the crash signature instead of adding it",
        )
        parser.add_argument(
            "--product",
            default=DEFAULT_PRODUCT_NAME,
            help=f"go/crash product name (default: {DEFAULT_PRODUCT_NAME})",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON result",
        )

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        target = str(
            getattr(args, "target", None)
            or getattr(args, "signature", None)
            or ""
        ).strip()
        if not target:
            raise CommandValidationError(
                "Crash target signature or ReportID cannot be empty for 'crash link-bug'.",
                remediation="Pass a non-empty stable_signature or 16-hex crash ReportID.",
            )
        if not getattr(args, "target", None):
            args.target = target
        bug_arg = str(
            getattr(args, "bug", None) or getattr(args, "bug_id", None) or ""
        ).strip()
        try:
            normalize_bug_url_and_id(bug_arg)
        except ValueError as exc:
            raise CommandValidationError(
                str(exc),
                remediation="Pass a numeric Buganizer ID (e.g. '567125517'), shortlink ('b/567125517'), or full issue URL.",
            ) from exc
        if not getattr(args, "bug", None):
            args.bug = bug_arg
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        unlink = bool(getattr(args, "unlink", False))
        product_name = str(getattr(args, "product", DEFAULT_PRODUCT_NAME))
        target = str(args.target).strip()
        bug_id, bug_url = normalize_bug_url_and_id(str(args.bug))

        f1_client = ctx.services.get(SERVICE_F1_SQL)
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)

        report_id: str | None = None
        stable_signature = target
        if is_crash_report_id(target):
            report_id = parse_crash_id(target)
            resolved = resolve_signature_from_report_id(
                report_id,
                product_name=product_name,
                f1_client=f1_client,
                runner=runner,
            )
            if not resolved:
                raise ExecutionError(
                    f"Could not resolve stable_signature for ReportID '{report_id}' in product '{product_name}'.",
                    remediation="Verify the 16-hex ReportID exists in crash.prod.latest or pass the stable_signature directly.",
                )
            stable_signature = resolved

        ok, detail = update_crash_annotation(
            stable_signature=stable_signature,
            bug_url=bug_url,
            unlink=unlink,
            product_name=product_name,
            runner=runner,
        )
        if not ok:
            raise ExecutionError(
                f"Failed to {'unlink' if unlink else 'link'} {bug_url} for signature '{stable_signature}': {detail}",
                remediation="Ensure stubby is available and you have access to blade:crash-queryapi.",
            )

        current_annotations = search_crash_annotations(
            [stable_signature],
            product_name=product_name,
            runner=runner,
        ).get(stable_signature, [])

        verb = "Unlinked" if unlink else "Linked"
        payload: dict[str, Any] = {
            "status": "success",
            "action": "crash link-bug",
            "stable_signature": stable_signature,
            "report_id": report_id,
            "bug_id": bug_id,
            "bug_url": bug_url,
            "unlinked": unlink,
            "linked_bugs": current_annotations,
        }
        return CommandResult(
            data=payload,
            action="crash link-bug",
            summary=f"{verb} bug {bug_url} for go/crash signature '{stable_signature}'.",
        )


def register_link_bug_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `link-bug` subcommand parser under `crash`."""
    return CrashLinkBugCommand().register(crash_subparsers)
