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

"""Unit tests for `src/lib/stability/` health invariants, scenarios, forensics, and session loop."""

import json
import os
import tempfile
import time
import unittest
from typing import Any, List, Optional

os.environ.setdefault("HOME", tempfile.gettempdir())
os.environ.setdefault("USERPROFILE", tempfile.gettempdir())

from core.errors import UsageError
from core.process import ProcessResult, ProcessRunner
from lib.perf.adb import AdbDevice
from lib.perf_probe_client import PerfProbeSession
from lib.stability import (
    CycleHealthSample,
    HostProcessSample,
    ScenarioCycleOutcome,
    ScenarioName,
    StabilityRunConfig,
    StabilityScenarioContext,
    StabilityThresholds,
    StabilityVerdict,
    append_stability_history,
    build_stability_launch_command,
    collect_forensic_bundle,
    compare_stability_reports,
    compute_linear_slope_per_min,
    compute_rgb888_frame_digest,
    evaluate_stability_samples,
    format_clickable_file_link,
    format_file_uri,
    format_osc8_link,
    inspect_guest_health,
    inspect_rgb888_frame,
    load_stability_history,
    parse_dropbox_lines,
    query_system_server_pid,
    render_stability_comparison_markdown,
    render_stability_report_markdown,
    resolve_stability_scenarios,
    resolve_tier_and_duration,
    run_dev_loop_cycle,
    run_snapshot_stress_cycle,
    run_stability_session_against_device,
    run_ui_control_cycle,
    supports_osc8_hyperlinks,
)


class _ScriptedProcessRunner(ProcessRunner):
    """Deterministic ProcessRunner double for ADB command sequences."""

    def __init__(self, handler_fn=None) -> None:
        self.calls: List[tuple[str, ...]] = []
        self._handler_fn = handler_fn

    def run(
        self,
        args,
        *,
        cwd=None,
        env=None,
        timeout_sec=300.0,
        timeout=None,
        check=False,
        input_text=None,
        remediation=None,
        stream_output=False,
    ) -> ProcessResult:
        del cwd, env, timeout_sec, timeout, check, input_text, remediation, stream_output
        norm = tuple(str(a) for a in args)
        self.calls.append(norm)
        if self._handler_fn is not None:
            return self._handler_fn(norm)
        return ProcessResult(args=norm, returncode=0, stdout="ok\n", stderr="")


