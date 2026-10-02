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

"""Verifier script for `triage-crashes` workflow.

Checks live `QueryAPIService.SearchAnnotations` on `blade:crash-queryapi` to verify
that every crash signature in the batch queue now has a linked Buganizer URL in `go/crash`.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent
_EMU_DEV_CLI_DIR = _WORKFLOW_DIR.parent.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from commands.crash.advisor import ensure_crashadvisor_imports
from commands.crash.link_bug import normalize_bug_url_and_id, search_crash_annotations

_GENERIC_ABORT_TITLE_RE = re.compile(
    r"^\[Crash\](?:\s*\[[^\]]+\])?\s*(?:(?:absl::.*::)?FailWithoutStackTrace|__pthread_kill|abort|raise|std::terminate|__assert_fail)(?:-[0-9a-fA-F]+)?$",
    re.IGNORECASE,
)
_PLACEHOLDER_TITLE_RE = re.compile(
    r"<(?:Subsystem|Symptom|Function|OS|Platform|signature|stable_signature|report_id|latest_report_id|BUG_ID|descriptive_title|Failure|Concise[^>]*)[^>]*>",
    re.IGNORECASE,
)


def audit_linked_bug_titles(
    verified_links: dict[str, list[str]],
    token: str | None = None,
) -> list[dict[str, str]]:
    """Audits Buganizer issue titles for placeholders or boilerplate generic abort signatures.

    Returns a list of violation dictionaries with keys:
        'bug_id', 'url', 'title', 'signature', 'reason', 'suggested_fix'
    """
    violations: list[dict[str, str]] = []
    try:
        crash_modules = ensure_crashadvisor_imports()
        buganizer_mod = crash_modules.get("buganizer")
        if not buganizer_mod:
            return violations
        client = buganizer_mod.BuganizerClient(token=token)
    except Exception:
        return violations

    for sig, urls in verified_links.items():
        for url in urls:
            try:
                bug_id, _ = normalize_bug_url_and_id(url)
                if not bug_id or not bug_id.isdigit():
                    continue
                title = client.get_issue_title(int(bug_id))
                if not title:
                    continue
                if _PLACEHOLDER_TITLE_RE.search(title):
                    violations.append(
                        {
                            "bug_id": bug_id,
                            "url": url,
                            "title": title,
                            "signature": sig,
                            "reason": "Contains unresolved placeholder string (e.g. <Subsystem>, <Symptom>)",
                            "suggested_fix": (
                                f"/google/bin/releases/issues-cli/issues mutate update title {bug_id} "
                                f'"[Crash] <Subsystem>: <Symptom> ({sig})"'
                            ),
                        }
                    )
                elif _GENERIC_ABORT_TITLE_RE.match(title):
                    violations.append(
                        {
                            "bug_id": bug_id,
                            "url": url,
                            "title": title,
                            "signature": sig,
                            "reason": "Generic boilerplate abort signature without descriptive subsystem/symptom context",
                            "suggested_fix": (
                                f"/google/bin/releases/issues-cli/issues mutate update title {bug_id} "
                                f'"[Crash] <Subsystem>: <Symptom> ({sig})"'
                            ),
                        }
                    )
            except Exception:
                pass

    return violations


def _format_violations_error(
    violations: list[dict[str, str]], data: dict[str, Any]
) -> tuple[bool, str, dict[str, Any]]:
    lines = []
    for v in violations:
        lines.append(
            f"  - Issue b/{v['bug_id']} ({v['url']}): \"{v['title']}\"\n"
            f"    Signature: {v['signature']}\n"
            f"    Problem: {v['reason']}\n"
            f"    Command to fix:\n"
            f"      {v['suggested_fix']}"
        )
    msg = (
        f"Verification failed: {len(violations)} linked Buganizer issue(s) have uninformative or placeholder titles:\n\n"
        + "\n\n".join(lines)
        + "\n\nPlease update the Buganizer issue title(s) with meaningful subsystem/symptom context using the command(s) above, then re-run verification."
    )
    return False, msg, data


def verify_queue(
    queue_file: Path,
    dossier_file: Path | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Verifies that all `remaining_signatures` in `queue_file` have linked bugs in `go/crash`."""
    if not queue_file.is_file():
        return False, f"Queue file not found: {queue_file}", {}

    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"Failed to read queue file {queue_file}: {e}", {}

    target_sigs: list[str] = [
        s.strip() for s in data.get("remaining_signatures", []) if s and s.strip()
    ]
    verified_links: dict[str, list[str]] = dict(data.get("verified_links", {}))

    if not target_sigs:
        violations = audit_linked_bug_titles(verified_links)
        if violations:
            return _format_violations_error(violations, data)
        return (
            True,
            "All crash clusters in batch are verified and linked in go/crash.",
            data,
        )

    live_annotations = search_crash_annotations(target_sigs)
    still_unassigned: list[str] = []

    for sig in target_sigs:
        bugs = live_annotations.get(sig, [])
        urls = [
            b.get("bug_url", "").strip() for b in bugs if b.get("bug_url", "").strip()
        ]
        if urls:
            verified_links[sig] = urls
        elif verified_links.get(sig):
            pass
        else:
            still_unassigned.append(sig)

    data["verified_links"] = verified_links
    data["remaining_signatures"] = still_unassigned
    queue_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    if still_unassigned:
        crashes_by_sig = {
            c.get("stable_signature"): c for c in data.get("remaining_crashes", [])
        }
        missing_lines = []
        for sig in still_unassigned:
            rep_id = (crashes_by_sig.get(sig) or {}).get("latest_report_id") or "N/A"
            missing_lines.append(f"  - {sig} (Latest ReportID: {rep_id})")
        msg = (
            f"{len(still_unassigned)} signature(s) still lack a linked Buganizer issue in go/crash:\n"
            + "\n".join(missing_lines)
        )
        return False, msg, data

    violations = audit_linked_bug_titles(verified_links)
    if violations:
        return _format_violations_error(violations, data)

    if dossier_file and dossier_file.is_file():
        summary_lines = [
            "",
            "---",
            "",
            "## Verified `go/crash` Bug Links",
            "",
        ]
        for sig, urls in sorted(verified_links.items()):
            summary_lines.append(f"- `{sig}` -> {', '.join(urls)}")
        summary_lines.append("")
        existing_md = dossier_file.read_text(encoding="utf-8")
        if "## Verified `go/crash` Bug Links" not in existing_md:
            dossier_file.write_text(
                existing_md + "\n".join(summary_lines), encoding="utf-8"
            )

    return (
        True,
        f"Verified {len(verified_links)} signature(s) linked in go/crash.",
        data,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that all crash signatures in the triage batch are linked in go/crash."
    )
    parser.add_argument("--queue-file", required=True)
    parser.add_argument("--dossier-file", default="")
    args = parser.parse_args()

    q_path = Path(args.queue_file).expanduser().resolve()
    d_path = (
        Path(args.dossier_file).expanduser().resolve() if args.dossier_file else None
    )
    ok, msg, _ = verify_queue(q_path, d_path)
    if ok:
        print(msg)
        return 0
    sys.stderr.write(f"{msg}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
