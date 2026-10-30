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

"""Statistical sample aggregation and convergence helpers for `emu-dev-cli perf`."""

from collections.abc import Sequence
import statistics
from typing import Any, Dict, List, Optional, Tuple

CV_CONVERGENCE_THRESHOLD: float = 0.08
TOP_THREADS_SUMMARY_LIMIT: int = 6
MS_PER_SECOND: float = 1000.0

GUEST_VIRTIO_IRQ_KEYS: Tuple[str, ...] = (
    "virtio_input_irqs",
    "virtio_blk_irqs",
    "virtio_gpu_irqs",
    "virtio_vsock_irqs",
)

GUEST_SCALAR_IRQ_MAPPINGS: Tuple[Tuple[str, str], ...] = (
    ("guest_ipi_resched", "reschedule_ipis"),
    ("guest_tlb_shootdowns", "tlb_shootdowns"),
    ("guest_pgfault_delta", "pgfault"),
)


def _compute_triple_stats(
    vals: Sequence[float], digits: int = 2
) -> Tuple[float, float, float]:
    """Returns `(median, mean, stdev)` rounded to `digits` decimal places."""
    med = round(statistics.median(vals), digits)
    mean_v = round(statistics.mean(vals), digits)
    std_v = round(statistics.stdev(vals), digits) if len(vals) > 1 else 0.0
    return med, mean_v, std_v


def _populate_key_triple_stats(
    samples: List[Dict[str, float]],
    key: str,
    summary: Dict[str, Any],
    digits: int = 2,
) -> None:
    vals = [
        float(s[key])
        for s in samples
        if isinstance(s.get(key), (int, float))
    ]
    if vals:
        med, mean_v, std_v = _compute_triple_stats(vals, digits=digits)
        summary[f"{key}_p50"] = med
        summary[f"{key}_mean"] = mean_v
        summary[f"{key}_stdev"] = std_v


def is_cv_converged(
    values: Sequence[float], threshold: float = CV_CONVERGENCE_THRESHOLD
) -> bool:
    """Returns True when the sample coefficient of variation (`stdev / mean`) is at or below `threshold`."""
    if len(values) < 2:
        return False
    mean_v = statistics.mean(values)
    if mean_v <= 0:
        return True
    return (statistics.stdev(values) / mean_v) <= threshold


def _summarize_throughput_samples(
    samples: List[Dict[str, float]], summary: Dict[str, Any]
) -> None:
    """Populates throughput (`wall_mib_s`) and extra metric statistics into `summary`."""
    vals = [s["wall_mib_s"] for s in samples]
    durs_ms = [s.get("duration_s", 0.0) * MS_PER_SECOND for s in samples]
    adb_vals = [
        s.get("adb_mb_s", 0.0) for s in samples if s.get("adb_mb_s", 0.0) > 0
    ]
    med, mean_v, std_v = _compute_triple_stats(vals, digits=2)
    summary.update(
        {
            "wall_mib_s_p50": med,
            "wall_mib_s_mean": mean_v,
            "wall_mib_s_stdev": std_v,
            "wall_mib_s_min": round(min(vals), 2),
            "wall_mib_s_max": round(max(vals), 2),
            "duration_ms_p50": round(statistics.median(durs_ms), 2),
            "adb_mb_s_p50": (
                round(statistics.median(adb_vals), 2) if adb_vals else 0.0
            ),
        }
    )
    for k in samples[0].keys():
        if k not in ("wall_mib_s", "wall_mb_s", "duration_s", "adb_mb_s"):
            _populate_key_triple_stats(samples, k, summary, digits=2)


def _summarize_duration_samples(
    samples: List[Dict[str, float]], summary: Dict[str, Any]
) -> None:
    """Populates latency (`duration_ms`) and sub-metric statistics into `summary`."""
    durs_ms = [s["duration_ms"] for s in samples]
    med, mean_v, std_v = _compute_triple_stats(durs_ms, digits=2)
    summary.update(
        {
            "duration_ms_p50": med,
            "duration_ms_mean": mean_v,
            "duration_ms_stdev": std_v,
            "duration_ms_min": round(min(durs_ms), 2),
            "duration_ms_max": round(max(durs_ms), 2),
        }
    )
    for k in samples[0].keys():
        if k == "duration_ms":
            continue
        extra_vals = [
            float(s[k])
            for s in samples
            if isinstance(s.get(k), (int, float))
        ]
        if not extra_vals:
            summary[k] = samples[-1][k]
            continue

        e_med, e_mean, e_std = _compute_triple_stats(extra_vals, digits=3)
        summary[k] = e_med
        if not k.endswith(("_p50", "_p95", "_mean", "_stdev")):
            summary[f"{k}_p50"] = e_med
            summary[f"{k}_mean"] = e_mean
            summary[f"{k}_stdev"] = e_std
        elif k.endswith("_p50"):
            base_k = k[:-4]
            summary[f"{base_k}_mean"] = e_mean
            summary[f"{base_k}_stdev"] = e_std


def _summarize_generic_samples(
    samples: List[Dict[str, float]], summary: Dict[str, Any]
) -> None:
    """Populates generic numeric metric statistics into `summary`."""
    for k in samples[0].keys():
        _populate_key_triple_stats(samples, k, summary, digits=2)


