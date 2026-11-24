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

"""Stability tiers, user-experience scenario catalog, and SLA threshold definitions."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.errors import UsageError


class StabilityVerdict(str, Enum):
    """Canonical 3-state stability qualification verdict."""

    PASS = "PASS"
    DEGRADED = "DEGRADED"
    FAIL = "FAIL"


class StabilityTier(str, Enum):
    """Standardized stability run duration tiers."""

    SMOKE = "smoke"
    STANDARD = "standard"
    SOAK = "soak"


class ScenarioName(str, Enum):
    """Built-in user-experience stability scenarios."""

    DEV_LOOP = "dev-loop"
    SNAPSHOT_STRESS = "snapshot-stress"
    UI_CONTROL = "ui-control"
    ALL = "all"


DEFAULT_TIER: str = StabilityTier.SMOKE.value
DEFAULT_SCENARIO_SPEC: str = ScenarioName.ALL.value
DEFAULT_GPU_MODE: str = "auto"
DEFAULT_ADB_BIN: str = "adb"

SMOKE_DURATION_SEC: float = 300.0
STANDARD_DURATION_SEC: float = 1800.0
SOAK_DURATION_SEC: float = 14400.0

SMOKE_MIN_CYCLES: int = 2
STANDARD_MIN_CYCLES: int = 8
SOAK_MIN_CYCLES: int = 40

SMOKE_IDLE_SETTLE_SEC: float = 6.0
STANDARD_IDLE_SETTLE_SEC: float = 12.0
SOAK_IDLE_SETTLE_SEC: float = 15.0

DEFAULT_RSS_SLOPE_DEGRADED_MIB_PER_MIN: float = 4.0
DEFAULT_RSS_SLOPE_FAIL_MIB_PER_MIN: float = 15.0
DEFAULT_RSS_NET_GROWTH_FAIL_MIB: float = 512.0
DEFAULT_RSS_MIN_ELAPSED_MIN_FOR_SLOPE: float = 2.0

DEFAULT_HANDLE_GROWTH_DEGRADED: int = 64
DEFAULT_HANDLE_GROWTH_FAIL: int = 256

DEFAULT_LATENCY_RATIO_DEGRADED: float = 1.5
DEFAULT_LATENCY_RATIO_FAIL: float = 3.0

DEFAULT_IDLE_CPU_DEGRADED_PCT: float = 15.0
DEFAULT_IDLE_CPU_FAIL_PCT: float = 80.0
DEFAULT_JANK_DEGRADED_PCT: float = 25.0


TIER_CATALOG: Dict[str, Dict[str, Any]] = {
    StabilityTier.SMOKE.value: {
        "duration_sec": SMOKE_DURATION_SEC,
        "min_cycles": SMOKE_MIN_CYCLES,
        "idle_settle_sec": SMOKE_IDLE_SETTLE_SEC,
        "description": (
            "Fast ~5-minute developer and agent sanity soak across core UX loops"
        ),
    },
    StabilityTier.STANDARD.value: {
        "duration_sec": STANDARD_DURATION_SEC,
        "min_cycles": STANDARD_MIN_CYCLES,
        "idle_settle_sec": STANDARD_IDLE_SETTLE_SEC,
        "description": (
            "30-minute pre-release qualification soak detecting memory/handle leaks and drift"
        ),
    },
    StabilityTier.SOAK.value: {
        "duration_sec": SOAK_DURATION_SEC,
        "min_cycles": SOAK_MIN_CYCLES,
        "idle_settle_sec": SOAK_IDLE_SETTLE_SEC,
        "description": (
            "4-hour endurance soak for overnight qualification and slow resource leak detection"
        ),
    },
}


SCENARIO_CATALOG: Dict[str, Dict[str, Any]] = {
    ScenarioName.DEV_LOOP.value: {
        "description": (
            "Inner developer loop: APK install/launch/uninstall, ADB push/pull + logcat, "
            "UI touch/swipe bursts, and post-burst idle CPU settle checks"
        ),
        "leaf_scenarios": [ScenarioName.DEV_LOOP.value],
    },
    ScenarioName.SNAPSHOT_STRESS.value: {
        "description": (
            "Quickboot lifecycle torture: repeated save/load cycles while guest is active, "
            "verifying post-restore ADB readiness, system_server continuity, and frame freshness"
        ),
        "leaf_scenarios": [ScenarioName.SNAPSHOT_STRESS.value],
    },
    ScenarioName.UI_CONTROL.value: {
        "description": (
            "Studio control-plane & streaming stress: concurrent gRPC screenshot streaming, "
            "WebM screen recording, device rotation, sensor updates, and foldable postures"
        ),
        "leaf_scenarios": [ScenarioName.UI_CONTROL.value],
    },
    ScenarioName.ALL.value: {
        "description": (
            "Full interleaved user-experience stability suite (dev-loop + snapshot-stress + ui-control)"
        ),
        "leaf_scenarios": [
            ScenarioName.DEV_LOOP.value,
            ScenarioName.SNAPSHOT_STRESS.value,
            ScenarioName.UI_CONTROL.value,
        ],
    },
}


@dataclass(frozen=True)
class StabilityThresholds:
    """SLA thresholds governing PASS vs DEGRADED vs FAIL verdicts."""

    rss_slope_degraded_mib_per_min: float = DEFAULT_RSS_SLOPE_DEGRADED_MIB_PER_MIN
    rss_slope_fail_mib_per_min: float = DEFAULT_RSS_SLOPE_FAIL_MIB_PER_MIN
    rss_net_growth_fail_mib: float = DEFAULT_RSS_NET_GROWTH_FAIL_MIB
    rss_min_elapsed_min_for_slope: float = DEFAULT_RSS_MIN_ELAPSED_MIN_FOR_SLOPE
    handle_growth_degraded: int = DEFAULT_HANDLE_GROWTH_DEGRADED
    handle_growth_fail: int = DEFAULT_HANDLE_GROWTH_FAIL
    latency_ratio_degraded: float = DEFAULT_LATENCY_RATIO_DEGRADED
    latency_ratio_fail: float = DEFAULT_LATENCY_RATIO_FAIL
    idle_cpu_degraded_pct: float = DEFAULT_IDLE_CPU_DEGRADED_PCT
    idle_cpu_fail_pct: float = DEFAULT_IDLE_CPU_FAIL_PCT
    jank_degraded_pct: float = DEFAULT_JANK_DEGRADED_PCT

    def to_dict(self) -> Dict[str, Any]:
        """Serializes thresholds for JSON reports and `stability list`."""
        return asdict(self)


@dataclass
class StabilityRunConfig:
    """Configuration for a managed or live-device stability run."""

    tier: str = DEFAULT_TIER
    duration_sec: float = SMOKE_DURATION_SEC
    min_cycles: int = SMOKE_MIN_CYCLES
    max_cycles: Optional[int] = None
    idle_settle_sec: float = SMOKE_IDLE_SETTLE_SEC
    scenarios: List[str] = field(
        default_factory=lambda: list(
            SCENARIO_CATALOG[ScenarioName.ALL.value]["leaf_scenarios"]
        )
    )
    gpu_mode: str = DEFAULT_GPU_MODE
    show_window: bool = False
    enable_audio: bool = True
    strict: bool = False
    avd_name: Optional[str] = None
    sysimg_dir: Optional[str] = None
    clean_avd: bool = True
    adb_bin: str = DEFAULT_ADB_BIN
    forensics_dir: Optional[str] = None
    always_bundle_forensics: bool = False
    label: Optional[str] = None
    boot_timeout_sec: float = 240.0
    settle_seconds: float = 15.0
    thresholds: StabilityThresholds = field(default_factory=StabilityThresholds)


def resolve_tier_and_duration(
    tier_name: Optional[str] = None,
    duration_override_sec: Optional[float] = None,
) -> Tuple[str, float, int, float]:
    """Resolves `(tier, duration_sec, min_cycles, idle_settle_sec)` from CLI inputs."""
    normalized = (tier_name or DEFAULT_TIER).strip().lower()
    if normalized not in TIER_CATALOG:
        valid = ", ".join(sorted(TIER_CATALOG))
        raise UsageError(
            f"Unknown stability tier '{tier_name}'. Valid tiers: {valid}.",
            remediation=f"Pass `--tier` with one of: {valid} (or override duration via `--duration <seconds>`).",
        )

    spec = TIER_CATALOG[normalized]
    duration_sec = float(spec["duration_sec"])
    min_cycles = int(spec["min_cycles"])
    idle_settle_sec = float(spec["idle_settle_sec"])

    if duration_override_sec is not None:
        if duration_override_sec <= 0:
            raise UsageError(
                f"Invalid `--duration` value {duration_override_sec}; duration must be > 0 seconds.",
                remediation="Specify a positive duration in seconds, e.g. `--duration 300`.",
            )
        duration_sec = float(duration_override_sec)
        if duration_sec < SMOKE_DURATION_SEC:
            min_cycles = 1
            idle_settle_sec = min(idle_settle_sec, max(1.0, duration_sec * 0.15))

    return normalized, duration_sec, min_cycles, idle_settle_sec


def resolve_stability_scenarios(spec: Optional[str] = None) -> List[str]:
    """Expands a comma-separated `--scenario` specification into ordered leaf scenario names."""
    raw = (spec or DEFAULT_SCENARIO_SPEC).strip().lower()
    if not raw:
        raw = DEFAULT_SCENARIO_SPEC

    tokens = [tok.strip() for tok in raw.split(",") if tok.strip()]
    if not tokens:
        tokens = [DEFAULT_SCENARIO_SPEC]

    resolved: List[str] = []
    valid_names = ", ".join(sorted(SCENARIO_CATALOG))
    for token in tokens:
        if token not in SCENARIO_CATALOG:
            raise UsageError(
                f"Unknown stability scenario '{token}'. Valid scenarios: {valid_names}.",
                remediation="Run `emu-dev-cli stability list` to view available scenarios.",
            )
        for leaf in SCENARIO_CATALOG[token]["leaf_scenarios"]:
            if leaf not in resolved:
                resolved.append(leaf)
    return resolved


def select_cycle_scenario(scenarios: Sequence[str], cycle_index: int) -> str:
    """Selects the leaf scenario to execute for zero-based `cycle_index`."""
    if not scenarios:
        return ScenarioName.DEV_LOOP.value
    return str(scenarios[cycle_index % len(scenarios)])
