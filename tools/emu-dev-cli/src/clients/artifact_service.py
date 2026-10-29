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

"""Unified Android Build (go/ab) artifact service with CLI fast-path and REST v4 fallback."""

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import fnmatch
import io
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from core.errors import AuthError, ExecutionError, UsageError
from core.events import ArtifactDownloaded, EventBus
from core.process import ProcessRunner
from lib.oauth import OAuthTokenManager, get_quota_project, get_ssl_context

LOGGER = logging.getLogger(__name__)

BUILD_API_BASE_URL = "https://androidbuild-pa.googleapis.com/v4"
DEFAULT_FETCH_ARTIFACT_BIN = "/google/data/ro/projects/android/fetch_artifact"
GOOGLE_DATA_ROOT = "/google/data"
DEFAULT_QUOTA_PROJECT = "emulator-builds"
ANDROID_BUILD_SCOPE = "https://www.googleapis.com/auth/androidbuild.internal"
ANDROID_BUILD_TOKEN_ENV_VARS: tuple[str, ...] = (
    "ANDROID_BUILD_TOKEN",
    "BUGANIZER_TOKEN",
    "OAUTH2_TOKEN",
)

BUILD_INFO_ARTIFACT = "BUILD_INFO"
BUILD_TYPE_SUBMITTED = "SUBMITTED"
LATEST_BUILD_SENTINEL = "latest"
GIT_BRANCH_PREFIX = "git_"
COMPLETE_BUILD_STATUSES: frozenset[str] = frozenset({"COMPLETE", "SUCCESSFUL"})

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
AUTH_RETRY_STATUS_CODES: frozenset[int] = frozenset({
    HTTP_UNAUTHORIZED,
    HTTP_FORBIDDEN,
})

DEFAULT_REST_TIMEOUT_SEC: float = 60.0
DEFAULT_DOWNLOAD_TIMEOUT_SEC: float = 300.0
DEFAULT_FETCH_CLI_TIMEOUT_SEC: float = 180.0
DEFAULT_BUILD_PAGE_SIZE: int = 20
MIN_LIST_BUILDS_PAGE_SIZE: int = 15
LATEST_BUILD_QUERY_PAGE_SIZE: int = 5
MAX_ARTIFACTS_PAGE_SIZE: int = 1000
DOWNLOAD_CHUNK_BYTES: int = 1024 * 1024

HEADER_AUTHORIZATION = "Authorization"
HEADER_ACCEPT = "Accept"
HEADER_QUOTA_PROJECT = "X-Goog-User-Project"
HEADER_CONTENT_LENGTH = "Content-Length"
MIME_JSON = "application/json"

AUTH_REMEDIATION = (
    "Run 'gcert' on GLinux or 'gcloud auth application-default login "
    "--scopes=https://www.googleapis.com/auth/androidbuild.internal,"
    "https://www.googleapis.com/auth/cloud-platform', or set ANDROID_BUILD_TOKEN."
)


def _candidate_branches(branch: str) -> tuple[str, ...]:
    """Returns candidate Android Build branch names (`branch` and `git_<branch>`)."""
    cleaned = branch.strip()
    if not cleaned:
        raise UsageError(
            "Android Build branch name cannot be empty.",
            remediation="Provide a non-empty branch name such as 'emu-main-next' or 'studio-main'.",
        )
    if cleaned.startswith(GIT_BRANCH_PREFIX):
        return (cleaned,)
    return (cleaned, f"{GIT_BRANCH_PREFIX}{cleaned}")


def _validate_non_empty(param_name: str, value: str) -> str:
    """Validates that `value` is a non-empty string after stripping whitespace."""
    cleaned = str(value or "").strip()
    if not cleaned:
        raise UsageError(
            f"Android Build parameter '{param_name}' cannot be empty (got {value!r}).",
            remediation=f"Pass a valid non-empty value for '{param_name}'.",
        )
    return cleaned


def _extract_selected_zip_entries(
    zip_path: Path, out_dir: Path, zip_entries: Sequence[str]
) -> None:
    """Extracts the requested `zip_entries` from `zip_path` into `out_dir`."""
    with zipfile.ZipFile(zip_path, "r") as archive:
        available = set(archive.namelist())
        for entry in zip_entries:
            if entry in available:
                archive.extract(entry, path=str(out_dir))


