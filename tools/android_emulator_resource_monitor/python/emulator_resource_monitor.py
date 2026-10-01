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
from collections import deque

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

# Guest Android Pre-Compiled Regexes
RE_GUEST_CPU_USAGE = re.compile(r'(\d+)\s*%?\s*cpu', re.IGNORECASE)
RE_GUEST_CPU_IDLE = re.compile(r'(\d+)\s*%?\s*idle', re.IGNORECASE)
RE_GUEST_CPU_ID = re.compile(r'(\d+(?:[\.,]\d+)?)\s*%?\s*id', re.IGNORECASE)
RE_GUEST_DUMPSYS_TOT = re.compile(r'(\d+(?:\.\d+)?)%\s*TOTAL', re.IGNORECASE)
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

    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and platform.system() != "Windows":
        try:
            import pwd
            user_home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir)
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
sudo_user = os.environ.get('SUDO_USER')


def run_cmd(cmd, timeout=5, stderr_devnull=False):
    try:
        cmd = list(cmd)
        kwargs = {'stdin': subprocess.DEVNULL}
        if platform.system() == "Windows":
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW',
                                              0x08000000)

        if cmd[0] == 'adb':
            cmd[0] = ADB_PATH
            if sudo_user and hasattr(os, 'geteuid') and os.geteuid() == 0:
                cmd = ['sudo', '-n', '-u', sudo_user] + cmd

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
            out = subprocess.check_output(
                ['sysctl', '-n', 'machdep.cpu.brand_string'],
                stderr=subprocess.DEVNULL)
            if out:
                cpu_str = out.decode().strip()
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

    return f"{os_str} | {cpu_str} | {ram_str} RAM | GPU: {gpu_str}"


def get_devices():
    out = run_cmd(['adb', 'devices'], timeout=5)
    devices = {}
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith('List') and not line.startswith('*'):
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ('device', 'offline',
                                                'unauthorized', 'recovery',
                                                'bootloader'):
                devices[parts[0]] = parts[1]
    return devices


_WIN_GPU_CACHE = {"time": 0, "val": "N/A", "fetching": False}
_MAC_GPU_CACHE = {"time": 0, "val": "N/A", "fetching": False}
_LINUX_INTEL_GPU_FAILED = False


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
            except ValueError:
                pass
    finally:
        _WIN_GPU_CACHE["fetching"] = False


def get_gpu_stats():
    global _LINUX_INTEL_GPU_FAILED
    os_name = platform.system()

    if HAS_GPUTIL:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                return f"{gpus[0].load * 100:.1f}%", f"{gpus[0].memoryUsed:.1f}MB"
        except Exception:
            pass

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
                        return f"{float(parts[0].strip()):.1f}%", f"{float(parts[1].strip()):.1f}MB"
    except Exception:
        pass

    if os_name == "Linux":
        try:
            if os.path.exists('/sys/class/drm/card0/device/gpu_busy_percent'):
                with open('/sys/class/drm/card0/device/gpu_busy_percent',
                          'r') as f:
                    return f"{f.read().strip()}%", "N/A"
        except Exception:
            pass

        if not _LINUX_INTEL_GPU_FAILED:
            out = run_cmd(['intel_gpu_top', '-s', '1', '-n', '1'],
                          timeout=2,
                          stderr_devnull=True)
            if out and (m := re.search(r'Render/3D:\s+(\d+)%',
                                       RE_ANSI.sub('', out))):
                return f"{m.group(1)}%", "N/A"
            _LINUX_INTEL_GPU_FAILED = True

        return "N/A (Requires sudo or nvidia-smi)", "N/A"

    elif os_name == "Windows":
        if time.monotonic(
        ) - _WIN_GPU_CACHE["time"] > 5 and not _WIN_GPU_CACHE["fetching"]:
            _WIN_GPU_CACHE["fetching"] = True
            _WIN_GPU_CACHE["time"] = time.monotonic()
            threading.Thread(target=update_win_gpu, daemon=True).start()
        return _WIN_GPU_CACHE["val"], "N/A"

    elif os_name == "Darwin":
        if time.monotonic(
        ) - _MAC_GPU_CACHE["time"] > 10 and not _MAC_GPU_CACHE["fetching"]:

            def fetch_mac_gpu():
                try:
                    out = run_cmd([
                        'powermetrics', '-i', '500', '-n', '1', '--samplers',
                        'gpu_power'
                    ],
                                  timeout=2,
                                  stderr_devnull=True)
                    if out and (m := re.search(
                            r'GPU (?:HW )?active residency:\s+([\d.]+)%', out)):
                        _MAC_GPU_CACHE["val"] = f"{m.group(1)}%"
                except Exception:
                    pass
                finally:
                    _MAC_GPU_CACHE["fetching"] = False

            _MAC_GPU_CACHE["fetching"] = True
            _MAC_GPU_CACHE["time"] = time.monotonic()
            threading.Thread(target=fetch_mac_gpu, daemon=True).start()

        return _MAC_GPU_CACHE["val"], "N/A"

    return "N/A", "N/A"


_EMU_GPU_CACHE = {"time": 0, "val": {}, "fetching": False}


def update_emu_gpu():
    try:
        raw_stats = {}
        out = run_cmd(['nvidia-smi', 'pmon', '-c', '1'],
                      timeout=2,
                      stderr_devnull=True)
        if out:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 7 and parts[1].isdigit():
                    try:
                        pid, sm_val = int(parts[1]), float(
                            parts[3]) if parts[3] not in ('-', '?') else 0.0
                        raw_stats[pid] = raw_stats.get(pid, 0.0) + sm_val
                    except ValueError:
                        pass
        _EMU_GPU_CACHE["val"] = raw_stats
    except Exception:
        pass
    finally:
        _EMU_GPU_CACHE["fetching"] = False


def get_emu_gpu_stats(qemu_pids):
    if not qemu_pids:
        return {}
    if time.monotonic(
    ) - _EMU_GPU_CACHE["time"] > 2.0 and not _EMU_GPU_CACHE["fetching"]:
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
            val = str(cmdline[i + 1]).split(',')[0]
            if val.isdigit():
                explicit_port = int(val)
            consumed_next = True
        elif arg.startswith('-port=') or arg.startswith('-ports='):
            val = arg.split('=', 1)[1].split(',')[0]
            if val.isdigit():
                explicit_port = int(val)
        elif arg.startswith('-fishtank='):
            val = arg.split('=', 1)[1].split(',')[0]
            if val.isdigit():
                explicit_port = int(val)
        elif arg in ('-avd', '-name') and i + 1 < len(cmdline):
            next_arg = str(cmdline[i + 1]).strip('\'"')
            if next_arg.startswith('guest='):
                next_arg = next_arg[6:]
            avd_id = next_arg.split(',')[0]
            consumed_next = True
        elif arg.startswith('-name='):
            val = arg[6:].strip('\'"')
            if val.startswith('guest='):
                val = val[6:]
            avd_id = val.split(',')[0]
        elif arg.startswith('@') and len(arg) > 1:
            avd_id = arg[1:].strip('\'"')
        elif arg.startswith('-avd') and len(
                arg) > 4 and not arg.startswith('-avd_name='):
            avd_id = arg[4:].strip('\'"')
        elif avd_id is None and (i == 0 or arg.startswith('-')) and (
                '-avd' in arg) and not arg.startswith('-avd_name='):
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
                explicit_port = int(m_sn.group(1))
            if (m_hp := re.search(r'host_port=(\d+)', prop_str)):
                hp = int(m_hp.group(1))
                if hp > 0:
                    fallback_port = hp - 1
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
                k: dict(v) for k, v in _LAST_EMU_GROUP_INFO.items()
            },
            dict(_LAST_PRIMARY_PROC_ID),
        )


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


def _current_uid():
    """The monitor's uid, or None where os.getuid() is missing (Windows)."""
    getuid = getattr(os, 'getuid', None)
    return getuid() if getuid else None


def _proc_uid(proc):
    """Real uid of a psutil process, or None off POSIX or once it is gone."""
    uids = getattr(proc, 'uids', None)
    if uids is None:
        return None
    try:
        return uids().real
    except Exception:
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


