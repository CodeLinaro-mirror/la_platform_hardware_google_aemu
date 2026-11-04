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

"""Hermetic unit tests for clients.artifact_service (zero @patch decorators)."""

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.artifact_service import (
    BUILD_API_BASE_URL,
    BuildMetadata,
    FakeArtifactService,
    FakeHttpTransport,
    HEADER_AUTHORIZATION,
    HEADER_QUOTA_PROJECT,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_UNAUTHORIZED,
    HttpStatusError,
    ProdArtifactService,
)
from core.errors import AuthError, ExecutionError, UsageError
from core.events import ArtifactDownloaded, EventBus
from core.process import FakeProcessRunner, ProcessResult


def _make_zip_bytes(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
            archive.writestr(name, raw)
    return buf.getvalue()


class ArtifactServiceTest(unittest.TestCase):
    """Tests ProdArtifactService and FakeArtifactService without mocks or network calls."""

    def test_resolve_build_id_explicit_and_rest_git_branch_fallback(self) -> None:
        runner = FakeProcessRunner()
        http = FakeHttpTransport()
        svc = ProdArtifactService(
            runner,
            http_transport=http,
            use_fetch_artifact_cli=False,
            token="initial-token",
            quota_project="emulator-builds",
        )

        # Explicit build ID bypasses network
        self.assertEqual(
            svc.resolve_build_id("emu-main-next", "emulator-linux_x64", "15000001"),
            "15000001",
        )
        self.assertEqual(len(http.calls), 0)

        # 'latest' queries 'emu-main-next' (empty) then falls back to 'git_emu-main-next'
        http.queue_get_response({"builds": []})
        http.queue_get_response(
            {
                "builds": [
                    {"buildId": "15000999", "buildAttemptStatus": "COMPLETE"},
                ]
            }
        )
        resolved = svc.resolve_build_id(
            "emu-main-next", "emulator-linux_x64", "latest"
        )
        self.assertEqual(resolved, "15000999")
        self.assertEqual(len(http.calls), 2)
        self.assertIn("branches=emu-main-next", http.calls[0].url)
        self.assertIn("branches=git_emu-main-next", http.calls[1].url)

    def test_http_403_retries_without_quota_project_then_refreshes_token(self) -> None:
        runner = FakeProcessRunner()
        http = FakeHttpTransport()
        refreshed_tokens = ["refreshed-oauth-token"]

        svc = ProdArtifactService(
            runner,
            http_transport=http,
            use_fetch_artifact_cli=False,
            token="stale-token",
            quota_project="emulator-builds",
            token_refresher=lambda: refreshed_tokens[0],
        )

        # 1st call: HTTP 403 with X-Goog-User-Project -> clears quota_project & retries
        # 2nd call: HTTP 401 without X-Goog-User-Project -> refreshes token & retries
        # 3rd call: HTTP 200 with refreshed token
        http.queue_get_error(HTTP_FORBIDDEN, "Quota project forbidden")
        http.queue_get_error(HTTP_UNAUTHORIZED, "Token expired")
        http.queue_get_response(
            {
                "builds": [
                    {
                        "buildId": "15001234",
                        "target": {"name": "emulator-linux_x64"},
                        "branch": "git_emu-main-next",
                        "buildAttemptStatus": "COMPLETE",
                        "successful": True,
                        "completionTimestamp": "1761680000000",
                    }
                ]
            }
        )

        build = svc.get_build("15001234", "emulator-linux_x64")
        self.assertIsNotNone(build)
        assert build is not None
        self.assertEqual(build.build_id, "15001234")
        self.assertIsNone(svc.quota_project)
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(
            http.calls[0].headers.get(HEADER_QUOTA_PROJECT), "emulator-builds"
        )
        self.assertNotIn(HEADER_QUOTA_PROJECT, http.calls[1].headers)
        self.assertEqual(
            http.calls[2].headers.get(HEADER_AUTHORIZATION),
            "Bearer refreshed-oauth-token",
        )

    def test_unrecoverable_http_401_raises_auth_error(self) -> None:
        runner = FakeProcessRunner()
        http = FakeHttpTransport()
        svc = ProdArtifactService(
            runner,
            http_transport=http,
            use_fetch_artifact_cli=False,
            token="invalid-token",
            quota_project="",
            token_refresher=lambda: None,
        )
        http.queue_get_error(HTTP_UNAUTHORIZED, "Unauthorized")
        with self.assertRaises(AuthError) as ctx:
            svc.get_build("15001234", "emulator-linux_x64")
        self.assertIn("HTTP 401", ctx.exception.message)
        self.assertTrue(ctx.exception.remediation)

    def test_list_builds_and_find_matching_artifact(self) -> None:
        runner = FakeProcessRunner()
        http = FakeHttpTransport()
        svc = ProdArtifactService(
            runner,
            http_transport=http,
            use_fetch_artifact_cli=False,
            token="valid-token",
            quota_project="",
        )
        http.queue_get_response(
            {
                "builds": [
                    {
                        "buildId": "15000003",
                        "buildAttemptStatus": "BUILDING",
                        "successful": False,
                    },
                    {
                        "buildId": "15000002",
                        "buildAttemptStatus": "COMPLETE",
                        "completionTimestamp": "1761680000000",
                        "successful": False,
                    },
                    {
                        "buildId": "15000001",
                        "buildAttemptStatus": "COMPLETE",
                        "completionTimestamp": "1761670000000",
                        "successful": True,
                    },
                ]
            }
        )
        green_builds = svc.list_builds(
            "emu-main-next", "emulator-linux_x64", limit=5, green_only=True
        )
        self.assertEqual([b.build_id for b in green_builds], ["15000001"])

        http.queue_get_response(
            {
                "artifacts": [
                    {"name": "BUILD_INFO", "size": "1024"},
                    {"name": "sdk-repo-linux-emulator-15000001.zip", "size": "4096"},
                ]
            }
        )
        matched = svc.find_matching_artifact(
            "15000001",
            "emulator-linux_x64",
            ["*darwin*", "sdk-repo-linux-emulator-*.zip"],
        )
        self.assertEqual(matched, "sdk-repo-linux-emulator-15000001.zip")

    def test_download_artifact_rest_zip_extraction_and_unicode_paths(self) -> None:
        runner = FakeProcessRunner()
        http = FakeHttpTransport()
        bus = EventBus(record_history=True)
        svc = ProdArtifactService(
            runner,
            event_bus=bus,
            http_transport=http,
            use_fetch_artifact_cli=False,
            token="valid-token",
            quota_project="",
        )

        zip_bytes = _make_zip_bytes(
            {
                "emulator/crash_report_测试.txt": "崩溃日志_🚀_é",
                "emulator/ignored.txt": "ignored",
            }
        )
        http.register_json(
            f"{BUILD_API_BASE_URL}/builds/15000001/emulator-linux_x64/attempts/latest/artifacts/",
            {"signedUrl": "https://storage.googleapis.com/signed/symbols.zip"},
        )
        http.register_download(
            "https://storage.googleapis.com/signed/symbols.zip", zip_bytes
        )

        progress_steps: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory(prefix="emu_ab_dl_测试_🚀_") as tmp_dir:
            out_path = svc.download_artifact(
                "15000001",
                "emulator-linux_x64",
                "symbols.zip",
                tmp_dir,
                zip_entries=["emulator/crash_report_测试.txt"],
                progress_callback=lambda cur, tot: progress_steps.append((cur, tot)),
            )
            self.assertEqual(out_path, Path(tmp_dir))
            extracted = Path(tmp_dir) / "emulator" / "crash_report_测试.txt"
            self.assertTrue(extracted.is_file())
            self.assertEqual(
                extracted.read_text(encoding="utf-8"), "崩溃日志_🚀_é"
            )
            self.assertFalse((Path(tmp_dir) / "emulator" / "ignored.txt").exists())
            self.assertTrue(progress_steps)

            events = bus.recorded_events(ArtifactDownloaded)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].build_id, "15000001")
            self.assertEqual(events[0].artifact_name, "symbols.zip")

    def test_download_artifact_via_fetch_artifact_cli_fast_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emu_cli_bin_") as tmp_dir:
            fake_bin = Path(tmp_dir) / "fetch_artifact"
            fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            out_dir = Path(tmp_dir) / "out"
            out_dir.mkdir()
            # Pre-create the downloaded file to simulate fetch_artifact writing it
            (out_dir / "BUILD_INFO").write_text('{"bid": "15000001"}', encoding="utf-8")

            runner = FakeProcessRunner()
            runner.register_prefix(
                [str(fake_bin)],
                ProcessResult(args=(), stdout="Downloaded BUILD_INFO"),
            )
            http = FakeHttpTransport()
            bus = EventBus(record_history=True)
            svc = ProdArtifactService(
                runner,
                event_bus=bus,
                http_transport=http,
                fetch_artifact_bin=str(fake_bin),
                use_fetch_artifact_cli=True,
            )

            path = svc.download_artifact(
                "15000001",
                "emulator-linux_x64",
                "BUILD_INFO",
                out_dir,
                branch="emu-main-next",
            )
            self.assertEqual(path, out_dir / "BUILD_INFO")
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(len(http.calls), 0)

    def test_fake_artifact_service_seeding_and_validation(self) -> None:
        bus = EventBus(record_history=True)
        fake = FakeArtifactService(event_bus=bus)
        fake.seed_build(
            BuildMetadata(
                build_id="15009999",
                target="emulator-linux_x64",
                branch="git_emu-main-next",
                status="COMPLETE",
                successful=True,
            )
        )
        fake.seed_artifact(
            "15009999",
            "emulator-linux_x64",
            "archive.zip",
            zip_entries={"nested/file_测试.txt": "内容_🚀"},
        )

        self.assertEqual(
            fake.resolve_build_id("emu-main-next", "emulator-linux_x64"),
            "15009999",
        )
        self.assertEqual(
            fake.find_matching_artifact(
                "15009999", "emulator-linux_x64", ["*.zip"]
            ),
            "archive.zip",
        )

        with tempfile.TemporaryDirectory(prefix="emu_fake_ab_") as tmp_dir:
            res_dir = fake.download_artifact(
                "15009999",
                "emulator-linux_x64",
                "archive.zip",
                tmp_dir,
                zip_entries=["nested/file_测试.txt"],
            )
            self.assertEqual(
                (res_dir / "nested" / "file_测试.txt").read_text(encoding="utf-8"),
                "内容_🚀",
            )

        with self.assertRaises(UsageError):
            fake.resolve_build_id("", "emulator-linux_x64")
        with self.assertRaises(ExecutionError):
            fake.download_artifact(
                "15009999", "emulator-linux_x64", "missing.zip", "/tmp"
            )


if __name__ == "__main__":
    unittest.main()
