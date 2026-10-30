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

"""Multi-commit continuous diff parsing, Code Search URL resolution, and risk heuristics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
import urllib.parse

from commands.gerrit.sidecar_updater import GerritSidecarUpdater
from core.web.assets import StaticAssetResolver
from lib.gerrit_api import (
    DEFAULT_GERRIT_HOST,
    GerritClient,
    decode_gerrit_patch,
    unquote_git_diff_path,
)

LOW_RISK_MAX_LINES = 60
HIGH_RISK_MIN_LINES = 250
DIFF_FETCH_WORKERS = 6
DIFF_BUNDLE_SUBTASK_WORKERS = 4
DEFAULT_MAX_DIFF_LINES_PER_FILE = 500
RAW_QUEUE_CACHE_TTL_SEC = 45.0
DIFF_BUNDLE_CACHE_MAX_ENTRIES = 128

_ORIGINAL_UI_HOOKS: dict[str, Any] = {}


def register_ui_hook(name: str, fn: Any) -> None:
    """Registers the default implementation of a mockable UI server hook."""
    _ORIGINAL_UI_HOOKS[name] = fn


def resolve_ui_hook(name: str, local_val: Any) -> Any:
    """Resolves `name`, honoring `unittest.mock.patch` on either the submodule or `ui_server`."""
    orig = _ORIGINAL_UI_HOOKS.get(name, local_val)
    if local_val is not orig:
        return local_val
    for mod_name in ("commands.gerrit.ui_server", "src.commands.gerrit.ui_server"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            candidate = getattr(mod, name)
            if candidate is not orig:
                return candidate
    return local_val


_HIGH_RISK_PATH_RE = re.compile(
    r"(\.proto$|/include/|mutex|lock|bql|thread|async|memory|dma|bar4|vulkan|webrtc)",
    re.IGNORECASE,
)
_LOW_RISK_SUBJECT_RE = re.compile(
    r"^(style|docs|chore|test)(\([^)]+\))?:|\bKISS\b",
    re.IGNORECASE,
)
_HIGH_RISK_SUBJECT_RE = re.compile(
    r"^(feat)(\([^)]+\))?:|\b(security|uaf|race|deadlock|escape)\b",
    re.IGNORECASE,
)


COMMIT_TYPE_SPECS: dict[str, dict[str, str]] = {
    "bugfix": {"id": "bugfix", "label": "🐛 Bug Fixes", "short": "Bug Fix"},
    "perf": {"id": "perf", "label": "⚡ Performance", "short": "Perf"},
    "ets_test": {"id": "ets_test", "label": "🧪 ETS & Tests", "short": "ETS / Test"},
    "cleanup": {"id": "cleanup", "label": "🧹 Cleanup & Refactor", "short": "Cleanup"},
    "feature": {"id": "feature", "label": "✨ Features & UI", "short": "Feature"},
    "docs_build": {"id": "docs_build", "label": "📦 Build, Deps & Docs", "short": "Build / Docs"},
}


CODE_SEARCH_BASE_HOST = "https://source.corp.google.com/h"
DEFAULT_CODE_SEARCH_GOB_HOST = "googleplex-android"
AEMU_MAP_BASE_URL = "http://go/aemu-map"
GERRIT_MAGIC_FILES: frozenset[str] = frozenset({
    "/COMMIT_MSG",
    "/MERGE_LIST",
    "/PATCHSET_LEVEL",
})


def _extract_gob_host_name(gerrit_host: str) -> str:
    """Extracts the short Git-on-Borg host name (e.g. `googleplex-android`) from a Gerrit host."""
    raw = (gerrit_host or "").strip().lower()
    if "://" in raw:
        raw = urllib.parse.urlparse(raw).netloc or raw
    subdomain = raw.split(".", 1)[0] if raw else ""
    if subdomain.endswith("-review"):
        subdomain = subdomain[: -len("-review")]
    return subdomain or DEFAULT_CODE_SEARCH_GOB_HOST


def _resolve_googleplex_android_superproject(
    clean_project: str, clean_branch: str
) -> tuple[str, str, str] | None:
    """Maps an Android GoB `(project, branch)` pair to `(superproject_repo, ref, mount_path)`."""
    if clean_project == "platform/hardware/generic/goldfish":
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/generic/goldfish")

    if clean_project == "platform/external/qemu":
        if clean_branch.startswith("emu-main-next"):
            return ("platform/superproject/emu-main-next", "emu-main-next", "third_party/qemu")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "external/qemu")
        return ("platform/superproject/emu-main-dev", "emu-main-dev", "external/qemu")

    if clean_project == "platform/hardware/google/aemu":
        if clean_branch == "emu-main-dev":
            return ("platform/superproject/emu-main-dev", "emu-main-dev", "hardware/google/aemu")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "hardware/google/aemu")
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/google/aemu")

    if clean_project == "platform/hardware/google/gfxstream":
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "hardware/google/gfxstream")
        if clean_branch == "emu-main-dev":
            return ("platform/superproject/emu-main-dev", "emu-main-dev", "hardware/google/gfxstream")
        return ("platform/superproject/emu-main-next", "emu-main-next", "hardware/google/gfxstream")

    if clean_project == "platform/build/bazel":
        if clean_branch == "studio-main":
            return ("platform/superproject/base", "studio-main", "build/bazel")
        if clean_branch == "main":
            return ("platform/superproject/main", "main", "build/bazel")
        return ("platform/superproject/emu-main-next", "emu-main-next", "build/bazel")

    if not clean_project.startswith("platform/"):
        return None

    subpath = clean_project[len("platform/") :]
    branch_superprojects: dict[str, tuple[str, str]] = {
        "emu-main-next": ("platform/superproject/emu-main-next", "emu-main-next"),
        "emu-main-dev": ("platform/superproject/emu-main-dev", "emu-main-dev"),
        "studio-main": ("platform/superproject/base", "studio-main"),
        "main": ("platform/superproject/main", "main"),
    }
    sp_entry = branch_superprojects.get(clean_branch)
    if sp_entry is None:
        return None
    return (sp_entry[0], sp_entry[1], subpath)


def resolve_code_search_base_url(
    project: str, branch: str, gerrit_host: str = DEFAULT_GERRIT_HOST
) -> str:
    """Resolves the Code Search base prefix URL for a Gerrit `(project, branch)` pair."""
    clean_project = (project or "").strip().strip("/")
    if not clean_project:
        return ""
    clean_branch = (branch or "").strip()
    if clean_branch.startswith("refs/heads/"):
        clean_branch = clean_branch[len("refs/heads/") :]
    clean_branch = clean_branch or "main"
    gob_host = _extract_gob_host_name(gerrit_host)

    if gob_host == DEFAULT_CODE_SEARCH_GOB_HOST:
        sp = _resolve_googleplex_android_superproject(clean_project, clean_branch)
        if sp is not None:
            sp_repo, sp_ref, mount_path = sp
            return f"{CODE_SEARCH_BASE_HOST}/{gob_host}/{sp_repo}/+/{sp_ref}:{mount_path}"

    encoded_branch = urllib.parse.quote(clean_branch, safe="/._-")
    return f"{CODE_SEARCH_BASE_HOST}/{gob_host}/{clean_project}/+/{encoded_branch}:"


def build_code_search_file_url(
    project: str,
    branch: str,
    file_path: str,
    line: int | None = None,
    gerrit_host: str = DEFAULT_GERRIT_HOST,
) -> str:
    """Builds a full `source.corp.google.com` Code Search URL for `file_path` (with optional line)."""
    raw_path = (file_path or "").strip()
    if not raw_path or raw_path in GERRIT_MAGIC_FILES:
        return ""
    rel_path = raw_path.lstrip("/")
    if not rel_path or rel_path == "dev/null":
        return ""
    base_url = resolve_code_search_base_url(project, branch, gerrit_host=gerrit_host)
    if not base_url:
        return ""
    encoded_path = "/".join(urllib.parse.quote(seg, safe="") for seg in rel_path.split("/") if seg)
    if not encoded_path:
        return ""
    url = f"{base_url}{encoded_path}" if base_url.endswith(":") else f"{base_url}/{encoded_path}"
    if line is not None and int(line) > 0:
        url = f"{url};l={int(line)}"
    return url


def build_aemu_map_commit_url(
    cl_number: str | int,
    file_paths: list[str] | tuple[str, ...] | set[str] | None = None,
    base_url: str = AEMU_MAP_BASE_URL,
) -> str:
    """Builds a zero-backend `go/aemu-map?ag=<cl>&files=<comma_separated_paths>` URL."""
    raw_cl = str(cl_number or "").strip()
    if raw_cl.lower().startswith("ag/"):
        raw_cl = raw_cl[3:].strip()
    if not raw_cl:
        return base_url
    cleaned_files: list[str] = []
    seen_files: set[str] = set()
    for path in file_paths or ():
        raw_path = (path or "").strip()
        if not raw_path or raw_path in GERRIT_MAGIC_FILES:
            continue
        rel_path = raw_path.lstrip("/")
        if not rel_path or rel_path == "dev/null" or rel_path in GERRIT_MAGIC_FILES:
            continue
        if f"/{rel_path}" in GERRIT_MAGIC_FILES:
            continue
        if rel_path not in seen_files:
            seen_files.add(rel_path)
            cleaned_files.append(rel_path)
    encoded_cl = urllib.parse.quote(raw_cl, safe="")
    if not cleaned_files:
        return f"{base_url}?ag={encoded_cl}"
    encoded_files = ",".join(
        urllib.parse.quote(p, safe="/._-~") for p in cleaned_files
    )
    return f"{base_url}?ag={encoded_cl}&files={encoded_files}"


def format_repo_display_path(project: str) -> str:
    """Formats a Gerrit project name into its concise repo path (e.g. `hardware/generic/goldfish`)."""
    clean = (project or "").strip().strip("/")
    if clean.startswith("platform/"):
        return clean[len("platform/") :]
    return clean


def _candidate_gerrit_ui_template_paths(template_name: str = "gerrit_ui.html") -> list[Path]:
    """Builds ordered candidate paths for `templates/<template_name>`."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in here.parents:
        tpl_cand = parent / "templates" / template_name
        if tpl_cand.is_file() or parent.name == "src":
            candidates.append(tpl_cand)
            break
    if not candidates:
        candidates.append(here.parents[3] / "templates" / template_name)
    home = Path.home()
    candidates.append(home / ".android" / "emu-dev-cli" / "lib" / "templates" / template_name)
    cfg_file = home / ".android" / "emu-dev-cli.json"
    if not cfg_file.is_file():
        return candidates
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return candidates
    src_dirs = cfg.get("source_directories") or {}
    if not isinstance(src_dirs, dict):
        return candidates
    for root_str in src_dirs.values():
        if isinstance(root_str, str) and root_str.strip():
            root = Path(root_str.strip())
            candidates.append(
                root
                / "hardware"
                / "google"
                / "aemu"
                / "tools"
                / "emu-dev-cli"
                / "src"
                / "templates"
                / template_name
            )
            candidates.append(root / "tools" / "emu-dev-cli" / "src" / "templates" / template_name)
    return candidates


