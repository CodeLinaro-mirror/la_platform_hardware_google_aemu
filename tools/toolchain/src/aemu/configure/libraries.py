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
from typing import Dict, Set, Tuple

from aemu.toolchains.package_config_pc import PackageConfigPc


# Base class for shared properties
class Lib:
    def __init__(self, builder, target, version, shim):
        self.builder = builder
        self.version = version
        self.shim = shim
        self.target = target

    def get_library_config(
        self, builder, bazel_target: str, shim: Dict[str, str]
    ) -> Tuple[Set[Path], Path]:
        """Get the public includes and archive path exported by the given Bazel target.

        Args:
            bazel_target (str): The Bazel target to query.
            shim (Dict[str, str]): Additional shims we wish to apply

        Returns:
            Tuple[Set[Path], Path]: A tuple containing a set of include paths and the archive path.
        """
        if "archive" in shim:
            archive = Path(shim["archive"])
        else:
            if "archive_target" in shim:
                archive = builder.get_archive(shim["archive_target"])
            else:
                archive = builder.get_archive(bazel_target)

        if "includes" in shim:
            # Apply shims, vs. what bazel reports.
            libdir = str(archive.parent)
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

        return includes, archive

    def generate_pkg_config(self, dest, pkg_config_dir):
        """Generate a pkgconfig .pc file for the given Bazel target,
          registering it with a provided version.

        Apply specified shims if needed, including building the target,
        retrieving the archive,
        obtaining library includes, and creating the pkg-config discovery file.

        Args:
            target (str): The Bazel target to generate the pkg-config file for.
            version (str): The version to register in the pkg-config file.
            shim (Dict[str, str]): Shims to apply during the generation process.
        """
        # Build the specified Bazel target.
        builder = self.builder

        # Retrieve the information associated with the target.
        includes, archive = self.get_library_config(builder, self.target, self.shim)

        # Name is @module//:<name>, i.e. the thing after :
        pkglib_name = self.target[self.target.rfind(":") + 1 :]

        cfg = PackageConfigPc(
            name=pkglib_name,
            version=self.version,
            release_dir=dest / "release",
            archive=archive,
            includes=includes,
            shim=self.shim,
        )

        cfg.write(pkg_config_dir)
        if not cfg.is_static():
            cfg.binplace(dest)


# BazelLib class with additional Bazel-specific property
class BazelLib(Lib):
    def __init__(self, builder, target, version, shim):
        super().__init__(builder, target, version, shim)


# CMakeLib class inherits from Lib
class CMakeLib(Lib):
    def __init__(self, builder, target, version, shim):
        super().__init__(builder, target, version, shim)

    def generate_pkg_config(self, dest, pkg_config_dir):
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
        )

        cfg.write(pkg_config_dir)
        if not cfg.is_static():
            cfg.binplace(dest)

        pc_file = Path(output) / self.shim.get("pc", "")
        if pc_file.is_file() and pc_file.exists():
            shutil.copyfile(pc_file, pkg_config_dir / pc_file.name)
