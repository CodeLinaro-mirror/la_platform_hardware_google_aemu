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

"""8-Specialist Single-Wave Parallel Code-Review Swarm for the AEMU Harness.

Coordinates specialist prompt generation, `run_workflow` script synthesis, and
bundle preparation atop `review_targets` (diff/Gerrit/Git target discovery) and
`review_aggregation` (diff-boundary filtering and Gerrit review payloads).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from commands.harness.goldfish_libs import REUSABLE_LIBS_CATALOG
from commands.harness.ledger import get_harness_base_dir
from commands.harness.review_aggregation import (
    SHORT_ID_DISPLAY_LEN,
    SPECIALIST_FINDING_ITEM_SCHEMA,
    SPECIALIST_OUTPUT_SCHEMA,
    build_gerrit_review_payload,
    filter_and_aggregate_findings,
    render_review_swarm_markdown,
)
from commands.harness.review_gate_v2 import (
    CODE_REVIEW_EXTENSIONS,
    LITE_SWARM_MAX_CODE_LINES,
    VERIFIER_SCHEMA,
    apply_verifier_decisions,
    apply_verifier_to_specialist_outputs,
    build_enclosing_file_context,
    build_refutation_verifier_prompt,
    classify_review_risk_tier,
    collect_blocking_candidates,
    normalize_stage1_outputs,
    run_deterministic_stage0,
)
from commands.harness.review_history import (
    INCREMENTAL_PASS_MODEL,
    apply_incremental_history,
    build_wave_metrics,
    load_iteration_history,
    load_recent_swarm_metrics,
    persist_iteration_and_metrics,
)
from commands.harness.review_swarm_status import (
    REVIEW_SWARM_SUBDIR,
    write_completed_swarm_status,
    write_initial_swarm_status,
)
from commands.harness.review_targets import (
    COMMIT_MSG_FILE,
    DEFAULT_GERRIT_HOST,
    atomic_write_text,
    extract_bug_ids_from_commit_msg,
    fetch_gerrit_cl_target,
    fetch_git_commit_target,
    parse_cl_identifier,
    parse_unified_diff_changed_lines,
    resolve_git_repo_dir,
    sanitize_target_id,
    serialize_changed_lines,
)

MAX_SWARM_CONCURRENCY: int = 8
COMMIT_AUDITOR_NAME: str = "commit-and-bug-auditor"

SHARED_SPECIALIST_RULES: str = """## Shared Non-Negotiable Review Rules
1. **Strict Diff Boundary**: Only report findings on added or modified lines in the diff (listed in `changed_lines_by_file`). NEVER report findings on untouched surrounding code, pre-existing issues, or unmodified callers.
2. **High Signal Only (Zero Pedantry)**: Skip trivial nitpicks, style preferences not backed by team rules, or theoretical lectures (`sin^2(a) + cos^2(a) = 1`). Return an empty `findings: []` array if your specialist area is clean.
3. **Concise & Actionable**: Keep each finding's `message` to 1-3 sentences identifying the concrete defect or rule violation and provide a drop-in `suggested_fix`.
4. **Explicit Severity Rubric (Only `blocking` Blocks Gerrit Upload)**:
   - `"blocking"` (Severe — blocks upload): ONLY for verified functional bugs, thread-safety/lock/lifetime races, resource/FD/memory leaks, unguarded null/bounds/StatusOr dereferences, wrong API contracts/state transitions, cross-platform/Unicode breakages (`std::ifstream`/`fopen` on paths, missing `cc_binary_with_manifest`), or broken `BUILD.bazel` targets.
   - `"warning"` (Medium — non-blocking advisory): library reuse opportunities, error-message wording (`go/error-messages`), StatusOr naming (`TotW 181`), modularity/control-flow refactoring, named constants, or commit-message formatting.
   - `"nit"` (Low — non-blocking minor suggestion): minor readability or documentation polish.
