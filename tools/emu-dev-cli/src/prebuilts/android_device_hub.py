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

"""Android Device Hub prebuilt updater (`emu-dev-cli update-prebuilt android-device-hub`)."""

from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Union

from lib.prebuilt_updater import (
    build_update_report,
    fetch_ab_artifact,
    install_prebuilt_file,
    log_progress,
    resolve_build_number,
    resolve_dest_dir,
    resolve_targets_list,
)

PREBUILT_NAME = "android-device-hub"
ALIASES = ("device-hub",)
DESCRIPTION = "Update Android Device Hub prebuilt packages in prebuilts/"
DEFAULT_BRANCH = "studio-main"
SUPPORTED_TARGETS = ("linux-x64", "mac-arm64", "win-x64")

TARGETS_CONFIG: Dict[str, Dict[str, Any]] = {
    "linux-x64": {
        "ab_target": "studio-linux",
        "archive": "artifacts/android-cli.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/android-device-hub/linux-x64"
        ),
        "files": (
            (
                "device-hub_pack.zip",
                "device-hub_pack_linux_x64.zip",
                0o640,
            ),
        ),
    },
    "mac-arm64": {
        "ab_target": "studio-mac-arm",
        "archive": "android-cli.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/android-device-hub/darwin-arm64"
        ),
        "files": (
            (
                "device-hub_pack.zip",
                "device-hub_pack_darwin_arm64.zip",
                0o640,
            ),
        ),
    },
    "win-x64": {
        "ab_target": "studio-win",
        "archive": "android-cli.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/android-device-hub/win32-x64"
        ),
        "files": (
            (
                "device-hub_pack.zip",
                "device-hub_pack_win32_x64.zip",
                0o640,
            ),
        ),
    },
}


def update_prebuilt(
    build_number: Optional[str],
    targets: Optional[Union[str, Sequence[str]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Updates Android Device Hub prebuilt archives for `build_number` across `targets`."""
    dry_run = bool(kwargs.get("dry_run", False))
    custom_dest = kwargs.get("dest_dir")
    branch = kwargs.get("branch") or DEFAULT_BRANCH
    token = kwargs.get("token")

    resolved_targets = resolve_targets_list(targets, SUPPORTED_TARGETS)
    is_multi = len(resolved_targets) > 1
    total_targets = len(resolved_targets)

    file_records: List[Dict[str, Any]] = []
    updated_files: List[str] = []
    errors: List[str] = []
    resolved_bids: List[str] = []

    for idx, target in enumerate(resolved_targets, 1):
        cfg = TARGETS_CONFIG.get(target)
        if not cfg:
            errors.append(f"Unsupported target '{target}' for {PREBUILT_NAME}")
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
        if not dest_dir or (not custom_dest and not dest_dir.is_dir()):
            msg = f"Could not locate destination directory '{cfg['dest_rel_dir']}'"
            log_progress(f"  ❌ {msg}")
            errors.append(f"[{target}] {msg}")
            continue

        try:
            bid = resolve_build_number(
                build_number, branch, cfg["ab_target"], token=token
            )
        except Exception as e:
            log_progress(f"  ❌ {e}")
            errors.append(f"[{target}] {e}")
            continue

        if bid not in resolved_bids:
            resolved_bids.append(bid)

        archive = cfg["archive"].format(build_number=bid)
        zip_entries = [src_rel for src_rel, _, _ in cfg["files"]]

        with tempfile.TemporaryDirectory(prefix=f"emu_adh_{target}_") as tmp:
            tmp_dir = Path(tmp)
            err = fetch_ab_artifact(
                build_number=bid,
                ab_target=cfg["ab_target"],
                artifact=archive,
                out_dir=tmp_dir,
                zip_entries=zip_entries,
                token=token,
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
        prebuilt=PREBUILT_NAME,
        build_number=bid_display,
        targets=resolved_targets,
        file_records=file_records,
        updated_files=updated_files,
        errors=errors,
        dry_run=dry_run,
    )
