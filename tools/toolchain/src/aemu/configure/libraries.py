# -*- coding: utf-8 -*-
# Copyright 2023 - The Android Open Source Project
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
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from aemu.toolchains.package_config_pc import PackageConfigPc


# Base class for shared properties
class Lib:
    """A base class for representing a library dependency."""

    def __init__(
        self,
        builder: Any,
        target: str,
        version: str,
        shim: Dict[str, str],
        features: List[str] = [],
        label_to_pkg_name: Dict[str, str] = {},
    ) -> None:
        """Initializes a Lib object.

        Args:
            builder: The build system to use for building the library.
            target: The build target for the library.
            version: The version of the library.
            shim: A dictionary of shims to apply.
            features: A list of enabled features.
            label_to_pkg_name: A mapping from Bazel labels to pkg-config names.
        """
        self.builder = builder
        self.version = version
        self.shim = shim
        self.target = target
        self.features = features
        self.label_to_pkg_name = label_to_pkg_name

    def get_library_config(
        self, builder: Any, bazel_target: str, shim: Dict[str, str]
    ) -> Tuple[List[Path], List[Path], List[str]]:
        """Get the public includes, archive paths and requirements exported by the given Bazel target.

        Args:
            builder: The builder object.
            bazel_target: The Bazel target to query.
            shim: Additional shims we wish to apply.

        Returns:
            A tuple containing a list of include paths, a list of archive paths, and a list of requirements.
        """
        archives = []
        if "archive" in shim:
            archives = [Path(shim["archive"])]
        else:
            if "archive_target" in shim:
                archives = [builder.get_archive(shim["archive_target"])]
            else:
                archives = [builder.get_archive(bazel_target)]

        if "extra_targets" in shim:
            for extra in shim["extra_targets"]:
                archives.append(builder.get_archive(extra))

        if "includes" in shim:
            # Apply shims, vs. what bazel reports.
            libdir = str(archives[0].parent) if archives else ""
            includes = list(
                set([Path(x.replace("${libdir}", libdir)) for x in shim["includes"]])
            )
        else:
            if "include_target" in shim:
                includes = builder.get_includes(shim["include_target"])
            else:
                includes = builder.get_includes(bazel_target)

        if "include_suffix" in shim:
            includes = [i / Path(shim["include_suffix"]) for i in includes]

        return includes, archives, []

    def get_workspace(self) -> Optional[Path]:
        """Returns the workspace root directory."""
        if hasattr(self.builder, "info") and "workspace" in self.builder.info:
            return Path(self.builder.info["workspace"])
        if hasattr(self.builder, "aosp"):
            return Path(self.builder.aosp)
        return None

    def generate_pkg_config(
        self, dest: Path, pkg_config_dir: Path, packages_dir: Path
    ) -> None:
        """Generate a pkgconfig .pc file for the given Bazel target.

        This method registers the library with a provided version and applies
        specified shims if needed. This includes building the target, retrieving
        the archive, obtaining library includes, and creating the pkg-config
        discovery file.

        Args:
            dest: The destination directory for the release.
            pkg_config_dir: The directory to write the .pc file to.
            packages_dir: The directory to persist the packages to.
        """
        # Build the specified Bazel target.
        builder = self.builder

        # Retrieve the information associated with the target.
        includes, archives, discovered_requires = self.get_library_config(
            builder, self.target, self.shim
        )

        # Name is @module//:<name>, i.e. the thing after :
        pkglib_name = self.target[self.target.rfind(":") + 1 :]

        # Merge discovered requirements with shimmed ones.
        shim = self.shim.copy()
        if discovered_requires:
            existing_reqs = shim.get("Requires", "")
            all_reqs = set(
                [r.strip() for r in existing_reqs.split(",") if r.strip()]
                + discovered_requires
            )
            shim["Requires"] = ", ".join(sorted(list(all_reqs)))

        cfg = PackageConfigPc(
            name=pkglib_name,
            version=self.version,
            release_dir=dest / "release",
            archives=archives,
            includes=includes,
            shim=shim,
            target=self.target,
        )

        if not cfg.is_static():
            cfg.binplace(dest)
        # Make sure the package artifacts are persisted, so bazel cleanup doesn't remove them.
        cfg.persist(packages_dir, self.get_workspace())
        cfg.write(pkg_config_dir)