def _is_owned_dir(path):
    """A directory owned by the monitor's user (any directory on Windows)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    my_uid = _current_uid()
    return my_uid is None or st.st_uid == my_uid


def _is_trusted_executable(path):
    """A regular file that only the monitor's user or root can replace.

    The file and its directory must be owned by the monitor's user or root and
    must not be group- or world-writable, so a binary found next to another
    process's executable cannot be one an unprivileged user planted. Windows
    has no POSIX ownership; there only the regular-file check applies.
    """
    try:
        st = os.stat(path)
        dir_st = os.stat(os.path.dirname(path) or '.')
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not stat.S_ISDIR(dir_st.st_mode):
        return False
    my_uid = _current_uid()
    if my_uid is None:
        return True
    for entry in (st, dir_st):
        if entry.st_uid not in (my_uid, 0):
            return False
        if entry.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
    return True


def resolve_netsim_cli_bin(daemon_exe, same_user):
    """Picks the `netsim` CLI to drive a daemon with.

    A sibling of the daemon's executable is used only when the daemon runs as
    the monitor's user and the sibling passes _is_trusted_executable().
    Otherwise the bare name resolves through the monitor's own PATH.
    """
    default = 'netsim.exe' if os.name == 'nt' else 'netsim'
    if not daemon_exe or not same_user:
        return default
    exe_dir = os.path.dirname(daemon_exe)
    for bin_name in ('netsim.exe', 'netsim'):
        cand = os.path.join(exe_dir, bin_name)
        if _is_trusted_executable(cand):
            return cand
    return default


def _copy_regular_file(src, dst):
    """Copies src to dst without following a symlink at src.

    The artifact dir comes from the daemon's environment, so a symlink planted
    there must not turn the copy into a read of an arbitrary file. The source
    is opened with O_NOFOLLOW and its identity re-checked on the open
    descriptor. Returns the source stat, or None when src is not a plain file.
    """
    try:
        lst = os.lstat(src)
    except OSError:
        return None
    if not stat.S_ISREG(lst.st_mode):
        return None
    flags = os.O_RDONLY
    for name in ('O_NOFOLLOW', 'O_CLOEXEC', 'O_BINARY'):
        flags |= getattr(os, name, 0)
    try:
        fd = os.open(src, flags)
    except OSError:
        return None
    try:
        fst = os.fstat(fd)
        if (not stat.S_ISREG(fst.st_mode) or
            (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino)):
            return None
        fsrc = os.fdopen(fd, 'rb')
        fd = None
        with fsrc, open(dst, 'wb') as fdst:
            shutil.copyfileobj(fsrc, fdst)
        return fst
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def host_discovery_dirs(host_env, target_os=None, uid=None):
    """get_netsim_discovery_dirs() for the monitor's own environment.

    On Linux this adds /run/user/<uid>, which the emulator uses for
    avd/running when XDG_RUNTIME_DIR is unset (GetAlternativeRoot()).
    """
    dirs = get_netsim_discovery_dirs(host_env, target_os)
    if _os_family(target_os) == 'linux' and uid is not None:
        _extend_unique(dirs, [f'/run/user/{uid}'])
    return dirs


def _read_emulator_discovery_ini(pid, proc_obj=None):
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
    dirs = get_netsim_discovery_dirs(env if isinstance(env, dict) else {})
    _extend_unique(dirs, host_discovery_dirs(os.environ, uid=_current_uid()))

    for base in dirs:
        ini_path = os.path.join(base, 'avd', 'running', f'pid_{pid}.ini')
        try:
            if not os.path.isfile(ini_path):
                continue
            data = {}
            with open(ini_path, 'r', encoding='utf-8', errors='replace') as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or '=' not in line or 'token' in line.lower():
                        continue
                    k, v = line.split('=', 1)
                    data[k.strip()] = v.strip()
            if data:
                return data
        except Exception:
            continue
    return {}


def _strip_exe(name):
    """Drops a Windows '.exe' suffix."""
    return name[:-4] if name.endswith('.exe') else name


def _classify_process(name_lower, exe_base, cmd0_base):
    """Returns ('netsimd' | 'worker' | 'emulator' | None)."""
    for bad in ('terminal', 'crash', 'qemu-img', 'resource_monitor'):
        if bad in name_lower or bad in exe_base or bad in cmd0_base:
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
            cmd0_stem == 'emulator'):
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

    raw_procs = list(psutil.process_iter(['name', 'pid', 'create_time']))
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
        try:
            info = proc.info or {}
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
                except Exception:
                    exe_str = ""
            exe_base = os.path.basename(exe_str).lower() if exe_str else ""

            quick_hit = (any(x in name
                             for x in ('qemu', 'emulator', 'crosvm', 'fishtank',
                                       'netsim', 'launcherloop')) or
                         exe_base in ('emulator', 'netsimd') or
                         exe_base.startswith('qemu-system') or
                         'crosvm' in exe_base or 'fishtank' in exe_base)
            if not quick_hit:
                continue

            cmdline = None
            cmd0_base = ""
            if _classify_process(name, exe_base, "") is None:
                try:
                    cmdline = proc.cmdline()
                    if isinstance(cmdline,
                                  (list, tuple)) and cmdline and isinstance(
                                      cmdline[0], str):
                        cmd0_base = os.path.basename(cmdline[0]).lower()
                except Exception:
                    cmdline = None

            role = _classify_process(name, exe_base, cmd0_base)
            if role is None:
                continue

            current_pids.add(cache_key)

            is_new = False
            if cache_key not in process_cache:
                process_cache[cache_key] = proc
                is_new = True
                try:
                    proc.cpu_percent(interval=None)
                except Exception:
                    pass

            cached_proc = process_cache[cache_key]

            try:
                with cached_proc.oneshot():
                    try:
                        cpu_pct = 0.0 if is_new else cached_proc.cpu_percent(
                            interval=None)
                    except Exception:
                        cpu_pct = 0.0

                    try:
                        mem_info = cached_proc.memory_info()
                    except Exception:
                        mem_info = None

                    try:
                        threads = cached_proc.num_threads()
                    except Exception:
                        threads = 0

                    try:
                        ctx = cached_proc.num_ctx_switches()
                        ctx_total = ctx.voluntary + ctx.involuntary
                    except Exception:
                        ctx_total = 0
            except Exception:
                cpu_pct, mem_info, threads, ctx_total = 0.0, None, 0, 0

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

            if cache_val == 'unmapped':
                pass
            elif isinstance(cache_val, dict):
                port = cache_val.get('port')
                avd_name = cache_val.get('avd_name')
                avd_id = cache_val.get('avd_id')
                display_name = cache_val.get('display_name')
                grpc_endpoint = cache_val.get('grpc_endpoint')
                no_netsim = cache_val.get('no_netsim', False)
            elif isinstance(cache_val, tuple):
                port, avd_name = cache_val[0], cache_val[1]
                avd_id = avd_name
            elif isinstance(cache_val, int):
                port = cache_val
            else:
                first_attempt = cache_val if isinstance(
                    cache_val, float) else time.monotonic()
                try:
                    if cmdline is None:
                        cmdline = cached_proc.cmdline()
                    (port, avd_name, avd_id, display_name, grpc_endpoint,
                     no_netsim) = _parse_cmdline_details(cmdline)
                except Exception:
                    pass

                if role == 'worker' and (not port or not avd_id or
                                         not display_name or not grpc_endpoint):
                    disc = _read_emulator_discovery_ini(pid, cached_proc)
                    if not port and disc.get('port.serial', '').isdigit():
                        port = int(disc['port.serial'])
                    if not avd_id and disc.get('avd.id'):
                        avd_id = disc['avd.id']
                    if not display_name and disc.get('avd.name'):
                        display_name = disc['avd.name']
                    if not grpc_endpoint and disc.get('netsim.endpoint'):
                        grpc_endpoint = disc['netsim.endpoint']
                    if not avd_name:
                        avd_name = avd_id or display_name

                if not port:
                    for conn_kind in ('inet', 'tcp4', 'tcp6'):
                        try:
                            conns = cached_proc.net_connections(kind=conn_kind)
                        except Exception:
                            continue
                        for conn in conns or []:
                            try:
                                if (conn.status == 'LISTEN' and
                                        5554 <= conn.laddr.port <= 5584 and
                                        conn.laddr.port % 2 == 0):
                                    port = conn.laddr.port
                                    break
                            except Exception:
                                continue
                        if port:
                            break

                if port:
                    pid_port_cache[cache_key] = {
                        'port': port,
                        'avd_name': avd_name,
                        'avd_id': avd_id,
                        'display_name': display_name,
                        'grpc_endpoint': grpc_endpoint,
                        'no_netsim': no_netsim,
                    }
                elif time.monotonic() - first_attempt > 60:
                    pid_port_cache[cache_key] = 'unmapped'
                else:
                    pid_port_cache[cache_key] = first_attempt

            is_worker = (role == 'worker')
            if port:
                serial = f"emulator-{port}"
                if serial not in accumulated_emus:
                    accumulated_emus[serial] = {
                        'cpu_pct':
                            cpu_pct,
                        'rss':
                            rss,
                        'threads':
                            threads,
                        'ctx_total':
                            ctx_total,
                        'primary_pid':
                            pid,
                        'primary_ctime':
                            ctime,
                        'primary_is_worker':
                            is_worker,
                        'max_cpu':
                            cpu_pct,
                        'max_rss':
                            rss,
                        'avd_id':
                            avd_id,
                        'display_name':
                            display_name,
                        'avd_name':
                            avd_id or display_name or avd_name,
                        'grpc_endpoint':
                            grpc_endpoint,
                        'no_netsim':
                            no_netsim,
                        'pids': {pid} if pid is not None else set(),
                        'worker_pid':
                            pid if is_worker else None,
                        'worker_ctime':
                            ctime if is_worker else None,
                        'launcher_pid':
                            pid if not is_worker and 'fishtank' not in name else
                            None,
                        'launcher_ctime':
                            ctime if not is_worker and 'fishtank' not in name
                            else None,
                        'procs': [cached_proc],
                    }
                else:
                    entry = accumulated_emus[serial]
                    entry['cpu_pct'] += cpu_pct
                    entry['rss'] += rss
                    entry['threads'] += threads
                    entry['ctx_total'] += ctx_total
                    if pid is not None:
                        entry['pids'].add(pid)
                    entry['procs'].append(cached_proc)
                    if is_worker and entry['worker_pid'] is None:
                        entry['worker_pid'] = pid
                        entry['worker_ctime'] = ctime
                    elif not is_worker and 'fishtank' not in name and entry[
                            'launcher_pid'] is None:
                        entry['launcher_pid'] = pid
                        entry['launcher_ctime'] = ctime

                    if is_worker and not entry['primary_is_worker']:
                        is_new_primary = True
                    elif is_worker != entry['primary_is_worker']:
                        is_new_primary = False
                    else:
                        is_new_primary = (cpu_pct > entry['max_cpu']) or (
                            cpu_pct == entry['max_cpu'] and
                            rss > entry['max_rss'])

                    if is_new_primary:
                        entry['primary_is_worker'] = is_worker
                        entry['max_cpu'] = cpu_pct
                        entry['max_rss'] = rss
                        entry['primary_pid'] = pid
                        entry['primary_ctime'] = ctime

                    if avd_id and not entry['avd_id']:
                        entry['avd_id'] = avd_id
                    if display_name and not entry['display_name']:
                        entry['display_name'] = display_name
                    if grpc_endpoint and not entry['grpc_endpoint']:
                        entry['grpc_endpoint'] = grpc_endpoint
                    if no_netsim:
                        entry['no_netsim'] = True
                    entry['avd_name'] = (entry['avd_id'] or
                                         entry['display_name'] or
                                         entry['avd_name'] or avd_name)
            else:
                cpu_str = f"{cpu_pct:.1f}%"
                ram_str = f"{rss / (1024**2):.1f}MB ({threads} thr, {ctx_total} cs)"
                unmapped_qemu.append((pid, cpu_str, ram_str))

        except (psutil.NoSuchProcess, psutil.AccessDenied,
                psutil.ZombieProcess):
            pass

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


_LAST_DISK_IO = None
_LAST_DISK_TIME = time.monotonic()


def get_host_stats():
    global _LAST_DISK_IO, _LAST_DISK_TIME
    os_name = platform.system()
    host_total = f"{psutil.cpu_percent(interval=None):.1f}%"

    mem = psutil.virtual_memory()
    host_ram = f"{mem.percent:.1f}% ({mem.used / (1024**3):.1f}GB/{mem.total / (1024**3):.1f}GB)"

    swap = psutil.swap_memory()
    host_swap = f"{swap.percent:.1f}% ({swap.used / (1024**3):.1f}GB/{swap.total / (1024**3):.1f}GB)"

    qemu_stats, unmapped_qemu, netsim_str, netsim_ram_str, qemu_pids, qemu_avds = get_process_stats(
    )

    pgin, pgout = 0, 0
    if os_name == "Darwin":
        for line in run_cmd(['vm_stat'], timeout=2).splitlines():
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
                    if entry.current is not None
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
                        if val < 150.0:
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
                    freq_str = f"@{int(freq.current)}MHz" if freq and hasattr(
                        freq, 'current') and freq.current else ""
                except Exception:
                    freq_str = ""
                batt_str = f"{batt.percent:.1f}% ({status}) {freq_str}".strip()
    except Exception:
        pass

    curr_time = time.monotonic()
    elapsed = max(0.001, curr_time - _LAST_DISK_TIME)
    curr_disk = psutil.disk_io_counters()

    read_mb, write_mb = 0.0, 0.0
    if _LAST_DISK_IO and curr_disk:
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
        return known_model if known_model else f"Unknown ({status})", True, f"N/A ({status})", f"N/A ({status})", f"N/A ({status})", f"N/A ({status})"

    model = known_model
    if not model or "android" not in model.lower(
    ) or "unknown model" in model.lower() or "error:" in model.lower(
    ) or "offline" in model.lower() or ("Res: N/A" in model and is_booting):
        script = 'm=$(getprop ro.product.model); v=$(getprop ro.build.version.release); a=$(getprop ro.build.version.sdk); b=$(getprop ro.build.id); w=$(wm size 2>/dev/null); c=$(getprop sys.boot_completed); echo "$m|!|$v|!|$a|!|$b|!|$w|!|$c"'
        m_out = run_cmd(['adb', '-s', serial, 'shell', script],
                        timeout=2).strip()
        if m_out and "error:" not in m_out.lower(
        ) and "offline" not in m_out.lower():
            parts = m_out.split('|!|')
            if len(parts) >= 6:
                res = parts[4].lower().split('size:')[-1].strip(
                ) if "size:" in parts[4].lower() else "N/A"
                model = f"{parts[0]} | Android {parts[1]} (API {parts[2]}) | Build: {parts[3]} | Res: {res}"
                is_booting = (parts[5].strip() != "1")
        else:
            model = "Unknown Model"

    guest_ram = "N/A"
    guest_swap = "N/A"

    # BATCH OPTIMIZATION: Combine separated ADB shell calls into a single string to entirely bypass network/daemon round-trip latency
    batch_script = "cat /proc/meminfo; echo '===ADB_MON_SPLIT==='; df -h /data 2>/dev/null; echo '===ADB_MON_SPLIT==='; logcat -d -t 500 -b main,crash *:E 2>/dev/null | grep -v \"^---------\" | wc -l"
    batch_script += "; echo '===ADB_MON_SPLIT==='; getprop sys.boot_completed"
    batch_script += "; echo '===ADB_MON_SPLIT==='; vmstat"

    out = run_cmd(['adb', '-s', serial, 'shell', batch_script], timeout=2.5)

    if out and not any(err in out.lower()
                       for err in ["no devices", "offline", "unauthorized"]):
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
                    used = mem_total - mem_avail

            if len(parts) >= 2:
                for line in parts[1].splitlines():
                    # Fallback to simpler check to support mounts like /data/user/0
                    if '/data' in line:
                        dp = line.split()
                        if len(dp) >= 4:
                            pct_idx = next(
                                (i for i, x in enumerate(dp) if '%' in x), -1)
                            if pct_idx >= 2:
                                used_s, avail_s, pct = dp[pct_idx -
                                                          2], dp[pct_idx -
                                                                 1], dp[pct_idx]
                            else:
                                used_s, avail_s, pct = dp[2] if len(
                                    dp) > 2 else "N/A", dp[3] if len(
                                        dp) > 3 else "N/A", "N/A%"
                            data_space = f"{pct} ({used_s} used/{avail_s} avail)"
                            break

            if len(parts) >= 3:
                err_str = parts[2].strip()
                if err_str.isdigit():
                    err_count = int(err_str)

            if len(parts) >= 4:
                is_booting = (parts[3].strip() != "1")

            if len(parts) >= 5:
                vmstat_lines = parts[4].strip().splitlines()
                if vmstat_lines:
                    vmstat_cols = vmstat_lines[-1].split()
                    if len(vmstat_cols) >= 3 and vmstat_cols[2].isdigit():
                        swap_used_kb = int(vmstat_cols[2])
                        guest_swap = f"{swap_used_kb / 1024:.1f} MB"

            if mem_total > 0:
                guest_ram = f"{(used / mem_total) * 100:.1f}% ({used/1048576:.1f}GB/{mem_total/1048576:.1f}GB, {err_count} errs, {data_space} data)"
        except Exception:
            pass

    guest_diff, guest_real = "N/A", "N/A"
    out = run_cmd(
        ['adb', '-s', serial, 'shell', 'top', '-b', '-n', '2', '-d', '0.2'],
        timeout=2)
    if not out or "not found" in out.lower() or "usage" in out.lower():
        out = run_cmd(
            ['adb', '-s', serial, 'shell', 'top', '-n', '2', '-d', '1'],
            timeout=3)

    if out:
        out = RE_ANSI.sub('', out)
        target_block = out.split('Tasks:')[-1] if 'Tasks:' in out else (
            out.split('User ')[-1] if 'User ' in out else out)
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

    out = run_cmd(['adb', '-s', serial, 'shell', 'dumpsys', 'cpuinfo'],
                  timeout=2)
    if out:
        for line in out.splitlines():
            if match := RE_GUEST_DUMPSYS_TOT.search(line):
                return model, is_booting, f"{float(match.group(1)):.1f}%", f"{float(match.group(1)):.1f}% (Cores unknown)", guest_ram, guest_swap

    return model, is_booting, guest_diff, guest_real, guest_ram, guest_swap


def get_val(s, for_ts=False):
    """Safely extracts payload. Returns NaNs for timeseries data gaps preventing average/P95 zero skew."""
    if isinstance(s, (int, float)):
        return float(s)
    if for_ts and any(x in str(s)
                      for x in ("N/A", "Unsupported", "Calculating", "No ADB",
                                "Error", "Timeout", "Hang")):
        return float('nan')
    try:
        s_str = str(s)
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
RE_NETSIM_ARTIFACTS_PATH = re.compile(r'netsim artifacts path:\s*"([^"]+)"')
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
                    return boot_time + (starttime_ticks / float(clk_tck))
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
        if len(parts) < 6:
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
        st = os.stat(path)
    except OSError:
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
    if m and int(m.group(1)) <= 0xFFFFFFFF:
        return int(m.group(1))
    return None


def parse_netsim_ini(path):
    """Reads non-sensitive keys from a netsim.ini file (never reads token lines)."""
    data = {}
    if not path or not os.path.isfile(path):
        return data
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip()
                if 'token' in k.lower():
                    continue
                if k in ('grpc.port', 'hci.port', 'web.port', 'pid'):
                    try:
                        data[k] = int(v)
                    except ValueError:
                        data[k] = v
                else:
                    data[k] = v
    except OSError:
        pass
    return data


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
    (Linux /proc/locks); 'pid' and 'port' are None unless valid.
    """
    data = parse_netsim_ini(path)
    holders = []
    for suffix in ('.lock', '.init.lock'):
        key = get_file_lock_key(path + suffix)
        if key in lock_holders:
            holders.append(lock_holders[key])
    pid = data.get('pid')
    port = data.get('grpc.port')
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = None
    return {
        'path': path,
        'data': data,
        'holders': holders,
        'pid': pid if isinstance(pid, int) and 0 < pid <= 0xFFFFFFFF else None,
        'port': port if isinstance(port, int) and 0 <= port <= 0xFFFF else None,
        'instance': extract_ini_instance_num(path),
        'mtime': mtime,
    }


