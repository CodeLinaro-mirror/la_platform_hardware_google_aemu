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
import json
import logging
import platform
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set, Optional
from aemu.process.runner import check_output, run


class Bazel:
    """A class for interacting with the Bazel build system."""

    PLATFORM_MAP = {
        "windows-x64": "@goldfish_build//platforms:windows_x64",
        "linux-x64": "@goldfish_build//platforms:linux_x64",
        "linux-aarch64": "@goldfish_build//platforms:linux_aarch64",
        "mac-aarch64": "@goldfish_build//platforms:macos_aarch64",
        "mac-x64": "@goldfish_build//platforms:macos_x64",
    }

    def __init__(
        self,
        aosp: Path,
        dist: Path,
        startup_options: List[str] = [],
        build_options: List[str] = [],
        target: Optional[str] = None,
    ) -> None:
        """Initializes a Bazel object.

        Args:
            aosp: The path to the AOSP source tree.
            dist: The distribution directory.
            startup_options: A list of Bazel startup options.
            build_options: A list of Bazel build options.
            target: The target platform.
        """
        self.aosp = aosp.absolute()
        self.log_dir = dist.absolute() / "bazel-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.startup_options = startup_options
        self.build_options = build_options
        self.bazel_dir = aosp / "prebuilts" / "bazel"
        self.exe = self.bazel_dir / f"{self.host()}-x86_64" / "bazel"
        self.info = self._load_bazel_info()
        self.platform = self._set_platform(target) if target else None

        logging.info("Using bazel config: %s", self.info)
        assert "output_base" in self.info
        assert "workspace" in self.info
        assert "output_path" in self.info

    def _set_platform(self, target: str) -> str:
        """Sets the target platform for the build.

        Args:
            target: The target platform.

        Returns:
            The Bazel platform string.
        """
        platform = self.PLATFORM_MAP[target]
        try:
            run([self.exe, "query", platform], cwd=self.aosp)
            return platform
        except:
            logging.error(
                "The target platform '%s' for target '%s' is not available. "
                "This commonly occurs when the AEMU Bazel configuration, which provides the "
                "'goldfish_build' module, is not available. Please ensure you are using the "
                'correct `bazel_dep(name = "goldfish_build", version = "0.0.2")` file for your build.',
                platform,
                target,
            )
            raise

    def host(self) -> str:
        """Returns the host operating system."""
        return platform.system().lower()

    @lru_cache(maxsize=None)
    def build_exe(
        self,
        bazel_target: str,
        for_host: bool = True,
    ) -> Path:
        """Builds a Bazel target that produces an executable.

        Args:
            bazel_target: The Bazel target to build.
            for_host: Whether to build for the host.

        Returns:
            The path to the built executable.
        """
        self.build_target(bazel_target, for_host=for_host)

        build_options = self.build_options + [
            "--verbose_explanations",
        ]

        # Only add target platform if we are not building for host.
        # Otherwise cquery returns paths for target platform which may not exist.
        if not for_host and self.platform:
            build_options += [
                f"--platforms={self.platform}",
            ]

        files = check_output(
            [self.exe]
            + self.startup_options
            + ["cquery"]
            + build_options
            + [bazel_target, "--output=files"],
            cwd=self.aosp,
        )

        dest = self.aosp / files.splitlines()[0].strip()
        return dest.absolute()

    @lru_cache(maxsize=None)
    def build_target(
        self,
        bazel_target: str,
        build_for_includes: bool = False,
        for_host: bool = False,
    ) -> List[str]:
        """Builds the specified Bazel target.

        Args:
            bazel_target: The Bazel target to build.
            build_for_includes: Whether to build for includes.
            for_host: Whether to build for the host.

        Returns:
            A list of the built targets.
        """
        if ":" in bazel_target:
            label = bazel_target[bazel_target.rfind(":") + 1 :]
        else:
            label = bazel_target[bazel_target.rfind("/") + 1 :]
        bazel_explain_file = (self.log_dir / f"explain-{label}.txt").absolute()

        build_options = self.build_options + [
            f"--explain={bazel_explain_file}",
            "--verbose_explanations",
        ]

        if not for_host and self.platform:
            build_options += [
                f"--platforms={self.platform}",
            ]

        if build_for_includes:
            build_options.append("--output_groups=compilation_prerequisites_INTERNAL_")

        output = run(
            [self.exe]
            + self.startup_options
            + ["build"]
            + build_options
            + [bazel_target],
            cwd=self.aosp,
        )
        return self.get_build_artifacts(bazel_target, for_host)

    def get_build_artifacts(
        self,
        bazel_target: str,
        for_host: bool = False,
    ) -> List[str]:

        # Now we query the artifacts using cquery
        cquery_options = self.build_options[:]
        if not for_host and self.platform:
            cquery_options += [
                f"--platforms={self.platform}",
            ]

        try:
            files = check_output(
                [self.exe]
                + self.startup_options
                + ["cquery"]
                + cquery_options
                + ["--output=files", bazel_target],
                cwd=self.aosp,
            )
            return [x.strip() for x in files.splitlines() if x.strip()]
        except:
            logging.warning("Failed to determine output files for %s", bazel_target)
            return []

    def _replace_labels(self, package_info_result: str) -> str:
        """Replaces labels from the package info script."""
        # Concatenate includes and shim, then replace labels with actual values.
        return (
            package_info_result.replace("${output_base}", self.info["output_base"])
            .replace("${bazel_out}", self.info["output_path"])
            .replace("${workspace}", self.info["workspace"])
        )

    def _replace_targets(self, target: str) -> str:
        return target.replace("bazel-out", self.info["output_path"]).replace(
            "bazel-bin", self.info["bazel-bin"]
        )

    @lru_cache(maxsize=None)
    def get_introspection_file(self) -> Path:
        """
        Locates the path to the Bazel introspection cquery file (`introspection.cquery.bzl`).

        The location is determined based on the execution environment:
        1. **Inside Bazel (Runfiles):** It attempts to use the `Runfiles` library
           to find the file using its Bazel runfile path (`aemu+/tools/toolchain/src/aemu/process/introspection.cquery.bzl`).
        2. **Outside Bazel (Standalone/Scripting):** If the `Runfiles` import
           or path resolution fails, it assumes the file is located alongside
           the current Python script.

        Returns:
            Path: An absolute path object pointing to the 'introspection.cquery.bzl' file.
        """
        try:
            from python.runfiles import Runfiles

            r = Runfiles.Create()
            return Path(
                r.Rlocation(
                    "aemu+/tools/toolchain/src/aemu/process/introspection.cquery.bzl"
                )
            )
        except:
            # Likely outside of bazel, the introspection file is next to us.
            return Path(__file__).parent.absolute() / "introspection.cquery.bzl"

    def aquery(
        self,
        query: str,
        aquery_options: List[str] = [],
    ) -> str:
        """Runs a Bazel aquery.

        Args:
            query: The query string.
            aquery_options: Additional aquery options.

        Returns:
            The output of the aquery.
        """
        options = aquery_options
        if self.platform and not any(opt.startswith("--platforms") for opt in options):
            options.append(f"--platforms={self.platform}")

        return check_output(
            [self.exe] + self.startup_options + ["aquery"] + options + [query],
            cwd=self.aosp,
        )

    def cquery(
        self,
        query: str,
        cquery_options: List[str] = [],
    ) -> str:
        """Runs a Bazel cquery.

        Args:
            query: The query string.
            cquery_options: Additional cquery options.

        Returns:
            The output of the cquery.
        """
        options = cquery_options
        if self.platform and not any(opt.startswith("--platforms") for opt in options):
            options.append(f"--platforms={self.platform}")

        return check_output(
            [self.exe] + self.startup_options + ["cquery"] + options + [query],
            cwd=self.aosp,
        )

    def query(
        self,
        query: str,
        query_options: List[str] = [],
    ) -> str:
        """Runs a Bazel query.

        Args:
            query: The query string.
            query_options: Additional query options.

        Returns:
            The output of the query.
        """
        return check_output(
            [self.exe] + self.startup_options + ["query"] + query_options + [query],
            cwd=self.aosp,
        )

    @lru_cache(maxsize=None)
    def package_info(self, bazel_target: str) -> Dict[str, List[str]]:
        """Retrieves information about the Bazel target with paths normalized.

        Args:
            bazel_target: The Bazel target to query.

        Returns:
            A dictionary containing information about the Bazel target.
        """
        # Load information about Bazel target using 'cquery' and retrieve
        # A json string that looks like:
        # {
        #    "archive": "path/to/archive",
        #    "includes": "path1;path2;...",
        #    "defines": "key1;key2=val;...",
        # }
        #

        # First make sure that the virtual includes are actually generated.
        self.build_target(bazel_target, build_for_includes=True)

        query_script = self.get_introspection_file()

        build_options = self.build_options + [
            f"--starlark:file={query_script}",
            "--output=starlark",
            f"--platforms={self.platform}",
        ]

        starlark = check_output(
            [self.exe]
            + self.startup_options
            + ["cquery"]
            + build_options
            + [f'"{bazel_target}"'],
            cwd=self.aosp,
        )
        normalized = self._replace_labels(starlark)
        info = json.loads(normalized)
        for k, v in info.items():
            if isinstance(v, str):
                info[k] = list(set([x for x in v.split(";") if x]))

        logging.info("Target %s config info: %s", bazel_target, info)
        return info

    def get_archive_location(self, bazel_target: str) -> Path:
        """Gets the path of the compiled archive (.lib/.a) generated by this target, this does not build the target."""
        archives = [self._replace_targets(x) for x in self.get_build_artifacts(bazel_target)]
        return Path(next(iter(archives), ""))

    def get_archive(self, bazel_target: str) -> Path:
        """Builds the archive and gets the path of the compiled archive (.lib/.a) generated by this target."""
        archives = [self._replace_targets(x) for x in self.build_target(bazel_target)]
        return Path(next(iter(archives), ""))

    def get_includes(self, bazel_target: str) -> Set[Path]:
        return set(
            [Path(x) for x in self.package_info(bazel_target).get("includes", [])]
        )

    def _load_bazel_info(self) -> Dict[str, str]:
        """Retrieves the bazel configuration."""

        # Bazel info gives:
        # key: value
        #
        # for example:
        #
        # command_log: /private/var/tmp/_bazel_me/66e1d3546ce0030819ddb695de13f5d3/command.log
        # committed-heap-size: 209MB
        # execution_root: /private/var/tmp/_bazel_me/66e1d3546ce0030819ddb695de13f5d3/execroot/_main
        # gc-count: 121
        info = check_output(
            cmd=[self.exe] + self.startup_options + ["info"] + self.build_options,
            cwd=self.aosp,
        ).splitlines()
        return dict(line.strip().split(": ") for line in info)
