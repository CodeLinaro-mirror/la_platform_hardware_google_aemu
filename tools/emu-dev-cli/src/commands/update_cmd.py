import importlib.util
import os
import sys
import shutil
import platform
import subprocess

from commands.source_directory import get_source_directory, ensure_codesearch_urls_config
from install.installer import install_launcher_wrapper, install_skill, detect_default_install_path, resolve_built_artifact
from lib.bazel import find_bazel_cmd
from lib.output import print_result

BAZEL_TARGET = "@aemu//tools/emu-dev-cli:emu-dev-cli"


def _resolve_installer_hooks(source_dir):
    """Loads (install_launcher_wrapper, install_skill) from source_dir so updates to installer.py take effect immediately."""
    wrapper_fn = install_launcher_wrapper
    skill_fn = install_skill
    if hasattr(wrapper_fn, "_mock_name") or hasattr(skill_fn, "_mock_name"):
        return wrapper_fn, skill_fn

    source_src_dir = os.path.join(
        source_dir, "hardware", "google", "aemu", "tools", "emu-dev-cli", "src"
    )
    source_installer = os.path.join(source_src_dir, "install", "installer.py")
    if not os.path.isfile(source_installer):
        return wrapper_fn, skill_fn

    try:
        if source_src_dir not in sys.path:
            sys.path.insert(0, source_src_dir)
        for pkg_name in ("commands", "install", "lib", "targets"):
            pkg_mod = sys.modules.get(pkg_name)
            pkg_dir = os.path.join(source_src_dir, pkg_name)
            if pkg_mod is not None and hasattr(pkg_mod, "__path__") and os.path.isdir(pkg_dir):
                if pkg_dir not in pkg_mod.__path__:
                    pkg_mod.__path__.insert(0, pkg_dir)

        spec = importlib.util.spec_from_file_location(
            "_emu_dev_cli_source_installer", source_installer
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            wrapper_fn = getattr(mod, "install_launcher_wrapper", wrapper_fn)
            skill_fn = getattr(mod, "install_skill", skill_fn)
    except Exception:
        pass
    return wrapper_fn, skill_fn


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
    advisor_target = "@goldfish//emulator/crashreport/tool/advisor:advisor"
    crashreport_target = "@goldfish//emulator/crashreport/tool:crashreport"
    print(f"🔨 Building {BAZEL_TARGET}, {advisor_target}, and {crashreport_target} in {source_dir}...")

    cmd = [bazel_cmd, "build", BAZEL_TARGET, advisor_target, crashreport_target]
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
    built_bin = resolve_built_artifact(source_dir, BAZEL_TARGET, "emu-dev-cli")
    if not built_bin or not os.path.exists(built_bin):
        print_result({
            "status": "error",
            "action": "update",
            "source_directory": source_dir,
            "built_bin": str(built_bin) if built_bin else None,
            "error_message": f"Built executable for {BAZEL_TARGET} could not be resolved via Bazel cquery.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    print(f"📦 Re-installing compiled emu-dev-cli release package from {built_bin}...")
    wrapper_fn, skill_fn = _resolve_installer_hooks(source_dir)
    wrapper_fn(built_bin, dest_path, source_dir=source_dir)
    skill_files = skill_fn(source_dir=source_dir)
    ensure_codesearch_urls_config()

    print_result({
        "status": "success",
        "action": "update",
        "summary": f"Successfully updated and re-installed emu-dev-cli from {source_dir}",
        "source_directory": source_dir,
        "built_bin": built_bin,
        "launcher_path": str(dest_path),
        "skill_files": skill_files,
    }, json_mode=json_mode)
