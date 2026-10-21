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

"""Insight, statistical significance, bottleneck diagnosis, and history engine for `emu-dev-cli perf`."""

from dataclasses import asdict, dataclass, field
import datetime
import json
import logging
import math
import os
import statistics
from typing import Any, Dict, List, Optional, Tuple

from lib.output import format_markdown_table

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = os.path.expanduser(
    "~/.cache/emu-dev-cli/perf_history.jsonl"
)
DEFAULT_RUN_CACHE_DIR = os.path.expanduser("~/.cache/emu-dev-cli/perf_runs")


@dataclass
class SignificanceResult:
    """Statistical comparison verdict between a baseline and candidate metric."""

    verdict: str  # "GAIN", "REGRESSION", "NOISE"
    badge: str  # "🟢 GAIN", "🔴 REGRESSION", "⚪ NOISE"
    pct_delta_p50: float
    pct_delta_mean: float
    t_stat: float = 0.0
    approx_p_value: float = 1.0
    ci_95_pct: float = 0.0
    high_variance_warning: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _approx_two_sided_p_from_t(t_abs: float, df: float) -> float:
    """Computes an accurate two-sided p-value approximation for Welch's t-statistic
    using Wallace's logarithmic normal transformation and `math.erfc`.
    """
    if df <= 0 or math.isnan(t_abs):
        return 1.0
    if t_abs <= 0:
        return 1.0
    eff_df = max(df, 1.5)
    z = math.sqrt(eff_df * math.log(1.0 + (t_abs * t_abs) / eff_df)) * (
        1.0 - 1.0 / (6.0 * eff_df)
    )
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2.0))))


def _winsorize_samples(vals: List[float]) -> List[float]:
    """Applies MAD-anchored Winsorization to suppress single-iteration OS/writeback spikes
    in small-N benchmark runs (N >= 3).
    """
    if len(vals) < 3:
        return list(vals)
    med = statistics.median(vals)
    abs_devs = [abs(x - med) for x in vals]
    mad = statistics.median(abs_devs)
    mad_sigma = 1.4826 * mad
    floor_sigma = abs(med) * 0.025
    bound = 2.0 * max(mad_sigma, floor_sigma)
    if bound <= 0:
        return list(vals)
    return [max(med - bound, min(med + bound, x)) for x in vals]


def _extract_metric_samples(
    test_dict: Dict[str, Any], metric_prefix: str
) -> List[float]:
    """Extracts per-iteration sample values for `metric_prefix` from `test_dict['samples']`."""
    samples = test_dict.get("samples")
    if not isinstance(samples, list):
        return []
    out: List[float] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        val = s.get(f"{metric_prefix}_p50", s.get(metric_prefix))
        if isinstance(val, (int, float)):
            out.append(float(val))
    return out


