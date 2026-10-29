import argparse
import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from commands.source_directory import (
    find_file_in_source_directories,
    get_source_directory,
)
from core.command import (
    Command,
    CommandResult,
    STATUS_ERROR,
    STATUS_SUCCESS,
    bind_command,
)
from core.context import (
    ExecutionContext,
    SERVICE_ARTIFACT,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import ExecutionError, ExitCode, UsageError
from lib.bazel import find_bazel_cmd
from lib.output import print_result, print_status

PUBLIC_VERIFIER_URLS = {
    "x86":
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-x86.zip",
    "x86_64":
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-x86.zip",
    "arm":
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-arm.zip",
    "arm64":
        "https://dl.google.com/dl/android/cts/android-cts-verifier-17_r1-linux_x86-arm.zip",
}

FETCH_ARTIFACT_BIN = "/google/data/ro/projects/android/fetch_artifact"
_ADB_CMD_TIMEOUT_SECS = 15
_APK_INSTALL_TIMEOUT_SECS = 120
_TEST_SCRIPT_TIMEOUT_SECS = 600
_BAZEL_RUN_TIMEOUT_SECS = 1800


def find_verifier_scripts_dir():
    rel_path = os.path.join("third_party", "adt-infra", "goldfish_test", "xts",
                            "verifier")
    found = find_file_in_source_directories(rel_path)
    if found:
        return found, f"source-directory registry ({found})"

    # Fallback: walk relative directories from script location
    curr = os.path.abspath(__file__)
    for _ in range(7):
        curr = os.path.dirname(curr)
        candidate = os.path.join(curr, rel_path)
        if os.path.isdir(candidate):
            return candidate, f"relative repo path ({candidate})"

    return None, None


def discover_available_modules(scripts_dir):
    """
    Dynamically scans third_party/adt-infra/goldfish_test/xts/verifier/
    for pass_*.py automation scripts without hardcoding module lists.
    """
    modules = {}
    if not scripts_dir or not os.path.isdir(scripts_dir):
        return modules

    for script_name in os.listdir(scripts_dir):
        if not script_name.startswith("pass_") or not script_name.endswith(
                ".py"):
            continue
        # Base alias from filename: strip 'pass_' and '_test.py'
        base_name = script_name[len("pass_"):-len(".py")]
        if base_name.endswith("_test"):
            base_name = base_name[:-len("_test")]

        modules[base_name] = script_name

        # Create short convenient aliases (e.g. 'pass_other_tts_test.py' -> 'tts', 'pass_vibrations_has_vibrator_test.py' -> 'vibrations')
        short_parts = base_name.split("_")
        if len(short_parts) > 1:
            # Add last word (e.g., 'tts', 'vibrator', 'service') if not ambiguous
            short_alias = short_parts[-1]
            if short_alias not in modules:
                modules[short_alias] = script_name
            # If path was like 'other_battery_saver', register 'battery_saver'
            if len(short_parts) >= 3 and short_parts[0] in (
                    "other", "tiles", "projection", "sharesheet", "vibrations"):
                sub_alias = "_".join(short_parts[1:])
                if sub_alias not in modules:
                    modules[sub_alias] = script_name

    return modules


def register_parser(subparsers):
    cts_parser = subparsers.add_parser(
        "cts",
        help="CTS and CTS-Verifier testing tools for emulator verification")
    cts_subparsers = cts_parser.add_subparsers(dest="cts_cmd",
                                               help="Available CTS subcommands")
    cts_parser.set_defaults(parser=cts_parser, func=run_cts_help)

    verifier_parser = cts_subparsers.add_parser(
        "run-cts-verifier",
        help=
        "Download CTS-Verifier package (public release or go/ab build ID) and execute automated tests"
    )
    verifier_parser.add_argument(
        "--build-id",
        "--bid",
        dest="build_id",
        type=str,
        default=None,
        help=
        "Optional numeric build ID from Android Build (go/ab). If omitted, downloads latest public release from Google CDN"
    )
    verifier_parser.add_argument(
        "--module",
        type=str,
        default=None,
        help=
        "Module name to test (run --list-modules to discover available test scripts dynamically)"
    )
    verifier_parser.add_argument(
        "--all",
        action="store_true",
        help="Run all available automated CTS-Verifier test modules sequentially"
    )
    verifier_parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help=
        "ADB device serial number (auto-detected via 'adb devices' if omitted)")
    verifier_parser.add_argument(
        "--branch",
        type=str,
        default="trunk-release",
        help="Branch when using --build-id (default: trunk-release)")
    verifier_parser.add_argument(
        "--target",
        type=str,
        default=None,
        help=
        "Build target when using --build-id (defaults to test_suites_x86_64 or test_suites_arm64)"
    )
    verifier_parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download and extract CTS-Verifier archive even if cached")
    verifier_parser.add_argument(
        "--list-modules",
        action="store_true",
        help=
        "Dynamically scan and list all automated CTS-Verifier test modules in repository"
    )
    verifier_parser.add_argument(
        "--bazel",
        action="store_true",
        default=True,
        help=
        "Execute hermetic CTS-Verifier test target defined in verifier.bzl using Bazel (default)"
    )
    verifier_parser.add_argument(
        "--no-bazel",
        "--direct",
        dest="bazel",
        action="store_false",
        help=
        "Execute tests directly against an active emulator device over ADB instead of using Bazel"
    )
    verifier_parser.add_argument(
        "--rbe",
        action="store_true",
        default=False,
        help=
        "Execute test target on Remote Build Execution (RBE) using Bazel remote config"
    )
    verifier_parser.add_argument(
        "--window",
        action="store_true",
        default=True,
        help="Launch emulator with GUI window enabled (default via bazel run)")
    verifier_parser.add_argument(
        "--no-window",
        "--headless",
        dest="window",
        action="store_false",
        help="Launch emulator in headless mode without GUI window")
    verifier_parser.add_argument(
        "--test-builder-mode",
        "--test-builder",
        action="store_true",
        dest="test_builder_mode",
        help=
        "Run module using TestBuilder interactive automation generator (automation_dev/test_builder.py)"
    )
    verifier_parser.add_argument(
        "--collect-tests",
        "--collect-test-module-names",
        "--collect-labels",
        action="store_true",
        dest="collect_test_module_names",
        help=
        "Scroll through CtsVerifier on device and collect all test activity labels into a JSON file"
    )
    bind_command(
        verifier_parser,
        CtsRunVerifierCommand(),
        configure=False,
        legacy_func=run_cts_verifier,
    )
    verifier_parser.set_defaults(parser=verifier_parser)


