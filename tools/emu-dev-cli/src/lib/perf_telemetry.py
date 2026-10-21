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

"""Cross-platform Host & Guest telemetry collectors and profilers for emulator binaries.

Supports Linux (KVM), macOS (HVF), and Windows (WHPX/AEHD) via:
  1. Host /proc/<pid> (with per-vCPU guest_time/stime/utime split) + psutil cross-platform fallback
  2. Linux /sys/kernel/debug/kvm/<pid>-*/ hardware VM-exit counters (when readable)
  3. Universal Android guest /proc/interrupts & /proc/vmstat hypervisor boundary counters
  4. Linux perf stat / perf record and macOS sample call-stack profilers
"""

from dataclasses import asdict, dataclass, field
import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

KVM_DEBUGFS_COUNTERS = (
    "exits",
    "mmio_exits",
    "io_exits",
    "signal_exits",
    "notify_window_exits",
    "pf_fixed",
    "pf_guest",
    "tdp_page_faults",
    "tlb_flush",
    "remote_tlb_flush",
    "remote_tlb_flush_requests",
    "irq_exits",
    "irq_injections",
    "irq_window_exits",
    "halt_exits",
    "halt_successful_poll",
    "halt_wakeup",
    "insn_emulation",
    "hypercalls",
)


@dataclass
class ProcSnapshot:
    """Point-in-time snapshot of a host emulator process from /proc/<pid> or psutil."""

    pid: int
    timestamp: float
    utime_ticks: int = 0
    stime_ticks: int = 0
    rss_mib: float = 0.0
    peak_rss_mib: float = 0.0
    rss_anon_mib: float = 0.0
    rss_shmem_mib: float = 0.0
    voluntary_ctxt_switches: int = 0
    nonvoluntary_ctxt_switches: int = 0
    thread_ticks: Dict[str, int] = field(default_factory=dict)
    vcpu_guest_ticks: int = 0
    vcpu_kernel_ticks: int = 0
    vcpu_userspace_ticks: int = 0


@dataclass
class WorkloadTelemetry:
    """Aggregated host/guest resource telemetry captured during a workload window."""

    wall_duration_s: float = 0.0
    host_cpu_pct: float = 0.0
    host_user_cpu_pct: float = 0.0
    host_sys_cpu_pct: float = 0.0
    host_cpu_ms: float = 0.0
    rss_mib: float = 0.0
    peak_rss_mib: float = 0.0
    voluntary_ctxt_switches_delta: int = 0
    nonvoluntary_ctxt_switches_delta: int = 0
    vcpu_guest_cpu_pct: float = 0.0
    vcpu_kvm_kernel_cpu_pct: float = 0.0
    vcpu_qemu_userspace_cpu_pct: float = 0.0
    top_threads_cpu_pct: Dict[str, float] = field(default_factory=dict)
    kvm_exits: Dict[str, int] = field(default_factory=dict)
    guest_irqs: Dict[str, int] = field(default_factory=dict)
    perf_stat: Dict[str, float] = field(default_factory=dict)
    top_symbols: List[Dict[str, Any]] = field(default_factory=list)
    perfetto_slices: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


def parse_proc_stat_line_ex(stat_content: str) -> Tuple[str, int, int, int]:
    """Parses /proc/<pid>/stat or /proc/<pid>/task/<tid>/stat content including guest_time.

    Returns (comm, utime_ticks, stime_ticks, guest_time_ticks).
    In Linux /proc/[pid]/stat:
      - field 3 (rest[0]) is state
      - field 14 (rest[11]) is utime (includes guest_time)
      - field 15 (rest[12]) is stime
      - field 43 (rest[40]) is guest_time
    """
    txt = stat_content.strip()
    lpar = txt.find("(")
    rpar = txt.rfind(")")
    if lpar == -1 or rpar == -1 or rpar <= lpar:
        raise ValueError(f"Malformed /proc stat line: {txt[:80]!r}")
    comm = txt[lpar + 1 : rpar].strip()
    rest = txt[rpar + 1 :].strip().split()
    if len(rest) < 13:
        raise ValueError(f"Truncated /proc stat fields: {txt[:80]!r}")
    utime = int(rest[11])
    stime = int(rest[12])
    guest_time = int(rest[40]) if len(rest) >= 41 and rest[40].lstrip("-").isdigit() else 0
    return comm, utime, stime, max(0, guest_time)