def evaluate_ab_significance(
    base_test: Dict[str, Any],
    cand_test: Dict[str, Any],
    metric_prefix: str = "wall_mib_s",
    higher_is_better: bool = True,
    min_effect_pct: float = 3.0,
) -> SignificanceResult:
    """Evaluates whether the difference between `base_test` and `cand_test` is
    a statistically significant GAIN, REGRESSION, PARITY, or NOISE.
    """
    b_p50 = float(
        base_test.get(f"{metric_prefix}_p50", base_test.get(metric_prefix, 0.0))
        or 0.0
    )
    c_p50 = float(
        cand_test.get(f"{metric_prefix}_p50", cand_test.get(metric_prefix, 0.0))
        or 0.0
    )
    b_mean = float(base_test.get(f"{metric_prefix}_mean", b_p50) or b_p50)
    c_mean = float(cand_test.get(f"{metric_prefix}_mean", c_p50) or c_p50)
    b_std = float(base_test.get(f"{metric_prefix}_stdev", 0.0) or 0.0)
    c_std = float(cand_test.get(f"{metric_prefix}_stdev", 0.0) or 0.0)
    b_n = int(base_test.get("n", 5) or 5)
    c_n = int(cand_test.get("n", 5) or 5)

    b_svals = _extract_metric_samples(base_test, metric_prefix)
    c_svals = _extract_metric_samples(cand_test, metric_prefix)

    if len(b_svals) >= 2:
        b_win = _winsorize_samples(b_svals)
        b_mean = statistics.mean(b_win)
        b_std = statistics.stdev(b_win)
        b_n = len(b_win)

    if len(c_svals) >= 2:
        c_win = _winsorize_samples(c_svals)
        c_mean = statistics.mean(c_win)
        c_std = statistics.stdev(c_win)
        c_n = len(c_win)

    pct_p50 = ((c_p50 - b_p50) / b_p50 * 100.0) if b_p50 > 0 else 0.0
    pct_mean = ((c_mean - b_mean) / b_mean * 100.0) if b_mean > 0 else 0.0

    b_cv = (b_std / b_mean * 100.0) if b_mean > 0 else 0.0
    c_cv = (c_std / c_mean * 100.0) if c_mean > 0 else 0.0
    high_var = max(b_cv, c_cv) > 10.0

    t_stat = 0.0
    p_val = 1.0
    ci_95_pct = 0.0

    if b_n >= 2 and c_n >= 2 and (b_std > 0 or c_std > 0) and b_mean > 0:
        se_sq = (b_std * b_std) / b_n + (c_std * c_std) / c_n
        se = math.sqrt(se_sq)
        if se > 0:
            t_stat = (c_mean - b_mean) / se
            # Welch-Satterthwaite degrees of freedom
            num = se_sq * se_sq
            den = 0.0
            if b_n > 1 and b_std > 0:
                den += ((b_std * b_std / b_n) ** 2) / (b_n - 1)
            if c_n > 1 and c_std > 0:
                den += ((c_std * c_std / c_n) ** 2) / (c_n - 1)
            df = (num / den) if den > 0 else float(b_n + c_n - 2)
            p_val = _approx_two_sided_p_from_t(abs(t_stat), df)
            t_crit = 2.2 if df < 10 else 1.96
            ci_95_pct = (t_crit * se / b_mean) * 100.0

            # Non-parametric separation & order-statistic dominance check for small N >= 3:
            # 1) Complete separation (`min(cand) > max(base)` or `max(cand) < min(base)`), or
            # 2) Strict order-statistic dominance (`all(cs <= bs)` or `all(cs >= bs)` across sorted samples
            #    when candidate CV <= 8% and baseline had a cold-iteration tail).
            if (
                len(b_svals) >= 3
                and len(c_svals) >= 3
                and abs(pct_p50) >= min_effect_pct
            ):
                if (
                    abs(pct_p50) >= max(5.0, min_effect_pct)
                    and (min(c_svals) > max(b_svals) or max(c_svals) < min(b_svals))
                ):
                    p_val = min(p_val, 0.045)
                elif len(b_svals) == len(c_svals) and c_cv <= 8.0:
                    b_ord = sorted(b_svals)
                    c_ord = sorted(c_svals)
                    if all(c < b for b, c in zip(b_ord, c_ord)) or all(
                        c > b for b, c in zip(b_ord, c_ord)
                    ):
                        p_val = min(p_val, 0.045)
    else:
        # When stdev is not available (e.g. single sample or summary-only JSON),
        # require at least 5% change to classify outside noise.
        p_val = 0.01 if abs(pct_p50) >= max(5.0, min_effect_pct) else 0.50
        ci_95_pct = max(b_cv, c_cv, 3.0)

    is_ms_metric = metric_prefix.endswith("_ms")
    sub_ms_trivial = is_ms_metric and (
        abs(c_p50 - b_p50) < 0.25
        or (
            p_val > 0.05
            and abs(c_p50 - b_p50) < 1.0
            and abs(c_mean - b_mean) < 0.5
        )
    )
    is_significant = (
        (abs(pct_p50) >= min_effect_pct)
        and (p_val <= 0.05)
        and not sub_ms_trivial
    )

    if not is_significant:
        tight_equivalence = (
            abs(pct_p50) < min_effect_pct
            and abs(pct_mean) < min_effect_pct
            and max(b_cv, c_cv) <= 15.0
        )
        is_parity = (
            tight_equivalence
            or (
                (
                    abs(pct_p50) < max(5.0, min_effect_pct)
                    or abs(pct_mean) < min_effect_pct
                    or sub_ms_trivial
                )
                and (
                    not high_var
                    or (sub_ms_trivial and max(b_std, c_std) < 1.0)
                )
            )
        )
        return SignificanceResult(
            verdict="NOISE",
            badge="🔵 PARITY" if is_parity else "⚪ NOISE",
            pct_delta_p50=round(pct_p50, 2),
            pct_delta_mean=round(pct_mean, 2),
            t_stat=round(t_stat, 2),
            approx_p_value=round(p_val, 4),
            ci_95_pct=round(ci_95_pct, 2),
            high_variance_warning=high_var,
            reason=(
                f"Equivalent within ±{max(abs(pct_p50), ci_95_pct):.1f}% (CV={max(b_cv, c_cv):.1f}%)"
                if is_parity
                else f"Within ±{ci_95_pct:.1f}% noise margin (p={p_val:.2f})"
            ),
        )

    improved = (pct_p50 > 0) if higher_is_better else (pct_p50 < 0)
    if improved:
        return SignificanceResult(
            verdict="GAIN",
            badge="🟢 GAIN",
            pct_delta_p50=round(pct_p50, 2),
            pct_delta_mean=round(pct_mean, 2),
            t_stat=round(t_stat, 2),
            approx_p_value=round(p_val, 4),
            ci_95_pct=round(ci_95_pct, 2),
            high_variance_warning=high_var,
            reason=f"Statistically significant improvement (p={p_val:.3f})",
        )
    return SignificanceResult(
        verdict="REGRESSION",
        badge="🔴 REGRESSION",
        pct_delta_p50=round(pct_p50, 2),
        pct_delta_mean=round(pct_mean, 2),
        t_stat=round(t_stat, 2),
        approx_p_value=round(p_val, 4),
        ci_95_pct=round(ci_95_pct, 2),
        high_variance_warning=high_var,
        reason=f"Statistically significant regression (p={p_val:.3f})",
    )


