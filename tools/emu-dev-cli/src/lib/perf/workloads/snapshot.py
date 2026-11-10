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

"""Quickboot snapshot save and load workload strategy (`snapshot_save`, `snapshot_load`)."""

from dataclasses import dataclass
import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf.workloads.base import MeasuredWorkloadTemplate, WorkloadContext

LOGGER = logging.getLogger(__name__)

SNAPSHOT_WORKLOAD_NAMES = frozenset({"snapshot_save", "snapshot_load"})
SNAPSHOT_BENCH_TAG: str = "emu_perf_bench_snap"
CONSOLE_AUTH_TOKEN_PATH: str = "~/.emulator_console_auth_token"
MIN_CONSOLE_AUTH_TOKEN_BYTES: int = 12
DEFAULT_SNAPSHOT_ITERATIONS: int = 3
SNAPSHOT_PRE_SAVE_SETTLE_SEC: float = 0.5
SNAPSHOT_CMD_TIMEOUT_SEC: float = 60.0
MS_PER_SECOND: float = 1000.0


def _ensure_valid_console_auth_token() -> None:
    """Ensures `~/.emulator_console_auth_token` is not a malformed short (<12 byte) token
    that causes the emulator console to reject authentication and hide `avd` commands.
    """
    token_path = os.path.expanduser(CONSOLE_AUTH_TOKEN_PATH)
    if not os.path.isfile(token_path):
        return
    try:
        with open(token_path, "rb") as f:
            raw = f.read().strip()
        if 0 < len(raw) < MIN_CONSOLE_AUTH_TOKEN_BYTES:
            with open(token_path, "wb") as f:
                f.write(b"")
    except OSError as exc:
        LOGGER.debug("Ignored error in _ensure_valid_console_auth_token: %s", exc)


@dataclass
class _SnapshotState:
    """Prepared state for `snapshot_save` or `snapshot_load` execution."""

    action: str
    snap_tag: str = SNAPSHOT_BENCH_TAG


class SnapshotWorkloadStrategy(MeasuredWorkloadTemplate):
    """Strategy measuring `adb emu avd snapshot save` and `load` latency."""

    def __init__(self, *, cap_iterations_at: Optional[int] = DEFAULT_SNAPSHOT_ITERATIONS) -> None:
        self._cap_iterations_at = cap_iterations_at

    def matches(self, workload_name: str) -> bool:
        return workload_name in SNAPSHOT_WORKLOAD_NAMES

    def setup_workload(
        self, workload_name: str, ctx: WorkloadContext
    ) -> _SnapshotState:
        _ensure_valid_console_auth_token()
        action = "save" if workload_name == "snapshot_save" else "load"
        state = _SnapshotState(action=action, snap_tag=SNAPSHOT_BENCH_TAG)

        # Flush dirty pages so snapshot_save / snapshot_load measure a clean guest state
        ctx.adb.drop_caches(timeout=15.0)
        time.sleep(SNAPSHOT_PRE_SAVE_SETTLE_SEC)

        # Always perform 1 untimed pre-save
        prep = ctx.adb.emu(
            ["avd", "snapshot", "save", state.snap_tag],
            timeout=SNAPSHOT_CMD_TIMEOUT_SEC,
        )
        if prep.returncode != 0 or "KO:" in prep.stdout:
            raise RuntimeError(
                f"Snapshot pre-save failed on {ctx.serial}: "
                f"{prep.stdout.strip()} {prep.stderr.strip()}".strip()
                + ". Verify ~/.emulator_console_auth_token and snapshot support."
            )

        # For `load`, also perform 1 untimed warmup `load` so QCOW2 L2 cluster tables
        # and host memory mappings are warm before timed iterations begin.
        if action == "load":
            warm = ctx.adb.emu(
                ["avd", "snapshot", "load", state.snap_tag],
                timeout=SNAPSHOT_CMD_TIMEOUT_SEC,
            )
            if warm.returncode == 0 and "KO:" not in warm.stdout:
                try:
                    ctx.adb.wait_for_device(timeout=15.0)
                except Exception as exc:
                    LOGGER.debug(
                        "Ignored error waiting for device after warmup snapshot load: %s",
                        exc,
                    )
        return state

    def warmup_count(
        self, workload_name: str, ctx: WorkloadContext, state: _SnapshotState
    ) -> int:
        del workload_name, ctx, state
        # Pre-save (and warmup load) are already handled in setup_workload
        return 0

    def target_iterations(
        self, workload_name: str, ctx: WorkloadContext, state: _SnapshotState
    ) -> int:
        del workload_name, state
        iters = max(1, ctx.iterations)
        if self._cap_iterations_at is not None:
            return min(iters, self._cap_iterations_at)
        return iters

    def allow_adaptive_extra_iterations(
        self, workload_name: str, ctx: WorkloadContext, state: _SnapshotState
    ) -> bool:
        del workload_name, ctx, state
        return False

    def run_single_iteration(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: _SnapshotState,
        *,
        is_warmup: bool = False,
    ) -> Dict[str, float]:
        del workload_name, is_warmup
        t0 = time.perf_counter()
        res = ctx.adb.emu(
            ["avd", "snapshot", state.action, state.snap_tag],
            timeout=SNAPSHOT_CMD_TIMEOUT_SEC,
        )
        elapsed_ms = (time.perf_counter() - t0) * MS_PER_SECOND
        if res.returncode != 0 or "KO:" in res.stdout:
            raise RuntimeError(
                f"Snapshot {state.action} failed on {ctx.serial}: "
                f"{res.stdout.strip()} {res.stderr.strip()}".strip()
                + ". Verify snapshot configuration and disk space."
            )
        if state.action == "load":
            try:
                ctx.adb.wait_for_device(timeout=15.0)
            except Exception as exc:
                LOGGER.debug(
                    "Ignored error waiting for device after snapshot load: %s",
                    exc,
                )
        return {"duration_ms": round(elapsed_ms, 2)}

    def teardown_workload(
        self,
        workload_name: str,
        ctx: WorkloadContext,
        state: Optional[_SnapshotState],
    ) -> None:
        del workload_name
        if state is not None and state.action == "load":
            ctx.adb.emu(
                ["avd", "snapshot", "delete", state.snap_tag],
                timeout=15.0,
            )


def run_snapshot_workload(
    workload_name: str,
    serial: str,
    emu_pid: Optional[int],
    iterations: int = DEFAULT_SNAPSHOT_ITERATIONS,
    profiles: Optional[List[str]] = None,
    adb_bin: str = DEFAULT_ADB_BIN,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Measures `adb emu avd snapshot save` or `load` latency and host CPU cost."""
    return SnapshotWorkloadStrategy(cap_iterations_at=None).execute_for_device(
        workload_name,
        serial,
        emu_pid,
        iterations=iterations,
        warmup=0,
        profiles=profiles,
        adb_bin=adb_bin,
        process_runner=process_runner,
    )
