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

"""Fix checking & older repository build version awareness module for crash analyzer."""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CLOSED_STATUSES = {"FIXED", "VERIFIED", "OBSOLETE", "CLOSED", "DONE"}


@dataclass
class FixStatusResult:
    """Encapsulates whether a crash has already been fixed and older repository build info."""

    is_already_fixed: bool = False
    fix_status: str = "UNKNOWN"
    fix_reason: str = ""
    build_id: str = "unknown"
    version_str: str = "unknown"
    is_older_build: bool = True
    older_version_warning: str = ""
    fixed_issues: List[Dict[str, Any]] = field(default_factory=list)
    relevant_commits: List[str] = field(default_factory=list)
    agent_version_notice: str = ""


def extract_crash_version_info(meta_json: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts Build ID, product version, OS, architecture, and timestamp from crash metadata.

    Args:
        meta_json: Dictionary loaded from metadata.json or report_proto structure.

    Returns:
        Dictionary containing build_id, version_str, product_name, os_name, architecture, and timestamp.
    """
    proto = meta_json.get("report_proto", meta_json)
    prod = proto.get("product", {})
    version_str = prod.get("Version", "")
    build_id = "unknown"
    if version_str:
        build_id = version_str.split("-")[-1] if "-" in version_str else version_str

    if build_id == "unknown" and "build_id" in meta_json:
        build_id = str(meta_json["build_id"])

    os_info = proto.get("os", {})
    cpu_info = proto.get("cpu", {})
    os_name = os_info.get("Name", "Unknown")
    architecture = cpu_info.get("Architecture", "Unknown")
    timestamp = proto.get("crashTime") or proto.get("Timestamp") or ""

    custom_keys = {}
    for item in proto.get("productdata", []):
        k, v = item.get("Key"), item.get("Value")
        if k and v:
            custom_keys[str(k)] = str(v)

    return {
        "build_id": build_id,
        "version_str": version_str or build_id,
        "product_name": prod.get("Name", "Android Emulator"),
        "os_name": os_name,
        "architecture": architecture,
        "timestamp": timestamp,
        "custom_keys": custom_keys,
    }


def check_buganizer_fixed_status(
    client: Any,
    stable_signature: Optional[str] = None,
    top_func: Optional[str] = None,
    component_id: int = 29601,
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Queries Buganizer for both open and closed issues matching signature or top fault frame.

    Args:
        client: BuganizerClient instance.
        stable_signature: Primary crash signature (e.g., 'android::FrameBuffer::post').
        top_func: Top faulting stack frame function name.
        component_id: Buganizer component ID (default: 29601).

    Returns:
        Tuple of (is_fixed, candidate_issues, fixed_reason).
    """
    candidates: List[Dict[str, Any]] = []
    is_fixed = False
    fixed_reasons: List[str] = []

    if not client:
        return is_fixed, candidates, None

    # Tier 1: Search by signature
    if stable_signature and stable_signature != "Unknown":
        clean_sig = re.sub(r"[^\w::]", " ", stable_signature).strip()
        try:
            issue_t1 = client.search_issue_by_signature(clean_sig or stable_signature)
            if issue_t1:
                iss_id = issue_t1.get("issueId") or issue_t1.get("id")
                state = issue_t1.get("issueState", {})
                status = (
                    state.get("status") or issue_t1.get("status", "UNKNOWN")
                ).upper()
                title = state.get("title") or issue_t1.get("title", "Untitled")
                assignee = state.get("assignee", {}).get("emailAddress", "Unassigned")
                candidate_info = {
                    "issue_id": str(iss_id),
                    "status": status,
                    "title": title,
                    "assignee": assignee,
                    "match_type": "Signature Match",
                }
                candidates.append(candidate_info)
                if status in CLOSED_STATUSES:
                    is_fixed = True
                    fixed_reasons.append(
                        f"Buganizer issue b/{iss_id} is {status} (Assignee: {assignee})"
                    )
        except Exception:
            pass

    # Tier 2: Search by top fault function across both open and closed bugs
    if top_func:
        try:
            queries = [
                f'componentid:{component_id} "{top_func}"',
                f'componentid:{component_id} status:open "{top_func}"',
            ]
            for q in queries:
                if getattr(client, "cli_binary", None) and not getattr(
                    client, "token", None
                ):
                    cmd = [client.cli_binary, "search", q, "--format=json"]
                    res = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=10
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        issues = json.loads(res.stdout)
                        for iss in issues if isinstance(issues, list) else [issues]:
                            iss_id = str(iss.get("issueId") or iss.get("id", ""))
                            if not iss_id or any(
                                c["issue_id"] == iss_id for c in candidates
                            ):
                                continue
                            state = iss.get("issueState", {})
                            status = (
                                state.get("status") or iss.get("status", "UNKNOWN")
                            ).upper()
                            title = state.get("title") or iss.get("title", "Untitled")
                            assignee = state.get("assignee", {}).get(
                                "emailAddress", "Unassigned"
                            )
                            candidates.append(
                                {
                                    "issue_id": iss_id,
                                    "status": status,
                                    "title": title,
                                    "assignee": assignee,
                                    "match_type": "Top Fault Frame Function",
                                }
                            )
                            if status in CLOSED_STATUSES:
                                is_fixed = True
                                fixed_reasons.append(
                                    f"Buganizer issue b/{iss_id} is {status} (Assignee: {assignee})"
                                )
                elif hasattr(client, "_call_rest_api"):
                    pass
        except Exception:
            pass

    fixed_reason_str = "; ".join(fixed_reasons) if fixed_reasons else None
    return is_fixed, candidates, fixed_reason_str


def check_git_history_fixes(
    source_dir: Optional[str],
    target_file: Optional[str],
    target_func: Optional[str] = None,
    issue_id: Optional[str] = None,
    signature: Optional[str] = None,
) -> Tuple[bool, List[str], Optional[str]]:
    """Inspects local git repository history for commits touching the faulting file or referencing the bug.

    Args:
        source_dir: Root directory of local source checkout.
        target_file: Relative path to target source file.
        target_func: Target function name where crash occurred.
        issue_id: Associated Buganizer issue ID if known.
        signature: Stable crash signature string.

    Returns:
        Tuple of (has_recent_fix_commit, list_of_commit_messages, summary_reason).
    """
    if not source_dir or not os.path.isdir(source_dir):
        return False, [], None

    relevant_commits: List[str] = []
    has_fix = False
    evidence_parts: List[str] = []

    git_dir = Path(source_dir) / ".git"
    if not git_dir.exists() and not Path(source_dir).is_dir():
        return False, [], None

    # Check git log for target file
    if target_file and (Path(source_dir) / target_file).exists():
        try:
            cmd = [
                "git",
                "-C",
                source_dir,
                "log",
                "-n",
                "15",
                "--oneline",
                "--",
                target_file,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                relevant_commits.extend(lines[:5])
                for line in lines:
                    lower_line = line.lower()
                    if any(
                        kw in lower_line
                        for kw in ("fix", "null", "crash", "bounds", "check", "deref")
                    ):
                        has_fix = True
                        evidence_parts.append(
                            f"Recent commit touching {target_file}: '{line.strip()}'"
                        )
                        break
        except Exception:
            pass

    # Check git log by issue ID if available
    if issue_id:
        try:
            cmd = [
                "git",
                "-C",
                source_dir,
                "log",
                "-n",
                "5",
                "--oneline",
                f"--grep={issue_id}",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    has_fix = True
                    relevant_commits.append(line.strip())
                    evidence_parts.append(
                        f"Commit references b/{issue_id}: '{line.strip()}'"
                    )
        except Exception:
            pass

    summary = "; ".join(evidence_parts) if evidence_parts else None
    return has_fix, relevant_commits, summary


def evaluate_crash_fix_status(
    crash_id: str,
    metadata_path: Optional[Path] = None,
    action_data: Optional[Dict[str, Any]] = None,
    buganizer_client: Optional[Any] = None,
    source_dir: Optional[str] = None,
    candidate_issues: Optional[List[Dict[str, Any]]] = None,
    meta_json: Optional[Dict[str, Any]] = None,
) -> FixStatusResult:
    """Evaluates whether a crash report was built against an older repo revision and whether the bug is already fixed.

    Args:
        crash_id: The crash ID string.
        metadata_path: Optional path to metadata.json file.
        action_data: Optional parsed actionability dictionary.
        buganizer_client: Optional BuganizerClient instance.
        source_dir: Optional local source checkout directory path.
        candidate_issues: Optional pre-fetched list of candidate Buganizer issues.
        meta_json: Optional pre-loaded metadata dictionary.

    Returns:
        FixStatusResult containing fix evaluation, build ID, warnings, and agent guardrail prompt notice.
    """
    data = meta_json or {}
    if not data and metadata_path and metadata_path.exists():
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    vinfo = extract_crash_version_info(data)
    build_id = vinfo.get("build_id", "unknown")
    version_str = vinfo.get("version_str", "unknown")
    stable_sig = data.get("report_proto", {}).get("stableSignature") or vinfo.get(
        "custom_keys", {}
    ).get("signature")

    target_file = (action_data or {}).get("target_file")
    target_func = (action_data or {}).get("target_function")

    is_fixed_buganizer = False
    fixed_issues: List[Dict[str, Any]] = []
    bug_reason: Optional[str] = None

    if candidate_issues:
        for iss in candidate_issues:
            status = iss.get("status", "UNKNOWN").upper()
            if status in CLOSED_STATUSES:
                is_fixed_buganizer = True
                fixed_issues.append(iss)
                bug_reason = f"Buganizer issue b/{iss.get('issue_id')} is {status}"
    elif buganizer_client:
        is_fixed_buganizer, fixed_issues, bug_reason = check_buganizer_fixed_status(
            client=buganizer_client,
            stable_signature=stable_sig,
            top_func=target_func,
        )

    # Check local git history
    first_issue_id = fixed_issues[0].get("issue_id") if fixed_issues else None
    has_git_fix, recent_commits, git_reason = check_git_history_fixes(
        source_dir=source_dir,
        target_file=target_file,
        target_func=target_func,
        issue_id=first_issue_id,
        signature=stable_sig,
    )

    is_already_fixed = is_fixed_buganizer or has_git_fix
    if is_already_fixed:
        fix_status = "FIXED" if is_fixed_buganizer else "POTENTIALLY_FIXED"
        reasons = [r for r in (bug_reason, git_reason) if r]
        fix_reason = (
            "; ".join(reasons) or "Bug marked resolved in tracker or repository"
        )
    else:
        fix_status = "OPEN"
        fix_reason = "No closed Buganizer tickets or recent fixing commits identified"

    older_warning = (
        f"⚠️ Warning: This crash was captured from Build ID {build_id} (Version: {version_str}), "
        "which was built against an older version of the repository than current workspace (HEAD). "
        "The bug may already have been fixed in the current codebase."
    )

    agent_version_notice = f"""⚠️ CRASH BUILD & REPOSITORY VERSION CONTEXT:
• Crash Build ID: {build_id} (Product Version: {version_str})
• Warning: This crash was captured from a build compiled against an older version of the repository than the current workspace (HEAD). The bug may already have been fixed in the current codebase!
• Fix Status Assessment: {fix_status} ({fix_reason})

BEFORE WRITING CODE OR MAKING MODIFICATIONS:
1. Inspect git log for {target_file or 'the faulting source file'} and verify if {target_func or 'the target function'} or this crash signature has already been modified or fixed in newer commits.
2. Inspect the current source code at {target_file or 'target file'} to verify whether the faulting condition (e.g., null pointer dereference, bounds overflow) still exists at HEAD.
3. If the bug is ALREADY FIXED in the current workspace:
   - Verify existing test coverage or write a unit test to confirm the fix.
   - Document the existing fixing commit/CL in your final response.
   - Do NOT introduce redundant, duplicate, or conflicting code changes.
4. If and only if the bug is confirmed to still exist in the current workspace, follow the TDD loop (Red/Green/Refactor) coordinating with test_enforcer."""

    return FixStatusResult(
        is_already_fixed=is_already_fixed,
        fix_status=fix_status,
        fix_reason=fix_reason,
        build_id=build_id,
        version_str=version_str,
        is_older_build=True,
        older_version_warning=older_warning,
        fixed_issues=fixed_issues,
        relevant_commits=recent_commits,
        agent_version_notice=agent_version_notice,
    )


def format_agent_version_guardrail_prompt(
    crash_id: str,
    rca_path: str,
    target_file: str,
    local_file_path: Optional[str],
    target_func: str,
    remediation: str,
    fix_status: FixStatusResult,
) -> str:
    """Constructs the comprehensive agent dispatch prompt including older repo version guardrails.

    Args:
        crash_id: The crash ID string.
        rca_path: Path to rca_summary.md file.
        target_file: Target relative file path.
        local_file_path: Absolute path to local file in workspace.
        target_func: Target function name.
        remediation: Remediation summary text.
        fix_status: FixStatusResult instance.

    Returns:
        Formatted prompt string ready for agentapi conversation initiation.
    """
    fixed_indicator = (
        "ALREADY FIXED / RESOLVED ✅"
        if fix_status.is_already_fixed
        else "NEEDS VERIFICATION / FIX ⚠️"
    )
    notice = fix_status.agent_version_notice
    if not notice:
        notice = f"""⚠️ CRASH BUILD & REPOSITORY VERSION CONTEXT:
• Crash Build ID: {fix_status.build_id} (Product Version: {fix_status.version_str})
• Warning: This crash was captured from a build compiled against an older version of the repository than the current workspace (HEAD). The bug may already have been fixed in the current codebase!
• Fix Status Assessment: {fix_status.fix_status} ({fix_status.fix_reason})

BEFORE WRITING CODE OR MAKING MODIFICATIONS:
1. Inspect git log for {target_file or 'the faulting source file'} and verify if {target_func or 'the target function'} or this crash signature has already been modified or fixed in newer commits.
2. Inspect the current source code at {target_file or 'target file'} to verify whether the faulting condition (e.g., null pointer dereference, bounds overflow) still exists at HEAD.
3. If the bug is ALREADY FIXED in the current workspace:
   - Verify existing test coverage or write a unit test to confirm the fix.
   - Document the existing fixing commit/CL in your final response.
   - Do NOT introduce redundant, duplicate, or conflicting code changes.
4. If and only if the bug is confirmed to still exist in the current workspace, follow the TDD loop (Red/Green/Refactor) coordinating with test_enforcer."""

    return f"""Implement the remediation plan detailed in {rca_path} for Crash Target {crash_id}.
Target File: {target_file} (Local Path: {local_file_path or 'Search workspace'})
Target Function: {target_func}
Remediation Summary: {remediation}
Current Fix Status Assessment: {fixed_indicator} ({fix_status.fix_reason})

{notice}

Follow the TDD loop (Red/Green/Refactor) coordinating with test_enforcer.
Upon successful verification, hand off to reviewer.md for audit and committer.md to execute repo upload.
"""


class CrashFixChecker:
    """Unified engine for inspecting crash report age, Buganizer resolution, and git commit history."""

    def __init__(
        self, source_dir: Optional[str] = None, buganizer_client: Optional[Any] = None
    ) -> None:
        self.source_dir = source_dir
        self.buganizer_client = buganizer_client

    def evaluate(
        self,
        crash_id: str,
        metadata_path: Optional[Path] = None,
        action_data: Optional[Dict[str, Any]] = None,
    ) -> FixStatusResult:
        return evaluate_crash_fix_status(
            crash_id=crash_id,
            metadata_path=metadata_path,
            action_data=action_data,
            buganizer_client=self.buganizer_client,
            source_dir=self.source_dir,
        )
