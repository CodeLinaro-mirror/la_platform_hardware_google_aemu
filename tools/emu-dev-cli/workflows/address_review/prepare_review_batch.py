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

"""Initializes the `address-review` workflow queue and Markdown dossier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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
    fetch_and_checkout_cl,
    parse_cl_identifier,
    render_comments_markdown,
    resolve_project_repo_path,
    upload_cl,
)


def prepare_batch(
    cl: str = "HEAD",
    all_open: bool = False,
    queue_file: Path | str = "",
    dossier_file: Path | str = "",
    repo_out_file: Path | str = "",
    workspace_root: Path | str = ".",
    checkout: bool = True,
    host: str = DEFAULT_GERRIT_HOST,
) -> int:
    """Queries Gerrit, prepares the review queue JSON and Markdown dossier, and optionally checks out the CL."""
    client = GerritClient(host=host)
    target_identifiers: list[str] = []

    if all_open:
        open_cls = client.list_open_changes(owner="self", unresolved_only=True)
        for chg in open_cls:
            target_identifiers.append(str(chg["number"]))
    else:
        ident = parse_cl_identifier(cl, cwd=workspace_root)
        target_identifiers.append(ident)

    cls_entries: list[dict[str, Any]] = []
    dossier_sections: list[str] = [
        "# Gerrit Code Review Dossier",
        "",
    ]
    primary_repo_str = str(Path(workspace_root).resolve())

    for idx, ident in enumerate(target_identifiers):
        try:
            change = client.get_change_detail(ident)
        except Exception as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                upload_cl(Path(workspace_root).resolve(), presubmit=True)
                change = client.get_change_detail(ident)
            else:
                raise
        cl_num = int(change.get("_number", 0) or (ident if ident.isdigit() else 0))
        project = str(change.get("project", ""))
        repo_path = resolve_project_repo_path(
            project, workspace_root=workspace_root
        )
        if idx == 0:
            primary_repo_str = str(repo_path)

        if checkout:
            try:
                fetch_and_checkout_cl(change, repo_path=repo_path, mode="auto")
            except Exception as exc:
                print(
                    f"Warning: automatic checkout for CL {cl_num} reported: {exc}",
                    file=sys.stderr,
                )

        threads = client.get_comment_threads(
            str(cl_num or ident), include_resolved=False
        )
        cur_rev = str(change.get("current_revision", ""))
        rev_info = (change.get("revisions") or {}).get(cur_rev) or {}
        patch_set = int(rev_info.get("_number", 1) or 1)

        serialized_threads: list[dict[str, Any]] = []
        for thread in threads:
            td = thread.to_dict()
            td["staged_reply"] = ""
            td["resolve"] = True
            td["published"] = False
            serialized_threads.append(td)

        cls_entries.append(
            {
                "cl_number": cl_num,
                "change_id": str(change.get("change_id", "")),
                "project": project,
                "branch": str(change.get("branch", "")),
                "subject": str(change.get("subject", "")),
                "current_revision": cur_rev,
                "patch_set": patch_set,
                "repo_path": str(repo_path),
                "unresolved_threads": serialized_threads,
            }
        )

        dossier_sections.append(
            render_comments_markdown(
                change,
                threads,
                repo_path=repo_path,
                queue_file=queue_file,
            )
        )

    queue_payload = {
        "mode": "all_open" if all_open else "single",
        "cls": cls_entries,
    }

    if queue_file:
        q_path = Path(queue_file)
        q_path.parent.mkdir(parents=True, exist_ok=True)
        q_path.write_text(json.dumps(queue_payload, indent=2), encoding="utf-8")

    if dossier_file:
        d_path = Path(dossier_file)
        d_path.parent.mkdir(parents=True, exist_ok=True)
        d_path.write_text("\n".join(dossier_sections), encoding="utf-8")

    if repo_out_file:
        r_path = Path(repo_out_file)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(primary_repo_str + "\n", encoding="utf-8")

    print(primary_repo_str)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare review queue and dossier for address-review workflow."
    )
    parser.add_argument("--cl", default="HEAD", help="Target CL identifier")
    parser.add_argument(
        "--all-open",
        default="false",
        help="Whether to batch-process all open CLs with unresolved comments",
    )
    parser.add_argument("--queue-file", required=True, help="Output queue JSON path")
    parser.add_argument(
        "--dossier-file", required=True, help="Output Markdown dossier path"
    )
    parser.add_argument(
        "--repo-out-file", default="", help="Optional path to write primary repo dir"
    )
    parser.add_argument(
        "--workspace-root", default=".", help="Workspace root directory"
    )
    parser.add_argument(
        "--checkout", default="true", help="Whether to fetch/checkout target CL"
    )
    args = parser.parse_args()

    all_open_flag = str(args.all_open).strip().lower() in ("1", "true", "yes")
    checkout_flag = str(args.checkout).strip().lower() not in ("0", "false", "no")

    try:
        return prepare_batch(
            cl=args.cl,
            all_open=all_open_flag,
            queue_file=args.queue_file,
            dossier_file=args.dossier_file,
            repo_out_file=args.repo_out_file,
            workspace_root=args.workspace_root,
            checkout=checkout_flag,
        )
    except Exception as exc:
        print(f"Error preparing review batch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
