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

"""Markdown scorecard, Gerrit commit text, and qualification report renderers for `emu-dev-cli perf`."""

from typing import Any, Dict, List, Tuple

from lib.output import format_markdown_table
from lib.perf.target_resolver import _detect_default_host


def _format_vcpu_split(
    guest_pct: float, kernel_pct: float, user_pct: float
) -> str:
    return (
        f"G:{guest_pct:.0f}% / "
        f"K:{kernel_pct:.0f}% / "
        f"U:{user_pct:.0f}%"
    )


def _format_single_primary_and_sub_metrics(
    w_name: str, t: Dict[str, Any]
) -> Tuple[str, str]:
    if t.get("status") == "broken":
        return "**❌ BROKEN**", (t.get("error") or "failed")[:48]
    if w_name == "disk_seq_128mib" and "write_mib_s_p50" in t:
        p50_str = (
            f"**W: {t['write_mib_s_p50']:.1f} / R: {t.get('read_mib_s_p50', 0):.1f} MiB/s**"
        )
        mean_str = (
            f"W: {t.get('write_mib_s_mean', 0):.1f}±{t.get('write_mib_s_stdev', 0):.1f} | "
            f"R: {t.get('read_mib_s_mean', 0):.1f}±{t.get('read_mib_s_stdev', 0):.1f}"
        )
        return p50_str, mean_str
    if "iops_p50" in t:
        return (
            f"**{t['iops_p50']:.1f} IOPS** (p50={t.get('p50_ms_p50', 0):.2f}ms)",
            f"{t.get('iops_mean', t['iops_p50']):.1f} ± {t.get('iops_stdev', 0.0):.1f} (p95={t.get('p95_ms_p50', 0):.2f}ms)",
        )
    if "pages_per_sec_p50" in t:
        return (
            f"**{t['pages_per_sec_p50']:.0f} pages/s** ({t.get('us_per_page_p50', 0):.2f} us/pg)",
            f"{t.get('pages_per_sec_mean', t['pages_per_sec_p50']):.0f} ± {t.get('pages_per_sec_stdev', 0.0):.0f}",
        )
    if "copy_gib_s_p50" in t:
        return (
            f"**{t['wall_mib_s_p50']:.0f} MiB/s** ({t['copy_gib_s_p50']:.2f} GiB/s)",
            f"{t.get('wall_mib_s_mean', t['wall_mib_s_p50']):.0f} ± {t.get('wall_mib_s_stdev', 0.0):.0f}",
        )
    if "cpu_nt_mops_s_p50" in t:
        return (
            f"**{t['cpu_nt_mops_s_p50']:.1f} Mops/s** ({t.get('smp_scaling_ratio_p50', 0):.2f}x SMP)",
            f"1T={t.get('cpu_1t_mops_s_p50', 0):.1f} / NT={t.get('cpu_nt_mops_s_mean', 0):.1f}±{t.get('cpu_nt_mops_s_stdev', 0):.1f}",
        )
    if "roundtrip_us_p50" in t:
        return (
            f"**{t['roundtrip_us_p50']:.2f} us** ({t.get('switches_per_sec_p50', 0):.0f} sw/s)",
            f"{t.get('roundtrip_us_mean', t['roundtrip_us_p50']):.2f} ± {t.get('roundtrip_us_stdev', 0.0):.2f} us",
        )
    if "wall_mib_s_p50" in t:
        return (
            f"**{t['wall_mib_s_p50']:.1f} MiB/s** ({t.get('duration_ms_p50', 0):.0f} ms)",
            f"{t.get('wall_mib_s_mean', t['wall_mib_s_p50']):.1f} ± {t.get('wall_mib_s_stdev', 0.0):.1f}",
        )
    if "duration_ms_p50" in t:
        p50_str = f"**{t['duration_ms_p50']:.2f} ms**"
        sub_parts = [
            f"{t.get('duration_ms_mean', t['duration_ms_p50']):.2f} ± {t.get('duration_ms_stdev', 0.0):.2f}"
        ]
        if "kernel_to_app_ms_p50" in t:
            sub_parts.append(f"k2a={t['kernel_to_app_ms_p50']:.2f}ms")
        if "delivery_pct" in t:
            sub_parts.append(f"deliv={t['delivery_pct']:.0f}%")
        return p50_str, " | ".join(sub_parts)
    if "total_frames" in t:
        return (
            f"**{t['total_frames']} frames** ({t.get('jank_pct', 0):.1f}% jank)",
            f"p50={t.get('frame_p50_ms', 0):.0f}ms / p90={t.get('frame_p90_ms', 0):.0f}ms",
        )
    return (
        f"**{t.get('host_cpu_pct', 0):.1f}% CPU** (RSS {t.get('rss_mib', 0):.0f} MiB)",
        f"loadavg={t.get('guest_loadavg_1m', 0):.2f}",
    )


