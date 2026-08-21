import os
import re
import glob
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


def find_cached_sysimg_dir(arch="x86_64"):
    """
    Scans /tmp and standard locations for existing extracted system-image directories.
    """
    candidates = []
    search_patterns = [
        "/tmp/system-image-*/extracted",
        "/tmp/system-image-*",
        "/tmp/sysimg-*",
        os.path.expanduser("~/.android/system-images/*"),
    ]
    for pattern in search_patterns:
        for p in glob.glob(pattern):
            if os.path.isdir(p):
                actual = find_actual_sysimg_dir(p)
                if os.path.exists(os.path.join(actual, "kernel-ranchu")) or os.path.exists(
                        os.path.join(actual, "system.img")):
                    mtime = os.path.getmtime(p)
                    is_matching_arch = arch in p.lower() or arch in actual.lower()
                    candidates.append((1 if is_matching_arch else 0, mtime, actual))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


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


def create_single_avd(
    avd_name,
    profile_name="medium_phone",
    raw_sysimg_dir=None,
    ram=None,
    cores=4,
    disk_size=None,
    gpu="auto",
    force=False,
    avd_root=None,
):
    """
    Creates a single Android Virtual Device (AVD) directory and ini pointer file.
    """
    profile = DEVICE_PROFILES.get(profile_name, DEVICE_PROFILES["medium_phone"])
    if not raw_sysimg_dir:
        raise ValueError("raw_sysimg_dir must be provided")

    abs_raw_sysimg = os.path.abspath(os.path.expanduser(raw_sysimg_dir))
    if not os.path.isdir(abs_raw_sysimg):
        raise FileNotFoundError(f"System image directory not found: {abs_raw_sysimg}")

    sysimg_dir = find_actual_sysimg_dir(abs_raw_sysimg)

    if avd_root is None:
        home_dir = os.path.expanduser("~")
        avd_root = os.path.join(home_dir, ".android", "avd")
    os.makedirs(avd_root, exist_ok=True)

    ini_file = os.path.join(avd_root, f"{avd_name}.ini")
    avd_dir = os.path.join(avd_root, f"{avd_name}.avd")

    if (os.path.exists(ini_file) or os.path.exists(avd_dir)) and not force:
        raise FileExistsError(f"AVD '{avd_name}' already exists at {avd_dir}. Pass --force to overwrite.")

    if os.path.exists(ini_file):
        os.unlink(ini_file)
    if os.path.exists(avd_dir):
        shutil.rmtree(avd_dir)

    os.makedirs(avd_dir, exist_ok=True)

    arch, abi = detect_arch_and_abi(sysimg_dir)
    ram_size = ram if ram else profile["ram"]
    disk_size_val = disk_size if disk_size else profile["disk"]

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
        f.write(f"hw.cpu.ncore={cores}\n")
        f.write(f"hw.ramSize={ram_size}\n")
        f.write(f"vm.heapSize={profile['heap']}\n")
        f.write(f"disk.dataPartition.size={disk_size_val}\n")
        f.write(f"image.sysdir.1={sysimg_dir_slash}\n")
        f.write(f"tag.id=google_apis\n")
        f.write(f"tag.display=Google APIs\n")
        f.write(f"hw.gpu.enabled=yes\n")
        f.write(f"hw.gpu.mode={gpu}\n")
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

    return {
        "avd_name": avd_name,
        "profile": profile_name,
        "ini_file": ini_file,
        "avd_dir": avd_dir,
        "arch": arch,
        "abi": abi,
        "display_resolution": f"{profile['width']}x{profile['height']} ({profile['density']} dpi)",
        "sysimg_dir": sysimg_dir,
    }


def register_parser(subparsers):
    create_parser = subparsers.add_parser(
        "create",
        help="Create AVDs or other developer artifacts"
    )
    create_subparsers = create_parser.add_subparsers(dest="create_cmd", help="Available resource types to create")
    create_parser.set_defaults(func=lambda args: create_parser.print_help() or sys.exit(0))

    def add_avd_arguments(parser):
        parser.add_argument("--name", type=str, default=None, help="Name of the AVD (defaults to profile name if omitted)")
        parser.add_argument("--prefix", type=str, default=None, help="Prefix name when creating multiple AVD instances (e.g. 'mesh-node')")
        parser.add_argument("--count", type=int, default=1, help="Number of AVD instances to batch create (default: 1)")
        parser.add_argument("--sysimg-dir", type=str, default=None, help="Path to extracted system-image directory")
        parser.add_argument(
            "--profile",
            type=str,
            default="medium_phone",
            choices=list(DEVICE_PROFILES.keys()),
            help="Device profile: small_phone, medium_phone (default), medium_tablet, small_desktop, medium_desktop, large_desktop"
        )
        parser.add_argument("--list-profiles", action="store_true", help="List available device profiles and exit")
        parser.add_argument("--ram", type=int, default=None, help="Override RAM size in MB")
        parser.add_argument("--cores", type=int, default=4, help="Number of CPU cores (default: 4)")
        parser.add_argument("--disk-size", type=str, default=None, help="Override data partition size (e.g. 8G)")
        parser.add_argument("--gpu", type=str, default="auto", help="GPU mode: auto, host, swiftshader_indirect (default: auto)")
        parser.add_argument("--force", action="store_true", help="Overwrite existing AVD with the same name")

    # create avd
    avd_parser = create_subparsers.add_parser(
        "avd",
        help="Create a new Android Virtual Device (AVD) or batch of AVDs from a system-image directory"
    )
    add_avd_arguments(avd_parser)
    avd_parser.set_defaults(parser=avd_parser, func=run_create_avd)

    # create mesh
    mesh_parser = create_subparsers.add_parser(
        "mesh",
        help="Create a mesh batch of N Android Virtual Devices (AVDs) from a system-image directory"
    )
    add_avd_arguments(mesh_parser)
    mesh_parser.set_defaults(parser=mesh_parser, func=run_create_avd)


