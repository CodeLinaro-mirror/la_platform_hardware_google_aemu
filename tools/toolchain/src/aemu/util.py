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
"""A collection of utility functions."""
import logging
import shutil
from pathlib import Path


def mkdirs(out: Path, force: bool):
    """Create a directory, removing the existing one if requested.

    Args:
        out: The directory to create.
        force: True if the directory should be removed if it exists.

    Raises:
        FileExistsError: If the directory exists and force is False.
    """
    if out.exists():
        if force:
            shutil.rmtree(out)
        else:
            logging.fatal(
                "The directory %s already exists, please delete it first or use the -f flag.",
                out,
            )
            raise FileExistsError(f"The directory {out} already exists")


def find_aosp_root(start_directory=Path(__file__).resolve()) -> str:
    """Find the root of the AOSP source tree.

    This is done by traversing up the directory tree until a .repo directory is found.

    Args:
        start_directory: The directory to start searching from.

    Returns:
        The path to the AOSP root, or the root of the filesystem if not found.
    """
    current_directory = Path(start_directory).resolve()

    while True:
        repo_directory = current_directory / ".repo"
        if repo_directory.is_dir():
            return str(current_directory)

        # Move up one directory
        parent_directory = current_directory.parent

        # Check if we've reached the root directory
        if current_directory == parent_directory:
            return str(current_directory)

        current_directory = parent_directory