def _aggregate_telemetry_into_summary(
    summary: Dict[str, Any],
    telemetry_list: Sequence[Any],
    total_mib_per_iter: float,
) -> None:
    """Aggregates host/guest profiler telemetry snapshots into `summary`."""
    valid_t = [t for t in telemetry_list if t and t.wall_duration_s > 0]
    if not valid_t:
        return

    cpu_pcts = [t.host_cpu_pct for t in valid_t]
    cpu_ms_list = [t.host_cpu_ms for t in valid_t]
    rss_list = [t.rss_mib for t in valid_t if t.rss_mib > 0]
    peak_rss_list = [t.peak_rss_mib for t in valid_t if t.peak_rss_mib > 0]

    summary["host_cpu_pct"] = round(statistics.median(cpu_pcts), 2)
    median_cpu_ms = statistics.median(cpu_ms_list)
    summary["host_cpu_ms_p50"] = round(median_cpu_ms, 2)
    if total_mib_per_iter > 0:
        summary["cpu_ms_per_mib"] = round(
            median_cpu_ms / total_mib_per_iter, 3
        )
    if rss_list:
        summary["rss_mib"] = round(statistics.median(rss_list), 2)
    if peak_rss_list:
        summary["peak_rss_mib"] = round(max(peak_rss_list), 2)

    # Aggregate 3-way vCPU split (Linux /proc/<pid>/task/<tid>/stat guest_time)
    vcpu_guest = [
        getattr(t, "vcpu_guest_cpu_pct", 0.0)
        for t in valid_t
        if getattr(t, "vcpu_guest_cpu_pct", 0.0) > 0
    ]
    if vcpu_guest:
        summary["vcpu_guest_pct"] = round(statistics.median(vcpu_guest), 2)
        summary["vcpu_kernel_exit_pct"] = round(
            statistics.median(
                [getattr(t, "vcpu_kvm_kernel_cpu_pct", 0.0) for t in valid_t]
            ),
            2,
        )
        summary["vcpu_userspace_exit_pct"] = round(
            statistics.median(
                [
                    getattr(t, "vcpu_qemu_userspace_cpu_pct", 0.0)
                    for t in valid_t
                ]
            ),
            2,
        )

    # Aggregate Linux /sys/kernel/debug/kvm VM-exit counters when present
    kvm_rates = [
        t.kvm_exits.get("exits", 0) / t.wall_duration_s
        for t in valid_t
        if getattr(t, "kvm_exits", None)
        and t.kvm_exits.get("exits", 0) > 0
        and t.wall_duration_s > 0
    ]
    if kvm_rates:
        summary["kvm_exits_per_sec"] = round(statistics.median(kvm_rates), 1)
        for t in reversed(valid_t):
            if getattr(t, "kvm_exits", None):
                summary["kvm_top_exit_reasons"] = {
                    k: v for k, v in t.kvm_exits.items() if k != "exits"
                }
                break

    # Aggregate universal Guest /proc/interrupts & /proc/vmstat deltas
    irq_accum: Dict[str, List[int]] = {}
    for t in valid_t:
        girqs = getattr(t, "guest_irqs", None) or {}
        for dev in GUEST_VIRTIO_IRQ_KEYS:
            if girqs.get(dev, 0) > 0:
                irq_accum.setdefault(dev, []).append(int(girqs[dev]))
    if irq_accum:
        summary["guest_virtio_irqs"] = {
            dev: int(round(statistics.median(cnts)))
            for dev, cnts in irq_accum.items()
        }
    for summary_key, irq_key in GUEST_SCALAR_IRQ_MAPPINGS:
        g_vals = [
            int((getattr(t, "guest_irqs", None) or {}).get(irq_key, 0))
            for t in valid_t
            if int((getattr(t, "guest_irqs", None) or {}).get(irq_key, 0)) > 0
        ]
        if g_vals:
            summary[summary_key] = int(round(statistics.median(g_vals)))

    # Aggregate per-thread CPU% across iterations
    thread_accum: Dict[str, List[float]] = {}
    for t in valid_t:
        for th_name, th_pct in (t.top_threads_cpu_pct or {}).items():
            thread_accum.setdefault(th_name, []).append(th_pct)
    merged_threads = {
        th: round(statistics.median(pcts), 2)
        for th, pcts in thread_accum.items()
    }
    summary["top_threads_cpu_pct"] = dict(
        sorted(merged_threads.items(), key=lambda x: x[1], reverse=True)[
            :TOP_THREADS_SUMMARY_LIMIT
        ]
    )

    # Attach perf_stat / top_symbols / perfetto_slices if captured
    for t in reversed(valid_t):
        if t.perf_stat and "perf_stat" not in summary:
            summary["perf_stat"] = t.perf_stat
        if t.top_symbols and "top_symbols" not in summary:
            summary["top_symbols"] = t.top_symbols
        if t.perfetto_slices and "perfetto_slices" not in summary:
            summary["perfetto_slices"] = t.perfetto_slices


def summarize_samples(
    samples: List[Dict[str, float]],
    telemetry_list: Optional[List[Any]] = None,
    total_mib_per_iter: float = 0.0,
) -> Dict[str, Any]:
    """Computes statistical aggregates (`p50`, `mean`, `stdev`, `min`, `max`) and
    merges host process/thread telemetry across iterations.
    """
    if not samples:
        return {"n": 0}

    summary: Dict[str, Any] = {"n": len(samples)}
    if "wall_mib_s" in samples[0]:
        _summarize_throughput_samples(samples, summary)
    elif "duration_ms" in samples[0]:
        _summarize_duration_samples(samples, summary)
    else:
        _summarize_generic_samples(samples, summary)

    if telemetry_list:
        _aggregate_telemetry_into_summary(
            summary, telemetry_list, total_mib_per_iter
        )

    summary["samples"] = samples
    return summary
