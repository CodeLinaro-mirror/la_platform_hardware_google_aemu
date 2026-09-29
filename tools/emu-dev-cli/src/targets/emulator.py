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

HOST_BRANCH_TARGET_MAP = {
    "linux-x64": {
        "emu-main-dev": "emulator-linux_x64_gfxstream",
        "emu-main-next": "emulator_linux_x64",
    },
    "mac-arm64": {
        "emu-main-dev": "emulator-mac_aarch64_gfxstream",
        "emu-main-next": "emulator_mac_aarch64",
    },
    "mac-x64": {
        "emu-main-dev": "emulator-mac_x64_gfxstream",
        "emu-main-next": "emulator_mac_x64",
    },
}

HOST_ARTIFACT_PATTERNS = {
    "linux-x64": {
        "latest": [
            "sdk-repo-linux-emulator-[0-9]*.zip",
            "sdk-repo-linux-emulator-*.zip",
        ],
        "build_id": "sdk-repo-linux-emulator-{build_id}.zip",
    },
    "mac-arm64": {
        "latest": [
            "sdk-repo-darwin_aarch64-emulator-[0-9]*.zip",
            "sdk-repo-darwin_aarch64-emulator-*.zip",
        ],
        "build_id": "sdk-repo-darwin_aarch64-emulator-{build_id}.zip",
    },
    "mac-x64": {
        "latest": [
            "sdk-repo-darwin-emulator-[0-9]*.zip",
            "sdk-repo-darwin-emulator-*.zip",
        ],
        "build_id": "sdk-repo-darwin-emulator-{build_id}.zip",
    },
}

HOST_CANDIDATES = {
    "linux-x64": [
        {"target": "emulator-linux_x64_gfxstream", "branch": "emu-main-dev"},
        {"target": "emulator_linux_x64", "branch": "emu-main-next"},
        {"target": "sdk_tools_linux", "branch": "unknown"},
    ],
    "mac-arm64": [
        {"target": "emulator-mac_aarch64_gfxstream", "branch": "emu-main-dev"},
        {"target": "emulator_mac_aarch64", "branch": "emu-main-next"},
    ],
    "mac-x64": [
        {"target": "emulator-mac_x64_gfxstream", "branch": "emu-main-dev"},
        {"target": "emulator_mac_x64", "branch": "emu-main-next"},
    ],
}


