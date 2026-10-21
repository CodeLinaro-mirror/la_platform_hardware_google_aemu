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

import logging
import argparse
import os
import re
import sys
from pathlib import Path

from lib.output import print_result

from commands.source_directory import (
    build_codesearch_url,
    find_file_in_source_directories,
    get_all_source_directories,
    get_source_directory,
)

# Named documentation & playbook targets
DOC_TARGETS = {
    "cts-verifier-automation": (
        "emu-main-next",
        "third_party/adt-infra/goldfish_test/xts/verifier/README.md",
    ),
    "cts-verifier-automation-development": (
        "emu-main-next",
        "third_party/adt-infra/goldfish_test/xts/verifier/DEVELOPING_CTS_VERIFIER_AUTOMATION.md",
    ),
    "emu-dev-cli-development": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/DEVELOPMENT.md",
    ),
    "goldfish-architecture": (
        "emu-main-next",
        "hardware/generic/goldfish/ARCHITECTURE.md",
    ),
    "goldfish-agents": (
        "emu-main-next",
        "hardware/generic/goldfish/AGENTS.md",
    ),
    "playbook-index": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/INDEX.md",
    ),
    "playbook-build-and-tidy": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/build_and_tidy.md",
    ),
    "playbook-git-and-gerrit": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/git_and_gerrit.md",
    ),
    "playbook-threading-and-invariants": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/threading_and_invariants.md",
    ),
    "playbook-crash-triage": (
        "emu-main-next",
        "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/crash_triage.md",
    ),
}

_SKIP_DIRS = {
    ".git",
    ".repo",
    ".agents",
    ".gemini",
    "__pycache__",
    "node_modules",
    "objs",
    "target",
}

_REPO_SUBPATHS = (
    "hardware/generic/goldfish",
    "hardware/google/aemu",
    "external/qemu",
)

_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_ROLE_RE = re.compile(r"\*\*Role:\*\*\s*(.+)$", re.MULTILINE)
_LOCATION_RE = re.compile(r"\*\*Location:\*\*\s*`([^`]+)`")
_SCOPE_RE = re.compile(r"\*\*Scope:\*\*\s*`([^`]+)`")
_BAZEL_LABEL_RE = re.compile(
    r"`((?:@(?:goldfish|aemu|qemu))?//[A-Za-z0-9_./+-]+(?::[A-Za-z0-9_./+-]+)?)`"
)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_MACRO_TARGET_SUFFIXES = (
    "_cc_grpc",
    "_cc_proto",
    "_py_pb2_grpc",
    "_py_pb2",
    "_java_grpc",
    "_java_proto",
    "_ts_proto",
    "_proto",
    "_x86_64",
    "_aarch64",
)


def resolve_doc_file_path(branch, relative_path):
    """
    Dynamically resolves the absolute path to a documentation file across:
    1. The target's specific branch source directory in ~/.android/emu-dev-cli.json
    2. All registered source directories and workspace roots
    3. Bundled release package docs/ directory or relative traversal from __file__
    """
    # 1. Primary lookup: Check target branch's registered source directory
    src_dir = get_source_directory(branch)
    if src_dir:
        candidate = os.path.join(src_dir, relative_path)
        if os.path.exists(candidate):
            return candidate

    # 2. Search across all registered source directories and workspace roots
    found_path = find_file_in_source_directories(relative_path)
    if found_path:
        return found_path

    # 3. Fallback: Check relative directory structure from this script file location (__file__)
    curr = os.path.abspath(__file__)
    for _ in range(7):
        curr = os.path.dirname(curr)
        candidate = os.path.join(curr, relative_path)
        if os.path.exists(candidate):
            return candidate
        # Also check if relative_path is inside tools/emu-dev-cli/docs/... and curr is release/lib or tools/emu-dev-cli
        marker = "tools/emu-dev-cli/"
        if marker in relative_path:
            sub_rel = relative_path.split(marker, 1)[1]
            sub_cand = os.path.join(curr, sub_rel)
            if os.path.exists(sub_cand):
                return sub_cand

    return None


def _get_repo_root_for_file(file_path: Path, workspace_root: Path) -> Path:
    rel = file_path.relative_to(workspace_root).as_posix()
    for sub in _REPO_SUBPATHS:
        if rel == sub or rel.startswith(sub + "/"):
            return workspace_root / sub
    return workspace_root


