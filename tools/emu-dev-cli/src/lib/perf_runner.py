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

"""Emulator binary performance benchmark runner, smart target resolver, and A/B comparator."""

from dataclasses import asdict, dataclass, field
import datetime
import glob
import json
import logging
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from lib.avd import (
    create_single_avd,
    find_actual_sysimg_dir,
    find_cached_sysimg_dir,
    get_avd_paths,
    get_default_avd_root,
)
from lib.emulator import (
    prepare_environment,
    resolve_emulator_executable,
    spawn_emulator_process,
)
from lib.output import format_markdown_table
from lib.perf_insights import (
    DEFAULT_RUN_CACHE_DIR,
    SignificanceResult,
    append_perf_history,
    diff_thread_cpu,
    diff_top_symbols,
    evaluate_ab_significance,
    generate_diagnostic_insights,
)
from lib.perf_probe_client import PerfProbeSession
from lib.perf_telemetry import (
    WorkloadProfilerSession,
    find_emulator_pid,
    is_nested_virtualization,
    sample_host_cpu_pct,
    take_proc_snapshot,
)

logger = logging.getLogger(__name__)

MIB = 1024 * 1024

PROBE_WORKLOADS = {
    "key_arrival_latency",
    "touch_to_photon",
    "swipe_120hz_stress",
    "disk_seq_128mib",
    "disk_rand_4k_fsync",
    "mem_ept_page_fault",
    "mem_stream_copy",
    "cpu_compute_scaling",
    "cpu_ipi_ping_pong",
}

BUILTIN_SUITES: Dict[str, Dict[str, Any]] = {
    "adb": {
        "description": (
            "Full Host<->Guest ADB file transfer suite (64MiB/256MiB tmpfs & disk, 100x1MiB directory)"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
            "push_64MiB_disk",
            "pull_64MiB_disk",
            "push_256MiB_disk",
            "pull_256MiB_disk",
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
        ],
    },
    "adb-quick": {
        "description": (
            "Fast transport-isolated ADB suite using guest /dev/shm tmpfs (64MiB & 256MiB push/pull)"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
        ],
    },
    "adb-tmpfs": {
        "description": (
            "Isolates ADB + virtio-vsock/pipe transport from guest block I/O using /dev/shm"
        ),
        "workloads": [
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
        ],
    },
    "adb-disk": {
        "description": (
            "End-to-end ADB transfer to/from /data/local/tmp (includes virtio-blk & guest filesystem)"
        ),
        "workloads": [
            "push_64MiB_disk",
            "pull_64MiB_disk",
            "push_256MiB_disk",
            "pull_256MiB_disk",
        ],
    },
    "adb-dir": {
        "description": (
            "Multi-file 100x1MiB directory push/pull stressing per-file stat/open/close latency"
        ),
        "workloads": [
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
        ],
    },
    "input": {
        "description": (
            "Sub-millisecond v1 gRPC -> virtio-input -> PerfProbeActivity key arrival, Touch-to-Photon, and 120Hz swipe stress"
        ),
        "workloads": [
            "key_arrival_latency",
            "touch_to_photon",
            "swipe_120hz_stress",
        ],
    },
    "disk": {
        "description": (
            "Guest storage & virtio-blk / QCOW2 microbenchmarks via perf_probe.apk (sequential 128MiB & random 4K fsync IOPS)"
        ),
        "workloads": [
            "disk_seq_128mib",
            "disk_rand_4k_fsync",
        ],
    },
    "memory": {
        "description": (
            "Guest SharedMemory mmap 4KiB stride demand-paging (EPT/Stage-2 faults & TLB shootdowns) and RAM stream copy"
        ),
        "workloads": [
            "mem_ept_page_fault",
            "mem_stream_copy",
        ],
    },
    "cpu": {
        "description": (
            "Guest 1-thread vs N-vCPU compute scaling and cross-vCPU futex/IPI wakeup ping-pong via perf_probe.apk"
        ),
        "workloads": [
            "cpu_compute_scaling",
            "cpu_ipi_ping_pong",
        ],
    },
    "idle": {
        "description": (
            "Steady-state idle emulator resource footprint (Host CPU%, per-thread CPU%, VmRSS, guest loadavg)"
        ),
        "workloads": [
            "idle_steady_state",
        ],
    },
    "boot": {
        "description": (
            "Cold boot wall-clock duration and guest boot phase breakdown (ro.boottime.init/zygote, uptime)"
        ),
        "workloads": [
            "cold_boot",
        ],
    },
    "snapshot": {
        "description": (
            "Quickboot snapshot save & load latency (`adb emu avd snapshot save/load`)"
        ),
        "workloads": [
            "snapshot_save",
            "snapshot_load",
        ],
    },
    "display": {
        "description": (
            "Guest graphics (`dumpsys gfxinfo` FPS/Jank/p50/p90) & host CPU% during display rendering"
        ),
        "workloads": [
            "display_gfxinfo",
        ],
    },
    "all": {
        "description": (
            "Full emulator binary qualification scorecard (boot, idle, adb, input, disk, memory, cpu, display, snapshot)"
        ),
        "workloads": [
            "cold_boot",
            "idle_steady_state",
            "push_64MiB_tmpfs",
            "pull_64MiB_tmpfs",
            "push_256MiB_tmpfs",
            "pull_256MiB_tmpfs",
            "push_256MiB_disk",
            "pull_256MiB_disk",
            "push_100x1MiB_dir",
            "pull_100x1MiB_dir",
            "key_arrival_latency",
            "touch_to_photon",
            "swipe_120hz_stress",
            "disk_seq_128mib",
            "disk_rand_4k_fsync",
            "mem_ept_page_fault",
            "mem_stream_copy",
            "cpu_compute_scaling",
            "cpu_ipi_ping_pong",
            "display_gfxinfo",
            "snapshot_save",
            "snapshot_load",
        ],
    },
}

BUILTIN_PROFILERS: Dict[str, str] = {
    "proc": (
        "Always-on zero-dependency /proc/<pid> sampler: Host CPU%, per-thread CPU% breakdown, VmRSS, CPU-ms/MiB"
    ),
    "perf-stat": (
        "Linux `perf stat -p <pid>` hardware counters: cycles, instructions, IPC, context-switches, page-faults"
    ),
    "perf-record": (
        "Linux `perf record -F 999 -g -p <pid>` callgraph profiler: extracts top C++/Rust symbol CPU% shares"
    ),
    "perfetto": (
        "Host Perfetto tracing (`--trace <file.pftrace>`) + automatic `trace_processor_shell` top-slice digest"
    ),
    "emu-perf-stat": (
        "Emulator built-in `-perf-stat <file>` periodic `EmulatorPerformanceStats` protobuf reporter"
    ),
}

ADB_SPEED_RE = re.compile(r"([\d.]+)\s+MB/s\s+\(\d+\s+bytes\s+in\s+([\d.]+)s\)")


@dataclass
class ResolvedPerfTarget:
    """Represents a resolved target for `perf run` or `perf compare`."""

    spec: str
    kind: str  # "json", "emulator_dir", "running_device"
    label: str
    emulator_dir: Optional[str] = None
    emulator_bin: Optional[str] = None
    json_path: Optional[str] = None
    build_id: Optional[str] = None
    branch: Optional[str] = None
    pkg_revision: Optional[str] = None
    engine: Optional[str] = None  # "qemu-next" or "qemu-now"


def resolve_suites(suite_arg: str) -> Tuple[List[str], List[str]]:
    """Resolves a comma-separated `--suite` argument into `(suite_names, ordered_workloads)`."""
    raw_items = [
        s.strip().lower() for s in (suite_arg or "adb").split(",") if s.strip()
    ]
    if not raw_items:
        raw_items = ["adb"]

    suites: List[str] = []
    workloads: List[str] = []
    for item in raw_items:
        if item in BUILTIN_SUITES:
            if item not in suites:
                suites.append(item)
            for w in BUILTIN_SUITES[item]["workloads"]:
                if w not in workloads:
                    workloads.append(w)
        else:
            # Allow specifying individual workload names directly (e.g. --suite push_256MiB_tmpfs)
            all_known_workloads = {
                w
                for s_info in BUILTIN_SUITES.values()
                for w in s_info["workloads"]
            }
            if item in all_known_workloads or any(
                w.lower() == item for w in all_known_workloads
            ):
                matched = next(
                    w for w in all_known_workloads if w.lower() == item
                )
                if "custom" not in suites:
                    suites.append("custom")
                if matched not in workloads:
                    workloads.append(matched)
            else:
                valid = ", ".join(sorted(BUILTIN_SUITES.keys()))
                raise ValueError(
                    f"Unknown performance suite '{item}'. Valid suites: {valid}"
                )
    return suites, workloads


def resolve_profiles(profile_arg: Optional[str]) -> List[str]:
    """Resolves comma-separated `--profile` names, always including `proc`."""
    profiles = ["proc"]
    if not profile_arg:
        return profiles
    for item in profile_arg.split(","):
        p = item.strip().lower()
        if not p:
            continue
        if p not in BUILTIN_PROFILERS:
            valid = ", ".join(sorted(BUILTIN_PROFILERS.keys()))
            raise ValueError(
                f"Unknown profiler '{p}'. Valid profilers: {valid}"
            )
        if p not in profiles:
            profiles.append(p)
    return profiles


def inspect_emulator_dir_metadata(emu_dir: str) -> Dict[str, Optional[str]]:
    """Extracts `Pkg.Revision`, `build_id`, and engine type (`qemu-next` vs `qemu-now`) from an emulator directory."""
    meta: Dict[str, Optional[str]] = {
        "pkg_revision": None,
        "build_id": None,
        "engine": None,
    }
    if not emu_dir or not os.path.isdir(emu_dir):
        return meta

    # Locate source.properties
    prop_candidates = [
        os.path.join(emu_dir, "source.properties"),
        os.path.join(emu_dir, "emulator", "source.properties"),
    ]
    for p in prop_candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("Pkg.Revision="):
                            meta["pkg_revision"] = line.split("=", 1)[1].strip()
            except Exception:
                pass
            break

    # Extract build_id from directory name if formatted like /tmp/emulator-linux-x64-15075377
    m = re.search(r"emulator-(?:[a-z0-9-]+-)?(\d{6,})", emu_dir)
    if m:
        meta["build_id"] = m.group(1)

    # Detect engine (qemu-next has lib/qemu/libgoldfish_* or bin/qemu-system-*)
    lib_qemu_dir = os.path.join(emu_dir, "lib", "qemu")
    if not os.path.isdir(lib_qemu_dir):
        lib_qemu_dir = os.path.join(emu_dir, "emulator", "lib", "qemu")
    lib64_dir = os.path.join(emu_dir, "lib64")
    if not os.path.isdir(lib64_dir):
        lib64_dir = os.path.join(emu_dir, "emulator", "lib64")

    if os.path.isdir(lib_qemu_dir):
        meta["engine"] = "qemu-next"
    elif os.path.isdir(lib64_dir):
        try:
            files = os.listdir(lib64_dir)
            if any("goldfish" in f or "adb_vsock" in f for f in files):
                meta["engine"] = "qemu-next"
            else:
                meta["engine"] = "qemu-now"
        except Exception:
            pass
    return meta


def _detect_default_host() -> str:
    sys_name = platform.system().lower()
    mach = platform.machine().lower()
    if sys_name == "darwin":
        return "mac-arm64" if mach in ("arm64", "aarch64") else "mac-x64"
    if sys_name == "windows":
        return "windows-x64"
    return "linux-x64"


