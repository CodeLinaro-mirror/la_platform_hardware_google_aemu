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

"""Hermetic unit tests for `emu-dev-cli gerrit stack watch` (zero @patch)."""

import argparse
from collections.abc import Callable
from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.chat_service import FakeChatNotificationService, format_review_ping_message
from commands.gerrit import register_parser
from commands.gerrit.stack import (
    PingLedger,
    StackWatchArgs,
    StackWatchCommand,
    StackWatchReport,
    WatchEvent,
)
from commands.gerrit.stack.watch import (
    MAX_RENDERED_EVENTS,
    OUTCOME_ABORTED,
    OUTCOME_INTERRUPTED,
    OUTCOME_TIMEOUT,
    PING_LEDGER_FILENAME,
    render_watch_report,
)
from commands.gerrit.stack.watch_service import (
    EVENT_MERGEABLE,
    EVENT_NEW_PATCH_SET,
    EVENT_PING_FAILED,
    EVENT_PING_SKIPPED,
    EVENT_PINGED,
    EVENT_POLL_FAILED,
    EVENT_PRESUBMIT,
    EVENT_REBASE_CONFLICT,
    EVENT_REBASE_DEFERRED,
    EVENT_REBASE_FAILED,
    EVENT_REBASE_NEEDED,
    EVENT_REBASED,
    EVENT_STATUS,
    EVENT_UP_TO_DATE,
    MAX_PING_LEDGER_ENTRIES,
    OUTCOME_ATTENTION,
    OUTCOME_COMPLETE,
    OUTCOME_IN_PROGRESS,
)
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import ExecutionError, ExitCode, RetryableError, UsageError
from core.git import FakeGitService
from core.process import FakeProcessCall, ProcessResult
from testlib.gerrit_fakes import (
    AGENT,
    DENALEX,
    ERWIN,
    JOSHUA,
    FakeStackGerritClient,
    change_id,
    related_entry,
    stack_change,
    submit_requirements,
)

TOPIC = "emu-dev-cli-gerrit-stack-phase3"
POLL = 60
HOST = "https://googleplex-android-review.git.corp.google.com"
# No Presubmit-Verified requirement, so the presubmit state comes from the labels alone.
NEEDS_REVIEW = submit_requirements(review_enforcement="UNSATISFIED", code_review="UNSATISFIED")
APPROVED = submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED")
CONFLICT = {"mergeable": False, "conflicts": ["src/测试_🚀_é.py"]}


def _green(number: int, **kwargs: Any) -> dict[str, Any]:
    """An open CL with Presubmit-Verified +2 that still needs two approvals."""
    kwargs.setdefault("labels", {"Presubmit-Verified": 2})
    kwargs.setdefault("requirements", NEEDS_REVIEW)
    return stack_change(number, **kwargs)


