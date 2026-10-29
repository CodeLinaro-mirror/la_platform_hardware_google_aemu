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

from typing import Any, Dict, Optional, Sequence, Union

from lib.prebuilt_updater import run_standard_prebuilt_update

PREBUILT_NAME = "android-device-hub"
ALIASES = ("device-hub",)
DESCRIPTION = "Update Android Device Hub prebuilt packages in prebuilts/"
DEFAULT_BRANCH = "studio-main"
SUPPORTED_TARGETS = ("linux-x64", "linux-arm64", "mac-arm64", "win-x64")

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
    "linux-arm64": {
        "ab_target": "studio-linux",
        "archive": "artifacts/android-cli-linux-arm64.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/android-device-hub/linux-arm64"
        ),
        "files": (
            (
                "device-hub_pack.zip",
                "device-hub_pack_linux_arm64.zip",
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
    return run_standard_prebuilt_update(
        prebuilt_name=PREBUILT_NAME,
        default_branch=DEFAULT_BRANCH,
        supported_targets=SUPPORTED_TARGETS,
        targets_config=TARGETS_CONFIG,
        build_number=build_number,
        targets=targets,
        allow_missing_leaf_dir=True,
        tmp_prefix="emu_adh_",
        **kwargs,
    )