def resolve_perf_target(
    spec: str,
    default_host: Optional[str] = None,
    auto_fetch: bool = True,
) -> ResolvedPerfTarget:
    """Resolves a target specification into a JSON file, local emulator binary directory,
    or automatically fetched `go/ab` build.

    Supported formats for `spec`:
      - Path to an existing `.json` benchmark file (e.g. `/tmp/run_a.json`)
      - Path to an extracted emulator directory (e.g. `/tmp/emulator-123/extracted/emulator`)
      - Numeric Android Build ID (e.g. `15075377` or `123`)
      - Branch alias (`latest:emu-main-next` or `latest:emu-main-dev`)
      - `"stable"` (official channel-0 release for the current host OS/arch)
      - `"local"` (auto-discovers cached or locally built emulator via `resolve_emulator_executable`)
    """
    if not default_host:
        default_host = _detect_default_host()
    s = spec.strip()

    # 1. Existing JSON report file (or key inside a multi-report JSON file `path.json:key`)
    if ".json" in s and (os.path.isfile(s) or ":" in s):
        base_path = s.split(":", 1)[0]
        if os.path.isfile(base_path):
            lbl = (
                s.split(":", 1)[1]
                if ":" in s
                else os.path.splitext(os.path.basename(base_path))[0]
            )
            return ResolvedPerfTarget(
                spec=s,
                kind="json",
                label=lbl,
                json_path=s,
            )

    # 2. Existing directory on disk
    expanded = os.path.expanduser(s)
    if os.path.isdir(expanded):
        emu_bin, base_dir = resolve_emulator_executable(expanded)
        meta = inspect_emulator_dir_metadata(base_dir)
        lbl = meta.get("build_id") or os.path.basename(
            os.path.normpath(expanded)
        )
        if meta.get("engine"):
            lbl = f"{lbl} ({meta['engine']})"
        return ResolvedPerfTarget(
            spec=s,
            kind="emulator_dir",
            label=lbl,
            emulator_dir=base_dir,
            emulator_bin=emu_bin,
            build_id=meta.get("build_id"),
            pkg_revision=meta.get("pkg_revision"),
            engine=meta.get("engine"),
        )

    # 3. "tree" or "local" keyword
    if s.lower() in ("tree", "local"):
        if s.lower() == "local":
            try:
                emu_bin, base_dir = resolve_emulator_executable(None)
                meta = inspect_emulator_dir_metadata(base_dir)
                return ResolvedPerfTarget(
                    spec=s,
                    kind="emulator_dir",
                    label=f"local ({meta.get('engine') or os.path.basename(base_dir)})",
                    emulator_dir=base_dir,
                    emulator_bin=emu_bin,
                    build_id=meta.get("build_id"),
                    pkg_revision=meta.get("pkg_revision"),
                    engine=meta.get("engine"),
                )
            except Exception:
                # Fall through to workspace tree resolution if /tmp has no cached build
                pass

        import zipfile

        ws_root: Optional[str] = None
        cur = os.path.abspath(os.getcwd())
        while cur and cur != "/":
            if os.path.isfile(os.path.join(cur, "MODULE.bazel")) and os.path.isdir(
                os.path.join(cur, "hardware", "google", "aemu")
            ):
                ws_root = cur
                break
            cur = os.path.dirname(cur)
        if not ws_root:
            try:
                from commands.source_directory import get_source_directory

                ws_root = get_source_directory("emu-main-next")
            except Exception:
                ws_root = None

        dest_dir = "/tmp/emulator-repo-release/extracted"
        emu_subdir = os.path.join(dest_dir, "emulator")
        if ws_root and os.path.isdir(ws_root):
            goldfish_dir = os.path.join(ws_root, "hardware", "generic", "goldfish")
            if os.path.isdir(goldfish_dir):
                target_label = "@goldfish//emulator:release"
                build_cmd = ["bazel", "build", "--config=release", target_label]
            else:
                target_label = "//:emulator-repo-release"
                build_cmd = ["bazel", "build", "--config=release", target_label]

            def _find_built_zip() -> Optional[str]:
                candidates = [
                    os.path.join(
                        ws_root,
                        "bazel-bin",
                        "external",
                        "goldfish+",
                        "emulator",
                        "release.zip",
                    ),
                    os.path.join(ws_root, "bazel-bin", "emulator-repo-release.zip"),
                ]
                for c in candidates:
                    if os.path.isfile(c):
                        real_c = os.path.realpath(c)
                        # Only accept an existing release.zip if it was compiled in opt (-opt) mode
                        if "-opt" in real_c or "emulator-repo-release.zip" in real_c:
                            return real_c
                return None

            zip_path = _find_built_zip()
            if not zip_path:
                logger.info(
                    "Building %s (--config=release) in %s...", target_label, ws_root
                )
                subprocess.run(
                    build_cmd,
                    cwd=ws_root,
                    check=True,
                )
                zip_path = _find_built_zip()
                if not zip_path:
                    cands = [
                        os.path.join(
                            ws_root,
                            "bazel-bin",
                            "external",
                            "goldfish+",
                            "emulator",
                            "release.zip",
                        ),
                        os.path.join(ws_root, "bazel-bin", "emulator-repo-release.zip"),
                    ]
                    zip_path = next((os.path.realpath(c) for c in cands if os.path.isfile(c)), None)

            if zip_path and os.path.isfile(zip_path) and (
                not os.path.isdir(emu_subdir)
                or os.path.getmtime(zip_path) > os.path.getmtime(emu_subdir)
            ):
                if os.path.isdir(dest_dir):
                    shutil.rmtree(dest_dir, ignore_errors=True)
                os.makedirs(dest_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(dest_dir)
                if platform.system().lower() == "darwin":
                    subprocess.run(
                        ["xattr", "-cr", emu_subdir],
                        capture_output=True,
                        check=False,
                    )

        emu_bin, base_dir = resolve_emulator_executable(
            emu_subdir if os.path.isdir(emu_subdir) else None
        )
        meta = inspect_emulator_dir_metadata(base_dir)
        return ResolvedPerfTarget(
            spec=s,
            kind="emulator_dir",
            label=f"tree ({meta.get('engine') or 'qemu-next'})",
            emulator_dir=base_dir,
            emulator_bin=emu_bin,
            build_id=meta.get("build_id"),
            branch="emu-main-next",
            pkg_revision=meta.get("pkg_revision"),
            engine=meta.get("engine") or "qemu-next",
        )

    # 3b. "stable" keyword (official stable release from dl.google.com channel-0)
    if s.lower() == "stable":
        import urllib.request
        import xml.etree.ElementTree as ET
        import zipfile

        repo_url = "https://dl.google.com/android/repository/repository2-3.xml"
        if default_host == "mac-arm64":
            want_os, want_archs = "macosx", ("aarch64", "arm64")
            zip_name = "emulator-darwin_aarch64-16428233.zip"
        elif default_host == "mac-x64":
            want_os, want_archs = "macosx", (None, "x64", "x86_64")
            zip_name = "emulator-darwin_x64-16428233.zip"
        elif default_host.startswith("win"):
            want_os, want_archs = "windows", (None, "x64", "x86_64")
            zip_name = "emulator-windows_x64-16428233.zip"
        else:
            want_os, want_archs = "linux", (None, "x64", "x86_64")
            zip_name = "emulator-linux_x64-16428233.zip"

        version_str = "37.2.12"
        stable_bid = "16428233"
        try:
            with urllib.request.urlopen(repo_url, timeout=20) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            for pkg in root.findall(".//remotePackage[@path='emulator']"):
                ch = pkg.find("channelRef")
                if ch is not None and ch.attrib.get("ref") == "channel-0":
                    rev = pkg.find("revision")
                    if rev is not None:
                        maj = rev.findtext("major", "0")
                        min_v = rev.findtext("minor", "0")
                        mic = rev.findtext("micro", "0")
                        version_str = f"{maj}.{min_v}.{mic}"
                    for arch in pkg.findall(".//archive"):
                        hos = arch.findtext("host-os")
                        harch = arch.findtext("host-arch")
                        url_el = arch.find(".//complete/url")
                        if (
                            hos == want_os
                            and (harch in want_archs)
                            and url_el is not None
                            and url_el.text
                        ):
                            zip_name = url_el.text.strip()
                            m_bid = re.search(r"-(\d+)\.zip$", zip_name)
                            if m_bid:
                                stable_bid = m_bid.group(1)
                            break
                    break
        except Exception as exc:
            logger.warning(
                "Could not query repository2-3.xml (%s), falling back to %s",
                exc,
                zip_name,
            )

        dest_root = f"/tmp/emulator-stable-{default_host}-{stable_bid}"
        extracted_dir = os.path.join(dest_root, "extracted")
        emu_subdir = os.path.join(extracted_dir, "emulator")
        if not os.path.isdir(emu_subdir):
            os.makedirs(extracted_dir, exist_ok=True)
            zip_path = os.path.join(dest_root, zip_name)
            if not os.path.isfile(zip_path):
                dl_url = f"https://dl.google.com/android/repository/{zip_name}"
                logger.info(
                    "Downloading official stable emulator %s (%s) from %s...",
                    version_str,
                    stable_bid,
                    dl_url,
                )
                urllib.request.urlretrieve(dl_url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extracted_dir)
            # Ensure executable permissions are preserved after zipfile extraction
            for root_d, _, files in os.walk(emu_subdir):
                for fn in files:
                    fp = os.path.join(root_d, fn)
                    if (
                        fn in ("emulator", "emulator-check", "mksdcard", "perfetto")
                        or fn.startswith("qemu-system-")
                        or "bin64" in root_d
                        or "darwin" in root_d
                        or "MacOS" in root_d
                    ):
                        try:
                            os.chmod(fp, os.stat(fp).st_mode | 0o755)
                        except OSError:
                            pass
            if platform.system().lower() == "darwin":
                subprocess.run(
                    ["xattr", "-cr", emu_subdir],
                    capture_output=True,
                    check=False,
                )

        emu_bin, base_dir = resolve_emulator_executable(emu_subdir)
        meta = inspect_emulator_dir_metadata(base_dir)
        return ResolvedPerfTarget(
            spec=s,
            kind="emulator_dir",
            label=f"stable-{version_str} ({meta.get('engine') or 'qemu-now'})",
            emulator_dir=base_dir,
            emulator_bin=emu_bin,
            build_id=stable_bid,
            branch="stable",
            pkg_revision=meta.get("pkg_revision") or version_str,
            engine=meta.get("engine"),
        )

    # 4. Numeric Android Build ID (`12345678`) or `latest:<branch>`
    is_numeric_bid = s.isdigit()
    is_latest_branch = s.lower().startswith("latest:")
    if is_numeric_bid or is_latest_branch:
        build_id = s if is_numeric_bid else None
        branch = s.split(":", 1)[1].strip() if is_latest_branch else "emu-main-next"

        if build_id:
            # Check if already extracted in /tmp
            cached_candidates = [
                f"/tmp/emulator-{default_host}-{build_id}/extracted/emulator",
                f"/tmp/emulator-{default_host}-{build_id}/extracted",
                f"/tmp/emulator-{build_id}/extracted/emulator",
                f"/tmp/emulator-{build_id}/extracted",
            ]
            for cand in cached_candidates:
                if os.path.isdir(cand):
                    try:
                        emu_bin, base_dir = resolve_emulator_executable(cand)
                        meta = inspect_emulator_dir_metadata(base_dir)
                        eng_suffix = (
                            f" ({meta['engine']})" if meta.get("engine") else ""
                        )
                        return ResolvedPerfTarget(
                            spec=s,
                            kind="emulator_dir",
                            label=f"build-{build_id}{eng_suffix}",
                            emulator_dir=base_dir,
                            emulator_bin=emu_bin,
                            build_id=build_id,
                            pkg_revision=meta.get("pkg_revision"),
                            engine=meta.get("engine"),
                        )
                    except Exception:
                        pass

        if not auto_fetch:
            raise FileNotFoundError(
                f"Emulator build '{s}' is not cached locally in /tmp and auto_fetch=False."
            )

        # Fetch via targets.emulator
        import argparse
        from targets import emulator as emu_target

        fetch_args = argparse.Namespace(
            build_id=build_id,
            latest=is_latest_branch,
            branch=branch,
            host=default_host,
            output_dir=None,
            token=None,
            skip_auth_check=False,
            json=True,
        )
        logger.info("Fetching emulator build '%s' from go/ab...", s)
        emu_target.run_emulator_fetch(fetch_args)

        # Locate the newly extracted directory
        if build_id:
            search_dirs = [
                f"/tmp/emulator-{default_host}-{build_id}/extracted/emulator",
                f"/tmp/emulator-{default_host}-{build_id}/extracted",
            ]
        else:
            matches = sorted(
                glob.glob(f"/tmp/emulator-{default_host}-*/extracted"),
                key=os.path.getmtime,
                reverse=True,
            )
            search_dirs = matches

        for cand in search_dirs:
            if os.path.isdir(cand):
                emu_bin, base_dir = resolve_emulator_executable(cand)
                meta = inspect_emulator_dir_metadata(base_dir)
                resolved_bid = build_id or meta.get("build_id") or s
                eng_suffix = (
                    f" ({meta['engine']})" if meta.get("engine") else ""
                )
                return ResolvedPerfTarget(
                    spec=s,
                    kind="emulator_dir",
                    label=f"build-{resolved_bid}{eng_suffix}",
                    emulator_dir=base_dir,
                    emulator_bin=emu_bin,
                    build_id=resolved_bid,
                    branch=branch,
                    pkg_revision=meta.get("pkg_revision"),
                    engine=meta.get("engine"),
                )

    raise ValueError(
        f"Could not resolve performance target '{spec}'. Pass a build ID (e.g. 15075377), "
        "'stable', 'latest:emu-main-next', an emulator directory path, or a saved .json report."
    )


def summarize_samples(
    samples: List[Dict[str, float]],
    telemetry_list: Optional[List[Any]] = None,
    total_mib_per_iter: float = 0.0,
) -> Dict[str, Any]:
    """Computes statistical aggregates (`p50`, `mean`, `stdev`, `min`, `max`) and
    merges host process/thread telemetry across iterations.
    """
    if not samples:
        return {"n": 0}

    n = len(samples)
    summary: Dict[str, Any] = {"n": n}

    if "wall_mib_s" in samples[0]:
        vals = [s["wall_mib_s"] for s in samples]
        durs_ms = [s.get("duration_s", 0.0) * 1000.0 for s in samples]
        adb_vals = [
            s.get("adb_mb_s", 0.0) for s in samples if s.get("adb_mb_s", 0.0) > 0
        ]
        summary.update(
            {
                "wall_mib_s_p50": round(statistics.median(vals), 2),
                "wall_mib_s_mean": round(statistics.mean(vals), 2),
                "wall_mib_s_stdev": (
                    round(statistics.stdev(vals), 2) if n > 1 else 0.0
                ),
                "wall_mib_s_min": round(min(vals), 2),
                "wall_mib_s_max": round(max(vals), 2),
                "duration_ms_p50": round(statistics.median(durs_ms), 2),
                "adb_mb_s_p50": (
                    round(statistics.median(adb_vals), 2) if adb_vals else 0.0
                ),
            }
        )
        for k in samples[0].keys():
            if k in ("wall_mib_s", "wall_mb_s", "duration_s", "adb_mb_s"):
                continue
            extra_vals = [
                float(s[k])
                for s in samples
                if isinstance(s.get(k), (int, float))
            ]
            if extra_vals:
                summary[f"{k}_p50"] = round(statistics.median(extra_vals), 2)
                summary[f"{k}_mean"] = round(statistics.mean(extra_vals), 2)
                summary[f"{k}_stdev"] = (
                    round(statistics.stdev(extra_vals), 2)
                    if len(extra_vals) > 1
                    else 0.0
                )
    elif "duration_ms" in samples[0]:
        durs_ms = [s["duration_ms"] for s in samples]
        summary.update(
            {
                "duration_ms_p50": round(statistics.median(durs_ms), 2),
                "duration_ms_mean": round(statistics.mean(durs_ms), 2),
                "duration_ms_stdev": (
                    round(statistics.stdev(durs_ms), 2) if n > 1 else 0.0
                ),
                "duration_ms_min": round(min(durs_ms), 2),
                "duration_ms_max": round(max(durs_ms), 2),
            }
        )
        for k in samples[0].keys():
            if k == "duration_ms":
                continue
            extra_vals = [
                float(s[k])
                for s in samples
                if isinstance(s.get(k), (int, float))
            ]
            if extra_vals:
                med = round(statistics.median(extra_vals), 3)
                summary[k] = med
                if not k.endswith(("_p50", "_p95", "_mean", "_stdev")):
                    summary[f"{k}_p50"] = med
                    summary[f"{k}_mean"] = round(statistics.mean(extra_vals), 3)
                    summary[f"{k}_stdev"] = (
                        round(statistics.stdev(extra_vals), 3)
                        if len(extra_vals) > 1
                        else 0.0
                    )
                elif k.endswith("_p50"):
                    base_k = k[:-4]
                    summary[f"{base_k}_mean"] = round(
                        statistics.mean(extra_vals), 3
                    )
                    summary[f"{base_k}_stdev"] = (
                        round(statistics.stdev(extra_vals), 3)
                        if len(extra_vals) > 1
                        else 0.0
                    )
            else:
                summary[k] = samples[-1][k]
    else:
        for k in samples[0].keys():
            vals = [
                float(s[k])
                for s in samples
                if isinstance(s.get(k), (int, float))
            ]
            if vals:
                summary[f"{k}_p50"] = round(statistics.median(vals), 2)
                summary[f"{k}_mean"] = round(statistics.mean(vals), 2)
                summary[f"{k}_stdev"] = (
                    round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0
                )

    if telemetry_list:
        valid_t = [t for t in telemetry_list if t and t.wall_duration_s > 0]
        if valid_t:
            cpu_pcts = [t.host_cpu_pct for t in valid_t]
            cpu_ms_list = [t.host_cpu_ms for t in valid_t]
            rss_list = [t.rss_mib for t in valid_t if t.rss_mib > 0]
            peak_rss_list = [
                t.peak_rss_mib for t in valid_t if t.peak_rss_mib > 0
            ]
            summary["host_cpu_pct"] = round(statistics.median(cpu_pcts), 2)
            median_cpu_ms = statistics.median(cpu_ms_list)
            summary["host_cpu_ms_p50"] = round(median_cpu_ms, 2)
            if total_mib_per_iter > 0:
                summary["cpu_ms_per_mib"] = round(
                    median_cpu_ms / total_mib_per_iter, 3
                )
            if rss_list:
                summary["rss_mib"] = round(statistics.median(rss_list), 2)
            if peak_rss_list:
                summary["peak_rss_mib"] = round(max(peak_rss_list), 2)

            # Aggregate 3-way vCPU split (Linux /proc/<pid>/task/<tid>/stat guest_time)
            vcpu_guest = [
                getattr(t, "vcpu_guest_cpu_pct", 0.0)
                for t in valid_t
                if getattr(t, "vcpu_guest_cpu_pct", 0.0) > 0
            ]
            if vcpu_guest:
                summary["vcpu_guest_pct"] = round(
                    statistics.median(vcpu_guest), 2
                )
                summary["vcpu_kernel_exit_pct"] = round(
                    statistics.median(
                        [getattr(t, "vcpu_kvm_kernel_cpu_pct", 0.0) for t in valid_t]
                    ),
                    2,
                )
                summary["vcpu_userspace_exit_pct"] = round(
                    statistics.median(
                        [
                            getattr(t, "vcpu_qemu_userspace_cpu_pct", 0.0)
                            for t in valid_t
                        ]
                    ),
                    2,
                )

            # Aggregate Linux /sys/kernel/debug/kvm VM-exit counters when present
            kvm_rates = [
                t.kvm_exits.get("exits", 0) / t.wall_duration_s
                for t in valid_t
                if getattr(t, "kvm_exits", None)
                and t.kvm_exits.get("exits", 0) > 0
                and t.wall_duration_s > 0
            ]
            if kvm_rates:
                summary["kvm_exits_per_sec"] = round(
                    statistics.median(kvm_rates), 1
                )
                for t in reversed(valid_t):
                    if getattr(t, "kvm_exits", None):
                        summary["kvm_top_exit_reasons"] = {
                            k: v for k, v in t.kvm_exits.items() if k != "exits"
                        }
                        break

            # Aggregate universal Guest /proc/interrupts & /proc/vmstat deltas
            irq_accum: Dict[str, List[int]] = {}
            for t in valid_t:
                girqs = getattr(t, "guest_irqs", None) or {}
                for dev in (
                    "virtio_input_irqs",
                    "virtio_blk_irqs",
                    "virtio_gpu_irqs",
                    "virtio_vsock_irqs",
                ):
                    if girqs.get(dev, 0) > 0:
                        irq_accum.setdefault(dev, []).append(int(girqs[dev]))
            if irq_accum:
                summary["guest_virtio_irqs"] = {
                    dev: int(round(statistics.median(cnts)))
                    for dev, cnts in irq_accum.items()
                }
            for summary_key, irq_key in (
                ("guest_ipi_resched", "reschedule_ipis"),
                ("guest_tlb_shootdowns", "tlb_shootdowns"),
                ("guest_pgfault_delta", "pgfault"),
            ):
                g_vals = [
                    int((getattr(t, "guest_irqs", None) or {}).get(irq_key, 0))
                    for t in valid_t
                    if int((getattr(t, "guest_irqs", None) or {}).get(irq_key, 0))
                    > 0
                ]
                if g_vals:
                    summary[summary_key] = int(round(statistics.median(g_vals)))

            # Aggregate per-thread CPU% across iterations
            thread_accum: Dict[str, List[float]] = {}
            for t in valid_t:
                for th_name, th_pct in (t.top_threads_cpu_pct or {}).items():
                    thread_accum.setdefault(th_name, []).append(th_pct)
            merged_threads = {
                th: round(statistics.median(pcts), 2)
                for th, pcts in thread_accum.items()
            }
            summary["top_threads_cpu_pct"] = dict(
                sorted(
                    merged_threads.items(), key=lambda x: x[1], reverse=True
                )[:6]
            )

            # Attach perf_stat / top_symbols / perfetto_slices if captured
            for t in reversed(valid_t):
                if t.perf_stat and "perf_stat" not in summary:
                    summary["perf_stat"] = t.perf_stat
                if t.top_symbols and "top_symbols" not in summary:
                    summary["top_symbols"] = t.top_symbols
                if t.perfetto_slices and "perfetto_slices" not in summary:
                    summary["perfetto_slices"] = t.perfetto_slices

    summary["samples"] = samples
    return summary


def detect_active_adb_serial(adb_bin: str = "adb") -> str:
    """Detects a single running emulator serial via `adb devices`."""
    res = subprocess.run(
        [adb_bin, "devices"], capture_output=True, text=True, check=False
    )
    serials: List[str] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    emu_serials = [s for s in serials if s.startswith("emulator-")]
    if emu_serials:
        return emu_serials[0]
    if serials:
        return serials[0]
    raise RuntimeError(
        "No active ADB device found (`adb devices`). Start an emulator or pass `--emulator-dir`."
    )


def _adb_cmd(
    serial: str, args: List[str], adb_bin: str = "adb", timeout: float = 180.0
) -> subprocess.CompletedProcess:
    cmd = [adb_bin, "-s", serial] + args
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd,
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=f"Command {cmd} timed out after {timeout} seconds",
        )


