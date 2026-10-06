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

"""Pre-fetches untriaged qemu-next crashes, auto-links exact Buganizer matches, and builds a compact triage dossier."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent
_EMU_DEV_CLI_DIR = _WORKFLOW_DIR.parent.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from commands.crash.advisor import ensure_crashadvisor_imports
from commands.crash.link_bug import (
    is_safe_signature_issue_match,
    search_crash_annotations,
    update_crash_annotation,
)
from commands.crash.list_crashes import (
    DEFAULT_EXCLUDE_MODULES,
    DEFAULT_F1_SQL_BIN,
    DEFAULT_HOURS,
    DEFAULT_VERSION_REGEX,
    build_f1_crashes_query,
    execute_f1_query,
    merge_and_normalize_rows,
)
from commands.crash.utils import acquire_auth_token


def try_auto_link_existing_buganizer_matches(
    crashes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Checks Buganizer for exact signature matches and auto-links them in go/crash.

    Args:
        crashes: List of unassigned crash cluster dictionaries from `list_crashes`.

    Returns:
        Tuple of (remaining_untriaged_crashes, auto_linked_crashes).
    """
    if not crashes:
        return [], []

    client = None
    try:
        token = acquire_auth_token()
        crash_modules = ensure_crashadvisor_imports()
        buganizer_mod = crash_modules["buganizer"]
        client = buganizer_mod.BuganizerClient(token=token, verbose=False)
    except Exception:  # noqa: BLE001
        client = None

    if client is None:
        return list(crashes), []

    remaining: list[dict[str, Any]] = []
    auto_linked: list[dict[str, Any]] = []

    for cluster in crashes:
        sig = cluster.get("stable_signature", "").strip()
        if not sig:
            continue
        matched_bug_id = ""
        matched_title = ""
        matched_status = ""
        try:
            existing = client.search_issue_by_signature(sig)
            if is_safe_signature_issue_match(sig, existing):
                matched_bug_id = str(
                    existing.get("issueId") or existing.get("id") or ""
                ).strip()
                state = existing.get("issueState") or {}
                matched_title = state.get("title") or existing.get("title", "")
                matched_status = state.get("status") or existing.get(
                    "status", "ASSIGNED"
                )
        except Exception:  # noqa: BLE001
            matched_bug_id = ""

        if matched_bug_id:
            clean_bug_id = str(matched_bug_id).removeprefix("b/").strip()
            bug_url = f"https://b.corp.google.com/issues/{clean_bug_id if clean_bug_id else matched_bug_id}"
            ok, _ = update_crash_annotation(
                stable_signature=sig,
                bug_url=bug_url,
            )
            if ok:
                try:
                    raw_ver = cluster.get("versions")
                    ver_list = (
                        raw_ver
                        if isinstance(raw_ver, (list, tuple))
                        else ([raw_ver] if raw_ver is not None else [])
                    )
                    first_ver = (
                        str(ver_list[0]).strip()
                        if ver_list and ver_list[0] is not None
                        else None
                    )
                    if first_ver and hasattr(client, "clean_version_string"):
                        first_ver = client.clean_version_string(first_ver)
                    if hasattr(client, "ensure_crash_hotlists") and clean_bug_id.isdigit():
                        client.ensure_crash_hotlists(
                            int(clean_bug_id), version=first_ver
                        )
                except Exception:  # noqa: BLE001
                    pass

                enriched = dict(cluster)
                enriched["bug_ids"] = [clean_bug_id if clean_bug_id else matched_bug_id]
                enriched["bug_urls"] = [bug_url]
                enriched["bug_title"] = matched_title
                enriched["bug_status"] = matched_status
                auto_linked.append(enriched)
                continue

        remaining.append(cluster)

    return remaining, auto_linked


