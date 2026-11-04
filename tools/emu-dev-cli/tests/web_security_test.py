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

"""Unit tests for the local web server checks in `lib.web_security` and `lib.security`."""

from http import HTTPStatus
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from lib import web_security
from lib.security import resolve_within_roots
from lib.web_security import (
    MAX_JSON_BODY_BYTES,
    RequestRejected,
    allowed_cors_origin,
    mint_ui_token,
    read_json_body,
    require_ui_token,
)

_TOKEN = "test-ui-token"
_HINT = "Open the UI from its launch URL."


def _headers(**fields: str) -> http.client.HTTPMessage:
    """Builds case-insensitive request headers like the ones http.server parses."""
    message = http.client.HTTPMessage()
    for name, value in fields.items():
        message[name.replace("_", "-")] = value
    return message


class RequireUiTokenTest(unittest.TestCase):

    def assert_rejected(self, expected_token: str, headers: http.client.HTTPMessage, query: str = "") -> str:
        with self.assertRaises(RequestRejected) as ctx:
            require_ui_token(expected_token, headers, query, _HINT)
        self.assertEqual(ctx.exception.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn(_HINT, str(ctx.exception))
        return str(ctx.exception)

    def test_unset_token_fails_closed(self):
        message = self.assert_rejected("", _headers(X_Sidecar_Token=""), query="token=")
        self.assertIn("no UI token configured", message)
        self.assert_rejected("", _headers(X_Sidecar_Token="anything"), query="token=anything")

    def test_missing_or_wrong_token_is_rejected(self):
        self.assert_rejected(_TOKEN, _headers())
        self.assert_rejected(_TOKEN, _headers(X_Sidecar_Token="wrong"))
        self.assert_rejected(_TOKEN, _headers(), query="token=wrong")
        self.assert_rejected(_TOKEN, _headers(X_Sidecar_Token=_TOKEN + "x"))
        self.assert_rejected(_TOKEN, _headers(X_Sidecar_Token="tökén_🚀"))

    def test_header_or_query_token_is_accepted(self):
        require_ui_token(_TOKEN, _headers(X_Sidecar_Token=_TOKEN), "", _HINT)
        require_ui_token(_TOKEN, _headers(), f"token={_TOKEN}", _HINT)
        require_ui_token(_TOKEN, _headers(X_Sidecar_Token="wrong"), f"viewer=a&token={_TOKEN}", _HINT)

    def test_uses_constant_time_comparison(self):
        with mock.patch.object(
            web_security.hmac, "compare_digest", wraps=web_security.hmac.compare_digest
        ) as compare_digest:
            require_ui_token(_TOKEN, _headers(X_Sidecar_Token=_TOKEN), "", _HINT)
        compare_digest.assert_called_with(_TOKEN.encode("utf-8"), _TOKEN.encode("utf-8"))

    def test_minted_tokens_are_random_and_url_safe(self):
        first, second = mint_ui_token(), mint_ui_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)
        self.assertRegex(first, r"^[A-Za-z0-9_-]+$")


class AllowedCorsOriginTest(unittest.TestCase):

    def test_loopback_origins_are_allowed(self):
        for origin in (
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "https://localhost",
            "http://[::1]:8085",
        ):
            with self.subTest(origin=origin):
                self.assertEqual(allowed_cors_origin(origin), origin)

    def test_other_origins_are_rejected(self):
        for origin in (
            None,
            "",
            "*",
            "null",
            "file://",
            "https://evil.example.com",
            "http://127.0.0.1.evil.example.com",
            "http://localhost.evil.example.com:8080",
            "ftp://localhost",
            "http://[::1",
        ):
            with self.subTest(origin=origin):
                self.assertIsNone(allowed_cors_origin(origin))


class ReadJsonBodyTest(unittest.TestCase):

    def read(self, body: bytes, max_bytes: int = MAX_JSON_BODY_BYTES, **fields: str):
        headers = {"Content_Type": "application/json", "Content_Length": str(len(body))}
        headers.update(fields)
        return read_json_body(_headers(**headers), io.BytesIO(body), max_bytes=max_bytes)

    def assert_status(self, status: HTTPStatus, body: bytes = b"{}", **fields: str) -> None:
        with self.assertRaises(RequestRejected) as ctx:
            self.read(body, **fields)
        self.assertEqual(ctx.exception.status, status)

    def test_parses_json_object_with_unicode(self):
        payload = {"message": "测试_🚀_é", "cls": [1, 2]}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.read(body, Content_Type="application/json; charset=utf-8"), payload)

    def test_rejects_non_json_content_types_with_415(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            with self.subTest(content_type=content_type):
                self.assert_status(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, Content_Type=content_type)

    def test_rejects_missing_or_malformed_length(self):
        headers = _headers(Content_Type="application/json")
        with self.assertRaises(RequestRejected) as ctx:
            read_json_body(headers, io.BytesIO(b"{}"))
        self.assertEqual(ctx.exception.status, HTTPStatus.LENGTH_REQUIRED)
        self.assert_status(HTTPStatus.BAD_REQUEST, Content_Length="-1")
        self.assert_status(HTTPStatus.BAD_REQUEST, Content_Length="ten")

    def test_rejects_oversized_body_without_reading_it(self):
        rfile = mock.Mock(spec=io.BytesIO)
        headers = _headers(Content_Type="application/json", Content_Length=str(MAX_JSON_BODY_BYTES + 1))
        with self.assertRaises(RequestRejected) as ctx:
            read_json_body(headers, rfile)
        self.assertEqual(ctx.exception.status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertIn(str(MAX_JSON_BODY_BYTES), str(ctx.exception))
        rfile.read.assert_not_called()

    def test_rejects_bodies_that_are_not_json_objects(self):
        for body in (b"", b"   ", b"not json", b"[1, 2]", b'"text"', b"\xff\xfe{}", b"[" * 100000):
            with self.subTest(body=body):
                self.assert_status(HTTPStatus.BAD_REQUEST, body=body, Content_Length=str(len(body)))


class ResolveWithinRootsTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="web_security_test_")).resolve()
        self.root = self.temp_dir / "root"
        self.root.mkdir()
        self.outside = self.temp_dir / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("secret", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_resolved_path_inside_root(self):
        artifact = self.root / "测试_🚀_é" / "report.md"
        artifact.parent.mkdir()
        artifact.write_text("ok", encoding="utf-8")
        self.assertEqual(resolve_within_roots(str(artifact), [self.root]), artifact)
        self.assertEqual(resolve_within_roots(self.root / "missing.txt", [self.root]), self.root / "missing.txt")

    def test_blocks_traversal_and_outside_paths(self):
        for raw_path in (
            f"{self.root}{os.sep}..{os.sep}outside{os.sep}secret.txt",
            str(self.secret),
            str(self.temp_dir),
            "/etc/passwd",
            "relative/../../../etc/passwd",
            "bad\x00path",
        ):
            with self.subTest(raw_path=raw_path):
                self.assertIsNone(resolve_within_roots(raw_path, [self.root]))

    def test_blocks_symlink_that_points_outside(self):
        link = self.root / "link.txt"
        try:
            link.symlink_to(self.secret)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")
        self.assertIsNone(resolve_within_roots(link, [self.root]))

    def test_checks_every_root(self):
        self.assertEqual(resolve_within_roots(self.secret, [self.root, self.outside]), self.secret)


if __name__ == "__main__":
    unittest.main()
