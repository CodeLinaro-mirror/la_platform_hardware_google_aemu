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

"""Bazel command.profile.gz & Android Build timing analyzer for emu-dev-cli."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import datetime
import gzip
import json
import logging
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.android_build_client import AndroidBuildClient

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1

SUPPORTED_BUILD_TARGETS: List[str] = [
    "emulator_linux_x64",
    "emulator_linux_x64_asan",
    "emulator_linux_x64_debug",
    "emulator_linux_x64_tsan",
    "emulator_mac_aarch64",
    "emulator_mac_aarch64_debug",
    "emulator_windows_x64",
    "emulator_windows_x64_debug",
]

TARGET_ALIASES: Dict[str, str] = {
    "linux": "emulator_linux_x64",
    "linux-x64": "emulator_linux_x64",
    "linux_x64": "emulator_linux_x64",
    "asan": "emulator_linux_x64_asan",
    "linux_asan": "emulator_linux_x64_asan",
    "linux-asan": "emulator_linux_x64_asan",
    "linux_debug": "emulator_linux_x64_debug",
    "linux-debug": "emulator_linux_x64_debug",
    "tsan": "emulator_linux_x64_tsan",
    "linux_tsan": "emulator_linux_x64_tsan",
    "linux-tsan": "emulator_linux_x64_tsan",
    "mac": "emulator_mac_aarch64",
    "mac_arm": "emulator_mac_aarch64",
    "mac-arm": "emulator_mac_aarch64",
    "mac_arm64": "emulator_mac_aarch64",
    "mac-arm64": "emulator_mac_aarch64",
    "mac_aarch64": "emulator_mac_aarch64",
    "mac-aarch64": "emulator_mac_aarch64",
    "mac_debug": "emulator_mac_aarch64_debug",
    "mac-debug": "emulator_mac_aarch64_debug",
    "mac_arm_debug": "emulator_mac_aarch64_debug",
    "win": "emulator_windows_x64",
    "windows": "emulator_windows_x64",
    "win_x64": "emulator_windows_x64",
    "win-x64": "emulator_windows_x64",
    "windows_x64": "emulator_windows_x64",
    "windows-x64": "emulator_windows_x64",
    "win_debug": "emulator_windows_x64_debug",
    "win-debug": "emulator_windows_x64_debug",
    "windows_debug": "emulator_windows_x64_debug",
    "windows-debug": "emulator_windows_x64_debug",
}


def resolve_build_targets(target_arg: str) -> List[str]:
    """Resolves a target name, alias, comma-separated list, or 'all' into canonical targets."""
    if not target_arg:
        return ["emulator_linux_x64"]
    cleaned = target_arg.strip()
    if cleaned.lower() == "all":
        return list(SUPPORTED_BUILD_TARGETS)

    resolved: List[str] = []
    for part in cleaned.split(","):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low == "all":
            return list(SUPPORTED_BUILD_TARGETS)
        if low in TARGET_ALIASES:
            canon = TARGET_ALIASES[low]
        elif p in SUPPORTED_BUILD_TARGETS:
            canon = p
        elif low in SUPPORTED_BUILD_TARGETS:
            canon = low
        else:
            raise ValueError(
                f"Unsupported target '{p}'. Supported targets: {', '.join(SUPPORTED_BUILD_TARGETS)} "
                f"(or aliases: {', '.join(sorted(TARGET_ALIASES.keys()))}, all)"
            )
        if canon not in resolved:
            resolved.append(canon)
    return resolved or ["emulator_linux_x64"]


def format_duration(seconds: Optional[float]) -> str:
    """Formats a duration in seconds into a concise human-readable string (e.g., '16.6m (994s)')."""
    if seconds is None or seconds < 0:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m ({seconds:.0f}s)"
    hours = minutes / 60.0
    return f"{hours:.1f}h ({minutes:.0f}m)"


def format_delta(delta_sec: float, base_sec: float = 0.0) -> str:
    """Formats a signed duration delta with optional percentage change."""
    sign = "+" if delta_sec >= 0 else "-"
    abs_s = abs(delta_sec)
    if abs_s < 60:
        base_str = f"{sign}{abs_s:.1f}s"
    else:
        base_str = f"{sign}{abs_s / 60.0:.1f}m ({sign}{abs_s:.0f}s)"
    if base_sec > 1.0:
        pct = (delta_sec / base_sec) * 100.0
        return f"{base_str} ({sign}{abs(pct):.0f}%)"
    return base_str


@dataclass
class CriticalPathItem:
    name: str
    duration_sec: float
    pct_of_critical_path: float


@dataclass
class MnemonicSummary:
    mnemonic: str
    count: int
    total_sec: float
    avg_sec: float
    max_sec: float
    max_action_name: str = ""


@dataclass
class BuildProfileMetrics:
    build_id: str
    target: str
    branch: str = "git_emu-main-next"
    status: str = "COMPLETE"
    successful: bool = True
    machine_name: str = ""
    free_disk_gb: str = ""
    base_build_id: str = ""
    creation_time: str = ""
    completion_time: str = ""
    total_ci_sec: float = 0.0
    queue_sec: float = 0.0
    sync_sec: float = 0.0
    build_py_sec: float = 0.0
    bazel_wall_sec: float = 0.0
    bazel_version: str = ""
    phase_launch_sec: float = 0.0
    phase_init_sec: float = 0.0
    phase_evaluate_sec: float = 0.0
    phase_load_analyze_sec: float = 0.0
    phase_execute_sec: float = 0.0
    critical_path_sec: float = 0.0
    critical_path_items: List[CriticalPathItem] = field(default_factory=list)
    total_actions: int = 0
    remote_cache_checks: int = 0
    remote_cache_check_sec: float = 0.0
    remote_executions: int = 0
    remote_execution_sec: float = 0.0
    remote_downloads: int = 0
    remote_download_sec: float = 0.0
    local_subprocesses: int = 0
    repo_fetch_count: int = 0
    repo_fetch_total_sec: float = 0.0
    slowest_repo_fetches: List[Dict[str, Any]] = field(default_factory=list)
    mnemonics: List[MnemonicSummary] = field(default_factory=list)
    slowest_actions: List[Dict[str, Any]] = field(default_factory=list)
    repo_revisions: Dict[str, str] = field(default_factory=dict)
    repo_paths: Dict[str, str] = field(default_factory=dict)
    artifact_viewer_url: str = ""
    profile_available: bool = False
    is_regressed: bool = False
    regression_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = CACHE_SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BuildProfileMetrics":
        cp_items = [
            CriticalPathItem(**item) if isinstance(item, dict) else item
            for item in data.get("critical_path_items", [])
        ]
        mnems = [
            MnemonicSummary(**item) if isinstance(item, dict) else item
            for item in data.get("mnemonics", [])
        ]
        filtered = {
            k: v
            for k, v in data.items()
            if k in cls.__dataclass_fields__ and k not in ("critical_path_items", "mnemonics")
        }
        return cls(
            critical_path_items=cp_items,
            mnemonics=mnems,
            **filtered,
        )

    @property
    def top_critical_bottleneck(self) -> Optional[CriticalPathItem]:
        if not self.critical_path_items:
            return None
        return max(self.critical_path_items, key=lambda x: x.duration_sec)


def get_profile_cache_dir() -> Path:
    """Returns the cache directory for parsed build profiles."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    cache_dir = base / "emu-dev-cli" / "build-profiles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _parse_iso_ts(ts_str: Optional[str]) -> Optional[float]:
    if not ts_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def parse_bazel_profile(profile_path: str, top_n: int = 15) -> Dict[str, Any]:
    """Parses a Bazel Chrome Trace profile (.gz or raw JSON) into structured timing metrics."""
    open_fn = gzip.open if str(profile_path).endswith(".gz") else open
    with open_fn(profile_path, "rt", encoding="utf-8", errors="replace") as f:
        raw = json.load(f)

    other_data = raw.get("otherData", {})
    events = raw.get("traceEvents", [])

    phase_markers: Dict[str, float] = {}
    launch_dur_us = 0.0
    max_end_us = 0.0

    critical_items_raw: List[Tuple[float, str]] = []
    mnemonic_stats: Dict[str, List[Any]] = defaultdict(lambda: [0, 0.0, 0.0, ""])
    all_actions: List[Tuple[float, str, str, str]] = []
    repo_fetches: Dict[str, float] = defaultdict(float)
    repo_fetch_count = 0

    remote_cache_checks = 0
    remote_cache_check_us = 0.0
    remote_executions = 0
    remote_execution_us = 0.0
    remote_downloads = 0
    remote_download_us = 0.0
    local_subprocesses = 0

    first_action_ts_us: Optional[float] = None

    for ev in events:
        ts = ev.get("ts")
        dur = ev.get("dur", 0)
        if isinstance(ts, (int, float)):
            end_ts = ts + (dur if isinstance(dur, (int, float)) else 0)
            if end_ts > max_end_us:
                max_end_us = end_ts

        cat = ev.get("cat", "")
        name = ev.get("name", "")

        if cat == "build phase marker":
            if name == "Launch Blaze" and isinstance(dur, (int, float)):
                launch_dur_us = float(dur)
            elif isinstance(ts, (int, float)):
                phase_markers[name] = float(ts)

        elif cat == "critical path component":
            if isinstance(dur, (int, float)) and dur > 0:
                critical_items_raw.append((float(dur) / 1e6, name))

        elif cat == "action processing":
            if isinstance(ts, (int, float)) and ts > 0:
                if first_action_ts_us is None or ts < first_action_ts_us:
                    first_action_ts_us = float(ts)
            dur_s = float(dur) / 1e6 if isinstance(dur, (int, float)) else 0.0
            args = ev.get("args") or {}
            m = args.get("mnemonic", "Unknown")
            target = args.get("target", "")
            rec = mnemonic_stats[m]
            rec[0] += 1
            rec[1] += dur_s
            if dur_s > rec[2]:
                rec[2] = dur_s
                rec[3] = name
            all_actions.append((dur_s, m, name, target))

        elif cat == "Fetching repository":
            repo_fetch_count += 1
            dur_s = float(dur) / 1e6 if isinstance(dur, (int, float)) else 0.0
            repo_fetches[name] += dur_s

        elif cat == "remote action cache check" and name == "check cache hit":
            remote_cache_checks += 1
            if isinstance(dur, (int, float)):
                remote_cache_check_us += float(dur)

        elif cat == "remote action execution" and name == "execute remotely":
            remote_executions += 1
            if isinstance(dur, (int, float)):
                remote_execution_us += float(dur)

        elif cat == "remote output download":
            remote_downloads += 1
            if isinstance(dur, (int, float)):
                remote_download_us += float(dur)

        elif cat == "general information" and "subprocess" in name:
            local_subprocesses += 1

    complete_ts_us = phase_markers.get("Complete build", max_end_us)
    eval_ts_us = phase_markers.get("Evaluate target patterns", 0.0)
    load_ts_us = phase_markers.get("Load, analyze dependencies and build artifacts", eval_ts_us)

    phase_launch_sec = launch_dur_us / 1e6
    phase_init_sec = max(0.0, eval_ts_us / 1e6)
    phase_evaluate_sec = max(0.0, (load_ts_us - eval_ts_us) / 1e6)

    exec_start_us = first_action_ts_us if (first_action_ts_us and first_action_ts_us > load_ts_us) else load_ts_us
    phase_load_analyze_sec = max(0.0, (exec_start_us - load_ts_us) / 1e6)
    phase_execute_sec = max(0.0, (complete_ts_us - exec_start_us) / 1e6)
    bazel_wall_sec = (max(complete_ts_us, max_end_us) + launch_dur_us) / 1e6

    crit_total_sec = sum(d for d, _ in critical_items_raw)
    critical_path_items = [
        CriticalPathItem(
            name=n,
            duration_sec=round(d, 2),
            pct_of_critical_path=round((d / crit_total_sec * 100.0) if crit_total_sec > 0 else 0.0, 1),
        )
        for d, n in critical_items_raw
    ]

    mnemonics: List[MnemonicSummary] = []
    for m, (cnt, tot_s, max_s, max_name) in sorted(
        mnemonic_stats.items(), key=lambda x: -x[1][1]
    )[:top_n]:
        mnemonics.append(
            MnemonicSummary(
                mnemonic=m,
                count=cnt,
                total_sec=round(tot_s, 2),
                avg_sec=round(tot_s / cnt if cnt > 0 else 0.0, 2),
                max_sec=round(max_s, 2),
                max_action_name=max_name,
            )
        )

    slowest_actions = [
        {
            "duration_sec": round(d, 2),
            "mnemonic": m,
            "name": n,
            "target": t,
        }
        for d, m, n, t in sorted(all_actions, key=lambda x: -x[0])[:top_n]
    ]

    slowest_repos = [
        {"repo": r, "duration_sec": round(d, 2)}
        for r, d in sorted(repo_fetches.items(), key=lambda x: -x[1])[:10]
    ]

    return {
        "bazel_version": other_data.get("bazel_version", ""),
        "bazel_wall_sec": round(bazel_wall_sec, 2),
        "phase_launch_sec": round(phase_launch_sec, 2),
        "phase_init_sec": round(phase_init_sec, 2),
        "phase_evaluate_sec": round(phase_evaluate_sec, 2),
        "phase_load_analyze_sec": round(phase_load_analyze_sec, 2),
        "phase_execute_sec": round(phase_execute_sec, 2),
        "critical_path_sec": round(crit_total_sec, 2),
        "critical_path_items": critical_path_items,
        "total_actions": len(all_actions),
        "remote_cache_checks": remote_cache_checks,
        "remote_cache_check_sec": round(remote_cache_check_us / 1e6, 2),
        "remote_executions": remote_executions,
        "remote_execution_sec": round(remote_execution_us / 1e6, 2),
        "remote_downloads": remote_downloads,
        "remote_download_sec": round(remote_download_us / 1e6, 2),
        "local_subprocesses": local_subprocesses,
        "repo_fetch_count": repo_fetch_count,
        "repo_fetch_total_sec": round(sum(repo_fetches.values()), 2),
        "slowest_repo_fetches": slowest_repos,
        "mnemonics": mnemonics,
        "slowest_actions": slowest_actions,
    }