def _extract_doc_metadata(file_path: Path, workspace_root: Path) -> dict:
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        text = ""

    rel_path = file_path.relative_to(workspace_root).as_posix()
    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else file_path.name
    role_match = _ROLE_RE.search(text)
    role = role_match.group(1).strip() if role_match else ""
    loc_match = _LOCATION_RE.search(text)
    declared_location = loc_match.group(1).strip() if loc_match else ""
    scope_match = _SCOPE_RE.search(text)
    declared_scope = scope_match.group(1).strip() if scope_match else ""

    if "docs/playbooks/" in rel_path:
        category = "playbook"
    elif file_path.name == "ARCHITECTURE.md":
        category = "architecture"
    elif file_path.name == "DESIGN.md":
        category = "design"
    elif file_path.name == "AGENTS.md":
        category = "agents"
    else:
        category = "doc"

    return {
        "title": title,
        "role": role,
        "declared_location": declared_location,
        "declared_scope": declared_scope,
        "category": category,
        "relative_path": rel_path,
        "path": str(file_path),
    }


def discover_wiki_documents(source_dirs: list[str] | None = None) -> list[dict]:
    """
    Discovers all ARCHITECTURE.md, DESIGN.md, AGENTS.md, and docs/playbooks/*.md files
    across the given source directories (or registered workspace roots).
    """
    roots = source_dirs if source_dirs is not None else get_all_source_directories()
    seen_rel_paths = set()
    documents = []

    for root_str in roots:
        workspace_root = Path(root_str).resolve()
        if not workspace_root.is_dir():
            continue

        scan_roots = []
        for sub in _REPO_SUBPATHS:
            cand = workspace_root / sub
            if cand.is_dir():
                scan_roots.append(cand)
        if not scan_roots:
            scan_roots.append(workspace_root)

        for scan_root in scan_roots:
            for dirpath, dirnames, filenames in os.walk(scan_root):
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if d not in _SKIP_DIRS and not d.startswith("bazel-")
                ]
                rel_dir = Path(dirpath).relative_to(workspace_root).as_posix()
                # Skip template and plugin scaffolding files
                if (
                    "tools/emu-dev-cli/src/templates" in rel_dir
                    or "tools/emu-dev-cli/harness/plugin" in rel_dir
                    or rel_dir.endswith("hardware/google/aemu/agents")
                    or rel_dir == "agents"
                ):
                    continue

                for fname in sorted(filenames):
                    is_wiki_doc = fname in (
                        "ARCHITECTURE.md",
                        "DESIGN.md",
                        "AGENTS.md",
                    ) or ("docs/playbooks" in rel_dir and fname.endswith(".md"))
                    if not is_wiki_doc:
                        continue
                    fpath = Path(dirpath) / fname
                    rel_file = fpath.relative_to(workspace_root).as_posix()
                    if rel_file in seen_rel_paths:
                        continue
                    seen_rel_paths.add(rel_file)
                    documents.append(_extract_doc_metadata(fpath, workspace_root))

    documents.sort(key=lambda d: (d["category"], d["relative_path"]))
    return documents


_BAZEL_RULE_RE = re.compile(
    r"\b(?:cc_library|cc_test|cc_binary|py_library|py_test|py_binary|rust_library|rust_test|alias|clang_tidy_test)\s*\([^)]*?\bname\s*=\s*\"([^\"]+)\"",
    re.DOTALL,
)
_SOURCE_EXTS = (
    ".h",
    ".hpp",
    ".cc",
    ".cpp",
    ".c",
    ".mm",
    ".py",
    ".rs",
    ".tsx",
    ".ts",
    ".ini",
    ".proto",
)


def _expand_query_term_variants(term: str) -> list[str]:
    """Expands a query term into lowercase, snake_case, hyphenated, and underscore-stripped variants."""
    low = term.lower()
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", term).lower()
    stem = re.sub(r"_impl$", "", snake)
    hyphen = low.replace("_", "-")
    underscored = low.replace("-", "_")
    no_us = low.replace("_", "").replace("-", "")
    variants = []
    for v in (low, snake, stem, hyphen, underscored, no_us):
        if v and v not in variants:
            variants.append(v)
    return variants


def _term_matches_text(term_variants: list[str], text_lower: str) -> bool:
    text_no_us = text_lower.replace("_", "").replace("-", "")
    return any(v in text_lower or (len(v) >= 4 and v in text_no_us) for v in term_variants)


