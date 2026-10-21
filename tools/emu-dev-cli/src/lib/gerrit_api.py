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

"""Gerrit REST API client and local Git workspace coordination utilities.

Provides authenticated inspection of Gerrit changes, comment thread tree
reconstruction, automated inline reply and thread resolution publishing,
active review polling, and local repository checkout/upload operations.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from lib.oauth import OAuthTokenManager, get_ssl_context
from lib.workspace import WorkspacePathResolver

logger = logging.getLogger(__name__)

DEFAULT_GERRIT_HOST = "https://googleplex-android-review.git.corp.google.com"
GERRIT_OAUTH_SCOPE = "https://www.googleapis.com/auth/gerritcodereview"
GERRIT_HTTP_TIMEOUT_SEC = 20.0
GERRIT_CLI_TIMEOUT_SEC = 25.0
_AUTH_FALLBACK_HTTP_CODES = (400, 401, 403)
BASE64_QUANTUM_LEN = 4
OCTAL_ESCAPE_DIGITS = "01234567"
OCTAL_ESCAPE_LEN = 3
MAX_OCTAL_BYTE_VAL = 0xFF
MIN_QUOTED_PATH_LEN = 2
GIT_C_QUOTE_ESCAPES: dict[str, int] = {
    "\\": 92,
    '"': 34,
    "t": 9,
    "n": 10,
    "r": 13,
    "a": 7,
    "b": 8,
    "f": 12,
    "v": 11,
}

_URL_CL_RE = re.compile(r"/\+/(\d+)(?:/\d+)?(?:[?#].*)?$")
_LEGACY_URL_CL_RE = re.compile(r"/c/(?:[^/]+/)*(\d+)(?:/\d+)?(?:[?#].*)?$")
_AG_SHORTLINK_RE = re.compile(r"^ag/(\d+)(?:/\d+)?(?:[?#].*)?$", re.IGNORECASE)
_CHANGE_ID_RE = re.compile(r"\b(I[0-9a-fA-F]{40})\b")
_CHANGE_ID_FULL_RE = re.compile(r"^I[0-9a-fA-F]{40}$")
COMMIT_CHANGE_ID_RE = re.compile(r"(?im)^\s*Change-Id:\s*(I[0-9a-fA-F]{40})\b")


@dataclass
class GerritCommentMessage:
    """Single comment entry within a Gerrit review thread."""

    comment_id: str
    author_name: str
    author_email: str
    patch_set: int
    updated: str
    message: str
    unresolved: bool
    is_ai: bool = False
    commit_id: str = ""


@dataclass
class GerritCommentThread:
    """Reconstructed inline or file-level Gerrit comment thread."""

    root_id: str
    leaf_id: str
    file_path: str
    line: int
    start_line: int
    end_line: int
    patch_set: int
    commit_id: str
    unresolved: bool
    is_ai: bool
    author_name: str
    author_email: str
    updated: str
    messages: list[GerritCommentMessage] = field(default_factory=list)

    @property
    def latest_message(self) -> str:
        """Returns the text of the most recent message in the thread."""
        return self.messages[-1].message if self.messages else ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes the thread to a JSON-compatible dictionary."""
        data = asdict(self)
        data["latest_message"] = self.latest_message
        return data


@dataclass
class GerritReplyInput:
    """Structured reply to post against an existing Gerrit comment."""

    comment_id: str
    file_path: str
    line: int
    patch_set: int
    message: str
    resolve: bool = True


def strip_xssi_prefix(raw_text: str) -> str:
    """Strips Gerrit's XSSI prefix (`)]}'`) and any leading SSO tool diagnostic lines.

    @param raw_text Raw HTTP response body from Gerrit or `gosso`/`gob-curl`.
    @return Clean JSON string ready for `json.loads`.
    """
    text = raw_text.lstrip()
    lines = text.splitlines()
    while lines and not (
        lines[0].startswith(")]}'")
        or lines[0].startswith("{")
        or lines[0].startswith("[")
        or lines[0].startswith('"')
    ):
        lines.pop(0)
    if lines and lines[0].startswith(")]}'"):
        lines.pop(0)
    return "\n".join(lines).strip()


def _consume_c_escape(inner: str, index: int) -> tuple[bytes, int]:
    """Decodes one backslash escape token inside a git C-quoted path."""
    if index + 1 >= len(inner):
        return inner[index].encode("utf-8"), index + 1
    nxt = inner[index + 1]
    octal_end = index + 1 + OCTAL_ESCAPE_LEN
    if (
        nxt in OCTAL_ESCAPE_DIGITS
        and octal_end <= len(inner)
        and all(ch in OCTAL_ESCAPE_DIGITS for ch in inner[index + 1 : octal_end])
    ):
        byte_val = int(inner[index + 1 : octal_end], 8)
        if byte_val <= MAX_OCTAL_BYTE_VAL:
            return bytes([byte_val]), octal_end
    if nxt in GIT_C_QUOTE_ESCAPES:
        return bytes([GIT_C_QUOTE_ESCAPES[nxt]]), index + 2
    return inner[index].encode("utf-8"), index + 1


