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

"""Unit tests for the update-prebuilt command and per-prebuilt modules."""

import argparse
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from commands import update_prebuilt
from core.command import COMMAND_INSTANCE_ATTR, CommandResult
from core.context import ExecutionContext
from lib import prebuilt_updater
from prebuilts import android_device_hub, kosmickrisp, lavapipe


class UpdatePrebuiltTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        update_prebuilt.register_parser(subparsers, default_host="linux-x64")
        ctx_root = tempfile.TemporaryDirectory()
        self.addCleanup(ctx_root.cleanup)
        self.ctx = ExecutionContext.for_testing(root_dir=ctx_root.name)

    def _create_fake_zip(self, zip_path: Path, files: dict):
        with zipfile.ZipFile(zip_path, "w") as zf:
            for arcname, content in files.items():
                zf.writestr(arcname, content)

    def _run(self, args: argparse.Namespace) -> CommandResult:
        """Runs the Command bound to `args` the way CliDispatcher does."""
        cmd = getattr(args, COMMAND_INSTANCE_ATTR)
        validated = cmd.validate(args, self.ctx)
        return cmd.execute(validated if validated is not None else args, self.ctx)

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_lavapipe_linux_x64_by_build_number(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            self._create_fake_zip(
                zip_file,
                {
                    "emulator/lib64/vulkan/libvulkan_lvp.so": b"new-lvp-binary",
                    "emulator/lib64/vulkan/libLLVM.so": b"new-llvm-binary",
                    "emulator/lib64/vulkan/libxcb-aemu.so": b"xcb-unchanged",
                    "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "lvp"}',
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dest:
            dest_path = Path(tmp_dest)
            (dest_path / "libvulkan_lvp.so").write_bytes(b"old-lvp")
            (dest_path / "libLLVM.so").write_bytes(b"old-llvm")
            (dest_path / "libxcb-aemu.so").write_bytes(b"xcb-unchanged")
            (dest_path / "lvp_icd.json").write_bytes(b'{"icd": "lvp"}')

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "lavapipe",
                    "--target=linux-x64",
                    "--build-number=16534139",
                    "--token=fake-token",
                    f"--dest-dir={tmp_dest}",
                ]
            )
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["build_number"], "16534139")
            self.assertEqual(res["target"], "linux-x64")
            self.assertEqual(
                res["updated_files"], ["libvulkan_lvp.so", "libLLVM.so"]
            )
            self.assertEqual(
                (dest_path / "libvulkan_lvp.so").read_bytes(), b"new-lvp-binary"
            )
            self.assertEqual(
                (dest_path / "libLLVM.so").read_bytes(), b"new-llvm-binary"
            )
            self.assertEqual(
                (dest_path / "libxcb-aemu.so").read_bytes(), b"xcb-unchanged"
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_lavapipe_dry_run_does_not_modify_files(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            self._create_fake_zip(
                zip_file,
                {
                    "emulator/lib64/vulkan/libvulkan_lvp.so": b"new-lvp-binary",
                    "emulator/lib64/vulkan/libLLVM.so": b"new-llvm-binary",
                    "emulator/lib64/vulkan/libxcb-aemu.so": b"new-xcb-binary",
                    "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "lvp"}',
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dest:
            dest_path = Path(tmp_dest)
            (dest_path / "libvulkan_lvp.so").write_bytes(b"old-lvp")

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "lavapipe",
                    "--target=linux-x64",
                    "--build-number=16534139",
                    "--token=fake-token",
                    f"--dest-dir={tmp_dest}",
                    "--dry-run",
                ]
            )
            result = self._run(args)

            self.assertEqual(
                (dest_path / "libvulkan_lvp.so").read_bytes(), b"old-lvp"
            )
            self.assertFalse((dest_path / "libLLVM.so").exists())
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["dry_run"])

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_lavapipe_mac_arm64(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            self._create_fake_zip(
                zip_file,
                {
                    "emulator/lib64/vulkan/libvulkan_lvp.dylib": b"new-mac-lvp-dylib",
                    "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "mac-lvp"}\n',
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dest:
            dest_path = Path(tmp_dest)
            (dest_path / "libvulkan_lvp.dylib").write_bytes(b"old-mac-lvp")
            (dest_path / "lvp_icd.json").write_bytes(b'{"icd": "mac-lvp"}\n')

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "lavapipe",
                    "--target=mac-arm64",
                    "--build-number=16534139",
                    "--token=fake-token",
                    f"--dest-dir={tmp_dest}",
                ]
            )
            self.assertEqual(args.target, "mac-arm64")
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "mac-arm64")
            self.assertEqual(res["updated_files"], ["libvulkan_lvp.dylib"])
            self.assertEqual(
                (dest_path / "libvulkan_lvp.dylib").read_bytes(),
                b"new-mac-lvp-dylib",
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_lavapipe_win_x64_and_crlf_normalization(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            self._create_fake_zip(
                zip_file,
                {
                    "emulator/lib64/vulkan/libvulkan_lvp.dll": b"new-win-lvp-dll",
                    "emulator/lib64/vulkan/lvp_icd.json": b'{\r\n  "icd": "win-lvp"\r\n}\r\n',
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dest:
            dest_path = Path(tmp_dest)
            (dest_path / "libvulkan_lvp.dll").write_bytes(b"old-win-lvp")
            (dest_path / "lvp_icd.json").write_bytes(b'{\n  "icd": "win-lvp"\n}\n')

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "lavapipe",
                    "--target=win-x64",
                    "--build-number=16534139",
                    "--token=fake-token",
                    f"--dest-dir={tmp_dest}",
                ]
            )
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "win-x64")
            self.assertEqual(res["updated_files"], ["libvulkan_lvp.dll"])
            self.assertEqual(
                (dest_path / "libvulkan_lvp.dll").read_bytes(),
                b"new-win-lvp-dll",
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_lavapipe_default_all_targets(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            if target == "emulator_linux_x64":
                self._create_fake_zip(
                    zip_file,
                    {
                        "emulator/lib64/vulkan/libvulkan_lvp.so": b"new-linux-lvp",
                        "emulator/lib64/vulkan/libLLVM.so": b"new-linux-llvm",
                        "emulator/lib64/vulkan/libxcb-aemu.so": b"new-linux-xcb",
                        "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "linux"}',
                    },
                )
            elif target == "emulator_mac_aarch64":
                self._create_fake_zip(
                    zip_file,
                    {
                        "emulator/lib64/vulkan/libvulkan_lvp.dylib": b"new-mac-lvp",
                        "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "mac"}',
                    },
                )
            elif target == "emulator_windows_x64":
                self._create_fake_zip(
                    zip_file,
                    {
                        "emulator/lib64/vulkan/libvulkan_lvp.dll": b"new-win-lvp",
                        "emulator/lib64/vulkan/lvp_icd.json": b'{"icd": "win"}',
                    },
                )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_root:
            root_path = Path(tmp_root)
            for tgt in ("linux-x64", "mac-arm64", "win-x64"):
                (root_path / tgt).mkdir(parents=True, exist_ok=True)

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "lavapipe",
                    "--build-number=16534139",
                    "--token=fake-token",
                    f"--dest-dir={tmp_root}",
                ]
            )
            self.assertEqual(args.target, "all")
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "all")
            self.assertEqual(res["build_number"], "16534139")
            self.assertEqual(len(res["files"]), 8)

            self.assertEqual(
                (root_path / "linux-x64" / "libvulkan_lvp.so").read_bytes(),
                b"new-linux-lvp",
            )
            self.assertEqual(
                (root_path / "mac-arm64" / "libvulkan_lvp.dylib").read_bytes(),
                b"new-mac-lvp",
            )
            self.assertEqual(
                (root_path / "win-x64" / "libvulkan_lvp.dll").read_bytes(),
                b"new-win-lvp",
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    def test_custom_plugin_with_minimal_two_arg_update_prebuilt(
        self, mock_check_auth
    ):
        """Verifies a prebuilt module implementing strictly update_prebuilt(build_number, targets)."""
        mock_check_auth.return_value = {"valid": True}
        captured_calls = []

        fake_mod = types.ModuleType("prebuilts.kosmic_krisp")
        fake_mod.SUPPORTED_TARGETS = ("mac-arm64",)
        fake_mod.DESCRIPTION = "Update KosmicKrisp Vulkan ICD"

        def update_prebuilt_fn(build_number, targets):
            captured_calls.append((build_number, targets))
            return {
                "status": "success",
                "action": "update-prebuilt kosmic-krisp",
                "summary": f"Updated KosmicKrisp from {build_number}",
            }

        fake_mod.update_prebuilt = update_prebuilt_fn

        with patch(
            "commands.update_prebuilt.get_prebuilt_modules",
            return_value=[("kosmic-krisp", fake_mod)],
        ):
            parser = argparse.ArgumentParser()
            parser.add_argument("--json", action="store_true")
            subparsers = parser.add_subparsers(dest="subcommand")
            update_prebuilt.register_parser(subparsers)

            args = parser.parse_args(
                [
                    "update-prebuilt",
                    "kosmic-krisp",
                    "--build-number=16534139",
                    "--token=fake-token",
                ]
            )
            result = self._run(args)

        self.assertEqual(captured_calls, [("16534139", ["mac-arm64"])])
        res = result.data
        self.assertEqual(res["status"], "success")

    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_fetch_ab_artifact_with_custom_zip(
        self, mock_download, mock_find_fetch_tool
    ):
        """Verifies fetch_ab_artifact works for any AB target and zip name (e.g. android-cli.zip)."""
        def fake_download(bid, target, art, dest_dir):
            outer_zip = Path(dest_dir) / art
            with zipfile.ZipFile(outer_zip, "w") as zf:
                zf.writestr("device-hub_pack_linux_x64.zip", b"inner-zip-bytes")
            return str(outer_zip)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            err = prebuilt_updater.fetch_ab_artifact(
                build_number="88776655",
                ab_target="studio-linux",
                artifact="android-cli.zip",
                out_dir=out_dir,
                zip_entries=["device-hub_pack_linux_x64.zip"],
                token="fake-token",
            )
            self.assertIsNone(err)
            self.assertEqual(
                (out_dir / "device-hub_pack_linux_x64.zip").read_bytes(),
                b"inner-zip-bytes",
            )

    def test_update_prebuilt_list_command(self):
        args = self.parser.parse_args(["update-prebuilt", "--list"])
        result = self._run(args)
        res = result.data
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "update-prebuilt --list")
        self.assertIn("lavapipe", res["prebuilts"])
        self.assertEqual(
            res["prebuilts"]["lavapipe"]["module"], "prebuilts.lavapipe"
        )
        self.assertIn("kosmickrisp", res["prebuilts"])
        self.assertEqual(
            res["prebuilts"]["kosmickrisp"]["module"], "prebuilts.kosmickrisp"
        )
        self.assertEqual(
            res["prebuilts"]["kosmickrisp"]["targets"], ["mac-arm64"]
        )
        self.assertIn("android-device-hub", res["prebuilts"])
        self.assertEqual(
            res["prebuilts"]["android-device-hub"]["module"],
            "prebuilts.android_device_hub",
        )
        self.assertEqual(
            res["prebuilts"]["android-device-hub"]["targets"],
            ["linux-x64", "linux-arm64", "mac-arm64", "win-x64"],
        )
        self.assertEqual(
            res["prebuilts"]["android-device-hub"]["default_branch"],
            "studio-main",
        )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_kosmickrisp_mac_arm64(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            self._create_fake_zip(
                zip_file,
                {
                    "emulator/lib64/vulkan/libvulkan_kosmickrisp.dylib": b"new-kk-dylib",
                    "emulator/lib64/vulkan/libkosmickrisp_icd.json": b'{"icd": "kk"}\n',
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_dest:
            dest_path = Path(tmp_dest)
            (dest_path / "libvulkan_kosmickrisp.dylib").write_bytes(b"old-kk")
            (dest_path / "libkosmickrisp_icd.json").write_bytes(b'{"icd": "kk"}\n')

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "kosmickrisp",
                    "--build-number=16476462",
                    "--token=fake-token",
                    f"--dest-dir={tmp_dest}",
                ]
            )
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["prebuilt"], "kosmickrisp")
            self.assertEqual(res["target"], "mac-arm64")
            self.assertEqual(res["build_number"], "16476462")
            self.assertEqual(
                res["updated_files"], ["libvulkan_kosmickrisp.dylib"]
            )
            self.assertEqual(
                (dest_path / "libvulkan_kosmickrisp.dylib").read_bytes(),
                b"new-kk-dylib",
            )

    def test_kosmickrisp_rejects_non_mac_arm64_target(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(
                [
                    "update-prebuilt",
                    "kosmickrisp",
                    "--target=linux-x64",
                    "--build-number=16476462",
                ]
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_android_device_hub_all_targets(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            zip_file.parent.mkdir(parents=True, exist_ok=True)
            self._create_fake_zip(
                zip_file,
                {
                    "device-hub_pack.zip": f"pack-bytes-{target}-{Path(art).name}".encode(
                        "utf-8"
                    ),
                },
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_root:
            root_path = Path(tmp_root)
            for tgt in ("linux-x64", "linux-arm64", "mac-arm64", "win-x64"):
                (root_path / tgt).mkdir(parents=True, exist_ok=True)

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "android-device-hub",
                    "--build-number=16557439",
                    "--token=fake-token",
                    f"--dest-dir={tmp_root}",
                ]
            )
            self.assertEqual(args.target, "all")
            result = self._run(args)
            self.assertTrue(result.ok)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["prebuilt"], "android-device-hub")
            self.assertEqual(res["target"], "all")
            self.assertEqual(res["build_number"], "16557439")
            self.assertEqual(len(res["files"]), 4)
            self.assertEqual(
                (
                    root_path / "linux-x64" / "device-hub_pack_linux_x64.zip"
                ).read_bytes(),
                b"pack-bytes-studio-linux-android-cli.zip",
            )
            self.assertEqual(
                (
                    root_path
                    / "linux-arm64"
                    / "device-hub_pack_linux_arm64.zip"
                ).read_bytes(),
                b"pack-bytes-studio-linux-android-cli-linux-arm64.zip",
            )
            self.assertEqual(
                (
                    root_path / "mac-arm64" / "device-hub_pack_darwin_arm64.zip"
                ).read_bytes(),
                b"pack-bytes-studio-mac-arm-android-cli.zip",
            )
            self.assertEqual(
                (
                    root_path / "win-x64" / "device-hub_pack_win32_x64.zip"
                ).read_bytes(),
                b"pack-bytes-studio-win-android-cli.zip",
            )

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_android_device_hub_linux_arm_alias_and_missing_leaf_dir(
        self,
        mock_download,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}

        def fake_download(bid, target, art, dest_dir):
            self.assertEqual(target, "studio-linux")
            self.assertEqual(art, "artifacts/android-cli-linux-arm64.zip")
            zip_file = Path(dest_dir) / art
            zip_file.parent.mkdir(parents=True, exist_ok=True)
            self._create_fake_zip(
                zip_file,
                {"device-hub_pack.zip": b"arm64-pack-payload"},
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_ws:
            ws_root = Path(tmp_ws)
            adh_parent = (
                ws_root
                / "prebuilts"
                / "android-emulator-build"
                / "common"
                / "android-device-hub"
            )
            adh_parent.mkdir(parents=True, exist_ok=True)

            with patch.dict(
                "os.environ", {"BUILD_WORKING_DIRECTORY": str(ws_root)}
            ):
                args = self.parser.parse_args(
                    [
                        "update-prebuilt",
                        "android-device-hub",
                        "--target=linux-arm",
                        "--build-number=16709810",
                        "--token=fake-token",
                    ]
                )
                self.assertEqual(args.target, "linux-arm64")
                result = self._run(args)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "linux-arm64")
            installed = (
                adh_parent
                / "linux-arm64"
                / "device-hub_pack_linux_arm64.zip"
            )
            self.assertTrue(installed.is_file())
            self.assertEqual(installed.read_bytes(), b"arm64-pack-payload")

    @patch("commands.update_prebuilt.check_authentication_status")
    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.resolve_build_id")
    @patch("lib.prebuilt_updater.ProdArtifactService.download_artifact")
    def test_update_android_device_hub_common_dir_layout_dedups_studio_linux_bid(
        self,
        mock_download,
        mock_get_latest_bid,
        mock_find_fetch_tool,
        mock_check_auth,
    ):
        mock_check_auth.return_value = {"valid": True}
        mock_get_latest_bid.side_effect = lambda branch, ab_tgt: {
            "studio-linux": "16709810",
            "studio-mac-arm": "16711071",
            "studio-win": "16709810",
        }[ab_tgt]

        def fake_download(bid, target, art, dest_dir):
            zip_file = Path(dest_dir) / art
            zip_file.parent.mkdir(parents=True, exist_ok=True)
            self._create_fake_zip(
                zip_file,
                {"device-hub_pack.zip": f"{target}:{art}:{bid}".encode("utf-8")},
            )
            return str(zip_file)

        mock_download.side_effect = fake_download

        with tempfile.TemporaryDirectory() as tmp_adh:
            adh_dir = Path(tmp_adh)
            for subdir in ("linux-x64", "darwin-arm64", "win32-x64"):
                (adh_dir / subdir).mkdir(parents=True, exist_ok=True)

            args = self.parser.parse_args(
                [
                    "update-prebuilt",
                    "android-device-hub",
                    "--latest",
                    "--token=fake-token",
                    f"--dest-dir={tmp_adh}",
                ]
            )
            result = self._run(args)

            # studio-linux should only be queried once even though both
            # linux-x64 and linux-arm64 use studio-linux.
            self.assertEqual(mock_get_latest_bid.call_count, 3)
            res = result.data
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target"], "all")
            self.assertEqual(res["build_number"], "16709810, 16711071")
            self.assertEqual(
                (adh_dir / "darwin-arm64" / "device-hub_pack_darwin_arm64.zip").read_bytes(),
                b"studio-mac-arm:android-cli.zip:16711071",
            )
            self.assertEqual(
                (adh_dir / "win32-x64" / "device-hub_pack_win32_x64.zip").read_bytes(),
                b"studio-win:android-cli.zip:16709810",
            )
            self.assertEqual(
                (adh_dir / "linux-arm64" / "device-hub_pack_linux_arm64.zip").read_bytes(),
                b"studio-linux:artifacts/android-cli-linux-arm64.zip:16709810",
            )

    @patch("lib.prebuilt_updater._find_fetch_artifact_tool", return_value=None)
    @patch("lib.prebuilt_updater.ProdArtifactService.resolve_build_id")
    def test_resolve_build_number_latest(
        self, mock_get_latest_bid, mock_find_fetch_tool
    ):
        mock_get_latest_bid.return_value = "16599999"
        bid = prebuilt_updater.resolve_build_number(
            "latest", "studio-main", "studio-linux", token="fake-token"
        )
        self.assertEqual(bid, "16599999")
        mock_get_latest_bid.assert_called_once_with(
            "studio-main", "studio-linux"
        )

    def test_prebuilt_modules_expose_update_prebuilt(self):
        for mod in (lavapipe, kosmickrisp, android_device_hub):
            self.assertTrue(hasattr(mod, "update_prebuilt"))
            self.assertTrue(callable(mod.update_prebuilt))


if __name__ == "__main__":
    unittest.main()