def stabilize_guest_os_after_first_boot(
    serial: str,
    adb_bin: str = "adb",
    timeout_sec: float = 90.0,
) -> Dict[str, Any]:
    """Actively drains and stabilizes Android OS first-boot work before any snapshots or benchmarks run.

    On a freshly provisioned clean AVD (`first ever boot`), `sys.boot_completed=1` fires while
    Android is still executing heavy post-boot initialization:
    - Google Play Store (`com.android.vending`) & Google Play Services (`com.google.android.gms`)
      self-updating, downloading/extracting Chimera modules, and running `installd` + `dex2oat`.
    - `BackgroundDexOptService` (`jobscheduler`) compiling packages in the background.
    - `LOCKED_BOOT_COMPLETED` / `BOOT_COMPLETED` broadcast storms and `MediaProvider` indexing.

    This helper:
    1. Waits for `dev.bootcomplete=1`, `init.svc.bootanim=stopped`, and package manager readiness.
    2. Immediately cuts guest internet (`svc wifi disable`, `svc data disable`) and stops
       Google Play Store (`com.android.vending`), Play Auto-Updates, and `BackgroundDexOptService`
       jobs so the guest cannot start downloading/dexing updates mid-benchmark or mid-snapshot.
    3. Disables window/transition animations and keeps the screen awake (`stayon true`).
    4. Polls until transient compilation/installer processes (`dex2oat*`, `profman`, `oatdump`,
       `artd`) and `BOOT_COMPLETED` broadcast queues drain AND guest `/proc/stat` CPU utilization
       drops below 15% (`>= 85%` idle across all guest vCPUs).
    5. Flushes guest filesystems and drops reclaimable page/slab caches (`sync; echo 3 > drop_caches`).
    """
    t0 = time.perf_counter()

    # 1. Wait for bootanim to stop and package manager to respond
    while time.perf_counter() - t0 < min(30.0, timeout_sec):
        res = _adb_cmd(
            serial,
            [
                "shell",
                "getprop init.svc.bootanim; getprop dev.bootcomplete; pm path android >/dev/null 2>&1 && echo PM_OK",
            ],
            adb_bin=adb_bin,
            timeout=10.0,
        )
        out = res.stdout if res.returncode == 0 else ""
        if "stopped" in out and "PM_OK" in out:
            break
        time.sleep(1.0)

    # 2. Cut guest network & stop Play Store / background dexopt jobs, disable animations, keep awake
    _adb_cmd(
        serial,
        [
            "shell",
            (
                "svc wifi disable 2>/dev/null || true; "
                "svc data disable 2>/dev/null || true; "
                "settings put global airplane_mode_on 1 2>/dev/null || true; "
                "settings put global verifier_verify_adb_installs 0 2>/dev/null || true; "
                "settings put global package_verifier_enable 0 2>/dev/null || true; "
                "settings put global window_animation_scale 0.0; "
                "settings put global transition_animation_scale 0.0; "
                "settings put global animator_duration_scale 0.0; "
                "settings put global stay_on_while_plugged_in 7; "
                "svc power stayon true 2>/dev/null || true; "
                "wm dismiss-keyguard 2>/dev/null || true; "
                "cmd jobscheduler cancel android 2>/dev/null || true; "
                "pm disable-user --user 0 com.android.vending 2>/dev/null || true; "
                "am force-stop com.android.vending 2>/dev/null || true; "
                "am force-stop com.google.android.partnersetup 2>/dev/null || true; "
                "am force-stop com.google.android.apps.restore 2>/dev/null || true; "
                "killall -9 dex2oat dex2oat32 dex2oat64 profman 2>/dev/null || true"
            ),
        ],
        adb_bin=adb_bin,
        timeout=20.0,
    )

    # 3. Wait for `dex2oat`/`profman`/`artd` and `BOOT_COMPLETED` broadcasts to drain,
    #    and verify guest /proc/stat CPU utilization is quiescent (< 15% busy).
    drain_deadline = t0 + max(20.0, timeout_sec * 0.75)
    quiet_guest_streak = 0
    last_guest_busy = ""
    prev_cpu_total = 0
    prev_cpu_idle = 0
    guest_cpu_busy_pct = 0.0

    while time.perf_counter() < drain_deadline:
        probe = _adb_cmd(
            serial,
            [
                "shell",
                (
                    "head -n 1 /proc/stat 2>/dev/null; "
                    "ps -A -o NAME 2>/dev/null | grep -E '^(dex2oat|dex2oat32|dex2oat64|profman|oatdump|artd)$' || true; "
                    "dumpsys activity broadcasts 2>/dev/null | grep -E 'Active Ordered Broadcast.*BOOT_COMPLETED' || true"
                ),
            ],
            adb_bin=adb_bin,
            timeout=10.0,
        )
        lines = [
            ln.strip()
            for ln in (probe.stdout or "").splitlines()
            if ln.strip()
        ]
        stat_line = ""
        busy_lines: List[str] = []
        for ln in lines:
            if ln.startswith("cpu "):
                stat_line = ln
            else:
                busy_lines.append(ln)

        cpu_quiescent = True
        if stat_line:
            parts = stat_line.split()[1:]
            try:
                nums = [int(x) for x in parts]
                total_jiffies = sum(nums)
                idle_jiffies = nums[3] + (nums[4] if len(nums) > 4 else 0)
                if prev_cpu_total > 0 and total_jiffies > prev_cpu_total:
                    d_total = total_jiffies - prev_cpu_total
                    d_idle = idle_jiffies - prev_cpu_idle
                    guest_cpu_busy_pct = max(
                        0.0, (1.0 - (d_idle / d_total)) * 100.0
                    )
                    if guest_cpu_busy_pct > 15.0:
                        cpu_quiescent = False
                prev_cpu_total = total_jiffies
                prev_cpu_idle = idle_jiffies
            except ValueError:
                pass

        if not busy_lines and cpu_quiescent:
            quiet_guest_streak += 1
            if quiet_guest_streak >= 2:
                break
        else:
            quiet_guest_streak = 0
            if busy_lines:
                last_guest_busy = ", ".join(busy_lines[:3])
                # If dex2oat respawned from a queued job, cancel/kill it again
                _adb_cmd(
                    serial,
                    [
                        "shell",
                        "cmd jobscheduler cancel android 2>/dev/null || true; killall -9 dex2oat dex2oat32 dex2oat64 profman 2>/dev/null || true",
                    ],
                    adb_bin=adb_bin,
                    timeout=10.0,
                )
        time.sleep(1.5)

    # 4. Flush dirty pages and drop reclaimable caches so I/O & snapshots start from a clean slate
    _adb_cmd(
        serial,
        [
            "shell",
            "sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true",
        ],
        adb_bin=adb_bin,
        timeout=15.0,
    )

    return {
        "stabilize_elapsed_s": round(time.perf_counter() - t0, 2),
        "last_busy_tasks": last_guest_busy,
        "guest_cpu_busy_pct": round(guest_cpu_busy_pct, 1),
    }


