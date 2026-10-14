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

"""Minimal helper utilities for fetching and installing prebuilts from go/ab."""

import filecmp
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import zipfile

from lib.android_build_client import AndroidBuildClient, FETCH_ARTIFACT_BIN
from lib.output import format_markdown_table
from lib.workspace import WorkspacePathResolver

SUPPORTED_TARGETS: Tuple[str, ...] = (
    "linux-x64",
    "mac-arm64",
    "win-x64",
)


def log_progress(message: str) -> None:
    """Prints a real-time progress line to stderr."""
    print(message, file=sys.stderr, flush=True)


def format_bytes(num_bytes: int) -> str:
    """Formats a byte count into a human-readable size string."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def resolve_targets_list(
    targets: Optional[Union[str, Sequence[str]]],
    supported_targets: Sequence[str] = SUPPORTED_TARGETS,
) -> List[str]:
    """Resolves a user/CLI `targets` argument into an ordered list of targets."""
    if not targets:
        return list(supported_targets)

    if isinstance(targets, str):
        raw_items = [t.strip().lower() for t in targets.split(",") if t.strip()]
    else:
        raw_items = []
        for item in targets:
            for part in str(item).split(","):
                if part.strip():
                    raw_items.append(part.strip().lower())

    if not raw_items or "all" in raw_items:
        return list(supported_targets)
    return raw_items


def resolve_dest_dir(
    rel_dir: str,
    custom_dest: Optional[str] = None,
    target: Optional[str] = None,
    is_multi_target: bool = False,
) -> Optional[Path]:
    """Resolves the destination prebuilts directory in the workspace."""
    if custom_dest:
        custom_root = Path(custom_dest).resolve()
        if is_multi_target and target:
            if (custom_root / rel_dir).is_dir():
                return (custom_root / rel_dir).resolve()
            return (custom_root / target).resolve()
        return custom_root

    search_roots = [Path.cwd()]
    build_wd = os.environ.get("BUILD_WORKING_DIRECTORY")
    if build_wd:
        search_roots.insert(0, Path(build_wd))

    for root in search_roots:
        cand = root / rel_dir
        if cand.is_dir():
            return cand.resolve()

    resolver = WorkspacePathResolver(
        default_workspace="",
        custom_roots=search_roots + [Path(__file__).resolve()],
    )
    return resolver.find_directory(rel_dir)


def _find_fetch_artifact_tool() -> Optional[str]:
    """Locates the fetch_artifact CLI binary if available."""
    if os.path.exists(FETCH_ARTIFACT_BIN):
        return FETCH_ARTIFACT_BIN
    return shutil.which("fetch_artifact")


def resolve_build_number(
    build_number: Optional[str],
    branch: Optional[str],
    ab_target: str,
    token: Optional[str] = None,
) -> str:
    """Returns `build_number` directly, or resolves the latest green build ID on `branch`."""
    if build_number and build_number != "latest":
        return str(build_number)
    if not branch:
        raise ValueError(
            f"A branch is required to resolve the latest build for target '{ab_target}'."
        )

    start_ts = time.monotonic()
    log_progress(
        f"  🔍 Resolving latest build on '{branch}' ({ab_target})..."
    )
    fetch_tool = _find_fetch_artifact_tool()
    if fetch_tool:
        with tempfile.TemporaryDirectory(prefix="emu_ab_bid_") as tmp:
            cmd = [
                fetch_tool,
                "--branch",
                branch,
                "--target",
                ab_target,
                "--latest",
                "BUILD_INFO",
                tmp,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            info_path = Path(tmp) / "BUILD_INFO"
            if res.returncode == 0 and info_path.is_file():
                data = json.loads(info_path.read_text(encoding="utf-8"))
                if data.get("bid"):
                    bid = str(data["bid"])
                    elapsed = time.monotonic() - start_ts
                    log_progress(f"  ✓ Resolved build: go/ab/{bid} ({elapsed:.1f}s)")
                    return bid

    client = AndroidBuildClient(token=token)
    bid = str(client.get_latest_build_id(branch, ab_target))
    elapsed = time.monotonic() - start_ts
    log_progress(f"  ✓ Resolved build: go/ab/{bid} ({elapsed:.1f}s)")
    return bid


def fetch_ab_artifact(
    build_number: str,
    ab_target: str,
    artifact: str,
    out_dir: Path,
    zip_entries: Optional[Sequence[str]] = None,
    token: Optional[str] = None,
) -> Optional[str]:
    """Fetches an artifact (or specific zip entries from it) from go/ab into `out_dir`.

    Returns None on success, or an error message string on failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fetch_tool = _find_fetch_artifact_tool()
    last_err = ""
    start_ts = time.monotonic()

    if zip_entries:
        log_progress(
            f"  ⬇️  Fetching {len(zip_entries)} file(s) from {artifact} "
            f"(go/ab/{build_number}, {ab_target})..."
        )
    else:
        log_progress(
            f"  ⬇️  Fetching {artifact} (go/ab/{build_number}, {ab_target})..."
        )

    if fetch_tool:
        cmd = [
            fetch_tool,
            "--bid",
            str(build_number),
            "--target",
            ab_target,
        ]
        if zip_entries:
            cmd.extend(f"--zip_entry={entry}" for entry in zip_entries)
        cmd.extend([artifact, str(out_dir)])

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            elapsed = time.monotonic() - start_ts
            log_progress(f"  ✓ Downloaded in {elapsed:.1f}s")
            return None
        last_err = res.stderr.strip() or res.stdout.strip()

    try:
        client = AndroidBuildClient(token=token)
        dl_path = client.download_artifact(
            str(build_number), ab_target, artifact, str(out_dir)
        )
        if zip_entries:
            log_progress(f"  📂 Extracting {len(zip_entries)} file(s) from {artifact}...")
            with zipfile.ZipFile(dl_path, "r") as zf:
                available = set(zf.namelist())
                for entry in zip_entries:
                    if entry in available:
                        zf.extract(entry, path=str(out_dir))
        elapsed = time.monotonic() - start_ts
        log_progress(f"  ✓ Downloaded in {elapsed:.1f}s")
        return None
    except Exception as e:
        err_msg = f"{e} (fetch_artifact error: {last_err})" if last_err else str(e)
        log_progress(f"  ❌ Failed to fetch {artifact}: {err_msg}")
        return err_msg


