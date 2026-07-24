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
    except Exception: pass

import pathlib
import shutil
import time
import platform
import subprocess
import re
import argparse
import threading
import concurrent.futures
import glob
import math
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
    print(f"\033[91m[Error] Missing strictly required package: {', '.join(missing_deps)}\033[0m")
    print(f"\033[93mPlease install it via pip:\033[0m pip install {' '.join(missing_deps)}")
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
    if adb_path := shutil.which("adb"): return adb_path
    for env in ['ANDROID_HOME', 'ANDROID_SDK_ROOT']:
        if env_path := os.environ.get(env):
            adb_exe = os.path.join(env_path, "platform-tools", "adb" + (".exe" if os.name == 'nt' else ""))
            if os.path.exists(adb_exe): return adb_exe

    home = pathlib.Path.home()
    defaults = [
        home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
        home / "Android" / "Sdk" / "platform-tools" / "adb"
    ]

    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and platform.system() != "Windows":
        try:
            import pwd
            user_home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir)
            defaults.extend([
                user_home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
                user_home / "Android" / "Sdk" / "platform-tools" / "adb"
            ])
        except Exception: pass

    for p in defaults:
        if p.exists(): return str(p)
    return "adb"

ADB_PATH = find_adb()
sudo_user = os.environ.get('SUDO_USER')

def run_cmd(cmd, timeout=5, stderr_devnull=False):
    try:
        cmd = list(cmd)
        kwargs = {'stdin': subprocess.DEVNULL}
        if platform.system() == "Windows":
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

        if cmd[0] == 'adb':
            cmd[0] = ADB_PATH
            if sudo_user and hasattr(os, 'geteuid') and os.geteuid() == 0:
                cmd = ['sudo', '-n', '-u', sudo_user] + cmd

        stderr_target = subprocess.DEVNULL if stderr_devnull else subprocess.STDOUT
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=stderr_target,
            text=True, errors='replace', timeout=timeout, **kwargs
        )
        return result.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        # Hardened safety decode fixes a known Py3.8 bug where timeouts bypass text=True bounds
        if isinstance(out, bytes): out = out.decode('utf-8', errors='replace')
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
    except Exception: pass

    cpu_str = platform.processor() or "Unknown CPU"
    if platform.system() == "Windows":
        try:
            import winreg
            # BugFix: Context manager ensures native registry handle isn't permanently leaked
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu_str = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except Exception: pass
    elif platform.system() == "Darwin":
        try:
            # Overrides generic 'i386' readouts on M1/M2/M3 Apple Silicon Macs running x86_64 architecture translation Python bindings
            out = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], stderr=subprocess.DEVNULL)
            if out: cpu_str = out.decode().strip()
        except Exception: pass
    elif platform.system() == "Linux":
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if "model name" in line:
                        cpu_str = line.split(':')[-1].strip()
                        break
        except Exception: pass

    ram_str = f"{psutil.virtual_memory().total / (1024**3):.1f} GB"

    gpu_str = "N/A"
    try:
        if HAS_GPUTIL:
            gpus = GPUtil.getGPUs()
            if gpus: gpu_str = f"{gpus[0].name} ({gpus[0].memoryTotal / 1024:.1f}GB)"
        else:
            out = run_cmd(['nvidia-smi', '--query-gpu=gpu_name,memory.total', '--format=csv,noheader,nounits'], timeout=2, stderr_devnull=True)
            if out and out.strip():
                parts = out.strip().splitlines()[0].split(',')
                if len(parts) >= 2:
                    gpu_str = f"{parts[0].strip()} ({float(parts[1].strip())/1024:.1f}GB)"
    except Exception: pass

    return f"{os_str} | {cpu_str} | {ram_str} RAM | GPU: {gpu_str}"

def get_devices():
    out = run_cmd(['adb', 'devices'], timeout=5)
    devices = {}
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith('List') and not line.startswith('*'):
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ('device', 'offline', 'unauthorized', 'recovery', 'bootloader'):
                devices[parts[0]] = parts[1]
    return devices

_WIN_GPU_CACHE = {"time": 0, "val": "N/A", "fetching": False}
_MAC_GPU_CACHE = {"time": 0, "val": "N/A", "fetching": False}
_LINUX_INTEL_GPU_FAILED = False

def update_win_gpu():
    try:
        # BugFix: Added -NoProfile and -NonInteractive to drastically reduce background Host CPU parsing spikes
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"]
        out = run_cmd(cmd, timeout=3, stderr_devnull=True)
        if out and out.strip():
            try: _WIN_GPU_CACHE["val"] = f"{float(out.strip().replace(',', '.')):.1f}%"
            except ValueError: pass
    finally:
        _WIN_GPU_CACHE["fetching"] = False