5. **Verify Against Enclosing File Context**: Before reporting a missing lock, missing null/bounds check, or missing header/declaration, inspect the `Enclosing File Context` section (when present) to verify it is not already handled just outside the diff hunk.
"""

# The duplication auditor's library list comes from the canonical catalog, so the prompt
# never recommends a label or header that goldfish does not have.
_REUSABLE_LIBS_PROMPT_LIST: str = ", ".join(
    f"`{entry['namespace']}` in `{entry['bazel_target']}` `{entry['header']}`"
    for entry in REUSABLE_LIBS_CATALOG
)

REVIEW_SWARM_SPECIALISTS: List[Dict[str, str]] = [
    {
        "name": "commit-and-bug-auditor",
        "role": "Commit & Bug Auditor",
        "model": "flash",
        "workflow_mapping": "create-commit / amend-commit",
        "focus": (
            "Audit `/COMMIT_MSG` using `aemu_validate_commit_message` against Erwin Jansen's "
            "Semantic Commit Message rules (`<type>[optional scope]: <Description>`, <=50 chars, "
            "allowed types feat|fix|build|docs|chore|test|refactor|perf|style, capitalized "
            "imperative description, no trailing period, blank line, explanatory body <=72 chars/line "
            "covering what and why, required `Bug:` and `Test:` footers, no `CONVERSATION=`/`Tag=` tags). "
            "Cross-check the referenced `Bug:` ID in Buganizer to verify the issue exists and the "
            "diff addresses the reported problem. Report `/COMMIT_MSG` findings on `file: \"/COMMIT_MSG\"`."
        ),
    },
    {
        "name": "duplication-and-reuse-auditor",
        "role": "Duplication & Reuse Auditor",
        "model": "inherit",
        "workflow_mapping": "review-duplication",
        "focus": (
            "Enumerate every new class, helper, parser, server, client, or `Fake*` test double in "
            "the diff (production and test code included) and check `//emulator/libs/*` "
            f"({_REUSABLE_LIBS_PROMPT_LIST}), "
            "`aemu`, and Abseil for existing implementations. Set `checked_count` to the number of "
            "new abstractions audited and `duplicate_count` to the number of duplicates found."
        ),
    },
    {
        "name": "modularity-auditor",
        "role": "Modularity Auditor",
        "model": "inherit",
        "workflow_mapping": "review-modularity",
        "focus": (
            "Audit added/modified functions and classes for modularity (`review-modularity`): "
            "flag god functions (>50 lines or mixing multiple responsibilities), high cyclomatic "
            "complexity, and monolithic classes/files that should be decomposed into focused helpers."
        ),
    },
    {
        "name": "control-flow-auditor",
        "role": "Control-Flow & Logic Auditor",
        "model": "flash",
        "workflow_mapping": "review-control-flow",
        "focus": (
            "Audit added/modified code for (1) functional logic, off-by-one, wrong state-machine "
            "transitions, wrong API/caller contracts, and broken `BUILD.bazel` target dependencies "
            "(use category `logic`, `contracts`, or `build` and `severity: \"blocking\"` for verified "
            "defects), and (2) control-flow complexity (`review-control-flow`: 3+ levels of nested "
            "conditionals/loops, missing early-return guard clauses, complex compound boolean "
            "expressions; use category `complexity` and `severity: \"warning\"` or `\"nit\"`)."
        ),
    },
    {
        "name": "constants-auditor",
        "role": "Named Constants Auditor",
        "model": "flash",
        "workflow_mapping": "review-constants",
        "focus": (
            "Audit added/modified code for unnamed magic numbers, hardcoded timeout/buffer/port "
            "literals, repeated string literals, or raw ints/strings where a named `constexpr` "
            "constant or enum belongs (`review-constants`)."
        ),
    },
    {
        "name": "unicode-and-platform-auditor",
        "role": "Unicode & Platform Auditor",
        "model": "inherit",
        "workflow_mapping": "review-unicode",
        "focus": (
            "Audit added/modified code for cross-platform UTF-8 and Windows safety (`review-unicode`): "
            "(1) require `android::base::file` (`//emulator/libs/file`, `goldfish/file/file.h`) in `goldfish` "
            "or `android/utils/file_io.h` in `external/qemu` instead of `std::ifstream`/`std::ofstream`/`fopen`; "
            "(2) require `cc_binary_with_manifest` (`load(\"//emulator/tools:cc_binary_with_manifest.bzl\", "
            "\"cc_binary_with_manifest\")`) instead of raw `cc_binary` (unless `linkshared = True`) in `goldfish` "
            "`BUILD` files (`b/572839247`); (3) require non-ASCII (`\"测试_🚀_é\"`) unit test coverage for new "
            "path/string handling."
        ),
    },
    {
        "name": "error-messages-and-statusor-auditor",
        "role": "Error Messages & StatusOr Auditor",
        "model": "inherit",
        "workflow_mapping": "go/error-messages + go/totw/181",
        "focus": (
            "Enumerate every error/warning/status site in the diff (`absl::*Error`, `Status(`, "
            "`LOG(ERROR/WARNING)`, CLI stderr, `raise`, `return Error`) against `go/error-messages` "
            "(TotT 607): each must state (a) what operation failed, (b) why (underlying errno/uv/curl/HTTP "
            "detail), (c) offending value + expected boundary without leaking secrets, and (d) actionable "
            "next step, using specific canonical `absl::StatusCode` values. Also enforce TotW 181 "
            "(`go/totw/181`) on `absl::StatusOr`: forbid Hungarian `_or` / `maybe_` variable names, "
            "verify `.ok()`, and access via `*var` / `var->`. Set `checked_count` to the total error/StatusOr "
            "sites audited and `finding_count` to the number of violations."
        ),
    },
    {
        "name": "concurrency-and-safety-auditor",
        "role": "Concurrency & Memory Safety Auditor",
        "model": "inherit",
        "workflow_mapping": "concurrency-and-raii",
        "focus": (
            "Audit added/modified code for thread safety, lock discipline (deadlocks, unlocked shared "
            "state, TSAN data races, BQL `RecursiveScopedVmLock` in QEMU), RAII resource/FD/handle "
            "lifetimes, integer overflow/narrowing casts on buffer sizes, and null/bounds safety."
        ),
    },
]


def _build_prior_context_section(
    spec_name: str,
    target_manifest: Dict[str, Any],
) -> str:
    """Formats incremental-pass prior specialist feedback when `review_scope == 'incremental'`."""
    if target_manifest.get("review_scope") != "incremental":
        return ""
    round_num = int(target_manifest.get("round_number") or 2)
    prev_short = str(target_manifest.get("previous_target_id") or "")[:SHORT_ID_DISPLAY_LEN]
    prior_items = (target_manifest.get("prior_specialist_feedback") or {}).get(spec_name) or []
    prior_json = json.dumps(prior_items, indent=2, ensure_ascii=False)
    return (
        f"## Incremental Review Context (Round {round_num}, Interdiff from `{prev_short}`)\n"
        "Verify whether your previous pass findings below were resolved, inspect only the "
        "interdiff hunks below for new defects on `changed_lines_by_file`, and return "
        "`findings: []` if clean.\n\n"
        "### Previous Pass Feedback From Your Specialist Role\n"
        f"```json\n{prior_json}\n```\n\n"
    )


def build_specialist_prompts(target_manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Constructs the non-overlapping specialist definitions with embedded diff context."""
    is_incremental = target_manifest.get("review_scope") == "incremental"
    risk_tier = str(target_manifest.get("risk_tier") or "full")
    if risk_tier in ("stage0_only", "stage0_blocked"):
        return []
    changed_map = target_manifest.get("changed_lines_by_file") or {}
    prior_map = target_manifest.get("prior_specialist_feedback") or {}
    msg_changed = bool(target_manifest.get("commit_msg_changed"))
    if is_incremental and not changed_map and not msg_changed and not any(prior_map.values()):
        return []

    changed_json = json.dumps(changed_map, indent=2, ensure_ascii=False)
    commit_msg = (target_manifest.get("commit_message") or "").strip()
    bug_ids = ", ".join(target_manifest.get("bug_ids") or []) or "None"
    diff_text = target_manifest.get("diff_text") or ""
    enclosing_context = build_enclosing_file_context(
        target_manifest,
        repo_dir=str(target_manifest.get("repo_dir") or "."),
    )
    enclosing_section = (
        f"## Enclosing File Context\n```text\n{enclosing_context}\n```\n\n"
        if enclosing_context and enclosing_context not in ("(no changed files)", "(enclosing file context unavailable)")
        else ""
    )
    use_fast_model = is_incremental or risk_tier == "lite"
    prompts: List[Dict[str, Any]] = []
    for spec in REVIEW_SWARM_SPECIALISTS:
        if is_incremental and spec["name"] == COMMIT_AUDITOR_NAME and not msg_changed and not prior_map.get(COMMIT_AUDITOR_NAME):
            continue
        prior_section = _build_prior_context_section(spec["name"], target_manifest)
        prompt_text = (
            f"# Role: {spec['role']} (`{spec['name']}`)\n"
            f"Target: {target_manifest.get('target_type')} `{target_manifest.get('target_id')}` "
            f"(project `{target_manifest.get('project')}`, branch `{target_manifest.get('branch')}`)\n"
            f"Referenced Bug IDs: {bug_ids}\n\n"
            f"## Specialist Assignment (`{spec['workflow_mapping']}`)\n{spec['focus']}\n\n"
            f"{SHARED_SPECIALIST_RULES}\n{prior_section}"
            f"## Modified Line Allowlist (`changed_lines_by_file`)\n```json\n{changed_json}\n```\n\n"
            f"## `/COMMIT_MSG`\n```text\n{commit_msg}\n```\n\n"
            f"{enclosing_section}"
            f"## Unified Diff\n```diff\n{diff_text}\n```\n"
        )
        prompts.append({
            "name": spec["name"],
            "role": spec["role"],
            "model": INCREMENTAL_PASS_MODEL if use_fast_model else spec["model"],
            "workflow_mapping": spec["workflow_mapping"],
            "prompt": prompt_text,
        })
    return prompts


