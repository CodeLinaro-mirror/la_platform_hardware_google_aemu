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

"""Live-device and managed-emulator stability session orchestration."""

import datetime
import json
import logging
import os
import re
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Set

from core.context import detect_host_platform
from core.process import ProcessRunner
from lib.avd import create_single_avd, get_default_avd_root
from lib.emulator import prepare_environment, spawn_emulator_process
from lib.perf.adb import AdbDevice
from lib.perf.catalog import ResolvedPerfTarget
from lib.perf.guest_settle import wait_for_guest_and_host_settled
from lib.perf.session import (
    _cleanup_stale_netsim,
    _create_secure_temp_file,
    _find_free_console_port,
    _kill_emulator_process_tree,
    _resolve_perf_avd_template,
)
from lib.perf.workloads.snapshot import _ensure_valid_console_auth_token
from lib.perf_probe_client import open_emulator_grpc_channel
from lib.perf_telemetry import find_emulator_pid
from lib.stability.catalog import (
    StabilityRunConfig,
    StabilityVerdict,
    select_cycle_scenario,
)
from lib.stability.forensics import (
    ForensicBundleInfo,
    collect_forensic_bundle,
    discover_recent_minidumps,
)
from lib.stability.health import (
    CycleHealthSample,
    compute_cpu_pct_between_snapshots,
    evaluate_stability_samples,
    inspect_guest_health,
    parse_dropbox_lines,
    sample_host_process_health,
)
from lib.stability.render import append_stability_history
from lib.stability.scenarios import (
    GrpcChannelFactory,
    ScenarioCycleOutcome,
    StabilityScenarioContext,
    capture_grpc_png_bytes,
    cleanup_stability_artifacts_on_device,
    dispatch_stability_scenario,
)

logger = logging.getLogger(__name__)

ScenarioDispatcher = Callable[[str, StabilityScenarioContext, int], ScenarioCycleOutcome]


def _seed_initial_dropbox_entries(adb: AdbDevice) -> Set[str]:
    res = adb.shell(["dumpsys", "dropbox"], timeout=12.0)
    if not res.ok:
        return set()
    anrs, tombstones, app_crashes = parse_dropbox_lines(res.stdout)
    return set(anrs | tombstones | app_crashes)


def _slugify_target(label: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (label or "emu").strip()).strip("-")
    return (slug[:32] or "emu").lower()


def _should_continue_soak_loop(
    config: StabilityRunConfig,
    cycles_executed: int,
    elapsed_sec: float,
) -> bool:
    if config.max_cycles is not None and cycles_executed >= config.max_cycles:
        return False
    if cycles_executed < config.min_cycles:
        return True
    return elapsed_sec < config.duration_sec


