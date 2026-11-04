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

"""Lavapipe Vulkan ICD prebuilt updater (`emu-dev-cli update-prebuilt lavapipe`)."""

from typing import Any, Dict, Optional, Sequence, Union

from lib.prebuilt_updater import run_standard_prebuilt_update

PREBUILT_NAME = "lavapipe"
DESCRIPTION = "Update Lavapipe Vulkan ICD and LLVM libraries in prebuilts/"
DEFAULT_BRANCH = "emu-main-next"
SUPPORTED_TARGETS = ("linux-x64", "mac-arm64", "win-x64")

TARGETS_CONFIG: Dict[str, Dict[str, Any]] = {
    "linux-x64": {
        "ab_target": "emulator_linux_x64",
        "archive": "sdk-repo-linux-emulator-{build_number}.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/vulkan/linux-x86_64/icds"
        ),
        "files": (
            ("emulator/lib64/vulkan/libvulkan_lvp.so", "libvulkan_lvp.so", 0o750),
            ("emulator/lib64/vulkan/libLLVM.so", "libLLVM.so", 0o750),
            ("emulator/lib64/vulkan/libxcb-aemu.so", "libxcb-aemu.so", 0o750),
            ("emulator/lib64/vulkan/lvp_icd.json", "lvp_icd.json", 0o640),
        ),
    },
    "mac-arm64": {
        "ab_target": "emulator_mac_aarch64",
        "archive": "sdk-repo-darwin_aarch64-emulator-{build_number}.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/vulkan/darwin-aarch64/icds"
        ),
        "files": (
            ("emulator/lib64/vulkan/libvulkan_lvp.dylib", "libvulkan_lvp.dylib", 0o750),
            ("emulator/lib64/vulkan/lvp_icd.json", "lvp_icd.json", 0o640),
        ),
    },
    "win-x64": {
        "ab_target": "emulator_windows_x64",
        "archive": "sdk-repo-windows-emulator-{build_number}.zip",
        "dest_rel_dir": (
            "prebuilts/android-emulator-build/common/vulkan/windows-x86_64/icds"
        ),
        "files": (
            ("emulator/lib64/vulkan/libvulkan_lvp.dll", "libvulkan_lvp.dll", 0o640),
            ("emulator/lib64/vulkan/lvp_icd.json", "lvp_icd.json", 0o640),
        ),
    },
}


def update_prebuilt(
    build_number: Optional[str],
    targets: Optional[Union[str, Sequence[str]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Updates Lavapipe prebuilt libraries for `build_number` across `targets`."""
    return run_standard_prebuilt_update(
        prebuilt_name=PREBUILT_NAME,
        default_branch=DEFAULT_BRANCH,
        supported_targets=SUPPORTED_TARGETS,
        targets_config=TARGETS_CONFIG,
        build_number=build_number,
        targets=targets,
        tmp_prefix="emu_lvp_",
        **kwargs,
    )