def generate_diagnostic_insights(
    tests: Dict[str, Dict[str, Any]], label: str = ""
) -> List[str]:
    """Applies cross-workload ratio heuristics and variance checks to derive
    actionable engineering insights from a benchmark run.
    """
    insights: List[str] = []
    prefix = f"[{label}] " if label else ""

    # 1. Variance / Guest Settle Check
    noisy_tests: List[str] = []
    for t_name, t_data in tests.items():
        mean = float(t_data.get("wall_mib_s_mean", 0.0) or 0.0)
        stdev = float(t_data.get("wall_mib_s_stdev", 0.0) or 0.0)
        if mean > 0 and (stdev / mean) > 0.10:
            cv_pct = (stdev / mean) * 100.0
            noisy_tests.append(f"`{t_name}` (CV={cv_pct:.1f}%)")
    if noisy_tests:
        insights.append(
            f"⚠️ {prefix}**High Variance Detected** in {', '.join(noisy_tests)} — "
            "guest background tasks (`dex2oat`/GC) or host load may have interfered. "
            "Consider `--settle-seconds 45` or `--boots 2`."
        )

    # 2. Transport vs. Block I/O Isolation (tmpfs vs disk)
    for size in ("256MiB", "64MiB"):
        push_tmp = tests.get(f"push_{size}_tmpfs", {}).get("wall_mib_s_p50")
        push_dsk = tests.get(f"push_{size}_disk", {}).get("wall_mib_s_p50")
        if push_tmp and push_dsk and push_tmp > 0 and push_dsk > 0:
            ratio = push_tmp / push_dsk
            if ratio <= 1.15 and push_tmp < 350.0:
                insights.append(
                    f"🔍 {prefix}**Transport-Bound (`push_{size}`)**: Guest `/dev/shm` tmpfs "
                    f"(`{push_tmp:.1f} MiB/s`) and `/data/local/tmp` disk (`{push_dsk:.1f} MiB/s`) "
                    "have nearly identical speed. The bottleneck is in the ADB/vsock transport or "
                    "host event loop, NOT guest `virtio-blk` disk I/O."
                )
            elif ratio >= 1.30:
                insights.append(
                    f"💾 {prefix}**Disk-Bound (`push_{size}`)**: Transport reaches `{push_tmp:.1f} MiB/s` "
                    f"on tmpfs, while `/data/local/tmp` is capped at `{push_dsk:.1f} MiB/s` "
                    f"({ratio:.2f}x slower) due to guest filesystem / `virtio-blk` writeback."
                )
            break

    # 3. Directional Asymmetry (push vs pull on tmpfs)
    for size in ("256MiB", "64MiB"):
        push_t = tests.get(f"push_{size}_tmpfs", {}).get("wall_mib_s_p50")
        pull_t = tests.get(f"pull_{size}_tmpfs", {}).get("wall_mib_s_p50")
        if push_t and pull_t and push_t > 0 and pull_t > 0:
            if push_t / pull_t >= 1.40:
                insights.append(
                    f"⚖️ {prefix}**Directional Asymmetry (`{size} tmpfs`)**: `push` (`{push_t:.1f} MiB/s`) "
                    f"is {push_t / pull_t:.2f}x faster than `pull` (`{pull_t:.1f} MiB/s`). "
                    "Inspect guest->host TX buffer chaining, credit update frequency, or host read chunking."
                )
            elif pull_t / push_t >= 1.40:
                insights.append(
                    f"⚖️ {prefix}**Directional Asymmetry (`{size} tmpfs`)**: `pull` (`{pull_t:.1f} MiB/s`) "
                    f"is {pull_t / push_t:.2f}x faster than `push` (`{push_t:.1f} MiB/s`). "
                    "Inspect host->guest RX credit window or host write buffering."
                )
            break

    # 4. Bulk Bandwidth vs. Per-File Turnaround Latency (64MiB vs 100x1MiB dir)
    bulk_push = tests.get("push_64MiB_tmpfs", {}).get(
        "wall_mib_s_p50"
    ) or tests.get("push_64MiB_disk", {}).get("wall_mib_s_p50")
    dir_push = tests.get("push_100x1MiB_dir", {}).get("wall_mib_s_p50")
    if bulk_push and dir_push and bulk_push > 0 and dir_push > 0:
        if dir_push / bulk_push < 0.55:
            insights.append(
                f"⏱️ {prefix}**Metadata / Turnaround Overhead**: Multi-file `push_100x1MiB_dir` "
                f"(`{dir_push:.1f} MiB/s`) achieves only {(dir_push / bulk_push) * 100:.0f}% of single-file "
                f"bandwidth (`{bulk_push:.1f} MiB/s`), indicating per-file STAT/OPEN round-trip latency."
            )

    # 5. Host CPU Efficiency & Dominant Threads
    for t_name, t_data in tests.items():
        cpu_ms_mib = t_data.get("cpu_ms_per_mib")
        top_threads = t_data.get("top_threads_cpu_pct") or {}
        if cpu_ms_mib and top_threads:
            top_str = ", ".join(
                f"`{k}` ({v:.0f}%)" for k, v in list(top_threads.items())[:3]
            )
            insights.append(
                f"⚙️ {prefix}**`{t_name}` Work Cost**: `{cpu_ms_mib:.2f} Host CPU-ms/MiB` "
                f"(Dominant emulator threads: {top_str})."
            )
            break

    # 6. Input Pipeline Decomposition (key_arrival_latency & touch_to_photon)
    key_test = tests.get("key_arrival_latency") or {}
    if "kernel_to_app_ms_p50" in key_test and "app_to_frame_ms_p50" in key_test:
        k2a = float(key_test["kernel_to_app_ms_p50"])
        a2f = float(key_test["app_to_frame_ms_p50"])
        insights.append(
            f"⌨️ {prefix}**Input Dispatch Breakdown (`key_arrival_latency`)**: "
            f"Guest `virtio-input` → `PerfProbeActivity` takes `{k2a:.2f} ms` p50 "
            f"(`p95={key_test.get('kernel_to_app_ms_p95', 0):.2f} ms`), and "
            f"`Choreographer` frame commit takes `{a2f:.2f} ms` p50."
        )
    t2p_test = tests.get("touch_to_photon") or {}
    if "touch_to_photon_ms_p50" in t2p_test:
        t2p = float(t2p_test["touch_to_photon_ms_p50"])
        gk2a = float(t2p_test.get("guest_kernel_to_app_ms_p50", 0.0))
        ga2f = float(t2p_test.get("guest_app_to_frame_ms_p50", 0.0))
        host_display_ms = max(0.0, t2p - (gk2a + ga2f))
        insights.append(
            f"👆 {prefix}**Touch-to-Photon Loop**: `{t2p:.1f} ms` p50 end-to-end "
            f"(Guest input `{gk2a:.2f} ms` + Guest frame `{ga2f:.2f} ms` + "
            f"Host gRPC/renderer readback `~{host_display_ms:.1f} ms`)."
        )

    # 7. Raw Guest Storage vs. Sync IOPS (disk_seq_128mib & disk_rand_4k_fsync)
    dseq = tests.get("disk_seq_128mib") or {}
    drand = tests.get("disk_rand_4k_fsync") or {}
    if "write_mib_s_p50" in dseq or "iops_p50" in drand:
        parts: List[str] = []
        if "write_mib_s_p50" in dseq:
            parts.append(
                f"Sequential 128MiB Write `{dseq['write_mib_s_p50']:.1f} MiB/s` / "
                f"Read `{dseq.get('read_mib_s_p50', 0):.1f} MiB/s`"
            )
        if "iops_p50" in drand:
            parts.append(
                f"Random 4K `fsync` `{drand['iops_p50']:.1f} IOPS` "
                f"(`p50={drand.get('p50_ms_p50', 0):.2f} ms`, `p95={drand.get('p95_ms_p50', 0):.2f} ms`)"
            )
        insights.append(
            f"💽 {prefix}**Guest `virtio-blk` Storage Profile**: " + "; ".join(parts) + "."
        )

    # 8. Hypervisor EPT / Stage-2 Demand Paging & SMP Scaling
    mept = tests.get("mem_ept_page_fault") or {}
    if "pages_per_sec_p50" in mept:
        insights.append(
            f"🧠 {prefix}**Hypervisor EPT / Demand-Paging (`mem_ept_page_fault`)**: "
            f"`{mept['pages_per_sec_p50']:.0f} pages/s` (`{mept.get('us_per_page_p50', 0):.2f} us/4KiB fault`, "
            f"Guest TLB shootdowns: `+{mept.get('guest_tlb_shootdowns', 0)}`)."
        )
    ccpu = tests.get("cpu_compute_scaling") or {}
    cipi = tests.get("cpu_ipi_ping_pong") or {}
    if "cpu_nt_mops_s_p50" in ccpu or "roundtrip_us_p50" in cipi:
        cparts: List[str] = []
        if "cpu_nt_mops_s_p50" in ccpu:
            cparts.append(
                f"SMP Scaling `{ccpu.get('smp_scaling_ratio_p50', 0):.2f}x` across "
                f"`{int(ccpu.get('vcpus_p50', 4))} vCPUs` (`{ccpu['cpu_nt_mops_s_p50']:.1f} Mops/s`)"
            )
        if "roundtrip_us_p50" in cipi:
            cparts.append(
                f"Cross-vCPU IPI Wakeup `{cipi['roundtrip_us_p50']:.2f} us` round-trip "
                f"(`RES:+{cipi.get('guest_ipi_resched', 0)}` reschedule IPIs)"
            )
        insights.append(
            f"⚡ {prefix}**vCPU & Hypervisor Scheduling**: " + "; ".join(cparts) + "."
        )

    # 9. vCPU Guest vs. Hypervisor Exit Overhead Check
    for t_name, t_data in tests.items():
        k_exit = float(t_data.get("vcpu_kernel_exit_pct", 0.0) or 0.0)
        u_exit = float(t_data.get("vcpu_userspace_exit_pct", 0.0) or 0.0)
        g_exec = float(t_data.get("vcpu_guest_pct", 0.0) or 0.0)
        if (k_exit + u_exit) >= 35.0 and (g_exec + k_exit + u_exit) > 0:
            insights.append(
                f"🔬 {prefix}**High VM-Exit Overhead in `{t_name}`**: vCPU threads spent "
                f"`{k_exit:.0f}%` in host kernel KVM exits and `{u_exit:.0f}%` in QEMU userspace exits "
                f"(vs. `{g_exec:.0f}%` executing guest code)."
            )
            break

    return insights