@dataclass(frozen=True)
class BuildMetadata:
    """Immutable metadata describing an Android Build (`go/ab`) build attempt."""

    build_id: str
    target: str
    branch: str = ""
    status: str = "COMPLETE"
    successful: bool = True
    creation_timestamp: str = ""
    completion_timestamp: str = ""
    machine_name: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_dict(
        cls,
        data: Mapping[str, Any],
        *,
        default_target: str = "",
        default_branch: str = "",
    ) -> "BuildMetadata":
        """Constructs a BuildMetadata record from an Android Build v4 JSON object."""
        target_obj = data.get("target")
        target_name = (
            str(target_obj.get("name") or default_target)
            if isinstance(target_obj, Mapping)
            else str(target_obj or default_target)
        )
        return cls(
            build_id=str(data.get("buildId") or ""),
            target=target_name,
            branch=str(data.get("branch") or default_branch),
            status=str(data.get("buildAttemptStatus") or ""),
            successful=bool(data.get("successful", True)),
            creation_timestamp=str(data.get("creationTimestamp") or ""),
            completion_timestamp=str(data.get("completionTimestamp") or ""),
            machine_name=str(data.get("machineName") or ""),
            raw=dict(data),
        )


@dataclass(frozen=True)
class ArtifactMetadata:
    """Immutable metadata describing a single artifact in an Android Build."""

    name: str
    size_bytes: int = 0
    content_type: str = ""
    crc32c: str = ""

    @classmethod
    def from_api_dict(cls, data: Mapping[str, Any]) -> "ArtifactMetadata":
        """Constructs an ArtifactMetadata record from an Android Build v4 artifact entry."""
        raw_size = data.get("size", 0)
        try:
            size_val = int(raw_size)
        except (TypeError, ValueError):
            size_val = 0
        return cls(
            name=str(data.get("name") or ""),
            size_bytes=size_val,
            content_type=str(data.get("contentType") or ""),
            crc32c=str(data.get("crc32c") or ""),
        )


@dataclass(frozen=True)
class HttpResponse:
    """Structured HTTP response returned by an HttpTransport."""

    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        """Decodes the response body as a UTF-8 JSON object."""
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExecutionError(
                f"Failed to decode Android Build JSON response (status {self.status_code}): {exc}.",
                remediation="Verify that the Android Build API endpoint returned valid JSON.",
                cause=exc,
            ) from exc
        if not isinstance(parsed, dict):
            raise ExecutionError(
                f"Expected JSON object from Android Build API, got {type(parsed).__name__}.",
                remediation="Check the requested Android Build API URL and query parameters.",
            )
        return parsed


class HttpStatusError(Exception):
    """Internal transport exception carrying an HTTP error status code and body."""

    def __init__(
        self,
        status_code: int,
        reason: str,
        body_text: str = "",
        url: str = "",
    ) -> None:
        super().__init__(f"HTTP {status_code}: {reason}")
        self.status_code = int(status_code)
        self.reason = str(reason)
        self.body_text = str(body_text)
        self.url = str(url)


class HttpTransport(ABC):
    """Abstract HTTP transport seam for Android Build REST queries and downloads."""

    @abstractmethod
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_REST_TIMEOUT_SEC,
    ) -> HttpResponse:
        """Performs an HTTP GET request and returns an HttpResponse or raises HttpStatusError."""

    @abstractmethod
    def download(
        self,
        url: str,
        dest_file: Path,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        """Streams `url` into `dest_file` and returns the number of bytes written."""


class UrllibHttpTransport(HttpTransport):
    """Production HttpTransport backed by Python's stdlib urllib.request."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_REST_TIMEOUT_SEC,
    ) -> HttpResponse:
        req = urllib.request.Request(url, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(
                req, context=get_ssl_context(), timeout=timeout_sec
            ) as resp:
                body = resp.read()
                resp_headers = {str(k): str(v) for k, v in resp.headers.items()}
                return HttpResponse(
                    status_code=int(resp.status),
                    body=body,
                    headers=resp_headers,
                )
        except urllib.error.HTTPError as exc:
            body_text = _read_http_error_body(exc)
            raise HttpStatusError(
                status_code=exc.code,
                reason=str(exc.reason),
                body_text=body_text,
                url=url,
            ) from exc
        except OSError as exc:
            raise ExecutionError(
                f"Network request to Android Build API '{url}' failed: {exc}.",
                remediation="Check network connectivity and corporate proxy / SSL settings.",
                cause=exc,
            ) from exc

    def download(
        self,
        url: str,
        dest_file: Path,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(
                req, context=get_ssl_context(), timeout=timeout_sec
            ) as resp:
                total_size = int(resp.headers.get(HEADER_CONTENT_LENGTH, 0) or 0)
                return _stream_response_to_file(
                    resp, dest_file, total_size, progress_callback
                )
        except urllib.error.HTTPError as exc:
            body_text = _read_http_error_body(exc)
            raise HttpStatusError(
                status_code=exc.code,
                reason=str(exc.reason),
                body_text=body_text,
                url=url,
            ) from exc
        except OSError as exc:
            raise ExecutionError(
                f"Artifact download from '{url}' to '{dest_file}' failed: {exc}.",
                remediation="Verify disk space and network connectivity, then retry the download.",
                cause=exc,
            ) from exc


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    """Safely extracts the UTF-8 error body from an HTTPError if present."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _stream_response_to_file(
    resp: Any,
    dest_file: Path,
    total_size: int,
    progress_callback: Callable[[int, int], None] | None,
) -> int:
    """Writes response chunks to `dest_file` and invokes `progress_callback`."""
    downloaded = 0
    with dest_file.open("wb") as file_handle:
        while True:
            chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            file_handle.write(chunk)
            downloaded += len(chunk)
            if progress_callback is not None:
                progress_callback(downloaded, total_size)
    return downloaded