def parse_build_info(build_info_path: str) -> Dict[str, Any]:
    """Parses Android Build BUILD_INFO JSON artifact into timing and revision metadata."""
    with open(build_info_path, "r", encoding="utf-8", errors="replace") as f:
        bi = json.load(f)

    sync_start = float(bi.get("sync_start_time") or 0.0)
    sync_end = float(bi.get("sync_finish_time") or 0.0)
    target_dict = bi.get("target") or {}
    tgt_start = float(target_dict.get("target_start_time") or 0.0)
    tgt_end = float(target_dict.get("target_finish_time") or 0.0)

    worknode = bi.get("worknode") or {}
    creation_ms = float(worknode.get("creationTimeMillis") or 0.0)
    creation_sec = creation_ms / 1000.0 if creation_ms > 0 else 0.0

    queue_sec = max(0.0, sync_start - creation_sec) if (sync_start > 0 and creation_sec > 0) else 0.0
    sync_sec = max(0.0, sync_end - sync_start) if (sync_end > 0 and sync_start > 0) else 0.0
    build_py_sec = max(0.0, tgt_end - tgt_start) if (tgt_end > 0 and tgt_start > 0) else 0.0

    repo_revisions: Dict[str, str] = {}
    repo_paths: Dict[str, str] = {}
    if isinstance(bi.get("repo-dict"), dict):
        for k, v in bi["repo-dict"].items():
            repo_revisions[str(k)] = str(v)

    parsed_manifest = bi.get("parsed_manifest") or {}
    for proj in parsed_manifest.get("projects", []):
        name = proj.get("name")
        path = proj.get("path")
        rev = proj.get("revision")
        if name and rev and name not in repo_revisions:
            repo_revisions[name] = rev
        if name and path:
            repo_paths[name] = path

    return {
        "hostname": str(bi.get("hostname") or ""),
        "branch": str(bi.get("branch") or ""),
        "queue_sec": round(queue_sec, 2),
        "sync_sec": round(sync_sec, 2),
        "build_py_sec": round(build_py_sec, 2),
        "repo_revisions": repo_revisions,
        "repo_paths": repo_paths,
    }


