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
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent
_EMU_DEV_CLI_DIR = _WORKFLOW_DIR.parent.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from commands.crash.link_bug import search_crash_annotations


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
            rep_id = crashes_by_sig.get(sig, {}).get("latest_report_id", "N/A")
            missing_lines.append(f"  - {sig} (Latest ReportID: {rep_id})")
        msg = (
            f"{len(still_unassigned)} signature(s) still lack a linked Buganizer issue in go/crash:\n"
            + "\n".join(missing_lines)
        )
        return False, msg, data

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
