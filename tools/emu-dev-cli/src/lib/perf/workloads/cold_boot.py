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

"""Cold boot duration and guest boot phase breakdown workload strategy (`cold_boot`)."""

from typing import Any, Dict, Optional

from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf.workloads.base import WorkloadContext, WorkloadStrategy

COLD_BOOT_WORKLOAD_NAME: str = "cold_boot"


class ColdBootWorkloadStrategy(WorkloadStrategy):
    """Strategy collecting guest `ro.boottime.init` and `ro.boottime.zygote` metrics and merging host cold boot duration."""

    def matches(self, workload_name: str) -> bool:
        return workload_name == COLD_BOOT_WORKLOAD_NAME

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        del workload_name
        init_str = ctx.adb.getprop("ro.boottime.init")
        zyg_str = ctx.adb.getprop("ro.boottime.zygote")
        init_ns = int(init_str) if init_str.isdigit() else 0
        zyg_ns = int(zyg_str) if zyg_str.isdigit() else 0

        if ctx.boot_metrics:
            merged = dict(ctx.boot_metrics)
            merged["ro_boottime_init_ns"] = init_ns
            merged["ro_boottime_zygote_ns"] = zyg_ns
            return merged

        return {
            "n": 1,
            "ro_boottime_init_ns": init_ns,
            "ro_boottime_zygote_ns": zyg_ns,
        }


def run_cold_boot_workload(
    serial: str,
    boot_metrics: Optional[Dict[str, Any]] = None,
    adb_bin: str = DEFAULT_ADB_BIN,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Queries guest boot-phase properties and merges them with optional host boot metrics."""
    ctx = WorkloadContext.from_device(
        serial,
        adb_bin=adb_bin,
        process_runner=process_runner,
        boot_metrics=boot_metrics,
    )
    return ColdBootWorkloadStrategy().execute(COLD_BOOT_WORKLOAD_NAME, ctx)
