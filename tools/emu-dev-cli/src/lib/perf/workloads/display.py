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

"""Guest graphics (`dumpsys gfxinfo`) and display rendering workload strategy."""

import re
import time
from typing import Any, Dict, List, Optional

from core.process import ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf.catalog import DEFAULT_ADB_BIN
from lib.perf.workloads.base import WorkloadContext, WorkloadStrategy
from lib.perf_telemetry import WorkloadProfilerSession

DISPLAY_WORKLOAD_NAME: str = "display_gfxinfo"
DEFAULT_DISPLAY_DURATION_SEC: float = 5.0
MIN_DISPLAY_DURATION_SEC: float = 1.0
SWIPE_GESTURE_ARGS = ("input", "swipe", "300", "800", "300", "200", "400")

TOTAL_FRAMES_RE = re.compile(r"Total frames rendered:\s*(\d+)")
JANKY_FRAMES_RE = re.compile(r"Janky frames:\s*\d+\s*\(([\d.]+)%\)")
P50_FRAME_RE = re.compile(r"50th percentile:\s*(\d+)ms")
P90_FRAME_RE = re.compile(r"90th percentile:\s*(\d+)ms")


class DisplayWorkloadStrategy(WorkloadStrategy):
    """Strategy measuring guest `dumpsys gfxinfo` frame statistics and host CPU% during UI rendering."""

    def __init__(
        self, duration_sec: float = DEFAULT_DISPLAY_DURATION_SEC
    ) -> None:
        self.window_sec = max(MIN_DISPLAY_DURATION_SEC, duration_sec)

    def matches(self, workload_name: str) -> bool:
        return workload_name == DISPLAY_WORKLOAD_NAME

    def execute(
        self, workload_name: str, ctx: WorkloadContext
    ) -> Dict[str, Any]:
        del workload_name
        ctx.adb.shell(["dumpsys", "gfxinfo", "--reset"], timeout=15.0)
        with WorkloadProfilerSession(
            pid=ctx.emu_pid,
            profiles=ctx.profiles,
            adb_fn=ctx.adb.as_adb_fn(default_timeout=30.0),
        ) as prof:
            ctx.adb.shell(SWIPE_GESTURE_ARGS, timeout=15.0)
            time.sleep(self.window_sec)

        gfx_res = ctx.adb.shell(["dumpsys", "gfxinfo"], timeout=15.0)
        out = gfx_res.stdout if gfx_res.returncode == 0 else ""

        m_f = TOTAL_FRAMES_RE.search(out)
        total_frames = int(m_f.group(1)) if m_f else 0
        m_j = JANKY_FRAMES_RE.search(out)
        jank_pct = float(m_j.group(1)) if m_j else 0.0
        m_50 = P50_FRAME_RE.search(out)
        p50_ms = float(m_50.group(1)) if m_50 else 0.0
        m_90 = P90_FRAME_RE.search(out)
        p90_ms = float(m_90.group(1)) if m_90 else 0.0

        t = prof.telemetry
        return {
            "n": 1,
            "host_cpu_pct": t.host_cpu_pct,
            "rss_mib": t.rss_mib,
            "top_threads_cpu_pct": t.top_threads_cpu_pct,
            "total_frames": total_frames,
            "jank_pct": jank_pct,
            "frame_p50_ms": p50_ms,
            "frame_p90_ms": p90_ms,
        }


def run_display_workload(
    serial: str,
    emu_pid: Optional[int],
    duration_sec: float = DEFAULT_DISPLAY_DURATION_SEC,
    **ctx_kwargs: Any,
) -> Dict[str, Any]:
    """Measures guest `dumpsys gfxinfo` frame statistics and host CPU% during UI rendering."""
    return DisplayWorkloadStrategy(duration_sec=duration_sec).execute_for_device(
        DISPLAY_WORKLOAD_NAME,
        serial,
        emu_pid,
        **ctx_kwargs,
    )
