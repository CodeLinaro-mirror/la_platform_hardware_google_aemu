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

"""Unit tests for `lib.corp_sso`, `lib.screencast`, and `commands.screencast`."""

import argparse
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from commands import screencast as screencast_cmd
from lib import corp_sso
from lib import screencast as screencast_lib


class ScreencastLibTest(unittest.TestCase):
    """Tests for cross-platform Screencast upload and corporate SSO helpers."""

    def test_validate_video_file_supported_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for ext, expected_mime in (
                (".mp4", "video/mp4"),
                (".webm", "video/webm"),
                (".mov", "video/quicktime"),
            ):
                p = Path(tmpdir) / f"sample{ext}"
                p.write_bytes(b"fake-video-bytes")
                abs_path, mime_type, size_bytes = screencast_lib.validate_video_file(
                    str(p)
                )
                self.assertEqual(abs_path, os.path.abspath(str(p)))
                self.assertEqual(mime_type, expected_mime)
                self.assertEqual(size_bytes, len(b"fake-video-bytes"))

    def test_validate_video_file_rejects_missing_empty_and_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                screencast_lib.validate_video_file(os.path.join(tmpdir, "missing.mp4"))

            empty_file = Path(tmpdir) / "empty.mp4"
            empty_file.write_bytes(b"")
            with self.assertRaises(ValueError):
                screencast_lib.validate_video_file(str(empty_file))

            bad_ext = Path(tmpdir) / "notes.txt"
            bad_ext.write_text("hello", encoding="utf-8")
            with self.assertRaises(ValueError):
                screencast_lib.validate_video_file(str(bad_ext))

            with self.assertRaises(ValueError):
                screencast_lib.validate_video_file(tmpdir)

    def test_redact_sensitive_text_scrubs_tokens_headers_and_cookies(self) -> None:
        raw = (
            "POST https://screencast.googleplex.com/_ah/upload/AMmfu6ZSecretToken123/?upload_id=up_secret_9 "
            "--headers=X-XSRF-Token: xsrf_hdr_secret|X-CSRFToken: csrf_hdr_secret|Authorization: Bearer bearer_secret "
            "Cookie: SACSID=secret_sacsid_val; ACSID=secret_acsid_val; HSID=secret_hsid; "
            "__Secure-GSSO_UberProxy=secret_up; __Host-GAPS=secret_host_gaps; "
            "SSO=master_ticket&upxsrf=xsrf_secret&token=tok_secret"
        )
        redacted = screencast_lib.redact_sensitive_text(raw)
        self.assertNotIn("AMmfu6ZSecretToken123", redacted)
        self.assertNotIn("up_secret_9", redacted)
        self.assertNotIn("xsrf_hdr_secret", redacted)
        self.assertNotIn("csrf_hdr_secret", redacted)
        self.assertNotIn("bearer_secret", redacted)
        self.assertNotIn("secret_sacsid_val", redacted)
        self.assertNotIn("secret_acsid_val", redacted)
        self.assertNotIn("secret_hsid", redacted)
        self.assertNotIn("secret_up", redacted)
        self.assertNotIn("secret_host_gaps", redacted)
        self.assertNotIn("master_ticket", redacted)
        self.assertNotIn("xsrf_secret", redacted)
        self.assertNotIn("tok_secret", redacted)
        self.assertIn("/_ah/upload/<redacted-token>", redacted)

    def test_parse_prepare_upload_and_cast_id(self) -> None:
        prep_raw = (
            ")]}'\n"
            '{"response": {"upload_url": "https://screencast.googleplex.com/_ah/upload/tok1"}}'
        )
        self.assertEqual(
            screencast_lib._parse_prepare_upload_response(prep_raw),
            "https://screencast.googleplex.com/_ah/upload/tok1",
        )
        self.assertIsNone(
            screencast_lib._parse_prepare_upload_response(
                "<html>Redirecting to login.corp.google.com</html>"
            )
        )

        up_raw = ")]}'\n" '{"response": {"id": "abc123XYZ"}}'
        self.assertEqual(screencast_lib.parse_upload_cast_id(up_raw), "abc123XYZ")
        self.assertIsNone(
            screencast_lib.parse_upload_cast_id(
                "<html>accounts.google.com/ServiceLogin</html>"
            )
        )

    def test_write_multipart_body_streams_fields_and_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "clip.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            body_file = os.path.join(tmpdir, "body.bin")

            total_bytes = screencast_lib.write_multipart_body(
                body_file=body_file,
                title="My Clip",
                description="Test run",
                video_path=str(video),
                mime_type="video/mp4",
                boundary="TestBoundary99",
            )
            payload = Path(body_file).read_bytes()
            self.assertEqual(total_bytes, len(payload))
            self.assertIn(b'name="title"\r\n\r\nMy Clip\r\n', payload)
            self.assertIn(b'name="description"\r\n\r\nTest run\r\n', payload)
            self.assertIn(b'name="encoded_form"', payload)
            self.assertIn(b'name="cast"; filename="clip.mp4"', payload)
            self.assertIn(
                b"Content-Type: video/mp4\r\n\r\n\x00\x00\x00\x18ftypmp42", payload
            )
            self.assertTrue(payload.endswith(b"\r\n--TestBoundary99--\r\n"))

    def test_unicode_paths_and_metadata_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            unicode_dir = Path(tmpdir) / "目录_测试_🚀_é"
            unicode_dir.mkdir()
            video = unicode_dir / "测试_🚀_é.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

            abs_path, mime_type, size_bytes = screencast_lib.validate_video_file(
                str(video)
            )
            self.assertEqual(abs_path, os.path.abspath(str(video)))
            self.assertEqual(mime_type, "video/mp4")
            self.assertEqual(size_bytes, 12)

            body_file = os.path.join(tmpdir, "body_utf8.bin")
            screencast_lib.write_multipart_body(
                body_file=body_file,
                title="标题_测试_🚀_é",
                description="描述_测试_🚀_é",
                video_path=abs_path,
                mime_type=mime_type,
                boundary="TestBoundaryUTF8",
            )
            payload = Path(body_file).read_bytes()
            self.assertIn(
                'name="title"\r\n\r\n标题_测试_🚀_é\r\n'.encode("utf-8"), payload
            )
            self.assertIn(
                'filename="测试_🚀_é.mp4"'.encode("utf-8"), payload
            )

    def test_find_gosso_and_soecks_cross_platform(self) -> None:
        with patch.dict(os.environ, {"SCREENCAST_GOSSO_BIN": ""}, clear=False):
            with (
                patch("platform.system", return_value="Windows"),
                patch("shutil.which", return_value=None),
                patch(
                    "lib.corp_sso._is_usable_binary",
                    side_effect=lambda p: p == r"C:\Program Files\Google\gosso\gosso.exe",
                ),
            ):
                self.assertEqual(
                    screencast_lib.find_gosso_binary(),
                    r"C:\Program Files\Google\gosso\gosso.exe",
                )
                self.assertIsNone(screencast_lib.find_uplink_soecks_binary())

            with (
                patch("platform.system", return_value="Linux"),
                patch("shutil.which", return_value=None),
                patch(
                    "lib.corp_sso._is_usable_binary",
                    side_effect=lambda p: p == "/google/bin/releases/gosso/gosso",
                ),
            ):
                self.assertEqual(
                    screencast_lib.find_gosso_binary(),
                    "/google/bin/releases/gosso/gosso",
                )

            with (
                patch("platform.system", return_value="Darwin"),
                patch("shutil.which", return_value=None),
                patch(
                    "lib.corp_sso._is_usable_binary",
                    side_effect=lambda p: p == "/usr/local/bin/gosso",
                ),
            ):
                self.assertEqual(
                    screencast_lib.find_gosso_binary(),
                    "/usr/local/bin/gosso",
                )

    def test_soecks_handshake_bounds_and_malformed_http_headers(self) -> None:
        self.assertEqual(corp_sso._encode_varint(127), b"\x7f")
        self.assertEqual(corp_sso._encode_varint(200), b"\xc8\x01")

        read_fd, write_fd = os.pipe()
        try:
            self.assertIsNone(corp_sso._read_soecks_socket_path(read_fd, timeout_sec=0.02))
        finally:
            os.close(read_fd)
            os.close(write_fd)

        client_sock, server_sock = socket.socketpair()
        try:
            # Oversized handshake length must be rejected without allocating huge buffer.
            server_sock.sendall(struct.pack("<I", 10_000_000))
            err = corp_sso._soecks_handshake(client_sock, "screencast.googleplex.com")
            self.assertIsNotNone(err)
            assert err is not None
            self.assertIn("invalid handshake length", err)
        finally:
            client_sock.close()
            server_sock.close()

        client_sock, server_sock = socket.socketpair()
        try:
            # Empty HTTP header block (\r\n\r\n...) must not raise IndexError.
            server_sock.sendall(b"\r\n\r\nbody")
            server_sock.shutdown(socket.SHUT_WR)
            ok, _body, diag = corp_sso._exchange_raw_http_post(
                client_sock,
                "screencast.googleplex.com",
                "/upload",
                {},
                "",
                b"payload",
            )
            self.assertFalse(ok)
            self.assertIn("Malformed HTTP response", diag)
        finally:
            client_sock.close()
            server_sock.close()

    def test_cleanup_soecks_process_closes_pipes_and_kills_on_timeout(self) -> None:
        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd=["uplink-soecks"], timeout=2),
            0,
        ]
        corp_sso._cleanup_soecks_process(fake_proc, None)
        fake_proc.stdin.close.assert_called_once()
        fake_proc.stdout.close.assert_called_once()
        fake_proc.kill.assert_called_once()

    def test_prime_screencast_cookies_surfaces_redacted_gosso_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            jar_path = os.path.join(tmpdir, "cookies.json")
            failed_proc = subprocess.CompletedProcess(
                args=["gosso"],
                returncode=1,
                stdout="",
                stderr="LOAS ticket expired; SACSID=secret_leak",
            )
            with (
                patch("lib.screencast.run_gosso", return_value=failed_proc),
                patch("platform.system", return_value="Darwin"),
                self.assertRaises(RuntimeError) as cm,
            ):
                screencast_lib.prime_screencast_cookies(
                    "/usr/local/bin/gosso", jar_path, {}
                )
            msg = str(cm.exception)
            self.assertIn("exit 1: LOAS ticket expired", msg)
            self.assertNotIn("secret_leak", msg)
            self.assertIn("gcert", msg)

    def test_upload_screencast_falls_back_with_fresh_upload_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "demo.mp4"
            video.write_bytes(b"mp4-content")

            upload_urls = iter(
                [
                    "https://screencast.googleplex.com/_ah/upload/token_first",
                    "https://screencast.googleplex.com/_ah/upload/token_second",
                ]
            )
            seen_urls: list[str] = []

            def fake_soecks(_bin, url, _body, _headers, _cookies):
                seen_urls.append(url)
                return False, "", "uplink-soecks socket unavailable"

            def fake_gosso_upload(_bin, url, _body, _headers, _jar, _env):
                seen_urls.append(url)
                return True, ")]}'\n" '{"response": {"id": "cast_987"}}', ""

            with (
                patch(
                    "lib.screencast.find_gosso_binary",
                    return_value="/usr/local/bin/gosso",
                ),
                patch(
                    "lib.screencast.find_uplink_soecks_binary",
                    return_value="/usr/local/bin/uplink-soecks",
                ),
                patch("lib.screencast.find_sso_client_binary", return_value=None),
                patch(
                    "lib.screencast.prime_screencast_cookies",
                    return_value={"SACSID": "s1", "__Secure-GSSO_UberProxy": "u1"},
                ),
                patch(
                    "lib.screencast.fetch_prepare_upload_url",
                    side_effect=lambda *_a, **_kw: next(upload_urls),
                ),
                patch(
                    "lib.screencast._upload_via_uplink_soecks", side_effect=fake_soecks
                ),
                patch(
                    "lib.screencast._upload_via_gosso", side_effect=fake_gosso_upload
                ),
            ):
                res = screencast_lib.upload_screencast(
                    str(video), title="Demo Run", description="Desc"
                )

            self.assertEqual(res.cast_id, "cast_987")
            self.assertEqual(
                res.url, "https://screencast.googleplex.com/cast/cast_987"
            )
            self.assertEqual(res.transport, "gosso")
            self.assertEqual(
                seen_urls,
                [
                    "https://screencast.googleplex.com/_ah/upload/token_first",
                    "https://screencast.googleplex.com/_ah/upload/token_second",
                ],
            )