WORKFLOW_SCRIPT_TEMPLATE: str = '''# Auto-generated 8-Specialist Single-Wave Parallel Code-Review Workflow
import json
from pathlib import Path
import sys
import time

REPO_SRC_DIR = {repo_src_dir}
if REPO_SRC_DIR not in sys.path:
    sys.path.insert(0, REPO_SRC_DIR)

from commands.harness.review_history import (
    build_wave_metrics,
    persist_iteration_and_metrics,
)
from commands.harness.review_swarm_status import (
    record_specialist_completion,
    write_auditing_swarm_status,
    write_completed_swarm_status,
)
from commands.harness.review_swarm import (
    MAX_SWARM_CONCURRENCY,
    SPECIALIST_OUTPUT_SCHEMA,
    VERIFIER_SCHEMA,
    apply_verifier_to_specialist_outputs,
    atomic_write_text,
    build_enclosing_file_context,
    build_refutation_verifier_prompt,
    build_specialist_prompts,
    collect_blocking_candidates,
    filter_and_aggregate_findings,
    normalize_stage1_outputs,
    run_deterministic_stage0,
)
from lib.output import ensure_utf8_stdio

try:
    from advanced import parallel as _adv_parallel
except ImportError:
    _adv_parallel = None

MANIFEST_PATH = Path({manifest_path})
OUTPUT_DIR = Path({output_dir})
CACHE_ROOT = Path({cache_root})

manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
specialists = build_specialist_prompts(manifest)
wave_t0 = time.monotonic()
status_doc = write_auditing_swarm_status(OUTPUT_DIR, manifest, specialists)

phase("Stage 0: Deterministic Pre-Upload Checks")
stage0_outputs = run_deterministic_stage0(manifest, repo_dir=str(manifest.get("repo_dir") or "."))

phase("Wave 1: 8-Specialist Parallel Code Review")
log(
    f"Launching {{len(specialists)}} specialist(s) "
    f"(round={{manifest.get('round_number', 1)}}, scope={{manifest.get('review_scope', 'full')}})..."
)

async def _launch_specialist(spec):
    t0 = time.monotonic()
    effective_model = spec["model"]
    try:
        res = await agent(
            prompt=spec["prompt"],
            schema=SPECIALIST_OUTPUT_SCHEMA,
            role=spec["role"],
            model=effective_model,
        )
    except Exception:
        if effective_model != "flash":
            effective_model = "flash"
            res = await agent(
                prompt=spec["prompt"],
                schema=SPECIALIST_OUTPUT_SCHEMA,
                role=spec["role"],
                model=effective_model,
            )
        else:
            res = {{"findings": []}}
    elapsed = round(time.monotonic() - t0, 3)
    out = dict(res) if isinstance(res, dict) else {{"findings": []}}
    out["specialist"] = spec["name"]
    out["role"] = spec["role"]
    out["model"] = effective_model
    out["duration_sec"] = elapsed
    record_specialist_completion(OUTPUT_DIR, status_doc, out)
    log(f"[{{spec['name']}}] completed in {{elapsed:.2f}}s (model={{effective_model}})")
    return out

if not specialists:
    log("Incremental pass has 0 changed diff lines and 0 unresolved findings; skipping launches.")
    raw_results = []
elif _adv_parallel is not None:
    raw_results = await _adv_parallel(
        specialists,
        _launch_specialist,
        max_concurrency=MAX_SWARM_CONCURRENCY,
    )
else:
    raw_results = await parallel(specialists, _launch_specialist)

changed_lines_map = {{
    k: set(v) for k, v in (manifest.get("changed_lines_by_file") or {{}}).items()
}}
normalized_outputs = normalize_stage1_outputs(stage0_outputs + list(raw_results), changed_lines_map)
flat_stage1 = [
    {{**f, "specialist": out.get("specialist", "")}}
    for out in normalized_outputs
    for f in (out.get("findings") or [])
    if isinstance(f, dict)
]
blocking_candidates = collect_blocking_candidates(flat_stage1)
if blocking_candidates:
    phase("Stage 2: Refutation Verifier on Candidate Blocking Findings")
    enclosing_ctx = build_enclosing_file_context(manifest, repo_dir=str(manifest.get("repo_dir") or "."))
    v_prompt = build_refutation_verifier_prompt(manifest, blocking_candidates, enclosing_ctx)
    try:
        v_res = await agent(
            prompt=v_prompt,
            schema=VERIFIER_SCHEMA,
            role="Stage 2 Refutation Verifier",
            model="flash",
        )
    except Exception:
        v_res = {{"decisions": []}}
    verified_outputs = apply_verifier_to_specialist_outputs(normalized_outputs, v_res)
else:
    verified_outputs = normalized_outputs

wall_clock_sec = time.monotonic() - wave_t0
phase("Wave 2: Deterministic Python Diff-Boundary Filter & Telemetry Persistence")
metrics = build_wave_metrics(manifest, raw_results, wall_clock_sec)
aggregated = filter_and_aggregate_findings(
    verified_outputs,
    manifest.get("changed_lines_by_file") or {{}},
    metrics=metrics,
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
report_md_path = OUTPUT_DIR / "review_swarm_report.md"
gerrit_json_path = OUTPUT_DIR / "gerrit_review_payload.json"
atomic_write_text(report_md_path, aggregated["markdown_report"])
atomic_write_text(
    gerrit_json_path,
    json.dumps(aggregated["gerrit_review_payload"], indent=2, ensure_ascii=False) + "\\n",
)
metrics_path = persist_iteration_and_metrics(
    OUTPUT_DIR, CACHE_ROOT, manifest, raw_results, aggregated, metrics
)
write_completed_swarm_status(OUTPUT_DIR, status_doc, aggregated, metrics)

log(aggregated["reuse_audit_line"])
log(aggregated["error_audit_line"])
log(f"Saved report={{report_md_path}}, metrics={{metrics_path}} ({{wall_clock_sec:.2f}}s)")
ensure_utf8_stdio()
print(aggregated["markdown_report"])
'''


