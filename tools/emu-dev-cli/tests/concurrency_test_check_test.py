#!/usr/bin/env python3
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

"""Unit tests for the AEMU Harness concurrency-test check (PostToolUse warning)."""

import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict
import unittest

from commands.harness.concurrency_test_check import (
    WARNING_HEADER,
    find_untested_concurrency_signals,
    parse_added_lines,
)
from commands.harness.constants import HookEvent
from commands.harness.guardrails import dispatch_hook_event
from commands.harness.ledger import SessionLedgerStore

GIT_TEST_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@google.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@google.com",
}
CONVERSATION_ID = "conv-concurrency-check"
COMMIT_RESULT = "[main 1234567] feat(async): Guard the task queue"
GOLDFISH_PARTS = ("hardware", "generic", "goldfish")
UNICODE_SRC_PATH = "emulator/libs/async/事件_loop_🚀_é.cc"

MUTEX_DIFF = """\
diff --git a/emulator/libs/async/event_loop.cc b/emulator/libs/async/event_loop.cc
index 1111111..2222222 100644
--- a/emulator/libs/async/event_loop.cc
+++ b/emulator/libs/async/event_loop.cc
@@ -10,0 +11,2 @@ void EventLoop::Drain() {
+    absl::MutexLock lock(&mu_);
+    loop_->Post([this] { Drain(); }).IgnoreError();
@@ -40 +41,0 @@ void EventLoop::Stop() {
-    std::thread worker(Run);
"""

TEST_DIFF = """\
diff --git a/emulator/libs/async/event_loop_test.cc b/emulator/libs/async/event_loop_test.cc
--- a/emulator/libs/async/event_loop_test.cc
+++ b/emulator/libs/async/event_loop_test.cc
@@ -5,0 +6 @@
+TEST(EventLoopTest, DrainFromTwoThreads) {}
"""

IGNORED_DIFF = """\
diff --git a/emulator/libs/async/notes.cc b/emulator/libs/async/notes.cc
--- a/emulator/libs/async/notes.cc
+++ b/emulator/libs/async/notes.cc
@@ -1,0 +2,2 @@
+// Callers hold absl::Mutex mu_ while calling Post(...).
+ * std::thread is never created here.
diff --git a/emulator/libs/async/README.md b/emulator/libs/async/README.md
--- a/emulator/libs/async/README.md
+++ b/emulator/libs/async/README.md
@@ -1,0 +2 @@
+Use `absl::MutexLock` to guard the queue.
diff --git a/third_party/sparse/sparse.cc b/third_party/sparse/sparse.cc
--- a/third_party/sparse/sparse.cc
+++ b/third_party/sparse/sparse.cc
@@ -1,0 +2 @@
+static std::mutex g_sparse_mutex;
"""


def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=GIT_TEST_ENV,
    )
    return proc.stdout.strip()


class ConcurrencyDiffScanTest(unittest.TestCase):
    """Tests diff parsing and signal detection without touching git."""

    def test_parse_added_lines_keeps_only_added_lines(self) -> None:
        added = parse_added_lines(MUTEX_DIFF)
        self.assertEqual(
            added,
            {
                "emulator/libs/async/event_loop.cc": [
                    "    absl::MutexLock lock(&mu_);",
                    "    loop_->Post([this] { Drain(); }).IgnoreError();",
                ]
            },
        )

    def test_flags_concurrency_change_without_tests(self) -> None:
        signals = find_untested_concurrency_signals(parse_added_lines(MUTEX_DIFF))
        self.assertEqual(
            signals,
            {"emulator/libs/async/event_loop.cc": ["mutex/lock", "event-loop posting/timer"]},
        )

    def test_test_change_in_same_commit_suppresses_warning(self) -> None:
        signals = find_untested_concurrency_signals(parse_added_lines(MUTEX_DIFF + TEST_DIFF))
        self.assertEqual(signals, {})

    def test_ignores_comments_non_sources_and_third_party(self) -> None:
        signals = find_untested_concurrency_signals(parse_added_lines(IGNORED_DIFF))
        self.assertEqual(signals, {})


class ConcurrencyPostToolHookTest(unittest.TestCase):
    """Tests the PostToolUse integration against real temporary git repositories."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ledger_store = SessionLedgerStore(base_dir=self.root / "harness")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_repo(self, *parts: str) -> Path:
        repo = self.root.joinpath(*parts)
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "chore: Initial commit")
        return repo

    def _commit_files(self, repo: Path, files: Dict[str, str]) -> None:
        for rel_path, content in files.items():
            path = repo / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "feat(async): Guard the task queue")

    def _post_tool(self, command: str, **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "conversationId": CONVERSATION_ID,
            "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
            "result": COMMIT_RESULT,
            **extra,
        }
        return dispatch_hook_event(HookEvent.POST_TOOL.value, payload, store=self.ledger_store)

    def test_warns_after_goldfish_commit_and_upload_without_tests(self) -> None:
        repo = self._init_repo(*GOLDFISH_PARTS)
        self._commit_files(
            repo, {UNICODE_SRC_PATH: "void Drain() {\n  absl::MutexLock lock(&mu_);\n}\n"}
        )

        commit_out = self._post_tool(f"git -C {repo} commit --amend --no-edit")
        self.assertIn("overwriteResult", commit_out)
        result = commit_out["overwriteResult"]
        self.assertTrue(result.startswith(COMMIT_RESULT))
        self.assertIn(WARNING_HEADER, result)
        self.assertIn(f"{UNICODE_SRC_PATH}: mutex/lock", result)
        self.assertIn("Concurrency & Lifetime Tests", result)

        upload_out = self._post_tool(f"cd {repo} && repo upload --cbr --no-verify -y .")
        self.assertIn(WARNING_HEADER, upload_out.get("overwriteResult", ""))

        ledger = self.ledger_store.load(CONVERSATION_ID)
        self.assertEqual(ledger["stats"]["concurrency_test_warnings"], 2)

    def test_no_warning_when_the_commit_also_changes_a_test(self) -> None:
        repo = self._init_repo(*GOLDFISH_PARTS)
        self._commit_files(
            repo,
            {
                "emulator/libs/async/queue.cc": "std::thread worker(Run);\n",
                "emulator/libs/async/queue_test.cc": "TEST(QueueTest, Races) {}\n",
            },
        )
        self.assertNotIn("overwriteResult", self._post_tool(f"git -C {repo} commit --amend --no-edit"))

    def test_no_warning_for_failed_commands_or_other_repos(self) -> None:
        goldfish = self._init_repo(*GOLDFISH_PARTS)
        self._commit_files(goldfish, {"emulator/libs/async/queue.cc": "std::thread worker(Run);\n"})
        failed = self._post_tool(f"git -C {goldfish} commit --amend --no-edit", error="exit status 1")
        self.assertNotIn("overwriteResult", failed)

        other = self._init_repo("hardware", "google", "aemu")
        self._commit_files(other, {"src/queue.cc": "std::thread worker(Run);\n"})
        self.assertNotIn("overwriteResult", self._post_tool(f"git -C {other} commit --amend --no-edit"))

    def test_no_warning_for_quoted_git_commit_mentions(self) -> None:
        repo = self._init_repo(*GOLDFISH_PARTS)
        self._commit_files(repo, {"emulator/libs/async/queue.cc": "std::thread worker(Run);\n"})
        for command in (
            f'cd {repo} && grep -rn "git commit" docs/',
            f"cd {repo} && echo 'run git commit --amend later'",
        ):
            self.assertNotIn("overwriteResult", self._post_tool(command), msg=command)


if __name__ == "__main__":
    unittest.main()
