import os
import re
import glob
import shutil
import zipfile
import subprocess
from lib.output import print_result

FETCH_ARTIFACT_BIN = "/google/data/ro/projects/android/fetch_artifact"

BRANCH_TARGET_MAP = {
    "emu-main-dev": "emulator-linux_x64_gfxstream",
    "emu-main-next": "emulator_linux_x64",
}

CANDIDATES = [
    {"target": "emulator-linux_x64_gfxstream", "branch": "emu-main-dev"},
    {"target": "emulator_linux_x64", "branch": "emu-main-next"},
    {"target": "sdk_tools_linux", "branch": "unknown"},
]


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
        choices=["linux-x64"],
        help=f"Target host OS platform (defaults to auto-detected '{default_host}')"
    )
    parser.add_argument(
        "--output-dir", "--output_dir",
        dest="output_dir",
        type=str,
        default=None,
        help="Directory to store downloaded artifacts (defaults to /tmp/emulator-<host>-<build-id>)"
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

    fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")
    if not fetch_tool:
        print_result({
            "status": "error",
            "action": "fetch-build emulator",
            "summary": "fetch_artifact tool not found",
            "error_message": f"Neither {FETCH_ARTIFACT_BIN} nor 'fetch_artifact' in PATH is accessible.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)

    # Temporary download directory if output_dir is not specified yet
    custom_output_dir = args.output_dir
    if custom_output_dir:
        dest_dir = custom_output_dir
    else:
        dest_dir = f"/tmp/emulator-{host}-{build_id}" if build_id else f"/tmp/emulator_tmp_{os.getpid()}"
    os.makedirs(dest_dir, exist_ok=True)

    successful_target = None
    successful_branch = None
    last_err = ""

    if is_latest:
        target_name = BRANCH_TARGET_MAP.get(branch, "emulator_linux_x64")
        artifact_patterns = ["sdk-repo-linux-emulator-[0-9]*.zip"]
        for pattern in artifact_patterns:
            cmd = [fetch_tool, "--branch", branch, "--target", target_name, "--latest", pattern, dest_dir]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and glob.glob(os.path.join(dest_dir, "*.zip")):
                successful_target = target_name
                successful_branch = branch
                break
            else:
                last_err = res.stderr.strip() or res.stdout.strip()
    else:
        artifact_patterns = [
            f"sdk-repo-linux-emulator-{build_id}.zip",
        ]
        for c in CANDIDATES:
            target_name, branch_name = c["target"], c["branch"]
            for pattern in artifact_patterns:
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

    if not successful_target:
        desc = f"latest on branch {branch}" if is_latest else f"build {build_id}"
        print_result({
            "status": "error",
            "action": "fetch-build emulator",
            "summary": f"Failed to fetch emulator artifact for {desc}",
            "last_error": last_err,
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)

    all_zips = glob.glob(os.path.join(dest_dir, "*.zip"))
    zips = [z for z in all_zips if "symbols" not in os.path.basename(z) and "breakpad" not in os.path.basename(z)]
    if not zips and all_zips:
        zips = all_zips

    resolved_build_id = build_id
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
                    for f in files:
                        if f.startswith("emulator") or f.startswith("qemu-system-") or f.startswith("e2fsprogs") or f.endswith(".sh"):
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