def _extract_netsim_instance_num(cmdline_tokens, environ_dict=None):
    """Determines netsim instance number from NETSIM_INSTANCE or -i/--instance."""
    if environ_dict and environ_dict.get('NETSIM_INSTANCE'):
        try:
            return int(environ_dict['NETSIM_INSTANCE'])
        except ValueError:
            pass
    if cmdline_tokens:
        for idx, tok in enumerate(cmdline_tokens):
            if tok in ('-i', '--instance') and idx + 1 < len(cmdline_tokens):
                try:
                    return int(cmdline_tokens[idx + 1])
                except ValueError:
                    pass
            elif tok.startswith('--instance='):
                try:
                    return int(tok.split('=', 1)[1])
                except ValueError:
                    pass
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


class NetsimLogTailer:
    """Incremental log tailer for netsim_stderr.log (1.0.23/1.0.24 and HEAD 1.0.25)."""

    def __init__(self, log_path):
        self.log_path = log_path
        self.offset = 0
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
        self.wifi_associated_ts = None
        self.last_timestamp = None
        self.truncation_count = 0

    def poll(self, now_utc=None):
        if now_utc is None:
            now_utc = time.time()
        if not self.log_path or not os.path.isfile(self.log_path):
            return False
        try:
            st_size = os.path.getsize(self.log_path)
        except OSError:
            return False
        if st_size < self.offset:
            self.offset = 0
            old_trunc = self.truncation_count + 1
            self.reset_state()
            self.truncation_count = old_trunc
            _append_bounded(self.events, (now_utc, 'LOG_TRUNCATED'))
        if st_size == self.offset:
            return True
        try:
            with open(self.log_path, 'r', encoding='utf-8',
                      errors='replace') as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
        except OSError:
            return False

        for raw_line in chunk.splitlines():
            self.feed_line(raw_line, now_utc=now_utc)
        return True

    def feed_line(self, raw_line, now_utc=None):
        if now_utc is None:
            now_utc = time.time()
        stripped = raw_line.strip()
        if not stripped:
            return
        level, ts, msg = parse_netsim_log_timestamp(raw_line, now_utc=now_utc)
        if ts is not None:
            self.last_timestamp = ts
        eff_ts = ts if ts is not None else (self.last_timestamp or now_utc)

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
                self.grpc_port_logged = int(m_port.group(1))
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

        m_guid = RE_NETSIM_GUID_PORT.search(msg)
        if m_guid:
            try:
                self.pending_guid_port = (eff_ts, int(m_guid.group(1)))
            except ValueError:
                pass
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

        m_add_1024 = RE_NETSIM_ADD_CHIP_1024.search(msg)
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
            if self.wifi_associated_ts is None:
                self.wifi_associated_ts = eff_ts