def parse_proc_stat_line(stat_content: str) -> Tuple[str, int, int]:
    """Parses /proc/<pid>/stat or /proc/<pid>/task/<tid>/stat content.

    Handles thread names (comm) containing spaces or parentheses.
    Returns (comm, utime_ticks, stime_ticks).
    """
    comm, utime, stime, _ = parse_proc_stat_line_ex(stat_content)
    return comm, utime, stime


def parse_proc_status(status_content: str) -> Dict[str, float]:
    """Parses memory and context-switch counters from /proc/<pid>/status."""
    out: Dict[str, float] = {}
    for line in status_content.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key in ("VmRSS", "VmHWM", "RssAnon", "RssShmem"):
            parts = val.split()
            if parts and parts[0].isdigit():
                out[key] = int(parts[0]) / 1024.0  # kB -> MiB
        elif key in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"):
            if val.isdigit():
                out[key] = float(val)
    return out


def normalize_thread_name(comm: str, tid: int, pid: int) -> str:
    """Normalizes QEMU/AEMU thread names for clean aggregation across runs."""
    name = comm.strip()
    if not name:
        return f"tid-{tid}"
    if tid == pid and name.startswith("qemu-system"):
        return "qemu-main"
    # Keep vCPU threads distinct (e.g. 'CPU 0/KVM', 'CPU 1/KVM')
    if name.startswith("CPU ") and ("/KVM" in name or "/HVF" in name or "/WHPX" in name):
        return name
    # Group numbered worker pool threads (e.g. 'worker-3' -> 'worker-*')
    m = re.match(r"^([a-zA-Z0-9_-]+?)[-_]\d+$", name)
    if m and not name.startswith("CPU"):
        return f"{m.group(1)}-*"
    return name


def _take_psutil_snapshot(pid: int) -> Optional[ProcSnapshot]:
    """Cross-platform host process snapshot via psutil (macOS, Windows, and Linux fallback)."""
    if psutil is None:
        return None
    try:
        proc = psutil.Process(pid)
        now = time.perf_counter()
        hz = _get_clk_tck()
        cpu_t = proc.cpu_times()
        utime_ticks = int(getattr(cpu_t, "user", 0.0) * hz)
        stime_ticks = int(getattr(cpu_t, "system", 0.0) * hz)

        mem = proc.memory_info()
        rss_mib = round(getattr(mem, "rss", 0) / (1024.0 * 1024.0), 2)
        peak_bytes = getattr(mem, "peak_wset", 0) or getattr(mem, "rss", 0)
        peak_rss_mib = round(peak_bytes / (1024.0 * 1024.0), 2)

        vol_ctx = 0
        invol_ctx = 0
        try:
            ctx = proc.num_ctx_switches()
            vol_ctx = int(getattr(ctx, "voluntary", 0))
            invol_ctx = int(getattr(ctx, "involuntary", 0))
        except Exception:
            pass

        thread_ticks: Dict[str, int] = {}
        try:
            for th in proc.threads():
                t_ticks = int((th.user_time + th.system_time) * hz)
                t_name = "qemu-main" if th.id == pid else f"tid-{th.id}"
                thread_ticks[t_name] = thread_ticks.get(t_name, 0) + t_ticks
        except Exception:
            pass

        return ProcSnapshot(
            pid=pid,
            timestamp=now,
            utime_ticks=utime_ticks,
            stime_ticks=stime_ticks,
            rss_mib=rss_mib,
            peak_rss_mib=peak_rss_mib,
            voluntary_ctxt_switches=vol_ctx,
            nonvoluntary_ctxt_switches=invol_ctx,
            thread_ticks=thread_ticks,
        )
    except Exception as e:
        logger.debug("psutil snapshot failed for pid %s: %s", pid, e)
        return None