def generate_run_workflow_script(
    manifest_path: Path,
    output_dir: Path,
    cache_root: Optional[Path] = None,
) -> str:
    """Generates a single-wave 8-agent `run_workflow` Python script with atomic output writes."""
    repo_src_dir = Path(__file__).resolve().parents[2]
    resolved_cache = cache_root if cache_root is not None else output_dir.parent
    return WORKFLOW_SCRIPT_TEMPLATE.format(
        repo_src_dir=repr(str(repo_src_dir)),
        manifest_path=repr(str(manifest_path)),
        output_dir=repr(str(output_dir)),
        cache_root=repr(str(resolved_cache)),
    )


def _resolve_target_manifest(
    cl: Optional[str],
    commit: Optional[str],
    target: Optional[str],
    repo_dir: Optional[Path],
    preloaded_target: Optional[Dict[str, Any]],
    branch_hint: Optional[str],
) -> Dict[str, Any]:
    """Resolves the target manifest dictionary from a preloaded dict, Gerrit CL, or Git commit."""
    if preloaded_target is not None:
        return dict(preloaded_target)
    if cl:
        return fetch_gerrit_cl_target(cl)
    if target:
        try:
            cl_id = parse_cl_identifier(target)
        except ValueError:
            commit = target
        else:
            return fetch_gerrit_cl_target(cl_id)
    return fetch_git_commit_target(
        commit or "HEAD",
        repo_dir=repo_dir,
        branch_hint=branch_hint,
    )


