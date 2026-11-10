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

"""Command for 'emu-dev-cli flakiness fetch-logs'."""

import argparse
from collections.abc import Callable
import logging
from typing import Any

from core.command import Command, CommandResult
from core.context import ExecutionContext
from core.errors import UsageError
from lib.ath_api import fetch_invocation_artifacts
from lib.output import format_markdown_table
from . import options

logger = logging.getLogger(__name__)

ArtifactFetcher = Callable[..., dict[str, Any]]

ACTION = "flakiness fetch-logs"
ARTIFACT_TYPE_CHOICES = ("LOGCAT", "HOST_LOG", "PERFETTO", "THREAD_DUMP", "ALL")
DEFAULT_ARTIFACT_TYPE = "LOGCAT"
REMOTE_SOURCE = "REMOTE"
REMOTE_BADGE = "🌐 `REMOTE`"
LOCAL_STUB_BADGE = "📁 `LOCAL_STUB` (Upstream 404)"
FILE_HEADERS = ["#", "Artifact File", "Source", "Local Path", "Size (bytes)"]

FETCH_LOGS_DESCRIPTION = """
Download Invocation Diagnostic Logs (`emu-dev-cli flakiness fetch-logs`)

Downloads logcat files, host stdout/stderr logs, thread dumps, and Perfetto traces for any Android Test Hub invocation ID (e.g. I75500010177130681) into a local sandbox directory.

Sample Invocations:
  # Download logcat file for an invocation ID:
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type LOGCAT

  # Download host stdout/stderr logs into a custom output directory:
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type HOST_LOG --out-dir scratch/my_logs/

  # Download all available diagnostic artifacts (logcat, host logs, thread dump, trace):
  emu-dev-cli flakiness fetch-logs --invocation-id I75500010177130681 --artifact-type ALL
"""


class FlakinessFetchLogsCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Downloads diagnostic logs for an Android Test Hub invocation."""

    name = "fetch-logs"
    help_text = "Download logcat outputs, host logs, and diagnostic traces for a given invocation ID"
    description = FETCH_LOGS_DESCRIPTION
    formatter_class = argparse.RawDescriptionHelpFormatter

    def __init__(
        self, *, artifact_fetcher: ArtifactFetcher | None = None
    ) -> None:
        self._artifact_fetcher = artifact_fetcher

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
            default=DEFAULT_ARTIFACT_TYPE,
            choices=ARTIFACT_TYPE_CHOICES,
            help="Type of diagnostic artifact to download (default: LOGCAT)",
        )
        parser.add_argument(
            "--out-dir",
            "--out_dir",
            dest="out_dir",
            type=str,
            default=None,
            help=(
                "Output directory (defaults to user temporary directory "
                "/tmp/flakiness_<user>/<invocation_id>/)"
            ),
        )
        options.add_token_option(parser)

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        inv_id = str(getattr(args, "invocation_id", "") or "").strip()
        if not inv_id:
            raise UsageError(
                "Invocation ID (--invocation-id) cannot be empty for 'flakiness fetch-logs'.",
                remediation="Pass a non-empty invocation ID (e.g. --invocation-id I75500010177130681).",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        invocation_id = str(args.invocation_id).strip()
        artifact_type = str(getattr(args, "artifact_type", DEFAULT_ARTIFACT_TYPE))
        logger.info(
            "Executing flakiness fetch-logs (invocation_id=%s, type=%s)",
            invocation_id,
            artifact_type,
        )
        # Without --out-dir the fetcher uses the documented per-user sandbox.
        fetcher = self._artifact_fetcher or fetch_invocation_artifacts
        result = fetcher(
            invocation_id=invocation_id,
            artifact_type=artifact_type,
            out_dir=getattr(args, "out_dir", None) or None,
            token=getattr(args, "token", None),
        )
        downloaded = result.get("downloaded_files", [])
        rows = [
            [
                idx,
                f"`{f['file_name']}`",
                REMOTE_BADGE if f.get("source") == REMOTE_SOURCE else LOCAL_STUB_BADGE,
                f"`{f['file_path']}`",
                f"{f['size_bytes']:,}",
            ]
            for idx, f in enumerate(downloaded, 1)
        ]
        summary_table = format_markdown_table(
            ["Property", "Value"],
            [
                ["Invocation ID", f"`{invocation_id}`"],
                ["Artifact Type", f"`{artifact_type}`"],
                ["Output Directory", f"`{result['out_dir']}`"],
                ["Files Processed", f"{result['total_files']}"],
            ],
        )
        files_table = (
            format_markdown_table(FILE_HEADERS, rows)
            if rows
            else "\n*No artifacts processed.*"
        )
        full_md = f"{summary_table}\n\n### Diagnostic Artifacts\n\n{files_table}"
        summary = (
            f"Processed {result['total_files']} diagnostic artifacts for invocation "
            f"'{invocation_id}' into {result['out_dir']}"
        )
        data: dict[str, Any] = {
            "summary": summary,
            "invocation_id": invocation_id,
            "artifact_type": artifact_type,
            "out_dir": result["out_dir"],
            "downloaded_files": downloaded,
            "total_files": result["total_files"],
            "markdown_table": full_md,
        }
        return CommandResult(
            data=data, action=ACTION, summary=summary, markdown_table=full_md
        )
