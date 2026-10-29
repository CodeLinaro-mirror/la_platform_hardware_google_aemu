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

"""CLI command for uploading local recordings to screencast.googleplex.com."""

from __future__ import annotations

import argparse
import os

from lib.output import ensure_utf8_stdio, print_result, print_status
from lib.screencast import (
    get_platform_auth_hint,
    inspect_transports,
    upload_screencast,
    validate_video_file,
)


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Registers the `screencast` subcommand parser."""
    parser = subparsers.add_parser(
        "screencast",
        help="Upload a local video recording (.mp4, .webm, .mov) to screencast.googleplex.com",
        description=(
            "Uploads a local screen recording (.mp4, .webm, .mov) to "
            "screencast.googleplex.com and returns the shareable cast URL."
        ),
    )
    parser.add_argument(
        "video_file",
        help="Path to the local video file (.mp4, .webm, or .mov) to upload",
    )
    parser.add_argument(
        "-t",
        "--title",
        default="",
        help="Optional title for the Screencast (defaults to the video filename)",
    )
    parser.add_argument(
        "-d",
        "--description",
        default="",
        help="Optional description for the Screencast",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the video file and inspect local corporate auth transports without uploading",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output results in machine-readable JSON format",
    )
    parser.set_defaults(func=run_screencast_cmd)


def _emit_dry_run_result(
    *,
    abs_path: str,
    mime_type: str,
    size_bytes: int,
    resolved_title: str,
    description_arg: str,
    json_mode: bool,
) -> None:
    """Emits `--dry-run` validation and corporate transport diagnostics."""
    diag = inspect_transports()
    has_gosso = bool(diag.get("gosso_bin"))
    basename = os.path.basename(abs_path)
    summary = (
        f"Validated {basename} ({mime_type}, {size_bytes} bytes); "
        f"ready to upload via {', '.join(diag['transports'])}"
        if has_gosso
        else f"Validated {basename}, but `gosso` was not found. {get_platform_auth_hint()}"
    )
    print_result(
        {
            "status": "success" if has_gosso else "error",
            "action": "screencast",
            "dry_run": True,
            "summary": summary,
            "title": resolved_title,
            "description": description_arg,
            "source_file": abs_path,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "platform": diag["platform"],
            "gosso_bin": diag["gosso_bin"] or "missing",
            "available_transports": (
                diag["transports"] if json_mode else ", ".join(diag["transports"])
            ),
            "exit_code": 0 if has_gosso else 1,
        },
        json_mode=json_mode,
        is_error=not has_gosso,
    )


def run_screencast_cmd(args: argparse.Namespace) -> None:
    """Executes `emu-dev-cli screencast <video_file>`."""
    ensure_utf8_stdio()
    json_mode = bool(getattr(args, "json", False))
    video_arg = getattr(args, "video_file", "")
    title_arg = getattr(args, "title", "")
    description_arg = getattr(args, "description", "")
    dry_run = bool(getattr(args, "dry_run", False))

    try:
        abs_path, mime_type, size_bytes = validate_video_file(video_arg)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print_result(
            {
                "status": "error",
                "action": "screencast",
                "summary": str(exc),
                "source_file": video_arg,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    resolved_title = (title_arg or "").strip() or os.path.basename(abs_path)
    if dry_run:
        _emit_dry_run_result(
            abs_path=abs_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            resolved_title=resolved_title,
            description_arg=description_arg,
            json_mode=json_mode,
        )
        return

    print_status(
        f"🎬 Uploading {os.path.basename(abs_path)} to screencast.googleplex.com...",
        json_mode=json_mode,
    )
    try:
        result = upload_screencast(
            video_path=abs_path,
            title=resolved_title,
            description=description_arg,
        )
    except Exception as exc:
        print_result(
            {
                "status": "error",
                "action": "screencast",
                "summary": str(exc),
                "source_file": abs_path,
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        return

    print_result(
        {
            "status": "success",
            "action": "screencast",
            "summary": f"Uploaded {os.path.basename(result.source_file)} -> {result.url}",
            "url": result.url,
            "id": result.cast_id,
            "title": result.title,
            "source_file": result.source_file,
            "mime_type": result.mime_type,
            "size_bytes": result.size_bytes,
            "transport": result.transport,
        },
        json_mode=json_mode,
    )