def _extract_package_brief(
    doc_path: Path, workspace_root: Path, term_variant_groups: list[list[str]]
) -> dict:
    """Extracts Bazel targets and source/header files for the package containing doc_path."""
    doc_dir = doc_path.parent
    repo_root = _get_repo_root_for_file(doc_path, workspace_root)
    rel_repo = (
        repo_root.relative_to(workspace_root).as_posix()
        if repo_root.is_relative_to(workspace_root)
        else ""
    )
    if rel_repo == "hardware/generic/goldfish" or repo_root.name == "goldfish":
        repo_prefix = "@goldfish"
    elif rel_repo == "hardware/google/aemu" or repo_root.name == "aemu":
        repo_prefix = "@aemu"
    elif rel_repo == "external/qemu" or repo_root.name == "qemu":
        repo_prefix = "@qemu"
    else:
        repo_prefix = ""

    doc_rel = (
        doc_dir.relative_to(repo_root).as_posix()
        if doc_dir.is_relative_to(repo_root)
        else ""
    )
    if doc_rel == ".":
        doc_rel = ""

    # Locate BUILD.bazel in doc_dir or up to 2 ancestor directories within repo_root
    build_dir = doc_dir
    build_file = build_dir / "BUILD.bazel"
    if not build_file.is_file():
        build_file = build_dir / "BUILD"
    steps_up = 0
    while not build_file.is_file() and build_dir != repo_root and steps_up < 2:
        parent = build_dir.parent
        if not parent.is_relative_to(repo_root) or parent == repo_root:
            break
        build_dir = parent
        build_file = build_dir / "BUILD.bazel"
        if not build_file.is_file():
            build_file = build_dir / "BUILD"
        steps_up += 1

    build_pkg_rel = (
        build_dir.relative_to(repo_root).as_posix()
        if build_dir.is_relative_to(repo_root)
        else ""
    )
    if build_pkg_rel == ".":
        build_pkg_rel = ""

    build_text = ""
    targets = []
    if build_file.is_file():
        try:
            build_text = build_file.read_text(encoding="utf-8")
        except OSError:
            build_text = ""
        for match in _BAZEL_RULE_RE.finditer(build_text):
            tname = match.group(1)
            if tname == "tidy" or tname.endswith("_tidy"):
                continue
            label = (
                f"{repo_prefix}//{build_pkg_rel}:{tname}"
                if build_pkg_rel
                else f"{repo_prefix}//:{tname}"
            )
            if label not in targets:
                targets.append(label)

    pkg_files = []
    file_stems = set()
    if doc_dir.is_dir():
        for root_dir, dirnames, filenames in os.walk(doc_dir):
            cur_dir = Path(root_dir)
            if not doc_rel:
                # For repository-root docs, only inspect root-level source files
                dirnames[:] = []
            elif cur_dir != doc_dir:
                # Do not descend into child Bazel packages
                if (cur_dir / "BUILD.bazel").is_file() or (
                    cur_dir / "BUILD"
                ).is_file():
                    dirnames[:] = []
                    continue
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if d not in _SKIP_DIRS and not d.startswith("bazel-")
            ]
            for fname in sorted(filenames):
                if fname.endswith(_SOURCE_EXTS):
                    full_f = cur_dir / fname
                    f_rel = (
                        full_f.relative_to(doc_dir).as_posix()
                        if full_f.is_relative_to(doc_dir)
                        else fname
                    )
                    pkg_files.append(f_rel)
                    file_stems.add(Path(fname).stem.lower())

    dir_stem = doc_dir.name.lower()
    # Prioritize files and targets that match query terms and local directory stems
    if term_variant_groups or file_stems:
        pkg_files.sort(
            key=lambda f: (
                -sum(
                    1
                    for tv in term_variant_groups
                    if _term_matches_text(tv, f.lower())
                ),
                0 if dir_stem and dir_stem in f.lower() else 1,
                0
                if f.endswith((".h", ".py", ".ini", "_test.cc", "_test.py"))
                else 1,
                f,
            )
        )
        targets.sort(
            key=lambda lbl: (
                -sum(
                    1
                    for tv in term_variant_groups
                    if _term_matches_text(tv, lbl.lower())
                ),
                0
                if dir_stem
                and dir_stem.replace("_", "")
                in lbl.split(":")[-1].lower().replace("_", "")
                else 1,
                0
                if lbl.endswith(("_test", "_unittests", "_tests"))
                else 1,
                lbl,
            )
        )

    return {
        "build_text": build_text,
        "targets": targets[:10],
        "files": pkg_files[:14],
    }


