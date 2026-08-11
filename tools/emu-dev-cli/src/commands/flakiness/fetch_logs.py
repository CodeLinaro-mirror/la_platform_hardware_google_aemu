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

"""Subcommand handler for 'emu-dev-cli flakiness fetch-logs'."""

import logging
from lib.ath_api import fetch_invocation_artifacts
from lib.output import format_markdown_table, print_result

logger = logging.getLogger(__name__)


def handle_flakiness_fetch_logs(args):
    """Handles 'emu-dev-cli flakiness fetch-logs' subcommand.

    Downloads logcat outputs, host logs, and diagnostic traces from Android
    Build/ATH. Clearly identifies REMOTE vs LOCAL_STUB diagnostic artifacts.
    """
    logger.info("Executing flakiness fetch-logs command (invocation_id=%s, type=%s)", args.invocation_id, args.artifact_type)
    json_mode = getattr(args, "json", False)
    result = fetch_invocation_artifacts(
        invocation_id=args.invocation_id,
        artifact_type=args.artifact_type,
        out_dir=args.out_dir,
        token=getattr(args, "token", None),
    )


    headers = ["#", "Artifact File", "Source", "Local Path", "Size (bytes)"]
    rows = []
    for idx, f in enumerate(result.get("downloaded_files", []), 1):
        source_badge = (
            "🌐 `REMOTE`"
            if f.get("source") == "REMOTE"
            else "📁 `LOCAL_STUB` (Upstream 404)"
        )
        rows.append(
            [
                idx,
                f"`{f['file_name']}`",
                source_badge,
                f"`{f['file_path']}`",
                f"{f['size_bytes']:,}",
            ]
        )

    summary_headers = ["Property", "Value"]
    summary_rows = [
        ["Invocation ID", f"`{args.invocation_id}`"],
        ["Artifact Type", f"`{args.artifact_type}`"],
        ["Output Directory", f"`{result['out_dir']}`"],
        ["Files Processed", f"{result['total_files']}"],
    ]

    summary_table = format_markdown_table(summary_headers, summary_rows)
    files_table = (
        format_markdown_table(headers, rows)
        if rows
        else "\n*No artifacts processed.*"
    )

    data = {
        "summary": f"Processed {result['total_files']} diagnostic artifacts for invocation '{args.invocation_id}' into {result['out_dir']}",
        "invocation_id": args.invocation_id,
        "artifact_type": args.artifact_type,
        "out_dir": result["out_dir"],
        "downloaded_files": result["downloaded_files"],
        "total_files": result["total_files"],
        "markdown_table": f"{summary_table}\n\n### Diagnostic Artifacts\n\n{files_table}",
    }
    print_result(data, json_mode=json_mode)
