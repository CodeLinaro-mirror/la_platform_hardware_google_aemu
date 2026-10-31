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

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from core.errors import ExecutionError
from lib.oauth import OAuthTokenManager
from lib.security import is_path_secure_user_owned
from lib.stacktrace import StackTraceParser, extract_top_fault_frame

logger = logging.getLogger(__name__)

# Resolves the OAuth2 token from the --token value (None when the flag is absent).
TokenProvider = Callable[[Optional[str]], Optional[str]]

CRASHADVISOR_CACHE_DIR_NAME = "crashadvisor"
PRIVATE_DIR_MODE = 0o700
FALLBACK_WORK_DIR_NAME = "unknown"
_UNSAFE_DIR_NAME_CHARS = re.compile(r"[^\w.-]")


def resolve_crash_work_dir(
    cache_dir: Path, crash_id: str, explicit_dir: Optional[str] = None
) -> Path:
    """Returns the CrashAdvisor work directory for `crash_id`, creating it if needed.

    An explicit --work-dir wins. Otherwise each crash gets a private folder in the
    CLI cache, so find-bug, analyze, autofix and reproduce reuse one download.
    """
    if explicit_dir:
        work_dir = Path(explicit_dir).expanduser()
    else:
        dir_name = _UNSAFE_DIR_NAME_CHARS.sub("_", crash_id).lstrip(".")
        work_dir = (
            Path(cache_dir)
            / CRASHADVISOR_CACHE_DIR_NAME
            / (dir_name or FALLBACK_WORK_DIR_NAME)
        )
    try:
        work_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise ExecutionError(
            f"Could not create the crash work directory '{work_dir}': {exc.strerror or exc}.",
            remediation="Pass a writable directory with --work-dir.",
            cause=exc,
        ) from exc
    return work_dir


def print_warning(message: str) -> None:
    """Writes a warning to stderr so JSON output on stdout stays clean."""
    sys.stderr.write(f"{message}\n")


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


from commands.crash.fix_checker import (
    FixStatusResult,
    check_git_history_fixes,
    evaluate_crash_fix_status,
    extract_crash_version_info,
    format_agent_version_guardrail_prompt,
)