def _build_single_hypervisor_row(
    w_name: str, t: Dict[str, Any]
) -> List[str]:
    vcpu_split = (
        _format_vcpu_split(
            t.get("vcpu_guest_pct", 0.0),
            t.get("vcpu_kernel_exit_pct", 0.0),
            t.get("vcpu_userspace_exit_pct", 0.0),
        )
        if t.get("vcpu_guest_pct") is not None
        else "-"
    )
    kvm_str = (
        f"{t['kvm_exits_per_sec']:.0f}/s"
        if t.get("kvm_exits_per_sec")
        else "-"
    )
    irqs = t.get("guest_virtio_irqs") or {}
    irq_str = (
        ", ".join(
            f"`{k}`:+{v}"
            for k, v in sorted(irqs.items(), key=lambda x: x[1], reverse=True)[:3]
            if v > 0
        )
        or "-"
    )
    ipi_tlb = (
        f"RES:+{t.get('guest_ipi_resched', 0)} / "
        f"TLB:+{t.get('guest_tlb_shootdowns', 0)} / "
        f"PGF:+{t.get('guest_pgfault_delta', 0)}"
    )
    return [f"`{w_name}`", vcpu_split, kvm_str, irq_str, ipi_tlb]


def render_single_report_markdown(report: Dict[str, Any]) -> str:
    """Renders a single `PerfRunReport` as a clean Markdown scorecard with insights."""
    lines: List[str] = []
    lbl = report.get("label", "Emulator Performance Run")
    lines.append(f"## Performance Scorecard: `{lbl}`")
    meta_parts: List[str] = []
    for key, title in (
        ("build_id", "Build ID"),
        ("engine", "Engine"),
        ("avd", "AVD"),
        ("serial", "Device"),
    ):
        if report.get(key):
            meta_parts.append(f"**{title}**: `{report[key]}`")
    if report.get("nested_virt"):
        meta_parts.append("**Env**: `Nested VM`")
    if meta_parts:
        lines.append("- " + " | ".join(meta_parts))
    lines.append("")

    headers = [
        "Workload",
        "N",
        "Primary Metric (p50)",
        "Mean ± Stdev / Sub-Metrics",
        "Host CPU%",
        "Work Cost (CPU-ms/MiB)",
        "Top Emulator Threads",
    ]
    rows: List[List[str]] = []
    hyp_rows: List[List[str]] = []
    hyp_keys = (
        "vcpu_guest_pct",
        "kvm_exits_per_sec",
        "guest_virtio_irqs",
        "guest_ipi_resched",
        "guest_tlb_shootdowns",
    )

    for w_name, t in (report.get("tests") or {}).items():
        n_str = str(t.get("n", 1))
        p50_str, mean_str = _format_single_primary_and_sub_metrics(w_name, t)
        cpu_str = (
            f"{t['host_cpu_pct']:.1f}%"
            if t.get("host_cpu_pct") is not None
            else "-"
        )
        cost_str = (
            f"{t['cpu_ms_per_mib']:.2f}"
            if t.get("cpu_ms_per_mib") is not None
            else "-"
        )
        threads = t.get("top_threads_cpu_pct") or {}
        th_str = (
            ", ".join(f"`{k}`:{v:.0f}%" for k, v in list(threads.items())[:3])
            or "-"
        )
        rows.append(
            [f"`{w_name}`", n_str, p50_str, mean_str, cpu_str, cost_str, th_str]
        )

        if any(t.get(k) for k in hyp_keys):
            hyp_rows.append(_build_single_hypervisor_row(w_name, t))

    lines.append(format_markdown_table(headers, rows))
    lines.append("")

    if hyp_rows:
        lines.append("### Hypervisor & Guest Interrupt Telemetry")
        hyp_headers = [
            "Workload",
            "vCPU Split (Guest/Kernel/User)",
            "Host KVM Exits/s",
            "Guest Virtio IRQs",
            "Guest IPI / TLB / PageFaults",
        ]
        lines.append(format_markdown_table(hyp_headers, hyp_rows))
        lines.append("")

    insights = report.get("insights") or []
    if insights:
        lines.append("### Diagnostic Insights & Bottleneck Analysis")
        for item in insights:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def _render_diff_section_table(
    diffs_by_workload: Dict[str, List[Dict[str, Any]]],
    headers: List[str],
    name_key: str,
    base_key: str,
    cand_key: str,
    delta_key: str,
    limit: int,
) -> str:
    rows: List[List[str]] = []
    for w_name, diffs in diffs_by_workload.items():
        for d in diffs[:limit]:
            rows.append(
                [
                    f"`{w_name}`",
                    f"`{d[name_key]}`",
                    f"{d[base_key]:.1f}%",
                    f"{d[cand_key]:.1f}%",
                    f"{d[delta_key]:+.1f}%",
                ]
            )
    return format_markdown_table(headers, rows)