def get_gpu_stats():
    global _LINUX_INTEL_GPU_FAILED
    os_name = platform.system()

    if HAS_GPUTIL:
        try:
            gpus = GPUtil.getGPUs()
            if gpus: return f"{gpus[0].load * 100:.1f}%", f"{gpus[0].memoryUsed:.1f}MB"
        except Exception: pass

    try:
        out = run_cmd(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'], timeout=2, stderr_devnull=True)
        if out:
            out_stripped = out.strip()
            if out_stripped:
                lines = out_stripped.splitlines()
                if lines:
                    parts = lines[0].split(',')
                    if len(parts) >= 2:
                        return f"{float(parts[0].strip()):.1f}%", f"{float(parts[1].strip()):.1f}MB"
    except Exception: pass

    if os_name == "Linux":
        try:
            if os.path.exists('/sys/class/drm/card0/device/gpu_busy_percent'):
                with open('/sys/class/drm/card0/device/gpu_busy_percent', 'r') as f:
                    return f"{f.read().strip()}%", "N/A"
        except Exception: pass

        if not _LINUX_INTEL_GPU_FAILED:
            out = run_cmd(['intel_gpu_top', '-s', '1', '-n', '1'], timeout=2, stderr_devnull=True)
            if out and (m := re.search(r'Render/3D:\s+(\d+)%', RE_ANSI.sub('', out))):
                return f"{m.group(1)}%", "N/A"
            _LINUX_INTEL_GPU_FAILED = True

        return "N/A (Requires sudo or nvidia-smi)", "N/A"

    elif os_name == "Windows":
        if time.monotonic() - _WIN_GPU_CACHE["time"] > 5 and not _WIN_GPU_CACHE["fetching"]:
            _WIN_GPU_CACHE["fetching"] = True
            _WIN_GPU_CACHE["time"] = time.monotonic()
            threading.Thread(target=update_win_gpu, daemon=True).start()
        return _WIN_GPU_CACHE["val"], "N/A"

    elif os_name == "Darwin":
        if time.monotonic() - _MAC_GPU_CACHE["time"] > 10 and not _MAC_GPU_CACHE["fetching"]:
            def fetch_mac_gpu():
                try:
                    out = run_cmd(['powermetrics', '-i', '500', '-n', '1', '--samplers', 'gpu_power'], timeout=2, stderr_devnull=True)
                    if out and (m := re.search(r'GPU (?:HW )?active residency:\s+([\d.]+)%', out)):
                         _MAC_GPU_CACHE["val"] = f"{m.group(1)}%"
                except Exception: pass
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
        out = run_cmd(['nvidia-smi', 'pmon', '-c', '1'], timeout=2, stderr_devnull=True)
        if out:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 7 and parts[1].isdigit():
                    try:
                        pid, sm_val = int(parts[1]), float(parts[3]) if parts[3] not in ('-', '?') else 0.0
                        raw_stats[pid] = raw_stats.get(pid, 0.0) + sm_val
                    except ValueError: pass
        _EMU_GPU_CACHE["val"] = raw_stats
    except Exception: pass
    finally: _EMU_GPU_CACHE["fetching"] = False

def get_emu_gpu_stats(qemu_pids):
    if not qemu_pids: return {}
    if time.monotonic() - _EMU_GPU_CACHE["time"] > 2.0 and not _EMU_GPU_CACHE["fetching"]:
        _EMU_GPU_CACHE["fetching"] = True
        _EMU_GPU_CACHE["time"] = time.monotonic()
        threading.Thread(target=update_emu_gpu, daemon=True).start()

    raw_stats = {}
    cached = _EMU_GPU_CACHE["val"]
    for serial, e_pid in qemu_pids.items():
        if e_pid in cached: raw_stats[serial] = cached[e_pid]
    return {k: f"{v:.1f}%" for k, v in raw_stats.items()}


_PID_PORT_CACHE = {}
_PROCESS_CACHE = {}

def get_process_stats():
    global _PID_PORT_CACHE, _PROCESS_CACHE
    current_pids = set()
    qemu_stats, unmapped_qemu, netsim, netsim_ram, qemu_pids, qemu_avds = {}, [], [], [], {}, {}

    for proc in psutil.process_iter(['name', 'pid', 'create_time']):
        try:
            info = proc.info or {}
            name = (info.get('name') or "").lower()
            pid = info.get('pid')
            ctime = info.get('create_time') or 0.0
            cache_key = (pid, ctime)

            # BugFix: Explicitly exclude 'crash' to prevent emulator-crash-service from populating unmapped_qemu endlessly
            if any(x in name for x in ('qemu', 'emulator', 'crosvm', 'netsim')) and not any(x in name for x in ('terminal', 'crash')):
                current_pids.add(cache_key)

                is_new = False
                if cache_key not in _PROCESS_CACHE:
                    _PROCESS_CACHE[cache_key] = proc
                    is_new = True
                    try: proc.cpu_percent(interval=None)
                    except Exception: pass

                cached_proc = _PROCESS_CACHE[cache_key]

                # Group measurements to retrieve all OS process states atomically in a single kernel read
                try:
                    with cached_proc.oneshot():
                        try: cpu_pct = 0.0 if is_new else cached_proc.cpu_percent(interval=None)
                        except Exception: cpu_pct = 0.0

                        try: mem_info = cached_proc.memory_info()
                        except Exception: mem_info = None

                        try: threads = cached_proc.num_threads()
                        except Exception: threads = 0

                        try:
                            ctx = cached_proc.num_ctx_switches()
                            ctx_total = ctx.voluntary + ctx.involuntary
                        except Exception: ctx_total = 0
                except Exception:
                    cpu_pct, mem_info, threads, ctx_total = 0.0, None, 0, 0


                if 'netsim' in name:
                    netsim.append(cpu_pct)
                    if mem_info: netsim_ram.append(mem_info.rss)
                    continue

                cache_val = _PID_PORT_CACHE.get(cache_key)
                port = None
                avd_name = None

                if cache_val == 'unmapped': pass
                elif isinstance(cache_val, tuple): port, avd_name = cache_val
                elif isinstance(cache_val, int): port = cache_val
                else:
                    first_attempt = cache_val if isinstance(cache_val, float) else time.monotonic()
                    try:
                        cmdline = cached_proc.cmdline()
                        for i, arg in enumerate(cmdline):
                            if arg in ('-port', '-ports') and i + 1 < len(cmdline):
                                if (val := cmdline[i+1].split(',')[0]).isdigit():
                                    port = int(val)
                            elif arg == '-avd' and i + 1 < len(cmdline):
                                avd_name = cmdline[i+1]
                            elif arg.startswith('@'):
                                avd_name = arg[1:]
                    except Exception: pass

                    if not port:
                        try:
                            for conn in cached_proc.net_connections(kind='tcp4'):
                                if conn.status == 'LISTEN' and 5554 <= conn.laddr.port <= 5584 and conn.laddr.port % 2 == 0:
                                    port = conn.laddr.port
                                    break
                        except Exception: pass

                    if port: _PID_PORT_CACHE[cache_key] = (port, avd_name)
                    elif time.monotonic() - first_attempt > 60: _PID_PORT_CACHE[cache_key] = 'unmapped'
                    else: _PID_PORT_CACHE[cache_key] = first_attempt

                cpu_str = f"{cpu_pct:.1f}%"
                ram_str = f"{(mem_info.rss if mem_info else 0) / (1024**2):.1f}MB ({threads} thr, {ctx_total} cs)"

                if port:
                    serial = f"emulator-{port}"
                    qemu_stats[serial] = (cpu_str, ram_str)
                    qemu_pids[serial] = pid
                    if avd_name: qemu_avds[serial] = avd_name
                else:
                    unmapped_qemu.append((pid, cpu_str, ram_str))

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess): pass

    _PID_PORT_CACHE = {k: v for k, v in _PID_PORT_CACHE.items() if k in current_pids}
    _PROCESS_CACHE = {k: v for k, v in _PROCESS_CACHE.items() if k in current_pids}

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

    qemu_stats, unmapped_qemu, netsim_str, netsim_ram_str, qemu_pids, qemu_avds = get_process_stats()

    pgin, pgout = 0, 0
    if os_name == "Darwin":
        for line in run_cmd(['vm_stat'], timeout=2).splitlines():
            if ("Pages paged in:" in line or "Pageins:" in line) and (m := RE_FLOAT.search(line)): pgin = int(float(m.group(1)))
            elif ("Pages paged out:" in line or "Pageouts:" in line) and (m := RE_FLOAT.search(line)): pgout = int(float(m.group(1)))
    elif os_name == "Linux":
        try:
            with open('/proc/vmstat', 'r') as f: content = f.read()
            if m_in := re.search(r'pgpgin\s+(\d+)', content): pgin = int(m_in.group(1))
            if m_out := re.search(r'pgpgout\s+(\d+)', content): pgout = int(m_out.group(1))
        except Exception: pass

    gpu_load, gpu_ram = get_gpu_stats()
    emu_gpu_stats = get_emu_gpu_stats(qemu_pids)

    temp_str = "N/A"
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                all_t = [entry.current for entries in temps.values() for entry in entries if entry.current is not None]
                if all_t: temp_str = f"{max(all_t):.1f}°C"
    except Exception: pass

    if temp_str == "N/A" and os_name == "Linux":
        try:
            zones = glob.glob('/sys/class/thermal/thermal_zone*/temp')
            t_vals = []
            for z in zones:
                try:
                    with open(z, 'r') as f:
                        val = float(f.read().strip()) / 1000.0
                        if val < 150.0: t_vals.append(val)
                except Exception: pass
            if t_vals: temp_str = f"{max(t_vals):.1f}°C"
        except Exception: pass

    batt_str = "N/A"
    try:
        if hasattr(psutil, "sensors_battery"):
            batt = psutil.sensors_battery()
            if batt:
                status = "Plugged" if batt.power_plugged else "Discharging"
                try:
                    freq = psutil.cpu_freq()
                    freq_str = f"@{int(freq.current)}MHz" if freq and hasattr(freq, 'current') and freq.current else ""
                except Exception: freq_str = ""
                batt_str = f"{batt.percent:.1f}% ({status}) {freq_str}".strip()
    except Exception: pass

    curr_time = time.monotonic()
    elapsed = max(0.001, curr_time - _LAST_DISK_TIME)
    curr_disk = psutil.disk_io_counters()

    read_mb, write_mb = 0.0, 0.0
    if _LAST_DISK_IO and curr_disk:
        read_mb = max(0.0, (curr_disk.read_bytes - _LAST_DISK_IO.read_bytes) / (1024**2)) / elapsed
        write_mb = max(0.0, (curr_disk.write_bytes - _LAST_DISK_IO.write_bytes) / (1024**2)) / elapsed

    _LAST_DISK_IO = curr_disk
    _LAST_DISK_TIME = curr_time

    disk_str = f"Read: {read_mb:.1f} MB/s | Write: {write_mb:.1f} MB/s"

    return host_total, qemu_stats, netsim_str, host_ram, host_swap, pgin, pgout, unmapped_qemu, netsim_ram_str, qemu_pids, gpu_load, gpu_ram, emu_gpu_stats, temp_str, disk_str, read_mb, write_mb, batt_str, qemu_avds