def run_stability_session_against_device(
    *,
    serial: str,
    config: StabilityRunConfig,
    emu_pid: Optional[int] = None,
    grpc_port: Optional[int] = None,
    popen_proc: Optional[Any] = None,
    host_log_path: Optional[str] = None,
    avd_label: str = "live-device",
    target_meta: Optional[Dict[str, Any]] = None,
    extra_minidump_dirs: Optional[List[str]] = None,
    process_runner: Optional[ProcessRunner] = None,
    grpc_channel_factory: GrpcChannelFactory = open_emulator_grpc_channel,
    scenario_dispatcher: ScenarioDispatcher = dispatch_stability_scenario,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.perf_counter,
    record_history: bool = True,
) -> Dict[str, Any]:
    """Executes the configured stability cycle loop against an active emulator `serial`."""
    adb = AdbDevice(
        serial=serial,
        adb_bin=config.adb_bin,
        process_runner=process_runner,
    )
    resolved_pid = emu_pid or find_emulator_pid(serial)
    session_start_epoch = time.time()

    if config.settle_seconds > 0:
        wait_for_guest_and_host_settled(
            serial=serial,
            emu_pid=resolved_pid,
            settle_seconds=config.settle_seconds,
            adb_bin=config.adb_bin,
            proc=popen_proc,
            log_path=host_log_path,
            process_runner=process_runner,
        )
        resolved_pid = resolved_pid or find_emulator_pid(serial)

    seen_dropbox = _seed_initial_dropbox_entries(adb)
    ctx = StabilityScenarioContext(
        adb=adb,
        emu_pid=resolved_pid,
        grpc_port=grpc_port,
        idle_settle_sec=config.idle_settle_sec,
        grpc_channel_factory=grpc_channel_factory,
        sleep_fn=sleep_fn,
    )

    samples: List[CycleHealthSample] = []
    t_loop_start = clock_fn()
    cycle_idx = 0

    try:
        while _should_continue_soak_loop(
            config, cycle_idx, max(0.0, clock_fn() - t_loop_start)
        ):
            scenario_name = select_cycle_scenario(config.scenarios, cycle_idx)
            host_before = sample_host_process_health(resolved_pid, popen_proc)
            if not host_before.alive:
                dmps = discover_recent_minidumps(
                    session_start_epoch, extra_minidump_dirs
                )
                samples.append(
                    CycleHealthSample(
                        cycle_index=cycle_idx,
                        elapsed_sec=round(max(0.0, clock_fn() - t_loop_start), 2),
                        scenario=scenario_name,
                        process_alive=False,
                        adb_ok=False,
                        crashpad_minidumps=len(dmps),
                        scenario_error="Emulator host process terminated before cycle started.",
                    )
                )
                break

            outcome = ScenarioCycleOutcome()
            scenario_err: Optional[str] = None
            try:
                outcome = scenario_dispatcher(scenario_name, ctx, cycle_idx)
            except Exception as exc:  # noqa: BLE001
                scenario_err = str(exc)
                logger.warning(
                    "Stability cycle %d (%s) failed on %s: %s",
                    cycle_idx,
                    scenario_name,
                    serial,
                    exc,
                )

            host_after = sample_host_process_health(resolved_pid, popen_proc)
            cpu_pct = compute_cpu_pct_between_snapshots(
                host_before.proc_snapshot, host_after.proc_snapshot
            )
            guest_snap = (
                inspect_guest_health(adb, seen_dropbox)
                if host_after.alive
                else None
            )
            dmps = discover_recent_minidumps(
                session_start_epoch, extra_minidump_dirs
            )

            adb_ok = bool(guest_snap.adb_ok) if guest_snap is not None else False
            sample = CycleHealthSample(
                cycle_index=cycle_idx,
                elapsed_sec=round(max(0.0, clock_fn() - t_loop_start), 2),
                scenario=scenario_name,
                process_alive=host_after.alive,
                rss_mib=host_after.rss_mib,
                peak_rss_mib=host_after.peak_rss_mib,
                open_handles_or_fds=host_after.open_handles_or_fds,
                thread_count=host_after.thread_count,
                host_cpu_pct=cpu_pct,
                idle_cpu_pct=outcome.idle_cpu_pct,
                adb_ok=adb_ok,
                adb_flaps=outcome.adb_flaps,
                adb_ping_ms=outcome.adb_ping_ms,
                touch_latency_p95_ms=outcome.touch_latency_p95_ms,
                system_server_pid=(
                    guest_snap.system_server_pid if guest_snap else None
                ),
                expected_system_server_pid=outcome.expected_system_server_pid,
                dropbox_anr_count=(
                    len(guest_snap.new_anr_entries) if guest_snap else 0
                ),
                dropbox_tombstone_count=(
                    len(guest_snap.new_tombstone_entries) if guest_snap else 0
                ),
                dropbox_app_crash_count=(
                    len(guest_snap.new_app_crash_entries) if guest_snap else 0
                ),
                frame_non_black=outcome.frame_non_black,
                frame_pixel_std=outcome.frame_pixel_std,
                frame_digest=outcome.frame_digest,
                frame_frozen=outcome.frame_frozen,
                jank_pct=outcome.jank_pct,
                crashpad_minidumps=len(dmps),
                scenario_error=scenario_err,
                notes=list(outcome.notes),
            )
            samples.append(sample)
            ctx.last_cycle_healthy = bool(
                host_after.alive
                and adb_ok
                and scenario_err is None
                and guest_snap is not None
                and guest_snap.system_server_pid is not None
                and not guest_snap.new_anr_entries
                and not guest_snap.new_tombstone_entries
            )
            cycle_idx += 1

            # Stop early on unrecoverable process death or lost ADB device
            if not host_after.alive or not adb_ok:
                break

        evaluation = evaluate_stability_samples(
            samples,
            thresholds=config.thresholds,
            strict=config.strict,
        )

        label = config.label or (target_meta or {}).get("label") or f"live-{serial}"
        forensics_info: Optional[ForensicBundleInfo] = None
        if (
            evaluation.verdict != StabilityVerdict.PASS.value
            or config.always_bundle_forensics
        ):
            forensics_info = collect_forensic_bundle(
                label=label,
                samples=samples,
                evaluation=evaluation,
                session_start_epoch=session_start_epoch,
                forensics_root=config.forensics_dir,
                host_log_path=host_log_path,
                adb=adb if (samples and samples[-1].adb_ok) else None,
                png_capture_fn=(
                    (
                        lambda: capture_grpc_png_bytes(
                            serial,
                            emu_pid=resolved_pid,
                            grpc_port=grpc_port,
                            channel_factory=grpc_channel_factory,
                        )
                    )
                    if (samples and samples[-1].process_alive)
                    else None
                ),
                extra_minidump_dirs=extra_minidump_dirs,
            )

        report: Dict[str, Any] = {
            "schema_version": 1,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "label": label,
            "serial": serial,
            "emulator_pid": resolved_pid,
            "host_platform": detect_host_platform(),
            "avd": avd_label,
            "tier": config.tier,
            "scenarios": list(config.scenarios),
            "runtime": {
                "gpu_mode": config.gpu_mode,
                "show_window": config.show_window,
                "enable_audio": config.enable_audio,
                "duration_target_sec": config.duration_sec,
                "min_cycles": config.min_cycles,
            },
            "target": target_meta or {"label": label, "kind": "live"},
            "thresholds": config.thresholds.to_dict(),
            "evaluation": evaluation.to_dict(),
            "samples": [s.to_dict() for s in samples],
            "forensics": forensics_info.to_dict() if forensics_info else None,
        }
        if record_history:
            append_stability_history(report)
        return report
    finally:
        if not config.clean_avd:
            cleanup_stability_artifacts_on_device(ctx)


