import os
import sys
import shutil
import glob
import subprocess
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from lib.output import print_result
from commands.source_directory import get_source_directory, find_file_in_source_directories
from commands.update_cmd import find_bazel_cmd

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
    verifier_parser.set_defaults(parser=verifier_parser, func=run_cts_verifier)


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
                         check=False)
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
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    abi = res.stdout.strip().lower()
    if "arm" in abi or "aarch64" in abi:
        return "arm64"
    return "x86_64"


def download_with_progress(url, dest_path):
    print(f"Downloading CTS-Verifier package from:\n  {url}\n  -> {dest_path}")

    def reporthook(blocknum, blocksize, totalsize):
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
    sys.stdout.write("\nDownload complete.\n")
    sys.stdout.flush()


def fetch_from_android_build(build_id, branch, target, arch, dest_zip):
    fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(
        FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")
    if not fetch_tool:
        raise RuntimeError(
            "Neither /google/data/ro/projects/android/fetch_artifact nor 'fetch_artifact' in PATH was found."
        )
    target_name = target or ("test_suites_arm64"
                             if arch == "arm64" else "test_suites_x86_64")
    cmd = [
        fetch_tool, "--bid", build_id, "--branch", branch, "--target",
        target_name, "android-cts-verifier.zip", dest_zip
    ]
    print(
        f"Fetching CTS-Verifier from Android Build (bid={build_id}, branch={branch}, target={target_name})..."
    )
    res = subprocess.run(cmd, check=False)
    if res.returncode != 0:
        raise RuntimeError(
            f"fetch_artifact failed with exit code {res.returncode}")


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
        print("🔍 Running CtsVerifier test label collector over ADB...")
        res = subprocess.run(
            [sys.executable, builder_script, "--collect-test-module-names"],
            check=False)
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
            print(
                f"Executing hermetic CTS-Verifier test label collector via Bazel ({bazel_bin}): {target}..."
            )
            cmd = [
                bazel_bin, "run", target, "--", "--collect_tests", "--window"
            ]
            res = subprocess.run(cmd, cwd=source_dir, check=False)
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
            print(
                f"Executing hermetic CTS-Verifier TestBuilder via Bazel ({bazel_bin}): {target}..."
            )
            cmd = [
                bazel_bin, "run", target, "--", "--dev_mode", "--window",
                "--script", script_name
            ]
            res = subprocess.run(cmd, cwd=source_dir, check=False)
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
            print(
                f"Executing hermetic ETS-Verifier test target on Remote Build Execution (RBE) via Bazel ({bazel_bin}): {target}..."
            )
            cmd = [
                bazel_bin, "test", "-c", "opt", "--config=remote",
                "--sandbox_debug", "--nocache_test_results", "--config=ants",
                "--config=sponge", "--flaky_test_attempts=8", target
            ]
        elif args.all or not args.module:
            print(
                f"Executing hermetic ETS-Verifier test suite via Bazel ({bazel_bin}): {target}..."
            )
            cmd = [bazel_bin, "test", target]
            if not getattr(args, "window", True):
                cmd.append("--test_arg=--no-window")
            else:
                cmd.append("--test_arg=--window")
        else:
            print(
                f"Executing hermetic ETS-Verifier test target via Bazel ({bazel_bin}): {target}..."
            )
            cmd = [bazel_bin, "run", target]
            if not getattr(args, "window", True):
                cmd.extend(["--", "--no-window"])
            else:
                cmd.extend(["--", "--window"])

        res = subprocess.run(cmd, cwd=source_dir, check=False)
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
                                     arch_key, dest_zip)
        else:
            url = PUBLIC_VERIFIER_URLS.get(arch_key,
                                           PUBLIC_VERIFIER_URLS["x86_64"])
            download_with_progress(url, dest_zip)

        print("Extracting CTS-Verifier package contents from archive...")
        with zipfile.ZipFile(dest_zip, "r") as zf:
            zf.extractall(os.path.join(cache_dir, "extracted"))

    if not os.path.exists(extracted_apk):
        raise RuntimeError(f"CtsVerifier.apk not accessible at {extracted_apk}")

    print(f"✓ CtsVerifier.apk ready at: {extracted_apk}")

    # Step 2: Install APK and grant permissions over ADB
    adb_prefix = ["adb"]
    if serial:
        adb_prefix.extend(["-s", serial])

    print(f"Installing CtsVerifier.apk onto device {serial}...")
    inst_res = subprocess.run(adb_prefix +
                              ["install", "-r", "-g", extracted_apk],
                              capture_output=True,
                              text=True,
                              check=False)
    if inst_res.returncode != 0:
        print(f"⚠️  adb install warning: {inst_res.stderr.strip()}")
        if "INSTALL_FAILED_OLDER_SDK" in inst_res.stderr:
            print(
                "  Hint: DEVICE is a release build while APK targeted DEV preview. Supply matching --build-id."
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
            print(f"Installing companion APK {helper} onto device {serial}...")
            subprocess.run(adb_prefix + ["install", "-r", "-g", helper_path],
                           capture_output=True,
                           text=True,
                           check=False)

    # Configure appops / settings policies
    subprocess.run(
        adb_prefix +
        ["shell", "settings", "put", "global", "hidden_api_policy", "1"],
        check=False)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier",
        "android:read_device_identifiers", "allow"
    ],
                   check=False)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier",
        "MANAGE_EXTERNAL_STORAGE", "0"
    ],
                   check=False)
    subprocess.run(adb_prefix + [
        "shell", "appops", "set", "com.android.cts.verifier", "TURN_SCREEN_ON",
        "0"
    ],
                   check=False)
    subprocess.run(adb_prefix + [
        "shell", "am", "compat", "enable", "ALLOW_TEST_API_ACCESS",
        "com.android.cts.verifier"
    ],
                   check=False)

    # Step 3: Run requested test module(s)
    if not scripts_dir:
        raise RuntimeError(
            "Could not find third_party/adt-infra/goldfish_test/xts/verifier/ scripts directory."
        )

    if args.all:
        # Run all unique python test scripts
        unique_scripts = sorted(set(discovered_modules.values()))
        print("-" * 50)
        print(
            f"🚀 Running ALL {len(unique_scripts)} CTS-Verifier automated modules..."
        )
        print("-" * 50)
        results = []
        overall_failed = 0
        for idx, script_file in enumerate(unique_scripts, 1):
            mod_label = script_file[len("pass_"):-len(".py")]
            print(
                f"\n[{idx}/{len(unique_scripts)}] Module: {mod_label} ({script_file})"
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
                                     check=False)
            passed = run_res.returncode == 0
            if not passed:
                overall_failed += 1
            results.append({
                "module": mod_label,
                "script": script_file,
                "passed": passed
            })

        print("\n" + "=" * 50)
        print("📊 CTS-Verifier --all Execution Summary:")
        for r in results:
            status_str = "✓ PASS" if r["passed"] else "❌ FAIL"
            print(f"  {status_str}  {r['module']:<35} ({r['script']})")
        print("=" * 50)

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

    print("-" * 50)
    print(f"🚀 Running CTS-Verifier module: {module_name} ({script_file})")
    print("-" * 50)

    run_res = subprocess.run([sys.executable, script_path],
                             cwd=scripts_dir,
                             env=env,
                             check=False)

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
