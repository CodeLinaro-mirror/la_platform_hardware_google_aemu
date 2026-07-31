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

"""Crash Investigation Subcommand Group for emu-dev-cli.

Integrates CrashAdvisor (emulator/crashreport/tool/advisor) to provide:
- Intelligent Buganizer deduplication & stack analysis (`find-bug`)
- Automated RCA & Buganizer issue filing (`file-bug`)
- Closed-loop autonomous engineer fixing (`autofix`)
- Unified crash analysis (`analyze`)
"""

import argparse
import getpass
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from commands.source_directory import (
    get_source_directory,
    find_file_in_source_directories,
)
from lib.output import print_result


def is_path_secure_user_owned(path: str, is_dir: bool = False) -> bool:
    """Verifies that a path exists, is not a symlink, is owned by current user,
    and is not writable by group or others."""
    try:
        if os.path.islink(path):
            return False
        st = os.stat(path)
        if is_dir and not stat.S_ISDIR(st.st_mode):
            return False
        if not is_dir and not stat.S_ISREG(st.st_mode):
            return False
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return False
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
        return True
    except OSError:
        return False


def create_secure_sandbox_dir(crash_id: str) -> str:
    """Creates a guaranteed atomic, user-exclusive (0o700) sandbox directory."""
    return tempfile.mkdtemp(prefix=f"crashadvisor_{crash_id}_")


