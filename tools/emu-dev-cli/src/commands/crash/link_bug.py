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
import subprocess
import sys
from typing import Any

from lib.output import print_result

from commands.crash.utils import parse_crash_id

DEFAULT_PRODUCT_NAME = "AndroidEmulator"
DEFAULT_SIGNATURE_TYPE = "Stable"
CRASH_QUERYAPI_TARGET = "blade:crash-queryapi"
DEFAULT_F1_SQL_BIN = "/google/bin/releases/f1-team/public/f1-sql"

_HEX_REPORT_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_BUG_ID_EXTRACT_RE = re.compile(r"(?:^|b/|issues/)(\d+)(?:[/?#]|$)")
_ANNOTATION_BLOCK_RE = re.compile(r"annotations\s*\{(.*?)\n\}", re.DOTALL)
_STACK_SIG_RE = re.compile(r'StackSignature:\s*"((?:[^"\\]|\\.)*)"')
_BUG_INFO_BLOCK_RE = re.compile(r"BugInfo\s*\{(.*?)\}", re.DOTALL)
_BUG_ID_FIELD_RE = re.compile(r"BugID:\s*(\d+)")
_BUG_URL_FIELD_RE = re.compile(r'BugUrl:\s*"((?:[^"\\]|\\.)*)"')
_SIG_HASH_SUFFIX_RE = re.compile(r"-([0-9a-fA-F]{6,16})\b")
_GENERIC_WRAPPER_SIG_RE = re.compile(
    r"(?:FailWithoutStackTrace|__abort_with_payload|PthreadWaiter::Wait|"
    r"FutexWaiter::Wait|CrashReporterImpl::Die|LogMessage::Flush|"
    r"\babort\b|std::terminate)"
)


def is_safe_signature_issue_match(
    stable_signature: str, issue: dict[str, Any] | None
) -> bool:
    """Checks whether a Buganizer issue returned by signature search is a safe exact match.

    Prevents false-positive matches when `BuganizerClient.search_issue_by_signature`
    or Buganizer tokenization strips the `-<hex_hash>` suffix on generic abort/log/waiter
    wrapper signatures (e.g. `FailWithoutStackTrace-7a37e8e9` matching `FailWithoutStackTrace-b59fb732`).
    """
    if not issue or not isinstance(issue, dict):
        return False
    sig = stable_signature.strip()
    if not sig:
        return False

    state = issue.get("issueState") or {}
    title = str(state.get("title") or issue.get("title") or "").strip()
    if not title:
        return True
    sig_hash_match = _SIG_HASH_SUFFIX_RE.search(sig)

    if sig_hash_match:
        expected_hash = sig_hash_match.group(1).lower()
        title_hashes = [m.lower() for m in _SIG_HASH_SUFFIX_RE.findall(title)]
        if title_hashes and expected_hash not in title_hashes:
            return False
        if _GENERIC_WRAPPER_SIG_RE.search(sig) and expected_hash not in title_hashes:
            return False

    return True