def run_cts_help(args):
    json_mode = getattr(args, "json", False)
    if not json_mode and hasattr(args, "parser"):
        args.parser.print_help()
        sys.exit(0)
    print_result(
        {
            "status": "error",
            "action": "cts",
            "error_message": "Subcommand required: run-cts-verifier",
            "exit_code": 1
        },
        json_mode=json_mode,
        is_error=True)
    sys.exit(1)


def get_adb_serial(requested_serial=None):
    if requested_serial:
        return requested_serial
    res = subprocess.run(["adb", "devices"],
                         capture_output=True,
                         text=True,
                         check=False,
                         timeout=_ADB_CMD_TIMEOUT_SECS)
    lines = res.stdout.strip().splitlines()
    devices = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        return None
    return devices[0]


def get_device_arch(serial):
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", "getprop", "ro.product.cpu.abi"])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=_ADB_CMD_TIMEOUT_SECS)
    abi = res.stdout.strip().lower()
    if "arm" in abi or "aarch64" in abi:
        return "arm64"
    return "x86_64"


def download_with_progress(url, dest_path, json_mode=False):
    print_status(
        f"Downloading CTS-Verifier package from:\n  {url}\n  -> {dest_path}",
        json_mode=json_mode,
    )

    def reporthook(blocknum, blocksize, totalsize):
        if json_mode:
            return
        readsofar = blocknum * blocksize
        if totalsize > 0:
            percent = readsofar * 100 / totalsize
            mb_read = readsofar / (1024 * 1024)
            mb_total = totalsize / (1024 * 1024)
            sys.stdout.write(
                f"\rDownloading... {percent:5.1f}% [{mb_read:.1f}MB / {mb_total:.1f}MB]"
            )
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest_path, reporthook)
    if not json_mode:
        sys.stdout.write("\nDownload complete.\n")
        sys.stdout.flush()