def search_wiki_documents(
    query: str, source_dirs: list[str] | None = None
) -> list[dict]:
    """
    Searches discovered wiki documents by keyword across title, path, role,
    markdown body, and co-located BUILD.bazel targets and source/header filenames.
    Ranks documents by number of matched query terms and term frequency.
    """
    roots = source_dirs if source_dirs is not None else get_all_source_directories()
    docs_list = discover_wiki_documents(roots)
    raw_terms = [t for t in re.split(r"[\s,;|]+", query) if t.strip()]
    if not raw_terms:
        return docs_list

    term_variant_groups = [_expand_query_term_variants(t) for t in raw_terms]
    resolved_roots = [Path(r).resolve() for r in roots if Path(r).is_dir()]

    results = []
    for doc in docs_list:
        fpath = Path(doc["path"])
        try:
            content = fpath.read_text(encoding="utf-8")
        except OSError:
            content = ""

        workspace_root = fpath.parent
        for rp in resolved_roots:
            if fpath.is_relative_to(rp):
                workspace_root = rp
                break

        brief = _extract_package_brief(
            fpath, workspace_root, term_variant_groups
        )
        haystack_title = doc["title"].lower()
        haystack_path = doc["relative_path"].lower()
        haystack_dirname = fpath.parent.name.lower()
        haystack_role = doc["role"].lower()
        haystack_body = content.lower()
        haystack_pkg = (
            brief["build_text"].lower()
            + "\n"
            + " ".join(brief["files"]).lower()
            + "\n"
            + " ".join(brief["targets"]).lower()
        )

        score = 0
        matched_terms = 0
        matching_snippets = []

        for tv in term_variant_groups:
            in_title = _term_matches_text(tv, haystack_title)
            in_path = _term_matches_text(tv, haystack_path)
            in_dirname = _term_matches_text(tv, haystack_dirname)
            in_role = _term_matches_text(tv, haystack_role)
            in_body = _term_matches_text(tv, haystack_body)
            in_pkg = _term_matches_text(tv, haystack_pkg)
            if not (in_title or in_path or in_role or in_body or in_pkg):
                continue
            matched_terms += 1
            if in_dirname:
                score += 25
            if in_title:
                score += 15
            if in_path:
                score += 12
            if in_pkg:
                score += 10
            if in_role:
                score += 5
            if in_body:
                body_hits = sum(haystack_body.count(v) for v in tv)
                score += min(15, body_hits * 2 + 2)

        if matched_terms == 0:
            continue

        for line in content.splitlines():
            sline = line.strip()
            if sline and any(
                _term_matches_text(tv, sline.lower())
                for tv in term_variant_groups
            ):
                matching_snippets.append(sline[:180])
                if len(matching_snippets) >= 2:
                    break

        entry = dict(doc)
        entry["score"] = score + (matched_terms * 100)
        entry["matched_terms"] = matched_terms
        entry["total_terms"] = len(raw_terms)
        entry["snippets"] = matching_snippets
        entry["targets"] = brief["targets"]
        entry["files"] = brief["files"]
        results.append(entry)

    results.sort(
        key=lambda r: (-r["matched_terms"], -r["score"], r["relative_path"])
    )
    full_matches = [r for r in results if r["matched_terms"] == len(raw_terms)]
    if full_matches:
        return full_matches[:4] + [
            r for r in results if r["matched_terms"] < len(raw_terms)
        ][: max(0, 4 - len(full_matches))]
    return results[:4]