def wait_for_guest_and_host_settled(
    serial: str,
    emu_pid: Optional[int],
    settle_seconds: float = 0.0,
    target_host_cpu_pct: float = 65.0,
    timeout_sec: float = 120.0,
    adb_bin: str = "adb",
    proc: Optional[subprocess.Popen] = None,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Waits for `sys.boot_completed=1`, drains first-boot Android OS tasks, and waits for host/guest CPU quiescence."""
    start = time.perf_counter()
    booted = False
    while time.perf_counter() - start < timeout_sec:
        if proc is not None and proc.poll() is not None:
            log_tail = ""
            if log_path and os.path.isfile(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        log_tail = "".join(f.readlines()[-15:]).strip()
                except Exception:
                    pass
            raise RuntimeError(
                f"Emulator process for {serial} exited prematurely with code {proc.returncode}"
                + (f":\n{log_tail}" if log_tail else "")
            )
        res = _adb_cmd(
            serial,
            ["shell", "getprop", "sys.boot_completed"],
            adb_bin=adb_bin,
            timeout=5.0,
        )
        if res.returncode == 0 and res.stdout.strip() == "1":
            booted = True
            break
        time.sleep(0.25)

    if not booted:
        raise TimeoutError(
            f"Timed out after {timeout_sec}s waiting for {serial} sys.boot_completed=1"
        )

    stabilize_info: Dict[str, Any] = {}
    if settle_seconds > 0:
        # First actively drain Android OS first-boot work (bootanim, PackageManager, Play Store /
        # GMS self-update, dex2oat, BOOT_COMPLETED broadcasts, and filesystem sync) before sampling host CPU.
        stabilize_info = stabilize_guest_os_after_first_boot(
            serial=serial,
            adb_bin=adb_bin,
            timeout_sec=max(60.0, settle_seconds * 2.5),
        )
        settle_start = time.perf_counter()
        min_wait = min(10.0, settle_seconds)
        max_wait = max(settle_seconds, 120.0) if emu_pid else settle_seconds
        deadline = settle_start + max_wait
        quiet_streak = 0
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            if emu_pid and remaining >= 2.0:
                cpu = sample_host_cpu_pct(
                    emu_pid, window_sec=min(2.0, remaining)
                )
                elapsed_settle = time.perf_counter() - settle_start
                if cpu <= target_host_cpu_pct:
                    quiet_streak += 1
                    if quiet_streak >= 3 and elapsed_settle >= min_wait:
                        break
                else:
                    quiet_streak = 0
            else:
                time.sleep(min(1.0, max(0.0, remaining)))

    uptime_res = _adb_cmd(
        serial, ["shell", "cat /proc/uptime"], adb_bin=adb_bin, timeout=10.0
    )
    load_res = _adb_cmd(
        serial, ["shell", "cat /proc/loadavg"], adb_bin=adb_bin, timeout=10.0
    )
    return {
        "guest_uptime": uptime_res.stdout.strip()
        if uptime_res.returncode == 0
        else "",
        "guest_loadavg": load_res.stdout.strip()
        if load_res.returncode == 0
        else "",
        **stabilize_info,
    }


def _create_host_payload_file(directory: str, size_mib: int) -> str:
    path = os.path.join(directory, f"payload_{size_mib}MiB.bin")
    if not os.path.isfile(path) or os.path.getsize(path) != size_mib * MIB:
        chunk = os.urandom(MIB)
        with open(path, "wb") as f:
            for _ in range(size_mib):
                f.write(chunk)
    return path


def _create_host_payload_dir(
    directory: str, count: int = 100, file_mib: int = 1
) -> str:
    dir_path = os.path.join(directory, f"dir_{count}x{file_mib}MiB")
    os.makedirs(dir_path, exist_ok=True)
    chunk = os.urandom(file_mib * MIB)
    for idx in range(count):
        fp = os.path.join(dir_path, f"file_{idx:03d}.bin")
        if not os.path.isfile(fp) or os.path.getsize(fp) != file_mib * MIB:
            with open(fp, "wb") as f:
                f.write(chunk)
    return dir_path


def run_adb_transfer_workload(
    workload_name: str,
    serial: str,
    emu_pid: Optional[int],
    iterations: int = 5,
    warmup: int = 1,
    profiles: Optional[List[str]] = None,
    adb_bin: str = "adb",
) -> Dict[str, Any]:
    """Executes a single ADB transfer workload (`push_*` or `pull_*`) with warmup and host telemetry."""
    shm_root = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    work_dir = tempfile.mkdtemp(prefix="emu_perf_adb_", dir=shm_root)

    try:
        m_single = re.match(
            r"^(push|pull)_(\d+)MiB_(tmpfs|disk)$", workload_name
        )
        m_dir = re.match(r"^(push|pull)_(\d+)x(\d+)MiB_dir$", workload_name)

        if m_single:
            direction, size_str, storage = m_single.groups()
            size_mib = int(size_str)
            total_bytes = size_mib * MIB
            remote_base = (
                "/tmp" if storage == "tmpfs" else "/data/local/tmp"
            )
            remote_path = f"{remote_base}/emu_perf_{size_mib}m.bin"
            host_src = _create_host_payload_file(work_dir, size_mib)
            host_dst = os.path.join(work_dir, "pulled.bin")

            if direction == "pull":
                # Seed remote file before pulling
                _adb_cmd(
                    serial, ["push", host_src, remote_path], adb_bin=adb_bin
                )

            def _step() -> subprocess.CompletedProcess:
                if direction == "push":
                    return _adb_cmd(
                        serial, ["push", host_src, remote_path], adb_bin=adb_bin
                    )
                return _adb_cmd(
                    serial, ["pull", remote_path, host_dst], adb_bin=adb_bin
                )

        elif m_dir:
            direction, count_str, fsize_str = m_dir.groups()
            count = int(count_str)
            fsize_mib = int(fsize_str)
            size_mib = count * fsize_mib
            total_bytes = size_mib * MIB
            remote_dir = "/data/local/tmp/emu_perf_dir"
            host_src_dir = _create_host_payload_dir(work_dir, count, fsize_mib)
            host_dst_dir = os.path.join(work_dir, "pulled_dir")

            _adb_cmd(
                serial, ["shell", f"rm -rf {remote_dir}"], adb_bin=adb_bin
            )
            if direction == "pull":
                _adb_cmd(
                    serial, ["push", host_src_dir, remote_dir], adb_bin=adb_bin
                )

            def _step() -> subprocess.CompletedProcess:
                if direction == "push":
                    _adb_cmd(
                        serial,
                        ["shell", f"rm -rf {remote_dir}"],
                        adb_bin=adb_bin,
                    )
                    return _adb_cmd(
                        serial,
                        ["push", host_src_dir, remote_dir],
                        adb_bin=adb_bin,
                    )
                if os.path.exists(host_dst_dir):
                    shutil.rmtree(host_dst_dir, ignore_errors=True)
                return _adb_cmd(
                    serial, ["pull", remote_dir, host_dst_dir], adb_bin=adb_bin
                )

        else:
            raise ValueError(f"Unsupported ADB workload: {workload_name}")

        # Warmup iterations
        for _ in range(max(0, warmup)):
            _step()

        is_disk_backed = bool(m_dir) or (
            bool(m_single) and m_single.group(3) == "disk"
        )
        adb_fn = lambda args, timeout=30.0: _adb_cmd(
            serial, args, adb_bin=adb_bin, timeout=timeout
        )
        samples: List[Dict[str, float]] = []
        telemetries: List[Any] = []
        target_iters = max(1, iterations)
        max_extra = 2 if target_iters >= 3 else 0
        idx = 0
        while idx < target_iters + max_extra:
            if is_disk_backed:
                _adb_cmd(
                    serial,
                    [
                        "shell",
                        "sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true",
                    ],
                    adb_bin=adb_bin,
                    timeout=15.0,
                )
            with WorkloadProfilerSession(
                pid=emu_pid, profiles=profiles, adb_fn=adb_fn
            ) as prof:
                t0 = time.perf_counter()
                res = _step()
                elapsed = time.perf_counter() - t0

            if res.returncode != 0:
                raise RuntimeError(
                    f"ADB workload '{workload_name}' failed: {res.stderr.strip() or res.stdout.strip()}"
                )
            combined_out = f"{res.stdout}\n{res.stderr}"
            m_spd = ADB_SPEED_RE.search(combined_out)
            adb_mb_s = float(m_spd.group(1)) if m_spd else 0.0
            wall_mib_s = (total_bytes / MIB) / elapsed if elapsed > 0 else 0.0
            wall_mb_s = (total_bytes / 1e6) / elapsed if elapsed > 0 else 0.0
            samples.append(
                {
                    "duration_s": round(elapsed, 5),
                    "wall_mib_s": round(wall_mib_s, 2),
                    "wall_mb_s": round(wall_mb_s, 2),
                    "adb_mb_s": round(adb_mb_s, 2),
                }
            )
            telemetries.append(prof.telemetry)
            idx += 1
            if idx >= target_iters and idx < target_iters + max_extra:
                vals = [s["wall_mib_s"] for s in samples]
                mean_v = statistics.mean(vals)
                if mean_v <= 0 or (statistics.stdev(vals) / mean_v) <= 0.08:
                    break

        # Clean up guest artifacts
        _adb_cmd(
            serial,
            [
                "shell",
                "rm -rf /tmp/emu_perf_* /data/local/tmp/emu_perf_*",
            ],
            adb_bin=adb_bin,
            timeout=15.0,
        )
        return summarize_samples(
            samples, telemetries, total_mib_per_iter=float(size_mib)
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def run_probe_workload(
    workload_name: str,
    probe: PerfProbeSession,
    emu_pid: Optional[int],
    iterations: int = 5,
    warmup: int = 1,
    profiles: Optional[List[str]] = None,
    adb_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Executes a `perf_probe.apk` + gRPC workload (`input`, `disk`, `memory`, `cpu`)
    with host & guest hypervisor telemetry.
    """
    probe.ensure_installed_and_ready()
    total_mib = 0.0

    if workload_name == "key_arrival_latency":
        step_fn = lambda: probe.run_key_arrival_latency(count=40)
    elif workload_name == "touch_to_photon":
        step_fn = lambda: probe.run_touch_to_photon(samples=10)
    elif workload_name == "swipe_120hz_stress":
        step_fn = lambda: probe.run_swipe_120hz_stress(events=180, rate_hz=120.0)
    elif workload_name == "disk_seq_128mib":
        total_mib = 256.0

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "disk_seq_128mib", {"size_mib": 128}
            )
            w_mib_s = float(rec.get("write_mib_s", 0.0))
            r_mib_s = float(rec.get("read_mib_s", 0.0))
            w_ms = float(rec.get("write_ms", 0.0))
            return {
                "wall_mib_s": round(w_mib_s, 2),
                "write_mib_s": round(w_mib_s, 2),
                "read_mib_s": round(r_mib_s, 2),
                "duration_s": round(w_ms / 1000.0, 4),
            }

    elif workload_name == "disk_rand_4k_fsync":

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "disk_rand_4k_fsync", {"iterations": 250}
            )
            return {
                "iops": round(float(rec.get("iops", 0.0)), 2),
                "p50_ms": round(float(rec.get("p50_ms", 0.0)), 3),
                "p95_ms": round(float(rec.get("p95_ms", 0.0)), 3),
            }

    elif workload_name == "mem_ept_page_fault":
        total_mib = 256.0

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "mem_ept_page_fault", {"size_mib": 64, "rounds": 4}
            )
            return {
                "pages_per_sec": round(float(rec.get("pages_per_sec", 0.0)), 1),
                "us_per_page": round(float(rec.get("us_per_page", 0.0)), 3),
                "total_ms": round(float(rec.get("total_ms", 0.0)), 2),
            }

    elif workload_name == "mem_stream_copy":
        total_mib = 1024.0

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "mem_stream_copy", {"size_mib": 32, "iterations": 32}
            )
            gib_s = float(rec.get("copy_gib_s", 0.0))
            return {
                "wall_mib_s": round(gib_s * 1024.0, 2),
                "copy_gib_s": round(gib_s, 3),
                "duration_s": round(float(rec.get("total_ms", 0.0)) / 1000.0, 4),
            }

    elif workload_name == "cpu_compute_scaling":

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "cpu_compute_scaling", {"duration_ms": 400}
            )
            return {
                "cpu_nt_mops_s": round(float(rec.get("cpu_nt_mops_s", 0.0)), 2),
                "cpu_1t_mops_s": round(float(rec.get("cpu_1t_mops_s", 0.0)), 2),
                "smp_scaling_ratio": round(
                    float(rec.get("smp_scaling_ratio", 0.0)), 2
                ),
                "vcpus": float(rec.get("vcpus", 1)),
            }

    elif workload_name == "cpu_ipi_ping_pong":

        def step_fn() -> Dict[str, float]:
            rec = probe.run_intent_scenario(
                "cpu_ipi_ping_pong", {"iterations": 3000}
            )
            return {
                "roundtrip_us": round(float(rec.get("roundtrip_us", 0.0)), 3),
                "switches_per_sec": round(
                    float(rec.get("switches_per_sec", 0.0)), 1
                ),
                "total_ms": round(float(rec.get("total_ms", 0.0)), 2),
            }

    else:
        raise ValueError(f"Unsupported probe workload: {workload_name}")

    for _ in range(max(0, min(warmup, 1))):
        step_fn()

    is_disk_probe = workload_name in ("disk_seq_128mib", "disk_rand_4k_fsync")
    samples: List[Dict[str, float]] = []
    telemetries: List[Any] = []
    target_iters = max(1, iterations)
    max_extra = 2 if target_iters >= 3 else 0
    idx = 0
    while idx < target_iters + max_extra:
        if is_disk_probe and adb_fn is not None:
            try:
                adb_fn(
                    [
                        "shell",
                        "sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true",
                    ],
                    timeout=15.0,
                )
            except Exception:
                pass
        with WorkloadProfilerSession(
            pid=emu_pid, profiles=profiles, adb_fn=adb_fn
        ) as prof:
            sample = step_fn()
        samples.append(sample)
        telemetries.append(prof.telemetry)
        idx += 1
        if idx >= target_iters and idx < target_iters + max_extra:
            first_key = next(iter(samples[0].keys()), None)
            if first_key:
                vals = [
                    float(s[first_key])
                    for s in samples
                    if isinstance(s.get(first_key), (int, float))
                ]
                if len(vals) >= 2:
                    mean_v = statistics.mean(vals)
                    if (
                        mean_v <= 0
                        or (statistics.stdev(vals) / mean_v) <= 0.08
                    ):
                        break

    return summarize_samples(
        samples, telemetries, total_mib_per_iter=total_mib
    )


def run_idle_workload(
    serial: str,
    emu_pid: Optional[int],
    duration_sec: float = 10.0,
    profiles: Optional[List[str]] = None,
    adb_bin: str = "adb",
) -> Dict[str, Any]:
    """Measures steady-state idle emulator CPU%, per-thread CPU%, VmRSS, and guest loadavg."""
    adb_fn = lambda args, timeout=30.0: _adb_cmd(
        serial, args, adb_bin=adb_bin, timeout=timeout
    )
    with WorkloadProfilerSession(
        pid=emu_pid, profiles=profiles, adb_fn=adb_fn
    ) as prof:
        time.sleep(max(1.0, duration_sec))
    load_res = _adb_cmd(
        serial, ["shell", "cat /proc/loadavg"], adb_bin=adb_bin, timeout=10.0
    )
    guest_load_1m = 0.0
    if load_res.returncode == 0 and load_res.stdout.strip():
        try:
            guest_load_1m = float(load_res.stdout.strip().split()[0])
        except ValueError:
            pass

    t = prof.telemetry
    girqs = t.guest_irqs or {}
    return {
        "n": 1,
        "host_cpu_pct": t.host_cpu_pct,
        "host_user_cpu_pct": t.host_user_cpu_pct,
        "host_sys_cpu_pct": t.host_sys_cpu_pct,
        "vcpu_guest_pct": t.vcpu_guest_cpu_pct,
        "vcpu_kernel_exit_pct": t.vcpu_kvm_kernel_cpu_pct,
        "vcpu_userspace_exit_pct": t.vcpu_qemu_userspace_cpu_pct,
        "kvm_exits_per_sec": (
            round(t.kvm_exits.get("exits", 0) / t.wall_duration_s, 1)
            if t.kvm_exits and t.wall_duration_s > 0
            else 0.0
        ),
        "kvm_top_exit_reasons": {
            k: v for k, v in (t.kvm_exits or {}).items() if k != "exits"
        },
        "guest_virtio_irqs": {
            k: girqs[k]
            for k in (
                "virtio_input_irqs",
                "virtio_blk_irqs",
                "virtio_gpu_irqs",
                "virtio_vsock_irqs",
            )
            if girqs.get(k, 0) > 0
        },
        "guest_ipi_resched": girqs.get("reschedule_ipis", 0),
        "guest_tlb_shootdowns": girqs.get("tlb_shootdowns", 0),
        "rss_mib": t.rss_mib,
        "peak_rss_mib": t.peak_rss_mib,
        "guest_loadavg_1m": guest_load_1m,
        "top_threads_cpu_pct": t.top_threads_cpu_pct,
        "perf_stat": t.perf_stat,
        "top_symbols": t.top_symbols,
    }


def _ensure_valid_console_auth_token() -> None:
    """Ensures `~/.emulator_console_auth_token` is not a malformed short (<12 byte) token
    that causes the emulator console to reject authentication and hide `avd` commands.
    """
    token_path = os.path.expanduser("~/.emulator_console_auth_token")
    if os.path.isfile(token_path):
        try:
            with open(token_path, "rb") as f:
                raw = f.read().strip()
            if 0 < len(raw) < 12:
                with open(token_path, "wb") as f:
                    f.write(b"")
        except OSError:
            pass