def get_guest_stats(serial, known_model, is_booting, status):
    if status != 'device':
        return known_model if known_model else f"Unknown ({status})", True, f"N/A ({status})", f"N/A ({status})", f"N/A ({status})", f"N/A ({status})"

    model = known_model
    if not model or "android" not in model.lower() or "unknown model" in model.lower() or "error:" in model.lower() or "offline" in model.lower() or ("Res: N/A" in model and is_booting):
        script = 'm=$(getprop ro.product.model); v=$(getprop ro.build.version.release); a=$(getprop ro.build.version.sdk); b=$(getprop ro.build.id); w=$(wm size 2>/dev/null); c=$(getprop sys.boot_completed); echo "$m|!|$v|!|$a|!|$b|!|$w|!|$c"'
        m_out = run_cmd(['adb', '-s', serial, 'shell', script], timeout=2).strip()
        if m_out and "error:" not in m_out.lower() and "offline" not in m_out.lower():
            parts = m_out.split('|!|')
            if len(parts) >= 6:
                res = parts[4].lower().split('size:')[-1].strip() if "size:" in parts[4].lower() else "N/A"
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

    if out and not any(err in out.lower() for err in ["no devices", "offline", "unauthorized"]):
        try:
            parts = out.split('===ADB_MON_SPLIT===')
            mem_total, mem_avail, used, err_count = 0, 0, 0, 0
            data_space = "N/A"

            if len(parts) >= 1:
                mem = {}
                for line in parts[0].splitlines():
                    lp = line.split()
                    if len(lp) >= 2 and lp[1].isdigit(): mem[lp[0].replace(':', '')] = int(lp[1])
                mem_total = mem.get('MemTotal', 0)
                mem_avail = mem.get('MemAvailable', 0) or (mem.get('MemFree', 0) + mem.get('Buffers', 0) + mem.get('Cached', 0))
                if mem_total > 0: used = mem_total - mem_avail

            if len(parts) >= 2:
                for line in parts[1].splitlines():
                    # Fallback to simpler check to support mounts like /data/user/0
                    if '/data' in line:
                        dp = line.split()
                        if len(dp) >= 4:
                            pct_idx = next((i for i, x in enumerate(dp) if '%' in x), -1)
                            if pct_idx >= 2:
                                used_s, avail_s, pct = dp[pct_idx-2], dp[pct_idx-1], dp[pct_idx]
                            else:
                                used_s, avail_s, pct = dp[2] if len(dp) > 2 else "N/A", dp[3] if len(dp) > 3 else "N/A", "N/A%"
                            data_space = f"{pct} ({used_s} used/{avail_s} avail)"
                            break

            if len(parts) >= 3:
                err_str = parts[2].strip()
                if err_str.isdigit(): err_count = int(err_str)

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
        except Exception: pass

    guest_diff, guest_real = "N/A", "N/A"
    out = run_cmd(['adb', '-s', serial, 'shell', 'top', '-b', '-n', '2', '-d', '0.2'], timeout=2)
    if not out or "not found" in out.lower() or "usage" in out.lower():
        out = run_cmd(['adb', '-s', serial, 'shell', 'top', '-n', '2', '-d', '1'], timeout=3)

    if out:
        out = RE_ANSI.sub('', out)
        target_block = out.split('Tasks:')[-1] if 'Tasks:' in out else (out.split('User ')[-1] if 'User ' in out else out)
        for line in target_block.splitlines():
            line = line.strip()

            t_match = RE_GUEST_CPU_USAGE.search(line)
            i_match = RE_GUEST_CPU_IDLE.search(line)
            if t_match and i_match:
                t_val, i_val = int(t_match.group(1)), int(i_match.group(1))
                if t_val >= 90:
                    diff, cores = max(0, t_val - i_val), max(1, round(t_val / 100.0))
                    return model, is_booting, f"{diff}%", f"{diff / cores:.1f}% (over {cores} cores)", guest_ram, guest_swap


            elif (idle_match := RE_GUEST_CPU_ID.search(line)):
                real_pct = max(0.0, 100.0 - float(idle_match.group(1).replace(',', '.')))
                return model, is_booting, f"{real_pct:.1f}%", f"{real_pct:.1f}% (Cores unknown)", guest_ram, guest_swap

    out = run_cmd(['adb', '-s', serial, 'shell', 'dumpsys', 'cpuinfo'], timeout=2)
    if out:
        for line in out.splitlines():
            if match := RE_GUEST_DUMPSYS_TOT.search(line):
                return model, is_booting, f"{float(match.group(1)):.1f}%", f"{float(match.group(1)):.1f}% (Cores unknown)", guest_ram, guest_swap

    return model, is_booting, guest_diff, guest_real, guest_ram, guest_swap

def get_val(s, for_ts=False):
    """Safely extracts payload. Returns NaNs for timeseries data gaps preventing average/P95 zero skew."""
    if isinstance(s, (int, float)): return float(s)
    if for_ts and any(x in str(s) for x in ("N/A", "Unsupported", "Calculating", "No ADB", "Error", "Timeout", "Hang")):
        return float('nan')
    try:
        s_str = str(s)
        pcts = RE_PCT.findall(s_str)
        if pcts: return float(pcts[0])

        if any(x in s_str for x in ["pages/s", "Out:", "KB/s"]):
            matches = RE_FLOAT.findall(s_str)
            return max(map(float, matches)) if matches else 0.0

        m = RE_FLOAT.search(s_str)
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return float('nan') if for_ts else 0.0

