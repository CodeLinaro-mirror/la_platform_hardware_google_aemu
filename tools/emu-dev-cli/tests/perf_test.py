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

"""Unit tests for `emu-dev-cli perf` telemetry, insights, target resolution, and CLI."""

import argparse
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from commands import perf
from lib.perf_insights import (
    append_perf_history,
    diff_thread_cpu,
    diff_top_symbols,
    evaluate_ab_significance,
    format_perf_history_markdown,
    generate_diagnostic_insights,
    load_perf_history,
)
from lib.perf_runner import (
    compare_perf_reports,
    inspect_emulator_dir_metadata,
    load_perf_report_any_format,
    render_comparison_commit_text,
    render_comparison_markdown,
    render_single_report_markdown,
    resolve_perf_target,
    resolve_profiles,
    resolve_suites,
    summarize_samples,
)
from lib.perf_telemetry import (
    ProcSnapshot,
    compute_proc_telemetry,
    normalize_thread_name,
    parse_perf_report_stdio,
    parse_perf_stat_output,
    parse_proc_stat_line,
    parse_proc_status,
    take_proc_snapshot,
)


class PerfTelemetryAndInsightsTest(unittest.TestCase):
    """Tests for src/lib/perf_telemetry.py, src/lib/perf_insights.py, and src/lib/perf_runner.py."""

    def test_parse_proc_stat_line_handles_spaces_and_parens_in_comm(self):
        # Fields after comm: state(0) ppid(1) pgrp(2) session(3) tty(4) tpgid(5)
        # flags(6) minflt(7) cminflt(8) majflt(9) cmajflt(10) utime(11) stime(12)
        stat_str = (
            "12345 (CPU 0/KVM (vcpu)) R 1 12345 12345 0 -1 4194560 "
            "100 0 0 0 450 150 0 0 20 0 12 0 987654 12345678 5000"
        )
        comm, utime, stime = parse_proc_stat_line(stat_str)
        self.assertEqual(comm, "CPU 0/KVM (vcpu)")
        self.assertEqual(utime, 450)
        self.assertEqual(stime, 150)

    def test_parse_proc_status_and_compute_telemetry(self):
        status_txt = (
            "Name:\tqemu-system-x86\n"
            "VmHWM:\t 2097152 kB\n"
            "VmRSS:\t 1048576 kB\n"
            "RssAnon:\t 524288 kB\n"
            "RssShmem:\t 524288 kB\n"
            "voluntary_ctxt_switches:\t1000\n"
            "nonvoluntary_ctxt_switches:\t250\n"
        )
        parsed = parse_proc_status(status_txt)
        self.assertAlmostEqual(parsed["VmRSS"], 1024.0)
        self.assertAlmostEqual(parsed["VmHWM"], 2048.0)
        self.assertEqual(parsed["voluntary_ctxt_switches"], 1000.0)

        s1 = ProcSnapshot(
            pid=100,
            timestamp=10.0,
            utime_ticks=1000,
            stime_ticks=500,
            rss_mib=1024.0,
            peak_rss_mib=1500.0,
            voluntary_ctxt_switches=100,
            nonvoluntary_ctxt_switches=20,
            thread_ticks={"CPU 0/KVM": 800, "libuv-loop": 400, "qemu-main": 300},
        )
        # 2 seconds elapsed at 100 Hz: +200 utime ticks (2.0s), +100 stime ticks (1.0s) => 150% total CPU
        s2 = ProcSnapshot(
            pid=100,
            timestamp=12.0,
            utime_ticks=1200,
            stime_ticks=600,
            rss_mib=1100.0,
            peak_rss_mib=1600.0,
            voluntary_ctxt_switches=500,
            nonvoluntary_ctxt_switches=70,
            thread_ticks={"CPU 0/KVM": 940, "libuv-loop": 500, "qemu-main": 360},
        )
        tel = compute_proc_telemetry(s1, s2, clk_tck=100)
        self.assertAlmostEqual(tel.host_cpu_pct, 150.0)
        self.assertAlmostEqual(tel.host_user_cpu_pct, 100.0)
        self.assertAlmostEqual(tel.host_sys_cpu_pct, 50.0)
        self.assertAlmostEqual(tel.host_cpu_ms, 3000.0)
        self.assertAlmostEqual(tel.rss_mib, 1100.0)
        self.assertAlmostEqual(tel.peak_rss_mib, 1600.0)
        self.assertEqual(tel.voluntary_ctxt_switches_delta, 400)
        self.assertAlmostEqual(tel.top_threads_cpu_pct["CPU 0/KVM"], 70.0)
        self.assertAlmostEqual(tel.top_threads_cpu_pct["libuv-loop"], 50.0)
        self.assertAlmostEqual(tel.top_threads_cpu_pct["qemu-main"], 30.0)

    def test_take_proc_snapshot_from_mock_procfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_dir = os.path.join(tmp, "4242")
            t1_dir = os.path.join(pid_dir, "task", "4242")
            t2_dir = os.path.join(pid_dir, "task", "4243")
            os.makedirs(t1_dir)
            os.makedirs(t2_dir)
            stat_template = "4242 ({comm}) S 1 1 1 0 -1 0 0 0 0 0 {u} {s} 0 0 20 0 2 0 100 1000 100\n"
            with open(os.path.join(pid_dir, "stat"), "w") as f:
                f.write(stat_template.format(comm="qemu-system-x86", u=300, s=100))
            with open(os.path.join(pid_dir, "status"), "w") as f:
                f.write("VmRSS:\t 512000 kB\nVmHWM:\t 512000 kB\n")
            with open(os.path.join(t1_dir, "stat"), "w") as f:
                f.write(stat_template.format(comm="qemu-system-x86", u=100, s=50))
            with open(os.path.join(t2_dir, "stat"), "w") as f:
                f.write(stat_template.format(comm="CPU 0/KVM", u=200, s=50))

            snap = take_proc_snapshot(4242, proc_root=tmp)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.utime_ticks, 300)
            self.assertEqual(snap.stime_ticks, 100)
            self.assertAlmostEqual(snap.rss_mib, 500.0)
            self.assertEqual(snap.thread_ticks["qemu-main"], 150)
            self.assertEqual(snap.thread_ticks["CPU 0/KVM"], 250)

    def test_parse_perf_stat_and_perf_report(self):
        stat_out = """
 Performance counter stats for process id '9999':

     2,500,000,000      cycles                    #    3.100 GHz
     3,750,000,000      instructions              #    1.50  insn per cycle
            14,200      context-switches          #    5.680 K/sec
               120      cpu-migrations
             1,050      page-faults

       1.002341123 seconds time elapsed
"""
        metrics = parse_perf_stat_output(stat_out)
        self.assertEqual(metrics["cycles"], 2500000000.0)
        self.assertEqual(metrics["instructions"], 3750000000.0)
        self.assertAlmostEqual(metrics["ipc"], 1.50)
        self.assertEqual(metrics["context-switches"], 14200.0)

        report_out = """
# Overhead  Command          Shared Object             Symbol
# ........  ...............  ........................  ........................................
#
    18.40%  qemu-system-x86  qemu-system-x86_64        [.] goldfish::SocketBuffer::append
    11.25%  qemu-system-x86  [kernel.kallsyms]         [k] copy_user_enhanced_fast_string
     6.10%  CPU 0/KVM        libgoldfish_vsock.so      [.] VirtioVsock::SendCreditRequest
"""
        syms = parse_perf_report_stdio(report_out)
        self.assertEqual(len(syms), 3)
        self.assertEqual(syms[0]["symbol"], "goldfish::SocketBuffer::append")
        self.assertAlmostEqual(syms[0]["overhead_pct"], 18.40)
        self.assertEqual(syms[1]["mode"], "kernel")

    def test_evaluate_ab_significance_gain_regression_and_noise(self):
        base = {
            "n": 10,
            "wall_mib_s_p50": 220.0,
            "wall_mib_s_mean": 221.0,
            "wall_mib_s_stdev": 8.0,
        }
        cand_gain = {
            "n": 10,
            "wall_mib_s_p50": 450.0,
            "wall_mib_s_mean": 448.0,
            "wall_mib_s_stdev": 10.0,
        }
        res_gain = evaluate_ab_significance(base, cand_gain)
        self.assertEqual(res_gain.verdict, "GAIN")
        self.assertIn("GAIN", res_gain.badge)
        self.assertGreater(res_gain.pct_delta_p50, 100.0)

        cand_noise = {
            "n": 10,
            "wall_mib_s_p50": 222.5,
            "wall_mib_s_mean": 223.0,
            "wall_mib_s_stdev": 12.0,
        }
        res_noise = evaluate_ab_significance(base, cand_noise)
        self.assertEqual(res_noise.verdict, "NOISE")

        cand_reg = {
            "n": 10,
            "wall_mib_s_p50": 160.0,
            "wall_mib_s_mean": 162.0,
            "wall_mib_s_stdev": 7.0,
        }
        res_reg = evaluate_ab_significance(base, cand_reg)
        self.assertEqual(res_reg.verdict, "REGRESSION")

    def test_generate_diagnostic_insights_heuristics(self):
        # Transport-bound + directional asymmetry + high CV
        tests = {
            "push_256MiB_tmpfs": {
                "wall_mib_s_p50": 215.0,
                "wall_mib_s_mean": 215.0,
                "wall_mib_s_stdev": 30.0,  # CV > 10%
                "cpu_ms_per_mib": 3.45,
                "top_threads_cpu_pct": {"libuv-loop": 42.0, "CPU 0/KVM": 35.0},
            },
            "pull_256MiB_tmpfs": {
                "wall_mib_s_p50": 120.0,
                "wall_mib_s_mean": 120.0,
                "wall_mib_s_stdev": 5.0,
            },
            "push_256MiB_disk": {
                "wall_mib_s_p50": 210.0,
                "wall_mib_s_mean": 210.0,
                "wall_mib_s_stdev": 5.0,
            },
            "push_64MiB_tmpfs": {
                "wall_mib_s_p50": 220.0,
            },
            "push_100x1MiB_dir": {
                "wall_mib_s_p50": 80.0,
            },
        }
        insights = generate_diagnostic_insights(tests, label="test-build")
        joined = "\n".join(insights)
        self.assertIn("High Variance Detected", joined)
        self.assertIn("Transport-Bound", joined)
        self.assertIn("Directional Asymmetry", joined)
        self.assertIn("Metadata / Turnaround Overhead", joined)
        self.assertIn("Work Cost", joined)

    def test_load_any_format_and_compare_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Test loading multi-target summary format (like /tmp/qemu_now_vs_next_base_bench.json)
            multi_path = os.path.join(tmp, "multi.json")
            multi_data = {
                "summary": {
                    "build_123": {
                        "push_256MiB_tmpfs": {
                            "n": 10,
                            "wall_mib_s_p50": 250.0,
                            "wall_mib_s_mean": 252.0,
                            "wall_mib_s_stdev": 10.0,
                            "cpu_ms_per_mib": 4.2,
                            "top_threads_cpu_pct": {"libuv-loop": 40.0, "CPU 0/KVM": 30.0},
                        }
                    },
                    "build_321": {
                        "push_256MiB_tmpfs": {
                            "n": 10,
                            "wall_mib_s_p50": 480.0,
                            "wall_mib_s_mean": 475.0,
                            "wall_mib_s_stdev": 12.0,
                            "cpu_ms_per_mib": 2.1,
                            "top_threads_cpu_pct": {"libuv-loop": 18.0, "CPU 0/KVM": 42.0},
                        }
                    },
                },
                "raw": {
                    "build_123": {"description": "Build 123 (Baseline)"},
                    "build_321": {"description": "Build 321 (Candidate)"},
                },
            }
            with open(multi_path, "w") as f:
                json.dump(multi_data, f)

            r_base = load_perf_report_any_format(f"{multi_path}:build_123")
            r_cand = load_perf_report_any_format(f"{multi_path}:build_321")
            self.assertEqual(r_base["label"], "Build 123 (Baseline)")
            self.assertEqual(r_cand["label"], "Build 321 (Candidate)")

            comp = compare_perf_reports(r_base, r_cand)
            self.assertEqual(len(comp["comparisons"]), 1)
            row = comp["comparisons"][0]
            self.assertEqual(row["verdict"], "GAIN")
            self.assertAlmostEqual(row["pct_delta_p50"], 92.0)
            self.assertIn("push_256MiB_tmpfs", comp["thread_diffs"])

            md = render_comparison_markdown(comp)
            self.assertIn("Workload Scorecard & Statistical Significance", md)
            self.assertIn("Host Emulator Per-Thread CPU Shift", md)
            self.assertIn("4.20 → 2.10", md)

            commit_txt = render_comparison_commit_text(comp)
            self.assertIn(
                "- push_256MiB_tmpfs: 250.0 -> 480.0 MiB/s p50 (+92.0%",
                commit_txt,
            )

    def test_perf_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist_file = os.path.join(tmp, "perf_history.jsonl")
            sample_rep = {
                "timestamp": "2026-10-10T19:00:00Z",
                "label": "build-15075377",
                "build_id": "15075377",
                "engine": "qemu-next",
                "avd": "Generic_Phone_API_36",
                "suites": ["adb-tmpfs"],
                "tests": {
                    "push_256MiB_tmpfs": {"n": 5, "wall_mib_s_p50": 465.2}
                },
            }
            append_perf_history(sample_rep, history_path=hist_file)
            loaded = load_perf_history(history_path=hist_file, limit=10)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["build_id"], "15075377")
            md = format_perf_history_markdown(loaded)
            self.assertIn("15075377", md)
            self.assertIn("465.2 MiB/s", md)

    def test_resolve_suites_and_profiles(self):
        suites, workloads = resolve_suites("adb-tmpfs,idle")
        self.assertEqual(suites, ["adb-tmpfs", "idle"])
        self.assertIn("push_256MiB_tmpfs", workloads)
        self.assertIn("idle_steady_state", workloads)

        profs = resolve_profiles("perf-record,perfetto")
        self.assertEqual(profs, ["proc", "perf-record", "perfetto"])

    def test_cli_register_and_list(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="subcommand")
        perf.register_parser(sub)

        args = parser.parse_args(["perf", "list"])
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            args.func(args)
        out = buf.getvalue()
        self.assertIn("Built-in Emulator Performance Suites", out)
        self.assertIn("adb-tmpfs", out)
        self.assertIn("perf-record", out)

    def test_broken_workload_in_compare(self):
        base = {
            "label": "stable-37.2.12",
            "tests": {
                "snapshot_load": {
                    "n": 3,
                    "duration_ms_p50": 2013.9,
                    "duration_ms_mean": 2013.9,
                }
            },
        }
        cand = {
            "label": "repo-release",
            "tests": {
                "snapshot_load": {
                    "n": 0,
                    "status": "broken",
                    "error": "KO: virtio-gpu-rutabaga -22",
                }
            },
        }
        comp = compare_perf_reports(base, cand)
        self.assertEqual(comp["comparisons"][0]["verdict"], "broken")
        self.assertEqual(comp["comparisons"][0]["badge"], "❌ BROKEN")
        md = render_comparison_markdown(comp)
        self.assertIn("❌ BROKEN", md)
        self.assertIn("**FAIL**", md)

    def test_resolve_perf_avd_template(self):
        from lib.perf_runner import _resolve_perf_avd_template

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "system.img"), "wb") as f:
                f.write(b"0")
            tmpl = _resolve_perf_avd_template(avd_name="my-avd", sysimg_dir=tmp)
            self.assertEqual(tmpl["sysimg_dir"], tmp)
            self.assertEqual(tmpl["cores"], 4)

    def test_guest_interrupts_vmstat_and_kvm_deltas(self):
        from lib.perf_telemetry import (
            compute_guest_hypervisor_deltas,
            compute_kvm_exit_deltas,
            parse_guest_proc_interrupts,
            parse_guest_proc_vmstat,
            parse_proc_stat_line_ex,
        )

        # Field 0 of rest is state ('R'), 11 is utime (500), 12 is stime (200), 40 is guest_time (350)
        rest_fields = [str(i) for i in range(45)]
        rest_fields[0] = "R"
        rest_fields[11] = "500"
        rest_fields[12] = "200"
        rest_fields[40] = "350"
        stat_line = f"999 (CPU 0/KVM) {' '.join(rest_fields)}"
        comm, utime, stime, gtime = parse_proc_stat_line_ex(stat_line)
        self.assertEqual(comm, "CPU 0/KVM")
        self.assertEqual(utime, 500)
        self.assertEqual(stime, 200)
        self.assertEqual(gtime, 350)

        intr_txt = """           CPU0       CPU1       CPU2       CPU3
 24:        120         30          0          0   PCI-MSI 32768-edge      virtio0-input.0
 28:       1000        500        200        100   PCI-MSI 49152-edge      virtio2-req.0
RES:       4000       3000       2000       1000   Rescheduling interrupts
TLB:        250        150        100         50   TLB shootdowns
"""
        irqs = parse_guest_proc_interrupts(intr_txt)
        self.assertEqual(irqs["virtio_input_irqs"], 150)
        self.assertEqual(irqs["virtio_blk_irqs"], 1800)
        self.assertEqual(irqs["reschedule_ipis"], 10000)
        self.assertEqual(irqs["tlb_shootdowns"], 550)

        vmstat_txt = "nr_free_pages 12345\npgfault 50000\npgmajfault 120\n"
        vms = parse_guest_proc_vmstat(vmstat_txt)
        self.assertEqual(vms["pgfault"], 50000)
        self.assertEqual(vms["pgmajfault"], 120)

        g1 = {
            "virtio_input_irqs": 100,
            "reschedule_ipis": 1000,
            "tlb_shootdowns": 50,
            "pgfault": 10000,
        }
        g2 = {
            "virtio_input_irqs": 280,
            "reschedule_ipis": 4500,
            "tlb_shootdowns": 350,
            "pgfault": 75536,
        }
        g_deltas = compute_guest_hypervisor_deltas(g1, g2)
        self.assertEqual(g_deltas["virtio_input_irqs"], 180)
        self.assertEqual(g_deltas["reschedule_ipis"], 3500)
        self.assertEqual(g_deltas["tlb_shootdowns"], 300)
        self.assertEqual(g_deltas["pgfault"], 65536)

        kvm1 = {"exits": 10000, "io_exits": 2000, "mmio_exits": 3000, "signal_exits": 50}
        kvm2 = {"exits": 25000, "io_exits": 8000, "mmio_exits": 7000, "signal_exits": 50}
        kvm_deltas = compute_kvm_exit_deltas(kvm1, kvm2)
        self.assertEqual(kvm_deltas["exits"], 15000)
        self.assertEqual(kvm_deltas["io_exits"], 6000)
        self.assertEqual(kvm_deltas["mmio_exits"], 4000)

    def test_perf_probe_logcat_parser_and_apk_resolution(self):
        from lib.perf_probe_client import (
            parse_perf_probe_logcat_lines,
            resolve_perf_probe_apk,
        )

        sample_logcat = (
            "10-11 00:05:00.100  4123  4123 I PerfProbeJson: "
            '{"scenario":"ready","status":"ok","lifecycle":"onCreate"}\n'
            "10-11 00:05:01.250  4123  4150 I PerfProbeJson: "
            '{"scenario":"disk_rand_4k_fsync","status":"ok","ops":250,"iops":845.2,"p50_ms":1.12,"p95_ms":2.04}\n'
        )
        records = parse_perf_probe_logcat_lines(sample_logcat)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["scenario"], "disk_rand_4k_fsync")
        self.assertAlmostEqual(records[1]["iops"], 845.2)

        apk_path = resolve_perf_probe_apk()
        self.assertTrue(os.path.isfile(apk_path))
        self.assertTrue(apk_path.endswith("perf_probe.apk"))
        import zipfile
        with zipfile.ZipFile(apk_path, "r") as zf:
            names = zf.namelist()
            self.assertIn("AndroidManifest.xml", names)
            self.assertIn("classes.dex", names)
            if "resources.arsc" in names:
                info = zf.getinfo("resources.arsc")
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                data_offset = (
                    info.header_offset
                    + 30
                    + len(info.filename.encode("utf-8"))
                    + len(info.extra)
                )
                self.assertEqual(data_offset % 4, 0)

    def test_probe_suites_comparison_and_markdown(self):
        suites, workloads = resolve_suites("input,disk,memory,cpu")
        self.assertEqual(suites, ["input", "disk", "memory", "cpu"])
        self.assertIn("key_arrival_latency", workloads)
        self.assertIn("touch_to_photon", workloads)
        self.assertIn("disk_seq_128mib", workloads)
        self.assertIn("disk_rand_4k_fsync", workloads)
        self.assertIn("mem_ept_page_fault", workloads)
        self.assertIn("cpu_compute_scaling", workloads)
        self.assertIn("cpu_ipi_ping_pong", workloads)

        base_rep = {
            "label": "stable-37.2.12",
            "nested_virt": True,
            "tests": {
                "key_arrival_latency": {
                    "n": 3,
                    "duration_ms_p50": 5.2,
                    "duration_ms_mean": 5.3,
                    "duration_ms_stdev": 0.3,
                    "kernel_to_app_ms_p50": 2.1,
                    "app_to_frame_ms_p50": 3.1,
                    "vcpu_guest_pct": 62.0,
                    "vcpu_kernel_exit_pct": 24.0,
                    "vcpu_userspace_exit_pct": 14.0,
                    "guest_virtio_irqs": {"virtio0-input.0": 80},
                },
                "disk_rand_4k_fsync": {
                    "n": 3,
                    "iops_p50": 620.0,
                    "iops_mean": 618.0,
                    "iops_stdev": 15.0,
                    "p50_ms_p50": 1.55,
                    "p95_ms_p50": 2.40,
                },
                "mem_ept_page_fault": {
                    "n": 3,
                    "pages_per_sec_p50": 180000.0,
                    "pages_per_sec_mean": 179000.0,
                    "pages_per_sec_stdev": 4000.0,
                    "us_per_page_p50": 5.55,
                    "guest_tlb_shootdowns": 42,
                    "guest_pgfault_delta": 65536,
                },
                "cpu_ipi_ping_pong": {
                    "n": 3,
                    "roundtrip_us_p50": 14.5,
                    "roundtrip_us_mean": 14.6,
                    "roundtrip_us_stdev": 0.4,
                    "switches_per_sec_p50": 137900.0,
                    "guest_ipi_resched": 6000,
                },
            },
        }
        cand_rep = {
            "label": "qemu-next",
            "nested_virt": True,
            "tests": {
                "key_arrival_latency": {
                    "n": 3,
                    "duration_ms_p50": 3.8,
                    "duration_ms_mean": 3.9,
                    "duration_ms_stdev": 0.2,
                    "kernel_to_app_ms_p50": 1.5,
                    "app_to_frame_ms_p50": 2.3,
                },
                "disk_rand_4k_fsync": {
                    "n": 3,
                    "iops_p50": 890.0,
                    "iops_mean": 885.0,
                    "iops_stdev": 20.0,
                    "p50_ms_p50": 1.08,
                    "p95_ms_p50": 1.75,
                },
                "mem_ept_page_fault": {
                    "n": 3,
                    "pages_per_sec_p50": 240000.0,
                    "pages_per_sec_mean": 238000.0,
                    "pages_per_sec_stdev": 5000.0,
                    "us_per_page_p50": 4.16,
                },
                "cpu_ipi_ping_pong": {
                    "n": 3,
                    "roundtrip_us_p50": 11.2,
                    "roundtrip_us_mean": 11.3,
                    "roundtrip_us_stdev": 0.3,
                    "switches_per_sec_p50": 178500.0,
                },
            },
        }
        base_rep["insights"] = generate_diagnostic_insights(
            base_rep["tests"], label="stable-37.2.12"
        )
        single_md = render_single_report_markdown(base_rep)
        self.assertIn("Hypervisor & Guest Interrupt Telemetry", single_md)
        self.assertIn("G:62% / K:24% / U:14%", single_md)
        self.assertIn("Input Dispatch Breakdown", single_md)
        self.assertIn("Hypervisor EPT / Demand-Paging", single_md)

        comp = compare_perf_reports(base_rep, cand_rep)
        verdicts = {r["workload"]: r["verdict"] for r in comp["comparisons"]}
        self.assertEqual(verdicts["key_arrival_latency"], "GAIN")
        self.assertEqual(verdicts["disk_rand_4k_fsync"], "GAIN")
        self.assertEqual(verdicts["mem_ept_page_fault"], "GAIN")
        self.assertEqual(verdicts["cpu_ipi_ping_pong"], "GAIN")

    def test_run_snapshot_workload_load_waits_for_device(self):
        from lib.perf_runner import run_snapshot_workload

        recorded_cmds = []

        def fake_adb_cmd(serial, args, adb_bin="adb", timeout=60.0):
            recorded_cmds.append(list(args))

            class _Res:
                returncode = 0
                stdout = "OK\r\n"
                stderr = ""

            return _Res()

        with patch(
            "lib.perf_runner._adb_cmd", side_effect=fake_adb_cmd
        ):
            res = run_snapshot_workload(
                workload_name="snapshot_load",
                serial="emulator-5560",
                emu_pid=None,
                iterations=2,
            )
        self.assertEqual(res["n"], 2)
        self.assertIn(
            ["emu", "avd", "snapshot", "save", "emu_perf_bench_snap"],
            recorded_cmds,
        )
        self.assertIn(
            ["emu", "avd", "snapshot", "load", "emu_perf_bench_snap"],
            recorded_cmds,
        )
        self.assertIn(["wait-for-device"], recorded_cmds)
        self.assertIn(
            ["emu", "avd", "snapshot", "delete", "emu_perf_bench_snap"],
            recorded_cmds,
        )

    def test_parity_badge_winsorization_and_submetric_summary(self):
        # 1. Tight CV (<5%) and <3% delta => PARITY badge
        b_parity = {
            "n": 4,
            "duration_ms_p50": 5.90,
            "duration_ms_mean": 5.90,
            "duration_ms_stdev": 0.02,
        }
        c_parity = {
            "n": 4,
            "duration_ms_p50": 5.87,
            "duration_ms_mean": 5.87,
            "duration_ms_stdev": 0.02,
        }
        sig_p = evaluate_ab_significance(
            b_parity, c_parity, metric_prefix="duration_ms", higher_is_better=False
        )
        self.assertEqual(sig_p.verdict, "NOISE")
        self.assertEqual(sig_p.badge, "🔵 PARITY")

        # 2. Single cold outlier sample in baseline with strict sample separation => GAIN
        b_outlier = {
            "n": 3,
            "duration_ms_p50": 3827.05,
            "duration_ms_mean": 4468.51,
            "duration_ms_stdev": 1358.93,
            "samples": [
                {"duration_ms": 6035.15},
                {"duration_ms": 3827.05},
                {"duration_ms": 3543.34},
            ],
        }
        c_clean = {
            "n": 3,
            "duration_ms_p50": 2818.68,
            "duration_ms_mean": 2795.69,
            "duration_ms_stdev": 128.76,
            "samples": [
                {"duration_ms": 2818.68},
                {"duration_ms": 2911.83},
                {"duration_ms": 2656.55},
            ],
        }
        sig_w = evaluate_ab_significance(
            b_outlier, c_clean, metric_prefix="duration_ms", higher_is_better=False
        )
        self.assertEqual(sig_w.verdict, "GAIN")
        self.assertEqual(sig_w.badge, "🟢 GAIN")

        # 3. summarize_samples populates <base>_mean and <base>_stdev for _p50 keys
        s_out = summarize_samples(
            [
                {"duration_ms": 10.0, "kernel_to_app_ms_p50": 1.40},
                {"duration_ms": 10.2, "kernel_to_app_ms_p50": 1.50},
                {"duration_ms": 10.1, "kernel_to_app_ms_p50": 1.60},
            ]
        )
        self.assertAlmostEqual(s_out["kernel_to_app_ms_p50"], 1.50)
        self.assertAlmostEqual(s_out["kernel_to_app_ms_mean"], 1.50)
        self.assertAlmostEqual(s_out["kernel_to_app_ms_stdev"], 0.10)

    def test_ensure_valid_console_auth_token_clears_short_tokens(self):
        from lib.perf_runner import _ensure_valid_console_auth_token

        with tempfile.TemporaryDirectory() as tmp:
            tok_file = os.path.join(tmp, ".emulator_console_auth_token")
            with open(tok_file, "wb") as f:
                f.write(b"x\n")
            with patch("os.path.expanduser", return_value=tok_file):
                _ensure_valid_console_auth_token()
            with open(tok_file, "rb") as f:
                self.assertEqual(f.read(), b"")

            # Valid >=12 byte token should be preserved
            with open(tok_file, "wb") as f:
                f.write(b"0123456789abcdef\n")
            with patch("os.path.expanduser", return_value=tok_file):
                _ensure_valid_console_auth_token()
            with open(tok_file, "rb") as f:
                self.assertEqual(f.read(), b"0123456789abcdef\n")

    def test_order_statistic_dominance_subframe_parity_and_weekly_report(self):
        from lib.perf_runner import render_weekly_qualification_report

        # 1. Sub-frame VSYNC latency jitter (< 1.0 ms p50 delta and < 0.5 ms mean delta with p > 0.05) -> PARITY (not NOISE)
        b_key = {
            "n": 5,
            "duration_ms_p50": 10.486,
            "duration_ms_mean": 10.538,
            "duration_ms_stdev": 0.270,
            "samples": [
                {"duration_ms": 10.275},
                {"duration_ms": 10.701},
                {"duration_ms": 10.486},
                {"duration_ms": 10.311},
                {"duration_ms": 10.915},
            ],
        }
        c_key = {
            "n": 5,
            "duration_ms_p50": 11.044,
            "duration_ms_mean": 10.724,
            "duration_ms_stdev": 0.829,
            "samples": [
                {"duration_ms": 10.285},
                {"duration_ms": 11.044},
                {"duration_ms": 11.336},
                {"duration_ms": 9.490},
                {"duration_ms": 11.466},
            ],
        }
        sig_key = evaluate_ab_significance(
            b_key, c_key, metric_prefix="duration_ms", higher_is_better=False
        )
        self.assertEqual(sig_key.badge, "🔵 PARITY")

        # 2. Order-statistic dominance: candidate beats baseline at every quantile (e.g. snapshot_load) -> GAIN
        b_snap = {
            "n": 3,
            "duration_ms_p50": 1468.37,
            "duration_ms_mean": 1635.22,
            "duration_ms_stdev": 305.03,
            "samples": [
                {"duration_ms": 1987.28},
                {"duration_ms": 1468.37},
                {"duration_ms": 1450.01},
            ],
        }
        c_snap = {
            "n": 3,
            "duration_ms_p50": 1401.87,
            "duration_ms_mean": 1437.71,
            "duration_ms_stdev": 67.79,
            "samples": [
                {"duration_ms": 1515.90},
                {"duration_ms": 1401.87},
                {"duration_ms": 1395.37},
            ],
        }
        sig_snap = evaluate_ab_significance(
            b_snap, c_snap, metric_prefix="duration_ms", higher_is_better=False
        )
        self.assertEqual(sig_snap.verdict, "GAIN")
        self.assertEqual(sig_snap.badge, "🟢 GAIN")

        # 3. Weekly report rendering covers all required sections and workloads
        comp = compare_perf_reports(
            {
                "label": "stable-37.2.12",
                "build_id": "16428233",
                "engine": "qemu-now",
                "tests": {
                    "key_arrival_latency": b_key,
                    "snapshot_load": b_snap,
                },
            },
            {
                "label": "tree (qemu-next)",
                "engine": "qemu-next",
                "tests": {
                    "key_arrival_latency": c_key,
                    "snapshot_load": c_snap,
                },
            },
        )
        report_md = render_weekly_qualification_report(comp)
        self.assertIn("# Weekly Emulator Performance Qualification Report", report_md)
        self.assertIn("Executive Summary", report_md)
        self.assertIn("Full 4-Part Performance Scorecard & Telemetry", report_md)
        self.assertIn("Key Wins & Architectural Improvements", report_md)
        self.assertIn("Regressions, Broken Workloads & Root-Cause Attribution", report_md)
        self.assertIn("key_arrival_latency", report_md)
        self.assertIn("snapshot_load", report_md)
        self.assertNotIn("⚪ NOISE", report_md)

    def test_stabilize_guest_os_after_first_boot(self):
        from lib.perf_runner import stabilize_guest_os_after_first_boot

        shell_cmds = []
        call_count = [0]

        def fake_adb_cmd(serial, args, adb_bin="adb", timeout=60.0):
            call_count[0] += 1
            if args and args[0] == "shell":
                shell_cmds.append(args[1])

            class _Res:
                returncode = 0
                stdout = ""
                stderr = ""

            res = _Res()
            if len(args) > 1 and "init.svc.bootanim" in args[1]:
                res.stdout = "stopped\n1\nPM_OK\n"
            elif len(args) > 1 and "dex2oat" in args[1]:
                # Simulate dex2oat busy on first poll, then drained on next 2 polls
                res.stdout = "dex2oat64\n" if call_count[0] == 3 else ""
            return res

        with (
            patch("lib.perf_runner._adb_cmd", side_effect=fake_adb_cmd),
            patch("time.sleep", return_value=None),
        ):
            info = stabilize_guest_os_after_first_boot(
                "emulator-5560", timeout_sec=30.0
            )
        self.assertEqual(info["last_busy_tasks"], "dex2oat64")
        joined_cmds = "\n".join(shell_cmds)
        self.assertIn("init.svc.bootanim", joined_cmds)
        self.assertIn("svc wifi disable", joined_cmds)
        self.assertIn("com.android.vending", joined_cmds)
        self.assertIn("cmd jobscheduler cancel android", joined_cmds)
        self.assertIn("window_animation_scale 0.0", joined_cmds)
        self.assertIn("dex2oat", joined_cmds)
        self.assertIn("drop_caches", joined_cmds)

    def test_parse_emulator_boot_completed_ms_and_cleanup_stale_netsim(self):
        from lib.perf_runner import (
            _cleanup_stale_netsim,
            _parse_emulator_boot_completed_ms,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_now = os.path.join(tmpdir, "qemu_now.log")
            with open(log_now, "w", encoding="utf-8") as f:
                f.write("INFO         | Boot completed in 13098 ms\n")
            self.assertEqual(_parse_emulator_boot_completed_ms(log_now), 13098.0)

            log_next = os.path.join(tmpdir, "qemu_next.log")
            with open(log_next, "w", encoding="utf-8") as f:
                f.write(
                    "WARNING | Boot completed in 8847 ms\nWARNING | Boot completed in 8847 ms\n"
                )
            self.assertEqual(_parse_emulator_boot_completed_ms(log_next), 8847.0)

            netsim_ini = os.path.join(tmpdir, "netsim.ini")
            with open(netsim_ini, "w", encoding="utf-8") as f:
                f.write("grpc.port=61599\n")
            with patch.dict(os.environ, {"TMPDIR": tmpdir}):
                _cleanup_stale_netsim(force=False)
            self.assertFalse(os.path.exists(netsim_ini))


if __name__ == "__main__":
    unittest.main()