def render_dossier_markdown(
    hours: int,
    remaining: list[dict[str, Any]],
    auto_linked: list[dict[str, Any]],
    cache_dir: str,
) -> str:
    """Renders a compact, token-efficient Markdown dossier for the daily triage agent."""
    lines: list[str] = [
        f"# Daily `qemu-next` Crash Triage Dossier (Last {hours}h)",
        "",
        f"- **Untriaged Clusters Requiring Action:** {len(remaining)}",
        f"- **Auto-Linked Existing Buganizer Matches:** {len(auto_linked)}",
        "- **Hotlists Maintained:** `qemu-next-crashes` & `qemu-next-crashes-version-<version>`",
        "- **Deduplication Policy:** Do not comment on existing bugs unless novel info (new OS platform, distinct breadcrumbs, regression) is observed. Occurrences are tracked in `go/crash`.",
        "",
    ]

    if auto_linked:
        lines.append("## Auto-Linked Clusters (No Action Needed)")
        lines.append("")
        for item in auto_linked:
            sig = item["stable_signature"]
            bid = item["bug_ids"][0]
            occ = item.get("occurrences", 1)
            lines.append(
                f"- `{sig}` ({occ}x) -> Linked to **b/{bid}** (`{item.get('bug_status', 'ASSIGNED')}`: {item.get('bug_title', '')})"
            )
        lines.append("")

    if not remaining:
        lines.append("## Remaining Untriaged Clusters")
        lines.append("")
        lines.append(
            "🟢 **All `qemu-next` crash clusters in this window are triaged and linked in `go/crash`!**"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append("## Remaining Untriaged Clusters (Action Required)")
    lines.append("")

    for idx, item in enumerate(remaining, 1):
        sig = item["stable_signature"]
        rep_id = item.get("latest_report_id") or "<latest_report_id>"
        occ = item.get("occurrences", 1)
        versions = ", ".join(str(x) for x in (item.get("versions") or []) if x) or "N/A"
        build_ids = ", ".join(str(x) for x in (item.get("build_ids") or []) if x) or "N/A"
        os_list = [str(x) for x in (item.get("os") or []) if x]
        os_names = ", ".join(os_list) or "N/A"
        loc = item.get("crash_location") or "N/A"
        rca_path = os.path.join(cache_dir, f"rca-{rep_id}.md")

        lines.extend(
            [
                f"### {idx}. `{sig}` ({occ}x)",
                f"- **Latest Report ID:** `{rep_id}` (`https://crash.corp.google.com/{rep_id}`)",
                f"- **Versions / Build IDs:** `{versions}` (Build IDs: `{build_ids}`)",
                f"- **Host OS:** `{os_names}`",
                f"- **Top Emulator Crash Location:** `{loc}`",
            ]
        )

        custom_keys = item.get("custom_keys", {})
        if custom_keys:
            lines.append("- **Crash Annotations / Keys:**")
            for k, v in custom_keys.items():
                compact_v = v.replace("\n", " ")[:300]
                lines.append(f"  - `{k}`: `{compact_v}`")

        stack_frames = item.get("stack_frames", [])
        if stack_frames:
            lines.append("- **Crashing Thread Top Frames (from F1):**")
            lines.append("  ```text")
            for frame in stack_frames:
                lines.append(f"  {frame}")
            lines.append("  ```")

        primary_os = (os_list[0] or "") if os_list else "Unknown"
        if "Mac" in primary_os:
            os_tag = "macOS"
        elif "Windows" in primary_os:
            os_tag = "Windows"
        elif "Linux" in primary_os:
            os_tag = "Linux"
        else:
            os_tag = primary_os or "General"

        lines.extend(
            [
                "- **Target RCA Path:** `" + rca_path + "`",
                "- **One-Shot File & Link Command:**",
                "  ```bash",
                f'  emu-dev-cli crash file-bug {rep_id} --signature "{sig}" --title "[Crash][{os_tag}] <Subsystem>: <Symptom> ({sig})" --from-rca "{rca_path}"',
                "  ```",
                "  *(Replace `<Subsystem>: <Symptom>` with the real faulting component/function and failure symptom from your RCA, e.g. `Launcher: port binding failure in launcher.cc` - avoid generic `FailWithoutStackTrace`)*",
                "",
            ]
        )

    return "\n".join(lines)


def prepare_batch(
    hours: int,
    limit: int,
    queue_file: Path,
    dossier_file: Path,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
) -> dict[str, Any]:
    """Queries F1 + live annotations, auto-links exact matches, and writes queue & dossier files."""
    sql = build_f1_crashes_query(
        hours=hours,
        version_regex=DEFAULT_VERSION_REGEX,
        exclude_modules=DEFAULT_EXCLUDE_MODULES,
        unassigned_only=True,
        raw=False,
        limit=limit,
    )
    csv_rows = execute_f1_query(sql=sql, f1_sql_bin=f1_sql_bin)
    unique_sigs = list(
        {
            r.get("stable_signature", "").strip()
            for r in csv_rows
            if r.get("stable_signature", "").strip()
        }
    )
    live_annotations = search_crash_annotations(unique_sigs)
    unassigned_crashes = merge_and_normalize_rows(
        csv_rows=csv_rows,
        live_annotations=live_annotations,
        raw=False,
        unassigned_only=True,
    )

    remaining, auto_linked = try_auto_link_existing_buganizer_matches(
        unassigned_crashes
    )

    cache_dir = str(dossier_file.parent)
    queue_payload: dict[str, Any] = {
        "hours": hours,
        "limit": limit,
        "remaining_signatures": [c["stable_signature"] for c in remaining],
        "remaining_crashes": remaining,
        "auto_linked_crashes": auto_linked,
        "verified_links": {c["stable_signature"]: c["bug_urls"] for c in auto_linked},
    }

    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(queue_payload, indent=2), encoding="utf-8")

    dossier_md = render_dossier_markdown(
        hours=hours,
        remaining=remaining,
        auto_linked=auto_linked,
        cache_dir=cache_dir,
    )
    dossier_file.parent.mkdir(parents=True, exist_ok=True)
    dossier_file.write_text(dossier_md, encoding="utf-8")

    return queue_payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare daily qemu-next crash triage batch and dossier."
    )
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--queue-file", required=True)
    parser.add_argument("--dossier-file", required=True)
    args = parser.parse_args()

    try:
        payload = prepare_batch(
            hours=args.hours,
            limit=args.limit,
            queue_file=Path(args.queue_file).expanduser().resolve(),
            dossier_file=Path(args.dossier_file).expanduser().resolve(),
        )
    except Exception as e:
        sys.stderr.write(f"Failed to prepare triage batch: {e}\n")
        return 1

    rem_count = len(payload["remaining_crashes"])
    auto_count = len(payload["auto_linked_crashes"])
    print(
        f"{rem_count} untriaged cluster(s) queued in dossier ({auto_count} existing Buganizer match(es) auto-linked)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
