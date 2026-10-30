# Copyright (C) 2026 The Android Open Source Project
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

"""Template Method benchmark session lifecycle for live devices and managed emulators."""

from abc import ABC, abstractmethod
import datetime
import glob
import logging
import os
import platform
import re
import shutil
import signal
import socket
import statistics
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.process import ProcessRunner, SubprocessRunner
from lib.avd import (
    create_single_avd,
    find_actual_sysimg_dir,
    find_cached_sysimg_dir,
    get_avd_paths,
    get_default_avd_root,
)
from lib.emulator import prepare_environment, spawn_emulator_process
from lib.perf.adb import AdbDevice, _adb_cmd
from lib.perf.catalog import (
    BenchmarkPlan,
    EmulatorLaunchConfig,
    ManagedBenchmarkConfig,
    PROBE_WORKLOADS,
    ResolvedPerfTarget,
)
from lib.perf.guest_settle import wait_for_guest_and_host_settled
from lib.perf.stats import summarize_samples
from lib.perf.workloads.base import WorkloadContext
from lib.perf.workloads.registry import (
    WorkloadStrategyRegistry,
    create_default_workload_registry,
)
from lib.perf.workloads.snapshot import _ensure_valid_console_auth_token
from lib.perf_insights import append_perf_history, generate_diagnostic_insights
from lib.perf_probe_client import PerfProbeSession
from lib.perf_telemetry import (
    find_emulator_pid,
    is_nested_virtualization,
    take_proc_snapshot,
)

logger = logging.getLogger(__name__)

CARRY_OVER_METRIC_KEYS = (
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
)

CARRY_OVER_DICT_KEYS = (
    "top_threads_cpu_pct",
    "top_symbols",
    "perfetto_slices",
    "kvm_top_exit_reasons",
    "guest_virtio_irqs",
)