class StabilityCatalogAndHealthTest(unittest.TestCase):
    """Tests catalog resolution, least-squares slopes, frame freshness, and 3-state SLA verdicts."""

    def test_resolve_tier_and_scenarios_valid_and_invalid(self) -> None:
        tier, dur, min_c, settle = resolve_tier_and_duration("smoke", None)
        self.assertEqual(tier, "smoke")
        self.assertEqual(dur, 300.0)
        self.assertEqual(min_c, 2)
        self.assertGreater(settle, 0.0)

        tier_o, dur_o, min_co, _ = resolve_tier_and_duration("standard", 45.0)
        self.assertEqual(tier_o, "standard")
        self.assertEqual(dur_o, 45.0)
        self.assertEqual(min_co, 1)

        with self.assertRaises(UsageError):
            resolve_tier_and_duration("nonexistent-tier")
        with self.assertRaises(UsageError):
            resolve_tier_and_duration("smoke", -5.0)

        all_leafs = resolve_stability_scenarios("all")
        self.assertEqual(
            all_leafs,
            [
                ScenarioName.DEV_LOOP.value,
                ScenarioName.SNAPSHOT_STRESS.value,
                ScenarioName.UI_CONTROL.value,
            ],
        )
        with self.assertRaises(UsageError):
            resolve_stability_scenarios("invalid-scenario")

    def test_compute_linear_slope_and_warmup_exclusion(self) -> None:
        # +10 MiB every 60s -> +10.0 MiB/min
        pts = [(60.0, 1000.0), (120.0, 1010.0), (180.0, 1020.0), (240.0, 1030.0)]
        self.assertAlmostEqual(compute_linear_slope_per_min(pts), 10.0, places=2)

        # Cycle 0 has cold-start jump (800 -> 1200 MiB), then flat 1200 MiB across 5 minutes -> PASS
        samples = [
            CycleHealthSample(cycle_index=0, elapsed_sec=30.0, scenario="dev-loop", rss_mib=800.0, open_handles_or_fds=500, system_server_pid=1111, touch_latency_p95_ms=20.0, idle_cpu_pct=4.0),
            CycleHealthSample(cycle_index=1, elapsed_sec=90.0, scenario="dev-loop", rss_mib=1200.0, open_handles_or_fds=520, system_server_pid=1111, touch_latency_p95_ms=21.0, idle_cpu_pct=5.0),
            CycleHealthSample(cycle_index=2, elapsed_sec=180.0, scenario="ui-control", rss_mib=1202.0, open_handles_or_fds=522, system_server_pid=1111, touch_latency_p95_ms=22.0, idle_cpu_pct=4.5),
            CycleHealthSample(cycle_index=3, elapsed_sec=270.0, scenario="snapshot-stress", rss_mib=1203.0, open_handles_or_fds=523, system_server_pid=1111, touch_latency_p95_ms=21.5, idle_cpu_pct=4.2),
        ]
        ev = evaluate_stability_samples(samples)
        self.assertEqual(ev.verdict, StabilityVerdict.PASS.value)
        self.assertEqual(ev.effective_exit_code, 0)
        self.assertEqual(ev.findings, [])

    def test_degraded_leak_and_strict_mode_promotion(self) -> None:
        # Post-warmup grows +90 MiB over 10 minutes (+9 MiB/min -> DEGRADED) and +80 handles (-> DEGRADED)
        samples = [
            CycleHealthSample(cycle_index=0, elapsed_sec=30.0, scenario="dev-loop", rss_mib=1000.0, open_handles_or_fds=400),
            CycleHealthSample(cycle_index=1, elapsed_sec=60.0, scenario="dev-loop", rss_mib=1100.0, open_handles_or_fds=410),
            CycleHealthSample(cycle_index=2, elapsed_sec=360.0, scenario="dev-loop", rss_mib=1145.0, open_handles_or_fds=450),
            CycleHealthSample(cycle_index=3, elapsed_sec=660.0, scenario="dev-loop", rss_mib=1190.0, open_handles_or_fds=490),
        ]
        ev_non_strict = evaluate_stability_samples(samples, strict=False)
        self.assertEqual(ev_non_strict.verdict, StabilityVerdict.DEGRADED.value)
        self.assertEqual(ev_non_strict.effective_exit_code, 0)

        ev_strict = evaluate_stability_samples(samples, strict=True)
        self.assertEqual(ev_strict.verdict, StabilityVerdict.DEGRADED.value)
        self.assertEqual(ev_strict.effective_exit_code, 1)

    def test_system_server_restart_vs_snapshot_restore_pid(self) -> None:
        # Legitimate snapshot restore sets expected_system_server_pid -> PASS
        snap_ok = [
            CycleHealthSample(cycle_index=0, elapsed_sec=30.0, scenario="dev-loop", system_server_pid=1500),
            CycleHealthSample(cycle_index=1, elapsed_sec=90.0, scenario="snapshot-stress", system_server_pid=1420, expected_system_server_pid=1420),
        ]
        self.assertEqual(
            evaluate_stability_samples(snap_ok).verdict,
            StabilityVerdict.PASS.value,
        )

        # Unexplained system_server PID change -> FAIL
        soft_reboot = [
            CycleHealthSample(cycle_index=0, elapsed_sec=30.0, scenario="dev-loop", system_server_pid=1500),
            CycleHealthSample(cycle_index=1, elapsed_sec=90.0, scenario="dev-loop", system_server_pid=2999),
        ]
        ev_fail = evaluate_stability_samples(soft_reboot)
        self.assertEqual(ev_fail.verdict, StabilityVerdict.FAIL.value)
        self.assertEqual(ev_fail.effective_exit_code, 1)
        self.assertEqual(ev_fail.system_server_restarts, 1)

        # Zombie or unreaped system_server (PID becomes None while adb_ok=True) -> FAIL
        zombie_death = [
            CycleHealthSample(cycle_index=0, elapsed_sec=30.0, scenario="dev-loop", system_server_pid=686, adb_ok=True),
            CycleHealthSample(cycle_index=1, elapsed_sec=90.0, scenario="dev-loop", system_server_pid=None, adb_ok=True),
            CycleHealthSample(cycle_index=2, elapsed_sec=150.0, scenario="ui-control", system_server_pid=None, adb_ok=True),
        ]
        ev_zombie = evaluate_stability_samples(zombie_death)
        self.assertEqual(ev_zombie.verdict, StabilityVerdict.FAIL.value)
        self.assertEqual(ev_zombie.system_server_restarts, 1)

        def _zombie_adb(cmd: tuple[str, ...]) -> ProcessResult:
            joined = " ".join(cmd)
            if "pidof system_server" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="686\n")
            if "ps -o STAT= -p 686" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="Z<\n")
            return ProcessResult(args=cmd, returncode=0, stdout="")

        adb_z = AdbDevice("emulator-5564", process_runner=_ScriptedProcessRunner(_zombie_adb))
        self.assertIsNone(query_system_server_pid(adb_z))

        # Whitespace-only stdout from `ps -o STAT=` must not raise IndexError
        def _ws_stat_adb(cmd: tuple[str, ...]) -> ProcessResult:
            joined = " ".join(cmd)
            if "pidof system_server" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="686\n")
            if "ps -o STAT= -p 686" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="  \n")
            return ProcessResult(args=cmd, returncode=0, stdout="")

        adb_ws = AdbDevice("emulator-5564", process_runner=_ScriptedProcessRunner(_ws_stat_adb))
        self.assertEqual(query_system_server_pid(adb_ws), 686)

    def test_rgb888_frame_black_vs_live_and_frozen_detection(self) -> None:
        all_black = bytes([0, 0, 0] * 256)
        ok_black, std_black = inspect_rgb888_frame(all_black)
        self.assertFalse(ok_black)
        self.assertEqual(std_black, 0.0)

        checker = bytes(([20, 40, 200] + [220, 180, 30]) * 128)
        ok_live, std_live = inspect_rgb888_frame(checker)
        self.assertTrue(ok_live)
        self.assertGreater(std_live, 10.0)

        digest = compute_rgb888_frame_digest(checker)
        self.assertEqual(len(digest), 16)
        self.assertEqual(compute_rgb888_frame_digest(b""), "")

        # 4 consecutive cycles with identical non-empty frame_digest -> FAIL (display_freshness)
        frozen_samples = [
            CycleHealthSample(cycle_index=i, elapsed_sec=float((i + 1) * 30), scenario="ui-control", frame_non_black=True, frame_pixel_std=35.0, frame_digest=digest)
            for i in range(4)
        ]
        ev_frozen = evaluate_stability_samples(frozen_samples)
        self.assertEqual(ev_frozen.verdict, StabilityVerdict.FAIL.value)
        self.assertTrue(
            any(f.category == "display_freshness" and f.metric_name == "frozen_frame_cycles" for f in ev_frozen.findings)
        )

    def test_dropbox_parsing_and_guest_health_delta(self) -> None:
        dropbox_txt = (
            "2026-11-12 09:00:00 system_app_anr (text, 1234 bytes)\n"
            "2026-11-12 09:01:00 SYSTEM_TOMBSTONE (text, 4096 bytes)\n"
            "2026-11-12 09:02:00 data_app_crash (text, 512 bytes)\n"
        )
        anrs, tombs, crashes = parse_dropbox_lines(dropbox_txt)
        self.assertEqual(len(anrs), 1)
        self.assertEqual(len(tombs), 1)
        self.assertEqual(len(crashes), 1)

        def _fake_adb(cmd: tuple[str, ...]) -> ProcessResult:
            joined = " ".join(cmd)
            if "getprop sys.boot_completed" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="1\n")
            if "pidof system_server" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="1777\n")
            if "dumpsys dropbox" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout=dropbox_txt)
            return ProcessResult(args=cmd, returncode=0, stdout="")

        adb = AdbDevice("emulator-5554", process_runner=_ScriptedProcessRunner(_fake_adb))
        seen: set[str] = set()
        first = inspect_guest_health(adb, seen)
        self.assertTrue(first.adb_ok)
        self.assertEqual(first.system_server_pid, 1777)
        self.assertEqual(len(first.new_anr_entries), 1)

        # Second poll sees identical dropbox output -> 0 new entries
        second = inspect_guest_health(adb, seen)
        self.assertEqual(len(second.new_anr_entries), 0)
        self.assertEqual(len(second.new_tombstone_entries), 0)

    def test_launch_command_uses_real_studio_defaults(self) -> None:
        cfg = StabilityRunConfig()
        cmd = build_stability_launch_command(
            "/opt/emu/emulator", "test_avd", 5564, 8558, cfg
        )
        self.assertIn("-gpu", cmd)
        self.assertEqual(cmd[cmd.index("-gpu") + 1], "auto")
        self.assertIn("-no-window", cmd)
        self.assertNotIn("-no-audio", cmd)
        self.assertNotIn("-no-snapshot-save", cmd)

    def test_format_clickable_file_link_expands_tilde_and_encodes_parens(self) -> None:
        link = format_clickable_file_link("~/.android/emu_测试_🚀_é_(next).log")
        self.assertNotIn("~/", link)
        self.assertIn("file:///", link)
        self.assertIn("%28next%29", link)
        self.assertTrue(link.startswith("[file:///"))

        # Cross-platform POSIX and Windows path URI + OSC 8 formatting
        posix_uri = format_file_uri("/tmp/stability_测试_🚀_é_(1)/host.log")
        self.assertTrue(posix_uri.startswith("file:///tmp/"))
        self.assertIn("%281%29", posix_uri)

        win_uri = format_file_uri(r"C:\Users\dev\stability_(1)\host.log")
        self.assertEqual(win_uri, "file:///C:/Users/dev/stability_%281%29/host.log")

        osc8 = format_clickable_file_link(
            r"C:\Users\dev\stability_(1)\host.log", osc8=True
        )
        self.assertEqual(
            osc8,
            f"\x1b]8;;{win_uri}\x1b\\{win_uri}\x1b]8;;\x1b\\",
        )
        self.assertEqual(
            format_osc8_link(posix_uri, "host.log"),
            f"\x1b]8;;{posix_uri}\x1b\\host.log\x1b]8;;\x1b\\",
        )

        class _FakeStream:
            def __init__(self, tty: bool) -> None:
                self._tty = tty

            def isatty(self) -> bool:
                return self._tty

        self.assertTrue(supports_osc8_hyperlinks(_FakeStream(True)))
        self.assertFalse(supports_osc8_hyperlinks(_FakeStream(False)))


