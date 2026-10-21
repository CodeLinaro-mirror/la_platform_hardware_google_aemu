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

"""Unit tests for fetch-build commands and Android Build v4 API client."""

import argparse
import io
import os
from pathlib import Path
import tempfile
import urllib.error
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from commands import fetch_build
from lib.android_build_client import AndroidBuildClient, check_authentication_status
from targets import emulator, system_image, check_auth


class FetchBuildTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        fetch_build.register_parser(subparsers, default_host="mac-arm64")

    @patch("lib.android_build_client.check_authentication_status")
    @patch("lib.android_build_client.AndroidBuildClient.get_latest_build_id")
    @patch("lib.android_build_client.AndroidBuildClient.find_matching_artifact")
    @patch("lib.android_build_client.AndroidBuildClient.download_artifact")
    @patch("targets.emulator.print_result")
    @patch("shutil.which", return_value=None)
    @patch("os.path.exists")
    def test_fetch_emulator_latest_mac_arm64(
        self, mock_exists, mock_which, mock_print_result, mock_download, mock_find, mock_latest_bid, mock_check_auth
    ):
        mock_check_auth.return_value = {
            "valid": True,
            "auth_method": "adc",
            "file_path": "/fake/adc.json",
            "file_exists": True,
            "error_message": None,
            "procedure": None,
        }

        def exists_side_effect(path):
            if path == emulator.FETCH_ARTIFACT_BIN:
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_latest_bid.return_value = "16000000"
        mock_find.return_value = "sdk-repo-darwin_aarch64-emulator-16000000.zip"

        def fake_download(bid, target, art, dest_dir, progress_callback=None):
            dest_file = os.path.join(dest_dir, art)
            with zipfile.ZipFile(dest_file, "w") as z:
                z.writestr("emulator/emulator", "fake-binary-content")
            return dest_file

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parser.parse_args([
                "fetch-build", "emulator",
                "--latest",
                "--branch", "emu-main-next",
                "--host", "mac-arm64",
                "--output-dir", tmpdir,
            ])
            args.func(args)

            mock_check_auth.assert_called_once()
            mock_latest_bid.assert_called_once_with("emu-main-next", "emulator_mac_aarch64")
            mock_download.assert_called_once()
            mock_print_result.assert_called_once()

            res = mock_print_result.call_args[0][0]
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "emulator_mac_aarch64")
            self.assertEqual(res["build_id"], "16000000")
            self.assertEqual(res["host"], "mac-arm64")

    @patch("lib.android_build_client.check_authentication_status")
    @patch("lib.android_build_client.AndroidBuildClient.get_latest_build_id")
    @patch("lib.android_build_client.AndroidBuildClient.find_matching_artifact")
    @patch("lib.android_build_client.AndroidBuildClient.download_artifact")
    @patch("targets.emulator.print_result")
    @patch("shutil.which", return_value=None)
    @patch("os.path.exists")
    def test_fetch_emulator_latest_dev_branch_mac_arm64(
        self, mock_exists, mock_which, mock_print_result, mock_download, mock_find, mock_latest_bid, mock_check_auth
    ):
        mock_check_auth.return_value = {
            "valid": True,
            "auth_method": "adc",
            "file_path": "/fake/adc.json",
            "file_exists": True,
            "error_message": None,
            "procedure": None,
        }

        def exists_side_effect(path):
            if path == emulator.FETCH_ARTIFACT_BIN:
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_latest_bid.return_value = "16000001"
        mock_find.return_value = "sdk-repo-darwin_aarch64-emulator-16000001.zip"

        def fake_download(bid, target, art, dest_dir, progress_callback=None):
            dest_file = os.path.join(dest_dir, art)
            with zipfile.ZipFile(dest_file, "w") as z:
                z.writestr("emulator/emulator", "fake-binary-content")
            return dest_file

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parser.parse_args([
                "fetch-build", "emulator",
                "--latest",
                "--branch", "emu-main-dev",
                "--host", "mac-arm64",
                "--output-dir", tmpdir,
            ])
            args.func(args)

            mock_latest_bid.assert_called_once_with("emu-main-dev", "emulator-mac_aarch64_gfxstream")
            res = mock_print_result.call_args[0][0]
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "emulator-mac_aarch64_gfxstream")

    @patch("lib.android_build_client.check_authentication_status")
    @patch("lib.android_build_client.AndroidBuildClient.find_matching_artifact")
    @patch("lib.android_build_client.AndroidBuildClient.download_artifact")
    @patch("targets.emulator.print_result")
    @patch("shutil.which", return_value=None)
    @patch("os.path.exists")
    def test_fetch_emulator_by_build_id_mac_arm64(
        self, mock_exists, mock_which, mock_print_result, mock_download, mock_find, mock_check_auth
    ):
        mock_check_auth.return_value = {
            "valid": True,
            "auth_method": "adc",
            "file_path": "/fake/adc.json",
            "file_exists": True,
            "error_message": None,
            "procedure": None,
        }

        def exists_side_effect(path):
            if path == emulator.FETCH_ARTIFACT_BIN:
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_find.return_value = "sdk-repo-darwin_aarch64-emulator-15943073.zip"

        def fake_download(bid, target, art, dest_dir, progress_callback=None):
            dest_file = os.path.join(dest_dir, art)
            with zipfile.ZipFile(dest_file, "w") as z:
                z.writestr("emulator/emulator", "binary")
            return dest_file

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parser.parse_args([
                "fetch-build", "emulator",
                "--build-id", "15943073",
                "--host", "mac-arm64",
                "--output-dir", tmpdir,
            ])
            args.func(args)

            mock_download.assert_called_once()
            res = mock_print_result.call_args[0][0]
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["build_id"], "15943073")

    @patch("lib.android_build_client.check_authentication_status")
    @patch("lib.android_build_client.AndroidBuildClient.download_artifact")
    @patch("targets.emulator.print_result")
    def test_fetch_emulator_auth_check_failure(
        self, mock_print_result, mock_download, mock_check_auth
    ):
        mock_check_auth.return_value = {
            "valid": False,
            "auth_method": "Application Default Credentials (ADC)",
            "file_path": "/Users/test/.config/gcloud/application_default_credentials.json",
            "file_exists": False,
            "error_message": "Application Default Credentials (ADC) file was not found.",
            "procedure": "Step 1: Run gcloud auth application-default login ...",
        }

        args = self.parser.parse_args([
            "fetch-build", "emulator",
            "--latest",
            "--branch", "emu-main-next",
            "--host", "mac-arm64",
        ])
        args.func(args)

        mock_download.assert_not_called()
        mock_print_result.assert_called_once()
        call_kwargs = mock_print_result.call_args[1]
        self.assertTrue(call_kwargs.get("is_error"))
        res = mock_print_result.call_args[0][0]
        self.assertEqual(res["status"], "error")
        self.assertIn("procedure", res)
        self.assertIn("gcloud auth", res["procedure"])
        self.assertEqual(res["file_exists"], False)

    @patch("lib.android_build_client.check_authentication_status")
    @patch("lib.android_build_client.AndroidBuildClient.get_latest_build_id")
    @patch("lib.android_build_client.AndroidBuildClient.find_matching_artifact")
    @patch("lib.android_build_client.AndroidBuildClient.download_artifact")
    @patch("targets.emulator.print_result")
    @patch("shutil.which", return_value=None)
    @patch("os.path.exists")
    def test_fetch_emulator_skip_auth_check(
        self, mock_exists, mock_which, mock_print_result, mock_download, mock_find, mock_latest_bid, mock_check_auth
    ):
        def exists_side_effect(path):
            if path == emulator.FETCH_ARTIFACT_BIN:
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_latest_bid.return_value = "16000000"
        mock_find.return_value = "sdk-repo-darwin_aarch64-emulator-16000000.zip"

        def fake_download(bid, target, art, dest_dir, progress_callback=None):
            dest_file = os.path.join(dest_dir, art)
            with zipfile.ZipFile(dest_file, "w") as z:
                z.writestr("emulator/emulator", "fake-binary-content")
            return dest_file

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parser.parse_args([
                "fetch-build", "emulator",
                "--latest",
                "--skip-auth-check",
                "--output-dir", tmpdir,
            ])
            args.func(args)

            mock_check_auth.assert_not_called()
            mock_latest_bid.assert_called_once()
            mock_download.assert_called_once()

    @patch("lib.android_build_client.check_authentication_status")
    @patch("targets.check_auth.print_result")
    def test_check_auth_subcommand_success(self, mock_print_result, mock_check_auth):
        mock_check_auth.return_value = {
            "valid": True,
            "auth_method": "Application Default Credentials (ADC)",
            "file_path": "/fake/adc.json",
            "file_exists": True,
            "error_message": None,
            "procedure": None,
        }

        args = self.parser.parse_args(["fetch-build", "check-auth"])
        args.func(args)

        mock_print_result.assert_called_once()
        self.assertFalse(mock_print_result.call_args[1].get("is_error", False))
        res = mock_print_result.call_args[0][0]
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["auth_file"], "/fake/adc.json")

    @patch("lib.android_build_client.check_authentication_status")
    @patch("targets.check_auth.print_result")
    def test_check_auth_subcommand_failure(self, mock_print_result, mock_check_auth):
        mock_check_auth.return_value = {
            "valid": False,
            "auth_method": "Application Default Credentials (ADC)",
            "file_path": "/fake/adc.json",
            "file_exists": False,
            "error_message": "ADC file missing",
            "procedure": "Run gcloud auth ...",
        }

        args = self.parser.parse_args(["fetch-build", "check-auth"])
        args.func(args)

        mock_print_result.assert_called_once()
        self.assertTrue(mock_print_result.call_args[1].get("is_error", False))
        res = mock_print_result.call_args[0][0]
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["procedure"], "Run gcloud auth ...")

    @patch("os.path.exists", return_value=False)
    def test_check_authentication_status_missing_adc_file(self, mock_exists):
        with patch.dict(os.environ, {}, clear=True):
            status = check_authentication_status()
            self.assertFalse(status["valid"])
            self.assertFalse(status["file_exists"])
            self.assertIn("was not found", status["error_message"])
            self.assertIsNotNone(status["procedure"])

    @patch("shutil.which", return_value=None)
    @patch("lib.android_build_client.OAuthTokenManager")
    @patch("os.path.exists")
    def test_check_authentication_status_token_failure(self, mock_exists, mock_token_mgr_cls, mock_which):
        def exists_side_effect(path):
            if "/google/data" in str(path):
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_mgr = MagicMock()
        mock_mgr.get_token.return_value = None
        mock_token_mgr_cls.return_value = mock_mgr

        with patch.dict(os.environ, {}, clear=True):
            status = check_authentication_status()
            self.assertFalse(status["valid"])
            self.assertTrue(status["file_exists"])
            self.assertIn("Could not acquire or refresh OAuth2 token", status["error_message"])

    @patch("shutil.which", return_value=None)
    @patch("urllib.request.urlopen")
    @patch("lib.android_build_client.OAuthTokenManager")
    @patch("os.path.exists")
    def test_check_authentication_status_insufficient_scope(
        self, mock_exists, mock_token_mgr_cls, mock_urlopen, mock_which
    ):
        def exists_side_effect(path):
            if "/google/data" in str(path):
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_mgr = MagicMock()
        mock_mgr.get_token.return_value = "fake-token"
        mock_token_mgr_cls.return_value = mock_mgr

        err_fp = io.BytesIO(b'{"error": {"message": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}}')
        http_err = urllib.error.HTTPError(
            url="https://androidbuild-pa.googleapis.com/v4/builds",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=err_fp,
        )
        mock_urlopen.side_effect = http_err

        with patch.dict(os.environ, {}, clear=True):
            status = check_authentication_status()
            self.assertFalse(status["valid"])
            self.assertIn("lacks the required scope", status["error_message"])
            self.assertIsNotNone(status["procedure"])

    @patch("shutil.which", return_value=None)
    @patch("urllib.request.urlopen")
    @patch("lib.android_build_client.OAuthTokenManager")
    @patch("os.path.exists")
    def test_check_authentication_status_success(
        self, mock_exists, mock_token_mgr_cls, mock_urlopen, mock_which
    ):
        def exists_side_effect(path):
            if "/google/data" in str(path):
                return False
            return True
        mock_exists.side_effect = exists_side_effect

        mock_mgr = MagicMock()
        mock_mgr.get_token.return_value = "fake-token"
        mock_token_mgr_cls.return_value = mock_mgr

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {}, clear=True):
            status = check_authentication_status()
            self.assertTrue(status["valid"])
            self.assertIsNone(status["error_message"])
            self.assertIsNone(status["procedure"])

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("os.path.exists")
    def test_check_authentication_status_linux_cfs(self, mock_exists, mock_which, mock_run):
        mock_exists.return_value = True
        mock_which.side_effect = lambda cmd: "/google/data/ro/projects/android/fetch_artifact" if "fetch_artifact" in cmd else "/usr/bin/gcertstatus"
        mock_run.return_value = MagicMock(returncode=0)

        status = check_authentication_status()
        self.assertTrue(status["valid"])
        self.assertEqual(status["auth_method"], "FETCH_ARTIFACT_CLI")

    @patch("urllib.request.urlopen")
    def test_android_build_client_v4_methods(self, mock_urlopen):
        # 1. Test get_latest_build_id
        mock_resp_builds = MagicMock()
        mock_resp_builds.read.return_value = b'{"builds": [{"buildId": "12345"}]}'
        mock_resp_builds.__enter__.return_value = mock_resp_builds

        # 2. Test list_artifacts
        mock_resp_arts = MagicMock()
        mock_resp_arts.read.return_value = b'{"artifacts": [{"name": "sdk-repo-darwin_aarch64-emulator-12345.zip"}]}'
        mock_resp_arts.__enter__.return_value = mock_resp_arts

        mock_urlopen.side_effect = [mock_resp_builds, mock_resp_arts]

        client = AndroidBuildClient(token="mock_token")
        bid = client.get_latest_build_id("emu-main-next", "emulator_mac_aarch64")
        self.assertEqual(bid, "12345")

        art = client.find_matching_artifact(bid, "emulator_mac_aarch64", ["sdk-repo-darwin_aarch64-emulator-*.zip"])
        self.assertEqual(art, "sdk-repo-darwin_aarch64-emulator-12345.zip")


if __name__ == "__main__":
    unittest.main()
