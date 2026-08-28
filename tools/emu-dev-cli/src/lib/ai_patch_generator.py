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

"""AI-assisted root cause analysis, Jetski environment setup, and patch generator for flaky tests."""

import logging
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional, Tuple

from lib.agent import AgentApiClient
from lib.ath_api import FlakyTestRecord, fetch_invocation_artifacts
from lib.markdown import extract_fenced_code_blocks
from lib.workspace import WorkspacePathResolver, get_flakiness_sandbox_dir

logger = logging.getLogger(__name__)


@dataclass
class AIPatchResult:
    test_identifier: str
    root_cause_summary: str
    detailed_explanation: str
    proposed_diff: str
    investigation_cmd_path: Optional[str] = None
    rca_path: Optional[str] = None
    confidence_score: float = 0.50


def _perform_in_process_log_analysis(
    sandbox_dir: Path, record: FlakyTestRecord, matched_src_file: Optional[Path]
) -> Tuple[str, str, str]:
    """Scans downloaded logs and source code to formulate root cause summary, explanation, and diff strategy."""
    findings = []
    has_tsan = False
    has_asan = False

    for log_path in sandbox_dir.glob("*"):
        if log_path.is_file() and log_path.suffix in (".txt", ".log", ".dump"):
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
                if "ThreadSanitizer: data race" in content:
                    has_tsan = True
                    findings.append("ThreadSanitizer detected data race condition")
                if "AddressSanitizer: heap-use-after-free" in content:
                    has_asan = True
                    findings.append("AddressSanitizer detected heap-use-after-free")
                for line in content.splitlines():
                    if any(k in line for k in ("FAILED", "SIGSEGV", "SIGABRT", "Assertion failed")):
                        findings.append(line.strip())
                        if len(findings) >= 5:
                            break
            except Exception:
                pass

    if has_tsan:
        rc_summary = f"ThreadSanitizer data race in {record.module_name}"
    elif has_asan:
        rc_summary = f"AddressSanitizer heap memory corruption in {record.module_name}"
    elif findings:
        rc_summary = f"Execution assertion/signal failure in {record.module_name}"
    else:
        rc_summary = f"Unsynchronized timing anomaly in {record.module_name}"

    src_rel = matched_src_file.name if matched_src_file else f"{record.module_name}.cpp"

    diff_str = (
        f"--- a/{src_rel}\n"
        f"+++ b/{src_rel}\n"
        f"@@ -1,5 +1,8 @@\n"
        f" // Source: {matched_src_file or src_rel}\n"
        f"+// Automated Fix Strategy for {record.test_identifier}:\n"
        f"+// 1. Synchronize shared state access across concurrent execution threads.\n"
        f"+// 2. Eliminate sleep-based timing assumptions in favor of condition variable notifications.\n"
    )

    explanation_lines = [
        f"Test target '{record.test_identifier}' failed with flake rate {record.flake_rate_pct:.1f}% ({record.failed_runs}/{record.total_runs} runs) on target '{record.target}'.",
        f"Matched source file: file://{matched_src_file}" if matched_src_file else "Matched source file: Search workspace",
    ]
    if findings:
        explanation_lines.append("\nLog Analysis Findings:")
        for f in findings[:5]:
            explanation_lines.append(f"  • {f}")

    return rc_summary, "\n".join(explanation_lines), diff_str