def normalize_bug_url_and_id(bug_input: str) -> tuple[str, str]:
    """Normalizes a Buganizer ID, shortlink, or full URL into (bug_id, canonical_url).

    Args:
        bug_input: Raw bug string such as '567125517', 'b/567125517', or
            'https://b.corp.google.com/issues/567125517'.

    Returns:
        Tuple of (bug_id_str, canonical_bug_url).

    Raises:
        ValueError: If no numeric bug ID or valid URL can be extracted.
    """
    cleaned = bug_input.strip()
    if cleaned.isdigit():
        return cleaned, f"https://b.corp.google.com/issues/{cleaned}"

    match = _BUG_ID_EXTRACT_RE.search(cleaned)
    if match:
        bug_id = match.group(1)
        return bug_id, f"https://b.corp.google.com/issues/{bug_id}"

    if cleaned.startswith(("http://", "https://")):
        return "", cleaned

    raise ValueError(
        f"Invalid bug identifier '{bug_input}'. Expected a numeric Buganizer ID (e.g. 567125517), "
        "shortlink (b/567125517), or full issue URL."
    )


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
) -> dict[str, list[dict[str, str]]]:
    """Queries live `blade:crash-queryapi` `QueryAPIService.SearchAnnotations` for bug links.

    Args:
        signatures: List of `stable_signature` strings to query.
        product_name: Crash product name (default: 'AndroidEmulator').
        signature_type: Signature type (default: 'Stable').
        stubby_bin: Optional explicit path to `stubby` binary.

    Returns:
        Mapping from `stable_signature` to list of `{"bug_id": str, "bug_url": str}`.
    """
    clean_sigs = [s.strip() for s in signatures if s and s.strip()]
    if not clean_sigs:
        return {}

    resolved_stubby = stubby_bin or shutil.which("stubby")
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

    try:
        res = subprocess.run(
            [
                resolved_stubby,
                "call",
                CRASH_QUERYAPI_TARGET,
                "QueryAPIService.SearchAnnotations",
                request_proto,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode != 0 or not isinstance(res.stdout, str):
            return {}
        return parse_search_annotations_output(res.stdout)
    except (OSError, subprocess.SubprocessError):
        return {}


def update_crash_annotation(
    stable_signature: str,
    bug_url: str,
    unlink: bool = False,
    product_name: str = DEFAULT_PRODUCT_NAME,
    signature_type: str = DEFAULT_SIGNATURE_TYPE,
    stubby_bin: str | None = None,
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

    Returns:
        Tuple of (success: bool, raw_output_or_error: str).
    """
    resolved_stubby = stubby_bin or shutil.which("stubby")
    if not resolved_stubby:
        return False, "stubby binary not found on PATH"

    url_field = "remove_bug_urls" if unlink else "add_bug_urls"
    request_proto = (
        f"product: {json.dumps(product_name)}\n"
        f"signature_type: {json.dumps(signature_type)}\n"
        f"stack_signature: {json.dumps(stable_signature)}\n"
        f"{url_field}: {json.dumps(bug_url)}\n"
    )

    try:
        res = subprocess.run(
            [
                resolved_stubby,
                "call",
                CRASH_QUERYAPI_TARGET,
                "QueryAPIService.UpdateAnnotation",
                request_proto,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        stdout_str = res.stdout if isinstance(res.stdout, str) else ""
        stderr_str = res.stderr if isinstance(res.stderr, str) else ""
        if res.returncode == 0 and "status_code: SUCCESS" in stdout_str:
            return True, stdout_str.strip()
        return False, (
            stderr_str.strip() or stdout_str.strip() or f"Exit code {res.returncode}"
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


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
) -> str | None:
    """Resolves `stable_signature` for a 16-hex Crash `ReportID` via F1 SQL.

    Args:
        report_id: 16-character hex Crash ReportID.
        f1_sql_bin: Path to `f1-sql` executable.
        product_name: Crash product name (default: 'AndroidEmulator').

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
    try:
        res = subprocess.run(
            [f1_sql_bin, "--csv_output=true"],
            input=sql,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if res.returncode != 0 or not isinstance(res.stdout, str):
            return None
        raw_lines = res.stdout.splitlines()
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
    except (OSError, subprocess.SubprocessError):
        return None


def run_link_bug(args: argparse.Namespace) -> None:
    """CLI handler for `emu-dev-cli crash link-bug`."""
    json_mode = getattr(args, "json", False)
    unlink = getattr(args, "unlink", False)
    product_name = getattr(args, "product", DEFAULT_PRODUCT_NAME)
    target = args.target.strip()

    try:
        bug_id, bug_url = normalize_bug_url_and_id(args.bug)
    except ValueError as e:
        if json_mode:
            print_result(
                {"status": "error", "message": str(e)},
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {e}\n")
        sys.exit(1)

    report_id: str | None = None
    stable_signature = target
    if is_crash_report_id(target):
        report_id = parse_crash_id(target)
        if not json_mode:
            print(f"🔎 Resolving stable_signature for ReportID {report_id}...")
        resolved = resolve_signature_from_report_id(
            report_id, product_name=product_name
        )
        if not resolved:
            msg = (
                f"Could not resolve stable_signature for ReportID '{report_id}'. "
                "Please pass the stable_signature directly."
            )
            if json_mode:
                print_result(
                    {"status": "error", "message": msg},
                    json_mode=True,
                    is_error=True,
                )
            else:
                sys.stderr.write(f"❌ {msg}\n")
            sys.exit(1)
        stable_signature = resolved

    ok, detail = update_crash_annotation(
        stable_signature=stable_signature,
        bug_url=bug_url,
        unlink=unlink,
        product_name=product_name,
    )

    if not ok:
        msg = f"Failed to {'unlink' if unlink else 'link'} {bug_url} for signature '{stable_signature}': {detail}"
        if json_mode:
            print_result(
                {
                    "status": "error",
                    "action": "crash link-bug",
                    "stable_signature": stable_signature,
                    "report_id": report_id,
                    "bug_id": bug_id,
                    "bug_url": bug_url,
                    "unlinked": unlink,
                    "message": msg,
                },
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ {msg}\n")
        sys.exit(1)

    # Fetch updated annotations to confirm current state
    current_annotations = search_crash_annotations(
        [stable_signature], product_name=product_name
    ).get(stable_signature, [])

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

    if json_mode:
        print_result(payload, json_mode=True)
    else:
        verb = "Unlinked" if unlink else "Linked"
        print(
            f"✅ {verb} bug {bug_url} {'from' if unlink else 'to'} go/crash signature:"
        )
        print(f"   • Signature: {stable_signature}")
        if report_id:
            print(f"   • Report ID: {report_id}")
        if current_annotations:
            urls = ", ".join(b["bug_url"] for b in current_annotations)
            print(f"   • Active go/crash Bug Links: {urls}")
        else:
            print("   • Active go/crash Bug Links: (none)")


def register_link_bug_parser(crash_subparsers) -> None:
    """Registers the `link-bug` subcommand parser under `crash`."""
    link_parser = crash_subparsers.add_parser(
        "link-bug",
        help="Associate (or unlink) a Buganizer issue URL with a crash signature in go/crash",
        description=(
            "Associates or removes a Buganizer issue URL on a crash signature in go/crash "
            "via QueryAPIService.UpdateAnnotation on blade:crash-queryapi. Accepts either a "
            "stable_signature or a 16-hex crash ReportID."
        ),
    )
    link_parser.add_argument(
        "target",
        help="Crash stable_signature (e.g. 'virtio_gpu_rutabaga_load-e709a6ba') or 16-hex crash ReportID",
    )
    link_parser.add_argument(
        "bug",
        help="Buganizer issue ID (e.g. '567125517', 'b/567125517') or full URL",
    )
    link_parser.add_argument(
        "--unlink",
        action="store_true",
        help="Remove the bug URL association from the crash signature instead of adding it",
    )
    link_parser.add_argument(
        "--product",
        default=DEFAULT_PRODUCT_NAME,
        help=f"go/crash product name (default: {DEFAULT_PRODUCT_NAME})",
    )
    link_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output machine-readable JSON result",
    )
    link_parser.set_defaults(func=run_link_bug)