def get_crashadvisor_sandbox_dir(crash_id: str, create: bool = True) -> str:
    """Resolves or creates the absolute path to a secure CrashAdvisor sandbox directory."""
    if create:
        return create_secure_sandbox_dir(crash_id)

    temp_dir = Path(tempfile.gettempdir())
    matches = sorted(
        [
            p
            for p in temp_dir.glob(f"crashadvisor_{crash_id}_*")
            if is_path_secure_user_owned(p, is_dir=True)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return str(matches[0])

    return create_secure_sandbox_dir(crash_id)



def resolve_crashadvisor_path() -> Optional[str]:
    """Dynamically resolves the absolute path to the crashadvisor directory."""
    rel_path = "hardware/generic/goldfish/emulator/crashreport/tool/advisor"

    # 1. Check registered emu-main-next source directory
    src_dir = get_source_directory("emu-main-next")
    if src_dir:
        candidate = os.path.join(src_dir, rel_path)
        if os.path.exists(candidate):
            return candidate

    # 2. Search across all registered source directories
    found = find_file_in_source_directories(os.path.join(rel_path, "advisor.py"))
    if found:
        return os.path.dirname(found)

    # 3. Traversal from script location
    curr = os.path.abspath(__file__)
    for _ in range(7):
        curr = os.path.dirname(curr)
        candidate = os.path.join(curr, rel_path)
        if os.path.exists(candidate):
            return candidate

    return None


def ensure_crashadvisor_imports():
    """Ensures crashadvisor and libs_python directories are in sys.path and returns imported modules."""
    advisor_dir = resolve_crashadvisor_path()
    if not advisor_dir or not os.path.exists(advisor_dir):
        sys.stderr.write(
            "❌ ERROR: Could not locate CrashAdvisor at hardware/generic/goldfish/emulator/crashreport/tool/advisor.\n"
            "Ensure your workspace source directory is registered via `emu-dev-cli source-directory set emu-main-next /path/to/src`.\n"
        )
        sys.exit(1)

    if advisor_dir not in sys.path:
        sys.path.insert(0, advisor_dir)

    # Ensure hardware/google/aemu/tools/libs_python is also in sys.path for `import ab`
    libs_python_rel = os.path.join("hardware", "google", "aemu", "tools", "libs_python")
    libs_python_dir = os.path.normpath(
        os.path.join(
            advisor_dir,
            "..",
            "..",
            "..",
            "..",
            "..",
            "..",
            "google",
            "aemu",
            "tools",
            "libs_python",
        )
    )
    if not os.path.exists(libs_python_dir):
        libs_python_dir = find_file_in_source_directories(libs_python_rel) or ""
    if not os.path.exists(libs_python_dir):
        src_dir = get_source_directory("emu-main-next")
        if src_dir:
            libs_python_dir = os.path.join(src_dir, libs_python_rel)
    if os.path.exists(libs_python_dir) and libs_python_dir not in sys.path:
        sys.path.insert(0, libs_python_dir)

    # Provide fallback for `from python.runfiles import Runfiles` when executed outside Bazel
    if "python.runfiles" not in sys.modules:
        try:
            from python.runfiles import Runfiles
        except ImportError:
            import types

            py_mod = types.ModuleType("python")
            rf_mod = types.ModuleType("python.runfiles")

            class DummyRunfiles:
                @staticmethod
                def Create():
                    return None

                def Rlocation(self, path):
                    return path

            rf_mod.Runfiles = DummyRunfiles
            py_mod.runfiles = rf_mod
            sys.modules["python"] = py_mod
            sys.modules["python.runfiles"] = rf_mod

    # Try importing googleapiclient, oauth2client, tqdm; if missing in host Python environment, set up dummy module stubs
    for mod_name in [
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.http",
        "oauth2client",
        "oauth2client.client",
        "tqdm",
    ]:
        try:
            __import__(mod_name)
        except ImportError:
            import types

            m = types.ModuleType(mod_name)
            if mod_name == "tqdm":
                m.tqdm = lambda *a, **k: None
            if mod_name == "oauth2client.client":
                m.AccessTokenCredentials = lambda *a, **k: None
            sys.modules[mod_name] = m

    try:
        import api
        import buganizer
        import client
        import context
        import dump
        import metadata
        import rca
        import symbols
        import advisor

        return {
            "api": api,
            "buganizer": buganizer,
            "client": client,
            "context": context,
            "dump": dump,
            "metadata": metadata,
            "rca": rca,
            "symbols": symbols,
            "advisor": advisor,
        }
    except ImportError as e:
        sys.stderr.write(f"❌ ERROR: Failed to import CrashAdvisor modules: {e}\n")
        sys.exit(1)


def parse_crash_id(input_str: str) -> str:
    """Parses a crash ID from a raw ID or go/crash URL."""
    cleaned = input_str.strip()
    if "crash.corp.google.com/" in cleaned or "go/crash/" in cleaned:
        cleaned = cleaned.rstrip("/").split("/")[-1]
    return cleaned


def acquire_auth_token(user_token: Optional[str] = None) -> Optional[str]:
    """Returns explicit token, BUGANIZER_TOKEN env var, or attempts automated oauth2l fetch/refresh."""
    if user_token:
        # Strip trailing newlines or "Bearer " prefix if user passed shell subshell output
        cleaned = user_token.strip()
        if cleaned.startswith("Bearer "):
            cleaned = cleaned.replace("Bearer ", "").strip()
        return cleaned

    env_token = os.environ.get("BUGANIZER_TOKEN") or os.environ.get("OAUTH2_TOKEN")
    if env_token:
        cleaned = env_token.strip()
        if cleaned.startswith("Bearer "):
            cleaned = cleaned.replace("Bearer ", "").strip()
        return cleaned

    import getpass

    user = os.environ.get("USER") or getpass.getuser()
    sso_email = f"{user}@google.com"
    scopes = [
        "https://www.googleapis.com/auth/buganizer",
        "https://www.googleapis.com/auth/androidbuild.internal",
    ]

    # 1. On Linux, attempt --sso flow first; on macOS/Windows or fallback, run standard fetch
    oauth2l_cmd_variants = []
    if sys.platform == "linux" and os.path.exists("/google/data"):
        oauth2l_cmd_variants.append(["oauth2l", "fetch", "--sso", sso_email] + scopes)
    oauth2l_cmd_variants.append(["oauth2l", "fetch"] + scopes)

    for cmd in oauth2l_cmd_variants:
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
                if not token.startswith("Error") and " " not in token:
                    return token
        except Exception:
            pass

    # 2. Retry with oauth2l reset if stale cache caused failure
    try:
        subprocess.run(["oauth2l", "reset"], capture_output=True, timeout=5.0)
        for cmd in oauth2l_cmd_variants:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
                if not token.startswith("Error") and " " not in token:
                    return token
    except Exception:
        pass

    # 3. Fallback: try oauth2l header
    try:
        res = subprocess.run(
            ["oauth2l", "header", "--sso", sso_email] + scopes,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode == 0 and "Bearer " in res.stdout:
            return res.stdout.replace("Bearer ", "").strip()
    except Exception:
        pass

    return None


def extract_top_fault_frame(
    crash_dump_text: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Extracts top faulting function and source file from a rendered crash dump text.

    Filters out system runtime / libc / signal handler frames.
    Returns (function_name, file_and_line).
    """
    ignore_patterns = [
        r"__GI_raise",
        r"abort",
        r"__kernel_vsyscall",
        r"pthread_cond_wait",
        r"CrashReportDumper",
        r"signal_handler",
        r"libc\.so",
        r"libpthread\.so",
    ]

    lines = crash_dump_text.splitlines()
    in_crashing_thread = False

    for line in lines:
        if "Crashing Thread" in line or "Thread " in line and "crashed" in line.lower():
            in_crashing_thread = True
            continue
        if in_crashing_thread:
            if not line.strip() or line.startswith("Thread "):
                # Next thread section
                if line.startswith("Thread ") and "crashed" not in line.lower():
                    break

            # Check for stack frame pattern like:
            # #0 0x12345 in android::FrameBuffer::post() at FrameBuffer.cpp:123
            # or #0 0x12345 VkDecoder::vkQueueSubmit [vulkan_decoder.cpp + 0x10]
            if re.search(r"#\d+\s+", line):
                should_ignore = any(re.search(pat, line) for pat in ignore_patterns)
                if not should_ignore:
                    # Extract function name
                    func_match = re.search(
                        r"#\d+\s+(?:0x[0-9a-fA-F]+\s+in\s+)?([^\s(]+)", line
                    )
                    func_name = func_match.group(1) if func_match else None

                    # Extract source file
                    file_match = re.search(
                        r"(?:at|\[)\s*([a-zA-Z0-9_\-/\.]+(?:\:[0-9]+)?)", line
                    )
                    file_name = file_match.group(1) if file_match else None

                    if func_name:
                        return func_name, file_name

    return None, None


# --- Subcommand Handlers ---


def run_find_bug(args: argparse.Namespace) -> None:
    """Finds existing Buganizer issues using exact signatures and stack fingerprint analysis."""
    crash_modules = ensure_crashadvisor_imports()
    buganizer_mod = crash_modules["buganizer"]
    context_mod = crash_modules["context"]
    client_mod = crash_modules["client"]
    api_mod = crash_modules["api"]
    metadata_mod = crash_modules["metadata"]
    symbols_mod = crash_modules["symbols"]
    dump_mod = crash_modules["dump"]

    crash_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    component_id = getattr(args, "component_id", buganizer_mod.EMULATOR_COMPONENT_ID)
    json_mode = getattr(args, "json", False)

    # Ingest context & metadata
    ctx = context_mod.CrashReportContext(crash_id)
    ctx.prepare_sandbox()
    gosso = client_mod.GossoClient()
    api = api_mod.CrashApi(gosso, verbose=getattr(args, "verbose", False))

    if not json_mode:
        print(f"📥 Fetching crash metadata for ID: {crash_id}...")

    api.download_metadata(ctx.crash_id, ctx.metadata_path)
    meta = metadata_mod.CrashMetadata(ctx.metadata_path)
    stable_sig = meta.primary_signature or "Unknown"

    # Ensure local symbolication & stack dump exist
    txt_dump = ctx.work_dir / "crashreport.txt"
    if not txt_dump.exists():
        if not json_mode:
            print(
                f"⚙️ Running minidump symbolication & stack extraction for ID: {crash_id}..."
            )
        cmd_args = [crash_id]
        if token:
            cmd_args.extend(["--token", token])
        try:
            run_crashadvisor_bazel(cmd_args)
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Symbolication execution notice: {e}\n")

    # Attempt local stack fingerprinting from crashreport.txt dump
    top_func, top_file = None, None
    if txt_dump.exists():
        try:
            with open(txt_dump, "r", encoding="utf-8") as f:
                top_func, top_file = extract_top_fault_frame(f.read())
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Stack fingerprint reading warning: {e}\n")

    client = buganizer_mod.BuganizerClient(
        token=token,
        component_id=component_id,
        verbose=getattr(args, "verbose", False),
    )

    candidates = []

    # Tier 1: Signature Match
    if stable_sig and stable_sig != "Unknown":
        clean_sig = re.sub(r"[^\w::]", " ", stable_sig).strip()
        try:
            issue_t1 = client.search_issue_by_signature(clean_sig or stable_sig)
            if issue_t1:
                candidates.append(
                    {
                        "confidence": "HIGH (Signature Match)",
                        "issue": issue_t1,
                    }
                )
        except Exception as e:
            if getattr(args, "verbose", False):
                sys.stderr.write(f"⚠️ Buganizer signature search notice: {e}\n")

    # Tier 2: Top Fault Function Match
    if top_func and not any(
        c["issue"].get("issueId") == c.get("issueId") for c in candidates
    ):
        try:
            queries = [f'componentid:{component_id} status:open "{top_func}"']
            for q in queries:
                if client.cli_binary and not client.token:
                    cmd = [client.cli_binary, "search", q, "--format=json"]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode == 0 and res.stdout.strip():
                        issues = json.loads(res.stdout)
                        if issues:
                            candidates.append(
                                {
                                    "confidence": "MEDIUM (Top Stack Frame Function)",
                                    "issue": issues[0],
                                }
                            )
                            break
        except Exception:
            pass

    if json_mode:
        print_result(
            {
                "status": "success",
                "action": "crash find-bug",
                "crash_id": crash_id,
                "stable_signature": stable_sig,
                "top_fault_function": top_func,
                "top_fault_file": top_file,
                "candidates_found": len(candidates),
                "candidates": [
                    {
                        "confidence": c["confidence"],
                        "issue_id": c["issue"].get("issueId"),
                        "title": c["issue"].get("issueState", {}).get("title")
                        or c["issue"].get("title"),
                        "status": c["issue"].get("issueState", {}).get("status")
                        or c["issue"].get("status"),
                        "assignee": c["issue"]
                        .get("issueState", {})
                        .get("assignee", {})
                        .get("emailAddress"),
                        "url": f"https://b.corp.google.com/issues/{c['issue'].get('issueId')}",
                    }
                    for c in candidates
                ],
            },
            json_mode=True,
        )
    else:
        print(f"\n🔍 Crash ID: {crash_id}")
        print(f"📌 Primary Signature: {stable_sig}")
        if top_func:
            print(f"🎯 Fault Frame: {top_func} ({top_file or 'unknown file'})")
        print()

        if not candidates:
            print("🟢 No matching existing issues found in Buganizer Component 29601.")
        else:
            print(f"🐛 Found {len(candidates)} Candidate Buganizer Issue(s):\n")
            for idx, cand in enumerate(candidates, 1):
                iss = cand["issue"]
                iss_id = iss.get("issueId")
                state = iss.get("issueState", {})
                title = state.get("title") or iss.get("title", "Untitled")
                status = state.get("status") or iss.get("status", "UNKNOWN")
                assignee = state.get("assignee", {}).get("emailAddress", "Unassigned")
                print(f"  {idx}. [{cand['confidence']}]")
                print(f"     • Issue: b/{iss_id}")
                print(f"     • Title: {title}")
                print(f"     • Status: {status} (Assignee: {assignee})")
                print(f"     • URL: https://b.corp.google.com/issues/{iss_id}\n")


def resolve_crashadvisor_executable() -> Optional[str]:
    """Resolves absolute path to compiled self-contained CrashAdvisor binary executable."""
    rel_path = os.path.join(
        "bazel-bin",
        "external",
        "goldfish+",
        "emulator",
        "crashreport",
        "tool",
        "advisor",
        "advisor",
    )
    found = find_file_in_source_directories(rel_path)
    if found and os.access(found, os.X_OK):
        return found

    user_home = os.path.expanduser("~")
    installed_bin = os.path.join(
        user_home, ".android", "emu-dev-cli", "lib", "bin", "advisor"
    )
    if os.path.exists(installed_bin) and os.access(installed_bin, os.X_OK):
        return installed_bin

    return None


def run_crashadvisor_bazel(args_list: List[str]) -> subprocess.CompletedProcess:
    """Executes CrashAdvisor via compiled executable binary or falls back to Bazel target."""
    advisor_bin = resolve_crashadvisor_executable()
    if advisor_bin:
        cmd = [advisor_bin] + args_list
        return subprocess.run(cmd, check=True)

    src_dir = get_source_directory("emu-main-next")
    if not src_dir or not os.path.exists(src_dir):
        all_dirs = get_all_source_directories()
        src_dir = all_dirs[0] if all_dirs else os.getcwd()

    cmd = [
        "bazel",
        "run",
        "@goldfish//emulator/crashreport/tool/advisor",
        "--",
    ] + args_list
    return subprocess.run(cmd, cwd=src_dir, check=True)


def run_file_bug(args: argparse.Namespace) -> None:
    """Runs RCA analysis and files or updates a Buganizer issue."""
    crash_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))

    cmd_args = [crash_id, "--enable-buganizer", "--auto-run"]
    if token:
        cmd_args.extend(["--token", token])
    if getattr(args, "qa", False):
        cmd_args.append("--qa")
    if getattr(args, "verbose", False):
        cmd_args.append("--verbose")

    print(
        f"🚀 Filing/updating Buganizer issue for Crash ID {crash_id} via CrashAdvisor..."
    )
    try:
        run_crashadvisor_bazel(cmd_args)
    except Exception as e:
        sys.stderr.write(f"❌ Failed to run CrashAdvisor: {e}\n")
        sys.exit(1)


def run_autofix(args: argparse.Namespace) -> None:
    """Parses RCA actionability and dispatches emu_main_next_engineer for autonomous fix."""
    target_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    dry_run = getattr(args, "dry_run", False)

    print(f"🔧 Initiating autonomous autofix pipeline for Target: {target_id}...")

    # Run CrashAdvisor in auto-run mode to ensure RCA summary exists
    cmd_args = [target_id, "--auto-run"]
    if token:
        cmd_args.extend(["--token", token])

    try:
        run_crashadvisor_bazel(cmd_args)
    except Exception as e:
        sys.stderr.write(f"⚠️ CrashAdvisor execution notice: {e}\n")

    user = os.environ.get("USER", "unknown")
    sandbox_dir = get_crashadvisor_sandbox_dir(target_id)
    rca_file = os.path.join(sandbox_dir, "rca_summary.md")

    if not os.path.exists(rca_file):
        sys.stderr.write(f"❌ ERROR: RCA summary file not found at {rca_file}.\n")
        sys.exit(1)

    with open(rca_file, "r", encoding="utf-8") as f:
        rca_content = f.read()

    if "actionability:" not in rca_content:
        print(
            "🟡 RCA analysis completed, but no structured actionability block was generated. Halting autofix."
        )
        sys.exit(0)

    lines = rca_content.split("actionability:")[1].split("```")[0].strip().split("\n")
    action_data = {}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            action_data[k.strip()] = (
                v.strip().strip('"').strip("'").split("#")[0].strip()
            )

    is_fixable = action_data.get("fixable", "false").lower() == "true"
    target_file = action_data.get("target_file", "unknown")
    target_func = action_data.get("target_function", "unknown")
    remediation = action_data.get("remediation_summary", "Refer to rca_summary.md")

    # Resolve local file path via source_directory registry
    local_src_dir = get_source_directory("emu-main-next")
    local_file_path = None
    if local_src_dir and target_file != "unknown":
        cand = os.path.join(local_src_dir, target_file)
        if os.path.exists(cand):
            local_file_path = cand

    print(f"\n📋 Actionability Analysis Results:")
    print(f"  • Fixable: {'YES ✅' if is_fixable else 'NO ❌'}")
    print(f"  • Target File: {target_file}")
    if local_file_path:
        print(f"  • Local File Path: file://{local_file_path}")
    print(f"  • Target Function: {target_func}")
    print(f"  • Remediation: {remediation}\n")

    if not is_fixable:
        print(
            "🛑 Issue is marked as not fixable autonomously. Halting for developer review."
        )
        sys.exit(0)

    if dry_run:
        print("🔍 Dry-run specified. Skipping agentapi dispatch.")
        sys.exit(0)

    # Dispatch emu_main_next_engineer subagent
    prompt = f"""Implement the remediation plan detailed in {rca_file} for Crash Target {target_id}.
Target File: {target_file} (Local Path: {local_file_path or 'Search workspace'})
Target Function: {target_func}
Remediation Summary: {remediation}

Follow the TDD loop (Red/Green/Refactor) coordinating with test_enforcer.
Upon successful verification, hand off to reviewer.md for audit and committer.md to execute repo upload.
"""
    print("🤖 Dispatching emu_main_next_engineer subagent via agentapi...")
    try:
        res = subprocess.run(
            [
                "agentapi",
                "new-conversation",
                "--model=pro",
                "--agent=.gemini/agents/emu_main_next_engineer.md",
                prompt,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"✅ Autonomous engineer successfully dispatched!\n{res.stdout.strip()}")
    except Exception as e:
        sys.stderr.write(f"❌ Failed to dispatch agentapi: {e}\n")
        sys.exit(1)


def run_analyze(args: argparse.Namespace) -> None:
    """Unified crash analysis command."""
    crash_id = parse_crash_id(args.crash_id)
    file_bug_flag = getattr(args, "file_bug", False)
    autofix_flag = getattr(args, "autofix", False)

    if file_bug_flag:
        run_file_bug(args)
    elif autofix_flag:
        run_autofix(args)
    else:
        # Run find-bug first for quick deduplication feedback, then run interactive/auto RCA
        run_find_bug(args)
        print("\n------------------------------------------------------------")
        print(f"🔬 Launching interactive CrashAdvisor RCA diagnostic session...")
        crash_modules = ensure_crashadvisor_imports()
        advisor_mod = crash_modules["advisor"]

        sandbox_dir = get_crashadvisor_sandbox_dir(crash_id, create=True)
        is_auto_run = getattr(args, "auto_run", False)
        token = acquire_auth_token(getattr(args, "token", None))
        cmd_args = [crash_id, "--work-dir", sandbox_dir]
        if token:
            cmd_args.extend(["--token", token])
        if is_auto_run:
            cmd_args.append("--auto-run")
        if getattr(args, "timeout", None):
            cmd_args.extend(["--timeout", args.timeout])

        run_crashadvisor_bazel(cmd_args)

        # In interactive mode (not auto-run), automatically execute investigation_cmd.sh
        if not is_auto_run:
            script_path = os.path.join(sandbox_dir, "investigation_cmd.sh")
            if os.path.exists(script_path):
                if not (
                    is_path_secure_user_owned(sandbox_dir, is_dir=True)
                    and is_path_secure_user_owned(script_path, is_dir=False)
                ):
                    current_user = (
                        os.getuid() if hasattr(os, "getuid") else "current"
                    )
                    sys.stderr.write(
                        f"⚠️ Security warning: Refusing to execute {script_path} because directory or script permissions/ownership are insecure (must be owned by user {current_user} and not writable by group/others).\n"
                    )
                else:
                    print(
                        f"\n🚀 Launching interactive AI investigation session ({script_path})...\n"
                    )
                    try:
                        os.chmod(script_path, 0o700)
                        subprocess.run(["bash", script_path])
                    except Exception as e:
                        sys.stderr.write(
                            f"⚠️ Failed to launch interactive script: {e}\n"
                        )
            else:
                sys.stderr.write(
                    f"⚠️ Investigation script not found at {script_path}\n"
                )


def _add_analyze_arguments(parser: argparse.ArgumentParser) -> None:
    """Helper to populate argument flags for analyze subcommands."""
    parser.add_argument(
        "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
    )
    parser.add_argument("--token", help="OAuth2 token")
    parser.add_argument(
        "--file-bug",
        action="store_true",
        help="Automatically file/update Buganizer issue",
    )
    parser.add_argument(
        "--autofix", action="store_true", help="Automatically attempt closed-loop fix"
    )
    parser.add_argument(
        "--auto-run", action="store_true", help="Non-interactive RCA mode"
    )
    parser.add_argument("--timeout", help="Execution timeout (e.g. 30m)")
    parser.set_defaults(func=run_analyze)


def register_parser(subparsers) -> None:
    """Registers the `crash` subcommand parser group in emu-dev-cli."""
    crash_parser = subparsers.add_parser(
        "crash",
        help="Crash investigation group: minidump symbolication, stack analysis (analyze), Buganizer deduplication (find-bug, file-bug), and AI autofixing (autofix) tools",
        description="Automated crash investigation, Buganizer deduplication, issue filing, and AI autofixing tools group. Contains subcommands: analyze, find-bug, file-bug, autofix.",
    )
    crash_parser.set_defaults(
        func=lambda args: crash_parser.print_help() or sys.exit(0)
    )

    crash_subparsers = crash_parser.add_subparsers(
        dest="crash_cmd", help="Available crash subcommands"
    )

    # 1. find-bug
    find_bug_parser = crash_subparsers.add_parser(
        "find-bug",
        help="Search Buganizer for existing/duplicate bugs matching crash signature and stack fingerprint",
        description="Searches Buganizer issue tracker for existing or duplicate bugs matching the crash signature, normalized stack fingerprint, and faulting function extracted from CrashAdvisor minidumps.",
    )
    find_bug_parser.add_argument(
        "crash_id",
        help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL (Note: expects a Crash ID, NOT a Buganizer bug number)",
    )
    find_bug_parser.add_argument(
        "--token", help="OAuth2 token for Buganizer & Crash API"
    )
    find_bug_parser.add_argument(
        "--component-id",
        type=int,
        default=29601,
        help="Buganizer Component ID (default: 29601)",
    )
    find_bug_parser.set_defaults(func=run_find_bug)

    # 2. file-bug
    file_bug_parser = crash_subparsers.add_parser(
        "file-bug",
        help="Run RCA analysis and create or update a Buganizer issue",
        description="Runs CrashAdvisor RCA analysis for a Crash ID, extracts faulting stack traces and looper execution timelines, and creates a new Buganizer issue or updates an existing one.",
    )
    file_bug_parser.add_argument(
        "crash_id",
        help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL (Note: expects a Crash ID, NOT a Buganizer bug number)",
    )
    file_bug_parser.add_argument(
        "--token", help="OAuth2 token for Buganizer & Crash API"
    )
    file_bug_parser.add_argument(
        "--qa", action="store_true", help="Run against QA Issue Tracker endpoint"
    )
    file_bug_parser.set_defaults(func=run_file_bug)

    # 3. autofix
    autofix_parser = crash_subparsers.add_parser(
        "autofix",
        help="Perform RCA and dispatch emu_main_next_engineer to implement and verify a local fix",
        description="Parses CrashAdvisor RCA actionability data for a Crash ID, maps faulting code locations to local source repository paths, and dispatches the autonomous AI engineer to write unit tests and fix the code.",
    )
    autofix_parser.add_argument(
        "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
    )
    autofix_parser.add_argument("--token", help="OAuth2 token")
    autofix_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform analysis and file mapping without launching agent",
    )
    autofix_parser.set_defaults(func=run_autofix)

    # 4. analyze (subcommand under `crash`)
    analyze_parser = crash_subparsers.add_parser(
        "analyze",
        help="Run minidump symbolication, stack fingerprinting, interactive RCA, or automated Buganizer filing and autofixing",
        description="Unified crash analysis tool. Downloads minidumps, symbolicates stack traces, computes normalized stack fingerprints, and executes interactive or automated Root Cause Analysis (RCA). Can automatically file Buganizer issues (--file-bug) and trigger autonomous AI fixes (--autofix).",
    )
    _add_analyze_arguments(analyze_parser)