def generate_ai_patch_for_test(
    record: FlakyTestRecord,
    out_dir: Optional[str] = None,
    model: str = "Gemini Next",
) -> AIPatchResult:
    """Invokes Jetski / Antigravity AI analysis pipeline for a flaky test record.

    1. Fetches diagnostic logcats, host logs, and traces for latest_invocation_id.
    2. Resolves exact local workspace file paths via WorkspacePathResolver.
    3. Generates investigation prompt (investigation_prompt.txt).
    4. Dispatches emu_main_next_engineer via AgentDispatcher and synchronously waits for RCA report.
    5. Reads existing or newly generated rca_summary.md for root cause and diff.
    """
    safe_name = (
        record.test_identifier.replace("/", "_")
        .replace(":", "_")
        .replace("@", "")
    )
    sandbox_dir = (
        Path(out_dir).resolve()
        if out_dir
        else get_flakiness_sandbox_dir(f"triage/{safe_name}")
    )
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    if record.failed_runs == 0 or record.flake_rate_pct == 0.0:
        sys.stderr.write(
            f"\nℹ️ Notice: Cannot run AI root-cause analysis for '{record.test_identifier}'.\n"
            f"   The test has 0 failing runs (0.0% failure rate across {record.total_runs} runs).\n"
        )
        raise RuntimeError(
            f"Test target '{record.test_identifier}' has 0 failing runs (0.0% failure rate). AI analysis skipped."
        )

    # Step 1: Download diagnostic artifacts
    if not record.latest_invocation_id:
        sys.stderr.write(
            f"\n❌ Error: Cannot run AI root-cause analysis for '{record.test_identifier}'.\n"
            f"   No valid invocation ID is associated with this test record.\n"
        )
        raise RuntimeError(
            f"No valid invocation ID associated with test record '{record.test_identifier}'."
        )

    artifact_res = {}
    try:
        artifact_res = fetch_invocation_artifacts(
            invocation_id=record.latest_invocation_id,
            artifact_type="ALL",
            out_dir=str(sandbox_dir),
        )
    except Exception as e:
        sys.stderr.write(
            f"ℹ️ Artifact retrieval notice for {record.latest_invocation_id}: {e}\n"
        )

    # Step 2: Resolve workspace package directory and source file locations via WorkspacePathResolver
    resolver = WorkspacePathResolver("emu-main-next")
    pkg_dir, matched_src_file = resolver.resolve_bazel_target_source_path(record.test_identifier)

    # Step 3: Construct Jetski prompt via TemplateEngine
    log_files_str = "\n".join(
        f"  • {f['file_name']}: file://{f['file_path']} ({f['source']})"
        for f in artifact_res.get("downloaded_files", [])
    ) or "  • (Logcats and diagnostic logs stored in sandbox directory)"

    from lib.template_engine import get_template_engine

    engine = get_template_engine()
    context = {
        "test_identifier": record.test_identifier,
        "target": record.target,
        "flake_rate_pct": f"{record.flake_rate_pct:.1f}",
        "failed_runs": record.failed_runs,
        "total_runs": record.total_runs,
        "latest_build_id": record.latest_build_id or "N/A",
        "latest_invocation_id": record.latest_invocation_id or "N/A",
        "sandbox_dir": str(sandbox_dir),
        "log_files_str": log_files_str,
        "matched_src_file": (
            f"file://{matched_src_file}"
            if matched_src_file
            else (f"file://{pkg_dir}" if pkg_dir else "Search workspace for target definition")
        ),
        "module_name": record.module_name,
    }
    jetski_prompt = engine.render("jetski_investigation_prompt.md", context)

    prompt_path = sandbox_dir / "investigation_prompt.txt"
    prompt_path.write_text(jetski_prompt, encoding="utf-8")

    from lib.agent_dispatcher import AgentDispatcher

    dispatcher = AgentDispatcher()
    dispatcher.dispatch_investigation(
        record=record,
        prompt=jetski_prompt,
        sandbox_dir=sandbox_dir,
        profile="emu_main_next_engineer",
        model=model,
    )
    rca_summary_path = sandbox_dir / "rca_summary.md"

    # Step 6: Read generated RCA summary
    if not rca_summary_path.exists():
        raise RuntimeError(f"RCA summary file missing at file://{rca_summary_path}")

    rca_text = rca_summary_path.read_text(encoding="utf-8")
    diffs = extract_fenced_code_blocks(rca_text, lang="diff")
    if not diffs:
        sys.stderr.write(
            f"\n❌ Error: RCA summary at file://{rca_summary_path} did not contain a valid diff block.\n"
        )
        raise RuntimeError(
            f"RCA summary for '{record.test_identifier}' did not contain a valid diff code block."
        )

    proposed_diff = diffs[0].strip()
    root_cause_summary = "AI-generated root cause analysis"
    for line in rca_text.splitlines():
        if "**Root Cause Summary:**" in line:
            root_cause_summary = line.split("**Root Cause Summary:**", 1)[1].strip()
    detailed_explanation = rca_text
    confidence = 0.90

    return AIPatchResult(
        test_identifier=record.test_identifier,
        root_cause_summary=root_cause_summary,
        detailed_explanation=detailed_explanation,
        proposed_diff=proposed_diff,
        investigation_cmd_path=None,
        rca_path=str(rca_summary_path),
        confidence_score=confidence,
    )


def format_bug_body_with_ai_patch(
    record: FlakyTestRecord, patch: AIPatchResult
) -> str:
    """Formats structured Buganizer issue body containing AI root cause and suggested fix."""
    inv_script_line = (
        f"\n*Interactive Jetski Investigation Script:* `{patch.investigation_cmd_path}`\n"
        if patch.investigation_cmd_path
        else ""
    )
    return f"""### ⚠️ Flakiness Alert: {record.test_identifier}

Test target `{record.test_identifier}` experienced a **{record.flake_rate_pct:.1f}% failure rate** ({record.failed_runs}/{record.total_runs} runs) on platform `{record.target}`.

* **Android Test Hub Config:** `{record.config_name}`
* **Latest Build ID:** `{record.latest_build_id or 'N/A'}`
* **Latest Invocation ID:** `{record.latest_invocation_id or 'N/A'}`{inv_script_line}
---

### 🤖 AI-Suggested Root Cause & Proposed Fix

**Root Cause Summary:** {patch.root_cause_summary}

**Explanation:**
{patch.detailed_explanation}

**Proposed Code Patch (Diff):**
```diff
{patch.proposed_diff}
```

---

*To automatically reproduce this flake, apply the fix, and upload a Gerrit CL, run:*
```bash
emu-dev-cli flakiness fix --bug <BUG_ID>
```
"""
