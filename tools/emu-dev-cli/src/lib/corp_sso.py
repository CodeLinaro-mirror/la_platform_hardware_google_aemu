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

"""Cross-platform corporate SSO/BeyondCorp discovery, cookie jars, and HTTP transports.

Provides reusable `gosso`, `uplink-soecks`, `sso_client`, and `urllib` helpers
across gLinux, gMac, and gWindows without leaking credentials or cookies in
diagnostics.
"""

from __future__ import annotations

from collections.abc import Iterator
import http.cookiejar
import json
import os
import platform
import re
import selectors
import shutil
import socket
import struct
import subprocess
import time
from typing import Any, BinaryIO
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_AUTH_TIMEOUT_SEC = 45
DEFAULT_UPLOAD_TIMEOUT_SEC = 300
SSO_CLIENT_PROCESS_SLACK_SEC = 15
PROC_TERMINATE_TIMEOUT_SEC = 2
SOECKS_CONNECT_HEADER_LEN = 4
MAX_SOECKS_HANDSHAKE_LEN = 4096
SOECKS_PATH_READ_BYTES = 256
SOECKS_HANDSHAKE_OK_PREFIX = b"\x08\x00"
SOECKS_RECV_CHUNK_BYTES = 65536
VARINT_PAYLOAD_MASK = 0x7F
VARINT_CONT_BIT = 0x80
VARINT_SHIFT_BITS = 7
HTTP_OK_MIN = 200
HTTP_OK_MAX = 299
HTTPS_DEFAULT_PORT = 443
PRIVATE_FILE_MODE = 0o600
MAX_DIAG_CHARS = 160
DEFAULT_USER_AGENT = "emu-dev-cli-screencast/1.0"

CORP_NO_PROXY_DOMAINS: tuple[str, ...] = (
    "screencast.googleplex.com",
    "googleplex.com",
    ".googleplex.com",
    "corp.google.com",
    ".corp.google.com",
    "google.com",
    ".google.com",
    "accounts.google.com",
    "appengine.google.com",
    "uc.appengine.google.com",
    "localhost",
    "127.0.0.1",
)

PROXY_ENV_KEYS: tuple[str, ...] = (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "grpc_proxy",
    "GRPC_PROXY",
    "ftp_proxy",
    "FTP_PROXY",
)

GOSSO_SHARED_COOKIE_DOMAINS: frozenset[str] = frozenset(
    {"googleplex.com", "google.com", "corp.google.com"}
)

POSIX_GOSSO_CANDIDATES: tuple[str, ...] = (
    "/usr/local/bin/gosso",
    "/opt/homebrew/bin/gosso",
    "/usr/bin/gosso",
    "/usr/local/google/gosso/gosso",
    "/google/bin/releases/gosso/gosso",
)

POSIX_SOECKS_CANDIDATES: tuple[str, ...] = (
    "/usr/local/bin/uplink-soecks",
    "/usr/bin/uplink-soecks",
)

SKIP_RAW_HTTP_HEADER_KEYS: frozenset[str] = frozenset(
    {"content-length", "connection", "host", "user-agent", "cookie"}
)


