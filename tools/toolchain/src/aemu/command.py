# Copyright 2025 The Android Open Source Project
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
"""A module for reconstructing the command used to invoke a script."""
import os
import logging
import sys
import shlex
from typing import List, Optional, Tuple, Union
from pathlib import Path


class CommandLineReconstructor:
    """A class for reconstructing the command used to invoke a script."""

    def get_command_parts(self) -> List[str]:
        """Reconstructs the command used to invoke the script as a list of parts.

        The --update flag is filtered out to prevent infinite loops.

        Returns:
            A list of strings representing the command and its arguments.
        """
        args = [arg for arg in sys.argv[1:] if arg != "--update"]
        if "BAZEL_SELF_LABEL" in os.environ:
            command = ["bazel", "run", os.environ["BAZEL_SELF_LABEL"]]
            if args:
                command += ["--"] + args
            return command

        # Reconstruct python command
        # sys.argv is ['script.py', 'arg1', ...]
        # We want ['/path/to/python', 'script.py', 'arg1', ...]
        filtered_argv = [sys.argv[0]] + args
        return [sys.executable] + filtered_argv

    def get_command_string(self) -> str:
        """Reconstructs the command used to invoke the script as a properly quoted string."""
        return " ".join(shlex.quote(p) for p in self.get_command_parts())

    def get_cwd(self, fallback: Path = None) -> str:
        """Returns the current working directory.

        If running under bazel, this will be the invoking directory or workspace root.

        Returns:
            The path to the current working directory.
        """
        # BUILD_WORKING_DIRECTORY is set by Bazel to the directory where `bazel run` was invoked.
        # BUILD_WORKSPACE_DIRECTORY is set by Bazel to the root of the workspace.
        bazel_dir = os.environ.get("BUILD_WORKING_DIRECTORY") or os.environ.get(
            "BUILD_WORKSPACE_DIRECTORY"
        )
        if bazel_dir:
            return bazel_dir
        try:
            return os.getcwd()
        except FileNotFoundError as e:
            logging.warning(
                "Unable to find the current working directory %s, falling back to: %s",
                e,
                fallback,
            )
            return str(fallback)

    _DEFAULT_PATH_ATTRS = ("out", "release", "config", "aosp", "build", "shim")

    def resolve_path(
        self, path_value: Union[str, Path], fallback: Optional[Path] = None
    ) -> Path:
        """Resolves a path argument relative to the invoking working directory."""
        path = Path(path_value)
        if path.is_absolute():
            return path
        return (Path(self.get_cwd(fallback=fallback)) / path).absolute()

    def resolve_cli_paths(
        self, args, attrs: Tuple[str, ...] = _DEFAULT_PATH_ATTRS
    ) -> None:
        """Resolves relative CLI path arguments on an argparse.Namespace."""
        for attr in attrs:
            val = getattr(args, attr, None)
            if not val:
                continue
            resolved = self.resolve_path(val)
            setattr(args, attr, resolved if attr == "out" else str(resolved))
