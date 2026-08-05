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

"""Filesystem security and ownership validation module for emu-dev-cli."""

import logging
import os
from pathlib import Path
import stat
from typing import Union

logger = logging.getLogger(__name__)


def is_path_secure_user_owned(
    path: Union[str, Path], is_dir: bool = False, check_parents: bool = True
) -> bool:
    """Verifies that a path exists, contains no unauthorized symlinks in its hierarchy,
    is owned by the current user, is not writable by group or others, and that parent
    directories are securely controlled.

    Args:
        path: Path to directory or file to check.
        is_dir: True if path is expected to be a directory, False for regular file.
        check_parents: Whether to recursively verify security of parent directories.

    Returns:
        True if path exists and meets ownership and permission requirements, False otherwise.
    """
    try:
        raw_p = Path(path).absolute()
        if not raw_p.exists():
            logger.debug("Security check failed: Path '%s' does not exist", raw_p)
            return False

        current_uid = os.getuid() if hasattr(os, "getuid") else None

        # Check target node itself - target must NEVER be a symlink
        if os.path.islink(str(raw_p)):
            logger.debug("Security check failed: Path '%s' is a symlink", raw_p)
            return False
        st = os.lstat(str(raw_p))
        if is_dir and not stat.S_ISDIR(st.st_mode):
            logger.debug("Security check failed: Path '%s' is not a directory", raw_p)
            return False
        if not is_dir and not stat.S_ISREG(st.st_mode):
            logger.debug("Security check failed: Path '%s' is not a regular file", raw_p)
            return False
        if current_uid is not None and st.st_uid != current_uid:
            logger.debug(
                "Security check failed: Path '%s' owner UID %s != current UID %s",
                raw_p,
                st.st_uid,
                current_uid,
            )
            return False
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            logger.debug(
                "Security check failed: Path '%s' is group or world writable (mode: %o)",
                raw_p,
                st.st_mode,
            )
            return False

        if check_parents:
            # Walk up the unresolved parent hierarchy
            curr = raw_p.parent
            while curr != curr.parent:
                pst = os.lstat(str(curr))

                # If parent component is a symlink:
                if os.path.islink(str(curr)):
                    # Allow ONLY root-owned system root links (e.g. /var -> private/var on macOS)
                    if pst.st_uid != 0 or curr.parent != curr.parent.parent:
                        return False

                # If directory is world-writable, it MUST have sticky bit set (e.g. /tmp, /var/tmp)
                if pst.st_mode & stat.S_IWOTH:
                    if not (pst.st_mode & stat.S_ISVTX):
                        return False
                elif current_uid is not None and pst.st_uid not in (0, current_uid):
                    # Parent must be owned by current user or root
                    return False

                curr = curr.parent

        return True
    except OSError:
        return False


def ensure_secure_permissions(path: Union[str, Path], mode: int = 0o700) -> None:
    """Sets safe permissions on a path (defaults to user-only read/write/execute 0o700).

    Args:
        path: Path to file or directory.
        mode: Permission bits mode (default: 0o700).
    """
    Path(path).chmod(mode)