def print_dashboard(os_name, host_total, qemu_stats, netsim_cpu, guest_results, host_ram, host_swap, paging_str, iter_count, term_width, force_clear, unmapped_qemu, netsim_ram, gpu_load, gpu_ram, emu_gpu_stats, temp_str="N/A", disk_str="N/A", is_once=False, batt_str="N/A", hw_specs="N/A"):
    buf = []
    def display(text): buf.append(text)

    if not is_once:
        if force_clear: display('\033[2J\033[H')
        else: display('\033[H\033[J')

    width = max(76, min(120, term_width - 1))
    bar_width, val_width = 20, width - 60
    c1, c2 = Colors.BLUE + Colors.BOLD, Colors.RESET

    display(f"{c1}┌{'─' * (width - 2)}┐{c2}\033[K")
    logo = f"{Colors.BLUE}G{Colors.RED}o{Colors.YELLOW}o{Colors.BLUE}g{Colors.GREEN}l{Colors.RED}e{Colors.RESET}"
    title_text = "Android Emulator & Device Resource Monitor"
    line_content = f" {logo}   {title_text}"
    visible_len = len(RE_ANSI.sub('', line_content))
    padding = max(0, width - 2 - visible_len)
    display(f"{c1}│{c2}{Colors.BOLD}{line_content}{' ' * padding}{c2}{c1}│{c2}\033[K")
    display(f"{c1}├{'─' * (width - 2)}┤{c2}\033[K")

    def print_section(title, right_badge="", color=Colors.HEADER):
        inside_text = f" {title}"
        v_title_len = len(RE_ANSI.sub('', inside_text))
        v_badge_len = len(RE_ANSI.sub('', right_badge))
        if v_title_len + v_badge_len > width - 4:
            inside_text = inside_text[:max(0, (width - 4) - v_badge_len - 1)] + "…"
            v_title_len = len(RE_ANSI.sub('', inside_text))

        padding = max(0, (width - 2) - (v_title_len + v_badge_len))
        display(f"{c1}│{c2}{color}{Colors.BOLD}{inside_text}{right_badge}{' ' * padding}{c2}{c1}│{c2}\033[K")

    def get_color(val_str, label):
        try:
            val = get_val(val_str)

            if any(x in val_str for x in ["Unsupported", "No ADB", "Error", "Timeout", "Hang"]) or "Calculating" in val_str:
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

            if any(x in label for x in ["Total Device CPU", "Host QEMU CPU", "Unmapped Emulator CPU", "Network Simulator"]):
                return Colors.RED + Colors.BOLD if val > 750 else Colors.RED if val > 350 else Colors.YELLOW if val > 150 else Colors.GREEN

            # Standard percent-based threshold
            if val > 90: return Colors.RED + Colors.BOLD
            elif val > 70: return Colors.RED
            elif val > 40: return Colors.YELLOW
            else: return Colors.GREEN
        except Exception:
            return Colors.CYAN



    def print_row(label, value):
        c_val = str(value)
        color = get_color(c_val, label)
        is_alert = False
        try:
            val = get_val(c_val)
            if "Battery" in label:
                if val <= 10: is_alert = True
            elif "°C" in c_val:
                if val > 85: is_alert = True
            elif "%" in c_val:
                threshold = 750 if any(x in label for x in ["Total Device CPU", "Host QEMU CPU", "Unmapped Emulator CPU", "Network Simulator"]) else 90
                if val > threshold:
                    is_alert = True
                    color = Colors.RED + Colors.BOLD
        except Exception: pass

        try:
            raw_val = get_val(c_val)
            clamped_val = min(100.0, max(0.0, raw_val))
            filled = int((clamped_val / 100.0) * bar_width)
            half = 1 if ((clamped_val / 100.0) * bar_width - filled) >= 0.5 else 0
            bar = f"[{'█' * filled}{'▌' * half}{' ' * max(0, (bar_width - filled - half))}]"
        except Exception:
            bar = f"[{' ' * bar_width}]"

        is_placeholder = any(x in c_val for x in ["Unsupported", "No ADB", "Error", "Timeout", "Hang"]) or "Calculating" in c_val
        try:
            if math.isnan(raw_val) or (raw_val == 0.0 and "N/A" in c_val and "%" not in c_val):
                is_placeholder = True
        except Exception: pass

        r_c, l_c = (Colors.CYAN, Colors.BOLD) if is_placeholder else (color, Colors.BOLD)

        # Override host visual labels to Blue strictly if threshold warnings aren't already actively triggering
        if "Host QEMU" in label and not is_alert and not is_placeholder: r_c = Colors.BLUE

        # Ensure we strip ANSI before calculating offsets
        c_val_clean = RE_ANSI.sub('', c_val)
        c_val_clean_len = len(c_val_clean)

        if "%" in c_val or "°C" in c_val:
            if is_alert:
                cap = max(0, val_width - 6)
                c_val_display = c_val_clean[:cap] + "…" if c_val_clean_len > cap else c_val_clean
                c_val_display += " [!!]"
            else:
                c_val_display = c_val_clean[:max(0, val_width - 1)] + "…" if c_val_clean_len > val_width else c_val_clean
            display(f"{c1}│{c2} {l_c}{label:<30}{c2} {c1}│{c2} {r_c}{bar} {c_val_display:<{val_width}}{c2} {c1}│{c2}\033[K")
        else:
            full_w = val_width + bar_width + 3
            c_val_display = c_val_clean[:max(0, full_w - 1)] + "…" if c_val_clean_len > full_w else c_val_clean
            display(f"{c1}│{c2} {l_c}{label:<30}{c2} {c1}│{c2} {r_c}{c_val_display:<{full_w}}{c2} {c1}│{c2}\033[K")


    display_os = "macOS" if os_name == "Darwin" else os_name
    if hw_specs != "N/A":
        specs_text = f" Host Specs: {hw_specs}"
        visible_specs_len = len(RE_ANSI.sub('', specs_text))
        if visible_specs_len > width - 4:
            specs_text = specs_text[:max(0, width - 5)] + "…"
            visible_specs_len = len(RE_ANSI.sub('', specs_text))

        padding = max(0, width - 2 - visible_specs_len)
        display(f"{c1}│{c2}{Colors.DIM}{specs_text}{' ' * padding}{c2}{c1}│{c2}\033[K")
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
    if batt_str != "N/A": print_row("Laptop Battery Status", batt_str)

    if unmapped_qemu:
        sum_cpu = sum(get_val(c) for p, c, r in unmapped_qemu)
        sum_ram = sum(get_val(r) for p, c, r in unmapped_qemu)
        pids = [str(p) for p, c, r in unmapped_qemu]
        pid_str = ", ".join(pids[:4]) + (f" ... (+{len(pids)-4} more)" if len(pids) > 4 else "")

        print_row("Unmapped Emulator CPU", f"{sum_cpu:.1f}% (PIDs: {pid_str})")
        print_row("Unmapped Emulator RAM", f"{sum_ram:.1f}MB (PIDs: {pid_str})")


    print_row("Network Simulator CPU", netsim_cpu)
    print_row("Network Simulator RAM", netsim_ram)

    if not guest_results:
        display(f"{c1}├{'─' * 32}┴{'─' * (width - 35)}┤{c2}\033[K")
        print_section("GUEST SYSTEM (ADB)", color=Colors.GREEN)
        display(f"{c1}├{'─' * 32}┬{'─' * (width - 35)}┤{c2}\033[K")
        print_row("Guest CPU", "N/A (No devices connected)")
        print_row("Guest RAM Usage", "N/A")
        print_row("Guest Swap Memory", "N/A")
    else:
        for serial, (disp_name, tags, g_diff, g_real, g_ram, g_swap) in guest_results.items():
            display(f"{c1}├{'─' * 32}┴{'─' * (width - 35)}┤{c2}\033[K")
            clean_name = re.sub(r" \| Android [^|]+ \(API ([^|]+)\) \| Build: ([^|]+)", r" | API \1 | \2", disp_name)
            print_section(f"GUEST SYSTEM ({clean_name})", right_badge=tags, color=Colors.GREEN)
            display(f"{c1}├{'─' * 32}┬{'─' * (width - 35)}┤{c2}\033[K")
            print_row("Total Device CPU Usage", g_diff)
            print_row("Adjusted CPU Load", g_real)
            m_ram = re.search(r"(\d+\.\d+%) \(([^,]+), (\d+ errs), (.*?) data\)", g_ram)
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

    display(f"{c1}└{'─' * 32}┴{'─' * (width - 35)}┘{c2}\033[K")
    msg = f"Last Updated: {time.strftime('%H:%M:%S')}" if is_once else f"Press Ctrl+C to exit. Last Updated: {time.strftime('%H:%M:%S')} | Refreshing{('.' * (iter_count % 4)):<3}"
    display(f"\n{Colors.YELLOW}{msg}{Colors.RESET}\033[K")

    if not is_once: display('\033[J')
    sys.stdout.write('\n'.join(buf) + '\n')
    sys.stdout.flush()