def register_parser(subparser, default_host):
    parser = subparser.add_parser(
        "emulator",
        help="Fetch Android Emulator binaries"
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
        help="Fetch the latest submitted emulator build on the target branch"
    )
    parser.add_argument(
        "--branch",
        type=str,
        default="emu-main-next",
        choices=["emu-main-dev", "emu-main-next"],
        help="Branch to target when --latest is used (defaults to emu-main-next)"
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
        help="Directory to store downloaded artifacts (defaults to /tmp/emulator-<host>-<build-id>)"
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
        run_emulator_fetch(args)

    parser.set_defaults(func=handler)


def run_emulator_fetch(args):
    host = args.host.lower()
    build_id = getattr(args, "build_id", None)
    is_latest = getattr(args, "latest", False)
    branch = getattr(args, "branch", "emu-main-next")
    json_mode = getattr(args, "json", False)
    token = getattr(args, "token", None)

    if not getattr(args, "skip_auth_check", False):
        from lib.android_build_client import check_authentication_status
        auth_status = check_authentication_status(token=token)
        if not auth_status["valid"]:
            res_data = {
                "status": "error",
                "action": "fetch-build emulator",
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
        dest_dir = f"/tmp/emulator-{host}-{build_id}" if build_id else f"/tmp/emulator_tmp_{os.getpid()}"
    os.makedirs(dest_dir, exist_ok=True)

    successful_target = None
    successful_branch = None
    resolved_build_id = build_id
    last_err = ""

    # Strategy 1: Use fetch_artifact CLI tool if available (e.g. Linux CFS)
    if fetch_tool:
        if is_latest:
            target_map = HOST_BRANCH_TARGET_MAP.get(host, HOST_BRANCH_TARGET_MAP["linux-x64"])
            target_name = target_map.get(branch, "emulator_linux_x64")
            patterns = HOST_ARTIFACT_PATTERNS.get(host, HOST_ARTIFACT_PATTERNS["linux-x64"])["latest"]
            for pattern in patterns:
                cmd = [fetch_tool, "--branch", branch, "--target", target_name, "--latest", pattern, dest_dir]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and glob.glob(os.path.join(dest_dir, "*.zip")):
                    successful_target = target_name
                    successful_branch = branch
                    break
                else:
                    last_err = res.stderr.strip() or res.stdout.strip()
        else:
            pattern = HOST_ARTIFACT_PATTERNS.get(host, HOST_ARTIFACT_PATTERNS["linux-x64"])["build_id"].format(build_id=build_id)
            candidates = HOST_CANDIDATES.get(host, HOST_CANDIDATES["linux-x64"])
            for c in candidates:
                target_name, branch_name = c["target"], c["branch"]
                cmd = [fetch_tool, "--bid", build_id, "--target", target_name, pattern, dest_dir]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and glob.glob(os.path.join(dest_dir, "*.zip")):
                    successful_target = target_name
                    successful_branch = branch_name
                    break
                else:
                    last_err = res.stderr.strip() or res.stdout.strip()
                if successful_target:
                    break

    # Strategy 2: Direct Android Build v4 REST API fallback (e.g. macOS workstations)
    if not successful_target and not fetch_tool:
        try:
            from lib.android_build_client import AndroidBuildClient
            client = AndroidBuildClient(token=token)

            if is_latest:
                target_map = HOST_BRANCH_TARGET_MAP.get(host, HOST_BRANCH_TARGET_MAP["mac-arm64" if host == "mac-arm64" else "linux-x64"])
                target_name = target_map.get(branch, "emulator_mac_aarch64" if host == "mac-arm64" else "emulator_linux_x64")
                resolved_build_id = client.get_latest_build_id(branch, target_name)
                patterns = HOST_ARTIFACT_PATTERNS.get(host, HOST_ARTIFACT_PATTERNS["mac-arm64" if host == "mac-arm64" else "linux-x64"])["latest"]
                matched_art = client.find_matching_artifact(resolved_build_id, target_name, patterns)
                if not matched_art:
                    # Fallback search for any non-symbols emulator zip
                    for art in client.list_artifacts(resolved_build_id, target_name):
                        if "emulator" in art and art.endswith(".zip") and "symbols" not in art and "breakpad" not in art:
                            matched_art = art
                            break
                if matched_art:
                    client.download_artifact(resolved_build_id, target_name, matched_art, dest_dir)
                    successful_target = target_name
                    successful_branch = branch
                else:
                    last_err = f"No matching emulator artifact found in build {resolved_build_id} on {target_name}"
            else:
                resolved_build_id = build_id
                pattern = HOST_ARTIFACT_PATTERNS.get(host, HOST_ARTIFACT_PATTERNS["mac-arm64" if host == "mac-arm64" else "linux-x64"])["build_id"].format(build_id=build_id)
                candidates = HOST_CANDIDATES.get(host, HOST_CANDIDATES["mac-arm64" if host == "mac-arm64" else "linux-x64"])
                for c in candidates:
                    target_name, branch_name = c["target"], c["branch"]
                    try:
                        matched_art = client.find_matching_artifact(resolved_build_id, target_name, [pattern, f"*-emulator-{build_id}.zip"])
                        if matched_art:
                            client.download_artifact(resolved_build_id, target_name, matched_art, dest_dir)
                            successful_target = target_name
                            successful_branch = branch_name
                            break
                    except Exception as e:
                        last_err = str(e)
        except Exception as e:
            last_err = str(e)

    if not successful_target:
        desc = f"latest on branch {branch}" if is_latest else f"build {build_id}"
        print_result({
            "status": "error",
            "action": "fetch-build emulator",
            "summary": f"Failed to fetch emulator artifact for {desc} (host: {host})",
            "error_message": last_err or "Artifact download failed.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)

    all_zips = glob.glob(os.path.join(dest_dir, "*.zip"))
    zips = [z for z in all_zips if "symbols" not in os.path.basename(z) and "breakpad" not in os.path.basename(z)]
    if not zips and all_zips:
        zips = all_zips

    if zips and not resolved_build_id:
        m = re.search(r"-(\d+)\.zip$", os.path.basename(zips[0]))
        if m:
            resolved_build_id = m.group(1)

    # Rename temporary directory to include actual numeric build_id if output_dir was not explicitly provided
    if not custom_output_dir and resolved_build_id:
        final_dir = f"/tmp/emulator-{host}-{resolved_build_id}"
        if dest_dir != final_dir:
            if os.path.exists(final_dir):
                shutil.rmtree(final_dir)
            shutil.move(dest_dir, final_dir)
            dest_dir = final_dir
            zips = glob.glob(os.path.join(dest_dir, "*.zip"))
            main_zips = [z for z in zips if "symbols" not in os.path.basename(z) and "breakpad" not in os.path.basename(z)]
            if main_zips:
                zips = main_zips

    extracted_dir = None
    binary_path = None

    if zips:
        zip_path = zips[0]
        extracted_dir = os.path.join(dest_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(extracted_dir)
            if platform.system().lower() != "windows":
                for root, _, files in os.walk(extracted_dir):
                    is_bin_dir = os.path.basename(root) == "bin"
                    for f in files:
                        if is_bin_dir or f.startswith("emulator") or f.startswith("qemu-") or f.startswith("e2fsprogs") or f.endswith(".sh"):
                            fp = os.path.join(root, f)
                            try:
                                os.chmod(fp, 0o755)
                            except Exception:
                                pass
            candidates_bin = [
                os.path.join(extracted_dir, "emulator", "emulator"),
                os.path.join(extracted_dir, "emulator"),
            ]
            for cb in candidates_bin:
                if os.path.isfile(cb):
                    binary_path = cb
                    break
        except Exception:
            pass

    summary_desc = (
        f"Successfully fetched latest emulator build ({resolved_build_id or 'latest'}) on {successful_branch}"
        if is_latest
        else f"Successfully fetched emulator build {resolved_build_id} ({host}) via branch {successful_branch}"
    )

    result_data = {
        "status": "success",
        "action": "fetch-build emulator",
        "summary": summary_desc,
        "target_type": "emulator",
        "host": host,
        "mode": "latest" if is_latest else "build_id",
        "build_id": resolved_build_id,
        "target": successful_target,
        "branch": successful_branch,
        "output_directory": dest_dir,
        "downloaded_archives": zips,
    }
    if extracted_dir:
        result_data["extracted_directory"] = extracted_dir
    if binary_path:
        result_data["emulator_binary"] = binary_path

    print_result(result_data, json_mode=json_mode)