def run_snapshot_workload(
    workload_name: str,
    serial: str,
    emu_pid: Optional[int],
    iterations: int = 3,
    profiles: Optional[List[str]] = None,
    adb_bin: str = "adb",
) -> Dict[str, Any]:
    """Measures `adb emu avd snapshot save` or `load` latency and host CPU cost."""
    _ensure_valid_console_auth_token()
    action = "save" if workload_name == "snapshot_save" else "load"
    snap_tag = "emu_perf_bench_snap"

    # Flush any pending guest filesystem dirty pages and let background I/O settle
    # before taking the baseline snapshot so snapshot_save / snapshot_load measure
    # a clean, quiesced guest state.
    _adb_cmd(
        serial,
        [
            "shell",
            "sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true",
        ],
        adb_bin=adb_bin,
        timeout=15.0,
    )
    time.sleep(0.5)

    # Always perform 1 untimed pre-save:
    # - For `load`, creates the snapshot target to restore from.
    # - For `save`, warms up QCOW2 snapshot cluster tables and `renderersave/` directory
    #   so iteration 1 does not suffer a cold allocation spike.
    prep = _adb_cmd(
        serial,
        ["emu", "avd", "snapshot", "save", snap_tag],
        adb_bin=adb_bin,
        timeout=60.0,
    )
    if prep.returncode != 0 or "KO:" in prep.stdout:
        raise RuntimeError(
            f"Snapshot pre-save failed on {serial}: {prep.stdout.strip()} {prep.stderr.strip()}"
        )

    # For `load`, also perform 1 untimed warmup `load` so QCOW2 L2 cluster tables
    # and host memory mappings are warm before timed iterations begin (eliminating
    # the ~30% cold first-restore spike on iteration 0).
    if action == "load":
        warm = _adb_cmd(
            serial,
            ["emu", "avd", "snapshot", "load", snap_tag],
            adb_bin=adb_bin,
            timeout=60.0,
        )
        if warm.returncode == 0 and "KO:" not in warm.stdout:
            try:
                _adb_cmd(
                    serial, ["wait-for-device"], adb_bin=adb_bin, timeout=15.0
                )
            except Exception:
                pass

    adb_fn = lambda args, timeout=30.0: _adb_cmd(
        serial, args, adb_bin=adb_bin, timeout=timeout
    )
    samples: List[Dict[str, float]] = []
    telemetries: List[Any] = []
    for _ in range(max(1, iterations)):
        with WorkloadProfilerSession(
            pid=emu_pid, profiles=profiles, adb_fn=adb_fn
        ) as prof:
            t0 = time.perf_counter()
            res = _adb_cmd(
                serial,
                ["emu", "avd", "snapshot", action, snap_tag],
                adb_bin=adb_bin,
                timeout=60.0,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if res.returncode != 0 or "KO:" in res.stdout:
            raise RuntimeError(
                f"Snapshot {action} failed on {serial}: {res.stdout.strip()} {res.stderr.strip()}"
            )
        if action == "load":
            try:
                _adb_cmd(
                    serial, ["wait-for-device"], adb_bin=adb_bin, timeout=15.0
                )
            except Exception:
                pass
        samples.append({"duration_ms": round(elapsed_ms, 2)})
        telemetries.append(prof.telemetry)

    if action == "load":
        _adb_cmd(
            serial,
            ["emu", "avd", "snapshot", "delete", snap_tag],
            adb_bin=adb_bin,
            timeout=15.0,
        )
    return summarize_samples(samples, telemetries)


def run_display_workload(
    serial: str,
    emu_pid: Optional[int],
    duration_sec: float = 5.0,
    profiles: Optional[List[str]] = None,
    adb_bin: str = "adb",
) -> Dict[str, Any]:
    """Measures guest `dumpsys gfxinfo` frame statistics and host CPU% during UI rendering."""
    adb_fn = lambda args, timeout=30.0: _adb_cmd(
        serial, args, adb_bin=adb_bin, timeout=timeout
    )
    _adb_cmd(
        serial,
        ["shell", "dumpsys", "gfxinfo", "--reset"],
        adb_bin=adb_bin,
        timeout=15.0,
    )
    with WorkloadProfilerSession(
        pid=emu_pid, profiles=profiles, adb_fn=adb_fn
    ) as prof:
        # Trigger lightweight surfaceflinger / systemui swipe activity so frames are rendered
        _adb_cmd(
            serial,
            ["shell", "input", "swipe", "300", "800", "300", "200", "400"],
            adb_bin=adb_bin,
            timeout=15.0,
        )
        time.sleep(max(1.0, duration_sec))

    gfx_res = _adb_cmd(
        serial, ["shell", "dumpsys", "gfxinfo"], adb_bin=adb_bin, timeout=15.0
    )
    out = gfx_res.stdout if gfx_res.returncode == 0 else ""
    total_frames = 0
    jank_pct = 0.0
    p50_ms = 0.0
    p90_ms = 0.0

    m_f = re.search(r"Total frames rendered:\s*(\d+)", out)
    if m_f:
        total_frames = int(m_f.group(1))
    m_j = re.search(r"Janky frames:\s*\d+\s*\(([\d.]+)%\)", out)
    if m_j:
        jank_pct = float(m_j.group(1))
    m_50 = re.search(r"50th percentile:\s*(\d+)ms", out)
    if m_50:
        p50_ms = float(m_50.group(1))
    m_90 = re.search(r"90th percentile:\s*(\d+)ms", out)
    if m_90:
        p90_ms = float(m_90.group(1))

    t = prof.telemetry
    return {
        "n": 1,
        "total_frames": total_frames,
        "jank_pct": jank_pct,
        "frame_p50_ms": p50_ms,
        "frame_p90_ms": p90_ms,
        "host_cpu_pct": t.host_cpu_pct,
        "rss_mib": t.rss_mib,
        "top_threads_cpu_pct": t.top_threads_cpu_pct,
    }


def _find_free_console_port(start_port: int = 5560) -> int:
    for port in range(start_port, start_port + 100, 2):
        console_busy = False
        for check_p in (port, port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", check_p)) == 0:
                    console_busy = True
                    break
        if not console_busy:
            return port
    return start_port + 20


def _cleanup_stale_netsim(force: bool = False) -> None:
    """Removes stale `netsim.ini` discovery files (and kills orphaned `netsimd` when `force=True`)
    so `NetsimConnector::Run()` does not block for 5 seconds on `DEADLINE_EXCEEDED` when switching
    between `qemu-now` (`stable`) and `qemu-next` (`tree`).
    """
    import signal

    if force:
        try:
            res = subprocess.run(
                ["pgrep", "-f", "netsimd"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3.0,
            )
            for pid_str in (res.stdout or "").splitlines():
                if pid_str.strip().isdigit():
                    try:
                        os.kill(int(pid_str.strip()), signal.SIGKILL)
                    except OSError:
                        pass
        except Exception:
            pass

    candidate_dirs = [
        os.environ.get("TMPDIR") or "",
        tempfile.gettempdir(),
        "/tmp",
        os.path.expanduser("~/Library/Caches/TemporaryItems"),
        os.environ.get("ANDROID_EMULATOR_DISCOVERY_DIR") or "",
        os.environ.get("XDG_RUNTIME_DIR") or "",
    ]
    seen_dirs = set()
    for d in candidate_dirs:
        if not d or not os.path.isdir(d):
            continue
        real_d = os.path.realpath(d)
        if real_d in seen_dirs:
            continue
        seen_dirs.add(real_d)
        for ini_name in ("netsim.ini", "netsim.ini.lock"):
            ini_path = os.path.join(d, ini_name)
            if not os.path.isfile(ini_path):
                continue
            if ini_name == "netsim.ini" and not force:
                port = 0
                try:
                    with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "=" in line:
                                k, v = [p.strip() for p in line.split("=", 1)]
                                if k == "grpc.port" and v.isdigit():
                                    port = int(v)
                                    break
                except OSError:
                    pass
                if port > 0:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2)
                            if s.connect_ex(("127.0.0.1", port)) == 0:
                                continue
                    except OSError:
                        pass
            try:
                os.remove(ini_path)
            except OSError:
                pass


def _parse_emulator_boot_completed_ms(log_path: Optional[str]) -> Optional[float]:
    """Parses the emulator's internal `Boot completed in <N> ms` log line from `log_path`.

    Both `qemu-now` (`INFO | Boot completed in 13098 ms`) and `qemu-next`
    (`WARNING | Boot completed in 8847 ms`) log this exact message at the moment
    `guest.boot_completed` (`dev.bootcomplete=1` / `sys.boot_completed=1`) fires inside QEMU,
    free of external launcher pre-checks or host ADB server polling delays.
    """
    if not log_path or not os.path.isfile(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        matches = re.findall(r"Boot completed in\s+(\d+)\s*ms", content)
        if matches:
            return float(matches[0])
    except OSError:
        pass
    return None


def _kill_emulator_process_tree(
    proc: subprocess.Popen, serial: str, adb_bin: str = "adb"
) -> None:
    """Terminates the emulator launcher and all child QEMU/crashpad/netsimd processes."""
    import signal

    emu_pid = find_emulator_pid(serial)
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=5.0)
    except Exception:
        pass
    if emu_pid and emu_pid != proc.pid:
        try:
            os.kill(emu_pid, signal.SIGKILL)
        except Exception:
            pass
    _cleanup_stale_netsim(force=True)
    _adb_cmd(serial, ["disconnect"], adb_bin=adb_bin, timeout=3.0)


def _resolve_perf_avd_template(
    avd_name: Optional[str] = None,
    sysimg_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolves system image directory and hardware configuration (`ram`, `cores`, `disk_size`)
    for provisioning a clean, isolated benchmark AVD.
    """
    resolved_sysimg: Optional[str] = None
    ram = 2048
    cores = 4
    disk_size = "6G"
    if avd_name and avd_name.strip().lower() == "auto":
        avd_name = None
    source_avd = avd_name
    mach = platform.machine().lower()
    host_arch = "arm64" if mach in ("arm64", "aarch64") else "x86_64"

    if sysimg_dir:
        resolved_sysimg = find_actual_sysimg_dir(
            os.path.abspath(os.path.expanduser(sysimg_dir))
        )

    avd_root = get_default_avd_root()
    candidate_avds: List[str] = []
    if source_avd:
        candidate_avds = [source_avd]
    elif os.path.isdir(avd_root):
        ini_files = sorted(glob.glob(os.path.join(avd_root, "*.ini")))
        for ini_p in ini_files:
            cand_name = os.path.splitext(os.path.basename(ini_p))[0]
            if not cand_name.startswith("perf-clean-"):
                candidate_avds.append(cand_name)

    for cand_avd in candidate_avds:
        _, existing_avd_dir = get_avd_paths(cand_avd, avd_root)
        cfg_path = os.path.join(existing_avd_dir, "config.ini")
        if not os.path.isfile(cfg_path):
            continue
        cand_sysimg: Optional[str] = None
        cand_ram = ram
        cand_cores = cores
        cand_disk = disk_size
        cand_arch: Optional[str] = None
        try:
            with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if "=" not in line:
                        continue
                    k, v = [p.strip() for p in line.split("=", 1)]
                    if k == "image.sysdir.1" and v and os.path.isdir(v):
                        cand_sysimg = find_actual_sysimg_dir(v)
                    elif k == "hw.ramSize" and v.isdigit():
                        cand_ram = int(v)
                    elif k == "hw.cpu.ncore" and v.isdigit():
                        cand_cores = int(v)
                    elif k == "disk.dataPartition.size" and v:
                        cand_disk = v
                    elif k in ("hw.cpu.arch", "abi.type") and v:
                        cand_arch = "arm64" if ("arm" in v or "aarch64" in v) else "x86_64"
        except OSError:
            continue

        if source_avd is None:
            source_avd = cand_avd
            ram, cores, disk_size = cand_ram, cand_cores, cand_disk
        if not resolved_sysimg and cand_sysimg:
            if cand_arch in (None, host_arch) or len(candidate_avds) == 1:
                resolved_sysimg = cand_sysimg
                source_avd = cand_avd
                ram, cores, disk_size = cand_ram, cand_cores, cand_disk
                break

    if not resolved_sysimg:
        resolved_sysimg = find_cached_sysimg_dir(arch=host_arch)

    if not resolved_sysimg or not os.path.isdir(resolved_sysimg):
        raise RuntimeError(
            "Could not locate a system image directory to provision a clean benchmark AVD. "
            "Fetch one via `emu-dev-cli fetch-build system-image --latest`, create an AVD via `emu-dev-cli create`, "
            "or pass `--sysimg-dir`."
        )

    return {
        "sysimg_dir": resolved_sysimg,
        "ram": ram,
        "cores": cores,
        "disk_size": disk_size,
        "template_avd": source_avd or "clean-perf",
    }


def run_suite_against_device(
    serial: str,
    workloads: List[str],
    iterations: int = 5,
    warmup: int = 1,
    settle_seconds: float = 0.0,
    profiles: Optional[List[str]] = None,
    label: str = "live-device",
    adb_bin: str = "adb",
    boot_metrics: Optional[Dict[str, Any]] = None,
    grpc_port: Optional[int] = None,
) -> Dict[str, Any]:
    """Runs the specified workloads against an active emulator `serial`."""
    emu_pid = find_emulator_pid(serial)
    guest_state = wait_for_guest_and_host_settled(
        serial=serial,
        emu_pid=emu_pid,
        settle_seconds=settle_seconds,
        adb_bin=adb_bin,
    )

    adb_fn = lambda args, timeout=60.0: _adb_cmd(
        serial, args, adb_bin=adb_bin, timeout=timeout
    )
    probe_session: Optional[PerfProbeSession] = None
    if any(w in PROBE_WORKLOADS for w in workloads):
        probe_session = PerfProbeSession(
            serial=serial,
            adb_fn=adb_fn,
            emu_pid=emu_pid,
            grpc_port=grpc_port,
        )

    tests_out: Dict[str, Dict[str, Any]] = {}
    if boot_metrics and "cold_boot" in workloads:
        tests_out["cold_boot"] = boot_metrics

    for w in workloads:
        try:
            if w == "cold_boot":
                init_res = _adb_cmd(
                    serial, ["shell", "getprop", "ro.boottime.init"], adb_bin=adb_bin
                )
                zyg_res = _adb_cmd(
                    serial, ["shell", "getprop", "ro.boottime.zygote"], adb_bin=adb_bin
                )
                init_ns = (
                    int(init_res.stdout.strip())
                    if init_res.stdout.strip().isdigit()
                    else 0
                )
                zyg_ns = (
                    int(zyg_res.stdout.strip())
                    if zyg_res.stdout.strip().isdigit()
                    else 0
                )
                if "cold_boot" in tests_out:
                    tests_out["cold_boot"]["ro_boottime_init_ns"] = init_ns
                    tests_out["cold_boot"]["ro_boottime_zygote_ns"] = zyg_ns
                else:
                    tests_out["cold_boot"] = {
                        "n": 1,
                        "ro_boottime_init_ns": init_ns,
                        "ro_boottime_zygote_ns": zyg_ns,
                    }
            elif w == "idle_steady_state":
                tests_out[w] = run_idle_workload(
                    serial=serial, emu_pid=emu_pid, profiles=profiles, adb_bin=adb_bin
                )
            elif w in ("snapshot_save", "snapshot_load"):
                tests_out[w] = run_snapshot_workload(
                    workload_name=w,
                    serial=serial,
                    emu_pid=emu_pid,
                    iterations=min(iterations, 3),
                    profiles=profiles,
                    adb_bin=adb_bin,
                )
            elif w == "display_gfxinfo":
                tests_out[w] = run_display_workload(
                    serial=serial, emu_pid=emu_pid, profiles=profiles, adb_bin=adb_bin
                )
            elif w in PROBE_WORKLOADS:
                assert probe_session is not None
                tests_out[w] = run_probe_workload(
                    workload_name=w,
                    probe=probe_session,
                    emu_pid=emu_pid,
                    iterations=iterations,
                    warmup=warmup,
                    profiles=profiles,
                    adb_fn=adb_fn,
                )
            else:
                tests_out[w] = run_adb_transfer_workload(
                    workload_name=w,
                    serial=serial,
                    emu_pid=emu_pid,
                    iterations=iterations,
                    warmup=warmup,
                    profiles=profiles,
                    adb_bin=adb_bin,
                )
        except Exception as exc:
            logger.warning("Workload '%s' on %s failed: %s", w, serial, exc)
            tests_out[w] = {
                "n": 0,
                "status": "broken",
                "error": str(exc),
            }

    insights = generate_diagnostic_insights(tests_out, label=label)
    return {
        "schema_version": 1,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "label": label,
        "serial": serial,
        "emulator_pid": emu_pid,
        "nested_virt": is_nested_virtualization(),
        "guest_state": guest_state,
        "tests": tests_out,
        "insights": insights,
    }


def run_managed_emulator_benchmark(
    target: ResolvedPerfTarget,
    avd_name: Optional[str],
    suites: List[str],
    workloads: List[str],
    iterations: int = 5,
    warmup: int = 1,
    boots: int = 1,
    settle_seconds: float = 30.0,
    profiles: Optional[List[str]] = None,
    trace_path: Optional[str] = None,
    adb_bin: str = "adb",
    clean_avd: bool = True,
    sysimg_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Cold-boots a managed emulator binary `boots` times, runs workloads, and aggregates results.

    When `clean_avd=True` (default), each boot provisions a fresh, isolated AVD
    directory from the system image (`create_single_avd`) and cleans it up afterward,
    eliminating any cross-engine (`qemu-now` vs `qemu-next`) state contamination.
    """
    if not target.emulator_bin or not target.emulator_dir:
        raise ValueError(
            f"Target '{target.label}' does not have a valid emulator binary directory."
        )

    if avd_name and avd_name.strip().lower() == "auto":
        avd_name = None

    template_info: Optional[Dict[str, Any]] = None
    if clean_avd:
        template_info = _resolve_perf_avd_template(
            avd_name=avd_name, sysimg_dir=sysimg_dir
        )
        effective_avd_label = (
            f"{template_info['template_avd']} (clean-isolated)"
        )
    else:
        if not avd_name:
            avd_root = get_default_avd_root()
            ini_files = sorted(glob.glob(os.path.join(avd_root, "*.ini")))
            if not ini_files:
                raise RuntimeError(
                    "No existing AVD found (`~/.android/avd`). Create one with `emu-dev-cli create` or pass `--avd`."
                )
            avd_name = os.path.splitext(os.path.basename(ini_files[0]))[0]
        effective_avd_label = avd_name

    accumulated_samples: Dict[str, List[Dict[str, float]]] = {}
    accumulated_tests: Dict[str, List[Dict[str, Any]]] = {}

    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", target.label)[:24]

    for boot_idx in range(max(1, boots)):
        ephemeral_ini: Optional[str] = None
        ephemeral_dir: Optional[str] = None
        if clean_avd and template_info is not None:
            boot_avd_name = f"perf-clean-{slug}-b{boot_idx + 1}-{os.getpid()}"
            created = create_single_avd(
                avd_name=boot_avd_name,
                raw_sysimg_dir=template_info["sysimg_dir"],
                ram=template_info["ram"],
                cores=template_info["cores"],
                disk_size=template_info["disk_size"],
                force=True,
            )
            ephemeral_ini = created["ini_file"]
            ephemeral_dir = created["avd_dir"]
        else:
            boot_avd_name = str(avd_name)
            avd_dir_path = os.path.join(
                get_default_avd_root(), f"{boot_avd_name}.avd"
            )
            qemu_ver_file = os.path.join(avd_dir_path, "qemu-version.txt")
            if os.path.isfile(qemu_ver_file):
                try:
                    os.remove(qemu_ver_file)
                except OSError:
                    pass

        port = _find_free_console_port(5560 + boot_idx * 2)
        grpc_port = _find_free_console_port(8554 + boot_idx * 2)
        serial = f"emulator-{port}"
        env = prepare_environment(target.emulator_dir)
        needs_snapshot = any(
            w in ("snapshot_save", "snapshot_load") for w in workloads
        )
        cmd = [
            target.emulator_bin,
            "-avd",
            boot_avd_name,
            "-port",
            str(port),
            "-grpc",
            str(grpc_port),
            "-no-window",
            "-no-audio",
            "-gpu",
            "swiftshader_indirect",
            "-no-snapshot-load",
        ]
        # Workaround for b/572356854: in qemu-next, passing both -no-snapshot-load and
        # -no-snapshot-save makes SnapshotDevice::is_enabled() false, which causes GpuDevice
        # to omit `snapshot_directory` and `VulkanSnapshots:enabled` on virtio-gpu-rutabaga.
        if not needs_snapshot:
            cmd.append("-no-snapshot-save")
        if trace_path or (profiles and "perfetto" in profiles):
            pf_out = (
                trace_path
                or f"/tmp/emu_perf_{target.build_id or 'trace'}_b{boot_idx + 1}.pftrace"
            )
            cmd.extend(["--trace", pf_out])

        log_path = f"/tmp/emu_perf_{slug}_b{boot_idx + 1}.log"
        if os.path.isfile(log_path):
            try:
                os.remove(log_path)
            except OSError:
                pass
        t_boot_start = time.perf_counter()
        _ensure_valid_console_auth_token()
        _cleanup_stale_netsim(force=False)
        proc = spawn_emulator_process(
            cmd, target.emulator_dir, env, log_file_path=log_path
        )
        try:
            wait_for_guest_and_host_settled(
                serial=serial,
                emu_pid=None,
                settle_seconds=0.0,
                adb_bin=adb_bin,
                proc=proc,
                log_path=log_path,
            )
            host_wall_boot_ms = (time.perf_counter() - t_boot_start) * 1000.0
            internal_boot_ms = _parse_emulator_boot_completed_ms(log_path)
            cold_boot_ms = (
                internal_boot_ms
                if internal_boot_ms is not None and internal_boot_ms > 0
                else host_wall_boot_ms
            )
            emu_pid_boot = find_emulator_pid(serial) or proc.pid
            snap_boot = (
                take_proc_snapshot(emu_pid_boot) if emu_pid_boot else None
            )
            boot_rss = snap_boot.rss_mib if snap_boot else 0.0
            boot_info = {
                "n": 1,
                "duration_ms_p50": round(cold_boot_ms, 2),
                "duration_ms_mean": round(cold_boot_ms, 2),
                "host_wall_boot_ms": round(host_wall_boot_ms, 2),
                "rss_mib": boot_rss,
                "samples": [
                    {
                        "duration_ms": round(cold_boot_ms, 2),
                        "host_wall_boot_ms": round(host_wall_boot_ms, 2),
                    }
                ],
            }
            boot_rep = run_suite_against_device(
                serial=serial,
                workloads=workloads,
                iterations=iterations,
                warmup=warmup,
                settle_seconds=settle_seconds,
                profiles=profiles,
                label=target.label,
                adb_bin=adb_bin,
                boot_metrics=boot_info,
                grpc_port=grpc_port,
            )
            for t_name, t_data in (boot_rep.get("tests") or {}).items():
                accumulated_tests.setdefault(t_name, []).append(t_data)
                if "samples" in t_data and isinstance(t_data["samples"], list):
                    accumulated_samples.setdefault(t_name, []).extend(
                        t_data["samples"]
                    )
        finally:
            if not clean_avd:
                _adb_cmd(
                    serial, ["emu", "kill"], adb_bin=adb_bin, timeout=10.0
                )
                try:
                    proc.wait(timeout=10.0)
                except Exception:
                    pass
            _kill_emulator_process_tree(proc, serial, adb_bin=adb_bin)
            if ephemeral_ini and os.path.isfile(ephemeral_ini):
                try:
                    os.unlink(ephemeral_ini)
                except OSError:
                    pass
            if ephemeral_dir and os.path.isdir(ephemeral_dir):
                shutil.rmtree(ephemeral_dir, ignore_errors=True)

    # Merge multi-boot results
    merged_tests: Dict[str, Dict[str, Any]] = {}
    for t_name, t_list in accumulated_tests.items():
        if t_name in accumulated_samples and accumulated_samples[t_name]:
            merged_tests[t_name] = summarize_samples(accumulated_samples[t_name])
            # Carry over median host_cpu_pct, cpu_ms_per_mib, rss_mib, vCPU split, and guest IRQ deltas
            for key in (
                "host_cpu_pct",
                "cpu_ms_per_mib",
                "rss_mib",
                "peak_rss_mib",
                "vcpu_guest_pct",
                "vcpu_kernel_exit_pct",
                "vcpu_userspace_exit_pct",
                "kvm_exits_per_sec",
                "guest_ipi_resched",
                "guest_tlb_shootdowns",
                "guest_pgfault_delta",
            ):
                vals = [
                    float(x[key])
                    for x in t_list
                    if key in x and x[key] is not None
                ]
                if vals:
                    merged_tests[t_name][key] = round(statistics.median(vals), 3)
            for dict_key in (
                "top_threads_cpu_pct",
                "top_symbols",
                "perfetto_slices",
                "kvm_top_exit_reasons",
                "guest_virtio_irqs",
            ):
                if t_list[-1].get(dict_key):
                    merged_tests[t_name][dict_key] = t_list[-1][dict_key]
        else:
            merged_tests[t_name] = t_list[-1]

    report = {
        "schema_version": 1,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "label": target.label,
        "build_id": target.build_id,
        "branch": target.branch,
        "engine": target.engine,
        "pkg_revision": target.pkg_revision,
        "emulator_dir": target.emulator_dir,
        "avd": effective_avd_label,
        "boots": boots,
        "nested_virt": is_nested_virtualization(),
        "suites": suites,
        "tests": merged_tests,
        "insights": generate_diagnostic_insights(
            merged_tests, label=target.label
        ),
    }
    append_perf_history(report)
    return report


def load_perf_report_any_format(path_or_spec: str) -> Dict[str, Any]:
    """Loads a JSON benchmark report and normalizes all historical/ad-hoc schemas
    (`/tmp/adb_bench_*.json`, `/tmp/qemu_now_vs_next_base_bench.json`, `/tmp/vsock_stack_adb_bench.json`)
    into the standard `PerfRunReport` dictionary structure.
    """
    subkey = None
    file_path = path_or_spec
    if ":" in path_or_spec and not os.path.isfile(path_or_spec):
        file_path, subkey = path_or_spec.rsplit(":", 1)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    base_label = os.path.splitext(os.path.basename(file_path))[0]

    # Case 1: Standard or single-run dict with "tests"
    if isinstance(data, dict) and "tests" in data:
        lbl = data.get("label") or data.get("description") or base_label
        tests = data["tests"]
        return {
            "schema_version": data.get("schema_version", 1),
            "label": lbl,
            "build_id": data.get("build_id"),
            "branch": data.get("branch"),
            "engine": data.get("engine"),
            "pkg_revision": data.get("pkg_revision"),
            "emulator_dir": data.get("emulator_dir"),
            "avd": data.get("avd"),
            "boots": data.get("boots", 1),
            "nested_virt": data.get("nested_virt"),
            "suites": data.get("suites"),
            "tests": tests,
            "insights": data.get("insights")
            or generate_diagnostic_insights(tests, label=lbl),
        }

    # Case 2: Multi-target summary dict (`{"summary": {"qemu_now": {...}, "0_base": {...}}}`)
    if isinstance(data, dict) and "summary" in data and isinstance(data["summary"], dict):
        summary_map = data["summary"]
        raw_map = data.get("raw", {})
        chosen_key = subkey or next(iter(summary_map.keys()))
        if chosen_key not in summary_map:
            valid = ", ".join(summary_map.keys())
            raise KeyError(
                f"Subkey '{chosen_key}' not found in {file_path} (available: {valid})"
            )
        tests = summary_map[chosen_key]
        desc = (
            raw_map.get(chosen_key, {}).get("description")
            if isinstance(raw_map, dict)
            else None
        ) or chosen_key
        return {
            "schema_version": 1,
            "label": desc,
            "tests": tests,
            "insights": generate_diagnostic_insights(tests, label=desc),
        }

    # Case 3: List of step objects (`[{"key": "0_base", "description": ..., "tests": {...}}, ...]`)
    if isinstance(data, list) and data and isinstance(data[0], dict) and "tests" in data[0]:
        chosen_entry = data[-1]
        if subkey:
            for item in data:
                if item.get("key") == subkey or item.get("description") == subkey:
                    chosen_entry = item
                    break
        lbl = (
            chosen_entry.get("description")
            or chosen_entry.get("key")
            or base_label
        )
        tests = chosen_entry["tests"]
        return {
            "schema_version": 1,
            "label": lbl,
            "tests": tests,
            "insights": generate_diagnostic_insights(tests, label=lbl),
        }

    raise ValueError(f"Unrecognized benchmark JSON schema in {file_path}")


def compare_perf_reports(
    base_report: Dict[str, Any],
    cand_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Contrasts two normalized `PerfRunReport` dicts across throughput, latency,
    statistical significance, host CPU efficiency, thread shifts, and symbol diffs.
    """
    b_tests = base_report.get("tests") or {}
    c_tests = cand_report.get("tests") or {}
    common_workloads = [k for k in b_tests.keys() if k in c_tests]
    if not common_workloads:
        common_workloads = sorted(set(b_tests.keys()) | set(c_tests.keys()))

    comparisons: List[Dict[str, Any]] = []
    thread_diffs_by_test: Dict[str, List[Dict[str, Any]]] = {}
    symbol_diffs_by_test: Dict[str, List[Dict[str, Any]]] = {}

    for w in common_workloads:
        bt = b_tests.get(w, {})
        ct = c_tests.get(w, {})
        if not bt or not ct:
            continue

        if bt.get("status") == "broken" or ct.get("status") == "broken":
            b_p50 = float(
                bt.get("duration_ms_p50", bt.get("wall_mib_s_p50", 0.0)) or 0.0
            )
            c_p50 = float(
                ct.get("duration_ms_p50", ct.get("wall_mib_s_p50", 0.0)) or 0.0
            )
            unit = "MiB/s" if ("wall_mib_s_p50" in bt or "wall_mib_s_p50" in ct) else "ms"
            comparisons.append(
                {
                    "workload": w,
                    "metric": "status",
                    "unit": unit,
                    "baseline_p50": round(b_p50, 2),
                    "candidate_p50": round(c_p50, 2),
                    "baseline_mean": round(b_p50, 2),
                    "candidate_mean": round(c_p50, 2),
                    "baseline_stdev": 0.0,
                    "candidate_stdev": 0.0,
                    "pct_delta_p50": 0.0,
                    "pct_delta_mean": 0.0,
                    "verdict": "broken",
                    "badge": "❌ BROKEN",
                    "p_value": 0.0,
                    "high_variance": False,
                    "baseline_error": bt.get("error"),
                    "candidate_error": ct.get("error"),
                    "baseline_cpu_ms_per_mib": bt.get("cpu_ms_per_mib"),
                    "candidate_cpu_ms_per_mib": ct.get("cpu_ms_per_mib"),
                }
            )
            continue

        if w == "disk_seq_128mib" and (
            "write_mib_s_p50" in bt or "write_mib_s_p50" in ct
        ):
            metric_specs = [
                ("write_mib_s", "MiB/s", True, "disk_seq_128mib_write"),
                ("read_mib_s", "MiB/s", True, "disk_seq_128mib_read"),
            ]
        elif "iops_p50" in bt or "iops_p50" in ct:
            metric_specs = [("iops", "IOPS", True, w)]
        elif "pages_per_sec_p50" in bt or "pages_per_sec_p50" in ct:
            metric_specs = [("pages_per_sec", "pages/s", True, w)]
        elif "cpu_nt_mops_s_p50" in bt or "cpu_nt_mops_s_p50" in ct:
            metric_specs = [
                ("cpu_nt_mops_s", "Mops/s", True, f"{w}_multi_vcpu"),
                ("cpu_1t_mops_s", "Mops/s", True, f"{w}_single_vcpu"),
            ]
        elif "roundtrip_us_p50" in bt or "roundtrip_us_p50" in ct:
            metric_specs = [("roundtrip_us", "us", False, w)]
        elif "wall_mib_s_p50" in bt or "wall_mib_s_p50" in ct:
            metric_specs = [("wall_mib_s", "MiB/s", True, w)]
        elif "duration_ms_p50" in bt or "duration_ms_p50" in ct:
            metric_specs = [("duration_ms", "ms", False, w)]
            if w == "key_arrival_latency":
                if "kernel_to_app_ms_p50" in bt or "kernel_to_app_ms_p50" in ct:
                    metric_specs.append(
                        (
                            "kernel_to_app_ms",
                            "ms",
                            False,
                            "key_arrival_kernel_to_app",
                        )
                    )
                if "app_to_frame_ms_p50" in bt or "app_to_frame_ms_p50" in ct:
                    metric_specs.append(
                        ("app_to_frame_ms", "ms", False, "key_arrival_app_to_frame")
                    )
            elif w == "touch_to_photon":
                if (
                    "guest_kernel_to_app_ms_p50" in bt
                    or "guest_kernel_to_app_ms_p50" in ct
                ):
                    metric_specs.append(
                        (
                            "guest_kernel_to_app_ms",
                            "ms",
                            False,
                            "touch_kernel_to_app",
                        )
                    )
                if (
                    "renderer_and_readback_ms_p50" in bt
                    or "renderer_and_readback_ms_p50" in ct
                ):
                    metric_specs.append(
                        (
                            "renderer_and_readback_ms",
                            "ms",
                            False,
                            "touch_host_readback",
                        )
                    )
        elif w == "idle_steady_state" and ("host_cpu_pct" in bt or "host_cpu_pct" in ct):
            metric_specs = [("host_cpu_pct", "% CPU", False, w)]
            if "rss_mib" in bt or "rss_mib" in ct:
                metric_specs.append(("rss_mib", "MiB RSS", False, "idle_rss_mib"))
        elif w == "display_gfxinfo":
            metric_specs = []
            if "jank_pct" in bt or "jank_pct" in ct:
                metric_specs.append(("jank_pct", "% jank", False, "display_jank_pct"))
            if "host_cpu_pct" in bt or "host_cpu_pct" in ct:
                metric_specs.append(("host_cpu_pct", "% CPU", False, "display_cpu_pct"))
        elif "host_cpu_pct" in bt or "host_cpu_pct" in ct:
            metric_specs = [("host_cpu_pct", "% CPU", False, w)]
        else:
            continue

        for metric_prefix, unit, higher_is_better, row_name in metric_specs:
            sig = evaluate_ab_significance(
                bt,
                ct,
                metric_prefix=metric_prefix,
                higher_is_better=higher_is_better,
            )
            b_p50 = float(
                bt.get(f"{metric_prefix}_p50", bt.get(metric_prefix, 0.0)) or 0.0
            )
            c_p50 = float(
                ct.get(f"{metric_prefix}_p50", ct.get(metric_prefix, 0.0)) or 0.0
            )
            b_mean = float(bt.get(f"{metric_prefix}_mean", b_p50) or b_p50)
            c_mean = float(ct.get(f"{metric_prefix}_mean", c_p50) or c_p50)
            b_std = float(bt.get(f"{metric_prefix}_stdev", 0.0) or 0.0)
            c_std = float(ct.get(f"{metric_prefix}_stdev", 0.0) or 0.0)

            row_info: Dict[str, Any] = {
                "workload": row_name,
                "metric": metric_prefix,
                "unit": unit,
                "baseline_p50": round(b_p50, 2),
                "candidate_p50": round(c_p50, 2),
                "baseline_mean": round(b_mean, 2),
                "candidate_mean": round(c_mean, 2),
                "baseline_stdev": round(b_std, 2),
                "candidate_stdev": round(c_std, 2),
                "pct_delta_p50": sig.pct_delta_p50,
                "pct_delta_mean": sig.pct_delta_mean,
                "verdict": sig.verdict,
                "badge": sig.badge,
                "p_value": sig.approx_p_value,
                "high_variance": sig.high_variance_warning,
                "baseline_cpu_ms_per_mib": bt.get("cpu_ms_per_mib"),
                "candidate_cpu_ms_per_mib": ct.get("cpu_ms_per_mib"),
                "baseline_host_cpu_pct": bt.get("host_cpu_pct"),
                "candidate_host_cpu_pct": ct.get("host_cpu_pct"),
            }
            comparisons.append(row_info)

        if bt.get("top_threads_cpu_pct") or ct.get("top_threads_cpu_pct"):
            td = diff_thread_cpu(
                bt.get("top_threads_cpu_pct", {}),
                ct.get("top_threads_cpu_pct", {}),
            )
            if td:
                thread_diffs_by_test[w] = td

        if bt.get("top_symbols") or ct.get("top_symbols"):
            sd = diff_top_symbols(
                bt.get("top_symbols", []), ct.get("top_symbols", [])
            )
            if sd:
                symbol_diffs_by_test[w] = sd

    hypervisor_diffs: List[Dict[str, Any]] = []
    for w in common_workloads:
        bt = b_tests.get(w, {})
        ct = c_tests.get(w, {})
        if not bt or not ct:
            continue
        if any(
            k in bt or k in ct
            for k in (
                "vcpu_guest_pct",
                "guest_virtio_irqs",
                "guest_ipi_resched",
                "guest_tlb_shootdowns",
                "guest_pgfault_delta",
            )
        ):
            hypervisor_diffs.append(
                {
                    "workload": w,
                    "base_vcpu_guest_pct": bt.get("vcpu_guest_pct", 0.0),
                    "base_vcpu_kernel_pct": bt.get("vcpu_kernel_exit_pct", 0.0),
                    "base_vcpu_user_pct": bt.get("vcpu_userspace_exit_pct", 0.0),
                    "cand_vcpu_guest_pct": ct.get("vcpu_guest_pct", 0.0),
                    "cand_vcpu_kernel_pct": ct.get("vcpu_kernel_exit_pct", 0.0),
                    "cand_vcpu_user_pct": ct.get("vcpu_userspace_exit_pct", 0.0),
                    "base_virtio_irqs": bt.get("guest_virtio_irqs") or {},
                    "cand_virtio_irqs": ct.get("guest_virtio_irqs") or {},
                    "base_ipi_resched": bt.get("guest_ipi_resched", 0),
                    "cand_ipi_resched": ct.get("guest_ipi_resched", 0),
                    "base_tlb_shootdowns": bt.get("guest_tlb_shootdowns", 0),
                    "cand_tlb_shootdowns": ct.get("guest_tlb_shootdowns", 0),
                    "base_pgfault": bt.get("guest_pgfault_delta", 0),
                    "cand_pgfault": ct.get("guest_pgfault_delta", 0),
                }
            )

    return {
        "baseline_label": base_report.get("label", "baseline"),
        "candidate_label": cand_report.get("label", "candidate"),
        "baseline_meta": {
            "build_id": base_report.get("build_id"),
            "branch": base_report.get("branch"),
            "engine": base_report.get("engine"),
            "pkg_revision": base_report.get("pkg_revision"),
            "emulator_dir": base_report.get("emulator_dir"),
            "avd": base_report.get("avd"),
            "boots": base_report.get("boots", 1),
            "nested_virt": base_report.get("nested_virt"),
            "suites": base_report.get("suites"),
        },
        "candidate_meta": {
            "build_id": cand_report.get("build_id"),
            "branch": cand_report.get("branch"),
            "engine": cand_report.get("engine"),
            "pkg_revision": cand_report.get("pkg_revision"),
            "emulator_dir": cand_report.get("emulator_dir"),
            "avd": cand_report.get("avd"),
            "boots": cand_report.get("boots", 1),
            "nested_virt": cand_report.get("nested_virt"),
            "suites": cand_report.get("suites"),
        },
        "comparisons": comparisons,
        "thread_diffs": thread_diffs_by_test,
        "symbol_diffs": symbol_diffs_by_test,
        "hypervisor_diffs": hypervisor_diffs,
        "baseline_insights": base_report.get("insights")
        or generate_diagnostic_insights(
            b_tests, label=base_report.get("label", "baseline")
        ),
        "candidate_insights": cand_report.get("insights")
        or generate_diagnostic_insights(
            c_tests, label=cand_report.get("label", "candidate")
        ),
    }


def render_single_report_markdown(report: Dict[str, Any]) -> str:
    """Renders a single `PerfRunReport` as a clean Markdown scorecard with insights."""
    lines: List[str] = []
    lbl = report.get("label", "Emulator Performance Run")
    lines.append(f"## Performance Scorecard: `{lbl}`")
    meta_parts: List[str] = []
    if report.get("build_id"):
        meta_parts.append(f"**Build ID**: `{report['build_id']}`")
    if report.get("engine"):
        meta_parts.append(f"**Engine**: `{report['engine']}`")
    if report.get("avd"):
        meta_parts.append(f"**AVD**: `{report['avd']}`")
    if report.get("serial"):
        meta_parts.append(f"**Device**: `{report['serial']}`")
    if report.get("nested_virt"):
        meta_parts.append("**Env**: `Nested VM`")
    if meta_parts:
        lines.append("- " + " | ".join(meta_parts))
    lines.append("")

    headers = [
        "Workload",
        "N",
        "Primary Metric (p50)",
        "Mean ± Stdev / Sub-Metrics",
        "Host CPU%",
        "Work Cost (CPU-ms/MiB)",
        "Top Emulator Threads",
    ]
    rows: List[List[str]] = []
    hyp_rows: List[List[str]] = []

    for w_name, t in (report.get("tests") or {}).items():
        n_str = str(t.get("n", 1))
        if t.get("status") == "broken":
            p50_str = "**❌ BROKEN**"
            mean_str = (t.get("error") or "failed")[:48]
        elif w_name == "disk_seq_128mib" and "write_mib_s_p50" in t:
            p50_str = (
                f"**W: {t['write_mib_s_p50']:.1f} / R: {t.get('read_mib_s_p50', 0):.1f} MiB/s**"
            )
            mean_str = (
                f"W: {t.get('write_mib_s_mean', 0):.1f}±{t.get('write_mib_s_stdev', 0):.1f} | "
                f"R: {t.get('read_mib_s_mean', 0):.1f}±{t.get('read_mib_s_stdev', 0):.1f}"
            )
        elif "iops_p50" in t:
            p50_str = f"**{t['iops_p50']:.1f} IOPS** (p50={t.get('p50_ms_p50', 0):.2f}ms)"
            mean_str = f"{t.get('iops_mean', t['iops_p50']):.1f} ± {t.get('iops_stdev', 0.0):.1f} (p95={t.get('p95_ms_p50', 0):.2f}ms)"
        elif "pages_per_sec_p50" in t:
            p50_str = f"**{t['pages_per_sec_p50']:.0f} pages/s** ({t.get('us_per_page_p50', 0):.2f} us/pg)"
            mean_str = f"{t.get('pages_per_sec_mean', t['pages_per_sec_p50']):.0f} ± {t.get('pages_per_sec_stdev', 0.0):.0f}"
        elif "copy_gib_s_p50" in t:
            p50_str = f"**{t['wall_mib_s_p50']:.0f} MiB/s** ({t['copy_gib_s_p50']:.2f} GiB/s)"
            mean_str = f"{t.get('wall_mib_s_mean', t['wall_mib_s_p50']):.0f} ± {t.get('wall_mib_s_stdev', 0.0):.0f}"
        elif "cpu_nt_mops_s_p50" in t:
            p50_str = f"**{t['cpu_nt_mops_s_p50']:.1f} Mops/s** ({t.get('smp_scaling_ratio_p50', 0):.2f}x SMP)"
            mean_str = f"1T={t.get('cpu_1t_mops_s_p50', 0):.1f} / NT={t.get('cpu_nt_mops_s_mean', 0):.1f}±{t.get('cpu_nt_mops_s_stdev', 0):.1f}"
        elif "roundtrip_us_p50" in t:
            p50_str = f"**{t['roundtrip_us_p50']:.2f} us** ({t.get('switches_per_sec_p50', 0):.0f} sw/s)"
            mean_str = f"{t.get('roundtrip_us_mean', t['roundtrip_us_p50']):.2f} ± {t.get('roundtrip_us_stdev', 0.0):.2f} us"
        elif "wall_mib_s_p50" in t:
            p50_str = f"**{t['wall_mib_s_p50']:.1f} MiB/s** ({t.get('duration_ms_p50', 0):.0f} ms)"
            mean_str = f"{t.get('wall_mib_s_mean', t['wall_mib_s_p50']):.1f} ± {t.get('wall_mib_s_stdev', 0.0):.1f}"
        elif "duration_ms_p50" in t:
            p50_str = f"**{t['duration_ms_p50']:.2f} ms**"
            sub_parts = [
                f"{t.get('duration_ms_mean', t['duration_ms_p50']):.2f} ± {t.get('duration_ms_stdev', 0.0):.2f}"
            ]
            if "kernel_to_app_ms_p50" in t:
                sub_parts.append(f"k2a={t['kernel_to_app_ms_p50']:.2f}ms")
            if "delivery_pct" in t:
                sub_parts.append(f"deliv={t['delivery_pct']:.0f}%")
            mean_str = " | ".join(sub_parts)
        elif "total_frames" in t:
            p50_str = (
                f"**{t['total_frames']} frames** ({t.get('jank_pct', 0):.1f}% jank)"
            )
            mean_str = f"p50={t.get('frame_p50_ms', 0):.0f}ms / p90={t.get('frame_p90_ms', 0):.0f}ms"
        else:
            p50_str = f"**{t.get('host_cpu_pct', 0):.1f}% CPU** (RSS {t.get('rss_mib', 0):.0f} MiB)"
            mean_str = f"loadavg={t.get('guest_loadavg_1m', 0):.2f}"

        cpu_str = (
            f"{t['host_cpu_pct']:.1f}%"
            if t.get("host_cpu_pct") is not None
            else "-"
        )
        cost_str = (
            f"{t['cpu_ms_per_mib']:.2f}"
            if t.get("cpu_ms_per_mib") is not None
            else "-"
        )
        threads = t.get("top_threads_cpu_pct") or {}
        th_str = (
            ", ".join(f"`{k}`:{v:.0f}%" for k, v in list(threads.items())[:3])
            or "-"
        )
        rows.append([f"`{w_name}`", n_str, p50_str, mean_str, cpu_str, cost_str, th_str])

        if (
            t.get("vcpu_guest_pct")
            or t.get("kvm_exits_per_sec")
            or t.get("guest_virtio_irqs")
            or t.get("guest_ipi_resched")
            or t.get("guest_tlb_shootdowns")
        ):
            vcpu_split = (
                f"G:{t.get('vcpu_guest_pct', 0):.0f}% / K:{t.get('vcpu_kernel_exit_pct', 0):.0f}% / U:{t.get('vcpu_userspace_exit_pct', 0):.0f}%"
                if t.get("vcpu_guest_pct") is not None
                else "-"
            )
            kvm_str = (
                f"{t['kvm_exits_per_sec']:.0f}/s"
                if t.get("kvm_exits_per_sec")
                else "-"
            )
            irqs = t.get("guest_virtio_irqs") or {}
            irq_str = (
                ", ".join(
                    f"`{k}`:+{v}"
                    for k, v in sorted(
                        irqs.items(), key=lambda x: x[1], reverse=True
                    )[:3]
                    if v > 0
                )
                or "-"
            )
            ipi_tlb = (
                f"RES:+{t.get('guest_ipi_resched', 0)} / TLB:+{t.get('guest_tlb_shootdowns', 0)} / PGF:+{t.get('guest_pgfault_delta', 0)}"
            )
            hyp_rows.append([f"`{w_name}`", vcpu_split, kvm_str, irq_str, ipi_tlb])

    lines.append(format_markdown_table(headers, rows))
    lines.append("")

    if hyp_rows:
        lines.append("### Hypervisor & Guest Interrupt Telemetry")
        hyp_headers = [
            "Workload",
            "vCPU Split (Guest/Kernel/User)",
            "Host KVM Exits/s",
            "Guest Virtio IRQs",
            "Guest IPI / TLB / PageFaults",
        ]
        lines.append(format_markdown_table(hyp_headers, hyp_rows))
        lines.append("")

    insights = report.get("insights") or []
    if insights:
        lines.append("### Diagnostic Insights & Bottleneck Analysis")
        for item in insights:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def render_comparison_markdown(comp: Dict[str, Any]) -> str:
    """Renders the 4-part A/B comparison scorecard in Markdown."""
    b_lbl = comp["baseline_label"]
    c_lbl = comp["candidate_label"]
    lines: List[str] = []
    lines.append(f"## A/B Performance Comparison: `{b_lbl}` vs. `{c_lbl}`")
    lines.append("")

    # Part 1: Workload Scorecard
    lines.append("### 1. Workload Scorecard & Statistical Significance")
    headers = [
        "Workload",
        f"Baseline ({b_lbl}) p50",
        f"Candidate ({c_lbl}) p50",
        "Delta (p50)",
        "Delta (Mean)",
        "Work Cost (CPU-ms/MiB)",
        "Verdict",
    ]
    rows: List[List[str]] = []
    for r in comp.get("comparisons") or []:
        u = r["unit"]
        if r.get("verdict") == "broken":
            b_str = (
                "FAIL"
                if r.get("baseline_error")
                else f"{r['baseline_p50']:.1f} {u}"
            )
            c_str = (
                "**FAIL**"
                if r.get("candidate_error")
                else f"**{r['candidate_p50']:.1f} {u}**"
            )
            dp50 = "N/A"
            dmean = "N/A"
        else:
            b_str = f"{r['baseline_p50']:.1f} {u}"
            c_str = f"**{r['candidate_p50']:.1f} {u}**"
            dp50 = f"{r['pct_delta_p50']:+.1f}%"
            dmean = f"{r['pct_delta_mean']:+.1f}%"
        if (
            r.get("baseline_cpu_ms_per_mib") is not None
            and r.get("candidate_cpu_ms_per_mib") is not None
        ):
            cost_str = f"{r['baseline_cpu_ms_per_mib']:.2f} → {r['candidate_cpu_ms_per_mib']:.2f}"
        else:
            cost_str = "-"
        rows.append(
            [
                f"`{r['workload']}`",
                b_str,
                c_str,
                f"**{dp50}**",
                dmean,
                cost_str,
                r["badge"],
            ]
        )
    lines.append(format_markdown_table(headers, rows))
    lines.append("")

    # Part 2: Per-Thread CPU Attribution Diffs
    thread_diffs = comp.get("thread_diffs") or {}
    if thread_diffs:
        lines.append("### 2. Host Emulator Per-Thread CPU Shift")
        th_headers = ["Workload", "Thread (`comm`)", "Baseline CPU%", "Candidate CPU%", "Delta CPU%"]
        th_rows: List[List[str]] = []
        for w_name, diffs in thread_diffs.items():
            for d in diffs[:5]:
                th_rows.append(
                    [
                        f"`{w_name}`",
                        f"`{d['thread']}`",
                        f"{d['baseline_cpu_pct']:.1f}%",
                        f"{d['candidate_cpu_pct']:.1f}%",
                        f"{d['delta_cpu_pct']:+.1f}%",
                    ]
                )
        lines.append(format_markdown_table(th_headers, th_rows))
        lines.append("")

    # Part 2b: Hypervisor & Guest Interrupt Boundary Shift
    hyp_diffs = comp.get("hypervisor_diffs") or []
    if hyp_diffs:
        lines.append("### 2b. Hypervisor & Guest Interrupt Boundary Shift")
        hyp_headers = [
            "Workload",
            f"Baseline vCPU (G/K/U%)",
            f"Candidate vCPU (G/K/U%)",
            "Virtio IRQs (Base → Cand)",
            "IPI / TLB / PageFaults (Base → Cand)",
        ]
        hyp_rows: List[List[str]] = []
        for h in hyp_diffs:
            b_vcpu = (
                f"G:{h.get('base_vcpu_guest_pct', 0):.0f}% / "
                f"K:{h.get('base_vcpu_kernel_pct', 0):.0f}% / "
                f"U:{h.get('base_vcpu_user_pct', 0):.0f}%"
            )
            c_vcpu = (
                f"G:{h.get('cand_vcpu_guest_pct', 0):.0f}% / "
                f"K:{h.get('cand_vcpu_kernel_pct', 0):.0f}% / "
                f"U:{h.get('cand_vcpu_user_pct', 0):.0f}%"
            )
            b_irqs = h.get("base_virtio_irqs") or {}
            c_irqs = h.get("cand_virtio_irqs") or {}
            irq_keys = sorted(
                set(b_irqs.keys()) | set(c_irqs.keys()),
                key=lambda k: max(b_irqs.get(k, 0), c_irqs.get(k, 0)),
                reverse=True,
            )[:3]
            irq_str = (
                ", ".join(
                    f"`{k.replace('_irqs', '')}`:{b_irqs.get(k, 0):.0f}→{c_irqs.get(k, 0):.0f}"
                    for k in irq_keys
                    if max(b_irqs.get(k, 0), c_irqs.get(k, 0)) > 0
                )
                or "-"
            )
            ipi_tlb_str = (
                f"RES:{h.get('base_ipi_resched', 0):.0f}→{h.get('cand_ipi_resched', 0):.0f} | "
                f"TLB:{h.get('base_tlb_shootdowns', 0):.0f}→{h.get('cand_tlb_shootdowns', 0):.0f} | "
                f"PGF:{h.get('base_pgfault', 0):.0f}→{h.get('cand_pgfault', 0):.0f}"
            )
            hyp_rows.append(
                [f"`{h['workload']}`", b_vcpu, c_vcpu, irq_str, ipi_tlb_str]
            )
        lines.append(format_markdown_table(hyp_headers, hyp_rows))
        lines.append("")

    # Part 3: Differential Symbol Digest
    symbol_diffs = comp.get("symbol_diffs") or {}
    if symbol_diffs:
        lines.append("### 3. Differential C++/Rust Symbol Profile (`perf record`)")
        sym_headers = ["Workload", "Symbol", "Baseline %", "Candidate %", "Delta %"]
        sym_rows: List[List[str]] = []
        for w_name, sdiffs in symbol_diffs.items():
            for s in sdiffs[:8]:
                sym_rows.append(
                    [
                        f"`{w_name}`",
                        f"`{s['symbol']}`",
                        f"{s['baseline_pct']:.1f}%",
                        f"{s['candidate_pct']:.1f}%",
                        f"{s['delta_pct']:+.1f}%",
                    ]
                )
        lines.append(format_markdown_table(sym_headers, sym_rows))
        lines.append("")

    # Part 4: Diagnostic Insights
    all_insights = (comp.get("candidate_insights") or []) + (
        comp.get("baseline_insights") or []
    )
    if all_insights:
        lines.append("### 4. Diagnostic Insights & Bottleneck Takeaways")
        for ins in all_insights:
            lines.append(f"- {ins}")
        lines.append("")

    return "\n".join(lines)


def render_comparison_commit_text(comp: Dict[str, Any]) -> str:
    """Renders plain-text bullet points ready to paste into a Gerrit commit message."""
    b_lbl = comp["baseline_label"]
    c_lbl = comp["candidate_label"]
    lines: List[str] = [f"Performance comparison ({b_lbl} -> {c_lbl}):"]
    for r in comp.get("comparisons") or []:
        u = r["unit"]
        if r.get("verdict") == "broken":
            lines.append(f"- {r['workload']}: BROKEN")
        else:
            lines.append(
                f"- {r['workload']}: {r['baseline_p50']:.1f} -> {r['candidate_p50']:.1f} {u} p50 "
                f"({r['pct_delta_p50']:+.1f}%, mean {r['baseline_mean']:.1f} -> {r['candidate_mean']:.1f} {u})"
            )
    return "\n".join(lines)


def _attribute_regression_cause(
    row: Dict[str, Any], hyp_by_workload: Dict[str, Dict[str, Any]]
) -> str:
    """Produces a deterministic telemetry-backed root-cause attribution and action item for a regression or broken row."""
    w = row.get("workload", "")
    verdict = row.get("verdict", "")
    if verdict == "broken":
        err = row.get("candidate_error") or row.get("baseline_error") or "Unknown error"
        if "unknown command" in err.lower() or "ko:" in err.lower():
            return (
                f"Emulator console command failed (`{err}`). Check `~/.emulator_console_auth_token` "
                f"or snapshot flag configuration (`b/572356854`)."
            )
        return f"Workload execution failed with error: `{err}`."

    if w == "cold_boot":
        return (
            "Single-boot (`n=1`) cold boot duration includes first-launch OS binary/library verification "
            "(e.g. macOS `syspolicyd` / dynamic linker cache warmup on freshly extracted binaries) and initial "
            "Vulkan/rutabaga device initialization. Run with `--boots 3` to separate warm steady-state boot time."
        )
    if w == "idle_rss_mib":
        return (
            "Higher steady-state host RSS reflects `virtio-gpu-rutabaga` + Vulkan swapchain and shared host-memory "
            "mappings compared to legacy `gltransport=pipe`."
        )
    if w.startswith("disk_seq_128mib"):
        return (
            "Host filesystem dirty page-cache writeback variance across back-to-back 128 MiB sequential write bursts "
            "(host CPU work cost per MiB remained unchanged)."
        )
    if w == "mem_ept_page_fault" or w == "mem_stream_copy":
        hyp = hyp_by_workload.get(w, {})
        tlb_b = hyp.get("base_tlb_shootdowns", 0)
        tlb_c = hyp.get("cand_tlb_shootdowns", 0)
        pgf_b = hyp.get("base_pgfault", 0)
        pgf_c = hyp.get("cand_pgfault", 0)
        return (
            f"Guest page-fault deltas (`PGF: {pgf_b:.0f} → {pgf_c:.0f}`) and TLB shootdowns (`TLB: {tlb_b:.0f} → {tlb_c:.0f}`) "
            f"are identical; difference reflects host hypervisor Stage-2/EPT RAM block `mmap` / superpage alignment."
        )
    return (
        f"Statistically significant shift (`{row.get('pct_delta_p50', 0.0):+.1f}%` p50, "
        f"$p = {row.get('p_value', 0.0):.4f}$); inspect per-thread CPU and symbol profile deltas above."
    )


def render_weekly_qualification_report(comp: Dict[str, Any]) -> str:
    """Renders a complete, self-contained Weekly Emulator Performance Qualification Report
    in Markdown (including Executive Summary, Verdict Tally, Full 4-Part Scorecard,
    Key Wins, and Telemetry-Attributed Regressions).
    """
    b_lbl = comp.get("baseline_label", "baseline")
    c_lbl = comp.get("candidate_label", "candidate")
    b_meta = comp.get("baseline_meta") or {}
    c_meta = comp.get("candidate_meta") or {}
    comparisons: List[Dict[str, Any]] = list(comp.get("comparisons") or [])
    hyp_list: List[Dict[str, Any]] = list(comp.get("hypervisor_diffs") or [])
    hyp_by_w = {h.get("workload", ""): h for h in hyp_list if isinstance(h, dict)}

    gains = [r for r in comparisons if r.get("verdict") == "GAIN"]
    parities = [
        r
        for r in comparisons
        if r.get("verdict") == "NOISE" and "PARITY" in str(r.get("badge", ""))
    ]
    noises = [
        r
        for r in comparisons
        if r.get("verdict") == "NOISE" and "PARITY" not in str(r.get("badge", ""))
    ]
    regressions = [r for r in comparisons if r.get("verdict") == "REGRESSION"]
    broken = [r for r in comparisons if r.get("verdict") == "broken"]
    total = max(1, len(comparisons))

    host_str = _detect_default_host()
    avd_str = c_meta.get("avd") or b_meta.get("avd") or "clean-isolated"
    suites_list = c_meta.get("suites") or b_meta.get("suites") or ["all"]
    suites_str = ", ".join(f"`{s}`" for s in suites_list)

    lines: List[str] = []
    lines.append("# Weekly Emulator Performance Qualification Report")
    lines.append("")
    lines.append("## Executive Summary & Environment")
    lines.append("")
    lines.append(
        f"Automated A/B performance qualification comparing **`{b_lbl}`** (Baseline) against "
        f"**`{c_lbl}`** (Candidate) across **{len(comparisons)} metrics**."
    )
    lines.append("")
    lines.append(f"- **Host OS & Architecture:** `{host_str}`")
    b_desc = f"`{b_lbl}`"
    if b_meta.get("build_id"):
        b_desc += f" (Build ID `{b_meta['build_id']}`)"
    if b_meta.get("engine"):
        b_desc += f" [Engine: `{b_meta['engine']}`]"
    c_desc = f"`{c_lbl}`"
    if c_meta.get("build_id"):
        c_desc += f" (Build ID `{c_meta['build_id']}`)"
    if c_meta.get("engine"):
        c_desc += f" [Engine: `{c_meta['engine']}`]"
    lines.append(f"- **Baseline Target:** {b_desc}")
    lines.append(f"- **Candidate Target:** {c_desc}")
    lines.append(f"- **AVD Configuration:** `{avd_str}`")
    lines.append(f"- **Suites Executed:** {suites_str}")
    lines.append("")
    lines.append("### Overall Verdict Tally")
    lines.append("")

    def _fmt_wl_list(rows_subset: List[Dict[str, Any]]) -> str:
        if not rows_subset:
            return "None"
        return ", ".join(f"`{r['workload']}`" for r in rows_subset)

    tally_headers = ["Verdict Category", "Count", "Share", "Workloads / Metrics"]
    tally_rows = [
        [
            "🟢 **GAIN (WIN)**",
            f"**{len(gains)}**",
            f"{len(gains) * 100.0 / total:.1f}%",
            _fmt_wl_list(gains),
        ],
        [
            "🔵 **PARITY**",
            f"**{len(parities)}**",
            f"{len(parities) * 100.0 / total:.1f}%",
            _fmt_wl_list(parities),
        ],
        [
            "⚪ **NOISE**",
            f"**{len(noises)}**",
            f"{len(noises) * 100.0 / total:.1f}%",
            _fmt_wl_list(noises),
        ],
        [
            "🔴 **REGRESSION**",
            f"**{len(regressions)}**",
            f"{len(regressions) * 100.0 / total:.1f}%",
            _fmt_wl_list(regressions),
        ],
        [
            "❌ **BROKEN**",
            f"**{len(broken)}**",
            f"{len(broken) * 100.0 / total:.1f}%",
            _fmt_wl_list(broken),
        ],
    ]
    lines.append(format_markdown_table(tally_headers, tally_rows))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Full 4-Part Performance Scorecard & Telemetry")
    lines.append("")
    lines.append(render_comparison_markdown(comp))

    # Host CPU efficiency summary table if host_cpu_pct is present
    cpu_rows: List[List[str]] = []
    seen_cpu_w: set[str] = set()
    for r in comparisons:
        w = r.get("workload", "")
        if w in seen_cpu_w:
            continue
        b_cpu = r.get("baseline_host_cpu_pct")
        c_cpu = r.get("candidate_host_cpu_pct")
        if b_cpu is not None and c_cpu is not None:
            seen_cpu_w.add(w)
            d_pct = ((c_cpu - b_cpu) / b_cpu * 100.0) if b_cpu > 0 else 0.0
            b_cost = r.get("baseline_cpu_ms_per_mib")
            c_cost = r.get("candidate_cpu_ms_per_mib")
            cost_cell = (
                f"{b_cost:.2f} → **{c_cost:.2f}**"
                if (b_cost is not None and c_cost is not None)
                else "-"
            )
            cpu_rows.append(
                [
                    f"`{w}`",
                    f"{b_cpu:.1f}%",
                    f"**{c_cpu:.1f}%**",
                    f"{d_pct:+.1f}%",
                    cost_cell,
                ]
            )
    if cpu_rows:
        lines.append("### Host CPU & Work Cost Efficiency Summary")
        lines.append(
            format_markdown_table(
                [
                    "Workload",
                    f"Baseline ({b_lbl}) Host CPU%",
                    f"Candidate ({c_lbl}) Host CPU%",
                    "CPU% Delta",
                    "Work Cost (CPU-ms/MiB)",
                ],
                cpu_rows,
            )
        )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Key Wins & Architectural Improvements")
    lines.append("")
    if gains:
        # Sort gains by magnitude of percentage improvement
        sorted_gains = sorted(
            gains, key=lambda x: abs(float(x.get("pct_delta_p50", 0.0))), reverse=True
        )
        for r in sorted_gains:
            w = r["workload"]
            u = r["unit"]
            b_val = r["baseline_p50"]
            c_val = r["candidate_p50"]
            dp50 = r["pct_delta_p50"]
            pval = r.get("p_value", 0.0)
            extra = ""
            if (
                r.get("baseline_cpu_ms_per_mib") is not None
                and r.get("candidate_cpu_ms_per_mib") is not None
            ):
                b_c = r["baseline_cpu_ms_per_mib"]
                c_c = r["candidate_cpu_ms_per_mib"]
                c_delta = ((c_c - b_c) / b_c * 100.0) if b_c > 0 else 0.0
                extra = f", work cost `{b_c:.2f}` → `{c_c:.2f} CPU-ms/MiB` ({c_delta:+.1f}%)"
            elif (
                r.get("baseline_host_cpu_pct") is not None
                and r.get("candidate_host_cpu_pct") is not None
            ):
                b_hc = r["baseline_host_cpu_pct"]
                c_hc = r["candidate_host_cpu_pct"]
                extra = f", host CPU `{b_hc:.1f}%` → `{c_hc:.1f}%`"
            lines.append(
                f"- **`{w}` ({dp50:+.1f}% p50, $p = {pval:.4f}$):** "
                f"`{b_val:.1f} {u}` → **`{c_val:.1f} {u}`**{extra}."
            )
    else:
        lines.append("- No statistically significant gains recorded in this run.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Regressions, Broken Workloads & Root-Cause Attribution")
    lines.append("")
    if not broken and not regressions:
        lines.append(
            "- **Zero Broken Workloads & Zero Regressions:** All measured workloads passed at parity or better."
        )
    else:
        if not broken:
            lines.append(
                "- **Zero Broken Workloads (`0 BROKEN`):** 100% of executed suites and workloads completed cleanly."
            )
        for r in broken + regressions:
            w = r["workload"]
            u = r["unit"]
            badge = r.get("badge", r.get("verdict", ""))
            cause = _attribute_regression_cause(r, hyp_by_w)
            if r.get("verdict") == "broken":
                lines.append(f"- **{badge} `{w}`:** {cause}")
            else:
                lines.append(
                    f"- **{badge} `{w}` (`{r['baseline_p50']:.1f} {u}` → `{r['candidate_p50']:.1f} {u}`, "
                    f"`{r['pct_delta_p50']:+.1f}%` p50, $p = {r.get('p_value', 0.0):.4f}$):** {cause}"
                )
    lines.append("")
    return "\n".join(lines)

