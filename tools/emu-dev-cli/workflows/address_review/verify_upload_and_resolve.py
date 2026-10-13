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

"""Verifier for Step 2 of `address-review`: uploads patchset, resolves comments, and polls Gerrit for follow-up feedback."""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

_CUR_DIR = Path(__file__).resolve().parent
_TOOL_ROOT = _CUR_DIR.parent.parent
_SRC_DIR = _TOOL_ROOT / "src"
for _p in (str(_SRC_DIR), str(_TOOL_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.gerrit_api import (  # noqa: E402
    DEFAULT_GERRIT_HOST,
    GerritClient,
    GerritReplyInput,
    render_comments_markdown,
    upload_cl,
)


def _check_clean_working_tree(repo_path: Path) -> str | None:
    """Returns an error message if `repo_path` has uncommitted changes to tracked files."""
    if not (repo_path / ".git").exists():
        return None
    res = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_path),
    )
    if res.returncode == 0 and res.stdout.strip():
        return (
            f"Repository '{repo_path}' has uncommitted changes:\n"
            f"{res.stdout.strip()}\n"
            "Please amend all changes into the commit before uploading."
        )
    return None


def upload_resolve_and_poll(
    queue_file: Path | str,
    dossier_file: Path | str,
    skip_upload: bool = False,
    presubmit: bool = True,
    wait_timeout_sec: int = 120,
    poll_interval_sec: int = 15,
    wait_for_approval: bool = False,
    host: str = DEFAULT_GERRIT_HOST,
) -> int:
    """Uploads amended CL(s), posts staged replies, and polls Gerrit for new feedback.

    Returns:
      - `0` when all comments are resolved and no new unresolved comments arrived.
      - `1` when an error occurred (dirty working tree, upload failure, API failure).
      - `2` (`loop_exit_code`) when new unresolved comments arrived during polling,
        after refreshing `queue_file` and `dossier_file` for the next review round.
    """
    q_path = Path(queue_file)
    d_path = Path(dossier_file)
    if not q_path.is_file():
        print(f"Error: Queue file '{q_path}' not found.", file=sys.stderr)
        return 1

    q_data = json.loads(q_path.read_text(encoding="utf-8"))
    client = GerritClient(host=host)

    uploaded_repos: set[str] = set()
    for cl_entry in q_data.get("cls") or []:
        repo_path = Path(cl_entry.get("repo_path") or ".").resolve()
        dirty_err = _check_clean_working_tree(repo_path)
        if dirty_err:
            print(f"Error: {dirty_err}", file=sys.stderr)
            return 1

        if not skip_upload and str(repo_path) not in uploaded_repos:
            try:
                upload_cl(repo_path, presubmit=presubmit)
                uploaded_repos.add(str(repo_path))
            except Exception as exc:
                print(f"Error uploading CL in '{repo_path}': {exc}", file=sys.stderr)
                return 1

        cl_num = str(cl_entry.get("cl_number") or cl_entry.get("change_id"))
        replies: list[GerritReplyInput] = []
        for thread in cl_entry.get("unresolved_threads") or []:
            if thread.get("published"):
                continue
            comment_id = str(thread.get("leaf_id") or thread.get("root_id") or "")
            if not comment_id:
                continue
            msg = str(thread.get("staged_reply") or "").strip() or "Done."
            replies.append(
                GerritReplyInput(
                    comment_id=comment_id,
                    file_path=str(thread.get("file_path", "")),
                    line=int(thread.get("line") or 0),
                    patch_set=int(thread.get("patch_set") or 1),
                    message=msg,
                    resolve=bool(thread.get("resolve", True)),
                )
            )

        if replies:
            try:
                client.post_review_replies(cl_num, replies)
            except Exception as exc:
                print(
                    f"Error posting review replies to CL {cl_num}: {exc}",
                    file=sys.stderr,
                )
                return 1
            for thread in cl_entry.get("unresolved_threads") or []:
                thread["published"] = True

    q_path.write_text(json.dumps(q_data, indent=2), encoding="utf-8")

    if wait_timeout_sec <= 0:
        print("Patchset uploaded and review threads resolved on Gerrit.")
        return 0

    new_dossier_sections: list[str] = [
        "# Gerrit Code Review Dossier (Follow-Up Round)",
        "",
    ]
    any_new_comments = False

    for cl_entry in q_data.get("cls") or []:
        cl_num = str(cl_entry.get("cl_number") or cl_entry.get("change_id"))
        repo_path = Path(cl_entry.get("repo_path") or ".").resolve()
        known_ids: set[str] = set()
        for thread in cl_entry.get("unresolved_threads") or []:
            if thread.get("root_id"):
                known_ids.add(str(thread["root_id"]))
            if thread.get("leaf_id"):
                known_ids.add(str(thread["leaf_id"]))

        detail = client.get_change_detail(cl_num)
        cur_rev = str(detail.get("current_revision", ""))
        rev_info = (detail.get("revisions") or {}).get(cur_rev) or {}
        latest_ps = int(rev_info.get("_number", cl_entry.get("patch_set", 1)) or 1)

        outcome = client.wait_for_review_update(
            cl_num,
            baseline_patchset=latest_ps,
            known_comment_ids=known_ids,
            timeout=timedelta(seconds=wait_timeout_sec),
            poll_interval=timedelta(seconds=poll_interval_sec),
            wait_for_ai=not wait_for_approval,
            wait_for_approval=wait_for_approval,
        )

        unresolved_threads = outcome.get("unresolved_threads") or []
        if outcome["status"] == "new_comments" or unresolved_threads:
            any_new_comments = True
            latest_detail = outcome.get("detail") or detail
            refreshed_threads: list[dict[str, Any]] = []
            for ut in unresolved_threads:
                td = ut.to_dict()
                td["staged_reply"] = ""
                td["resolve"] = True
                td["published"] = False
                refreshed_threads.append(td)
            cl_entry["patch_set"] = latest_ps
            cl_entry["current_revision"] = cur_rev
            cl_entry["unresolved_threads"] = refreshed_threads
            new_dossier_sections.append(
                render_comments_markdown(
                    latest_detail,
                    unresolved_threads,
                    repo_path=repo_path,
                    queue_file=q_path,
                )
            )
        else:
            cl_entry["unresolved_threads"] = []

    q_path.write_text(json.dumps(q_data, indent=2), encoding="utf-8")

    if any_new_comments:
        d_path.write_text("\n".join(new_dossier_sections), encoding="utf-8")
        print(
            f"New unresolved review comments arrived on Gerrit! "
            f"Refreshed dossier at '{d_path}' and looping back to Step 0."
        )
        return 2

    print("All Gerrit review comments resolved and no new comments arrived.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload amended CL, resolve comments on Gerrit, and poll for new feedback."
    )
    parser.add_argument("--queue-file", required=True, help="Path to queue JSON file")
    parser.add_argument(
        "--dossier-file", required=True, help="Path to Markdown dossier file"
    )
    parser.add_argument(
        "--skip-upload",
        default="false",
        help="Whether to skip running repo upload (for dry-run/testing)",
    )
    parser.add_argument(
        "--presubmit",
        default="true",
        help="Whether to attach Presubmit-Ready+1 on upload",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=120,
        help="Seconds to poll Gerrit for new review comments after uploading (0 to disable)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=15,
        help="Seconds between Gerrit poll requests",
    )
    parser.add_argument(
        "--wait-for-approval",
        default="false",
        help="Continue polling until Code-Review+2 is granted",
    )
    args = parser.parse_args()

    skip_upload_flag = str(args.skip_upload).strip().lower() in ("1", "true", "yes")
    presubmit_flag = str(args.presubmit).strip().lower() not in ("0", "false", "no")
    approval_flag = str(args.wait_for_approval).strip().lower() in ("1", "true", "yes")

    return upload_resolve_and_poll(
        queue_file=args.queue_file,
        dossier_file=args.dossier_file,
        skip_upload=skip_upload_flag,
        presubmit=presubmit_flag,
        wait_timeout_sec=args.wait_timeout,
        poll_interval_sec=args.poll_interval,
        wait_for_approval=approval_flag,
    )


if __name__ == "__main__":
    sys.exit(main())