class BazelLib(Lib):
    """A library dependency that is built with Bazel."""

    def get_library_config(
        self, builder: Any, bazel_target: str, shim: Dict[str, str]
    ) -> Tuple[List[Path], List[Path], List[str]]:
        """Get the public includes, archive paths and requirements exported by the given Bazel target.

        Args:
            builder: The builder object.
            bazel_target: The Bazel target to query.
            shim: Additional shims we wish to apply.

        Returns:
            A tuple containing a list of include paths, a list of archive paths, and a list of requirements.
        """
        if "transitive_dependencies" not in self.features:
            return super().get_library_config(builder, bazel_target, shim)

        # Retrieve detailed information about the target using introspection.
        info = builder.package_info(bazel_target)

        includes = [Path(i) for i in info.get("includes", [])]
        if "include_suffix" in shim:
            includes = [i / Path(shim["include_suffix"]) for i in includes]

        archives = []
        requires = []

        # The direct archive(s) of the target
        target_archives = [builder.get_archive(bazel_target)]
        if "extra_targets" in shim:
            for extra in shim["extra_targets"]:
                target_archives.append(builder.get_archive(extra))

        archives.extend([a for a in target_archives if a and a.name])

        # Transitive dependencies
        for dep in info.get("dependencies", []):
            if "|" not in dep:
                continue

            label, path = dep.split("|", 1)

            if label == bazel_target:
                continue

            if label in self.label_to_pkg_name:
                # This dependency is a top-level dependency in our config.
                # Add it to Requires instead of bundling it.
                requires.append(self.label_to_pkg_name[label])
            else:
                # This is an internal dependency, bundle it.
                # We use the path directly from the introspection info.
                da_path = Path(path)
                if da_path and da_path.name and da_path not in archives:
                    # Check if bazel created the dependency archive, if not build it.
                    if not da_path.is_file() or not da_path.exists():
                        builder.build_target(label)
                    archives.append(da_path)
        return includes, archives, sorted(list(set(requires)))


class CMakeLib(Lib):
    """A library dependency that is built with CMake."""

    def __init__(
        self,
        builder: Any,
        target: str,
        version: str,
        shim: Dict[str, str],
        features: List[str] = [],
        label_to_pkg_name: Dict[str, str] = {},
    ) -> None:
        """Initializes a CMakeLib object.

        Args:
            builder: The CMake build system to use.
            target: The CMake target for the library.
            version: The version of the library.
            shim: A dictionary of shims to apply.
            features: A list of enabled features.
            label_to_pkg_name: A mapping from Bazel labels to pkg-config names.
        """
        super().__init__(builder, target, version, shim, features, label_to_pkg_name)

    def generate_pkg_config(
        self, dest: Path, pkg_config_dir: Path, packages_dir: Path
    ) -> None:
        """Generate a pkgconfig .pc file for the given CMake target.

        Args:
            dest: The destination directory for the release.
            pkg_config_dir: The directory to write the .pc file to.
            packages_dir: The directory to persist the packages to.
        """
        builder = self.builder
        output = builder.build_target(self.target)
        pkglib_name = self.target[self.target.rfind(":") + 1 :]

        cfg = PackageConfigPc(
            name=pkglib_name,
            version=self.version,
            release_dir=dest / "release",
            archive=output
            / self.shim.get("archive", "unknown"),  # For now we will use shims.
            includes=None,
            shim=self.shim,
            target=self.target,
        )

        cfg.persist(packages_dir, self.get_workspace())
        cfg.write(pkg_config_dir)
        if not cfg.is_static():
            cfg.binplace(dest)

        pc_file = Path(output) / self.shim.get("pc", "")
        if pc_file.is_file() and pc_file.exists():
            shutil.copy2(pc_file, pkg_config_dir / pc_file.name)
