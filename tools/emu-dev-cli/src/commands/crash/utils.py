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

"""Utility functions for crash investigation subcommands in emu-dev-cli.

Provides modular helpers for:
- Parsing crash IDs from raw strings and go/crash URLs
- Resolving auth tokens from flags, environment variables, or oauth2l SSO
- Extracting top faulting frames from crash dump text
- Resolving and validating secure user-owned sandbox directories
"""

import getpass
import os
from pathlib import Path
import tempfile
from typing import Optional, Tuple

from lib.oauth import OAuthTokenManager
from lib.security import is_path_secure_user_owned
from lib.stacktrace import StackTraceParser, extract_top_fault_frame


def create_secure_sandbox_dir(crash_id: str) -> str:
    """Creates a guaranteed atomic, user-exclusive (0o700) sandbox directory.

    Args:
        crash_id: The crash ID string (e.g. '05d8356e2f800000').

    Returns:
        Absolute path to the newly created secure directory.
    """
    return tempfile.mkdtemp(prefix=f"crashadvisor_{crash_id}_")


def get_crashadvisor_sandbox_dir(crash_id: str, create: bool = True) -> str:
    """Resolves or creates the absolute path to a secure CrashAdvisor sandbox directory.

    Args:
        crash_id: The crash ID string (e.g. '05d8356e2f800000').
        create: Whether to create a new secure sandbox directory if needed.

    Returns:
        Absolute path string to the sandbox directory.
    """
    if create:
        return create_secure_sandbox_dir(crash_id)

    temp_dir = Path(tempfile.gettempdir())
    matches = sorted(
        [
            p
            for p in temp_dir.glob(f"crashadvisor_{crash_id}_*")
            if is_path_secure_user_owned(p, is_dir=True)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return str(matches[0])

    return create_secure_sandbox_dir(crash_id)


def parse_crash_id(input_str: str) -> str:
    """Parses a crash ID from a raw ID string or go/crash URL.

    Args:
        input_str: Raw crash ID string or URL (e.g., 'https://crash.corp.google.com/05d8356e2f800000').

    Returns:
        Cleaned crash ID token.
    """
    cleaned = input_str.strip().rstrip("/")
    if "crash.corp.google.com/" in cleaned or "go/crash/" in cleaned:
        cleaned = cleaned.rstrip("/").split("/")[-1]
    return cleaned.strip()


def acquire_auth_token(user_token: Optional[str] = None) -> Optional[str]:
    """Returns explicit token, BUGANIZER_TOKEN env var, or attempts automated oauth2l fetch/refresh.

    Args:
        user_token: Optional token string explicitly supplied via CLI argument.

    Returns:
        Cleaned Bearer OAuth2 token string if available, None otherwise.
    """
    return OAuthTokenManager().acquire_token(user_token)


__all__ = [
    "OAuthTokenManager",
    "StackTraceParser",
    "acquire_auth_token",
    "create_secure_sandbox_dir",
    "extract_top_fault_frame",
    "get_crashadvisor_sandbox_dir",
    "is_path_secure_user_owned",
    "parse_crash_id",
]