def unquote_git_diff_path(raw: str) -> str:
    """Unquotes git C-quoted UTF-8 diff header paths (`"b/..."`) into normalized relative paths."""
    path = (raw or "").strip()
    if len(path) >= MIN_QUOTED_PATH_LEN and path[0] == '"' and path[-1] == '"':
        inner = path[1:-1]
        out = bytearray()
        idx = 0
        while idx < len(inner):
            if inner[idx] == "\\":
                chunk, idx = _consume_c_escape(inner, idx)
                out.extend(chunk)
                continue
            out.extend(inner[idx].encode("utf-8"))
            idx += 1
        path = out.decode("utf-8", errors="replace")
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("b/") else normalized


def decode_gerrit_patch(raw_patch: str) -> str:
    """Decodes a Gerrit `/patch` response from XSSI JSON, strict base64, or plain diff text."""
    candidate = strip_xssi_prefix(raw_patch)
    if candidate.startswith('"'):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            parsed = None
        if isinstance(parsed, str):
            raw_patch = parsed

    if "diff --git " in raw_patch:
        return raw_patch

    compact = "".join(raw_patch.split())
    if not compact or len(compact) % BASE64_QUANTUM_LEN != 0:
        return raw_patch
    try:
        return base64.b64decode(compact, validate=True).decode("utf-8", errors="replace")
    except ValueError:
        return raw_patch


def _resolve_head_change_id(cwd: Path | str | None) -> str:
    """Extracts the `Change-Id: I...` footer from the local `HEAD` commit."""
    work_dir = str(Path(cwd).resolve()) if cwd else None
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%B", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            cwd=work_dir,
            timeout=GERRIT_CLI_TIMEOUT_SEC,
        )
    except Exception as exc:
        stderr = (getattr(exc, "stderr", None) or "").strip()
        detail = f"{exc}: {stderr}" if stderr else str(exc)
        raise ValueError(
            f"Failed to read HEAD commit message in '{work_dir or '.'}' ({detail}). "
            "Provide an explicit CL number or URL."
        ) from exc
    match = COMMIT_CHANGE_ID_RE.search(res.stdout)
    if match:
        return match.group(1)
    raise ValueError(
        f"No 'Change-Id: I...' footer found in HEAD commit at '{work_dir or '.'}'. "
        "Provide an explicit Gerrit CL number or URL."
    )


def parse_cl_identifier(
    cl_input: str | None,
    cwd: Path | str | None = None,
    *,
    allow_head: bool = True,
    strict: bool = False,
) -> str:
    """Normalizes a CL URL, `ag/<id>`, numeric CL ID, Change-Id, or `HEAD` into a Gerrit ID."""
    cleaned = (cl_input or "").strip()
    if not cleaned or cleaned.upper() == "HEAD":
        if allow_head:
            return _resolve_head_change_id(cwd)
    else:
        for pattern in (_AG_SHORTLINK_RE, _URL_CL_RE, _LEGACY_URL_CL_RE):
            matched = pattern.search(cleaned)
            if matched:
                return matched.group(1)
        if cleaned.isdigit() or _CHANGE_ID_FULL_RE.fullmatch(cleaned):
            return cleaned
        if not strict:
            m_cid = _CHANGE_ID_RE.search(cleaned)
            return m_cid.group(1) if m_cid else cleaned

    safe_input = cleaned.split("?", 1)[0]
    raise ValueError(
        f"Could not extract a Gerrit CL identifier from '{safe_input}' "
        "(expected a numeric CL ID such as '43137255', 'ag/<id>', Gerrit review URL, "
        "or 'I<40-hex>' Change-Id). Pass a valid Gerrit CL reference to `--cl`, or "
        "use `--commit <ref>` to audit a local git commit."
    )


