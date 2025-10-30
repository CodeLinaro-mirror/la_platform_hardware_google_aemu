# -*- coding: utf-8 -*-
# Copyright 2025 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Functions for creating a bazel shim configuration."""
import json
import platform
from pathlib import Path


def _read_jsonc(path: Path) -> dict:
    """Read a JSONC file, stripping comments.

    Args:
        path: The path to the JSONC file.

    Returns:
        The parsed JSON object.
    """
    jsonc = path.read_text()
    noc = "\n".join(l for l in jsonc.split("\n") if not l.lstrip(" ").startswith("//"))
    return json.loads(noc)


def create_shim(aosp_path: Path, build_path: Path) -> Path:
    """Create a shim.jsonc file for the Bazel backend.

    The shim file is used to configure the Bazel build, by providing information
    about dependencies, and other build parameters.

    Args:
        aosp_path: The path to the AOSP source tree.
        build_path: The path to the build directory.

    Returns:
        The path to the generated shim.jsonc file.
    """
    toolchain_path = aosp_path / "third_party" / "qemu" / "google" / "toolchain"
    common = _read_jsonc(toolchain_path / "shim-common.jsonc")
    plat = _read_jsonc(toolchain_path / f"shim-{platform.system().lower()}.jsonc")
    out = {}
    out["bazel_prefix"] = common.get("bazel_prefix", "") + plat.get("bazel_prefix", "")
    out["shims"] = common.get("shims", []) + plat.get("shims", [])
    # platform external_deps entries can override common ones.
    out["external_deps"] = common.get("external_deps", {}) | plat.get(
        "external_deps", {}
    )
    out["export"] = common.get("export", []) + plat.get("export", [])
    out["exclude"] = common.get("exclude", []) + plat.get("exclude", [])

    out_path = build_path / "shim.jsonc"
    out_path.write_text(json.dumps(out))
    return out_path
