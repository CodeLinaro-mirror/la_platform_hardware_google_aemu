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
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
import zipfile

from clients.artifact_service import (
    DEFAULT_FETCH_ARTIFACT_BIN as FETCH_ARTIFACT_BIN,
    ArtifactService,
)
from core.process import ProcessRunner, SubprocessRunner
from lib.android_build_client import AndroidBuildClient
from lib.output import format_markdown_table
from lib.workspace import WorkspacePathResolver

SUPPORTED_TARGETS: Tuple[str, ...] = (
    "linux-x64",
    "mac-arm64",
    "win-x64",
)

TARGET_ALIASES: Dict[str, str] = {
    "linux-arm": "linux-arm64",
    "linux-aarch64": "linux-arm64",
    "mac-arm": "mac-arm64",
    "mac-aarch64": "mac-arm64",
}


def normalize_target_name(
    target: str,
    supported_targets: Optional[Sequence[str]] = None,
) -> str:
    """Normalizes a target alias (e.g. 'linux-arm' -> 'linux-arm64') when supported."""
    cleaned = target.strip().lower()
    if supported_targets is not None and cleaned in supported_targets:
        return cleaned
    mapped = TARGET_ALIASES.get(cleaned, cleaned)
    if supported_targets is None or mapped in supported_targets:
        return mapped
    return cleaned


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
    normalized = [
        normalize_target_name(item, supported_targets) for item in raw_items
    ]
    return list(dict.fromkeys(normalized))