def diff_thread_cpu(
    base_threads: Dict[str, float],
    cand_threads: Dict[str, float],
    min_delta_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    """Computes per-thread CPU% shifts between baseline and candidate."""
    all_keys = sorted(set(base_threads.keys()) | set(cand_threads.keys()))
    diffs: List[Dict[str, Any]] = []
    for k in all_keys:
        b_val = float(base_threads.get(k, 0.0) or 0.0)
        c_val = float(cand_threads.get(k, 0.0) or 0.0)
        delta = c_val - b_val
        if abs(delta) >= min_delta_pct or max(b_val, c_val) >= 5.0:
            diffs.append(
                {
                    "thread": k,
                    "baseline_cpu_pct": round(b_val, 2),
                    "candidate_cpu_pct": round(c_val, 2),
                    "delta_cpu_pct": round(delta, 2),
                }
            )
    diffs.sort(key=lambda d: abs(d["delta_cpu_pct"]), reverse=True)
    return diffs


def diff_top_symbols(
    base_symbols: List[Dict[str, Any]],
    cand_symbols: List[Dict[str, Any]],
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Computes symbol overhead_pct diffs from `perf record` between baseline and candidate."""
    b_map = {
        s["symbol"]: float(s.get("overhead_pct", 0.0))
        for s in base_symbols
        if "symbol" in s
    }
    c_map = {
        s["symbol"]: float(s.get("overhead_pct", 0.0))
        for s in cand_symbols
        if "symbol" in s
    }
    all_syms = set(b_map.keys()) | set(c_map.keys())
    diffs: List[Dict[str, Any]] = []
    for sym in all_syms:
        b_v = b_map.get(sym, 0.0)
        c_v = c_map.get(sym, 0.0)
        delta = c_v - b_v
        if max(b_v, c_v) >= 1.0:
            diffs.append(
                {
                    "symbol": sym,
                    "baseline_pct": round(b_v, 2),
                    "candidate_pct": round(c_v, 2),
                    "delta_pct": round(delta, 2),
                }
            )
    diffs.sort(key=lambda d: abs(d["delta_pct"]), reverse=True)
    return diffs[:top_n]


def append_perf_history(
    report: Dict[str, Any], history_path: str = DEFAULT_HISTORY_PATH
) -> None:
    """Appends a compact summary record of a completed `perf run` to the JSONL history ledger."""
    try:
        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        compact_tests: Dict[str, Dict[str, Any]] = {}
        for t_name, t_vals in (report.get("tests") or {}).items():
            if isinstance(t_vals, dict):
                compact_tests[t_name] = {
                    k: t_vals[k]
                    for k in (
                        "n",
                        "wall_mib_s_p50",
                        "wall_mib_s_mean",
                        "wall_mib_s_stdev",
                        "write_mib_s_p50",
                        "read_mib_s_p50",
                        "iops_p50",
                        "pages_per_sec_p50",
                        "cpu_nt_mops_s_p50",
                        "roundtrip_us_p50",
                        "duration_ms_p50",
                        "host_cpu_pct",
                        "cpu_ms_per_mib",
                        "rss_mib",
                        "value",
                        "unit",
                    )
                    if k in t_vals
                }
        entry = {
            "timestamp": report.get("timestamp")
            or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "label": report.get("label", "unknown"),
            "build_id": report.get("build_id"),
            "branch": report.get("branch"),
            "engine": report.get("engine"),
            "git_commit": report.get("git_commit"),
            "avd": report.get("avd"),
            "suites": report.get("suites", []),
            "tests": compact_tests,
        }
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as e:
        logger.debug("Failed appending to perf history %s: %s", history_path, e)


def load_perf_history(
    history_path: str = DEFAULT_HISTORY_PATH,
    limit: int = 20,
    suite_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Loads recent benchmark entries from the JSONL history ledger."""
    if not os.path.isfile(history_path):
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    item = json.loads(s)
                    if suite_filter:
                        suites = item.get("suites") or []
                        if not any(suite_filter in st for st in suites):
                            continue
                    entries.append(item)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.debug("Failed reading perf history %s: %s", history_path, e)
    return entries[-limit:]


def format_perf_history_markdown(entries: List[Dict[str, Any]]) -> str:
    """Formats longitudinal performance history into a Markdown table."""
    if not entries:
        return "No performance history entries recorded yet (`~/.cache/emu-dev-cli/perf_history.jsonl`)."

    headers = [
        "Timestamp (UTC)",
        "Label / Build",
        "Engine",
        "AVD",
        "Key Metrics (p50)",
    ]
    rows: List[List[str]] = []
    for e in entries:
        ts = str(e.get("timestamp", ""))[:19].replace("T", " ")
        lbl = str(e.get("label") or e.get("build_id") or "run")
        if e.get("build_id") and str(e.get("build_id")) not in lbl:
            lbl = f"{lbl} (`{e['build_id']}`)"
        eng = str(e.get("engine") or "-")
        avd = str(e.get("avd") or "-")
        tests = e.get("tests") or {}
        highlights: List[str] = []
        for t_name, t_val in list(tests.items())[:4]:
            if "wall_mib_s_p50" in t_val:
                highlights.append(
                    f"`{t_name}`: {t_val['wall_mib_s_p50']:.1f} MiB/s"
                )
            elif "duration_ms_p50" in t_val:
                highlights.append(
                    f"`{t_name}`: {t_val['duration_ms_p50']:.0f} ms"
                )
            elif "value" in t_val:
                highlights.append(
                    f"`{t_name}`: {t_val['value']} {t_val.get('unit', '')}".strip()
                )
        rows.append([ts, lbl, eng, avd, " | ".join(highlights) or "-"])

    return "## Longitudinal Emulator Performance History\n\n" + format_markdown_table(
        headers, rows
    )
