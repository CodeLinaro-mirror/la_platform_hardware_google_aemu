#!/usr/bin/env python3
"""
Android Emulator & Device Resource Monitor
Cross-platform tool to monitor resource consumption on Host and multiple Guests.
Requires: pip install psutil
Optional: pip install GPUtil plotille (for discrete GPU tracking and ASCII charts)
"""

import os
import sys
import signal

# Enable ANSI escape color codes natively for Windows CMD/PowerShell BEFORE any printing
if os.name == 'nt':
    os.system("")
    try:
        # Enforce UTF-8 to prevent Plotille Braille characters from crashing Windows terminals
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import pathlib
import shutil
import time
import platform
import subprocess
import re
import argparse
import threading
import concurrent.futures
import calendar
import glob
import json
import math
import stat
import traceback
import dataclasses
from collections import deque, namedtuple
from typing import (Any, Dict, Iterable, List, Mapping, NamedTuple, Optional,
                    Set, Tuple)

missing_deps = []

try:
    import psutil
except ImportError:
    missing_deps.append("psutil")

try:
    import GPUtil
    HAS_GPUTIL = True
except ImportError:
    HAS_GPUTIL = False

if missing_deps:
    print(
        f"\033[91m[Error] Missing strictly required package: {', '.join(missing_deps)}\033[0m"
    )
    print(
        f"\033[93mPlease install it via pip:\033[0m pip install {' '.join(missing_deps)}"
    )
    sys.exit(1)


class Colors:
    HEADER, CYAN, GREEN = '\033[95m', '\033[96m', '\033[92m'
    YELLOW, RED, BLUE = '\033[93m', '\033[91m', '\033[94m'
    RESET, BOLD, DIM = '\033[0m', '\033[1m', '\033[2m'


# Pre-compile Regex globally for fast looping
RE_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
RE_FLOAT = re.compile(r'(-?\d+(?:\.\d+)?)')
RE_PCT = re.compile(r'(\d+(?:\.\d+)?)%')
RE_DF_USE_PCT = re.compile(r'\d+(?:\.\d+)?%')

# Guest Android Pre-Compiled Regexes
RE_GUEST_CPU_USAGE = re.compile(r'(\d+)\s*%?\s*cpu', re.IGNORECASE)
RE_GUEST_CPU_IDLE = re.compile(r'(\d+)\s*%?\s*idle', re.IGNORECASE)
RE_GUEST_CPU_ID = re.compile(r'(\d+(?:[\.,]\d+)?)\s*%?\s*id\b', re.IGNORECASE)
RE_GUEST_TOOLBOX_TICKS = re.compile(
    r'Idle (-?\d+) \+ IOW -?\d+ \+ IRQ -?\d+ \+ SIRQ -?\d+ = (-?\d+)')
RE_GUEST_ERRS = re.compile(r'(\d+)\s*errs', re.IGNORECASE)
RE_GUEST_PCT_EXTRACT = re.compile(r'(\d+)%')


def setup_alt_screen(is_once, headless):
    """Enters TUI alternate buffer and hides cursor to prevent terminal scrollback pollution."""
    if not is_once and not headless and sys.stdout.isatty():
        sys.stdout.write('\033[?1049h\033[?25l\033[H')
        sys.stdout.flush()


def restore_screen(is_once, headless):
    """Restores pristine terminal state on exit strictly if it was entered."""
    if not is_once and not headless and sys.stdout.isatty():
        sys.stdout.write('\033[?1049l\033[?25h')
        sys.stdout.flush()


def _validate_sudo_user(raw):
    if not isinstance(raw, str):
        return None
    u = raw.strip()
    if (not u or u == 'root' or u.startswith('-') or
            not all(c.isascii() and (c.isalnum() or c in '_.-') for c in u)):
        return None
    return u


def find_adb():
    if adb_path := shutil.which("adb"):
        return adb_path
    for env in ['ANDROID_HOME', 'ANDROID_SDK_ROOT']:
        if env_path := os.environ.get(env):
            adb_exe = os.path.join(env_path, "platform-tools",
                                   "adb" + (".exe" if os.name == 'nt' else ""))
            if os.path.exists(adb_exe):
                return adb_exe

    home = pathlib.Path.home()
    defaults = [
        home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" /
        "adb.exe",
        home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
        home / "Android" / "Sdk" / "platform-tools" / "adb"
    ]

    s_user = (_validate_sudo_user(os.environ.get('SUDO_USER'))
              if hasattr(os, 'geteuid') and os.geteuid() == 0 else None)
    if s_user and platform.system() != "Windows":
        try:
            import pwd
            user_home = pathlib.Path(pwd.getpwnam(s_user).pw_dir)
            defaults.extend([
                user_home / "Library" / "Android" / "sdk" / "platform-tools" /
                "adb", user_home / "Android" / "Sdk" / "platform-tools" / "adb"
            ])
        except Exception:
            pass

    for p in defaults:
        if p.exists():
            return str(p)
    return "adb"


ADB_PATH = find_adb()
sudo_user = _validate_sudo_user(os.environ.get('SUDO_USER'))


def run_cmd(cmd, timeout=5, stderr_devnull=False):
    try:
        cmd = list(cmd)
        kwargs = {'stdin': subprocess.DEVNULL}
        if platform.system() == "Windows":
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW',
                                              0x08000000)

        if cmd[0] == 'adb':
            cmd[0] = ADB_PATH
            valid_sudo = _validate_sudo_user(sudo_user)
            if valid_sudo and hasattr(os, 'geteuid') and os.geteuid() == 0:
                cmd = ['sudo', '-n', '-u', valid_sudo] + cmd

        stderr_target = subprocess.DEVNULL if stderr_devnull else subprocess.STDOUT
        result = subprocess.run(cmd,
                                stdout=subprocess.PIPE,
                                stderr=stderr_target,
                                text=True,
                                errors='replace',
                                timeout=timeout,
                                **kwargs)
        return result.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        # Hardened safety decode fixes a known Py3.8 bug where timeouts bypass text=True bounds
        if isinstance(out, bytes):
            out = out.decode('utf-8', errors='replace')
        return out or ""
    except Exception:
        return ""


def get_static_hardware_info():
    os_str = f"{platform.system()} {platform.release()}"
    try:
        if platform.system() == "Linux" and os.path.exists('/etc/os-release'):
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        os_str = line.split('=')[-1].strip('" \n')
                        break
    except Exception:
        pass

    cpu_str = platform.processor() or "Unknown CPU"
    if platform.system() == "Windows":
        try:
            import winreg
            # BugFix: Context manager ensures native registry handle isn't permanently leaked
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu_str = winreg.QueryValueEx(key,
                                              "ProcessorNameString")[0].strip()
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            # Overrides generic 'i386' readouts on M1/M2/M3 Apple Silicon Macs running x86_64 architecture translation Python bindings
            out = run_cmd(['sysctl', '-n', 'machdep.cpu.brand_string'],
                          timeout=2,
                          stderr_devnull=True)
            if out and out.strip():
                cpu_str = out.strip()
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if "model name" in line:
                        cpu_str = line.split(':')[-1].strip()
                        break
        except Exception:
            pass

    ram_str = f"{psutil.virtual_memory().total / (1024**3):.1f} GB"

    gpu_str = "N/A"
    try:
        if HAS_GPUTIL:
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu_str = f"{gpus[0].name} ({gpus[0].memoryTotal / 1024:.1f}GB)"
        else:
            out = run_cmd([
                'nvidia-smi', '--query-gpu=gpu_name,memory.total',
                '--format=csv,noheader,nounits'
            ],
                          timeout=2,
                          stderr_devnull=True)
            if out and out.strip():
                parts = out.strip().splitlines()[0].split(',')
                if len(parts) >= 2:
                    gpu_str = f"{parts[0].strip()} ({float(parts[1].strip())/1024:.1f}GB)"
    except Exception:
        pass

    if gpu_str == "N/A" and platform.system() == "Darwin":
        try:
            out = run_cmd(
                ['ioreg', '-r', '-d', '1', '-w', '0', '-c', 'IOAccelerator'],
                timeout=2,
                stderr_devnull=True)
            models = _parse_mac_ioreg_gpu_models(out)
            if models:
                gpu_str = ", ".join(models)
        except Exception:
            pass

    return f"{os_str} | {cpu_str} | {ram_str} RAM | GPU: {gpu_str}"


def get_devices():
    out = run_cmd(['adb', 'devices'], timeout=5)
    devices = {}
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith('List') and not line.startswith('*'):
            parts = line.split()
            if len(parts
                  ) >= 3 and parts[1] == 'no' and parts[2] == 'permissions':
                parts[1] = 'no permissions'
            if len(parts) >= 2 and parts[1] in (
                    'device', 'offline', 'unauthorized', 'recovery',
                    'bootloader', 'sideload', 'authorizing', 'connecting',
                    'no permissions'):
                devices[parts[0]] = parts[1]
    return devices


_WIN_GPU_CACHE = {"time": 0, "val": "N/A", "fetching": False, "failed": False}
_MAC_GPU_CACHE = {
    "time": 0,
    "val": "N/A",
    "mem": "N/A",
    "fetching": False,
    "failed": False,
}
_LINUX_INTEL_GPU_FAILED = False
_NVIDIA_SMI_FAILED = False
_NVIDIA_PMON_FAILED = False

_RE_IOREG_UTIL = re.compile(
    r'"(?:Device|Renderer|Tiler) Utilization %"\s*=\s*(\d+(?:\.\d+)?)')
_RE_IOREG_MEM_IN_USE = re.compile(r'"In use system memory"\s*=\s*(\d+)')
_RE_IOREG_MEM_ALLOC = re.compile(r'"Alloc system memory"\s*=\s*(\d+)')
_RE_IOREG_MODEL = re.compile(r'"model"\s*=\s*(?:<"([^"]+)">|"([^"]+)")')
_RE_IOREG_CORES = re.compile(r'"gpu-core-count"\s*=\s*(\d+)')


def _parse_mac_ioreg_gpu(
        out: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Parses IOAccelerator PerformanceStatistics into (load_pct, mem_mb).

    Args:
        out: Raw stdout from `ioreg -r -d 1 -w 0 -c IOAccelerator`, or None.

    Returns:
        Tuple of `(max_util_pct, total_mem_mb)` where either element is None
        when absent or unparseable in `out`.
    """
    if not out:
        return None, None
    max_util = None
    total_mem_bytes = 0
    found_mem = False
    for segment in out.split('"PerformanceStatistics"')[1:]:
        open_idx = segment.find('{')
        if open_idx < 0:
            continue
        block = segment[open_idx + 1:].split('}', 1)[0]
        for m in _RE_IOREG_UTIL.finditer(block):
            try:
                val = max(0.0, min(100.0, float(m.group(1))))
                max_util = val if max_util is None else max(max_util, val)
            except ValueError:
                pass
        m_mem = (_RE_IOREG_MEM_IN_USE.search(block) or
                 _RE_IOREG_MEM_ALLOC.search(block))
        if m_mem:
            try:
                total_mem_bytes += int(m_mem.group(1))
                found_mem = True
            except ValueError:
                pass
    mem_mb = (total_mem_bytes / (1024.0 * 1024.0)) if found_mem else None
    return max_util, mem_mb


def _format_mac_gpu_label(model: Optional[str],
                          cores: Optional[int]) -> Optional[str]:
    """Formats a single IOAccelerator model + optional core count label.

    Args:
        model: Parsed GPU model name, or None.
        cores: Parsed GPU core count, or None.

    Returns:
        Formatted label like `'Apple M4 Pro (20-core)'`, or None if `model` is
        empty or None.
    """
    if not model:
        return None
    if cores and cores > 0:
        return f"{model} ({cores}-core)"
    return model


def _parse_mac_ioreg_gpu_models(out: Optional[str]) -> List[str]:
    """Parses IOAccelerator registry entries into deduplicated GPU labels.

    Args:
        out: Raw stdout from `ioreg -r -d 1 -w 0 -c IOAccelerator`, or None.

    Returns:
        Deduplicated list of formatted GPU model strings (empty if `out` is
        None or contains no matching entries).
    """
    if not out:
        return []
    models = []
    cur_model = None
    cur_cores = None

    for line in out.splitlines():
        if '+-o ' in line:
            label = _format_mac_gpu_label(cur_model, cur_cores)
            if label and label not in models:
                models.append(label)
            cur_model = None
            cur_cores = None
        if cur_model is None:
            if m_mod := _RE_IOREG_MODEL.search(line):
                val = (m_mod.group(1) or m_mod.group(2) or '').strip()
                if val:
                    cur_model = val
        if cur_cores is None:
            if m_cores := _RE_IOREG_CORES.search(line):
                try:
                    c_val = int(m_cores.group(1))
                    if c_val > 0:
                        cur_cores = c_val
                except ValueError:
                    pass
    label = _format_mac_gpu_label(cur_model, cur_cores)
    if label and label not in models:
        models.append(label)
    return models


def update_mac_gpu() -> None:
    """Refreshes cached macOS GPU load and memory from ioreg or powermetrics."""
    try:
        out = run_cmd(
            ['ioreg', '-r', '-d', '1', '-w', '0', '-c', 'IOAccelerator'],
            timeout=2,
            stderr_devnull=True)
        load, mem = _parse_mac_ioreg_gpu(out)
        if load is not None or mem is not None:
            _MAC_GPU_CACHE["val"] = (f"{load:.1f}%"
                                     if load is not None else "N/A")
            _MAC_GPU_CACHE["mem"] = (f"{mem:.1f}MB"
                                     if mem is not None else "N/A")
            return
        out = run_cmd(
            ['powermetrics', '-i', '500', '-n', '1', '--samplers', 'gpu_power'],
            timeout=2,
            stderr_devnull=True)
        if out and (m := re.search(r'GPU (?:HW )?active residency:\s+([\d.]+)%',
                                   out)):
            _MAC_GPU_CACHE["val"] = f"{m.group(1)}%"
            _MAC_GPU_CACHE["mem"] = "N/A"
        else:
            _MAC_GPU_CACHE["val"] = "N/A"
            _MAC_GPU_CACHE["mem"] = "N/A"
            _MAC_GPU_CACHE["failed"] = True
    except Exception:
        _MAC_GPU_CACHE["val"] = "N/A"
        _MAC_GPU_CACHE["mem"] = "N/A"
        _MAC_GPU_CACHE["failed"] = True
    finally:
        _MAC_GPU_CACHE["fetching"] = False


def update_win_gpu():
    try:
        # BugFix: Added -NoProfile and -NonInteractive to drastically reduce background Host CPU parsing spikes
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"
        ]
        out = run_cmd(cmd, timeout=3, stderr_devnull=True)
        if out and out.strip():
            try:
                _WIN_GPU_CACHE[
                    "val"] = f"{float(out.strip().replace(',', '.')):.1f}%"
                return
            except ValueError:
                pass
        _WIN_GPU_CACHE["failed"] = True
    except Exception:
        _WIN_GPU_CACHE["failed"] = True
    finally:
        _WIN_GPU_CACHE["fetching"] = False


def get_gpu_stats():
    global _LINUX_INTEL_GPU_FAILED, _NVIDIA_SMI_FAILED, _NVIDIA_PMON_FAILED
    os_name = platform.system()

    if HAS_GPUTIL:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                return f"{gpus[0].load * 100:.1f}%", f"{gpus[0].memoryUsed:.1f}MB"
        except Exception:
            pass

    if not _NVIDIA_SMI_FAILED:
        try:
            out = run_cmd([
                'nvidia-smi', '--query-gpu=utilization.gpu,memory.used',
                '--format=csv,noheader,nounits'
            ],
                          timeout=2,
                          stderr_devnull=True)
            if out:
                out_stripped = out.strip()
                if out_stripped:
                    lines = out_stripped.splitlines()
                    if lines:
                        parts = lines[0].split(',')
                        if len(parts) >= 2:
                            return (f"{float(parts[0].strip()):.1f}%",
                                    f"{float(parts[1].strip()):.1f}MB")
        except Exception:
            pass
        _NVIDIA_SMI_FAILED = True
        _NVIDIA_PMON_FAILED = True

    if os_name == "Linux":
        drm_paths = ['/sys/class/drm/card0/device/gpu_busy_percent']
        try:
            for p in sorted(
                    glob.glob('/sys/class/drm/card*/device/gpu_busy_percent')):
                if p not in drm_paths:
                    drm_paths.append(p)
        except Exception:
            pass
        for busy_path in drm_paths:
            try:
                if os.path.exists(busy_path):
                    with open(busy_path, 'r') as f:
                        val = f.read().strip()
                    float(val)
                    return f"{val}%", "N/A"
            except Exception:
                continue

        if not _LINUX_INTEL_GPU_FAILED:
            out = run_cmd(['intel_gpu_top', '-s', '1', '-n', '1'],
                          timeout=2,
                          stderr_devnull=True)
            if out and (m := re.search(r'Render/3D(?:/\d+)?:\s+([\d.]+)%',
                                       RE_ANSI.sub('', out), re.IGNORECASE)):
                return f"{m.group(1)}%", "N/A"
            _LINUX_INTEL_GPU_FAILED = True

        return "N/A (Requires sudo or nvidia-smi)", "N/A"

    elif os_name == "Windows":
        if (not _WIN_GPU_CACHE.get("failed") and
                time.monotonic() - _WIN_GPU_CACHE["time"] > 5 and
                not _WIN_GPU_CACHE["fetching"]):
            _WIN_GPU_CACHE["fetching"] = True
            _WIN_GPU_CACHE["time"] = time.monotonic()
            threading.Thread(target=update_win_gpu, daemon=True).start()
        return _WIN_GPU_CACHE["val"], "N/A"

    elif os_name == "Darwin":
        if (not _MAC_GPU_CACHE.get("failed") and
                time.monotonic() - _MAC_GPU_CACHE["time"] > 10 and
                not _MAC_GPU_CACHE["fetching"]):
            was_initial = (_MAC_GPU_CACHE["time"] == 0)
            _MAC_GPU_CACHE["fetching"] = True
            _MAC_GPU_CACHE["time"] = time.monotonic()
            t = threading.Thread(target=update_mac_gpu, daemon=True)
            t.start()
            if was_initial:
                t.join(timeout=0.1)

        return _MAC_GPU_CACHE["val"], _MAC_GPU_CACHE.get("mem", "N/A")

    return "N/A", "N/A"


_EMU_GPU_CACHE = {"time": 0, "val": {}, "fetching": False}


def update_emu_gpu():
    global _NVIDIA_PMON_FAILED
    if _NVIDIA_SMI_FAILED or _NVIDIA_PMON_FAILED:
        _EMU_GPU_CACHE["fetching"] = False
        return
    try:
        raw_stats = {}
        saw_pmon_output = False
        out = run_cmd(['nvidia-smi', 'pmon', '-c', '1'],
                      timeout=2,
                      stderr_devnull=True)
        if out:
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith('#'):
                    saw_pmon_output = True
                    continue
                parts = stripped.split()
                if len(parts) >= 7 and parts[0].isdigit():
                    saw_pmon_output = True
                    if parts[1].isdigit():
                        try:
                            pid, sm_val = int(parts[1]), float(
                                parts[3]) if parts[3] not in ('-', '?') else 0.0
                            raw_stats[pid] = raw_stats.get(pid, 0.0) + sm_val
                        except ValueError:
                            pass
        if saw_pmon_output:
            _EMU_GPU_CACHE["val"] = raw_stats
        else:
            _NVIDIA_PMON_FAILED = True
    except Exception:
        _NVIDIA_PMON_FAILED = True
    finally:
        _EMU_GPU_CACHE["fetching"] = False


def get_emu_gpu_stats(qemu_pids):
    if not qemu_pids:
        return {}
    if (not _NVIDIA_SMI_FAILED and not _NVIDIA_PMON_FAILED and
            time.monotonic() - _EMU_GPU_CACHE["time"] > 2.0 and
            not _EMU_GPU_CACHE["fetching"]):
        _EMU_GPU_CACHE["fetching"] = True
        _EMU_GPU_CACHE["time"] = time.monotonic()
        threading.Thread(target=update_emu_gpu, daemon=True).start()

    raw_stats = {}
    cached = _EMU_GPU_CACHE["val"]
    _, all_group_pids, _, _ = _snapshot_process_state()
    for serial, e_pid in qemu_pids.items():
        group_pids = set(all_group_pids.get(serial, ()))
        if e_pid is not None:
            group_pids.add(e_pid)
        matched = [cached[p] for p in group_pids if p in cached]
        if matched:
            raw_stats[serial] = sum(matched)
    return {k: f"{v:.1f}%" for k, v in raw_stats.items()}


# Options that make the `emulator` launcher perform a one-shot action and exit
# instead of booting an AVD. Launcher options take a single dash, with `--help`
# as the only double-dash spelling; `-help-<topic>` is matched by prefix.
_EMULATOR_UTILITY_FLAGS = frozenset({
    '-kill',
    '-list-avds',
    '-accel-check',
    '-version',
    '-snapshot-list',
    '-check-snapshot-loadable',
    '-webcam-list',
    '-help',
    '--help',
})


def _read_proc_cmdline(proc: Any) -> Optional[List[str]]:
    """Returns the non-empty argv of `proc`, or None if it cannot be read.

    `psutil.ZombieProcess` and `psutil.NoSuchProcess` propagate so that the
    caller drops the process.
    """
    try:
        cmdline = proc.cmdline()
    except (psutil.ZombieProcess, psutil.NoSuchProcess):
        raise
    except Exception:
        return None
    if cmdline and isinstance(cmdline, (list, tuple)):
        return list(cmdline)
    return None


def _is_emulator_utility_cmdline(cmdline: Optional[List[str]]) -> bool:
    """Returns True if `cmdline` is a one-shot `emulator` invocation.

    Such invocations, for example `emulator -kill <pid> -sleep <secs>` or
    `emulator -list-avds`, exit without booting an AVD.
    """
    return any(
        isinstance(arg, str) and
        (arg in _EMULATOR_UTILITY_FLAGS or arg.startswith('-help-'))
        for arg in (cmdline or [])[1:])


def _parse_cmdline_details(cmdline):
    """Extracts console port, AVD id, display name, and netsim args from cmdline."""
    explicit_port = None
    fallback_port = None
    avd_id = None
    display_name = None
    grpc_endpoint = None
    no_netsim = False
    if not cmdline or not isinstance(cmdline, (list, tuple)):
        return None, None, None, None, None, False

    consumed_next = False
    for i, raw_arg in enumerate(cmdline):
        if consumed_next:
            consumed_next = False
            continue
        if not isinstance(raw_arg, str):
            continue
        arg = raw_arg
        if arg in ('-no-netsim', '--no-netsim'):
            no_netsim = True
        elif arg in ('-port', '-ports', '-fishtank') and i + 1 < len(cmdline):
            port_val = _parse_decimal(
                str(cmdline[i + 1]).split(',')[0], _U16_MAX)
            if port_val is not None:
                explicit_port = port_val
            consumed_next = True
        elif (arg.startswith('-port=') or arg.startswith('-ports=') or
              arg.startswith('-fishtank=')):
            port_val = _parse_decimal(
                arg.split('=', 1)[1].split(',')[0], _U16_MAX)
            if port_val is not None:
                explicit_port = port_val
        elif arg in ('-avd', '-name') and i + 1 < len(cmdline):
            next_arg = str(cmdline[i + 1]).strip('\'"')
            if next_arg.startswith('guest='):
                next_arg = next_arg[6:]
            cand = next_arg.split(',')[0]
            if cand:
                avd_id = cand
            consumed_next = True
        elif arg.startswith('-name='):
            val = arg[6:].strip('\'"')
            if val.startswith('guest='):
                val = val[6:]
            cand = val.split(',')[0]
            if cand:
                avd_id = cand
        elif arg.startswith('@') and len(arg) > 1:
            cand = arg[1:].strip('\'"')
            if cand:
                avd_id = cand
        elif arg.startswith('-avd') and len(
                arg) > 4 and not arg.startswith('-avd_name='):
            val = arg[5:] if arg[4] == '=' else arg[4:]
            val = val.strip('\'"')
            if val.startswith('guest='):
                val = val[6:]
            if val:
                avd_id = val
        elif (avd_id is None and (i == 0 or arg.startswith('-')) and
              '-avd' in arg and not arg.startswith('-avd_name=') and
              not any(sep in arg for sep in ('/', '\\', '='))):
            idx = arg.find('-avd')
            rest = arg[idx + 4:].strip('\'"')
            candidate = rest.split('-')[0] if '-' in rest else rest
            if candidate:
                avd_id = candidate

        target_props = [arg]
        if arg == '-device' and i + 1 < len(cmdline) and isinstance(
                cmdline[i + 1], str):
            target_props.append(cmdline[i + 1])
            consumed_next = True

        for prop_str in target_props:
            if (m_sn := re.search(r'serial_number=(\d+)', prop_str)):
                port_val = _parse_decimal(m_sn.group(1), _U16_MAX)
                if port_val is not None:
                    explicit_port = port_val
            if (m_hp := re.search(r'host_port=(\d+)', prop_str)):
                hp = _parse_decimal(m_hp.group(1), _U16_MAX)
                if hp:
                    fallback_port = hp - 1
            if not avd_id and (m_aid := re.search(r'avd_id=([^,]+)', prop_str)):
                avd_id = m_aid.group(1).strip('\'"')
            if (m_an := re.search(r'avd_name=([^,]+)', prop_str)):
                display_name = m_an.group(1).strip('\'"')
            if (m_ep := re.search(r'grpc_endpoint=([^,\s]+)', prop_str)):
                grpc_endpoint = m_ep.group(1).strip('\'"')

    port = explicit_port if explicit_port is not None else fallback_port
    avd_name = avd_id if avd_id is not None else display_name
    return port, avd_name, avd_id, display_name, grpc_endpoint, no_netsim


def parse_cmdline(cmdline):
    """Parses command line arguments to extract emulator port and AVD name."""
    port, avd_name, _, _, _, _ = _parse_cmdline_details(cmdline)
    return port, avd_name


_PROCESS_STATE_LOCK = threading.RLock()
_PID_PORT_CACHE = {}
_PROCESS_CACHE = {}
_LAST_EMU_GROUP_PIDS = {}
_LAST_EMU_GROUP_INFO = {}
_LAST_PRIMARY_PROC_ID = {}
_LAST_NETSIMD_PROCS = []


def _snapshot_process_state():
    """Returns a synchronized snapshot of the latest process scan under _PROCESS_STATE_LOCK."""
    # Lock is released when exiting the with-block after copying current state dicts/lists.
    with _PROCESS_STATE_LOCK:
        return (
            [dict(d) for d in _LAST_NETSIMD_PROCS],
            {
                k: set(v) for k, v in _LAST_EMU_GROUP_PIDS.items()
            },
            {
                k:
                    dict(
                        v,
                        pids=set(v.get('pids', ())),
                        member_start_times=dict(
                            v.get('member_start_times') or {}),
                    ) for k, v in _LAST_EMU_GROUP_INFO.items()
            },
            dict(_LAST_PRIMARY_PROC_ID),
        )


def _build_live_group_procs(new_pid, new_proc_id, raw_group_pids, g_info=None):
    """Maps each live group PID to its create_time (None when unknown in mocks)."""
    member_times = dict((g_info or {}).get('member_start_times') or {})
    procs = {}
    for p in (raw_group_pids or ()):
        if p:
            procs[p] = member_times.get(p)
    if new_pid and (not raw_group_pids or new_pid in raw_group_pids):
        p_ctime = (new_proc_id[1] if
                   (new_proc_id and new_proc_id[0] == new_pid and
                    new_proc_id[1] is not None) else member_times.get(new_pid))
        procs[new_pid] = p_ctime
    return procs


def _merge_group_procs(dst, src):
    for p, ct in (src or {}).items():
        if p not in dst or ct is not None:
            dst[p] = ct


def _group_procs_match(known_group_procs, live_group_procs, ctime_tol=0.0):
    if not known_group_procs or not live_group_procs:
        return False
    for pid, live_ct in live_group_procs.items():
        if pid in known_group_procs:
            prev_ct = known_group_procs[pid]
            if (prev_ct is None or live_ct is None or prev_ct == 0 or
                    live_ct == 0 or abs(live_ct - prev_ct) <= ctime_tol):
                return True
    return False


def _safe_str(val):
    return val if isinstance(val, str) else ""


def _os_family(target_os=None):
    """'linux', 'darwin' or 'win32' for a sys.platform-style name.

    Other Unixes use the Linux paths, as in Rust HostOs::current().
    """
    name = (target_os or sys.platform).lower()
    if name.startswith('darwin') or name == 'macos':
        return 'darwin'
    if name.startswith('win') or name == 'nt':
        return 'win32'
    return 'linux'


# Root variable and subdir that netsim's get_discovery_directory() and the
# emulator's GetDiscoveryDirectory() use on each OS.
_DISCOVERY_ROOTS = {
    'linux': ('XDG_RUNTIME_DIR', ()),
    'darwin': ('HOME', ('Library', 'Caches', 'TemporaryItems')),
    'win32': ('LOCALAPPDATA', ('Temp',)),
}


def _extend_unique(dirs, more):
    """Appends each non-empty path in `more` not already in `dirs`.

    Paths are compared after os.path.normpath(); the first spelling is kept.
    Returns `dirs`.
    """
    seen = {os.path.normpath(d) for d in dirs}
    for p in more:
        if p and os.path.normpath(p) not in seen:
            seen.add(os.path.normpath(p))
            dirs.append(p)
    return dirs


def get_netsim_discovery_dirs(environ_dict, target_os=None):
    """Dirs that may hold netsim[_<N>].ini and avd/running/pid_<pid>.ini.

    netsim's get_discovery_directory() (common/src/util/os_utils.rs) uses
    TMPDIR, else the OS root variable plus subdir, else /tmp plus subdir. The
    monitor cannot tell which branch a daemon took, so every candidate is
    returned for a process with environment `environ_dict`. Set-but-empty
    variables count as unset, whereas netsim would use an empty TMPDIR as-is.
    """
    root_env, subdir = _DISCOVERY_ROOTS[_os_family(target_os)]
    root = environ_dict.get(root_env)
    return _extend_unique([], [
        environ_dict.get('ANDROID_EMULATOR_DISCOVERY_DIR'),
        environ_dict.get('TMPDIR'),
        os.path.join(root, *subdir) if root else None,
        os.path.join('/tmp', *subdir),
        environ_dict.get('ANDROID_TMP'),
    ])


def _current_uid() -> Optional[int]:
    """Returns the monitor's real UID (`os.getuid()`), or None on Windows.

    Ownership checks use the real UID; `os.geteuid()` only decides whether
    `sudo -n -u` is needed.
    """
    getuid = getattr(os, 'getuid', None)
    return getuid() if getuid else None


def _proc_uid(proc: Any) -> Optional[int]:
    """Real UID of a psutil process, or None off POSIX or once it is gone."""
    uids = getattr(proc, 'uids', None)
    if uids is None:
        return None
    try:
        real_uid = uids().real
    except Exception:
        return None
    return (real_uid if isinstance(real_uid, int) and
            not isinstance(real_uid, bool) else None)


def _resolve_expected_uid(target_uid: Optional[int] = None,
                          *,
                          same_user: bool = True,
                          monitor_uid: Optional[int] = None) -> Optional[int]:
    """Resolves the expected owner UID for a process's shared-temp artifacts.

    Prefers the observed target process's real UID (`target_uid`) when known so
    `sudo` (`monitor_uid == 0`) and readable foreign-daemon tracking validate
    against the actual daemon/worker owner; falls back to `monitor_uid` when
    `same_user` is True and `monitor_uid` is a non-root POSIX UID.
    """
    if isinstance(target_uid, int) and not isinstance(target_uid, bool):
        return target_uid
    eff_monitor = _current_uid() if monitor_uid is None else monitor_uid
    if same_user and eff_monitor not in (None, 0):
        return eff_monitor
    return None


def _is_same_user(proc_uid):
    """True when a process runs as the monitor's user.

    Windows has no uids; there the temp dir a daemon reports is already per
    logon session, so every daemon counts as the monitor's own.
    """
    my_uid = _current_uid()
    if my_uid is None:
        return True
    return proc_uid is not None and proc_uid == my_uid


def _is_live_non_zombie_pid(pid):
    """True when `pid` exists and is neither a zombie nor a dead process."""
    try:
        if not psutil.pid_exists(pid):
            return False
    except (OverflowError, ValueError):
        # A pid read from an INI can exceed what the OS takes as a pid.
        return False
    try:
        status = psutil.Process(pid).status()
    except psutil.ZombieProcess:
        return False
    except psutil.NoSuchProcess:
        return psutil.pid_exists(pid)
    except psutil.AccessDenied:
        return True
    except Exception:
        return True
    return status not in (getattr(psutil, 'STATUS_ZOMBIE', 'zombie'),
                          getattr(psutil, 'STATUS_DEAD', 'dead'))


def _is_real_dir(path):
    """True when `path` is an existing directory and not a symbolic link."""
    if not path:
        return False
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        # ValueError: a NUL byte, as a path taken from a log line may hold.
        return False


def _has_parent_dir_segment(path: str) -> bool:
    """True when `path` contains a raw `..` parent-traversal component."""
    _, tail = os.path.splitdrive(path)
    return '..' in tail.replace(os.altsep or os.sep, os.sep).split(os.sep)


def _iter_path_prefixes(path):
    """Yields the cumulative prefixes of `path`, shallowest first."""
    norm = os.path.normpath(path)
    drive, tail = os.path.splitdrive(norm)
    is_abs = tail.startswith(os.sep) or (os.altsep and
                                         tail.startswith(os.altsep))
    cur = (drive + os.sep) if is_abs else drive
    if is_abs:
        yield cur
    parts = [
        p for p in tail.replace(os.altsep or os.sep, os.sep).split(os.sep)
        if p and p != '.'
    ]
    for part in parts:
        cur = os.path.join(cur, part) if cur else part
        yield cur


def _is_trusted_dir_stat(dir_st: os.stat_result,
                         expected_uid: Optional[int],
                         root_uid: int = 0) -> bool:
    """True when `dir_st` is a directory with safe ownership and write bits."""
    if not stat.S_ISDIR(dir_st.st_mode):
        return False
    my_uid = _current_uid()
    if my_uid is None:
        return True
    is_sticky = bool(dir_st.st_mode & stat.S_ISVTX)
    root_uids = (0, root_uid)
    if expected_uid is None:
        if my_uid == 0:
            return is_sticky or (dir_st.st_mode & stat.S_IWOTH) == 0
        expected_uid = my_uid
    if dir_st.st_uid == expected_uid:
        if expected_uid in root_uids:
            return is_sticky or (dir_st.st_mode &
                                 (stat.S_IWGRP | stat.S_IWOTH)) == 0
        return is_sticky or (dir_st.st_mode & stat.S_IWOTH) == 0
    if dir_st.st_uid in root_uids:
        return is_sticky or (dir_st.st_mode &
                             (stat.S_IWGRP | stat.S_IWOTH)) == 0
    return False


# Symlinks one path resolution may follow (Linux MAXSYMLINKS).
_MAX_SYMLINK_HOPS = 40


def _resolve_trusted_dir(target_dir, node_ok, link_owner_ok):
    """Canonical path of `target_dir` if its whole resolution is trusted.

    Resolves one component at a time, as the kernel does. Every directory
    entered must pass node_ok(lstat result) and every symlink followed must
    pass link_owner_ok(owner uid); otherwise the result is None. Each symlink
    is read inside a directory that was already accepted and its target is
    resolved the same way, so no directory a third party can write to takes
    part in the resolution. `..` is refused in `target_dir` itself; inside a
    symlink target it steps back to a directory accepted earlier.

    Mirrors resolve_trusted_dir_with() in rust/src/netsim/security.rs.
    """
    if not target_dir or _has_parent_dir_segment(target_dir):
        return None
    try:
        absolute = (target_dir if os.path.isabs(target_dir) else os.path.join(
            os.getcwd(), target_dir))
        pending = [p for p in absolute.split(os.sep) if p and p != '.']
        pending.reverse()
        cur = os.sep
        if not node_ok(os.lstat(cur)):
            return None
        hops = 0
        while pending:
            name = pending.pop()
            if name == '..':
                cur = os.path.dirname(cur)
                continue
            entry = os.path.join(cur, name)
            lst = os.lstat(entry)
            if stat.S_ISLNK(lst.st_mode):
                hops += 1
                if hops > _MAX_SYMLINK_HOPS or not link_owner_ok(lst.st_uid):
                    return None
                link = os.readlink(entry)
                if link.startswith(os.sep):
                    cur = os.sep
                pending.extend(
                    reversed([p for p in link.split(os.sep) if p and p != '.']))
            elif node_ok(lst):
                cur = entry
            else:
                return None
    except (OSError, ValueError):
        # ValueError: a NUL byte in the path.
        return None
    return cur


def _trusted_canonical_dir(target_dir: Optional[str],
                           expected_uid: Optional[int] = None,
                           root_uid: int = 0) -> Optional[str]:
    """Canonical `target_dir` when resolving it stays inside trusted dirs.

    Every directory entered must be owned by `expected_uid` or root (`0` /
    `root_uid`) with safe write bits, and every symlink followed must be owned
    by them (keeping macOS `/tmp -> private/tmp` and `/var -> private/var`
    while rejecting third-party intermediate directories). Without an
    `expected_uid` the monitor's own uid is expected; root accepts any owner.
    """
    if not target_dir or _has_parent_dir_segment(target_dir):
        return None
    my_uid = _current_uid()
    if my_uid is None:
        if not os.path.isdir(target_dir):
            return None
        return os.path.realpath(target_dir)
    eff_uid = (expected_uid if expected_uid is not None else
               (my_uid if my_uid != 0 else None))
    root_uids = (0, root_uid)

    def node_ok(st):
        return _is_trusted_dir_stat(st, eff_uid, root_uid)

    def link_owner_ok(uid):
        return uid in root_uids or (eff_uid is not None and uid == eff_uid)

    return _resolve_trusted_dir(target_dir, node_ok, link_owner_ok)


def _has_trusted_ancestors(target_dir: Optional[str],
                           expected_uid: Optional[int] = None,
                           root_uid: int = 0) -> bool:
    """True when _trusted_canonical_dir() accepts `target_dir`."""
    return _trusted_canonical_dir(target_dir, expected_uid,
                                  root_uid) is not None


def _is_trusted_owned_real_dir(path: Optional[str],
                               expected_uid: Optional[int] = None,
                               root_uid: int = 0) -> bool:
    """True when `path` is a real directory owned by `expected_uid`.

    It must not be world-writable and must pass _has_trusted_ancestors().
    Without `expected_uid` the monitor's own uid is expected; root accepts
    any owner.
    """
    if not path:
        return False
    try:
        lst = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(lst.st_mode):
        return False
    my_uid = _current_uid()
    if my_uid is None:
        return _has_trusted_ancestors(path,
                                      expected_uid=None,
                                      root_uid=root_uid)
    # As in Rust, the directory itself may not be world-writable, sticky or not.
    if lst.st_mode & stat.S_IWOTH:
        return False
    eff_uid = (expected_uid if expected_uid is not None else
               (my_uid if my_uid != 0 else None))
    if eff_uid is not None and lst.st_uid != eff_uid:
        return False
    if not _is_trusted_dir_stat(lst, eff_uid, root_uid):
        return False
    return _has_trusted_ancestors(path, expected_uid=eff_uid, root_uid=root_uid)


def _is_trusted_ini_path(ini_path: Optional[str],
                         root_uid: int = 0,
                         expected_uid: Optional[int] = None) -> bool:
    """True when the netsim CLI may use the directory of `ini_path` as TMPDIR.

    The directory must resolve through _trusted_canonical_dir(), and
    `ini_path`, if it exists, must be a regular file owned by `expected_uid`
    (None: the monitor's user). A missing file is accepted only in a
    directory that user owns and others cannot write to. An empty path or a
    bare file name has no directory to check and returns True.
    """
    if not ini_path:
        return True
    ini_dir = os.path.dirname(ini_path)
    if not ini_dir:
        return True
    my_uid = _current_uid()
    eff_uid = my_uid if expected_uid is None else expected_uid
    canon_dir = _trusted_canonical_dir(ini_dir,
                                       expected_uid=eff_uid,
                                       root_uid=root_uid)
    if canon_dir is None:
        return False
    try:
        dir_st = os.lstat(canon_dir)
    except OSError:
        return False
    if not _is_trusted_dir_stat(dir_st, eff_uid, root_uid):
        return False
    private_write_mask = ((stat.S_IWGRP | stat.S_IWOTH)
                          if eff_uid in (0, root_uid) else stat.S_IWOTH)
    owned_private_dir = (my_uid is None or
                         (eff_uid is not None and dir_st.st_uid == eff_uid and
                          (dir_st.st_mode & private_write_mask) == 0))

    def _check_ini(candidate: str) -> bool:
        try:
            ini_st = os.lstat(candidate)
        except FileNotFoundError:
            return owned_private_dir
        except OSError:
            return False
        if not stat.S_ISREG(ini_st.st_mode):
            return False
        return my_uid is None or (eff_uid is not None and
                                  ini_st.st_uid == eff_uid)

    if not _check_ini(ini_path):
        return False
    if canon_dir != ini_dir:
        base_name = os.path.basename(ini_path)
        if not base_name or not _check_ini(os.path.join(canon_dir, base_name)):
            return False
    return True


def _has_trusted_ancestors_unowned(target_dir: str, root_uid: int = 0) -> bool:
    """_has_trusted_ancestors() for a foreign daemon whose uid is unknown.

    Directories may belong to anyone but must not be world-writable without
    the sticky bit, and only root's symlinks are followed, as Rust
    has_trusted_ancestors() does without an expected uid.
    """
    if not target_dir or _has_parent_dir_segment(target_dir):
        return False
    if _current_uid() is None:
        return os.path.isdir(target_dir)
    root_uids = (0, root_uid)

    def safe_dir(st: os.stat_result) -> bool:
        return stat.S_ISDIR(st.st_mode) and (bool(st.st_mode & stat.S_ISVTX) or
                                             (st.st_mode & stat.S_IWOTH) == 0)

    return _resolve_trusted_dir(target_dir, safe_dir,
                                lambda uid: uid in root_uids) is not None


def _can_adopt_logged_ini_path(path: Optional[str],
                               same_user: bool,
                               expected_uid: Optional[int] = None,
                               root_uid: int = 0) -> bool:
    """True when the INI path a daemon logged can be adopted for it.

    It must be a regular file of at most _MAX_INI_BYTES whose owner fits the
    daemon (`same_user` / `expected_uid`) and whose directory is trusted.
    """
    if not path or _has_parent_dir_segment(path):
        return False
    try:
        lst = os.lstat(path)
    except (OSError, ValueError):
        # ValueError: a NUL byte in the logged path.
        return False
    if not stat.S_ISREG(lst.st_mode) or lst.st_size > _MAX_INI_BYTES:
        return False
    my_uid = _current_uid()
    if my_uid is not None:
        if expected_uid is not None and lst.st_uid != expected_uid:
            return False
        if my_uid != 0 and (lst.st_uid == my_uid) != bool(same_user):
            return False
    if same_user or expected_uid is not None:
        return _is_trusted_ini_path(path,
                                    root_uid=root_uid,
                                    expected_uid=expected_uid)
    ini_dir = os.path.dirname(path)
    if ini_dir:
        return _has_trusted_ancestors_unowned(ini_dir, root_uid=root_uid)
    return True


def _is_trusted_executable(path):
    """Whether `path` is a program the monitor's user or root put in place.

    The file and its directory must be owned by the monitor's user or root and
    must not be group- or world-writable. Every directory entered on the way
    to them must pass _trusted_canonical_dir(), with the user's own
    directories held to root's rule (no group write): unlike a data file, a
    program swapped in by a member of the user's group would run as the user.
    A symlink is followed only if the user or root owns it, under the same
    rules. Windows has no POSIX ownership; there only the regular-file check
    applies.
    """
    my_uid = _current_uid()
    if my_uid is None:
        try:
            return (stat.S_ISREG(os.stat(path).st_mode) and
                    stat.S_ISDIR(os.stat(os.path.dirname(path) or '.').st_mode))
        except (OSError, ValueError):
            return False

    def only_owner_writes(st):
        return (st.st_uid in (my_uid, 0) and
                not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))

    candidate = path
    for _ in range(_MAX_SYMLINK_HOPS + 1):
        parent, name = os.path.split(candidate)
        # root_uid=my_uid applies root's rule to the user's own directories
        # as well.
        canon_dir = _trusted_canonical_dir(parent,
                                           expected_uid=my_uid,
                                           root_uid=my_uid)
        if not name or canon_dir is None:
            return False
        leaf = os.path.join(canon_dir, name)
        try:
            if not only_owner_writes(os.lstat(canon_dir)):
                return False
            st = os.lstat(leaf)
            if not stat.S_ISLNK(st.st_mode):
                return stat.S_ISREG(st.st_mode) and only_owner_writes(st)
            if st.st_uid not in (my_uid, 0):
                return False
            candidate = os.path.join(canon_dir, os.readlink(leaf))
        except (OSError, ValueError):
            return False
    return False


def resolve_netsim_cli_bin(daemon_exe: Optional[str],
                           same_user: bool = True,
                           os_family: Optional[str] = None) -> str:
    """Picks the `netsim` CLI to drive a daemon with.

    A sibling of the daemon's executable is used only when the daemon runs as
    the monitor's user and the sibling passes _is_trusted_executable().
    Otherwise the bare name resolves through the monitor's own PATH.
    """
    is_win = (os.name == 'nt'
              if os_family is None else _os_family(os_family) == 'win32')
    default = 'netsim.exe' if is_win else 'netsim'
    # argv[0], which stands in for an unreadable executable path, may be
    # relative to the daemon's working directory, not the monitor's.
    if not daemon_exe or not same_user or not os.path.isabs(daemon_exe):
        return default
    exe_dir = os.path.dirname(daemon_exe)
    if not exe_dir:
        return default
    for bin_name in ('netsim.exe', 'netsim'):
        cand = os.path.join(exe_dir, bin_name)
        if _is_trusted_executable(cand):
            return cand
    return default


def _copy_regular_file(
    src: str,
    dst: str,
    expected_uid: Optional[int] = None,
) -> Optional[os.stat_result]:
    """Copies src to dst without following a symlink at src.

    The artifact dir comes from the daemon's environment, so a symlink planted
    there must not turn the copy into a read of an arbitrary file. The source
    is opened via _open_owned_regular_file (O_NOFOLLOW + identity re-check).
    Returns the source stat, or None when src is not a plain file.
    """
    fsrc, fst = _open_owned_regular_file(src, expected_uid=expected_uid)
    if fsrc is None:
        return None
    try:
        with fsrc, open(dst, 'wb') as fdst:
            shutil.copyfileobj(fsrc, fdst)
        return fst
    except OSError:
        return None


_MAX_INI_BYTES = 64 * 1024
_MAX_SESSION_STATS_BYTES = 4 * 1024 * 1024
_MAX_LOG_POLL_BYTES = 16 * 1024 * 1024


def _open_owned_regular_file(
    path: Optional[str],
    expected_uid: Optional[int] = None,
) -> Tuple[Optional[Any], Optional[os.stat_result]]:
    """Opens a verified regular file without following leaf symlinks or blocking."""
    if not path:
        return None, None
    my_uid = _current_uid()
    try:
        lst = os.lstat(path)
    except OSError:
        return None, None
    if not stat.S_ISREG(lst.st_mode):
        return None, None
    if (my_uid is not None and expected_uid is not None and
            lst.st_uid != expected_uid):
        return None, None
    flags = os.O_RDONLY
    for name in ('O_NOFOLLOW', 'O_CLOEXEC', 'O_BINARY', 'O_NONBLOCK'):
        flags |= getattr(os, name, 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None, None
    try:
        fst = os.fstat(fd)
        if (not stat.S_ISREG(fst.st_mode) or
            (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino)):
            return None, None
        if (my_uid is not None and expected_uid is not None and
                fst.st_uid != expected_uid):
            return None, None
        fobj = os.fdopen(fd, 'rb')
        fd = None
        return fobj, fst
    except OSError:
        return None, None
    finally:
        if fd is not None:
            os.close(fd)


def _read_regular_file_bounded(
    path: Optional[str],
    max_bytes: int,
    reject_oversize: bool = False,
    require_self_owner: bool = False,
    expected_uid: Optional[int] = None,
) -> Tuple[Optional[str], Optional[os.stat_result]]:
    """Reads up to `max_bytes` from a verified regular file, decoded as UTF-8."""
    eff_uid = (expected_uid if expected_uid is not None else
               (_resolve_expected_uid(None, same_user=True)
                if require_self_owner else None))
    fobj, fst = _open_owned_regular_file(path, expected_uid=eff_uid)
    if fobj is None or fst is None:
        return None, None
    try:
        with fobj:
            if reject_oversize and fst.st_size > max_bytes:
                return None, fst
            raw = fobj.read(max_bytes + (1 if reject_oversize else 0))
            if reject_oversize and len(raw) > max_bytes:
                return None, fst
            return raw[:max_bytes].decode('utf-8', errors='replace'), fst
    except OSError:
        return None, None


def host_discovery_dirs(host_env, target_os=None, uid=None):
    """get_netsim_discovery_dirs() for the monitor's own environment.

    On Linux this adds /run/user/<uid>, which the emulator uses for
    avd/running when XDG_RUNTIME_DIR is unset (GetAlternativeRoot()).
    """
    dirs = get_netsim_discovery_dirs(host_env, target_os)
    if _os_family(target_os) == 'linux' and uid is not None:
        _extend_unique(dirs, [f'/run/user/{uid}'])
    return dirs


def _read_emulator_discovery_ini(
    pid: int,
    proc_obj: Any = None,
    expected_uid: Optional[int] = None,
) -> Dict[str, str]:
    """Reads <discovery_dir>/avd/running/pid_<pid>.ini without reading tokens.

    Searches the worker's discovery dirs, then the monitor's, and skips empty
    files.
    """
    if not pid:
        return {}
    env = {}
    if proc_obj is not None:
        try:
            env = proc_obj.environ()
        except Exception:
            pass
    my_uid = _current_uid()
    worker_uid = (expected_uid
                  if expected_uid is not None else _proc_uid(proc_obj))
    eff_uid = _resolve_expected_uid(worker_uid,
                                    same_user=True,
                                    monitor_uid=my_uid)
    dirs = get_netsim_discovery_dirs(env if isinstance(env, dict) else {})
    disc_uid = eff_uid if eff_uid is not None else my_uid
    _extend_unique(dirs, host_discovery_dirs(os.environ, uid=disc_uid))

    for base in dirs:
        avd_dir = os.path.join(base, 'avd')
        running_dir = os.path.join(avd_dir, 'running')
        if (not _is_trusted_owned_real_dir(avd_dir, expected_uid=eff_uid) or
                not _is_trusted_owned_real_dir(running_dir,
                                               expected_uid=eff_uid)):
            continue
        ini_path = os.path.join(running_dir, f'pid_{pid}.ini')
        text, _ = _read_regular_file_bounded(ini_path,
                                             _MAX_INI_BYTES,
                                             reject_oversize=True,
                                             expected_uid=eff_uid)
        if not text:
            continue
        data = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            # Only the key marks a secret (grpc.token): an AVD name or path
            # may contain the word.
            if 'token' in k.lower():
                continue
            data[k] = v.strip()
        if data:
            return data
    return {}


def _strip_exe(name):
    """Drops a Windows '.exe' suffix."""
    return name[:-4] if name.endswith('.exe') else name


# Upper bounds of the Rust integer types _parse_decimal() mirrors.
_U16_MAX = 0xFFFF
_U32_MAX = 0xFFFFFFFF
_U64_MAX = 0xFFFFFFFFFFFFFFFF


def _parse_decimal(text, max_value):
    """`text` as Rust's `str::parse::<uN>()` reads it, or None.

    That is ASCII digits after an optional '+', no larger than `max_value`.
    Unlike int() it takes no '_' or other digit scripts, and a long run of
    digits cannot make it raise.
    """
    if not isinstance(text, str) or not re.fullmatch(r'\+?[0-9]+', text):
        return None
    digits = text.lstrip('+').lstrip('0') or '0'
    if len(digits) > len(str(max_value)):
        return None
    num = int(digits)
    return num if num <= max_value else None


_I64_MAX = 0x7FFFFFFFFFFFFFFF
_I64_MIN = -0x8000000000000000
# The strings Rust's `str::parse::<f64>()` takes. Unlike float(), it takes no
# '_', no surrounding whitespace and no other digit scripts.
_RE_RUST_F64 = re.compile(
    r'[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?'
    r'|inf|infinity|nan)', re.ASCII | re.IGNORECASE)


def _json_whole_seconds(value):
    """A JSON number, or a string holding one, as Rust reads whole seconds.

    A number, or a string `parse::<f64>()` takes, is cast like `as i64`:
    truncated, clamped to the i64 range, NaN is 0. Anything else is 0.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, str):
        if not _RE_RUST_F64.fullmatch(value):
            return 0
        value = float(value)
    elif isinstance(value, int):
        try:
            value = float(value)
        except OverflowError:
            return _I64_MAX if value > 0 else _I64_MIN
    elif not isinstance(value, float):
        return 0
    if value != value:
        return 0
    if value >= 2.0**63:
        return _I64_MAX
    if value <= -(2.0**63):
        return _I64_MIN
    return int(value)


def _merge_discovery_ini(disc, port, avd_id, display_name, grpc_endpoint):
    """Fills fields the cmdline lacks from a pid_<pid>.ini dict.

    As in Rust, the display name comes only from avd.name, with '_' shown as
    a space, and port.serial is a u16 above 0.
    """
    if not port:
        port = _parse_decimal(disc.get('port.serial'), _U16_MAX) or port
    if not avd_id and disc.get('avd.id'):
        avd_id = disc['avd.id']
    if not display_name and disc.get('avd.name'):
        display_name = disc['avd.name'].replace('_', ' ')
    if not grpc_endpoint and disc.get('netsim.endpoint'):
        grpc_endpoint = disc['netsim.endpoint']
    return port, avd_id, display_name, grpc_endpoint


_SCRIPT_INTERPRETERS = (
    'py',
    'sh',
    'bash',
    'zsh',
    'dash',
    'ksh',
    'fish',
    'env',
    'perl',
    'ruby',
    'node',
    'pwsh',
    'powershell',
    'cmd',
)


def _is_script_interpreter(stem):
    return bool(stem) and (stem.startswith(
        ('python', 'pypy')) or stem in _SCRIPT_INTERPRETERS)


def _extract_script_cmd_base(cmdline):
    """Returns (cmd0_base, script_base) from a process cmdline sequence."""
    if not isinstance(cmdline, (list, tuple)) or not cmdline or not isinstance(
            cmdline[0], str):
        return "", ""
    cmd0_base = os.path.basename(cmdline[0].replace('\\', '/')).lower()
    cmd0_stem = _strip_exe(cmd0_base)
    if not _is_script_interpreter(cmd0_stem):
        return cmd0_base, ""
    idx = 1
    if cmd0_stem == 'env':
        while idx < len(cmdline) and isinstance(cmdline[idx], str):
            tok = cmdline[idx]
            if tok.startswith('-') or '=' in tok:
                idx += 1
                continue
            tok_stem = _strip_exe(
                os.path.basename(tok.replace('\\', '/')).lower())
            if _is_script_interpreter(tok_stem):
                idx += 1
            break
    while idx < len(cmdline) and isinstance(cmdline[idx], str):
        tok = cmdline[idx]
        if tok in ('-c', '--'):
            break
        if tok == '-m' and idx + 1 < len(cmdline) and isinstance(
                cmdline[idx + 1], str):
            return cmd0_base, cmdline[idx + 1].lower()
        if tok.startswith('-'):
            idx += 1
            continue
        return cmd0_base, os.path.basename(tok.replace('\\', '/')).lower()
    return cmd0_base, ""


def _classify_process(name_lower, exe_base, cmd0_base, cmd1_base=""):
    """Returns ('netsimd' | 'worker' | 'emulator' | None)."""
    for bad in ('terminal', 'crash', 'qemu-img', 'resource_monitor',
                'resource-monitor', 'emulator_resour', 'emulator-resour',
                'emulator-check'):
        if (bad in name_lower or bad in exe_base or bad in cmd0_base or
                bad in cmd1_base):
            return None

    name_stem = _strip_exe(name_lower)
    exe_stem = _strip_exe(exe_base)
    cmd0_stem = _strip_exe(cmd0_base)

    if 'netsimd' in (name_stem, exe_stem, cmd0_stem):
        return 'netsimd'
    if 'netsim' in name_lower or 'netsim' in exe_base or 'netsim' in cmd0_base:
        return None

    if (name_lower.startswith('qemu-system') or
            exe_base.startswith('qemu-system') or
            cmd0_base.startswith('qemu-system') or 'crosvm' in name_lower or
            'crosvm' in exe_base or 'crosvm' in cmd0_base or
            'qemu' in name_lower):
        return 'worker'

    if ('fishtank' in name_lower or 'fishtank' in exe_base or
            'fishtank' in cmd0_base or 'launcherloop' in name_lower or
            'launcherloop' in exe_base or 'launcherloop' in cmd0_base or
            'emulator' in name_lower or exe_stem == 'emulator' or
            cmd0_stem == 'emulator' or exe_stem.startswith(
                ('emulator64', 'emulator-')) or cmd0_stem.startswith(
                    ('emulator64', 'emulator-'))):
        return 'emulator'

    return None


def _is_descendant_of(pid, ppid_map, target_pid):
    curr = pid
    seen = set()
    for _ in range(16):
        if curr is None or curr in seen:
            return False
        if curr == target_pid:
            return True
        seen.add(curr)
        curr = ppid_map.get(curr)
    return False


def _find_listening_console_port(cached_proc: Any) -> Optional[int]:
    """Returns first listening console port (even 5554..5584), or None.

    Args:
        cached_proc: Cached `psutil.Process` handle to inspect.

    Returns:
        Even console TCP port in `5554..=5584`, or None when no listening
        console socket is visible.
    """
    for conn_kind in ('inet', 'tcp4', 'tcp6'):
        try:
            conns = cached_proc.net_connections(kind=conn_kind)
        except (psutil.ZombieProcess, psutil.NoSuchProcess):
            raise
        except Exception:
            continue
        for conn in conns or []:
            try:
                if (conn.status == 'LISTEN' and
                        5554 <= conn.laddr.port <= 5584 and
                        conn.laddr.port % 2 == 0):
                    return conn.laddr.port
            except Exception:
                continue
    return None


@dataclasses.dataclass
class _EmuSample:
    """Per-process emulator telemetry and identity captured in a host scan."""

    cache_key: Tuple[Any, float]
    role: str
    name: str
    pid: Any
    ppid: Optional[int]
    ctime: float
    cached_proc: Any
    cpu_pct: float
    rss: int
    threads: int
    ctx_total: int
    port: Optional[int] = None
    avd_name: Optional[str] = None
    avd_id: Optional[str] = None
    display_name: Optional[str] = None
    grpc_endpoint: Optional[str] = None
    no_netsim: bool = False
    ini_resolved: bool = False
    cmdline_resolved: bool = True
    first_attempt: Optional[float] = None
    unmapped_timeout: bool = False
    legacy_cache: bool = False
    propagated: bool = False

    def merge_identity(self, src: '_EmuSample') -> bool:
        """Fills missing port, AVD identity, and netsim metadata from `src`.

        Args:
            src: Source sample to copy missing identity fields from.

        Returns:
            True if any field changed; False if both samples hold conflicting
            non-None console ports or nothing changed.
        """
        if (self.port is not None and src.port is not None and
                self.port != src.port):
            return False
        changed = False
        if self.port is None and src.port is not None:
            self.port = src.port
            changed = True
        if not self.avd_id and src.avd_id:
            self.avd_id = src.avd_id
            changed = True
        if self.avd_id:
            if self.avd_name != self.avd_id:
                self.avd_name = self.avd_id
                changed = True
        elif not self.avd_name and src.avd_name:
            self.avd_name = src.avd_name
            changed = True
        if not self.display_name and src.display_name:
            self.display_name = src.display_name
            changed = True
        if not self.grpc_endpoint and src.grpc_endpoint:
            self.grpc_endpoint = src.grpc_endpoint
            changed = True
        if src.no_netsim and not self.no_netsim:
            self.no_netsim = True
            changed = True
        return changed


def _propagate_emu_hierarchy(samples: List[_EmuSample],
                             ppid_map: Dict[int, int]) -> None:
    """Propagates port, AVD identity, and netsim args between parent/child.

    Args:
        samples: `_EmuSample` records of the current tick, updated in place.
        ppid_map: Mapping of `pid -> ppid` across live OS processes.
    """
    idx_by_pid = {
        s.pid: idx for idx, s in enumerate(samples) if isinstance(s.pid, int)
    }

    for _ in range(2):
        for idx, s in enumerate(samples):
            curr = s.ppid
            if curr is None and isinstance(s.pid, int):
                curr = ppid_map.get(s.pid)
            seen = {s.pid}
            parent_idx = None
            for _ in range(16):
                if curr is None or curr in seen:
                    break
                if curr in idx_by_pid and idx_by_pid[curr] != idx:
                    parent_idx = idx_by_pid[curr]
                    break
                seen.add(curr)
                curr = ppid_map.get(curr)
            if parent_idx is None:
                continue
            parent = samples[parent_idx]
            if s.merge_identity(parent):
                s.propagated = True
            if parent.merge_identity(s):
                parent.propagated = True


def _persist_pid_port_cache(pid_port_cache: Dict[Any, Any],
                            sample: _EmuSample) -> None:
    """Writes one uniform cache record schema for `sample` when warranted."""
    has_identity = any((sample.avd_name, sample.avd_id, sample.display_name,
                        sample.grpc_endpoint, sample.no_netsim))
    should_persist = (bool(sample.port) and
                      (not sample.legacy_cache or sample.propagated)) or (
                          not sample.port and not sample.legacy_cache and
                          (has_identity or sample.unmapped_timeout or
                           sample.first_attempt is not None))
    if not should_persist:
        return
    pid_port_cache[sample.cache_key] = {
        'port': sample.port or None,
        'avd_name': sample.avd_name,
        'avd_id': sample.avd_id,
        'display_name': sample.display_name,
        'grpc_endpoint': sample.grpc_endpoint,
        'no_netsim': sample.no_netsim,
        'ini_resolved': sample.ini_resolved,
        'cmdline_resolved': sample.cmdline_resolved,
        'first_attempt': sample.first_attempt,
        'unmapped': False if sample.port else sample.unmapped_timeout,
    }


def _accumulate_emu_sample(accumulated_emus: Dict[str, Dict[str, Any]],
                           unmapped_qemu: List[Tuple[Any, str, str]],
                           sample: _EmuSample) -> None:
    """Buckets `sample` into an `emulator-<port>` group or unmapped list."""
    if not sample.port:
        cpu_str = f"{sample.cpu_pct:.1f}%"
        ram_str = (f"{sample.rss / (1024**2):.1f}MB "
                   f"({sample.threads} thr, {sample.ctx_total} cs)")
        unmapped_qemu.append((sample.pid, cpu_str, ram_str))
        return

    is_worker = sample.role == 'worker'
    is_launcher = not is_worker and 'fishtank' not in sample.name
    serial = f"emulator-{sample.port}"
    entry = accumulated_emus.get(serial)
    if entry is None:
        accumulated_emus[serial] = {
            'cpu_pct': sample.cpu_pct,
            'rss': sample.rss,
            'threads': sample.threads,
            'ctx_total': sample.ctx_total,
            'primary_pid': sample.pid,
            'primary_ctime': sample.ctime,
            'primary_is_worker': is_worker,
            'max_cpu': sample.cpu_pct,
            'max_rss': sample.rss,
            'avd_id': sample.avd_id,
            'display_name': sample.display_name,
            'avd_name': sample.avd_id or sample.avd_name,
            'grpc_endpoint': sample.grpc_endpoint,
            'no_netsim': sample.no_netsim,
            'pids': {sample.pid} if sample.pid is not None else set(),
            'member_start_times': {
                sample.pid: sample.ctime
            } if sample.pid is not None else {},
            'worker_pid': sample.pid if is_worker else None,
            'worker_ctime': sample.ctime if is_worker else None,
            'launcher_pid': sample.pid if is_launcher else None,
            'launcher_ctime': sample.ctime if is_launcher else None,
            'procs': [sample.cached_proc],
        }
        return

    entry['cpu_pct'] += sample.cpu_pct
    entry['rss'] += sample.rss
    entry['threads'] += sample.threads
    entry['ctx_total'] += sample.ctx_total
    if sample.pid is not None:
        entry['pids'].add(sample.pid)
        entry['member_start_times'][sample.pid] = sample.ctime
    entry['procs'].append(sample.cached_proc)
    if is_worker and entry['worker_pid'] is None:
        entry['worker_pid'] = sample.pid
        entry['worker_ctime'] = sample.ctime
    elif is_launcher and entry['launcher_pid'] is None:
        entry['launcher_pid'] = sample.pid
        entry['launcher_ctime'] = sample.ctime

    if is_worker and not entry['primary_is_worker']:
        is_new_primary = True
    elif is_worker != entry['primary_is_worker']:
        is_new_primary = False
    else:
        is_new_primary = (sample.cpu_pct > entry['max_cpu']) or (
            sample.cpu_pct == entry['max_cpu'] and
            sample.rss > entry['max_rss'])

    if is_new_primary:
        entry['primary_is_worker'] = is_worker
        entry['max_cpu'] = sample.cpu_pct
        entry['max_rss'] = sample.rss
        entry['primary_pid'] = sample.pid
        entry['primary_ctime'] = sample.ctime

    if sample.avd_id and not entry['avd_id']:
        entry['avd_id'] = sample.avd_id
    if sample.display_name and not entry['display_name']:
        entry['display_name'] = sample.display_name
    if sample.grpc_endpoint and not entry['grpc_endpoint']:
        entry['grpc_endpoint'] = sample.grpc_endpoint
    if sample.no_netsim:
        entry['no_netsim'] = True
    entry['avd_name'] = (entry['avd_id'] or entry['avd_name'] or
                         sample.avd_name)


def get_process_stats():
    global _PID_PORT_CACHE, _PROCESS_CACHE, _LAST_EMU_GROUP_PIDS
    global _LAST_EMU_GROUP_INFO, _LAST_PRIMARY_PROC_ID, _LAST_NETSIMD_PROCS
    # Lock is released after copying caches so psutil I/O runs without holding the lock.
    with _PROCESS_STATE_LOCK:
        pid_port_cache = dict(_PID_PORT_CACHE)
        process_cache = dict(_PROCESS_CACHE)
    current_pids = set()
    accumulated_emus = {}
    unmapped_qemu, netsim, netsim_ram = [], [], []
    netsimd_procs = []
    emu_samples = []

    dead_statuses = {
        getattr(psutil, 'STATUS_ZOMBIE', 'zombie'),
        getattr(psutil, 'STATUS_DEAD', 'dead'),
    }
    raw_procs = list(
        psutil.process_iter(['name', 'pid', 'create_time', 'ppid', 'status']))
    ppid_map = {}
    for proc in raw_procs:
        try:
            info = proc.info or {}
            pid = info.get('pid')
            ppid = info.get('ppid')
            if not isinstance(ppid, int):
                try:
                    cand = proc.ppid()
                    if isinstance(cand, int):
                        ppid = cand
                except Exception:
                    ppid = None
            if isinstance(pid, int) and isinstance(ppid, int):
                ppid_map[pid] = ppid
        except Exception:
            pass

    my_pid = os.getpid()

    for proc in raw_procs:
        cache_key = None
        try:
            info = proc.info or {}
            if _safe_str(info.get('status')).lower() in dead_statuses:
                continue
            name = _safe_str(info.get('name')).lower()
            pid = info.get('pid')
            ctime = info.get('create_time') or 0.0
            cache_key = (pid, ctime)

            if isinstance(pid, int) and _is_descendant_of(
                    pid, ppid_map, my_pid):
                continue

            exe_str = _safe_str(info.get('exe'))
            if not exe_str:
                try:
                    exe_str = _safe_str(proc.exe())
                except (psutil.ZombieProcess, psutil.NoSuchProcess):
                    raise
                except Exception:
                    exe_str = ""
            exe_base = (os.path.basename(exe_str.replace('\\', '/')).lower()
                        if exe_str else "")
            exe_stem = _strip_exe(exe_base)

            quick_hit = (any(x in name
                             for x in ('qemu', 'emulator', 'crosvm', 'fishtank',
                                       'netsim', 'launcherloop')) or
                         exe_stem in ('emulator', 'netsimd') or
                         exe_stem.startswith(
                             ('qemu-system', 'emulator64', 'emulator-')) or
                         'crosvm' in exe_stem or 'fishtank' in exe_stem or
                         'launcherloop' in exe_stem)
            if not quick_hit:
                continue

            cmdline = None
            cmd0_base = ""
            cmd1_base = ""
            if (_classify_process(name, exe_base, "") is None or
                    _is_script_interpreter(exe_stem) or not exe_base):
                cmdline = _read_proc_cmdline(proc)
                if cmdline is not None:
                    cmd0_base, cmd1_base = _extract_script_cmd_base(cmdline)

            role = _classify_process(name, exe_base, cmd0_base, cmd1_base)
            if role is None:
                continue

            if role == 'emulator':
                if cmdline is None:
                    cmdline = _read_proc_cmdline(proc)
                if _is_emulator_utility_cmdline(cmdline):
                    continue

            is_new = False
            if cache_key not in process_cache:
                process_cache[cache_key] = proc
                is_new = True
                try:
                    proc.cpu_percent(interval=None)
                except (psutil.ZombieProcess, psutil.NoSuchProcess):
                    raise
                except Exception:
                    pass

            cached_proc = process_cache[cache_key]

            try:
                with cached_proc.oneshot():
                    try:
                        live_status = cached_proc.status()
                        if (isinstance(live_status, str) and
                                live_status.lower() in dead_statuses):
                            raise psutil.ZombieProcess(pid)
                    except (psutil.ZombieProcess, psutil.NoSuchProcess):
                        raise
                    except Exception:
                        pass

                    try:
                        cpu_pct = 0.0 if is_new else cached_proc.cpu_percent(
                            interval=None)
                    except (psutil.ZombieProcess, psutil.NoSuchProcess):
                        raise
                    except Exception:
                        cpu_pct = 0.0

                    try:
                        mem_info = cached_proc.memory_info()
                    except (psutil.ZombieProcess, psutil.NoSuchProcess):
                        raise
                    except Exception:
                        mem_info = None

                    try:
                        threads = cached_proc.num_threads()
                    except (psutil.ZombieProcess, psutil.NoSuchProcess):
                        raise
                    except Exception:
                        threads = 0

                    try:
                        ctx = cached_proc.num_ctx_switches()
                        ctx_total = ctx.voluntary + ctx.involuntary
                    except (psutil.ZombieProcess, psutil.NoSuchProcess):
                        raise
                    except Exception:
                        ctx_total = 0
            except (psutil.ZombieProcess, psutil.NoSuchProcess):
                raise
            except Exception:
                cpu_pct, mem_info, threads, ctx_total = 0.0, None, 0, 0

            current_pids.add(cache_key)

            rss = mem_info.rss if mem_info else 0

            if role == 'netsimd':
                netsim.append(cpu_pct)
                if mem_info:
                    netsim_ram.append(mem_info.rss)
                netsimd_procs.append({
                    'pid': pid,
                    'create_time': ctime,
                    'cpu_pct': cpu_pct,
                    'rss': rss,
                    'proc': cached_proc,
                    'ppid': ppid_map.get(pid),
                    'uid': _proc_uid(cached_proc),
                })
                continue

            cache_val = pid_port_cache.get(cache_key)
            port = None
            avd_name = None
            avd_id = None
            display_name = None
            grpc_endpoint = None
            no_netsim = False
            ini_resolved = False
            cmdline_resolved = True
            first_attempt = None
            unmapped_timeout = False
            legacy_cache = False

            if cache_val == 'unmapped':
                unmapped_timeout = True
                legacy_cache = True
            elif isinstance(cache_val, dict):
                port = cache_val.get('port')
                avd_name = cache_val.get('avd_name')
                avd_id = cache_val.get('avd_id')
                display_name = cache_val.get('display_name')
                grpc_endpoint = cache_val.get('grpc_endpoint')
                no_netsim = cache_val.get('no_netsim', False)
                ini_resolved = cache_val.get('ini_resolved', False)
                cmdline_resolved = cache_val.get('cmdline_resolved', True)
                first_attempt = cache_val.get('first_attempt')
                unmapped_timeout = bool(cache_val.get('unmapped', False))
                if not port and not unmapped_timeout and not cmdline_resolved:
                    if cmdline is None:
                        cmdline = _read_proc_cmdline(cached_proc)
                    if cmdline is not None:
                        cmdline_resolved = True
                        (cmd_port, cmd_avd_name, cmd_avd_id, _,
                         cmd_grpc_endpoint,
                         cmd_no_netsim) = _parse_cmdline_details(cmdline)
                        port = port if port is not None else cmd_port
                        avd_id = avd_id or cmd_avd_id
                        avd_name = avd_id or avd_name or cmd_avd_name
                        grpc_endpoint = grpc_endpoint or cmd_grpc_endpoint
                        no_netsim = no_netsim or cmd_no_netsim
                # The emulator writes pid_<pid>.ini after the worker starts,
                # so keep looking for it until it is found.
                if role == 'worker' and not ini_resolved:
                    disc = _read_emulator_discovery_ini(
                        pid, cached_proc, expected_uid=_proc_uid(cached_proc))
                    if disc:
                        (port, avd_id, display_name,
                         grpc_endpoint) = _merge_discovery_ini(
                             disc, port, avd_id, display_name, grpc_endpoint)
                        avd_name = avd_id or avd_name
                        ini_resolved = True
                if not port and not unmapped_timeout:
                    port = _find_listening_console_port(cached_proc)
                    if (not port and first_attempt is not None and
                            time.monotonic() - first_attempt > 60):
                        unmapped_timeout = True
            elif isinstance(cache_val, tuple):
                port, avd_name = cache_val[0], cache_val[1]
                avd_id = avd_name
                legacy_cache = True
            elif isinstance(cache_val, int):
                port = cache_val
                legacy_cache = True
            else:
                first_attempt = (cache_val if isinstance(cache_val, float) else
                                 time.monotonic())
                if cmdline is None:
                    cmdline = _read_proc_cmdline(cached_proc)
                cmdline_resolved = cmdline is not None
                # A cmdline avd_name= is only a fallback AVD id, kept in
                # avd_name; the display name comes from pid_<pid>.ini.
                (port, avd_name, avd_id, _, grpc_endpoint,
                 no_netsim) = _parse_cmdline_details(cmdline)

                if role == 'worker':
                    disc = _read_emulator_discovery_ini(
                        pid, cached_proc, expected_uid=_proc_uid(cached_proc))
                    ini_resolved = bool(disc)
                    (port, avd_id, display_name,
                     grpc_endpoint) = _merge_discovery_ini(
                         disc, port, avd_id, display_name, grpc_endpoint)
                    avd_name = avd_id or avd_name

                if not port:
                    port = _find_listening_console_port(cached_proc)

                if not port and time.monotonic() - first_attempt > 60:
                    unmapped_timeout = True

            emu_samples.append(
                _EmuSample(
                    cache_key=cache_key,
                    role=role,
                    name=name,
                    pid=pid,
                    ppid=ppid_map.get(pid),
                    ctime=ctime,
                    cached_proc=cached_proc,
                    cpu_pct=cpu_pct,
                    rss=rss,
                    threads=threads,
                    ctx_total=ctx_total,
                    port=port,
                    avd_name=avd_name,
                    avd_id=avd_id,
                    display_name=display_name,
                    grpc_endpoint=grpc_endpoint,
                    no_netsim=no_netsim,
                    ini_resolved=ini_resolved,
                    cmdline_resolved=cmdline_resolved,
                    first_attempt=first_attempt,
                    unmapped_timeout=unmapped_timeout,
                    legacy_cache=legacy_cache,
                ))

        except (psutil.NoSuchProcess, psutil.AccessDenied,
                psutil.ZombieProcess):
            current_pids.discard(cache_key)
            process_cache.pop(cache_key, None)

    _propagate_emu_hierarchy(emu_samples, ppid_map)

    for sample in emu_samples:
        _persist_pid_port_cache(pid_port_cache, sample)
        _accumulate_emu_sample(accumulated_emus, unmapped_qemu, sample)

    qemu_stats = {}
    qemu_pids = {}
    qemu_avds = {}
    group_pids = {}
    group_info = {}
    primary_proc_id = {}
    for serial, entry in accumulated_emus.items():
        cpu_str = f"{entry['cpu_pct']:.1f}%"
        ram_str = f"{entry['rss'] / (1024**2):.1f}MB ({entry['threads']} thr, {entry['ctx_total']} cs)"
        qemu_stats[serial] = (cpu_str, ram_str)
        qemu_pids[serial] = entry['primary_pid']
        primary_proc_id[serial] = (entry['primary_pid'], entry['primary_ctime'])
        group_pids[serial] = set(entry['pids'])
        # As in Rust, a cmdline avd_name= is the AVD id when nothing better
        # is known.
        entry['avd_id'] = entry['avd_id'] or entry['avd_name']
        group_info[serial] = entry
        if entry['avd_name']:
            qemu_avds[serial] = entry['avd_name']

    # Publish updated caches and scan snapshots atomically under _PROCESS_STATE_LOCK.
    with _PROCESS_STATE_LOCK:
        _PID_PORT_CACHE = {
            k: v for k, v in pid_port_cache.items() if k in current_pids
        }
        _PROCESS_CACHE = {
            k: v for k, v in process_cache.items() if k in current_pids
        }
        _LAST_EMU_GROUP_PIDS = group_pids
        _LAST_EMU_GROUP_INFO = group_info
        _LAST_PRIMARY_PROC_ID = primary_proc_id
        _LAST_NETSIMD_PROCS = netsimd_procs

    netsim_str = f"{sum(netsim):.1f}% ({len(netsim)} processes)" if netsim else "N/A"
    netsim_ram_str = f"{sum(netsim_ram) / (1024**2):.1f}MB ({len(netsim)} processes)" if netsim else "N/A"

    return qemu_stats, unmapped_qemu, netsim_str, netsim_ram_str, qemu_pids, qemu_avds


DiskIo = namedtuple('DiskIo', ['read_bytes', 'write_bytes'])
_LAST_DISK_IO = None
_LAST_DISK_TIME = time.monotonic()
_LAST_PAGING_SAMPLE_TIME = None

_LAST_CPU_TIMES = None


def _cpu_total_and_busy(times):
    """Returns (total, busy) CPU seconds as psutil.cpu_percent() defines them."""
    total = sum(times)
    if sys.platform.startswith('linux'):
        total -= getattr(times, 'guest', 0.0) + getattr(times, 'guest_nice',
                                                        0.0)
    return total, total - times.idle - getattr(times, 'iowait', 0.0)


def host_cpu_percent():
    """Machine-wide busy CPU % since the previous call.

    Uses our own cpu_times() baseline, so other psutil.cpu_percent() callers
    cannot shrink the measurement window. The first call primes the baseline.
    """
    global _LAST_CPU_TIMES
    if _LAST_CPU_TIMES is None:
        _LAST_CPU_TIMES = psutil.cpu_times()
        time.sleep(0.2)
    prev, _LAST_CPU_TIMES = _LAST_CPU_TIMES, psutil.cpu_times()
    prev_total, prev_busy = _cpu_total_and_busy(prev)
    cur_total, cur_busy = _cpu_total_and_busy(_LAST_CPU_TIMES)
    total = cur_total - prev_total
    if total <= 0:
        return 0.0
    return min(100.0, max(0.0, 100.0 * (cur_busy - prev_busy) / total))


def physical_disk_names():
    """Linux whole physical disks: /sys/block entries backed by a device.

    Partitions are not in /sys/block, and device-mapper, loop, md, zram and
    ram devices have no `device` link, so their I/O (already counted on the
    underlying disk) is not added twice. Returns None if /sys/block is missing.
    """
    try:
        names = os.listdir('/sys/block')
    except OSError:
        return None
    return {n for n in names if os.path.exists(f'/sys/block/{n}/device')}


def read_disk_io(os_name):
    """Cumulative bytes read/written: whole physical disks on Linux, else psutil's total."""
    disks = physical_disk_names() if os_name == 'Linux' else None
    if disks is not None:
        if not disks:
            return DiskIo(0, 0)
        per_disk = psutil.disk_io_counters(perdisk=True) or {}
        counters = [io for name, io in per_disk.items() if name in disks]
        return DiskIo(sum(io.read_bytes for io in counters),
                      sum(io.write_bytes for io in counters))
    return psutil.disk_io_counters()


_MIN_VALID_CPU_FREQ_MHZ = 100
_RE_VM_STAT_PAGE_SIZE = re.compile(r'page size of (\d{1,12}) bytes')
_RE_VM_STAT_COUNTER = re.compile(r'^([^:]+):\s*(\d{1,20})\.?$')


def _parse_mac_vm_stat_used_bytes(out: Optional[str]) -> Optional[int]:
    """Computes macOS Activity Monitor 'Memory Used' bytes from `vm_stat`.

    Matches Rust `sysinfo` (`HOST_VM_INFO64`): `(Anonymous pages - Pages
    purgeable) + Pages wired down + Pages occupied by compressor`, scaled by
    the header page size.

    Args:
        out: Raw stdout from `vm_stat`, or None.

    Returns:
        Used memory in bytes, or None when required fields are absent.
    """
    if not out:
        return None
    m_page = _RE_VM_STAT_PAGE_SIZE.search(out)
    if not m_page:
        return None
    page_size = int(m_page.group(1))
    if page_size <= 0:
        return None
    counts: Dict[str, int] = {}
    for line in out.splitlines():
        if m := _RE_VM_STAT_COUNTER.match(line.strip()):
            counts[m.group(1).strip()] = int(m.group(2))
    if 'Anonymous pages' not in counts or 'Pages wired down' not in counts:
        return None
    app_pages = max(
        0, counts['Anonymous pages'] - counts.get('Pages purgeable', 0))
    used_pages = (app_pages + counts['Pages wired down'] +
                  counts.get('Pages occupied by compressor', 0))
    return used_pages * page_size


def get_host_stats():
    global _LAST_DISK_IO, _LAST_DISK_TIME, _LAST_PAGING_SAMPLE_TIME
    os_name = platform.system()
    host_total = f"{host_cpu_percent():.1f}%"

    mem = psutil.virtual_memory()
    host_ram = f"{mem.percent:.1f}% ({mem.used / (1024**3):.1f}GB/{mem.total / (1024**3):.1f}GB)"

    swap = psutil.swap_memory()
    host_swap = f"{swap.percent:.1f}% ({swap.used / (1024**3):.1f}GB/{swap.total / (1024**3):.1f}GB)"

    qemu_stats, unmapped_qemu, netsim_str, netsim_ram_str, qemu_pids, qemu_avds = get_process_stats(
    )

    pgin, pgout = 0, 0
    if os_name == "Darwin":
        vm_stat_out = run_cmd(['vm_stat'], timeout=2)
        mac_used = _parse_mac_vm_stat_used_bytes(vm_stat_out)
        if mac_used is not None and mem.total > 0:
            mac_pct = (mac_used / mem.total) * 100.0
            host_ram = (f"{mac_pct:.1f}% "
                        f"({mac_used / (1024**3):.1f}GB/"
                        f"{mem.total / (1024**3):.1f}GB)")
        for line in vm_stat_out.splitlines():
            if ("Pages paged in:" in line or
                    "Pageins:" in line) and (m := RE_FLOAT.search(line)):
                pgin = int(float(m.group(1)))
            elif ("Pages paged out:" in line or
                  "Pageouts:" in line) and (m := RE_FLOAT.search(line)):
                pgout = int(float(m.group(1)))
    elif os_name == "Linux":
        try:
            with open('/proc/vmstat', 'r') as f:
                content = f.read()
            if m_in := re.search(r'pgpgin\s+(\d+)', content):
                pgin = int(m_in.group(1))
            if m_out := re.search(r'pgpgout\s+(\d+)', content):
                pgout = int(m_out.group(1))
        except Exception:
            pass
    _LAST_PAGING_SAMPLE_TIME = time.monotonic()

    gpu_load, gpu_ram = get_gpu_stats()
    emu_gpu_stats = get_emu_gpu_stats(qemu_pids)

    temp_str = "N/A"
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                all_t = [
                    entry.current
                    for entries in temps.values()
                    for entry in entries
                    if entry.current is not None and 0.0 < entry.current < 150.0
                ]
                if all_t:
                    temp_str = f"{max(all_t):.1f}°C"
    except Exception:
        pass

    if temp_str == "N/A" and os_name == "Linux":
        try:
            zones = glob.glob('/sys/class/thermal/thermal_zone*/temp')
            t_vals = []
            for z in zones:
                try:
                    with open(z, 'r') as f:
                        val = float(f.read().strip()) / 1000.0
                        if 0.0 < val < 150.0:
                            t_vals.append(val)
                except Exception:
                    pass
            if t_vals:
                temp_str = f"{max(t_vals):.1f}°C"
        except Exception:
            pass

    batt_str = "N/A"
    try:
        if hasattr(psutil, "sensors_battery"):
            batt = psutil.sensors_battery()
            if batt:
                status = "Plugged" if batt.power_plugged else "Discharging"
                try:
                    freq = psutil.cpu_freq()
                    freq_str = (f"@{int(freq.current)}MHz" if freq and
                                hasattr(freq, 'current') and freq.current and
                                freq.current >= _MIN_VALID_CPU_FREQ_MHZ else "")
                except Exception:
                    freq_str = ""
                batt_str = f"{batt.percent:.1f}% ({status}) {freq_str}".strip()
    except Exception:
        pass

    curr_time = time.monotonic()
    elapsed = curr_time - _LAST_DISK_TIME
    curr_disk = read_disk_io(os_name)

    read_mb, write_mb = 0.0, 0.0
    if _LAST_DISK_IO and curr_disk and elapsed > 0.001:
        read_mb = max(0.0, (curr_disk.read_bytes - _LAST_DISK_IO.read_bytes) /
                      (1024**2)) / elapsed
        write_mb = max(0.0,
                       (curr_disk.write_bytes - _LAST_DISK_IO.write_bytes) /
                       (1024**2)) / elapsed

    _LAST_DISK_IO = curr_disk
    _LAST_DISK_TIME = curr_time

    disk_str = f"Read: {read_mb:.1f} MB/s | Write: {write_mb:.1f} MB/s"

    return host_total, qemu_stats, netsim_str, host_ram, host_swap, pgin, pgout, unmapped_qemu, netsim_ram_str, qemu_pids, gpu_load, gpu_ram, emu_gpu_stats, temp_str, disk_str, read_mb, write_mb, batt_str, qemu_avds


def get_guest_stats(serial, known_model, is_booting, status):
    if status != 'device':
        return known_model if known_model else f"Unknown ({status})", bool(
            is_booting
        ), f"N/A ({status})", f"N/A ({status})", f"N/A ({status})", f"N/A ({status})"

    model = known_model
    if not model or "android" not in model.lower(
    ) or "unknown model" in model.lower() or "error:" in model.lower(
    ) or "adb:" in model.lower() or "offline" in model.lower() or (
            "Res: N/A" in model and (is_booting is None or is_booting)):
        script = 'm=$(getprop ro.product.model); v=$(getprop ro.build.version.release); a=$(getprop ro.build.version.sdk); b=$(getprop ro.build.id); w=$(wm size 2>/dev/null); c=$(getprop sys.boot_completed); echo "$m|!|$v|!|$a|!|$b|!|$w|!|$c"'
        m_out = run_cmd(['adb', '-s', serial, 'shell', script],
                        timeout=2).strip()
        if m_out and not any(err in m_out.lower()
                             for err in ("error:", "adb:", "offline")):
            parts = m_out.split('|!|')
            # Empty getprop output early in boot is not a model.
            if len(parts) >= 6 and parts[0].strip():
                res = parts[4].lower().split('size:')[-1].strip(
                ) if "size:" in parts[4].lower() else "N/A"
                model = f"{parts[0]} | Android {parts[1]} (API {parts[2]}) | Build: {parts[3]} | Res: {res}"
                is_booting = (parts[5].strip() != "1")
        else:
            model = known_model or "Unknown Model"

    guest_ram = "N/A"
    guest_swap = "N/A"

    # BATCH OPTIMIZATION: Combine separated ADB shell calls into a single string to entirely bypass network/daemon round-trip latency
    # API 24 logcat rejects a comma-separated -b list.
    batch_script = "cat /proc/meminfo; echo '===ADB_MON_SPLIT==='; df -h /data 2>/dev/null; echo '===ADB_MON_SPLIT==='; logcat -d -t 500 -b main -b crash *:E 2>/dev/null | grep -v \"^---------\" | wc -l"
    batch_script += "; echo '===ADB_MON_SPLIT==='; getprop sys.boot_completed"
    # The trailing separator marks the boot section complete, so an empty
    # getprop (still booting) is not mistaken for a truncated reply.
    batch_script += "; echo '===ADB_MON_SPLIT==='"

    out = run_cmd(['adb', '-s', serial, 'shell', batch_script], timeout=2.5)

    if out and not any(err in out.lower(
    ) for err in ["no devices", "offline", "unauthorized", "error:", "adb:"]):
        try:
            parts = out.split('===ADB_MON_SPLIT===')
            mem_total, mem_avail, used, err_count = 0, 0, 0, 0
            data_space = "N/A"

            if len(parts) >= 1:
                mem = {}
                for line in parts[0].splitlines():
                    lp = line.split()
                    if len(lp) >= 2 and lp[1].isdigit():
                        mem[lp[0].replace(':', '')] = int(lp[1])
                mem_total = mem.get('MemTotal', 0)
                mem_avail = mem.get(
                    'MemAvailable',
                    0) or (mem.get('MemFree', 0) + mem.get('Buffers', 0) +
                           mem.get('Cached', 0))
                if mem_total > 0:
                    used = max(0, mem_total - mem_avail)
                # SwapTotal - SwapFree is what vmstat reports as swpd, and
                # vmstat cannot open /proc/vmstat on API 28-29.
                if 'SwapTotal' in mem and 'SwapFree' in mem:
                    swap_used_kb = max(0, mem['SwapTotal'] - mem['SwapFree'])
                    guest_swap = f"{swap_used_kb / 1024:.1f} MB"

            if len(parts) >= 2:
                # Pick the df row by its numeric Use% column, not its mount
                # point: on API 30-33 `df -h /data` names a bind mount of the
                # /data device (such as /storage/emulated/0/Android/obb), see
                # b/583871630. The emulator images tested print a single row;
                # if a df variant prints several, a row naming /data wins.
                first_row_space = None
                for line in parts[1].splitlines():
                    dp = line.split()
                    pct_idx = next((i for i, x in enumerate(dp)
                                    if RE_DF_USE_PCT.fullmatch(x)), -1)
                    if pct_idx < 2:
                        continue
                    row_space = (f"{dp[pct_idx]} ({dp[pct_idx - 2]} used/"
                                 f"{dp[pct_idx - 1]} avail)")
                    if '/data' in line:
                        data_space = row_space
                        break
                    if first_row_space is None:
                        first_row_space = row_space
                else:
                    if first_row_space is not None:
                        data_space = first_row_space

            if len(parts) >= 3:
                err_str = parts[2].strip()
                if err_str.isdigit():
                    err_count = int(err_str)

            # Five parts means the trailing separator arrived (see batch_script).
            if len(parts) >= 5 or (len(parts) == 4 and parts[3].strip()):
                is_booting = (parts[3].strip() != "1")

            if mem_total > 0:
                guest_ram = f"{(used / mem_total) * 100:.1f}% ({used/1048576:.1f}GB/{mem_total/1048576:.1f}GB, {err_count} errs, {data_space} data)"
        except Exception:
            pass

    guest_diff, guest_real = "N/A", "N/A"
    # Toybox 0.7 top (API 26-28) rejects `-d 0.2` and toolbox top (API 24)
    # rejects `-b`, so the guest shell falls back to an integer delay. Toolbox
    # top also dies of a divide-by-zero when its first frame spans no clock
    # ticks, so the fallback gets a second try.
    out = run_cmd([
        'adb', '-s', serial, 'shell',
        'top -b -n 2 -d 0.2 || top -n 2 -d 1 || top -n 2 -d 1'
    ],
                  timeout=3)

    if out:
        out = RE_ANSI.sub('', out)
        # Toolbox top prints each frame's tick deltas instead of a %cpu line.
        # Its first frame spans only a few ticks, so a reply needs both `-n 2`
        # frames and the last one is used. The kernel's iowait counter can go
        # backwards, so deltas may be negative.
        frames = RE_GUEST_TOOLBOX_TICKS.findall(out)
        if len(frames) >= 2 and int(frames[-1][1]) > 0:
            idle, total = map(int, frames[-1])
            busy = min(100.0, max(0.0, (total - idle) * 100.0 / total))
            return model, is_booting, f"{busy:.1f}%", f"{busy:.1f}% (Cores unknown)", guest_ram, guest_swap
        target_block = out.split('Tasks:')[-1]
        for line in target_block.splitlines():
            line = line.strip()

            t_match = RE_GUEST_CPU_USAGE.search(line)
            i_match = RE_GUEST_CPU_IDLE.search(line)
            if t_match and i_match:
                t_val, i_val = int(t_match.group(1)), int(i_match.group(1))
                if t_val >= 90:
                    diff, cores = max(0, t_val - i_val), max(
                        1, round(t_val / 100.0))
                    return model, is_booting, f"{diff}%", f"{diff / cores:.1f}% (over {cores} cores)", guest_ram, guest_swap

            elif (idle_match := RE_GUEST_CPU_ID.search(line)):
                real_pct = max(
                    0.0, 100.0 - float(idle_match.group(1).replace(',', '.')))
                return model, is_booting, f"{real_pct:.1f}%", f"{real_pct:.1f}% (Cores unknown)", guest_ram, guest_swap

    return model, is_booting, guest_diff, guest_real, guest_ram, guest_swap


def get_val(s, for_ts=False):
    """Safely extracts payload. Returns NaNs for timeseries data gaps preventing average/P95 zero skew."""
    if isinstance(s, (int, float)):
        return float(s)
    s_str = str(s)
    if for_ts and (s_str.split('(', 1)[0].lstrip().startswith("N/A") or
                   any(x in s_str
                       for x in ("Unsupported", "Calculating", "No ADB",
                                 "Error", "Timeout", "Hang"))):
        return float('nan')
    try:
        pcts = RE_PCT.findall(s_str)
        if pcts:
            return float(pcts[0])

        if any(x in s_str for x in ["pages/s", "Out:", "KB/s"]):
            matches = RE_FLOAT.findall(s_str)
            return max(map(float, matches)) if matches else 0.0

        m = RE_FLOAT.search(s_str)
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return float('nan') if for_ts else 0.0


RE_NETSIM_LOG_LINE = re.compile(
    r'^netsim\s+([EWIDT])\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+(\S+)\s+-\s+(.*)$'
)
RE_NETSIM_ARTIFACTS_PATH = re.compile(
    r'(?:netsim artifacts path|Artifacts):\s*"([^"]+)"')
RE_NETSIM_VERSION = re.compile(r'Netsim Version:\s*([^,\s]+)')
RE_NETSIM_INI_PATH = re.compile(r'INI file path:\s*(\S+)')
RE_NETSIM_GRPC_PORT = re.compile(r'gRPC port:\s*(\d+)')
RE_NETSIM_WROTE_INI = re.compile(r'Wrote to INI file\s+(\S+)')
RE_NETSIM_CONN_HEAD = re.compile(
    r'Handling new connection for\s+(\S+)\s+on device\s+(.+?)\s*$')
RE_NETSIM_CONN_1024 = re.compile(
    r'Handling new connection for\s+"([^"]*)",\s+device\s+(.+?)\s*$')
RE_NETSIM_CREATE_DEVICE = re.compile(
    r'DeviceActor:\s*Create device\s+(.+?)\s*$')
RE_NETSIM_ADD_CHIP_HEAD = re.compile(
    r'DeviceActor:\s*AddChip\s+(\S+)\s+\(kind\s+([A-Za-z0-9_]+)(?:,\s*name\s*"([^"]*)")?\)\s+to device\s+(\S+)\s+\((.+)\)\s*$'
)
RE_NETSIM_ADD_CHIP_1024 = re.compile(
    r'DeviceActor:\s*AddChip\s+(.+?)\s+\(([^,]+),\s*(.+?)\)\s+to device\s+(.+?)\s*$'
)
RE_NETSIM_REMOVED_CHIP = re.compile(
    r'DeviceActor:\s*Removed chip\s+(\S+)\s+\(kind\s+([A-Za-z0-9_]+)\)\s+to?from device\s+(\S+)|DeviceActor:\s*Removed chip\s+(\S+)\s+\(kind\s+([A-Za-z0-9_]+)\)\s+from device\s+(\S+)'
)
RE_NETSIM_EMPTY_DEVICE = re.compile(
    r'DeviceActor:\s*Device\s+(\S+)\s+is empty,\s*auto-deleting')
RE_NETSIM_GUID_PORT = re.compile(
    r'DeviceActor:\s*AddChipByGuid(?:.*?)\s+for device\s+\S+?:(\d+)\s*$')
RE_NETSIM_WIFI_ASSOC = re.compile(r'ApActor:\s*Associated\s+([0-9a-fA-F:]{17})')
RE_PROC_LOCK_DEVID = re.compile(r'^([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)$')


def _normalize_chip_kind(kind_str):
    if not kind_str:
        return '?'
    k = kind_str.strip().upper()
    if k in ('BLUETOOTH_LOW_ENERGY', 'BLUETOOTH_CLASSIC', 'BLUETOOTH', 'BLE'):
        return 'BT'
    return k


def get_proc_precise_start_time(pid, fallback_create_time=None):
    """Computes sub-second process start time from /proc/uptime and /proc/<pid>/stat.

    The boot time comes from a clock read right after /proc/uptime. A timestamp
    taken earlier in the tick would move the start back by however long the
    tick has been running.
    """
    try:
        with open('/proc/uptime', 'r', encoding='utf-8') as f:
            uptime_secs = float(f.read().split()[0])
        boot_time = time.time() - uptime_secs
        with open(f'/proc/{int(pid)}/stat', 'r', encoding='utf-8') as f:
            stat_str = f.read()
        rparen = stat_str.rfind(')')
        if rparen != -1:
            fields_after = stat_str[rparen + 2:].split()
            if len(fields_after) > 19:
                starttime_ticks = int(fields_after[19])
                try:
                    clk_tck = int(os.sysconf('SC_CLK_TCK'))
                except (ValueError, AttributeError, OSError):
                    clk_tck = 100
                if clk_tck > 0:
                    precise = boot_time + (starttime_ticks / float(clk_tck))
                    if (fallback_create_time and fallback_create_time > 0 and
                            abs(precise - float(fallback_create_time)) > 2.0):
                        return fallback_create_time
                    return precise
    except (OSError, ValueError, IndexError):
        pass
    return fallback_create_time


def parse_proc_locks(proc_locks_text):
    """Parses /proc/locks content and returns a dict mapping 'maj:min:ino' -> pid."""
    holders = {}
    if not proc_locks_text:
        return holders
    for raw_line in proc_locks_text.splitlines():
        parts = raw_line.split()
        if len(parts) < 6 or '->' in parts:
            continue
        if 'FLOCK' not in parts or 'WRITE' not in parts:
            continue
        for idx, token in enumerate(parts):
            m = RE_PROC_LOCK_DEVID.match(token)
            if m and idx >= 1:
                try:
                    pid = int(parts[idx - 1])
                    maj = int(m.group(1), 16)
                    min_ = int(m.group(2), 16)
                    ino = int(m.group(3))
                    if pid > 0:
                        holders[f"{maj:02x}:{min_:02x}:{ino}"] = pid
                except ValueError:
                    pass
                break
    return holders


def get_file_lock_key(path):
    """Returns the 'maj:min:ino' key for a file path matching /proc/locks format."""
    major = getattr(os, 'major', None)
    minor = getattr(os, 'minor', None)
    if major is None or minor is None:
        return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return f"{major(st.st_dev):02x}:{minor(st.st_dev):02x}:{st.st_ino}"


# Default netsim instance (netsim os_utils.rs DEFAULT_INSTANCE).
DEFAULT_NETSIM_INSTANCE = 1


def extract_ini_instance_num(ini_path):
    """Instance number encoded in a netsim INI file name, or None.

    netsim.ini is the default instance and netsim_<N>.ini is instance N
    (netsim get_ini_filename()). Other names, including the .lock files, are
    not netsim INIs.
    """
    fname = os.path.basename(ini_path or '')
    if fname == 'netsim.ini':
        return DEFAULT_NETSIM_INSTANCE
    m = re.fullmatch(r'netsim_([0-9]+)\.ini', fname)
    return _parse_decimal(m.group(1), _U32_MAX) if m else None


def _parse_netsim_ini_text(text: Optional[str]) -> Dict[str, Any]:
    """Non-sensitive keys of netsim.ini text (token lines are never kept)."""
    data: Dict[str, Any] = {}
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip()
        if 'token' in k.lower():
            continue
        num = None
        if k == 'pid':
            num = _parse_decimal(v, _U32_MAX)
        elif k in ('grpc.port', 'hci.port', 'web.port'):
            num = _parse_decimal(v, _U16_MAX)
        data[k] = v if num is None else num
    return data


def parse_netsim_ini(path: str,
                     expected_uid: Optional[int] = None) -> Dict[str, Any]:
    """Reads non-sensitive keys from a netsim.ini file (never reads token lines)."""
    text, _ = _read_regular_file_bounded(path,
                                         _MAX_INI_BYTES,
                                         reject_oversize=True,
                                         expected_uid=expected_uid)
    return _parse_netsim_ini_text(text)


# Clock slack when comparing an INI's mtime with its writer's start time.
_INI_MTIME_SLACK_S = 2.0

# Ticks an emulator must be missing from both the host scan and adb before it
# counts as exited, so a one-tick miss is not reported as a disconnect.
_SETTLE_AFTER_MISSED_TICKS = 2


def _same_file(a, b):
    """Whether paths a and b name the same file (missing files: by path)."""
    if a == b:
        return True
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _read_ini_observation(path, lock_holders):
    """A netsim INI plus the evidence used to pair it with a daemon.

    'holders' lists the holders of <ini>.lock, then of <ini>.init.lock
    (Linux /proc/locks); 'pid' and 'port' are None unless valid. Owner and
    mtime come from the descriptor the text was read from, so they describe
    the bytes that were parsed; a file that cannot be opened contributes no
    keys and is described by lstat.
    """
    text, fst = _read_regular_file_bounded(path,
                                           _MAX_INI_BYTES,
                                           reject_oversize=True)
    data = _parse_netsim_ini_text(text)
    holders = []
    for suffix in ('.lock', '.init.lock'):
        key = get_file_lock_key(path + suffix)
        if key in lock_holders:
            holders.append(lock_holders[key])
    pid = data.get('pid')
    port = data.get('grpc.port')
    my_uid = _current_uid()
    try:
        lst = fst if fst is not None else os.lstat(path)
        is_reg = stat.S_ISREG(lst.st_mode)
        mtime = lst.st_mtime if is_reg else None
        file_uid = lst.st_uid if (is_reg and my_uid is not None) else None
        owned_by_self = ((file_uid == my_uid) if file_uid is not None else None)
    except OSError:
        mtime = None
        file_uid = None
        owned_by_self = None
    return {
        'path': path,
        'data': data,
        'holders': holders,
        'pid': pid if isinstance(pid, int) and 0 < pid <= 0xFFFFFFFF else None,
        'port': port if isinstance(port, int) and 0 < port <= 0xFFFF else None,
        'instance': extract_ini_instance_num(path),
        'mtime': mtime,
        'file_uid': file_uid,
        'owned_by_self': owned_by_self,
    }


def _parse_netsim_instance_num(text):
    """The instance number netsim's get_instance() takes from one source.

    That is a u16 above 0; anything else is None.
    """
    return _parse_decimal(text, _U16_MAX) or None


def _extract_netsim_instance_num(cmdline_tokens, environ_dict=None):
    """Mirrors netsim's get_instance(): NETSIM_INSTANCE, else -i/--instance.

    A source without a valid number is skipped.
    """
    environ = environ_dict or {}
    num = _parse_netsim_instance_num(environ.get('NETSIM_INSTANCE'))
    if num is not None:
        return num
    tokens = list(cmdline_tokens or ())
    for idx, tok in enumerate(tokens):
        if tok in ('-i', '--instance'):
            following = tokens[idx + 1] if idx + 1 < len(tokens) else None
            num = _parse_netsim_instance_num(following)
        elif isinstance(tok, str) and tok.startswith('--instance='):
            num = _parse_netsim_instance_num(tok.split('=', 1)[1])
        if num is not None:
            return num
    return None


def resolve_netsim_artifact_dir(environ_dict=None,
                                instance_num=None,
                                target_os=None):
    """Mirrors netsimd_temp_dir() in netsim common/src/system/mod.rs.

    ANDROID_TMP, else std::env::temp_dir(), then android-$USER (or android)
    off Windows, then netsimd. Set-but-empty variables count as unset. An
    empty environ_dict (unreadable daemon env) falls back to os.environ,
    which netsimd normally inherits from the same login session. On macOS
    with TMPDIR unset, temp_dir() asks confstr(_CS_DARWIN_USER_TEMP_DIR);
    this falls back to /tmp instead.
    """
    env = environ_dict or os.environ
    if _os_family(target_os) == 'win32':
        # GetTempPath() is already per-user, so netsimd adds no android-$USER.
        base = (env.get('ANDROID_TMP') or env.get('TMP') or env.get('TEMP') or
                env.get('USERPROFILE') or env.get('SystemRoot') or
                'C:\\Windows')
        return os.path.join(base, 'netsimd')
    base = env.get('ANDROID_TMP') or env.get('TMPDIR') or '/tmp'
    user = env.get('USER')
    subdir = f"android-{user}" if user else "android"
    return os.path.join(base, subdir, 'netsimd')


def resolve_netsim_stderr_log_path(artifact_dir, instance_num=None):
    """Resolves netsim_{instance_name}stderr.log inside artifact_dir."""
    inst_prefix = (f"{instance_num}_"
                   if instance_num and int(instance_num) > 1 else "")
    return os.path.join(artifact_dir, f"netsim_{inst_prefix}stderr.log")


def parse_netsim_log_timestamp(line, now_utc=None):
    """Parses a netsim log line header into (level, epoch_utc, message)."""
    if now_utc is None:
        now_utc = time.time()
    m = RE_NETSIM_LOG_LINE.match(line)
    if not m:
        return None, None, line.strip()
    level = m.group(1)
    month, day = int(m.group(2)), int(m.group(3))
    hh, mm, ss, ms = (int(m.group(4)), int(m.group(5)), int(m.group(6)),
                      int(m.group(7)))
    msg = m.group(9)
    now_tm = time.gmtime(now_utc)
    year = now_tm.tm_year
    try:
        ts = calendar.timegm((year, month, day, hh, mm, ss)) + (ms / 1000.0)
        if ts - now_utc > 86400:
            ts = calendar.timegm(
                (year - 1, month, day, hh, mm, ss)) + (ms / 1000.0)
    except ValueError:
        ts = now_utc
    return level, ts, msg


_MAX_TAILER_EVENTS = 512


def _append_bounded(items, item, maxlen=_MAX_TAILER_EVENTS):
    items.append(item)
    if len(items) > maxlen:
        del items[:len(items) - maxlen]


_LOG_HEAD_PREFIX_BYTES = 128
_MAX_LOG_LINE_BYTES = 64 * 1024


class NetsimLogTailer:
    """Incremental log tailer for netsim_stderr.log (1.0.23/1.0.24 and HEAD 1.0.25)."""

    def __init__(self,
                 log_path: str,
                 require_self_owner: bool = False,
                 expected_uid: Optional[int] = None):
        self.log_path = log_path
        self.require_self_owner = bool(require_self_owner)
        self.expected_uid = expected_uid
        self.min_ts = None
        self.offset = 0
        self.inode = None
        self.head_prefix = b''
        self.frozen = False
        self.reset_state()

    def reset_state(self):
        self.version = None
        self.artifact_dir_logged = None
        self.ini_path_logged = None
        self.grpc_port_logged = None
        self.ready_ts = None
        self.frontend_disabled = False
        self.pcap_flag = False
        self.client_mode_spawn = False
        self.exit_reason_hint = None
        self.events = []
        self.connections = []
        self.created_devices = []
        self.chip_adds = []
        self.chip_removals = []
        self.empty_devices = []
        self.pending_guid_port = None
        self.wifi_associations = []
        self.last_timestamp = None
        self.truncation_count = 0
        self.skipping_oversize_line = False

    def poll(self, now_utc=None, allow_reset=True):
        if now_utc is None:
            now_utc = time.time()
        if not self.log_path:
            return False
        log_dir = os.path.dirname(self.log_path)
        eff_uid = (self.expected_uid if self.expected_uid is not None else
                   (_resolve_expected_uid(None, same_user=True)
                    if self.require_self_owner else None))
        if log_dir:
            if eff_uid is not None or self.require_self_owner:
                if not _is_trusted_owned_real_dir(log_dir,
                                                  expected_uid=eff_uid):
                    return False
            elif not _is_real_dir(log_dir):
                return False
        try:
            lst = os.lstat(self.log_path)
        except OSError:
            return False
        if not stat.S_ISREG(lst.st_mode):
            return False
        if (_current_uid() is not None and eff_uid is not None and
                lst.st_uid != eff_uid):
            return False
        if self.frozen:
            return True
        f, st = _open_owned_regular_file(self.log_path, expected_uid=eff_uid)
        if f is None:
            return False
        st_size = st.st_size
        # A replaced log (new inode) or in-place O_TRUNC rewrite (changed head
        # prefix) restarts from the top even when longer than the old offset.
        cur_inode = st.st_ino or None
        replaced = (self.inode is not None and cur_inode is not None and
                    cur_inode != self.inode)
        try:
            with f:
                cur_prefix = f.read(min(_LOG_HEAD_PREFIX_BYTES, st_size))
                prefix_changed = bool(
                    self.offset > 0 and self.head_prefix and
                    (len(cur_prefix) < len(self.head_prefix) or
                     cur_prefix[:len(self.head_prefix)] != self.head_prefix))
                if st_size < self.offset or replaced or prefix_changed:
                    if not allow_reset:
                        self.frozen = True
                        return True
                    self.offset = 0
                    self.head_prefix = b''
                    old_trunc = self.truncation_count + 1
                    self.reset_state()
                    self.truncation_count = old_trunc
                    _append_bounded(self.events, (now_utc, 'LOG_TRUNCATED'))
                self.inode = cur_inode
                if st_size == self.offset:
                    return True
                f.seek(self.offset)
                chunk = f.read(_MAX_LOG_POLL_BYTES)
        except OSError:
            return False

        if not chunk:
            return True

        prev_offset = self.offset
        slice_bytes = chunk
        if self.skipping_oversize_line:
            nl_pos = slice_bytes.find(b'\n')
            if nl_pos < 0:
                self.offset += len(slice_bytes)
                return True
            skip = nl_pos + 1
            self.offset += skip
            self.skipping_oversize_line = False
            slice_bytes = slice_bytes[skip:]
            if not slice_bytes:
                return True

        # Hold back an unterminated trailing line until netsimd finishes it.
        last_nl = slice_bytes.rfind(b'\n')
        if last_nl < 0:
            if len(slice_bytes) >= _MAX_LOG_POLL_BYTES:
                self.offset += len(slice_bytes)
                if (prev_offset == len(self.head_prefix) and
                        len(self.head_prefix) < _LOG_HEAD_PREFIX_BYTES and
                        cur_prefix):
                    self.head_prefix = cur_prefix[:min(_LOG_HEAD_PREFIX_BYTES,
                                                       self.offset)]
                self.skipping_oversize_line = True
            return True
        self.offset += last_nl + 1
        if (prev_offset == len(self.head_prefix) and
                len(self.head_prefix) < _LOG_HEAD_PREFIX_BYTES and cur_prefix):
            self.head_prefix = cur_prefix[:min(_LOG_HEAD_PREFIX_BYTES, self.
                                               offset)]
        text = slice_bytes[:last_nl].decode('utf-8', errors='replace')
        for raw_line in text.split('\n'):
            if raw_line.endswith('\r'):
                raw_line = raw_line[:-1]
            self.feed_line(raw_line, now_utc=now_utc)
        return True

    def feed_line(self, raw_line, now_utc=None):
        if now_utc is None:
            now_utc = time.time()
        if len(raw_line) > _MAX_LOG_LINE_BYTES:
            return
        stripped = raw_line.strip()
        if not stripped:
            return
        level, ts, msg = parse_netsim_log_timestamp(raw_line, now_utc=now_utc)
        if ts is not None:
            self.last_timestamp = ts
        eff_ts = ts if ts is not None else (self.last_timestamp or now_utc)
        if self.min_ts is not None and eff_ts < self.min_ts:
            return

        if stripped == 'no_cli_ui: true,' or 'no_cli_ui: true' in msg:
            if not self.frontend_disabled:
                self.frontend_disabled = True
                _append_bounded(self.events, (eff_ts, 'FRONTEND_DISABLED'))
        if stripped == 'pcap: true,' or 'pcap: true' in msg:
            self.pcap_flag = True

        m_art = RE_NETSIM_ARTIFACTS_PATH.search(msg)
        if m_art:
            self.artifact_dir_logged = m_art.group(1)

        m_ver = RE_NETSIM_VERSION.search(msg)
        if m_ver:
            self.version = m_ver.group(1)

        m_ini = RE_NETSIM_INI_PATH.search(msg)
        if m_ini:
            self.ini_path_logged = m_ini.group(1)

        m_port = RE_NETSIM_GRPC_PORT.search(msg)
        if m_port:
            try:
                parsed_port = int(m_port.group(1))
                if 0 < parsed_port <= 0xFFFF:
                    self.grpc_port_logged = parsed_port
            except ValueError:
                pass

        m_wrote = RE_NETSIM_WROTE_INI.search(msg)
        if m_wrote:
            self.ini_path_logged = m_wrote.group(1)
            if self.ready_ts is None:
                self.ready_ts = eff_ts
            _append_bounded(self.events, (eff_ts, 'READY'))

        if 'Lock held by another process' in msg:
            self.client_mode_spawn = True
            _append_bounded(self.events, (eff_ts, 'CLIENT_MODE_SPAWN'))

        if ('Startup timeout reached' in msg or
                'No devices connected within' in msg):
            self.exit_reason_hint = 'startup_timeout'
        elif 'Idle timeout reached' in msg:
            self.exit_reason_hint = 'idle_timeout'
        elif 'Shutting down gracefully' in msg or 'Received signal' in msg:
            self.exit_reason_hint = 'sigterm_sigint'
        elif 'NetsimDaemon main loop exited' in msg:
            if not self.exit_reason_hint:
                self.exit_reason_hint = 'clean_unknown'

        m_guid = RE_NETSIM_GUID_PORT.search(
            msg) if 'for device' in msg else None
        if m_guid:
            try:
                guid_port = int(m_guid.group(1))
            except ValueError:
                guid_port = 0
            self.pending_guid_port = ((eff_ts, guid_port)
                                      if 0 < guid_port <= 0xFFFF else None)
            return

        m_c_head = RE_NETSIM_CONN_HEAD.search(msg)
        if m_c_head:
            _append_bounded(self.connections,
                            (eff_ts, _normalize_chip_kind(
                                m_c_head.group(1)), m_c_head.group(2).strip()))
            return

        m_c_1024 = RE_NETSIM_CONN_1024.search(msg)
        if m_c_1024:
            _append_bounded(self.connections,
                            (eff_ts, None, m_c_1024.group(2).strip()))
            return

        m_create = RE_NETSIM_CREATE_DEVICE.search(msg)
        if m_create:
            _append_bounded(self.created_devices,
                            (eff_ts, m_create.group(1).strip()))
            return

        m_add_head = RE_NETSIM_ADD_CHIP_HEAD.search(msg)
        if m_add_head:
            peer_port = None
            if (self.pending_guid_port and
                    abs(eff_ts - self.pending_guid_port[0]) <= 2.0):
                peer_port = self.pending_guid_port[1]
            _append_bounded(
                self.chip_adds, {
                    'ts': eff_ts,
                    'chip_id': m_add_head.group(1),
                    'kind': _normalize_chip_kind(m_add_head.group(2)),
                    'chip_name': m_add_head.group(3) or '',
                    'device_id': m_add_head.group(4),
                    'device_name': m_add_head.group(5).strip(),
                    'peer_port': peer_port,
                })
            return

        m_add_1024 = (RE_NETSIM_ADD_CHIP_1024.search(msg)
                      if 'to device' in msg else None)
        if m_add_1024:
            peer_port = None
            if (self.pending_guid_port and
                    abs(eff_ts - self.pending_guid_port[0]) <= 2.0):
                peer_port = self.pending_guid_port[1]
            _append_bounded(
                self.chip_adds, {
                    'ts': eff_ts,
                    'chip_id': None,
                    'kind': '?',
                    'chip_name': m_add_1024.group(1).strip(),
                    'device_id': None,
                    'device_name': m_add_1024.group(4).strip(),
                    'peer_port': peer_port,
                })
            return

        m_rem = RE_NETSIM_REMOVED_CHIP.search(msg)
        if m_rem:
            chip_id = m_rem.group(1) or m_rem.group(4)
            kind = m_rem.group(2) or m_rem.group(5)
            dev_id = m_rem.group(3) or m_rem.group(6)
            _append_bounded(
                self.chip_removals, {
                    'ts': eff_ts,
                    'chip_id': chip_id,
                    'kind': _normalize_chip_kind(kind),
                    'device_id': dev_id,
                })
            return

        m_empty = RE_NETSIM_EMPTY_DEVICE.search(msg)
        if m_empty:
            _append_bounded(self.empty_devices,
                            (eff_ts, m_empty.group(1).strip()))
            return

        m_wifi = RE_NETSIM_WIFI_ASSOC.search(msg)
        if m_wifi:
            _append_bounded(self.wifi_associations, eff_ts)


def _read_session_stats_json(stats_json_path,
                             cached_state=None,
                             not_before=0.0,
                             require_self_owner=False,
                             expected_uid: Optional[int] = None):
    """Reads netsim_session_stats.json with mtime caching and start-time floor."""
    if not stats_json_path or stats_json_path.endswith('.tmp'):
        return None, 0.0, cached_state, False
    eff_uid = (expected_uid if expected_uid is not None else
               (_resolve_expected_uid(None, same_user=True)
                if require_self_owner else None))
    stats_dir = os.path.dirname(stats_json_path)
    if stats_dir:
        if eff_uid is not None or require_self_owner:
            if not _is_trusted_owned_real_dir(stats_dir, expected_uid=eff_uid):
                return None, 0.0, cached_state, False
        elif not _is_real_dir(stats_dir):
            return None, 0.0, cached_state, False
    try:
        lst = os.lstat(stats_json_path)
    except OSError:
        return None, 0.0, cached_state, False
    if not stat.S_ISREG(lst.st_mode):
        return None, 0.0, cached_state, False
    if (_current_uid() is not None and eff_uid is not None and
            lst.st_uid != eff_uid):
        return None, 0.0, cached_state, False
    stats_mtime = lst.st_mtime
    if stats_mtime < not_before:
        return None, 0.0, cached_state, False
    if (cached_state and cached_state.get('mtime') == stats_mtime and
            cached_state.get('data') is not None):
        return cached_state['data'], stats_mtime, cached_state, False
    text, fst = _read_regular_file_bounded(
        stats_json_path,
        _MAX_SESSION_STATS_BYTES,
        reject_oversize=True,
        require_self_owner=require_self_owner,
        expected_uid=eff_uid)
    if fst is None or (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino):
        return None, 0.0, cached_state, False
    if text is None:
        return None, fst.st_mtime, cached_state, True
    try:
        parsed_json = json.loads(text)
        cached_state = {'mtime': fst.st_mtime, 'data': parsed_json}
        return parsed_json, fst.st_mtime, cached_state, False
    except (ValueError, TypeError, RecursionError):
        # RecursionError: nesting too deep for the decoder.
        return None, fst.st_mtime, cached_state, True


def resolve_session_stats_chips(stats_json_path,
                                unknown_add_timestamps,
                                joined_emulator_count=1,
                                cached_state=None,
                                not_before=0.0,
                                require_self_owner=False,
                                expected_uid: Optional[int] = None):
    """Resolves 1.0.23/1.0.24 kind-less AddChip events via netsim_session_stats.json (L2).

    A file older than not_before (the daemon's start) was left by an earlier
    daemon in the same artifact dir and is ignored.
    """
    parsed_json, stats_mtime, cached_state, read_err = _read_session_stats_json(
        stats_json_path,
        cached_state=cached_state,
        not_before=not_before,
        require_self_owner=require_self_owner,
        expected_uid=expected_uid)
    if read_err:
        return (cached_state.get('num_devices', 0) if cached_state else 0,
                cached_state.get('resolved') if cached_state else None,
                cached_state)

    if not isinstance(parsed_json, dict):
        return 0, None, cached_state

    # As in Rust, a value of the wrong JSON type counts as absent.
    device_stats = parsed_json.get('device_stats')
    if not isinstance(device_stats, list):
        device_stats = []
    radio_stats = parsed_json.get('radio_stats')
    if not isinstance(radio_stats, list):
        radio_stats = []
    num_devices = len(device_stats)
    if num_devices != 1 or joined_emulator_count != 1:
        cached_state['num_devices'] = num_devices
        cached_state['resolved'] = None
        return num_devices, None, cached_state

    first_device = device_stats[0]
    has_dev_id = isinstance(first_device, dict) and 'device_id' in first_device
    dev_id = first_device['device_id'] if has_dev_id else None
    kind_durations = {}
    for r in radio_stats:
        if not isinstance(r, dict):
            continue
        rid = r.get('device_id')
        # As in Rust, ids match as JSON values: 1, 1.0 and true all differ.
        if (has_dev_id and rid is not None and
                not (type(rid) is type(dev_id) and rid == dev_id)):
            continue
        raw_kind = r.get('kind')
        if not raw_kind or not isinstance(raw_kind, str):
            continue
        norm_k = _normalize_chip_kind(raw_kind)
        if not norm_k or norm_k in ('UNSPECIFIED', '?'):
            continue
        dur = _json_whole_seconds(r.get('duration_secs'))
        if norm_k not in kind_durations or dur > kind_durations[norm_k]:
            kind_durations[norm_k] = dur

    if not kind_durations:
        cached_state['num_devices'] = num_devices
        cached_state['resolved'] = None
        return num_devices, None, cached_state

    sorted_unk = sorted(float(t) for t in (unknown_add_timestamps or []))
    used_indices = set()
    resolved = {}
    for k in sorted(kind_durations.keys()):
        est_k = stats_mtime - kind_durations[k]
        matched_ts = None
        for idx, ev_ts in enumerate(sorted_unk):
            if idx in used_indices:
                continue
            if abs(ev_ts - est_k) <= 2.0:
                matched_ts = ev_ts
                used_indices.add(idx)
                break
        if matched_ts is not None:
            resolved[k] = (matched_ts, False)
        else:
            resolved[k] = (est_k, True)

    cached_state['num_devices'] = num_devices
    cached_state['resolved'] = resolved
    return num_devices, resolved, cached_state


def parse_netsim_capture_list(output):
    """Parses `netsim -v -p <port> capture list` output into (rows, error_msg)."""
    if output is None:
        return [], 'cli_failed'
    text = str(output)
    if any(err in text for err in (
            'UNIMPLEMENTED',
            'gRPC error',
            'Connection refused',
            'transport error',
            'Unable to find netsim ini file',
            'Failed to connect',
            'error=',
    )):
        return [], text.strip()
    rows = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('---') or line.startswith('ID'):
            continue
        if '|' in line:
            cols = [c.strip() for c in line.split('|')]
            # As in Rust: a u32 id, and u64 counters that default to 0.
            chip_id = (_parse_decimal(cols[0], _U32_MAX)
                       if len(cols) >= 7 else None)
            if chip_id is not None:
                rows.append({
                    'id': chip_id,
                    'device_name': cols[1],
                    'chip_kind': _normalize_chip_kind(cols[2]),
                    'raw_chip_kind': cols[2],
                    'state': cols[3].lower(),
                    'timestamp': cols[4],
                    'records': _parse_decimal(cols[5], _U64_MAX) or 0,
                    'size_bytes': _parse_decimal(cols[6], _U64_MAX) or 0,
                })
                continue
        parts = line.split()
        if len(parts) >= 6 and parts[0].isdigit() and parts[-1].isdigit():
            try:
                chip_id = int(parts[0])
                size_b = int(parts[-1])
                records = int(parts[-2])
                ts_tok = parts[-3]
                state_tok = parts[-4].lower()
                raw_kind = parts[-5]
                dev_name = ' '.join(parts[1:-5])
                if state_tok in ('on', 'off') and dev_name:
                    rows.append({
                        'id': chip_id,
                        'device_name': dev_name,
                        'chip_kind': _normalize_chip_kind(raw_kind),
                        'raw_chip_kind': raw_kind,
                        'state': state_tok,
                        'timestamp': ts_tok,
                        'records': records,
                        'size_bytes': size_b,
                    })
            except ValueError:
                pass
    return rows, None


def simulate_netsim_capture_filter(rows, patterns):
    """Simulates netsim CLI capture pattern matching across capture list rows."""
    if not patterns:
        return list(rows)
    up_pats = [str(p).upper() for p in patterns]
    matched = []
    for r in rows:
        hay_id = str(r.get('id', '')).upper()
        hay_dev = str(r.get('device_name', '')).upper()
        hay_kind = str(r.get('raw_chip_kind') or r.get('chip_kind', '')).upper()
        if all(p in hay_id or p in hay_dev or p in hay_kind for p in up_pats):
            matched.append(r)
    return matched


def select_safe_capture_patterns(rows, target_device_name, target_row=None):
    """Returns safe CLI filter patterns that match ONLY target_device_name's row(s), or None."""
    if not rows or not target_device_name:
        return None
    if target_row is not None:
        pats = [
            str(target_row['id']), target_device_name,
            str(target_row.get('raw_chip_kind') or target_row['chip_kind'])
        ]
        matched = simulate_netsim_capture_filter(rows, pats)
        if matched and all(m['device_name'] == target_device_name and
                           m['id'] == target_row['id'] for m in matched):
            return pats
        return None

    pats = [target_device_name]
    matched = simulate_netsim_capture_filter(rows, pats)
    if matched and all(m['device_name'] == target_device_name for m in matched):
        return pats
    return None


def select_cleanup_capture_patterns(rows,
                                    target_device_name,
                                    target_row,
                                    enabled_chip_ids=()):
    """Returns CLI filter patterns safe to turn off during cleanup, or None."""
    if not rows or not target_device_name or target_row is None:
        return None
    pats = [
        str(target_row['id']), target_device_name,
        str(target_row.get('raw_chip_kind') or target_row['chip_kind'])
    ]
    matched = simulate_netsim_capture_filter(rows, pats)
    if matched and all(
        (m['device_name'] == target_device_name and m['id'] == target_row['id']
        ) or m.get('state') == 'off' or m.get('id') in enabled_chip_ids
            for m in matched):
        return pats
    return None


def sanitize_netsim_filename(name):
    """Approximates netsim's capture filename sanitization (fallback after chip_id)."""
    if not name:
        return ''
    return ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in name)


def build_netsim_cli_args(grpc_port, instance_num, args):
    """Builds netsim CLI flags (`-v [-i <N>] -p <port> <args>...`)."""
    full_args = ['-v']
    if instance_num and int(instance_num) > DEFAULT_NETSIM_INSTANCE:
        full_args.extend(['-i', str(int(instance_num))])
    full_args.extend(['-p', str(grpc_port)])
    full_args.extend(str(a) for a in args)
    return full_args


RE_NETSIM_CLI_PREFIX = re.compile(
    r'netsim\s+[EWIDT]\s+\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+\S+\s+-\s+')


class NetsimPcapManager:
    """Manages opt-in pcap capture enabling, harvesting, zip rescue, and exit cleanup."""

    def __init__(self, pcap_dir=None, max_mb=500, out_dir='.'):
        self.enabled = pcap_dir is not None
        if pcap_dir == '':
            self.pcap_dir = os.path.join(os.path.expanduser(out_dir),
                                         'netsim_pcaps')
        elif pcap_dir is not None:
            self.pcap_dir = os.path.expanduser(pcap_dir)
        else:
            self.pcap_dir = None
        self.max_bytes = max(1, int(max_mb)) * 1024 * 1024
        self.total_copied_bytes = 0
        self.copied_files = {}
        self.rescued_zips = set()
        self.enabled_by_monitor = {}
        self.checked_emulators = set()
        self.checked_chip_counts = {}
        self.cli_error_counts = {}
        self.skipped_notes = []
        self.last_harvest_by_daemon = {}
        self.cap_reached = False

    def _record_skipped_note(self, note):
        parts = []
        for raw in str(note or '').splitlines():
            cleaned = RE_NETSIM_CLI_PREFIX.sub('', raw).strip()
            if cleaned:
                parts.append(cleaned)
        normalized = '; '.join(parts)
        if normalized and normalized not in self.skipped_notes:
            self.skipped_notes.append(normalized)

    @staticmethod
    def _emu_chip_sig(emu):
        return (
            len(emu.get('chip_attach_s') or
                ()) + int(emu.get('unknown_chip_count') or 0),
            frozenset(
                str(c) for c in (emu.get('chip_ids') or ()) if c is not None))

    def forget_daemon(self, d_key):
        self.last_harvest_by_daemon.pop(d_key, None)
        self.checked_emulators = {
            item for item in self.checked_emulators if item[0] != d_key
        }
        self.checked_chip_counts = {
            k: v for k, v in self.checked_chip_counts.items() if k[0] != d_key
        }
        self.cli_error_counts = {
            k: v for k, v in self.cli_error_counts.items() if k[0] != d_key
        }
        self.enabled_by_monitor = {
            k: v for k, v in self.enabled_by_monitor.items() if k[0] != d_key
        }

    @staticmethod
    def _cli_target_for_daemon(daemon_info):
        ini_path = daemon_info.get('ini_path')
        ini_dir = (os.path.dirname(ini_path)
                   if ini_path and os.path.dirname(ini_path) else None)
        inst_num = ((extract_ini_instance_num(ini_path) if ini_path else None)
                    or daemon_info.get('instance_num'))
        return ini_dir, inst_num

    def _invoke_cli(self,
                    netsim_bin,
                    grpc_port,
                    args,
                    ini_dir=None,
                    instance_num=None):
        # A sibling of the daemon's executable was vetted when the daemon was
        # first seen. It is vetted again before every run: below a sticky
        # directory such as /tmp, its directory can be removed by its owner
        # and recreated by anyone.
        if (netsim_bin and os.path.dirname(netsim_bin) and
                not _is_trusted_executable(netsim_bin)):
            return None
        if ini_dir is None and instance_num is None:
            return self._run_netsim_cli(netsim_bin, grpc_port, args)
        return self._run_netsim_cli(netsim_bin,
                                    grpc_port,
                                    args,
                                    ini_dir=ini_dir,
                                    instance_num=instance_num)

    def _run_netsim_cli(self,
                        netsim_bin,
                        grpc_port,
                        args,
                        timeout=5.0,
                        ini_dir=None,
                        instance_num=None):
        # `capture get` makes netsimd panic; `capture` subcommands also ignore
        # `-p <grpc_port>` and look up `<TMPDIR>/netsim[_<N>].ini`, so refuse
        # `capture` calls without a verified `ini_dir` and always pin
        # `NETSIM_INSTANCE` (`os_utils.rs:get_instance_with_env` prioritizes
        # `NETSIM_INSTANCE` above `-i <N>`).
        if args and args[0] == 'capture':
            if list(args[:2]) == ['capture', 'get'] or not ini_dir:
                return None
        cmd = [netsim_bin or 'netsim'] + build_netsim_cli_args(
            grpc_port, instance_num, args)
        inst = (int(instance_num) if
                (isinstance(instance_num, int) and
                 not isinstance(instance_num, bool) and instance_num > 0) else
                DEFAULT_NETSIM_INSTANCE)
        env = dict(os.environ)
        if ini_dir:
            env['TMPDIR'] = ini_dir
        env['NETSIM_INSTANCE'] = str(inst)
        try:
            res = subprocess.run(cmd,
                                 stdin=subprocess.DEVNULL,
                                 capture_output=True,
                                 encoding='utf-8',
                                 errors='replace',
                                 timeout=timeout,
                                 env=env,
                                 check=False)
            out = (res.stdout or '') + ('\n' + res.stderr if res.stderr else '')
            return out
        except (OSError, subprocess.SubprocessError):
            return None

    def poll_and_manage_daemon(self,
                               daemon_info,
                               joined_emulators,
                               now_mono=None,
                               shutting_down=False):
        if now_mono is None:
            now_mono = time.monotonic()
        interval = 1.0 if shutting_down else 10.0
        d_key = daemon_info.get('daemon_key')
        live_emulators = [
            emu for emu in joined_emulators
            if not str(emu.get('state', '')).startswith('FAILED') and
            emu.get('state') not in ('DISCONNECTED', 'DISABLED')
        ]
        named_live_emulators = [
            emu for emu in live_emulators if emu.get('device_name')
        ]
        ini_dir, inst_num = self._cli_target_for_daemon(daemon_info)
        art_dir = daemon_info.get('artifact_dir')
        d_same = bool(daemon_info.get('same_user'))
        eff_uid = _resolve_expected_uid(daemon_info.get('uid'),
                                        same_user=d_same)
        art_dir_trusted = (
            bool(art_dir) and not NetsimTracker._is_disallowed_daemon_art_dir(
                art_dir, True, expected_uid=eff_uid) and
            (not os.path.lexists(art_dir) or
             _is_trusted_owned_real_dir(art_dir, expected_uid=eff_uid)))
        cli_allowed = (self.enabled and not daemon_info.get('exited') and
                       d_same and bool(named_live_emulators) and
                       bool(ini_dir) and
                       _is_trusted_ini_path(daemon_info.get('ini_path'),
                                            expected_uid=eff_uid) and
                       art_dir_trusted)
        has_unmanaged_emu = (cli_allowed and any(
            ((d_key, emu['device_name']) not in self.checked_emulators or
             self.checked_chip_counts.get(
                 (d_key, emu.get('serial') or '',
                  emu['device_name'])) != self._emu_chip_sig(emu)) and
            self.cli_error_counts.get((d_key, emu['device_name']), 0) < 2
            for emu in named_live_emulators))
        last_harvest = self.last_harvest_by_daemon.get(d_key, 0.0)
        if (last_harvest > 0 and (now_mono - last_harvest) < interval and
                not shutting_down and not has_unmanaged_emu):
            return
        self.last_harvest_by_daemon[d_key] = now_mono

        grpc_port = daemon_info.get('grpc_port')
        netsim_bin = daemon_info.get('netsim_cli_bin') or 'netsim'
        rows = []
        if cli_allowed and grpc_port:
            out = self._invoke_cli(netsim_bin,
                                   grpc_port, ['capture', 'list'],
                                   ini_dir=ini_dir,
                                   instance_num=inst_num)
            parsed_rows, err = parse_netsim_capture_list(out)
            if not err:
                for emu in named_live_emulators:
                    key = (d_key, emu['device_name'])
                    chip_key = (d_key, emu.get('serial') or
                                '', emu['device_name'])
                    self.checked_emulators.add(key)
                    self.checked_chip_counts[chip_key] = self._emu_chip_sig(emu)
                    self.cli_error_counts.pop(key, None)
                rows = parsed_rows
                daemon_info['capture_rows'] = rows
            else:
                for emu in named_live_emulators:
                    key = (d_key, emu['device_name'])
                    self.cli_error_counts[key] = (
                        self.cli_error_counts.get(key, 0) + 1)
                detail = ('netsim CLI unavailable or timed out'
                          if out is None else err)
                self._record_skipped_note(
                    f"Skipped capture list for port {grpc_port}: {detail}")

        if rows:
            d_key = daemon_info['daemon_key']
            for emu in named_live_emulators:
                dev_name = emu['device_name']
                dev_rows = [r for r in rows if r['device_name'] == dev_name]
                for r in dev_rows:
                    if r['state'] == 'off':
                        pats = select_safe_capture_patterns(rows,
                                                            dev_name,
                                                            target_row=r)
                        if pats is None:
                            self._record_skipped_note(
                                f"Skipped auto-enable for '{dev_name}' (chip {r['id']}) "
                                "due to ambiguous CLI substring match")
                            continue
                        patch_out = self._invoke_cli(
                            netsim_bin,
                            grpc_port, ['capture', 'patch', 'on'] + pats,
                            ini_dir=ini_dir,
                            instance_num=inst_num)
                        _, err = parse_netsim_capture_list(patch_out)
                        if not err:
                            self.enabled_by_monitor[(d_key, r['id'])] = {
                                'netsim_bin': netsim_bin,
                                'grpc_port': grpc_port,
                                'ini_path': daemon_info.get('ini_path'),
                                'ini_dir': ini_dir,
                                'instance_num': inst_num,
                                'device_name': dev_name,
                                'row': r,
                            }
                        else:
                            detail = ('netsim CLI unavailable or timed out'
                                      if patch_out is None else err)
                            self._record_skipped_note(
                                f"Skipped auto-enable for '{dev_name}' (chip {r['id']}): {detail}"
                            )

        self.harvest_daemon_files(daemon_info, joined_emulators)

    def harvest_daemon_files(self, daemon_info, joined_emulators):
        art_dir = daemon_info.get('artifact_dir')
        if not _is_real_dir(art_dir):
            return
        pcaps_dir = os.path.join(art_dir, 'pcaps')
        live_bytes = 0
        live_files = 0
        if _is_real_dir(pcaps_dir):
            try:
                for fname in os.listdir(pcaps_dir):
                    if not fname.endswith('.pcap'):
                        continue
                    fpath = os.path.join(pcaps_dir, fname)
                    try:
                        # As in Rust (DirEntry::metadata), a symlink counts as
                        # itself; its target is not looked at.
                        st = os.lstat(fpath)
                        live_bytes += st.st_size
                        live_files += 1
                    except OSError:
                        continue
            except OSError:
                pass
        daemon_info['pcap_live_bytes'] = live_bytes
        daemon_info['pcap_live_files'] = live_files

        if not self.enabled or not self.pcap_dir:
            return
        # The artifact dir is taken from the daemon's environment. Copy out of
        # it only when the daemon and the directory both belong to the
        # monitor's user, and never through symlinks (_copy_regular_file).
        eff_uid = _resolve_expected_uid(daemon_info.get('uid'), same_user=True)
        if (not daemon_info.get('same_user') or
                not _is_trusted_owned_real_dir(art_dir, expected_uid=eff_uid)):
            return

        pid = daemon_info.get('pid') or 0
        start_epoch = int(daemon_info.get('start_wall') or 0)
        daemon_subdir = os.path.join(self.pcap_dir, f"{pid}-{start_epoch}")

        try:
            for fname in os.listdir(art_dir):
                if fname.startswith('netsim_artifacts_') and fname.endswith(
                        '.zip'):
                    zpath = os.path.join(art_dir, fname)
                    dst = os.path.join(daemon_subdir, fname)
                    if dst not in self.rescued_zips:
                        try:
                            st = os.lstat(zpath)
                            if not stat.S_ISREG(st.st_mode):
                                continue
                            if self.total_copied_bytes + st.st_size <= self.max_bytes:
                                os.makedirs(daemon_subdir, exist_ok=True)
                                copied = _copy_regular_file(
                                    zpath, dst, expected_uid=eff_uid)
                                if copied is None:
                                    continue
                                self.rescued_zips.add(dst)
                                self.total_copied_bytes += copied.st_size
                                self.copied_files[dst] = copied.st_size
                            else:
                                self.cap_reached = True
                        except OSError:
                            pass
        except OSError:
            pass

        if not _is_trusted_owned_real_dir(pcaps_dir, expected_uid=eff_uid):
            return

        chip_to_serial = {}
        name_to_serial = {}
        for emu in joined_emulators:
            serial = emu.get('serial') or 'unassigned'
            dev_name = emu.get('device_name') or ''
            if dev_name:
                safe_name = sanitize_netsim_filename(dev_name)
                if safe_name in name_to_serial and name_to_serial[
                        safe_name] != serial:
                    name_to_serial[safe_name] = 'shared'
                else:
                    name_to_serial[safe_name] = serial
            for cid in emu.get('chip_ids', ()):
                if cid is not None:
                    cid_s = str(cid)
                    if cid_s in chip_to_serial and chip_to_serial[
                            cid_s] != serial:
                        chip_to_serial[cid_s] = 'shared'
                    else:
                        chip_to_serial[cid_s] = serial
        if len(joined_emulators) == 1:
            default_serial = joined_emulators[0].get('serial') or 'unassigned'
        else:
            default_serial = 'shared'

        try:
            for fname in sorted(os.listdir(pcaps_dir)):
                if not fname.endswith('.pcap'):
                    continue
                src = os.path.join(pcaps_dir, fname)
                try:
                    st = os.lstat(src)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                target_serial = default_serial
                m_pcap = re.match(r'^netsim-(\d+)-(.+)-([A-Za-z0-9_]+)\.pcap$',
                                  fname)
                if m_pcap:
                    cid_str, safe_dev, _ = m_pcap.groups()
                    if cid_str in chip_to_serial:
                        target_serial = chip_to_serial[cid_str]
                    elif safe_dev in name_to_serial:
                        target_serial = name_to_serial[safe_dev]

                dst_dir = os.path.join(daemon_subdir, target_serial)
                dst = os.path.join(dst_dir, fname)
                prev_meta = self.copied_files.get(dst)
                if prev_meta and prev_meta == (st.st_size, st.st_mtime_ns):
                    continue
                prev_size = prev_meta[0] if isinstance(
                    prev_meta, tuple) else (prev_meta or 0)
                base = max(0, self.total_copied_bytes - prev_size)
                if base + st.st_size > self.max_bytes:
                    self.cap_reached = True
                    note = f"Reached --netsim-pcap-max-mb cap ({self.max_bytes // (1024 * 1024)} MB); skipped {fname}"
                    if note not in self.skipped_notes:
                        self.skipped_notes.append(note)
                    continue
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    copied = _copy_regular_file(src, dst, expected_uid=eff_uid)
                    if copied is None:
                        continue
                    self.copied_files[dst] = (copied.st_size, st.st_mtime_ns)
                    self.total_copied_bytes = base + copied.st_size
                except OSError:
                    pass
        except OSError:
            pass

    def restore_captures_on_exit(self, daemons_by_key):
        """Turns off the captures this monitor turned on.

        A chip whose daemon INI no longer passes _is_trusted_ini_path() is
        left as it is and named in a skipped note.
        """
        rows_by_daemon = {}
        enabled_ids_by_daemon = {}
        for d_key, chip_id in self.enabled_by_monitor:
            enabled_ids_by_daemon.setdefault(d_key, set()).add(chip_id)
        for (d_key, chip_id), info in sorted(self.enabled_by_monitor.items(),
                                             key=lambda kv: kv[0]):
            daemon_info = daemons_by_key.get(d_key)
            if daemon_info and daemon_info.get('exited'):
                continue
            ini_path = info.get('ini_path') or (daemon_info.get('ini_path')
                                                if daemon_info else None)
            ini_trusted = bool(ini_path) and _is_trusted_ini_path(ini_path)
            netsim_bin = info['netsim_bin']
            grpc_port = info['grpc_port']
            ini_dir = info.get('ini_dir') or os.path.dirname(ini_path or '')
            inst_num = info.get('instance_num')
            if ini_trusted and d_key not in rows_by_daemon:
                out = self._invoke_cli(netsim_bin,
                                       grpc_port, ['capture', 'list'],
                                       ini_dir=ini_dir,
                                       instance_num=inst_num)
                rows, err = parse_netsim_capture_list(out)
                rows_by_daemon[d_key] = None if err else rows
                # The list call can take seconds.
                ini_trusted = _is_trusted_ini_path(ini_path)
            if not ini_trusted:
                self._record_skipped_note(
                    f"Left capture on for '{info['device_name']}' "
                    f"(chip {chip_id}): netsim INI is missing or no longer "
                    "trusted")
                continue
            rows = rows_by_daemon[d_key]
            if not rows:
                continue
            pats = select_cleanup_capture_patterns(
                rows,
                info['device_name'],
                info['row'],
                enabled_chip_ids=enabled_ids_by_daemon.get(d_key, ()))
            if pats:
                self._invoke_cli(netsim_bin,
                                 grpc_port, ['capture', 'patch', 'off'] + pats,
                                 ini_dir=ini_dir,
                                 instance_num=inst_num)

    def cleanup(self, daemons_by_key):
        self.restore_captures_on_exit(daemons_by_key)
        for d_info in daemons_by_key.values():
            if d_info.get('exited') or d_info.get('client_mode_recorded'):
                continue
            self.harvest_daemon_files(
                d_info,
                d_info.get('all_joined_emulators') or
                d_info.get('joined_emulators', []))


def _parse_host_port_endpoint(ep):
    """Splits '127.0.0.1:41935' or '[::1]:41935' into (host, port_int)."""
    if not ep or ':' not in ep:
        return '', None
    host, port_str = ep.rsplit(':', 1)
    host = host.strip('[]')
    try:
        return host, int(port_str)
    except ValueError:
        return host, None


def _is_loopback_host(host):
    if not host:
        return False
    h = host.strip('[]').lower()
    return (h in ('127.0.0.1', '::1', '::ffff:127.0.0.1', 'localhost') or
            h.startswith('127.'))


def _loopback_endpoint_port(ep):
    """Port of a host:port / [host]:port endpoint if its host is loopback."""
    host, port = _parse_host_port_endpoint(ep)
    if port is None or not 0 < port <= 0xFFFF or not _is_loopback_host(host):
        return None
    return port


def _parse_pid(token):
    try:
        return int(token)
    except ValueError:
        return None


def _lsof_loopback_rows(lsof_out):
    """(pid, local_port, remote_port) of loopback connections in lsof output.

    Reads `lsof -F pn` output (p<pid> / n<src>-><dst> lines). lsof itself
    applies the ESTABLISHED filter (-sTCP:ESTABLISHED).
    """
    rows = []
    current_pid = None
    for raw_line in lsof_out.splitlines():
        line = raw_line.strip()
        if line.startswith('p'):
            current_pid = _parse_pid(line[1:])
        elif line.startswith('n') and current_pid is not None and '->' in line:
            src, dst = line[1:].split('->', 1)
            src_port = _loopback_endpoint_port(src)
            dst_port = _loopback_endpoint_port(dst)
            if src_port is not None and dst_port is not None:
                rows.append((current_pid, src_port, dst_port))
    return rows


def _netstat_loopback_rows(netstat_out):
    """(pid, local_port, remote_port) of ESTABLISHED loopback TCP rows.

    Reads Windows `netstat -ano` rows (TCP <local> <foreign> <state> <pid>).
    netstat localizes the State column, so non-English Windows yields no rows.
    """
    rows = []
    for raw_line in netstat_out.splitlines():
        parts = raw_line.split()
        if (len(parts) < 5 or parts[0].upper() != 'TCP' or
                parts[3].upper() != 'ESTABLISHED'):
            continue
        pid = _parse_pid(parts[4])
        local_port = _loopback_endpoint_port(parts[1])
        remote_port = _loopback_endpoint_port(parts[2])
        if None not in (pid, local_port, remote_port):
            rows.append((pid, local_port, remote_port))
    return rows


def _empty_port_map(target_ports):
    return {port: [] for port in target_ports if port}


def _group_loopback_conns(rows, target_ports, pid_to_serial):
    """Groups (pid, local_port, remote_port) rows by target remote port.

    Keeps emulator PIDs only and drops duplicate rows.
    """
    port_to_conns = _empty_port_map(target_ports)
    seen = set()
    for pid, local_port, remote_port in rows:
        serial = pid_to_serial.get(pid)
        conns = port_to_conns.get(remote_port)
        if serial is None or conns is None:
            continue
        if (remote_port, pid, local_port) not in seen:
            seen.add((remote_port, pid, local_port))
            conns.append({
                'serial': serial,
                'pid': pid,
                'peer_port': local_port,
            })
    return port_to_conns


def parse_lsof_established_loopback(output, target_ports, pid_to_serial):
    """Loopback connections in `lsof -n -P -F pn` output, keyed by port."""
    return _group_loopback_conns(_lsof_loopback_rows(output or ''),
                                 target_ports, pid_to_serial)


def parse_netstat_established_loopback(output, target_ports, pid_to_serial):
    """Loopback connections in Windows `netstat -ano` output, keyed by port."""
    return _group_loopback_conns(_netstat_loopback_rows(output or ''),
                                 target_ports, pid_to_serial)


def _psutil_loopback_rows(pid):
    """(pid, local_port, remote_port) of a process's loopback TCP sockets.

    Only ESTABLISHED sockets count. psutil errors such as AccessDenied
    propagate.
    """
    rows = []
    for c in psutil.Process(pid).net_connections(kind='tcp'):
        if (c.status == 'ESTABLISHED' and c.laddr and c.raddr and
                _is_loopback_host(c.laddr[0]) and
                _is_loopback_host(c.raddr[0]) and 0 < c.laddr[1] <= 0xFFFF and
                0 < c.raddr[1] <= 0xFFFF):
            rows.append((pid, c.laddr[1], c.raddr[1]))
    return rows


# Upper bound for one lsof / netstat run on the sampling path.
_SOCKET_CLI_TIMEOUT_S = 1.5


def _run_cli_with_timeout(args, timeout=_SOCKET_CLI_TIMEOUT_S):
    """Returns the stdout of `args`, ignoring its exit status.

    Returns None if the tool cannot start or does not finish within
    `timeout`; subprocess.run() kills it in that case. Only ASCII fields are
    parsed, so decoding with the locale codec and errors='replace' is enough.
    """
    try:
        return subprocess.run(args,
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              text=True,
                              errors='replace',
                              timeout=timeout,
                              check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _cli_loopback_rows(os_family, pids, run):
    """Loopback connection rows of `pids` from netstat (Windows) or lsof.

    `run(args)` returns the tool's stdout, or None if it failed; this function
    returns None on failure so callers can distinguish tool failure from an
    empty socket list.
    """
    if os_family == 'win32':
        # No '-p tcp': it lists IPv4 only, and emulators reach netsimd over
        # [::1].
        out = run(['netstat', '-ano'])
        return None if out is None else _netstat_loopback_rows(out)
    pid_list = ','.join(str(p) for p in sorted(pids))
    out = run([
        'lsof', '-n', '-P', '-F', 'pn', '-iTCP', '-sTCP:ESTABLISHED', '-a',
        '-p', pid_list
    ])
    return None if out is None else _lsof_loopback_rows(out)


# 1s slack for /proc stat starttime tick rounding vs. millisecond log timestamps.
_LOG_FLOOR_SLACK_S = 1.0


class NetsimTracker:
    """Tracks netsimd daemon lifecycle outside AVDs, per-emulator attach latency, and pcaps."""

    def __init__(self,
                 pcap_dir=None,
                 pcap_max_mb=500,
                 enable_log=True,
                 out_dir='.',
                 os_family=None):
        self.enable_log = enable_log
        self.pcap_mgr = NetsimPcapManager(pcap_dir=pcap_dir,
                                          max_mb=pcap_max_mb,
                                          out_dir=out_dir)
        self.daemons = {}
        self.stale_inis = {}
        self.timeline = []
        self.emulator_records = {}
        self.restart_count = 0
        self._last_active_by_ini = {}
        self._last_socket_conns = {}
        self.socket_os_family = os_family
        self.socket_cli_runner = _run_cli_with_timeout
        # When set, INI discovery scans only these dirs (tests).
        self.custom_ini_dirs = []
        self._cleaned_up = False

    def _record_event(self, ts, text):
        entry = (ts, text)
        if not self.timeline or self.timeline[-1][1] != text:
            _append_bounded(self.timeline, entry)

    def record_tracking_failure(self, ts, exc):
        """Notes in the timeline that a netsim tracking pass raised `exc`.

        Names the exception type and the innermost frame only: the message
        can quote text read from another process's files.
        """
        frames = traceback.extract_tb(exc.__traceback__)
        where = f" in {frames[-1].name}:{frames[-1].lineno}" if frames else ''
        self._record_event(ts, f"TRACKING_ERROR({type(exc).__name__}{where})")

    def _read_proc_locks(self):
        try:
            if os.path.exists('/proc/locks'):
                with open('/proc/locks',
                          'r',
                          encoding='utf-8',
                          errors='replace') as f:
                    return parse_proc_locks(f.read())
        except OSError:
            pass
        return {}

    def _discover_ini_files(self, daemon_environs):
        """netsim INIs in the monitor's and each daemon's discovery dirs.

        Returns one path per file, spelled as first seen: macOS /tmp and
        $TMPDIR are symlinks, so one INI can be reachable twice.
        custom_ini_dirs, when set, replaces the search.
        """
        if self.custom_ini_dirs:
            dirs = _extend_unique([], self.custom_ini_dirs)
        else:
            if self.socket_os_family is None:
                dirs = host_discovery_dirs(os.environ, uid=_current_uid())
                for env in daemon_environs:
                    _extend_unique(dirs, get_netsim_discovery_dirs(env or {}))
            else:
                dirs = host_discovery_dirs(os.environ,
                                           target_os=self.socket_os_family,
                                           uid=_current_uid())
                for env in daemon_environs:
                    _extend_unique(
                        dirs,
                        get_netsim_discovery_dirs(
                            env or {}, target_os=self.socket_os_family))
        seen = set()
        ini_files = []
        for d in dirs:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for name in names:
                if extract_ini_instance_num(name) is None:
                    continue
                path = os.path.join(d, name)
                try:
                    lst = os.lstat(path)
                    if (not stat.S_ISREG(lst.st_mode) or
                            lst.st_size > _MAX_INI_BYTES):
                        continue
                except OSError:
                    continue
                key = os.path.normcase(os.path.join(os.path.realpath(d), name))
                if key not in seen:
                    seen.add(key)
                    ini_files.append(path)
        return sorted(ini_files)

    def _match_inis_to_daemons(self, inis, live_keys):
        """Pairs each INI with at most one live daemon and vice versa.

        Returns the matched daemon key (or None) for each of `inis`, taking
        the strongest evidence first:
        1. Holders of the INI's lock files (Linux /proc/locks); an INI with
           holders pairs only through them. Then pid=, if that daemon started
           before the INI was written, which rejects reused PIDs.
        2. The daemon that already owns the INI (earlier tick or its log).
        3. For INIs without holders or pid= (older netsim), an unowned daemon
           of the same instance that started before the INI was written.
           Newer daemons choose first and take the oldest such INI, because
           an older daemon's window also covers INIs written after it.

        Mirrors NetsimTracker::match_inis_to_daemons() in
        rust/src/netsim/tracker.rs.
        """
        live_keys = sorted(
            live_keys,
            key=lambda k:
            (not bool(self.daemons.get(k, {}).get('same_user', True)), k))
        live_by_pid = {}
        for key in live_keys:
            live_by_pid.setdefault(key[0], key)

        def written_after_start(ini, key):
            d = self.daemons.get(key)
            if d is None:
                return False
            # An unknown start time (create_time 0) or mtime accepts the INI.
            return (not d.get('create_time') or ini['mtime'] is None or
                    ini['mtime'] >= d['start_wall'] - _INI_MTIME_SLACK_S)

        my_uid = _current_uid()

        def uid_compatible(ini, key):
            d_cand = self.daemons.get(key, {})
            d_same = bool(d_cand.get('same_user', True))
            exp_uid = _resolve_expected_uid(d_cand.get('uid'),
                                            same_user=d_same,
                                            monitor_uid=my_uid)
            file_uid = ini.get('file_uid')
            if file_uid is not None and exp_uid is not None:
                return file_uid == exp_uid
            if my_uid in (None, 0) or ini.get('owned_by_self') is None:
                return True
            return ini['owned_by_self'] == d_same

        matched = [None] * len(inis)
        claimed = set()
        for i, ini in enumerate(inis):
            for pid in ini['holders']:
                key = live_by_pid.get(pid)
                if key is not None and key not in claimed:
                    matched[i] = key
                    claimed.add(key)
                    break
        for i, ini in enumerate(inis):
            if matched[i] is not None or ini['holders']:
                continue
            key = live_by_pid.get(ini['pid'])
            if (key is not None and key not in claimed and
                    uid_compatible(ini, key) and written_after_start(ini, key)):
                matched[i] = key
                claimed.add(key)
        for i, ini in enumerate(inis):
            if matched[i] is not None or ini['holders']:
                continue
            for key in live_keys:
                d_cand = self.daemons.get(key, {})
                owned = d_cand.get('ini_path')
                if (key not in claimed and uid_compatible(ini, key) and
                        not d_cand.get('client_mode_recorded') and owned and
                        _same_file(owned, ini['path'])):
                    matched[i] = key
                    claimed.add(key)
                    break

        unowned = [
            key for key in live_keys
            if key not in claimed and key in self.daemons and
            not self.daemons[key].get('client_mode_recorded') and
            not self.daemons[key].get('ini_path')
        ]
        # Stable, so equal start times keep the sorted key order.
        unowned.sort(key=lambda k: (bool(self.daemons[k].get('same_user', True)
                                        ), self.daemons[k]['start_wall']),
                     reverse=True)
        for key in unowned:
            instance = self.daemons[key].get('instance_num')
            if instance is None:
                instance = DEFAULT_NETSIM_INSTANCE
            candidates = [
                i for i, ini in enumerate(inis)
                if matched[i] is None and not ini['holders'] and
                ini['pid'] is None and ini['instance'] == instance and
                written_after_start(ini, key) and uid_compatible(ini, key)
            ]
            if candidates:
                oldest = min(candidates,
                             key=lambda i: math.inf
                             if inis[i]['mtime'] is None else inis[i]['mtime'])
                matched[oldest] = key
        return matched

    def _classify_daemon_spawner(self, ppid, all_emu_group_pids):
        if not ppid or ppid <= 1:
            return 'STANDALONE'
        for group_pids in all_emu_group_pids.values():
            if ppid in group_pids:
                return 'emulator'
        try:
            parent = psutil.Process(ppid)
            pname = (parent.name() or '').lower()
            if 'qemu' in pname or 'crosvm' in pname:
                return 'qemu'
            if 'emulator' in pname or 'launcherloop' in pname:
                return 'emulator'
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass
        return 'STANDALONE'

    def _collect_loopback_grpc_sockets(self,
                                       target_ports,
                                       all_emu_group_pids,
                                       daemon_pids,
                                       os_family=None,
                                       run_cli=None,
                                       use_cache_on_err=True):
        """Emulator-group connections to each target port, keyed by port.

        Sockets come from psutil. lsof (macOS) or netstat (Windows) is asked
        as well only when psutil raised AccessDenied, not merely because
        nothing is connected yet: the 250 ms CONNECTING sub-poll would
        otherwise start a tool on every poll. Linux has no such fallback,
        because lsof reads the same /proc files as psutil.
        """
        pid_to_serial = {
            pid: serial for serial, pids in all_emu_group_pids.items()
            for pid in pids
            if pid not in daemon_pids
        }
        if not _empty_port_map(target_ports) or not pid_to_serial:
            self._last_socket_conns = {}
            return _empty_port_map(target_ports)
        rows = []
        denied_pids = set()
        for pid in pid_to_serial:
            try:
                rows.extend(_psutil_loopback_rows(pid))
            except psutil.AccessDenied:
                denied_pids.add(pid)
            except psutil.NoSuchProcess:
                continue
        os_family = os_family or self.socket_os_family or _os_family()
        run_cli = run_cli or self.socket_cli_runner
        cli_failed = False
        if denied_pids and os_family != 'linux':
            cli_rows = _cli_loopback_rows(os_family, pid_to_serial, run_cli)
            if cli_rows is None:
                cli_failed = True
            else:
                rows.extend(cli_rows)
        grouped = _group_loopback_conns(rows, target_ports, pid_to_serial)
        if cli_failed:
            if not use_cache_on_err:
                return None
            # Retain cached sockets only for still-live denied PIDs across transient
            # lsof/netstat timeouts; full eviction requires an empty CLI result [].
            for port in target_ports:
                for c in self._last_socket_conns.get(port, ()):
                    if (c['pid'] in denied_pids and
                            pid_to_serial.get(c['pid']) == c['serial']):
                        grouped.setdefault(port, []).append(c)
        self._last_socket_conns = {p: list(cs) for p, cs in grouped.items()}
        return grouped

    def has_any_connecting(self):
        if not any(not d.get('exited') and d.get('grpc_port')
                   for d in self.daemons.values()):
            return False
        return any(
            rec.get('state') == 'CONNECTING' and not rec.get('no_netsim') and
            rec.get('first_transport_ts') is None
            for rec in self.emulator_records.values())

    def poll_sockets_fast(self, now_wall=None):
        """Lightweight 250ms socket sub-poll while any emulator is CONNECTING."""
        if now_wall is None:
            now_wall = time.time()
        active_ports = {
            d.get('grpc_port')
            for d in self.daemons.values()
            if not d.get('exited') and d.get('grpc_port')
        }
        if not active_ports:
            return
        daemon_pids = {
            d['pid'] for d in self.daemons.values() if not d.get('exited')
        }
        _, all_emu_group_pids, _, _ = _snapshot_process_state()
        port_to_conns = self._collect_loopback_grpc_sockets(
            active_ports,
            all_emu_group_pids,
            daemon_pids,
            use_cache_on_err=False)
        if not port_to_conns:
            return
        for d in self.daemons.values():
            if d.get('exited') or not d.get('grpc_port'):
                continue
            conns = port_to_conns.get(d['grpc_port'], [])
            for c in conns:
                serial = c['serial']
                for rec in self.emulator_records.values():
                    if (self._is_settled(rec) or rec.get('no_netsim') or
                            rec.get('state') != 'CONNECTING'):
                        continue
                    if rec.get('serial') == serial and rec.get(
                            'first_transport_ts') is None:
                        rec['first_transport_ts'] = now_wall
                        if rec.get('worker_start_ts'):
                            rec['netsim_connect_s'] = max(
                                0.0, now_wall - rec['worker_start_ts'])

    @staticmethod
    def _record_missed_tick(rec, adb_live, now_mono):
        """Records a tick on which a known emulator is missing.

        Returns whether it has exited. While adb reports the serial live the
        emulator is held at its last state and any miss run is cleared. It
        exits after _SETTLE_AFTER_MISSED_TICKS consecutive ticks missing from
        both, dated to the first of them ('missing_since_mono').
        """
        if adb_live:
            rec['missing_since_mono'] = None
            rec['missed_ticks'] = 0
            return False
        if rec.get('missing_since_mono') is None:
            rec['missing_since_mono'] = now_mono
        missed = rec.get('missed_ticks', 0)
        if missed < _SETTLE_AFTER_MISSED_TICKS:
            missed += 1
            rec['missed_ticks'] = missed
        return missed >= _SETTLE_AFTER_MISSED_TICKS

    @staticmethod
    def _is_settled(rec):
        state = rec.get('state') or ''
        return state == 'DISCONNECTED' or state.startswith('FAILED')

    @staticmethod
    def _is_exited_disabled(rec):
        return bool(
            rec.get('no_netsim') and
            rec.get('missed_ticks', 0) >= _SETTLE_AFTER_MISSED_TICKS)

    @staticmethod
    def _merge_prior_run(rec, prev_rec, now_mono):
        """Carries cumulative session counters from `prev_rec` into `rec`."""
        if not prev_rec:
            return
        prior_disc = prev_rec.get('disconnect_count', 0)
        prior_total = prev_rec.get('connected_total_s', 0.0)
        if prev_rec.get('state') in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
            prior_disc += 1
            end_mono = (prev_rec['missing_since_mono']
                        if prev_rec.get('missing_since_mono') is not None else
                        now_mono)
            if prev_rec.get('connected_start_mono') is not None:
                prior_total += max(0.0,
                                   end_mono - prev_rec['connected_start_mono'])
        rec['disconnect_count'] = rec.get('disconnect_count', 0) + prior_disc
        rec['connected_total_s'] = (rec.get('connected_total_s', 0.0) +
                                    prior_total)
        prev_ct = prev_rec.get('worker_create_time')
        cur_ct = rec.get('worker_create_time')
        reused_pid = bool(
            prev_rec.get('worker_pid') and
            prev_rec.get('worker_pid') == rec.get('worker_pid') and
            ((prev_ct and cur_ct and abs(cur_ct - prev_ct) > 0.5) or
             (prev_rec.get('worker_start_ts') and rec.get('worker_start_ts') and
              abs(rec['worker_start_ts'] - prev_rec['worker_start_ts']) > 2.0)))
        if (prev_rec.get('worker_pid') and rec.get('worker_pid') and
            (prev_rec.get('worker_pid') != rec.get('worker_pid') or
             reused_pid)):
            rec['min_log_ts'] = rec.get('worker_start_ts')

    def migrate_bare_record(self, serial, storage_key, now_mono):
        """Migrates a provisional bare-serial record into its canonical AVD storage_key."""
        if storage_key == serial or serial not in self.emulator_records:
            return
        bare_rec = self.emulator_records.pop(serial)
        prior_info = bare_rec.pop('_prior_bare_snapshot', None)
        if prior_info is not None:
            prior_snap, prior_disc, prior_total = prior_info
            bare_rec['disconnect_count'] = max(
                0,
                bare_rec.get('disconnect_count', 0) - prior_disc)
            bare_rec['connected_total_s'] = max(
                0.0,
                bare_rec.get('connected_total_s', 0.0) - prior_total)
        prev_rec = self.emulator_records.pop(storage_key, None)
        self._merge_prior_run(bare_rec, prev_rec, now_mono)
        bare_rec['storage_key'] = storage_key
        self.emulator_records[storage_key] = bare_rec
        if prior_info is not None:
            self.emulator_records[serial] = prior_snap

    @staticmethod
    def _live_group_has_worker(rec, live_group_pids, live_info=None):
        w_pid = rec.get('worker_pid')
        if not w_pid or w_pid not in (live_group_pids or ()):
            return False
        m_starts = (live_info or {}).get('member_start_times') or {}
        live_ct = m_starts.get(w_pid)
        if live_ct is None and (live_info or {}).get('primary_pid') == w_pid:
            live_ct = ((live_info or {}).get('primary_ctime') or
                       (live_info or {}).get('primary_create_time'))
        if live_ct:
            prev_ct = rec.get('worker_create_time')
            if prev_ct is not None and prev_ct != 0:
                return abs(live_ct - prev_ct) <= 2.0
            if rec.get('worker_start_ts'):
                return abs(live_ct - rec['worker_start_ts']) <= 2.0
        return True

    @staticmethod
    def _member_matches_rec(rec, pid, ctime=None, ctime_tol=2.0):
        if not pid:
            return False
        group_procs = rec.get('group_procs')
        if group_procs:
            if pid not in group_procs:
                return False
            prev_ct = group_procs[pid]
            if prev_ct is not None and prev_ct != 0:
                return (ctime is None or ctime == 0 or
                        abs(ctime - prev_ct) <= ctime_tol)
            if ctime and rec.get('worker_start_ts'):
                return (ctime - rec['worker_start_ts']) <= ctime_tol
            return True
        if pid not in (rec.get('group_pids') or ()):
            return False
        if ctime and rec.get('worker_start_ts'):
            return (ctime - rec['worker_start_ts']) <= ctime_tol
        return True

    @staticmethod
    def _effective_daemon_instance(d):
        if not d:
            return DEFAULT_NETSIM_INSTANCE
        ini_path = d.get('ini_path')
        return ((extract_ini_instance_num(ini_path) if ini_path else None) or
                d.get('instance_num') or DEFAULT_NETSIM_INSTANCE)

    @classmethod
    def _has_replacement_lineage(cls, old_d, new_d, target_port=None):
        if not old_d or not new_d:
            return False
        if new_d['start_wall'] <= old_d['start_wall']:
            return False
        if (cls._effective_daemon_instance(old_d)
                != cls._effective_daemon_instance(new_d)):
            return False
        if (target_port and new_d.get('grpc_port') and
                new_d['grpc_port'] != target_port):
            return False
        old_art = old_d.get('artifact_dir') or ''
        new_art = new_d.get('artifact_dir') or ''
        if old_art and new_art and not _same_file(old_art, new_art):
            return False
        return True

    @classmethod
    def _is_replacement_daemon(cls, old_d, new_d, target_port=None):
        return bool(
            old_d and new_d and old_d.get('exited') and
            cls._has_replacement_lineage(old_d, new_d, target_port=target_port))

    @staticmethod
    def _is_disallowed_daemon_art_dir(art_dir: Optional[str],
                                      same_user: bool,
                                      expected_uid: Optional[int] = None,
                                      root_uid: int = 0) -> bool:
        if not art_dir:
            return False
        if _has_parent_dir_segment(art_dir):
            return True
        my_uid = _current_uid()
        eff_uid = _resolve_expected_uid(expected_uid,
                                        same_user=same_user,
                                        monitor_uid=my_uid)
        # The directory may not exist yet at daemon startup: check as much of
        # it as does, as Rust has_trusted_existing_ancestors() does.
        deepest = None
        for prefix in _iter_path_prefixes(art_dir):
            try:
                os.lstat(prefix)
            except FileNotFoundError:
                break
            except OSError:
                return True
            deepest = prefix
        if my_uid is None or eff_uid is not None or my_uid == 0:
            trusted = _has_trusted_ancestors(deepest or '.',
                                             expected_uid=eff_uid,
                                             root_uid=root_uid)
        else:
            trusted = _has_trusted_ancestors_unowned(deepest or '.',
                                                     root_uid=root_uid)
        if not trusted:
            return True

        def _disallowed_existing(node: str) -> bool:
            try:
                lst = os.lstat(node)
            except FileNotFoundError:
                return False
            except OSError:
                return True
            if not stat.S_ISDIR(lst.st_mode):
                return True
            if my_uid is None:
                return False
            # As in Rust, the directory itself may not be world-writable,
            # sticky or not.
            if lst.st_mode & stat.S_IWOTH:
                return True
            if eff_uid is not None:
                if lst.st_uid != eff_uid:
                    return True
                return not _is_trusted_dir_stat(lst, eff_uid, root_uid)
            if same_user or my_uid == 0:
                return not _is_trusted_dir_stat(lst, None, root_uid)
            return lst.st_uid == my_uid

        if _disallowed_existing(art_dir):
            return True
        parent = os.path.dirname(os.path.normpath(art_dir))
        parent_name = os.path.basename(parent) if parent else ''
        if (parent and
            (parent_name == 'android' or parent_name.startswith('android-')) and
                _disallowed_existing(parent)):
            return True
        return False

    @staticmethod
    def _reset_for_new_worker(rec,
                              worker_pid,
                              worker_start_ts,
                              no_netsim,
                              now_mono,
                              worker_ctime=None,
                              prev_exit=None):
        """Resets per-run fields when the same AVD restarts under a new PID."""
        if rec.get('state') in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
            rec['disconnect_count'] = rec.get('disconnect_count', 0) + 1
            end_mono = (rec['missing_since_mono'] if
                        rec.get('missing_since_mono') is not None else now_mono)
            if rec.get('connected_start_mono') is not None:
                rec['connected_total_s'] = (
                    rec.get('connected_total_s', 0.0) +
                    max(0.0, end_mono - rec['connected_start_mono']))
            rec['connected_start_mono'] = None
            rec['state'] = 'DISCONNECTED'
        if (rec.get('storage_key') == rec.get('serial') and
            (rec.get('worker_pid') or NetsimTracker._is_settled(rec) or
             NetsimTracker._is_exited_disabled(rec))):
            snap = dict(rec)
            snap.pop('_prior_bare_snapshot', None)
            snap['chip_attach_s'] = dict(rec.get('chip_attach_s') or {})
            snap['chip_ids'] = set(rec.get('chip_ids') or ())
            snap['group_pids'] = set()
            snap['group_procs'] = {}
            snap['connected_start_mono'] = None
            snap['adb_online_since_mono'] = None
            snap['missed_ticks'] = max(snap.get('missed_ticks', 0),
                                       _SETTLE_AFTER_MISSED_TICKS)
            if (not NetsimTracker._is_settled(snap) and
                    snap.get('state') != 'DISABLED'):
                snap['state'] = ('FAILED(launcher_abort)'
                                 if prev_exit == 'startup_timeout' else
                                 'FAILED(worker_exit_no_connect)')
            rec['_prior_bare_snapshot'] = (snap,
                                           snap.get('disconnect_count', 0),
                                           snap.get('connected_total_s', 0.0))
        rec['avd_id'] = None
        rec['device_name'] = None
        rec['worker_pid'] = worker_pid
        rec['worker_create_time'] = worker_ctime
        rec['group_pids'] = set()
        rec['group_procs'] = {}
        rec['worker_start_ts'] = worker_start_ts
        rec['first_seen_mono'] = now_mono
        rec['min_log_ts'] = worker_start_ts
        rec['no_netsim'] = bool(no_netsim)
        rec['state'] = 'CONNECTING'
        rec['ambiguous'] = False
        rec['daemon_key'] = None
        rec['provisional_daemon'] = False
        rec['netsim_daemon_ready_s'] = None
        rec['first_transport_ts'] = None
        rec['netsim_connect_s'] = None
        rec['chip_attach_s'] = {}
        rec['chip_ids'] = set()
        rec['unknown_chip_count'] = 0
        rec['wifi_associated_s'] = None
        rec['connected_start_mono'] = None
        rec['adb_online_since_mono'] = None
        rec['host_seen'] = False
        rec['missing_since_mono'] = None
        rec['missed_ticks'] = 0

    def _poll_daemon_log(self,
                         d,
                         now_wall,
                         allow_reset=True,
                         has_older_server_sharer=False,
                         has_newer_client_sharer=False,
                         active_sharer_pids=()):
        if not self.enable_log:
            return
        pid = d['pid']
        tailer = d.get('tailer')
        if tailer is None:
            d['log_available'] = False
            if not d.get('log_unavail_recorded'):
                d['log_unavail_recorded'] = True
                self._record_event(now_wall, f"LOG_UNAVAILABLE(pid={pid})")
            return
        prev_offset = tailer.offset
        ok = tailer.poll(now_utc=now_wall, allow_reset=allow_reset)
        d['log_available'] = ok
        if not ok and not d.get('log_unavail_recorded'):
            d['log_unavail_recorded'] = True
            self._record_event(now_wall, f"LOG_UNAVAILABLE(pid={pid})")
        d_same = bool(d.get('same_user', True))
        d_exp_uid = _resolve_expected_uid(d.get('uid'), same_user=d_same)
        logged_ini_ok = (bool(tailer.ini_path_logged) and
                         _can_adopt_logged_ini_path(tailer.ini_path_logged,
                                                    d_same,
                                                    expected_uid=d_exp_uid))
        if (d.get('ready_ts') is None and d.get('ini_path') is None and
                d.get('grpc_port') is None):
            ini_pid = None
            if logged_ini_ok:
                raw_pid = parse_netsim_ini(tailer.ini_path_logged,
                                           expected_uid=d_exp_uid).get('pid')
                if isinstance(raw_pid, int):
                    ini_pid = raw_pid
            if (tailer.client_mode_spawn and ini_pid != pid and
                (ini_pid is not None or has_older_server_sharer)):
                if not d.get('client_mode_recorded'):
                    d['client_mode_recorded'] = True
                    self._record_event(now_wall,
                                       f"CLIENT_MODE_SPAWN(pid={pid})")
                tailer.connections.clear()
                tailer.created_devices.clear()
                tailer.chip_adds.clear()
                tailer.chip_removals.clear()
                tailer.empty_devices.clear()
                tailer.wifi_associations.clear()
                return
            if (not tailer.client_mode_spawn and prev_offset == 0 and
                    ini_pid is not None and ini_pid != pid and
                    ini_pid in active_sharer_pids):
                tailer.offset = 0
                tailer.head_prefix = b''
                tailer.frozen = False
                tailer.reset_state()
                return
        if tailer.artifact_dir_logged and _is_real_dir(
                tailer.artifact_dir_logged):
            if not self._is_disallowed_daemon_art_dir(
                    tailer.artifact_dir_logged, d_same, expected_uid=d_exp_uid):
                d['artifact_dir'] = tailer.artifact_dir_logged
        if tailer.version:
            d['version'] = tailer.version
        if logged_ini_ok and (not d.get('ini_path') or
                              (not d.get('exited') and
                               not os.path.exists(d['ini_path']))):
            d['ini_path'] = tailer.ini_path_logged
        if tailer.grpc_port_logged is not None:
            d['grpc_port'] = tailer.grpc_port_logged
        if tailer.exit_reason_hint in ('startup_timeout', 'idle_timeout',
                                       'sigterm_sigint'):
            d['exit_reason_hint'] = tailer.exit_reason_hint
        own_ready_ts = (tailer.ready_ts if
                        (tailer.ready_ts and tailer.ready_ts
                         >= d['start_wall'] - _LOG_FLOOR_SLACK_S) else None)
        if own_ready_ts and d['ready_ts'] is None:
            d['ready_ts'] = own_ready_ts
            d['netsim_daemon_ready_s'] = max(0.0,
                                             own_ready_ts - d['start_wall'])
            d['_ready_event_pending'] = True
        if tailer.frontend_disabled and not d['frontend_disabled']:
            d['frontend_disabled'] = True
            self._record_event(now_wall, f"FRONTEND_DISABLED(pid={pid})")
        if tailer.pcap_flag:
            d['pcap_flag'] = True
        if (tailer.client_mode_spawn and not d.get('client_mode_recorded') and
                not has_newer_client_sharer and d.get('ready_ts') is None and
                d.get('ini_path') is None and d.get('grpc_port') is None):
            d['client_mode_recorded'] = True
            self._record_event(now_wall, f"CLIENT_MODE_SPAWN(pid={pid})")

    def _emit_pending_ready(self, d):
        if not d.pop('_ready_event_pending', False):
            return
        if d.get('grpc_port') is None and d.get('ini_path'):
            d_same = bool(d.get('same_user', True))
            d_exp_uid = _resolve_expected_uid(d.get('uid'), same_user=d_same)
            if _can_adopt_logged_ini_path(d['ini_path'],
                                          d_same,
                                          expected_uid=d_exp_uid):
                ini_port = parse_netsim_ini(
                    d['ini_path'], expected_uid=d_exp_uid).get('grpc.port')
                if isinstance(ini_port, int) and 0 < ini_port <= 0xFFFF:
                    d['grpc_port'] = ini_port
        port_str = (str(d['grpc_port'])
                    if d.get('grpc_port') is not None else 'N/A')
        self._record_event(
            d['ready_ts'],
            f"READY(pid={d['pid']}, port={port_str}, ready_s={d['netsim_daemon_ready_s']:.2f}s)"
        )

    @staticmethod
    def _backfill_daemon_version_from_stats(d):
        art_dir = d.get('artifact_dir')
        same_user = bool(d.get('same_user', True))
        eff_uid = _resolve_expected_uid(d.get('uid'), same_user=same_user)
        if (d.get('version') or not _is_real_dir(art_dir) or
                NetsimTracker._is_disallowed_daemon_art_dir(
                    art_dir, same_user, expected_uid=eff_uid)):
            return
        stats_path = os.path.join(art_dir, 'netsim_session_stats.json')
        parsed, _, new_cache, _ = _read_session_stats_json(
            stats_path,
            cached_state=d.get('stats_cache'),
            not_before=d['start_wall'],
            require_self_owner=same_user,
            expected_uid=eff_uid)
        d['stats_cache'] = new_cache
        if isinstance(parsed, dict):
            ver = parsed.get('version')
            if isinstance(ver, str) and ver.strip():
                d['version'] = ver.strip()

    def update(self,
               summary_emulators,
               connected_devices,
               now_wall=None,
               now_mono=None):
        if now_wall is None:
            now_wall = time.time()
        if now_mono is None:
            now_mono = time.monotonic()

        (daemon_procs, all_emu_group_pids, all_emu_group_info,
         _) = _snapshot_process_state()
        lock_holders = self._read_proc_locks()

        seen_daemon_keys = set()
        art_dir_counts = {}

        for p in daemon_procs:
            pid = p['pid']
            ctime = p.get('create_time') or 0.0
            d_key = (pid, ctime)
            seen_daemon_keys.add(d_key)

            if d_key not in self.daemons:
                precise_start = get_proc_precise_start_time(
                    pid, fallback_create_time=ctime)
                proc_obj = p.get('proc')
                env = p.get('environ')
                if not env and proc_obj is not None:
                    try:
                        cand_env = proc_obj.environ()
                        if isinstance(cand_env, dict):
                            env = cand_env
                    except Exception:
                        pass
                env = env or {}
                cmd = p.get('cmdline')
                if not cmd and proc_obj is not None:
                    try:
                        cand_cmd = proc_obj.cmdline()
                        if isinstance(cand_cmd, (list, tuple)):
                            cmd = list(cand_cmd)
                    except Exception:
                        pass
                cmd = cmd or []
                inst_num = _extract_netsim_instance_num(cmd, env)
                raw_uid = p.get('uid') if 'uid' in p else _proc_uid(proc_obj)
                proc_uid = (raw_uid if isinstance(raw_uid, int) and
                            not isinstance(raw_uid, bool) else None)
                # As in Rust is_same_user(), a daemon whose uid cannot be read
                # is not the monitor's (off Windows, which has no uids).
                if 'same_user' in p:
                    same_user = bool(p['same_user'])
                elif 'uid' in p or proc_uid is not None:
                    same_user = _is_same_user(proc_uid)
                else:
                    same_user = True
                eff_uid = _resolve_expected_uid(proc_uid, same_user=same_user)
                # Only a same-user daemon with an unreadable env falls back to
                # ours; a foreign daemon must not adopt the monitor user's dir.
                if not same_user and not env:
                    art_dir = ''
                else:
                    art_dir = (
                        resolve_netsim_artifact_dir(env, inst_num)
                        if self.socket_os_family is None else
                        resolve_netsim_artifact_dir(
                            env, inst_num, target_os=self.socket_os_family))
                    if self._is_disallowed_daemon_art_dir(art_dir,
                                                          same_user,
                                                          expected_uid=eff_uid):
                        art_dir = ''
                log_path = (resolve_netsim_stderr_log_path(art_dir, inst_num)
                            if art_dir else '')
                spawner = self._classify_daemon_spawner(p.get('ppid'),
                                                        all_emu_group_pids)
                exe_path = p.get('exe')
                if not exe_path and proc_obj is not None:
                    try:
                        cand_exe = proc_obj.exe()
                        if isinstance(cand_exe, str):
                            exe_path = cand_exe
                    except Exception:
                        pass
                exe_path = exe_path or (cmd[0] if cmd else '')
                netsim_cli_bin = resolve_netsim_cli_bin(
                    exe_path, same_user, os_family=self.socket_os_family)

                frontend_off = any(
                    tok in ('--no-cli-ui', '--no-web-ui') for tok in cmd)
                pcap_cli_on = any(tok == '--pcap' for tok in cmd)
                start_wall = precise_start or now_wall
                tailer = None
                if self.enable_log and log_path:
                    tailer = NetsimLogTailer(log_path,
                                             require_self_owner=same_user,
                                             expected_uid=eff_uid)
                    if ctime > 0:
                        tailer.min_ts = start_wall - _LOG_FLOOR_SLACK_S

                self.daemons[d_key] = {
                    'daemon_key': d_key,
                    'pid': pid,
                    'uid': proc_uid,
                    'create_time': ctime,
                    'start_wall': start_wall,
                    'first_seen_mono': now_mono,
                    'last_seen_mono': now_mono,
                    'spawner': spawner,
                    'cmdline': cmd,
                    'environ': env,
                    'instance_num': inst_num,
                    'artifact_dir': art_dir,
                    'log_path': log_path,
                    'tailer': tailer,
                    'netsim_cli_bin': netsim_cli_bin,
                    'same_user': same_user,
                    'version': None,
                    'ini_path': None,
                    'grpc_port': None,
                    'ready_ts': None,
                    'netsim_daemon_ready_s': None,
                    'frontend_disabled': frontend_off,
                    'pcap_flag': pcap_cli_on,
                    'log_available': False,
                    'exited': False,
                    'exit_reason': None,
                    'orphaned': False,
                    'zero_devices_since_mono': now_mono,
                    'stats_cache': None,
                    'capture_rows': [],
                    'pcap_live_bytes': 0,
                    'pcap_live_files': 0,
                    'joined_emulators': [],
                }
                self._record_event(
                    precise_start or now_wall,
                    f"STARTED(pid={pid}, spawner={spawner})",
                )
                if frontend_off:
                    self._record_event(precise_start or now_wall,
                                       f"FRONTEND_DISABLED(pid={pid})")

            d = self.daemons[d_key]
            d['last_seen_mono'] = now_mono

        for d_key in seen_daemon_keys:
            d = self.daemons[d_key]
            d_same = bool(d.get('same_user', True))
            d_exp_uid = _resolve_expected_uid(d.get('uid'), same_user=d_same)
            if self._is_disallowed_daemon_art_dir(d.get('artifact_dir'),
                                                  d_same,
                                                  expected_uid=d_exp_uid):
                d['artifact_dir'] = ''
                d['log_path'] = ''
                d['tailer'] = None
                d['stats_cache'] = None
                d['log_available'] = False
            elif d.get('tailer') is not None:
                d['tailer'].require_self_owner = d_same
                d['tailer'].expected_uid = d_exp_uid

        ordered_active_keys = sorted(seen_daemon_keys,
                                     key=lambda k:
                                     (self.daemons[k]['start_wall'], k[0]),
                                     reverse=True)
        pre_polled = set()
        if self.enable_log:
            for d_key in ordered_active_keys:
                d = self.daemons[d_key]
                tailer = d.get('tailer')
                if (not d.get('log_path') or d.get('client_mode_recorded') or
                        tailer is None or tailer.offset <= 0):
                    continue
                if any(k != d_key and
                       self.daemons[k].get('log_path') == d.get('log_path') and
                       not self.daemons[k].get('client_mode_recorded') and
                       (self.daemons[k]['start_wall'], k[0]) > (d['start_wall'],
                                                                d_key[0])
                       for k in seen_daemon_keys):
                    self._poll_daemon_log(d, now_wall, allow_reset=False)
                    pre_polled.add(d_key)

        for d_key in ordered_active_keys:
            if d_key in pre_polled:
                continue
            d = self.daemons[d_key]
            d_has_offset = bool(
                d.get('tailer') is not None and d['tailer'].offset > 0)
            has_newer_server_sharer = False
            has_older_server_sharer = False
            has_newer_client_sharer = False
            active_sharer_pids = set()
            if d.get('log_path'):
                for k in seen_daemon_keys:
                    if k == d_key:
                        continue
                    od = self.daemons[k]
                    if od.get('log_path') != d.get('log_path'):
                        continue
                    od_is_newer = ((od['start_wall'], k[0])
                                   > (d['start_wall'], d_key[0]))
                    if od.get('client_mode_recorded'):
                        if od_is_newer:
                            has_newer_client_sharer = True
                        continue
                    od_tailer = od.get('tailer')
                    od_frozen = bool(od_tailer is not None and od_tailer.frozen)
                    if not od_frozen:
                        active_sharer_pids.add(od['pid'])
                    if od_is_newer:
                        od_claimed = bool(
                            (od_tailer is not None and od_tailer.offset > 0) or
                            od.get('ready_ts') is not None or
                            od.get('grpc_port') is not None or
                            od.get('ini_path') is not None)
                        if d_has_offset or od_claimed:
                            has_newer_server_sharer = True
                    elif not od_frozen:
                        has_older_server_sharer = True
            if (not has_newer_server_sharer or d.get('tailer') is None or
                    d['tailer'].offset > 0):
                self._poll_daemon_log(
                    d,
                    now_wall,
                    allow_reset=not has_newer_server_sharer,
                    has_older_server_sharer=has_older_server_sharer,
                    has_newer_client_sharer=has_newer_client_sharer,
                    active_sharer_pids=active_sharer_pids)

        for p in daemon_procs:
            d_key = (p['pid'], p.get('create_time') or 0.0)
            art_dir = self.daemons[d_key].get('artifact_dir')
            if art_dir:
                art_dir_counts[art_dir] = art_dir_counts.get(art_dir, 0) + 1

        for art_dir, cnt in art_dir_counts.items():
            if cnt > 1:
                self._record_event(now_wall,
                                   f"DUPLICATE_ARTIFACT_DIR({art_dir})")

        ini_files = self._discover_ini_files(
            [self.daemons[k]['environ'] for k in sorted(seen_daemon_keys)])
        self.stale_inis = {
            k: v for k, v in self.stale_inis.items() if any(
                _same_file(k, p) for p in ini_files)
        }
        seen_pids = {pid for pid, _ in seen_daemon_keys}
        inis = [_read_ini_observation(p, lock_holders) for p in ini_files]
        matches = self._match_inis_to_daemons(inis, seen_daemon_keys)
        for ini, m_key in zip(inis, matches):
            ini_path = ini['path']
            if m_key is not None:
                d = self.daemons[m_key]
                d['ini_path'] = ini_path
                if ini['port'] is not None:
                    d['grpc_port'] = ini['port']
                prev_key = next((k for p, k in self._last_active_by_ini.items()
                                 if _same_file(p, ini_path)), None)
                if prev_key is not None and prev_key != m_key:
                    self.restart_count += 1
                    self._record_event(
                        now_wall, f"RESTARTED(ini={ini_path}, "
                        f"old_pid={prev_key[0]}, new_pid={m_key[0]})")
                self.stale_inis = {
                    p: v
                    for p, v in self.stale_inis.items()
                    if not _same_file(p, ini_path)
                }
                self._last_active_by_ini = {
                    p: k
                    for p, k in self._last_active_by_ini.items()
                    if not _same_file(p, ini_path)
                }
                self._last_active_by_ini[ini_path] = m_key
            elif not ini['holders'] and not any(
                    _same_file(p, ini_path) for p in self.stale_inis):
                # A pid= naming a tracked daemon that did not match was
                # reused; only an untracked live non-zombie writer keeps the
                # INI fresh.
                writer_alive = (ini['pid'] is not None and
                                ini['pid'] not in seen_pids and
                                _is_live_non_zombie_pid(ini['pid']))
                if not writer_alive:
                    self._record_event(now_wall, f"STALE_INI(path={ini_path})")
                    self.stale_inis[ini_path] = ini['data']

        for p in daemon_procs:
            d_key = (p['pid'], p.get('create_time') or 0.0)
            if d_key in self.daemons:
                self._emit_pending_ready(self.daemons[d_key])

        active_log_paths = {
            self.daemons[k].get('log_path')
            for k in seen_daemon_keys
            if k in self.daemons and self.daemons[k].get('log_path')
        }
        active_artifact_dirs = {
            self.daemons[k].get('artifact_dir')
            for k in seen_daemon_keys
            if k in self.daemons and self.daemons[k].get('artifact_dir') and
            not self.daemons[k].get('client_mode_recorded')
        }
        for d_key, d in list(self.daemons.items()):
            if d_key not in seen_daemon_keys and not d.get('exited'):
                d['exited'] = True
                d_same = bool(d.get('same_user', True))
                d_exp_uid = _resolve_expected_uid(d.get('uid'),
                                                  same_user=d_same)
                if self._is_disallowed_daemon_art_dir(d.get('artifact_dir'),
                                                      d_same,
                                                      expected_uid=d_exp_uid):
                    d['artifact_dir'] = ''
                    d['log_path'] = ''
                    d['tailer'] = None
                    d['stats_cache'] = None
                    d['log_available'] = False
                elif d.get('tailer') is not None:
                    d['tailer'].require_self_owner = d_same
                    d['tailer'].expected_uid = d_exp_uid
                shared_active = bool(
                    d.get('log_path') and d.get('log_path') in active_log_paths)
                newer_server_active = False
                older_server_active = False
                exited_sharer_pids = set()
                if shared_active:
                    for k in seen_daemon_keys:
                        od = self.daemons[k]
                        if (od.get('log_path') != d.get('log_path') or
                                od.get('client_mode_recorded')):
                            continue
                        exited_sharer_pids.add(od['pid'])
                        if ((od['start_wall'], k[0])
                                > (d['start_wall'], d_key[0])):
                            newer_server_active = True
                        else:
                            older_server_active = True
                if not shared_active:
                    self._poll_daemon_log(d, now_wall, allow_reset=True)
                elif (d.get('tailer') is not None and
                      (not newer_server_active or d['tailer'].offset > 0)):
                    self._poll_daemon_log(
                        d,
                        now_wall,
                        allow_reset=False,
                        has_older_server_sharer=older_server_active,
                        active_sharer_pids=exited_sharer_pids)
                self._emit_pending_ready(d)
                can_harvest_exit = bool(
                    not d.get('client_mode_recorded') and
                    d.get('artifact_dir') and
                    d.get('artifact_dir') not in active_artifact_dirs)
                if can_harvest_exit:
                    self._backfill_daemon_version_from_stats(d)
                hint = d.get('exit_reason_hint') or (
                    d['tailer'].exit_reason_hint if d.get('tailer') else None)
                if hint in ('startup_timeout', 'idle_timeout',
                            'sigterm_sigint'):
                    reason = hint
                else:
                    ini_p = d.get('ini_path')
                    ini_owner = (next(
                        (k for p, k in self._last_active_by_ini.items()
                         if _same_file(p, ini_p)), None) if ini_p else None)
                    ini_still_owned = bool(ini_p and os.path.exists(ini_p) and
                                           ini_owner in (None, d_key))
                    if ini_still_owned:
                        reason = 'killed_or_crashed'
                    else:
                        reason = 'clean_unknown'
                d['exit_reason'] = reason
                self._record_event(now_wall,
                                   f"EXITED(pid={d['pid']}, reason={reason})")
                if can_harvest_exit:
                    all_joined = [
                        r for r in self.emulator_records.values()
                        if r.get('daemon_key') == d_key and
                        r.get('state') != 'DISABLED'
                    ]
                    d['all_joined_emulators'] = all_joined
                    self.pcap_mgr.poll_and_manage_daemon(d,
                                                         all_joined,
                                                         now_mono=now_mono,
                                                         shutting_down=True)
                d['tailer'] = None
                d['stats_cache'] = None
                d['environ'] = {}
                d['capture_rows'] = []
                d['all_joined_emulators'] = []
                self.pcap_mgr.forget_daemon(d_key)

        active_ports = {
            d.get('grpc_port')
            for d in self.daemons.values()
            if not d.get('exited') and d.get('grpc_port')
        }
        daemon_pids = {
            d['pid'] for d in self.daemons.values() if not d.get('exited')
        }
        port_to_conns = self._collect_loopback_grpc_sockets(
            active_ports, all_emu_group_pids, daemon_pids)

        for d in self.daemons.values():
            d['joined_emulators'] = []
            d['active_socket_conns'] = len(
                port_to_conns.get(d.get('grpc_port'), []))

        effective_emulators = {
            s_key: e_data
            for s_key, e_data in summary_emulators.items()
            if (e_data.get('current_serial', s_key).startswith('emulator-') or
                e_data.get('current_serial', s_key) in all_emu_group_pids or
                e_data.get('current_serial', s_key) in all_emu_group_info or
                s_key in self.emulator_records)
        }
        # A session emulator whose serial now runs a different AVD was
        # replaced and settles at once, as does one already settled. Any other
        # emulator missing from the host scan (or, if the scan never saw it,
        # from adb) is held at its last state until _record_missed_tick says
        # it exited. adb can keep an 'offline' entry for an emulator that was
        # killed, so only other adb states hold it. An exited or replaced
        # emulator must not take the live group, sockets or chips of whatever
        # runs on that serial now.
        gone_keys, held_keys = set(), set()
        for s_key, e_data in effective_emulators.items():
            serial = e_data.get('current_serial', s_key)
            rec = self.emulator_records.get(s_key) or {}
            live_info = all_emu_group_info.get(serial) or {}
            live_avd = live_info.get('avd_id')
            live_pid = live_info.get('primary_pid')
            live_ctime = (live_info.get('primary_ctime') or
                          live_info.get('primary_create_time'))
            live_group_pids = all_emu_group_pids.get(serial) or ()
            live_matches_group = self._member_matches_rec(
                rec, live_pid, live_ctime)
            worker_in_live_group = self._live_group_has_worker(
                rec, live_group_pids, live_info)
            start_changed = bool(live_ctime and rec.get('worker_start_ts') and
                                 (live_ctime - rec['worker_start_ts']) > 2.0 and
                                 not worker_in_live_group and
                                 not live_matches_group)
            rec_inactive = (self._is_settled(rec) or
                            self._is_exited_disabled(rec))
            pid_changed = bool(
                start_changed or
                (rec.get('worker_pid') and live_pid and
                 rec['worker_pid'] != live_pid and not worker_in_live_group and
                 (not live_matches_group or
                  (rec_inactive and live_avd and rec.get('avd_id') and
                   live_avd != rec['avd_id']))))
            replaced = bool(
                e_data.get('retired') or
                (rec.get('avd_id') and
                 ((live_avd and rec['avd_id'] != live_avd) or
                  (pid_changed and live_avd != rec['avd_id']) or
                  (rec_inactive and live_avd != rec['avd_id'] and
                   not live_matches_group))))
            adb_lists = serial in connected_devices
            if not replaced and (serial in all_emu_group_pids or
                                 (adb_lists and not rec.get('host_seen'))):
                continue
            if not rec:
                if not adb_lists:
                    gone_keys.add(s_key)
                continue
            adb_live = connected_devices.get(serial, 'offline') != 'offline'
            if replaced and rec.get('no_netsim'):
                rec['missed_ticks'] = max(rec.get('missed_ticks', 0),
                                          _SETTLE_AFTER_MISSED_TICKS)
            if (replaced or self._is_settled(rec) or
                    self._is_exited_disabled(rec) or
                    self._record_missed_tick(rec, adb_live, now_mono)):
                gone_keys.add(s_key)
            else:
                held_keys.add(s_key)
        known_serials = {
            e_data.get('current_serial', s_key)
            for s_key, e_data in effective_emulators.items()
            if s_key not in gone_keys
        }
        for serial, g_info in all_emu_group_info.items():
            if serial not in known_serials:
                # The key the session gives this emulator once adb lists it.
                avd = g_info.get('avd_name')
                s_key = f"{avd}_{serial}" if avd else serial
                effective_emulators[s_key] = {
                    'current_serial': serial,
                    'current_pid': g_info.get('primary_pid'),
                    'name': (g_info.get('display_name') or
                             g_info.get('avd_id') or serial),
                    'first_seen': now_mono,
                }
        for s_key, e_data in list(effective_emulators.items()):
            if s_key in gone_keys:
                continue
            serial = e_data.get('current_serial', s_key)
            if s_key == serial:
                g_info = all_emu_group_info.get(serial) or {}
                cur_pid = g_info.get('primary_pid') or e_data.get('current_pid')
                cur_ctime = (g_info.get('primary_ctime') or
                             g_info.get('primary_create_time'))
                live_group_pids = all_emu_group_pids.get(serial) or ()
                bare_cur = self.emulator_records.get(serial)
                bare_active_match = bool(
                    bare_cur and not self._is_settled(bare_cur) and
                    not self._is_exited_disabled(bare_cur) and
                    (self._live_group_has_worker(bare_cur, live_group_pids,
                                                 g_info) or
                     (cur_pid and
                      self._member_matches_rec(bare_cur, cur_pid, cur_ctime))))
                if not bare_active_match and cur_pid:
                    owner_key = None
                    for cand_k, cand_r in self.emulator_records.items():
                        if cand_k == serial or cand_r.get('serial') != serial:
                            continue
                        if (self._is_settled(cand_r) or
                                self._is_exited_disabled(cand_r)):
                            if cur_ctime and (
                                (cand_r.get('worker_pid') == cur_pid and
                                 cand_r.get('worker_create_time') and
                                 abs(cand_r['worker_create_time'] - cur_ctime)
                                 <= 2.0) or
                                (cur_pid in (cand_r.get('group_procs') or {})
                                 and cand_r['group_procs'][cur_pid] is not None
                                 and abs(cand_r['group_procs'][cur_pid] -
                                         cur_ctime) <= 2.0)):
                                owner_key = cand_k
                                break
                        elif (self._live_group_has_worker(
                                cand_r, live_group_pids, g_info) or
                              self._member_matches_rec(cand_r, cur_pid,
                                                       cur_ctime)):
                            owner_key = cand_k
                            break
                    if owner_key is not None:
                        e_bare = effective_emulators.pop(serial)
                        if (owner_key not in effective_emulators or
                                owner_key in gone_keys):
                            effective_emulators[owner_key] = e_bare
                        gone_keys.discard(owner_key)
                        held_keys.discard(owner_key)
                        continue
            elif serial in self.emulator_records:
                g_info = all_emu_group_info.get(serial) or {}
                cur_pid = g_info.get('primary_pid') or e_data.get('current_pid')
                cur_ctime = (g_info.get('primary_ctime') or
                             g_info.get('primary_create_time'))
                live_group_pids = all_emu_group_pids.get(serial) or ()
                bare_rec = self.emulator_records.get(serial)
                same_member = bool(
                    bare_rec and cur_pid and
                    self._member_matches_rec(bare_rec, cur_pid, cur_ctime))
                bare_worker_in_live = bool(
                    bare_rec and self._live_group_has_worker(
                        bare_rec, live_group_pids, g_info))
                same_start = bool(
                    bare_rec and
                    (same_member or not cur_ctime or
                     not bare_rec.get('worker_start_ts') or
                     abs(cur_ctime - bare_rec['worker_start_ts']) <= 2.0 or
                     (bare_rec.get('worker_pid') and bare_rec['worker_pid']
                      != cur_pid and bare_worker_in_live)))
                if (bare_rec and not self._is_settled(bare_rec) and
                        not self._is_exited_disabled(bare_rec) and
                        same_start and
                    (not cur_pid or not bare_rec.get('worker_pid') or
                     bare_rec.get('worker_pid') == cur_pid or
                     bare_worker_in_live or same_member)):
                    self.migrate_bare_record(serial, s_key, now_mono)
                    effective_emulators.pop(serial, None)
                    gone_keys.discard(serial)
                    held_keys.discard(serial)
        live_serials = {
            e_data.get('current_serial', s_key)
            for s_key, e_data in effective_emulators.items()
            if s_key not in gone_keys and s_key not in held_keys and
            e_data.get('current_serial', s_key) in all_emu_group_pids
        }
        # A record made for a host group that adb has not listed yet is in
        # neither the session nor the host scan once the group goes missing.
        for s_key, rec in self.emulator_records.items():
            if s_key not in effective_emulators:
                serial = rec['serial']
                effective_emulators[s_key] = {'current_serial': serial}
                adb_live = connected_devices.get(serial, 'offline') != 'offline'
                if serial in live_serials and rec.get('no_netsim'):
                    rec['missed_ticks'] = max(rec.get('missed_ticks', 0),
                                              _SETTLE_AFTER_MISSED_TICKS)
                if (serial in live_serials or self._is_settled(rec) or
                        self._is_exited_disabled(rec) or
                        self._record_missed_tick(rec, adb_live, now_mono)):
                    gone_keys.add(s_key)
                else:
                    held_keys.add(s_key)
        held_netsim_count = sum(
            1 for k in held_keys
            if not (self.emulator_records.get(k) or {}).get('no_netsim') and
            (self.emulator_records.get(k) or {}).get('state') != 'DISABLED')
        joined_live_entries = []
        unjoined_live_entries = []

        for s_key, e_data in effective_emulators.items():
            if s_key in held_keys:
                continue
            serial = e_data.get('current_serial', s_key)
            gone = s_key in gone_keys
            g_info = {} if gone else (all_emu_group_info.get(serial) or {})
            worker_pid = g_info.get('primary_pid') or e_data.get('current_pid')
            worker_ctime = (g_info.get('primary_ctime') or
                            g_info.get('primary_create_time'))
            existing_rec = self.emulator_records.get(s_key)
            prev_w_ctime = (existing_rec.get('worker_create_time')
                            if existing_rec else None)
            ctime_shifted = bool(
                worker_ctime and
                ((prev_w_ctime and abs(worker_ctime - prev_w_ctime) > 0.5)
                 if prev_w_ctime is not None else
                 (existing_rec and existing_rec.get('worker_start_ts') and
                  abs(worker_ctime - existing_rec['worker_start_ts']) > 2.0)))
            needs_start = bool(
                worker_pid and
                (existing_rec is None or
                 existing_rec.get('worker_pid') != worker_pid or ctime_shifted))
            worker_start_ts = (get_proc_precise_start_time(
                worker_pid, fallback_create_time=worker_ctime) if needs_start
                               else (existing_rec.get('worker_start_ts')
                                     if existing_rec else now_wall))

            if s_key not in self.emulator_records:
                self.emulator_records[s_key] = {
                    'storage_key':
                        s_key,
                    'serial':
                        serial,
                    'avd_id':
                        g_info.get('avd_id'),
                    'device_name':
                        g_info.get('display_name') or g_info.get('avd_id'),
                    'worker_pid':
                        worker_pid,
                    'worker_create_time':
                        worker_ctime,
                    'group_pids':
                        set(),
                    'group_procs': {},
                    'worker_start_ts':
                        worker_start_ts,
                    'first_seen_mono':
                        (e_data['first_seen']
                         if e_data.get('first_seen') is not None else now_mono),
                    'min_log_ts':
                        worker_start_ts if worker_pid else None,
                    'no_netsim':
                        bool(g_info.get('no_netsim')),
                    'state':
                        'CONNECTING',
                    'ambiguous':
                        False,
                    'daemon_key':
                        None,
                    'provisional_daemon':
                        False,
                    'netsim_daemon_ready_s':
                        None,
                    'first_transport_ts':
                        None,
                    'netsim_connect_s':
                        None,
                    'chip_attach_s': {},
                    'chip_ids':
                        set(),
                    'unknown_chip_count':
                        0,
                    'wifi_associated_s':
                        None,
                    'disconnect_count':
                        0,
                    'connected_start_mono':
                        None,
                    'connected_total_s':
                        0.0,
                    'adb_online_since_mono':
                        None,
                    'host_seen':
                        False,
                    'missing_since_mono':
                        None,
                    'missed_ticks':
                        0,
                }

            rec = self.emulator_records[s_key]
            rec['serial'] = serial
            live_group_pids = (() if gone else
                               (all_emu_group_pids.get(serial) or ()))
            rec.setdefault('group_pids', set())
            rec_group_procs = rec.setdefault('group_procs', {})
            was_settled = self._is_settled(rec)
            same_pid_restarted = bool(
                worker_pid and rec.get('worker_pid') == worker_pid and
                ((worker_ctime and rec.get('worker_create_time') and
                  abs(worker_ctime - rec['worker_create_time']) > 0.5)
                 if rec.get('worker_create_time') is not None else
                 (worker_ctime and rec.get('worker_start_ts') and
                  abs(worker_start_ts - rec['worker_start_ts']) > 2.0)))
            is_shutdown_member = False
            if not gone and worker_pid and (
                    rec.get('worker_pid') != worker_pid or same_pid_restarted or
                (was_settled and serial in all_emu_group_pids)):
                member_match = (not same_pid_restarted and
                                self._member_matches_rec(
                                    rec, worker_pid, worker_ctime))
                is_surviving_member = (not was_settled and member_match)
                is_shutdown_member = bool(
                    was_settled and rec.get('worker_pid') != worker_pid and
                    rec.get('worker_pid') not in live_group_pids and
                    member_match)
                is_worker_upgrade = bool(not was_settled and
                                         not same_pid_restarted and
                                         rec.get('worker_pid') and
                                         rec['worker_pid'] in live_group_pids)
                if is_surviving_member or is_shutdown_member:
                    pass
                elif (not was_settled and not same_pid_restarted and
                      (not rec.get('worker_pid') or is_worker_upgrade)):
                    rec['worker_pid'] = worker_pid
                    rec['worker_create_time'] = worker_ctime
                    if rec.get('state') == 'CONNECTING':
                        rec['worker_start_ts'] = worker_start_ts
                        rec['min_log_ts'] = worker_start_ts
                else:
                    prev_d = self.daemons.get(rec.get('daemon_key'))
                    self._reset_for_new_worker(
                        rec,
                        worker_pid,
                        worker_start_ts,
                        g_info.get('no_netsim'),
                        now_mono,
                        worker_ctime=worker_ctime,
                        prev_exit=(prev_d.get('exit_reason')
                                   if prev_d else None))
            if not gone:
                if serial in all_emu_group_pids:
                    rec['host_seen'] = True
                rec['missing_since_mono'] = None
                rec['missed_ticks'] = 0
                rec['group_pids'].update(p for p in live_group_pids if p)
                if worker_pid:
                    rec['group_pids'].add(worker_pid)
                live_g_procs = _build_live_group_procs(
                    worker_pid, (worker_pid, worker_ctime), live_group_pids,
                    g_info)
                _merge_group_procs(rec_group_procs, live_g_procs)
            elif serial not in all_emu_group_pids:
                rec['group_pids'].clear()
                rec_group_procs.clear()
            if is_shutdown_member or self._is_settled(rec):
                continue
            if g_info.get('avd_id'):
                rec['avd_id'] = g_info['avd_id']
            if g_info.get('display_name'):
                rec['device_name'] = g_info['display_name']
            elif g_info.get('avd_id') and not rec.get('device_name'):
                rec['device_name'] = g_info['avd_id']
            if g_info.get('no_netsim'):
                rec['no_netsim'] = True

            if rec['no_netsim']:
                rec['state'] = 'DISABLED'
                rec['daemon_key'] = None
                rec['netsim_daemon_ready_s'] = None
                continue

            if connected_devices.get(serial) == 'device' and not gone:
                if rec['adb_online_since_mono'] is None:
                    rec['adb_online_since_mono'] = now_mono
            else:
                rec['adb_online_since_mono'] = None

            endpoint = g_info.get('grpc_endpoint') or ''
            target_port = None
            if ':' in endpoint:
                try:
                    cand_port = int(endpoint.rsplit(':', 1)[1])
                    if 0 < cand_port <= 0xFFFF:
                        target_port = cand_port
                except ValueError:
                    pass

            # A finished emulator has no endpoint (g_info is empty) and joins
            # no daemon: the serial's sockets belong to whatever runs there now.
            worker_exited = bool(
                not gone and live_group_pids and rec.get('worker_pid') and
                not self._live_group_has_worker(rec, live_group_pids, g_info))
            joined_daemon = None
            joined_via_fallback = False
            if target_port:
                port_matches = [
                    d for d in self.daemons.values()
                    if not d.get('exited') and not d.get('client_mode_recorded')
                    and d.get('grpc_port') == target_port
                ]
                if port_matches:
                    joined_daemon = next(
                        (d for d in port_matches if d.get('same_user', True)),
                        port_matches[0])
            if joined_daemon is None and not gone:
                conn_matches = [
                    d for d in self.daemons.values()
                    if not d.get('exited') and not d.get('client_mode_recorded')
                    and d.get('grpc_port') and any(
                        c['serial'] == serial
                        for c in port_to_conns.get(d['grpc_port'], []))
                ]
                if conn_matches:
                    joined_daemon = next(
                        (d for d in conn_matches if d.get('same_user', True)),
                        conn_matches[0])
            had_prior_attach = bool(
                rec.get('first_transport_ts') is not None or
                rec.get('chip_attach_s') or
                rec.get('unknown_chip_count', 0) > 0 or rec.get('state')
                in ('CONNECTED', 'CONNECTED_NO_CHIPS', 'DISCONNECTED'))
            if joined_daemon is None and not gone and not worker_exited:
                active_daemons = [
                    d for d in self.daemons.values()
                    if not d.get('exited') and not d.get('client_mode_recorded')
                ]
                same_user_active = [
                    d for d in active_daemons if d.get('same_user', True)
                ]
                cand_daemon = None
                if len(same_user_active) == 1:
                    cand_daemon = same_user_active[0]
                elif (not same_user_active and _current_uid() == 0 and
                      len(active_daemons) == 1):
                    cand_daemon = active_daemons[0]
                if cand_daemon is not None:
                    port_conflict = bool(
                        target_port and cand_daemon.get('grpc_port') and
                        cand_daemon['grpc_port'] != target_port)
                    old_dk = rec.get('daemon_key')
                    if port_conflict:
                        cand_daemon = None
                    elif (old_dk is not None and
                          cand_daemon['daemon_key'] != old_dk and
                          had_prior_attach):
                        old_d = self.daemons.get(old_dk)
                        if not self._is_replacement_daemon(
                                old_d, cand_daemon, target_port=target_port):
                            cand_daemon = None
                    joined_daemon = cand_daemon
                    joined_via_fallback = cand_daemon is not None
            if (worker_exited and joined_daemon is not None and
                    rec.get('daemon_key') != joined_daemon['daemon_key']):
                joined_daemon = None
                joined_via_fallback = False

            host_worker_gone = bool(
                not e_data.get('_pending_host_scan') and
                (rec.get('host_seen') or rec.get('worker_pid') is not None or
                 connected_devices.get(serial) != 'device') and
                serial not in all_emu_group_pids)

            if joined_daemon is None:
                if (not had_prior_attach and
                        rec.get('connected_total_s', 0.0) == 0.0 and
                        rec.get('disconnect_count', 0) == 0 and
                        rec.get('daemon_key') is not None):
                    old_d = self.daemons.get(rec['daemon_key'])
                    old_port_conflict = bool(old_d and target_port and
                                             old_d.get('grpc_port') and
                                             old_d['grpc_port'] != target_port)
                    drop_provisional = False
                    if old_d is None or old_port_conflict:
                        drop_provisional = True
                    elif (not gone and not worker_exited and
                          not host_worker_gone and
                          rec.get('provisional_daemon') and
                          not old_d.get('exited')):
                        pre_existing_bystander = bool(
                            old_d.get('ready_ts') and
                            rec.get('worker_start_ts') and old_d['ready_ts']
                            < rec['worker_start_ts'] - _LOG_FLOOR_SLACK_S)
                        relevant_others = [
                            od for od in self.daemons.values()
                            if od['daemon_key'] != rec['daemon_key'] and
                            not od.get('exited') and
                            not od.get('client_mode_recorded') and
                            (not old_d.get('same_user', True) or
                             od.get('same_user', True))
                        ]
                        overlapping_restart = bool(
                            not pre_existing_bystander and relevant_others and
                            all(
                                bool(od.get('same_user', True)) == bool(
                                    old_d.get('same_user', True)) and
                                self._has_replacement_lineage(
                                    old_d, od, target_port=target_port)
                                for od in relevant_others))
                        drop_provisional = not overlapping_restart
                    if drop_provisional:
                        rec['daemon_key'] = None
                        rec['provisional_daemon'] = False
                        rec['netsim_daemon_ready_s'] = None
                # Settling again could change a settled state, e.g.
                # DISCONNECTED to FAILED(launcher_abort) once the daemon exits.
                if self._is_settled(rec):
                    continue
                exit_mono = now_mono
                if gone and rec.get('missing_since_mono') is not None:
                    exit_mono = rec['missing_since_mono']
                prev_state = rec.get('state')
                if prev_state in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
                    rec['state'] = 'DISCONNECTED'
                    rec['disconnect_count'] += 1
                    if rec['connected_start_mono'] is not None:
                        rec['connected_total_s'] += max(
                            0.0, exit_mono - rec['connected_start_mono'])
                        rec['connected_start_mono'] = None
                else:
                    d_prev = self.daemons.get(rec.get('daemon_key'))
                    if d_prev and d_prev.get(
                            'exit_reason') == 'startup_timeout':
                        rec['state'] = 'FAILED(launcher_abort)'
                    elif gone or worker_exited or host_worker_gone:
                        rec['state'] = 'FAILED(worker_exit_no_connect)'
                if not gone and not self._is_settled(rec):
                    unjoined_live_entries.append((s_key, e_data, rec, serial))
                continue

            joined_live_entries.append(
                (s_key, e_data, rec, serial, worker_exited, host_worker_gone,
                 joined_daemon, joined_via_fallback))

        live_emulator_count = (len(joined_live_entries) +
                               len(unjoined_live_entries) + held_netsim_count)
        display_name_counts = {}
        for _, e_d, r, ser in ([
            (k, ed, rc, sr) for k, ed, rc, sr, _, _, _, _ in joined_live_entries
        ] + unjoined_live_entries):
            g_i = all_emu_group_info.get(ser) or {}
            disp = (g_i.get('display_name') or r.get('device_name') or
                    g_i.get('avd_id') or e_d.get('name', '').split(' | ')[0])
            if disp:
                display_name_counts[disp] = display_name_counts.get(disp, 0) + 1

        for (s_key, e_data, rec, serial, worker_exited, host_worker_gone,
             joined_daemon, joined_via_fallback) in joined_live_entries:
            gone = s_key in gone_keys
            if (rec.get('daemon_key') is not None and
                    rec['daemon_key'] != joined_daemon['daemon_key']):
                old_daemon = self.daemons.get(rec['daemon_key'])
                was_bystander = bool(
                    not self._is_replacement_daemon(old_daemon, joined_daemon)
                    or (old_daemon and
                        (not old_daemon.get('exited') or
                         (old_daemon.get('ready_ts') and
                          rec.get('worker_start_ts') and old_daemon['ready_ts']
                          < rec['worker_start_ts'] - _LOG_FLOOR_SLACK_S))))
                rec['chip_attach_s'].clear()
                rec['chip_ids'].clear()
                rec['unknown_chip_count'] = 0
                if (was_bystander and rec.get('first_transport_ts') is None and
                        rec.get('disconnect_count', 0) == 0 and
                        rec.get('connected_total_s', 0.0) == 0.0):
                    rec['netsim_daemon_ready_s'] = None
            rec['daemon_key'] = joined_daemon['daemon_key']
            rec['provisional_daemon'] = joined_via_fallback
            # The first latency sticks: later ticks and daemon restarts
            # must not replace it.
            if rec.get('netsim_daemon_ready_s') is None:
                if joined_daemon.get('ready_ts') and rec.get('worker_start_ts'):
                    rec['netsim_daemon_ready_s'] = max(
                        0.0, joined_daemon['ready_ts'] -
                        min(joined_daemon['start_wall'],
                            rec['worker_start_ts']))
                elif joined_daemon.get('netsim_daemon_ready_s') is not None:
                    rec['netsim_daemon_ready_s'] = joined_daemon[
                        'netsim_daemon_ready_s']

            conns = port_to_conns.get(joined_daemon.get('grpc_port'), [])
            emu_conns = [c for c in conns if c['serial'] == serial]
            emu_peer_ports = {
                c['peer_port'] for c in emu_conns if c.get('peer_port')
            }
            other_peer_ports = {
                c['peer_port']
                for c in conns
                if c['serial'] != serial and c.get('peer_port')
            }

            if emu_conns and rec['first_transport_ts'] is None:
                rec['first_transport_ts'] = now_wall

            dev_name = rec.get('device_name') or ''
            dup_name = bool(dev_name and
                            display_name_counts.get(dev_name, 0) > 1)
            rec['ambiguous'] = bool(dup_name and not emu_peer_ports)

            tailer = joined_daemon.get('tailer')
            unknown_adds = []
            if tailer is not None:
                # With one live emulator, events that match no device are
                # its own unless they name another session emulator, such
                # as one that exited while sharing this daemon.
                other_names = set()
                if live_emulator_count == 1:
                    other_names = {
                        r.get('device_name')
                        for k, r in self.emulator_records.items()
                        if k != s_key and r.get('device_name')
                    }
                log_floor = (rec['min_log_ts'] - _LOG_FLOOR_SLACK_S
                             if rec.get('min_log_ts') is not None else None)
                for c_ts, _, c_dev in tailer.connections:
                    if log_floor is not None and c_ts < log_floor:
                        continue
                    if (not dup_name and
                            c_dev == dev_name) or (not dev_name and
                                                   live_emulator_count == 1 and
                                                   c_dev not in other_names):
                        if (rec['first_transport_ts'] is None or
                                c_ts < rec['first_transport_ts']):
                            rec['first_transport_ts'] = c_ts
                for add_ev in tailer.chip_adds:
                    if log_floor is not None and add_ev['ts'] < log_floor:
                        continue
                    ev_port = add_ev.get('peer_port')
                    if ev_port and ev_port in other_peer_ports:
                        continue
                    if dup_name and ev_port and emu_peer_ports:
                        matches_dev = (ev_port in emu_peer_ports)
                    else:
                        matches_dev = (add_ev['device_name'] == dev_name or
                                       bool(ev_port and
                                            ev_port in emu_peer_ports))
                        if (dup_name and matches_dev and
                                not (ev_port and ev_port in emu_peer_ports)):
                            rec['ambiguous'] = True
                    if (not matches_dev and live_emulator_count == 1 and
                            add_ev['device_name'] not in other_names):
                        matches_dev = True
                    if not matches_dev:
                        continue
                    if add_ev['device_name'] and not rec.get('device_name'):
                        rec['device_name'] = add_ev['device_name']
                    if (rec['first_transport_ts'] is None or
                            add_ev['ts'] < rec['first_transport_ts']):
                        rec['first_transport_ts'] = add_ev['ts']
                    if add_ev.get('chip_id') is not None:
                        rec['chip_ids'].add(add_ev['chip_id'])
                    kind = add_ev.get('kind') or '?'
                    if kind != '?':
                        delta = max(
                            0.0, add_ev['ts'] -
                            (rec['worker_start_ts'] or add_ev['ts']))
                        if kind not in rec['chip_attach_s']:
                            rec['chip_attach_s'][kind] = (delta, False)
                    else:
                        unknown_adds.append(add_ev['ts'])
                rec['unknown_chip_count'] = len(unknown_adds)

            if (rec['first_transport_ts'] is not None and
                    rec.get('worker_start_ts')):
                rec['netsim_connect_s'] = max(
                    0.0, rec['first_transport_ts'] - rec['worker_start_ts'])

            prev_state = rec.get('state')
            has_chips = bool(rec['chip_attach_s'] or
                             rec['unknown_chip_count'] > 0)

            if emu_conns or (has_chips and not joined_daemon.get('exited')):
                if (not has_chips and
                        rec['adb_online_since_mono'] is not None and
                    (now_mono - rec['adb_online_since_mono']) >= 120.0):
                    rec['state'] = 'CONNECTED_NO_CHIPS'
                else:
                    rec['state'] = 'CONNECTED'
                if rec['connected_start_mono'] is None:
                    rec['connected_start_mono'] = now_mono
            else:
                if prev_state in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
                    rec['state'] = 'DISCONNECTED'
                    rec['disconnect_count'] += 1
                    if rec['connected_start_mono'] is not None:
                        rec['connected_total_s'] += max(
                            0.0, now_mono - rec['connected_start_mono'])
                        rec['connected_start_mono'] = None
                elif not self._is_settled(rec):
                    if gone or worker_exited or host_worker_gone:
                        rec['state'] = 'FAILED(worker_exit_no_connect)'
                    else:
                        first_seen_fallback = (e_data['first_seen']
                                               if e_data.get('first_seen')
                                               is not None else now_mono)
                        elapsed_wait = now_mono - (rec.get('first_seen_mono')
                                                   if rec.get('first_seen_mono')
                                                   is not None else
                                                   first_seen_fallback)
                        if elapsed_wait >= 50.0:
                            rec['state'] = 'STALLED'
                        else:
                            rec['state'] = 'CONNECTING'
            if not self._is_settled(rec):
                joined_daemon['joined_emulators'].append(rec)

        for d in self.daemons.values():
            if d.get('exited') or d.get('client_mode_recorded'):
                if d.get('client_mode_recorded'):
                    d['zero_devices_since_mono'] = None
                    d['orphaned'] = False
                continue
            self._backfill_daemon_version_from_stats(d)
            all_joined = [
                r for r in self.emulator_records.values()
                if r.get('daemon_key') == d['daemon_key'] and
                r.get('state') != 'DISABLED'
            ]
            d['all_joined_emulators'] = all_joined
            joined = [
                r for r in d.get('joined_emulators', [])
                if not self._is_settled(r) and r.get('state') != 'DISABLED'
            ]
            tailer = d.get('tailer')
            has_any_dev = bool(joined or (tailer and tailer.created_devices))
            if not has_any_dev:
                if d.get('zero_devices_since_mono') is None:
                    d['zero_devices_since_mono'] = now_mono
                elif (now_mono - d['zero_devices_since_mono']
                     ) >= 30.0 and not d.get('orphaned'):
                    d['orphaned'] = True
                    self._record_event(now_wall, f"ORPHANED(pid={d['pid']})")
            else:
                d['zero_devices_since_mono'] = None
                d['orphaned'] = False

            if len(joined) == 1 and len(all_joined) == 1:
                rec = joined[0]
                log_floor = (rec['min_log_ts'] - _LOG_FLOOR_SLACK_S
                             if rec.get('min_log_ts') is not None else None)
                tailer = d.get('tailer')
                wifi_ts = next(
                    (wt for wt in (tailer.wifi_associations if tailer else ())
                     if log_floor is None or wt >= log_floor), None)
                if wifi_ts is not None and rec.get('worker_start_ts'):
                    rec['wifi_associated_s'] = max(
                        0.0, wifi_ts - rec['worker_start_ts'])
                d_same = bool(d.get('same_user', True))
                d_exp_uid = _resolve_expected_uid(d.get('uid'),
                                                  same_user=d_same)
                if (_is_real_dir(d.get('artifact_dir')) and
                        not self._is_disallowed_daemon_art_dir(
                            d.get('artifact_dir'),
                            d_same,
                            expected_uid=d_exp_uid) and
                    (not rec['chip_attach_s'] or
                     any(is_approx
                         for _, is_approx in rec['chip_attach_s'].values()))):
                    stats_path = os.path.join(d['artifact_dir'],
                                              'netsim_session_stats.json')
                    unk_ts = [
                        a['ts']
                        for a in (tailer.chip_adds if tailer else [])
                        if a.get('kind') == '?' and
                        (log_floor is None or a['ts'] >= log_floor)
                    ]
                    not_before = max(d['start_wall'],
                                     rec.get('min_log_ts') or 0.0)
                    _, resolved, new_cache = resolve_session_stats_chips(
                        stats_path,
                        unk_ts,
                        joined_emulator_count=len(joined),
                        cached_state=d.get('stats_cache'),
                        not_before=not_before,
                        require_self_owner=d_same,
                        expected_uid=d_exp_uid)
                    d['stats_cache'] = new_cache
                    if resolved:
                        w_start = rec.get('worker_start_ts') or d['start_wall']
                        for k, (ev_ts, is_approx) in resolved.items():
                            # Never trade an exact attach time for an estimate.
                            prev = rec['chip_attach_s'].get(k)
                            if prev is not None and not prev[1]:
                                continue
                            rec['chip_attach_s'][k] = (max(
                                0.0, ev_ts - w_start), is_approx)

            self.pcap_mgr.poll_and_manage_daemon(d,
                                                 all_joined,
                                                 now_mono=now_mono)

    def _format_chip_summary(self, rec):
        if rec['chip_attach_s']:
            parts = []
            for k in sorted(rec['chip_attach_s'].keys()):
                val_s, is_approx = rec['chip_attach_s'][k]
                prefix = '~' if is_approx else ''
                parts.append(f"{k}:{prefix}{val_s:.1f}s")
            return ','.join(parts)
        if rec.get('unknown_chip_count', 0) > 0:
            return f"chips={rec['unknown_chip_count']}"
        return 'no-chips'

    def get_dashboard_snapshot(self, now_wall=None):
        if now_wall is None:
            now_wall = time.time()
        daemon_rows = []
        active_daemons = [
            d for d in self.daemons.values()
            if not d.get('exited') and not d.get('client_mode_recorded')
        ]
        for idx, d in enumerate(active_daemons):
            uptime_s = max(0, int(now_wall - d['start_wall']))
            ver = d.get('version') or '?'
            ready_str = (f"{d['netsim_daemon_ready_s']:.2f}s" if
                         d.get('netsim_daemon_ready_s') is not None else 'N/A')
            conns = d.get('active_socket_conns', 0)
            ui_str = 'off' if d.get('frontend_disabled') else 'on'
            log_str = 'ok' if d.get('log_available') else 'unavail'
            pcap_bytes = d.get('pcap_live_bytes', 0)
            pcap_mode = ('auto' if self.pcap_mgr.enabled else
                         ('on' if d.get('pcap_flag') else 'off'))
            pcap_str = f"{pcap_mode} ({pcap_bytes / 1024.0:.1f}KB)"
            label = ("Netsim Daemon"
                     if len(active_daemons) == 1 else f"Netsim Daemon #{idx+1}")
            val = (
                f"pid={d['pid']} | v={ver} | up={uptime_s}s | "
                f"restarts={self.restart_count} | ready={ready_str} | "
                f"conns={conns} | ui={ui_str} | log={log_str} | pcap={pcap_str}"
            )
            daemon_rows.append((label, val))

        daemon_status_row = None
        if not active_daemons:
            if self.stale_inis:
                stale_paths = ', '.join(
                    os.path.basename(p) for p in self.stale_inis)
                daemon_status_row = f"STALE_INI ({stale_paths})"
            else:
                daemon_status_row = "N/A"

        chosen_by_serial = {}
        for rec in self.emulator_records.values():
            serial = rec['serial']
            prev = chosen_by_serial.get(serial)
            if prev is None:
                chosen_by_serial[serial] = rec
                continue
            prev_settled = (self._is_settled(prev) or
                            self._is_exited_disabled(prev))
            cur_settled = (self._is_settled(rec) or
                           self._is_exited_disabled(rec))
            if prev_settled and not cur_settled:
                chosen_by_serial[serial] = rec
            elif prev_settled == cur_settled and (
                    rec.get('first_seen_mono') or
                    0.0) >= (prev.get('first_seen_mono') or 0.0):
                chosen_by_serial[serial] = rec
        emulator_rows = {}
        for serial, rec in chosen_by_serial.items():
            st = rec['state']
            amb = ' [ambiguous]' if rec.get('ambiguous') else ''
            conn_s = (f" {rec['netsim_connect_s']:.2f}s"
                      if rec.get('netsim_connect_s') is not None else '')
            chips = self._format_chip_summary(rec)
            emulator_rows[serial] = (f"netsim: {st}{conn_s} ({chips}) | "
                                     f"disc={rec['disconnect_count']}{amb}")

        return {
            'daemon_rows': daemon_rows,
            'daemon_status_row': daemon_status_row,
            'emulator_rows': emulator_rows,
        }

    def get_csv_fields(self):
        """This tick's CSV columns: daemon totals and every emulator's state.

        Covers emulators adb does not list yet, since netsim can connect first,
        and ones that have exited.
        """
        active_daemons = [
            d for d in self.daemons.values()
            if not d.get('exited') and not d.get('client_mode_recorded')
        ]
        if active_daemons:
            d0 = active_daemons[0]
            netsim_up = 1
            netsim_pid = d0['pid']
            netsim_conns = sum(
                d.get('active_socket_conns', 0) for d in active_daemons)
            netsim_devices = sum(
                len(d.get('joined_emulators', [])) for d in active_daemons)
        else:
            netsim_up = 0
            netsim_pid = ''
            netsim_conns = 0
            netsim_devices = 0

        fields = {
            'netsim_up': netsim_up,
            'netsim_pid': netsim_pid,
            'netsim_restarts': self.restart_count,
            'netsim_conns': netsim_conns,
            'netsim_devices': netsim_devices,
        }
        for key, rec in self.emulator_records.items():
            fields[f'netsim_state_{key}'] = rec['state']
        return fields

    def format_summary_sections(self, now_mono=None):
        if now_mono is None:
            now_mono = time.monotonic()
        lines = ["Netsim daemon lifecycle:"]
        if not self.timeline and not self.daemons:
            lines.append("  No netsimd daemon activity observed.")
        else:
            for d in self.daemons.values():
                status = (f"EXITED({d.get('exit_reason')})" if d.get('exited')
                          else ('ORPHANED' if d.get('orphaned') else 'RUNNING'))
                ready_s = (f"{d['netsim_daemon_ready_s']:.2f}s"
                           if d.get('netsim_daemon_ready_s') is not None else
                           'N/A')
                lines.append(
                    f"  Daemon pid={d['pid']} spawner={d['spawner']} "
                    f"version={d.get('version') or 'N/A'} port={d.get('grpc_port') or 'N/A'} "
                    f"ready_s={ready_s} status={status} restarts={self.restart_count}"
                )
            for ts, ev in self.timeline:
                t_str = time.strftime('%H:%M:%S', time.localtime(ts))
                lines.append(f"  [{t_str}] {ev}")
        lines.append("")

        lines.append("Netsim attach:")
        if not self.emulator_records:
            lines.append("  No emulator netsim sessions recorded.")
        else:
            for key, rec in self.emulator_records.items():
                ready_s = (f"{rec['netsim_daemon_ready_s']:.2f}s"
                           if rec.get('netsim_daemon_ready_s') is not None else
                           'N/A')
                conn_s = (f"{rec['netsim_connect_s']:.2f}s"
                          if rec.get('netsim_connect_s') is not None else 'N/A')
                chips = self._format_chip_summary(rec)
                wifi_s = (f"{rec['wifi_associated_s']:.2f}s" if
                          rec.get('wifi_associated_s') is not None else 'N/A')
                tot_conn = rec.get('connected_total_s', 0.0)
                if rec.get('connected_start_mono') is not None:
                    end = rec.get('missing_since_mono')
                    if end is None:
                        end = now_mono
                    tot_conn += max(0.0, end - rec['connected_start_mono'])
                amb = ' (ambiguous)' if rec.get('ambiguous') else ''
                lines.append(
                    f"  {key} ({rec.get('device_name') or rec['serial']}){amb}: "
                    f"state={rec['state']} ready_s={ready_s} connect_s={conn_s} "
                    f"chips=[{chips}] wifi_assoc_s={wifi_s} "
                    f"disconnects={rec['disconnect_count']} connected_time={tot_conn:.1f}s"
                )
        lines.append("")

        lines.append("Netsim pcap manifest:")
        if not self.pcap_mgr.enabled:
            lines.append("  Pcap collection disabled (pass --netsim-pcap).")
        else:
            if not self.pcap_mgr.copied_files:
                lines.append("  No pcap files copied.")
            else:
                for path, meta in sorted(self.pcap_mgr.copied_files.items()):
                    sz = meta[0] if isinstance(meta, tuple) else meta
                    lines.append(f"  {path} ({sz} bytes)")
            for note in self.pcap_mgr.skipped_notes:
                lines.append(f"  Note: {note}")
        lines.append("")
        return lines

    def cleanup(self):
        """Turns off the captures this monitor enabled and harvests pcaps.

        Runs at most once, and only with --netsim-pcap.
        """
        if self._cleaned_up or not self.pcap_mgr.enabled:
            return
        self._cleaned_up = True
        self.pcap_mgr.cleanup(self.daemons)


def print_dashboard(os_name,
                    host_total,
                    qemu_stats,
                    netsim_cpu,
                    guest_results,
                    host_ram,
                    host_swap,
                    paging_str,
                    iter_count,
                    term_width,
                    force_clear,
                    unmapped_qemu,
                    netsim_ram,
                    gpu_load,
                    gpu_ram,
                    emu_gpu_stats,
                    temp_str="N/A",
                    disk_str="N/A",
                    is_once=False,
                    batt_str="N/A",
                    hw_specs="N/A",
                    netsim_snapshot=None):
    buf = []

    def display(text):
        buf.append(text)

    if not is_once:
        if force_clear:
            display('\033[2J\033[H')
        else:
            display('\033[H\033[J')

    width = max(76, min(120, term_width - 1))
    bar_width, val_width = 20, width - 60
    c1, c2 = Colors.BLUE + Colors.BOLD, Colors.RESET

    display(f"{c1}┌{'─' * (width - 2)}┐{c2}\033[K")
    logo = f"{Colors.BLUE}G{Colors.RED}o{Colors.YELLOW}o{Colors.BLUE}g{Colors.GREEN}l{Colors.RED}e{Colors.RESET}"
    title_text = "Android Emulator & Device Resource Monitor"
    line_content = f" {logo}   {title_text}"
    visible_len = len(RE_ANSI.sub('', line_content))
    padding = max(0, width - 2 - visible_len)
    display(
        f"{c1}│{c2}{Colors.BOLD}{line_content}{' ' * padding}{c2}{c1}│{c2}\033[K"
    )
    display(f"{c1}├{'─' * (width - 2)}┤{c2}\033[K")

    def print_section(title, right_badge="", color=Colors.HEADER):
        inside_text = f" {title}"
        v_title_len = len(RE_ANSI.sub('', inside_text))
        v_badge_len = len(RE_ANSI.sub('', right_badge))
        if v_title_len + v_badge_len > width - 4:
            inside_text = inside_text[:max(0, (width - 4) - v_badge_len -
                                           1)] + "…"
            v_title_len = len(RE_ANSI.sub('', inside_text))

        padding = max(0, (width - 2) - (v_title_len + v_badge_len))
        display(
            f"{c1}│{c2}{color}{Colors.BOLD}{inside_text}{right_badge}{' ' * padding}{c2}{c1}│{c2}\033[K"
        )

    def get_color(val_str, label):
        try:
            val = get_val(val_str)

            if any(x in val_str for x in [
                    "Unsupported", "No ADB", "Error", "Timeout", "Hang"
            ]) or "Calculating" in val_str:
                return Colors.CYAN
            if val == 0.0 and "N/A" in val_str and "%" not in val_str:
                return Colors.CYAN

            if "Battery" in label:
                return Colors.GREEN if val > 20 else Colors.YELLOW if val > 10 else Colors.RED + Colors.BOLD
            if "°C" in val_str:
                return Colors.RED + Colors.BOLD if val > 85 else Colors.YELLOW if val > 70 else Colors.GREEN
            if any(x in val_str for x in ["pages/s", "Out:", "KB/s"]):
                return Colors.RED + Colors.BOLD if val > 5000 else Colors.YELLOW if val > 1000 else Colors.GREEN
            if "%" not in val_str:
                return Colors.CYAN

            if any(x in label for x in [
                    "Total Device CPU", "Host QEMU CPU",
                    "Unmapped Emulator CPU", "Network Simulator"
            ]):
                return Colors.RED + Colors.BOLD if val > 750 else Colors.RED if val > 350 else Colors.YELLOW if val > 150 else Colors.GREEN

            # Standard percent-based threshold
            if val > 90:
                return Colors.RED + Colors.BOLD
            elif val > 70:
                return Colors.RED
            elif val > 40:
                return Colors.YELLOW
            else:
                return Colors.GREEN
        except Exception:
            return Colors.CYAN

    def print_row(label, value):
        c_val = str(value)
        color = get_color(c_val, label)
        raw_val = 0.0
        try:
            raw_val = get_val(c_val)
        except Exception:
            pass

        is_placeholder = any(
            x in c_val
            for x in ["Unsupported", "No ADB", "Error", "Timeout", "Hang"
                     ]) or "Calculating" in c_val or "NaN" in c_val
        try:
            if math.isnan(raw_val) or (raw_val == 0.0 and "N/A" in c_val and
                                       "%" not in c_val):
                is_placeholder = True
        except Exception:
            pass

        is_alert = False
        if not is_placeholder:
            try:
                if "Battery" in label:
                    if raw_val <= 10:
                        is_alert = True
                elif "°C" in c_val:
                    if raw_val > 85:
                        is_alert = True
                elif "%" in c_val:
                    threshold = 750 if any(x in label for x in [
                        "Total Device CPU", "Host QEMU CPU",
                        "Unmapped Emulator CPU", "Network Simulator"
                    ]) else 90
                    if raw_val > threshold:
                        is_alert = True
                        color = Colors.RED + Colors.BOLD
            except Exception:
                pass

        try:
            val_to_clamp = 0.0 if is_placeholder else raw_val
            clamped_val = min(100.0, max(0.0, val_to_clamp))
            filled = int((clamped_val / 100.0) * bar_width)
            half = 1 if ((clamped_val / 100.0) * bar_width -
                         filled) >= 0.5 else 0
            bar = f"[{'█' * filled}{'▌' * half}{' ' * max(0, (bar_width - filled - half))}]"
        except Exception:
            bar = f"[{' ' * bar_width}]"

        r_c, l_c = (Colors.CYAN,
                    Colors.BOLD) if is_placeholder else (color, Colors.BOLD)

        # Override host visual labels to Blue strictly if threshold warnings aren't already actively triggering
        if ("Host QEMU" in label and not is_alert and not is_placeholder and
                color in (Colors.GREEN, Colors.CYAN)):
            r_c = Colors.BLUE

        # Ensure we strip ANSI before calculating offsets
        c_val_clean = RE_ANSI.sub('', c_val)
        c_val_clean_len = len(c_val_clean)

        if "%" in c_val or "°C" in c_val:
            if is_alert:
                max_base_len = max(0, val_width - 5)
                c_val_display = (c_val_clean[:max(0, max_base_len - 1)] +
                                 "…" if c_val_clean_len > max_base_len else
                                 c_val_clean)
                c_val_display += " [!!]"
            else:
                c_val_display = c_val_clean[:max(
                    0, val_width -
                    1)] + "…" if c_val_clean_len > val_width else c_val_clean
            display(
                f"{c1}│{c2} {l_c}{label:<30}{c2} {c1}│{c2} {r_c}{bar} {c_val_display:<{val_width}}{c2} {c1}│{c2}\033[K"
            )
        else:
            full_w = val_width + bar_width + 3
            if is_alert:
                max_base_len = max(0, full_w - 5)
                c_val_display = (c_val_clean[:max(0, max_base_len - 1)] +
                                 "…" if c_val_clean_len > max_base_len else
                                 c_val_clean) + " [!!]"
            else:
                c_val_display = c_val_clean[:max(
                    0, full_w -
                    1)] + "…" if c_val_clean_len > full_w else c_val_clean
            display(
                f"{c1}│{c2} {l_c}{label:<30}{c2} {c1}│{c2} {r_c}{c_val_display:<{full_w}}{c2} {c1}│{c2}\033[K"
            )

    display_os = "macOS" if os_name == "Darwin" else os_name
    if hw_specs != "N/A":
        specs_text = f" Host Specs: {hw_specs}"
        visible_specs_len = len(RE_ANSI.sub('', specs_text))
        if visible_specs_len > width - 4:
            specs_text = specs_text[:max(0, width - 5)] + "…"
            visible_specs_len = len(RE_ANSI.sub('', specs_text))

        padding = max(0, width - 2 - visible_specs_len)
        display(
            f"{c1}│{c2}{Colors.DIM}{specs_text}{' ' * padding}{c2}{c1}│{c2}\033[K"
        )
        display(f"{c1}├{'─' * (width - 2)}┤{c2}\033[K")

    print_section(f"HOST SYSTEM ({display_os})", color=Colors.YELLOW)
    display(f"{c1}├{'─' * 32}┬{'─' * (width - 35)}┤{c2}\033[K")
    print_row("Complete System CPU", host_total)
    print_row("Complete System RAM", host_ram)
    print_row("Backup Memory (Swap) Usage", host_swap)
    print_row("Memory Swapping Activity", paging_str)
    print_row("Host GPU Load", gpu_load)
    print_row("Host GPU Memory", gpu_ram)
    print_row("Host Thermal Temperature",
              temp_str if temp_str != '0.0' else 'N/A')
    print_row("Host Disk I/O Rate", disk_str)
    if batt_str != "N/A":
        print_row("Laptop Battery Status", batt_str)

    if unmapped_qemu:
        sum_cpu = sum(get_val(c) for p, c, r in unmapped_qemu)
        sum_ram = sum(get_val(r) for p, c, r in unmapped_qemu)
        pids = [str(p) for p, c, r in unmapped_qemu]
        pid_str = ", ".join(
            pids[:4]) + (f" ... (+{len(pids)-4} more)" if len(pids) > 4 else "")

        print_row("Unmapped Emulator CPU", f"{sum_cpu:.1f}% (PIDs: {pid_str})")
        print_row("Unmapped Emulator RAM", f"{sum_ram:.1f}MB (PIDs: {pid_str})")

    print_row("Network Simulator CPU", netsim_cpu)
    print_row("Network Simulator RAM", netsim_ram)
    if netsim_snapshot:
        for d_label, d_val in netsim_snapshot.get('daemon_rows', ()):
            print_row(d_label, d_val)
        if (not netsim_snapshot.get('daemon_rows') and
                netsim_snapshot.get('daemon_status_row')):
            print_row("Netsim Daemon", netsim_snapshot['daemon_status_row'])

    if not guest_results:
        display(f"{c1}├{'─' * 32}┴{'─' * (width - 35)}┤{c2}\033[K")
        print_section("GUEST SYSTEM (ADB)", color=Colors.GREEN)
        display(f"{c1}├{'─' * 32}┬{'─' * (width - 35)}┤{c2}\033[K")
        print_row("Guest CPU", "N/A (No devices connected)")
        print_row("Guest RAM Usage", "N/A")
        print_row("Guest Swap Memory", "N/A")
    else:
        for serial, (disp_name, tags, g_diff, g_real, g_ram,
                     g_swap) in guest_results.items():
            display(f"{c1}├{'─' * 32}┴{'─' * (width - 35)}┤{c2}\033[K")
            clean_name = re.sub(
                r" \| Android [^|]+ \(API ([^|]+)\) \| Build: ([^|]+)",
                r" | API \1 | \2", disp_name)
            print_section(f"GUEST SYSTEM ({clean_name})",
                          right_badge=tags,
                          color=Colors.GREEN)
            display(f"{c1}├{'─' * 32}┬{'─' * (width - 35)}┤{c2}\033[K")
            print_row("Total Device CPU Usage", g_diff)
            print_row("Adjusted CPU Load", g_real)
            m_ram = re.search(
                r"(\d+\.\d+%) \(([^,]+), (\d+ errs), (.*?) data\)", g_ram)
            errs_to_print = None
            if m_ram:
                ram_pct, ram_size, errs, disk = m_ram.groups()
                print_row("Guest RAM Usage", f"{ram_pct} ({ram_size})")
                print_row("Guest Disk Space", disk)
                errs_to_print = errs
            else:
                print_row("Guest RAM Usage", g_ram)
                print_row("Guest Disk Space", "N/A")

            is_disconnected = "[DISCONNECTED]" in tags
            if not is_disconnected and serial in qemu_stats:
                print_row("Host QEMU CPU", qemu_stats[serial][0])
                print_row("Host QEMU RAM", qemu_stats[serial][1])

            print_row("Guest Swap Memory", g_swap)
            if (not is_disconnected and emu_gpu_stats and
                    serial in emu_gpu_stats):
                print_row("Host QEMU GPU Load", emu_gpu_stats[serial])

            if errs_to_print:
                print_row("Guest Logcat Errors", errs_to_print)
            else:
                print_row("Guest Logcat Errors", "N/A")

            if (netsim_snapshot and
                    serial in netsim_snapshot.get('emulator_rows', {}) and
                (not is_disconnected or serial not in qemu_stats)):
                print_row("Netsim Status",
                          netsim_snapshot['emulator_rows'][serial])

    display(f"{c1}└{'─' * 32}┴{'─' * (width - 35)}┘{c2}\033[K")
    msg = f"Last Updated: {time.strftime('%H:%M:%S')}" if is_once else f"Press Ctrl+C to exit. Last Updated: {time.strftime('%H:%M:%S')} | Refreshing{('.' * (iter_count % 4)):<3}"
    display(f"\n{Colors.YELLOW}{msg}{Colors.RESET}\033[K")

    if not is_once:
        display('\033[J')
    sys.stdout.write('\n'.join(buf) + '\n')
    sys.stdout.flush()


def format_boot_log(entries):
    """Summary lines for boot log entries of (elapsed, cpu, ram[, swap])."""
    lines = []
    for elapsed, cpu, ram, *swap in entries:
        line = f"    {elapsed} | CPU: {cpu:<15} | RAM: {ram}"
        lines.append(f"{line} | Swap: {swap[0]}" if swap else line)
    return lines


def _build_summary_lines(summary_data, end_time, ts_data):
    duration = max(0.1, end_time - summary_data['start_time'])

    def get_p95(col_name):
        if not ts_data:
            return 0.0
        vals = [
            r[col_name] for r in ts_data if col_name in r and
            r[col_name] is not None and math.isfinite(r[col_name])
        ]
        if not vals:
            return 0.0
        vals.sort()
        idx = max(0, min(len(vals) - 1, (95 * len(vals) + 99) // 100 - 1))
        return vals[idx]

    def get_avg(col_name):
        if not ts_data:
            return 0.0
        vals = [
            r[col_name] for r in ts_data if col_name in r and
            r[col_name] is not None and math.isfinite(r[col_name])
        ]
        return sum(vals) / len(vals) if vals else 0.0

    def get_max(col_name):
        if not ts_data:
            return 0.0
        vals = [
            r[col_name] for r in ts_data if col_name in r and
            r[col_name] is not None and math.isfinite(r[col_name])
        ]
        return max(vals) if vals else 0.0

    avg_h_cpu = summary_data['host_cpu_sum'] / max(
        1, summary_data['host_cpu_iterations'])
    avg_h_ram = summary_data['host_ram_sum'] / max(
        1, summary_data['host_ram_iterations'])
    avg_n_cpu = summary_data['netsim_cpu_sum'] / max(
        1, summary_data['netsim_cpu_iterations'])
    avg_n_ram = summary_data.get('netsim_ram_sum', 0.0) / max(
        1, summary_data.get('netsim_ram_iterations', 0))

    p95_h_cpu = get_p95('host_cpu')
    p95_h_ram = get_p95('host_ram')
    p95_n_cpu = get_p95('netsim_cpu')
    p95_n_ram = get_p95('netsim_ram')

    avg_t = get_avg('host_thermal_c')
    max_t = get_max('host_thermal_c')
    p95_t = get_p95('host_thermal_c')

    avg_r = get_avg('host_disk_read_mbs')
    max_r = get_max('host_disk_read_mbs')
    p95_r = get_p95('host_disk_read_mbs')

    avg_w = get_avg('host_disk_write_mbs')
    max_w = get_max('host_disk_write_mbs')
    p95_w = get_p95('host_disk_write_mbs')

    os_info = platform.platform()
    cpu_cores = os.cpu_count() or "Unknown"
    adb_ver = summary_data.get('adb_version')
    if not adb_ver:
        adb_ver = "N/A"
        adb_out = run_cmd(['adb', 'version'], timeout=2, stderr_devnull=True)
        if adb_out:
            out_lines = adb_out.splitlines()
            if out_lines:
                first_line = out_lines[0].strip()
                if first_line and not first_line.lower().startswith(
                    ('error:', 'adb:')):
                    adb_ver = first_line
                    summary_data['adb_version'] = adb_ver

    is_vm = False
    try:
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                if 'hypervisor' in f.read():
                    is_vm = True
        elif platform.system() == "Darwin":
            out = run_cmd(['sysctl', '-n', 'kern.hv_vmm_present'],
                          timeout=2,
                          stderr_devnull=True)
            if out and out.strip() == '1':
                is_vm = True
    except Exception:
        pass

    lines = [
        "Android Emulator & Device Resource Monitor Session Summary", "=" * 60,
        f"Start Time:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(summary_data['start_time']))}",
        f"End Time:    {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}",
        f"Duration:    {duration:.1f} seconds",
        f"Total Loops: {summary_data['iterations']}",
        f"Host OS:      {os_info}",
        f"Host Specs:   {summary_data.get('hardware_specs', 'N/A')}",
        f"Host Cores:   {cpu_cores}", f"Host Virtual: {is_vm}",
        f"ADB Version:  {adb_ver}\n", "HOST SYSTEM:",
        f"  Average CPU Usage: {avg_h_cpu:.1f}%",
        f"  Peak CPU Usage:    {summary_data['host_cpu_max']:.1f}%",
        f"  P95 CPU Usage:     {p95_h_cpu:.1f}%",
        f"  Average RAM Usage: {avg_h_ram:.1f}%",
        f"  Peak RAM Usage:    {summary_data['host_ram_max']:.1f}%",
        f"  P95 RAM Usage:     {p95_h_ram:.1f}%",
        f"  Average Thermal:   {avg_t:.1f}°C" if avg_t else "  Average Thermal:   N/A",
        f"  Peak Thermal:      {max_t:.1f}°C" if max_t else "  Peak Thermal:      N/A",
        f"  P95 Thermal:       {p95_t:.1f}°C" if p95_t else "  P95 Thermal:       N/A",
        f"  Avg Disk Read:     {avg_r:.1f} MB/s",
        f"  Peak Disk Read:    {max_r:.1f} MB/s",
        f"  P95 Disk Read:     {p95_r:.1f} MB/s",
        f"  Avg Disk Write:    {avg_w:.1f} MB/s",
        f"  Peak Disk Write:   {max_w:.1f} MB/s",
        f"  P95 Disk Write:    {p95_w:.1f} MB/s\n"
    ]

    if summary_data['netsim_cpu_iterations'] > 0:
        lines.extend([
            "NETSIM:", f"  Average CPU Usage: {avg_n_cpu:.1f}%",
            f"  Peak CPU Usage:    {summary_data['netsim_cpu_max']:.1f}%",
            f"  P95 CPU Usage:     {p95_n_cpu:.1f}%",
            f"  Average RAM Usage: {avg_n_ram:.1f}MB",
            f"  Peak RAM Usage:    {summary_data.get('netsim_ram_max', 0.0):.1f}MB",
            f"  P95 RAM Usage:     {p95_n_ram:.1f}MB\n"
        ])

    if summary_data.get('netsim_summary_lines'):
        lines.extend(summary_data['netsim_summary_lines'])

    for serial, e_data in summary_data['emulators'].items():
        if e_data.get('_retired_prior_bare'):
            e_data = _combined_bare_entry(e_data, e_data['_retired_prior_bare'])
        avg_g_cpu = e_data['cpu_sum'] / max(1, e_data['cpu_iterations'])
        avg_g_ram = e_data['ram_sum'] / max(1, e_data['ram_iterations'])
        avg_g_swap = e_data.get('swap_sum', 0.0) / max(
            1, e_data.get('swap_iterations', 1))

        p95_g_ram = get_p95(f'emu_ram_{serial}')
        p95_g_swap = get_p95(f'emu_swap_{serial}')

        avg_g_cpu_tot = get_avg(f'emu_cpu_tot_{serial}')
        avg_g_cpu_adj = get_avg(f'emu_cpu_adj_{serial}')
        peak_g_cpu_tot = get_max(f'emu_cpu_tot_{serial}')
        peak_g_cpu_adj = get_max(f'emu_cpu_adj_{serial}')
        p95_g_cpu_tot = get_p95(f'emu_cpu_tot_{serial}')
        p95_g_cpu_adj = get_p95(f'emu_cpu_adj_{serial}')

        lines.extend([
            f"GUEST SYSTEM ({e_data.get('name', serial)}):",
            f"  Average Total CPU:    {avg_g_cpu_tot:.1f}%",
            f"  Peak Total CPU:       {peak_g_cpu_tot:.1f}%",
            f"  P95 Total CPU:        {p95_g_cpu_tot:.1f}%",
            f"  Average Adjusted CPU: {avg_g_cpu_adj:.1f}%",
            f"  Peak Adjusted CPU:    {peak_g_cpu_adj:.1f}%",
            f"  P95 Adjusted CPU:     {p95_g_cpu_adj:.1f}%",
            f"  Average RAM Usage:    {avg_g_ram:.1f}%",
            f"  Peak RAM Usage:       {e_data['ram_max']:.1f}%",
            f"  P95 RAM Usage:        {p95_g_ram:.1f}%",
            f"  Average Swap Usage:   {avg_g_swap:.1f}MB",
            f"  Peak Swap Usage:      {e_data.get('swap_max', 0.0):.1f}MB",
            f"  P95 Swap Usage:       {p95_g_swap:.1f}MB"
        ])

        if e_data.get('qemu_cpu_iterations', 0) > 0:
            avg_q_cpu = e_data['qemu_cpu_sum'] / max(
                1, e_data['qemu_cpu_iterations'])
            avg_q_ram = e_data['qemu_ram_sum'] / max(
                1, e_data['qemu_ram_iterations'])
            p95_q_cpu = get_p95(f'qemu_cpu_{serial}')
            p95_q_ram = get_p95(f'qemu_ram_{serial}')

            lines.extend([
                f"  Host QEMU CPU (Avg): {avg_q_cpu:.1f}%",
                f"  Host QEMU CPU (Max): {e_data['qemu_cpu_max']:.1f}%",
                f"  Host QEMU CPU (P95): {p95_q_cpu:.1f}%",
                f"  Host QEMU RAM (Avg): {avg_q_ram:.1f}MB",
                f"  Host QEMU RAM (Max): {e_data['qemu_ram_max']:.1f}MB",
                f"  Host QEMU RAM (P95): {p95_q_ram:.1f}MB"
            ])

        if e_data.get('boot_log'):
            lines.append("  Boot Progression Log:")
            lines.extend(format_boot_log(e_data['boot_log']))
        lines.append("")

    if len(ts_data) > 1:
        try:
            import plotille
            times = [
                r['time'] - summary_data['start_time_monotonic']
                for r in ts_data
            ]

            def make_chart(times, vals, title, x_label, y_label):
                valid_t, valid_v = [], []
                for t, v in zip(times, vals):
                    if (t is not None and v is not None and math.isfinite(t) and
                            math.isfinite(v)):
                        valid_t.append(t)
                        valid_v.append(v)
                if len(valid_t) < 2:
                    return []

                # Downsample for long sessions
                w_dots = 120
                max_t = max(valid_t)
                min_t = min(valid_t)
                span_t = max_t - min_t

                if span_t > 0:
                    bins = [[] for _ in range(w_dots)]
                    for t, v in zip(valid_t, valid_v):
                        idx = min(int((t - min_t) / span_t * (w_dots - 1)),
                                  w_dots - 1)
                        bins[idx].append(v)

                    valid_t, valid_v = [], []
                    for i, b in enumerate(bins):
                        if b:
                            valid_t.append(min_t + (i / (w_dots - 1)) * span_t)
                            valid_v.append(sum(b) / len(b))

                if len(valid_t) < 2:
                    return []

                fig = plotille.Figure()
                fig.width, fig.height = 60, 10
                fig.x_label, fig.y_label = x_label, y_label
                if valid_t:
                    fig.set_x_limits(min_=0, max_=max(0.001, max(valid_t)))

                try:
                    fig.plot(valid_t, valid_v)
                except Exception:
                    return []
                res = [title]
                res.extend(
                    [RE_ANSI.sub('', line) for line in fig.show().splitlines()])
                res.append("")
                return res

            def extract(key):
                return [
                    v if (v := r.get(key)) is not None and math.isfinite(v) else
                    float('nan') for r in ts_data
                ]

            h_cpu = extract('host_cpu')
            if any(v > 0 for v in h_cpu if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_cpu, "HOST CPU HISTORY CHART:",
                               "Time (s)", "Usage (%)"))

            h_ram = extract('host_ram')
            if any(v > 0 for v in h_ram if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_ram, "HOST RAM HISTORY CHART:",
                               "Time (s)", "Usage (%)"))

            h_t = extract('host_thermal_c')
            if any(v > 0 for v in h_t if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_t, "HOST THERMAL HISTORY CHART:",
                               "Time (s)", "Temp (°C)"))

            h_swp = extract('host_swap')
            if any(v > 0 for v in h_swp if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_swp, "HOST SWAP HISTORY CHART:",
                               "Time (s)", "Usage (%)"))

            h_gpu = extract('host_gpu')
            if any(v > 0 for v in h_gpu if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_gpu, "HOST GPU LOAD HISTORY CHART:",
                               "Time (s)", "Usage (%)"))

            h_gpumem = extract('host_gpu_mem')
            if any(v > 0
                   for v in h_gpumem
                   if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, h_gpumem,
                               "HOST GPU MEMORY HISTORY CHART:", "Time (s)",
                               "Usage/MB"))

            h_rd = extract('host_disk_read_mbs')
            lines.extend(
                make_chart(times, h_rd, "HOST DISK READ HISTORY CHART:",
                           "Time (s)", "Read (MB/s)"))

            h_wr = extract('host_disk_write_mbs')
            lines.extend(
                make_chart(times, h_wr, "HOST DISK WRITE HISTORY CHART:",
                           "Time (s)", "Write (MB/s)"))

            n_cpu = extract('netsim_cpu')
            if any(v > 0 for v in n_cpu if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, n_cpu, "NETSIM CPU HISTORY CHART:",
                               "Time (s)", "Usage (%)"))

            n_ram = extract('netsim_ram')
            if any(v > 0 for v in n_ram if v is not None and not math.isnan(v)):
                lines.extend(
                    make_chart(times, n_ram, "NETSIM RAM HISTORY CHART:",
                               "Time (s)", "MB"))

            for serial in summary_data['emulators'].keys():
                e_data = summary_data['emulators'][serial]
                title_name = e_data['name']
                if " | " in title_name:
                    parts = title_name.split(" | ", 1)
                    if parts[0].startswith("emulator-"):
                        title_name = parts[1]

                guest_charts = []

                e_cpu = extract(f'emu_cpu_tot_{serial}')
                if any(v > 0
                       for v in e_cpu
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, e_cpu,
                                   f"GUEST CPU HISTORY ({serial}):", "Time (s)",
                                   "Usage (%)"))

                e_ram = extract(f'emu_ram_{serial}')
                if any(v > 0
                       for v in e_ram
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, e_ram,
                                   f"GUEST RAM HISTORY ({serial}):", "Time (s)",
                                   "Usage (%)"))

                q_cpu = extract(f'qemu_cpu_{serial}')
                if any(v > 0
                       for v in q_cpu
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, q_cpu,
                                   f"HOST QEMU CPU HISTORY ({serial}):",
                                   "Time (s)", "Usage (%)"))

                q_ram = extract(f'qemu_ram_{serial}')
                if any(v > 0
                       for v in q_ram
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, q_ram,
                                   f"HOST QEMU RAM HISTORY ({serial}):",
                                   "Time (s)", "MB"))

                e_disk = extract(f'emu_disk_pct_{serial}')
                if any(v > 0
                       for v in e_disk
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(
                            times, e_disk,
                            f"GUEST DATA PARTITION FULLNESS ({serial}):",
                            "Time (s)", "Usage (%)"))

                e_swap = extract(f'emu_swap_{serial}')
                if any(v > 0
                       for v in e_swap
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, e_swap,
                                   f"GUEST SWAP MEMORY HISTORY ({serial}):",
                                   "Time (s)", "MB"))

                e_errs = extract(f'emu_errs_{serial}')
                if any(v > 0
                       for v in e_errs
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, e_errs,
                                   f"GUEST LOGCAT ERRORS ({serial}):",
                                   "Time (s)", "Errors"))

                q_cs = extract(f'qemu_cs_{serial}')
                if any(v > 0
                       for v in q_cs
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, q_cs,
                                   f"QEMU CONTEXT SWITCHES ({serial}):",
                                   "Time (s)", "Switches"))

                q_gpu = extract(f'qemu_gpu_{serial}')
                if any(v > 0
                       for v in q_gpu
                       if v is not None and not math.isnan(v)):
                    guest_charts.extend(
                        make_chart(times, q_gpu,
                                   f"HOST QEMU GPU LOAD HISTORY ({serial}):",
                                   "Time (s)", "Usage (%)"))

                if guest_charts:
                    lines.append("=" * 80)
                    lines.append(f"CHARTS FOR: {title_name}")
                    lines.append("=" * 80)
                    lines.append("")
                    lines.extend(guest_charts)
        except ImportError:
            pass

        except Exception:
            pass

    return lines


def save_summary(summary_data,
                 end_time,
                 save_csv=False,
                 save_txt=True,
                 out_dir='.',
                 is_autosave=False):
    if summary_data.get('iterations',
                        0) == 0 and (not save_csv or
                                     not summary_data.get('time_series', [])):
        return

    ts_data = list(summary_data.get('time_series', []))
    lines = (_build_summary_lines(summary_data, end_time, ts_data) if
             (save_txt or not is_autosave) else [])

    out_dir_expanded = os.path.expanduser(out_dir) or '.'
    if save_txt or (save_csv and ts_data):
        try:
            os.makedirs(out_dir_expanded, exist_ok=True)
        except Exception:
            pass

    file_ts = time.strftime('%Y%m%d_%H%M%S')

    txt_ok = not save_txt
    if save_txt:
        filename = os.path.join(
            out_dir_expanded, "device_monitor_summary_autosave.txt"
            if is_autosave else f"device_monitor_summary_{file_ts}.txt")
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            txt_ok = True
            if not is_autosave:
                print(
                    f"\n{Colors.GREEN}Session summary saved to {Colors.BOLD}{pathlib.Path(os.path.abspath(filename)).as_uri()}{Colors.RESET}"
                )
        except Exception as e:
            print(
                f"\n{Colors.RED}Failed to save summary file: {e}{Colors.RESET}")

    if not is_autosave:
        # Print summary to console as requested by user
        print("\n" + "=" * 60)
        print("SESSION SUMMARY (Console Output)")
        print("=" * 60)
        print('\n'.join(lines))
        print("=" * 60 + "\n")

    csv_ok = not (save_csv and ts_data)
    if save_csv and ts_data:
        csv_filename = os.path.join(
            out_dir_expanded, "device_monitor_summary_autosave.csv"
            if is_autosave else f"device_monitor_summary_{file_ts}.csv")

        try:
            import csv
            seen_keys = {}
            for row in ts_data:
                seen_keys.update(dict.fromkeys(row.keys()))
            all_keys = _filter_csv_keys(list(seen_keys), summary_data, ts_data)

            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f,
                                        fieldnames=all_keys,
                                        restval='',
                                        extrasaction='ignore')
                writer.writeheader()
                for row in ts_data:
                    row_copy = dict(row)

                    # Convert monotonic uptime delta back to an absolute UNIX epoch
                    epoch_time = summary_data['start_time'] + (
                        row['time'] - summary_data['start_time_monotonic'])
                    if (not isinstance(epoch_time, (int, float)) or
                            not math.isfinite(epoch_time) or epoch_time < 0):
                        epoch_time = 0.0
                    try:
                        row_copy['time'] = time.strftime(
                            '%Y-%m-%d %H:%M:%S', time.localtime(epoch_time))
                    except (OSError, OverflowError, ValueError):
                        row_copy['time'] = time.strftime(
                            '%Y-%m-%d %H:%M:%S', time.localtime(0))

                    for k, v in row_copy.items():
                        if isinstance(v, float) and not math.isfinite(v):
                            row_copy[k] = ""
                    writer.writerow(row_copy)

            csv_ok = True
            if not is_autosave:
                print(
                    f"{Colors.GREEN}CSV exported to {Colors.BOLD}{pathlib.Path(os.path.abspath(csv_filename)).as_uri()}{Colors.RESET}"
                )
        except Exception as e:
            print(f"\n{Colors.RED}Failed to export CSV file: {e}{Colors.RESET}")

    if not is_autosave:
        for ok, cleanup_file in [
            (txt_ok, "device_monitor_summary_autosave.txt"),
            (csv_ok, "device_monitor_summary_autosave.csv"),
        ]:
            if not ok:
                continue
            target = os.path.join(out_dir_expanded, cleanup_file)
            try:
                if os.path.exists(target):
                    os.remove(target)
            except Exception:
                pass


def handle_sigterm(signum, frame):
    raise KeyboardInterrupt()


def _combined_bare_entry(active_entry, prior_bare):
    combined = dict(active_entry)
    for metric in ('cpu', 'ram', 'swap', 'qemu_cpu', 'qemu_ram'):
        combined[f'{metric}_iterations'] = (
            active_entry.get(f'{metric}_iterations', 0) +
            prior_bare.get(f'{metric}_iterations', 0))
        combined[f'{metric}_sum'] = (active_entry.get(f'{metric}_sum', 0.0) +
                                     prior_bare.get(f'{metric}_sum', 0.0))
        combined[f'{metric}_max'] = max(active_entry.get(f'{metric}_max', 0.0),
                                        prior_bare.get(f'{metric}_max', 0.0))
    if prior_bare.get('boot_log') or active_entry.get('boot_log'):
        combined['boot_log'] = list(prior_bare.get('boot_log', ())) + list(
            active_entry.get('boot_log', ()))
    return combined


def _retire_bare_entry_in_place(e_cur, summary_data):
    prior = e_cur.pop('_retired_prior_bare', None)
    has_samples = (bool(e_cur.get('boot_log')) or any(
        e_cur.get(f'{m}_iterations', 0) > 0
        for m in ('cpu', 'ram', 'swap', 'qemu_cpu', 'qemu_ram')))
    if has_samples:
        snap = _combined_bare_entry(e_cur, prior) if prior else dict(e_cur)
        snap.pop('_retired_prior_bare', None)
        snap.pop('_series_start_iter', None)
        snap['boot_log'] = list(snap.get('boot_log', ()))
        snap['retired'] = True
        snap['host_exited'] = True
        snap['group_pids'] = set()
        snap['group_procs'] = {}
        e_cur['_retired_prior_bare'] = snap
    elif prior is not None:
        e_cur['_retired_prior_bare'] = prior

    for metric in ('cpu', 'ram', 'swap', 'qemu_cpu', 'qemu_ram'):
        e_cur[f'{metric}_iterations'] = 0
        e_cur[f'{metric}_sum'] = 0.0
        e_cur[f'{metric}_max'] = 0.0
    e_cur['boot_log'] = []
    e_cur['_series_start_iter'] = summary_data.get('iterations', 0) + 1


_PER_EMU_NUMERIC_PREFIXES = (
    'emu_cpu_tot_',
    'emu_cpu_adj_',
    'emu_ram_',
    'emu_swap_',
    'emu_errs_',
    'emu_disk_pct_',
    'qemu_cpu_',
    'qemu_ram_',
    'qemu_cs_',
    'qemu_gpu_',
)
_PER_EMU_TS_PREFIXES = _PER_EMU_NUMERIC_PREFIXES + ('netsim_state_',)


def _per_emulator_col_key(col):
    for prefix in _PER_EMU_TS_PREFIXES:
        if col.startswith(prefix):
            return col[len(prefix):]
    return None


def _purge_emulator_ts_columns(summary_data, emu_key):
    cols = [f'{prefix}{emu_key}' for prefix in _PER_EMU_TS_PREFIXES]
    for row in summary_data.get('time_series', ()):
        for col in cols:
            row.pop(col, None)


def _filter_csv_keys(all_keys, summary_data, ts_data):
    emulators = summary_data.get('emulators', {})
    csv_emu_keys = {
        ek for k in all_keys if (ek := _per_emulator_col_key(k)) is not None
    }
    dropped_bare = set()
    for ek in csv_emu_keys:
        if ek in emulators or '_' in ek:
            continue
        suffix = f'_{ek}'
        if not (any(k.endswith(suffix) for k in emulators) or
                any(k.endswith(suffix) for k in csv_emu_keys)):
            continue
        num_cols = [f'{p}{ek}' for p in _PER_EMU_NUMERIC_PREFIXES]
        has_finite = False
        for row in ts_data:
            if any(
                    isinstance(row.get(c), (int,
                                            float)) and math.isfinite(row[c])
                    for c in num_cols):
                has_finite = True
                break
        if not has_finite:
            dropped_bare.add(ek)
    if not dropped_bare:
        return all_keys
    return [
        col for col in all_keys
        if (_per_emulator_col_key(col) not in dropped_bare)
    ]


def _is_stale_offline_placeholder(e_entry):
    return bool(e_entry and not e_entry.get('_pending_host_scan') and
                e_entry.get('first_seen') is None and
                e_entry.get('current_pid') is None and
                not e_entry.get('host_exited') and all(
                    e_entry.get(f'{m}_iterations', 0) == 0
                    for m in ('cpu', 'ram', 'swap', 'qemu_cpu', 'qemu_ram')))


def _can_migrate_bare_serial(summary_data,
                             netsim_tracker,
                             serial,
                             new_pid,
                             live_group_pids,
                             live_group_procs=None):
    new_ctime = (live_group_procs or {}).get(new_pid) if new_pid else None
    bare_emu = summary_data.get('emulators', {}).get(serial)
    if bare_emu is not None:
        if (bare_emu.get('retired') or bare_emu.get('host_exited') or
                bare_emu.get('evicted')):
            return False
        if new_pid and _is_stale_offline_placeholder(bare_emu):
            return False
        b_pid = bare_emu.get('current_pid')
        b_proc_id = bare_emu.get('current_proc_id', (b_pid, None))
        if (b_pid and new_pid and b_pid == new_pid and b_proc_id and
                b_proc_id[1] is not None and new_ctime is not None and
                b_proc_id[1] != new_ctime):
            return False
        if b_pid and new_pid and b_pid != new_pid:
            b_procs = bare_emu.get('group_procs') or {}
            if b_procs and live_group_procs:
                if not _group_procs_match(b_procs, live_group_procs):
                    return False
            elif (b_pid not in live_group_pids and
                  new_pid not in bare_emu.get('group_pids', ())):
                return False
    ns_rec = netsim_tracker.emulator_records.get(serial)
    if ns_rec is not None:
        if ((NetsimTracker._is_settled(ns_rec) or
             NetsimTracker._is_exited_disabled(ns_rec)) and
            (bare_emu is None or bare_emu.get('retired') or
             bare_emu.get('host_exited') or bare_emu.get('evicted'))):
            return False
        ns_pid = ns_rec.get('worker_pid')
        if new_pid and ns_pid is None and (
                bare_emu is None or _is_stale_offline_placeholder(bare_emu)):
            return False
        if (ns_pid and new_pid and ns_pid == new_pid and
                ns_rec.get('worker_create_time') and new_ctime is not None and
                abs(ns_rec['worker_create_time'] - new_ctime) > 2.0):
            return False
        if ns_pid and new_pid and ns_pid != new_pid:
            ns_procs = ns_rec.get('group_procs') or {}
            if ns_procs and live_group_procs:
                if not _group_procs_match(
                        ns_procs, live_group_procs, ctime_tol=2.0):
                    return False
            elif (ns_pid not in live_group_pids and
                  new_pid not in ns_rec.get('group_pids', ())):
                return False
    return True


def _migrate_summary_keys(summary_data, serial, storage_key):
    bare_entry = summary_data['emulators'].pop(serial, None)
    prior_bare = None
    start_iter = 0
    if bare_entry is not None:
        bare_entry.pop('evicted', None)
        prior_bare = bare_entry.pop('_retired_prior_bare', None)
        start_iter = bare_entry.pop('_series_start_iter', 0)
        if storage_key not in summary_data['emulators']:
            summary_data['emulators'][storage_key] = bare_entry
        else:
            dst = summary_data['emulators'][storage_key]
            for metric in ('cpu', 'ram', 'swap', 'qemu_cpu', 'qemu_ram'):
                dst[f'{metric}_iterations'] = (
                    dst.get(f'{metric}_iterations', 0) +
                    bare_entry.get(f'{metric}_iterations', 0))
                dst[f'{metric}_sum'] = (dst.get(f'{metric}_sum', 0.0) +
                                        bare_entry.get(f'{metric}_sum', 0.0))
                dst[f'{metric}_max'] = max(dst.get(f'{metric}_max', 0.0),
                                           bare_entry.get(f'{metric}_max', 0.0))
            if bare_entry.get('boot_log'):
                dst.setdefault('boot_log', []).extend(bare_entry['boot_log'])
            dst.pop('retired', None)
            dst.pop('evicted', None)
            for field in ('current_serial', 'current_pid', 'current_proc_id',
                          'first_seen', 'last_boot_log_time', 'is_booting',
                          'boot_state_known', 'track_boot_stats',
                          'boot_completed', 'last_status',
                          'online_indicator_until', 'last_seen', 'host_exited'):
                if field in bare_entry and bare_entry[field] is not None:
                    dst[field] = bare_entry[field]
            if bare_entry.get('group_pids'):
                dst.setdefault('group_pids',
                               set()).update(bare_entry['group_pids'])
            if bare_entry.get('group_procs'):
                _merge_group_procs(dst.setdefault('group_procs', {}),
                                   bare_entry['group_procs'])
            if bare_entry.get('adb_seen'):
                dst['adb_seen'] = True
            if not bare_entry.get('boot_completed', True):
                dst.pop('boot_finished_time', None)
        if prior_bare is not None:
            summary_data['emulators'][serial] = prior_bare
    col_pairs = [
        (f"{prefix}{serial}", f"{prefix}{storage_key}")
        for prefix in ('emu_cpu_tot_', 'emu_cpu_adj_', 'emu_ram_', 'emu_swap_',
                       'emu_errs_', 'emu_disk_pct_', 'qemu_cpu_', 'qemu_ram_',
                       'qemu_cs_', 'qemu_gpu_', 'netsim_state_')
    ]
    ts_rows = summary_data.get('time_series', ())
    skip_rows = 0
    if start_iter > 0 and ts_rows:
        total_iters = summary_data.get('iterations', len(ts_rows))
        first_iter = total_iters - len(ts_rows) + 1
        skip_rows = max(0, start_iter - first_iter)
    for idx, row in enumerate(ts_rows):
        if idx < skip_rows:
            continue
        for old_col, new_col in col_pairs:
            if old_col in row:
                val = row.pop(old_col)
                existing = row.get(new_col)
                if (new_col not in row or existing == "" or
                    (isinstance(existing, float) and math.isnan(existing))):
                    row[new_col] = val


def compute_paging_stats(pgin, pgout, last_pgin, last_pgout, elapsed, os_name,
                         iter_count):
    paging_unit = "pages/s" if os_name == "Darwin" else "KB/s"
    has_prev = last_pgin > 0 or last_pgout > 0
    if has_prev and elapsed > 0.001:
        in_rate = max(0.0, pgin - last_pgin) / elapsed
        out_rate = max(0.0, pgout - last_pgout) / elapsed
    else:
        in_rate, out_rate = 0.0, 0.0

    if pgin > 0 or pgout > 0:
        if has_prev:
            paging_str = (
                f"In: {int(in_rate)} / Out: {int(out_rate)} ({paging_unit})")
        else:
            paging_str = "Calculating..."
        last_pgin, last_pgout = pgin, pgout
    else:
        paging_str = "N/A"
        in_rate, out_rate = float('nan'), float('nan')
    return paging_str, in_rate, out_rate, last_pgin, last_pgout


def update_device_cache(device_names,
                        disconnect_times,
                        serial_pids,
                        connected_serials,
                        qemu_pids,
                        current_time,
                        timeout=5.0,
                        serial_group_pids=None,
                        live_group_pids=None,
                        serial_group_procs=None,
                        live_group_procs=None,
                        host_fresh=True,
                        devices=None,
                        seen_offline_without_host=None,
                        host_exited_serials=None):
    """Updates the device cache state based on connectivity and PID changes.

    Returns the serials whose cached guest state belongs to an emulator that
    is gone: evicted after the disconnect timeout, or replaced by a new
    process on the same serial.
    """
    forgotten = set()
    reconnected_from_disconnect = set()
    tracked_serials = (set(device_names) | set(serial_pids) |
                       set(disconnect_times))
    if serial_group_pids:
        tracked_serials |= set(serial_group_pids)
    if serial_group_procs:
        tracked_serials |= set(serial_group_procs)
    for s in list(tracked_serials):
        if s not in connected_serials:
            if s in qemu_pids and s not in device_names:
                disconnect_times.pop(s, None)
            elif s not in disconnect_times:
                disconnect_times[s] = current_time
            elif current_time - disconnect_times[s] > timeout:
                device_names.pop(s, None)
                disconnect_times.pop(s, None)
                serial_pids.pop(s, None)
                if serial_group_pids is not None:
                    serial_group_pids.pop(s, None)
                if serial_group_procs is not None:
                    serial_group_procs.pop(s, None)
                if seen_offline_without_host is not None:
                    seen_offline_without_host.discard(s)
                if host_exited_serials is not None:
                    host_exited_serials.discard(s)
                forgotten.add(s)
        elif s in disconnect_times:
            reconnected_from_disconnect.add(s)
            del disconnect_times[s]

    active_tracked = ((tracked_serials - forgotten) if
                      (host_exited_serials is not None or
                       seen_offline_without_host is not None) else set())
    for s in (active_tracked | set(connected_serials) | set(qemu_pids)):
        new_pid = qemu_pids.get(s)
        old_pid = serial_pids.get(s)
        known_group = (serial_group_pids.get(s, set())
                       if serial_group_pids is not None else set())
        raw_live_group = (set(live_group_pids.get(s) or
                              ()) if live_group_pids is not None else set())
        curr_group = (raw_live_group | {new_pid} if
                      (new_pid and new_pid in raw_live_group) else
                      ({new_pid} if new_pid else set()))
        known_procs = (serial_group_procs.get(s)
                       if serial_group_procs is not None else None)
        curr_procs = (live_group_procs.get(s)
                      if live_group_procs is not None else None)
        if known_procs and curr_procs:
            same_group = bool(old_pid and new_pid and
                              _group_procs_match(known_procs, curr_procs))
        else:
            same_group = bool(old_pid and new_pid and
                              (old_pid == new_pid or new_pid in known_group or
                               old_pid in curr_group))
        if seen_offline_without_host is not None:
            had_prior = bool(old_pid or (host_exited_serials is not None and
                                         s in host_exited_serials) or
                             s in seen_offline_without_host)
        else:
            # Legacy direct callers without seen_offline_without_host tracking.
            had_prior = bool(old_pid or s in device_names)
        if new_pid and not same_group and had_prior:
            device_names.pop(s, None)
            disconnect_times.pop(s, None)
            forgotten.add(s)
            if serial_group_pids is not None:
                serial_group_pids[s] = set(curr_group)
            if serial_group_procs is not None:
                serial_group_procs[s] = dict(curr_procs or {new_pid: None})
        elif (new_pid is None and old_pid is None and
              ((host_exited_serials is not None and s in host_exited_serials) or
               (seen_offline_without_host is not None and
                s in seen_offline_without_host)) and
              (s in reconnected_from_disconnect or
               (s in connected_serials and
                (devices or {}).get(s) == 'device'))):
            device_names.pop(s, None)
            disconnect_times.pop(s, None)
            if host_exited_serials is not None:
                host_exited_serials.discard(s)
            if seen_offline_without_host is not None:
                seen_offline_without_host.discard(s)
            forgotten.add(s)

        if new_pid:
            serial_pids[s] = new_pid
            if seen_offline_without_host is not None:
                seen_offline_without_host.discard(s)
            if host_exited_serials is not None:
                host_exited_serials.discard(s)
            if serial_group_pids is not None:
                if same_group or s not in serial_group_pids:
                    serial_group_pids.setdefault(s, set()).update(curr_group)
                else:
                    serial_group_pids[s] = set(curr_group)
            if serial_group_procs is not None:
                src_procs = curr_procs or {p: None for p in curr_group}
                if same_group or s not in serial_group_procs:
                    _merge_group_procs(serial_group_procs.setdefault(s, {}),
                                       src_procs)
                else:
                    serial_group_procs[s] = dict(src_procs)
        elif host_fresh:
            if old_pid is not None and host_exited_serials is not None:
                host_exited_serials.add(s)
            elif (old_pid is None and (host_exited_serials is None or
                                       s not in host_exited_serials) and
                  s in connected_serials and
                  (devices or {}).get(s, 'offline') != 'device' and
                  seen_offline_without_host is not None):
                seen_offline_without_host.add(s)
            serial_pids.pop(s, None)
            if serial_group_pids is not None:
                serial_group_pids.pop(s, None)
            if serial_group_procs is not None:
                serial_group_procs.pop(s, None)
    return forgotten


def _storage_key_for_serial(emulators,
                            serial,
                            pid=None,
                            group_pids=None,
                            proc_id=None,
                            group_procs=None):
    """Returns the session key that owns serial now, preferring pid's key.

    A key retired by a newer emulator on the same serial never matches.
    """
    keys = sorted([
        k for k, v in emulators.items()
        if v.get('current_serial') == serial and not v.get('retired')
    ],
                  key=lambda k: bool(emulators[k].get('evicted')))
    if pid:
        p_ctime = (proc_id[1] if (proc_id and proc_id[0] == pid) else
                   (group_procs or {}).get(pid))
        for k in keys:
            v = emulators[k]
            v_procs = ({} if v.get('host_exited') else
                       (v.get('group_procs') or {}))
            old_proc_id = v.get('current_proc_id', (v.get('current_pid'), None))
            same_primary = (v.get('current_pid') == pid and
                            not v.get('host_exited') and
                            (p_ctime is None or old_proc_id[1] is None or
                             old_proc_id[1] == p_ctime))
            in_group = (not v.get('host_exited') and
                        (_group_procs_match(v_procs, {pid: p_ctime})
                         if v_procs else pid in v.get('group_pids', ())))
            if same_primary or in_group:
                return k
    if group_procs or group_pids:
        for k in keys:
            v = emulators[k]
            if v.get('host_exited'):
                continue
            v_procs = v.get('group_procs') or {}
            if v_procs and group_procs:
                if _group_procs_match(v_procs, group_procs):
                    return k
            elif (v.get('current_pid') in (group_pids or ()) or
                  bool(set(v.get('group_pids', ())) & set(group_pids or ()))):
                return k
    return keys[0] if keys else None


class _HostGroupIdentity(NamedTuple):
    """Live host process-group identity for an emulator serial."""

    pid: Optional[int]
    proc_id: Tuple[Optional[int], Optional[float]]
    group_pids: Set[int]
    group_procs: Dict[int, Optional[float]]

    @classmethod
    def from_scan(
        cls,
        *,
        serial: str,
        pid: Optional[int],
        primary_proc_ids: Mapping[str, Tuple[Optional[int], Optional[float]]],
        emu_group_pids: Mapping[str, Iterable[int]],
        emu_group_info: Mapping[str, Mapping[str, Any]],
    ) -> '_HostGroupIdentity':
        """Constructs the live host process-group identity for `serial`."""
        proc_id = primary_proc_ids.get(serial, (pid, None))
        raw_pids = set(emu_group_pids.get(serial) or ())
        group_pids = (raw_pids | {pid} if (pid and pid in raw_pids) else
                      ({pid} if pid else set()))
        group_procs = _build_live_group_procs(pid, proc_id, raw_pids,
                                              emu_group_info.get(serial))
        return cls(
            pid=pid,
            proc_id=proc_id,
            group_pids=group_pids,
            group_procs=group_procs,
        )


class _ReconciledEmulatorSession(NamedTuple):
    """Outcome of reconciling an emulator's session storage entry."""

    storage_key: str
    entry: Dict[str, Any]
    created: bool
    restarted: bool
    effective_avd: Optional[str]


def _is_replaced_session_entry(*, entry: Mapping[str, Any], entry_key: str,
                               serial: str,
                               host_group: _HostGroupIdentity) -> bool:
    """Whether `entry` is left over from an earlier emulator on `serial`.

    Args:
      entry: Existing session emulator dictionary.
      entry_key: Storage key under which `entry` is stored.
      serial: Active emulator serial (e.g. `emulator-5554`).
      host_group: Live host process-group identity observed for `serial`.

    Returns:
      True when `entry` belongs to a prior session replaced by a new process or
      evicted after disconnect.
    """
    new_pid = host_group.pid
    new_proc_id = host_group.proc_id
    old_pid = entry.get('current_pid')
    old_proc_id = entry.get('current_proc_id', (old_pid, None))
    known_group_pids = (set() if entry.get('host_exited') else entry.get(
        'group_pids', set()))
    known_group_procs = ({} if entry.get('host_exited') else
                         (entry.get('group_procs') or {}))
    same_pid_reused = bool(old_proc_id and new_proc_id and old_proc_id[0] and
                           old_proc_id[0] == new_proc_id[0] and
                           old_proc_id[1] is not None and
                           new_proc_id[1] is not None and
                           old_proc_id[1] != new_proc_id[1])
    if known_group_procs and host_group.group_procs:
        group_match = _group_procs_match(known_group_procs,
                                         host_group.group_procs)
    else:
        group_match = (old_pid == new_pid or new_pid in known_group_pids or
                       old_pid in host_group.group_pids)
    same_group = bool(not same_pid_reused and not entry.get('host_exited') and
                      old_pid and new_pid and group_match)
    was_evicted_emu = bool(
        entry.get('evicted') and not same_group and
        (entry_key != serial or old_pid is not None or
         entry.get('host_exited')))
    return bool((new_pid and not same_group and
                 (old_pid or entry.get('host_exited'))) or was_evicted_emu)


def _new_emulator_entry(*,
                        name: str,
                        storage_key: str,
                        serial: str,
                        host_group: _HostGroupIdentity,
                        adb_seen: bool,
                        has_live_target: bool = True,
                        known_boot: bool = False,
                        is_booting: bool = False,
                        track_boot_stats: bool = False,
                        stale_start_iter: Optional[int] = None,
                        pending_host_scan: bool = False) -> Dict[str, Any]:
    """Creates a fresh per-emulator summary entry for `storage_key`."""
    now_mono = time.monotonic()
    entry: Dict[str, Any] = {
        'name':
            name,
        'current_serial':
            serial,
        'current_pid':
            host_group.pid,
        'current_proc_id':
            host_group.proc_id,
        'group_pids':
            set(host_group.group_pids),
        'group_procs':
            dict(host_group.group_procs),
        'host_exited':
            False,
        'cpu_iterations':
            0,
        'cpu_sum':
            0.0,
        'cpu_max':
            0.0,
        'ram_iterations':
            0,
        'ram_sum':
            0.0,
        'ram_max':
            0.0,
        'swap_iterations':
            0,
        'swap_sum':
            0.0,
        'swap_max':
            0.0,
        'qemu_cpu_iterations':
            0,
        'qemu_cpu_sum':
            0.0,
        'qemu_cpu_max':
            0.0,
        'qemu_ram_iterations':
            0,
        'qemu_ram_sum':
            0.0,
        'qemu_ram_max':
            0.0,
        'first_seen':
            now_mono if (has_live_target or not adb_seen) else None,
        'last_boot_log_time':
            0,
        'boot_log': [],
        'boot_state_known':
            bool(known_boot) if adb_seen else False,
        'is_booting':
            bool(is_booting) if (adb_seen and known_boot) else False,
        'track_boot_stats':
            bool(track_boot_stats),
        'boot_completed': (not is_booting) if
                          (adb_seen and known_boot) else False,
        'last_status':
            'offline',
        'online_indicator_until':
            0,
        'adb_seen':
            bool(adb_seen),
        'last_seen':
            now_mono if adb_seen else 0,
    }
    if pending_host_scan:
        entry['_pending_host_scan'] = True
    if stale_start_iter is not None and storage_key == serial:
        entry['_series_start_iter'] = stale_start_iter
    return entry


def _reset_restarted_emulator_entry(*,
                                    entry: Dict[str, Any],
                                    storage_key: str,
                                    serial: str,
                                    summary_data: Dict[str, Any],
                                    netsim_tracker: NetsimTracker,
                                    host_group: _HostGroupIdentity,
                                    adb_seen: bool,
                                    has_live_target: bool = True,
                                    track_boot_stats: bool = False,
                                    pending_host_scan: bool = False) -> None:
    """Resets per-run boot and process fields when `storage_key` restarts."""
    if storage_key == serial:
        _retire_bare_entry_in_place(entry, summary_data)
        if adb_seen and host_group.pid is None:
            ns_bare = netsim_tracker.emulator_records.get(serial)
            if ns_bare is not None:
                prev_d = netsim_tracker.daemons.get(ns_bare.get('daemon_key'))
                NetsimTracker._reset_for_new_worker(
                    ns_bare,
                    None,
                    time.time(),
                    False,
                    time.monotonic(),
                    worker_ctime=None,
                    prev_exit=(prev_d.get('exit_reason') if prev_d else None))
    entry['first_seen'] = (time.monotonic() if
                           (has_live_target or not adb_seen) else None)
    if pending_host_scan:
        entry['_pending_host_scan'] = True
    entry['last_boot_log_time'] = 0
    entry.pop('boot_finished_time', None)
    entry['boot_state_known'] = False
    entry['boot_completed'] = False
    entry['is_booting'] = False
    entry['track_boot_stats'] = bool(track_boot_stats)
    entry['last_status'] = 'offline'
    entry['online_indicator_until'] = 0
    entry['current_pid'] = host_group.pid
    entry['current_proc_id'] = host_group.proc_id
    entry['group_pids'] = set(host_group.group_pids)
    entry['group_procs'] = dict(host_group.group_procs)
    entry['host_exited'] = False
    if not adb_seen:
        entry['adb_seen'] = False


def _update_emulator_display_name(*, entry: Dict[str, Any], name: str,
                                  model_part: str, effective_avd: Optional[str],
                                  restarted: bool) -> None:
    """Updates `entry['name']` without overwriting a resolved device model."""
    prev_name = entry.get('name', 'Unknown')
    prev_model = (prev_name.split(' | ', 1)[1]
                  if ' | ' in prev_name else prev_name)
    if (not model_part.startswith('Unknown') or
            prev_model.startswith('Unknown') or restarted):
        entry['name'] = name
    elif effective_avd and ' | ' in entry['name']:
        entry['name'] = f"{effective_avd} | {entry['name'].split(' | ', 1)[1]}"


def _reconcile_emulator_session_entry(
    *,
    summary_data: Dict[str, Any],
    netsim_tracker: NetsimTracker,
    bare_key_serials: Set[str],
    serial: str,
    avd_name: Optional[str],
    model_part: str,
    host_group: _HostGroupIdentity,
    adb_seen: bool,
    has_live_target: bool = True,
    host_fresh: bool = True,
    known_boot: bool = False,
    is_booting: bool = False,
    track_boot_stats_on_init: bool = False,
    track_boot_stats_on_reset: bool = False,
    pending_host_scan: bool = False,
) -> _ReconciledEmulatorSession:
    """Finds, creates or resets the entry that takes `serial`'s samples.

    Runs once per tick for each emulator adb lists (`adb_seen`) and for each
    one only the host scan found. Picks the storage key (`<avd>_<serial>`, or
    the bare serial until the AVD is known), moves a bare-serial entry to its
    AVD key, retires other entries that still hold `serial`, and resets an
    entry left by an earlier emulator session. `summary_data`,
    `bare_key_serials` and the netsim records for `serial` are updated to
    match.

    Args:
      host_group: Process group on `serial` in the last host scan; its pid is
        None if the scan found none.
      has_live_target: Whether a process or an online adb device stands
        behind the entry. An adb row without one gets no `first_seen` yet.
      host_fresh: Whether this tick has a new host scan. Only then can a
        missing pid mark the entry `host_exited`.
      known_boot: Whether `is_booting` holds a value read from the device
        this tick. Both are used for a new entry only.
      track_boot_stats_on_init: `track_boot_stats` of a new entry.
      track_boot_stats_on_reset: `track_boot_stats` of a reset entry.
      pending_host_scan: Flags a new or reset entry whose process the next
        host scan has yet to confirm.

    Returns:
      The storage key and entry, whether the entry is new, whether an
      existing one was reset, and the AVD name used for display.
    """
    emulators = summary_data.setdefault('emulators', {})
    stale_start_iter: Optional[int] = None
    if has_live_target and _is_stale_offline_placeholder(emulators.get(serial)):
        emulators.pop(serial, None)
        bare_key_serials.discard(serial)
        _purge_emulator_ts_columns(summary_data, serial)
        stale_start_iter = summary_data.get('iterations', 0) + 1
        ns_stale = netsim_tracker.emulator_records.get(serial)
        if ns_stale is not None and ns_stale.get('worker_pid') is None:
            netsim_tracker.emulator_records.pop(serial, None)

    existing_key = _storage_key_for_serial(emulators,
                                           serial,
                                           host_group.pid,
                                           host_group.group_pids,
                                           proc_id=host_group.proc_id,
                                           group_procs=host_group.group_procs)
    pid_changed = False
    if existing_key:
        e_prev = emulators[existing_key]
        pid_changed = _is_replaced_session_entry(entry=e_prev,
                                                 entry_key=existing_key,
                                                 serial=serial,
                                                 host_group=host_group)
        if existing_key == serial and avd_name:
            storage_key = f"{avd_name}_{serial}"
        elif pid_changed:
            # A new process is a new emulator; until its AVD is known it gets
            # the bare serial, not the old AVD key.
            storage_key = f"{avd_name}_{serial}" if avd_name else serial
        else:
            storage_key = existing_key
    else:
        storage_key = f"{avd_name}_{serial}" if avd_name else serial

    if storage_key == serial:
        bare_key_serials.add(serial)
    elif (not pid_changed and
          (existing_key == serial or serial in bare_key_serials or
           serial in netsim_tracker.emulator_records) and
          _can_migrate_bare_serial(summary_data,
                                   netsim_tracker,
                                   serial,
                                   host_group.pid,
                                   host_group.group_pids,
                                   live_group_procs=host_group.group_procs)):
        bare_key_serials.discard(serial)
        _migrate_summary_keys(summary_data, serial, storage_key)
        netsim_tracker.migrate_bare_record(serial, storage_key,
                                           time.monotonic())
    else:
        bare_key_serials.discard(serial)

    was_retired_target = bool(emulators.get(storage_key, {}).get('retired'))
    # Only the record that owns the serial now takes its samples; a record left
    # by an emulator it replaced is retired.
    for k, v in emulators.items():
        if v.get('current_serial') == serial:
            if k == storage_key:
                v.pop('retired', None)
            else:
                v['retired'] = True

    effective_avd = avd_name
    if (not effective_avd and storage_key != serial and
            storage_key.endswith(f"_{serial}")):
        effective_avd = storage_key[:-len(serial) - 1]
    name = f"{effective_avd or serial} | {model_part}"

    if storage_key not in emulators:
        entry = _new_emulator_entry(name=name,
                                    storage_key=storage_key,
                                    serial=serial,
                                    host_group=host_group,
                                    adb_seen=adb_seen,
                                    has_live_target=has_live_target,
                                    known_boot=known_boot,
                                    is_booting=is_booting,
                                    track_boot_stats=track_boot_stats_on_init,
                                    stale_start_iter=stale_start_iter,
                                    pending_host_scan=pending_host_scan)
        emulators[storage_key] = entry
        if adb_seen:
            _update_emulator_display_name(entry=entry,
                                          name=name,
                                          model_part=model_part,
                                          effective_avd=effective_avd,
                                          restarted=False)
        return _ReconciledEmulatorSession(storage_key=storage_key,
                                          entry=entry,
                                          created=True,
                                          restarted=False,
                                          effective_avd=effective_avd)

    entry = emulators[storage_key]
    if host_fresh:
        entry.pop('_pending_host_scan', None)
    restarted = bool((pid_changed and storage_key == existing_key) or
                     _is_replaced_session_entry(entry=entry,
                                                entry_key=storage_key,
                                                serial=serial,
                                                host_group=host_group) or
                     was_retired_target)
    entry.pop('evicted', None)
    if restarted:
        _reset_restarted_emulator_entry(
            entry=entry,
            storage_key=storage_key,
            serial=serial,
            summary_data=summary_data,
            netsim_tracker=netsim_tracker,
            host_group=host_group,
            adb_seen=adb_seen,
            has_live_target=has_live_target,
            track_boot_stats=track_boot_stats_on_reset,
            pending_host_scan=pending_host_scan)
    elif entry.get('first_seen') is None and has_live_target:
        entry['first_seen'] = time.monotonic()

    if adb_seen:
        entry['adb_seen'] = True
        entry['last_seen'] = time.monotonic()
    entry['current_serial'] = serial
    if host_group.pid:
        entry['current_pid'] = host_group.pid
        entry['current_proc_id'] = host_group.proc_id
        entry.setdefault('group_pids', set()).update(host_group.group_pids)
        _merge_group_procs(entry.setdefault('group_procs', {}),
                           host_group.group_procs)
        entry['host_exited'] = False
    elif host_fresh and entry.get('current_pid'):
        entry['host_exited'] = True
        entry['group_pids'] = set()
        entry['group_procs'] = {}
    _update_emulator_display_name(entry=entry,
                                  name=name,
                                  model_part=model_part,
                                  effective_avd=effective_avd,
                                  restarted=restarted)
    return _ReconciledEmulatorSession(storage_key=storage_key,
                                      entry=entry,
                                      created=False,
                                      restarted=restarted,
                                      effective_avd=effective_avd)


def main():
    exit_code = 0
    orig_sigterm = signal.getsignal(signal.SIGTERM)
    orig_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    parser = argparse.ArgumentParser(
        description="Cross-platform Android Emulator & Device Resource Monitor."
    )

    parser.add_argument('-1',
                        '--once',
                        action='store_true',
                        help="Print metrics once and exit")
    parser.add_argument('--no-summary',
                        action='store_true',
                        help="Disable saving the session summary file")
    parser.add_argument('--csv',
                        action='store_true',
                        help="Export time-series data to CSV on exit")
    parser.add_argument(
        '-o',
        '--out-dir',
        default='.',
        help="Directory to save session summaries and CSV files")
    parser.add_argument('-i',
                        '--interval',
                        type=float,
                        default=2.0,
                        help="Refresh rate interval in seconds (1.0 to 10.0)")
    parser.add_argument(
        '--headless',
        action='store_true',
        help="Suppress terminal output completely (useful for automation/CI)")
    parser.add_argument('--auto-save-interval',
                        type=int,
                        default=5,
                        help="Auto-save interval in minutes (use 0 to disable)")
    parser.add_argument(
        '--netsim-pcap',
        nargs='?',
        const='',
        default=None,
        metavar='DIR',
        help=
        "Enable netsim pcap capture and harvest to DIR (default: <out-dir>/netsim_pcaps)"
    )
    parser.add_argument(
        '--netsim-pcap-max-mb',
        type=int,
        default=500,
        help="Maximum disk space in MB for harvested netsim pcaps (default: 500)"
    )
    parser.add_argument(
        '--no-netsim-log',
        action='store_true',
        help=
        "Disable tailing netsimd stderr logs (process, ini, lock, and socket tracking continue)"
    )
    args = parser.parse_args()
    args.interval = max(1.0, min(10.0, args.interval))

    os_name = platform.system()
    hardware_specs = get_static_hardware_info()
    iter_count, last_pgin, last_pgout, prev_term_width = 0, 0, 0, 0
    paging_str, paging_in_rate, paging_out_rate = "Calculating...", 0.0, 0.0
    last_time = time.monotonic()
    prev_paging_sample = _LAST_PAGING_SAMPLE_TIME
    last_auto_save = time.time()
    prev_stats, device_names = {}, {}
    disconnect_times, serial_pids, serial_group_pids = {}, {}, {}
    serial_group_procs = {}
    seen_offline_without_host, host_exited_serials = set(), set()
    bare_key_serials = set()
    _LAST_NETSIMD_PROCS.clear()
    _LAST_EMU_GROUP_PIDS.clear()
    _LAST_EMU_GROUP_INFO.clear()
    _LAST_PRIMARY_PROC_ID.clear()

    netsim_tracker = NetsimTracker(
        pcap_dir=getattr(args, 'netsim_pcap', None),
        pcap_max_mb=getattr(args, 'netsim_pcap_max_mb', 500),
        enable_log=not getattr(args, 'no_netsim_log', False),
        out_dir=args.out_dir,
    )

    summary_data = {
        'start_time': time.time(),
        'start_time_monotonic': time.monotonic(),
        'iterations': 0,
        'hardware_specs': hardware_specs,
        'host_cpu_iterations': 0,
        'host_cpu_sum': 0.0,
        'host_cpu_max': 0.0,
        'host_ram_iterations': 0,
        'host_ram_sum': 0.0,
        'host_ram_max': 0.0,
        'netsim_cpu_iterations': 0,
        'netsim_cpu_sum': 0.0,
        'netsim_cpu_max': 0.0,
        'netsim_ram_iterations': 0,
        'netsim_ram_sum': 0.0,
        'netsim_ram_max': 0.0,
        'emulators': {},
        'time_series': deque(maxlen=43200)
    }

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=40)
    timeout_bound = args.interval + 1.0

    # State Dictionaries securely resolve the critical Thread Pool Starvation bugs
    active_tasks = {'host': None, 'devices': None, 'guests': {}}
    last_devices = {}
    last_host_res = ("Calculating...", {}, "N/A", "N/A", "N/A", 0, 0, [], "N/A",
                     {}, "N/A", "N/A", {}, "N/A", "N/A", 0.0, 0.0, "N/A", {})
    last_guest_res = {}

    try:
        if not args.headless:
            setup_alt_screen(args.once, args.headless)
        while True:
            loop_start = time.monotonic()
            has_deadline = iter_count > 0 and not args.once

            # Asynchronously deduce host-level states strictly separated from main rendering thread
            if active_tasks['host'] is None:
                active_tasks['host'] = executor.submit(get_host_stats)

            # Poll active devices attached to host asynchronously, but give
            # this tick's query until the tick deadline so new guests are
            # queried on the tick adb first lists them.
            if active_tasks['devices'] is None:
                active_tasks['devices'] = executor.submit(get_devices)
            if not active_tasks['devices'].done():
                devices_timeout = (max(
                    0.0, args.interval -
                    (time.monotonic() - loop_start)) if has_deadline else None)
                concurrent.futures.wait([active_tasks['devices']],
                                        timeout=devices_timeout)
            if active_tasks['devices'].done():
                try:
                    last_devices = active_tasks['devices'].result()
                except Exception:
                    pass
                active_tasks['devices'] = None

            devices = last_devices
            connected = set(devices.keys())

            for s, e_data in summary_data['emulators'].items():
                if e_data.get('current_serial', s) not in connected:
                    e_data['last_status'] = 'offline'
                    e_data['online_indicator_until'] = 0

            for s in list(active_tasks['guests'].keys()):
                if s not in devices:
                    active_tasks['guests'][s].cancel()
                    del active_tasks['guests'][s]

            # Submits tasks cleanly while avoiding uncompleted background threads from compounding latency
            clean_spawned_guests = set()
            for s in devices:
                if s not in active_tasks['guests']:
                    storage_key = _storage_key_for_serial(
                        summary_data.get('emulators', {}), s) or s
                    e_entry = summary_data.get('emulators',
                                               {}).get(storage_key, {})
                    stale_prior = bool(
                        e_entry.get('evicted') or
                        ((e_entry.get('host_exited') or s in host_exited_serials
                          or s in seen_offline_without_host) and
                         (devices[s] == 'device' or s in disconnect_times)))
                    known_model = "" if stale_prior else device_names.get(s, "")
                    is_booting = (None if stale_prior else
                                  (e_entry.get('is_booting', False) if
                                   e_entry.get('boot_state_known') else None))
                    if stale_prior:
                        clean_spawned_guests.add(s)
                    active_tasks['guests'][s] = executor.submit(
                        get_guest_stats, s, known_model, is_booting,
                        devices[s])

            futures_to_wait = [active_tasks['host']] + [
                active_tasks['guests'][s]
                for s in devices
                if s in active_tasks['guests']
            ]

            # Ensures absolute UI refresh pacing natively without abandoning lingering threads
            pending_futures = [f for f in futures_to_wait if not f.done()]
            if pending_futures:
                wait_timeout = (max(
                    0.1, args.interval -
                    (time.monotonic() - loop_start)) if has_deadline else None)
                concurrent.futures.wait(pending_futures, timeout=wait_timeout)

            host_fresh = False
            if active_tasks['host'].done():
                try:
                    last_host_res = active_tasks['host'].result()
                    host_fresh = True
                except Exception as e:
                    last_host_res = (f"Error: {e}", {}, "N/A", "N/A", "N/A", 0,
                                     0, [], "N/A", {}, "N/A", "N/A", {}, "N/A",
                                     "N/A", 0.0, 0.0, "N/A", {})
                active_tasks['host'] = None
                host_res = last_host_res
            else:
                ht = last_host_res[0]
                if "Timeout" not in ht and "Calculating" not in ht:
                    ht += " (Timeout)"
                host_res = (ht,) + last_host_res[1:]

            host_scan_pending = bool(not host_fresh and
                                     active_tasks['host'] is not None)
            qemu_pids = host_res[9] if len(host_res) > 9 else {}
            (_, last_emu_group_pids, last_emu_group_info,
             last_primary_proc_id) = _snapshot_process_state()
            if host_fresh:
                for e_ex in summary_data['emulators'].values():
                    if e_ex.get('retired'):
                        continue
                    curr_s = e_ex.get('current_serial')
                    if curr_s not in qemu_pids:
                        e_ex.pop('_pending_host_scan', None)
                    if e_ex.get('current_pid') and curr_s not in qemu_pids:
                        e_ex['host_exited'] = True
                        e_ex['group_pids'] = set()
                        e_ex['group_procs'] = {}
            live_group_procs_map = {
                s_k:
                    _build_live_group_procs(
                        qemu_pids.get(s_k),
                        last_primary_proc_id.get(s_k,
                                                 (qemu_pids.get(s_k), None)),
                        last_emu_group_pids.get(s_k),
                        last_emu_group_info.get(s_k),
                    ) for s_k in (set(qemu_pids) | set(last_emu_group_pids))
            }
            forgotten = update_device_cache(
                device_names,
                disconnect_times,
                serial_pids,
                connected,
                qemu_pids,
                time.monotonic(),
                serial_group_pids=serial_group_pids,
                live_group_pids=last_emu_group_pids,
                serial_group_procs=serial_group_procs,
                live_group_procs=live_group_procs_map,
                host_fresh=host_fresh,
                devices=devices,
                seen_offline_without_host=seen_offline_without_host,
                host_exited_serials=host_exited_serials)
            respawned_guests = []
            for s in forgotten:
                # A query started for the previous emulator on this serial
                # must not be shown or recorded for the one there now.
                if s not in connected or s not in qemu_pids:
                    for e_ev in summary_data['emulators'].values():
                        if (not e_ev.get('retired') and
                                e_ev.get('current_serial') == s):
                            e_ev['evicted'] = True
                last_guest_res.pop(s, None)
                if s not in qemu_pids or s in devices:
                    bare_key_serials.discard(s)
                prev_stats.pop(f'{s}_real', None)
                prev_stats.pop(f'{s}_ram', None)
                prev_stats.pop(f'{s}_swap', None)
                if s not in clean_spawned_guests:
                    f_old = active_tasks['guests'].pop(s, None)
                    if f_old is not None:
                        f_old.cancel()
                    if s in devices and (not has_deadline or
                                         (args.interval -
                                          (time.monotonic() - loop_start))
                                         > 0.05):
                        f_new = executor.submit(get_guest_stats, s, "", None,
                                                devices[s])
                        active_tasks['guests'][s] = f_new
                        respawned_guests.append(f_new)
            if respawned_guests:
                pending_respawn = [f for f in respawned_guests if not f.done()]
                if pending_respawn:
                    respawn_timeout = (max(
                        0.05, args.interval - (time.monotonic() - loop_start))
                                       if has_deadline else None)
                    concurrent.futures.wait(pending_respawn,
                                            timeout=respawn_timeout)

            guest_results, current_metrics, guest_fresh = {}, {}, {}
            qemu_avds = host_res[-1] if len(host_res) > 18 else {}

            # De-reference dead devices cleanly from tracking memory
            for s in list(active_tasks['guests'].keys()):
                if s not in devices and active_tasks['guests'][s].done():
                    del active_tasks['guests'][s]

            for serial in devices:
                f = active_tasks['guests'].get(serial)
                is_fresh = False
                if f and f.done():
                    active_tasks['guests'].pop(serial, None)
                    try:
                        model, is_booting, g_diff, g_real, g_ram, g_swap = f.result(
                        )
                        last_guest_res[serial] = (model, is_booting, g_diff,
                                                  g_real, g_ram, g_swap)
                        is_fresh = True
                    except Exception as e:
                        storage_key = _storage_key_for_serial(
                            summary_data.get('emulators', {}), serial) or serial
                        last_b = summary_data.get('emulators', {}).get(
                            storage_key, {}).get('is_booting', False)
                        last_guest_res[serial] = (device_names.get(
                            serial, "Unknown"), last_b, "N/A", f"Error: {e}",
                                                  "N/A", "N/A")
                guest_fresh[serial] = is_fresh

                if serial in last_guest_res:
                    model, is_booting, g_diff, g_real, g_ram, g_swap = last_guest_res[
                        serial]
                    if f and not f.done(
                    ) and "Timeout" not in g_real and "Calculating" not in g_real:
                        g_real += " (Timeout)"
                else:
                    storage_key = _storage_key_for_serial(
                        summary_data.get('emulators', {}), serial) or serial
                    last_b = summary_data.get('emulators', {}).get(
                        storage_key, {}).get('is_booting', False)
                    model, is_booting, g_diff, g_real, g_ram, g_swap = device_names.get(
                        serial, "Unknown"
                    ), last_b, "Calculating...", "Calculating...", "Calculating...", "Calculating..."

                current_metrics[serial] = (g_diff, g_real, g_ram, g_swap)
                if is_fresh and model and (not model.startswith("Unknown") or
                                           serial not in device_names):
                    device_names[serial] = model

                avd_name = qemu_avds.get(serial)
                new_pid = host_res[9].get(serial) if len(host_res) > 9 else None
                host_group = _HostGroupIdentity.from_scan(
                    serial=serial,
                    pid=new_pid,
                    primary_proc_ids=last_primary_proc_id,
                    emu_group_pids=last_emu_group_pids,
                    emu_group_info=last_emu_group_info)
                has_live_target = bool(new_pid or
                                       devices.get(serial) == 'device')
                known_boot = bool(is_fresh and
                                  devices.get(serial) == 'device' and
                                  is_booting is not None)
                model_part = device_names.get(serial, 'Unknown Device')
                reconciled = _reconcile_emulator_session_entry(
                    summary_data=summary_data,
                    netsim_tracker=netsim_tracker,
                    bare_key_serials=bare_key_serials,
                    serial=serial,
                    avd_name=avd_name,
                    model_part=model_part,
                    host_group=host_group,
                    adb_seen=True,
                    has_live_target=has_live_target,
                    host_fresh=host_fresh,
                    known_boot=known_boot,
                    is_booting=bool(is_booting),
                    track_boot_stats_on_init=(bool(is_booting)
                                              if known_boot else
                                              devices.get(serial) == 'offline'),
                    track_boot_stats_on_reset=(
                        devices.get(serial) == 'offline'),
                    pending_host_scan=(host_scan_pending and
                                       not has_live_target))
                e_data = reconciled.entry
                if not reconciled.created:
                    if (is_fresh and devices.get(serial) == 'device' and
                            is_booting is not None):
                        if not e_data.get('boot_state_known', False):
                            e_data['boot_state_known'] = True
                            e_data['is_booting'] = bool(is_booting)
                            e_data.pop('boot_finished_time', None)
                            if is_booting:
                                e_data['boot_completed'] = False
                                e_data['track_boot_stats'] = True
                            else:
                                e_data['boot_completed'] = True
                                e_data['track_boot_stats'] = False
                        elif e_data.get('boot_completed'):
                            e_data['is_booting'] = False
                        else:
                            was_booting = e_data.get('is_booting', False)
                            e_data['is_booting'] = bool(is_booting)
                            if was_booting and not is_booting:
                                e_data['boot_finished_time'] = time.monotonic()
                                e_data['boot_completed'] = True
                            elif not is_booting:
                                e_data['boot_completed'] = True
                            else:
                                e_data['track_boot_stats'] = True
                    elif is_fresh:
                        if e_data.get('boot_completed'):
                            e_data['is_booting'] = False
                        elif (devices.get(serial) == 'offline' and
                              not e_data.get('boot_state_known', False)):
                            e_data['track_boot_stats'] = True

                if iter_count > 0 and is_fresh:
                    # Dynamically capture Boot Phase Resource Profiling automatically
                    if e_data.get('track_boot_stats') and e_data.get(
                            'first_seen') is not None and (
                                time.monotonic() - e_data['first_seen'] < 900):
                        if not e_data.get('boot_completed') or (
                                'boot_finished_time' in e_data and
                                time.monotonic() - e_data['boot_finished_time']
                                < 300):
                            if time.monotonic() - e_data.get(
                                    'last_boot_log_time', 0) >= 5.0 and not any(
                                        x in s
                                        for s in (g_real,
                                                  g_ram.split(',', 1)[0])
                                        for x in ("N/A", "Error", "Timeout",
                                                  "Hang", "Calculating")):
                                elapsed_boot = int(time.monotonic() -
                                                   e_data['first_seen'])
                                e_data['boot_log'].append(
                                    (f"+{elapsed_boot}s", g_real, g_ram,
                                     g_swap))
                                e_data['last_boot_log_time'] = time.monotonic()

                    if not any(x in g_real for x in ("N/A", "Error", "Timeout",
                                                     "Hang", "Calculating")):
                        e_data['cpu_iterations'] += 1
                        g_c = get_val(g_real)
                        e_data['cpu_sum'] += g_c
                        e_data['cpu_max'] = max(e_data['cpu_max'], g_c)
                    if not any(x in g_ram.split(',', 1)[0]
                               for x in ("N/A", "Error", "Timeout", "Hang",
                                         "Calculating")):
                        e_data['ram_iterations'] += 1
                        g_r = get_val(g_ram)
                        e_data['ram_sum'] += g_r
                        e_data['ram_max'] = max(e_data['ram_max'], g_r)
                    if not any(x in g_swap for x in ("N/A", "Error", "Timeout",
                                                     "Hang", "Calculating")):
                        e_data['swap_iterations'] += 1
                        g_sw = get_val(g_swap)
                        e_data['swap_sum'] += g_sw
                        e_data['swap_max'] = max(e_data.get('swap_max', 0.0),
                                                 g_sw)

            host_total, qemu_stats, netsim_cpu, host_ram, host_swap, pgin, pgout, unmapped_qemu, netsim_ram, qemu_pids, gpu_load, gpu_ram, emu_gpu_stats, temp_str, disk_str, read_mb, write_mb, batt_str, qemu_avds = host_res

            if host_fresh:
                for serial in qemu_stats:
                    if serial in devices:
                        continue
                    avd_name = qemu_avds.get(serial)
                    new_pid = qemu_pids.get(serial)
                    host_group = _HostGroupIdentity.from_scan(
                        serial=serial,
                        pid=new_pid,
                        primary_proc_ids=last_primary_proc_id,
                        emu_group_pids=last_emu_group_pids,
                        emu_group_info=last_emu_group_info)
                    host_model_part = device_names.get(serial, 'Unknown Device')
                    _reconcile_emulator_session_entry(
                        summary_data=summary_data,
                        netsim_tracker=netsim_tracker,
                        bare_key_serials=bare_key_serials,
                        serial=serial,
                        avd_name=avd_name,
                        model_part=host_model_part,
                        host_group=host_group,
                        adb_seen=False,
                        has_live_target=bool(new_pid),
                        host_fresh=True)

            try:
                netsim_tracker.update(summary_data['emulators'],
                                      devices,
                                      now_wall=time.time(),
                                      now_mono=time.monotonic())
            except Exception as e:
                # As in Rust, a failed netsim pass does not end host and guest
                # sampling. It is kept in the netsim timeline.
                netsim_tracker.record_tracking_failure(time.time(), e)
            netsim_snapshot = netsim_tracker.get_dashboard_snapshot(
                now_wall=time.time())
            summary_data[
                'netsim_summary_lines'] = netsim_tracker.format_summary_sections(
                    now_mono=time.monotonic())

            for storage_key, e_data in summary_data['emulators'].items():
                if e_data.get('retired'):
                    continue
                name = e_data['name']
                current_serial = e_data.get('current_serial', storage_key)
                if current_serial in connected:
                    g_diff, g_real, g_ram, g_swap = current_metrics[
                        current_serial]
                    current_status = ('online' if (
                        devices.get(current_serial) == 'device' and
                        "No ADB" not in g_diff and
                        "offline" not in g_diff.lower()) else 'offline')

                    if e_data.get('last_status'
                                 ) == 'offline' and current_status == 'online':
                        e_data['online_indicator_until'] = time.monotonic() + 10
                    elif current_status != 'online':
                        e_data['online_indicator_until'] = 0
                    e_data['last_status'] = current_status

                    tags = f" {Colors.GREEN}[ONLINE]{Colors.RESET}" if time.monotonic(
                    ) < e_data.get('online_indicator_until', 0) else ""
                    if e_data.get('track_boot_stats') and (
                            e_data.get('first_seen') is None or
                            time.monotonic() - e_data['first_seen'] < 900):
                        if e_data.get('is_booting') or (
                                not e_data.get('boot_state_known') and
                                devices.get(current_serial) == 'offline') or (
                                    'boot_finished_time' in e_data and
                                    time.monotonic() -
                                    e_data['boot_finished_time'] < 300):
                            tags += f" {Colors.YELLOW}[RECORDING BOOT STATS]{Colors.RESET}"
                    guest_results[current_serial] = (name, tags, g_diff, g_real,
                                                     g_ram, g_swap)
                else:
                    last_seen = e_data.get('last_seen', 0)
                    if e_data.get('adb_seen',
                                  True) and time.monotonic() - last_seen < 120:
                        tags = f" {Colors.RED}[DISCONNECTED]{Colors.RESET}"
                        guest_results[current_serial] = (name, tags, "N/A",
                                                         "N/A", "N/A", "N/A")

            retired_disc = {}
            now_disc = time.monotonic()
            for storage_key, e_data in summary_data['emulators'].items():
                candidates = []
                if e_data.get('retired'):
                    candidates.append(e_data)
                if e_data.get('_retired_prior_bare'):
                    candidates.append(e_data['_retired_prior_bare'])
                for cand in candidates:
                    c_serial = cand.get('current_serial', storage_key)
                    if c_serial in connected or c_serial in guest_results:
                        continue
                    c_last = cand.get('last_seen', 0)
                    if (cand.get('adb_seen', False) and c_last > 0 and
                            now_disc - c_last < 120):
                        prev_best = retired_disc.get(c_serial)
                        if prev_best is None or c_last >= prev_best[0]:
                            retired_disc[c_serial] = (c_last, cand['name'])
            for c_serial, (_, c_name) in retired_disc.items():
                tags = f" {Colors.RED}[DISCONNECTED]{Colors.RESET}"
                guest_results[c_serial] = (c_name, tags, "N/A", "N/A", "N/A",
                                           "N/A")

            curr_time = time.monotonic()
            if host_fresh:
                sample_now = _LAST_PAGING_SAMPLE_TIME
                if sample_now is not None and sample_now != prev_paging_sample:
                    prev_paging_sample = sample_now
                    paging_now = sample_now
                else:
                    paging_now = curr_time
                elapsed = paging_now - last_time
                (paging_str, paging_in_rate, paging_out_rate, last_pgin,
                 last_pgout) = compute_paging_stats(pgin, pgout, last_pgin,
                                                    last_pgout, elapsed,
                                                    os_name, iter_count)
                last_time = paging_now

            if iter_count > 0:
                summary_data['iterations'] += 1
                if host_fresh:
                    if not any(x in host_total
                               for x in ("Calculating", "N/A", "Unsupported",
                                         "Error", "Timeout")):
                        summary_data['host_cpu_iterations'] += 1
                        h_c = get_val(host_total)
                        summary_data['host_cpu_sum'] += h_c
                        summary_data['host_cpu_max'] = max(
                            summary_data['host_cpu_max'], h_c)
                    if not any(x in host_ram
                               for x in ("Calculating", "N/A", "Unsupported",
                                         "Error", "Timeout")):
                        summary_data['host_ram_iterations'] += 1
                        h_r = get_val(host_ram)
                        summary_data['host_ram_sum'] += h_r
                        summary_data['host_ram_max'] = max(
                            summary_data['host_ram_max'], h_r)

                    if netsim_cpu != "N/A" and "Calculating" not in netsim_cpu:
                        summary_data['netsim_cpu_iterations'] += 1
                        n_c = get_val(netsim_cpu)
                        summary_data['netsim_cpu_sum'] += n_c
                        summary_data['netsim_cpu_max'] = max(
                            summary_data.get('netsim_cpu_max', 0.0), n_c)

                    if netsim_ram != "N/A" and "Calculating" not in netsim_ram:
                        summary_data['netsim_ram_iterations'] += 1
                        n_r = get_val(netsim_ram)
                        summary_data['netsim_ram_sum'] += n_r
                        summary_data['netsim_ram_max'] = max(
                            summary_data.get('netsim_ram_max', 0.0), n_r)

                    for key, e_data in summary_data['emulators'].items():
                        curr_s = (None if e_data.get('retired') else e_data.get(
                            'current_serial', key))
                        if curr_s in qemu_stats:
                            q_cpu_val = get_val(qemu_stats[curr_s][0])
                            q_ram_val = get_val(qemu_stats[curr_s][1])
                            e_data['qemu_cpu_iterations'] += 1
                            e_data['qemu_cpu_sum'] += q_cpu_val
                            e_data['qemu_cpu_max'] = max(
                                e_data.get('qemu_cpu_max', 0.0), q_cpu_val)
                            e_data['qemu_ram_iterations'] += 1
                            e_data['qemu_ram_sum'] += q_ram_val
                            e_data['qemu_ram_max'] = max(
                                e_data.get('qemu_ram_max', 0.0), q_ram_val)

                nan = float('nan')
                ts_entry = {
                    'time':
                        time.monotonic(),
                    'host_cpu':
                        get_val(host_total, True) if host_fresh else nan,
                    'host_ram':
                        get_val(host_ram, True) if host_fresh else nan,
                    'netsim_cpu':
                        get_val(netsim_cpu, True) if host_fresh else nan,
                    'netsim_ram':
                        get_val(netsim_ram, True) if host_fresh else nan,
                    'host_gpu':
                        get_val(gpu_load, True) if host_fresh else nan,
                    'host_gpu_mem':
                        get_val(gpu_ram, True) if host_fresh else nan,
                    'host_swap':
                        get_val(host_swap, True) if host_fresh else nan,
                    'host_thermal_c':
                        get_val(temp_str, True) if host_fresh else nan,
                    'host_disk_read_mbs':
                        read_mb if host_fresh else nan,
                    'host_disk_write_mbs':
                        write_mb if host_fresh else nan,
                    'host_paging_in':
                        paging_in_rate if host_fresh else nan,
                    'host_paging_out':
                        paging_out_rate if host_fresh else nan,
                    'host_battery':
                        get_val(batt_str, True) if host_fresh else nan,
                    'host_unmapped_qemu_cpu': (
                        (sum(get_val(c) for p, c, r in unmapped_qemu)
                         if unmapped_qemu else 0.0) if host_fresh else nan),
                    'host_unmapped_qemu_ram': (
                        (sum(get_val(r) for p, c, r in unmapped_qemu)
                         if unmapped_qemu else 0.0) if host_fresh else nan),
                }

                for key, e_data in summary_data['emulators'].items():
                    curr_s = (None if e_data.get('retired') else e_data.get(
                        'current_serial', key))
                    if curr_s in current_metrics and guest_fresh.get(
                            curr_s, False):
                        ts_entry[f'emu_cpu_tot_{key}'] = get_val(
                            current_metrics[curr_s][0], True)
                        ts_entry[f'emu_cpu_adj_{key}'] = get_val(
                            current_metrics[curr_s][1], True)
                        emu_ram_val = get_val(current_metrics[curr_s][2], True)
                        ts_entry[f'emu_ram_{key}'] = emu_ram_val
                        ts_entry[f'emu_swap_{key}'] = get_val(
                            current_metrics[curr_s][3], True)
                        if math.isnan(emu_ram_val):
                            ts_entry[f'emu_errs_{key}'] = nan
                        else:
                            m_err = RE_GUEST_ERRS.search(
                                current_metrics[curr_s][2])
                            ts_entry[f'emu_errs_{key}'] = float(
                                m_err.group(1)) if m_err else 0.0
                        m_pct = None
                        ram_parts = current_metrics[curr_s][2].split(',')
                        if len(ram_parts) >= 3:
                            m_pct = RE_GUEST_PCT_EXTRACT.search(ram_parts[2])
                        ts_entry[f'emu_disk_pct_{key}'] = float(
                            m_pct.group(1)) if m_pct else nan
                    else:
                        ts_entry[f'emu_cpu_tot_{key}'] = nan
                        ts_entry[f'emu_cpu_adj_{key}'] = nan
                        ts_entry[f'emu_ram_{key}'] = nan
                        ts_entry[f'emu_swap_{key}'] = nan
                        ts_entry[f'emu_errs_{key}'] = nan
                        ts_entry[f'emu_disk_pct_{key}'] = nan

                    if host_fresh and curr_s in qemu_stats:
                        ts_entry[f'qemu_cpu_{key}'] = get_val(
                            qemu_stats[curr_s][0], True)
                        ts_entry[f'qemu_ram_{key}'] = get_val(
                            qemu_stats[curr_s][1], True)
                        m_cs = re.search(r'(\d+)\s*cs', qemu_stats[curr_s][1])
                        ts_entry[f'qemu_cs_{key}'] = float(
                            m_cs.group(1)) if m_cs else 0.0
                        ts_entry[f'qemu_gpu_{key}'] = get_val(
                            emu_gpu_stats.get(curr_s, "0.0%"), True)
                    else:
                        ts_entry[f'qemu_cpu_{key}'] = nan
                        ts_entry[f'qemu_ram_{key}'] = nan
                        ts_entry[f'qemu_cs_{key}'] = nan
                        ts_entry[f'qemu_gpu_{key}'] = nan

                netsim_fields = netsim_tracker.get_csv_fields()
                ts_entry.update(netsim_fields)
                for ns_col in netsim_fields:
                    if ns_col.startswith('netsim_state_'):
                        ns_key = ns_col[len('netsim_state_'):]
                        if ns_key not in summary_data['emulators'] and (
                                ns_key in qemu_pids or ns_key in connected):
                            bare_key_serials.add(ns_key)
                summary_data['time_series'].append(ts_entry)
                for bare_s in list(bare_key_serials):
                    avd_s = qemu_avds.get(bare_s)
                    if avd_s and not summary_data['emulators'].get(
                            bare_s, {}).get('retired'):
                        s_pid = qemu_pids.get(bare_s)
                        s_raw_grp = set(last_emu_group_pids.get(bare_s) or ())
                        s_live_grp = (s_raw_grp | {s_pid} if
                                      (s_pid and s_pid in s_raw_grp) else
                                      ({s_pid} if s_pid else set()))
                        s_live_procs = _build_live_group_procs(
                            s_pid,
                            last_primary_proc_id.get(bare_s, (s_pid, None)),
                            s_raw_grp, last_emu_group_info.get(bare_s))
                        if _can_migrate_bare_serial(
                                summary_data,
                                netsim_tracker,
                                bare_s,
                                s_pid,
                                s_live_grp,
                                live_group_procs=s_live_procs):
                            bare_key_serials.discard(bare_s)
                            dst_key = f"{avd_s}_{bare_s}"
                            _migrate_summary_keys(summary_data, bare_s, dst_key)
                            netsim_tracker.migrate_bare_record(
                                bare_s, dst_key, time.monotonic())

            def apply_trend(key, curr_str):
                head = curr_str.split(',', 1)[0]
                if (head.split('(', 1)[0].lstrip().startswith("N/A") or
                        any(x in curr_str for x in [
                            "Unsupported", "Calculating", "No ADB", "Error",
                            "Timeout", "Hang"
                        ])):
                    return curr_str
                curr_val, prev_val = get_val(curr_str), prev_stats.get(
                    key, get_val(curr_str))
                prev_stats[key] = curr_val
                return f"{curr_str} {'↑' if curr_val > prev_val + 0.5 else '↓' if curr_val < prev_val - 0.5 else '→'}"

            if iter_count > 0:
                host_total = apply_trend('h_cpu', host_total)
                host_ram = apply_trend('h_ram', host_ram)
                host_swap = apply_trend('h_swap', host_swap)
                netsim_cpu = apply_trend('n_cpu', netsim_cpu)
                for serial in list(guest_results.keys()):
                    disp, tags, g_diff, g_real, g_ram, g_swap = guest_results[
                        serial]
                    guest_results[serial] = (disp, tags, g_diff,
                                             apply_trend(
                                                 f'{serial}_real', g_real),
                                             apply_trend(
                                                 f'{serial}_ram', g_ram),
                                             apply_trend(
                                                 f'{serial}_swap', g_swap))
            else:
                for k, v in zip(['h_cpu', 'h_ram', 'h_swap', 'n_cpu'],
                                [host_total, host_ram, host_swap, netsim_cpu]):
                    prev_stats[k] = get_val(v)
                for serial, stats in guest_results.items():
                    prev_stats[f'{serial}_real'] = get_val(stats[3])
                    prev_stats[f'{serial}_ram'] = get_val(stats[4])
                    prev_stats[f'{serial}_swap'] = get_val(stats[5])

            term_width = shutil.get_terminal_size(fallback=(86, 24)).columns
            force_clear = (iter_count == 0) or (term_width != prev_term_width)

            if not args.headless and (not args.once or iter_count > 0):
                print_dashboard(os_name,
                                host_total,
                                qemu_stats,
                                netsim_cpu,
                                guest_results,
                                host_ram,
                                host_swap,
                                paging_str,
                                iter_count,
                                term_width,
                                force_clear,
                                unmapped_qemu,
                                netsim_ram,
                                gpu_load,
                                gpu_ram,
                                emu_gpu_stats,
                                temp_str=temp_str,
                                disk_str=disk_str,
                                is_once=args.once,
                                batt_str=batt_str,
                                hw_specs=hardware_specs,
                                netsim_snapshot=netsim_snapshot)

            prev_term_width = term_width
            if args.once and iter_count > 0:
                break
            iter_count += 1

            if args.auto_save_interval > 0 and (
                    time.time() -
                    last_auto_save) >= args.auto_save_interval * 60:
                try:
                    summary_data['netsim_summary_lines'] = (
                        netsim_tracker.format_summary_sections(
                            now_mono=time.monotonic()))
                    save_summary(summary_data,
                                 time.time(),
                                 save_csv=args.csv,
                                 save_txt=not args.no_summary,
                                 out_dir=args.out_dir,
                                 is_autosave=True)
                except Exception:
                    pass
                last_auto_save = time.time()

            # Subtly corrects cadence offset natively, ensuring exact timer intervals
            sleep_time = max(0.1,
                             args.interval - (time.monotonic() - loop_start))
            if netsim_tracker.has_any_connecting() and sleep_time > 0.25:
                deadline = time.monotonic() + sleep_time
                while time.monotonic() < deadline:
                    step = min(0.25, max(0.01, deadline - time.monotonic()))
                    t_before = time.monotonic()
                    time.sleep(step)
                    if time.monotonic() - t_before < step * 0.5:
                        break
                    if netsim_tracker.has_any_connecting():
                        try:
                            netsim_tracker.poll_sockets_fast(
                                now_wall=time.time())
                        except Exception as e:
                            netsim_tracker.record_tracking_failure(
                                time.time(), e)
                    else:
                        rem = deadline - time.monotonic()
                        if rem > 0:
                            time.sleep(rem)
                        break
            else:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        pass
    except Exception:
        # Guarantee trace visibility by dropping out of Alternate Screen Buffer before logging crashes
        if not args.headless:
            restore_screen(args.once, args.headless)
        print(f"\n{Colors.RED}[Fatal Error] Application Crashed:{Colors.RESET}")
        traceback.print_exc()
        exit_code = 1  # Safely track the failure
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        executor.shutdown(wait=False)
        if not args.headless:
            restore_screen(args.once, args.headless)

        # Last-resort guards: report failures, but never let netsim cleanup or
        # one broken section cost the session summary.
        try:
            netsim_tracker.cleanup()
        except Exception:
            traceback.print_exc()

        try:
            summary_data['netsim_summary_lines'] = (
                netsim_tracker.format_summary_sections(
                    now_mono=time.monotonic()))
        except Exception:
            traceback.print_exc()
        try:
            save_summary(summary_data,
                         time.time(),
                         save_csv=args.csv,
                         save_txt=not args.no_summary,
                         out_dir=args.out_dir)
        except Exception:
            print(
                f"{Colors.RED}Failed to save the session summary:{Colors.RESET}",
                file=sys.stderr)
            traceback.print_exc()
            exit_code = 1

        if exit_code == 0:
            print(
                f"{Colors.GREEN}Monitoring stopped cleanly. Goodbye!{Colors.RESET}\033[K"
            )
        sys.stdout.flush()

        os._exit(exit_code)  # Dispatch the correct state
        signal.signal(signal.SIGINT, orig_sigint)
        signal.signal(signal.SIGTERM, orig_sigterm)


if __name__ == "__main__":
    main()