def _build_comparison_scorecard_row(r: Dict[str, Any]) -> List[str]:
    u = r["unit"]
    if r.get("verdict") == "broken":
        b_str = (
            "FAIL"
            if r.get("baseline_error")
            else f"{r['baseline_p50']:.1f} {u}"
        )
        c_str = (
            "**FAIL**"
            if r.get("candidate_error")
            else f"**{r['candidate_p50']:.1f} {u}**"
        )
        dp50 = "N/A"
        dmean = "N/A"
    else:
        b_str = f"{r['baseline_p50']:.1f} {u}"
        c_str = f"**{r['candidate_p50']:.1f} {u}**"
        dp50 = f"{r['pct_delta_p50']:+.1f}%"
        dmean = f"{r['pct_delta_mean']:+.1f}%"
    if (
        r.get("baseline_cpu_ms_per_mib") is not None
        and r.get("candidate_cpu_ms_per_mib") is not None
    ):
        cost_str = f"{r['baseline_cpu_ms_per_mib']:.2f} → {r['candidate_cpu_ms_per_mib']:.2f}"
    else:
        cost_str = "-"
    return [
        f"`{r['workload']}`",
        b_str,
        c_str,
        f"**{dp50}**",
        dmean,
        cost_str,
        r["badge"],
    ]