def save_summary(summary_data, end_time, save_csv=False, save_txt=True, out_dir='.', is_autosave=False):
    if summary_data.get('iterations', 0) == 0 and (not save_csv or not summary_data.get('time_series', [])): return

    duration = max(0.1, end_time - summary_data['start_time'])
    ts_data = list(summary_data.get('time_series', []))

    def get_p95(col_name):
        if not ts_data: return 0.0
        vals = [r[col_name] for r in ts_data if col_name in r and r[col_name] is not None and not math.isnan(r[col_name])]
        if not vals: return 0.0
        vals.sort()
        return vals[int(len(vals) * 0.95)]

    def get_avg(col_name):
        if not ts_data: return 0.0
        vals = [r[col_name] for r in ts_data if col_name in r and r[col_name] is not None and not math.isnan(r[col_name])]
        return sum(vals)/len(vals) if vals else 0.0

    def get_max(col_name):
        if not ts_data: return 0.0
        vals = [r[col_name] for r in ts_data if col_name in r and r[col_name] is not None and not math.isnan(r[col_name])]
        return max(vals) if vals else 0.0

    avg_h_cpu = summary_data['host_cpu_sum'] / max(1, summary_data['host_cpu_iterations'])
    avg_h_ram = summary_data['host_ram_sum'] / max(1, summary_data['host_ram_iterations'])
    avg_n_cpu = summary_data['netsim_cpu_sum'] / max(1, summary_data['netsim_cpu_iterations'])
    avg_n_ram = summary_data.get('netsim_ram_sum', 0.0) / max(1, summary_data.get('netsim_ram_iterations', 0))

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
        if lines: adb_ver = lines[0].strip()


    is_vm = False
    try:
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                if 'hypervisor' in f.read(): is_vm = True
    except Exception: pass

    lines = [
        "Android Emulator & Device Resource Monitor Session Summary", "=" * 60,
        f"Start Time:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(summary_data['start_time']))}",
        f"End Time:    {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}",
        f"Duration:    {duration:.1f} seconds", f"Total Loops: {summary_data['iterations']}",
        f"Host OS:      {os_info}",
        f"Host Specs:   {summary_data.get('hardware_specs', 'N/A')}",
        f"Host Cores:   {cpu_cores}",
        f"Host Virtual: {is_vm}",
        f"ADB Version:  {adb_ver}\n",
        "HOST SYSTEM:",
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
            "NETSIM:",
            f"  Average CPU Usage: {avg_n_cpu:.1f}%",
            f"  Peak CPU Usage:    {summary_data['netsim_cpu_max']:.1f}%",
            f"  P95 CPU Usage:     {p95_n_cpu:.1f}%",
            f"  Average RAM Usage: {avg_n_ram:.1f}MB",
            f"  Peak RAM Usage:    {summary_data.get('netsim_ram_max', 0.0):.1f}MB",
            f"  P95 RAM Usage:     {p95_n_ram:.1f}MB\n"
        ])

    for serial, e_data in summary_data['emulators'].items():
        avg_g_cpu = e_data['cpu_sum'] / max(1, e_data['cpu_iterations'])
        avg_g_ram = e_data['ram_sum'] / max(1, e_data['ram_iterations'])
        avg_g_swap = e_data.get('swap_sum', 0.0) / max(1, e_data.get('swap_iterations', 1))

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
            avg_q_cpu = e_data['qemu_cpu_sum'] / max(1, e_data['qemu_cpu_iterations'])
            avg_q_ram = e_data['qemu_ram_sum'] / max(1, e_data['qemu_ram_iterations'])
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
            lines.extend([f"    {t} | CPU: {c:<15} | RAM: {r} | Swap: {sw}" if len(entry) >= 4 else f"    {entry[0]} | CPU: {entry[1]:<15} | RAM: {entry[2]}" for entry in e_data['boot_log'] for t, c, r, sw in ([entry if len(entry) == 4 else entry + ("N/A",)],)])
        lines.append("")

    if len(ts_data) > 1:
        try:
            import plotille
            times = [r['time'] - summary_data['start_time_monotonic'] for r in ts_data]

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
                        idx = min(int((t - min_t) / span_t * (w_dots - 1)), w_dots - 1)
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
                res.extend([RE_ANSI.sub('', line) for line in fig.show().splitlines()])
                res.append("")
                return res

            def extract(key): return [r.get(key, float('nan')) for r in ts_data]


            h_cpu = extract('host_cpu')
            if any(v > 0 for v in h_cpu if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_cpu, "HOST CPU HISTORY CHART:", "Time (s)", "Usage (%)"))

            h_ram = extract('host_ram')
            if any(v > 0 for v in h_ram if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_ram, "HOST RAM HISTORY CHART:", "Time (s)", "Usage (%)"))

            h_t = extract('host_thermal_c')
            if any(v > 0 for v in h_t if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_t, "HOST THERMAL HISTORY CHART:", "Time (s)", "Temp (°C)"))

            h_swp = extract('host_swap')
            if any(v > 0 for v in h_swp if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_swp, "HOST SWAP HISTORY CHART:", "Time (s)", "Usage (%)"))

            h_gpu = extract('host_gpu')
            if any(v > 0 for v in h_gpu if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_gpu, "HOST GPU LOAD HISTORY CHART:", "Time (s)", "Usage (%)"))

            h_gpumem = extract('host_gpu_mem')
            if any(v > 0 for v in h_gpumem if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, h_gpumem, "HOST GPU MEMORY HISTORY CHART:", "Time (s)", "Usage/MB"))

            h_rd = extract('host_disk_read_mbs')
            lines.extend(make_chart(times, h_rd, "HOST DISK READ HISTORY CHART:", "Time (s)", "Read (MB/s)"))

            h_wr = extract('host_disk_write_mbs')
            lines.extend(make_chart(times, h_wr, "HOST DISK WRITE HISTORY CHART:", "Time (s)", "Write (MB/s)"))

            n_cpu = extract('netsim_cpu')
            if any(v > 0 for v in n_cpu if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, n_cpu, "NETSIM CPU HISTORY CHART:", "Time (s)", "Usage (%)"))

            n_ram = extract('netsim_ram')
            if any(v > 0 for v in n_ram if v is not None and not math.isnan(v)):
                lines.extend(make_chart(times, n_ram, "NETSIM RAM HISTORY CHART:", "Time (s)", "MB"))

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
                if any(v > 0 for v in e_cpu if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, e_cpu, f"GUEST CPU HISTORY ({serial}):", "Time (s)", "Usage (%)"))

                e_ram = extract(f'emu_ram_{serial}')
                if any(v > 0 for v in e_ram if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, e_ram, f"GUEST RAM HISTORY ({serial}):", "Time (s)", "Usage (%)"))

                q_cpu = extract(f'qemu_cpu_{serial}')
                if any(v > 0 for v in q_cpu if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, q_cpu, f"HOST QEMU CPU HISTORY ({serial}):", "Time (s)", "Usage (%)"))

                q_ram = extract(f'qemu_ram_{serial}')
                if any(v > 0 for v in q_ram if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, q_ram, f"HOST QEMU RAM HISTORY ({serial}):", "Time (s)", "MB"))

                e_disk = extract(f'emu_disk_pct_{serial}')
                if any(v > 0 for v in e_disk if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, e_disk, f"GUEST DATA PARTITION FULLNESS ({serial}):", "Time (s)", "Usage (%)"))

                e_swap = extract(f'emu_swap_{serial}')
                if any(v > 0 for v in e_swap if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, e_swap, f"GUEST SWAP MEMORY HISTORY ({serial}):", "Time (s)", "MB"))

                e_errs = extract(f'emu_errs_{serial}')
                if any(v > 0 for v in e_errs if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, e_errs, f"GUEST LOGCAT ERRORS ({serial}):", "Time (s)", "Errors"))

                q_cs = extract(f'qemu_cs_{serial}')
                if any(v > 0 for v in q_cs if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, q_cs, f"QEMU CONTEXT SWITCHES ({serial}):", "Time (s)", "Switches"))

                q_gpu = extract(f'qemu_gpu_{serial}')
                if any(v > 0 for v in q_gpu if v is not None and not math.isnan(v)):
                    lines.extend(make_chart(times, q_gpu, f"HOST QEMU GPU LOAD HISTORY ({serial}):", "Time (s)", "Usage (%)"))
        except ImportError: pass


        except Exception: pass

    out_dir_expanded = os.path.expanduser(out_dir)
    try: os.makedirs(out_dir_expanded, exist_ok=True)
    except Exception: pass

    file_ts = time.strftime('%Y%m%d_%H%M%S')

    if save_txt:
        filename = os.path.join(out_dir_expanded, "device_monitor_summary_autosave.txt" if is_autosave else f"device_monitor_summary_{file_ts}.txt")
        try:
            with open(filename, 'w', encoding='utf-8') as f: f.write('\n'.join(lines))
            if not is_autosave:
                print(f"\n{Colors.GREEN}Session summary saved to {Colors.BOLD}{pathlib.Path(os.path.abspath(filename)).as_uri()}{Colors.RESET}")
        except Exception as e: print(f"\n{Colors.RED}Failed to save summary file: {e}{Colors.RESET}")

    if not is_autosave:
        # Print summary to console as requested by user
        print("\n" + "=" * 60)
        print("SESSION SUMMARY (Console Output)")
        print("=" * 60)
        print('\n'.join(lines))
        print("=" * 60 + "\n")

    if save_csv and ts_data:
        csv_filename = os.path.join(out_dir_expanded, "device_monitor_summary_autosave.csv" if is_autosave else f"device_monitor_summary_{file_ts}.csv")

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
                    epoch_time = summary_data['start_time'] + (row['time'] - summary_data['start_time_monotonic'])
                    row_copy['time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch_time))

                    for k, v in row_copy.items():
                        if isinstance(v, float) and math.isnan(v):
                            row_copy[k] = ""
                    writer.writerow(row_copy)

            print(f"{Colors.GREEN}CSV exported to {Colors.BOLD}{pathlib.Path(os.path.abspath(csv_filename)).as_uri()}{Colors.RESET}")
        except Exception as e: print(f"\n{Colors.RED}Failed to export CSV file: {e}{Colors.RESET}")

    if not is_autosave:
        for cleanup_file in ["device_monitor_summary_autosave.txt", "device_monitor_summary_autosave.csv"]:
            target = os.path.join(out_dir_expanded, cleanup_file)
            try:
                if os.path.exists(target):
                    os.remove(target)
            except Exception:
                pass


def handle_sigterm(signum, frame):
    raise KeyboardInterrupt()

def update_device_cache(device_names, disconnect_times, serial_pids, connected_serials, qemu_pids, current_time, timeout=5.0):
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
                if s in serial_pids: del serial_pids[s]
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

    parser = argparse.ArgumentParser(description="Cross-platform Android Emulator & Device Resource Monitor.")

    parser.add_argument('-1', '--once', action='store_true', help="Print metrics once and exit")
    parser.add_argument('--no-summary', action='store_true', help="Disable saving the session summary file")
    parser.add_argument('--csv', action='store_true', help="Export time-series data to CSV on exit")
    parser.add_argument('-o', '--out-dir', default='.', help="Directory to save session summaries and CSV files")
    parser.add_argument('-i', '--interval', type=float, default=2.0, help="Refresh rate interval in seconds (1.0 to 10.0)")
    parser.add_argument('--headless', action='store_true', help="Suppress terminal output completely (useful for automation/CI)")
    parser.add_argument('--auto-save-interval', type=int, default=5, help="Auto-save interval in minutes (use 0 to disable)")
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

    summary_data = {
        'start_time': time.time(), 'start_time_monotonic': time.monotonic(), 'iterations': 0, 'hardware_specs': hardware_specs,
        'host_cpu_iterations': 0, 'host_cpu_sum': 0.0, 'host_cpu_max': 0.0,
        'host_ram_iterations': 0, 'host_ram_sum': 0.0, 'host_ram_max': 0.0,
        'netsim_cpu_iterations': 0, 'netsim_cpu_sum': 0.0, 'netsim_cpu_max': 0.0,
        'netsim_ram_iterations': 0, 'netsim_ram_sum': 0.0, 'netsim_ram_max': 0.0,
        'emulators': {}, 'time_series': deque(maxlen=43200)
    }

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=40)
    psutil.cpu_percent(interval=None)
    timeout_bound = args.interval + 1.0

    # State Dictionaries securely resolve the critical Thread Pool Starvation bugs
    active_tasks = {'host': None, 'devices': None, 'guests': {}}
    last_devices = {}
    last_host_res = ("Calculating...", {}, "N/A", "N/A", "N/A", 0, 0, [], "N/A", {}, "N/A", "N/A", {}, "N/A", "N/A", 0.0, 0.0, "N/A", {})
    last_guest_res = {}

    try:
        while True:
            loop_start = time.monotonic()

            # Asynchronously deduce host-level states strictly separated from main rendering thread
            if active_tasks['host'] is None or active_tasks['host'].done():
                active_tasks['host'] = executor.submit(get_host_stats)

            # Poll active devices attached to host asynchronously
            if active_tasks['devices'] is None or active_tasks['devices'].done():
                if active_tasks['devices'] is not None:
                    try:
                        last_devices = active_tasks['devices'].result()
                    except Exception: pass
                active_tasks['devices'] = executor.submit(get_devices)

            devices = last_devices
            connected = set(devices.keys())

            for s, e_data in summary_data['emulators'].items():
                if e_data.get('current_serial', s) not in connected: e_data['last_status'] = 'offline'

            # Submits tasks cleanly while avoiding uncompleted background threads from compounding latency
            for s in devices:
                if s not in active_tasks['guests'] or active_tasks['guests'][s].done():
                    storage_key = next((k for k, v in summary_data.get('emulators', {}).items() if v.get('current_serial') == s), s)
                    is_booting = summary_data.get('emulators', {}).get(storage_key, {}).get('is_booting', False)
                    active_tasks['guests'][s] = executor.submit(get_guest_stats, s, device_names.get(s, ""), is_booting, devices[s])

            futures_to_wait = [active_tasks['host']] + [active_tasks['guests'][s] for s in devices if s in active_tasks['guests']]

            # Ensures absolute UI refresh pacing natively without abandoning lingering threads
            wait_timeout = max(0.1, args.interval - (time.monotonic() - loop_start))
            concurrent.futures.wait(futures_to_wait, timeout=wait_timeout)

            if active_tasks['host'].done():
                try:
                    last_host_res = active_tasks['host'].result()
                except Exception as e:
                    last_host_res = (f"Error: {e}", {}, "N/A", "N/A", "N/A", 0, 0, [], "N/A", {}, "N/A", "N/A", {}, "N/A", "N/A", 0.0, 0.0, "N/A", {})
                host_res = last_host_res
            else:
                ht = last_host_res[0]
                if "Timeout" not in ht and "Calculating" not in ht: ht += " (Timeout)"
                host_res = (ht,) + last_host_res[1:]

            qemu_pids = host_res[9] if len(host_res) > 9 else {}
            update_device_cache(device_names, disconnect_times, serial_pids, connected, qemu_pids, time.monotonic())

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
                        model, is_booting, g_diff, g_real, g_ram, g_swap = f.result()
                        last_guest_res[serial] = (model, is_booting, g_diff, g_real, g_ram, g_swap)
                    except Exception as e:
                        storage_key = next((k for k, v in summary_data.get('emulators', {}).items() if v.get('current_serial') == serial), serial)
                        last_b = summary_data.get('emulators', {}).get(storage_key, {}).get('is_booting', False)
                        last_guest_res[serial] = (device_names.get(serial, "Unknown"), last_b, "N/A", f"Error: {e}", "N/A", "N/A")

                if serial in last_guest_res:
                    model, is_booting, g_diff, g_real, g_ram, g_swap = last_guest_res[serial]
                    if f and not f.done() and "Timeout" not in g_real and "Calculating" not in g_real:
                        g_real += " (Timeout)"
                else:
                    storage_key = next((k for k, v in summary_data.get('emulators', {}).items() if v.get('current_serial') == serial), serial)
                    last_b = summary_data.get('emulators', {}).get(storage_key, {}).get('is_booting', False)
                    model, is_booting, g_diff, g_real, g_ram, g_swap = device_names.get(serial, "Unknown"), last_b, "Calculating...", "Calculating...", "Calculating...", "Calculating..."

                current_metrics[serial] = (g_diff, g_real, g_ram, g_swap)
                if model: device_names[serial] = model

                avd_name = qemu_avds.get(serial)
                storage_key = f"{avd_name}_{serial}" if avd_name else serial

                if not avd_name:
                    existing_key = next((k for k, v in summary_data.get('emulators', {}).items() if v.get('current_serial') == serial), None)
                    if existing_key: storage_key = existing_key


                if avd_name and avd_name not in summary_data.get('emulators', {}) and serial in summary_data.get('emulators', {}):
                    summary_data['emulators'][avd_name] = summary_data['emulators'].pop(serial)
                    summary_data['emulators'][avd_name]['name'] = f"{avd_name} | {device_names.get(serial, 'Unknown Device')}"

                name = f"{serial} | {device_names.get(serial, 'Unknown Device')}"
                if avd_name:
                    name = f"{avd_name} | {device_names.get(serial, 'Unknown Device')}"

                if storage_key not in summary_data.get('emulators', {}):
                    summary_data['emulators'][storage_key] = {
                        'name': name, 'current_serial': serial, 'current_pid': host_res[9].get(serial) if len(host_res) > 9 else None,
                        'cpu_iterations': 0, 'cpu_sum': 0.0, 'cpu_max': 0.0,
                        'ram_iterations': 0, 'ram_sum': 0.0, 'ram_max': 0.0,
                        'swap_iterations': 0, 'swap_sum': 0.0, 'swap_max': 0.0,
                        'qemu_cpu_iterations': 0, 'qemu_cpu_sum': 0.0, 'qemu_cpu_max': 0.0,
                        'qemu_ram_iterations': 0, 'qemu_ram_sum': 0.0, 'qemu_ram_max': 0.0,
                        'first_seen': time.monotonic(), 'last_boot_log_time': 0, 'boot_log': [],
                        'is_booting': is_booting, 'track_boot_stats': is_booting or devices.get(serial) == 'offline',
                        'boot_completed': not is_booting and devices.get(serial) == 'device',
                        'last_status': 'offline', 'online_indicator_until': 0,
                        'last_seen': time.monotonic()
                    }
                else:
                    was_booting = summary_data['emulators'][storage_key].get('is_booting', False)
                    if was_booting and not is_booting:
                        summary_data['emulators'][storage_key]['boot_finished_time'] = time.monotonic()
                        summary_data['emulators'][storage_key]['boot_completed'] = True

                    old_pid = summary_data['emulators'][storage_key].get('current_pid')
                    new_pid = host_res[9].get(serial) if len(host_res) > 9 else None

                    if old_pid and new_pid and old_pid != new_pid:
                        summary_data['emulators'][storage_key]['boot_completed'] = False
                        summary_data['emulators'][storage_key]['track_boot_stats'] = is_booting or devices.get(serial) == 'offline'

                    summary_data['emulators'][storage_key]['is_booting'] = is_booting

                    if is_booting and not summary_data['emulators'][storage_key].get('boot_completed'):
                        summary_data['emulators'][storage_key]['track_boot_stats'] = True
                    summary_data['emulators'][storage_key]['last_seen'] = time.monotonic()
                    summary_data['emulators'][storage_key]['current_serial'] = serial
                    summary_data['emulators'][storage_key]['current_pid'] = host_res[9].get(serial) if len(host_res) > 9 else None

                e_data = summary_data['emulators'][storage_key]
                e_data['name'] = name

                if iter_count > 0:
                    # Dynamically capture Boot Phase Resource Profiling automatically
                    if e_data.get('track_boot_stats') and (time.monotonic() - e_data['first_seen'] < 900):
                        if not e_data.get('boot_completed') or ('boot_finished_time' in e_data and time.monotonic() - e_data['boot_finished_time'] < 300):
                            if time.monotonic() - e_data.get('last_boot_log_time', 0) >= 5.0 and not any(x in g_real for x in ("N/A", "Error", "Timeout", "Calculating")):
                                elapsed_boot = int(time.monotonic() - e_data['first_seen'])
                                e_data['boot_log'].append((f"+{elapsed_boot}s", g_real, g_ram, g_swap))
                                e_data['last_boot_log_time'] = time.monotonic()

                    if not any(x in g_real for x in ("N/A", "Error", "Timeout", "Hang", "Calculating")):
                        e_data['cpu_iterations'] += 1
                        g_c = get_val(g_real)
                        e_data['cpu_sum'] += g_c
                        e_data['cpu_max'] = max(e_data['cpu_max'], g_c)
                    if not any(x in g_ram for x in ("N/A", "Error", "Timeout", "Hang", "Calculating")):
                        e_data['ram_iterations'] += 1
                        g_r = get_val(g_ram)
                        e_data['ram_sum'] += g_r
                        e_data['ram_max'] = max(e_data['ram_max'], g_r)
                    if not any(x in g_swap for x in ("N/A", "Error", "Timeout", "Hang", "Calculating")):
                        e_data['swap_iterations'] += 1
                        g_sw = get_val(g_swap)
                        e_data['swap_sum'] += g_sw
                        e_data['swap_max'] = max(e_data.get('swap_max', 0.0), g_sw)

            host_total, qemu_stats, netsim_cpu, host_ram, host_swap, pgin, pgout, unmapped_qemu, netsim_ram, qemu_pids, gpu_load, gpu_ram, emu_gpu_stats, temp_str, disk_str, read_mb, write_mb, batt_str, qemu_avds = host_res

            for storage_key, e_data in summary_data['emulators'].items():
                name = e_data['name']
                current_serial = e_data.get('current_serial', storage_key)
                if current_serial in connected:
                    g_diff, g_real, g_ram, g_swap = current_metrics[current_serial]
                    current_status = 'offline' if "No ADB" in g_diff or "offline" in g_diff.lower() else 'online'

                    if e_data.get('last_status') == 'offline' and current_status == 'online':
                        e_data['online_indicator_until'] = time.monotonic() + 10
                    e_data['last_status'] = current_status

                    tags = f" {Colors.GREEN}[ONLINE]{Colors.RESET}" if time.monotonic() < e_data.get('online_indicator_until', 0) else ""
                    if e_data.get('track_boot_stats') and (time.monotonic() - e_data['first_seen'] < 900):
                        if e_data.get('is_booting') or ('boot_finished_time' in e_data and time.monotonic() - e_data['boot_finished_time'] < 300):
                            tags += f" {Colors.YELLOW}[RECORDING BOOT STATS]{Colors.RESET}"
                    guest_results[current_serial] = (name, tags, g_diff, g_real, g_ram, g_swap)
                else:
                    is_booting = e_data.get('is_booting', False)
                    last_seen = e_data.get('last_seen', 0)
                    if is_booting or (time.monotonic() - last_seen < 120):
                        tags = f" {Colors.RED}[DISCONNECTED]{Colors.RESET}"
                        guest_results[current_serial] = (name, tags, "N/A", "N/A", "N/A", "N/A")

            curr_time = time.monotonic()
            elapsed = max(0.001, curr_time - last_time)

            # Safely decouples paging metric calculations from loop-timeouts to inherently guarantee no "millions of pages/s" delta spikes
            paging_unit = "pages/s" if os_name == "Darwin" else "KB/s"
            paging_str = "Calculating..."

            paging_in_rate = max(0.0, pgin - last_pgin) / elapsed if last_pgin > 0 else 0.0
            paging_out_rate = max(0.0, pgout - last_pgout) / elapsed if last_pgout > 0 else 0.0

            if pgin > 0 or pgout > 0:
                if last_pgin > 0 or last_pgout > 0:
                    paging_str = f"In: {int(paging_in_rate)} / Out: {int(paging_out_rate)} ({paging_unit})"
                last_pgin, last_pgout = pgin, pgout
            else:
                paging_str = "Calculating..." if iter_count == 0 else f"In: 0 / Out: 0 ({paging_unit})"

            if iter_count > 0:
                summary_data['iterations'] += 1
                if not any(x in host_total for x in ("Calculating", "N/A", "Unsupported", "Error", "Timeout")):
                    summary_data['host_cpu_iterations'] += 1
                    h_c = get_val(host_total)
                    summary_data['host_cpu_sum'] += h_c
                    summary_data['host_cpu_max'] = max(summary_data['host_cpu_max'], h_c)
                if not any(x in host_ram for x in ("Calculating", "N/A", "Unsupported", "Error", "Timeout")):
                    summary_data['host_ram_iterations'] += 1
                    h_r = get_val(host_ram)
                    summary_data['host_ram_sum'] += h_r
                    summary_data['host_ram_max'] = max(summary_data['host_ram_max'], h_r)

                if netsim_cpu != "N/A" and "Calculating" not in netsim_cpu:
                    summary_data['netsim_cpu_iterations'] += 1
                    n_c = get_val(netsim_cpu)
                    summary_data['netsim_cpu_sum'] += n_c
                    summary_data['netsim_cpu_max'] = max(summary_data.get('netsim_cpu_max', 0.0), n_c)

                if netsim_ram != "N/A" and "Calculating" not in netsim_ram:
                    summary_data['netsim_ram_iterations'] += 1
                    n_r = get_val(netsim_ram)
                    summary_data['netsim_ram_sum'] += n_r
                    summary_data['netsim_ram_max'] = max(summary_data.get('netsim_ram_max', 0.0), n_r)

                for key, e_data in summary_data['emulators'].items():
                    curr_s = e_data.get('current_serial', key)
                    if curr_s in qemu_stats:
                        q_cpu_val = get_val(qemu_stats[curr_s][0])
                        q_ram_val = get_val(qemu_stats[curr_s][1])
                        e_data['qemu_cpu_iterations'] += 1
                        e_data['qemu_cpu_sum'] += q_cpu_val
                        e_data['qemu_cpu_max'] = max(e_data.get('qemu_cpu_max', 0.0), q_cpu_val)
                        e_data['qemu_ram_iterations'] += 1
                        e_data['qemu_ram_sum'] += q_ram_val
                        e_data['qemu_ram_max'] = max(e_data.get('qemu_ram_max', 0.0), q_ram_val)

                ts_entry = {
                    'time': time.monotonic(),
                    'host_cpu': get_val(host_total, True),
                    'host_ram': get_val(host_ram, True),
                    'netsim_cpu': get_val(netsim_cpu, True),
                    'netsim_ram': get_val(netsim_ram, True),
                    'host_gpu': get_val(gpu_load, True),
                    'host_gpu_mem': get_val(gpu_ram, True),
                    'host_swap': get_val(host_swap, True),
                    'host_thermal_c': get_val(temp_str, True),
                    'host_disk_read_mbs': read_mb,
                    'host_disk_write_mbs': write_mb,
                    'host_paging_in': paging_in_rate,
                    'host_paging_out': paging_out_rate,
                    'host_battery': get_val(batt_str, True),
                    'host_unmapped_qemu_cpu': sum(get_val(c) for p, c, r in unmapped_qemu) if unmapped_qemu else 0.0,
                    'host_unmapped_qemu_ram': sum(get_val(r) for p, c, r in unmapped_qemu) if unmapped_qemu else 0.0,
                }


                for key, e_data in summary_data['emulators'].items():
                    curr_s = e_data.get('current_serial', key)
                    if curr_s in current_metrics:
                        ts_entry[f'emu_cpu_tot_{key}'] = get_val(current_metrics[curr_s][0], True)
                        ts_entry[f'emu_cpu_adj_{key}'] = get_val(current_metrics[curr_s][1], True)
                        ts_entry[f'emu_ram_{key}'] = get_val(current_metrics[curr_s][2], True)
                        ts_entry[f'emu_swap_{key}'] = get_val(current_metrics[curr_s][3], True)
                        m_err = RE_GUEST_ERRS.search(current_metrics[curr_s][2])
                        ts_entry[f'emu_errs_{key}'] = float(m_err.group(1)) if m_err else 0.0
                        m_pct = None
                        ram_parts = current_metrics[curr_s][2].split(',')
                        if len(ram_parts) >= 3:
                            m_pct = RE_GUEST_PCT_EXTRACT.search(ram_parts[2])
                        ts_entry[f'emu_disk_pct_{key}'] = float(m_pct.group(1)) if m_pct else float('nan')
                    else:
                        ts_entry[f'emu_cpu_tot_{key}'] = float('nan')
                        ts_entry[f'emu_cpu_adj_{key}'] = float('nan')
                        ts_entry[f'emu_ram_{key}'] = float('nan')
                        ts_entry[f'emu_swap_{key}'] = float('nan')
                        ts_entry[f'emu_errs_{key}'] = float('nan')
                        ts_entry[f'emu_disk_pct_{key}'] = float('nan')

                    if curr_s in qemu_stats:
                        ts_entry[f'qemu_cpu_{key}'] = get_val(qemu_stats[curr_s][0], True)
                        ts_entry[f'qemu_ram_{key}'] = get_val(qemu_stats[curr_s][1], True)
                        m_cs = re.search(r'(\d+)\s*cs', qemu_stats[curr_s][1])
                        ts_entry[f'qemu_cs_{key}'] = float(m_cs.group(1)) if m_cs else 0.0
                        ts_entry[f'qemu_gpu_{key}'] = get_val(emu_gpu_stats.get(curr_s, "0.0%"), True)
                    else:
                        ts_entry[f'qemu_cpu_{key}'] = float('nan')
                        ts_entry[f'qemu_ram_{key}'] = float('nan')
                        ts_entry[f'qemu_cs_{key}'] = float('nan')
                        ts_entry[f'qemu_gpu_{key}'] = float('nan')

                summary_data['time_series'].append(ts_entry)

            def apply_trend(key, curr_str):
                if any(x in curr_str for x in ["N/A", "Unsupported", "Calculating", "No ADB", "Error", "Timeout"]): return curr_str
                curr_val, prev_val = get_val(curr_str), prev_stats.get(key, get_val(curr_str))
                prev_stats[key] = curr_val
                return f"{curr_str} {'↑' if curr_val > prev_val + 0.5 else '↓' if curr_val < prev_val - 0.5 else '→'}"

            if iter_count > 0:
                host_total = apply_trend('h_cpu', host_total)
                host_ram = apply_trend('h_ram', host_ram)
                netsim_cpu = apply_trend('n_cpu', netsim_cpu)
                for serial in list(guest_results.keys()):
                    disp, tags, g_diff, g_real, g_ram, g_swap = guest_results[serial]
                    guest_results[serial] = (disp, tags, g_diff, apply_trend(f'{serial}_real', g_real), apply_trend(f'{serial}_ram', g_ram), apply_trend(f'{serial}_swap', g_swap))
            else:
                for k, v in zip(['h_cpu','h_ram','n_cpu'], [host_total, host_ram, netsim_cpu]): prev_stats[k] = get_val(v)
                for serial, stats in guest_results.items():
                    prev_stats[f'{serial}_real'] = get_val(stats[3])
                    prev_stats[f'{serial}_ram'] = get_val(stats[4])
                    prev_stats[f'{serial}_swap'] = get_val(stats[5])

            last_time = curr_time
            term_width = shutil.get_terminal_size(fallback=(86, 24)).columns
            force_clear = (iter_count == 0) or (term_width != prev_term_width)

            if not args.headless and (not args.once or iter_count > 0):
                print_dashboard(os_name, host_total, qemu_stats, netsim_cpu, guest_results, host_ram, host_swap, paging_str, iter_count, term_width, force_clear, unmapped_qemu, netsim_ram, gpu_load, gpu_ram, emu_gpu_stats, temp_str=temp_str, disk_str=disk_str, is_once=args.once, batt_str=batt_str, hw_specs=hardware_specs)

            prev_term_width = term_width
            if args.once and iter_count > 0: break
            iter_count += 1

            if args.auto_save_interval > 0 and (time.time() - last_auto_save) >= args.auto_save_interval * 60:
                try: save_summary(summary_data, time.time(), save_csv=args.csv, save_txt=not args.no_summary, out_dir=args.out_dir, is_autosave=True)
                except Exception: pass
                last_auto_save = time.time()

            # Subtly corrects cadence offset natively, ensuring exact timer intervals
            sleep_time = args.interval - (time.monotonic() - loop_start)
            time.sleep(max(0.1, sleep_time))

    except KeyboardInterrupt: pass
    except Exception:
        # Guarantee trace visibility by dropping out of Alternate Screen Buffer before logging crashes
        if not args.headless: restore_screen(args.once, args.headless)
        print(f"\n{Colors.RED}[Fatal Error] Application Crashed:{Colors.RESET}")
        traceback.print_exc()
        exit_code = 1    # Safely track the failure
    finally:
        executor.shutdown(wait=False)
        if not args.headless: restore_screen(args.once, args.headless)

        try: save_summary(summary_data, time.time(), save_csv=args.csv, save_txt=not args.no_summary, out_dir=args.out_dir)
        except Exception: pass

        if exit_code == 0:
            print(f"{Colors.GREEN}Monitoring stopped cleanly. Goodbye!{Colors.RESET}\033[K")
        sys.stdout.flush()

        os._exit(exit_code) # Dispatch the correct state


if __name__ == "__main__":
    main()