@dataclass(frozen=True)
class FakeHttpCall:
    """Recorded HTTP request captured by FakeHttpTransport."""

    method: str
    url: str
    headers: Mapping[str, str]
    timeout_sec: float


class FakeHttpTransport(HttpTransport):
    """Hermetic in-memory HttpTransport for testing REST retries and downloads without @patch."""

    def __init__(self) -> None:
        self.calls: list[FakeHttpCall] = []
        self._queued_get: deque[HttpResponse | HttpStatusError] = deque()
        self._url_routes: dict[str, HttpResponse | HttpStatusError] = {}
        self._downloads: dict[str, bytes | HttpStatusError] = {}
        self._default_download: bytes = b"fake-artifact-bytes"

    def queue_get_response(
        self,
        payload: Mapping[str, Any] | bytes,
        *,
        status_code: int = HTTP_OK,
    ) -> None:
        """Queues a one-shot GET response returned on the next `get()` call."""
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(dict(payload)).encode("utf-8")
        )
        self._queued_get.append(HttpResponse(status_code=status_code, body=body))

    def queue_get_error(
        self,
        status_code: int,
        reason: str = "Error",
        body_text: str = "",
    ) -> None:
        """Queues a one-shot HttpStatusError raised on the next `get()` call."""
        self._queued_get.append(
            HttpStatusError(
                status_code=status_code,
                reason=reason,
                body_text=body_text,
            )
        )

    def register_json(
        self,
        url_prefix: str,
        payload: Mapping[str, Any],
        *,
        status_code: int = HTTP_OK,
    ) -> None:
        """Registers a JSON response for any GET request whose URL starts with `url_prefix`."""
        body = json.dumps(dict(payload)).encode("utf-8")
        self._url_routes[url_prefix] = HttpResponse(
            status_code=status_code, body=body
        )

    def register_download(
        self, url_prefix: str, content: bytes | HttpStatusError
    ) -> None:
        """Registers binary content (or an error) for downloads matching `url_prefix`."""
        self._downloads[url_prefix] = content

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_REST_TIMEOUT_SEC,
    ) -> HttpResponse:
        self.calls.append(
            FakeHttpCall(
                method="GET",
                url=url,
                headers=dict(headers or {}),
                timeout_sec=timeout_sec,
            )
        )
        if self._queued_get:
            item = self._queued_get.popleft()
            if isinstance(item, HttpStatusError):
                raise item
            return item

        for prefix, route in self._url_routes.items():
            if url.startswith(prefix):
                if isinstance(route, HttpStatusError):
                    raise route
                return route

        raise HttpStatusError(
            status_code=HTTP_NOT_FOUND,
            reason="Not Found",
            body_text=f"No fake route registered for {url}",
            url=url,
        )

    def download(
        self,
        url: str,
        dest_file: Path,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_sec: float = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        self.calls.append(
            FakeHttpCall(
                method="DOWNLOAD",
                url=url,
                headers=dict(headers or {}),
                timeout_sec=timeout_sec,
            )
        )
        payload: bytes | HttpStatusError = self._default_download
        for prefix, registered in self._downloads.items():
            if url.startswith(prefix):
                payload = registered
                break
        if isinstance(payload, HttpStatusError):
            raise payload

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(payload)
        if progress_callback is not None:
            progress_callback(len(payload), len(payload))
        return len(payload)


class ArtifactService(ABC):
    """Abstract interface for querying and downloading Android Build (`go/ab`) artifacts."""

    @abstractmethod
    def resolve_build_id(
        self, branch: str, target: str, build_id: str | None = None
    ) -> str:
        """Returns `build_id` if explicit, or resolves the latest green build ID on `branch`."""

    @abstractmethod
    def get_build(self, build_id: str, target: str) -> BuildMetadata | None:
        """Retrieves metadata for a specific `(build_id, target)` pair."""

    @abstractmethod
    def list_builds(
        self,
        branch: str,
        target: str,
        *,
        limit: int = 10,
        build_type: str = BUILD_TYPE_SUBMITTED,
        include_building: bool = False,
        green_only: bool = False,
    ) -> list[BuildMetadata]:
        """Lists recent builds for `branch` and `target`."""

    @abstractmethod
    def list_artifacts(
        self,
        build_id: str,
        target: str,
        *,
        name_regex: str | None = None,
    ) -> list[ArtifactMetadata]:
        """Lists artifacts available for `build_id` and `target`."""

    @abstractmethod
    def find_matching_artifact(
        self, build_id: str, target: str, patterns: Sequence[str]
    ) -> str | None:
        """Returns the first artifact name matching one of the glob `patterns`, or None."""

    @abstractmethod
    def download_artifact(
        self,
        build_id: str,
        target: str,
        artifact: str,
        dest_dir: str | os.PathLike[str],
        *,
        branch: str | None = None,
        zip_entries: Sequence[str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Downloads `artifact` (or extracts `zip_entries`) into `dest_dir` and returns the path."""


def _default_locate_fetch_artifact(explicit_bin: str | None = None) -> str | None:
    """Locates the `fetch_artifact` binary on GLinux / PATH if available."""
    if explicit_bin is not None:
        return explicit_bin if os.path.exists(explicit_bin) else None
    if os.path.exists(DEFAULT_FETCH_ARTIFACT_BIN):
        return DEFAULT_FETCH_ARTIFACT_BIN
    return shutil.which("fetch_artifact")


class ProdArtifactService(ArtifactService):
    """Production ArtifactService combining `fetch_artifact` LOAS CLI and REST v4 fallback."""

    def __init__(
        self,
        process_runner: ProcessRunner,
        *,
        event_bus: EventBus | None = None,
        http_transport: HttpTransport | None = None,
        fetch_artifact_bin: str | None = None,
        use_fetch_artifact_cli: bool | None = None,
        token: str | None = None,
        quota_project: str | None = None,
        token_provider: Callable[[], str | None] | None = None,
        token_refresher: Callable[[], str | None] | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._event_bus = event_bus
        self._http = http_transport or UrllibHttpTransport()
        self._fetch_artifact_bin = _default_locate_fetch_artifact(fetch_artifact_bin)
        self._use_fetch_cli = (
            bool(self._fetch_artifact_bin)
            if use_fetch_artifact_cli is None
            else bool(use_fetch_artifact_cli and self._fetch_artifact_bin)
        )
        self._token_manager = OAuthTokenManager(
            scopes=[ANDROID_BUILD_SCOPE],
            env_vars=list(ANDROID_BUILD_TOKEN_ENV_VARS),
        )
        self._token = token
        self._token_provider = token_provider
        self._token_refresher = token_refresher or self._token_manager.fetch_oauth2l_token
        self._quota_project: str | None = (
            quota_project if quota_project is not None else get_quota_project()
        )

    @property
    def quota_project(self) -> str | None:
        """Returns the active quota project header value (Cleared after HTTP 403 retry)."""
        return self._quota_project

    def _ensure_token(self) -> str | None:
        if self._token:
            return self._token
        if self._token_provider is not None:
            self._token = self._token_provider()
            return self._token
        self._token = self._token_manager.get_token()
        return self._token

    def _build_headers(self, *, include_accept_json: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_accept_json:
            headers[HEADER_ACCEPT] = MIME_JSON
        token = self._ensure_token()
        if token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {token}"
        if self._quota_project:
            headers[HEADER_QUOTA_PROJECT] = self._quota_project
        return headers

    def _request_json(
        self,
        url: str,
        *,
        retried_quota: bool = False,
        retried_token: bool = False,
    ) -> dict[str, Any]:
        """Executes a GET request with automatic HTTP 403 quota-project and OAuth2 retries."""
        headers = self._build_headers(include_accept_json=True)
        try:
            response = self._http.get(
                url, headers=headers, timeout_sec=DEFAULT_REST_TIMEOUT_SEC
            )
            return response.json()
        except HttpStatusError as exc:
            if (
                exc.status_code == HTTP_FORBIDDEN
                and self._quota_project
                and not retried_quota
            ):
                LOGGER.debug(
                    "Android Build API returned HTTP 403 with quota project '%s'; retrying without %s.",
                    self._quota_project,
                    HEADER_QUOTA_PROJECT,
                )
                self._quota_project = None
                return self._request_json(
                    url, retried_quota=True, retried_token=retried_token
                )
            if exc.status_code in AUTH_RETRY_STATUS_CODES and not retried_token:
                refreshed = (
                    self._token_refresher() if self._token_refresher else None
                )
                if refreshed and refreshed != self._token:
                    self._token = refreshed
                    return self._request_json(
                        url, retried_quota=retried_quota, retried_token=True
                    )
            if exc.status_code in AUTH_RETRY_STATUS_CODES:
                raise AuthError(
                    f"Android Build API authentication failed for '{url}' "
                    f"(HTTP {exc.status_code}: {exc.reason}).",
                    remediation=AUTH_REMEDIATION,
                    details={"url": url, "http_status": exc.status_code},
                    cause=exc,
                ) from exc
            raise ExecutionError(
                f"Android Build API request to '{url}' failed with HTTP {exc.status_code} ({exc.reason}).",
                remediation="Verify the build ID, branch, and target name in go/ab.",
                details={"url": url, "http_status": exc.status_code},
                cause=exc,
            ) from exc

    def _try_resolve_latest_via_cli(
        self, branch: str, target: str
    ) -> str | None:
        if not self._use_fetch_cli or not self._fetch_artifact_bin:
            return None
        with tempfile.TemporaryDirectory(prefix="emu_ab_bid_") as tmp_dir:
            info_path = Path(tmp_dir) / BUILD_INFO_ARTIFACT
            cmd = [
                self._fetch_artifact_bin,
                "--branch",
                branch,
                "--target",
                target,
                "--latest",
                BUILD_INFO_ARTIFACT,
                tmp_dir,
            ]
            result = self._process_runner.run(
                cmd, timeout_sec=DEFAULT_FETCH_CLI_TIMEOUT_SEC, check=False
            )
            if not result.ok or not info_path.is_file():
                return None
            try:
                parsed = json.loads(info_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            bid = parsed.get("bid") if isinstance(parsed, Mapping) else None
            return str(bid) if bid else None

    def resolve_build_id(
        self, branch: str, target: str, build_id: str | None = None
    ) -> str:
        clean_target = _validate_non_empty("target", target)
        if build_id and build_id.strip() and build_id.strip().lower() != LATEST_BUILD_SENTINEL:
            return build_id.strip()
        clean_branch = _validate_non_empty("branch", branch)

        cli_bid = self._try_resolve_latest_via_cli(clean_branch, clean_target)
        if cli_bid:
            return cli_bid

        for cand_branch in _candidate_branches(clean_branch):
            params = urllib.parse.urlencode({
                "branches": cand_branch,
                "targets": clean_target,
                "buildType": BUILD_TYPE_SUBMITTED,
                "successful": "true",
                "pageSize": LATEST_BUILD_QUERY_PAGE_SIZE,
            })
            url = f"{BUILD_API_BASE_URL}/builds?{params}"
            data = self._request_json(url)
            resolved = _pick_latest_build_id(data.get("builds", []))
            if resolved:
                return resolved

        raise ExecutionError(
            f"No submitted builds found for branch '{clean_branch}' and target '{clean_target}'.",
            remediation="Verify that the branch and target names match an active Android Build target in go/ab.",
            details={"branch": clean_branch, "target": clean_target},
        )

    def get_build(self, build_id: str, target: str) -> BuildMetadata | None:
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        params = urllib.parse.urlencode({
            "buildId": clean_bid,
            "targets": clean_target,
            "pageSize": LATEST_BUILD_QUERY_PAGE_SIZE,
        })
        url = f"{BUILD_API_BASE_URL}/builds?{params}"
        data = self._request_json(url)
        raw_builds = data.get("builds", [])
        if not isinstance(raw_builds, Sequence) or not raw_builds:
            return None
        for item in raw_builds:
            if isinstance(item, Mapping) and str(item.get("buildId", "")) == clean_bid:
                return BuildMetadata.from_api_dict(item, default_target=clean_target)
        first = raw_builds[0]
        return (
            BuildMetadata.from_api_dict(first, default_target=clean_target)
            if isinstance(first, Mapping)
            else None
        )

    def list_builds(
        self,
        branch: str,
        target: str,
        *,
        limit: int = 10,
        build_type: str = BUILD_TYPE_SUBMITTED,
        include_building: bool = False,
        green_only: bool = False,
    ) -> list[BuildMetadata]:
        clean_branch = _validate_non_empty("branch", branch)
        clean_target = _validate_non_empty("target", target)
        if limit <= 0:
            raise UsageError(
                f"Build listing limit must be positive (got {limit}).",
                remediation="Pass a positive integer for limit (e.g. limit=10).",
            )

        page_size = max(limit * 2, MIN_LIST_BUILDS_PAGE_SIZE)
        for cand_branch in _candidate_branches(clean_branch):
            params = urllib.parse.urlencode({
                "branches": cand_branch,
                "targets": clean_target,
                "buildType": build_type,
                "pageSize": page_size,
            })
            url = f"{BUILD_API_BASE_URL}/builds?{params}"
            data = self._request_json(url)
            matched = _filter_builds(
                data.get("builds", []),
                default_target=clean_target,
                default_branch=cand_branch,
                limit=limit,
                include_building=include_building,
                green_only=green_only,
            )
            if matched:
                return matched
        return []

    def list_artifacts(
        self,
        build_id: str,
        target: str,
        *,
        name_regex: str | None = None,
    ) -> list[ArtifactMetadata]:
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        query: dict[str, Any] = {"pageSize": MAX_ARTIFACTS_PAGE_SIZE}
        if name_regex:
            query["nameRegexp"] = name_regex
        encoded_query = urllib.parse.urlencode(query)
        url = (
            f"{BUILD_API_BASE_URL}/builds/{clean_bid}/{clean_target}"
            f"/attempts/latest/artifacts?{encoded_query}"
        )
        data = self._request_json(url)
        raw_items = data.get("artifacts", [])
        if not isinstance(raw_items, Sequence):
            return []
        return [
            ArtifactMetadata.from_api_dict(item)
            for item in raw_items
            if isinstance(item, Mapping) and item.get("name")
        ]

    def find_matching_artifact(
        self, build_id: str, target: str, patterns: Sequence[str]
    ) -> str | None:
        if not patterns:
            raise UsageError(
                "Artifact glob pattern list cannot be empty.",
                remediation="Provide at least one artifact filename or glob pattern.",
            )
        names = [art.name for art in self.list_artifacts(build_id, target)]
        for pattern in patterns:
            for name in names:
                if fnmatch.fnmatch(name, pattern):
                    return name
        return None

    def _try_download_via_cli(
        self,
        build_id: str,
        target: str,
        artifact: str,
        out_dir: Path,
        *,
        branch: str | None = None,
        zip_entries: Sequence[str] | None = None,
    ) -> Path | None:
        if not self._use_fetch_cli or not self._fetch_artifact_bin:
            return None
        cmd = [
            self._fetch_artifact_bin,
            "--bid",
            build_id,
            "--target",
            target,
        ]
        if branch:
            cmd.extend(["--branch", branch])
        if zip_entries:
            cmd.extend(f"--zip_entry={entry}" for entry in zip_entries)
        dest_target = out_dir if zip_entries else (out_dir / artifact)
        if not zip_entries:
            dest_target.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend([artifact, str(dest_target)])
        result = self._process_runner.run(
            cmd, timeout_sec=DEFAULT_FETCH_CLI_TIMEOUT_SEC, check=False
        )
        if not result.ok:
            LOGGER.debug(
                "fetch_artifact CLI failed for %s (exit %d); falling back to REST v4.",
                artifact,
                result.returncode,
            )
            return None
        if zip_entries:
            return out_dir
        if dest_target.is_file() and dest_target.stat().st_size > 0:
            return dest_target
        return None

    def _resolve_download_url_and_headers(
        self, build_id: str, target: str, encoded_artifact: str
    ) -> tuple[str, dict[str, str]]:
        url_endpoint = (
            f"{BUILD_API_BASE_URL}/builds/{build_id}/{target}"
            f"/attempts/latest/artifacts/{encoded_artifact}/url"
        )
        try:
            url_resp = self._request_json(url_endpoint)
            signed_url = url_resp.get("signedUrl")
            if signed_url:
                return str(signed_url), {}
        except ExecutionError as exc:
            LOGGER.debug("Signed URL unavailable for %s: %s", encoded_artifact, exc)

        media_url = (
            f"{BUILD_API_BASE_URL}/builds/{build_id}/{target}"
            f"/attempts/latest/artifacts/{encoded_artifact}?alt=media"
        )
        return media_url, self._build_headers(include_accept_json=False)

    def _publish_download_event(
        self, build_id: str, target: str, artifact: str, dest_path: Path
    ) -> None:
        if self._event_bus is None:
            return
        size_bytes = dest_path.stat().st_size if dest_path.is_file() else 0
        self._event_bus.publish(
            ArtifactDownloaded(
                build_id=build_id,
                target=target,
                artifact_name=artifact,
                destination_path=str(dest_path),
                size_bytes=size_bytes,
            )
        )

    def download_artifact(
        self,
        build_id: str,
        target: str,
        artifact: str,
        dest_dir: str | os.PathLike[str],
        *,
        branch: str | None = None,
        zip_entries: Sequence[str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        clean_artifact = _validate_non_empty("artifact", artifact)
        out_dir = Path(os.fspath(dest_dir))
        out_dir.mkdir(parents=True, exist_ok=True)

        cli_path = self._try_download_via_cli(
            clean_bid,
            clean_target,
            clean_artifact,
            out_dir,
            branch=branch,
            zip_entries=zip_entries,
        )
        if cli_path is not None:
            self._publish_download_event(
                clean_bid, clean_target, clean_artifact, cli_path
            )
            return cli_path

        dest_file = out_dir / clean_artifact
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        encoded_artifact = urllib.parse.quote(clean_artifact, safe="")
        download_url, headers = self._resolve_download_url_and_headers(
            clean_bid, clean_target, encoded_artifact
        )
        try:
            self._http.download(
                download_url,
                dest_file,
                headers=headers,
                timeout_sec=DEFAULT_DOWNLOAD_TIMEOUT_SEC,
                progress_callback=progress_callback,
            )
        except HttpStatusError as exc:
            if exc.status_code in AUTH_RETRY_STATUS_CODES:
                raise AuthError(
                    f"Authentication failed while downloading artifact '{clean_artifact}' "
                    f"from build {clean_bid} ({clean_target}): HTTP {exc.status_code}.",
                    remediation=AUTH_REMEDIATION,
                    details={"build_id": clean_bid, "target": clean_target, "artifact": clean_artifact},
                    cause=exc,
                ) from exc
            raise ExecutionError(
                f"Failed to download artifact '{clean_artifact}' from build {clean_bid} "
                f"({clean_target}): HTTP {exc.status_code} ({exc.reason}).",
                remediation="Verify that the artifact name exists in the specified Android Build attempt.",
                details={"build_id": clean_bid, "target": clean_target, "artifact": clean_artifact},
                cause=exc,
            ) from exc

        if zip_entries:
            _extract_selected_zip_entries(dest_file, out_dir, zip_entries)
            self._publish_download_event(
                clean_bid, clean_target, clean_artifact, out_dir
            )
            return out_dir

        self._publish_download_event(
            clean_bid, clean_target, clean_artifact, dest_file
        )
        return dest_file


def _pick_latest_build_id(raw_builds: Any) -> str | None:
    """Selects the first completed build ID from an Android Build list response."""
    if not isinstance(raw_builds, Sequence) or not raw_builds:
        return None
    for item in raw_builds:
        if not isinstance(item, Mapping):
            continue
        bid = str(item.get("buildId") or "").strip()
        status = str(item.get("buildAttemptStatus") or "").strip()
        if bid and (status in COMPLETE_BUILD_STATUSES or not status):
            return bid
    first = raw_builds[0]
    if isinstance(first, Mapping) and first.get("buildId"):
        return str(first["buildId"])
    return None


def _filter_builds(
    raw_builds: Any,
    *,
    default_target: str,
    default_branch: str,
    limit: int,
    include_building: bool,
    green_only: bool,
) -> list[BuildMetadata]:
    """Filters raw build records by completion and green status up to `limit`."""
    if not isinstance(raw_builds, Sequence):
        return []
    filtered: list[BuildMetadata] = []
    for item in raw_builds:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("buildAttemptStatus") or "")
        is_complete = status in COMPLETE_BUILD_STATUSES and bool(
            item.get("completionTimestamp")
        )
        if not include_building and not is_complete:
            continue
        if green_only and not item.get("successful", False):
            continue
        filtered.append(
            BuildMetadata.from_api_dict(
                item,
                default_target=default_target,
                default_branch=default_branch,
            )
        )
        if len(filtered) >= limit:
            break
    return filtered


class FakeArtifactService(ArtifactService):
    """Hermetic in-memory ArtifactService for unit testing commands and workflows."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._builds: list[BuildMetadata] = []
        self._artifacts: dict[tuple[str, str], dict[str, bytes]] = {}
        self.downloads: list[tuple[str, str, str, Path]] = []

    def seed_build(self, build: BuildMetadata) -> None:
        """Seeds a BuildMetadata record into the fake service."""
        self._builds.append(build)

    def seed_artifact(
        self,
        build_id: str,
        target: str,
        artifact_name: str,
        content: bytes | str = b"fake-content",
        *,
        zip_entries: Mapping[str, bytes | str] | None = None,
    ) -> None:
        """Seeds an artifact payload (or synthesized zip archive) for `(build_id, target)`."""
        if zip_entries is not None:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for entry_name, entry_data in zip_entries.items():
                    raw = (
                        entry_data.encode("utf-8")
                        if isinstance(entry_data, str)
                        else bytes(entry_data)
                    )
                    zf.writestr(entry_name, raw)
            payload = buf.getvalue()
        elif isinstance(content, str):
            payload = content.encode("utf-8")
        else:
            payload = bytes(content)

        bucket = self._artifacts.setdefault((str(build_id), str(target)), {})
        bucket[artifact_name] = payload

    def resolve_build_id(
        self, branch: str, target: str, build_id: str | None = None
    ) -> str:
        clean_target = _validate_non_empty("target", target)
        if build_id and build_id.strip() and build_id.strip().lower() != LATEST_BUILD_SENTINEL:
            return build_id.strip()
        clean_branch = _validate_non_empty("branch", branch)
        candidates = set(_candidate_branches(clean_branch))
        for build in self._builds:
            if build.target == clean_target and (
                not build.branch or build.branch in candidates
            ):
                return build.build_id
        raise ExecutionError(
            f"No fake builds seeded for branch '{clean_branch}' and target '{clean_target}'.",
            remediation="Call fake_artifact_service.seed_build(...) in your unit test setup.",
        )

    def get_build(self, build_id: str, target: str) -> BuildMetadata | None:
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        for build in self._builds:
            if build.build_id == clean_bid and build.target == clean_target:
                return build
        return None

    def list_builds(
        self,
        branch: str,
        target: str,
        *,
        limit: int = 10,
        build_type: str = BUILD_TYPE_SUBMITTED,
        include_building: bool = False,
        green_only: bool = False,
    ) -> list[BuildMetadata]:
        del build_type
        clean_branch = _validate_non_empty("branch", branch)
        clean_target = _validate_non_empty("target", target)
        candidates = set(_candidate_branches(clean_branch))
        results: list[BuildMetadata] = []
        for build in self._builds:
            if build.target != clean_target:
                continue
            if build.branch and build.branch not in candidates:
                continue
            if not include_building and build.status not in COMPLETE_BUILD_STATUSES:
                continue
            if green_only and not build.successful:
                continue
            results.append(build)
            if len(results) >= limit:
                break
        return results

    def list_artifacts(
        self,
        build_id: str,
        target: str,
        *,
        name_regex: str | None = None,
    ) -> list[ArtifactMetadata]:
        del name_regex
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        bucket = self._artifacts.get((clean_bid, clean_target), {})
        return [
            ArtifactMetadata(name=name, size_bytes=len(payload))
            for name, payload in sorted(bucket.items())
        ]

    def find_matching_artifact(
        self, build_id: str, target: str, patterns: Sequence[str]
    ) -> str | None:
        if not patterns:
            raise UsageError(
                "Artifact glob pattern list cannot be empty.",
                remediation="Provide at least one artifact filename or glob pattern.",
            )
        names = [art.name for art in self.list_artifacts(build_id, target)]
        for pattern in patterns:
            for name in names:
                if fnmatch.fnmatch(name, pattern):
                    return name
        return None

    def download_artifact(
        self,
        build_id: str,
        target: str,
        artifact: str,
        dest_dir: str | os.PathLike[str],
        *,
        branch: str | None = None,
        zip_entries: Sequence[str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        del branch
        clean_bid = _validate_non_empty("build_id", build_id)
        clean_target = _validate_non_empty("target", target)
        clean_artifact = _validate_non_empty("artifact", artifact)
        bucket = self._artifacts.get((clean_bid, clean_target), {})
        if clean_artifact not in bucket:
            raise ExecutionError(
                f"Artifact '{clean_artifact}' not found in fake build {clean_bid} ({clean_target}).",
                remediation="Seed the artifact via fake_artifact_service.seed_artifact(...) before downloading.",
            )

        payload = bucket[clean_artifact]
        out_dir = Path(os.fspath(dest_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        dest_file = out_dir / clean_artifact
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(payload)
        if progress_callback is not None:
            progress_callback(len(payload), len(payload))

        result_path = dest_file
        if zip_entries:
            _extract_selected_zip_entries(dest_file, out_dir, zip_entries)
            result_path = out_dir

        self.downloads.append((clean_bid, clean_target, clean_artifact, result_path))
        if self._event_bus is not None:
            self._event_bus.publish(
                ArtifactDownloaded(
                    build_id=clean_bid,
                    target=clean_target,
                    artifact_name=clean_artifact,
                    destination_path=str(result_path),
                    size_bytes=len(payload),
                )
            )
        return result_path
