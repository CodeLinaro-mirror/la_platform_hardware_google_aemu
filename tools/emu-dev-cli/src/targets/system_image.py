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

import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import zipfile

from lib.output import print_result

logger = logging.getLogger(__name__)

FETCH_ARTIFACT_BIN = "/google/data/ro/projects/android/fetch_artifact"

TARGET_CANDIDATES = {
    "x86_64": [
        "sdk_gphone16k_x86_64-user",
        "sdk_gphone16k_x86_64-userdebug",
        "sdk_gphone64_x86_64-userdebug",
        "sdk_gphone_x86_64-userdebug",
        "sdk_car_x86_64-userdebug",
    ],
    "arm64": [
        "sdk_gphone16k_arm64-user",
        "sdk_gphone16k_arm64-userdebug",
        "sdk_gphone64_arm64-userdebug",
        "sdk_gphone_arm64-userdebug",
    ],
}


def detect_default_arch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return "arm64"


def register_parser(subparser, default_host):
    parser = subparser.add_parser(
        "system-image",
        help="Fetch Android System Image artifacts"
    )
    build_group = parser.add_mutually_exclusive_group(required=False)
    build_group.add_argument(
        "--build-id", "--build_id",
        dest="build_id",
        type=str,
        help="Specific Android Build ID to fetch (e.g. 15943073)"
    )
    build_group.add_argument(
        "--latest",
        action="store_true",
        help="Fetch the latest submitted system-image build"
    )

    default_arch = detect_default_arch()
    parser.add_argument(
        "--arch",
        type=str,
        default=default_arch,
        choices=["x86_64", "arm64"],
        help=f"Target CPU architecture (defaults to auto-detected '{default_arch}')"
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Override Android Build target name"
    )
    parser.add_argument(
        "--branch",
        type=str,
        default="trunk-release",
        help="Branch to target when fetching system images (defaults to trunk-release)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=default_host,
        choices=["linux-x64", "mac-arm64", "mac-x64"],
        help=f"Target host OS platform (defaults to auto-detected '{default_host}')"
    )
    parser.add_argument(
        "--output-dir", "--output_dir",
        dest="output_dir",
        type=str,
        default=None,
        help="Directory to store downloaded artifacts (defaults to /tmp/system-image-<arch>-<build-id>)"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 access token for Android Build API (useful on macOS)"
    )
    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip preflight authentication readiness check"
    )

    def handler(args):
        if not args.build_id and not args.latest:
            parser.print_help()
            return
        run_system_image_fetch(args)

    parser.set_defaults(func=handler)