def install_prebuilt_file(
    src_file: Path,
    dst_file: Path,
    target: str,
    mode: int = 0o640,
    dry_run: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Normalizes JSON line endings, compares `src_file` to `dst_file`, and copies if changed."""
    if src_file.name.lower().endswith(".json"):
        try:
            raw = src_file.read_bytes()
            if b"\r\n" in raw:
                src_file.write_bytes(raw.replace(b"\r\n", b"\n"))
        except OSError:
            pass

    new_size = src_file.stat().st_size
    old_size = dst_file.stat().st_size if dst_file.is_file() else 0

    if not dst_file.is_file():
        status = "ADDED"
        changed = True
    elif filecmp.cmp(str(src_file), str(dst_file), shallow=False):
        status = "UNCHANGED"
        changed = False
    else:
        status = "UPDATED"
        changed = True

    if changed and not dry_run:
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        try:
            os.chmod(dst_file, mode)
        except OSError:
            pass

    display_status = f"{status} (dry-run)" if (changed and dry_run) else status
    if changed:
        log_progress(
            f"    • {dst_file.name}: {display_status} "
            f"({format_bytes(old_size)} → {format_bytes(new_size)})"
        )
    else:
        log_progress(
            f"    • {dst_file.name}: {display_status} ({format_bytes(old_size)})"
        )

    return changed, {
        "target": target,
        "file": dst_file.name,
        "status": display_status,
        "old_size": old_size,
        "new_size": new_size if changed else old_size,
    }


def build_update_report(
    prebuilt: str,
    build_number: str,
    targets: Sequence[str],
    file_records: List[Dict[str, Any]],
    updated_files: List[str],
    errors: Optional[Sequence[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Formats the markdown table and structured output dictionary for an update run."""
    action_name = f"update-prebuilt {prebuilt}"
    table_rows = [
        [
            r.get("target", "-"),
            r["file"],
            r["status"],
            f"{r['old_size']:,}",
            f"{r['new_size']:,}",
        ]
        for r in file_records
    ]
    md_table = format_markdown_table(
        ["Target", "File", "Status", "Old Size (B)", "New Size (B)"],
        table_rows,
    )
    target_label = (
        targets[0]
        if len(targets) == 1
        else ("all" if set(targets) == set(SUPPORTED_TARGETS) else ",".join(targets))
    )
    mode_suffix = " (dry-run)" if dry_run else ""

    if errors:
        return {
            "status": "error",
            "action": action_name,
            "prebuilt": prebuilt,
            "build_number": str(build_number),
            "target": target_label,
            "dry_run": dry_run,
            "summary": f"{prebuilt} prebuilt update failed with {len(errors)} error(s)",
            "errors": list(errors),
            "error_message": "; ".join(errors),
            "files": file_records,
            "markdown_table": md_table,
            "exit_code": 1,
        }

    return {
        "status": "success",
        "action": action_name,
        "prebuilt": prebuilt,
        "build_number": str(build_number),
        "target": target_label,
        "dry_run": dry_run,
        "summary": (
            f"Updated {len(updated_files)} {prebuilt} prebuilt file(s) across "
            f"{len(targets)} target(s) from go/ab/{build_number}{mode_suffix}"
        ),
        "updated_files": updated_files,
        "files": file_records,
        "markdown_table": md_table,
    }
