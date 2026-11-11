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

"""Normalization and A/B statistical comparison of emulator performance reports."""

import json
import os
from typing import Any, Dict, List, Tuple

from lib.perf_insights import (
    diff_thread_cpu,
    diff_top_symbols,
    evaluate_ab_significance,
    generate_diagnostic_insights,
)

MetricSpec = Tuple[str, str, bool, str]


def _normalize_standard_report(
    data: Dict[str, Any], base_label: str
) -> Dict[str, Any]:
    lbl = data.get("label") or data.get("description") or base_label
    tests = data["tests"]
    return {
        "schema_version": data.get("schema_version", 1),
        "label": lbl,
        "build_id": data.get("build_id"),
        "branch": data.get("branch"),
        "engine": data.get("engine"),
        "pkg_revision": data.get("pkg_revision"),
        "emulator_dir": data.get("emulator_dir"),
        "avd": data.get("avd"),
        "boots": data.get("boots", 1),
        "nested_virt": data.get("nested_virt"),
        "suites": data.get("suites"),
        "tests": tests,
        "insights": data.get("insights")
        or generate_diagnostic_insights(tests, label=lbl),
    }


def _normalize_summary_map_report(
    data: Dict[str, Any], subkey: Any, file_path: str
) -> Dict[str, Any]:
    summary_map = data["summary"]
    raw_map = data.get("raw", {})
    chosen_key = subkey or next(iter(summary_map.keys()))
    if chosen_key not in summary_map:
        valid = ", ".join(summary_map.keys())
        raise KeyError(
            f"Subkey '{chosen_key}' not found in {file_path} (available: {valid})"
        )
    tests = summary_map[chosen_key]
    desc = (
        raw_map.get(chosen_key, {}).get("description")
        if isinstance(raw_map, dict)
        else None
    ) or chosen_key
    return {
        "schema_version": 1,
        "label": desc,
        "tests": tests,
        "insights": generate_diagnostic_insights(tests, label=desc),
    }


def _normalize_step_list_report(
    data: List[Dict[str, Any]], subkey: Any, base_label: str
) -> Dict[str, Any]:
    chosen_entry = data[-1]
    if subkey:
        for item in data:
            if item.get("key") == subkey or item.get("description") == subkey:
                chosen_entry = item
                break
    lbl = (
        chosen_entry.get("description")
        or chosen_entry.get("key")
        or base_label
    )
    tests = chosen_entry["tests"]
    return {
        "schema_version": 1,
        "label": lbl,
        "tests": tests,
        "insights": generate_diagnostic_insights(tests, label=lbl),
    }


def load_perf_report_any_format(path_or_spec: str) -> Dict[str, Any]:
    """Loads a JSON benchmark report and normalizes all historical/ad-hoc schemas
    (`/tmp/adb_bench_*.json`, `/tmp/qemu_now_vs_next_base_bench.json`, `/tmp/vsock_stack_adb_bench.json`)
    into the standard `PerfRunReport` dictionary structure.
    """
    subkey = None
    file_path = path_or_spec
    if ":" in path_or_spec and not os.path.isfile(path_or_spec):
        file_path, subkey = path_or_spec.rsplit(":", 1)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    base_label = os.path.splitext(os.path.basename(file_path))[0]

    if isinstance(data, dict) and "tests" in data:
        return _normalize_standard_report(data, base_label)

    if (
        isinstance(data, dict)
        and "summary" in data
        and isinstance(data["summary"], dict)
    ):
        return _normalize_summary_map_report(data, subkey, file_path)

    if (
        isinstance(data, list)
        and data
        and isinstance(data[0], dict)
        and "tests" in data[0]
    ):
        return _normalize_step_list_report(data, subkey, base_label)

    raise ValueError(f"Unrecognized benchmark JSON schema in {file_path}")


def _has_key(key: str, bt: Dict[str, Any], ct: Dict[str, Any]) -> bool:
    return key in bt or key in ct