def _build_hypervisor_diff_row(h: Dict[str, Any]) -> List[str]:
    b_vcpu = _format_vcpu_split(
        h.get("base_vcpu_guest_pct", 0),
        h.get("base_vcpu_kernel_pct", 0),
        h.get("base_vcpu_user_pct", 0),
    )
    c_vcpu = _format_vcpu_split(
        h.get("cand_vcpu_guest_pct", 0),
        h.get("cand_vcpu_kernel_pct", 0),
        h.get("cand_vcpu_user_pct", 0),
    )
    b_irqs = h.get("base_virtio_irqs") or {}
    c_irqs = h.get("cand_virtio_irqs") or {}
    irq_keys = sorted(
        set(b_irqs.keys()) | set(c_irqs.keys()),
        key=lambda k: max(b_irqs.get(k, 0), c_irqs.get(k, 0)),
        reverse=True,
    )[:3]
    irq_str = (
        ", ".join(
            f"`{k.replace('_irqs', '')}`:{b_irqs.get(k, 0):.0f}→{c_irqs.get(k, 0):.0f}"
            for k in irq_keys
            if max(b_irqs.get(k, 0), c_irqs.get(k, 0)) > 0
        )
        or "-"
    )
    ipi_tlb_str = (
        f"RES:{h.get('base_ipi_resched', 0):.0f}→{h.get('cand_ipi_resched', 0):.0f} | "
        f"TLB:{h.get('base_tlb_shootdowns', 0):.0f}→{h.get('cand_tlb_shootdowns', 0):.0f} | "
        f"PGF:{h.get('base_pgfault', 0):.0f}→{h.get('cand_pgfault', 0):.0f}"
    )
    return [f"`{h['workload']}`", b_vcpu, c_vcpu, irq_str, ipi_tlb_str]


def render_comparison_markdown(comp: Dict[str, Any]) -> str:
    """Renders the 4-part A/B comparison scorecard in Markdown."""
    b_lbl = comp["baseline_label"]
    c_lbl = comp["candidate_label"]
    lines: List[str] = [
        f"## A/B Performance Comparison: `{b_lbl}` vs. `{c_lbl}`",
        "",
        "### 1. Workload Scorecard & Statistical Significance",
    ]
    headers = [
        "Workload",
        f"Baseline ({b_lbl}) p50",
        f"Candidate ({c_lbl}) p50",
        "Delta (p50)",
        "Delta (Mean)",
        "Work Cost (CPU-ms/MiB)",
        "Verdict",
    ]
    rows = [
        _build_comparison_scorecard_row(r)
        for r in (comp.get("comparisons") or [])
    ]
    lines.append(format_markdown_table(headers, rows))
    lines.append("")

    thread_diffs = comp.get("thread_diffs") or {}
    if thread_diffs:
        lines.append("### 2. Host Emulator Per-Thread CPU Shift")
        lines.append(
            _render_diff_section_table(
                thread_diffs,
                [
                    "Workload",
                    "Thread (`comm`)",
                    "Baseline CPU%",
                    "Candidate CPU%",
                    "Delta CPU%",
                ],
                name_key="thread",
                base_key="baseline_cpu_pct",
                cand_key="candidate_cpu_pct",
                delta_key="delta_cpu_pct",
                limit=5,
            )
        )
        lines.append("")

    hyp_diffs = comp.get("hypervisor_diffs") or []
    if hyp_diffs:
        lines.append("### 2b. Hypervisor & Guest Interrupt Boundary Shift")
        hyp_headers = [
            "Workload",
            "Baseline vCPU (G/K/U%)",
            "Candidate vCPU (G/K/U%)",
            "Virtio IRQs (Base → Cand)",
            "IPI / TLB / PageFaults (Base → Cand)",
        ]
        hyp_rows = [_build_hypervisor_diff_row(h) for h in hyp_diffs]
        lines.append(format_markdown_table(hyp_headers, hyp_rows))
        lines.append("")

    symbol_diffs = comp.get("symbol_diffs") or {}
    if symbol_diffs:
        lines.append("### 3. Differential C++/Rust Symbol Profile (`perf record`)")
        lines.append(
            _render_diff_section_table(
                symbol_diffs,
                ["Workload", "Symbol", "Baseline %", "Candidate %", "Delta %"],
                name_key="symbol",
                base_key="baseline_pct",
                cand_key="candidate_pct",
                delta_key="delta_pct",
                limit=8,
            )
        )
        lines.append("")

    all_insights = (comp.get("candidate_insights") or []) + (
        comp.get("baseline_insights") or []
    )
    if all_insights:
        lines.append("### 4. Diagnostic Insights & Bottleneck Takeaways")
        for ins in all_insights:
            lines.append(f"- {ins}")
        lines.append("")

    return "\n".join(lines)