class ScreencastCommandTest(unittest.TestCase):
    """Tests CLI argument parsing and JSON/human output for `emu-dev-cli screencast`."""

    def _build_parser(self) -> argparse.ArgumentParser:
        root = argparse.ArgumentParser(prog="emu-dev-cli")
        root.add_argument("--json", action="store_true")
        sub = root.add_subparsers(dest="subcommand")
        screencast_cmd.register_parser(sub)
        return root

    def test_parser_preserves_root_and_subcommand_json_flag(self) -> None:
        parser = self._build_parser()
        args_before = parser.parse_args(["--json", "screencast", "video.mp4"])
        self.assertTrue(args_before.json)
        self.assertEqual(args_before.video_file, "video.mp4")

        args_after = parser.parse_args(
            ["screencast", "video.mp4", "--title", "T", "--json"]
        )
        self.assertTrue(args_after.json)
        self.assertEqual(args_after.title, "T")

    def test_run_screencast_cmd_json_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "测试_🚀_é.mp4"
            video.write_bytes(b"video-data")
            parser = self._build_parser()
            args = parser.parse_args(["--json", "screencast", str(video)])

            fake_res = screencast_lib.ScreencastUploadResult(
                url="https://screencast.googleplex.com/cast/id_42",
                cast_id="id_42",
                title="测试_🚀_é.mp4",
                description="",
                source_file=str(video),
                mime_type="video/mp4",
                size_bytes=10,
                transport="uplink-soecks",
            )
            stdout_buf = io.StringIO()
            with (
                patch("commands.screencast.ensure_utf8_stdio") as mock_utf8,
                patch("commands.screencast.upload_screencast", return_value=fake_res),
                patch("sys.stdout", stdout_buf),
            ):
                res = args._command_instance.execute(args)

            mock_utf8.assert_called_once()
            self.assertEqual(res.exit_code, 0)
            payload = json.loads(stdout_buf.getvalue())
            self.assertEqual(payload["status"], "success")
            self.assertEqual(
                payload["url"], "https://screencast.googleplex.com/cast/id_42"
            )
            self.assertEqual(payload["id"], "id_42")


if __name__ == "__main__":
    unittest.main()
