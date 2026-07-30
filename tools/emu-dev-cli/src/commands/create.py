import os
import re
import shutil
import sys
from pathlib import Path
from lib.output import print_result

DEVICE_PROFILES = {
    "small_phone": {
        "width": 720,
        "height": 1280,
        "density": 320,
        "ram": 2048,
        "heap": 228,
        "disk": "6G",
        "description": "Small Phone (720x1280, 320 dpi, 2GB RAM)"
    },
    "medium_phone": {
        "width": 1080,
        "height": 2400,
        "density": 420,
        "ram": 2048,
        "heap": 228,
        "disk": "6G",
        "description": "Medium Phone (1080x2400, 420 dpi, 2GB RAM) [DEFAULT]"
    },
    "medium_tablet": {
        "width": 1600,
        "height": 2560,
        "density": 320,
        "ram": 4096,
        "heap": 384,
        "disk": "8G",
        "description": "Medium Tablet (1600x2560, 320 dpi, 4GB RAM)"
    },
    "small_desktop": {
        "width": 1366,
        "height": 768,
        "density": 160,
        "ram": 4096,
        "heap": 384,
        "disk": "8G",
        "description": "Small Desktop (1366x768, 160 dpi, 4GB RAM)"
    },
    "medium_desktop": {
        "width": 1920,
        "height": 1080,
        "density": 160,
        "ram": 8192,
        "heap": 512,
        "disk": "16G",
        "description": "Medium Desktop (1920x1080, 160 dpi, 8GB RAM)"
    },
    "large_desktop": {
        "width": 2560,
        "height": 1440,
        "density": 160,
        "ram": 8192,
        "heap": 512,
        "disk": "16G",
        "description": "Large Desktop (2560x1440, 160 dpi, 8GB RAM)"
    },
}


def find_actual_sysimg_dir(sysimg_dir):
    """
    If sysimg_dir is a root extracted directory, locate the subfolder
    containing system.img or kernel-ranchu (e.g. extracted/x86_64/).
    """
    if os.path.exists(os.path.join(sysimg_dir, "system.img")) or os.path.exists(os.path.join(sysimg_dir, "kernel-ranchu")):
        return sysimg_dir

    for root, _, files in os.walk(sysimg_dir):
        if "system.img" in files or "kernel-ranchu" in files:
            return root

    return sysimg_dir