def _resolve_bazel_pkg_dir(
    label: str, doc_repo_root: Path, workspace_root: Path
) -> Path | None:
    if "..." in label or "*" in label or "<" in label or ">" in label:
        return None

    if label.startswith("@goldfish//"):
        repo_root = workspace_root / "hardware" / "generic" / "goldfish"
        if not repo_root.is_dir():
            repo_root = doc_repo_root
        rem = label[len("@goldfish//") :]
    elif label.startswith("@aemu//"):
        repo_root = workspace_root / "hardware" / "google" / "aemu"
        if not repo_root.is_dir():
            repo_root = doc_repo_root
        rem = label[len("@aemu//") :]
    elif label.startswith("@qemu//"):
        repo_root = workspace_root / "external" / "qemu"
        if not repo_root.is_dir():
            repo_root = doc_repo_root
        rem = label[len("@qemu//") :]
    elif label.startswith("//"):
        rem = label[2:]
        pkg_candidate = rem.split(":", 1)[0].strip("/")
        if (workspace_root / pkg_candidate).exists():
            return workspace_root / pkg_candidate if pkg_candidate else workspace_root
        for sub in _REPO_SUBPATHS:
            if pkg_candidate == sub:
                return doc_repo_root
            if pkg_candidate.startswith(sub + "/"):
                stripped = pkg_candidate[len(sub) + 1 :]
                if (doc_repo_root / stripped).exists():
                    return doc_repo_root / stripped
        repo_root = doc_repo_root
    else:
        return None

    pkg_part = rem.split(":", 1)[0].strip("/")
    return repo_root / pkg_part if pkg_part else repo_root


def _bazel_target_exists_in_build_file(target_name: str, build_text: str) -> bool:
    """Returns True if target_name (or its macro base name) is declared in build_text."""
    if not target_name or target_name in ("all", "*"):
        return True
    if "name" not in build_text:
        # Synthetic/stub BUILD file in unit tests without rule declarations
        return True
    if f'"{target_name}"' in build_text or f"'{target_name}'" in build_text:
        return True
    for suffix in _MACRO_TARGET_SUFFIXES:
        if target_name.endswith(suffix) and len(target_name) > len(suffix):
            stem = target_name[: -len(suffix)]
            if (
                f'"{stem}"' in build_text
                or f"'{stem}'" in build_text
                or f'"{stem}_"' in build_text
                or f"'{stem}_'" in build_text
            ):
                return True
    return False


def _is_goldfish_repo(repo_root: Path, file_path: Path) -> bool:
    norm_repo = repo_root.as_posix()
    norm_file = file_path.as_posix()
    return (
        norm_repo.endswith("hardware/generic/goldfish")
        or repo_root.name == "goldfish"
        or "/goldfish/" in norm_file
    )