def resolve_session_stats_chips(stats_json_path,
                                unknown_add_timestamps,
                                joined_emulator_count=1,
                                cached_state=None,
                                not_before=0.0):
    """Resolves 1.0.23/1.0.24 kind-less AddChip events via netsim_session_stats.json (L2).

    A file older than not_before (the daemon's start) was left by an earlier
    daemon in the same artifact dir and is ignored.
    """
    if not stats_json_path or stats_json_path.endswith('.tmp'):
        return 0, None, cached_state
    try:
        st = os.stat(stats_json_path)
        stats_mtime = st.st_mtime
    except OSError:
        return 0, None, cached_state
    if stats_mtime < not_before:
        return 0, None, cached_state

    parsed_json = None
    if (cached_state and cached_state.get('mtime') == stats_mtime and
            cached_state.get('data') is not None):
        parsed_json = cached_state['data']
    else:
        try:
            with open(stats_json_path, 'r', encoding='utf-8') as f:
                parsed_json = json.load(f)
            cached_state = {'mtime': stats_mtime, 'data': parsed_json}
        except (OSError, ValueError, TypeError):
            return (cached_state.get('num_devices', 0) if cached_state else 0,
                    cached_state.get('resolved') if cached_state else None,
                    cached_state)

    if not isinstance(parsed_json, dict):
        return 0, None, cached_state

    device_stats = parsed_json.get('device_stats') or []
    radio_stats = parsed_json.get('radio_stats') or []
    num_devices = len(device_stats)
    if num_devices != 1 or joined_emulator_count != 1:
        cached_state['num_devices'] = num_devices
        cached_state['resolved'] = None
        return num_devices, None, cached_state

    dev_id = device_stats[0].get('device_id')
    kind_durations = {}
    for r in radio_stats:
        if not isinstance(r, dict):
            continue
        if dev_id is not None and r.get('device_id') not in (None, dev_id):
            continue
        raw_kind = r.get('kind')
        if not raw_kind:
            continue
        norm_k = _normalize_chip_kind(raw_kind)
        try:
            dur = int(float(str(r.get('duration_secs', 0))))
        except (ValueError, TypeError):
            dur = 0
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
    if any(err in text for err in ('UNIMPLEMENTED', 'gRPC error',
                                   'Connection refused', 'transport error')):
        return [], text.strip()
    rows = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('---') or line.startswith('ID'):
            continue
        if '|' in line:
            cols = [c.strip() for c in line.split('|')]
            if len(cols) >= 7 and cols[0].isdigit():
                rows.append({
                    'id': int(cols[0]),
                    'device_name': cols[1],
                    'chip_kind': _normalize_chip_kind(cols[2]),
                    'raw_chip_kind': cols[2],
                    'state': cols[3].lower(),
                    'timestamp': cols[4],
                    'records': int(cols[5]) if cols[5].isdigit() else 0,
                    'size_bytes': int(cols[6]) if cols[6].isdigit() else 0,
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


def sanitize_netsim_filename(name):
    """Approximates netsim's capture filename sanitization (fallback after chip_id)."""
    if not name:
        return ''
    return ''.join(c for c in name if c.isalnum() or c in ('-', '_'))


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
        self.skipped_notes = []
        self.last_harvest_mono = 0.0
        self.cap_reached = False

    def _run_netsim_cli(self, netsim_bin, grpc_port, args, timeout=5.0):
        cmd = [netsim_bin or 'netsim', '-v', '-p', str(grpc_port)] + list(args)
        try:
            res = subprocess.run(cmd,
                                 capture_output=True,
                                 encoding='utf-8',
                                 errors='replace',
                                 timeout=timeout,
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
        has_unmanaged_emu = (self.enabled and bool(joined_emulators) and any(
            emu.get('device_name') and not any(
                dk == d_key and
                info.get('device_name') == emu.get('device_name')
                for (dk, _), info in self.enabled_by_monitor.items())
            for emu in joined_emulators))
        if (self.last_harvest_mono > 0 and
            (now_mono - self.last_harvest_mono) < interval and
                not shutting_down and not has_unmanaged_emu):
            return
        self.last_harvest_mono = now_mono

        grpc_port = daemon_info.get('grpc_port')
        netsim_bin = daemon_info.get('netsim_cli_bin') or 'netsim'
        rows = []
        if grpc_port and not daemon_info.get('exited'):
            out = self._run_netsim_cli(netsim_bin, grpc_port,
                                       ['capture', 'list'])
            parsed_rows, err = parse_netsim_capture_list(out)
            if not err:
                rows = parsed_rows
                daemon_info['capture_rows'] = rows

        if self.enabled and rows and grpc_port and not daemon_info.get(
                'exited'):
            d_key = daemon_info['daemon_key']
            for emu in joined_emulators:
                dev_name = emu.get('device_name')
                if not dev_name:
                    continue
                dev_rows = [r for r in rows if r['device_name'] == dev_name]
                for r in dev_rows:
                    if r['state'] == 'off':
                        pats = select_safe_capture_patterns(rows,
                                                            dev_name,
                                                            target_row=r)
                        if pats is None:
                            note = (
                                f"Skipped auto-enable for '{dev_name}' (chip {r['id']}) "
                                "due to ambiguous CLI substring match")
                            if note not in self.skipped_notes:
                                self.skipped_notes.append(note)
                            continue
                        patch_out = self._run_netsim_cli(
                            netsim_bin, grpc_port,
                            ['capture', 'patch', 'on'] + pats)
                        _, err = parse_netsim_capture_list(patch_out)
                        if not err:
                            self.enabled_by_monitor[(d_key, r['id'])] = {
                                'netsim_bin': netsim_bin,
                                'grpc_port': grpc_port,
                                'device_name': dev_name,
                                'row': r,
                            }

        self.harvest_daemon_files(daemon_info, joined_emulators)

    def harvest_daemon_files(self, daemon_info, joined_emulators):
        art_dir = daemon_info.get('artifact_dir')
        if not art_dir or not os.path.isdir(art_dir):
            return
        pcaps_dir = os.path.join(art_dir, 'pcaps')
        live_bytes = 0
        live_files = 0
        if os.path.isdir(pcaps_dir):
            try:
                for fname in os.listdir(pcaps_dir):
                    if not fname.endswith('.pcap'):
                        continue
                    fpath = os.path.join(pcaps_dir, fname)
                    try:
                        st = os.stat(fpath)
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
        if not daemon_info.get('same_user') or not _is_owned_dir(art_dir):
            return

        pid = daemon_info.get('pid') or 0
        start_epoch = int(daemon_info.get('start_wall') or 0)
        daemon_subdir = os.path.join(self.pcap_dir, f"{pid}-{start_epoch}")

        try:
            for fname in os.listdir(art_dir):
                if fname.startswith('netsim_artifacts_') and fname.endswith(
                        '.zip'):
                    zpath = os.path.join(art_dir, fname)
                    if zpath not in self.rescued_zips:
                        try:
                            st = os.lstat(zpath)
                            if not stat.S_ISREG(st.st_mode):
                                continue
                            if self.total_copied_bytes + st.st_size <= self.max_bytes:
                                os.makedirs(daemon_subdir, exist_ok=True)
                                dst = os.path.join(daemon_subdir, fname)
                                copied = _copy_regular_file(zpath, dst)
                                if copied is None:
                                    continue
                                self.rescued_zips.add(zpath)
                                self.total_copied_bytes += copied.st_size
                                self.copied_files[dst] = copied.st_size
                            else:
                                self.cap_reached = True
                        except OSError:
                            pass
        except OSError:
            pass

        if not _is_owned_dir(pcaps_dir):
            return

        chip_to_serial = {}
        name_to_serial = {}
        for emu in joined_emulators:
            serial = emu.get('serial') or 'unassigned'
            dev_name = emu.get('device_name') or ''
            if dev_name:
                name_to_serial[sanitize_netsim_filename(dev_name)] = serial
            for cid in emu.get('chip_ids', ()):
                if cid is not None:
                    chip_to_serial[str(cid)] = serial
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
                if prev_meta and prev_meta == (st.st_size, st.st_mtime):
                    continue
                prev_size = prev_meta[0] if isinstance(
                    prev_meta, tuple) else (prev_meta or 0)
                delta = max(0, st.st_size - prev_size)
                if self.total_copied_bytes + delta > self.max_bytes:
                    self.cap_reached = True
                    note = f"Reached --netsim-pcap-max-mb cap ({self.max_bytes // (1024 * 1024)} MB); skipped {fname}"
                    if note not in self.skipped_notes:
                        self.skipped_notes.append(note)
                    continue
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    if _copy_regular_file(src, dst) is None:
                        continue
                    self.copied_files[dst] = (st.st_size, st.st_mtime)
                    self.total_copied_bytes += delta
                except OSError:
                    pass
        except OSError:
            pass

    def cleanup(self, daemons_by_key):
        for (d_key, _), info in list(self.enabled_by_monitor.items()):
            daemon_info = daemons_by_key.get(d_key)
            if daemon_info and daemon_info.get('exited'):
                continue
            netsim_bin = info['netsim_bin']
            grpc_port = info['grpc_port']
            out = self._run_netsim_cli(netsim_bin, grpc_port,
                                       ['capture', 'list'])
            rows, err = parse_netsim_capture_list(out)
            if err or not rows:
                continue
            pats = select_safe_capture_patterns(rows,
                                                info['device_name'],
                                                target_row=info['row'])
            if pats:
                self._run_netsim_cli(netsim_bin, grpc_port,
                                     ['capture', 'patch', 'off'] + pats)
        for d_info in daemons_by_key.values():
            self.harvest_daemon_files(d_info,
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
    if port is None or not 0 <= port <= 0xFFFF or not _is_loopback_host(host):
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
                _is_loopback_host(c.raddr[0])):
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
                 out_dir='.'):
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
        self.socket_os_family = None
        self.socket_cli_runner = _run_cli_with_timeout
        # When set, INI discovery scans only these dirs (tests).
        self.custom_ini_dirs = []

    def _record_event(self, ts, text):
        entry = (ts, text)
        if not self.timeline or self.timeline[-1][1] != text:
            _append_bounded(self.timeline, entry)

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
            dirs = host_discovery_dirs(os.environ, uid=_current_uid())
            for env in daemon_environs:
                _extend_unique(dirs, get_netsim_discovery_dirs(env or {}))
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
                key = os.path.normcase(os.path.realpath(path))
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

        Mirrors NetsimTracker::match_inis_to_daemons() in rust/src/netsim.rs.
        """
        live_keys = sorted(live_keys)
        live_by_pid = {key[0]: key for key in live_keys}

        def written_after_start(ini, key):
            d = self.daemons.get(key)
            if d is None:
                return False
            # An unknown start time (create_time 0) or mtime accepts the INI.
            return (not d.get('create_time') or ini['mtime'] is None or
                    ini['mtime'] >= d['start_wall'] - _INI_MTIME_SLACK_S)

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
                    written_after_start(ini, key)):
                matched[i] = key
                claimed.add(key)
        for i, ini in enumerate(inis):
            if matched[i] is not None or ini['holders']:
                continue
            for key in live_keys:
                owned = self.daemons.get(key, {}).get('ini_path')
                if (key not in claimed and owned and
                        _same_file(owned, ini['path'])):
                    matched[i] = key
                    claimed.add(key)
                    break

        unowned = [
            key for key in live_keys if key not in claimed and
            key in self.daemons and not self.daemons[key].get('ini_path')
        ]
        # Stable, so equal start times keep the sorted key order.
        unowned.sort(key=lambda k: self.daemons[k]['start_wall'], reverse=True)
        for key in unowned:
            instance = self.daemons[key].get('instance_num')
            if instance is None:
                instance = DEFAULT_NETSIM_INSTANCE
            candidates = [
                i for i, ini in enumerate(inis) if matched[i] is None and
                not ini['holders'] and ini['pid'] is None and
                ini['instance'] == instance and written_after_start(ini, key)
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
            rec.get('state') == 'CONNECTING'
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
    def _merge_prior_run(rec, prev_rec, now_mono):
        """Carries cumulative session counters from `prev_rec` into `rec`."""
        if not prev_rec:
            return
        prior_disc = prev_rec.get('disconnect_count', 0)
        prior_total = prev_rec.get('connected_total_s', 0.0)
        if prev_rec.get('state') in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
            prior_disc += 1
            if prev_rec.get('connected_start_mono') is not None:
                prior_total += max(0.0,
                                   now_mono - prev_rec['connected_start_mono'])
        rec['disconnect_count'] = rec.get('disconnect_count', 0) + prior_disc
        rec['connected_total_s'] = (rec.get('connected_total_s', 0.0) +
                                    prior_total)
        if (prev_rec.get('worker_pid') and rec.get('worker_pid') and
                prev_rec.get('worker_pid') != rec.get('worker_pid')):
            rec['min_log_ts'] = rec.get('worker_start_ts')

    def migrate_bare_record(self, serial, storage_key, now_mono):
        """Migrates a provisional bare-serial record into its canonical AVD storage_key."""
        if storage_key == serial or serial not in self.emulator_records:
            return
        bare_rec = self.emulator_records.pop(serial)
        prev_rec = self.emulator_records.pop(storage_key, None)
        self._merge_prior_run(bare_rec, prev_rec, now_mono)
        bare_rec['storage_key'] = storage_key
        self.emulator_records[storage_key] = bare_rec

    @staticmethod
    def _reset_for_new_worker(rec, worker_pid, worker_start_ts, no_netsim,
                              now_mono):
        """Resets per-run fields when the same AVD restarts under a new PID."""
        if rec.get('state') in ('CONNECTED', 'CONNECTED_NO_CHIPS'):
            rec['disconnect_count'] = rec.get('disconnect_count', 0) + 1
            if rec.get('connected_start_mono') is not None:
                rec['connected_total_s'] = (
                    rec.get('connected_total_s', 0.0) +
                    max(0.0, now_mono - rec['connected_start_mono']))
        rec['worker_pid'] = worker_pid
        rec['worker_start_ts'] = worker_start_ts
        rec['first_seen_mono'] = now_mono
        rec['min_log_ts'] = worker_start_ts
        rec['no_netsim'] = bool(no_netsim)
        rec['state'] = 'CONNECTING'
        rec['ambiguous'] = False
        rec['daemon_key'] = None
        rec['netsim_daemon_ready_s'] = None
        rec['first_transport_ts'] = None
        rec['netsim_connect_s'] = None
        rec['chip_attach_s'] = {}
        rec['chip_ids'] = set()
        rec['unknown_chip_count'] = 0
        rec['wifi_associated_s'] = None
        rec['connected_start_mono'] = None
        rec['adb_online_since_mono'] = None
        rec['missing_since_mono'] = None
        rec['missed_ticks'] = 0

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
                # An unreadable daemon env falls back to ours, which netsimd
                # normally inherits.
                art_dir = resolve_netsim_artifact_dir(env, inst_num)
                log_path = resolve_netsim_stderr_log_path(art_dir, inst_num)
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
                same_user = _is_same_user(p.get('uid'))
                netsim_cli_bin = resolve_netsim_cli_bin(exe_path, same_user)

                frontend_off = any(
                    tok in ('--no-cli-ui', '--no-web-ui') for tok in cmd)
                pcap_cli_on = any(tok == '--pcap' for tok in cmd)

                self.daemons[d_key] = {
                    'daemon_key':
                        d_key,
                    'pid':
                        pid,
                    'create_time':
                        ctime,
                    'start_wall':
                        precise_start or now_wall,
                    'first_seen_mono':
                        now_mono,
                    'last_seen_mono':
                        now_mono,
                    'spawner':
                        spawner,
                    'cmdline':
                        cmd,
                    'environ':
                        env,
                    'instance_num':
                        inst_num,
                    'artifact_dir':
                        art_dir,
                    'log_path':
                        log_path,
                    'tailer':
                        NetsimLogTailer(log_path) if self.enable_log else None,
                    'netsim_cli_bin':
                        netsim_cli_bin,
                    'same_user':
                        same_user,
                    'version':
                        None,
                    'ini_path':
                        None,
                    'grpc_port':
                        None,
                    'ready_ts':
                        None,
                    'netsim_daemon_ready_s':
                        None,
                    'frontend_disabled':
                        frontend_off,
                    'pcap_flag':
                        pcap_cli_on,
                    'log_available':
                        False,
                    'exited':
                        False,
                    'exit_reason':
                        None,
                    'orphaned':
                        False,
                    'zero_devices_since_mono':
                        now_mono,
                    'stats_cache':
                        None,
                    'capture_rows': [],
                    'pcap_live_bytes':
                        0,
                    'pcap_live_files':
                        0,
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

            if self.enable_log and d['tailer'] is not None:
                ok = d['tailer'].poll(now_utc=now_wall)
                d['log_available'] = ok
                if not ok and not d.get('log_unavail_recorded'):
                    d['log_unavail_recorded'] = True
                    self._record_event(now_wall, f"LOG_UNAVAILABLE(pid={pid})")
                tailer = d['tailer']
                if tailer.artifact_dir_logged and os.path.isdir(
                        tailer.artifact_dir_logged):
                    d['artifact_dir'] = tailer.artifact_dir_logged
                if tailer.version:
                    d['version'] = tailer.version
                if tailer.ini_path_logged:
                    d['ini_path'] = tailer.ini_path_logged
                if tailer.grpc_port_logged:
                    d['grpc_port'] = tailer.grpc_port_logged
                if tailer.ready_ts and d['ready_ts'] is None:
                    d['ready_ts'] = tailer.ready_ts
                    d['netsim_daemon_ready_s'] = max(
                        0.0, tailer.ready_ts - d['start_wall'])
                    self._record_event(
                        tailer.ready_ts,
                        f"READY(pid={pid}, port={d['grpc_port']}, ready_s={d['netsim_daemon_ready_s']:.2f}s)"
                    )
                if tailer.frontend_disabled and not d['frontend_disabled']:
                    d['frontend_disabled'] = True
                    self._record_event(now_wall,
                                       f"FRONTEND_DISABLED(pid={pid})")
                if tailer.pcap_flag:
                    d['pcap_flag'] = True
                if tailer.client_mode_spawn and not d.get(
                        'client_mode_recorded'):
                    d['client_mode_recorded'] = True
                    self._record_event(now_wall,
                                       f"CLIENT_MODE_SPAWN(pid={pid})")

            art_dir = d.get('artifact_dir')
            if art_dir:
                art_dir_counts[art_dir] = art_dir_counts.get(art_dir, 0) + 1

        for art_dir, cnt in art_dir_counts.items():
            if cnt > 1:
                self._record_event(now_wall,
                                   f"DUPLICATE_ARTIFACT_DIR({art_dir})")

        ini_files = self._discover_ini_files(
            [self.daemons[k]['environ'] for k in sorted(seen_daemon_keys)])
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
                prev_key = self._last_active_by_ini.get(ini_path)
                if prev_key is not None and prev_key != m_key:
                    self.restart_count += 1
                    self._record_event(
                        now_wall, f"RESTARTED(ini={ini_path}, "
                        f"old_pid={prev_key[0]}, new_pid={m_key[0]})")
                self.stale_inis.pop(ini_path, None)
                self._last_active_by_ini[ini_path] = m_key
            elif not ini['holders'] and ini_path not in self.stale_inis:
                # A pid= naming a tracked daemon that did not match was
                # reused; only an untracked live writer keeps the INI fresh.
                writer_alive = (ini['pid'] is not None and
                                ini['pid'] not in seen_pids and
                                psutil.pid_exists(ini['pid']))
                if not writer_alive:
                    self._record_event(now_wall, f"STALE_INI(path={ini_path})")
                    self.stale_inis[ini_path] = ini['data']

        for d_key, d in list(self.daemons.items()):
            if d_key not in seen_daemon_keys and not d.get('exited'):
                d['exited'] = True
                if self.enable_log and d['tailer'] is not None:
                    d['tailer'].poll(now_utc=now_wall)
                reason = (d['tailer'].exit_reason_hint if
                          (d['tailer'] and
                           d['tailer'].exit_reason_hint) else None)
                if not reason:
                    ini_p = d.get('ini_path')
                    if ini_p and os.path.exists(ini_p):
                        reason = 'killed_or_crashed'
                    else:
                        reason = 'clean_unknown'
                d['exit_reason'] = reason
                self._record_event(now_wall,
                                   f"EXITED(pid={d['pid']}, reason={reason})")
                self.pcap_mgr.poll_and_manage_daemon(d,
                                                     d.get(
                                                         'joined_emulators',
                                                         []),
                                                     now_mono=now_mono,
                                                     shutting_down=True)

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

        effective_emulators = dict(summary_emulators)
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
            pid_changed = bool(
                rec.get('worker_pid') and live_pid and
                rec['worker_pid'] != live_pid)
            replaced = bool(
                rec.get('avd_id') and
                ((live_avd and rec['avd_id'] != live_avd) or
                 (pid_changed and live_avd != rec['avd_id'])))
            adb_lists = serial in connected_devices
            if not replaced and (serial in all_emu_group_pids or
                                 (adb_lists and not rec.get('host_seen'))):
                if rec:
                    rec['missing_since_mono'] = None
                    rec['missed_ticks'] = 0
                continue
            if not rec:
                if not adb_lists:
                    gone_keys.add(s_key)
                continue
            adb_live = connected_devices.get(serial, 'offline') != 'offline'
            if (replaced or self._is_settled(rec) or
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
            if s_key != serial and serial in self.emulator_records:
                g_info = all_emu_group_info.get(serial) or {}
                cur_pid = g_info.get('primary_pid') or e_data.get('current_pid')
                bare_rec = self.emulator_records.get(serial)
                if bare_rec and (not cur_pid or not bare_rec.get('worker_pid')
                                 or bare_rec.get('worker_pid') == cur_pid):
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
                if (serial in live_serials or self._is_settled(rec) or
                        self._record_missed_tick(rec, adb_live, now_mono)):
                    gone_keys.add(s_key)
                else:
                    held_keys.add(s_key)
        # A held emulator may still be running, so it counts as live.
        live_emulator_count = len(effective_emulators) - len(gone_keys)

        display_name_counts = {}
        for s_key, e_data in effective_emulators.items():
            if s_key in gone_keys or s_key in held_keys:
                continue
            serial = e_data.get('current_serial', s_key)
            g_info = all_emu_group_info.get(serial) or {}
            disp = (g_info.get('display_name') or g_info.get('avd_id') or
                    e_data.get('name', '').split(' | ')[0])
            if disp:
                display_name_counts[disp] = display_name_counts.get(disp, 0) + 1

        for s_key, e_data in effective_emulators.items():
            if s_key in held_keys:
                continue
            serial = e_data.get('current_serial', s_key)
            gone = s_key in gone_keys
            g_info = {} if gone else (all_emu_group_info.get(serial) or {})
            worker_pid = g_info.get('primary_pid') or e_data.get('current_pid')
            worker_ctime = (g_info.get('primary_ctime') or
                            g_info.get('primary_create_time'))
            worker_start_ts = (get_proc_precise_start_time(
                worker_pid, fallback_create_time=worker_ctime)
                               if worker_pid else now_wall)

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
                    'worker_start_ts':
                        worker_start_ts,
                    'first_seen_mono':
                        e_data.get('first_seen', now_mono),
                    'min_log_ts':
                        None,
                    'no_netsim':
                        bool(g_info.get('no_netsim')),
                    'state':
                        'CONNECTING',
                    'ambiguous':
                        False,
                    'daemon_key':
                        None,
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
            if not gone and serial in all_emu_group_pids:
                rec['host_seen'] = True
                rec['missing_since_mono'] = None
                rec['missed_ticks'] = 0
            if worker_pid and rec.get('worker_pid') != worker_pid:
                if rec.get('worker_pid'):
                    self._reset_for_new_worker(rec, worker_pid, worker_start_ts,
                                               g_info.get('no_netsim'),
                                               now_mono)
                    e_data['first_seen'] = now_mono
                else:
                    rec['worker_pid'] = worker_pid
                    rec['worker_start_ts'] = worker_start_ts
                    if rec.get('min_log_ts') is not None:
                        rec['min_log_ts'] = worker_start_ts
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
                    target_port = int(endpoint.rsplit(':', 1)[1])
                except ValueError:
                    pass

            # A finished emulator has no endpoint (g_info is empty) and joins
            # no daemon: the serial's sockets belong to whatever runs there now.
            joined_daemon = None
            for d in self.daemons.values():
                if d.get('exited'):
                    continue
                if target_port and d.get('grpc_port') == target_port:
                    joined_daemon = d
                    break
            if joined_daemon is None and not gone:
                for d in self.daemons.values():
                    if d.get('exited') or not d.get('grpc_port'):
                        continue
                    conns = port_to_conns.get(d['grpc_port'], [])
                    if any(c['serial'] == serial for c in conns):
                        joined_daemon = d
                        break
            if joined_daemon is None and not gone:
                active_daemons = [
                    d for d in self.daemons.values() if not d.get('exited')
                ]
                if len(active_daemons) == 1:
                    joined_daemon = active_daemons[0]

            if joined_daemon is not None:
                rec['daemon_key'] = joined_daemon['daemon_key']
                joined_daemon['joined_emulators'].append(rec)
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

                if emu_conns and rec['first_transport_ts'] is None:
                    rec['first_transport_ts'] = now_wall

                dev_name = rec.get('device_name') or ''
                if dev_name and display_name_counts.get(
                        dev_name, 0) > 1 and not emu_peer_ports:
                    rec['ambiguous'] = True
                else:
                    rec['ambiguous'] = False

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
                        if c_dev == dev_name or (not dev_name and
                                                 live_emulator_count == 1 and
                                                 c_dev not in other_names):
                            if (rec['first_transport_ts'] is None or
                                    c_ts < rec['first_transport_ts']):
                                rec['first_transport_ts'] = c_ts
                    for add_ev in tailer.chip_adds:
                        if log_floor is not None and add_ev['ts'] < log_floor:
                            continue
                        matches_dev = (add_ev['device_name'] == dev_name or
                                       (add_ev.get('peer_port') and
                                        add_ev['peer_port'] in emu_peer_ports))
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
                        if gone or serial not in all_emu_group_pids:
                            rec['state'] = 'FAILED(worker_exit_no_connect)'
                        else:
                            elapsed_wait = now_mono - rec.get(
                                'first_seen_mono',
                                e_data.get('first_seen', now_mono))
                            if elapsed_wait >= 50.0:
                                rec['state'] = 'STALLED'
                            else:
                                rec['state'] = 'CONNECTING'
            else:
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
                    elif gone or serial not in all_emu_group_pids:
                        rec['state'] = 'FAILED(worker_exit_no_connect)'

        for d in self.daemons.values():
            if d.get('exited'):
                continue
            joined = [
                r for r in d.get('joined_emulators', [])
                if not self._is_settled(r)
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

            if len(joined) == 1:
                rec = joined[0]
                log_floor = (rec['min_log_ts'] - _LOG_FLOOR_SLACK_S
                             if rec.get('min_log_ts') is not None else None)
                tailer = d.get('tailer')
                if (tailer and tailer.wifi_associated_ts and
                        rec.get('worker_start_ts') and
                    (log_floor is None or
                     tailer.wifi_associated_ts >= log_floor)):
                    rec['wifi_associated_s'] = max(
                        0.0, tailer.wifi_associated_ts - rec['worker_start_ts'])
                if not rec['chip_attach_s'] or all(
                        is_approx
                        for _, is_approx in rec['chip_attach_s'].values()):
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
                        not_before=not_before)
                    d['stats_cache'] = new_cache
                    if resolved:
                        w_start = rec.get('worker_start_ts') or d['start_wall']
                        for k, (ev_ts, is_approx) in resolved.items():
                            rec['chip_attach_s'][k] = (max(
                                0.0, ev_ts - w_start), is_approx)

            self.pcap_mgr.poll_and_manage_daemon(d, joined, now_mono=now_mono)

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
            d for d in self.daemons.values() if not d.get('exited')
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

        emulator_rows = {}
        for rec in self.emulator_records.values():
            serial = rec['serial']
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
            d for d in self.daemons.values() if not d.get('exited')
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
        is_alert = False
        try:
            val = get_val(c_val)
            if "Battery" in label:
                if val <= 10:
                    is_alert = True
            elif "°C" in c_val:
                if val > 85:
                    is_alert = True
            elif "%" in c_val:
                threshold = 750 if any(x in label for x in [
                    "Total Device CPU", "Host QEMU CPU",
                    "Unmapped Emulator CPU", "Network Simulator"
                ]) else 90
                if val > threshold:
                    is_alert = True
                    color = Colors.RED + Colors.BOLD
        except Exception:
            pass

        try:
            raw_val = get_val(c_val)
            clamped_val = min(100.0, max(0.0, raw_val))
            filled = int((clamped_val / 100.0) * bar_width)
            half = 1 if ((clamped_val / 100.0) * bar_width -
                         filled) >= 0.5 else 0
            bar = f"[{'█' * filled}{'▌' * half}{' ' * max(0, (bar_width - filled - half))}]"
        except Exception:
            bar = f"[{' ' * bar_width}]"

        is_placeholder = any(
            x in c_val
            for x in ["Unsupported", "No ADB", "Error", "Timeout", "Hang"
                     ]) or "Calculating" in c_val
        try:
            if math.isnan(raw_val) or (raw_val == 0.0 and "N/A" in c_val and
                                       "%" not in c_val):
                is_placeholder = True
        except Exception:
            pass

        r_c, l_c = (Colors.CYAN,
                    Colors.BOLD) if is_placeholder else (color, Colors.BOLD)

        # Override host visual labels to Blue strictly if threshold warnings aren't already actively triggering
        if "Host QEMU" in label and not is_alert and not is_placeholder:
            r_c = Colors.BLUE

        # Ensure we strip ANSI before calculating offsets
        c_val_clean = RE_ANSI.sub('', c_val)
        c_val_clean_len = len(c_val_clean)

        if "%" in c_val or "°C" in c_val:
            if is_alert:
                cap = max(0, val_width - 6)
                c_val_display = c_val_clean[:
                                            cap] + "…" if c_val_clean_len > cap else c_val_clean
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
    print_row("Host Thermal Temperature", temp_str)
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

            if serial in qemu_stats:
                print_row("Host QEMU CPU", qemu_stats[serial][0])
                print_row("Host QEMU RAM", qemu_stats[serial][1])

            print_row("Guest Swap Memory", g_swap)
            if emu_gpu_stats and serial in emu_gpu_stats:
                print_row("Host QEMU GPU Load", emu_gpu_stats[serial])

            if errs_to_print:
                print_row("Guest Logcat Errors", errs_to_print)
            else:
                print_row("Guest Logcat Errors", "N/A")

            if (netsim_snapshot and
                    serial in netsim_snapshot.get('emulator_rows', {})):
                print_row("Netsim Status",
                          netsim_snapshot['emulator_rows'][serial])

    display(f"{c1}└{'─' * 32}┴{'─' * (width - 35)}┘{c2}\033[K")
    msg = f"Last Updated: {time.strftime('%H:%M:%S')}" if is_once else f"Press Ctrl+C to exit. Last Updated: {time.strftime('%H:%M:%S')} | Refreshing{('.' * (iter_count % 4)):<3}"
    display(f"\n{Colors.YELLOW}{msg}{Colors.RESET}\033[K")

    if not is_once:
        display('\033[J')
    sys.stdout.write('\n'.join(buf) + '\n')
    sys.stdout.flush()


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

    duration = max(0.1, end_time - summary_data['start_time'])
    ts_data = list(summary_data.get('time_series', []))

    def get_p95(col_name):
        if not ts_data:
            return 0.0
        vals = [
            r[col_name] for r in ts_data if col_name in r and
            r[col_name] is not None and not math.isnan(r[col_name])
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
            r[col_name] is not None and not math.isnan(r[col_name])
        ]
        return sum(vals) / len(vals) if vals else 0.0

    def get_max(col_name):
        if not ts_data:
            return 0.0
        vals = [
            r[col_name] for r in ts_data if col_name in r and
            r[col_name] is not None and not math.isnan(r[col_name])
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
    adb_ver = "N/A"
    adb_out = run_cmd(['adb', 'version'])
    if adb_out:
        lines = adb_out.splitlines()
        if lines:
            adb_ver = lines[0].strip()

    is_vm = False
    try:
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                if 'hypervisor' in f.read():
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
        f"  Average Thermal:   {avg_t:.1f}°C",
        f"  Peak Thermal:      {max_t:.1f}°C",
        f"  P95 Thermal:       {p95_t:.1f}°C",
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
            lines.extend([
                f"    {t} | CPU: {c:<15} | RAM: {r} | Swap: {sw}"
                if len(entry) >= 4 else
                f"    {entry[0]} | CPU: {entry[1]:<15} | RAM: {entry[2]}"
                for entry in e_data['boot_log']
                for t, c, r, sw in (
                    [entry if len(entry) == 4 else entry + ("N/A",)],)
            ])
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
                    if v is not None and not math.isnan(v):
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
                return [r.get(key, float('nan')) for r in ts_data]

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

                lines.append("=" * 80)
                lines.append(f"CHARTS FOR: {title_name}")
                lines.append("=" * 80)
                lines.append("")

                e_cpu = extract(f'emu_cpu_tot_{serial}')
                if any(v > 0
                       for v in e_cpu
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, e_cpu,
                                   f"GUEST CPU HISTORY ({serial}):", "Time (s)",
                                   "Usage (%)"))

                e_ram = extract(f'emu_ram_{serial}')
                if any(v > 0
                       for v in e_ram
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, e_ram,
                                   f"GUEST RAM HISTORY ({serial}):", "Time (s)",
                                   "Usage (%)"))

                q_cpu = extract(f'qemu_cpu_{serial}')
                if any(v > 0
                       for v in q_cpu
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, q_cpu,
                                   f"HOST QEMU CPU HISTORY ({serial}):",
                                   "Time (s)", "Usage (%)"))

                q_ram = extract(f'qemu_ram_{serial}')
                if any(v > 0
                       for v in q_ram
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, q_ram,
                                   f"HOST QEMU RAM HISTORY ({serial}):",
                                   "Time (s)", "MB"))

                e_disk = extract(f'emu_disk_pct_{serial}')
                if any(v > 0
                       for v in e_disk
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(
                            times, e_disk,
                            f"GUEST DATA PARTITION FULLNESS ({serial}):",
                            "Time (s)", "Usage (%)"))

                e_swap = extract(f'emu_swap_{serial}')
                if any(v > 0
                       for v in e_swap
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, e_swap,
                                   f"GUEST SWAP MEMORY HISTORY ({serial}):",
                                   "Time (s)", "MB"))

                e_errs = extract(f'emu_errs_{serial}')
                if any(v > 0
                       for v in e_errs
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, e_errs,
                                   f"GUEST LOGCAT ERRORS ({serial}):",
                                   "Time (s)", "Errors"))

                q_cs = extract(f'qemu_cs_{serial}')
                if any(v > 0
                       for v in q_cs
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, q_cs,
                                   f"QEMU CONTEXT SWITCHES ({serial}):",
                                   "Time (s)", "Switches"))

                q_gpu = extract(f'qemu_gpu_{serial}')
                if any(v > 0
                       for v in q_gpu
                       if v is not None and not math.isnan(v)):
                    lines.extend(
                        make_chart(times, q_gpu,
                                   f"HOST QEMU GPU LOAD HISTORY ({serial}):",
                                   "Time (s)", "Usage (%)"))
        except ImportError:
            pass

        except Exception:
            pass

    out_dir_expanded = os.path.expanduser(out_dir)
    try:
        os.makedirs(out_dir_expanded, exist_ok=True)
    except Exception:
        pass

    file_ts = time.strftime('%Y%m%d_%H%M%S')

    if save_txt:
        filename = os.path.join(
            out_dir_expanded, "device_monitor_summary_autosave.txt"
            if is_autosave else f"device_monitor_summary_{file_ts}.txt")
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
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

    if save_csv and ts_data:
        csv_filename = os.path.join(
            out_dir_expanded, "device_monitor_summary_autosave.csv"
            if is_autosave else f"device_monitor_summary_{file_ts}.csv")

        try:
            import csv
            # Instantly deduce unique headers without nested O(N*M) looping
            all_keys = list(dict.fromkeys(k for r in ts_data for k in r.keys()))

            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=all_keys, restval='')
                writer.writeheader()
                for row in ts_data:
                    row_copy = dict(row)

                    # Convert monotonic uptime delta back to an absolute UNIX epoch
                    epoch_time = summary_data['start_time'] + (
                        row['time'] - summary_data['start_time_monotonic'])
                    row_copy['time'] = time.strftime('%Y-%m-%d %H:%M:%S',
                                                     time.localtime(epoch_time))

                    for k, v in row_copy.items():
                        if isinstance(v, float) and math.isnan(v):
                            row_copy[k] = ""
                    writer.writerow(row_copy)

            print(
                f"{Colors.GREEN}CSV exported to {Colors.BOLD}{pathlib.Path(os.path.abspath(csv_filename)).as_uri()}{Colors.RESET}"
            )
        except Exception as e:
            print(f"\n{Colors.RED}Failed to export CSV file: {e}{Colors.RESET}")

    if not is_autosave:
        for cleanup_file in [
                "device_monitor_summary_autosave.txt",
                "device_monitor_summary_autosave.csv"
        ]:
            target = os.path.join(out_dir_expanded, cleanup_file)
            try:
                if os.path.exists(target):
                    os.remove(target)
            except Exception:
                pass


def handle_sigterm(signum, frame):
    raise KeyboardInterrupt()


def update_device_cache(device_names,
                        disconnect_times,
                        serial_pids,
                        connected_serials,
                        qemu_pids,
                        current_time,
                        timeout=5.0):
    """Updates the device cache state based on connectivity and PID changes.

    Extracted for testability.
    """
    for s in list(device_names.keys()):
        if s not in connected_serials:
            if s not in disconnect_times:
                disconnect_times[s] = current_time
            elif current_time - disconnect_times[s] > timeout:
                del device_names[s]
                del disconnect_times[s]
                if s in serial_pids:
                    del serial_pids[s]
        else:
            if s in disconnect_times:
                del disconnect_times[s]

            new_pid = qemu_pids.get(s)
            old_pid = serial_pids.get(s)
            if old_pid and new_pid and old_pid != new_pid:
                # PID changed! Invalidate cache immediately.
                del device_names[s]

            if new_pid:
                serial_pids[s] = new_pid


def main():
    exit_code = 0
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

    if not args.headless:
        setup_alt_screen(args.once, args.headless)

    os_name = platform.system()
    hardware_specs = get_static_hardware_info()
    iter_count, last_pgin, last_pgout, prev_term_width = 0, 0, 0, 0
    last_time = time.monotonic()
    last_auto_save = time.time()
    prev_stats, device_names = {}, {}
    disconnect_times, serial_pids = {}, {}
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
    psutil.cpu_percent(interval=None)
    timeout_bound = args.interval + 1.0

    # State Dictionaries securely resolve the critical Thread Pool Starvation bugs
    active_tasks = {'host': None, 'devices': None, 'guests': {}}
    last_devices = {}
    last_host_res = ("Calculating...", {}, "N/A", "N/A", "N/A", 0, 0, [], "N/A",
                     {}, "N/A", "N/A", {}, "N/A", "N/A", 0.0, 0.0, "N/A", {})
    last_guest_res = {}

    try:
        while True:
            loop_start = time.monotonic()

            # Asynchronously deduce host-level states strictly separated from main rendering thread
            if active_tasks['host'] is None or active_tasks['host'].done():
                active_tasks['host'] = executor.submit(get_host_stats)

            # Poll active devices attached to host asynchronously
            if active_tasks['devices'] is None or active_tasks['devices'].done(
            ):
                if active_tasks['devices'] is not None:
                    try:
                        last_devices = active_tasks['devices'].result()
                    except Exception:
                        pass
                active_tasks['devices'] = executor.submit(get_devices)

            devices = last_devices
            connected = set(devices.keys())

            for s, e_data in summary_data['emulators'].items():
                if e_data.get('current_serial', s) not in connected:
                    e_data['last_status'] = 'offline'

            # Submits tasks cleanly while avoiding uncompleted background threads from compounding latency
            for s in devices:
                if s not in active_tasks['guests'] or active_tasks['guests'][
                        s].done():
                    storage_key = next(
                        (k
                         for k, v in summary_data.get('emulators', {}).items()
                         if v.get('current_serial') == s), s)
                    is_booting = summary_data.get('emulators', {}).get(
                        storage_key, {}).get('is_booting', False)
                    active_tasks['guests'][s] = executor.submit(
                        get_guest_stats, s, device_names.get(s, ""), is_booting,
                        devices[s])

            futures_to_wait = [active_tasks['host']] + [
                active_tasks['guests'][s]
                for s in devices
                if s in active_tasks['guests']
            ]

            # Ensures absolute UI refresh pacing natively without abandoning lingering threads
            wait_timeout = max(0.1,
                               args.interval - (time.monotonic() - loop_start))
            concurrent.futures.wait(futures_to_wait, timeout=wait_timeout)

            if active_tasks['host'].done():
                try:
                    last_host_res = active_tasks['host'].result()
                except Exception as e:
                    last_host_res = (f"Error: {e}", {}, "N/A", "N/A", "N/A", 0,
                                     0, [], "N/A", {}, "N/A", "N/A", {}, "N/A",
                                     "N/A", 0.0, 0.0, "N/A", {})
                host_res = last_host_res
            else:
                ht = last_host_res[0]
                if "Timeout" not in ht and "Calculating" not in ht:
                    ht += " (Timeout)"
                host_res = (ht,) + last_host_res[1:]

            qemu_pids = host_res[9] if len(host_res) > 9 else {}
            update_device_cache(device_names, disconnect_times, serial_pids,
                                connected, qemu_pids, time.monotonic())

            guest_results, current_metrics = {}, {}
            qemu_avds = host_res[-1] if len(host_res) > 18 else {}

            # De-reference dead devices cleanly from tracking memory
            for s in list(active_tasks['guests'].keys()):
                if s not in devices and active_tasks['guests'][s].done():
                    del active_tasks['guests'][s]

            for serial in devices:
                f = active_tasks['guests'].get(serial)
                if f and f.done():
                    try:
                        model, is_booting, g_diff, g_real, g_ram, g_swap = f.result(
                        )
                        last_guest_res[serial] = (model, is_booting, g_diff,
                                                  g_real, g_ram, g_swap)
                    except Exception as e:
                        storage_key = next(
                            (k for k, v in summary_data.get('emulators',
                                                            {}).items()
                             if v.get('current_serial') == serial), serial)
                        last_b = summary_data.get('emulators', {}).get(
                            storage_key, {}).get('is_booting', False)
                        last_guest_res[serial] = (device_names.get(
                            serial, "Unknown"), last_b, "N/A", f"Error: {e}",
                                                  "N/A", "N/A")

                if serial in last_guest_res:
                    model, is_booting, g_diff, g_real, g_ram, g_swap = last_guest_res[
                        serial]
                    if f and not f.done(
                    ) and "Timeout" not in g_real and "Calculating" not in g_real:
                        g_real += " (Timeout)"
                else:
                    storage_key = next(
                        (k
                         for k, v in summary_data.get('emulators', {}).items()
                         if v.get('current_serial') == serial), serial)
                    last_b = summary_data.get('emulators', {}).get(
                        storage_key, {}).get('is_booting', False)
                    model, is_booting, g_diff, g_real, g_ram, g_swap = device_names.get(
                        serial, "Unknown"
                    ), last_b, "Calculating...", "Calculating...", "Calculating...", "Calculating..."

                current_metrics[serial] = (g_diff, g_real, g_ram, g_swap)
                if model:
                    device_names[serial] = model

                avd_name = qemu_avds.get(serial)
                new_pid = host_res[9].get(serial) if len(host_res) > 9 else None
                _, _, _, last_primary_proc_id = _snapshot_process_state()
                new_proc_id = last_primary_proc_id.get(serial, (new_pid, None))
                emulators = summary_data.get('emulators', {})
                existing_key = next(
                    (k for k, v in emulators.items()
                     if v.get('current_serial') == serial and new_pid and
                     v.get('current_pid') == new_pid),
                    next((k for k, v in emulators.items()
                          if v.get('current_serial') == serial), None))

                if existing_key:
                    old_pid = summary_data['emulators'][existing_key].get(
                        'current_pid')
                    pid_changed = bool(old_pid and new_pid and
                                       old_pid != new_pid)
                    if existing_key == serial and avd_name:
                        storage_key = f"{avd_name}_{serial}"
                        bare_entry = summary_data['emulators'].pop(serial)
                        if storage_key not in summary_data['emulators']:
                            summary_data['emulators'][storage_key] = bare_entry
                        netsim_tracker.migrate_bare_record(
                            serial, storage_key, time.monotonic())
                        for row in summary_data.get('time_series', ()):
                            for prefix in ('emu_cpu_tot_', 'emu_cpu_adj_',
                                           'emu_ram_', 'emu_swap_', 'emu_errs_',
                                           'emu_disk_pct_', 'qemu_cpu_',
                                           'qemu_ram_', 'qemu_cs_', 'qemu_gpu_',
                                           'netsim_state_'):
                                old_col = f"{prefix}{serial}"
                                new_col = f"{prefix}{storage_key}"
                                if old_col in row:
                                    val = row.pop(old_col)
                                    if new_col not in row:
                                        row[new_col] = val
                    elif pid_changed and avd_name:
                        storage_key = f"{avd_name}_{serial}"
                    else:
                        storage_key = existing_key
                else:
                    storage_key = (f"{avd_name}_{serial}"
                                   if avd_name else serial)

                effective_avd = avd_name
                if not effective_avd and existing_key and existing_key != serial and existing_key.endswith(
                        f"_{serial}"):
                    effective_avd = existing_key[:-len(serial) - 1]

                name = f"{serial} | {device_names.get(serial, 'Unknown Device')}"
                if effective_avd:
                    name = f"{effective_avd} | {device_names.get(serial, 'Unknown Device')}"

                if storage_key not in summary_data.get('emulators', {}):
                    summary_data['emulators'][storage_key] = {
                        'name':
                            name,
                        'current_serial':
                            serial,
                        'current_pid':
                            new_pid,
                        'current_proc_id':
                            new_proc_id,
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
                            time.monotonic(),
                        'last_boot_log_time':
                            0,
                        'boot_log': [],
                        'is_booting':
                            is_booting,
                        'track_boot_stats':
                            is_booting or devices.get(serial) == 'offline',
                        'boot_completed':
                            not is_booting and devices.get(serial) == 'device',
                        'last_status':
                            'offline',
                        'online_indicator_until':
                            0,
                        'last_seen':
                            time.monotonic()
                    }
                else:
                    was_booting = summary_data['emulators'][storage_key].get(
                        'is_booting', False)
                    if was_booting and not is_booting:
                        summary_data['emulators'][storage_key][
                            'boot_finished_time'] = time.monotonic()
                        summary_data['emulators'][storage_key][
                            'boot_completed'] = True

                    old_pid = summary_data['emulators'][storage_key].get(
                        'current_pid')
                    old_proc_id = summary_data['emulators'][storage_key].get(
                        'current_proc_id', (old_pid, None))

                    proc_restarted = False
                    if (old_proc_id and new_proc_id and old_proc_id[0] and
                            new_proc_id[0]):
                        if (old_proc_id[1] is not None and
                                new_proc_id[1] is not None):
                            proc_restarted = (old_proc_id != new_proc_id)
                        else:
                            proc_restarted = (old_proc_id[0] != new_proc_id[0])

                    if proc_restarted:
                        summary_data['emulators'][storage_key][
                            'first_seen'] = time.monotonic()
                        summary_data['emulators'][storage_key][
                            'boot_completed'] = False
                        summary_data['emulators'][storage_key][
                            'track_boot_stats'] = is_booting or devices.get(
                                serial) == 'offline'

                    summary_data['emulators'][storage_key][
                        'is_booting'] = is_booting

                    if is_booting and not summary_data['emulators'][
                            storage_key].get('boot_completed'):
                        summary_data['emulators'][storage_key][
                            'track_boot_stats'] = True
                    summary_data['emulators'][storage_key][
                        'last_seen'] = time.monotonic()
                    summary_data['emulators'][storage_key][
                        'current_serial'] = serial
                    summary_data['emulators'][storage_key][
                        'current_pid'] = new_pid
                    summary_data['emulators'][storage_key][
                        'current_proc_id'] = new_proc_id

                e_data = summary_data['emulators'][storage_key]
                e_data['name'] = name

                if iter_count > 0:
                    # Dynamically capture Boot Phase Resource Profiling automatically
                    if e_data.get('track_boot_stats') and (
                            time.monotonic() - e_data['first_seen'] < 900):
                        if not e_data.get('boot_completed') or (
                                'boot_finished_time' in e_data and
                                time.monotonic() - e_data['boot_finished_time']
                                < 300):
                            if time.monotonic() - e_data.get(
                                    'last_boot_log_time', 0) >= 5.0 and not any(
                                        x in g_real
                                        for x in ("N/A", "Error", "Timeout",
                                                  "Calculating")):
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
                    if not any(x in g_ram for x in ("N/A", "Error", "Timeout",
                                                    "Hang", "Calculating")):
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

            netsim_tracker.update(summary_data['emulators'],
                                  devices,
                                  now_wall=time.time(),
                                  now_mono=time.monotonic())
            netsim_snapshot = netsim_tracker.get_dashboard_snapshot(
                now_wall=time.time())
            summary_data[
                'netsim_summary_lines'] = netsim_tracker.format_summary_sections(
                    now_mono=time.monotonic())

            for storage_key, e_data in summary_data['emulators'].items():
                name = e_data['name']
                current_serial = e_data.get('current_serial', storage_key)
                if current_serial in connected:
                    g_diff, g_real, g_ram, g_swap = current_metrics[
                        current_serial]
                    current_status = 'offline' if "No ADB" in g_diff or "offline" in g_diff.lower(
                    ) else 'online'

                    if e_data.get('last_status'
                                 ) == 'offline' and current_status == 'online':
                        e_data['online_indicator_until'] = time.monotonic() + 10
                    e_data['last_status'] = current_status

                    tags = f" {Colors.GREEN}[ONLINE]{Colors.RESET}" if time.monotonic(
                    ) < e_data.get('online_indicator_until', 0) else ""
                    if e_data.get('track_boot_stats') and (
                            time.monotonic() - e_data['first_seen'] < 900):
                        if e_data.get('is_booting') or (
                                'boot_finished_time' in e_data and
                                time.monotonic() - e_data['boot_finished_time']
                                < 300):
                            tags += f" {Colors.YELLOW}[RECORDING BOOT STATS]{Colors.RESET}"
                    guest_results[current_serial] = (name, tags, g_diff, g_real,
                                                     g_ram, g_swap)
                else:
                    is_booting = e_data.get('is_booting', False)
                    last_seen = e_data.get('last_seen', 0)
                    if is_booting or (time.monotonic() - last_seen < 120):
                        tags = f" {Colors.RED}[DISCONNECTED]{Colors.RESET}"
                        guest_results[current_serial] = (name, tags, "N/A",
                                                         "N/A", "N/A", "N/A")

            curr_time = time.monotonic()
            elapsed = max(0.001, curr_time - last_time)

            # Safely decouples paging metric calculations from loop-timeouts to inherently guarantee no "millions of pages/s" delta spikes
            paging_unit = "pages/s" if os_name == "Darwin" else "KB/s"
            paging_str = "Calculating..."

            paging_in_rate = max(0.0, pgin -
                                 last_pgin) / elapsed if last_pgin > 0 else 0.0
            paging_out_rate = max(
                0.0, pgout - last_pgout) / elapsed if last_pgout > 0 else 0.0

            if pgin > 0 or pgout > 0:
                if last_pgin > 0 or last_pgout > 0:
                    paging_str = f"In: {int(paging_in_rate)} / Out: {int(paging_out_rate)} ({paging_unit})"
                last_pgin, last_pgout = pgin, pgout
            else:
                paging_str = "Calculating..." if iter_count == 0 else f"In: 0 / Out: 0 ({paging_unit})"

            if iter_count > 0:
                summary_data['iterations'] += 1
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
                    curr_s = e_data.get('current_serial', key)
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

                ts_entry = {
                    'time':
                        time.monotonic(),
                    'host_cpu':
                        get_val(host_total, True),
                    'host_ram':
                        get_val(host_ram, True),
                    'netsim_cpu':
                        get_val(netsim_cpu, True),
                    'netsim_ram':
                        get_val(netsim_ram, True),
                    'host_gpu':
                        get_val(gpu_load, True),
                    'host_gpu_mem':
                        get_val(gpu_ram, True),
                    'host_swap':
                        get_val(host_swap, True),
                    'host_thermal_c':
                        get_val(temp_str, True),
                    'host_disk_read_mbs':
                        read_mb,
                    'host_disk_write_mbs':
                        write_mb,
                    'host_paging_in':
                        paging_in_rate,
                    'host_paging_out':
                        paging_out_rate,
                    'host_battery':
                        get_val(batt_str, True),
                    'host_unmapped_qemu_cpu':
                        sum(get_val(c) for p, c, r in unmapped_qemu)
                        if unmapped_qemu else 0.0,
                    'host_unmapped_qemu_ram':
                        sum(get_val(r) for p, c, r in unmapped_qemu)
                        if unmapped_qemu else 0.0,
                }

                for key, e_data in summary_data['emulators'].items():
                    curr_s = e_data.get('current_serial', key)
                    if curr_s in current_metrics:
                        ts_entry[f'emu_cpu_tot_{key}'] = get_val(
                            current_metrics[curr_s][0], True)
                        ts_entry[f'emu_cpu_adj_{key}'] = get_val(
                            current_metrics[curr_s][1], True)
                        ts_entry[f'emu_ram_{key}'] = get_val(
                            current_metrics[curr_s][2], True)
                        ts_entry[f'emu_swap_{key}'] = get_val(
                            current_metrics[curr_s][3], True)
                        m_err = RE_GUEST_ERRS.search(current_metrics[curr_s][2])
                        ts_entry[f'emu_errs_{key}'] = float(
                            m_err.group(1)) if m_err else 0.0
                        m_pct = None
                        ram_parts = current_metrics[curr_s][2].split(',')
                        if len(ram_parts) >= 3:
                            m_pct = RE_GUEST_PCT_EXTRACT.search(ram_parts[2])
                        ts_entry[f'emu_disk_pct_{key}'] = float(
                            m_pct.group(1)) if m_pct else float('nan')
                    else:
                        ts_entry[f'emu_cpu_tot_{key}'] = float('nan')
                        ts_entry[f'emu_cpu_adj_{key}'] = float('nan')
                        ts_entry[f'emu_ram_{key}'] = float('nan')
                        ts_entry[f'emu_swap_{key}'] = float('nan')
                        ts_entry[f'emu_errs_{key}'] = float('nan')
                        ts_entry[f'emu_disk_pct_{key}'] = float('nan')

                    if curr_s in qemu_stats:
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
                        ts_entry[f'qemu_cpu_{key}'] = float('nan')
                        ts_entry[f'qemu_ram_{key}'] = float('nan')
                        ts_entry[f'qemu_cs_{key}'] = float('nan')
                        ts_entry[f'qemu_gpu_{key}'] = float('nan')

                ts_entry.update(netsim_tracker.get_csv_fields())

                summary_data['time_series'].append(ts_entry)

            def apply_trend(key, curr_str):
                if any(x in curr_str for x in [
                        "N/A", "Unsupported", "Calculating", "No ADB", "Error",
                        "Timeout"
                ]):
                    return curr_str
                curr_val, prev_val = get_val(curr_str), prev_stats.get(
                    key, get_val(curr_str))
                prev_stats[key] = curr_val
                return f"{curr_str} {'↑' if curr_val > prev_val + 0.5 else '↓' if curr_val < prev_val - 0.5 else '→'}"

            if iter_count > 0:
                host_total = apply_trend('h_cpu', host_total)
                host_ram = apply_trend('h_ram', host_ram)
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
                for k, v in zip(['h_cpu', 'h_ram', 'n_cpu'],
                                [host_total, host_ram, netsim_cpu]):
                    prev_stats[k] = get_val(v)
                for serial, stats in guest_results.items():
                    prev_stats[f'{serial}_real'] = get_val(stats[3])
                    prev_stats[f'{serial}_ram'] = get_val(stats[4])
                    prev_stats[f'{serial}_swap'] = get_val(stats[5])

            last_time = curr_time
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
                        netsim_tracker.poll_sockets_fast(now_wall=time.time())
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
        executor.shutdown(wait=False)
        if not args.headless:
            restore_screen(args.once, args.headless)

        try:
            netsim_tracker.cleanup()
        except Exception:
            pass

        try:
            summary_data['netsim_summary_lines'] = (
                netsim_tracker.format_summary_sections(
                    now_mono=time.monotonic()))
            save_summary(summary_data,
                         time.time(),
                         save_csv=args.csv,
                         save_txt=not args.no_summary,
                         out_dir=args.out_dir)
        except Exception:
            pass

        if exit_code == 0:
            print(
                f"{Colors.GREEN}Monitoring stopped cleanly. Goodbye!{Colors.RESET}\033[K"
            )
        sys.stdout.flush()

        os._exit(exit_code)  # Dispatch the correct state


if __name__ == "__main__":
    main()
