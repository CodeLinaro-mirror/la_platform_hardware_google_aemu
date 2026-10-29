# Goldfish & HAL Agent Evaluation Benchmark

This directory contains the task suites and automated transcript scorer used to evaluate AI agent correctness and efficiency across `hardware/generic/goldfish` and `hardware/google/aemu`.

## Dataset Structure
1. **[`tasks.json`](tasks.json) (`N = 32`: `24 Dev` + `8 Holdout` across 4 workflows):**
   - `bug_fix` (`6 Dev, 2 Holdout`): Locating root-cause files, classes/functions, and unit tests for crashes, race conditions, and platform bugs.
   - `perf_bottleneck` (`6 Dev, 2 Holdout`): Tracing producer-to-consumer hot paths, frame drops, and Perfetto instrumentation points.
   - `add_feature` (`6 Dev, 2 Holdout`): Mapping vertical slices across `.proto`, gRPC/WebRTC services, CLI flags, `hardware-properties.ini`, and tests.
   - `operational_cli` (`6 Dev, 2 Holdout`): Discovering `emu-dev-cli` and `mise` operational workflows (`fetch-build`, `create avd`, `launch emulator`, `mesh`, `cts`, `crash`, `flakiness`, `build-times`, `mise`).
2. **Anti-Overfitting Rule:** Only inspect transcripts from **Dev tasks** (`split == "dev"`) when diagnosing bottlenecks or tuning `AGENTS.md`, `SKILL.md`, `ARCHITECTURE.md`, or `emu-dev-cli docs`. Never inspect transcripts from **Holdout tasks** (`split == "holdout"`).

## Pareto Evaluation Metrics (No Arbitrary Composite Weights)
Rather than blending correctness and exploration volume into a synthetic weighted score, [`score_benchmark.py`](score_benchmark.py) parses `transcript_full.jsonl` and reports orthogonal metrics in their native units:

### 1. Correctness (Primary Gate — Unweighted)
- **`pass_at_1` ($\in \{0, 1\}$):** `1` if and only if **100%** of required rubric items are satisfied and zero `forbidden` patterns appear; `0` otherwise.
- **`recall` ($\in [0, 1]$):** Unweighted item recall across all required checklist items (`files`, `symbols`, `targets`, `invariants`, `defects`, plus absence of `forbidden` anti-patterns):
  $$\text{Recall} = \frac{N_{\text{matched}}}{N_{\text{total}}}$$
  Every required fact, architectural invariant, or seeded defect has equal weight ($1 / N_{\text{total}}$).

### 2. Cost & Friction (Reported Directly in Native Units)
- **`turns` ($T$):** Number of planner round-trips.
- **`tool_calls` ($C$):** Total discovery/execution tool calls.
- **`dead_ends` ($D$):** Failed shell commands (`exited with code [1-9]`) or zero-match searches.
- **`context_kb` ($K$):** Total KB of tool output ingested into the context window.
- **`wall_clock_sec`:** End-to-end wall-clock duration in seconds.

A candidate change (`AGENTS.md`, `SKILL.md`, or harness rule) is an improvement when **`pass_at_1` and `recall` increase (or hold at 100%) while `turns`, `tool_calls`, `dead_ends`, and `context_kb` decrease**.

## Usage

### 1. Score a Benchmark Run Manifest
```bash
python3 hardware/google/aemu/tools/emu-dev-cli/benchmarks/score_benchmark.py \
  --tasks hardware/google/aemu/tools/emu-dev-cli/benchmarks/tasks.json \
  --manifest /path/to/run_manifest.json \
  --out /path/to/run_results.json
```

### 2. Run Paired Statistical Comparison (Wilcoxon Signed-Rank & Paired $t$-Test)
```bash
python3 hardware/google/aemu/tools/emu-dev-cli/benchmarks/score_benchmark.py \
  --compare /path/to/baseline_results.json /path/to/candidate_results.json
```