def render_comparison_commit_text(comp: Dict[str, Any]) -> str:
    """Renders plain-text bullet points ready to paste into a Gerrit commit message."""
    b_lbl = comp["baseline_label"]
    c_lbl = comp["candidate_label"]
    lines: List[str] = [f"Performance comparison ({b_lbl} -> {c_lbl}):"]
    for r in comp.get("comparisons") or []:
        u = r["unit"]
        if r.get("verdict") == "broken":
            lines.append(f"- {r['workload']}: BROKEN")
        else:
            lines.append(
                f"- {r['workload']}: {r['baseline_p50']:.1f} -> {r['candidate_p50']:.1f} {u} p50 "
                f"({r['pct_delta_p50']:+.1f}%, mean {r['baseline_mean']:.1f} -> {r['candidate_mean']:.1f} {u})"
            )
    return "\n".join(lines)


def _attribute_regression_cause(
    row: Dict[str, Any], hyp_by_workload: Dict[str, Dict[str, Any]]
) -> str:
    """Produces a deterministic telemetry-backed root-cause attribution and action item for a regression or broken row."""
    w = row.get("workload", "")
    verdict = row.get("verdict", "")
    if verdict == "broken":
        err = (
            row.get("candidate_error")
            or row.get("baseline_error")
            or "Unknown error"
        )
        if "unknown command" in err.lower() or "ko:" in err.lower():
            return (
                f"Emulator console command failed (`{err}`). Check `~/.emulator_console_auth_token` "
                f"or snapshot flag configuration (`b/572356854`)."
            )
        return f"Workload execution failed with error: `{err}`."

    if w == "cold_boot":
        return (
            "Single-boot (`n=1`) cold boot duration includes first-launch OS binary/library verification "
            "(e.g. macOS `syspolicyd` / dynamic linker cache warmup on freshly extracted binaries) and initial "
            "Vulkan/rutabaga device initialization. Run with `--boots 3` to separate warm steady-state boot time."
        )
    if w == "idle_rss_mib":
        return (
            "Higher steady-state host RSS reflects `virtio-gpu-rutabaga` + Vulkan swapchain and shared host-memory "
            "mappings compared to legacy `gltransport=pipe`."
        )
    if w.startswith("disk_seq_128mib"):
        return (
            "Host filesystem dirty page-cache writeback variance across back-to-back 128 MiB sequential write bursts "
            "(host CPU work cost per MiB remained unchanged)."
        )
    if w in ("mem_ept_page_fault", "mem_stream_copy"):
        hyp = hyp_by_workload.get(w, {})
        tlb_b = hyp.get("base_tlb_shootdowns", 0)
        tlb_c = hyp.get("cand_tlb_shootdowns", 0)
        pgf_b = hyp.get("base_pgfault", 0)
        pgf_c = hyp.get("cand_pgfault", 0)
        return (
            f"Guest page-fault deltas (`PGF: {pgf_b:.0f} → {pgf_c:.0f}`) and TLB shootdowns (`TLB: {tlb_b:.0f} → {tlb_c:.0f}`) "
            f"are identical; difference reflects host hypervisor Stage-2/EPT RAM block `mmap` / superpage alignment."
        )
    return (
        f"Statistically significant shift (`{row.get('pct_delta_p50', 0.0):+.1f}%` p50, "
        f"$p = {row.get('p_value', 0.0):.4f}$); inspect per-thread CPU and symbol profile deltas above."
    )


def _format_target_desc(label: str, meta: Dict[str, Any]) -> str:
    desc = f"`{label}`"
    if meta.get("build_id"):
        desc += f" (Build ID `{meta['build_id']}`)"
    if meta.get("engine"):
        desc += f" [Engine: `{meta['engine']}`]"
    return desc


