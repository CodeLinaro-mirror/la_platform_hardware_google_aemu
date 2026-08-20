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

import re
from pathlib import Path
from typing import Optional, Tuple, List
import subprocess

from lib.bazel import BazelRunner
from lib.git_transaction import GitTransactionContext
from lib.path_utils import normalize_build_path
from lib.tidy_types import TidyDiagnostic


def _apply_seed_declaration_rename(
    decl_path: Path, diag: TidyDiagnostic, old_symbol: str, new_symbol: str
) -> bool:
    """Applies seed rename strictly leveraging clang-tidy replacement byte offsets.

    This averts risks inherent to line-based regex replacements like substring shadowing
    or corrupting identically named comments preceding the identifier.
    """
    if (
        not decl_path.exists()
        or not old_symbol
        or not new_symbol
        or old_symbol == new_symbol
    ):
        return False

    if not getattr(diag, "replacements", []):
        return False

    with open(decl_path, "rb") as f:
        content = f.read()

    reps = sorted(diag.replacements, key=lambda x: (-x.offset, -x.length))
    applied = 0
    for rep in reps:
        clean_rep = normalize_build_path(rep.file_path)

        if not decl_path.as_posix().endswith(clean_rep):
            continue
        if rep.offset < 0 or rep.offset + rep.length > len(content):
            continue

        prefix = content[: rep.offset]
        suffix = content[rep.offset + rep.length :]
        replacement_bytes = rep.replacement_text.encode("utf-8")
        content = prefix + replacement_bytes + suffix
        applied += 1

    if applied > 0:
        with open(decl_path, "wb") as f:
            f.write(content)
        return True
    return False


def _find_yaml_file(
    runner: BazelRunner, report_target: str, source_dir: str
) -> Optional[Path]:
    clean_target = (
        report_target.lstrip("@")
        .replace("goldfish//", "")
        .replace("android_emulator//", "")
        .lstrip("/")
    )
    if ":" in clean_target:
        pkg, rname = clean_target.split(":", 1)
        bases = [Path.cwd()]
        if source_dir:
            bases.append(Path(source_dir))
        for base in bases:
            for cand in [
                base / f"bazel-bin/external/goldfish+/{pkg}/{rname}.final_fixes.yaml",
                base
                / f"bazel-bin/external/android_emulator+/{pkg}/{rname}.final_fixes.yaml",
                base / f"bazel-bin/{pkg}/{rname}.final_fixes.yaml",
            ]:
                if cand.exists():
                    return cand
    return None


def _extract_rename_symbols(diagnostic: TidyDiagnostic) -> Tuple[str, str]:
    """Extracts old symbol name and proposed new symbol name from diagnostic."""
    msg = diagnostic.message
    old_name = ""
    new_name = ""

    # Pattern 1: invalid case style for <kind> 'foo'
    m_old = re.search(r"invalid case style for [^']*'([^']+)'", msg)
    if m_old:
        old_name = m_old.group(1).strip()
    else:
        # Pattern 2: any single quoted word before 'to' or in message
        quotes = re.findall(r"'([^']+)'", msg)
        if quotes:
            old_name = quotes[0].strip()
            if len(quotes) > 1 and not new_name:
                new_name = quotes[1].strip()

    to_match = re.search(r"\bto\s+'([^']+)'", msg)
    if to_match:
        new_name = to_match.group(1).strip()

    # If new_name not in message, retrieve from clang-tidy replacement
    if not new_name:
        for r in diagnostic.replacements:
            if r.replacement_text:
                new_name = r.replacement_text.strip()
                break

    if not new_name and diagnostic.affected_code:
        new_name = diagnostic.affected_code.strip()

    return old_name, new_name


def _run_clang_format(repo_dir: Path) -> None:
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        files_to_format = []
        for line in res.stdout.splitlines():
            if len(line) > 3:
                file_path = line[3:].strip()
                if file_path.endswith((".cc", ".cpp", ".h", ".hpp", ".c")):
                    files_to_format.append(file_path)

        if files_to_format:
            subprocess.run(
                ["clang-format", "-i"] + files_to_format, cwd=repo_dir, check=True
            )
            print(
                f"  [Style] Cleaned {len(files_to_format)} touched files with clang-format"
            )
    except Exception as e:
        print(f"  [Style] Warning: failed to run clang-format: {e}")


def finalize_transaction(
    tx: GitTransactionContext,
    target: str,
    level: int,
    bug_id: Optional[str],
    test_target: Optional[str],
    upload: bool,
    checks: Optional[str] = None,
) -> str:
    """Squashes all verified transaction step commits into a single atomic changelist."""
    module_name = target.split("//")[-1].split(":")[0].replace("/", "_")
    if checks:
        commit_msg = f"style({module_name}): Fix clang-tidy {checks}\n\n"
        commit_msg += f"Apply Level {level} clang-tidy modernizations for {checks} across {target}:\n"
    else:
        commit_msg = f"style({module_name}): Modernize symbol and variable naming conventions\n\n"
        commit_msg += f"Apply Level {level} clang-tidy identifier naming modernizations across {target}:\n"
    commit_msg += "- Standardize local variables and private members to Google style\n"
    commit_msg += (
        "- Update all referencing call sites and headers across the workspace\n\n"
    )
    if bug_id:
        commit_msg += f"Bug: {bug_id}\n"
    commit_msg += f"Test: bazel test --config=tidy {target}\n"
    if test_target:
        commit_msg += f"Test: bazel test {test_target}\n"

    final_commit = tx.finalize_and_squash(final_commit_msg=commit_msg)
    print(
        f"\n🎉 [Transaction Complete] Squashed all steps into atomic commit {final_commit[:8]}!"
    )

    if upload:
        print("\n🚀 Uploading change to Gerrit...")
        subprocess.run(["repo", "upload", ".", "--cbr", "-y"], check=True)
        print("🚀 Successfully uploaded change to Gerrit!")
    else:
        print("✨ Working tree is clean and ready for review.")
    return final_commit
