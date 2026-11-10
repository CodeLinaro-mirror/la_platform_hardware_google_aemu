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

"""ADB file and directory transfer workload strategy (`push_*` and `pull_*`)."""

from collections.abc import Callable
from dataclasses import dataclass
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

from core.process import ProcessResult, ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import (
    ADB_SPEED_RE,
    DEFAULT_ADB_BIN,
    DEFAULT_ITERATIONS,
    DEFAULT_WARMUP,
    MIB,
)
from lib.perf.workloads.base import MeasuredWorkloadTemplate, WorkloadContext

SINGLE_TRANSFER_RE = re.compile(r"^(push|pull)_(\d+)MiB_(tmpfs|disk)$")
DIR_TRANSFER_RE = re.compile(r"^(push|pull)_(\d+)x(\d+)MiB_dir$")

SHM_ROOT_PATH: str = "/dev/shm"
REMOTE_TMPFS_DIR: str = "/tmp"
REMOTE_DISK_DIR: str = "/data/local/tmp"
REMOTE_BENCH_DIR: str = "/data/local/tmp/emu_perf_dir"
CLEANUP_REMOTE_GLOB_CMD: str = (
    "rm -rf /tmp/emu_perf_* /data/local/tmp/emu_perf_*"
)
BYTES_PER_DECIMAL_MB: float = 1e6


def _create_host_payload_file(directory: str, size_mib: int) -> str:
    """Creates a host binary payload file of `size_mib` MiB in `directory`."""
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
    """Creates a host directory containing `count` binary files of `file_mib` MiB each."""
    dir_path = os.path.join(directory, f"dir_{count}x{file_mib}MiB")
    os.makedirs(dir_path, exist_ok=True)
    chunk = os.urandom(file_mib * MIB)
    for idx in range(count):
        fp = os.path.join(dir_path, f"file_{idx:03d}.bin")
        if not os.path.isfile(fp) or os.path.getsize(fp) != file_mib * MIB:
            with open(fp, "wb") as f:
                f.write(chunk)
    return dir_path


@dataclass
class _AdbTransferState:
    """Per-workload state prepared during `setup_workload`."""

    work_dir: str
    size_mib: int
    total_bytes: int
    is_disk_backed: bool
    step_fn: Callable[[], ProcessResult]


