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

import itertools
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, Optional

from core.git import GitService, ProdGitService
from core.process import ProcessResult

_ATOMIC_WRITE_SEQ = itertools.count()
DEFAULT_GIT_READONLY_TIMEOUT_SEC: float = 15.0


def atomic_write_text(dest_path: Path, content: str) -> None:
    """Atomically writes UTF-8 text via a PID/thread/sequence-scoped sibling temp file."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.parent / (
        f".{dest_path.name}.{os.getpid()}.{threading.get_ident()}.{next(_ATOMIC_WRITE_SEQ)}.tmp"
    )
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def read_json_dict(path: Path) -> Optional[Dict[str, Any]]:
    """Reads `path` as a JSON object, returning None if missing or malformed."""
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def run_git_readonly(
    args: list[str],
    cwd: Path,
    timeout: float = DEFAULT_GIT_READONLY_TIMEOUT_SEC,
    *,
    git_service: Optional[GitService] = None,
) -> ProcessResult:
    """Executes a read-only UTF-8 git subprocess via GitService with a bounded timeout."""
    git = git_service or ProdGitService()
    return git.run_git(args, cwd=str(cwd), timeout_sec=timeout, check=False)


def normalize_build_path(raw_path: str) -> str:
    """Strips Bazel virtual roots (execroot, bazel-out, external) from a file path."""
    clean_path = raw_path.replace("\\", "/")
    
    clean_path = re.sub(r"^(?:.*/)?bazel-out/[^/]+/bin/", "", clean_path)
    clean_path = re.sub(r"^(?:.*/)?execroot/[^/]+/(?:_main/)?", "", clean_path)
    clean_path = re.sub(r"^external/[^/]+/", "", clean_path)
    
    return clean_path
