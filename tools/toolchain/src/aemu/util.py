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
import os


def safe_link(src: Path, dst: Path):
    """Safely links a file from src to dst. I.e. dst --> src.

    This function attempts to create a hard link. If the destination exists,
    it is removed. If hard linking fails (e.g. cross-device), it falls back
    to copying the file.

    Args:
        src: The source file path.
        dst: The destination file path.
    """
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError as e:
        logging.debug("Hard link failed (%s), falling back to copy.", e)
        shutil.copy2(src, dst)


def safe_link_tree(src: Path, dst: Path):
    """Recursively links a directory tree from src to dst. I.e. dst --> src

    This function mirrors the directory structure of src into dst. Files are
    linked using safe_link (ie. hard linked or copied).
    Symlinks to directories are ignored.

    This makes sure that the src and dst directories are independent copies.
    For example, removing dst (bazel sandbox) will not affect src
    (the package config include/lib output).

    Args:
        src: The source directory path.
        dst: The destination directory path.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        try:
            if not item.exists():
                logging.warning(
                    "Source item %s does not exist (broken symlink?), skipping.", item
                )
                continue

            if item.is_dir():
                if not item.is_symlink():
                    safe_link_tree(item, dst / item.name)
            else:
                safe_link(item, dst / item.name)
        except PermissionError:
            logging.warning("Permission denied accessing %s, skipping.", item)


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
            try:
                shutil.rmtree(out)
            except OSError as e:
                logging.warning(
                    "Failed to remove directory %s: %s, will overwrite files instead.",
                    out,
                    e,
                )
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
