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

import argparse
import logging
from pathlib import Path
from typing import Any

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import UsageError
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


class FlakinessFetchLogsCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli flakiness fetch-logs`."""

    name = "fetch-logs"
    help_text = "Download logcat outputs, host logs, and diagnostic traces for a given invocation ID"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--invocation-id",
            "--invocation_id",
            dest="invocation_id",
            type=str,
            required=True,
            help="Invocation ID from Android Test Hub (e.g. I75500010177130681)",
        )
        parser.add_argument(
            "--artifact-type",
            "--artifact_type",
            dest="artifact_type",
            type=str,
            default="LOGCAT",
            choices=["LOGCAT", "HOST_LOG", "PERFETTO", "THREAD_DUMP", "ALL"],
            help="Type of diagnostic artifact to download (default: LOGCAT)",
        )
        parser.add_argument(
            "--out-dir",
            "--out_dir",
            dest="out_dir",
            type=str,
            default=None,
            help="Output directory",
        )
        parser.add_argument(
            "--token",
            type=str,
            default=None,
            help="Explicit OAuth2 token with androidbuild.internal scope",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        inv_id = str(getattr(args, "invocation_id", "") or "").strip()
        if not inv_id:
            raise UsageError(
                "Invocation ID (--invocation-id) cannot be empty for 'flakiness fetch-logs'.",
                remediation="Pass a non-empty invocation ID (e.g. --invocation-id I75500010177130681).",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        invocation_id = str(args.invocation_id).strip()
        artifact_type = str(getattr(args, "artifact_type", "LOGCAT"))
        out_dir = getattr(args, "out_dir", None)
        if not out_dir:
            out_dir = str(
                ctx.config.cache_dir / "flakiness_logs" / invocation_id
            )
            Path(out_dir).mkdir(parents=True, exist_ok=True)

        result = fetch_invocation_artifacts(
            invocation_id=invocation_id,
            artifact_type=artifact_type,
            out_dir=out_dir,
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
            ["Invocation ID", f"`{invocation_id}`"],
            ["Artifact Type", f"`{artifact_type}`"],
            ["Output Directory", f"`{result['out_dir']}`"],
            ["Files Processed", f"{result['total_files']}"],
        ]
        summary_table = format_markdown_table(summary_headers, summary_rows)
        files_table = (
            format_markdown_table(headers, rows)
            if rows
            else "\n*No artifacts processed.*"
        )
        full_md = f"{summary_table}\n\n### Diagnostic Artifacts\n\n{files_table}"
        summary_text = (
            f"Processed {result['total_files']} diagnostic artifacts for invocation "
            f"'{invocation_id}' into {result['out_dir']}"
        )
        data: dict[str, Any] = {
            "summary": summary_text,
            "invocation_id": invocation_id,
            "artifact_type": artifact_type,
            "out_dir": result["out_dir"],
            "downloaded_files": result["downloaded_files"],
            "total_files": result["total_files"],
            "markdown_table": full_md,
        }
        return CommandResult(
            data=data,
            action="flakiness fetch-logs",
            summary=summary_text,
            markdown_table=full_md,
        )