def take_proc_snapshot(pid: int, proc_root: str = "/proc") -> Optional[ProcSnapshot]:
    """Captures a point-in-time CPU, thread, vCPU split, and RSS snapshot of `pid`."""
    proc_dir = os.path.join(proc_root, str(pid))
    stat_path = os.path.join(proc_dir, "stat")
    if not os.path.exists(stat_path):
        if proc_root == "/proc":
            return _take_psutil_snapshot(pid)
        return None

    now = time.perf_counter()
    try:
        with open(stat_path, "r", encoding="utf-8", errors="replace") as f:
            _, utime, stime, proc_guest = parse_proc_stat_line_ex(f.read())
    except Exception as e:
        logger.debug("Failed reading %s: %s", stat_path, e)
        return None

    status_metrics: Dict[str, float] = {}
    status_path = os.path.join(proc_dir, "status")
    try:
        with open(status_path, "r", encoding="utf-8", errors="replace") as f:
            status_metrics = parse_proc_status(f.read())
    except Exception:
        pass

    thread_ticks: Dict[str, int] = {}
    vcpu_guest_ticks = 0
    vcpu_kernel_ticks = 0
    vcpu_userspace_ticks = 0

    task_dir = os.path.join(proc_dir, "task")
    if os.path.isdir(task_dir):
        try:
            for tid_str in os.listdir(task_dir):
                if not tid_str.isdigit():
                    continue
                tid = int(tid_str)
                t_stat_path = os.path.join(task_dir, tid_str, "stat")
                try:
                    with open(
                        t_stat_path, "r", encoding="utf-8", errors="replace"
                    ) as tf:
                        t_comm, t_u, t_s, t_guest = parse_proc_stat_line_ex(tf.read())
                    norm_name = normalize_thread_name(t_comm, tid, pid)
                    thread_ticks[norm_name] = (
                        thread_ticks.get(norm_name, 0) + t_u + t_s
                    )
                    if t_guest > 0 or (
                        t_comm.startswith("CPU ") and "/KVM" in t_comm
                    ):
                        vcpu_guest_ticks += t_guest
                        vcpu_kernel_ticks += t_s
                        vcpu_userspace_ticks += max(0, t_u - t_guest)
                except Exception:
                    continue
        except Exception:
            pass

    if vcpu_guest_ticks == 0 and proc_guest > 0:
        vcpu_guest_ticks = proc_guest

    return ProcSnapshot(
        pid=pid,
        timestamp=now,
        utime_ticks=utime,
        stime_ticks=stime,
        rss_mib=round(status_metrics.get("VmRSS", 0.0), 2),
        peak_rss_mib=round(status_metrics.get("VmHWM", 0.0), 2),
        rss_anon_mib=round(status_metrics.get("RssAnon", 0.0), 2),
        rss_shmem_mib=round(status_metrics.get("RssShmem", 0.0), 2),
        voluntary_ctxt_switches=int(
            status_metrics.get("voluntary_ctxt_switches", 0)
        ),
        nonvoluntary_ctxt_switches=int(
            status_metrics.get("nonvoluntary_ctxt_switches", 0)
        ),
        thread_ticks=thread_ticks,
        vcpu_guest_ticks=vcpu_guest_ticks,
        vcpu_kernel_ticks=vcpu_kernel_ticks,
        vcpu_userspace_ticks=vcpu_userspace_ticks,
    )