def redact_sensitive_text(text: str) -> str:
    """Scrubs upload tokens, cookies, auth headers, and XSRF secrets from diagnostics."""
    if not text:
        return ""
    scrubbed = re.sub(
        r"/_ah/upload/[^/?#\s\"']+", "/_ah/upload/<redacted-token>", text
    )
    scrubbed = re.sub(
        r"(?i)(upload_id|access_token|auth_token)=[^&#\s\"']+",
        r"\1=<redacted>",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(?i)Authorization\s*:\s*(?:[A-Za-z]+\s+)?[^\s|;&\"']+",
        "Authorization: <redacted>",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(?i)(X-XSRF-Token|X-CSRFToken)\s*:\s*[^|;&\s\"']+",
        r"\1: <redacted>",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(?i)((?:__Secure-|__Host-)[A-Za-z0-9_.-]+|[A-Z0-9_]*SID|SSO|GSSO|upxsrf|[A-Za-z0-9_-]*token)=[^|;&\s\"']+",
        r"\1=<redacted>",
        scrubbed,
    )
    return scrubbed


def get_platform_auth_hint(system_name: str | None = None) -> str:
    """Returns OS-specific installation and authentication guidance for `gosso`."""
    sys_lower = (system_name or platform.system()).lower()
    if sys_lower == "darwin":
        return (
            "On gMac: install gosso via `sudo mule install gosso` and refresh "
            "BeyondCorp credentials via `gcert` or `uplink-helper login`."
        )
    if sys_lower == "windows":
        return (
            "On gWindows: install gosso via `googet install gosso` "
            "(C:\\Program Files\\Google\\gosso\\gosso.exe) and refresh SSO via `gcert`."
        )
    return (
        "On gLinux: ensure `/google/bin/releases/gosso/gosso` is accessible "
        "(or run `sudo apt install gosso`) and refresh SSO via `gcert`."
    )


def _is_usable_binary(path: str | None) -> bool:
    """Returns True if `path` points to an existing executable file."""
    if not path or not os.path.isfile(path):
        return False
    if platform.system().lower() == "windows":
        return True
    return os.access(path, os.X_OK)


def find_gosso_binary() -> str | None:
    """Locates the `gosso` CLI binary across gLinux, gMac, and gWindows."""
    override = os.environ.get("SCREENCAST_GOSSO_BIN", "").strip()
    if _is_usable_binary(override):
        return override

    for binary_name in ("gosso", "gosso.exe"):
        found = shutil.which(binary_name)
        if _is_usable_binary(found):
            return found

    if platform.system().lower() == "windows":
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        win_candidates = [
            os.path.join(prog_files, "Google", "gosso", "gosso.exe"),
            r"C:\Program Files\Google\gosso\gosso.exe",
        ]
        if local_app_data:
            win_candidates.append(
                os.path.join(local_app_data, "Google", "gosso", "gosso.exe")
            )
        return next((c for c in win_candidates if _is_usable_binary(c)), None)

    return next((c for c in POSIX_GOSSO_CANDIDATES if _is_usable_binary(c)), None)


def find_uplink_soecks_binary() -> str | None:
    """Locates BeyondCorp `uplink-soecks` on POSIX hosts supporting AF_UNIX."""
    if platform.system().lower() == "windows" or not hasattr(socket, "AF_UNIX"):
        return None
    found = shutil.which("uplink-soecks")
    if _is_usable_binary(found):
        return found
    return next((c for c in POSIX_SOECKS_CANDIDATES if _is_usable_binary(c)), None)


def find_sso_client_binary() -> str | None:
    """Locates `sso_client` on gLinux if available."""
    found = shutil.which("sso_client")
    if _is_usable_binary(found):
        return found
    if _is_usable_binary("/usr/bin/sso_client"):
        return "/usr/bin/sso_client"
    return None


def build_corp_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Creates a clean subprocess environment with internal domains bypassed in NO_PROXY."""
    env = dict(base_env if base_env is not None else os.environ)
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)

    for key in ("no_proxy", "NO_PROXY"):
        existing = env.get(key, "")
        parts = [p.strip() for p in existing.split(",") if p.strip()]
        missing = [d for d in CORP_NO_PROXY_DOMAINS if d not in parts]
        env[key] = ",".join(parts + missing)
    return env


def set_private_file_mode(filepath: str) -> None:
    """Restricts file permissions to owner read/write (0600) on POSIX systems."""
    if platform.system().lower() == "windows":
        return
    try:
        os.chmod(filepath, PRIVATE_FILE_MODE)
    except OSError:
        pass


def _read_gosso_jar_entries(jar_path: str) -> list[Any]:
    """Reads top-level JSON list entries from a `gosso` cookie jar file."""
    if not jar_path or not os.path.isfile(jar_path):
        return []
    try:
        with open(jar_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _iter_gosso_cookie_dicts(jar_path: str) -> Iterator[dict[str, Any]]:
    """Yields raw cookie dicts from a `gosso` JSON cookie-jar file."""
    for entry in _read_gosso_jar_entries(jar_path):
        if not isinstance(entry, dict):
            continue
        raw_cookies = entry.get("Cookies") or []
        for cookie in raw_cookies:
            if isinstance(cookie, dict):
                yield cookie


def load_gosso_cookie_map(jar_paths: list[str]) -> dict[str, str]:
    """Extracts `{cookie_name: cookie_value}` from one or more gosso JSON cookie jars."""
    merged: dict[str, str] = {}
    for jar_path in jar_paths:
        for cookie in _iter_gosso_cookie_dicts(jar_path):
            name = str(cookie.get("Name", "")).strip()
            value = str(cookie.get("Value", "")).strip()
            if name and value:
                merged[name] = value
    return merged


def load_gosso_cookie_jar(
    jar_path: str, default_domain: str = "screencast.googleplex.com"
) -> http.cookiejar.CookieJar:
    """Loads a gosso JSON cookie file into a standard library `CookieJar`."""
    cookie_jar = http.cookiejar.CookieJar()
    for item in _iter_gosso_cookie_dicts(jar_path):
        name = item.get("Name")
        value = item.get("Value")
        if not name or value is None:
            continue
        domain = str(item.get("Domain") or default_domain)
        if not domain.startswith(".") and domain in GOSSO_SHARED_COOKIE_DOMAINS:
            domain = "." + domain
        cookie_jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=str(name),
                value=str(value),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=str(item.get("Path") or "/"),
                path_specified=True,
                secure=bool(item.get("Secure", True)),
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": None},
                rfc2109=False,
            )
        )
    return cookie_jar


def save_cookie_jar_to_gosso_json(
    cookie_jar: http.cookiejar.CookieJar, jar_path: str
) -> None:
    """Persists a Python `CookieJar` back to gosso's JSON jar format."""
    cookies_list: list[dict[str, Any]] = []
    for cookie in cookie_jar:
        domain = cookie.domain
        if domain.startswith(".") and domain[1:] in GOSSO_SHARED_COOKIE_DOMAINS:
            domain = domain[1:]
        cookies_list.append(
            {
                "Name": cookie.name,
                "Value": cookie.value,
                "Domain": domain,
                "Path": cookie.path,
                "Secure": cookie.secure,
            }
        )
    with open(jar_path, "w", encoding="utf-8") as fh:
        json.dump([{"Cookies": cookies_list}], fh, indent=2)
    set_private_file_mode(jar_path)


def _build_cookie_opener(
    cookie_jar_path: str,
) -> tuple[http.cookiejar.CookieJar, urllib.request.OpenerDirector]:
    """Builds a `urllib` opener backed by `cookie_jar_path`."""
    py_jar = load_gosso_cookie_jar(cookie_jar_path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(py_jar))
    opener.addheaders = [("User-Agent", DEFAULT_USER_AGENT)]
    return py_jar, opener


def fetch_via_urllib(
    url: str, cookie_jar_path: str, timeout_sec: int
) -> str | None:
    """Performs an HTTP GET with cookies from `cookie_jar_path` and saves updated cookies."""
    py_jar, opener = _build_cookie_opener(cookie_jar_path)
    try:
        with opener.open(url, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        save_cookie_jar_to_gosso_json(py_jar, cookie_jar_path)
        return body
    except Exception:
        return None


def _build_gosso_cmd(
    gosso_bin: str,
    url: str,
    cookie_jar_path: str,
    method: str,
    headers: dict[str, str] | None,
    data_file: str | None,
    use_loas: bool,
    enable_pac: bool,
) -> list[str]:
    """Builds the CLI argument list for a single `gosso` invocation."""
    cmd = [gosso_bin, "-url", url, "-method", method, "-cert=true"]
    cmd.append("-loas=true" if use_loas else "-fullticket=true")
    if not enable_pac:
        cmd.append("-pac=false")
    if cookie_jar_path:
        if os.path.isfile(cookie_jar_path):
            cmd.extend(["-cookie", cookie_jar_path])
        cmd.extend(["-cookie-jar", cookie_jar_path])
    if headers:
        for key, val in headers.items():
            cmd.extend(["-header", f"{key}: {val}"])
    if data_file:
        cmd.extend(["-data_file", data_file])
    return cmd


def run_gosso(
    gosso_bin: str,
    url: str,
    env: dict[str, str],
    cookie_jar_path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data_file: str | None = None,
    timeout_sec: int = DEFAULT_AUTH_TIMEOUT_SEC,
    use_loas: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Executes `gosso` with automatic `-pac=false` fallback if PAC lookup fails."""
    res: subprocess.CompletedProcess[str] | None = None
    for enable_pac in (True, False):
        res = subprocess.run(
            _build_gosso_cmd(
                gosso_bin,
                url,
                cookie_jar_path,
                method,
                headers,
                data_file,
                use_loas,
                enable_pac,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=timeout_sec,
        )
        if cookie_jar_path and os.path.isfile(cookie_jar_path):
            set_private_file_mode(cookie_jar_path)
        if res.returncode == 0:
            return res
    assert res is not None
    return res


def _encode_varint(value: int) -> bytes:
    """Encodes a non-negative integer as an unsigned protobuf base-128 varint."""
    out = bytearray()
    while value > VARINT_PAYLOAD_MASK:
        out.append((value & VARINT_PAYLOAD_MASK) | VARINT_CONT_BIT)
        value >>= VARINT_SHIFT_BITS
    out.append(value)
    return bytes(out)


def _recv_exact(sock: socket.socket, num_bytes: int) -> bytes | None:
    """Reads `num_bytes` from `sock`, returning `None` if EOF occurs early."""
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(num_bytes - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_soecks_socket_path(
    stdout_fd: int, timeout_sec: float = DEFAULT_AUTH_TIMEOUT_SEC
) -> str | None:
    """Reads the newline-terminated UNIX socket path from `uplink-soecks` with a timeout."""
    deadline = time.monotonic() + timeout_sec
    buf = bytearray()
    with selectors.DefaultSelector() as sel:
        sel.register(stdout_fd, selectors.EVENT_READ)
        while len(buf) < MAX_SOECKS_HANDSHAKE_LEN:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not sel.select(remaining):
                return None
            chunk = os.read(stdout_fd, SOECKS_PATH_READ_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in chunk:
                break
    return buf.partition(b"\n")[0].decode("utf-8", errors="replace").strip()


def _soecks_handshake(
    sock: socket.socket, host: str, port: int = HTTPS_DEFAULT_PORT
) -> str | None:
    """Sends a binary `ConnectRequest` over an `uplink-soecks` socket; returns error or None."""
    host_bytes = host.encode("utf-8")
    hostport = f"{host}:{port}".encode("utf-8")
    connect_req = (
        b"\x0a"
        + _encode_varint(len(hostport))
        + hostport
        + b"\x1a"
        + _encode_varint(len(host_bytes))
        + host_bytes
        + b"\x28\x01\x30\x01"
    )
    sock.sendall(struct.pack("<I", len(connect_req)) + connect_req)

    len_bytes = _recv_exact(sock, SOECKS_CONNECT_HEADER_LEN)
    if len_bytes is None:
        return "uplink-soecks handshake header truncated"
    resp_len = struct.unpack("<I", len_bytes)[0]
    if resp_len == 0 or resp_len > MAX_SOECKS_HANDSHAKE_LEN:
        return f"uplink-soecks invalid handshake length ({resp_len} bytes)"
    handshake = _recv_exact(sock, resp_len)
    if not handshake or not handshake.startswith(SOECKS_HANDSHAKE_OK_PREFIX):
        return "uplink-soecks ConnectResponse rejected"
    return None


def _exchange_raw_http_post(
    sock: socket.socket,
    host: str,
    path: str,
    headers: dict[str, str],
    cookie_header: str,
    body: bytes | BinaryIO,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[bool, str, str]:
    """Streams a raw HTTP/1.1 POST over `sock` and parses `(ok, body_text, status_diag)`."""
    content_len = (
        len(body) if isinstance(body, bytes) else os.fstat(body.fileno()).st_size
    )
    http_lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {host}",
        f"User-Agent: {user_agent}",
        f"Content-Length: {content_len}",
        "Connection: close",
    ]
    if cookie_header:
        http_lines.append(f"Cookie: {cookie_header}")
    for key, val in headers.items():
        if key.lower() not in SKIP_RAW_HTTP_HEADER_KEYS:
            http_lines.append(f"{key}: {val}")

    sock.sendall(("\r\n".join(http_lines) + "\r\n\r\n").encode("utf-8"))
    if isinstance(body, bytes):
        sock.sendall(body)
    else:
        while chunk := body.read(SOECKS_RECV_CHUNK_BYTES):
            sock.sendall(chunk)

    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(SOECKS_RECV_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    raw_resp = b"".join(chunks)
    if b"\r\n\r\n" not in raw_resp:
        return False, "", "Malformed HTTP response from uplink-soecks"
    head_bytes, resp_body = raw_resp.split(b"\r\n\r\n", 1)
    header_lines = head_bytes.decode("utf-8", errors="replace").splitlines()
    if not header_lines:
        return False, "", "Malformed HTTP response from uplink-soecks (empty headers)"
    parts = header_lines[0].split(" ", 2)
    status_code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return (
        HTTP_OK_MIN <= status_code <= HTTP_OK_MAX,
        resp_body.decode("utf-8", errors="replace"),
        f"HTTP {status_code}",
    )


def _cleanup_soecks_process(
    proc: subprocess.Popen[bytes], sock: socket.socket | None
) -> None:
    """Closes socket/pipe descriptors and terminates an `uplink-soecks` coprocess."""
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
    try:
        proc.terminate()
        proc.wait(timeout=PROC_TERMINATE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except OSError:
        pass


def upload_via_uplink_soecks(
    soecks_bin: str,
    upload_url: str,
    body_file: str,
    headers: dict[str, str],
    cookie_header: str,
) -> tuple[bool, str, str]:
    """Streams multipart payload via BeyondCorp `uplink-soecks` UNIX socket coprocess."""
    parsed = urllib.parse.urlparse(upload_url)
    host = parsed.netloc
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    proc = subprocess.Popen(
        [soecks_bin],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    sock: socket.socket | None = None
    try:
        assert proc.stdout is not None
        sock_path = _read_soecks_socket_path(proc.stdout.fileno())
        if sock_path is None:
            return False, "", "uplink-soecks timed out waiting for socket path"
        if not sock_path or not os.path.exists(sock_path):
            return False, "", "uplink-soecks did not return a socket path"

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DEFAULT_UPLOAD_TIMEOUT_SEC)
        sock.connect(sock_path)

        handshake_err = _soecks_handshake(sock, host)
        if handshake_err:
            return False, "", handshake_err
        with open(body_file, "rb") as body_fh:
            return _exchange_raw_http_post(
                sock, host, path, headers, cookie_header, body_fh
            )
    except (OSError, ValueError) as exc:
        return False, "", f"uplink-soecks error: {redact_sensitive_text(str(exc))}"
    finally:
        _cleanup_soecks_process(proc, sock)


def upload_via_sso_client(
    sso_client_bin: str,
    upload_url: str,
    body_file: str,
    headers: dict[str, str],
    cookie_header: str,
    tmpdir: str,
    env: dict[str, str],
) -> tuple[bool, str, str]:
    """Uploads multipart payload via `sso_client` using a mode-0600 flagfile for cookies."""
    flags_file = os.path.join(tmpdir, "sso_flags.cfg")
    with open(flags_file, "w", encoding="utf-8") as fh:
        if cookie_header:
            fh.write(f"--cookie={cookie_header}\n")
    set_private_file_mode(flags_file)

    header_items = [
        f"{k}: {v}" for k, v in headers.items() if k.lower() != "content-length"
    ]
    cmd = [
        sso_client_bin,
        f"--flagfile={flags_file}",
        "--method=POST",
        "--location",
        "--header_sep=|",
        f"--headers={'|'.join(header_items)}",
        f"--url={upload_url}",
        f"--data_file={body_file}",
        f"--request_timeout={DEFAULT_UPLOAD_TIMEOUT_SEC}",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=DEFAULT_UPLOAD_TIMEOUT_SEC + SSO_CLIENT_PROCESS_SLACK_SEC,
        )
        return (
            res.returncode == 0,
            res.stdout or "",
            redact_sensitive_text((res.stderr or "")[:MAX_DIAG_CHARS]),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", f"sso_client error: {redact_sensitive_text(str(exc))}"
    finally:
        try:
            if os.path.exists(flags_file):
                os.remove(flags_file)
        except OSError:
            pass


def upload_via_gosso(
    gosso_bin: str,
    upload_url: str,
    body_file: str,
    headers: dict[str, str],
    cookie_jar_path: str,
    env: dict[str, str],
) -> tuple[bool, str, str]:
    """Uploads multipart payload via cross-platform `gosso` (gWindows, gMac, gLinux)."""
    try:
        res = run_gosso(
            gosso_bin,
            upload_url,
            env,
            cookie_jar_path,
            method="POST",
            headers=headers,
            data_file=body_file,
            timeout_sec=DEFAULT_UPLOAD_TIMEOUT_SEC,
        )
        return (
            res.returncode == 0,
            res.stdout or "",
            redact_sensitive_text((res.stderr or "")[:MAX_DIAG_CHARS]),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", f"gosso upload error: {redact_sensitive_text(str(exc))}"


def upload_via_urllib(
    upload_url: str,
    body_file: str,
    headers: dict[str, str],
    cookie_jar_path: str,
) -> tuple[bool, str, str]:
    """Streams multipart payload directly via Python `urllib` with primed `CookieJar`."""
    _py_jar, opener = _build_cookie_opener(cookie_jar_path)
    req_headers = dict(headers)
    try:
        with open(body_file, "rb") as fh:
            if not any(k.lower() == "content-length" for k in req_headers):
                req_headers["Content-Length"] = str(os.fstat(fh.fileno()).st_size)
            req = urllib.request.Request(
                upload_url, data=fh, headers=req_headers, method="POST"
            )
            with opener.open(req, timeout=DEFAULT_UPLOAD_TIMEOUT_SEC) as resp:
                status = getattr(resp, "status", HTTP_OK_MIN)
                out = resp.read().decode("utf-8", errors="replace")
                return HTTP_OK_MIN <= status <= HTTP_OK_MAX, out, f"HTTP {status}"
    except urllib.error.HTTPError as err:
        return False, "", f"HTTP {err.code}"
    except Exception as exc:
        return False, "", f"urllib error: {redact_sensitive_text(str(exc))}"


def inspect_transports() -> dict[str, Any]:
    """Returns discovered authentication and upload transport binaries on the current host."""
    gosso_bin = find_gosso_binary()
    soecks_bin = find_uplink_soecks_binary()
    sso_client_bin = find_sso_client_binary()
    available_transports: list[str] = []
    if soecks_bin:
        available_transports.append("uplink-soecks")
    if sso_client_bin:
        available_transports.append("sso_client")
    if gosso_bin:
        available_transports.append("gosso")
    available_transports.append("urllib")
    return {
        "platform": platform.system().lower(),
        "gosso_bin": gosso_bin,
        "uplink_soecks_bin": soecks_bin,
        "sso_client_bin": sso_client_bin,
        "transports": available_transports,
    }
