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

"""Emulator executable resolution, environment setup, and process management."""

import glob
import os
import platform
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

NON_EXEC_EXTENSIONS = {
    ".so",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".txt",
    ".xml",
    ".json",
    ".img",
    ".dat",
    ".pak",
    ".pyc",
    ".py",
    ".md",
    ".ini",
    ".properties",
    ".pem",
    ".crt",
    ".key",
    ".cer",
    ".icns",
    ".ico",
    ".svg",
}


def ensure_executable_permissions(emu_dir: str, emu_bin: str) -> None:
    """Ensures binaries and libraries inside emu_dir have executable permissions."""
    if platform.system().lower() == "windows":
        return

    for root, _, files in os.walk(emu_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in NON_EXEC_EXTENSIONS:
                continue
            full_p = os.path.join(root, f)
            if not os.access(full_p, os.X_OK):
                try:
                    os.chmod(full_p, 0o755)
                except Exception:
                    pass


def find_cached_emulator_dir() -> Optional[str]:
    """Scans /tmp for cached extracted emulator directories."""
    patterns = [
        "/tmp/emulator-*/extracted/emulator",
        "/tmp/emulator-*/extracted",
        "/tmp/emulator-*",
    ]
    candidates = []
    for pattern in patterns:
        for p in glob.glob(pattern):
            if os.path.isdir(p):
                exe_name = (
                    "emulator.exe"
                    if platform.system().lower() == "windows"
                    else "emulator"
                )
                if os.path.isfile(os.path.join(p, exe_name)) or os.path.isfile(
                    os.path.join(p, "emulator", exe_name)
                ):
                    candidates.append((os.path.getmtime(p), p))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def resolve_emulator_executable(
    emulator_dir_arg: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Locates the emulator binary given a directory path or auto-discovers from /tmp.
    Returns (binary_path, base_directory).
    """
    target_dir = emulator_dir_arg or find_cached_emulator_dir()
    if not target_dir:
        raise ValueError(
            "Emulator directory not specified (--emulator-dir) and no cached"
            " emulator found in /tmp."
        )

    is_windows = platform.system().lower() == "windows"
    exe_name = "emulator.exe" if is_windows else "emulator"

    abs_dir = os.path.abspath(os.path.expanduser(target_dir))
    if not os.path.isdir(abs_dir):
        raise FileNotFoundError(
            f"Specified emulator directory does not exist: {abs_dir}"
        )

    direct_bin = os.path.join(abs_dir, exe_name)
    if os.path.exists(direct_bin):
        ensure_executable_permissions(abs_dir, direct_bin)
        return direct_bin, abs_dir

    sub_bin = os.path.join(abs_dir, "emulator", exe_name)
    if os.path.exists(sub_bin):
        sub_dir = os.path.join(abs_dir, "emulator")
        ensure_executable_permissions(sub_dir, sub_bin)
        return sub_bin, sub_dir

    raise FileNotFoundError(
        f"Could not locate '{exe_name}' binary inside directory: {abs_dir}"
    )


def prepare_environment(emu_dir: str) -> Dict[str, str]:
    """Constructs LD_LIBRARY_PATH and Qt environment variables for emulator execution."""
    env = os.environ.copy()
    system = platform.system().lower()

    if system == "linux":
        lib64 = os.path.join(emu_dir, "lib64")
        qt_lib = os.path.join(lib64, "qt", "lib")
        gles_lib = os.path.join(lib64, "gles_swiftshader")
        vulkan_lib = os.path.join(lib64, "vulkan")
        existing_ld = env.get("LD_LIBRARY_PATH", "")

        ld_paths = [
            p
            for p in [lib64, qt_lib, gles_lib, vulkan_lib]
            if os.path.exists(p)
        ]
        if existing_ld:
            ld_paths.append(existing_ld)
        env["LD_LIBRARY_PATH"] = ":".join(ld_paths)

        qt_plugins = os.path.join(lib64, "qt", "plugins")
        if os.path.exists(qt_plugins):
            env["QT_PLUGIN_PATH"] = qt_plugins

    elif system == "darwin":
        lib64 = os.path.join(emu_dir, "lib64")
        existing_dyld = env.get("DYLD_LIBRARY_PATH", "")
        if os.path.exists(lib64):
            env["DYLD_LIBRARY_PATH"] = (
                f"{lib64}:{existing_dyld}" if existing_dyld else lib64
            )

    return env


def is_headless_launch(emu_args: List[str]) -> bool:
    """Checks if launch arguments contain headless flags."""
    headless_flags = {"-no-window", "-no-gui", "-headless"}
    return any(flag in emu_args for flag in headless_flags)


def calculate_mesh_ports(
    base_port: int = 5554, count: int = 1
) -> List[Dict[str, Any]]:
    """
    Calculates console and ADB port assignments for N mesh nodes.
    Each emulator console port must be an even integer between 5554 and 5682.
    """
    if count < 1:
        raise ValueError(f"Invalid count: {count}. Must be >= 1.")
    if base_port % 2 != 0:
        raise ValueError(
            f"base_port must be an even integer (e.g. 5554), got {base_port}."
        )
    if base_port < 5554 or base_port > 5682:
        raise ValueError(
            f"base_port {base_port} is outside valid emulator range [5554, 5682]."
        )

    last_port = base_port + 2 * (count - 1)
    if last_port > 5682:
        raise ValueError(
            f"Requested {count} nodes starting at {base_port} exceeds maximum port 5682."
        )

    ports = []
    for i in range(count):
        c_port = base_port + 2 * i
        a_port = c_port + 1
        ports.append({
            "index": i + 1,
            "console_port": c_port,
            "adb_port": a_port,
            "serial": f"emulator-{c_port}",
        })
    return ports


def spawn_emulator_process(
    cmd: List[str],
    emu_dir: str,
    env: Dict[str, str],
    log_file_path: Optional[str] = None,
    detached: bool = True,
) -> subprocess.Popen:
    """Spawns an emulator process detached in the background or attached."""
    if not detached:
        return subprocess.Popen(cmd, env=env, cwd=emu_dir)

    if not log_file_path:
        ts = int(time.time())
        log_file_path = f"/tmp/emulator_{ts}.log"
    else:
        log_file_path = os.path.abspath(os.path.expanduser(log_file_path))

    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    log_fd = open(log_file_path, "a", encoding="utf-8")

    system = platform.system().lower()
    popen_kwargs = {
        "env": env,
        "cwd": emu_dir,
        "stdout": log_fd,
        "stderr": log_fd,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if system == "windows":
        popen_kwargs["creationflags"] = 0x00000008
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **popen_kwargs)
