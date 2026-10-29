#!/usr/bin/env python3
"""Automated scorer and statistical comparator for the Goldfish & HAL Agent Benchmarks.

Scores agent trajectories using unweighted checklist recall, binary Pass@1, and
orthogonal cost/friction metrics in native units (Planner Turns, Tool Calls,
Dead-Ends/Failed Commands, Context KB, and Wall-Clock Seconds) without
arbitrary magic-weighted composite formulas.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any

BRAIN_DIR = Path.home() / ".gemini" / "jetski" / "brain"

RUBRIC_CATEGORIES = (
    "files",
    "symbols",
    "targets",
    "invariants",
    "defects",
)


def parse_iso(ts: str) -> datetime:
  return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def score_rubric(text: str, rubric: dict[str, list[str]]) -> dict[str, Any]:
  """Scores agent output against a task rubric using unweighted item recall.

  Every required item across `files`, `symbols`, `targets`, `invariants`, and
  `defects` has equal weight (1 / total_items). Optional `forbidden` patterns
  verify that deprecated/buggy constructs do NOT appear in generated code.
  """
  total_required = 0
  total_matched = 0
  result: dict[str, Any] = {}

  for cat in RUBRIC_CATEGORIES:
    patterns = rubric.get(cat, [])
    matched = []
    missing = []
    for pat in patterns:
      if re.search(pat, text, re.IGNORECASE | re.DOTALL):
        matched.append(pat)
      else:
        missing.append(pat)
    total_required += len(patterns)
    total_matched += len(matched)
    cat_singular = cat[:-1] if cat.endswith("s") else cat
    cat_recall = len(matched) / len(patterns) if patterns else 1.0
    result[f"{cat_singular}_recall"] = round(cat_recall, 4)
    result[f"missing_{cat}"] = missing

  forbidden_patterns = rubric.get("forbidden", [])
  forbidden_violations = []
  for pat in forbidden_patterns:
    if re.search(pat, text, re.IGNORECASE | re.DOTALL):
      forbidden_violations.append(pat)
    else:
      total_matched += 1
  total_required += len(forbidden_patterns)
  result["forbidden_violations"] = forbidden_violations

  recall = (total_matched / total_required) if total_required > 0 else 1.0
  pass_at_1 = 1 if (total_matched == total_required and not forbidden_violations) else 0

  result["recall"] = round(recall, 4)
  # Keep `accuracy` as an alias for unweighted `recall` for backwards compatibility.
  result["accuracy"] = round(recall, 4)
  result["pass_at_1"] = pass_at_1
  result["matched_items"] = total_matched
  result["total_items"] = total_required
  return result


def analyze_transcript(
    conv_id: str, rubric: dict[str, list[str]]
) -> dict[str, Any]:
  log_path = (
      BRAIN_DIR / conv_id / ".system_generated" / "logs" / "transcript_full.jsonl"
  )
  if not log_path.exists():
    log_path = (
        BRAIN_DIR / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
  if not log_path.exists():
    raise FileNotFoundError(f"Transcript not found for conversation {conv_id}")

  steps = []
  for line in log_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    try:
      steps.append(json.loads(line))
    except json.JSONDecodeError:
      continue

  planner_steps = [s for s in steps if s.get("type") == "PLANNER_RESPONSE"]
  generic_steps = [s for s in steps if s.get("type") == "GENERIC"]

  turns = 0
  tool_calls = 0
  tool_counts: dict[str, int] = {}
  used_emu_dev_docs = False
  used_skill_md = False
  used_agents_md = False
  response_parts: list[str] = []
  sent_report_via_message = False

  for idx, s in enumerate(steps):
    if s.get("type") != "PLANNER_RESPONSE":
      continue
    ps = s
    if ps.get("content"):
      response_parts.append(ps["content"])
    tcs = ps.get("tool_calls", [])
    non_msg_tcs = [tc for tc in tcs if tc.get("name") != "send_message"]
    msg_tcs = [tc for tc in tcs if tc.get("name") == "send_message"]

    # Collect the immediately following GENERIC tool-result steps for this turn
    turn_results: list[dict[str, Any]] = []
    for next_s in steps[idx + 1 :]:
      if next_s.get("type") == "PLANNER_RESPONSE":
        break
      if next_s.get("type") == "GENERIC":
        turn_results.append(next_s)

    # Count discovery/execution turns (if the agent already sent its report via
    # send_message in a prior turn and the final turn has no tool calls, don't
    # double-count the sign-off turn)
    if non_msg_tcs or not sent_report_via_message:
      turns += 1

    for mtc in msg_tcs:
      tc_args = mtc.get("args", mtc.get("arguments", {}))
      msg_body = tc_args.get("Message", "")
      if msg_body:
        response_parts.append(msg_body)
        sent_report_via_message = True

    for tc_idx, tc in enumerate(non_msg_tcs):
      tool_calls += 1
      name = tc.get("name", "unknown")
      tool_counts[name] = tool_counts.get(name, 0) + 1
      args_str = json.dumps(tc.get("args", tc.get("arguments", {})))
      tc_ok = (
          tc_idx >= len(turn_results)
          or turn_results[tc_idx].get("status") != "ERROR"
      )
      if tc_ok:
        if (
            ("emu-dev-cli" in args_str and "docs" in args_str)
            or "ARCHITECTURE.md" in args_str
            or "DESIGN.md" in args_str
        ):
          used_emu_dev_docs = True
        if "SKILL.md" in args_str:
          used_skill_md = True
        if "AGENTS.md" in args_str:
          used_agents_md = True

  context_bytes = 0
  dead_ends = 0
  for gs in generic_steps:
    content = gs.get("content", "") or ""
    if "Message sent successfully" in content:
      continue
    context_bytes += len(content.encode("utf-8"))
    if gs.get("status") == "ERROR":
      dead_ends += 1
      continue
    if (
        content.rstrip().endswith("Matches:")
        or "No wiki documents matched" in content
        or re.search(r"Command exited with code [1-9]", content)
    ):
      dead_ends += 1

  context_kb = round(context_bytes / 1024.0, 2)

  timestamps = [
      parse_iso(s["created_at"]) for s in steps if s.get("created_at")
  ]
  wall_clock_sec = (
      round((max(timestamps) - min(timestamps)).total_seconds(), 2)
      if len(timestamps) >= 2
      else 0.0
  )

  final_response = "\n".join(response_parts)
  rubric_scores = score_rubric(final_response, rubric)

  return {
      "conversation_id": conv_id,
      "turns": turns,
      "tool_calls": tool_calls,
      "dead_ends": dead_ends,
      "tool_counts": tool_counts,
      "used_agents_md": used_agents_md,
      "used_emu_dev_docs": used_emu_dev_docs,
      "used_skill_md": used_skill_md,
      "context_kb": context_kb,
      "wall_clock_sec": wall_clock_sec,
      **rubric_scores,
  }


def summarize_group(results: list[dict[str, Any]]) -> dict[str, Any]:
  if not results:
    return {}
  n = len(results)
  metrics = [
      "pass_at_1",
      "recall",
      "turns",
      "tool_calls",
      "dead_ends",
      "context_kb",
      "wall_clock_sec",
  ]
  summary: dict[str, Any] = {"count": n}
  for m in metrics:
    vals = [float(r[m]) for r in results]
    summary[f"{m}_mean"] = round(statistics.mean(vals), 4 if m in ("pass_at_1", "recall") else 2)
    summary[f"{m}_median"] = round(statistics.median(vals), 4 if m in ("pass_at_1", "recall") else 2)
  summary["used_agents_md_rate"] = round(
      sum(1 for r in results if r.get("used_agents_md")) / n, 2
  )
  summary["used_emu_dev_docs_rate"] = round(
      sum(1 for r in results if r.get("used_emu_dev_docs")) / n, 2
  )
  return summary


def paired_t_and_wilcoxon(
    before: list[float], after: list[float]
) -> dict[str, float]:
  diffs = [a - b for b, a in zip(before, after)]
  n = len(diffs)
  if n < 2:
    return {"mean_diff": 0.0, "median_diff": 0.0, "p_t": 1.0, "p_wilcoxon": 1.0}
  mean_d = statistics.mean(diffs)
  median_d = statistics.median(diffs)
  sd_d = statistics.stdev(diffs)
  if sd_d == 0:
    t_p = 0.0 if mean_d != 0 else 1.0
  else:
    t_stat = abs(mean_d) / (sd_d / math.sqrt(n))
    # Normal approximation for two-tailed p-value
    t_p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(t_stat / math.sqrt(2.0))))

  # Wilcoxon signed-rank normal approximation with mid-ranks for ties
  non_zero = [(abs(d), 1 if d > 0 else -1) for d in diffs if d != 0]
  non_zero.sort(key=lambda x: x[0])
  m = len(non_zero)
  if m == 0:
    w_p = 1.0
  else:
    w_pos = 0.0
    tie_var_correction = 0.0
    idx = 0
    while idx < m:
      j = idx
      while j < m and non_zero[j][0] == non_zero[idx][0]:
        j += 1
      mid_rank = (idx + 1 + j) / 2.0
      tie_count = j - idx
      if tie_count > 1:
        tie_var_correction += tie_count**3 - tie_count
      for k in range(idx, j):
        if non_zero[k][1] > 0:
          w_pos += mid_rank
      idx = j
    mean_w = m * (m + 1) / 4.0
    var_w = (m * (m + 1) * (2 * m + 1) - 0.5 * tie_var_correction) / 24.0
    sd_w = math.sqrt(max(0.0, var_w))
    z = abs(w_pos - mean_w) / sd_w if sd_w > 0 else 0.0
    w_p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))

  return {
      "mean_diff": round(mean_d, 4),
      "median_diff": round(median_d, 4),
      "p_t": round(t_p, 4),
      "p_wilcoxon": round(w_p, 4),
  }


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--tasks",
      type=Path,
      default=Path(__file__).parent / "tasks.json",
      help="Path to tasks.json",
  )
  parser.add_argument(
      "--manifest",
      type=Path,
      help="Path to run manifest JSON mapping task_id -> conversation_id",
  )
  parser.add_argument(
      "--out",
      type=Path,
      help="Path to write scored results JSON",
  )
  parser.add_argument(
      "--compare",
      nargs=2,
      metavar=("BASELINE", "CANDIDATE"),
      type=Path,
      help="Compare two scored result JSON files",
  )
  args = parser.parse_args()

  if args.compare:
    base_data = json.loads(args.compare[0].read_text(encoding="utf-8"))
    cand_data = json.loads(args.compare[1].read_text(encoding="utf-8"))
    base_map = {r["id"]: r for r in base_data["results"]}
    cand_map = {r["id"]: r for r in cand_data["results"]}
    common_ids = [tid for tid in base_map if tid in cand_map]

    print(f"=== Paired Pareto Comparison across N={len(common_ids)} tasks ===")
    for split_filter in ["all", "dev", "holdout"]:
      ids = [
          tid
          for tid in common_ids
          if split_filter == "all" or base_map[tid]["split"] == split_filter
      ]
      if not ids:
        continue
      print(f"\n--- Split: {split_filter.upper()} (N={len(ids)}) ---")
      for metric in [
          "pass_at_1",
          "recall",
          "turns",
          "tool_calls",
          "dead_ends",
          "context_kb",
          "wall_clock_sec",
      ]:
        b_vals = [float(base_map[tid][metric]) for tid in ids]
        c_vals = [float(cand_map[tid][metric]) for tid in ids]
        stats = paired_t_and_wilcoxon(b_vals, c_vals)
        b_mean = statistics.mean(b_vals)
        c_mean = statistics.mean(c_vals)
        pct = ((c_mean - b_mean) / b_mean * 100.0) if b_mean else 0.0
        print(
            f"  {metric:15s}: {b_mean:6.2f} -> {c_mean:6.2f} "
            f"({pct:+6.1f}%, mean_diff={stats['mean_diff']:+6.2f}, "
            f"median_diff={stats['median_diff']:+6.2f}, "
            f"p_wilcoxon={stats['p_wilcoxon']:.4f}, p_t={stats['p_t']:.4f})"
        )
    return

  if not args.manifest:
    parser.error("Either --manifest or --compare is required.")

  tasks_doc = json.loads(args.tasks.read_text(encoding="utf-8"))
  manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

  results = []
  for task in tasks_doc["tasks"]:
    tid = task["id"]
    if tid not in manifest:
      continue
    conv_id = manifest[tid]
    scored = analyze_transcript(conv_id, task["rubric"])
    results.append({
        "id": tid,
        "workflow": task["workflow"],
        "split": task["split"],
        "title": task["title"],
        **scored,
    })

  workflows = sorted({r["workflow"] for r in results})
  output = {
      "summary_all": summarize_group(results),
      "summary_dev": summarize_group(
          [r for r in results if r["split"] == "dev"]
      ),
      "summary_holdout": summarize_group(
          [r for r in results if r["split"] == "holdout"]
      ),
      "summary_by_workflow": {
          wf: summarize_group([r for r in results if r["workflow"] == wf])
          for wf in workflows
      },
      "results": results,
  }

  if args.out:
    args.out.write_text(
        json.dumps(output, indent=2), encoding="utf-8", newline="\n"
    )

  print(json.dumps(output["summary_all"], indent=2))
  print("\nBy Split:")
  print("  DEV    :", json.dumps(output["summary_dev"]))
  print("  HOLDOUT:", json.dumps(output["summary_holdout"]))
  print("\nBy Workflow:")
  for wf, s in output["summary_by_workflow"].items():
    print(f"  {wf:16s}: {json.dumps(s)}")
  print("\nPer-Task Breakdown:")
  for r in results:
    print(
        f"  {r['id']:11s} [{r['workflow']:18s}|{r['split']:7s}] "
        f"Pass={r['pass_at_1']} Recall={r['recall']:.2f} "
        f"({r['matched_items']}/{r['total_items']}) "
        f"Turns={r['turns']:2d} Calls={r['tool_calls']:2d} "
        f"DeadEnds={r['dead_ends']:2d} CtxKB={r['context_kb']:6.1f} "
        f"Time={r['wall_clock_sec']:5.1f}s AGENTS.md={r['used_agents_md']}"
    )


if __name__ == "__main__":
  main()