def resolve_dest_dir(
    rel_dir: str,
    custom_dest: Optional[str] = None,
    target: Optional[str] = None,
    is_multi_target: bool = False,
) -> Optional[Path]:
    """Resolves the destination prebuilts directory in the workspace."""
    rel_path = Path(rel_dir)
    if custom_dest:
        custom_root = Path(custom_dest).resolve()
        if is_multi_target and target:
            if (custom_root / rel_path).is_dir():
                return (custom_root / rel_path).resolve()
            if (custom_root / rel_path.name).is_dir():
                return (custom_root / rel_path.name).resolve()
            return (custom_root / target).resolve()
        return custom_root

    search_roots = [Path.cwd()]
    build_wd = os.environ.get("BUILD_WORKING_DIRECTORY")
    if build_wd:
        search_roots.insert(0, Path(build_wd))

    for root in search_roots:
        cand = root / rel_path
        if cand.is_dir():
            return cand.resolve()

    resolver = WorkspacePathResolver(
        default_workspace="",
        custom_roots=search_roots + [Path(__file__).resolve()],
    )
    found = resolver.find_directory(rel_path)
    if found:
        return found

    if len(rel_path.parts) > 1:
        parent_rel = rel_path.parent
        for root in search_roots:
            parent_cand = root / parent_rel
            if parent_cand.is_dir():
                return (parent_cand / rel_path.name).resolve()
        found_parent = resolver.find_directory(parent_rel)
        if found_parent:
            return (found_parent / rel_path.name).resolve()

    return None


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
    *,
    artifact_service: Optional[ArtifactService] = None,
    runner: Optional[ProcessRunner] = None,
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
    proc_runner = runner or SubprocessRunner()
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
            res = proc_runner.run(cmd, check=False)
            info_path = Path(tmp) / "BUILD_INFO"
            if res.ok and info_path.is_file():
                data = json.loads(info_path.read_text(encoding="utf-8"))
                if data.get("bid"):
                    bid = str(data["bid"])
                    elapsed = time.monotonic() - start_ts
                    log_progress(f"  ✓ Resolved build: go/ab/{bid} ({elapsed:.1f}s)")
                    return bid

    client = AndroidBuildClient(
        token=token,
        service=artifact_service,
        process_runner=proc_runner,
    )
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
    *,
    artifact_service: Optional[ArtifactService] = None,
    runner: Optional[ProcessRunner] = None,
) -> Optional[str]:
    """Fetches an artifact (or specific zip entries from it) from go/ab into `out_dir`.

    Returns None on success, or an error message string on failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_runner = runner or SubprocessRunner()
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

        res = proc_runner.run(cmd, check=False)
        if res.ok:
            elapsed = time.monotonic() - start_ts
            log_progress(f"  ✓ Downloaded in {elapsed:.1f}s")
            return None
        last_err = res.stderr.strip() or res.stdout.strip()

    try:
        client = AndroidBuildClient(
            token=token,
            service=artifact_service,
            process_runner=proc_runner,
        )
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
        except OSError as exc:
            logging.getLogger(__name__).debug("Ignored error in install_prebuilt_file: %s", exc)

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
        except OSError as exc:
            logging.getLogger(__name__).debug("Ignored error in install_prebuilt_file: %s", exc)

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
    supported_targets: Sequence[str] = SUPPORTED_TARGETS,
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
        else (
            "all"
            if set(targets) == set(supported_targets)
            else ",".join(targets)
        )
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


def run_standard_prebuilt_update(
    *,
    prebuilt_name: str,
    default_branch: str,
    supported_targets: Sequence[str],
    targets_config: Mapping[str, Mapping[str, Any]],
    build_number: Optional[str],
    targets: Optional[Union[str, Sequence[str]]] = None,
    allow_missing_leaf_dir: bool = False,
    tmp_prefix: str = "emu_prebuilt_",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Shared execution pipeline for table-driven prebuilt updater modules."""
    dry_run = bool(kwargs.get("dry_run", False))
    custom_dest = kwargs.get("dest_dir")
    branch = kwargs.get("branch") or default_branch
    token = kwargs.get("token")
    runner = kwargs.get("runner")

    resolved_targets = resolve_targets_list(targets, supported_targets)
    is_multi = len(resolved_targets) > 1
    total_targets = len(resolved_targets)

    file_records: List[Dict[str, Any]] = []
    updated_files: List[str] = []
    errors: List[str] = []
    resolved_bids: List[str] = []
    bid_by_ab_target: Dict[str, str] = {}

    for idx, target in enumerate(resolved_targets, 1):
        cfg = targets_config.get(target)
        if not cfg:
            errors.append(f"Unsupported target '{target}' for {prebuilt_name}")
            continue

        log_progress(
            f"\n📦 [{idx}/{total_targets}] {target} → {cfg['dest_rel_dir']}"
        )
        dest_dir = resolve_dest_dir(
            cfg["dest_rel_dir"],
            custom_dest=custom_dest,
            target=target,
            is_multi_target=is_multi,
        )
        dir_ok = bool(dest_dir) and (
            bool(custom_dest)
            or dest_dir.is_dir()
            or (allow_missing_leaf_dir and dest_dir.parent.is_dir())
        )
        if not dest_dir or not dir_ok:
            msg = f"Could not locate destination directory '{cfg['dest_rel_dir']}'"
            log_progress(f"  ❌ {msg}")
            errors.append(f"[{target}] {msg}")
            continue

        ab_target = cfg["ab_target"]
        if ab_target in bid_by_ab_target:
            bid = bid_by_ab_target[ab_target]
        else:
            try:
                bid = resolve_build_number(
                    build_number,
                    branch,
                    ab_target,
                    token=token,
                    runner=runner,
                )
            except Exception as exc:  # noqa: BLE001
                log_progress(f"  ❌ {exc}")
                errors.append(f"[{target}] {exc}")
                continue
            bid_by_ab_target[ab_target] = bid

        if bid not in resolved_bids:
            resolved_bids.append(bid)

        archive = cfg["archive"].format(build_number=bid)
        zip_entries = [src_rel for src_rel, _, _ in cfg["files"]]

        with tempfile.TemporaryDirectory(prefix=f"{tmp_prefix}{target}_") as tmp:
            tmp_dir = Path(tmp)
            err = fetch_ab_artifact(
                build_number=bid,
                ab_target=ab_target,
                artifact=archive,
                out_dir=tmp_dir,
                zip_entries=zip_entries,
                token=token,
                runner=runner,
            )
            if err:
                errors.append(f"[{target}] {err}")
                continue

            for src_rel, dst_name, mode in cfg["files"]:
                src_file = tmp_dir / src_rel
                dst_file = dest_dir / dst_name
                if not src_file.is_file():
                    msg = f"Expected '{src_rel}' not found in {archive}"
                    log_progress(f"    ❌ {msg}")
                    errors.append(f"[{target}] {msg}")
                    continue

                changed, rec = install_prebuilt_file(
                    src_file=src_file,
                    dst_file=dst_file,
                    target=target,
                    mode=mode,
                    dry_run=dry_run,
                )
                file_records.append(rec)
                if changed:
                    updated_files.append(
                        f"{target}/{dst_name}" if is_multi else dst_name
                    )

    bid_display = (
        ", ".join(resolved_bids)
        if resolved_bids
        else str(build_number or "latest")
    )
    return build_update_report(
        prebuilt=prebuilt_name,
        build_number=bid_display,
        targets=resolved_targets,
        file_records=file_records,
        updated_files=updated_files,
        errors=errors,
        dry_run=dry_run,
        supported_targets=supported_targets,
    )