def run_create_avd(args):
    json_mode = getattr(args, "json", False)

    if args.list_profiles:
        print_device_profiles(json_mode)
        return

    count = getattr(args, "count", 1) or 1
    if count < 1:
        print_result({
            "status": "error",
            "action": "create avd",
            "error_message": f"Invalid --count value: {count}. Must be >= 1.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    profile_name = getattr(args, "profile", None) or "medium_phone"
    prefix = getattr(args, "prefix", None) or getattr(args, "name", None) or (profile_name if count == 1 else f"{profile_name}-mesh")

    sysimg_dir_arg = getattr(args, "sysimg_dir", None)
    if not sysimg_dir_arg:
        sysimg_dir_arg = find_cached_sysimg_dir()

    if not sysimg_dir_arg:
        cmd_type = "mesh" if count > 1 or getattr(args, "create_cmd", "") == "mesh" else "avd"
        name_val = getattr(args, "prefix", None) or getattr(args, "name", None) or ("bt-mesh" if count > 1 else "my-phone")
        count_flag = f" --count {count}" if count > 1 else ""
        err_msg = (
            f"No system-image directory specified (--sysimg-dir) and no cached system images were found in /tmp.\n\n"
            f"💡 To download a prebuilt system image, run:\n"
            f"   emu-dev-cli fetch-build system-image --latest\n\n"
            f"   Then re-run create:\n"
            f"   emu-dev-cli create {cmd_type} --name {name_val}{count_flag}\n"
        )
        if json_mode:
            print_result({
                "status": "error",
                "action": f"create {cmd_type}",
                "error_message": "No system-image directory specified and no cached images found in /tmp.",
                "suggestion": "emu-dev-cli fetch-build system-image --latest",
                "exit_code": 1
            }, json_mode=True, is_error=True)
        else:
            print(f"❌ Error: {err_msg}")
        sys.exit(1)

    if not args.sysimg_dir and not json_mode:
        print(f"ℹ️  Using auto-discovered system image: {sysimg_dir_arg}")

    names = []
    if count == 1 and args.name:
        names = [args.name]
    else:
        for i in range(1, count + 1):
            names.append(f"{prefix}-{i}" if count > 1 else prefix)

    created_avds = []
    for avd_name in names:
        try:
            res = create_single_avd(
                avd_name=avd_name,
                profile_name=profile_name,
                raw_sysimg_dir=sysimg_dir_arg,
                ram=args.ram,
                cores=args.cores,
                disk_size=args.disk_size,
                gpu=args.gpu,
                force=args.force,
            )
            created_avds.append(res)
        except (FileNotFoundError, FileExistsError, ValueError) as e:
            print_result({
                "status": "error",
                "action": "create avd",
                "name": avd_name,
                "error_message": str(e),
                "exit_code": 1
            }, json_mode=json_mode, is_error=True)
            sys.exit(1)

    if count == 1:
        first = created_avds[0]
        summary_msg = f"Created AVD '{first['avd_name']}' [{first['profile']}] ({first['arch']}/{first['abi']})"
        print_result({
            "status": "success",
            "action": "create avd",
            "summary": summary_msg,
            "avd_name": first["avd_name"],
            "profile": first["profile"],
            "ini_file": first["ini_file"],
            "avd_dir": first["avd_dir"],
            "arch": first["arch"],
            "abi": first["abi"],
            "display_resolution": first["display_resolution"],
            "sysimg_dir": first["sysimg_dir"],
            "run_command_example": f"emu-dev-cli launch emulator --emulator-dir=<dir> -- -avd {first['avd_name']}"
        }, json_mode=json_mode)
    else:
        summary_msg = f"Created {len(created_avds)} AVD mesh instances with prefix '{prefix}' [{profile_name}]"
        print_result({
            "status": "success",
            "action": "create mesh",
            "summary": summary_msg,
            "count": len(created_avds),
            "prefix": prefix,
            "profile": profile_name,
            "avds": created_avds,
            "avd_names": [a["avd_name"] for a in created_avds],
            "launch_mesh_example": f"emu-dev-cli launch mesh --emulator-dir=<dir> --prefix {prefix} --count {len(created_avds)}"
        }, json_mode=json_mode)