class GerritClient:
    """Authenticated client for querying and updating Gerrit code review changes."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        token: str | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.api_host = self.host.replace(
            ".git.corp.google.com", ".googlesource.com"
        )
        self._explicit_token = token
        self._urllib_auth_failed = False
        self._oauth = OAuthTokenManager(
            scopes=[GERRIT_OAUTH_SCOPE],
            env_vars=["GERRIT_TOKEN", "OAUTH2_TOKEN", "ANDROID_BUILD_TOKEN"],
        )

    def _get_token(self) -> str | None:
        if self._urllib_auth_failed and not self._explicit_token:
            return None
        return self._oauth.acquire_token(user_token=self._explicit_token)

    def _handle_urllib_http_error(
        self,
        http_err: urllib.error.HTTPError,
        method: str,
        path: str,
        transport_errors: list[str],
    ) -> None:
        """Closes `http_err` cleanly and raises RuntimeError on non-auth HTTP failures."""
        clean_path = path.split("?", 1)[0]
        with http_err:
            if http_err.code in _AUTH_FALLBACK_HTTP_CODES:
                self._urllib_auth_failed = True
                transport_errors.append(f"urllib HTTP {http_err.code}")
                return
            reason = getattr(http_err, "reason", "") or "HTTP error"
            raise RuntimeError(
                f"Gerrit API {method} '{clean_path}' failed with HTTP {http_err.code} ({reason}). "
                f"Verify that the target change and revision exist on '{self.host}' and retry."
            ) from http_err

    def _try_urllib_request(
        self,
        url: str,
        path: str,
        method: str,
        body_bytes: bytes | None,
        transport_errors: list[str],
    ) -> str | None:
        """Attempts an OAuth-authenticated HTTPS request via `urllib`, returning None on 400/401/403."""
        if self._urllib_auth_failed:
            transport_errors.append("urllib skipped after prior auth failure")
            return None
        token = self._get_token()
        if not token:
            transport_errors.append("no OAuth token available")
            return None
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if body_bytes is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                req, context=get_ssl_context(), timeout=GERRIT_HTTP_TIMEOUT_SEC
            ) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as http_err:
            self._handle_urllib_http_error(http_err, method, path, transport_errors)
        except Exception as exc:
            transport_errors.append(f"urllib {type(exc).__name__}")
            logger.debug("Direct urllib Gerrit call failed, trying CLI fallback: %s", exc)
        return None

    def _run_cli_request(self, cmd: list[str], label: str, transport_errors: list[str]) -> str | None:
        """Executes a `gob-curl` or `gosso` CLI fallback command with UTF-8 decoding."""
        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=GERRIT_CLI_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            transport_errors.append(f"{label} {type(exc).__name__}")
            return None
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout
        err_lines = (res.stderr or "").strip().splitlines()
        err_detail = f" ({err_lines[0].split('?', 1)[0]})" if err_lines else ""
        transport_errors.append(f"{label} exit code {res.returncode}{err_detail}")
        return None

    def _try_cli_fallbacks(
        self,
        path: str,
        url: str,
        method: str,
        data: dict[str, Any] | None,
        transport_errors: list[str],
    ) -> str | None:
        """Attempts authenticated Gerrit REST fallback via `gob-curl` then `gosso`."""
        gob_curl = shutil.which("gob-curl")
        if gob_curl:
            cmd = [gob_curl, "-s", "-f", "-S", "-X", method]
            if data is not None:
                cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(data)])
            out = self._run_cli_request([*cmd, url], "gob-curl", transport_errors)
            if out is not None:
                return out
        gosso = shutil.which("gosso")
        if gosso:
            unprefixed_path = path[2:] if path.startswith("/a/") else path
            cmd = [gosso, "-method", method, "-url", f"{self.host}{unprefixed_path}"]
            if data is not None:
                cmd.extend(["-header", "Content-Type: application/json", "-data", json.dumps(data)])
            return self._run_cli_request(cmd, "gosso", transport_errors)
        return None

    def _request_raw(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> str:
        """Executes an authenticated Gerrit REST API request and returns the raw response body."""
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if not path.startswith("/a/"):
            path = f"/a{path}"
        clean_path = path.split("?", 1)[0]
        if path.rstrip("/").endswith("/submit"):
            raise PermissionError(
                f"Blocked Gerrit submit request for '{clean_path}': automated CL submission via GerritClient "
                "is disabled by safety policy. Ask a human reviewer to approve and submit in Gerrit."
            )
        if isinstance(data, dict) and "labels" in data:
            raise PermissionError(
                f"Blocked Gerrit label vote on '{clean_path}': automated voting via GerritClient is "
                "disabled by safety policy. Use `gob-curl` with '\"notify\": \"OWNER\"' only when "
                "setting Presubmit-Ready+1 as authorized by the user."
            )
        url = f"{self.api_host}{path}"
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        transport_errors: list[str] = []

        direct_body = self._try_urllib_request(url, path, method, body_bytes, transport_errors)
        if direct_body is not None:
            return direct_body
        cli_body = self._try_cli_fallbacks(path, url, method, data, transport_errors)
        if cli_body is not None:
            return cli_body

        detail = "; ".join(transport_errors) or "no OAuth token, gob-curl, or gosso transport installed"
        raise RuntimeError(
            f"Unable to authenticate or reach Gerrit endpoint '{self.api_host}{clean_path}' ({detail}). "
            "Run `gcert` or `oauth2l fetch --sso $USER@google.com gerritcodereview` and retry."
        )

    def _request_json(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Executes an authenticated Gerrit REST API request and parses its JSON response."""
        raw = self._request_raw(endpoint, method=method, data=data)
        cleaned = strip_xssi_prefix(raw)
        try:
            return json.loads(cleaned) if cleaned else {}
        except json.JSONDecodeError as exc:
            clean_path = endpoint.split("?", 1)[0]
            raise RuntimeError(
                f"Failed to parse JSON response from Gerrit {method} '{clean_path}' ({exc.msg}). "
                f"Verify that the target CL exists on '{self.host}' and run `gcert` to refresh SSO credentials."
            ) from exc

    def get_revision_patch(self, change_id: str, revision: str = "current") -> str:
        """Fetches and decodes the unified diff patch for a Gerrit change revision."""
        encoded_id = urllib.parse.quote(str(change_id), safe="")
        encoded_rev = urllib.parse.quote(str(revision), safe="")
        raw_patch = self._request_raw(f"/a/changes/{encoded_id}/revisions/{encoded_rev}/patch")
        stripped = raw_patch.strip()
        if not stripped:
            raise RuntimeError(
                f"Received an empty unified diff patch for Gerrit CL '{change_id}' "
                f"(revision '{revision}') from '{self.host}'. "
                "Verify that the CL has at least one patchset uploaded and run `gcert` before retrying."
            )
        return decode_gerrit_patch(stripped)

    def list_open_changes(
        self,
        owner: str = "self",
        project: str | None = None,
        branch: str | None = None,
        unresolved_only: bool = False,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Lists open Gerrit changes for an owner with revision and label metadata."""
        q_parts = [f"owner:{owner}", "status:open"]
        if project:
            q_parts.append(f"project:{project}")
        if branch:
            q_parts.append(f"branch:{branch}")
        query = "+".join(urllib.parse.quote(p, safe=":") for p in q_parts)
        endpoint = (
            f"/a/changes/?q={query}"
            f"&o=CURRENT_REVISION&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS&n={limit}"
        )
        changes = self._request_json(endpoint)
        if not isinstance(changes, list):
            return []

        results: list[dict[str, Any]] = []
        for ch in changes:
            unresolved_count = int(ch.get("unresolved_comment_count", 0) or 0)
            if unresolved_only and unresolved_count <= 0:
                continue
            cur_rev_sha = ch.get("current_revision", "")
            rev_info = (ch.get("revisions") or {}).get(cur_rev_sha, {})
            patch_set = int(rev_info.get("_number", 1) or 1)
            labels = ch.get("labels") or {}
            cr_score = _extract_label_score(labels.get("Code-Review"))
            pv_score = _extract_label_score(labels.get("Presubmit-Verified"))
            num = ch.get("_number")
            project = ch.get("project", "")
            results.append(
                {
                    "number": num,
                    "change_id": ch.get("change_id", ""),
                    "project": project,
                    "branch": ch.get("branch", ""),
                    "subject": ch.get("subject", ""),
                    "status": ch.get("status", "NEW"),
                    "current_revision": cur_rev_sha,
                    "patch_set": patch_set,
                    "unresolved_comment_count": unresolved_count,
                    "code_review": cr_score,
                    "presubmit_verified": pv_score,
                    "updated": ch.get("updated", ""),
                    "url": f"{self.host}/c/{project}/+/{num}",
                }
            )
        return results

    def get_change_detail(
        self,
        change_id: str,
        include_files: bool = False,
    ) -> dict[str, Any]:
        """Retrieves full change detail including revisions, commit metadata, labels, and messages."""
        encoded_id = urllib.parse.quote(str(change_id), safe="")
        extra = "&o=CURRENT_FILES" if include_files else ""
        endpoint = (
            f"/a/changes/{encoded_id}/detail"
            f"?o=CURRENT_REVISION&o=ALL_REVISIONS&o=CURRENT_COMMIT"
            f"&o=MESSAGES&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS{extra}"
        )
        data = self._request_json(endpoint)
        if not isinstance(data, dict):
            raise RuntimeError(
                f"Unexpected Gerrit detail response type ({type(data).__name__}) for CL '{change_id}' "
                f"on '{self.host}'. Verify that the CL exists and run `gcert` before retrying."
            )
        return data

    def get_comment_threads(
        self,
        change_id: str,
        include_resolved: bool = False,
    ) -> list[GerritCommentThread]:
        """Fetches comments and robotcomments for a CL and reconstructs ordered threads.

        @param change_id Gerrit CL number or Change-Id.
        @param include_resolved Whether to include threads whose leaf comment has `unresolved=False`.
        @return List of `GerritCommentThread` objects ordered by file path and line number.
        """
        encoded_id = urllib.parse.quote(str(change_id), safe="")
        human_comments = self._request_json(f"/a/changes/{encoded_id}/comments")
        try:
            robot_comments = self._request_json(f"/a/changes/{encoded_id}/robotcomments")
        except Exception:
            robot_comments = {}

        merged_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if isinstance(human_comments, dict):
            for fpath, clist in human_comments.items():
                if isinstance(clist, list):
                    merged_by_file[fpath].extend(clist)
        if isinstance(robot_comments, dict):
            for fpath, clist in robot_comments.items():
                if isinstance(clist, list):
                    merged_by_file[fpath].extend(clist)

        threads: list[GerritCommentThread] = []
        for file_path, raw_list in sorted(merged_by_file.items()):
            threads.extend(
                _build_threads_for_file(
                    file_path=file_path,
                    raw_comments=raw_list,
                    include_resolved=include_resolved,
                )
            )

        threads.sort(key=lambda t: (t.file_path, t.line, t.updated))
        return threads

    def post_review_replies(
        self,
        change_id: str,
        replies: list[GerritReplyInput],
        top_message: str | None = None,
    ) -> list[dict[str, Any]]:
        """Posts inline comment replies grouped by their target patchset.

        Voting on review labels (e.g. `Code-Review+2`) is intentionally unsupported
        and blocked so agents can only reply to and resolve review comment threads.

        @param change_id Gerrit CL number or Change-Id.
        @param replies List of `GerritReplyInput` objects specifying `comment_id`, `patch_set`, and `message`.
        @param top_message Optional top-level CL review summary message.
        @return List of Gerrit ReviewResult response dictionaries.
        """
        encoded_id = urllib.parse.quote(str(change_id), safe="")
        by_patchset: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for rep in replies:
            entry: dict[str, Any] = {
                "in_reply_to": rep.comment_id,
                "message": rep.message,
                "unresolved": not rep.resolve,
            }
            if rep.line and rep.line > 0:
                entry["line"] = rep.line
            by_patchset[int(rep.patch_set or 1)][rep.file_path].append(entry)

        responses: list[dict[str, Any]] = []
        for ps in sorted(by_patchset.keys()):
            payload: dict[str, Any] = {
                "comments": dict(by_patchset[ps]),
            }
            res = self._request_json(
                f"/a/changes/{encoded_id}/revisions/{ps}/review",
                method="POST",
                data=payload,
            )
            responses.append(res if isinstance(res, dict) else {})

        if top_message:
            cur_payload: dict[str, Any] = {"message": top_message}
            res = self._request_json(
                f"/a/changes/{encoded_id}/revisions/current/review",
                method="POST",
                data=cur_payload,
            )
            responses.append(res if isinstance(res, dict) else {})

        return responses

    def wait_for_review_update(
        self,
        change_id: str,
        baseline_patchset: int,
        known_comment_ids: set[str] | None = None,
        timeout: timedelta = timedelta(seconds=120),
        poll_interval: timedelta = timedelta(seconds=15),
        wait_for_ai: bool = True,
        wait_for_approval: bool = False,
        require_presubmit: bool = False,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        """Polls Gerrit for new unresolved comments, AI review completion, or approval.

        @param change_id Gerrit CL number or Change-Id.
        @param baseline_patchset Patchset number being monitored.
        @param known_comment_ids Set of comment IDs already known prior to waiting.
        @param timeout Maximum duration to poll before returning `status='timeout'`.
        @param poll_interval Interval between Gerrit REST API polls.
        @param wait_for_ai Exit cleanly once `groupagent-android-review` completes on `baseline_patchset` with 0 new comments.
        @param wait_for_approval Continue polling until `Code-Review+2` is granted (unless new comments arrive).
        @param require_presubmit Require `Presubmit-Verified+1/+2` before returning `'approved'`.
        @param sleep_fn Injectable sleep callable for deterministic testing.
        @param clock_fn Injectable monotonic clock callable for deterministic testing.
        @return Dictionary containing `status` (`'new_comments'`, `'approved'`, `'presubmit_failed'`, `'ai_review_clean'`, or `'timeout'`), `new_threads`, and `detail`.
        """
        known = set(known_comment_ids or set())
        start_ts = clock_fn()
        deadline = start_ts + max(0.0, timeout.total_seconds())
        interval_sec = max(1.0, poll_interval.total_seconds())

        last_detail: dict[str, Any] = {}
        while True:
            last_detail = self.get_change_detail(change_id)
            unresolved_threads = self.get_comment_threads(
                change_id, include_resolved=False
            )
            new_threads = [
                t
                for t in unresolved_threads
                if t.leaf_id not in known and t.root_id not in known
            ]
            if new_threads:
                return {
                    "status": "new_comments",
                    "new_threads": new_threads,
                    "unresolved_threads": unresolved_threads,
                    "detail": last_detail,
                }

            labels = last_detail.get("labels") or {}
            pv_score = _extract_label_score(labels.get("Presubmit-Verified"))
            if pv_score < 0:
                return {
                    "status": "presubmit_failed",
                    "new_threads": [],
                    "unresolved_threads": unresolved_threads,
                    "detail": last_detail,
                }

            ai_done = _has_ai_review_completed_on_patchset(
                last_detail, baseline_patchset
            )
            cr_score = _extract_label_score(labels.get("Code-Review"))
            if (
                cr_score >= 2
                and not unresolved_threads
                and (not require_presubmit or pv_score >= 1)
                and (not wait_for_ai or wait_for_approval or ai_done)
            ):
                return {
                    "status": "approved",
                    "new_threads": [],
                    "unresolved_threads": [],
                    "detail": last_detail,
                }

            if wait_for_ai and not wait_for_approval:
                if ai_done and not unresolved_threads:
                    return {
                        "status": "ai_review_clean",
                        "new_threads": [],
                        "unresolved_threads": [],
                        "detail": last_detail,
                    }

            now = clock_fn()
            if now >= deadline:
                return {
                    "status": "timeout",
                    "new_threads": [],
                    "unresolved_threads": unresolved_threads,
                    "detail": last_detail,
                }

            sleep_fn(min(interval_sec, max(0.0, deadline - now)))


def _extract_label_score(label_info: dict[str, Any] | None) -> int:
    """Extracts the effective integer score (-2..+2) from a Gerrit DetailedLabelInfo dict."""
    if not isinstance(label_info, dict):
        return 0
    if label_info.get("rejected"):
        return -2
    if label_info.get("disliked"):
        return -1
    if label_info.get("approved"):
        return 2
    if label_info.get("recommended"):
        return 1
    votes = label_info.get("all")
    if isinstance(votes, list):
        vals = [int(v.get("value", 0) or 0) for v in votes if isinstance(v, dict)]
        if any(v < 0 for v in vals):
            return min(vals)
        if any(v > 0 for v in vals):
            return max(vals)
    return int(label_info.get("value", 0) or 0)


def _has_ai_review_completed_on_patchset(
    detail: dict[str, Any], patch_set: int
) -> bool:
    """Checks whether an AI review agent (`groupagent-android-*`) has posted a review message on or after `patch_set`."""
    messages = detail.get("messages") or []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        rev_num = int(msg.get("_revision_number", 0) or 0)
        if rev_num < patch_set:
            continue
        tag = str(msg.get("tag", "")).lower()
        author = msg.get("author") or {}
        author_name = str(author.get("name", "")).lower()
        if "cl_review_agent" in tag or "groupagent-android-" in author_name:
            return True
    return False


def _is_ai_comment(raw: dict[str, Any]) -> bool:
    if bool(raw.get("is_ai")):
        return True
    tag = str(raw.get("tag", "")).lower()
    if "cl_review_agent" in tag or "autogenerated" in tag or "robot" in tag:
        return True
    author = raw.get("author") or {}
    name = str(author.get("name", "")).lower()
    return "groupagent-android-" in name or "[gerrit review agent]" in str(
        raw.get("message", "")
    ).lower()


def _build_threads_for_file(
    file_path: str,
    raw_comments: list[dict[str, Any]],
    include_resolved: bool,
) -> list[GerritCommentThread]:
    """Reconstructs comment threads for a single file by linking `in_reply_to` chains."""
    by_id: dict[str, dict[str, Any]] = {}
    children_of: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []

    sorted_raw = sorted(
        [c for c in raw_comments if isinstance(c, dict) and c.get("id")],
        key=lambda c: (str(c.get("updated", "")), str(c.get("id", ""))),
    )

    for c in sorted_raw:
        cid = str(c["id"])
        by_id[cid] = c

    for c in sorted_raw:
        cid = str(c["id"])
        parent_id = str(c.get("in_reply_to", "") or "").strip()
        if parent_id and parent_id in by_id:
            children_of[parent_id].append(cid)
        else:
            roots.append(cid)

    threads: list[GerritCommentThread] = []
    for root_id in roots:
        chain_ids: list[str] = []
        queue = [root_id]
        visited: set[str] = set()
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            chain_ids.append(curr)
            queue.extend(children_of.get(curr, []))

        chain_objs = sorted(
            [by_id[i] for i in chain_ids if i in by_id],
            key=lambda c: (str(c.get("updated", "")), str(c.get("id", ""))),
        )
        if not chain_objs:
            continue

        root_obj = chain_objs[0]
        leaf_obj = chain_objs[-1]
        is_unresolved = bool(leaf_obj.get("unresolved", False))
        if not include_resolved and not is_unresolved:
            continue

        line_num = int(
            leaf_obj.get("line")
            or root_obj.get("line")
            or (root_obj.get("range") or {}).get("end_line")
            or 0
        )
        rng = root_obj.get("range") or leaf_obj.get("range") or {}
        start_line = int(rng.get("start_line") or line_num or 0)
        end_line = int(rng.get("end_line") or line_num or 0)

        msg_items: list[GerritCommentMessage] = []
        any_ai = False
        for item in chain_objs:
            author = item.get("author") or {}
            item_ai = _is_ai_comment(item)
            any_ai = any_ai or item_ai
            msg_items.append(
                GerritCommentMessage(
                    comment_id=str(item.get("id", "")),
                    author_name=str(author.get("name") or author.get("email") or "Reviewer"),
                    author_email=str(author.get("email") or ""),
                    patch_set=int(item.get("patch_set", 1) or 1),
                    updated=str(item.get("updated", "")),
                    message=str(item.get("message", "")).strip(),
                    unresolved=bool(item.get("unresolved", False)),
                    is_ai=item_ai,
                    commit_id=str(item.get("commit_id", "")),
                )
            )

        root_author = root_obj.get("author") or {}
        threads.append(
            GerritCommentThread(
                root_id=str(root_obj.get("id", "")),
                leaf_id=str(leaf_obj.get("id", "")),
                file_path=file_path,
                line=line_num,
                start_line=start_line,
                end_line=end_line,
                patch_set=int(leaf_obj.get("patch_set") or root_obj.get("patch_set") or 1),
                commit_id=str(leaf_obj.get("commit_id") or root_obj.get("commit_id") or ""),
                unresolved=is_unresolved,
                is_ai=any_ai,
                author_name=str(
                    root_author.get("name") or root_author.get("email") or "Reviewer"
                ),
                author_email=str(root_author.get("email") or ""),
                updated=str(leaf_obj.get("updated") or root_obj.get("updated") or ""),
                messages=msg_items,
            )
        )

    return threads


def find_repo_workspace_root(start_dir: Path | str | None = None) -> Path | None:
    """Locates the enclosing `.repo` workspace root directory."""
    cur = Path(start_dir or os.getcwd()).expanduser().resolve()
    for candidate in [cur] + list(cur.parents):
        if (candidate / ".repo").is_dir():
            return candidate
    return None


def resolve_project_repo_path(
    project_name: str,
    workspace_root: Path | str | None = None,
    fallback_cwd: Path | str | None = None,
) -> Path:
    """Resolves a Gerrit project name (e.g. `platform/hardware/google/aemu`) to its local checkout directory.

    @param project_name Gerrit project identifier from ChangeInfo.
    @param workspace_root Optional `.repo` workspace root directory.
    @param fallback_cwd Optional directory to check if not inside a `.repo` tree.
    @return Resolved `Path` to the local Git repository.
    """
    clean_proj = (project_name or "").strip().strip("/")
    rel_candidate = (
        clean_proj[len("platform/") :]
        if clean_proj.startswith("platform/")
        else clean_proj
    )

    ws_root = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root
        else find_repo_workspace_root(fallback_cwd)
    )

    if ws_root:
        manifest_file = ws_root / ".repo" / "manifest.xml"
        if manifest_file.is_file():
            try:
                tree = ET.parse(manifest_file)
                for elem in tree.getroot().findall(".//project"):
                    if elem.attrib.get("name") == clean_proj:
                        proj_path = elem.attrib.get("path") or rel_candidate
                        cand = (ws_root / proj_path).resolve()
                        if cand.is_dir():
                            return cand
            except Exception as exc:
                logging.getLogger(__name__).debug("Ignored error in resolve_project_repo_path: %s", exc)

        for rel in (rel_candidate, clean_proj):
            if rel:
                cand = (ws_root / rel).resolve()
                if cand.is_dir():
                    return cand

    resolver = WorkspacePathResolver()
    for rel in (rel_candidate, clean_proj):
        if rel:
            found = resolver.find_directory(rel)
            if found and found.is_dir():
                return found.resolve()

    if fallback_cwd:
        return Path(fallback_cwd).expanduser().resolve()
    return Path.cwd().resolve()


def extract_source_snippet(
    repo_path: Path,
    file_path: str,
    line: int,
    start_line: int = 0,
    end_line: int = 0,
    context_lines: int = 4,
) -> str:
    """Reads a numbered source code snippet around `line` from `repo_path / file_path`."""
    if not file_path or file_path.startswith("/"):
        return ""
    resolved_repo = repo_path.resolve()
    full_path = (resolved_repo / file_path).resolve()
    if not full_path.is_relative_to(resolved_repo) or not full_path.is_file():
        return ""
    try:
        all_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if not all_lines:
        return ""

    focus_start = start_line if start_line > 0 else (line if line > 0 else 1)
    focus_end = end_line if end_line >= focus_start else focus_start
    lo = max(1, focus_start - context_lines)
    hi = min(len(all_lines), focus_end + context_lines)

    snippet_lines: list[str] = []
    for idx in range(lo, hi + 1):
        marker = ">>" if focus_start <= idx <= focus_end else "  "
        snippet_lines.append(f"{marker} {idx:4d}: {all_lines[idx - 1]}")
    return "\n".join(snippet_lines)


def render_comments_markdown(
    change_detail: dict[str, Any],
    threads: list[GerritCommentThread],
    repo_path: Path | None = None,
    queue_file: Path | str | None = None,
    context_lines: int = 4,
) -> str:
    """Renders a compact, token-efficient Markdown dossier for a CL's comment threads."""
    cl_num = change_detail.get("_number", "?")
    subject = change_detail.get("subject", "")
    project = change_detail.get("project", "")
    branch = change_detail.get("branch", "")
    change_id = change_detail.get("change_id", "")
    cur_rev = change_detail.get("current_revision", "")
    rev_info = (change_detail.get("revisions") or {}).get(cur_rev, {})
    patch_set = rev_info.get("_number", 1)

    lines: list[str] = [
        f"## CL {cl_num}: {subject}",
        "",
        f"- **URL:** `{DEFAULT_GERRIT_HOST}/c/{project}/+/{cl_num}`",
        f"- **Project / Branch:** `{project}` (`{branch}`)",
        f"- **Local Repository:** `{repo_path or '.'}`",
        f"- **Change-Id:** `{change_id}`",
        f"- **Current Patchset:** `{patch_set}` (`{cur_rev[:10]}`)",
        f"- **Comment Threads Shown:** `{len(threads)}`",
        "",
    ]

    if not threads:
        lines.append("🟢 **No unresolved review comments on this CL!**")
        lines.append("")
        return "\n".join(lines)

    for idx, t in enumerate(threads, 1):
        status_tag = "UNRESOLVED" if t.unresolved else "RESOLVED"
        ai_tag = " [AI Review Agent]" if t.is_ai else ""
        loc_str = (
            f"{t.file_path}:{t.start_line}-{t.end_line}"
            if (t.start_line and t.end_line and t.start_line != t.end_line)
            else (f"{t.file_path}:{t.line}" if t.line else t.file_path)
        )
        lines.extend(
            [
                f"### {idx}. `{loc_str}` (Thread Leaf ID: `{t.leaf_id}`, Patchset {t.patch_set}) [{status_tag}]{ai_tag}",
                f"- **File:** `{t.file_path}` (Line `{t.line}`)",
                f"- **Author:** {t.author_name}",
            ]
        )

        if repo_path and t.line > 0:
            snippet = extract_source_snippet(
                repo_path=repo_path,
                file_path=t.file_path,
                line=t.line,
                start_line=t.start_line,
                end_line=t.end_line,
                context_lines=context_lines,
            )
            if snippet:
                lines.append("- **Local Source Context:**")
                lines.append("  ```text")
                for sline in snippet.splitlines():
                    lines.append(f"  {sline}")
                lines.append("  ```")

        lines.append("- **Thread Messages:**")
        for m in t.messages:
            lines.append(f"  - **{m.author_name}** (PS {m.patch_set}, `{m.comment_id}`):")
            for mline in m.message.splitlines():
                lines.append(f"    > {mline}")

        if t.unresolved:
            q_arg = f' --queue-file "{queue_file}" --stage-only' if queue_file else ""
            lines.extend(
                [
                    "- **Command to Record Reply:**",
                    "  ```bash",
                    f'  emu-dev-cli gerrit reply {cl_num} --comment-id "{t.leaf_id}" --message "Done: <concise explanation>" --resolve{q_arg}',
                    "  ```",
                ]
            )
        lines.append("")

    return "\n".join(lines)


def fetch_and_checkout_cl(
    change_detail: dict[str, Any],
    repo_path: Path,
    mode: str = "auto",
) -> dict[str, Any]:
    """Ensures the target CL's latest revision is present in `repo_path`.

    If `HEAD` already contains the CL's `Change-Id`, leaves the working branch intact
    and reports `action='already_on_change'`. Otherwise fetches the revision ref from
    `goog` or `origin` and cherry-picks or checks it out.
    """
    change_id = str(change_detail.get("change_id", "")).strip()
    cur_rev = str(change_detail.get("current_revision", "")).strip()
    rev_obj = (change_detail.get("revisions") or {}).get(cur_rev) or {}
    fetch_ref = str(rev_obj.get("ref", "")).strip()
    cl_num = change_detail.get("_number")

    if not fetch_ref and cl_num and rev_obj.get("_number"):
        shard = str(cl_num)[-2:].zfill(2)
        fetch_ref = f"refs/changes/{shard}/{cl_num}/{rev_obj['_number']}"

    # Check current HEAD commit message for matching Change-Id
    try:
        head_msg = subprocess.run(
            ["git", "log", "-1", "--format=%B", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_path),
        ).stdout
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_path),
        ).stdout.strip()
        if change_id and change_id in head_msg and mode == "auto":
            return {
                "status": "success",
                "action": "already_on_change",
                "repo_path": str(repo_path),
                "head_sha": head_sha,
                "current_revision": cur_rev,
                "ref": fetch_ref,
            }
    except Exception as exc:
        logging.getLogger(__name__).debug("Ignored error in fetch_and_checkout_cl: %s", exc)

    if not fetch_ref:
        raise RuntimeError(f"Could not determine Gerrit fetch ref for CL {cl_num}.")

    remote = _detect_git_remote(repo_path, change_detail.get("project", ""))
    fetch_res = subprocess.run(
        ["git", "fetch", remote, fetch_ref],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_path),
    )
    if fetch_res.returncode != 0:
        raise RuntimeError(
            f"git fetch {remote} {fetch_ref} failed in '{repo_path}': {fetch_res.stderr.strip()}"
        )

    effective_mode = "cherry-pick" if mode in ("auto", "cherry-pick") else "checkout"
    if effective_mode == "cherry-pick":
        cp_res = subprocess.run(
            ["git", "cherry-pick", "FETCH_HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo_path),
        )
        if cp_res.returncode != 0:
            subprocess.run(
                ["git", "cherry-pick", "--abort"],
                capture_output=True,
                check=False,
                cwd=str(repo_path),
            )
            co_res = subprocess.run(
                ["git", "checkout", "-B", f"review-{cl_num}", "FETCH_HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_path),
            )
            if co_res.returncode != 0:
                raise RuntimeError(
                    f"Failed to cherry-pick or checkout {fetch_ref} in '{repo_path}': {co_res.stderr.strip()}"
                )
            effective_mode = "checkout"
    else:
        co_res = subprocess.run(
            ["git", "checkout", "-B", f"review-{cl_num}", "FETCH_HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo_path),
        )
        if co_res.returncode != 0:
            raise RuntimeError(
                f"git checkout FETCH_HEAD failed in '{repo_path}': {co_res.stderr.strip()}"
            )

    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(repo_path),
    ).stdout.strip()
    return {
        "status": "success",
        "action": effective_mode,
        "repo_path": str(repo_path),
        "head_sha": new_head,
        "current_revision": cur_rev,
        "ref": fetch_ref,
    }


def _detect_git_remote(repo_path: Path, project_name: str) -> str:
    """Resolves the Git remote name ('goog' or 'origin') or full Gerrit URL for fetching refs."""
    try:
        remotes = subprocess.run(
            ["git", "remote"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_path),
        ).stdout.splitlines()
        for preferred in ("goog", "aosp", "origin"):
            if preferred in remotes:
                return preferred
        if remotes:
            return remotes[0].strip()
    except Exception as exc:
        logging.getLogger(__name__).debug("Ignored error in _detect_git_remote: %s", exc)
    return f"https://googleplex-android.googlesource.com/{project_name}"


def upload_cl(
    repo_path: Path,
    presubmit: bool = True,
) -> dict[str, Any]:
    """Configures `push.negotiate=false` and uploads the current branch via `repo upload`."""
    subprocess.run(
        ["git", "config", "push.negotiate", "false"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_path),
    )
    cmd = ["repo", "upload", ".", "--cbr", "-y", "-o", "nokeycheck", "-o", "notify=OWNER"]
    if presubmit:
        cmd.extend(["--label", "Presubmit-Ready+1"])

    res = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_path),
    )
    combined = (res.stdout or "") + "\n" + (res.stderr or "")
    if res.returncode != 0:
        upstream_match = re.search(
            r"git branch --set-upstream-to\s+(\S+)", combined
        )
        if upstream_match:
            upstream_ref = upstream_match.group(1).strip()
            subprocess.run(
                ["git", "branch", "--set-upstream-to", upstream_ref],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_path),
            )
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_path),
            )
            combined = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0 and "--no-verify" in combined and "--no-verify" not in cmd:
        res = subprocess.run(
            [*cmd, "--no-verify"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(repo_path),
        )
        combined = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0:
        raise RuntimeError(
            f"repo upload failed in '{repo_path}' (exit {res.returncode}):\n{combined.strip()}"
        )
    return {
        "status": "success",
        "repo_path": str(repo_path),
        "presubmit": presubmit,
        "output": combined.strip(),
    }