def run_system_image_fetch(args):
    host = args.host.lower()
    arch = args.arch.lower()
    build_id = getattr(args, "build_id", None)
    is_latest = getattr(args, "latest", False)
    branch = getattr(args, "branch", "trunk-release")
    json_mode = getattr(args, "json", False)
    token = getattr(args, "token", None)

    if not getattr(args, "skip_auth_check", False):
        from lib.android_build_client import check_authentication_status
        auth_status = check_authentication_status(token=token)
        if not auth_status["valid"]:
            res_data = {
                "status": "error",
                "action": "fetch-build system-image",
                "summary": "Android Build API authentication check failed",
                "auth_method": auth_status["auth_method"],
                "error_message": auth_status["error_message"],
                "procedure": auth_status["procedure"],
                "exit_code": 1,
            }
            if auth_status.get("file_path"):
                res_data["auth_file"] = auth_status["file_path"]
                res_data["file_exists"] = auth_status["file_exists"]
            print_result(res_data, json_mode=json_mode, is_error=True)
            return

    fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")

    custom_output_dir = args.output_dir
    if custom_output_dir:
        dest_dir = custom_output_dir
    else:
        dest_dir = f"/tmp/system-image-{arch}-{build_id}" if build_id else f"/tmp/sysimg_tmp_{os.getpid()}"
    os.makedirs(dest_dir, exist_ok=True)

    if is_latest:
        artifact_patterns = [
            "sdk-repo-*-system-images-[0-9]*.zip",
            "sdk-repo-*-system-images-*.zip",
            "*.zip",
        ]
    else:
        artifact_patterns = [
            f"sdk-repo-*-system-images-{build_id}.zip",
            "sdk-repo-*-system-images-*.zip",
            "*.zip",
        ]

    candidate_targets = [args.target] if args.target else TARGET_CANDIDATES.get(arch, TARGET_CANDIDATES["x86_64"])
    successful_target = None
    resolved_build_id = build_id
    last_err = ""

    # Strategy 1: CLI fetch_artifact if available
    if fetch_tool:
        for t_name in candidate_targets:
            for pattern in artifact_patterns:
                cmd = [fetch_tool, "--target", t_name, pattern, dest_dir]
                if is_latest:
                    cmd.insert(1, "--latest")
                    cmd.insert(1, branch)
                    cmd.insert(1, "--branch")
                else:
                    cmd.insert(1, build_id)
                    cmd.insert(1, "--bid")

                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and glob.glob(os.path.join(dest_dir, "*.zip")):
                    successful_target = t_name
                    break
                else:
                    last_err = res.stderr.strip() or res.stdout.strip()
            if successful_target:
                break

    # Strategy 2: Direct Android Build v4 REST API fallback
    if not successful_target and not fetch_tool:
        try:
            from lib.android_build_client import AndroidBuildClient
            client = AndroidBuildClient(token=token)

            for t_name in candidate_targets:
                try:
                    if is_latest:
                        resolved_build_id = client.get_latest_build_id(branch, t_name)
                    else:
                        resolved_build_id = build_id

                    matched_art = client.find_matching_artifact(resolved_build_id, t_name, artifact_patterns)
                    if matched_art:
                        client.download_artifact(resolved_build_id, t_name, matched_art, dest_dir)
                        successful_target = t_name
                        break
                except Exception as e:
                    last_err = str(e)
        except Exception as e:
            last_err = str(e)

    if not successful_target:
        desc = f"latest on branch {branch}" if is_latest else f"build {build_id}"
        print_result({
            "status": "error",
            "action": "fetch-build system-image",
            "summary": f"Failed to fetch system-image artifact under targets {candidate_targets} for {desc}",
            "error_message": last_err or "Artifact download failed.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)

    all_zips = glob.glob(os.path.join(dest_dir, "*.zip"))
    sys_zips = [z for z in all_zips if "system-images" in os.path.basename(z)]
    zips = sys_zips if sys_zips else all_zips

    if zips and not resolved_build_id:
        m = re.search(r"-(\d+)\.zip$", os.path.basename(zips[0]))
        if m:
            resolved_build_id = m.group(1)

    # Rename temporary directory to include actual numeric build_id if output_dir was not explicitly provided
    if not custom_output_dir and resolved_build_id:
        final_dir = f"/tmp/system-image-{arch}-{resolved_build_id}"
        if dest_dir != final_dir:
            if os.path.exists(final_dir):
                shutil.rmtree(final_dir)
            shutil.move(dest_dir, final_dir)
            dest_dir = final_dir
            all_zips = glob.glob(os.path.join(dest_dir, "*.zip"))
            sys_zips = [z for z in all_zips if "system-images" in os.path.basename(z)]
            zips = sys_zips if sys_zips else all_zips

    extracted_dir = None
    if zips:
        zip_path = zips[0]
        extracted_dir = os.path.join(dest_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(extracted_dir)
            for root, _, files in os.walk(extracted_dir):
                if "system.img" in files or "kernel-ranchu" in files:
                    extracted_dir = root
                    break
        except Exception:
            pass

    summary_desc = (
        f"Successfully fetched latest system-image ({successful_target}) build {resolved_build_id or 'latest'} [{arch}] on branch {branch}"
        if is_latest
        else f"Successfully fetched system-image ({successful_target}) build {resolved_build_id} [{arch}]"
    )

    result_data = {
        "status": "success",
        "action": "fetch-build system-image",
        "summary": summary_desc,
        "target_type": "system-image",
        "host": host,
        "arch": arch,
        "mode": "latest" if is_latest else "build_id",
        "build_id": resolved_build_id,
        "target": successful_target,
        "branch": branch,
        "output_directory": dest_dir,
        "downloaded_archives": zips,
    }
    if extracted_dir:
        result_data["extracted_directory"] = extracted_dir

    print_result(result_data, json_mode=json_mode)