def fetch_from_android_build(
    build_id,
    branch,
    target=None,
    arch=None,
    dest_zip=None,
    json_mode=False,
    *,
    artifact_service=None,
    runner=None,
):
    if dest_zip is None and arch is not None and target is not None:
        dest_zip = arch
        arch = "x86_64"
        target = branch
        branch = "trunk-release"

    target_name = target or ("test_suites_arm64"
                             if arch == "arm64" else "test_suites_x86_64")
    if artifact_service is not None:
        artifact_name = (
            os.path.basename(dest_zip)
            if dest_zip and str(dest_zip).endswith(".zip")
            else "android-cts-verifier.zip"
        )
        dest_dir = (
            os.path.dirname(str(dest_zip))
            if dest_zip and str(dest_zip).endswith(".zip")
            else str(dest_zip or ".")
        )
        if hasattr(artifact_service, "fetch_artifact"):
            artifact_service.fetch_artifact(
                build_id=str(build_id),
                target=target_name,
                artifact_name=artifact_name,
                dest_dir=dest_dir,
                branch=branch,
            )
            return True
        if hasattr(artifact_service, "download_artifact"):
            artifact_service.download_artifact(
                build_id=str(build_id),
                target=target_name,
                artifact_name=artifact_name,
                dest_dir=dest_dir,
                branch=branch,
            )
            return True

    fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(
        FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")
    if not fetch_tool and runner is None:
        raise RuntimeError(
            "Neither /google/data/ro/projects/android/fetch_artifact nor 'fetch_artifact' in PATH was found."
        )
    fetch_tool = fetch_tool or FETCH_ARTIFACT_BIN
    cmd = [
        fetch_tool, "--bid", str(build_id), "--branch", str(branch), "--target",
        target_name, "android-cts-verifier.zip", str(dest_zip)
    ]
    print_status(
        f"Fetching CTS-Verifier from Android Build (bid={build_id}, branch={branch}, target={target_name})...",
        json_mode=json_mode,
    )
    if runner is not None:
        res = runner.run(cmd, check=False, timeout_sec=float(_TEST_SCRIPT_TIMEOUT_SECS))
    else:
        res = subprocess.run(cmd, check=False, timeout=_TEST_SCRIPT_TIMEOUT_SECS)
    if res.returncode != 0:
        raise RuntimeError(
            f"fetch_artifact failed with exit code {res.returncode}")
    return True


def run_cts_verifier(args):
    json_mode = getattr(args, "json", False)
    scripts_dir, origin_info = find_verifier_scripts_dir()
    discovered_modules = discover_available_modules(scripts_dir)

    if not getattr(args, "module",
                   None) and not getattr(args, "all", False) and not getattr(
                       args, "list_modules", False) and not getattr(
                           args, "collect_test_module_names", False):
        if not json_mode and hasattr(args, "parser"):
            args.parser.print_help()
            print(
                "\n❌ error: Either --module <name>, --all, --list-modules, or --collect-test-module-names must be specified."
            )
            sys.exit(1)
        print_result(
            {
                "status":
                    "error",
                "action":
                    "run-cts-verifier",
                "error_message":
                    "Either --module <name>, --all, --list-modules, or --collect-test-module-names must be specified.",
                "exit_code":
                    1
            },
            json_mode=json_mode,
            is_error=True)
        sys.exit(1)

    if args.list_modules:
        if not json_mode:
            print(
                f"Dynamically Discovered Automated CTS-Verifier Modules ({len(discovered_modules)} modules found):"
            )
            print(
                f"Source directory: {scripts_dir or 'Not found'} (Resolved via {origin_info or 'Unknown'})\n"
            )
            for mod_name, script_file in sorted(discovered_modules.items()):
                print(f"  • {mod_name:<35} -> {script_file}")
        else:
            print_result(
                {
                    "status": "success",
                    "action": "list-modules",
                    "scripts_directory": scripts_dir,
                    "modules": discovered_modules
                },
                json_mode=True)
        sys.exit(0)

    # Auto-disable Bazel mode if user specified non-default configuration parameters
    has_custom_config = (bool(getattr(args, "serial", None)) or
                         bool(getattr(args, "build_id", None)) or
                         bool(getattr(args, "target", None)) or
                         bool(getattr(args, "force_download", False)) or
                         (getattr(args, "branch", "trunk-release")
                          != "trunk-release"))

    use_bazel = getattr(args, "bazel", True) and not has_custom_config

    if getattr(args, "collect_test_module_names", False) and not use_bazel:
        builder_script = os.path.join(scripts_dir, "automation_dev",
                                      "test_builder.py")
        print_status(
            "🔍 Running CtsVerifier test label collector over ADB...",
            json_mode=json_mode,
        )
        res = subprocess.run(
            [sys.executable, builder_script, "--collect-test-module-names"],
            check=False,
            timeout=_TEST_SCRIPT_TIMEOUT_SECS)
        sys.exit(res.returncode)

    if use_bazel:
        source_dir = get_source_directory("emu-main-next")
        if not source_dir or not os.path.exists(
                os.path.join(
                    source_dir,
                    "third_party/adt-infra/goldfish_test/xts/verifier.bzl")):
            source_dir = os.getcwd()

        bazel_bin = find_bazel_cmd(source_dir)

        if getattr(args, "collect_test_module_names", False):
            target = "@goldfish_test//xts:cts-verifier-automation-dev"
            print_status(
                f"Executing hermetic CTS-Verifier test label collector via Bazel ({bazel_bin}): {target}...",
                json_mode=json_mode,
            )
            cmd = [
                bazel_bin, "run", target, "--", "--collect_tests", "--window"
            ]
            res = subprocess.run(cmd, cwd=source_dir, check=False, timeout=_BAZEL_RUN_TIMEOUT_SECS)
            sys.exit(res.returncode)

        if getattr(args, "test_builder_mode", False):
            target = "@goldfish_test//xts:cts-verifier-automation-dev"
            script_name = "run_screen_lock_test.sh"
            if args.module:
                raw_mod = args.module.lower().replace("pass_", "").replace(
                    "run_",
                    "").replace(".py", "").replace(".sh", "").replace(" ", "_")
                if not raw_mod.endswith("_test"):
                    raw_mod += "_test"
                script_name = f"run_{raw_mod}.sh"
            print_status(
                f"Executing hermetic CTS-Verifier TestBuilder via Bazel ({bazel_bin}): {target}...",
                json_mode=json_mode,
            )
            cmd = [
                bazel_bin, "run", target, "--", "--dev_mode", "--window",
                "--script", script_name
            ]
            res = subprocess.run(cmd, cwd=source_dir, check=False, timeout=_BAZEL_RUN_TIMEOUT_SECS)
            sys.exit(res.returncode)

        if args.all or not args.module:
            target = "@goldfish_test//xts:ets-verifier"
        else:
            mod = args.module.lower().replace("pass_",
                                              "").replace("run_", "").replace(
                                                  ".py", "").replace(".sh", "")
            subname = None
            if mod in ("clock", "clocktest", "clock_test"):
                subname = "ClockTest"
            elif mod in discovered_modules:
                script_file = discovered_modules[mod]
                subname = script_file.replace("pass_", "").replace(".py", "")
            else:
                verifier_dir = os.path.join(source_dir, "third_party",
                                            "adt-infra", "goldfish_test", "xts",
                                            "verifier")
                if os.path.exists(verifier_dir):
                    sh_files = sorted([
                        f for f in os.listdir(verifier_dir)
                        if f.startswith("run_") and f.endswith(".sh")
                    ])
                    for sh in sh_files:
                        s_sub = sh.replace("run_", "").replace(".sh", "")
                        if s_sub == mod or s_sub == f"{mod}_test":
                            subname = s_sub
                            break
                    if not subname:
                        for sh in sh_files:
                            s_sub = sh.replace("run_", "").replace(".sh", "")
                            if mod in s_sub:
                                subname = s_sub
                                break
            if not subname:
                subname = mod
            target = f"@goldfish_test//xts:ets-verifier.{subname}"

        if getattr(args, "rbe", False):
            print_status(
                f"Executing hermetic ETS-Verifier test target on Remote Build Execution (RBE) via Bazel ({bazel_bin}): {target}...",
                json_mode=json_mode,
            )
            cmd = [
                bazel_bin, "test", "-c", "opt", "--config=remote",
                "--sandbox_debug", "--nocache_test_results", "--config=ants",
                "--config=sponge", "--flaky_test_attempts=8", target
            ]
        elif args.all or not args.module:
            print_status(
                f"Executing hermetic ETS-Verifier test suite via Bazel ({bazel_bin}): {target}...",
                json_mode=json_mode,
            )
            cmd = [bazel_bin, "test", target]
            if not getattr(args, "window", True):
                cmd.append("--test_arg=--no-window")
            else:
                cmd.append("--test_arg=--window")
        else:
            print_status(
                f"Executing hermetic ETS-Verifier test target via Bazel ({bazel_bin}): {target}...",
                json_mode=json_mode,
            )
            cmd = [bazel_bin, "run", target]
            if not getattr(args, "window", True):
                cmd.extend(["--", "--no-window"])
            else:
                cmd.extend(["--", "--window"])

        res = subprocess.run(cmd, cwd=source_dir, check=False, timeout=_BAZEL_RUN_TIMEOUT_SECS)
        sys.exit(res.returncode)

    serial = get_adb_serial(args.serial)
    if not serial and not args.list_modules:
        print_result(
            {
                "status":
                    "error",
                "action":
                    "run-cts-verifier",
                "error_message":
                    "No online ADB device/emulator found. Launch an emulator first or pass --serial.",
                "exit_code":
                    1
            },
            json_mode=json_mode,
            is_error=True)
        sys.exit(1)

    arch = get_device_arch(serial)
    arch_key = "arm64" if arch == "arm64" else "x86_64"

    # Determine caching directory and source for zip
    if args.build_id:
        cache_dir = f"/tmp/cts-verifier-bid-{args.build_id}-{arch_key}"
        dest_zip = os.path.join(cache_dir, "android-cts-verifier.zip")
        extracted_apk = os.path.join(cache_dir, "extracted",
                                     "android-cts-verifier", "CtsVerifier.apk")
    else:
        cache_dir = f"/tmp/cts-verifier-public-17_r1-{arch_key}"
        dest_zip = os.path.join(cache_dir, "android-cts-verifier.zip")
        extracted_apk = os.path.join(cache_dir, "extracted",
                                     "android-cts-verifier", "CtsVerifier.apk")

    os.makedirs(cache_dir, exist_ok=True)

    # Step 1: Download or fetch zip artifact
    if args.force_download or not os.path.exists(extracted_apk):
        os.makedirs(os.path.join(cache_dir, "extracted"), exist_ok=True)
        if args.build_id:
            fetch_from_android_build(args.build_id, args.branch, args.target,
                                     arch_key, dest_zip, json_mode=json_mode)
        else:
            url = PUBLIC_VERIFIER_URLS.get(arch_key,
                                           PUBLIC_VERIFIER_URLS["x86_64"])
            download_with_progress(url, dest_zip, json_mode=json_mode)

        print_status(
            "Extracting CTS-Verifier package contents from archive...",
            json_mode=json_mode,
        )
        with zipfile.ZipFile(dest_zip, "r") as zf:
            zf.extractall(os.path.join(cache_dir, "extracted"))

    if not os.path.exists(extracted_apk):
        raise RuntimeError(f"CtsVerifier.apk not accessible at {extracted_apk}")

    print_status(
        f"✓ CtsVerifier.apk ready at: {extracted_apk}",
        json_mode=json_mode,
    )

    # Step 2: Install APK and grant permissions over ADB
    adb_prefix = ["adb"]
    if serial:
        adb_prefix.extend(["-s", serial])

    print_status(
        f"Installing CtsVerifier.apk onto device {serial}...",
        json_mode=json_mode,
    )
    inst_res = subprocess.run(adb_prefix +
                              ["install", "-r", "-g", extracted_apk],
                              capture_output=True,
                              text=True,
                              check=False,
                              timeout=_APK_INSTALL_TIMEOUT_SECS)
    if inst_res.returncode != 0:
        print_status(
            f"⚠️  adb install warning: {inst_res.stderr.strip()}",
            json_mode=json_mode,
        )
        if "INSTALL_FAILED_OLDER_SDK" in inst_res.stderr:
            print_status(
                "  Hint: DEVICE is a release build while APK targeted DEV preview. Supply matching --build-id.",
                json_mode=json_mode,
            )

    # Install companion helper APKs (e.g. CtsEmptyDeviceAdmin.apk, CtsEmptyDeviceOwner.apk, etc.)
    extracted_dir = os.path.dirname(extracted_apk)
    if os.path.exists(extracted_dir):
        helper_apks = [
            f for f in os.listdir(extracted_dir)
            if f.endswith(".apk") and f != "CtsVerifier.apk"
        ]
        for helper in helper_apks:
            helper_path = os.path.join(extracted_dir, helper)
            print_status(
                f"Installing companion APK {helper} onto device {serial}...",
                json_mode=json_mode,
            )
            subprocess.run(adb_prefix + ["install", "-r", "-g", helper_path],
                           capture_output=True,
                           text=True,
                           check=False,
                           timeout=_APK_INSTALL_TIMEOUT_SECS)

    # Configure appops / settings policies
    subprocess.run(
        adb_prefix +
        ["shell", "settings", "put", "global", "hidden_api_policy", "1"],
        capture_output=json_mode,
        check=False,
        timeout=_ADB_CMD_TIMEOUT_SECS)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier",
        "android:read_device_identifiers", "allow"
    ],
                   capture_output=json_mode,
                   check=False,
                   timeout=_ADB_CMD_TIMEOUT_SECS)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier",
        "MANAGE_EXTERNAL_STORAGE", "0"
    ],
                   capture_output=json_mode,
                   check=False,
                   timeout=_ADB_CMD_TIMEOUT_SECS)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier", "TURN_SCREEN_ON",
        "0"
    ],
                   capture_output=json_mode,
                   check=False,
                   timeout=_ADB_CMD_TIMEOUT_SECS)
    subprocess.run(adb_prefix + [
        "shell", "am", "compat", "enable", "ALLOW_TEST_API_ACCESS",
        "com.android.cts.verifier"
    ],
                   capture_output=json_mode,
                   check=False,
                   timeout=_ADB_CMD_TIMEOUT_SECS)

    # Step 3: Run requested test module(s)
    if not scripts_dir:
        raise RuntimeError(
            "Could not find third_party/adt-infra/goldfish_test/xts/verifier/ scripts directory."
        )

    if args.all:
        # Run all unique python test scripts
        unique_scripts = sorted(set(discovered_modules.values()))
        print_status("-" * 50, json_mode=json_mode)
        print_status(
            f"🚀 Running ALL {len(unique_scripts)} CTS-Verifier automated modules...",
            json_mode=json_mode,
        )
        print_status("-" * 50, json_mode=json_mode)
        results = []
        overall_failed = 0
        for idx, script_file in enumerate(unique_scripts, 1):
            mod_label = script_file[len("pass_"):-len(".py")]
            print_status(
                f"\n[{idx}/{len(unique_scripts)}] Module: {mod_label} ({script_file})",
                json_mode=json_mode,
            )
            out_dir = f"/tmp/cts_v_results_{mod_label}"
            os.makedirs(out_dir, exist_ok=True)
            env = os.environ.copy()
            env["CTS_APK_PATH"] = extracted_apk
            env["CTS_OUTPUT_DIR"] = out_dir
            if serial:
                env["ANDROID_SERIAL"] = serial
            script_path = os.path.join(scripts_dir, script_file)
            run_res = subprocess.run([sys.executable, script_path],
                                     cwd=scripts_dir,
                                     env=env,
                                     capture_output=json_mode,
                                     check=False,
                                     timeout=_TEST_SCRIPT_TIMEOUT_SECS)
            passed = run_res.returncode == 0
            if not passed:
                overall_failed += 1
            results.append({
                "module": mod_label,
                "script": script_file,
                "passed": passed
            })

        print_status("\n" + "=" * 50, json_mode=json_mode)
        print_status("📊 CTS-Verifier --all Execution Summary:", json_mode=json_mode)
        for r in results:
            status_str = "✓ PASS" if r["passed"] else "❌ FAIL"
            print_status(
                f"  {status_str}  {r['module']:<35} ({r['script']})",
                json_mode=json_mode,
            )
        print_status("=" * 50, json_mode=json_mode)

        print_result(
            {
                "status": "success" if overall_failed == 0 else "error",
                "action": "run-cts-verifier",
                "mode": "all",
                "total_modules": len(results),
                "failed_modules": overall_failed,
                "exit_code": 0 if overall_failed == 0 else 1
            },
            json_mode=json_mode)
        sys.exit(0 if overall_failed == 0 else 1)

    module_name = args.module.lower()
    script_file = discovered_modules.get(module_name)

    # Allow direct matching of script file name if passed e.g. pass_other_tts_test.py
    if not script_file and scripts_dir:
        candidate_file = os.path.join(
            scripts_dir,
            module_name if module_name.endswith(".py") else f"{module_name}.py")
        if os.path.exists(candidate_file):
            script_file = os.path.basename(candidate_file)

    if not script_file:
        print_result(
            {
                "status":
                    "error",
                "action":
                    "run-cts-verifier",
                "error_message":
                    f"Unknown module '{args.module}'. Run with --list-modules to see all dynamically discovered tests.",
                "exit_code":
                    1
            },
            json_mode=json_mode,
            is_error=True)
        sys.exit(1)

    script_path = os.path.join(scripts_dir, script_file)
    if not os.path.exists(script_path):
        raise RuntimeError(f"Test runner script missing at {script_path}")

    out_dir = f"/tmp/cts_v_results_{module_name}"
    os.makedirs(out_dir, exist_ok=True)

    env = os.environ.copy()
    env["CTS_APK_PATH"] = extracted_apk
    env["CTS_OUTPUT_DIR"] = out_dir
    if serial:
        env["ANDROID_SERIAL"] = serial

    print_status("-" * 50, json_mode=json_mode)
    print_status(
        f"🚀 Running CTS-Verifier module: {module_name} ({script_file})",
        json_mode=json_mode,
    )
    print_status("-" * 50, json_mode=json_mode)

    run_res = subprocess.run([sys.executable, script_path],
                             cwd=scripts_dir,
                             env=env,
                             capture_output=json_mode,
                             check=False,
                             timeout=_TEST_SCRIPT_TIMEOUT_SECS)

    passed = run_res.returncode == 0
    xml_files = glob.glob(os.path.join(out_dir, "**", "test_result.xml"),
                          recursive=True)
    xml_report = xml_files[0] if xml_files else None

    print_result(
        {
            "status":
                "success" if passed else "error",
            "action":
                "run-cts-verifier",
            "module":
                module_name,
            "script":
                script_file,
            "device_serial":
                serial,
            "verifier_apk":
                extracted_apk,
            "source_mode":
                f"go/ab build-id {args.build_id}"
                if args.build_id else "public CDN 17_r1",
            "exit_code":
                run_res.returncode,
            "xml_report":
                xml_report,
        },
        json_mode=json_mode)

    sys.exit(run_res.returncode)


class CtsRunVerifierCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli cts run-cts-verifier`."""

    name = "run-cts-verifier"
    help_text = "Download CTS-Verifier package (public release or go/ab build ID) and execute automated tests"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--build-id",
            "--bid",
            dest="build_id",
            type=str,
            default=None,
            help="Optional numeric build ID from Android Build (go/ab)",
        )
        parser.add_argument(
            "--module",
            type=str,
            default=None,
            help="Module name to test (run --list-modules to discover available test scripts dynamically)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run all available automated CTS-Verifier test modules sequentially",
        )
        parser.add_argument(
            "--serial",
            type=str,
            default=None,
            help="ADB device serial number",
        )
        parser.add_argument(
            "--branch",
            type=str,
            default="trunk-release",
            help="Branch when using --build-id (default: trunk-release)",
        )
        parser.add_argument(
            "--target",
            type=str,
            default=None,
            help="Build target when using --build-id",
        )
        parser.add_argument(
            "--force-download",
            action="store_true",
            help="Re-download and extract CTS-Verifier archive even if cached",
        )
        parser.add_argument(
            "--list-modules",
            action="store_true",
            help="Dynamically scan and list all automated CTS-Verifier test modules in repository",
        )
        parser.add_argument(
            "--bazel",
            action="store_true",
            default=True,
            help="Execute hermetic CTS-Verifier test target defined in verifier.bzl using Bazel (default)",
        )
        parser.add_argument(
            "--no-bazel",
            "--direct",
            dest="bazel",
            action="store_false",
            help="Execute tests directly against an active emulator device over ADB",
        )
        parser.add_argument(
            "--rbe",
            action="store_true",
            default=False,
            help="Execute test target on Remote Build Execution (RBE) using Bazel remote config",
        )
        parser.add_argument(
            "--window",
            action="store_true",
            default=True,
            help="Launch emulator with GUI window enabled",
        )
        parser.add_argument(
            "--no-window",
            "--headless",
            dest="window",
            action="store_false",
            help="Launch emulator in headless mode without GUI window",
        )
        parser.add_argument(
            "--test-builder-mode",
            "--test-builder",
            action="store_true",
            dest="test_builder_mode",
            help="Run module using TestBuilder interactive automation generator",
        )
        parser.add_argument(
            "--collect-tests",
            "--collect-test-module-names",
            "--collect-labels",
            action="store_true",
            dest="collect_test_module_names",
            help="Scroll through CtsVerifier on device and collect all test activity labels into a JSON file",
        )

    def _resolve_scripts_dir(self, ctx: ExecutionContext) -> tuple[str | None, str | None]:
        rel_path = os.path.join(
            "third_party", "adt-infra", "goldfish_test", "xts", "verifier"
        )
        for branch_key, src_root in ctx.config.source_directories.items():
            cand = os.path.join(src_root, rel_path)
            if os.path.isdir(cand):
                return cand, f"context config ({branch_key}: {cand})"
        return find_verifier_scripts_dir()

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        if (
            not getattr(args, "module", None)
            and not getattr(args, "all", False)
            and not getattr(args, "list_modules", False)
            and not getattr(args, "collect_test_module_names", False)
        ):
            raise UsageError(
                "No CTS-Verifier execution mode specified.",
                remediation="Specify one of --module <name>, --all, --list-modules, or --collect-test-module-names.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        scripts_dir, origin_info = self._resolve_scripts_dir(ctx)
        discovered_modules = discover_available_modules(scripts_dir)

        if getattr(args, "list_modules", False):
            data: dict[str, Any] = {
                "status": "success",
                "action": "list-modules",
                "scripts_directory": scripts_dir,
                "origin": origin_info,
                "modules": discovered_modules,
            }
            return CommandResult(
                data=data,
                action="list-modules",
                summary=f"Discovered {len(discovered_modules)} automated CTS-Verifier module(s).",
            )

        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)
        artifact_service = ctx.services.get(SERVICE_ARTIFACT)

        has_custom_config = (
            bool(getattr(args, "serial", None))
            or bool(getattr(args, "build_id", None))
            or bool(getattr(args, "target", None))
            or bool(getattr(args, "force_download", False))
            or (getattr(args, "branch", "trunk-release") != "trunk-release")
        )
        use_bazel = bool(getattr(args, "bazel", True)) and not has_custom_config

        if use_bazel:
            source_dir = (
                ctx.config.get_source_directory("emu-main-next")
                or get_source_directory("emu-main-next")
                or os.getcwd()
            )
            bazel_bin = "bazel"
            if getattr(args, "collect_test_module_names", False):
                target = "@goldfish_test//xts:cts-verifier-automation-dev"
                cmd = [
                    bazel_bin,
                    "run",
                    target,
                    "--",
                    "--collect_tests",
                    "--window",
                ]
            elif getattr(args, "test_builder_mode", False):
                target = "@goldfish_test//xts:cts-verifier-automation-dev"
                raw_mod = (
                    str(getattr(args, "module", "") or "screen_lock")
                    .lower()
                    .replace("pass_", "")
                    .replace("run_", "")
                    .replace(".py", "")
                    .replace(".sh", "")
                    .replace(" ", "_")
                )
                if not raw_mod.endswith("_test"):
                    raw_mod += "_test"
                cmd = [
                    bazel_bin,
                    "run",
                    target,
                    "--",
                    "--dev_mode",
                    "--window",
                    "--script",
                    f"run_{raw_mod}.sh",
                ]
            else:
                if getattr(args, "all", False) or not getattr(
                    args, "module", None
                ):
                    target = "@goldfish_test//xts:ets-verifier"
                    cmd = [bazel_bin, "test", target]
                    cmd.append(
                        "--test_arg=--window"
                        if getattr(args, "window", True)
                        else "--test_arg=--no-window"
                    )
                else:
                    mod = (
                        str(args.module)
                        .lower()
                        .replace("pass_", "")
                        .replace("run_", "")
                        .replace(".py", "")
                        .replace(".sh", "")
                    )
                    if mod in ("clock", "clocktest", "clock_test"):
                        subname = "ClockTest"
                    elif mod in discovered_modules:
                        subname = (
                            discovered_modules[mod]
                            .replace("pass_", "")
                            .replace(".py", "")
                        )
                    else:
                        subname = mod
                    target = f"@goldfish_test//xts:ets-verifier.{subname}"
                    if getattr(args, "rbe", False):
                        cmd = [
                            bazel_bin,
                            "test",
                            "-c",
                            "opt",
                            "--config=remote",
                            "--sandbox_debug",
                            "--nocache_test_results",
                            "--config=ants",
                            "--config=sponge",
                            "--flaky_test_attempts=8",
                            target,
                        ]
                    else:
                        cmd = [bazel_bin, "run", target, "--"]
                        cmd.append(
                            "--window"
                            if getattr(args, "window", True)
                            else "--no-window"
                        )

            res = runner.run(
                cmd,
                cwd=source_dir,
                check=False,
                timeout_sec=float(_BAZEL_RUN_TIMEOUT_SECS),
            )
            ok = res.returncode == 0
            return CommandResult(
                data={
                    "status": STATUS_SUCCESS if ok else STATUS_ERROR,
                    "action": "run-cts-verifier",
                    "target": target,
                    "command": cmd,
                    "exit_code": res.returncode,
                },
                action="run-cts-verifier",
                summary=f"Executed Bazel CTS-Verifier target {target} (exit_code={res.returncode}).",
                status=STATUS_SUCCESS if ok else STATUS_ERROR,
                exit_code=(
                    int(ExitCode.SUCCESS) if ok else int(ExitCode.FAILURE)
                ),
            )

        serial = getattr(args, "serial", None)
        if not serial:
            adb_res = runner.run(
                ["adb", "devices"],
                check=False,
                timeout_sec=float(_ADB_CMD_TIMEOUT_SECS),
            )
            for line in (adb_res.stdout or "").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    break
        if not serial:
            raise ExecutionError(
                "No online ADB device/emulator found for direct CTS-Verifier execution.",
                remediation="Launch an emulator first or pass --serial <device_serial>.",
            )

        abi_res = runner.run(
            ["adb", "-s", str(serial), "shell", "getprop", "ro.product.cpu.abi"],
            check=False,
            timeout_sec=float(_ADB_CMD_TIMEOUT_SECS),
        )
        abi = (abi_res.stdout or "").strip().lower()
        arch_key = "arm64" if ("arm" in abi or "aarch64" in abi) else "x86_64"

        build_id = getattr(args, "build_id", None)
        cache_dir = ctx.config.cache_dir / (
            f"cts-verifier-bid-{build_id}-{arch_key}"
            if build_id
            else f"cts-verifier-public-17_r1-{arch_key}"
        )
        extracted_apk = (
            cache_dir / "extracted" / "android-cts-verifier" / "CtsVerifier.apk"
        )
        if getattr(args, "force_download", False) or not extracted_apk.is_file():
            target_name = getattr(args, "target", None) or (
                "test_suites_arm64"
                if arch_key == "arm64"
                else "test_suites_x86_64"
            )
            if build_id:
                artifact_service.download_artifact(
                    build_id=str(build_id),
                    target=target_name,
                    artifact_name="android-cts-verifier.zip",
                    dest_dir=cache_dir,
                    branch=str(getattr(args, "branch", "trunk-release")),
                )

        if not scripts_dir:
            raise ExecutionError(
                "Could not locate third_party/adt-infra/goldfish_test/xts/verifier/ scripts directory.",
                remediation="Configure emu-main-next in source-directory settings before running direct CTS-Verifier scripts.",
            )

        module_name = str(getattr(args, "module", "") or "").lower()
        script_file = discovered_modules.get(module_name)
        if not script_file:
            raise UsageError(
                f"Unknown CTS-Verifier module '{getattr(args, 'module', '')}'.",
                remediation="Run 'emu-dev-cli cts run-cts-verifier --list-modules' to view available modules.",
            )

        script_path = os.path.join(scripts_dir, script_file)
        run_res = runner.run(
            [sys.executable, script_path],
            cwd=scripts_dir,
            check=False,
            timeout_sec=float(_TEST_SCRIPT_TIMEOUT_SECS),
        )
        passed = run_res.returncode == 0
        data = {
            "status": STATUS_SUCCESS if passed else STATUS_ERROR,
            "action": "run-cts-verifier",
            "module": module_name,
            "script": script_file,
            "device_serial": serial,
            "verifier_apk": str(extracted_apk),
            "source_mode": (
                f"go/ab build-id {build_id}"
                if build_id
                else "public CDN 17_r1"
            ),
            "exit_code": run_res.returncode,
            "xml_report": None,
        }
        return CommandResult(
            data=data,
            action="run-cts-verifier",
            summary=f"Executed CTS-Verifier module '{module_name}' on {serial} (exit_code={run_res.returncode}).",
            status=STATUS_SUCCESS if passed else STATUS_ERROR,
            exit_code=(
                int(ExitCode.SUCCESS) if passed else int(ExitCode.FAILURE)
            ),
        )


class CtsCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Top-level `cts` subcommand group."""

    name = "cts"
    help_text = "CTS and CTS-Verifier testing tools for emulator verification"

    def __init__(self) -> None:
        self._verifier_cmd = CtsRunVerifierCommand()

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        cts_subparsers = parser.add_subparsers(
            dest="cts_cmd", help="Available CTS subcommands"
        )
        bind_command(
            cts_subparsers,
            self._verifier_cmd,
            legacy_func=run_cts_verifier,
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        sub_name = str(getattr(args, "cts_cmd", "") or "").strip()
        if sub_name != "run-cts-verifier":
            raise UsageError(
                "Subcommand required for 'cts': run-cts-verifier.",
                remediation="Run 'emu-dev-cli cts run-cts-verifier --list-modules' or pass '--module <name>'.",
            )
        self._verifier_cmd.validate(args, ctx)

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        return self._verifier_cmd.execute(args, ctx)