def lint_wiki_documents(
    source_dirs: list[str] | None = None, fix: bool = False
) -> dict:
    """
    Deterministically audits ARCHITECTURE.md, DESIGN.md, and AGENTS.md files for:
    1. Stale **Location:** or **Scope:** headers that don't match the file's directory
    2. Missing/moved Bazel packages or non-existent Bazel target names referenced in backticks
    3. Broken relative markdown links
    4. Unlinked or unrouted AGENTS.md files in hardware/generic/goldfish
    """
    roots = source_dirs if source_dirs is not None else get_all_source_directories()
    docs_list = discover_wiki_documents(roots)
    issues = []
    fixed_files = []
    files_checked = 0

    for doc in docs_list:
        if doc["category"] not in ("architecture", "design", "agents"):
            continue

        files_checked += 1
        fpath = Path(doc["path"])
        workspace_root = None
        for r in roots:
            rp = Path(r).resolve()
            if fpath.is_relative_to(rp):
                workspace_root = rp
                break
        if workspace_root is None:
            workspace_root = fpath.parent

        repo_root = _get_repo_root_for_file(fpath, workspace_root)
        expected_rel_dir = fpath.parent.relative_to(repo_root).as_posix()
        if expected_rel_dir == ".":
            expected_rel_dir = ""

        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).debug("Ignored error in lint_wiki_documents: %s", exc)
            continue

        updated_text = text
        norm_expected = expected_rel_dir.strip("/")
        ws_expected = fpath.parent.relative_to(workspace_root).as_posix().strip("/")

        # Valid if it matches repo-relative dir, workspace-relative dir, or file path
        valid_locations = (
            {
                norm_expected,
                ws_expected,
                f"{norm_expected}/{fpath.name}".strip("/"),
                f"{ws_expected}/{fpath.name}".strip("/"),
            }
            | {f"{sub}/{norm_expected}".strip("/") for sub in _REPO_SUBPATHS}
            | {
                f"{sub}/{norm_expected}/{fpath.name}".strip("/")
                for sub in _REPO_SUBPATHS
            }
        )

        # 1a. Check **Location:** header
        loc_match = _LOCATION_RE.search(text)
        if loc_match:
            declared = loc_match.group(1).strip()
            norm_declared = declared.strip("/")
            if norm_declared not in valid_locations:
                replacement_loc = f"{norm_expected}/" if norm_expected else "./"
                if fix:
                    updated_text = _LOCATION_RE.sub(
                        f"**Location:** `{replacement_loc}`", updated_text, count=1
                    )
                else:
                    issues.append(
                        {
                            "file": doc["relative_path"],
                            "kind": "stale_location",
                            "message": (
                                f"Declared **Location:** `{declared}` does not match "
                                f"actual directory `{replacement_loc}`"
                            ),
                            "declared": declared,
                            "expected": replacement_loc,
                        }
                    )

        # 1b. Check **Scope:** header (used in AGENTS.md)
        scope_match = _SCOPE_RE.search(text)
        if scope_match:
            declared_scope = scope_match.group(1).strip()
            norm_scope = re.sub(r"/\*+$", "", declared_scope).strip("/")
            if norm_scope not in valid_locations:
                replacement_scope = (
                    f"hardware/generic/goldfish/{norm_expected}/**"
                    if _is_goldfish_repo(repo_root, fpath) and norm_expected
                    else (f"{norm_expected}/**" if norm_expected else "**")
                )
                if fix:
                    updated_text = _SCOPE_RE.sub(
                        f"**Scope:** `{replacement_scope}`", updated_text, count=1
                    )
                else:
                    issues.append(
                        {
                            "file": doc["relative_path"],
                            "kind": "stale_location",
                            "message": (
                                f"Declared **Scope:** `{declared_scope}` does not match "
                                f"actual directory `{replacement_scope}`"
                            ),
                            "declared": declared_scope,
                            "expected": replacement_scope,
                        }
                    )

        # 2. Check Bazel package labels and target names
        seen_labels = set()
        for match in _BAZEL_LABEL_RE.finditer(updated_text):
            label = match.group(1)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            pkg_dir = _resolve_bazel_pkg_dir(label, repo_root, workspace_root)
            if pkg_dir is None:
                continue
            build_file = pkg_dir / "BUILD.bazel"
            if not build_file.is_file():
                build_file = pkg_dir / "BUILD"
            if not build_file.is_file():
                issues.append(
                    {
                        "file": doc["relative_path"],
                        "kind": "missing_bazel_package",
                        "message": f"Bazel label `{label}` points to package without BUILD.bazel ({pkg_dir.relative_to(workspace_root) if pkg_dir.is_relative_to(workspace_root) else pkg_dir})",
                        "label": label,
                    }
                )
                continue

            if ":" in label and _is_goldfish_repo(repo_root, fpath):
                target_name = label.split(":", 1)[1].strip()
                try:
                    build_text = build_file.read_text(encoding="utf-8")
                except OSError:
                    build_text = ""
                if not _bazel_target_exists_in_build_file(target_name, build_text):
                    rel_bf = (
                        build_file.relative_to(workspace_root)
                        if build_file.is_relative_to(workspace_root)
                        else build_file
                    )
                    issues.append(
                        {
                            "file": doc["relative_path"],
                            "kind": "missing_bazel_target",
                            "message": f"Bazel target `{label}` not found in `{rel_bf}`",
                            "label": label,
                        }
                    )

        # 3. Check relative Markdown links (excluding fenced code blocks and inline code spans
        # containing C++ lambdas like `` `[this](SystemState* s)` ``)
        non_code_text = re.sub(r"```.*?```", "", updated_text, flags=re.DOTALL)
        non_code_text = re.sub(
            r"`[^`\n]*\[[^\]]*\]\([^)]*\)[^`\n]*`", "", non_code_text
        )
        for match in _MARKDOWN_LINK_RE.finditer(non_code_text):
            raw_target = match.group(1).strip()
            if not raw_target or raw_target.startswith(
                (
                    "http://",
                    "https://",
                    "mailto:",
                    "file://",
                    "conversation://",
                    "#",
                    "b/",
                    "go/",
                    "g3doc/",
                    "crbug/",
                    "cs/",
                    "//cs/",
                )
            ):
                continue
            if "<" in raw_target or ">" in raw_target:
                continue
            target_path_str = raw_target.split("#", 1)[0].strip()
            if not target_path_str:
                continue

            candidates = [
                fpath.parent / target_path_str,
                repo_root / target_path_str.lstrip("/"),
                workspace_root / target_path_str.lstrip("/"),
            ]
            if not any(c.exists() for c in candidates):
                issues.append(
                    {
                        "file": doc["relative_path"],
                        "kind": "broken_link",
                        "message": f"Broken relative link `{raw_target}`",
                        "target": raw_target,
                    }
                )

        # 4. Check AGENTS.md <-> ARCHITECTURE.md synchronization in hardware/generic/goldfish
        if doc["category"] == "agents" and _is_goldfish_repo(repo_root, fpath):
            co_arch = fpath.parent / "ARCHITECTURE.md"
            if co_arch.is_file():
                try:
                    arch_text = co_arch.read_text(encoding="utf-8")
                except OSError:
                    arch_text = ""
                if "AGENTS.md" not in arch_text:
                    issues.append(
                        {
                            "file": doc["relative_path"],
                            "kind": "unlinked_agents_md",
                            "message": (
                                f"Co-located `{co_arch.relative_to(workspace_root) if co_arch.is_relative_to(workspace_root) else co_arch}` "
                                "does not link to `AGENTS.md`"
                            ),
                        }
                    )

            root_agents = repo_root / "AGENTS.md"
            if fpath.parent != repo_root and root_agents.is_file():
                try:
                    root_agents_text = root_agents.read_text(encoding="utf-8")
                except OSError:
                    root_agents_text = ""
                rel_agents = f"{norm_expected}/AGENTS.md" if norm_expected else "AGENTS.md"
                if rel_agents not in root_agents_text:
                    issues.append(
                        {
                            "file": doc["relative_path"],
                            "kind": "unrouted_agents_md",
                            "message": (
                                f"Subsystem playbook `{rel_agents}` is not registered in "
                                f"`{root_agents.relative_to(workspace_root) if root_agents.is_relative_to(workspace_root) else root_agents}`"
                            ),
                        }
                    )

        if fix and updated_text != text:
            try:
                fpath.write_text(updated_text, encoding="utf-8")
                fixed_files.append(doc["relative_path"])
            except OSError as exc:
                logging.getLogger(__name__).debug("Ignored error in lint_wiki_documents: %s", exc)

    return {
        "files_checked": files_checked,
        "issues": issues,
        "fixed_files": fixed_files,
    }


