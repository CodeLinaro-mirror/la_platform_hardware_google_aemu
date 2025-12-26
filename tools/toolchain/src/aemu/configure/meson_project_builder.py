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
import platform
from pathlib import Path
from aemu import jsonc

from aemu.process.bazel import Bazel
from aemu.configure.libraries import BazelLib, CMakeLib
from aemu.toolchains.toolchain_generator import ToolchainGenerator
from aemu.process.runner import run
from aemu.process.cmake import CMake
from aemu.log import run_meson_command


class MesonProjectBuilder:
    """Configures and builds a Meson-based project using a JSONC configuration file.

    This class orchestrates the entire build process by:
    1. Reading a project-specific configuration file (`.jsonc`).
    2. Setting up a toolchain suitable for the target platform.
    3. Generating `pkg-config` files for dependencies defined in the config.
    4. Generating any other necessary files (e.g., `config-host.mak`).
    5. Running `meson setup` with the appropriate options.

    The configuration file is expected to have two main sections:
    - "common": Contains settings (dependencies, meson_options, etc.) that
      apply to all platforms.
    - "platforms": Contains platform-specific configurations that can override
      or extend the common settings. The key for each platform should match
      the `target` passed to this builder (e.g., "linux-x64", "windows-x64").

    Merging Logic:
    The builder merges settings from "common" and the target-specific section
    under "platforms". For dictionaries (like "dependencies" and "meson_options"),
    the platform-specific values will overwrite the common values if there are
    conflicting keys. For lists (like "generated_files"), the platform-specific
    list is appended to the common list.

    Example of Overriding:
    Consider the following configuration structure:

    {
      "common": {
        "meson_options": {
          "-Dfeature_a": "enabled",
          "-Dfeature_b": "enabled"
        }
      },
      "platforms": {
        "linux-x64": {
          "meson_options": {
            "-Dfeature_b": "disabled" // This overrides the common setting
          }
        }
      }
    }

    When building for the "linux-x64" target, the final `meson_options` will be:
    {
      "-Dfeature_a": "enabled",
      "-Dfeature_b": "disabled"
    }
    The value for "feature_b" from the platform-specific configuration takes
    precedence.
    """

    TOOLCHAIN_DIR = "toolchain"

    def __init__(
        self,
        config_file,
        aosp,
        dest,
        toolchain_dir,
        ccache,
        generator,
        bazel_startup_options,
        bazel_build_options,
        target,
    ) -> None:
        """Initializes the MesonProjectBuilder and sets up the build environment.

        Args:
            config_file (str): Path to the JSONC configuration file for the project.
            aosp (str): Path to the AOSP root directory.
            dest (str): The destination directory for the build outputs.
            toolchain_dir (str): The directory to store the generated toolchain.
            ccache (str): Path to the ccache binary, if used.
            generator (ToolchainGenerator): The toolchain generator for the target platform.
            bazel_startup_options (list[str]): Startup options for Bazel.
            bazel_build_options (list[str]): Build options for Bazel.
            target (str): The canonical target platform (e.g., "linux-x64").
        """
        self.aosp = Path(aosp).absolute()
        self.dest = Path(dest).absolute()
        self.toolchain = Path(toolchain_dir).absolute()
        self.dest.mkdir(parents=True, exist_ok=True)
        self.target = target
        self.bazel = Bazel(
            self.aosp, self.dest, bazel_startup_options, bazel_build_options, target
        )
        self.cmake = CMake(self.aosp, self.toolchain, self.dest)

        self.toolchain_generator = generator
        if ccache:
            self.toolchain_generator.ccache = Path(ccache).absolute()
        self.toolchain_generator.bazel = self.bazel

        self._load_config(config_file)

    def _load_config(self, config_file):
        """Loads and parses the JSONC build configuration file.

        Args:
            config_file (str): The path to the .jsonc configuration file.
        """
        with open(config_file, "r") as f:
            self.config = jsonc.load(f)

    def _get_merged_config(self, key: str, default_value):
        """Merges a configuration section from "common" and platform-specific settings.

        This method retrieves a configuration section (e.g., "dependencies",
        "meson_options") from both the "common" block and the block corresponding
        to the current `self.target`.

        - If the configuration is a dictionary, the platform-specific keys
          overwrite the common keys.
        - If it's a list, the platform-specific list is appended to the common list.

        Args:
            key (str): The configuration key to merge (e.g., "dependencies").
            default_value: The default value to use if the key is not found.

        Returns:
            The merged configuration value (dict, list, or other type).
        """
        common_config = self.config.get("common", {}).get(key, default_value)
        platform_config = (
            self.config.get("platforms", {})
            .get(self.target, {})
            .get(key, default_value)
        )

        if isinstance(common_config, dict):
            return {**common_config, **platform_config}
        elif isinstance(common_config, list):
            return common_config + platform_config
        else:
            return platform_config

    def configure_meson(self, meson_flags):
        """Orchestrates the Meson build configuration process.

        This method performs the following steps:
        1. Generates the necessary toolchain wrappers.
        2. Creates `pkg-config` files for all defined dependencies.
        3. Generates any additional files specified in the configuration.
        4. Constructs and runs the `meson setup` command with the merged
           configuration and provided flags.

        Args:
            meson_flags (list[str]): A list of additional command-line flags
                                     to pass to `meson setup`.
        """
        packages = self.packages()
        binaries = self.binaries()
        self.toolchain_generator.gen_toolchain(packages, binaries)

        meson_flags = [] if meson_flags is None else meson_flags

        self.generate_files()

        prefix = (self.dest / "release").as_posix()
        source_path = self.aosp / self.config.get("source_path", "")
        meson_opts = self.meson_config()

        # Build the meson command line
        cmd = [self.toolchain / "meson", "setup", self.dest]
        cmd.append(f"--native-file={self.toolchain / 'aosp-cl.ini'}")
        cmd.extend([f"{k}={v}" if v else k for k, v in meson_opts.items()])
        cmd.append(f"-Dprefix={prefix}")
        cmd.extend(meson_flags)

        run_meson_command(cmd, self.dest, cwd=source_path, toolchain_path=self.toolchain)

    def packages(self):
        """Constructs a list of dependency library objects for the current target.

        This method merges the "dependencies" dictionaries from the "common" and
        platform-specific sections of the configuration. It then instantiates
        `BazelLib` or `CMakeLib` for each dependency, injecting the appropriate
        builder.

        Returns:
            list[Lib]: A list of initialized library objects.

        Raises:
            ValueError: If a dependency has an unknown `lib_type` or is missing
                        a `bazel_target` or `cmake_target`.
        """
        deps = self._get_merged_config("dependencies", {})
        platform_deps_list = []
        LIB_TYPES = {"bazel": (BazelLib, self.bazel), "cmake": (CMakeLib, self.cmake)}

        for name, config in deps.items():
            lib_type_str = config.get("lib_type", "bazel")
            lib_class, builder = LIB_TYPES.get(lib_type_str)

            if not lib_class:
                raise ValueError(
                    f"Unknown library type: {lib_type_str} for dependency {name}"
                )

            target = config.get("bazel_target") or config.get("cmake_target")
            if not target:
                raise ValueError(f"No target specified for dependency {name}")

            platform_deps_list.append(
                lib_class(builder, target, config["version"], config.get("shim", {}))
            )
        return platform_deps_list

    def binaries(self):
        """Constructs a dictionary of custom binaries for the current target."""
        return self._get_merged_config("binaries", {})

    def meson_config(self):
        """Constructs the final Meson options dictionary for the current target.

        Merges the "meson_options" from the "common" and platform-specific
        sections. Platform-specific options override common ones.

        Returns:
            dict: A dictionary of Meson options to be passed to `meson setup`.
        """
        return self._get_merged_config("meson_options", {})

    def generate_files(self):
        """Generates files based on the 'generated_files' section of the config.

        Merges the "generated_files" lists from "common" and platform-specific
        sections and writes the specified files to the destination directory.
        """
        files_to_generate = self._get_merged_config("generated_files", [])
        merged_file_defs = {}
        for file_def in files_to_generate:
            filename = file_def["filename"]
            if filename not in merged_file_defs:
                merged_file_defs[filename] = file_def
            else:
                # Deep merge the content.
                base_content = merged_file_defs[filename].setdefault("content", {})
                new_content = file_def.get("content", {})
                for key, value in new_content.items():
                    if (
                        key in base_content
                        and isinstance(base_content[key], dict)
                        and isinstance(value, dict)
                    ):
                        base_content[key].update(value)
                    else:
                        base_content[key] = value

        for file_def in merged_file_defs.values():
            content_def = file_def.get("content", {})
            key_value_pairs = self._get_platform_config_from_def(content_def)

            if key_value_pairs:
                lines = [f"{key}={value}" for key, value in key_value_pairs.items()]
                content = "\n".join(lines)

                output_path = self.dest / file_def["filename"]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w") as f:
                    f.write(content)

    def _get_platform_config_from_def(self, config_def):
        """Merges "common" and platform-specific dicts from a file definition.

        This is a helper for `generate_files` to construct the final content
        for a generated file.

        Args:
            config_def (dict): The "content" dictionary from a file definition
                               in the main configuration.

        Returns:
            dict: The merged dictionary of key-value pairs for the file.
        """
        common_config = config_def.get("common", {})
        platform_specific_config = config_def.get(self.target, {})
        return {**common_config, **platform_specific_config}