def analyze_single_build(
    build_id: str,
    target: str,
    branch: str = "git_emu-main-next",
    build_record: Optional[Dict[str, Any]] = None,
    client: Optional[AndroidBuildClient] = None,
    token: Optional[str] = None,
    use_cache: bool = True,
) -> BuildProfileMetrics:
    """Fetches and analyzes BUILD_INFO and logs/bazel/command.profile.gz for a single (build_id, target)."""
    cache_file = get_profile_cache_dir() / f"{build_id}_{target}.json"
    if use_cache and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("schema_version") == CACHE_SCHEMA_VERSION and cached.get("profile_available"):
                return BuildProfileMetrics.from_dict(cached)
        except Exception as e:
            logger.debug("Cache read skipped for %s/%s: %s", build_id, target, e)

    ab_client = client or AndroidBuildClient(token=token)
    if build_record is None:
        build_record = ab_client.get_build(build_id, target) or {}

    status = str(build_record.get("buildAttemptStatus") or "COMPLETE")
    successful = bool(build_record.get("successful", True))
    machine_name = str(build_record.get("machineName") or "")
    free_disk_gb = str(
        (build_record.get("diskSpaceStat") or {}).get("freeSpaceBeforeBuild")
        or build_record.get("buildbotAvailableSpaceGb")
        or ""
    )
    base_build_id = str(build_record.get("baseBuild") or "")
    creation_time = str(build_record.get("creationTimestamp") or "")
    completion_time = str(build_record.get("completionTimestamp") or "")

    t0 = _parse_iso_ts(creation_time)
    t1 = _parse_iso_ts(completion_time)
    total_ci_sec = round(max(0.0, t1 - t0), 2) if (t0 and t1) else 0.0

    artifact_viewer_url = (
        f"https://android-build.corp.google.com/build_explorer/artifact_viewer/"
        f"{build_id}/{target}/logs/bazel/command.profile.gz"
    )

    metrics = BuildProfileMetrics(
        build_id=str(build_id),
        target=target,
        branch=str(build_record.get("branch") or branch),
        status=status,
        successful=successful,
        machine_name=machine_name,
        free_disk_gb=free_disk_gb,
        base_build_id=base_build_id,
        creation_time=creation_time,
        completion_time=completion_time,
        total_ci_sec=total_ci_sec,
        artifact_viewer_url=artifact_viewer_url,
    )

    tmp_dir = tempfile.mkdtemp(prefix=f"emu_prof_{build_id}_{target}_")
    try:
        # 1. Download and parse BUILD_INFO if available
        try:
            bi_file = ab_client.download_artifact(build_id, target, "BUILD_INFO", tmp_dir)
            if os.path.exists(bi_file):
                bi_data = parse_build_info(bi_file)
                if bi_data.get("hostname") and not metrics.machine_name:
                    metrics.machine_name = bi_data["hostname"]
                metrics.queue_sec = bi_data.get("queue_sec", 0.0)
                metrics.sync_sec = bi_data.get("sync_sec", 0.0)
                metrics.build_py_sec = bi_data.get("build_py_sec", 0.0)
                metrics.repo_revisions = bi_data.get("repo_revisions", {})
                metrics.repo_paths = bi_data.get("repo_paths", {})
                if metrics.total_ci_sec <= 0.0 and metrics.build_py_sec > 0.0:
                    metrics.total_ci_sec = round(
                        metrics.queue_sec + metrics.sync_sec + metrics.build_py_sec, 2
                    )
        except Exception as e:
            logger.debug("BUILD_INFO not available for %s/%s: %s", build_id, target, e)

        # 2. Download and parse logs/bazel/command.profile.gz
        try:
            prof_file = ab_client.download_artifact(
                build_id, target, "logs/bazel/command.profile.gz", tmp_dir
            )
            if os.path.exists(prof_file):
                prof_data = parse_bazel_profile(prof_file)
                metrics.profile_available = True
                metrics.bazel_version = prof_data["bazel_version"]
                metrics.bazel_wall_sec = prof_data["bazel_wall_sec"]
                metrics.phase_launch_sec = prof_data["phase_launch_sec"]
                metrics.phase_init_sec = prof_data["phase_init_sec"]
                metrics.phase_evaluate_sec = prof_data["phase_evaluate_sec"]
                metrics.phase_load_analyze_sec = prof_data["phase_load_analyze_sec"]
                metrics.phase_execute_sec = prof_data["phase_execute_sec"]
                metrics.critical_path_sec = prof_data["critical_path_sec"]
                metrics.critical_path_items = prof_data["critical_path_items"]
                metrics.total_actions = prof_data["total_actions"]
                metrics.remote_cache_checks = prof_data["remote_cache_checks"]
                metrics.remote_cache_check_sec = prof_data["remote_cache_check_sec"]
                metrics.remote_executions = prof_data["remote_executions"]
                metrics.remote_execution_sec = prof_data["remote_execution_sec"]
                metrics.remote_downloads = prof_data["remote_downloads"]
                metrics.remote_download_sec = prof_data["remote_download_sec"]
                metrics.local_subprocesses = prof_data["local_subprocesses"]
                metrics.repo_fetch_count = prof_data["repo_fetch_count"]
                metrics.repo_fetch_total_sec = prof_data["repo_fetch_total_sec"]
                metrics.slowest_repo_fetches = prof_data["slowest_repo_fetches"]
                metrics.mnemonics = prof_data["mnemonics"]
                metrics.slowest_actions = prof_data["slowest_actions"]
        except Exception as e:
            logger.debug("command.profile.gz not available for %s/%s: %s", build_id, target, e)

        if use_cache and metrics.profile_available:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(metrics.to_dict(), f, indent=2)
            except Exception as e:
                logger.debug("Failed writing profile cache %s: %s", cache_file, e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return metrics


def annotate_regressions(history: List[BuildProfileMetrics]) -> Tuple[Optional[BuildProfileMetrics], List[BuildProfileMetrics]]:
    """Annotates a list of BuildProfileMetrics with regression flags relative to baseline.

    Returns (baseline_build, list_of_regressed_builds).
    """
    valid = [m for m in history if m.bazel_wall_sec > 0 or m.total_ci_sec > 0]
    if len(valid) < 2:
        return (valid[0] if valid else None, [])

    bazel_times = sorted(m.bazel_wall_sec for m in valid if m.bazel_wall_sec > 0)
    ci_times = sorted(m.total_ci_sec for m in valid if m.total_ci_sec > 0)

    # Use 25th percentile (fast normal builds) as baseline reference so that if 5/10 builds regressed,
    # the baseline still reflects healthy build performance.
    base_bazel = bazel_times[max(0, len(bazel_times) // 4)] if bazel_times else 0.0
    base_ci = ci_times[max(0, len(ci_times) // 4)] if ci_times else 0.0

    # Pick the most recent healthy build near the 25th percentile as the concrete baseline build
    baseline_candidates = [
        m for m in valid
        if (base_bazel <= 0 or m.bazel_wall_sec <= base_bazel * 1.25)
        and (base_ci <= 0 or m.total_ci_sec <= base_ci * 1.35)
    ]
    baseline = baseline_candidates[0] if baseline_candidates else min(
        valid, key=lambda m: m.bazel_wall_sec if m.bazel_wall_sec > 0 else m.total_ci_sec
    )

    regressed_list: List[BuildProfileMetrics] = []
    for m in history:
        reasons: List[str] = []
        if base_bazel > 60 and m.bazel_wall_sec > max(base_bazel * 1.35, base_bazel + 180):
            delta_pct = (m.bazel_wall_sec - base_bazel) / base_bazel * 100.0
            reasons.append(f"Bazel wall +{delta_pct:.0f}% ({format_duration(m.bazel_wall_sec)})")
        if m.queue_sec > 600:
            reasons.append(f"Queue wait {format_duration(m.queue_sec)}")
        elif base_ci > 120 and m.total_ci_sec > max(base_ci * 1.5, base_ci + 300) and not reasons:
            delta_pct = (m.total_ci_sec - base_ci) / base_ci * 100.0
            reasons.append(f"Total CI +{delta_pct:.0f}% ({format_duration(m.total_ci_sec)})")

        if reasons:
            m.is_regressed = True
            m.regression_reasons = reasons
            regressed_list.append(m)
        else:
            m.is_regressed = False
            m.regression_reasons = []

    return baseline, regressed_list


def fetch_target_history(
    target: str,
    branch: str = "git_emu-main-next",
    limit: int = 10,
    token: Optional[str] = None,
    use_cache: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[BuildProfileMetrics]:
    """Fetches and analyzes the last N submitted builds for a target in parallel."""
    client = AndroidBuildClient(token=token)
    builds = client.list_builds(branch=branch, target=target, limit=limit, include_building=False)
    if not builds:
        return []

    if progress_callback:
        progress_callback(f"Analyzing {len(builds)} builds for {target} on {branch}...")

    results_by_idx: Dict[int, BuildProfileMetrics] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(builds))) as pool:
        fut_to_idx = {
            pool.submit(
                analyze_single_build,
                build_id=str(b.get("buildId")),
                target=target,
                branch=branch,
                build_record=b,
                client=client,
                token=token,
                use_cache=use_cache,
            ): idx
            for idx, b in enumerate(builds)
        }
        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as e:
                bid = str(builds[idx].get("buildId", ""))
                logger.warning("Failed analyzing build %s (%s): %s", bid, target, e)

    ordered = [results_by_idx[i] for i in sorted(results_by_idx.keys())]
    annotate_regressions(ordered)
    return ordered


def _get_git_commits_between(
    source_root: Optional[str],
    repo_path: str,
    old_sha: str,
    new_sha: str,
    max_commits: int = 10,
) -> List[str]:
    """Runs git log --oneline old_sha..new_sha in source_root/repo_path if available locally."""
    if not source_root or not repo_path or not old_sha or not new_sha or old_sha == new_sha:
        return []
    full_dir = os.path.join(source_root, repo_path)
    if not os.path.isdir(full_dir):
        return []
    try:
        res = subprocess.run(
            ["git", "log", "--oneline", "-n", str(max_commits), f"{old_sha}..{new_sha}"],
            cwd=full_dir,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
    except Exception:
        pass
    return []


def compare_build_profiles(
    baseline: BuildProfileMetrics,
    regressed: BuildProfileMetrics,
    source_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Compares a regressed build against a fast baseline build and attributes root causes."""
    deltas = {
        "total_ci_sec": round(regressed.total_ci_sec - baseline.total_ci_sec, 2),
        "queue_sec": round(regressed.queue_sec - baseline.queue_sec, 2),
        "sync_sec": round(regressed.sync_sec - baseline.sync_sec, 2),
        "build_py_sec": round(regressed.build_py_sec - baseline.build_py_sec, 2),
        "bazel_wall_sec": round(regressed.bazel_wall_sec - baseline.bazel_wall_sec, 2),
        "critical_path_sec": round(regressed.critical_path_sec - baseline.critical_path_sec, 2),
        "phase_load_analyze_sec": round(
            regressed.phase_load_analyze_sec - baseline.phase_load_analyze_sec, 2
        ),
        "phase_execute_sec": round(regressed.phase_execute_sec - baseline.phase_execute_sec, 2),
        "total_actions": regressed.total_actions - baseline.total_actions,
        "remote_executions": regressed.remote_executions - baseline.remote_executions,
    }

    # 1. Critical path item deltas
    base_cp_map = {item.name: item.duration_sec for item in baseline.critical_path_items}
    cp_regressions: List[Dict[str, Any]] = []
    for item in regressed.critical_path_items:
        prev_dur = base_cp_map.get(item.name, 0.0)
        diff = item.duration_sec - prev_dur
        if diff >= 10.0 or item.duration_sec >= 30.0:
            cp_regressions.append({
                "name": item.name,
                "baseline_sec": round(prev_dur, 2),
                "regressed_sec": round(item.duration_sec, 2),
                "delta_sec": round(diff, 2),
            })
    cp_regressions.sort(key=lambda x: -x["delta_sec"])

    # 2. Mnemonic deltas
    base_mnem_map = {m.mnemonic: m for m in baseline.mnemonics}
    mnemonic_regressions: List[Dict[str, Any]] = []
    for m in regressed.mnemonics:
        bm = base_mnem_map.get(m.mnemonic)
        prev_tot = bm.total_sec if bm else 0.0
        prev_max = bm.max_sec if bm else 0.0
        prev_cnt = bm.count if bm else 0
        diff_tot = m.total_sec - prev_tot
        diff_max = m.max_sec - prev_max
        if abs(diff_tot) >= 30.0 or abs(diff_max) >= 15.0:
            mnemonic_regressions.append({
                "mnemonic": m.mnemonic,
                "baseline_count": prev_cnt,
                "regressed_count": m.count,
                "baseline_total_sec": round(prev_tot, 2),
                "regressed_total_sec": round(m.total_sec, 2),
                "delta_total_sec": round(diff_tot, 2),
                "baseline_max_sec": round(prev_max, 2),
                "regressed_max_sec": round(m.max_sec, 2),
                "delta_max_sec": round(diff_max, 2),
                "max_action_name": m.max_action_name,
            })
    mnemonic_regressions.sort(key=lambda x: -x["delta_total_sec"])

    # 3. Git repository revision diffs between the chronologically older and newer build
    older, newer = (
        (baseline, regressed)
        if int(baseline.build_id or 0) <= int(regressed.build_id or 0)
        else (regressed, baseline)
    )
    changed_repos: List[Dict[str, Any]] = []
    if older.repo_revisions and newer.repo_revisions:
        for repo_name, new_sha in sorted(newer.repo_revisions.items()):
            old_sha = older.repo_revisions.get(repo_name)
            if old_sha and old_sha != new_sha:
                repo_path = newer.repo_paths.get(repo_name) or older.repo_paths.get(repo_name, "")
                commits = _get_git_commits_between(source_root, repo_path, old_sha, new_sha)
                changed_repos.append({
                    "repo": repo_name,
                    "path": repo_path,
                    "old_sha": old_sha[:12],
                    "new_sha": new_sha[:12],
                    "commits": commits,
                })

    # 4. Synthesize actionable root-cause findings
    primary_causes: List[str] = []
    if deltas["queue_sec"] > 300:
        primary_causes.append(
            f"**Builder Queue Bottleneck ({format_delta(deltas['queue_sec'], baseline.queue_sec)})**: "
            f"Build `{regressed.build_id}` waited **{format_duration(regressed.queue_sec)}** in the Android Build worker queue "
            f"(vs `{format_duration(baseline.queue_sec)}` in `{baseline.build_id}`) before a `{regressed.machine_name or 'worker'}` host became free."
        )

    if deltas["bazel_wall_sec"] > 60:
        primary_causes.append(
            f"**Bazel Execution Slowdown ({format_delta(deltas['bazel_wall_sec'], baseline.bazel_wall_sec)})**: "
            f"Bazel wall time increased from **{format_duration(baseline.bazel_wall_sec)}** (`{baseline.build_id}`) "
            f"to **{format_duration(regressed.bazel_wall_sec)}** (`{regressed.build_id}`), with Critical Path shifting by "
            f"**{format_delta(deltas['critical_path_sec'], baseline.critical_path_sec)}**."
        )

    if cp_regressions:
        top_cp = cp_regressions[0]
        primary_causes.append(
            f"**#1 Critical Path Bottleneck (`+{top_cp['delta_sec']:.1f}s`)**: "
            f"`{top_cp['name']}` took **{format_duration(top_cp['regressed_sec'])}** on the critical path "
            f"(vs `{format_duration(top_cp['baseline_sec'])}` in baseline)."
        )

    if mnemonic_regressions:
        top_m = mnemonic_regressions[0]
        if top_m["delta_total_sec"] > 0:
            primary_causes.append(
                f"**Top Regressed Action Mnemonic (`{top_m['mnemonic']}`)**: "
                f"Cumulative time rose by **{format_delta(top_m['delta_total_sec'], top_m['baseline_total_sec'])}** "
                f"(max single action: `{top_m['regressed_max_sec']:.1f}s` vs `{top_m['baseline_max_sec']:.1f}s` — `{top_m['max_action_name']}`)."
            )

    if baseline.machine_name and regressed.machine_name and baseline.machine_name != regressed.machine_name:
        primary_causes.append(
            f"**Builder Machine Difference**: Regressed build ran on `{regressed.machine_name}` "
            f"(free disk: `{regressed.free_disk_gb}GB`) vs baseline on `{baseline.machine_name}` (`{baseline.free_disk_gb}GB`)."
        )

    if not primary_causes:
        primary_causes.append(
            f"Build `{regressed.build_id}` ({format_duration(regressed.bazel_wall_sec)} Bazel wall) is within normal variance of baseline `{baseline.build_id}` ({format_duration(baseline.bazel_wall_sec)})."
        )

    return {
        "baseline": baseline.to_dict(),
        "regressed": regressed.to_dict(),
        "deltas": deltas,
        "primary_causes": primary_causes,
        "critical_path_regressions": cp_regressions,
        "mnemonic_regressions": mnemonic_regressions,
        "changed_repos": changed_repos,
        "chronology": {"older_build_id": older.build_id, "newer_build_id": newer.build_id},
    }