class StabilityForensicsSessionAndScenariosTest(unittest.TestCase):
    """Tests forensic bundle collection (including Unicode), scenario runners, and session loop."""

    def test_forensic_bundle_handles_unicode_label_and_minidumps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stab_测试_🚀_é_") as tmpdir:
            host_log = os.path.join(tmpdir, "host_测试_🚀_é.log")
            with open(host_log, "w", encoding="utf-8") as f:
                f.write("Boot completed in 4200 ms — 测试_🚀_é\n")

            dmp_dir = os.path.join(tmpdir, "crashpad")
            os.makedirs(dmp_dir, exist_ok=True)
            dmp_file = os.path.join(dmp_dir, "qemu_crash.dmp")
            start_epoch = time.time()
            with open(dmp_file, "wb") as f:
                f.write(b"MDMP_FAKE_MINIDUMP")

            def _fake_adb(cmd: tuple[str, ...]) -> ProcessResult:
                joined = " ".join(cmd)
                if "-b crash" in joined:
                    return ProcessResult(args=cmd, returncode=0, stdout="FATAL EXCEPTION: main\n")
                if "dumpsys dropbox" in joined:
                    return ProcessResult(args=cmd, returncode=0, stdout="system_server_anr\n")
                return ProcessResult(args=cmd, returncode=0, stdout="logcat line 测试_🚀_é\n")

            adb = AdbDevice("emulator-5554", process_runner=_ScriptedProcessRunner(_fake_adb))
            samples = [
                CycleHealthSample(
                    cycle_index=0,
                    elapsed_sec=12.0,
                    scenario="dev-loop",
                    process_alive=False,
                    crashpad_minidumps=1,
                )
            ]
            ev = evaluate_stability_samples(samples)
            bundle = collect_forensic_bundle(
                label="unicode_build_测试_🚀_é",
                samples=samples,
                evaluation=ev,
                session_start_epoch=start_epoch - 5.0,
                forensics_root=os.path.join(tmpdir, "bundles"),
                host_log_path=host_log,
                adb=adb,
                png_capture_fn=lambda: b"\x89PNG\r\n\x1a\nFAKE",
                extra_minidump_dirs=[dmp_dir],
            )
            self.assertTrue(os.path.isdir(bundle.bundle_dir))
            self.assertIn("timeseries_json", bundle.artifacts)
            self.assertIn("emulator_host_log", bundle.artifacts)
            self.assertIn("logcat_crash", bundle.artifacts)
            self.assertIn("last_frame_png", bundle.artifacts)
            self.assertEqual(len(bundle.minidump_files), 1)

    def test_session_loop_render_and_history_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            def _fake_adb(cmd: tuple[str, ...]) -> ProcessResult:
                joined = " ".join(cmd)
                if "getprop sys.boot_completed" in joined:
                    return ProcessResult(args=cmd, returncode=0, stdout="1\n")
                if "pidof system_server" in joined:
                    return ProcessResult(args=cmd, returncode=0, stdout="2222\n")
                return ProcessResult(args=cmd, returncode=0, stdout="")

            runner = _ScriptedProcessRunner(_fake_adb)
            clock_vals = [0.0, 0.0, 10.0, 12.0, 25.0, 35.0, 40.0]

            def _fake_clock() -> float:
                return clock_vals.pop(0) if clock_vals else 50.0

            cfg = StabilityRunConfig(
                tier="smoke",
                duration_sec=30.0,
                min_cycles=2,
                max_cycles=2,
                settle_seconds=0.0,
                clean_avd=False,
                label="candidate-测试_🚀_é",
                forensics_dir=tmpdir,
                always_bundle_forensics=True,
            )

            # Inject deterministic host health via current process PID
            report = run_stability_session_against_device(
                serial="emulator-5554",
                config=cfg,
                emu_pid=os.getpid(),
                process_runner=runner,
                scenario_dispatcher=lambda scen, _ctx, idx: ScenarioCycleOutcome(
                    adb_ping_ms=4.2,
                    touch_latency_p95_ms=18.0 + idx,
                    idle_cpu_pct=3.5,
                    frame_non_black=True,
                    frame_pixel_std=42.0,
                ),
                sleep_fn=lambda _s: None,
                clock_fn=_fake_clock,
                record_history=False,
            )

            self.assertEqual(report["evaluation"]["verdict"], StabilityVerdict.PASS.value)
            self.assertEqual(report["evaluation"]["cycles_completed"], 2)
            self.assertIsNotNone(report["forensics"])

            md = render_stability_report_markdown(report)
            self.assertIn("PASS", md)
            self.assertIn("candidate-测试_🚀_é", md)

            hist_path = os.path.join(tmpdir, "history.jsonl")
            append_stability_history(report, history_path=hist_path)
            loaded = load_stability_history(limit=5, history_path=hist_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["label"], "candidate-测试_🚀_é")

            # Verify A/B comparison rendering
            degraded_rep = json.loads(json.dumps(report))
            degraded_rep["label"] = "regressed-build"
            degraded_rep["evaluation"]["verdict"] = StabilityVerdict.DEGRADED.value
            degraded_rep["evaluation"]["rss_slope_mib_per_min"] = 8.5
            comp = compare_stability_reports(report, degraded_rep)
            self.assertEqual(comp["overall_verdict"], StabilityVerdict.DEGRADED.value)
            comp_md = render_stability_comparison_markdown(comp)
            self.assertIn("DEGRADED", comp_md)

    def test_dev_loop_and_snapshot_and_ui_control_scenarios_hermetic(self) -> None:
        class _FakeImg:
            def __init__(self) -> None:
                self.image = bytes(([30, 120, 240] + [240, 80, 20]) * (64 * 32))
                self.seq = 1

        class _FakeStream:
            def __init__(self, frames: List[Any]) -> None:
                self._frames = frames

            def __iter__(self):
                return iter(self._frames)

            def cancel(self) -> None:
                return None

        class _FakeControllerStub:
            def __init__(self) -> None:
                self.rotations: List[Any] = []
                self.sensors: List[Any] = []
                self.postures: List[Any] = []

            def getScreenshot(self, _fmt: Any, timeout: Optional[float] = None) -> Any:
                del timeout
                from aemu.proto.emulator_controller_pb2 import Image

                return Image(image=_FakeImg().image, seq=1)

            def getStatus(self, _req: Any, timeout: Optional[float] = None) -> Any:
                del timeout
                from aemu.proto.emulator_controller_pb2 import EmulatorStatus

                return EmulatorStatus(version="37.3.0", booted=True, uptime=5000)

            def streamScreenshot(self, _fmt: Any) -> Any:
                from aemu.proto.emulator_controller_pb2 import Image

                return _FakeStream([
                    Image(image=_FakeImg().image, seq=1),
                    Image(image=_FakeImg().image, seq=2),
                ])

            def setPhysicalModel(self, val: Any, timeout: Optional[float] = None) -> None:
                del timeout
                self.rotations.append(val)

            def setSensor(self, val: Any, timeout: Optional[float] = None) -> None:
                del timeout
                self.sensors.append(val)

            def setPosture(self, val: Any, timeout: Optional[float] = None) -> None:
                del timeout
                self.postures.append(val)

        class _FakeChannel:
            def close(self) -> None:
                return None

        def _fake_adb(cmd: tuple[str, ...]) -> ProcessResult:
            joined = " ".join(cmd)
            if "echo ok" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="ok\n")
            if "pidof system_server" in joined:
                return ProcessResult(args=cmd, returncode=0, stdout="1888\n")
            if "dumpsys gfxinfo" in joined:
                return ProcessResult(
                    args=cmd,
                    returncode=0,
                    stdout="Total frames rendered: 100\nJanky frames: 4 (4.00%)\n",
                )
            return ProcessResult(args=cmd, returncode=0, stdout="OK\n")

        scripted_runner = _ScriptedProcessRunner(_fake_adb)
        adb = AdbDevice("emulator-5554", process_runner=scripted_runner)
        fake_stub = _FakeControllerStub()
        ctx = StabilityScenarioContext(
            adb=adb,
            emu_pid=None,
            grpc_port=8554,
            idle_settle_sec=0.0,
            grpc_channel_factory=lambda _s, _p, _g: _FakeChannel(),
            controller_stub_factory=lambda _ch: fake_stub,
            sleep_fn=lambda _s: None,
        )

        snap_out = run_snapshot_stress_cycle(ctx, 0)
        self.assertEqual(snap_out.expected_system_server_pid, 1888)
        self.assertTrue(snap_out.frame_non_black)
        self.assertTrue(ctx.snapshot_created)

        # If a preceding cycle left the guest unhealthy, snapshot-stress skips save to avoid poisoning
        # the clean baseline snapshot and invalidates PerfProbeSession install state on load.
        adb_calls_before = len(scripted_runner.calls)
        ctx.last_cycle_healthy = False
        probe = PerfProbeSession(
            serial="emulator-5554",
            adb_fn=adb.as_adb_fn(default_timeout=10.0),
            apk_path="/tmp/fake_probe.apk",
        )
        probe._installed = True
        ctx.probe_session = probe
        snap_rec = run_snapshot_stress_cycle(ctx, 1)
        self.assertIn("skipped_save_unhealthy_guest", snap_rec.notes)
        self.assertIn("invalidated_probe_install_state", snap_rec.notes)
        self.assertFalse(probe._installed)
        self.assertEqual(snap_rec.expected_system_server_pid, 1888)
        recent_emu_cmds = [
            " ".join(c) for c in scripted_runner.calls[adb_calls_before:] if "snapshot" in " ".join(c)
        ]
        self.assertTrue(any("snapshot load" in c for c in recent_emu_cmds))
        self.assertFalse(any("snapshot save" in c for c in recent_emu_cmds))

        ui_out = run_ui_control_cycle(ctx, 0)
        self.assertTrue(ui_out.frame_non_black)
        self.assertEqual(ui_out.jank_pct, 4.0)
        self.assertEqual(len(fake_stub.rotations), 3)
        self.assertEqual(len(fake_stub.sensors), 2)
        self.assertEqual(len(fake_stub.postures), 2)

    def test_perf_probe_session_preserves_logcat_and_recovers_missing_activity(self) -> None:
        recorded_cmds: List[List[str]] = []
        logcat_lines: List[str] = [
            '11-13 12:00:00.100 1000 1000 I PerfProbeJson: {"scenario": "reset_input", "status": "ok"}'
        ]
        am_attempts = [0]

        class _Completed:
            def __init__(self, rc: int, out: str = "", err: str = "") -> None:
                self.returncode = rc
                self.stdout = out
                self.stderr = err

        def _fake_adb_fn(args: List[str], _timeout: float) -> Any:
            recorded_cmds.append(list(args))
            joined = " ".join(args)
            if "logcat -d -s PerfProbeJson:I" in joined:
                return _Completed(0, "\n".join(logcat_lines) + "\n")
            if "--es scenario reset_input" in joined:
                am_attempts[0] += 1
                if am_attempts[0] == 1:
                    # Simulate snapshot load reverting package install
                    return _Completed(
                        0,
                        "Starting: Intent { cmp=com.android.emulator.perfprobe/.PerfProbeActivity }\n"
                        "Error type 3\n"
                        "Error: Activity class {com.android.emulator.perfprobe/com.android.emulator.perfprobe.PerfProbeActivity} does not exist.\n",
                    )
                logcat_lines.append(
                    '11-13 12:00:05.200 2000 2000 I PerfProbeJson: {"scenario": "reset_input", "status": "ok", "seq": 2}'
                )
                return _Completed(0, "Starting: Intent\n")
            return _Completed(0, "Success\n")

        session = PerfProbeSession(
            serial="emulator-5554",
            adb_fn=_fake_adb_fn,
            apk_path="/tmp/perf_probe_测试_🚀_é.apk",
        )
        session._installed = True
        result = session.run_intent_scenario("reset_input", timeout_sec=2.0)
        self.assertEqual(result.get("seq"), 2)
        # Verify logcat -c was never invoked so forensic logcat history is preserved
        self.assertFalse(any("logcat -c" in " ".join(c) for c in recorded_cmds))
        # Verify missing Activity class triggered automatic APK reinstall
        self.assertTrue(any("install -r -g" in " ".join(c) for c in recorded_cmds))


if __name__ == "__main__":
    unittest.main()
