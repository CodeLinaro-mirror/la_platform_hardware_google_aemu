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

"""Temporary Git repository fixture for workflow and VCS integration tests."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Optional

from core.git import GitService, ProdGitService


class TempGitRepo:
    """Manages an isolated temporary Git repository for integration tests."""

    def __init__(
        self,
        root_dir: Path | str | None = None,
        *,
        git_service: Optional[GitService] = None,
        user_name: str = "Test User",
        user_email: str = "test@google.com",
    ) -> None:
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if root_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory()
            self.path = Path(self._temp_dir.name) / "repo"
        else:
            self.path = Path(root_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git = git_service or ProdGitService()
        self.user_name = user_name
        self.user_email = user_email

    def init(self, initial_commit_msg: str | None = "Initial commit\n\nBug: 1\nTest: N/A") -> "TempGitRepo":
        """Initializes the git repository and optionally creates an initial commit."""
        self.git.run_git(["init"], cwd=self.path, check=True)
        self.git.run_git(["config", "user.name", self.user_name], cwd=self.path, check=True)
        self.git.run_git(["config", "user.email", self.user_email], cwd=self.path, check=True)
        if initial_commit_msg:
            self.commit_file("initial.txt", "initial content", initial_commit_msg)
        return self

    def write_file(self, rel_path: str, content: str) -> Path:
        """Writes `content` to `rel_path` inside the repository."""
        file_path = self.path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def commit_file(self, rel_path: str, content: str, message: str) -> str:
        """Writes `rel_path`, stages it, commits with `message`, and returns HEAD SHA."""
        self.write_file(rel_path, content)
        self.git.add([rel_path], cwd=self.path, check=True)
        self.git.commit(message, cwd=self.path, check=True)
        return self.head_sha()

    def head_sha(self) -> str:
        """Returns the current HEAD commit SHA."""
        return self.git.rev_parse("HEAD", cwd=self.path, check=True)

    def cleanup(self) -> None:
        """Cleans up the underlying temporary directory if owned by this instance."""
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def __enter__(self) -> "TempGitRepo":
        return self.init()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