def compute_proc_telemetry(
    before: Optional[ProcSnapshot],
    after: Optional[ProcSnapshot],
    clk_tck: Optional[int] = None,
    top_n_threads: int = 8,
) -> WorkloadTelemetry:
    """Computes CPU%, CPU-ms, vCPU 3-way split, RSS, and per-thread CPU% between two snapshots."""
    if not before or not after or after.timestamp <= before.timestamp:
        return WorkloadTelemetry()

    hz = float(clk_tck or _get_clk_tck())
    elapsed_s = after.timestamp - before.timestamp
    d_utime_s = max(0, after.utime_ticks - before.utime_ticks) / hz
    d_stime_s = max(0, after.stime_ticks - before.stime_ticks) / hz
    d_total_s = d_utime_s + d_stime_s

    host_cpu_pct = (d_total_s / elapsed_s) * 100.0
    host_user_pct = (d_utime_s / elapsed_s) * 100.0
    host_sys_pct = (d_stime_s / elapsed_s) * 100.0
    host_cpu_ms = d_total_s * 1000.0

    d_vcpu_guest_s = max(0, after.vcpu_guest_ticks - before.vcpu_guest_ticks) / hz
    d_vcpu_kern_s = max(0, after.vcpu_kernel_ticks - before.vcpu_kernel_ticks) / hz
    d_vcpu_user_s = max(0, after.vcpu_userspace_ticks - before.vcpu_userspace_ticks) / hz

    thread_deltas: List[Tuple[str, float]] = []
    for t_name, after_ticks in after.thread_ticks.items():
        before_ticks = before.thread_ticks.get(t_name, 0)
        d_ticks = max(0, after_ticks - before_ticks)
        if d_ticks > 0:
            t_pct = ((d_ticks / hz) / elapsed_s) * 100.0
            if t_pct >= 0.1:
                thread_deltas.append((t_name, round(t_pct, 2)))

    thread_deltas.sort(key=lambda item: item[1], reverse=True)
    top_threads = dict(thread_deltas[:top_n_threads])

    return WorkloadTelemetry(
        wall_duration_s=round(elapsed_s, 4),
        host_cpu_pct=round(host_cpu_pct, 2),
        host_user_cpu_pct=round(host_user_pct, 2),
        host_sys_cpu_pct=round(host_sys_pct, 2),
        host_cpu_ms=round(host_cpu_ms, 2),
        rss_mib=after.rss_mib,
        peak_rss_mib=max(before.peak_rss_mib, after.peak_rss_mib, after.rss_mib),
        voluntary_ctxt_switches_delta=max(
            0, after.voluntary_ctxt_switches - before.voluntary_ctxt_switches
        ),
        nonvoluntary_ctxt_switches_delta=max(
            0, after.nonvoluntary_ctxt_switches - before.nonvoluntary_ctxt_switches
        ),
        vcpu_guest_cpu_pct=round((d_vcpu_guest_s / elapsed_s) * 100.0, 2),
        vcpu_kvm_kernel_cpu_pct=round((d_vcpu_kern_s / elapsed_s) * 100.0, 2),
        vcpu_qemu_userspace_cpu_pct=round((d_vcpu_user_s / elapsed_s) * 100.0, 2),
        top_threads_cpu_pct=top_threads,
    )


def take_kvm_debugfs_snapshot(
    pid: int, kvm_debugfs_root: str = "/sys/kernel/debug/kvm"
) -> Dict[str, int]:
    """Reads per-VM hardware exit counters from /sys/kernel/debug/kvm/<pid>-*/ when accessible."""
    counters: Dict[str, int] = {}
    if not os.path.isdir(kvm_debugfs_root):
        return counters
    try:
        vm_dirs = glob.glob(os.path.join(kvm_debugfs_root, f"{pid}-*"))
        for vm_dir in vm_dirs:
            if not os.path.isdir(vm_dir):
                continue
            for key in KVM_DEBUGFS_COUNTERS:
                fpath = os.path.join(vm_dir, key)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        val_str = f.read().strip()
                    if val_str.lstrip("-").isdigit():
                        counters[key] = counters.get(key, 0) + int(val_str)
                except OSError:
                    continue
    except OSError:
        pass
    return counters