def _failed_presubmit(number: int, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("messages", [
        {"_revision_number": 1, "message": "Patch Set 1:\n\n## Presubmit started: L42"},
    ])
    return stack_change(number, labels={"Presubmit-Verified": -1}, requirements=NEEDS_REVIEW, **kwargs)


class FakeClock:
    """Monotonic clock that only moves when the watch sleeps, running one script step per sleep."""

    def __init__(self, *steps: Callable[[], None]) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self._steps = list(steps)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self._steps:
            self._steps.pop(0)()


def _watch(
    client: FakeStackGerritClient, clock: FakeClock | None = None, progress: list[str] | None = None
) -> StackWatchCommand:
    clock = clock or FakeClock()
    return StackWatchCommand(
        gerrit_client_factory=lambda ctx, host: client,
        sleep_fn=clock.sleep,
        clock_fn=clock.time,
        progress_fn=progress.append if progress is not None else None,
    )


def _merged_parent_repo(
    mergeable: dict[int, dict[str, Any]] | None = None, **child: Any
) -> tuple[FakeGitService, FakeStackGerritClient]:
    """ag/971 merged into studio-main; ag/972 on branch feat-stack is still based on its patch set."""
    git = FakeGitService(current_branch_name="feat-stack")
    git.config_values["user.email"] = AGENT
    git.seed_commit("base_sha", "chore: Base\n")
    git.seed_commit(
        "sha_971_ps1", f"feat: Parent\n\nChange-Id: {change_id(971)}\n", parents=("base_sha",)
    )
    git.seed_commit(
        "merge_sha",
        "Merge \"feat: Parent\" into studio-main\n",
        parents=("sha_971_ps1",),
        changed_files=("src/upstream.py",),
    )
    git.refs["aosp/studio-main"] = "merge_sha"
    git.seed_commit(
        "sha_972_ps1",
        f"feat: Child 测试_🚀_é\n\nChange-Id: {change_id(972)}\n",
        parents=("sha_971_ps1",),
        changed_files=("src/child.py",),
    )
    git.refs["feat-stack"] = "sha_972_ps1"
    child.setdefault("requirements", NEEDS_REVIEW)
    parent = stack_change(971, status="MERGED", extra={"topic": TOPIC})
    tip = stack_change(972, parent_commit="sha_971_ps1", extra={"topic": TOPIC}, **child)
    related = [related_entry(972, parent_commit="sha_971_ps1"), related_entry(971, status="MERGED")]
    return git, FakeStackGerritClient([parent, tip], {971: related, 972: related}, mergeable)


def _pushes(git: FakeGitService) -> list[tuple[str, ...]]:
    return [cmd for cmd in git.recorded_commands if cmd[:2] == ("git", "push")]


class WatchTestCase(unittest.TestCase):
    """Gives every test its own ping ledger file."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.state_file = os.path.join(self.tmp, "pings.json")

    def _ctx(self, git: FakeGitService | None = None, **services: Any) -> ExecutionContext:
        if git is not None:
            services["git_service"] = git
        return ExecutionContext.for_testing(self.tmp, services=services)

    def _args(self, **kwargs: Any) -> StackWatchArgs:
        kwargs.setdefault("state_file", self.state_file)
        kwargs.setdefault("repo", "/fake/repo")
        return StackWatchArgs(**kwargs)


class StackWatchPingTest(WatchTestCase):
    """Pings reviewers once per green CL and remembers it across runs."""

    def test_ping_reviewers_sends_one_stack_message_per_reviewer_once(self) -> None:
        base = _green(951, extra={"topic": TOPIC})
        tip = _green(952, parent_commit="sha_951_ps1", extra={"topic": TOPIC})
        related = [related_entry(952, parent_commit="sha_951_ps1"), related_entry(951)]
        client = FakeStackGerritClient([base, tip], {951: related, 952: related})
        ctx = self._ctx()

        result = _watch(client).execute(self._args(cl="952", once=True, ping_reviewers=True), ctx)

        self.assertTrue(result.ok)
        self.assertTrue(client.closed)
        report = result.data
        self.assertIsInstance(report, StackWatchReport)
        self.assertEqual(report.outcome, OUTCOME_IN_PROGRESS)
        self.assertEqual(report.iterations, 1)
        sent = ctx.fake_chat_service.sent_dms
        self.assertEqual([dm.recipient for dm in sent], [ERWIN, JOSHUA, DENALEX])
        message = sent[0].message
        self.assertTrue(message.startswith("Hi! These CLs are green and ready for review"))
        self.assertIn(f"[ag/951]({HOST}/c/platform/hardware/google/aemu/+/951)", message)
        self.assertIn("feat(gerrit): Add stack status 测试_🚀_é", message)
        self.assertIn("- [ag/952]", message)
        self.assertEqual([e.kind for e in report.events], [EVENT_PINGED] * 3)
        self.assertEqual(report.events[0].message, "Pinged jansene@ to review ag/951, ag/952")
        self.assertEqual(report.events[0].details["cls"], [951, 952])
        self.assertEqual(report.attention, ())

        with open(self.state_file, encoding="utf-8") as handle:
            ledger = json.load(handle)["pings"]
        self.assertEqual(len(ledger), 6)
        self.assertIn(PingLedger.key(HOST, 951, ERWIN), ledger)

        # A rerun, even on a new patch set, does not ping the same reviewers again.
        client.changes[952] = _green(952, patch_set=2, parent_commit="sha_951_ps1", extra={"topic": TOPIC})
        rerun = _watch(client).execute(self._args(cl="952", once=True, ping_reviewers=True), ctx)
        self.assertTrue(rerun.ok)
        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 3)
        self.assertEqual(rerun.data.events, ())

        payload = report.to_dict()
        self.assertEqual(payload["mode"], "once")
        self.assertEqual(payload["settings"]["ping_reviewers"], True)
        self.assertEqual(payload["events"][0]["details"]["recipient"], ERWIN)
        self.assertEqual(payload["note"], "")
        self.assertEqual(len(report), len(payload))
        self.assertEqual(report["outcome"], OUTCOME_IN_PROGRESS)
        self.assertIn("stacks", list(report))
        json.dumps(payload, ensure_ascii=False)
        rendered = result.markdown_table or ""
        self.assertIn("Stack watch: Still in flight (single poll)", rendered)
        self.assertIn("[poll 1] pinged: Pinged corakwue@ to review ag/951, ag/952", rendered)

    def test_explicit_ping_uses_the_single_cl_message_and_skips_cls_that_are_not_ready(self) -> None:
        topic = {"topic": TOPIC}
        green = _green(961, extra=topic)
        not_ready = [
            stack_change(962, labels={"Presubmit-Verified": 0}, requirements=NEEDS_REVIEW, extra=topic),
            _green(963, extra={**topic, "work_in_progress": True}),
            _green(964, votes=[(ERWIN, 2), (JOSHUA, 1)], requirements=APPROVED, extra=topic),
            _green(965, votes=[(DENALEX, -2)], extra=topic),
            _green(966, extra={**topic, "unresolved_comment_count": 1}),
        ]
        client = FakeStackGerritClient([green, *not_ready], {})
        ctx = self._ctx()

        result = _watch(client).execute(
            self._args(topic=TOPIC, once=True, rebase=False, ping=("jansene,, joshuaduong@google.com", "jansene")),
            ctx,
        )

        self.assertEqual(result.data.settings["ping"], [ERWIN, JOSHUA])
        sent = ctx.fake_chat_service.sent_dms
        self.assertEqual([(dm.recipient, dm.cl_number) for dm in sent], [(ERWIN, "961"), (JOSHUA, "961")])
        self.assertEqual(
            sent[0].message,
            format_review_ping_message(
                cl_number="961",
                subject=green["subject"],
                gerrit_url=f"{HOST}/c/platform/hardware/google/aemu/+/961",
                patch_set=1,
                presubmit_score=2,
            ),
        )
        self.assertEqual(result.data.query, {"mode": "topic", "input": TOPIC, "host": HOST})
        # ag/965 has a -2, which needs the author, so the stack needs attention.
        self.assertEqual(result.data.outcome, OUTCOME_ATTENTION)

    def test_reviewers_who_cannot_get_a_dm_are_skipped_without_attention(self) -> None:
        client = FakeStackGerritClient([_green(967, reviewers=(ERWIN, "partner@android.com"))], {})
        ctx = self._ctx()
        result = _watch(client, progress=[]).execute(
            self._args(cl="967", timeout=POLL, poll_interval=POLL, rebase=False, ping_reviewers=True), ctx
        )
        self.assertEqual([dm.recipient for dm in ctx.fake_chat_service.sent_dms], [ERWIN])
        skipped = [e for e in result.data.events if e.kind == EVENT_PING_SKIPPED]
        self.assertEqual(len(skipped), 1)
        self.assertIn("Not pinging partner@android.com about ag/967", skipped[0].message)
        self.assertEqual(result.data.attention, ())
        self.assertEqual(result.data.outcome, OUTCOME_TIMEOUT)

    def test_failed_ping_needs_attention_and_is_retried_by_the_next_run(self) -> None:
        client = FakeStackGerritClient([_green(968)], {})
        chat = FakeChatNotificationService(
            failure_error=RetryableError("Timed out after 25s while sending Google Chat DM.")
        )
        ctx = self._ctx(chat_service=chat)

        result = _watch(client).execute(self._args(cl="968", once=True, ping=("jansene",)), ctx)

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, int(ExitCode.FAILURE))
        event = result.data.events[0]
        self.assertEqual(event.kind, EVENT_PING_FAILED)
        self.assertTrue(event.attention)
        self.assertIn("Could not ping jansene@google.com about ag/968: Timed out", event.message)
        self.assertIn("Next: run `gcert`", event.message)
        self.assertFalse(os.path.exists(self.state_file))
        self.assertIn("Fix the items listed under 'Needs attention'", result.metadata["remediation"])
        self.assertIn("Next step: Fix the items", result.markdown_table)

    def test_unsaved_ledger_still_reports_the_ping_but_asks_for_attention(self) -> None:
        blocker = os.path.join(self.tmp, "not-a-directory")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        client = FakeStackGerritClient([_green(969)], {})
        ctx = self._ctx()

        result = _watch(client).execute(
            self._args(cl="969", once=True, ping=("jansene",), state_file=os.path.join(blocker, "pings.json")),
            ctx,
        )

        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 1)
        event = result.data.events[0]
        self.assertEqual(event.kind, EVENT_PINGED)
        self.assertTrue(event.attention)
        self.assertIn("but could not save", event.message)
        self.assertEqual(result.data.outcome, OUTCOME_ATTENTION)

    def test_default_ledger_lives_in_the_cache_dir(self) -> None:
        client = FakeStackGerritClient([_green(970)], {})
        result = _watch(client).execute(
            StackWatchArgs(cl="970", once=True, ping=("jansene",), repo="/fake/repo"), self._ctx()
        )
        expected = os.path.join(self.tmp, "cache", PING_LEDGER_FILENAME)
        self.assertEqual(result.data.settings["state_file"], expected)
        self.assertTrue(os.path.isfile(expected))


class PingLedgerTest(WatchTestCase):
    """Loads tolerant of bad files and keeps only the newest entries."""

    def test_missing_malformed_and_wrong_shape_files_start_empty(self) -> None:
        self.assertNotIn("k", PingLedger.load(Path(self.tmp) / "missing.json"))
        for name, text in (("bad.json", "{not json"), ("list.json", '{"pings": []}')):
            path = Path(self.tmp) / name
            path.write_text(text, encoding="utf-8")
            self.assertNotIn("k", PingLedger.load(path))

    def test_record_prunes_to_the_newest_entries_and_round_trips(self) -> None:
        path = Path(self.tmp) / "ledger.json"
        ledger = PingLedger.load(path)
        ledger.record([f"old-{i}" for i in range(MAX_PING_LEDGER_ENTRIES)], "2026-11-01T00:00:00+00:00")
        ledger.record(["newest|测试_🚀_é"], "2026-11-03T00:00:00+00:00")
        self.assertIn("newest|测试_🚀_é", PingLedger.load(path))
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["pings"]), MAX_PING_LEDGER_ENTRIES)


class StackWatchRebaseTest(WatchTestCase):
    """Rebases open CLs with `gerrit stack sync` once their parent merges, but only when it is safe."""

    def test_merged_parent_triggers_one_rebase_and_push_with_notify_owner(self) -> None:
        git, client = _merged_parent_repo()
        clock = FakeClock()

        result = _watch(client, clock, progress=[]).execute(
            self._args(timeout=2 * POLL, poll_interval=POLL, presubmit=True), self._ctx(git)
        )

        self.assertEqual(result.data.outcome, OUTCOME_TIMEOUT)
        self.assertEqual(result.exit_code, int(ExitCode.RETRY_OR_LOOP))
        self.assertIn("pass a larger --timeout", result.metadata["remediation"])
        self.assertEqual(result.data.iterations, 3)
        self.assertEqual(result.data.elapsed_sec, 2 * POLL)
        self.assertEqual(clock.sleeps, [POLL, POLL])
        self.assertEqual(len(git.pushed_refs), 1)
        remote, refspec, options = git.pushed_refs[0]
        self.assertEqual(remote, "aosp")
        self.assertTrue(refspec.endswith(":refs/for/studio-main"))
        self.assertIn("notify=OWNER", options)
        self.assertIn(f"topic={TOPIC}", options)
        self.assertIn("l=Presubmit-Ready+1", options)
        self.assertNotEqual(git.refs["feat-stack"], "sha_972_ps1")
        rebased = result.data.events[0]
        self.assertEqual(rebased.kind, EVENT_REBASED)
        self.assertEqual(rebased.number, 972)
        self.assertEqual(
            rebased.message,
            "ag/971 merged; rebased 1 CL(s) onto aosp/studio-main (merge_s), "
            "pushed them with notify=OWNER and queued presubmit",
        )
        self.assertEqual(rebased.details["rebased_count"], 1)
        self.assertEqual(result.data.query["mode"], "head")

    def test_green_child_that_still_merges_keeps_its_presubmit(self) -> None:
        git, client = _merged_parent_repo(labels={"Presubmit-Verified": 2})
        result = _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
        event = result.data.events[0]
        self.assertEqual(event.kind, EVENT_REBASE_DEFERRED)
        self.assertIn("ag/972 still merges cleanly and its presubmit is passed", event.message)
        self.assertIn("run `emu-dev-cli gerrit stack sync ag/972 --repo /fake/repo`", event.message)
        self.assertEqual(_pushes(git), [])
        self.assertTrue(result.ok)

    def test_unmergeable_green_child_is_rebased_and_its_old_patch_set_is_not_pinged(self) -> None:
        git, client = _merged_parent_repo({972: CONFLICT}, labels={"Presubmit-Verified": 2})
        ctx = self._ctx(git)
        result = _watch(client, progress=[]).execute(
            self._args(timeout=POLL, poll_interval=POLL, ping_reviewers=True), ctx
        )
        self.assertEqual(result.data.events[0].kind, EVENT_REBASED)
        self.assertIn("; vote Presubmit-Ready +1 to rerun presubmit", result.data.events[0].message)
        self.assertEqual(len(git.pushed_refs), 1)
        self.assertEqual(ctx.fake_chat_service.sent_dms, [])

    def test_parent_merged_by_fast_forward_needs_no_rebase(self) -> None:
        git, client = _merged_parent_repo()
        git.refs["aosp/studio-main"] = "sha_971_ps1"
        result = _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
        self.assertEqual(result.data.events[0].kind, EVENT_UP_TO_DATE)
        self.assertIn("ag/972 is already up to date with aosp/studio-main", result.data.events[0].message)
        self.assertEqual(git.pushed_refs, [])

    def test_waits_until_the_fetched_branch_contains_the_merged_parent(self) -> None:
        git, client = _merged_parent_repo()
        git.refs["aosp/studio-main"] = "base_sha"

        def branch_catches_up() -> None:
            git.refs["aosp/studio-main"] = "merge_sha"

        result = _watch(client, FakeClock(branch_catches_up), progress=[]).execute(
            self._args(cl="972", timeout=POLL, poll_interval=POLL), self._ctx(git)
        )
        kinds = [e.kind for e in result.data.events]
        self.assertEqual(kinds, [EVENT_REBASE_DEFERRED, EVENT_REBASED])
        self.assertEqual(
            result.data.events[0].message,
            "aosp/studio-main does not contain ag/971 yet, so the watch waits before rebasing ag/972",
        )
        self.assertEqual(len(git.pushed_refs), 1)

    def test_fetch_failure_needs_attention(self) -> None:
        git, client = _merged_parent_repo()
        git.process_runner.register_prefix(
            ["git", "fetch", "aosp", "studio-main"],
            ProcessResult(args=("git", "fetch"), returncode=128, stderr="fatal: unable to access remote\n"),
        )
        result = _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
        self.assertEqual(result.exit_code, int(ExitCode.FAILURE))
        event = result.data.events[0]
        self.assertEqual(event.kind, EVENT_REBASE_FAILED)
        self.assertIn("Could not fetch studio-main from 'aosp' to rebase ag/972: fatal: unable to access", event.message)
        self.assertEqual(git.pushed_refs, [])

    def test_no_rebase_only_reports_and_works_outside_a_checkout(self) -> None:
        _, client = _merged_parent_repo()
        ctx = self._ctx(FakeGitService(inside_work_tree=False))
        result = _watch(client).execute(self._args(cl="972", once=True, rebase=False, repo="."), ctx)
        self.assertTrue(result.ok)
        event = result.data.events[0]
        self.assertEqual(event.kind, EVENT_REBASE_NEEDED)
        self.assertEqual(
            event.message, "ag/971 merged; run `emu-dev-cli gerrit stack sync ag/972` to rebase ag/972"
        )

    def test_unsafe_checkouts_are_left_alone_without_attention(self) -> None:
        def detached(git: FakeGitService) -> None:
            git.current_branch_name = ""

        def dirty(git: FakeGitService) -> None:
            git.set_dirty(" M src/child.py\n")

        def elsewhere(git: FakeGitService) -> None:
            git.seed_commit("local_sha", "feat: Unrelated local work\n", parents=("base_sha",))

        def no_user(git: FakeGitService) -> None:
            del git.config_values["user.email"]

        def someone_else(git: FakeGitService) -> None:
            git.config_values["user.email"] = ERWIN

        cases = [
            (detached, "HEAD in '/fake/repo' is detached"),
            (dirty, "'/fake/repo' has uncommitted changes"),
            (elsewhere, "branch 'feat-stack' in '/fake/repo' is at local_s, not at the top CL ag/972 PS1 (sha_972)"),
            (no_user, "`git config user.email` is not set in '/fake/repo'"),
            (someone_else, "ag/972 (groupagent-android-emulator-heavy@) belong to someone else; "
                           "the watch only pushes CLs owned by jansene@google.com"),
        ]
        for setup, expected in cases:
            with self.subTest(expected=expected):
                git, client = _merged_parent_repo()
                setup(git)
                result = _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
                event = result.data.events[0]
                self.assertEqual(event.kind, EVENT_REBASE_NEEDED)
                self.assertFalse(event.attention)
                self.assertIn(expected, event.message)
                self.assertIn("Run `emu-dev-cli gerrit stack sync ag/972 --repo /fake/repo`", event.message)
                self.assertEqual(result.data.outcome, OUTCOME_IN_PROGRESS)
                self.assertEqual(_pushes(git), [])

    def test_head_on_a_middle_cl_does_not_push_the_cl_above_it(self) -> None:
        git, client = _merged_parent_repo()
        client.changes[973] = stack_change(973, parent_commit="sha_972_ps1", requirements=NEEDS_REVIEW,
                                           extra={"topic": TOPIC})
        related = [related_entry(973, parent_commit="sha_972_ps1"), *client.related[972]]
        client.related = {971: related, 972: related, 973: related}
        result = _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
        self.assertIn("not at the top CL ag/973", result.data.events[0].message)
        self.assertEqual(_pushes(git), [])

    def test_conflict_stays_on_the_attention_list_and_is_not_retried(self) -> None:
        git, client = _merged_parent_repo()
        git.rebase_conflicts.add("sha_972_ps1")

        result = _watch(client, progress=[]).execute(
            self._args(timeout=POLL, poll_interval=POLL), self._ctx(git)
        )

        rebases = [c for c in git.recorded_commands if c[:3] == ("git", "rebase", "--onto")]
        self.assertEqual(len(rebases), 1)
        self.assertEqual([e.kind for e in result.data.events], [EVENT_REBASE_CONFLICT])
        conflict = result.data.events[0]
        self.assertTrue(conflict.attention)
        self.assertIn("failed with conflicts in fake_conflict.py", conflict.message)
        self.assertEqual(result.data.attention, (conflict.message,))
        self.assertEqual(result.data.outcome, OUTCOME_ATTENTION)
        self.assertEqual(git.refs["feat-stack"], "sha_972_ps1")

    def test_failed_push_restores_the_branch_and_is_retried(self) -> None:
        git, client = _merged_parent_repo()
        git.push_returncode = 1
        git.push_stderr = " ! [remote rejected] HEAD -> refs/for/studio-main (prohibited by Gerrit)\n"

        result = _watch(client, progress=[]).execute(
            self._args(timeout=POLL, poll_interval=POLL), self._ctx(git)
        )

        self.assertEqual(len(git.pushed_refs), 2)
        self.assertEqual(git.refs["feat-stack"], "sha_972_ps1")
        self.assertIn(("git", "checkout", "-B", "feat-stack", "sha_972_ps1"), git.recorded_commands)
        failed = result.data.events[0]
        self.assertEqual(failed.kind, EVENT_REBASE_FAILED)
        self.assertIn("prohibited by Gerrit", failed.message)
        self.assertIn("Next: run `gcert`, check that you can push to Gerrit", failed.message)
        self.assertEqual(result.data.outcome, OUTCOME_ATTENTION)

    def test_interrupt_during_sync_restores_the_branch(self) -> None:
        git, client = _merged_parent_repo()

        def interrupt(call: FakeProcessCall) -> ProcessResult:
            raise KeyboardInterrupt

        git.process_runner.register_prefix(["git", "push"], interrupt)
        with self.assertRaises(KeyboardInterrupt):
            _watch(client).execute(self._args(cl="972", once=True), self._ctx(git))
        self.assertEqual(git.refs["feat-stack"], "sha_972_ps1")
        self.assertIn(("git", "rebase", "--abort"), git.recorded_commands)
        self.assertTrue(client.closed)

    def test_topic_with_two_chains_only_rebases_the_checked_out_one(self) -> None:
        git, client = _merged_parent_repo()
        other_related = [related_entry(982, parent_commit="sha_981_ps1"), related_entry(981, status="MERGED")]
        client.changes[981] = stack_change(981, status="MERGED", extra={"topic": TOPIC})
        client.changes[982] = stack_change(982, parent_commit="sha_981_ps1", requirements=NEEDS_REVIEW,
                                           extra={"topic": TOPIC})
        client.related.update({981: other_related, 982: other_related})

        result = _watch(client).execute(self._args(topic=TOPIC, once=True), self._ctx(git))

        self.assertTrue(result.ok)
        kinds = {e.number: e.kind for e in result.data.events}
        self.assertEqual(kinds, {972: EVENT_REBASED, 982: EVENT_REBASE_NEEDED})
        self.assertEqual(len(git.pushed_refs), 1)


class StackWatchLoopTest(WatchTestCase):
    """Polls until the stack closes, a deadline passes, or something needs attention."""

    def test_polls_report_transitions_until_every_cl_merges(self) -> None:
        base = stack_change(981, labels={"Presubmit-Ready": 1}, requirements=NEEDS_REVIEW)
        tip = stack_change(982, parent_commit="sha_981_ps1", requirements=NEEDS_REVIEW)
        client = FakeStackGerritClient(
            [base, tip],
            {n: [related_entry(982, parent_commit="sha_981_ps1"), related_entry(981)] for n in (981, 982)},
        )

        def presubmit_passes_and_tip_is_amended() -> None:
            client.changes[981] = _green(981)
            client.changes[982] = stack_change(982, patch_set=2, parent_commit="sha_981_ps1",
                                               requirements=NEEDS_REVIEW)
            related = [related_entry(982, patch_set=2, parent_commit="sha_981_ps1"), related_entry(981)]
            client.related = {981: related, 982: related}

        def everything_merges() -> None:
            client.changes[981] = stack_change(981, status="MERGED")
            client.changes[982] = stack_change(982, patch_set=2, parent_commit="sha_981_ps1", status="MERGED")
            related = [related_entry(982, patch_set=2, parent_commit="sha_981_ps1", status="MERGED"),
                       related_entry(981, status="MERGED")]
            client.related = {981: related, 982: related}

        clock = FakeClock(presubmit_passes_and_tip_is_amended, everything_merges)
        progress: list[str] = []
        result = _watch(client, clock, progress).execute(self._args(cl="982", poll_interval=POLL), self._ctx())

        self.assertTrue(result.ok)
        report = result.data
        self.assertEqual(report.outcome, OUTCOME_COMPLETE)
        self.assertEqual(report.iterations, 3)
        self.assertEqual(clock.sleeps, [POLL, POLL])
        messages = [(e.kind, e.message) for e in report.events]
        self.assertIn((EVENT_PRESUBMIT, "ag/981 PS1 presubmit: queued -> passed"), messages)
        self.assertIn((EVENT_NEW_PATCH_SET, "ag/982 has a new patch set: PS1 -> PS2"), messages)
        self.assertIn((EVENT_STATUS, "ag/981 is now merged"), messages)
        self.assertIn((EVENT_STATUS, "ag/982 is now merged"), messages)
        self.assertTrue(report.summary.startswith("Every CL is closed after 3 polls: "))
        self.assertTrue(progress[0].startswith(
            "[poll 1] platform/hardware/google/aemu (studio-main): 2 open CLs; first blocker ag/981: "
        ))
        self.assertIn("  status: ag/981 is now merged", progress)

    def test_repeated_transitions_are_all_logged(self) -> None:
        client = FakeStackGerritClient([stack_change(983, requirements=NEEDS_REVIEW)], {})

        def toggle() -> None:
            client.mergeable[983] = {"mergeable": True} if 983 in client.mergeable else CONFLICT
            if client.mergeable[983] == {"mergeable": True}:
                del client.mergeable[983]

        clock = FakeClock(toggle, toggle, toggle)
        result = _watch(client, clock, []).execute(
            self._args(cl="983", timeout=3 * POLL, poll_interval=POLL, rebase=False), self._ctx()
        )
        mergeable = [e.message for e in result.data.events if e.kind == EVENT_MERGEABLE]
        self.assertEqual(mergeable, [
            "ag/983 mergeable: true -> conflict",
            "ag/983 mergeable: conflict -> true",
            "ag/983 mergeable: true -> conflict",
        ])

    def test_exit_on_attention_stops_at_the_first_failed_presubmit(self) -> None:
        clock = FakeClock()
        result = _watch(FakeStackGerritClient([_failed_presubmit(985)], {}), clock, []).execute(
            self._args(cl="985", exit_on_attention=True, rebase=False), self._ctx()
        )
        self.assertEqual(result.exit_code, int(ExitCode.FAILURE))
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(result.data.attention, (
            "ag/985: Presubmit failed (PV -1) — Fix the failing targets: "
            "https://android-build.corp.google.com/workplan/L42",
        ))

    def test_attention_reuses_status_blockers_for_conflicts_vetoes_and_abandoned_parents(self) -> None:
        parent = stack_change(1001, status="ABANDONED")
        child = stack_change(1002, parent_commit="sha_1001_ps1", votes=[(DENALEX, -2)], requirements=NEEDS_REVIEW)
        related = [related_entry(1002, parent_commit="sha_1001_ps1"), related_entry(1001, status="ABANDONED")]
        client = FakeStackGerritClient(
            [parent, child], {1002: related}, {1002: {"mergeable": False, "conflicts": ["a.cc"]}}
        )
        result = _watch(client).execute(self._args(cl="1002", once=True, rebase=False), self._ctx())
        self.assertEqual(result.data.attention, (
            "ag/1002: Parent ag/1001 is abandoned — Rebase onto studio-main or restore ag/1001",
            "ag/1002: Does not merge cleanly into studio-main in a.cc — "
            "Rebase onto studio-main and resolve the conflicts",
            "ag/1002: Code-Review -2 from corakwue@ — Address the -2 from corakwue@",
        ))
        rendered = render_watch_report(result.data)
        self.assertIn("Needs attention:\n  - ag/1002: Parent ag/1001 is abandoned", rendered)

    def test_transient_poll_failure_keeps_the_last_state_and_is_not_attention(self) -> None:
        client = FakeStackGerritClient([stack_change(990, requirements=NEEDS_REVIEW, extra={"topic": TOPIC})], {})

        def fail() -> None:
            client.fail_with = RuntimeError("gob-curl exit code 1")

        def recover() -> None:
            client.fail_with = None

        progress: list[str] = []
        result = _watch(client, FakeClock(fail, recover), progress).execute(
            self._args(topic=TOPIC, timeout=2 * POLL, poll_interval=POLL, rebase=False, exit_on_attention=True),
            self._ctx(),
        )

        self.assertEqual(result.data.iterations, 3)
        self.assertEqual(client.queries.count(f'topic:"{TOPIC}"'), 2)
        failure = result.data.events[0]
        self.assertEqual(failure.kind, EVENT_POLL_FAILED)
        self.assertFalse(failure.attention)
        self.assertEqual(failure.message, "Poll 2 failed; showing the state from poll 1: gob-curl exit code 1")
        self.assertEqual(result.data.outcome, OUTCOME_TIMEOUT)
        self.assertIn("  poll_failed: " + failure.message, progress)

    def test_repeated_poll_failures_stop_with_a_report(self) -> None:
        client = FakeStackGerritClient([stack_change(991, requirements=NEEDS_REVIEW)], {})

        def fail() -> None:
            client.fail_with = UsageError("No Gerrit change matches '991'.", remediation="Check the CL number.")

        result = _watch(client, FakeClock(fail), []).execute(
            self._args(cl="991", poll_interval=POLL, rebase=False), self._ctx()
        )

        report = result.data
        self.assertEqual(report.outcome, OUTCOME_ABORTED)
        self.assertEqual(result.exit_code, int(ExitCode.FAILURE))
        self.assertEqual(report.iterations, 4)
        self.assertEqual(report.note, "Stopped after 3 failed polls in a row: No Gerrit change matches '991'.")
        self.assertEqual(
            report.next_step,
            "Check the CL number. Run `gcert`, check that the CLs still exist, and start the watch again.",
        )
        self.assertEqual(
            [e.message for e in report.events][-1],
            "Poll 4 failed; showing the state from poll 1: No Gerrit change matches '991'.",
        )
        self.assertIn("Stopped after 3 failed polls in a row", render_watch_report(report))
        self.assertTrue(client.closed)

    def test_ctrl_c_returns_the_report_so_far(self) -> None:
        client = FakeStackGerritClient([stack_change(992, requirements=NEEDS_REVIEW)], {})

        def interrupt() -> None:
            raise KeyboardInterrupt

        result = _watch(client, FakeClock(interrupt), []).execute(
            self._args(cl="992", poll_interval=POLL, rebase=False), self._ctx()
        )
        self.assertEqual(result.data.outcome, OUTCOME_INTERRUPTED)
        self.assertEqual(result.exit_code, int(ExitCode.INTERRUPTED))
        self.assertEqual(result.data.iterations, 1)
        self.assertIn("pings already sent are not repeated", result.data.next_step)

    def test_first_poll_failure_is_an_execution_error(self) -> None:
        client = FakeStackGerritClient([], {})
        client.fail_with = RuntimeError("network unreachable")
        with self.assertRaises(ExecutionError) as err:
            _watch(client).execute(self._args(cl="42", once=True, rebase=False), self._ctx())
        self.assertIn("Failed to watch the stack for '42'", err.exception.message)
        self.assertTrue(client.closed)

        with self.assertRaises(UsageError) as usage:
            _watch(FakeStackGerritClient([], {})).execute(self._args(cl="999", once=True, rebase=False), self._ctx())
        self.assertIn("No Gerrit change matches '999'", usage.exception.message)

    def test_later_polls_follow_the_cl_number_not_cherry_picks_of_its_change_id(self) -> None:
        git = FakeGitService()
        git.seed_commit("sha_995_ps1", f"feat: Local\n\nChange-Id: {change_id(995)}\n")
        client = FakeStackGerritClient([stack_change(995, requirements=NEEDS_REVIEW)], {})

        def cherry_pick_appears() -> None:
            client.changes[996] = stack_change(
                996, requirements=NEEDS_REVIEW, extra={"change_id": change_id(995), "branch": "emu-37-release"}
            )

        result = _watch(client, FakeClock(cherry_pick_appears), []).execute(
            self._args(timeout=POLL, poll_interval=POLL, rebase=False), self._ctx(git)
        )
        self.assertEqual(client.queries, [f"change:{change_id(995)}", "change:995"])
        self.assertEqual([c.number for c in result.data.stacks[0].changes], [995])
        self.assertEqual(result.data.query["mode"], "head")

    def test_progress_goes_to_stderr_by_default_and_is_silent_in_json_mode(self) -> None:
        client = FakeStackGerritClient([stack_change(994, status="MERGED")], {})
        command = StackWatchCommand(gerrit_client_factory=lambda ctx, host: client)
        stderr = StringIO()
        with redirect_stderr(stderr):
            result = command.execute(self._args(cl="994", rebase=False), self._ctx())
            json_result = command.execute(
                self._args(cl="994", rebase=False), ExecutionContext.for_testing(self.tmp, json_mode=True)
            )
        self.assertEqual(result.data.outcome, OUTCOME_COMPLETE)
        self.assertIsNone(json_result.markdown_table)
        self.assertEqual(
            stderr.getvalue(),
            "[poll 1] platform/hardware/google/aemu (studio-main): All 1 CL in the stack are closed.\n",
        )


class StackWatchValidationTest(WatchTestCase):
    """Rejects arguments that cannot work before talking to Gerrit."""

    def test_invalid_arguments_raise_usage_errors(self) -> None:
        ctx = self._ctx()
        command = StackWatchCommand()
        cases = [
            (self._args(cl="1", topic=TOPIC), "Conflicting stack selectors"),
            (self._args(poll_interval=5), "--poll-interval 5 is too short"),
            (self._args(timeout=-1), "--timeout -1 is negative"),
            (self._args(ping=("groupagent-android-emulator",)), "Invalid --ping value 'groupagent-android-emulator'"),
        ]
        for args, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(UsageError) as err:
                command.validate(args, ctx)
            self.assertIn(expected, err.exception.message)

        not_repo = self._ctx(FakeGitService(inside_work_tree=False))
        with self.assertRaises(UsageError) as err:
            command.validate(self._args(cl="1"), not_repo)
        self.assertIn("pass --no-rebase to only monitor", err.exception.remediation)
        self.assertEqual(command.validate(self._args(cl="1", rebase=False), not_repo).cl, "1")


class StackWatchRenderTest(unittest.TestCase):
    """Keeps long event logs readable."""

    def test_render_truncates_long_event_logs(self) -> None:
        events = tuple(
            WatchEvent(i, EVENT_PRESUBMIT, f"event {i}", number=i) for i in range(1, MAX_RENDERED_EVENTS + 6)
        )
        report = StackWatchReport(
            host="https://example.com", query={}, once=False, outcome=OUTCOME_IN_PROGRESS,
            iterations=25, elapsed_sec=1500.0, settings={}, stacks=(), events=events, attention=(),
        )
        rendered = render_watch_report(report)
        self.assertIn("Stack watch: Still in flight (25 poll(s) in 1500s)", rendered)
        self.assertIn(f"Events (last {MAX_RENDERED_EVENTS} of {len(events)}; see --json for all):", rendered)
        self.assertNotIn("event 5\n", rendered)
        self.assertIn("[poll 25] presubmit: event 25", rendered)
        self.assertNotIn("Next step:", rendered)
        self.assertEqual(WatchEvent(1, EVENT_STATUS, "x").to_dict()["number"], None)


class StackWatchCliTest(WatchTestCase):
    """Parses the real CLI and serves GerritClient's REST calls from FakeProcessRunner."""

    def _dispatch(self, change: dict[str, Any], *flags: str) -> tuple[int, StringIO, StringIO, ExecutionContext]:
        def gerrit_rest(call: FakeProcessCall) -> ProcessResult:
            url = call.args[-1]
            if "/related" in url:
                body: Any = {}
            elif "/mergeable" in url:
                body = {"mergeable": True}
            else:
                body = [change]
            return ProcessResult(args=call.args, returncode=0, stdout=")]}'\n" + json.dumps(body))

        ctx = ExecutionContext.for_testing(self.tmp, json_mode=True)
        gob_curl = shutil.which("gob-curl") or "/usr/bin/gob-curl"
        ctx.fake_process_runner.register_prefix([gob_curl, "-s", "-f", "-S", "--compressed", "-X", "GET"], gerrit_rest)
        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        parser.add_argument("--json", action="store_true")
        register_parser(parser.add_subparsers(dest="subcommand"))
        args = parser.parse_args([
            "gerrit", "stack", "watch", str(change["_number"]), "--once", "--no-rebase",
            "--state-file", self.state_file, "--json", *flags,
        ])
        stdout, stderr = StringIO(), StringIO()
        exit_code = CliDispatcher(renderer=OutputRenderer(stdout=stdout, stderr=stderr)).dispatch(args, ctx=ctx)
        return exit_code, stdout, stderr, ctx

    def test_json_cli_run_once_prints_the_report_on_stdout(self) -> None:
        exit_code, stdout, stderr, ctx = self._dispatch(_green(993), "--ping", "joshuaduong")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "gerrit stack watch")
        self.assertEqual(payload["mode"], "once")
        self.assertEqual(payload["outcome"], OUTCOME_IN_PROGRESS)
        self.assertEqual(payload["stacks"][0]["changes"][0]["number"], 993)
        self.assertEqual(payload["events"][0]["kind"], EVENT_PINGED)
        self.assertFalse(payload["settings"]["rebase"])
        self.assertNotIn("markdown_table", payload)
        self.assertEqual(ctx.fake_chat_service.sent_dms[0].recipient, JOSHUA)

    def test_json_cli_attention_exits_1_with_the_report_on_stderr(self) -> None:
        exit_code, stdout, stderr, _ = self._dispatch(_failed_presubmit(997))

        self.assertEqual(exit_code, int(ExitCode.FAILURE))
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["outcome"], OUTCOME_ATTENTION)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("Fix the items listed under 'Needs attention'", payload["next_step"])


if __name__ == "__main__":
    unittest.main()