def _write_bundle_artifacts(
    base_out: Path,
    effective_root: Path,
    target_manifest: Dict[str, Any],
    specialists: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Atomically writes `diff_manifest.json`, `run_review_swarm_workflow.py`, and `swarm_workers.json`."""
    manifest_path = base_out / "diff_manifest.json"
    workflow_script_path = base_out / "run_review_swarm_workflow.py"
    swarm_briefs_path = base_out / "swarm_workers.json"

    atomic_write_text(
        manifest_path,
        json.dumps(target_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(
        workflow_script_path,
        generate_run_workflow_script(manifest_path, base_out, cache_root=effective_root),
    )
    swarm_workers = [
        {
            "name": spec["name"],
            "role": spec["role"],
            "model": spec["model"],
            "workflow_mapping": spec["workflow_mapping"],
            "brief": spec["prompt"],
        }
        for spec in specialists
    ]
    atomic_write_text(
        swarm_briefs_path,
        json.dumps(swarm_workers, indent=2, ensure_ascii=False) + "\n",
    )
    status_path = write_initial_swarm_status(base_out, target_manifest, specialists)
    return {
        "manifest_path": str(manifest_path),
        "workflow_script_path": str(workflow_script_path),
        "swarm_briefs_path": str(swarm_briefs_path),
        "swarm_status_path": str(status_path),
    }


def prepare_review_swarm_bundle(
    cl: Optional[str] = None,
    commit: Optional[str] = None,
    mode: str = "workflow",
    repo_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    preloaded_target: Optional[Dict[str, Any]] = None,
    branch_hint: Optional[str] = None,
    target: Optional[str] = None,
    force_full: bool = False,
) -> Dict[str, Any]:
    """Prepares the diff manifest, specialist prompts, and `run_workflow` script."""
    raw_manifest = _resolve_target_manifest(
        cl=cl, commit=commit, target=target, repo_dir=repo_dir,
        preloaded_target=preloaded_target, branch_hint=branch_hint,
    )
    target_id = str(raw_manifest.get("target_id") or "HEAD")
    rev_id = str(raw_manifest.get("current_revision") or "").strip()
    safe_id = (
        f"{sanitize_target_id(target_id)}_{sanitize_target_id(rev_id)}"
        if raw_manifest.get("target_type") == "gerrit_cl" and rev_id
        else sanitize_target_id(target_id)
    )
    effective_root = cache_root if cache_root is not None else (get_harness_base_dir() / REVIEW_SWARM_SUBDIR)
    target_manifest = apply_incremental_history(raw_manifest, effective_root, force_full=force_full)
    tier_info = classify_review_risk_tier(
        target_manifest,
        repo_dir=str(repo_dir or target_manifest.get("repo_dir") or "."),
        force_full=force_full,
    )
    target_manifest["risk_tier"] = tier_info["risk_tier"]
    target_manifest["code_files"] = tier_info["code_files"]
    target_manifest["code_lines_changed"] = tier_info["code_lines_changed"]
    base_out = output_dir if output_dir is not None else (effective_root / safe_id)
    base_out.mkdir(parents=True, exist_ok=True)

    specialists = build_specialist_prompts(target_manifest)
    paths = _write_bundle_artifacts(base_out, effective_root, target_manifest, specialists)
    if (
        tier_info["risk_tier"] in ("stage0_only", "stage0_blocked")
        and not (base_out / "gerrit_review_payload.json").is_file()
    ):
        stage0_outputs = tier_info["stage0_outputs"]
        stage0_metrics = build_wave_metrics(target_manifest, stage0_outputs, 0.01)
        aggregated = filter_and_aggregate_findings(
            stage0_outputs,
            target_manifest.get("changed_lines_by_file") or {},
            metrics=stage0_metrics,
        )
        atomic_write_text(base_out / "review_swarm_report.md", aggregated["markdown_report"])
        atomic_write_text(
            base_out / "gerrit_review_payload.json",
            json.dumps(aggregated["gerrit_review_payload"], indent=2, ensure_ascii=False) + "\n",
        )
        if target_manifest.get("review_scope") != "incremental":
            persist_iteration_and_metrics(
                base_out, effective_root, target_manifest, stage0_outputs, aggregated, stage0_metrics
            )
        status_doc = json.loads(Path(paths["swarm_status_path"]).read_text(encoding="utf-8"))
        write_completed_swarm_status(base_out, status_doc, aggregated, stage0_metrics)
    spec_meta = [{k: s[k] for k in ("name", "role", "model", "workflow_mapping")} for s in specialists]
    return {
        "mode": mode, "target_type": target_manifest.get("target_type"), "target_id": target_id,
        "change_key": target_manifest.get("change_key", ""),
        "round_number": target_manifest.get("round_number", 1),
        "review_scope": target_manifest.get("review_scope", "full"),
        "risk_tier": target_manifest["risk_tier"],
        "code_lines_changed": target_manifest["code_lines_changed"],
        "subject": target_manifest.get("subject", ""), "bug_ids": target_manifest.get("bug_ids", []),
        "files": target_manifest.get("files", []),
        "changed_lines_by_file": target_manifest.get("changed_lines_by_file", {}),
        "specialist_count": len(specialists), "specialists": spec_meta,
        **paths, "output_dir": str(base_out),
    }


__all__ = [
    "CODE_REVIEW_EXTENSIONS",
    "COMMIT_MSG_FILE",
    "DEFAULT_GERRIT_HOST",
    "LITE_SWARM_MAX_CODE_LINES",
    "MAX_SWARM_CONCURRENCY",
    "REVIEW_SWARM_SPECIALISTS",
    "SHARED_SPECIALIST_RULES",
    "SPECIALIST_FINDING_ITEM_SCHEMA",
    "SPECIALIST_OUTPUT_SCHEMA",
    "VERIFIER_SCHEMA",
    "apply_verifier_decisions",
    "apply_verifier_to_specialist_outputs",
    "atomic_write_text",
    "build_enclosing_file_context",
    "build_gerrit_review_payload",
    "build_refutation_verifier_prompt",
    "build_specialist_prompts",
    "classify_review_risk_tier",
    "collect_blocking_candidates",
    "extract_bug_ids_from_commit_msg",
    "fetch_gerrit_cl_target",
    "fetch_git_commit_target",
    "filter_and_aggregate_findings",
    "generate_run_workflow_script",
    "normalize_stage1_outputs",
    "parse_cl_identifier",
    "parse_unified_diff_changed_lines",
    "prepare_review_swarm_bundle",
    "render_review_swarm_markdown",
    "resolve_git_repo_dir",
    "run_deterministic_stage0",
    "serialize_changed_lines",
]
