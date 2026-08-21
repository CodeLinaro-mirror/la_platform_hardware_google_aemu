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

"""Mesh node health, boot completion, keyguard unlock, and Netsim radio management."""

import glob
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional


def find_adb_binary() -> str:
    """Locates the adb binary from PATH or standard SDK locations."""
    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    android_home = os.environ.get("ANDROID_HOME") or os.environ.get(
        "ANDROID_SDK_ROOT"
    )
    if android_home:
        cand = os.path.join(android_home, "platform-tools", "adb")
        if os.path.isfile(cand):
            return cand

    user_sdk = os.path.expanduser("~/Android/Sdk/platform-tools/adb")
    if os.path.isfile(user_sdk):
        return user_sdk

    return "adb"


def find_netsim_binary(emu_dir: Optional[str] = None) -> Optional[str]:
    """Locates the netsim CLI binary from emu_dir, standard /tmp distributions, or PATH."""
    if emu_dir:
        cand = os.path.join(os.path.abspath(os.path.expanduser(emu_dir)), "bin", "netsim")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    search_patterns = [
        "/tmp/emu/emulator/bin/netsim",
        "/tmp/emulator-*/extracted/emulator/bin/netsim",
        "/tmp/emulator-*/bin/netsim",
    ]
    for pattern in search_patterns:
        for p in glob.glob(pattern):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p

    which_netsim = shutil.which("netsim")
    if which_netsim:
        return which_netsim

    return None


def get_connected_adb_devices(adb_path: Optional[str] = None) -> List[str]:
    """Returns a list of device serials currently recognized in 'device' state by adb."""
    adb_bin = adb_path or find_adb_binary()
    try:
        res = subprocess.run(
            [adb_bin, "devices"], capture_output=True, text=True, check=False
        )
        if res.returncode != 0:
            return []
        serials = []
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials
    except (OSError, subprocess.SubprocessError):
        return []


def check_device_boot_completed(
    serial: str, adb_path: Optional[str] = None
) -> bool:
    """Checks whether an Android device has finished booting (sys.boot_completed=1)."""
    adb_bin = adb_path or find_adb_binary()
    try:
        res = subprocess.run(
            [adb_bin, "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return res.returncode == 0 and res.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False


def unlock_device_screen(serial: str, adb_path: Optional[str] = None) -> bool:
    """Wakes device and dismisses keyguard / lockscreen."""
    adb_bin = adb_path or find_adb_binary()
    try:
        subprocess.run(
            [adb_bin, "-s", serial, "shell", "input", "keyevent", "224"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        subprocess.run(
            [adb_bin, "-s", serial, "shell", "wm", "dismiss-keyguard"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        subprocess.run(
            [adb_bin, "-s", serial, "shell", "input", "keyevent", "82"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return True
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False


def get_device_avd_name(
    serial: str, adb_path: Optional[str] = None
) -> Optional[str]:
    """Gets the AVD name for an emulator device via 'adb emu avd name' or system property."""
    adb_bin = adb_path or find_adb_binary()
    try:
        res = subprocess.run(
            [adb_bin, "-s", serial, "emu", "avd", "name"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0].strip()
            if first_line and first_line not in ("KO", "OK"):
                return first_line
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass

    try:
        res = subprocess.run(
            [adb_bin, "-s", serial, "shell", "getprop", "ro.boot.qemu.avd_name"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass

    return None


def summarize_netsim_chips(device_info: Optional[Dict[str, Any]]) -> str:
    """Summarizes active radio chips from a netsim device dictionary."""
    if not device_info or not isinstance(device_info, dict):
        return "None"

    chips = device_info.get("chips", [])
    summaries = []
    for chip in chips:
        kind = str(chip.get("kind", "")).upper()
        if kind == "BLUETOOTH":
            bt = chip.get("bt", {})
            le = bt.get("lowEnergy", {}).get("state", False)
            classic = bt.get("classic", {}).get("state", False)
            if le and classic:
                summaries.append("BLE+Classic")
            elif le:
                summaries.append("BLE")
            elif classic:
                summaries.append("Classic")
            else:
                summaries.append("Bluetooth")
        elif kind == "WIFI":
            summaries.append("Wi-Fi")
        elif kind == "UWB":
            summaries.append("UWB")
        elif kind:
            summaries.append(kind.capitalize())
    return ", ".join(summaries) if summaries else "None"


def find_netsim_device_by_name(
    netsim_data: Optional[Dict[str, Any]], name: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Finds a netsim device entry matching an AVD name or serial."""
    if not netsim_data or not name or not isinstance(netsim_data, dict):
        return None
    for dev in netsim_data.get("devices", []):
        if dev.get("name") == name:
            return dev
    return None


def get_netsim_devices(
    netsim_bin: Optional[str] = None, port: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Queries netsim for connected radio chips and returns parsed JSON device map,
    or None if netsim is unreachable.
    """
    bin_path = netsim_bin or find_netsim_binary()
    if not bin_path:
        return None

    cmd = [bin_path]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend(["devices", "--json"])

    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip())
        return None
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def wait_for_mesh_ready(
    serials: List[str],
    timeout: int = 180,
    poll_interval: float = 2.0,
    unlock: bool = True,
    verify_netsim: bool = True,
    emu_dir: Optional[str] = None,
    adb_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Blocks until all specified emulator serials have finished booting.
    Optionally unlocks screens and inspects netsim radio registration.
    """
    if not serials:
        raise ValueError("serials list cannot be empty")

    adb_bin = adb_path or find_adb_binary()
    netsim_bin = find_netsim_binary(emu_dir) if verify_netsim else None

    start_time = time.time()
    pending = set(serials)
    booted_nodes = {}

    while pending and (time.time() - start_time) < timeout:
        for serial in list(pending):
            if check_device_boot_completed(serial, adb_path=adb_bin):
                screen_ok = False
                if unlock:
                    screen_ok = unlock_device_screen(serial, adb_path=adb_bin)
                booted_nodes[serial] = {
                    "serial": serial,
                    "boot_completed": True,
                    "screen_unlocked": screen_ok if unlock else None,
                }
                pending.remove(serial)

        if pending:
            time.sleep(poll_interval)

    elapsed = round(time.time() - start_time, 2)
    all_ready = len(pending) == 0

    netsim_data = None
    if verify_netsim and netsim_bin:
        netsim_data = get_netsim_devices(netsim_bin)

    node_results = []
    for serial in serials:
        info = booted_nodes.get(
            serial,
            {
                "serial": serial,
                "boot_completed": False,
                "screen_unlocked": False if unlock else None,
            },
        ).copy()
        avd_name = get_device_avd_name(serial, adb_path=adb_bin)
        info["avd_name"] = avd_name
        matched_netsim = (
            find_netsim_device_by_name(netsim_data, avd_name)
            if avd_name
            else None
        )
        info["radios"] = (
            summarize_netsim_chips(matched_netsim)
            if matched_netsim
            else ("Active" if netsim_data else "N/A")
        )
        node_results.append(info)

    return {
        "status": "success" if all_ready else "timeout",
        "all_ready": all_ready,
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout,
        "total_nodes": len(serials),
        "ready_nodes": len(booted_nodes),
        "pending_serials": list(pending),
        "nodes": node_results,
        "netsim_active": netsim_data is not None,
        "netsim_devices": netsim_data,
    }