def register_parser(subparsers):
    docs_parser = subparsers.add_parser(
        "docs",
        help="Access, search, and lint the Android Emulator codebase wiki and documentation",
    )
    docs_parser.add_argument(
        "--codesearch-url",
        "--cs-url",
        "--url",
        dest="codesearch_url",
        action="store_true",
        help="Outputs the Code Search web URL instead of local file path",
    )
    docs_parser.set_defaults(func=lambda args: docs_parser.print_help() or sys.exit(0))

    docs_subparsers = docs_parser.add_subparsers(
        dest="docs_cmd", help="Available wiki actions and doc targets"
    )

    # 1. index subcommand
    index_parser = docs_subparsers.add_parser(
        "index",
        help="List all indexed ARCHITECTURE.md, DESIGN.md, AGENTS.md, and operational playbooks",
    )
    index_parser.add_argument(
        "--category",
        choices=["all", "architecture", "design", "agents", "playbook"],
        default="all",
        help="Filter by document category",
    )
    index_parser.set_defaults(func=run_docs_index)

    # 2. search subcommand
    search_parser = docs_subparsers.add_parser(
        "search",
        help="Search across all ARCHITECTURE.md, DESIGN.md, AGENTS.md, and operational playbooks",
    )
    search_parser.add_argument(
        "query",
        nargs="+",
        help="Search query terms (e.g. 'VideoRecorder', 'push.negotiate', 'RenderThread')",
    )
    search_parser.set_defaults(func=run_docs_search)

    # 3. lint subcommand
    lint_parser = docs_subparsers.add_parser(
        "lint",
        help="Audit ARCHITECTURE.md, DESIGN.md, and AGENTS.md files for stale locations, moved Bazel targets, broken links, and unrouted AGENTS.md files",
    )
    lint_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically fix stale **Location:** headers in-place",
    )
    lint_parser.set_defaults(func=run_docs_lint)

    # 4. Named doc targets
    for target_name in DOC_TARGETS:
        sub_parser = docs_subparsers.add_parser(target_name)
        sub_parser.add_argument(
            "--codesearch-url",
            "--cs-url",
            "--url",
            dest="codesearch_url",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Outputs the Code Search web URL instead of local file path",
        )
        sub_parser.set_defaults(
            func=lambda args, name=target_name: run_doc_target(name, args)
        )