def compute_kvm_exit_deltas(
    before: Optional[Dict[str, int]], after: Optional[Dict[str, int]]
) -> Dict[str, int]:
    """Computes non-zero deltas between two KVM debugfs snapshots."""
    if not before or not after:
        return {}
    deltas: Dict[str, int] = {}
    for k, after_val in after.items():
        d = after_val - before.get(k, 0)
        if d > 0:
            deltas[k] = d
    return deltas


def parse_guest_proc_interrupts(text: str) -> Dict[str, int]:
    """Parses guest Android /proc/interrupts into categorized hypervisor IRQ totals.

    Works identically across x86_64 (KVM/WHPX) and arm64 (HVF) guests.
    """
    totals: Dict[str, int] = {
        "virtio_input_irqs": 0,
        "virtio_blk_irqs": 0,
        "virtio_gpu_irqs": 0,
        "virtio_vsock_irqs": 0,
        "virtio_total_irqs": 0,
        "timer_irqs": 0,
        "reschedule_ipis": 0,
        "tlb_shootdowns": 0,
        "total_irqs": 0,
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        prefix, rest = line.split(":", 1)
        irq_id = prefix.strip().upper()
        tokens = rest.split()
        row_sum = 0
        desc_tokens: List[str] = []
        for idx, tok in enumerate(tokens):
            if tok.isdigit():
                row_sum += int(tok)
            else:
                desc_tokens = tokens[idx:]
                break
        if row_sum <= 0:
            continue

        desc = " ".join(desc_tokens).lower()
        totals["total_irqs"] += row_sum

        if irq_id == "LOC" or "arch_timer" in desc or "hrtimer" in desc:
            totals["timer_irqs"] += row_sum
        elif irq_id in ("RES", "CAL") or irq_id.startswith("IPI") or "rescheduling" in desc:
            totals["reschedule_ipis"] += row_sum
        elif irq_id == "TLB" or "tlb" in desc:
            totals["tlb_shootdowns"] += row_sum

        if "virtio" in desc or "goldfish" in desc:
            totals["virtio_total_irqs"] += row_sum
            if "input" in desc:
                totals["virtio_input_irqs"] += row_sum
            elif "req" in desc or "blk" in desc:
                totals["virtio_blk_irqs"] += row_sum
            elif "control" in desc or "cursor" in desc or "gpu" in desc:
                totals["virtio_gpu_irqs"] += row_sum
            elif "vsock" in desc or "vmw_vsock" in desc or "-rx" in desc or "-tx" in desc:
                totals["virtio_vsock_irqs"] += row_sum

    return totals


def parse_guest_proc_vmstat(text: str) -> Dict[str, int]:
    """Extracts page fault and swap/paging counters from guest /proc/vmstat."""
    keys = ("pgfault", "pgmajfault", "pgpgin", "pgpgout")
    out: Dict[str, int] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[0] in keys and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def take_guest_hypervisor_snapshot(
    adb_fn: Optional[Callable[[List[str], float], subprocess.CompletedProcess]]
) -> Dict[str, int]:
    """Snapshots guest /proc/interrupts and /proc/vmstat via adb shell."""
    if adb_fn is None:
        return {}
    combined: Dict[str, int] = {}
    try:
        res_irq = adb_fn(["shell", "cat /proc/interrupts"], 5.0)
        if res_irq.returncode == 0 and res_irq.stdout:
            combined.update(parse_guest_proc_interrupts(res_irq.stdout))
        res_vm = adb_fn(["shell", "cat /proc/vmstat"], 5.0)
        if res_vm.returncode == 0 and res_vm.stdout:
            combined.update(parse_guest_proc_vmstat(res_vm.stdout))
    except Exception as e:
        logger.debug("Guest hypervisor snapshot warning: %s", e)
    return combined


def compute_guest_hypervisor_deltas(
    before: Optional[Dict[str, int]], after: Optional[Dict[str, int]]
) -> Dict[str, int]:
    """Computes positive counter deltas between two guest hypervisor snapshots."""
    if not before or not after:
        return {}
    deltas: Dict[str, int] = {}
    for k, after_val in after.items():
        d = after_val - before.get(k, 0)
        if d > 0:
            deltas[k] = d
    return deltas


def is_nested_virtualization(proc_root: str = "/proc") -> bool:
    """Detects whether the current Linux host is itself running inside a hypervisor (L1 VM)."""
    cpuinfo_path = os.path.join(proc_root, "cpuinfo")
    if os.path.isfile(cpuinfo_path):
        try:
            with open(cpuinfo_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if re.search(r"^flags\s*:.*\bhypervisor\b", content, re.MULTILINE):
                return True
        except OSError:
            pass
    return False


def find_emulator_pid(
    serial: Optional[str] = None, proc_root: str = "/proc"
) -> Optional[int]:
    """Locates the running `qemu-system-*` host PID for an ADB serial across Linux, macOS, and Windows."""
    port_str = None
    if serial and serial.startswith("emulator-"):
        port_str = serial.split("-", 1)[1]

    candidates: List[Tuple[int, str]] = []
    if os.path.isdir(proc_root):
        try:
            for entry in os.listdir(proc_root):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                cmdline_path = os.path.join(proc_root, entry, "cmdline")
                try:
                    with open(cmdline_path, "rb") as f:
                        raw = f.read()
                    if not raw:
                        continue
                    args = [
                        part.decode("utf-8", errors="replace")
                        for part in raw.split(b"\x00")
                        if part
                    ]
                    if not args:
                        continue
                    exe_base = os.path.basename(args[0])
                    if "qemu-system" in exe_base:
                        cmd_joined = " ".join(args)
                        if port_str and (
                            f"-port {port_str}" in cmd_joined
                            or f"tcp:{port_str}" in cmd_joined
                        ):
                            return pid
                        candidates.append((pid, cmd_joined))
                except Exception:
                    continue
        except Exception:
            pass

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][0]

    # Cross-platform psutil discovery for Windows and macOS
    if proc_root == "/proc" and psutil is not None:
        try:
            ps_candidates: List[int] = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                name = (proc.info.get("name") or "").lower()
                cmdline = proc.info.get("cmdline") or []
                cmd_joined = " ".join(cmdline)
                if "qemu-system" in name or "qemu-system" in cmd_joined:
                    if port_str and (
                        f"-port {port_str}" in cmd_joined
                        or f"tcp:{port_str}" in cmd_joined
                    ):
                        return int(proc.info["pid"])
                    ps_candidates.append(int(proc.info["pid"]))
            if ps_candidates:
                return sorted(ps_candidates)[-1]
        except Exception:
            pass

    # Fallback to pgrep on macOS / non-procfs
    try:
        res = subprocess.run(
            ["pgrep", "-f", "qemu-system"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [
            int(line.strip())
            for line in res.stdout.splitlines()
            if line.strip().isdigit()
        ]
        if pids:
            return pids[-1]
    except Exception:
        pass
    return None


def sample_host_cpu_pct(pid: int, window_sec: float = 1.0) -> float:
    """Samples host CPU% of `pid` over `window_sec` across Linux, macOS, and Windows."""
    s1 = take_proc_snapshot(pid)
    if s1 is not None:
        time.sleep(max(0.1, window_sec))
        s2 = take_proc_snapshot(pid)
        if s2 is not None:
            return compute_proc_telemetry(s1, s2).host_cpu_pct

    # Fallback to `ps` for macOS
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu"],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return float(lines[1])
    except Exception:
        pass
    return 0.0


def parse_perf_stat_output(stderr_text: str) -> Dict[str, float]:
    """Parses `perf stat` output into a structured dictionary of hardware/OS counters."""
    metrics: Dict[str, float] = {}
    for line in stderr_text.splitlines():
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#"):
            continue
        m = re.match(
            r"^([\d,]+(?:\.\d+)?)\s+(?:[a-zA-Z]+\s+)?([a-zA-Z0-9_:-]+)",
            line_clean,
        )
        if m:
            val_str = m.group(1).replace(",", "")
            event_name = m.group(2).lower()
            try:
                val = float(val_str)
                metrics[event_name] = val
            except ValueError:
                pass
        m_ipc = re.search(r"#\s*([\d.]+)\s+insn per cycle", line_clean)
        if m_ipc:
            try:
                metrics["ipc"] = float(m_ipc.group(1))
            except ValueError:
                pass

    if (
        "ipc" not in metrics
        and metrics.get("cycles", 0) > 0
        and "instructions" in metrics
    ):
        metrics["ipc"] = round(
            metrics["instructions"] / metrics["cycles"], 3
        )
    return metrics


def parse_perf_report_stdio(
    report_text: str, top_n: int = 12
) -> List[Dict[str, Any]]:
    """Parses `perf report --stdio --no-children` output into top symbol entries."""
    symbols: List[Dict[str, Any]] = []
    for line in report_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(
            r"^([\d.]+)%\s+(.+?)\s{2,}(\S+)\s+\[([.kHgu])\]\s+(.+)$", s
        )
        if m:
            overhead = float(m.group(1))
            comm = m.group(2).strip()
            dso = m.group(3)
            mode = m.group(4)
            sym = m.group(5).strip()
            symbols.append(
                {
                    "overhead_pct": overhead,
                    "command": comm,
                    "shared_object": dso,
                    "mode": "kernel" if mode == "k" else "user",
                    "symbol": sym,
                }
            )
            if len(symbols) >= top_n:
                break
    return symbols


class WorkloadProfilerSession:
    """Context manager that attaches host & guest profilers around a workload."""

    def __init__(
        self,
        pid: Optional[int],
        profiles: Optional[List[str]] = None,
        perfetto_trace_path: Optional[str] = None,
        adb_fn: Optional[Callable[[List[str], float], subprocess.CompletedProcess]] = None,
    ):
        self.pid = pid
        self.profiles = [p.strip().lower() for p in (profiles or ["proc"]) if p]
        self.perfetto_trace_path = perfetto_trace_path
        self.adb_fn = adb_fn
        self.before_snap: Optional[ProcSnapshot] = None
        self.after_snap: Optional[ProcSnapshot] = None
        self.before_kvm: Dict[str, int] = {}
        self.after_kvm: Dict[str, int] = {}
        self.before_guest_irqs: Dict[str, int] = {}
        self.after_guest_irqs: Dict[str, int] = {}
        self._perf_proc: Optional[subprocess.Popen] = None
        self._perf_data_file: Optional[str] = None
        self._perf_mode: Optional[str] = None
        self.telemetry = WorkloadTelemetry()

    def __enter__(self) -> "WorkloadProfilerSession":
        if self.adb_fn is not None:
            self.before_guest_irqs = take_guest_hypervisor_snapshot(self.adb_fn)
        if self.pid:
            self.before_snap = take_proc_snapshot(self.pid)
            self.before_kvm = take_kvm_debugfs_snapshot(self.pid)
            perf_bin = shutil.which("perf")
            if perf_bin:
                if "perf-record" in self.profiles:
                    fd, self._perf_data_file = tempfile.mkstemp(
                        prefix="emu_perf_", suffix=".data"
                    )
                    os.close(fd)
                    self._perf_mode = "record"
                    self._perf_proc = subprocess.Popen(
                        [
                            perf_bin,
                            "record",
                            "-F",
                            "999",
                            "-g",
                            "-p",
                            str(self.pid),
                            "-o",
                            self._perf_data_file,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                elif "perf-stat" in self.profiles:
                    self._perf_mode = "stat"
                    self._perf_proc = subprocess.Popen(
                        [
                            perf_bin,
                            "stat",
                            "-e",
                            "cycles,instructions,context-switches,cpu-migrations,page-faults",
                            "-p",
                            str(self.pid),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.pid:
            self.after_snap = take_proc_snapshot(self.pid)
            self.after_kvm = take_kvm_debugfs_snapshot(self.pid)
            self.telemetry = compute_proc_telemetry(
                self.before_snap, self.after_snap
            )
            self.telemetry.kvm_exits = compute_kvm_exit_deltas(
                self.before_kvm, self.after_kvm
            )

        if self.adb_fn is not None and self.before_guest_irqs:
            self.after_guest_irqs = take_guest_hypervisor_snapshot(self.adb_fn)
            self.telemetry.guest_irqs = compute_guest_hypervisor_deltas(
                self.before_guest_irqs, self.after_guest_irqs
            )

        if self._perf_proc is not None:
            try:
                import signal

                self._perf_proc.send_signal(signal.SIGINT)
                _, stderr_out = self._perf_proc.communicate(timeout=10.0)
                if self._perf_mode == "stat" and stderr_out:
                    self.telemetry.perf_stat = parse_perf_stat_output(stderr_out)
                elif (
                    self._perf_mode == "record"
                    and self._perf_data_file
                    and os.path.exists(self._perf_data_file)
                ):
                    perf_bin = shutil.which("perf") or "perf"
                    rep = subprocess.run(
                        [
                            perf_bin,
                            "report",
                            "--stdio",
                            "--no-children",
                            "-i",
                            self._perf_data_file,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=15.0,
                    )
                    if rep.returncode == 0 and rep.stdout:
                        self.telemetry.top_symbols = parse_perf_report_stdio(
                            rep.stdout
                        )
            except Exception as e:
                logger.debug("Linux perf profiler collection warning: %s", e)
            finally:
                if self._perf_data_file and os.path.exists(self._perf_data_file):
                    try:
                        os.remove(self._perf_data_file)
                    except OSError:
                        pass

        if self.perfetto_trace_path and os.path.isfile(self.perfetto_trace_path):
            self.telemetry.perfetto_slices = query_perfetto_top_slices(
                self.perfetto_trace_path
            )


def query_perfetto_top_slices(
    trace_path: str, top_n: int = 12
) -> List[Dict[str, Any]]:
    """Queries top slices by total wall duration and count if trace_processor_shell is available."""
    tp_bin = shutil.which("trace_processor_shell") or shutil.which(
        "trace_processor"
    )
    if not tp_bin or not os.path.isfile(trace_path):
        return []

    sql = (
        "SELECT name, COUNT(*) AS cnt, "
        "ROUND(SUM(dur) / 1e6, 2) AS total_ms, "
        "ROUND(AVG(dur) / 1e3, 2) AS avg_us "
        "FROM slice WHERE dur >= 0 "
        "GROUP BY name ORDER BY total_ms DESC "
        f"LIMIT {int(top_n)};"
    )
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sql", delete=False, encoding="utf-8"
        ) as qf:
            qf.write(sql)
            q_path = qf.name
        try:
            res = subprocess.run(
                [tp_bin, "-q", q_path, trace_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=20.0,
            )
            if res.returncode != 0 or not res.stdout:
                return []
            lines = [
                ln.strip() for ln in res.stdout.splitlines() if ln.strip()
            ]
            if len(lines) < 2:
                return []
            slices: List[Dict[str, Any]] = []
            for row in lines[1:]:
                parts = [p.strip().strip('"') for p in row.split(",")]
                if len(parts) >= 4:
                    try:
                        slices.append(
                            {
                                "name": parts[0],
                                "count": int(parts[1]),
                                "total_ms": float(parts[2]),
                                "avg_us": float(parts[3]),
                            }
                        )
                    except ValueError:
                        continue
            return slices
        finally:
            if os.path.exists(q_path):
                os.remove(q_path)
    except Exception as e:
        logger.debug("Perfetto trace_processor query failed: %s", e)
        return []
