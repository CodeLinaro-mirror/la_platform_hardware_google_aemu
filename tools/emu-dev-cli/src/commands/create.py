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

import sys
from lib.avd import (
    DEVICE_PROFILES,
    create_mesh_avds,
    find_cached_sysimg_dir,
)
from lib.output import print_result


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

    try:
        created_avds = create_mesh_avds(
            prefix=prefix,
            count=count,
            profile_name=profile_name,
            raw_sysimg_dir=sysimg_dir_arg,
            ram=getattr(args, "ram", None),
            cores=getattr(args, "cores", 4),
            disk_size=getattr(args, "disk_size", None),
            gpu=getattr(args, "gpu", "auto"),
            force=getattr(args, "force", False),
            explicit_name=getattr(args, "name", None) if count == 1 else None,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print_result({
            "status": "error",
            "action": "create mesh" if count > 1 else "create avd",
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