def get_gerrit_ui_template_path(variant: str = "classic") -> Path:
    """Resolves the HTML template path for `emu-dev-cli gerrit ui` (`classic` or `polygerrit`)."""
    norm_variant = str(variant or "").strip().lower()
    template_name = (
        "gerrit_ui_polygerrit.html" if norm_variant == "polygerrit" else "gerrit_ui.html"
    )
    resolved = StaticAssetResolver(
        _candidate_gerrit_ui_template_paths(template_name)
    ).resolve_first_existing()
    if norm_variant == "polygerrit" and not resolved.is_file():
        return get_gerrit_ui_template_path("classic")
    return resolved


def get_gerrit_ui_template_version() -> str:
    """Returns a short SHA-256 content hash of the UI templates for automatic UI reload detection."""
    try:
        hasher = hashlib.sha256()
        for variant in ("classic", "polygerrit"):
            tpl_path = get_gerrit_ui_template_path(variant)
            if tpl_path.is_file():
                hasher.update(tpl_path.read_bytes())
        return hasher.hexdigest()[:12]
    except OSError:
        return "0"



def classify_commit_type(change: dict[str, Any], has_bugs: bool = False) -> dict[str, str]:
    """Classifies a Gerrit CL into `bugfix`, `perf`, `ets_test`, `cleanup`, `feature`, or `docs_build`."""
    subject = (change.get("subject") or "").strip()
    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    file_paths = [p for p in (rev_obj.get("files") or {}).keys() if p != "/COMMIT_MSG"]

    if re.match(r"^perf(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["perf"])
    if re.match(r"^(ets|test|tests)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["ets_test"])
    if re.match(r"^(kiss|refactor|cleanup|style|chore)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["cleanup"])
    if re.match(r"^(fix|bugfix|hotfix)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    if re.match(r"^(docs|build|ci|release)(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["docs_build"])
    if re.match(r"^feat(\([^)]*\))?:", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["feature"])

    all_docs_or_build = bool(file_paths) and all(
        p.endswith((
            ".md",
            ".txt",
            "OWNERS",
            "BUILD",
            "BUILD.bazel",
            "MODULE.bazel",
            ".bzl",
            ".mk",
            ".bp",
            ".apk",
            ".prop_template",
            "source.json",
        ))
        for p in file_paths
    )
    all_tests = bool(file_paths) and all(
        p.endswith((
            "_test.cc",
            "_test.cpp",
            "_test.py",
            "_unittest.cpp",
            ".spec.ts",
            ".test.ts",
            ".test.tsx",
            "Test.kt",
            "Test.java",
        ))
        or "/ets/" in p
        or p.startswith("goldfish_test/ets/")
        for p in file_paths
    )

    if re.search(r"\b(doc|document|documentation|readme)\b", subject, re.IGNORECASE) or all_docs_or_build:
        if not re.search(r"\b(fix|enable|desktop_mode|playstore)\b", subject, re.IGNORECASE):
            return dict(COMMIT_TYPE_SPECS["docs_build"])
    if all_tests or re.search(
        r"\b(ets|e2etest|contract test|unit test|integration test|flak(?:e|y|iness))\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["ets_test"])
    if re.search(r"\b(kiss|refactor|clean\s*up|decouple|simplify|dedup)\b", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["cleanup"])
    if re.search(
        r"\b(perf(?:ormance)?|avoid extra|reuse .* buffer|pool .* buffer|zero-copy|latency|throughput|optimize)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["perf"])
    if re.search(r"\b(update gmscore|update image rev|update to \d|build lavapipe)\b", subject, re.IGNORECASE):
        return dict(COMMIT_TYPE_SPECS["docs_build"])
    if re.search(
        r"\b(fix(?:es|ed|ing)?|crash|race|thread-safe|non-blocking|leak|uaf|deadlock|reject|prevent|workaround)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    if re.search(
        r"\b(add|enable|support|redesign|implement|introduce|prototype|install)\b",
        subject,
        re.IGNORECASE,
    ):
        return dict(COMMIT_TYPE_SPECS["feature"])
    if has_bugs:
        return dict(COMMIT_TYPE_SPECS["bugfix"])
    return dict(COMMIT_TYPE_SPECS["feature"])



def classify_risk_and_load(change: dict[str, Any]) -> dict[str, Any]:
    """Classifies a CL into Low, Medium, or High cognitive load / risk."""
    subject = (change.get("subject") or "").strip()
    insertions = int(change.get("insertions", 0) or 0)
    deletions = int(change.get("deletions", 0) or 0)
    total_lines = insertions + deletions

    cur_rev = change.get("current_revision", "")
    rev_obj = (change.get("revisions") or {}).get(cur_rev) or {}
    files_map = rev_obj.get("files") or {}
    file_paths = [p for p in files_map.keys() if p != "/COMMIT_MSG"]

    all_docs = bool(file_paths) and all(p.endswith((".md", ".txt", "OWNERS")) for p in file_paths)
    if all_docs or subject.lower().startswith("docs"):
        return {
            "tier": "low",
            "label": f"LOW LOAD · docs · {total_lines}L",
            "total_lines": total_lines,
            "reasons": ["docs only"],
        }

    all_tests = bool(file_paths) and all(
        p.endswith(("_test.cc", "_test.cpp", "_test.py", ".spec.ts", ".test.ts"))
        for p in file_paths
    )
    if all_tests:
        return {
            "tier": "low",
            "label": f"LOW LOAD · tests · {total_lines}L",
            "total_lines": total_lines,
            "reasons": ["tests only"],
        }

    high_reasons: list[str] = []
    low_reasons: list[str] = []

    if _HIGH_RISK_SUBJECT_RE.search(subject):
        high_reasons.append("feat/security")
    if any(_HIGH_RISK_PATH_RE.search(p) for p in file_paths):
        high_reasons.append("proto/header/concurrency")
    if total_lines >= HIGH_RISK_MIN_LINES:
        high_reasons.append(f"{total_lines}L diff")

    if high_reasons:
        return {
            "tier": "high",
            "label": f"HIGH RISK · {' · '.join(high_reasons[:2])} · {total_lines}L",
            "total_lines": total_lines,
            "reasons": high_reasons,
        }

    if _LOW_RISK_SUBJECT_RE.search(subject):
        low_reasons.append("style/test/KISS")
    if total_lines <= LOW_RISK_MAX_LINES and len(file_paths) <= 2:
        low_reasons.append(f"{total_lines}L")

    if low_reasons and total_lines <= 120:
        return {
            "tier": "low",
            "label": f"LOW LOAD · {total_lines}L",
            "total_lines": total_lines,
            "reasons": low_reasons,
        }

    return {
        "tier": "medium",
        "label": f"MED LOAD · {total_lines}L",
        "total_lines": total_lines,
        "reasons": [f"{total_lines}L"],
    }



def _extract_diff_git_path(diff_line: str) -> str:
    """Extracts the destination relative file path from a `diff --git` header line."""
    if ' "b/' in diff_line:
        quoted = '"' + diff_line.split(' "b/', 1)[1].strip()
        return unquote_git_diff_path(f'"b/{quoted[1:]}')
    parts = diff_line.split(" b/", 1)
    return parts[1].strip() if len(parts) == 2 else diff_line


def extract_commit_message_from_patch(raw_diff: str) -> str:
    """Extracts the full commit message (subject + body + footers) from a git format-patch."""
    if not raw_diff:
        return ""
    lines = raw_diff.splitlines()
    subject_parts: list[str] = []
    in_headers = False
    in_subject = False
    body_start_idx: int | None = None

    for idx, line in enumerate(lines):
        if idx == 0 and (line.startswith("From ") or line.startswith("From:") or line.startswith("Subject:")):
            in_headers = True
        if not in_headers:
            break
        if line == "":
            body_start_idx = idx + 1
            break
        if line.startswith("Subject:"):
            in_subject = True
            val = line[len("Subject:"):].strip()
            val = re.sub(r"^\[PATCH[^\]]*\]\s*", "", val)
            subject_parts.append(val)
        elif in_subject and line.startswith((" ", "\t")):
            subject_parts.append(line.strip())
        else:
            in_subject = False

    if body_start_idx is None:
        return ""

    body_lines: list[str] = []
    for line in lines[body_start_idx:]:
        if line == "---" or line.startswith("diff --git "):
            break
        body_lines.append(line)

    subject_line = " ".join(p for p in subject_parts if p).strip()
    body_text = "\n".join(body_lines).strip()
    if subject_line and body_text:
        return f"{subject_line}\n\n{body_text}"
    return subject_line or body_text



def parse_unified_diff_files(
    raw_diff: str,
    max_lines_per_file: int = DEFAULT_MAX_DIFF_LINES_PER_FILE,
    owner_approvals: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parses a unified git patch into per-file structured diff lines for UI rendering."""
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    old_ln = 0
    new_ln = 0

    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            current_file = {
                "file": _extract_diff_git_path(line),
                "lines": [],
                "additions": 0,
                "deletions": 0,
                "truncated": False,
                "status": "M",
            }
            files.append(current_file)
            continue
        if current_file is None:
            continue
        if line.startswith("+++ "):
            cand = unquote_git_diff_path(line[4:])
            if cand and cand != "/dev/null":
                current_file["file"] = cand
            elif cand == "/dev/null":
                current_file["status"] = "D"
            continue
        if line.startswith("--- "):
            cand = unquote_git_diff_path(line[4:])
            if cand == "/dev/null":
                current_file["status"] = "A"
            continue
        if line.startswith("new file mode"):
            current_file["status"] = "A"
            continue
        if line.startswith("deleted file mode"):
            current_file["status"] = "D"
            continue
        if line.startswith("rename to "):
            current_file["status"] = "R"
            if len(current_file["lines"]) < max_lines_per_file:
                current_file["lines"].append({"type": "hunk", "old": "", "new": "", "text": line})
            continue
        if line.startswith("index "):
            continue
        if line.startswith(("rename from ", "similarity index ", "Binary files ")):
            if len(current_file["lines"]) < max_lines_per_file:
                current_file["lines"].append({"type": "hunk", "old": "", "new": "", "text": line})
            continue
        if line.startswith("@@ "):
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
            if m:
                old_ln = int(m.group(1))
                new_ln = int(m.group(2))
            if len(current_file["lines"]) < max_lines_per_file:
                current_file["lines"].append({"type": "hunk", "old": "", "new": "", "text": line})
            continue

        if len(current_file["lines"]) >= max_lines_per_file:
            current_file["truncated"] = True
            continue

        if line.startswith("+"):
            current_file["additions"] += 1
            current_file["lines"].append({"type": "add", "old": "", "new": str(new_ln), "text": line})
            new_ln += 1
        elif line.startswith("-"):
            current_file["deletions"] += 1
            current_file["lines"].append({"type": "del", "old": str(old_ln), "new": "", "text": line})
            old_ln += 1
        else:
            current_file["lines"].append({"type": "ctx", "old": str(old_ln), "new": str(new_ln), "text": line})
            old_ln += 1
            new_ln += 1

    if owner_approvals is not None:
        for f in files:
            fpath = f.get("file", "")
            if fpath in owner_approvals:
                f["owner_status"] = owner_approvals[fpath]

    return files


_IDENT_START_CLASS = r"[A-Za-z_\u0080-\U0010ffff]"
_IDENT_CONT_CLASS = r"[\w\u0080-\U0010ffff]"
_HUNK_CONTEXT_RE = re.compile(r"^@@[^@]+@@\s*(.+?)\s*$")
_PY_DEF_OR_CLASS_RE = re.compile(
    rf"^\s*(?:async\s+def|def|class)\s+({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)"
)
_GO_FUNC_RE = re.compile(
    rf"^\s*func\s+(?:\([^)]*\)\s*)?({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)\s*[\[(]"
)
_RUST_FN_RE = re.compile(
    rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+(?:\"[^\"]*\"\s+)?)*"
    rf"fn\s+({_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*)"
)
_JS_FUNC_RE = re.compile(
    rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
    rf"([A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*[(<]"
)
_JS_ARROW_RE = re.compile(
    rf"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*="
    rf"\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$\u0080-\U0010ffff][\w$\u0080-\U0010ffff]*)\s*=>"
)
_C_STYLE_FUNC_RE = re.compile(
    rf"(?:^|[\s*&>:])(~?{_IDENT_START_CLASS}{_IDENT_CONT_CLASS}*"
    rf"(?:::[~A-Za-z_\u0080-\U0010ffff]{_IDENT_CONT_CLASS}*)*)\s*\("
)
_NON_FUNCTION_KEYWORDS: frozenset[str] = frozenset({
    "if",
    "else",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "decltype",
    "static_assert",
    "typeof",
    "new",
    "delete",
    "throw",
    "case",
    "LOCK",
    "GUARDED_BY",
})


def extract_symbol_from_code_line(raw_line: str) -> str:
    """Extracts a function, method, or class identifier from a declaration or diff hunk line."""
    line = (raw_line or "").strip()
    if not line or line.startswith(("//", "#", "/*", "*")):
        return ""
    for pattern in (_PY_DEF_OR_CLASS_RE, _GO_FUNC_RE, _RUST_FN_RE, _JS_FUNC_RE, _JS_ARROW_RE):
        m = pattern.match(line)
        if m:
            return m.group(1)
    if "=" in line.split("(", 1)[0] or line.endswith(";"):
        return ""
    m_c = _C_STYLE_FUNC_RE.search(line)
    if not m_c:
        return ""
    cand = m_c.group(1).lstrip(":")
    short_tail = cand.rsplit("::", 1)[-1]
    if cand in _NON_FUNCTION_KEYWORDS or short_tail in _NON_FUNCTION_KEYWORDS:
        return ""
    return cand


def extract_enclosing_function_from_diff_lines(
    diff_lines: list[dict[str, Any]],
    target_line: int,
    side: str = "REVISION",
) -> str:
    """Finds the nearest enclosing function/method/class name at or above `target_line` in `diff_lines`."""
    if not diff_lines:
        return ""
    ln_key = "old" if str(side).upper() == "PARENT" else "new"
    alt_key = f"{ln_key}_ln"
    hit_idx = -1
    for idx, entry in enumerate(diff_lines):
        raw_val = entry.get(ln_key)
        if raw_val in (None, ""):
            raw_val = entry.get(alt_key)
        raw_num = str(raw_val if raw_val is not None else "").strip()
        if raw_num.isdigit() and int(raw_num) == int(target_line):
            hit_idx = idx
            break
    if hit_idx < 0:
        return ""

    for idx in range(hit_idx, -1, -1):
        entry = diff_lines[idx]
        ltype = entry.get("type")
        text = str(entry.get("text") or "")
        if ltype == "hunk":
            m_hunk = _HUNK_CONTEXT_RE.match(text)
            return extract_symbol_from_code_line(m_hunk.group(1)) if m_hunk else ""
        if ltype == "del" and ln_key == "new":
            continue
        if ltype == "add" and ln_key == "old":
            continue
        code_body = text[1:] if text.startswith(("+", "-", " ")) else text
        if code_body != code_body.lstrip() and len(code_body) - len(code_body.lstrip()) > 8:
            continue
        sym = extract_symbol_from_code_line(code_body)
        if sym:
            return sym
    return ""




class GerritServiceBase:
    """Shared state and cache primitives across Gerrit UI domain services."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
        sidecar_updater: GerritSidecarUpdater | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.client = GerritClient(host=self.host)
        self.sidecar_updater = sidecar_updater or GerritSidecarUpdater()
        self._session_lock = threading.Lock()
        self._session_approved_cls: set[int] = set()
        self._session_votes: dict[int, dict[str, int]] = {}
        self._session_presubmit_voted_at: dict[int, float] = {}
        self._presubmit_targets_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._raw_queue_cache: dict[str, tuple[float, list[dict[str, Any]], bool]] = {}
        self._known_revisions_by_cl: dict[str, tuple[str, int]] = {}
        self._diff_revision_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._full_diff_bundle_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._file_content_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._last_prewarm_by_viewer: dict[str, float] = {}
        self._prewarm_in_progress = False
        self._qa_lock = threading.Lock()
        self._qa_threads: dict[str, dict[str, Any]] = {}
        self._tracked_qa_conv_ids: set[str] = set()
        self._deleted_qa_conv_ids: set[str] = set()
        self._analyzer_conv_ids: set[str] = set()
        self._ignored_steps_by_conv: dict[str, set[int]] = {}
        self._cached_project_id: str = ""
        self._next_qa_seq: int = 1
        self._ai_analysis_lock = threading.Lock()
        self._ai_analysis_state: dict[tuple[str, int], dict[str, Any]] = {}
        self._ai_analysis_gen: dict[tuple[str, int], int] = {}
        self._ai_analysis_cache_dir = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "ai_analysis"
        )
        self._bug_cache_path = (
            Path.home() / ".cache" / "emu-dev-cli" / "gerrit-ui" / "bug_metadata_cache.json"
        )
        self._bug_cache: dict[str, dict[str, str]] = (
            self._load_bug_cache() if hasattr(self, "_load_bug_cache") else {}
        )
        self.auth_email = (
            self._detect_auth_email() if hasattr(self, "_detect_auth_email") else ""
        )
        if hasattr(self, "_detect_viewer_email"):
            from commands.gerrit.services.queue_service import _normalize_viewer_email

            self.default_viewer = (
                _normalize_viewer_email(default_viewer) or self._detect_viewer_email()
            )
        else:
            self.default_viewer = default_viewer.strip().lower()
        self.default_query = default_query

    def _detect_auth_email(self) -> str:
        """Returns the Gerrit account email that `gob-curl` authenticates as on this host."""
        try:
            acct = self.client._request_json("/a/accounts/self")
            return (acct.get("email") or "").strip().lower()
        except Exception:
            return ""

    def _invalidate_cl_caches(self, cl_ids: list[str] | None = None) -> None:
        """Clears cached raw queue queries and optional full diff bundle entries after a mutation."""
        target_ids = {str(cid).strip() for cid in (cl_ids or []) if str(cid).strip()}
        with self._session_lock:
            self._raw_queue_cache.clear()
            if not target_ids:
                return
            stale_keys = [k for k in self._full_diff_bundle_cache if k[0] in target_ids]
            for key in stale_keys:
                self._full_diff_bundle_cache.pop(key, None)

    def _record_known_revisions(self, raw_changes: list[dict[str, Any]]) -> None:
        """Records `(current_revision, patch_set)` per CL number to accelerate diff bundle fetches."""
        updates: dict[str, tuple[str, int]] = {}
        for ch in raw_changes:
            num = ch.get("_number")
            if not num:
                continue
            cur_rev = str(ch.get("current_revision") or "")
            rev_obj = ((ch.get("revisions") or {}).get(cur_rev) or {}) if cur_rev else {}
            cur_ps = int(ch.get("current_revision_number", 0) or rev_obj.get("_number", 1) or 1)
            updates[str(num)] = (cur_rev, max(1, cur_ps))
        if updates:
            with self._session_lock:
                self._known_revisions_by_cl.update(updates)

    def _get_known_revision(self, cl_id: str) -> tuple[str, int]:
        """Returns `(current_revision, patch_set)` if previously seen in a queue response."""
        with self._session_lock:
            return self._known_revisions_by_cl.get(cl_id, ("", 0))



class GerritDiffService(GerritServiceBase):
    """Domain service for continuous multi-commit diff parsing, pure-rebase checks, and caching."""

    def get_code_questions(
        self,
        cl_numbers: list[str | int],
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Default no-op overridden when composed with `GerritInlineQaService`."""
        return []

    def _enrich_questions_with_diff_symbols(
        self,
        questions: list[dict[str, Any]],
        diff_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Populates `function_name` on inline Q&A items from parsed `diff_files` when missing."""
        if not questions or not diff_files:
            return questions
        lines_by_file = {
            str(f.get("file") or ""): (f.get("lines") or [])
            for f in diff_files
            if isinstance(f, dict)
        }
        for q in questions:
            if q.get("function_name"):
                continue
            fpath = str(q.get("file_path") or "")
            file_lines = lines_by_file.get(fpath) or []
            if file_lines:
                sym = extract_enclosing_function_from_diff_lines(
                    file_lines,
                    int(q.get("start_line") or q.get("line") or 1),
                    str(q.get("side") or "REVISION"),
                )
                if sym:
                    q["function_name"] = sym
        return questions

    def _resolve_is_pure_rebase(self, cl_id: str, known_ps: int) -> bool:
        """Checks whether the current patchset is a pure rebase, skipping `get_change_detail` when known."""
        if known_ps == 1:
            return False
        try:
            cur_ps = known_ps
            if cur_ps <= 0:
                detail = self.client.get_change_detail(cl_id, include_files=False)
                cur_ps = int(detail.get("current_revision_number", 1) or 1)
            if cur_ps <= 1:
                return False
            interdiff = self.client._request_json(
                f"/a/changes/{cl_id}/revisions/current/files?base={cur_ps - 1}"
            )
            if not isinstance(interdiff, dict):
                return False
            non_msg = [k for k in interdiff.keys() if k != "/COMMIT_MSG"]
            return len(non_msg) == 0
        except Exception:
            return False

    def _load_or_fetch_revision_diff_data(
        self,
        cl_id: str,
        known_rev: str,
        known_ps: int,
        base: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetches or reuses decoded patch + pure-rebase metadata concurrently with threads and Q&A."""
        base_clean = str(base).strip() if base is not None else ""
        if base_clean.lower() in ("", "0", "base", "parent"):
            base_clean = "base"
        cache_rev_key = (cl_id, known_rev, base_clean) if known_rev else None
        cached_rev_data: dict[str, Any] | None = None
        if cache_rev_key:
            with self._session_lock:
                cached_rev_data = self._diff_revision_cache.get(cache_rev_key)

        with ThreadPoolExecutor(max_workers=DIFF_BUNDLE_SUBTASK_WORKERS) as pool:
            threads_fut = pool.submit(
                lambda: [t.to_dict() for t in self.client.get_comment_threads(cl_id, include_resolved=False)]
            )
            qa_fut = pool.submit(self.get_code_questions, [cl_id])
            if cached_rev_data is not None:
                return (
                    cached_rev_data,
                    threads_fut.result(),
                    self._enrich_questions_with_diff_symbols(qa_fut.result(), cached_rev_data["files"]),
                )

            rev_target = known_rev or "current"
            patch_fut = pool.submit(
                self.client.get_revision_patch,
                cl_id,
                revision=rev_target,
                base=base_clean if base_clean != "base" else None,
            )
            rebase_fut = (
                pool.submit(self._resolve_is_pure_rebase, cl_id, known_ps)
                if base_clean == "base"
                else None
            )
            raw_patch = patch_fut.result()
            decoded_patch = decode_gerrit_patch(raw_patch) if raw_patch else ""
            rev_data = {
                "commit_message": extract_commit_message_from_patch(decoded_patch) if decoded_patch else "",
                "is_pure_rebase": rebase_fut.result() if rebase_fut else False,
                "files": parse_unified_diff_files(decoded_patch) if decoded_patch else [],
            }
            if cache_rev_key:
                with self._session_lock:
                    if len(self._diff_revision_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                        self._diff_revision_cache.pop(next(iter(self._diff_revision_cache)), None)
                    self._diff_revision_cache[cache_rev_key] = rev_data
            return (
                rev_data,
                threads_fut.result(),
                self._enrich_questions_with_diff_symbols(qa_fut.result(), rev_data["files"]),
            )

    def _store_diff_bundle_caches(
        self,
        cl_id: str,
        known_rev: str,
        rev_data: dict[str, Any],
        full_bundle: dict[str, Any],
    ) -> None:
        """Stores revision-immutable diff data and TTL-bounded full diff bundle when `known_rev` is known."""
        if not known_rev:
            return
        cache_key = (cl_id, known_rev, "base")
        with self._session_lock:
            if len(self._diff_revision_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._diff_revision_cache.pop(next(iter(self._diff_revision_cache)), None)
            if len(self._full_diff_bundle_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._full_diff_bundle_cache.pop(next(iter(self._full_diff_bundle_cache)), None)
            self._diff_revision_cache[cache_key] = rev_data
            self._full_diff_bundle_cache[cache_key] = (time.monotonic(), full_bundle)

    def fetch_commit_diff_bundle(
        self,
        cl_number: str | int,
        patch_set: str | int | None = None,
        base: str | int | None = None,
    ) -> dict[str, Any]:
        """Fetches decoded unified diff files, pure-rebase status, and comment threads for one CL or patchset comparison."""
        cl_id = str(cl_number).strip()
        known_rev, known_ps = self._get_known_revision(cl_id)
        target_ps_str = str(patch_set).strip() if patch_set is not None else ""
        target_rev = target_ps_str if target_ps_str and target_ps_str != "current" else (known_rev or "current")
        target_ps_num = int(target_ps_str) if target_ps_str.isdigit() else (known_ps or 1)
        base_clean = str(base).strip() if base is not None else ""
        if base_clean.lower() in ("", "0", "base", "parent"):
            base_clean = "base"

        cache_key = (cl_id, str(target_rev), base_clean)
        with self._session_lock:
            cached = self._full_diff_bundle_cache.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < RAW_QUEUE_CACHE_TTL_SEC:
            return dict(cached[1])

        rev_data, threads, code_questions = self._load_or_fetch_revision_diff_data(
            cl_id, target_rev, target_ps_num, base=base_clean if base_clean != "base" else None
        )
        bundle = {
            "number": int(cl_id) if cl_id.isdigit() else cl_id,
            "patch_set": target_ps_num,
            "base_patch_set": base_clean,
            "commit_message": rev_data["commit_message"],
            "is_pure_rebase": rev_data["is_pure_rebase"],
            "files": rev_data["files"],
            "unresolved_threads": threads,
            "code_questions": code_questions,
        }
        with self._session_lock:
            if len(self._full_diff_bundle_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._full_diff_bundle_cache.pop(next(iter(self._full_diff_bundle_cache)), None)
            self._full_diff_bundle_cache[cache_key] = (time.monotonic(), bundle)
        return bundle


    def fetch_multi_commit_diffs(self, cl_numbers: list[str | int]) -> list[dict[str, Any]]:
        """Fetches diff bundles concurrently for a list of CLs in a stack or queue."""
        cleaned = [str(x).strip() for x in cl_numbers if str(x).strip()]
        if not cleaned:
            return []
        with ThreadPoolExecutor(max_workers=min(DIFF_FETCH_WORKERS, len(cleaned))) as pool:
            return list(pool.map(self.fetch_commit_diff_bundle, cleaned))

    def fetch_file_content(
        self,
        cl_number: str | int,
        file_path: str,
        patch_set: str | int | None = None,
    ) -> dict[str, Any]:
        """Fetches raw file content and split lines for a file in a Gerrit change revision."""
        cl_id = str(cl_number).strip()
        path_clean = str(file_path).strip().lstrip("/")
        known_rev, known_ps = self._get_known_revision(cl_id)
        target_ps_str = str(patch_set).strip() if patch_set is not None else ""
        target_rev = target_ps_str if target_ps_str and target_ps_str != "current" else (known_rev or "current")
        target_ps_num = int(target_ps_str) if target_ps_str.isdigit() else (known_ps or 1)

        cache_key = (cl_id, str(target_rev), path_clean)
        with self._session_lock:
            cached = self._file_content_cache.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < RAW_QUEUE_CACHE_TTL_SEC:
            return dict(cached[1])

        try:
            content = self.client.get_revision_file_content(cl_id, path_clean, revision=target_rev)
        except Exception:
            content = ""

        res = {
            "status": "ok",
            "cl": int(cl_id) if cl_id.isdigit() else cl_id,
            "patch_set": target_ps_num,
            "file": path_clean,
            "content": content,
            "lines": content.splitlines(),
        }
        with self._session_lock:
            if len(self._file_content_cache) >= DIFF_BUNDLE_CACHE_MAX_ENTRIES:
                self._file_content_cache.pop(next(iter(self._file_content_cache)), None)
            self._file_content_cache[cache_key] = (time.monotonic(), res)
        return res