def _resolve_duration_metric_specs(
    workload: str, bt: Dict[str, Any], ct: Dict[str, Any]
) -> List[MetricSpec]:
    specs: List[MetricSpec] = [("duration_ms", "ms", False, workload)]
    if workload == "key_arrival_latency":
        if _has_key("kernel_to_app_ms_p50", bt, ct):
            specs.append(
                ("kernel_to_app_ms", "ms", False, "key_arrival_kernel_to_app")
            )
        if _has_key("app_to_frame_ms_p50", bt, ct):
            specs.append(
                ("app_to_frame_ms", "ms", False, "key_arrival_app_to_frame")
            )
    elif workload == "touch_to_photon":
        if _has_key("guest_kernel_to_app_ms_p50", bt, ct):
            specs.append(
                (
                    "guest_kernel_to_app_ms",
                    "ms",
                    False,
                    "touch_kernel_to_app",
                )
            )
        if _has_key("renderer_and_readback_ms_p50", bt, ct):
            specs.append(
                (
                    "renderer_and_readback_ms",
                    "ms",
                    False,
                    "touch_host_readback",
                )
            )
    return specs


def resolve_workload_metric_specs(
    workload: str, bt: Dict[str, Any], ct: Dict[str, Any]
) -> List[MetricSpec]:
    """Resolves the ordered `(metric_prefix, unit, higher_is_better, row_name)` tuples for a workload."""
    if workload == "disk_seq_128mib" and _has_key("write_mib_s_p50", bt, ct):
        return [
            ("write_mib_s", "MiB/s", True, "disk_seq_128mib_write"),
            ("read_mib_s", "MiB/s", True, "disk_seq_128mib_read"),
        ]
    if _has_key("iops_p50", bt, ct):
        return [("iops", "IOPS", True, workload)]
    if _has_key("pages_per_sec_p50", bt, ct):
        return [("pages_per_sec", "pages/s", True, workload)]
    if _has_key("cpu_nt_mops_s_p50", bt, ct):
        return [
            ("cpu_nt_mops_s", "Mops/s", True, f"{workload}_multi_vcpu"),
            ("cpu_1t_mops_s", "Mops/s", True, f"{workload}_single_vcpu"),
        ]
    if _has_key("roundtrip_us_p50", bt, ct):
        return [("roundtrip_us", "us", False, workload)]
    if _has_key("wall_mib_s_p50", bt, ct):
        return [("wall_mib_s", "MiB/s", True, workload)]
    if _has_key("duration_ms_p50", bt, ct):
        return _resolve_duration_metric_specs(workload, bt, ct)
    if workload == "idle_steady_state" and _has_key("host_cpu_pct", bt, ct):
        specs: List[MetricSpec] = [("host_cpu_pct", "% CPU", False, workload)]
        if _has_key("rss_mib", bt, ct):
            specs.append(("rss_mib", "MiB RSS", False, "idle_rss_mib"))
        return specs
    if workload == "display_gfxinfo":
        gfx_specs: List[MetricSpec] = []
        if _has_key("jank_pct", bt, ct):
            gfx_specs.append(("jank_pct", "% jank", False, "display_jank_pct"))
        if _has_key("host_cpu_pct", bt, ct):
            gfx_specs.append(
                ("host_cpu_pct", "% CPU", False, "display_cpu_pct")
            )
        return gfx_specs
    if _has_key("host_cpu_pct", bt, ct):
        return [("host_cpu_pct", "% CPU", False, workload)]
    return []