@dataclass(frozen=True)
class CrashMetadata:
    """Typed parameter object representing parsed crash context and sandbox artifacts.

    Consolidates crash_id parsing, work_dir resolution, metadata.json parsing,
    top fault frame extraction, and auth token acquisition so crash subcommands
    share one single source of truth instead of repeating ad-hoc file parsing.
    """

    crash_id: str
    raw_crash_arg: str = ""
    work_dir: Path = Path(".")
    metadata_path: Path = Path("metadata.json")
    crashreport_path: Path = Path("crashreport.txt")
    rca_path: Path = Path("rca_summary.md")
    stable_signature: str = ""
    build_id: str = "unknown"
    version_str: str = "unknown"
    os_names: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    build_ids: tuple[str, ...] = ()
    crash_location: str = ""
    custom_keys: dict[str, str] = field(default_factory=dict)
    top_fault_function: Optional[str] = None
    top_fault_file: Optional[str] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    token: Optional[str] = None

    @classmethod
    def from_work_dir(
        cls,
        crash_id: str,
        work_dir: Path,
        *,
        raw_crash_arg: str = "",
        signature_override: Optional[str] = None,
        rca_content: str = "",
        token: Optional[str] = None,
        extract_top_fault_frame_fn: Callable[[str], tuple[Optional[str], Optional[str]]] = extract_top_fault_frame,
    ) -> "CrashMetadata":
        """Constructs a CrashMetadata snapshot from an existing work directory."""
        cleaned_id = parse_crash_id(crash_id) if crash_id else ""
        resolved_dir = Path(work_dir)
        meta_path = resolved_dir / "metadata.json"
        txt_dump = resolved_dir / "crashreport.txt"
        rca_path = resolved_dir / "rca_summary.md"

        meta_json: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                parsed = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    meta_json = parsed
            except (OSError, ValueError, TypeError) as exc:
                logger.debug("Failed parsing %s: %s", meta_path, exc)

        vinfo = extract_crash_version_info(meta_json)
        build_id = str(vinfo.get("build_id") or "unknown")
        version_str = str(vinfo.get("version_str") or build_id)

        proto = meta_json.get("report_proto", meta_json)
        if not isinstance(proto, dict):
            proto = {}

        stable_sig = (
            str(signature_override or "").strip()
            or str(
                proto.get("stableSignature")
                or proto.get("stable_signature")
                or meta_json.get("stable_signature")
                or ""
            ).strip()
        )

        versions_list: list[str] = []
        os_list: list[str] = []
        build_ids_list: list[str] = []
        crash_location = ""
        custom_keys: dict[str, str] = {}

        if meta_json:
            prod = proto.get("product", {}) if isinstance(proto, dict) else {}
            raw_ver = (
                (prod.get("version") if isinstance(prod, dict) else None)
                or meta_json.get("version")
                or meta_json.get("versions")
                or proto.get("versions")
                or ""
            )
            if isinstance(raw_ver, list):
                versions_list.extend([str(v).strip() for v in raw_ver if v])
            elif raw_ver:
                versions_list.append(str(raw_ver).strip())

            raw_os = (
                proto.get("operatingSystem")
                or meta_json.get("os_name")
                or meta_json.get("os")
                or ""
            )
            if isinstance(raw_os, list):
                for item in raw_os:
                    val = item.get("name") if isinstance(item, dict) else item
                    name = str(val or "").strip()
                    if name and name not in os_list:
                        os_list.append(name)
            elif isinstance(raw_os, dict):
                os_name = str(raw_os.get("name") or raw_os.get("Name") or "").strip()
                if os_name and os_name not in os_list:
                    os_list.append(os_name)
            else:
                os_name = str(raw_os or "").strip()
                if os_name and os_name not in os_list:
                    os_list.append(os_name)

            raw_bid = (
                meta_json.get("build_id")
                or meta_json.get("build_ids")
                or proto.get("buildIds")
                or ""
            )
            if isinstance(raw_bid, list):
                build_ids_list.extend([str(b).strip() for b in raw_bid if b])
            elif raw_bid:
                build_ids_list.append(str(raw_bid).strip())

            loc = meta_json.get("crash_location") or proto.get("crashLocation") or ""
            if loc:
                crash_location = str(loc).strip()

            for raw_keys in (proto.get("customKeys"), meta_json.get("custom_keys")):
                if isinstance(raw_keys, list):
                    for item in raw_keys:
                        if isinstance(item, dict) and "key" in item:
                            val = item.get("value")
                            custom_keys[str(item["key"])] = str(
                                val if val is not None else ""
                            )
                elif isinstance(raw_keys, dict):
                    for k, v in raw_keys.items():
                        custom_keys[str(k)] = str(v if v is not None else "")

        if rca_content:
            if not versions_list:
                m_ver = re.search(
                    r"(?:Version|Build Version)[:\s*]+`?([0-9]+\.[0-9]+[^\s`\)]*)`?",
                    rca_content,
                    re.IGNORECASE,
                )
                if m_ver:
                    versions_list.append(m_ver.group(1))
            if not os_list:
                m_os = re.search(
                    r"(?:Host OS|Operating System)[:\s*]+`?([^`\n]+)`?",
                    rca_content,
                    re.IGNORECASE,
                )
                if m_os:
                    os_list.append(m_os.group(1).strip())
            if not build_ids_list:
                m_bid = re.search(
                    r"(?:Build ID|Build IDs)[:\s*]+`?([0-9]{7,})`?",
                    rca_content,
                    re.IGNORECASE,
                )
                if m_bid:
                    build_ids_list.append(m_bid.group(1))

        top_func: Optional[str] = None
        top_file: Optional[str] = None
        if txt_dump.is_file():
            try:
                top_func, top_file = extract_top_fault_frame_fn(
                    txt_dump.read_text(encoding="utf-8")
                )
            except OSError:
                top_func, top_file = None, None

        return cls(
            crash_id=cleaned_id,
            raw_crash_arg=raw_crash_arg or crash_id,
            work_dir=resolved_dir,
            metadata_path=meta_path,
            crashreport_path=txt_dump,
            rca_path=rca_path,
            stable_signature=stable_sig,
            build_id=build_id,
            version_str=version_str,
            os_names=tuple(os_list),
            versions=tuple(versions_list),
            build_ids=tuple(build_ids_list),
            crash_location=crash_location,
            custom_keys=custom_keys,
            top_fault_function=top_func,
            top_fault_file=top_file,
            raw_metadata=meta_json,
            token=token,
        )

    @classmethod
    def from_args(
        cls,
        args: Any,
        cache_dir: Path,
        *,
        resolve_token: bool = False,
        rca_content: str = "",
        extract_top_fault_frame_fn: Callable[[str], tuple[Optional[str], Optional[str]]] = extract_top_fault_frame,
    ) -> "CrashMetadata":
        """Constructs a CrashMetadata parameter object from a parsed CLI Namespace."""
        if isinstance(args, cls):
            return args
        raw_arg = str(getattr(args, "crash_id", "") or "").strip()
        cleaned_id = parse_crash_id(raw_arg) if raw_arg else ""
        explicit_work_dir = getattr(args, "work_dir", None)
        work_dir = resolve_crash_work_dir(
            cache_dir, cleaned_id, explicit_work_dir
        )
        raw_token = getattr(args, "token", None)
        token = acquire_auth_token(raw_token) if resolve_token else raw_token
        sig_override = getattr(args, "signature", None)
        return cls.from_work_dir(
            cleaned_id,
            work_dir,
            raw_crash_arg=raw_arg,
            signature_override=sig_override,
            rca_content=rca_content,
            token=token,
            extract_top_fault_frame_fn=extract_top_fault_frame_fn,
        )

    def refresh(
        self,
        *,
        signature_override: Optional[str] = None,
        rca_content: str = "",
        extract_top_fault_frame_fn: Callable[[str], tuple[Optional[str], Optional[str]]] = extract_top_fault_frame,
    ) -> "CrashMetadata":
        """Re-reads metadata and crash artifacts from `self.work_dir` after a CrashAdvisor run."""
        return CrashMetadata.from_work_dir(
            self.crash_id,
            self.work_dir,
            raw_crash_arg=self.raw_crash_arg,
            signature_override=signature_override or self.stable_signature or None,
            rca_content=rca_content,
            token=self.token,
            extract_top_fault_frame_fn=extract_top_fault_frame_fn,
        )

    def to_sandbox_info_dict(self) -> dict[str, Any]:
        """Returns the legacy dictionary shape expected by Buganizer RCA formatting helpers."""
        return {
            "os": list(self.os_names),
            "versions": list(self.versions),
            "build_ids": list(self.build_ids),
            "crash_location": self.crash_location,
            "custom_keys": dict(self.custom_keys),
        }


__all__ = [
    "CrashMetadata",
    "FixStatusResult",
    "OAuthTokenManager",
    "StackTraceParser",
    "acquire_auth_token",
    "check_git_history_fixes",
    "create_secure_sandbox_dir",
    "evaluate_crash_fix_status",
    "extract_crash_version_info",
    "extract_top_fault_frame",
    "format_agent_version_guardrail_prompt",
    "get_crashadvisor_sandbox_dir",
    "is_path_secure_user_owned",
    "parse_crash_id",
    "resolve_crash_work_dir",
]
