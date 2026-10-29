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

"""Cross-platform client for uploading videos to screencast.googleplex.com.

Supports gLinux, gMac, and gWindows by priming App Engine (`SACSID`) and
BeyondCorp (`__Secure-GSSO_UberProxy`) cookies via `gosso` (go/gosso-tool) and
uploading multipart payloads over the best available host transport
(`uplink-soecks`, `sso_client`, `gosso`, or Python `urllib`).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import platform
import shutil
import subprocess
import tempfile
from typing import Any
import urllib.parse

from lib.corp_sso import (
    DEFAULT_AUTH_TIMEOUT_SEC,
    build_corp_env,
    fetch_via_urllib,
    find_gosso_binary,
    find_sso_client_binary,
    find_uplink_soecks_binary,
    get_platform_auth_hint,
    inspect_transports,
    load_gosso_cookie_jar,
    load_gosso_cookie_map,
    redact_sensitive_text,
    run_gosso,
    save_cookie_jar_to_gosso_json,
    set_private_file_mode,
    upload_via_gosso as _upload_via_gosso,
    upload_via_sso_client as _upload_via_sso_client,
    upload_via_uplink_soecks as _upload_via_uplink_soecks,
    upload_via_urllib as _upload_via_urllib,
)
from lib.gerrit_api import strip_xssi_prefix

SCREENCAST_BASE_URL = "https://screencast.googleplex.com"
PREPARE_UPLOAD_URL = f"{SCREENCAST_BASE_URL}/api/cast/prepare_upload"
ACCESS_LANDING_URL = f"{SCREENCAST_BASE_URL}/access_landing"
CONFLOGIN_UPLOAD_URL = (
    f"{SCREENCAST_BASE_URL}/_ah/conflogin?continue={SCREENCAST_BASE_URL}/api/cast/upload"
)

MULTIPART_BOUNDARY = "ScreencastUploadBoundary12345"
COPY_CHUNK_BYTES = 1024 * 1024
CONFLOGIN_TIMEOUT_SEC = 15
PREPARE_UPLOAD_URLLIB_TIMEOUT_SEC = 20
LOAS_PRIME_TIMEOUT_SEC = 20
SSO_CLIENT_PREPARE_TIMEOUT_SEC = 30
PRIVATE_DIR_MODE = 0o700
MAX_ERROR_SNIPPET_CHARS = 200
MAX_OUT_SNIPPET_CHARS = 120

SUPPORTED_VIDEO_MIMES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

XSRF_COOKIE_NAMES: frozenset[str] = frozenset(
    {"xsrf-token", "xsrftoken", "csrf-token", "csrftoken"}
)

SSO_INTERCEPT_MARKERS: tuple[str, ...] = (
    "sso-authentication-errors",
    "login.corp.google.com",
    "Single Sign On",
    "/_ah/conflogin",
    "accounts.google.com",
)


@dataclass(frozen=True)
class ScreencastUploadResult:
    """Metadata returned after uploading a video to Screencast."""

    url: str
    cast_id: str
    title: str
    description: str
    source_file: str
    mime_type: str
    size_bytes: int
    transport: str


def detect_video_mime(video_path: str) -> str:
    """Validates a local video file extension and returns its MIME type."""
    ext = os.path.splitext(video_path)[1].lower()
    mime_type = SUPPORTED_VIDEO_MIMES.get(ext)
    if not mime_type:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_MIMES.keys()))
        raise ValueError(
            f"Unsupported video extension '{ext or '<none>'}' for '{video_path}'. "
            f"Provide a file in one of the supported formats: {supported}"
        )
    return mime_type


def validate_video_file(video_path: str) -> tuple[str, str, int]:
    """Resolves `video_path` and returns `(abs_path, mime_type, size_bytes)`."""
    abs_path = os.path.abspath(os.path.expanduser(video_path))
    if not os.path.exists(abs_path):
        raise FileNotFoundError(
            f"Video file not found at '{abs_path}'. Verify the path exists and retry."
        )
    if not os.path.isfile(abs_path):
        raise ValueError(
            f"Video path '{abs_path}' is not a regular file. "
            "Pass a .mp4, .webm, or .mov video file."
        )
    size_bytes = os.path.getsize(abs_path)
    if size_bytes <= 0:
        raise ValueError(
            f"Video file '{abs_path}' is empty (0 bytes). "
            "Provide a non-empty .mp4, .webm, or .mov recording."
        )
    mime_type = detect_video_mime(abs_path)
    return abs_path, mime_type, size_bytes


def _refresh_linux_loas_cookie(
    gosso_bin: str,
    cookie_jar_path: str,
    env: dict[str, str],
    cookies: dict[str, str],
) -> None:
    """Refreshes `__Secure-GSSO_UberProxy` via LOAS on gLinux when available."""
    if platform.system().lower() != "linux":
        return
    loas_jar = f"{cookie_jar_path}.loas"
    try:
        run_gosso(
            gosso_bin,
            PREPARE_UPLOAD_URL,
            env,
            loas_jar,
            timeout_sec=LOAS_PRIME_TIMEOUT_SEC,
            use_loas=True,
        )
        loas_cookies = load_gosso_cookie_map([loas_jar])
        uber_cookie = loas_cookies.get("__Secure-GSSO_UberProxy")
        if uber_cookie:
            cookies["__Secure-GSSO_UberProxy"] = uber_cookie
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        try:
            if os.path.exists(loas_jar):
                os.remove(loas_jar)
        except OSError:
            pass


def _summarize_gosso_failures(
    gosso_runs: list[subprocess.CompletedProcess[str]],
) -> str:
    """Formats the most recent non-zero or stderr-emitting `gosso` run for errors."""
    for res in reversed(gosso_runs):
        snippet = redact_sensitive_text(
            (res.stderr or res.stdout or "").strip()[:MAX_ERROR_SNIPPET_CHARS]
        )
        if res.returncode != 0 or snippet:
            detail = f": {snippet}" if snippet else ""
            return f"exit {res.returncode}{detail}"
    return "cookie jar remained empty"


def prime_screencast_cookies(
    gosso_bin: str, cookie_jar_path: str, env: dict[str, str]
) -> dict[str, str]:
    """Primes Screencast BeyondCorp and App Engine `SACSID` cookies into `cookie_jar_path`."""
    try:
        gosso_runs = [
            run_gosso(gosso_bin, f"{SCREENCAST_BASE_URL}/", env, cookie_jar_path),
            run_gosso(gosso_bin, ACCESS_LANDING_URL, env, cookie_jar_path),
        ]

        cookies = load_gosso_cookie_map([cookie_jar_path])
        if cookies and "SACSID" not in cookies:
            fetch_via_urllib(
                CONFLOGIN_UPLOAD_URL, cookie_jar_path, CONFLOGIN_TIMEOUT_SEC
            )
            cookies = load_gosso_cookie_map([cookie_jar_path])

        if "SACSID" not in cookies:
            gosso_runs.append(
                run_gosso(gosso_bin, CONFLOGIN_UPLOAD_URL, env, cookie_jar_path)
            )
            cookies = load_gosso_cookie_map([cookie_jar_path])
    except (OSError, subprocess.SubprocessError) as exc:
        reason = redact_sensitive_text(str(exc))
        raise RuntimeError(
            f"Unable to prime corporate SSO cookies via `gosso` ({reason}). "
            f"{get_platform_auth_hint()}"
        ) from exc

    _refresh_linux_loas_cookie(gosso_bin, cookie_jar_path, env, cookies)
    if not cookies:
        failure_reason = _summarize_gosso_failures(gosso_runs)
        raise RuntimeError(
            f"Unable to prime corporate SSO cookies via `gosso` ({failure_reason}). "
            f"{get_platform_auth_hint()}"
        )
    return cookies


def _try_prepare_via_gosso(
    gosso_bin: str, cookie_jar_path: str, env: dict[str, str]
) -> tuple[str | None, str]:
    """Attempts `/api/cast/prepare_upload` via `gosso`, returning `(url, failure_diag)`."""
    try:
        res = run_gosso(gosso_bin, PREPARE_UPLOAD_URL, env, cookie_jar_path)
        url = _parse_prepare_upload_response(res.stdout or "")
        if url:
            return url, ""
        snip = redact_sensitive_text(
            (res.stderr or res.stdout or "").strip()[:MAX_ERROR_SNIPPET_CHARS]
        )
        detail = f": {snip}" if snip else ""
        return None, f"gosso exit {res.returncode}{detail}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gosso: {redact_sensitive_text(str(exc))}"


def _try_prepare_via_sso_client(
    sso_client_bin: str, env: dict[str, str]
) -> tuple[str | None, str]:
    """Attempts `/api/cast/prepare_upload` via `sso_client`, returning `(url, failure_diag)`."""
    try:
        res = subprocess.run(
            [
                sso_client_bin,
                "--url",
                PREPARE_UPLOAD_URL,
                "--use_master_cookie",
                "--location",
                "--request_timeout",
                str(SSO_CLIENT_PREPARE_TIMEOUT_SEC),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=DEFAULT_AUTH_TIMEOUT_SEC,
        )
        url = _parse_prepare_upload_response(res.stdout or "")
        if url:
            return url, ""
        snip = redact_sensitive_text(
            (res.stderr or res.stdout or "").strip()[:MAX_ERROR_SNIPPET_CHARS]
        )
        detail = f": {snip}" if snip else ""
        return None, f"sso_client exit {res.returncode}{detail}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"sso_client: {redact_sensitive_text(str(exc))}"


def fetch_prepare_upload_url(
    gosso_bin: str,
    cookie_jar_path: str,
    env: dict[str, str],
    sso_client_bin: str | None = None,
) -> str:
    """Requests a fresh single-use Blobstore upload URL from `/api/cast/prepare_upload`."""
    raw_urllib = fetch_via_urllib(
        PREPARE_UPLOAD_URL, cookie_jar_path, PREPARE_UPLOAD_URLLIB_TIMEOUT_SEC
    )
    url = _parse_prepare_upload_response(raw_urllib or "")
    if url:
        return url

    failures: list[str] = []
    gosso_url, gosso_err = _try_prepare_via_gosso(gosso_bin, cookie_jar_path, env)
    if gosso_url:
        return gosso_url
    failures.append(gosso_err)

    if sso_client_bin:
        sso_url, sso_err = _try_prepare_via_sso_client(sso_client_bin, env)
        if sso_url:
            return sso_url
        failures.append(sso_err)

    joined_failures = "; ".join(f for f in failures if f)
    raise RuntimeError(
        f"Failed to obtain upload URL from '{PREPARE_UPLOAD_URL}' "
        f"({joined_failures}). {get_platform_auth_hint()}"
    )


def _parse_screencast_json(raw_text: str) -> dict[str, Any] | None:
    """Strips XSSI prefix and parses a non-SSO-intercepted Screencast JSON object."""
    if not raw_text or any(marker in raw_text for marker in SSO_INTERCEPT_MARKERS):
        return None
    cleaned = strip_xssi_prefix(raw_text)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_prepare_upload_response(raw_text: str) -> str | None:
    """Extracts `response.upload_url` from a `prepare_upload` payload, or None."""
    parsed = _parse_screencast_json(raw_text)
    if not parsed:
        return None
    resp = parsed.get("response")
    if not isinstance(resp, dict):
        return None
    upload_url = resp.get("upload_url")
    if isinstance(upload_url, str) and upload_url.strip():
        return upload_url.strip()
    return None


def parse_upload_cast_id(raw_text: str) -> str | None:
    """Extracts the newly created cast ID from a Screencast upload response."""
    parsed = _parse_screencast_json(raw_text)
    if not parsed:
        return None
    resp = parsed.get("response")
    if isinstance(resp, dict):
        cast_id = resp.get("id")
        if isinstance(cast_id, str) and cast_id.strip():
            return cast_id.strip()
    top_id = parsed.get("id")
    if isinstance(top_id, str) and top_id.strip():
        return top_id.strip()
    return None


def write_multipart_body(
    body_file: str,
    title: str,
    description: str,
    video_path: str,
    mime_type: str,
    boundary: str = MULTIPART_BOUNDARY,
) -> int:
    """Streams a Screencast `multipart/form-data` payload to `body_file` and returns byte size."""
    encoded_form = urllib.parse.quote(
        json.dumps({"title": title, "description": description})
    )
    filename = os.path.basename(video_path)
    with open(body_file, "wb") as out:
        for field_name, field_value in (
            ("title", title),
            ("description", description),
            ("encoded_form", encoded_form),
        ):
            out.write(f"--{boundary}\r\n".encode("utf-8"))
            out.write(
                f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode(
                    "utf-8"
                )
            )
            out.write(f"{field_value}\r\n".encode("utf-8"))

        out.write(f"--{boundary}\r\n".encode("utf-8"))
        out.write(
            f'Content-Disposition: form-data; name="cast"; filename="{filename}"\r\n'.encode(
                "utf-8"
            )
        )
        out.write(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        with open(video_path, "rb") as video_fh:
            shutil.copyfileobj(video_fh, out, COPY_CHUNK_BYTES)
        out.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    set_private_file_mode(body_file)
    return os.path.getsize(body_file)


def build_upload_headers(
    boundary: str, cookies: dict[str, str], content_length: int | None = None
) -> dict[str, str]:
    """Constructs standard Screencast multipart upload headers."""
    headers: dict[str, str] = {
        "Origin": SCREENCAST_BASE_URL,
        "Referer": f"{SCREENCAST_BASE_URL}/",
        "X-Same-Domain": "1",
        "X-Requested-With": "XMLHttpRequest",
        "X-Goog-AuthUser": "0",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    for name, val in cookies.items():
        if name.lower() in XSRF_COOKIE_NAMES and val:
            headers["X-XSRF-Token"] = val
            headers["X-CSRFToken"] = val
            break
    return headers


def _build_transport_uploaders(
    *,
    soecks_bin: str | None,
    sso_client_bin: str | None,
    gosso_bin: str,
    body_file: str,
    headers: dict[str, str],
    cookie_header: str,
    cookie_jar_path: str,
    tmpdir: str,
    env: dict[str, str],
) -> list[tuple[str, Any]]:
    """Returns ordered `(transport_name, upload_fn)` fallbacks for the current host."""
    attempts: list[tuple[str, Any]] = []
    if soecks_bin:
        attempts.append(
            (
                "uplink-soecks",
                lambda url: _upload_via_uplink_soecks(
                    soecks_bin, url, body_file, headers, cookie_header
                ),
            )
        )
    if sso_client_bin:
        attempts.append(
            (
                "sso_client",
                lambda url: _upload_via_sso_client(
                    sso_client_bin, url, body_file, headers, cookie_header, tmpdir, env
                ),
            )
        )
    attempts.append(
        (
            "gosso",
            lambda url: _upload_via_gosso(
                gosso_bin, url, body_file, headers, cookie_jar_path, env
            ),
        )
    )
    attempts.append(
        (
            "urllib",
            lambda url: _upload_via_urllib(url, body_file, headers, cookie_jar_path),
        )
    )
    return attempts


def _execute_upload_attempts(
    attempts: list[tuple[str, Any]],
    gosso_bin: str,
    cookie_jar_path: str,
    env: dict[str, str],
    sso_client_bin: str | None,
) -> tuple[str, str]:
    """Tries each transport with a fresh `upload_url` and returns `(cast_id, transport_name)`."""
    errors: list[str] = []
    for transport_name, uploader in attempts:
        upload_url = fetch_prepare_upload_url(
            gosso_bin, cookie_jar_path, env, sso_client_bin=sso_client_bin
        )
        ok, out_text, diag = uploader(upload_url)
        cast_id = parse_upload_cast_id(out_text) if ok or out_text else None
        if cast_id:
            return cast_id, transport_name
        snippet = " ".join(out_text.split())[:MAX_OUT_SNIPPET_CHARS]
        raw_diag = f"{diag}: {snippet}" if diag and snippet else (diag or snippet)
        sanitized_diag = redact_sensitive_text(raw_diag)
        errors.append(f"{transport_name}: {sanitized_diag or 'no cast ID returned'}")

    joined_errors = "; ".join(errors)
    raise RuntimeError(
        f"Screencast upload failed across all transports ({joined_errors}). "
        f"{get_platform_auth_hint()}"
    )


def upload_screencast(
    video_path: str,
    title: str | None = None,
    description: str = "",
) -> ScreencastUploadResult:
    """Validates and uploads a local video file to `screencast.googleplex.com`."""
    abs_path, mime_type, size_bytes = validate_video_file(video_path)
    resolved_title = (title or "").strip() or os.path.basename(abs_path)

    gosso_bin = find_gosso_binary()
    if not gosso_bin:
        raise RuntimeError(
            "Required corporate authentication tool `gosso` was not found. "
            f"{get_platform_auth_hint()}"
        )

    env = build_corp_env()
    soecks_bin = find_uplink_soecks_binary()
    sso_client_bin = find_sso_client_binary()

    with tempfile.TemporaryDirectory(prefix="emu_screencast_") as tmpdir:
        if platform.system().lower() != "windows":
            os.chmod(tmpdir, PRIVATE_DIR_MODE)
        cookie_jar_path = os.path.join(tmpdir, "cookies.json")
        body_file = os.path.join(tmpdir, "body.bin")

        cookies = prime_screencast_cookies(gosso_bin, cookie_jar_path, env)
        if not cookies:
            raise RuntimeError(
                "Unable to prime corporate SSO cookies via `gosso`. "
                f"{get_platform_auth_hint()}"
            )

        body_len = write_multipart_body(
            body_file=body_file,
            title=resolved_title,
            description=description,
            video_path=abs_path,
            mime_type=mime_type,
        )
        headers = build_upload_headers(
            MULTIPART_BOUNDARY, cookies, content_length=body_len
        )
        attempts = _build_transport_uploaders(
            soecks_bin=soecks_bin,
            sso_client_bin=sso_client_bin,
            gosso_bin=gosso_bin,
            body_file=body_file,
            headers=headers,
            cookie_header="; ".join(f"{k}={v}" for k, v in cookies.items()),
            cookie_jar_path=cookie_jar_path,
            tmpdir=tmpdir,
            env=env,
        )
        cast_id, transport_name = _execute_upload_attempts(
            attempts, gosso_bin, cookie_jar_path, env, sso_client_bin
        )
        return ScreencastUploadResult(
            url=f"{SCREENCAST_BASE_URL}/cast/{cast_id}",
            cast_id=cast_id,
            title=resolved_title,
            description=description,
            source_file=abs_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            transport=transport_name,
        )