def _build_broken_comparison_row(
    workload: str, bt: Dict[str, Any], ct: Dict[str, Any]
) -> Dict[str, Any]:
    b_p50 = float(
        bt.get("duration_ms_p50", bt.get("wall_mib_s_p50", 0.0)) or 0.0
    )
    c_p50 = float(
        ct.get("duration_ms_p50", ct.get("wall_mib_s_p50", 0.0)) or 0.0
    )
    unit = "MiB/s" if _has_key("wall_mib_s_p50", bt, ct) else "ms"
    return {
        "workload": workload,
        "metric": "status",
        "unit": unit,
        "baseline_p50": round(b_p50, 2),
        "candidate_p50": round(c_p50, 2),
        "baseline_mean": round(b_p50, 2),
        "candidate_mean": round(c_p50, 2),
        "baseline_stdev": 0.0,
        "candidate_stdev": 0.0,
        "pct_delta_p50": 0.0,
        "pct_delta_mean": 0.0,
        "verdict": "broken",
        "badge": "❌ BROKEN",
        "p_value": 0.0,
        "high_variance": False,
        "baseline_error": bt.get("error"),
        "candidate_error": ct.get("error"),
        "baseline_cpu_ms_per_mib": bt.get("cpu_ms_per_mib"),
        "candidate_cpu_ms_per_mib": ct.get("cpu_ms_per_mib"),
    }


def _build_metric_comparison_row(
    bt: Dict[str, Any],
    ct: Dict[str, Any],
    spec: MetricSpec,
) -> Dict[str, Any]:
    metric_prefix, unit, higher_is_better, row_name = spec
    sig = evaluate_ab_significance(
        bt,
        ct,
        metric_prefix=metric_prefix,
        higher_is_better=higher_is_better,
    )
    b_p50 = float(
        bt.get(f"{metric_prefix}_p50", bt.get(metric_prefix, 0.0)) or 0.0
    )
    c_p50 = float(
        ct.get(f"{metric_prefix}_p50", ct.get(metric_prefix, 0.0)) or 0.0
    )
    b_mean = float(bt.get(f"{metric_prefix}_mean", b_p50) or b_p50)
    c_mean = float(ct.get(f"{metric_prefix}_mean", c_p50) or c_p50)
    b_std = float(bt.get(f"{metric_prefix}_stdev", 0.0) or 0.0)
    c_std = float(ct.get(f"{metric_prefix}_stdev", 0.0) or 0.0)

    return {
        "workload": row_name,
        "metric": metric_prefix,
        "unit": unit,
        "baseline_p50": round(b_p50, 2),
        "candidate_p50": round(c_p50, 2),
        "baseline_mean": round(b_mean, 2),
        "candidate_mean": round(c_mean, 2),
        "baseline_stdev": round(b_std, 2),
        "candidate_stdev": round(c_std, 2),
        "pct_delta_p50": sig.pct_delta_p50,
        "pct_delta_mean": sig.pct_delta_mean,
        "verdict": sig.verdict,
        "badge": sig.badge,
        "p_value": sig.approx_p_value,
        "high_variance": sig.high_variance_warning,
        "baseline_cpu_ms_per_mib": bt.get("cpu_ms_per_mib"),
        "candidate_cpu_ms_per_mib": ct.get("cpu_ms_per_mib"),
        "baseline_host_cpu_pct": bt.get("host_cpu_pct"),
        "candidate_host_cpu_pct": ct.get("host_cpu_pct"),
    }


def _extract_report_meta(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "build_id": report.get("build_id"),
        "branch": report.get("branch"),
        "engine": report.get("engine"),
        "pkg_revision": report.get("pkg_revision"),
        "emulator_dir": report.get("emulator_dir"),
        "avd": report.get("avd"),
        "boots": report.get("boots", 1),
        "nested_virt": report.get("nested_virt"),
        "suites": report.get("suites"),
    }


