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

"""Unified workspace and path resolution module for emu-dev-cli."""

import logging
import os
from pathlib import Path
import shutil
import sys
from typing import List, Optional, Union

from commands.source_directory import (
    find_file_in_source_directories,
    get_all_source_directories,
    get_source_directory,
)
from lib.bazel import BazelRunner

logger = logging.getLogger(__name__)


class WorkspacePathResolver:
    """Unified locator for workspace repositories, components, and tools."""

    def __init__(
        self,
        default_workspace: str = "emu-main-next",
        custom_roots: Optional[List[Union[str, Path]]] = None,
    ):
        self.default_workspace = default_workspace
        self.custom_roots: List[Path] = (
            [Path(r) for r in custom_roots]
            if custom_roots is not None
            else [Path(__file__).resolve()]
        )

    def find_directory(self, rel_path: Union[str, Path]) -> Optional[Path]:
        """Resolves a directory path across registered workspaces and tree roots.

        Args:
            rel_path: Relative directory path string or Path object.

        Returns:
            Resolved Path if directory exists, None otherwise.
        """
        p = Path(rel_path)

        # 1. Check default workspace
        if self.default_workspace:
            src_dir = get_source_directory(self.default_workspace)
            if src_dir:
                cand = Path(src_dir) / p
                if cand.is_dir():
                    return cand

        # 2. Check all registered source directories
        for src_dir in get_all_source_directories():
            cand = Path(src_dir) / p
            if cand.is_dir():
                return cand

        # 3. Dynamic search relative to custom roots and ancestor trees
        for root in self.custom_roots:
            search_nodes = [root] + list(root.parents)
            for parent in search_nodes:
                cand = parent / p
                if cand.is_dir():
                    return cand

        return None

    def find_file(self, rel_path: Union[str, Path]) -> Optional[Path]:
        """Resolves a relative file path across registered workspaces and tree roots.

        Args:
            rel_path: Relative file path string or Path object.

        Returns:
            Resolved Path if file exists, None otherwise.
        """
        p = Path(rel_path)

        # 1. Check default workspace
        if self.default_workspace:
            src_dir = get_source_directory(self.default_workspace)
            if src_dir:
                cand = Path(src_dir) / p
                if cand.is_file():
                    return cand

        # 2. Check registry search helper
        found = find_file_in_source_directories(str(p))
        if found and Path(found).is_file():
            return Path(found)

        # 3. Check custom roots and ancestor trees
        for root in self.custom_roots:
            search_nodes = [root] + list(root.parents)
            for parent in search_nodes:
                cand = parent / p
                if cand.is_file():
                    return cand

        return None

    def find_tool_binary(
        self,
        binary_name: str,
        bazel_target: Optional[str] = None,
        bazel_bin_rel: Optional[str] = None,
    ) -> Optional[Path]:
        """Locates an executable tool binary across Bazel artifacts, emu-dev-cli lib/bin, and PATH.

        Uses dynamic Bazel cquery artifact resolution and globbing rather than brittle
        hardcoded Bzlmod repository paths.

        Args:
            binary_name: Name of the binary (e.g. 'advisor', 'crashreport').
            bazel_target: Optional Bazel target string (e.g. '@goldfish//emulator/crashreport/tool/advisor:advisor').
            bazel_bin_rel: Optional fallback relative path within bazel-bin.

        Returns:
            Resolved Path to executable binary if found, None otherwise.
        """
        is_windows = sys.platform.startswith("win")
        exe_name = f"{binary_name}.exe" if is_windows else binary_name

        # 1. Dynamically query Bazel target output artifact via BazelRunner cquery
        if bazel_target:
            src_dir = get_source_directory(self.default_workspace)
            candidate_roots = [Path(src_dir)] if src_dir else []
            candidate_roots.extend(self.custom_roots)

            for root in candidate_roots:
                if (root / "WORKSPACE").exists() or (root / "MODULE.bazel").exists():
                    try:
                        runner = BazelRunner(source_dir=root)
                        artifacts = runner.query_artifacts([bazel_target], cwd=root)
                        for art in artifacts:
                            if art.is_file() and (
                                is_windows or os.access(art, os.X_OK)
                            ):
                                return art
                    except Exception:
                        pass

        # 2. Check bazel_bin_rel if provided
        if bazel_bin_rel:
            src_dir = get_source_directory(self.default_workspace)
            if src_dir:
                cand = Path(src_dir) / bazel_bin_rel
                if cand.is_file() and (is_windows or os.access(cand, os.X_OK)):
                    return cand

        # 3. Dynamic search across bazel-bin directories without hardcoded Bzlmod mangling
        for root in self.custom_roots:
            for ancestor in [root] + list(root.parents):
                bazel_bin = ancestor / "bazel-bin"
                if bazel_bin.is_dir():
                    for match in bazel_bin.glob(f"**/{exe_name}"):
                        if match.is_file() and not match.name.endswith(
                            (".runfiles", ".params", ".manifest")
                        ):
                            if is_windows or os.access(match, os.X_OK):
                                return match

        # 4. Check pre-installed binary in ~/.android/emu-dev-cli/lib/bin/
        installed_bin = (
            Path.home() / ".android" / "emu-dev-cli" / "lib" / "bin" / exe_name
        )
        if installed_bin.is_file() and (
            is_windows or os.access(installed_bin, os.X_OK)
        ):
            return installed_bin

        # 5. Check system PATH
        on_path = shutil.which(binary_name)
        if on_path:
            return Path(on_path)

        return None


__all__ = ["WorkspacePathResolver"]
