import argparse
import os
from pathlib import Path
import re
import sys
from commands.source_directory import (
    build_codesearch_url,
    find_file_in_source_directories,
    get_all_source_directories,
    get_source_directory,
)
from lib.output import print_result

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
_BAZEL_LABEL_RE = re.compile(
    r"`((?:@(?:goldfish|aemu|qemu))?//[A-Za-z0-9_./+-]+(?::[A-Za-z0-9_./+-]+)?)`"
)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


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

    if "docs/playbooks/" in rel_path:
        category = "playbook"
    elif file_path.name == "ARCHITECTURE.md":
        category = "architecture"
    elif file_path.name == "DESIGN.md":
        category = "design"
    else:
        category = "doc"

    return {
        "title": title,
        "role": role,
        "declared_location": declared_location,
        "category": category,
        "relative_path": rel_path,
        "path": str(file_path),
    }


def discover_wiki_documents(source_dirs: list[str] | None = None) -> list[dict]:
    """
    Discovers all ARCHITECTURE.md, DESIGN.md, and docs/playbooks/*.md files across
    the given source directories (or registered workspace roots).
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
                # Skip template scaffolding files
                if "tools/emu-dev-cli/src/templates" in rel_dir:
                    continue

                for fname in sorted(filenames):
                    is_wiki_doc = fname in ("ARCHITECTURE.md", "DESIGN.md") or (
                        "docs/playbooks" in rel_dir and fname.endswith(".md")
                    )
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


def search_wiki_documents(
    query: str, source_dirs: list[str] | None = None
) -> list[dict]:
    """
    Searches discovered wiki documents by keyword across title, path, role, and content.
    """
    docs_list = discover_wiki_documents(source_dirs)
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return docs_list

    results = []
    for doc in docs_list:
        fpath = Path(doc["path"])
        try:
            content = fpath.read_text(encoding="utf-8")
        except OSError:
            content = ""

        haystack_title = doc["title"].lower()
        haystack_path = doc["relative_path"].lower()
        haystack_role = doc["role"].lower()
        haystack_body = content.lower()

        score = 0
        matched_all = True
        matching_snippets = []

        for term in terms:
            in_title = term in haystack_title
            in_path = term in haystack_path
            in_role = term in haystack_role
            in_body = term in haystack_body
            if not (in_title or in_path or in_role or in_body):
                matched_all = False
                break
            if in_title:
                score += 10
            if in_path:
                score += 8
            if in_role:
                score += 5
            if in_body:
                score += haystack_body.count(term)

        if not matched_all:
            continue

        for line in content.splitlines():
            sline = line.strip()
            if sline and any(t in sline.lower() for t in terms):
                matching_snippets.append(sline[:140])
                if len(matching_snippets) >= 3:
                    break

        entry = dict(doc)
        entry["score"] = score
        entry["snippets"] = matching_snippets
        results.append(entry)

    results.sort(key=lambda r: (-r["score"], r["relative_path"]))
    return results


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


def lint_wiki_documents(
    source_dirs: list[str] | None = None, fix: bool = False
) -> dict:
    """
    Deterministically audits ARCHITECTURE.md and DESIGN.md files for:
    1. Stale **Location:** headers that don't match the file's directory
    2. Missing/moved Bazel packages referenced in backticks
    3. Broken relative markdown links
    """
    roots = source_dirs if source_dirs is not None else get_all_source_directories()
    docs_list = discover_wiki_documents(roots)
    issues = []
    fixed_files = []
    files_checked = 0

    for doc in docs_list:
        if doc["category"] not in ("architecture", "design"):
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
        except OSError:
            continue

        updated_text = text

        # 1. Check **Location:** header
        loc_match = _LOCATION_RE.search(text)
        if loc_match:
            declared = loc_match.group(1).strip()
            norm_declared = declared.strip("/")
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

        # 2. Check Bazel package labels
        seen_labels = set()
        for match in _BAZEL_LABEL_RE.finditer(updated_text):
            label = match.group(1)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            pkg_dir = _resolve_bazel_pkg_dir(label, repo_root, workspace_root)
            if pkg_dir is None:
                continue
            has_build = (pkg_dir / "BUILD.bazel").is_file() or (
                pkg_dir / "BUILD"
            ).is_file()
            if not has_build:
                issues.append(
                    {
                        "file": doc["relative_path"],
                        "kind": "missing_bazel_package",
                        "message": f"Bazel label `{label}` points to package without BUILD.bazel ({pkg_dir.relative_to(workspace_root) if pkg_dir.is_relative_to(workspace_root) else pkg_dir})",
                        "label": label,
                    }
                )

        # 3. Check relative Markdown links
        for match in _MARKDOWN_LINK_RE.finditer(updated_text):
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

        if fix and updated_text != text:
            try:
                fpath.write_text(updated_text, encoding="utf-8")
                fixed_files.append(doc["relative_path"])
            except OSError:
                pass

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
    docs_parser.set_defaults(
        func=lambda args: docs_parser.print_help() or sys.exit(0)
    )

    docs_subparsers = docs_parser.add_subparsers(
        dest="docs_cmd", help="Available wiki actions and doc targets"
    )

    # 1. index subcommand
    index_parser = docs_subparsers.add_parser(
        "index",
        help="List all indexed ARCHITECTURE.md, DESIGN.md, and operational playbooks",
    )
    index_parser.add_argument(
        "--category",
        choices=["all", "architecture", "design", "playbook"],
        default="all",
        help="Filter by document category",
    )
    index_parser.set_defaults(func=run_docs_index)

    # 2. search subcommand
    search_parser = docs_subparsers.add_parser(
        "search",
        help="Search across all ARCHITECTURE.md, DESIGN.md, and operational playbooks",
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
        help="Audit ARCHITECTURE.md and DESIGN.md files for stale locations, moved Bazel packages, and broken links",
    )
    lint_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically fix stale **Location:** headers in-place",
    )
    lint_parser.set_defaults(func=run_docs_lint)

    # 4. Named doc targets
    for target_name in DOC_TARGETS.keys():
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
            f"✓ Wiki lint passed: {report['files_checked']} ARCHITECTURE.md/DESIGN.md file(s) verified with 0 issues."
        )
        return

    print(
        f"✗ Wiki lint found {len(issues)} issue(s) across {report['files_checked']} checked file(s):\n"
    )
    for issue in issues:
        print(f"  • [{issue['kind']}] {issue['file']}: {issue['message']}")
    sys.exit(1)


def run_doc_target(target_name, args):
    target_info = DOC_TARGETS.get(target_name)
    if not target_info:
        print_result(
            {
                "status": "error",
                "action": f"docs {target_name}",
                "error_message": f"Unknown doc target '{target_name}'",
                "exit_code": 1,
            },
            is_error=True,
        )
        sys.exit(1)

    branch, rel_path = target_info
    json_mode = getattr(args, "json", False)
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