def _collect_hypervisor_diffs(
    common_workloads: List[str],
    b_tests: Dict[str, Any],
    c_tests: Dict[str, Any],
) -> List[Dict[str, Any]]:
    hyp_keys = (
        "vcpu_guest_pct",
        "guest_virtio_irqs",
        "guest_ipi_resched",
        "guest_tlb_shootdowns",
        "guest_pgfault_delta",
    )
    hypervisor_diffs: List[Dict[str, Any]] = []
    for w in common_workloads:
        bt = b_tests.get(w, {})
        ct = c_tests.get(w, {})
        if not bt or not ct:
            continue
        if any(k in bt or k in ct for k in hyp_keys):
            hypervisor_diffs.append(
                {
                    "workload": w,
                    "base_vcpu_guest_pct": bt.get("vcpu_guest_pct", 0.0),
                    "base_vcpu_kernel_pct": bt.get("vcpu_kernel_exit_pct", 0.0),
                    "base_vcpu_user_pct": bt.get("vcpu_userspace_exit_pct", 0.0),
                    "cand_vcpu_guest_pct": ct.get("vcpu_guest_pct", 0.0),
                    "cand_vcpu_kernel_pct": ct.get("vcpu_kernel_exit_pct", 0.0),
                    "cand_vcpu_user_pct": ct.get("vcpu_userspace_exit_pct", 0.0),
                    "base_virtio_irqs": bt.get("guest_virtio_irqs") or {},
                    "cand_virtio_irqs": ct.get("guest_virtio_irqs") or {},
                    "base_ipi_resched": bt.get("guest_ipi_resched", 0),
                    "cand_ipi_resched": ct.get("guest_ipi_resched", 0),
                    "base_tlb_shootdowns": bt.get("guest_tlb_shootdowns", 0),
                    "cand_tlb_shootdowns": ct.get("guest_tlb_shootdowns", 0),
                    "base_pgfault": bt.get("guest_pgfault_delta", 0),
                    "cand_pgfault": ct.get("guest_pgfault_delta", 0),
                }
            )
    return hypervisor_diffs


def compare_perf_reports(
    base_report: Dict[str, Any],
    cand_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Contrasts two normalized `PerfRunReport` dicts across throughput, latency,
    statistical significance, host CPU efficiency, thread shifts, and symbol diffs.
    """
    b_tests = base_report.get("tests") or {}
    c_tests = cand_report.get("tests") or {}
    common_workloads = [k for k in b_tests.keys() if k in c_tests]
    if not common_workloads:
        common_workloads = sorted(set(b_tests.keys()) | set(c_tests.keys()))

    comparisons: List[Dict[str, Any]] = []
    thread_diffs_by_test: Dict[str, List[Dict[str, Any]]] = {}
    symbol_diffs_by_test: Dict[str, List[Dict[str, Any]]] = {}

    for w in common_workloads:
        bt = b_tests.get(w, {})
        ct = c_tests.get(w, {})
        if not bt or not ct:
            continue

        if bt.get("status") == "broken" or ct.get("status") == "broken":
            comparisons.append(_build_broken_comparison_row(w, bt, ct))
            continue

        metric_specs = resolve_workload_metric_specs(w, bt, ct)
        for spec in metric_specs:
            comparisons.append(_build_metric_comparison_row(bt, ct, spec))

        if bt.get("top_threads_cpu_pct") or ct.get("top_threads_cpu_pct"):
            td = diff_thread_cpu(
                bt.get("top_threads_cpu_pct", {}),
                ct.get("top_threads_cpu_pct", {}),
            )
            if td:
                thread_diffs_by_test[w] = td

        if bt.get("top_symbols") or ct.get("top_symbols"):
            sd = diff_top_symbols(
                bt.get("top_symbols", []), ct.get("top_symbols", [])
            )
            if sd:
                symbol_diffs_by_test[w] = sd

    return {
        "baseline_label": base_report.get("label", "baseline"),
        "candidate_label": cand_report.get("label", "candidate"),
        "baseline_meta": _extract_report_meta(base_report),
        "candidate_meta": _extract_report_meta(cand_report),
        "comparisons": comparisons,
        "thread_diffs": thread_diffs_by_test,
        "symbol_diffs": symbol_diffs_by_test,
        "hypervisor_diffs": _collect_hypervisor_diffs(
            common_workloads, b_tests, c_tests
        ),
        "baseline_insights": base_report.get("insights")
        or generate_diagnostic_insights(
            b_tests, label=base_report.get("label", "baseline")
        ),
        "candidate_insights": cand_report.get("insights")
        or generate_diagnostic_insights(
            c_tests, label=cand_report.get("label", "candidate")
        ),
    }