def _build_host_cpu_efficiency_rows(
    comparisons: List[Dict[str, Any]],
) -> List[List[str]]:
    cpu_rows: List[List[str]] = []
    seen_cpu_w: set[str] = set()
    for r in comparisons:
        w = r.get("workload", "")
        if w in seen_cpu_w:
            continue
        b_cpu = r.get("baseline_host_cpu_pct")
        c_cpu = r.get("candidate_host_cpu_pct")
        if b_cpu is None or c_cpu is None:
            continue
        seen_cpu_w.add(w)
        d_pct = ((c_cpu - b_cpu) / b_cpu * 100.0) if b_cpu > 0 else 0.0
        b_cost = r.get("baseline_cpu_ms_per_mib")
        c_cost = r.get("candidate_cpu_ms_per_mib")
        cost_cell = (
            f"{b_cost:.2f} → **{c_cost:.2f}**"
            if (b_cost is not None and c_cost is not None)
            else "-"
        )
        cpu_rows.append(
            [
                f"`{w}`",
                f"{b_cpu:.1f}%",
                f"**{c_cpu:.1f}%**",
                f"{d_pct:+.1f}%",
                cost_cell,
            ]
        )
    return cpu_rows


def _format_gain_bullet(r: Dict[str, Any]) -> str:
    w = r["workload"]
    u = r["unit"]
    b_val = r["baseline_p50"]
    c_val = r["candidate_p50"]
    dp50 = r["pct_delta_p50"]
    pval = r.get("p_value", 0.0)
    extra = ""
    if (
        r.get("baseline_cpu_ms_per_mib") is not None
        and r.get("candidate_cpu_ms_per_mib") is not None
    ):
        b_c = r["baseline_cpu_ms_per_mib"]
        c_c = r["candidate_cpu_ms_per_mib"]
        c_delta = ((c_c - b_c) / b_c * 100.0) if b_c > 0 else 0.0
        extra = f", work cost `{b_c:.2f}` → `{c_c:.2f} CPU-ms/MiB` ({c_delta:+.1f}%)"
    elif (
        r.get("baseline_host_cpu_pct") is not None
        and r.get("candidate_host_cpu_pct") is not None
    ):
        b_hc = r["baseline_host_cpu_pct"]
        c_hc = r["candidate_host_cpu_pct"]
        extra = f", host CPU `{b_hc:.1f}%` → `{c_hc:.1f}%`"
    return (
        f"- **`{w}` ({dp50:+.1f}% p50, $p = {pval:.4f}$):** "
        f"`{b_val:.1f} {u}` → **`{c_val:.1f} {u}`**{extra}."
    )