def _is_port_pair_busy(port: int) -> bool:
    for check_p in (port, port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", check_p)) == 0:
                return True
    return False


def _find_free_console_port(start_port: int = 5560) -> int:
    for port in range(start_port, start_port + 100, 2):
        if not _is_port_pair_busy(port):
            return port
    return start_port + 20


def _try_kill_pid(pid_str: str) -> None:
    clean_pid = pid_str.strip()
    if not clean_pid.isdigit():
        return
    try:
        os.kill(int(clean_pid), signal.SIGKILL)
    except OSError as exc:
        logger.debug("Ignored error killing netsimd %s: %s", clean_pid, exc)


def _kill_orphaned_netsimd(runner: ProcessRunner) -> None:
    try:
        res = runner.run(["pgrep", "-f", "netsimd"], check=False, timeout=3.0)
    except Exception as exc:
        logger.debug("Ignored error searching netsimd processes: %s", exc)
        return
    for pid_str in (res.stdout or "").splitlines():
        _try_kill_pid(pid_str)


def _read_netsim_grpc_port(ini_path: str) -> int:
    try:
        with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError as exc:
        logger.debug("Ignored error reading %s: %s", ini_path, exc)
        return 0
    for line in lines:
        if "=" not in line:
            continue
        k, v = [p.strip() for p in line.split("=", 1)]
        if k == "grpc.port" and v.isdigit():
            return int(v)
    return 0


def _is_local_port_listening(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError as exc:
        logger.debug("Ignored socket check error on port %d: %s", port, exc)
        return False


def _clean_netsim_files_in_dir(dir_path: str, force: bool) -> None:
    for ini_name in ("netsim.ini", "netsim.ini.lock"):
        ini_path = os.path.join(dir_path, ini_name)
        if not os.path.isfile(ini_path):
            continue
        if ini_name == "netsim.ini" and not force:
            port = _read_netsim_grpc_port(ini_path)
            if _is_local_port_listening(port):
                continue
        try:
            os.remove(ini_path)
        except OSError as exc:
            logger.debug("Ignored error removing %s: %s", ini_path, exc)


def _cleanup_stale_netsim(
    force: bool = False,
    process_runner: Optional[ProcessRunner] = None,
) -> None:
    """Removes stale `netsim.ini` discovery files (and kills orphaned `netsimd` when `force=True`)."""
    if force:
        _kill_orphaned_netsimd(process_runner or SubprocessRunner())

    candidate_dirs = (
        os.environ.get("TMPDIR") or "",
        tempfile.gettempdir(),
        "/tmp",
        os.path.expanduser("~/Library/Caches/TemporaryItems"),
        os.environ.get("ANDROID_EMULATOR_DISCOVERY_DIR") or "",
        os.environ.get("XDG_RUNTIME_DIR") or "",
    )
    seen_dirs = set()
    for d in candidate_dirs:
        if not d or not os.path.isdir(d):
            continue
        real_d = os.path.realpath(d)
        if real_d in seen_dirs:
            continue
        seen_dirs.add(real_d)
        _clean_netsim_files_in_dir(d, force=force)


def _create_secure_temp_file(prefix: str, suffix: str) -> str:
    """Creates a unique temporary file via `tempfile.mkstemp` and returns its path."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def _parse_emulator_boot_completed_ms(log_path: Optional[str]) -> Optional[float]:
    """Parses the emulator's internal `Boot completed in <N> ms` log line from `log_path`."""
    if not log_path or not os.path.isfile(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        matches = re.findall(r"Boot completed in\s+(\d+)\s*ms", content)
        if matches:
            return float(matches[0])
    except OSError as exc:
        logger.debug(
            "Ignored error in _parse_emulator_boot_completed_ms: %s", exc
        )
    return None


def _kill_emulator_process_tree(
    proc: subprocess.Popen,
    serial: str,
    adb_bin: str = "adb",
    process_runner: Optional[ProcessRunner] = None,
) -> None:
    """Terminates the emulator launcher and all child QEMU/crashpad/netsimd processes."""
    emu_pid = find_emulator_pid(serial)
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception as exc:
        logger.debug("Ignored killpg error in _kill_emulator_process_tree: %s", exc)
    try:
        proc.kill()
        proc.wait(timeout=5.0)
    except Exception as exc:
        logger.debug("Ignored proc.kill error in _kill_emulator_process_tree: %s", exc)
    if emu_pid and emu_pid != proc.pid:
        try:
            os.kill(emu_pid, signal.SIGKILL)
        except Exception as exc:
            logger.debug("Ignored os.kill error in _kill_emulator_process_tree: %s", exc)
    _cleanup_stale_netsim(force=True, process_runner=process_runner)
    _adb_cmd(
        serial,
        ["disconnect"],
        adb_bin=adb_bin,
        timeout=3.0,
        process_runner=process_runner,
    )


def _read_ini_key_values(cfg_path: str) -> Optional[Dict[str, str]]:
    try:
        with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_lines = f.readlines()
    except OSError as exc:
        logger.debug("Ignored error reading %s: %s", cfg_path, exc)
        return None
    kv: Dict[str, str] = {}
    for raw_line in raw_lines:
        line = raw_line.strip()
        if "=" in line:
            k, v = [p.strip() for p in line.split("=", 1)]
            kv[k] = v
    return kv


def _parse_avd_config_ini(
    cfg_path: str,
    default_ram: int,
    default_cores: int,
    default_disk: str,
) -> Optional[Tuple[Optional[str], int, int, str, Optional[str]]]:
    kv = _read_ini_key_values(cfg_path)
    if kv is None:
        return None
    sysdir = kv.get("image.sysdir.1", "")
    cand_sysimg = (
        find_actual_sysimg_dir(sysdir)
        if sysdir and os.path.isdir(sysdir)
        else None
    )
    ram_str = kv.get("hw.ramSize", "")
    cand_ram = int(ram_str) if ram_str.isdigit() else default_ram
    cores_str = kv.get("hw.cpu.ncore", "")
    cand_cores = int(cores_str) if cores_str.isdigit() else default_cores
    cand_disk = kv.get("disk.dataPartition.size") or default_disk
    arch_raw = kv.get("hw.cpu.arch") or kv.get("abi.type") or ""
    cand_arch: Optional[str] = None
    if arch_raw:
        cand_arch = (
            "arm64" if ("arm" in arch_raw or "aarch64" in arch_raw) else "x86_64"
        )
    return cand_sysimg, cand_ram, cand_cores, cand_disk, cand_arch


def _discover_candidate_avds(
    source_avd: Optional[str], avd_root: str
) -> List[str]:
    if source_avd:
        return [source_avd]
    if not os.path.isdir(avd_root):
        return []
    ini_files = sorted(glob.glob(os.path.join(avd_root, "*.ini")))
    return [
        os.path.splitext(os.path.basename(ini_p))[0]
        for ini_p in ini_files
        if not os.path.splitext(os.path.basename(ini_p))[0].startswith(
            "perf-clean-"
        )
    ]


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
    candidate_avds = _discover_candidate_avds(source_avd, avd_root)

    for cand_avd in candidate_avds:
        _, existing_avd_dir = get_avd_paths(cand_avd, avd_root)
        cfg_path = os.path.join(existing_avd_dir, "config.ini")
        if not os.path.isfile(cfg_path):
            continue
        parsed = _parse_avd_config_ini(cfg_path, ram, cores, disk_size)
        if parsed is None:
            continue
        cand_sysimg, cand_ram, cand_cores, cand_disk, cand_arch = parsed

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


class BenchmarkSessionTemplate(ABC):
    """Template Method defining the skeleton of a benchmark session lifecycle."""

    def run(self) -> Dict[str, Any]:
        """Executes the benchmark session lifecycle."""
        self.prepare_session()
        try:
            raw_results = self.execute_session_runs()
            report = self.aggregate_session_results(raw_results)
            self.record_session_history(report)
            return report
        finally:
            self.teardown_session()

    def prepare_session(self) -> None:
        """Hook to validate configuration and prepare session state."""

    @abstractmethod
    def execute_session_runs(self) -> Any:
        """Executes one or more benchmark passes and returns raw results."""

    @abstractmethod
    def aggregate_session_results(self, raw_results: Any) -> Dict[str, Any]:
        """Aggregates raw pass results into a normalized `PerfRunReport` dict."""

    def record_session_history(self, report: Dict[str, Any]) -> None:
        """Hook to persist session report to history if applicable."""

    def teardown_session(self) -> None:
        """Hook to clean up any session-level resources."""


class LiveDeviceBenchmarkSession(BenchmarkSessionTemplate):
    """Template Method implementation for running a benchmark suite against an active ADB device."""

    def __init__(
        self,
        serial: str,
        plan: BenchmarkPlan,
        label: str = "live-device",
        adb_bin: str = "adb",
        boot_metrics: Optional[Dict[str, Any]] = None,
        grpc_port: Optional[int] = None,
        registry: Optional[WorkloadStrategyRegistry] = None,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        self.serial = serial
        self.plan = plan
        self.label = label
        self.adb_bin = adb_bin
        self.boot_metrics = boot_metrics
        self.grpc_port = grpc_port
        self.registry = registry or create_default_workload_registry()
        self.process_runner = process_runner
        self.adb = AdbDevice(
            serial=serial, adb_bin=adb_bin, process_runner=process_runner
        )
        self.emu_pid: Optional[int] = None
        self.guest_state: Dict[str, Any] = {}
        self.probe_session: Optional[PerfProbeSession] = None

    def prepare_session(self) -> None:
        self.emu_pid = find_emulator_pid(self.serial)
        self.guest_state = wait_for_guest_and_host_settled(
            serial=self.serial,
            emu_pid=self.emu_pid,
            settle_seconds=self.plan.settle_seconds,
            adb_bin=self.adb_bin,
            process_runner=self.process_runner,
        )
        if any(w in PROBE_WORKLOADS for w in self.plan.workloads):
            self.probe_session = PerfProbeSession(
                serial=self.serial,
                adb_fn=self.adb.as_adb_fn(default_timeout=60.0),
                emu_pid=self.emu_pid,
                grpc_port=self.grpc_port,
            )

    def execute_session_runs(self) -> Dict[str, Dict[str, Any]]:
        tests_out: Dict[str, Dict[str, Any]] = {}
        if self.boot_metrics and "cold_boot" in self.plan.workloads:
            tests_out["cold_boot"] = dict(self.boot_metrics)

        ctx = WorkloadContext(
            adb=self.adb,
            emu_pid=self.emu_pid,
            iterations=self.plan.iterations,
            warmup=self.plan.warmup,
            profiles=self.plan.profiles,
            probe_session=self.probe_session,
            boot_metrics=self.boot_metrics,
        )

        for workload_name in self.plan.workloads:
            try:
                strategy = self.registry.resolve(workload_name)
                tests_out[workload_name] = strategy.execute(workload_name, ctx)
            except Exception as exc:
                logger.warning(
                    "Workload '%s' on %s failed: %s",
                    workload_name,
                    self.serial,
                    exc,
                )
                tests_out[workload_name] = {
                    "n": 0,
                    "status": "broken",
                    "error": str(exc),
                }
        return tests_out

    def aggregate_session_results(
        self, raw_results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        insights = generate_diagnostic_insights(raw_results, label=self.label)
        return {
            "schema_version": 1,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "label": self.label,
            "serial": self.serial,
            "emulator_pid": self.emu_pid,
            "nested_virt": is_nested_virtualization(),
            "guest_state": self.guest_state,
            "tests": raw_results,
            "insights": insights,
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
    registry: Optional[WorkloadStrategyRegistry] = None,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Runs the specified workloads against an active emulator `serial`."""
    plan = BenchmarkPlan(
        suites=[],
        workloads=workloads,
        iterations=iterations,
        warmup=warmup,
        settle_seconds=settle_seconds,
        profiles=profiles,
    )
    session = LiveDeviceBenchmarkSession(
        serial=serial,
        plan=plan,
        label=label,
        adb_bin=adb_bin,
        boot_metrics=boot_metrics,
        grpc_port=grpc_port,
        registry=registry,
        process_runner=process_runner,
    )
    return session.run()


def _apply_carry_over_fields(
    summary: Dict[str, Any], t_list: List[Dict[str, Any]]
) -> None:
    for key in CARRY_OVER_METRIC_KEYS:
        vals = [
            float(x[key]) for x in t_list if key in x and x[key] is not None
        ]
        if vals:
            summary[key] = round(statistics.median(vals), 3)
    for dict_key in CARRY_OVER_DICT_KEYS:
        if t_list[-1].get(dict_key):
            summary[dict_key] = t_list[-1][dict_key]


def _merge_multi_boot_tests(
    accumulated_tests: Dict[str, List[Dict[str, Any]]],
    accumulated_samples: Dict[str, List[Dict[str, float]]],
) -> Dict[str, Dict[str, Any]]:
    merged_tests: Dict[str, Dict[str, Any]] = {}
    for t_name, t_list in accumulated_tests.items():
        if t_name in accumulated_samples and accumulated_samples[t_name]:
            summary = summarize_samples(accumulated_samples[t_name])
            _apply_carry_over_fields(summary, t_list)
            merged_tests[t_name] = summary
        else:
            merged_tests[t_name] = t_list[-1]
    return merged_tests


class ManagedEmulatorBenchmarkSession(BenchmarkSessionTemplate):
    """Template Method implementation for multi-boot managed emulator benchmarks."""

    def __init__(
        self,
        config: ManagedBenchmarkConfig,
        registry: Optional[WorkloadStrategyRegistry] = None,
        process_runner: Optional[ProcessRunner] = None,
        record_history: bool = True,
    ) -> None:
        self.config = config
        self.registry = registry or create_default_workload_registry()
        self.process_runner = process_runner
        self.record_history = record_history
        self.template_info: Optional[Dict[str, Any]] = None
        self.effective_avd_label: str = ""
        self.resolved_avd_name: Optional[str] = None

    def prepare_session(self) -> None:
        target = self.config.launch.target
        if not target.emulator_bin or not target.emulator_dir:
            raise ValueError(
                f"Target '{target.label}' does not have a valid emulator binary directory."
            )

        avd_name = self.config.launch.avd_name
        if avd_name and avd_name.strip().lower() == "auto":
            avd_name = None

        if self.config.launch.clean_avd:
            self.template_info = _resolve_perf_avd_template(
                avd_name=avd_name, sysimg_dir=self.config.launch.sysimg_dir
            )
            self.effective_avd_label = (
                f"{self.template_info['template_avd']} (clean-isolated)"
            )
            self.resolved_avd_name = avd_name
        else:
            if not avd_name:
                avd_root = get_default_avd_root()
                ini_files = sorted(glob.glob(os.path.join(avd_root, "*.ini")))
                if not ini_files:
                    raise RuntimeError(
                        "No existing AVD found (`~/.android/avd`). Create one with `emu-dev-cli create` or pass `--avd`."
                    )
                avd_name = os.path.splitext(os.path.basename(ini_files[0]))[0]
            self.resolved_avd_name = avd_name
            self.effective_avd_label = avd_name

    def _provision_boot_avd(
        self, slug: str, boot_idx: int
    ) -> Tuple[str, Optional[str], Optional[str]]:
        if self.config.launch.clean_avd and self.template_info is not None:
            boot_avd_name = f"perf-clean-{slug}-b{boot_idx + 1}-{os.getpid()}"
            created = create_single_avd(
                avd_name=boot_avd_name,
                raw_sysimg_dir=self.template_info["sysimg_dir"],
                ram=self.template_info["ram"],
                cores=self.template_info["cores"],
                disk_size=self.template_info["disk_size"],
                force=True,
            )
            return boot_avd_name, created["ini_file"], created["avd_dir"]

        boot_avd_name = str(self.resolved_avd_name)
        avd_dir_path = os.path.join(
            get_default_avd_root(), f"{boot_avd_name}.avd"
        )
        qemu_ver_file = os.path.join(avd_dir_path, "qemu-version.txt")
        if os.path.isfile(qemu_ver_file):
            try:
                os.remove(qemu_ver_file)
            except OSError as exc:
                logger.debug("Ignored error removing %s: %s", qemu_ver_file, exc)
        return boot_avd_name, None, None

    def _build_emulator_launch_cmd(
        self, boot_avd_name: str, port: int, grpc_port: int, boot_idx: int
    ) -> List[str]:
        target = self.config.launch.target
        plan = self.config.plan
        needs_snapshot = any(
            w in ("snapshot_save", "snapshot_load") for w in plan.workloads
        )
        assert target.emulator_bin is not None
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
        if not needs_snapshot:
            cmd.append("-no-snapshot-save")
        if self.config.launch.trace_path or (
            plan.profiles and "perfetto" in plan.profiles
        ):
            pf_out = self.config.launch.trace_path or _create_secure_temp_file(
                prefix=f"emu_perf_{target.build_id or 'trace'}_b{boot_idx + 1}_",
                suffix=".pftrace",
            )
            cmd.extend(["--trace", pf_out])
        return cmd

    def _teardown_single_boot(
        self,
        proc: subprocess.Popen,
        serial: str,
        ephemeral_ini: Optional[str],
        ephemeral_dir: Optional[str],
        log_path: Optional[str] = None,
    ) -> None:
        adb_bin = self.config.launch.adb_bin
        if not self.config.launch.clean_avd:
            _adb_cmd(
                serial,
                ["emu", "kill"],
                adb_bin=adb_bin,
                timeout=10.0,
                process_runner=self.process_runner,
            )
            try:
                proc.wait(timeout=10.0)
            except Exception as exc:
                logger.debug("Ignored error waiting on proc shutdown: %s", exc)
        _kill_emulator_process_tree(
            proc,
            serial,
            adb_bin=adb_bin,
            process_runner=self.process_runner,
        )
        if ephemeral_ini and os.path.isfile(ephemeral_ini):
            try:
                os.unlink(ephemeral_ini)
            except OSError as exc:
                logger.debug("Ignored error unlinking %s: %s", ephemeral_ini, exc)
        if ephemeral_dir and os.path.isdir(ephemeral_dir):
            shutil.rmtree(ephemeral_dir, ignore_errors=True)
        if log_path and os.path.isfile(log_path):
            try:
                os.unlink(log_path)
            except OSError as exc:
                logger.debug("Ignored error unlinking %s: %s", log_path, exc)

    def _collect_boot_metrics(
        self,
        serial: str,
        proc: subprocess.Popen,
        log_path: str,
        t_boot_start: float,
    ) -> Dict[str, Any]:
        host_wall_boot_ms = (time.perf_counter() - t_boot_start) * 1000.0
        internal_boot_ms = _parse_emulator_boot_completed_ms(log_path)
        cold_boot_ms = (
            internal_boot_ms
            if internal_boot_ms is not None and internal_boot_ms > 0
            else host_wall_boot_ms
        )
        emu_pid_boot = find_emulator_pid(serial) or proc.pid
        snap_boot = take_proc_snapshot(emu_pid_boot) if emu_pid_boot else None
        boot_rss = snap_boot.rss_mib if snap_boot else 0.0
        return {
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

    @staticmethod
    def _record_boot_report(
        boot_rep: Dict[str, Any],
        accumulated_tests: Dict[str, List[Dict[str, Any]]],
        accumulated_samples: Dict[str, List[Dict[str, float]]],
    ) -> None:
        for t_name, t_data in (boot_rep.get("tests") or {}).items():
            accumulated_tests.setdefault(t_name, []).append(t_data)
            if "samples" in t_data and isinstance(t_data["samples"], list):
                accumulated_samples.setdefault(t_name, []).extend(
                    t_data["samples"]
                )

    def _run_single_boot_cycle(
        self,
        slug: str,
        boot_idx: int,
        accumulated_tests: Dict[str, List[Dict[str, Any]]],
        accumulated_samples: Dict[str, List[Dict[str, float]]],
    ) -> None:
        target = self.config.launch.target
        plan = self.config.plan
        adb_bin = self.config.launch.adb_bin
        assert target.emulator_dir is not None

        boot_avd_name, ephemeral_ini, ephemeral_dir = self._provision_boot_avd(
            slug, boot_idx
        )
        port = _find_free_console_port(5560 + boot_idx * 2)
        grpc_port = _find_free_console_port(8554 + boot_idx * 2)
        serial = f"emulator-{port}"
        env = prepare_environment(target.emulator_dir)
        cmd = self._build_emulator_launch_cmd(
            boot_avd_name, port, grpc_port, boot_idx
        )

        log_path = _create_secure_temp_file(
            prefix=f"emu_perf_{slug}_b{boot_idx + 1}_",
            suffix=".log",
        )

        t_boot_start = time.perf_counter()
        _ensure_valid_console_auth_token()
        _cleanup_stale_netsim(force=False, process_runner=self.process_runner)
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
                process_runner=self.process_runner,
            )
            boot_info = self._collect_boot_metrics(
                serial, proc, log_path, t_boot_start
            )
            boot_rep = run_suite_against_device(
                serial=serial,
                workloads=plan.workloads,
                iterations=plan.iterations,
                warmup=plan.warmup,
                settle_seconds=plan.settle_seconds,
                profiles=plan.profiles,
                label=target.label,
                adb_bin=adb_bin,
                boot_metrics=boot_info,
                grpc_port=grpc_port,
                registry=self.registry,
                process_runner=self.process_runner,
            )
            self._record_boot_report(
                boot_rep, accumulated_tests, accumulated_samples
            )
        finally:
            self._teardown_single_boot(
                proc, serial, ephemeral_ini, ephemeral_dir, log_path=log_path
            )

    def execute_session_runs(
        self,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, float]]]]:
        target = self.config.launch.target
        plan = self.config.plan
        accumulated_samples: Dict[str, List[Dict[str, float]]] = {}
        accumulated_tests: Dict[str, List[Dict[str, Any]]] = {}
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", target.label)[:24]
        total_boots = max(1, self.config.launch.boots, plan.boots)

        for boot_idx in range(total_boots):
            self._run_single_boot_cycle(
                slug, boot_idx, accumulated_tests, accumulated_samples
            )

        return accumulated_tests, accumulated_samples

    def aggregate_session_results(
        self,
        raw_results: Tuple[
            Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, float]]]
        ],
    ) -> Dict[str, Any]:
        accumulated_tests, accumulated_samples = raw_results
        merged_tests = _merge_multi_boot_tests(
            accumulated_tests, accumulated_samples
        )
        target = self.config.launch.target
        plan = self.config.plan
        return {
            "schema_version": 1,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "label": target.label,
            "build_id": target.build_id,
            "branch": target.branch,
            "engine": target.engine,
            "pkg_revision": target.pkg_revision,
            "emulator_dir": target.emulator_dir,
            "avd": self.effective_avd_label,
            "boots": max(1, self.config.launch.boots, plan.boots),
            "nested_virt": is_nested_virtualization(),
            "suites": plan.suites,
            "tests": merged_tests,
            "insights": generate_diagnostic_insights(
                merged_tests, label=target.label
            ),
        }

    def record_session_history(self, report: Dict[str, Any]) -> None:
        if self.record_history:
            append_perf_history(report)


def run_managed_emulator_benchmark(
    target: Optional[ResolvedPerfTarget] = None,
    avd_name: Optional[str] = None,
    suites: Optional[List[str]] = None,
    workloads: Optional[List[str]] = None,
    iterations: int = 5,
    warmup: int = 1,
    boots: int = 1,
    settle_seconds: float = 30.0,
    profiles: Optional[List[str]] = None,
    trace_path: Optional[str] = None,
    adb_bin: str = "adb",
    clean_avd: bool = True,
    sysimg_dir: Optional[str] = None,
    *,
    config: Optional[ManagedBenchmarkConfig] = None,
    registry: Optional[WorkloadStrategyRegistry] = None,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Cold-boots a managed emulator binary `boots` times, runs workloads, and aggregates results.

    Accepts either a `ManagedBenchmarkConfig` (via `config=` or as the first positional
    argument) or the legacy 13-parameter signature for 100% backward compatibility.
    """
    if isinstance(target, ManagedBenchmarkConfig):
        resolved_config = target
    elif config is not None:
        resolved_config = config
    else:
        if target is None:
            raise ValueError("`target` or `config` is required.")
        resolved_config = ManagedBenchmarkConfig(
            launch=EmulatorLaunchConfig(
                target=target,
                avd_name=avd_name,
                boots=boots,
                clean_avd=clean_avd,
                sysimg_dir=sysimg_dir,
                trace_path=trace_path,
                adb_bin=adb_bin,
            ),
            plan=BenchmarkPlan(
                suites=suites or [],
                workloads=workloads or [],
                iterations=iterations,
                warmup=warmup,
                boots=boots,
                settle_seconds=settle_seconds,
                profiles=profiles,
            ),
        )
    session = ManagedEmulatorBenchmarkSession(
        config=resolved_config,
        registry=registry,
        process_runner=process_runner,
    )
    return session.run()