def detect_arch_and_abi(sysimg_dir):
    source_props = os.path.join(sysimg_dir, "source.properties")
    if os.path.exists(source_props):
        try:
            with open(source_props, "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r"SystemImage\.Abi\s*=\s*(\S+)", content)
            if m:
                abi = m.group(1).strip()
                if "arm" in abi or "aarch64" in abi:
                    return "arm64", "arm64-v8a"
                elif "x86_64" in abi:
                    return "x86_64", "x86_64"
                elif "x86" in abi:
                    return "x86", "x86"
        except Exception:
            pass

    dir_str = sysimg_dir.lower()
    if "arm64" in dir_str or "aarch64" in dir_str:
        return "arm64", "arm64-v8a"
    return "x86_64", "x86_64"


def print_device_profiles(json_mode=False):
    if json_mode:
        print_result({
            "status": "success",
            "action": "create avd --list-profiles",
            "profiles": DEVICE_PROFILES
        }, json_mode=True)
        return

    print("Available Device Profiles:")
    print("-" * 65)
    for p_name, info in DEVICE_PROFILES.items():
        print(f"  {p_name:<16} : {info['description']}")
    print("-" * 65)


def register_parser(subparsers):
    create_parser = subparsers.add_parser(
        "create",
        help="Create AVDs or other developer artifacts"
    )
    create_subparsers = create_parser.add_subparsers(dest="create_cmd", help="Available resource types to create")
    create_parser.set_defaults(func=lambda args: create_parser.print_help() or sys.exit(0))

    # create avd
    avd_parser = create_subparsers.add_parser(
        "avd",
        help="Create a new Android Virtual Device (AVD) from a system-image directory"
    )
    avd_parser.add_argument("--name", type=str, default=None, help="Name of the AVD (defaults to profile name if omitted)")
    avd_parser.add_argument("--sysimg-dir", type=str, default=None, help="Path to extracted system-image directory")
    avd_parser.add_argument(
        "--profile",
        type=str,
        default="medium_phone",
        choices=list(DEVICE_PROFILES.keys()),
        help="Device profile: small_phone, medium_phone (default), medium_tablet, small_desktop, medium_desktop, large_desktop"
    )
    avd_parser.add_argument("--list-profiles", action="store_true", help="List available device profiles and exit")
    avd_parser.add_argument("--ram", type=int, default=None, help="Override RAM size in MB")
    avd_parser.add_argument("--cores", type=int, default=4, help="Number of CPU cores (default: 4)")
    avd_parser.add_argument("--disk-size", type=str, default=None, help="Override data partition size (e.g. 8G)")
    avd_parser.add_argument("--gpu", type=str, default="auto", help="GPU mode: auto, host, swiftshader_indirect (default: auto)")
    avd_parser.add_argument("--force", action="store_true", help="Overwrite existing AVD with the same name")
    avd_parser.set_defaults(parser=avd_parser, func=run_create_avd)


def run_create_avd(args):
    json_mode = getattr(args, "json", False)

    if args.list_profiles:
        print_device_profiles(json_mode)
        return

    if not args.sysimg_dir:
        if not json_mode and hasattr(args, "parser"):
            args.parser.print_help()
            sys.exit(0)
        print_result({
            "status": "error",
            "action": "create avd",
            "error_message": "--sysimg-dir is required when creating an AVD.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    profile_name = args.profile or "medium_phone"
    profile = DEVICE_PROFILES.get(profile_name, DEVICE_PROFILES["medium_phone"])

    avd_name = args.name or profile_name
    raw_sysimg_dir = os.path.abspath(os.path.expanduser(args.sysimg_dir))

    if not os.path.isdir(raw_sysimg_dir):
        print_result({
            "status": "error",
            "action": "create avd",
            "name": avd_name,
            "error_message": f"System image directory not found: {raw_sysimg_dir}",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    # Automatically locate inner directory containing system.img / kernel-ranchu
    sysimg_dir = find_actual_sysimg_dir(raw_sysimg_dir)

    home_dir = os.path.expanduser("~")
    avd_root = os.path.join(home_dir, ".android", "avd")
    os.makedirs(avd_root, exist_ok=True)

    ini_file = os.path.join(avd_root, f"{avd_name}.ini")
    avd_dir = os.path.join(avd_root, f"{avd_name}.avd")

    if (os.path.exists(ini_file) or os.path.exists(avd_dir)) and not args.force:
        print_result({
            "status": "error",
            "action": "create avd",
            "name": avd_name,
            "error_message": f"AVD '{avd_name}' already exists at {avd_dir}. Pass --force to overwrite.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    if os.path.exists(ini_file):
        os.unlink(ini_file)
    if os.path.exists(avd_dir):
        shutil.rmtree(avd_dir)

    os.makedirs(avd_dir, exist_ok=True)

    arch, abi = detect_arch_and_abi(sysimg_dir)
    ram_size = args.ram if args.ram else profile["ram"]
    disk_size = args.disk_size if args.disk_size else profile["disk"]

    # Ensure trailing slash on sysdir for emulator parser
    sysimg_dir_slash = sysimg_dir if sysimg_dir.endswith("/") else sysimg_dir + "/"

    # 1. Write <name>.ini pointer file
    with open(ini_file, "w", encoding="utf-8") as f:
        f.write(f"avd.ini.encoding=UTF-8\n")
        f.write(f"path={avd_dir}\n")
        f.write(f"path.rel=avd/{avd_name}.avd\n")
        f.write(f"target=android-emu-dev\n")

    # 2. Write <name>.avd/config.ini
    config_ini = os.path.join(avd_dir, "config.ini")
    with open(config_ini, "w", encoding="utf-8") as f:
        f.write(f"AvdId={avd_name}\n")
        f.write(f"avd.ini.displayname={avd_name}\n")
        f.write(f"abi.type={abi}\n")
        f.write(f"hw.cpu.arch={arch}\n")
        f.write(f"hw.cpu.ncore={args.cores}\n")
        f.write(f"hw.ramSize={ram_size}\n")
        f.write(f"vm.heapSize={profile['heap']}\n")
        f.write(f"disk.dataPartition.size={disk_size}\n")
        f.write(f"image.sysdir.1={sysimg_dir_slash}\n")
        f.write(f"tag.id=google_apis\n")
        f.write(f"tag.display=Google APIs\n")
        f.write(f"hw.gpu.enabled=yes\n")
        f.write(f"hw.gpu.mode={args.gpu}\n")
        f.write(f"hw.keyboard=yes\n")
        f.write(f"hw.dPad=no\n")
        f.write(f"hw.mainKeys=no\n")
        f.write(f"hw.trackBall=no\n")
        f.write(f"hw.lcd.width={profile['width']}\n")
        f.write(f"hw.lcd.height={profile['height']}\n")
        f.write(f"hw.lcd.density={profile['density']}\n")
        f.write(f"showDeviceFrame=yes\n")
        f.write(f"skin.dynamic=yes\n")
        f.write(f"fastboot.forceFastBoot=yes\n")

    # 3. Copy initial userdata.img if available
    source_userdata = os.path.join(sysimg_dir, "userdata.img")
    dest_userdata = os.path.join(avd_dir, "userdata.img")
    if os.path.exists(source_userdata):
        try:
            shutil.copy2(source_userdata, dest_userdata)
        except Exception:
            pass

    summary_msg = f"Created AVD '{avd_name}' [{profile_name}] ({arch}/{abi})"
    print_result({
        "status": "success",
        "action": "create avd",
        "summary": summary_msg,
        "avd_name": avd_name,
        "profile": profile_name,
        "ini_file": ini_file,
        "avd_dir": avd_dir,
        "arch": arch,
        "abi": abi,
        "display_resolution": f"{profile['width']}x{profile['height']} ({profile['density']} dpi)",
        "sysimg_dir": sysimg_dir,
        "run_command_example": f"emu-dev-cli launch emulator --emulator-dir=<dir> -- -avd {avd_name}"
    }, json_mode=json_mode)