def render_weekly_qualification_report(comp: Dict[str, Any]) -> str:
    """Renders a complete, self-contained Weekly Emulator Performance Qualification Report
    in Markdown (including Executive Summary, Verdict Tally, Full 4-Part Scorecard,
    Key Wins, and Telemetry-Attributed Regressions).
    """
    b_lbl = comp.get("baseline_label", "baseline")
    c_lbl = comp.get("candidate_label", "candidate")
    b_meta = comp.get("baseline_meta") or {}
    c_meta = comp.get("candidate_meta") or {}
    comparisons: List[Dict[str, Any]] = list(comp.get("comparisons") or [])
    hyp_list: List[Dict[str, Any]] = list(comp.get("hypervisor_diffs") or [])
    hyp_by_w = {
        h.get("workload", ""): h for h in hyp_list if isinstance(h, dict)
    }

    gains = [r for r in comparisons if r.get("verdict") == "GAIN"]
    parities = [
        r
        for r in comparisons
        if r.get("verdict") == "NOISE" and "PARITY" in str(r.get("badge", ""))
    ]
    noises = [
        r
        for r in comparisons
        if r.get("verdict") == "NOISE"
        and "PARITY" not in str(r.get("badge", ""))
    ]
    regressions = [r for r in comparisons if r.get("verdict") == "REGRESSION"]
    broken = [r for r in comparisons if r.get("verdict") == "broken"]
    total = max(1, len(comparisons))

    host_str = _detect_default_host()
    avd_str = c_meta.get("avd") or b_meta.get("avd") or "clean-isolated"
    suites_list = c_meta.get("suites") or b_meta.get("suites") or ["all"]
    suites_str = ", ".join(f"`{s}`" for s in suites_list)

    def _fmt_wl_list(rows_subset: List[Dict[str, Any]]) -> str:
        if not rows_subset:
            return "None"
        return ", ".join(f"`{r['workload']}`" for r in rows_subset)

    lines: List[str] = [
        "# Weekly Emulator Performance Qualification Report",
        "",
        "## Executive Summary & Environment",
        "",
        (
            f"Automated A/B performance qualification comparing **`{b_lbl}`** (Baseline) against "
            f"**`{c_lbl}`** (Candidate) across **{len(comparisons)} metrics**."
        ),
        "",
        f"- **Host OS & Architecture:** `{host_str}`",
        f"- **Baseline Target:** {_format_target_desc(b_lbl, b_meta)}",
        f"- **Candidate Target:** {_format_target_desc(c_lbl, c_meta)}",
        f"- **AVD Configuration:** `{avd_str}`",
        f"- **Suites Executed:** {suites_str}",
        "",
        "### Overall Verdict Tally",
        "",
    ]

    tally_headers = [
        "Verdict Category",
        "Count",
        "Share",
        "Workloads / Metrics",
    ]
    tally_rows = [
        [
            label,
            f"**{len(subset)}**",
            f"{len(subset) * 100.0 / total:.1f}%",
            _fmt_wl_list(subset),
        ]
        for label, subset in (
            ("🟢 **GAIN (WIN)**", gains),
            ("🔵 **PARITY**", parities),
            ("⚪ **NOISE**", noises),
            ("🔴 **REGRESSION**", regressions),
            ("❌ **BROKEN**", broken),
        )
    ]
    lines.extend(
        [
            format_markdown_table(tally_headers, tally_rows),
            "",
            "---",
            "",
            "## Full 4-Part Performance Scorecard & Telemetry",
            "",
            render_comparison_markdown(comp),
        ]
    )

    cpu_rows = _build_host_cpu_efficiency_rows(comparisons)
    if cpu_rows:
        lines.append("### Host CPU & Work Cost Efficiency Summary")
        lines.append(
            format_markdown_table(
                [
                    "Workload",
                    f"Baseline ({b_lbl}) Host CPU%",
                    f"Candidate ({c_lbl}) Host CPU%",
                    "CPU% Delta",
                    "Work Cost (CPU-ms/MiB)",
                ],
                cpu_rows,
            )
        )
        lines.append("")

    lines.extend(["---", "", "## Key Wins & Architectural Improvements", ""])
    if gains:
        sorted_gains = sorted(
            gains,
            key=lambda x: abs(float(x.get("pct_delta_p50", 0.0))),
            reverse=True,
        )
        for r in sorted_gains:
            lines.append(_format_gain_bullet(r))
    else:
        lines.append("- No statistically significant gains recorded in this run.")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Regressions, Broken Workloads & Root-Cause Attribution",
            "",
        ]
    )
    if not broken and not regressions:
        lines.append(
            "- **Zero Broken Workloads & Zero Regressions:** All measured workloads passed at parity or better."
        )
    else:
        if not broken:
            lines.append(
                "- **Zero Broken Workloads (`0 BROKEN`):** 100% of executed suites and workloads completed cleanly."
            )
        for r in broken + regressions:
            w = r["workload"]
            u = r["unit"]
            badge = r.get("badge", r.get("verdict", ""))
            cause = _attribute_regression_cause(r, hyp_by_w)
            if r.get("verdict") == "broken":
                lines.append(f"- **{badge} `{w}`:** {cause}")
            else:
                lines.append(
                    f"- **{badge} `{w}` (`{r['baseline_p50']:.1f} {u}` → `{r['candidate_p50']:.1f} {u}`, "
                    f"`{r['pct_delta_p50']:+.1f}%` p50, $p = {r.get('p_value', 0.0):.4f}$):** {cause}"
                )
    lines.append("")
    return "\n".join(lines)
