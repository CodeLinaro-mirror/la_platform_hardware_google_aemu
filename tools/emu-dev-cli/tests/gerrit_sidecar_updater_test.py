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

"""Unit tests for Fast Gerrit UI sidecar update detection and dirty-worktree protection."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from commands.gerrit.sidecar_updater import (
    GerritSidecarUpdater,
    collect_installed_gerrit_blobs,
    compute_git_blob_sha1,
)


def _git(cwd: Path, *args: str) -> str:
    """Runs a deterministic local `git` command for test fixture setup."""
    res = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return res.stdout.strip()


def _sync_repo_to_installed_lib(repo_root: Path, lib_dir: Path) -> None:
    """Copies watched Gerrit files from `repo_root` into `lib_dir` just like `emu-dev-cli install`."""
    if lib_dir.exists():
        shutil.rmtree(lib_dir)
    src_root = repo_root / "tools" / "emu-dev-cli" / "src"
    shutil.copytree(src_root, lib_dir)
    sidecar_src = repo_root / "tools" / "emu-dev-cli" / "harness" / "plugin" / "sidecars" / "aemu_gerrit_ui"
    sidecar_dst = lib_dir.parent / "harness" / "plugin" / "sidecars" / "aemu_gerrit_ui"
    if sidecar_dst.exists():
        shutil.rmtree(sidecar_dst)
    if sidecar_src.is_dir():
        shutil.copytree(sidecar_src, sidecar_dst)


class GerritSidecarUpdaterTest(unittest.TestCase):
    """Verifies Gerrit directory diff detection, upstream commit checks, and dirty-tree safety."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.bare_remote = self.root / "remote_aemu.git"
        self.bare_remote.mkdir(parents=True)
        _git(self.bare_remote, "init", "--bare", "--initial-branch=studio-main")

        # Create an upstream author worktree to push commits to `studio-main`.
        self.upstream_work = self.root / "upstream_author"
        self.upstream_work.mkdir(parents=True)
        _git(self.upstream_work, "init", "--initial-branch=studio-main")
        _git(self.upstream_work, "config", "user.email", "author@google.com")
        _git(self.upstream_work, "config", "user.name", "Author")
        _git(self.upstream_work, "remote", "add", "goog", str(self.bare_remote))

        self._write_watched_seed_files(self.upstream_work)
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Initial Fast Gerrit UI")
        _git(self.upstream_work, "push", "goog", "studio-main")

        # Create workspace/hardware/google/aemu clone with remote named `goog`.
        self.workspace_root = self.root / "emu-main-next"
        (self.workspace_root / "MODULE.bazel").parent.mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "MODULE.bazel").write_text("module(name = 'emu')\n", encoding="utf-8")
        self.aemu_repo = self.workspace_root / "hardware" / "google" / "aemu"
        self.aemu_repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--origin",
                "goog",
                "--branch",
                "studio-main",
                str(self.bare_remote),
                str(self.aemu_repo),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
        )
        _git(self.aemu_repo, "config", "user.email", "dev@google.com")
        _git(self.aemu_repo, "config", "user.name", "Dev")

        self.release_dir = self.root / "installed_release"
        self.lib_dir = self.release_dir / "lib"
        _sync_repo_to_installed_lib(self.aemu_repo, self.lib_dir)
        self.install_calls: list[tuple[Path, Path]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_watched_seed_files(self, repo: Path) -> None:
        files = {
            "tools/emu-dev-cli/src/commands/gerrit/ui_server.py": "# ui_server v1\n",
            "tools/emu-dev-cli/src/templates/gerrit_ui.html": "<html>v1</html>\n",
            "tools/emu-dev-cli/src/lib/gerrit_api.py": "# gerrit_api v1\n",
            "tools/emu-dev-cli/harness/plugin/sidecars/aemu_gerrit_ui/server.py": "# sidecar v1\n",
            "base/ unrelated.cpp": "// non-gerrit file\n",
        }
        for rel_path, content in files.items():
            full = repo / rel_path.replace(" ", "")
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

    def _make_updater(self) -> GerritSidecarUpdater:
        def fake_installer(ws: Path, repo: Path) -> None:
            self.install_calls.append((ws, repo))
            _sync_repo_to_installed_lib(repo, self.lib_dir)

        return GerritSidecarUpdater(
            workspace_root=self.workspace_root,
            aemu_repo_root=self.aemu_repo,
            installed_lib_dir=self.lib_dir,
            fetch_ttl_sec=0.0,
            install_runner=fake_installer,
        )

    def test_up_to_date_when_repo_and_installed_blobs_match(self) -> None:
        updater = self._make_updater()
        status = updater.get_update_status(force_fetch=True, ui_version="v1")
        self.assertTrue(status["configured"])
        self.assertFalse(status["update_available"])
        self.assertEqual(status["update_reason"], "up_to_date")
        self.assertEqual(status["behind_count"], 0)
        self.assertFalse(status["has_unstaged_changes"])
        self.assertEqual(status["dirty_files"], [])

        # Confirm Git blob SHA-1 matches exact file bytes.
        ui_bytes = (self.lib_dir / "templates" / "gerrit_ui.html").read_bytes()
        blobs = collect_installed_gerrit_blobs(self.lib_dir)
        self.assertEqual(
            blobs["tools/emu-dev-cli/src/templates/gerrit_ui.html"],
            compute_git_blob_sha1(ui_bytes),
        )

    def test_upstream_unrelated_commit_outside_gerrit_does_not_trigger_update(self) -> None:
        (self.upstream_work / "base" / "unrelated.cpp").write_text("// v2\n", encoding="utf-8")
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "chore(base): Touch non-gerrit C++ file")
        _git(self.upstream_work, "push", "goog", "studio-main")

        updater = self._make_updater()
        status = updater.get_update_status(force_fetch=True)
        self.assertFalse(status["update_available"])
        self.assertEqual(status["update_reason"], "up_to_date")
        self.assertEqual(status["behind_count"], 0)

    def test_upstream_gerrit_commit_triggers_update_available_and_clean_install_clears_it(self) -> None:
        (self.upstream_work / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html").write_text(
            "<html>v2 测试_🚀_é</html>\n", encoding="utf-8"
        )
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Add Unicode header 测试_🚀_é")
        _git(self.upstream_work, "push", "goog", "studio-main")

        updater = self._make_updater()
        status = updater.get_update_status(force_fetch=True)
        self.assertTrue(status["update_available"])
        self.assertEqual(status["update_reason"], "upstream_ahead")
        self.assertEqual(status["behind_count"], 1)
        self.assertEqual(len(status["pending_commits"]), 1)
        self.assertIn("测试_🚀_é", status["pending_commits"][0]["subject"])
        self.assertIn(
            "tools/emu-dev-cli/src/templates/gerrit_ui.html",
            status["changed_gerrit_files"],
        )

        res = updater.perform_update(force_discard_local=False, ui_version_fn=lambda: "v2")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(self.install_calls), 1)
        self.assertFalse(res["update_status"]["update_available"])
        self.assertIn(
            "测试_🚀_é",
            (self.lib_dir / "templates" / "gerrit_ui.html").read_text(encoding="utf-8"),
        )

    def test_local_committed_head_ahead_of_installed_triggers_and_already_installed_does_not(
        self,
    ) -> None:
        (self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "commands" / "gerrit" / "ui_server.py").write_text(
            "# local feature v2\n", encoding="utf-8"
        )
        _git(self.aemu_repo, "add", ".")
        _git(self.aemu_repo, "commit", "-m", "feat(gerrit): Local committed feature")

        updater = self._make_updater()
        # Before installing into `self.lib_dir`, local HEAD is ahead of installed sidecar.
        status_before = updater.get_update_status(force_fetch=True)
        self.assertTrue(status_before["update_available"])
        self.assertEqual(status_before["update_reason"], "local_ahead_of_installed")

        # Once installed, local HEAD ahead of `goog/studio-main` must NOT prompt a downgrade.
        _sync_repo_to_installed_lib(self.aemu_repo, self.lib_dir)
        status_after = updater.get_update_status(force_fetch=True)
        self.assertFalse(status_after["update_available"])
        self.assertEqual(status_after["update_reason"], "up_to_date")

    def test_installed_uncommitted_worktree_edits_do_not_falsely_trigger_until_upstream_advances(
        self,
    ) -> None:
        (self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html").write_text(
            "<html>local uncommitted installed WIP</html>\n", encoding="utf-8"
        )
        _sync_repo_to_installed_lib(self.aemu_repo, self.lib_dir)

        updater = self._make_updater()
        status = updater.get_update_status(force_fetch=True)
        self.assertFalse(status["update_available"])
        self.assertEqual(status["update_reason"], "up_to_date")
        self.assertTrue(status["has_unstaged_changes"])

        # When upstream pushes a new Gerrit commit, Update Available turns True AND warns of dirty worktree.
        (self.upstream_work / "tools" / "emu-dev-cli" / "src" / "lib" / "gerrit_api.py").write_text(
            "# upstream v2\n", encoding="utf-8"
        )
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Upstream API v2")
        _git(self.upstream_work, "push", "goog", "studio-main")

        status_upstream = updater.get_update_status(force_fetch=True)
        self.assertTrue(status_upstream["update_available"])
        self.assertEqual(status_upstream["update_reason"], "upstream_ahead")
        self.assertTrue(status_upstream["has_unstaged_changes"])

    def test_dirty_worktree_with_unicode_blocks_until_force_discard_local_confirmed(self) -> None:
        # Upstream adds a new Unicode file AND updates ui_server.py.
        unicode_rel = "tools/emu-dev-cli/src/commands/gerrit/helper_测试_🚀_é.py"
        (self.upstream_work / unicode_rel).write_text("# upstream unicode\n", encoding="utf-8")
        (self.upstream_work / "tools" / "emu-dev-cli" / "src" / "commands" / "gerrit" / "ui_server.py").write_text(
            "# upstream v2\n", encoding="utf-8"
        )
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Upstream helper 测试_🚀_é")
        _git(self.upstream_work, "push", "goog", "studio-main")

        # Local worktree has unstaged modification AND colliding untracked Unicode file,
        # plus an unrelated non-colliding untracked scratch note that should be preserved.
        local_ui_srv = self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "commands" / "gerrit" / "ui_server.py"
        local_ui_srv.write_text("# local unstaged WIP\n", encoding="utf-8")
        local_colliding = self.aemu_repo / unicode_rel
        local_colliding.write_text("# local untracked collision\n", encoding="utf-8")
        unrelated_note = self.aemu_repo / "MY_LOCAL_NOTES.txt"
        unrelated_note.write_text("keep me safe\n", encoding="utf-8")

        updater = self._make_updater()
        status = updater.get_update_status(force_fetch=True)
        self.assertTrue(status["update_available"])
        self.assertTrue(status["has_unstaged_changes"])
        dirty_paths = {d["path"]: d["kind"] for d in status["dirty_files"]}
        self.assertEqual(
            dirty_paths.get("tools/emu-dev-cli/src/commands/gerrit/ui_server.py"),
            "unstaged",
        )
        self.assertEqual(dirty_paths.get(unicode_rel), "untracked_conflict")
        self.assertNotIn("MY_LOCAL_NOTES.txt", dirty_paths)

        # Calling perform_update without confirmation MUST refuse to wipe local changes.
        blocked = updater.perform_update(force_discard_local=False)
        self.assertEqual(blocked["status"], "dirty_worktree")
        self.assertTrue(blocked["requires_confirmation"])
        self.assertEqual(len(self.install_calls), 0)
        self.assertEqual(local_ui_srv.read_text(encoding="utf-8"), "# local unstaged WIP\n")
        self.assertEqual(local_colliding.read_text(encoding="utf-8"), "# local untracked collision\n")

        # Confirming force_discard_local=True wipes dirty files, pulls upstream, and installs.
        confirmed = updater.perform_update(force_discard_local=True)
        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(len(self.install_calls), 1)
        self.assertEqual(local_ui_srv.read_text(encoding="utf-8"), "# upstream v2\n")
        self.assertEqual(local_colliding.read_text(encoding="utf-8"), "# upstream unicode\n")
        self.assertEqual(unrelated_note.read_text(encoding="utf-8"), "keep me safe\n")
        self.assertFalse(confirmed["update_status"]["update_available"])

    def test_local_committed_branch_rebases_cleanly_or_aborts_on_conflict(self) -> None:
        # Local committed change on gerrit_api.py.
        (self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "lib" / "gerrit_api.py").write_text(
            "# local committed api change\n", encoding="utf-8"
        )
        _git(self.aemu_repo, "add", ".")
        _git(self.aemu_repo, "commit", "-m", "feat(gerrit): Local API helper")
        _sync_repo_to_installed_lib(self.aemu_repo, self.lib_dir)

        # Upstream non-conflicting change on gerrit_ui.html -> rebases cleanly preserving local commit.
        (self.upstream_work / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html").write_text(
            "<html>upstream v2</html>\n", encoding="utf-8"
        )
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Upstream template update")
        _git(self.upstream_work, "push", "goog", "studio-main")

        updater = self._make_updater()
        res = updater.perform_update(force_discard_local=False)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(
            (self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "lib" / "gerrit_api.py").read_text(encoding="utf-8"),
            "# local committed api change\n",
        )
        self.assertEqual(
            (self.aemu_repo / "tools" / "emu-dev-cli" / "src" / "templates" / "gerrit_ui.html").read_text(encoding="utf-8"),
            "<html>upstream v2</html>\n",
        )

        # Upstream conflicting change on gerrit_api.py -> rebase aborts cleanly and raises actionable error.
        (self.upstream_work / "tools" / "emu-dev-cli" / "src" / "lib" / "gerrit_api.py").write_text(
            "# conflicting upstream api change\n", encoding="utf-8"
        )
        _git(self.upstream_work, "add", ".")
        _git(self.upstream_work, "commit", "-m", "feat(gerrit): Conflicting upstream API")
        _git(self.upstream_work, "push", "goog", "studio-main")

        with self.assertRaisesRegex(RuntimeError, "aborted cleanly"):
            updater.perform_update(force_discard_local=False)
        # Ensure repository is NOT left mid-rebase.
        self.assertFalse((self.aemu_repo / ".git" / "rebase-merge").exists())
        self.assertFalse((self.aemu_repo / ".git" / "rebase-apply").exists())

    def test_concurrent_perform_update_returns_busy_without_double_running(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking_installer(_ws: Path, _repo: Path) -> None:
            entered.set()
            release.wait(timeout=5.0)

        updater = GerritSidecarUpdater(
            workspace_root=self.workspace_root,
            aemu_repo_root=self.aemu_repo,
            installed_lib_dir=self.lib_dir,
            fetch_ttl_sec=0.0,
            install_runner=blocking_installer,
        )
        bg_result: list[dict[str, object]] = []

        def run_first() -> None:
            bg_result.append(updater.perform_update(force_discard_local=False))

        worker = threading.Thread(target=run_first, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(timeout=5.0))
        second = updater.perform_update(force_discard_local=False)
        self.assertEqual(second["status"], "busy")
        release.set()
        worker.join(timeout=5.0)
        self.assertEqual(bg_result[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