class AdbTransferWorkloadStrategy(MeasuredWorkloadTemplate):
    """Strategy executing single-file and multi-file ADB push/pull benchmarks."""

    def matches(self, workload_name: str) -> bool:
        return bool(
            SINGLE_TRANSFER_RE.match(workload_name)
            or DIR_TRANSFER_RE.match(workload_name)
        )

    def setup_workload(
        self, workload_name: str, ctx: WorkloadContext
    ) -> _AdbTransferState:
        shm_root = (
            SHM_ROOT_PATH
            if os.path.isdir(SHM_ROOT_PATH)
            else tempfile.gettempdir()
        )
        work_dir = tempfile.mkdtemp(prefix="emu_perf_adb_", dir=shm_root)
        try:
            m_single = SINGLE_TRANSFER_RE.match(workload_name)
            if m_single:
                return self._setup_single_file_transfer(
                    m_single, work_dir, ctx
                )
            m_dir = DIR_TRANSFER_RE.match(workload_name)
            if m_dir:
                return self._setup_dir_transfer(m_dir, work_dir, ctx)
            raise ValueError(
                f"Unsupported ADB workload '{workload_name}'. "
                "Expected '<push|pull>_<N>MiB_<tmpfs|disk>' or '<push|pull>_<N>x<M>MiB_dir'."
            )
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def _setup_single_file_transfer(
        self,
        match: re.Match[str],
        work_dir: str,
        ctx: WorkloadContext,
    ) -> _AdbTransferState:
        direction, size_str, storage = match.groups()
        size_mib = int(size_str)
        total_bytes = size_mib * MIB
        remote_base = (
            REMOTE_TMPFS_DIR if storage == "tmpfs" else REMOTE_DISK_DIR
        )
        remote_path = f"{remote_base}/emu_perf_{size_mib}m.bin"
        host_src = _create_host_payload_file(work_dir, size_mib)
        host_dst = os.path.join(work_dir, "pulled.bin")

        if direction == "pull":
            ctx.adb.push(host_src, remote_path)

        def _step() -> ProcessResult:
            if direction == "push":
                return ctx.adb.push(host_src, remote_path)
            return ctx.adb.pull(remote_path, host_dst)

        return _AdbTransferState(
            work_dir=work_dir,
            size_mib=size_mib,
            total_bytes=total_bytes,
            is_disk_backed=(storage == "disk"),
            step_fn=_step,
        )

    def _setup_dir_transfer(
        self,
        match: re.Match[str],
        work_dir: str,
        ctx: WorkloadContext,
    ) -> _AdbTransferState:
        direction, count_str, fsize_str = match.groups()
        count = int(count_str)
        fsize_mib = int(fsize_str)
        size_mib = count * fsize_mib
        total_bytes = size_mib * MIB
        host_src_dir = _create_host_payload_dir(work_dir, count, fsize_mib)
        host_dst_dir = os.path.join(work_dir, "pulled_dir")

        ctx.adb.shell(f"rm -rf {REMOTE_BENCH_DIR}")
        if direction == "pull":
            ctx.adb.push(host_src_dir, REMOTE_BENCH_DIR)

        def _step() -> ProcessResult:
            if direction == "push":
                ctx.adb.shell(f"rm -rf {REMOTE_BENCH_DIR}")
                return ctx.adb.push(host_src_dir, REMOTE_BENCH_DIR)
            if os.path.exists(host_dst_dir):
                shutil.rmtree(host_dst_dir, ignore_errors=True)
            return ctx.adb.pull(REMOTE_BENCH_DIR, host_dst_dir)

        return _AdbTransferState(
            work_dir=work_dir,
            size_mib=size_mib,
            total_bytes=total_bytes,
            is_disk_backed=True,
            step_fn=_step,
        )

    def should_drop_caches_before_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: _AdbTransferState,
    ) -> bool:
        del workload_name, ctx
        return state.is_disk_backed

    def total_mib_per_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: _AdbTransferState,
    ) -> float:
        del workload_name, ctx
        return float(state.size_mib)

    def run_single_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: _AdbTransferState,
        *,
        is_warmup: bool = False,
    ) -> Dict[str, float]:
        del ctx
        t0 = time.perf_counter()
        res = state.step_fn()
        elapsed = time.perf_counter() - t0
        if is_warmup:
            return {}
        if res.returncode != 0:
            detail = res.stderr.strip() or res.stdout.strip() or f"exit code {res.returncode}"
            raise RuntimeError(
                f"ADB workload '{workload_name}' failed: {detail}. "
                "Verify the emulator is connected (`adb devices`) and has sufficient storage."
            )
        combined_out = f"{res.stdout}\n{res.stderr}"
        m_spd = ADB_SPEED_RE.search(combined_out)
        adb_mb_s = float(m_spd.group(1)) if m_spd else 0.0
        wall_mib_s = (
            (state.total_bytes / MIB) / elapsed if elapsed > 0 else 0.0
        )
        wall_mb_s = (
            (state.total_bytes / BYTES_PER_DECIMAL_MB) / elapsed
            if elapsed > 0
            else 0.0
        )
        return {
            "duration_s": round(elapsed, 5),
            "wall_mib_s": round(wall_mib_s, 2),
            "wall_mb_s": round(wall_mb_s, 2),
            "adb_mb_s": round(adb_mb_s, 2),
        }

    def teardown_workload(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: Optional[_AdbTransferState],
    ) -> None:
        del workload_name
        if state is None:
            return
        try:
            ctx.adb.shell(CLEANUP_REMOTE_GLOB_CMD, timeout=15.0)
        finally:
            shutil.rmtree(state.work_dir, ignore_errors=True)


def run_adb_transfer_workload(
    workload_name: str,
    serial: str,
    emu_pid: Optional[int],
    iterations: int = DEFAULT_ITERATIONS,
    warmup: int = DEFAULT_WARMUP,
    profiles: Optional[List[str]] = None,
    adb_bin: str = DEFAULT_ADB_BIN,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Executes a single ADB transfer workload (`push_*` or `pull_*`) with warmup and host telemetry."""
    return AdbTransferWorkloadStrategy().execute_for_device(
        workload_name,
        serial,
        emu_pid,
        iterations=iterations,
        warmup=warmup,
        profiles=profiles,
        adb_bin=adb_bin,
        process_runner=process_runner,
    )
