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

"""AVD and System Image provisioning and management library."""

import glob
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

DEVICE_PROFILES: Dict[str, Dict[str, Any]] = {
    "small_phone": {
        "width": 720,
        "height": 1280,
        "density": 320,
        "ram": 2048,
        "heap": 228,
        "disk": "6G",
        "description": "Small Phone (720x1280, 320 dpi, 2GB RAM)",
    },
    "medium_phone": {
        "width": 1080,
        "height": 2400,
        "density": 420,
        "ram": 2048,
        "heap": 228,
        "disk": "6G",
        "description": "Medium Phone (1080x2400, 420 dpi, 2GB RAM) [DEFAULT]",
    },
    "medium_tablet": {
        "width": 1600,
        "height": 2560,
        "density": 320,
        "ram": 4096,
        "heap": 384,
        "disk": "8G",
        "description": "Medium Tablet (1600x2560, 320 dpi, 4GB RAM)",
    },
    "small_desktop": {
        "width": 1366,
        "height": 768,
        "density": 160,
        "ram": 4096,
        "heap": 384,
        "disk": "8G",
        "description": "Small Desktop (1366x768, 160 dpi, 4GB RAM)",
    },
    "medium_desktop": {
        "width": 1920,
        "height": 1080,
        "density": 160,
        "ram": 8192,
        "heap": 512,
        "disk": "16G",
        "description": "Medium Desktop (1920x1080, 160 dpi, 8GB RAM)",
    },
    "large_desktop": {
        "width": 2560,
        "height": 1440,
        "density": 160,
        "ram": 8192,
        "heap": 512,
        "disk": "16G",
        "description": "Large Desktop (2560x1440, 160 dpi, 8GB RAM)",
    },
}


def get_default_avd_root() -> str:
    """Returns the default directory where AVDs are stored (~/.android/avd)."""
    return os.path.join(os.path.expanduser("~"), ".android", "avd")


def get_avd_paths(avd_name: str, avd_root: Optional[str] = None) -> Tuple[str, str]:
    """Returns the .ini pointer file and .avd folder paths for a given AVD name."""
    root = avd_root if avd_root is not None else get_default_avd_root()
    ini_file = os.path.join(root, f"{avd_name}.ini")
    avd_dir = os.path.join(root, f"{avd_name}.avd")
    return ini_file, avd_dir


def find_actual_sysimg_dir(sysimg_dir: str) -> str:
    """
    If sysimg_dir is a root extracted directory, locate the subfolder
    containing system.img or kernel-ranchu (e.g. extracted/x86_64/).
    """
    if os.path.exists(os.path.join(sysimg_dir, "system.img")) or os.path.exists(
        os.path.join(sysimg_dir, "kernel-ranchu")
    ):
        return sysimg_dir

    for root, _, files in os.walk(sysimg_dir):
        if "system.img" in files or "kernel-ranchu" in files:
            return root

    return sysimg_dir


def find_cached_sysimg_dir(arch: str = "x86_64") -> Optional[str]:
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
                if os.path.exists(
                    os.path.join(actual, "kernel-ranchu")
                ) or os.path.exists(os.path.join(actual, "system.img")):
                    mtime = os.path.getmtime(p)
                    is_matching_arch = arch in p.lower() or arch in actual.lower()
                    candidates.append((1 if is_matching_arch else 0, mtime, actual))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def detect_arch_and_abi(sysimg_dir: str) -> Tuple[str, str]:
    """Inspects source.properties or directory name to determine (arch, abi)."""
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
        except (OSError, UnicodeDecodeError):
            pass

    dir_str = sysimg_dir.lower()
    if "arm64" in dir_str or "aarch64" in dir_str:
        return "arm64", "arm64-v8a"
    return "x86_64", "x86_64"


def get_mesh_node_names(
    prefix: str, count: int = 1, explicit_name: Optional[str] = None
) -> List[str]:
    """Generates the list of AVD names for single or mesh instances."""
    if count == 1 and explicit_name:
        return [explicit_name]
    if count == 1:
        return [prefix]
    return [f"{prefix}-{i}" for i in range(1, count + 1)]


def create_single_avd(
    avd_name: str,
    profile_name: str = "medium_phone",
    raw_sysimg_dir: Optional[str] = None,
    ram: Optional[int] = None,
    cores: int = 4,
    disk_size: Optional[str] = None,
    gpu: str = "auto",
    force: bool = False,
    avd_root: Optional[str] = None,
) -> Dict[str, Any]:
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
    ini_file, avd_dir = get_avd_paths(avd_name, avd_root)
    os.makedirs(os.path.dirname(ini_file), exist_ok=True)

    if (os.path.exists(ini_file) or os.path.exists(avd_dir)) and not force:
        raise FileExistsError(
            f"AVD '{avd_name}' already exists at {avd_dir}. Pass --force to overwrite."
        )

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
        f.write("avd.ini.encoding=UTF-8\n")
        f.write(f"path={avd_dir}\n")
        f.write(f"path.rel=avd/{avd_name}.avd\n")
        f.write("target=android-emu-dev\n")

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
        f.write("tag.id=google_apis\n")
        f.write("tag.display=Google APIs\n")
        f.write("hw.gpu.enabled=yes\n")
        f.write(f"hw.gpu.mode={gpu}\n")
        f.write("hw.keyboard=yes\n")
        f.write("hw.dPad=no\n")
        f.write("hw.mainKeys=no\n")
        f.write("hw.trackBall=no\n")
        f.write(f"hw.lcd.width={profile['width']}\n")
        f.write(f"hw.lcd.height={profile['height']}\n")
        f.write(f"hw.lcd.density={profile['density']}\n")
        f.write("showDeviceFrame=yes\n")
        f.write("skin.dynamic=yes\n")
        f.write("fastboot.forceFastBoot=yes\n")

    # 3. Copy initial userdata.img if available
    source_userdata = os.path.join(sysimg_dir, "userdata.img")
    dest_userdata = os.path.join(avd_dir, "userdata.img")
    if os.path.exists(source_userdata):
        try:
            shutil.copy2(source_userdata, dest_userdata)
        except (OSError, shutil.Error):
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


def create_mesh_avds(
    prefix: str,
    count: int = 1,
    profile_name: str = "medium_phone",
    raw_sysimg_dir: Optional[str] = None,
    ram: Optional[int] = None,
    cores: int = 4,
    disk_size: Optional[str] = None,
    gpu: str = "auto",
    force: bool = False,
    avd_root: Optional[str] = None,
    explicit_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Batch creates a list of AVDs using the mesh naming conventions.
    """
    if count < 1:
        raise ValueError(f"Invalid count: {count}. Must be >= 1.")

    names = get_mesh_node_names(prefix, count, explicit_name)
    results = []
    for name in names:
        res = create_single_avd(
            avd_name=name,
            profile_name=profile_name,
            raw_sysimg_dir=raw_sysimg_dir,
            ram=ram,
            cores=cores,
            disk_size=disk_size,
            gpu=gpu,
            force=force,
            avd_root=avd_root,
        )
        results.append(res)
    return results
