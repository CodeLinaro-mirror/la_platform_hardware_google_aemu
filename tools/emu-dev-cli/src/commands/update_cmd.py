import os
import sys
import shutil
import platform
import subprocess

from commands.source_directory import get_source_directory
from install.installer import install_launcher_wrapper, install_skill, detect_default_install_path
from lib.output import print_result

BAZEL_TARGET = "//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli"
RELATIVE_BUILT_BIN = os.path.join("bazel-bin", "hardware", "google", "aemu", "tools", "emu-dev-cli", "emu-dev-cli")
if platform.system().lower() == "windows":
    RELATIVE_BUILT_BIN += ".exe"


def register_parser(subparsers):
    update_parser = subparsers.add_parser(
        "update",
        help="Rebuild emu-dev-cli from local emu-main-next source directory and re-install executable"
    )
    update_parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Destination executable path override"
    )
    update_parser.set_defaults(func=run_update_cmd)


def find_bazel_cmd(source_dir):
    # 1. Check local repo bazel wrapper script
    local_bazel = os.path.join(source_dir, "prebuilts", "bazel", "linux-x86_64", "bazel")
    if os.path.exists(local_bazel) and os.access(local_bazel, os.X_OK):
        return local_bazel
    # 2. Check system PATH
    system_bazel = shutil.which("bazel")
    if system_bazel:
        return system_bazel
    return "bazel"


def run_update_cmd(args):
    json_mode = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)
    dest_path = getattr(args, "path", None) or detect_default_install_path()

    # 1. Resolve emu-main-next source directory
    source_dir = get_source_directory("emu-main-next")
    if not source_dir or not os.path.exists(source_dir):
        pwd = os.getcwd()
        if os.path.exists(os.path.join(pwd, "hardware", "google", "aemu", "tools", "emu-dev-cli", "BUILD.bazel")):
            source_dir = pwd
        elif os.path.exists("/work/emu-main-next/hardware/google/aemu/tools/emu-dev-cli/BUILD.bazel"):
            source_dir = "/work/emu-main-next"

    if not source_dir or not os.path.exists(source_dir):
        print_result({
            "status": "error",
            "action": "update",
            "error_message": "No valid source directory found for branch 'emu-main-next'. Use 'emu-dev-cli source-directory set emu-main-next <path>' first.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    bazel_cmd = find_bazel_cmd(source_dir)
    print(f"🔨 Building {BAZEL_TARGET} in {source_dir}...")

    cmd = [bazel_cmd, "build", BAZEL_TARGET]
    try:
        if verbose or not json_mode:
            res = subprocess.run(cmd, cwd=source_dir, check=False)
        else:
            res = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True, check=False)

        if res.returncode != 0:
            err_msg = getattr(res, "stderr", None) or f"Bazel build exited with code {res.returncode}"
            print_result({
                "status": "error",
                "action": "update",
                "source_directory": source_dir,
                "error_message": f"Failed to build {BAZEL_TARGET}: {err_msg}",
                "exit_code": res.returncode
            }, json_mode=json_mode, is_error=True)
            sys.exit(res.returncode)

    except Exception as e:
        print_result({
            "status": "error",
            "action": "update",
            "source_directory": source_dir,
            "error_message": f"Exception while running Bazel: {e}",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    # 2. Locate built executable binary
    built_bin = os.path.abspath(os.path.join(source_dir, RELATIVE_BUILT_BIN))
    if not os.path.exists(built_bin):
        print_result({
            "status": "error",
            "action": "update",
            "source_directory": source_dir,
            "built_bin": built_bin,
            "error_message": f"Built executable not found at expected path: {built_bin}",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    print(f"📦 Re-installing compiled emu-dev-cli release package from {built_bin}...")
    install_launcher_wrapper(built_bin, dest_path)
    skill_files = install_skill()

    print_result({
        "status": "success",
        "action": "update",
        "summary": f"Successfully updated and re-installed emu-dev-cli from {source_dir}",
        "source_directory": source_dir,
        "built_bin": built_bin,
        "launcher_path": str(dest_path),
        "skill_files": skill_files,
    }, json_mode=json_mode)