def build_stability_launch_command(
    emulator_bin: str,
    avd_name: str,
    console_port: int,
    grpc_port: int,
    config: StabilityRunConfig,
) -> List[str]:
    """Constructs the managed emulator launch command using Real-Studio runtime defaults."""
    cmd = [
        emulator_bin,
        "-avd",
        avd_name,
        "-port",
        str(console_port),
        "-grpc",
        str(grpc_port),
        "-gpu",
        config.gpu_mode or "auto",
        "-no-snapshot-load",
    ]
    if not config.show_window:
        cmd.append("-no-window")
    if not config.enable_audio:
        cmd.append("-no-audio")
    return cmd


def run_managed_stability_session(
    *,
    target: ResolvedPerfTarget,
    config: StabilityRunConfig,
    process_runner: Optional[ProcessRunner] = None,
    record_history: bool = True,
) -> Dict[str, Any]:
    """Provisions an isolated AVD clone, boots `target`, runs the stability loop, and cleans up."""
    if not target.emulator_bin or not target.emulator_dir:
        raise ValueError(
            f"Target '{target.label}' does not have a resolved emulator executable directory."
        )

    slug = _slugify_target(config.label or target.build_id or target.label)
    ephemeral_ini: Optional[str] = None
    ephemeral_dir: Optional[str] = None

    if config.clean_avd:
        tmpl = _resolve_perf_avd_template(
            avd_name=config.avd_name,
            sysimg_dir=config.sysimg_dir,
        )
        boot_avd_name = f"stability-clean-{slug}-{os.getpid()}"
        created = create_single_avd(
            avd_name=boot_avd_name,
            raw_sysimg_dir=tmpl["sysimg_dir"],
            ram=tmpl["ram"],
            cores=tmpl["cores"],
            disk_size=tmpl["disk_size"],
            force=True,
        )
        ephemeral_ini = created["ini_file"]
        ephemeral_dir = created["avd_dir"]
        avd_label = f"{tmpl['template_avd']} (clean-isolated)"
    else:
        boot_avd_name = config.avd_name or "medium_phone"
        avd_label = boot_avd_name
        ephemeral_dir = os.path.join(
            get_default_avd_root(), f"{boot_avd_name}.avd"
        )

    port = _find_free_console_port(5564)
    grpc_port = _find_free_console_port(8558)
    serial = f"emulator-{port}"
    env = prepare_environment(target.emulator_dir)
    cmd = build_stability_launch_command(
        emulator_bin=target.emulator_bin,
        avd_name=boot_avd_name,
        console_port=port,
        grpc_port=grpc_port,
        config=config,
    )
    log_path = _create_secure_temp_file(
        prefix=f"emu_stability_{slug}_", suffix=".log"
    )
    _ensure_valid_console_auth_token()
    _cleanup_stale_netsim(force=False, process_runner=process_runner)

    proc = None
    try:
        proc = spawn_emulator_process(
            cmd,
            target.emulator_dir,
            env,
            log_file_path=log_path,
        )
        settle_info = wait_for_guest_and_host_settled(
            serial=serial,
            emu_pid=None,
            settle_seconds=config.settle_seconds,
            adb_bin=config.adb_bin,
            proc=proc,
            log_path=log_path,
            process_runner=process_runner,
        )
        booted_pid = (
            (settle_info or {}).get("emulator_pid")
            or find_emulator_pid(serial)
            or proc.pid
        )
        # Settle already happened above; avoid double-waiting inside run_stability_session_against_device
        inner_cfg = StabilityRunConfig(
            tier=config.tier,
            duration_sec=config.duration_sec,
            min_cycles=config.min_cycles,
            max_cycles=config.max_cycles,
            idle_settle_sec=config.idle_settle_sec,
            scenarios=list(config.scenarios),
            gpu_mode=config.gpu_mode,
            show_window=config.show_window,
            enable_audio=config.enable_audio,
            strict=config.strict,
            avd_name=config.avd_name,
            sysimg_dir=config.sysimg_dir,
            clean_avd=config.clean_avd,
            adb_bin=config.adb_bin,
            forensics_dir=config.forensics_dir,
            always_bundle_forensics=config.always_bundle_forensics,
            label=config.label or target.label,
            boot_timeout_sec=config.boot_timeout_sec,
            settle_seconds=0.0,
            thresholds=config.thresholds,
        )
        target_meta = {
            "spec": target.spec,
            "kind": target.kind,
            "label": target.label,
            "build_id": target.build_id,
            "branch": target.branch,
            "engine": target.engine,
            "pkg_revision": target.pkg_revision,
            "emulator_dir": target.emulator_dir,
        }
        extra_dmp_dirs = [d for d in (ephemeral_dir, target.emulator_dir) if d]
        return run_stability_session_against_device(
            serial=serial,
            config=inner_cfg,
            emu_pid=booted_pid,
            grpc_port=grpc_port,
            popen_proc=proc,
            host_log_path=log_path,
            avd_label=avd_label,
            target_meta=target_meta,
            extra_minidump_dirs=extra_dmp_dirs,
            process_runner=process_runner,
            record_history=record_history,
        )
    finally:
        if proc is not None:
            _kill_emulator_process_tree(
                proc,
                serial,
                adb_bin=config.adb_bin,
                process_runner=process_runner,
            )
        if config.clean_avd:
            if ephemeral_ini and os.path.isfile(ephemeral_ini):
                try:
                    os.unlink(ephemeral_ini)
                except OSError:
                    pass
            if ephemeral_dir and os.path.isdir(ephemeral_dir):
                shutil.rmtree(ephemeral_dir, ignore_errors=True)
        if log_path and os.path.isfile(log_path):
            try:
                os.unlink(log_path)
            except OSError:
                pass


def load_stability_report_json(json_path: str) -> Dict[str, Any]:
    """Loads a saved stability report JSON artifact from disk."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "evaluation" not in data:
        raise ValueError(
            f"File '{json_path}' is not a valid stability report JSON (missing 'evaluation' object)."
        )
    return data