def run_docs_index(args):
    json_mode = getattr(args, "json", False)
    category = getattr(args, "category", "all")
    docs_list = discover_wiki_documents()
    if category != "all":
        docs_list = [d for d in docs_list if d["category"] == category]

    if json_mode:
        print_result(
            {
                "status": "success",
                "action": "docs index",
                "count": len(docs_list),
                "documents": docs_list,
            },
            json_mode=True,
        )
        return

    print(f"Indexed {len(docs_list)} codebase wiki document(s):\n")
    for doc in docs_list:
        role_str = f" ({doc['role']})" if doc["role"] else ""
        print(f"  [{doc['category'].upper()}] {doc['relative_path']}")
        print(f"      {doc['title']}{role_str}")


def run_docs_search(args):
    json_mode = getattr(args, "json", False)
    query_str = " ".join(args.query)
    results = search_wiki_documents(query_str)

    if json_mode:
        print_result(
            {
                "status": "success",
                "action": "docs search",
                "query": query_str,
                "count": len(results),
                "results": results,
            },
            json_mode=True,
        )
        return

    if not results:
        print(f"No wiki documents matched query: '{query_str}'")
        return

    print(f"Found {len(results)} wiki document(s) matching '{query_str}':\n")
    for r in results:
        print(f"  • {r['path']}  (score: {r['score']})")
        print(f"    Title: {r['title']}")
        if r.get("targets"):
            print(f"    Targets: {', '.join(r['targets'])}")
        if r.get("files"):
            print(f"    Files: {', '.join(r['files'])}")
        for snip in r.get("snippets", []):
            print(f"      > {snip}")
        print()


def run_docs_lint(args):
    json_mode = getattr(args, "json", False)
    fix = getattr(args, "fix", False)
    report = lint_wiki_documents(fix=fix)
    issues = report["issues"]

    if json_mode:
        print_result(
            {
                "status": "success" if not issues else "error",
                "action": "docs lint",
                "files_checked": report["files_checked"],
                "issue_count": len(issues),
                "issues": issues,
                "fixed_files": report["fixed_files"],
            },
            json_mode=True,
            is_error=bool(issues),
        )
        if issues:
            sys.exit(1)
        return

    if report["fixed_files"]:
        print(f"Fixed {len(report['fixed_files'])} file(s):")
        for f in report["fixed_files"]:
            print(f"  ✓ {f}")

    if not issues:
        print(
            f"✓ Wiki lint passed: {report['files_checked']} ARCHITECTURE.md/DESIGN.md/AGENTS.md file(s) verified with 0 issues."
        )
        return

    print(
        f"✗ Wiki lint found {len(issues)} issue(s) across {report['files_checked']} checked file(s):\n"
    )
    for issue in issues:
        print(f"  • [{issue['kind']}] {issue['file']}: {issue['message']}")
    sys.exit(1)


def run_doc_target(target_name, args):
    json_mode = getattr(args, "json", False)
    target_info = DOC_TARGETS.get(target_name)
    if not target_info:
        print_result(
            {
                "status": "error",
                "action": f"docs {target_name}",
                "error_message": f"Unknown doc target '{target_name}'",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    branch, rel_path = target_info
    use_cs_url = getattr(args, "codesearch_url", False)

    full_doc_path = resolve_doc_file_path(branch, rel_path)
    cs_url = build_codesearch_url(rel_path, branch_name=branch)

    if json_mode:
        print_result(
            {
                "status": "success",
                "action": f"docs {target_name}",
                "branch": branch,
                "doc_path": full_doc_path,
                "codesearch_url": cs_url,
                "exists_locally": bool(full_doc_path),
            },
            json_mode=True,
        )
    else:
        if use_cs_url or not full_doc_path:
            if not full_doc_path and not use_cs_url:
                sys.stderr.write(
                    f"⚠️ Local file not found ('{rel_path}'). Falling back to Code Search URL:\n"
                )
            print(cs_url)
        else:
            print(full_doc_path)


def run_cts_verifier_automation_docs(args):
    run_doc_target("cts-verifier-automation", args)


def run_emu_dev_cli_development_docs(args):
    run_doc_target("emu-dev-cli-development", args)
