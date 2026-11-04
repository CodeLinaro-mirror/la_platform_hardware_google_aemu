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

"""Hermetic unit tests for `emu-dev-cli gerrit stack sync` (zero @patch)."""

import argparse
from io import StringIO
import json
import os
import shutil
import sys
from typing import Any
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.gerrit import register_parser
from commands.gerrit.stack import (
    StackSyncArgs,
    StackSyncCommand,
    StackSyncReport,
)
from commands.gerrit.stack.sync import (
    SYNC_ACTION_ABANDONED,
    SYNC_ACTION_BLOCKED,
    SYNC_ACTION_CONFLICT,
    SYNC_ACTION_MERGED,
    SYNC_ACTION_REBASED,
    SYNC_ACTION_UP_TO_DATE,
    SYNC_ACTION_WOULD_REBASE,
    _ensure_change_id_footer,
    render_stack_sync_report,
)
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import ExecutionError, UsageError
from core.git import FakeGitService
from core.process import FakeProcessCall, ProcessResult
from testlib.gerrit_fakes import (
    FakeStackGerritClient,
    change_id,
    related_entry,
    stack_change,
)

TOPIC = "emu-dev-cli-gerrit-stack-phase2"
UNICODE_PATH = "src/commands/gerrit/stack/测试_🚀_é.py"


def _sync_change(
    number: int,
    *,
    parent_commit: str = "base_sha",
    patch_set: int = 1,
    status: str = "NEW",
    topic: str = TOPIC,
    files: list[str] | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    change = stack_change(
        number,
        parent_commit=parent_commit,
        patch_set=patch_set,
        status=status,
        subject=subject or f"feat(gerrit): Stack change {number} 测试_🚀_é",
        extra={"topic": topic} if topic else {},
    )
    rev = change["current_revision"]
    if files is not None:
        change["revisions"][rev]["files"] = {
            "/COMMIT_MSG": {"lines_inserted": 10},
            "/MERGE_LIST": {"lines_inserted": 1},
            **{path: {"lines_inserted": 5} for path in files},
        }
    return change


def _command(client: FakeStackGerritClient) -> StackSyncCommand:
    return StackSyncCommand(gerrit_client_factory=lambda ctx, host: client)


class StackSyncCommandTest(unittest.TestCase):
    """Tests collision detection, dry-run validation, rebase execution, and push."""

    def _seed_two_cl_stack_repo(self) -> tuple[FakeGitService, FakeStackGerritClient]:
        git = FakeGitService(current_branch_name="feat-stack")
        git.seed_commit("base_sha", "chore: Initial base\n")
        git.seed_commit(
            "up_sha_1",
            "feat: Upstream change 1\n",
            parents=("base_sha",),
            changed_files=("src/common.py", UNICODE_PATH),
        )
        git.seed_commit(
            "up_sha_2",
            "feat: Upstream change 2\n",
            parents=("up_sha_1",),
            changed_files=("docs/playbooks/git_and_gerrit.md",),
        )
        git.refs["aosp/studio-main"] = "up_sha_2"

        git.seed_commit(
            "sha_801_ps1",
            f"feat(gerrit): Part 1\n\nChange-Id: {change_id(801)}\n",
            parents=("base_sha",),
            changed_files=(UNICODE_PATH, "src/part1.py"),
        )
        git.seed_commit(
            "sha_802_ps1",
            f"feat(gerrit): Part 2\n\nChange-Id: {change_id(802)}\n",
            parents=("sha_801_ps1",),
            changed_files=("src/part2.py",),
        )
        git.refs["feat-stack"] = "sha_802_ps1"

        c801 = _sync_change(
            801,
            parent_commit="base_sha",
            files=[UNICODE_PATH, "src/part1.py"],
            subject="feat(gerrit): Part 1",
        )
        c802 = _sync_change(
            802,
            parent_commit="sha_801_ps1",
            files=["src/part2.py"],
            subject="feat(gerrit): Part 2",
        )
        related = [
            related_entry(802, parent_commit="sha_801_ps1"),
            related_entry(801, parent_commit="base_sha"),
        ]
        client = FakeStackGerritClient(
            [c801, c802],
            {801: related, 802: related},
        )
        return git, client

    def test_clean_two_cl_stack_rebases_and_pushes_with_notify_owner(self) -> None:
        git, client = self._seed_two_cl_stack_repo()
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-clean", services={"git_service": git})

        result = _command(client).execute(
            StackSyncArgs(cl="HEAD", repo="/fake/repo", presubmit=True),
            ctx,
        )

        self.assertTrue(result.ok)
        self.assertTrue(client.closed)
        report = result.data
        self.assertIsInstance(report, StackSyncReport)
        self.assertTrue(report.pushed)
        self.assertFalse(report.has_conflicts)
        self.assertEqual(len(report), 6)
        self.assertEqual(report["host"], "https://googleplex-android-review.git.corp.google.com")
        self.assertIn("stacks", list(report))

        stack = report.stacks[0]
        self.assertEqual(stack.rebased_count, 2)
        self.assertTrue(stack.pushed)
        self.assertEqual(len(stack.upstream_commits), 2)
        self.assertEqual(stack.upstream_commits[0].sha, "up_sha_2")
        self.assertEqual(stack.upstream_commits[1].sha, "up_sha_1")

        # Verify collision detection pinpointed UNICODE_PATH modified in up_sha_1 and ag/801.
        self.assertEqual(len(stack.collisions), 1)
        collision = stack.collisions[0]
        self.assertEqual(collision.path, UNICODE_PATH)
        self.assertEqual(collision.cl_numbers, (801,))
        self.assertEqual(collision.upstream_commits, ("up_sha_1",))

        first, second = stack.changes
        self.assertEqual(first.action, SYNC_ACTION_REBASED)
        self.assertTrue(first.pushed)
        self.assertEqual(first.old_parent, "base_sha")
        self.assertEqual(first.new_parent, "up_sha_2")
        self.assertNotEqual(first.new_revision, first.old_revision)

        self.assertEqual(second.action, SYNC_ACTION_REBASED)
        self.assertTrue(second.pushed)
        self.assertEqual(second.old_parent, "sha_801_ps1")
        self.assertEqual(second.new_parent, first.new_revision)

        # Verify local branch was updated to the new tip commit.
        self.assertEqual(git.refs["feat-stack"], second.new_revision)
        self.assertEqual(git.config_values.get("push.negotiate"), "false")

        # Verify Gerrit push options include notify=OWNER, topic, and Presubmit-Ready+1.
        self.assertEqual(len(git.pushed_refs), 1)
        remote, refspec, push_opts = git.pushed_refs[0]
        self.assertEqual(remote, "aosp")
        self.assertEqual(refspec, f"{second.new_revision}:refs/for/studio-main")
        self.assertIn("notify=OWNER", push_opts)
        self.assertIn("nokeycheck", push_opts)
        self.assertIn(f"topic={TOPIC}", push_opts)
        self.assertIn("l=Presubmit-Ready+1", push_opts)

        rendered = result.markdown_table or ""
        self.assertIn("collisions: 1 file overlap(s) with aosp/studio-main", rendered)
        self.assertIn(UNICODE_PATH, rendered)
        self.assertIn("[pushed notify=OWNER]", rendered)
        json.dumps(report.to_dict(), ensure_ascii=False)

    def test_already_up_to_date_stack_does_not_rebase_or_push(self) -> None:
        git = FakeGitService(current_branch_name="feat-up-to-date")
        git.seed_commit("base_sha", "chore: Base\n")
        git.refs["aosp/studio-main"] = "base_sha"
        git.seed_commit(
            "sha_810_ps1",
            f"feat: Up to date\n\nChange-Id: {change_id(810)}\n",
            parents=("base_sha",),
        )
        c810 = _sync_change(810, parent_commit="base_sha", files=["src/clean.py"])
        client = FakeStackGerritClient([c810], {})
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-noop", services={"git_service": git})

        result = _command(client).execute(StackSyncArgs(cl="810", repo="/fake/repo"), ctx)

        self.assertTrue(result.ok)
        stack = result.data.stacks[0]
        self.assertEqual(stack.rebased_count, 0)
        self.assertFalse(stack.pushed)
        self.assertEqual(stack.changes[0].action, SYNC_ACTION_UP_TO_DATE)
        self.assertEqual(git.pushed_refs, [])
        self.assertIn("All open CLs are already up to date", stack.summary())

    def test_dry_run_validates_clean_and_conflicting_stacks_without_mutating_repo(self) -> None:
        git, client = self._seed_two_cl_stack_repo()
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-dry", services={"git_service": git})

        clean_res = _command(client).execute(
            StackSyncArgs(cl="802", repo="/fake/repo", dry_run=True),
            ctx,
        )
        self.assertTrue(clean_res.ok)
        self.assertTrue(clean_res.data.dry_run)
        self.assertEqual(
            [c.action for c in clean_res.data.stacks[0].changes],
            [SYNC_ACTION_WOULD_REBASE, SYNC_ACTION_WOULD_REBASE],
        )
        self.assertEqual(git.pushed_refs, [])
        self.assertIn("Dry-run: 2 CL(s) cleanly rebase", clean_res.summary)

        # Now inject a merge-tree conflict on the base CL 801 and verify child 802 is marked blocked.
        git.merge_tree_conflicts.add(("up_sha_2", "sha_801_ps1"))
        conflict_res = _command(client).execute(
            StackSyncArgs(cl="802", repo="/fake/repo", dry_run=True),
            ctx,
        )
        self.assertFalse(conflict_res.ok)
        self.assertEqual(conflict_res.exit_code, 1)
        first, second = conflict_res.data.stacks[0].changes
        self.assertEqual(first.action, SYNC_ACTION_CONFLICT)
        self.assertIn("fake_file.py", first.conflicts)
        self.assertEqual(second.action, SYNC_ACTION_BLOCKED)
        self.assertIn("Blocked by conflict in parent ag/801", second.note)

    def test_dry_run_detects_gerrit_mergeable_conflict_when_merge_tree_passes(self) -> None:
        git, _ = self._seed_two_cl_stack_repo()
        c801 = _sync_change(801, parent_commit="base_sha", files=[UNICODE_PATH])
        client = FakeStackGerritClient(
            [c801],
            {},
            mergeable={801: {"mergeable": False, "conflicts": [UNICODE_PATH]}},
        )
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-gerrit-conflict", services={"git_service": git})
        res = _command(client).execute(
            StackSyncArgs(cl="801", repo="/fake/repo", dry_run=True),
            ctx,
        )
        self.assertFalse(res.ok)
        change = res.data.stacks[0].changes[0]
        self.assertEqual(change.action, SYNC_ACTION_CONFLICT)
        self.assertEqual(change.conflicts, (UNICODE_PATH,))
        self.assertIn("Gerrit MergeableInfo reports merge conflict", change.note)

    def test_rebase_conflict_aborts_and_restores_initial_branch_and_detached_head(self) -> None:
        git, client = self._seed_two_cl_stack_repo()
        git.rebase_conflicts.add("sha_801_ps1")
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-abort", services={"git_service": git})

        result = _command(client).execute(
            StackSyncArgs(cl="HEAD", repo="/fake/repo"),
            ctx,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        first, second = result.data.stacks[0].changes
        self.assertEqual(first.action, SYNC_ACTION_CONFLICT)
        self.assertIn("fake_conflict.py", first.conflicts)
        self.assertEqual(second.action, SYNC_ACTION_BLOCKED)
        self.assertIn(("git", "rebase", "--abort"), git.recorded_commands)
        self.assertEqual(git.refs["feat-stack"], "sha_802_ps1")
        self.assertEqual(git.pushed_refs, [])

        # Also test detached HEAD restoration on rebase conflict.
        git.current_branch_name = ""
        detached_res = _command(client).execute(
            StackSyncArgs(cl="802", repo="/fake/repo"),
            ctx,
        )
        self.assertFalse(detached_res.ok)
        self.assertIn(("git", "reset", "--hard", "sha_802_ps1"), git.recorded_commands)

    def test_merged_and_abandoned_parents_are_skipped_while_open_child_rebases(self) -> None:
        git = FakeGitService(current_branch_name="feat-child")
        git.seed_commit("base_sha", "chore: Base\n")
        git.seed_commit("sha_830_ps1", "feat: Merged parent\n", parents=("base_sha",))
        git.seed_commit("sha_831_ps1", "feat: Abandoned parent\n", parents=("sha_830_ps1",))
        git.seed_commit(
            "sha_832_ps1",
            f"feat: Open child\n\nChange-Id: {change_id(832)}\n",
            parents=("sha_831_ps1",),
            changed_files=("src/child.py",),
        )
        git.refs["aosp/studio-main"] = "sha_830_ps1"

        c830 = _sync_change(830, parent_commit="base_sha", status="MERGED")
        c831 = _sync_change(831, parent_commit="sha_830_ps1", status="ABANDONED")
        c832 = _sync_change(832, parent_commit="sha_831_ps1", status="NEW", files=None)
        related = [
            related_entry(832, parent_commit="sha_831_ps1", status="NEW"),
            related_entry(831, parent_commit="sha_830_ps1", status="ABANDONED"),
            related_entry(830, parent_commit="base_sha", status="MERGED"),
        ]
        client = FakeStackGerritClient([c830, c831, c832], {832: related})
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-closed-parents", services={"git_service": git})

        result = _command(client).execute(
            StackSyncArgs(cl="832", repo="/fake/repo", no_push=True),
            ctx,
        )
        self.assertTrue(result.ok)
        changes = result.data.stacks[0].changes
        self.assertEqual(
            [c.action for c in changes],
            [SYNC_ACTION_MERGED, SYNC_ACTION_ABANDONED, SYNC_ACTION_REBASED],
        )
        self.assertEqual(changes[2].new_parent, "sha_830_ps1")
        self.assertFalse(result.data.pushed)

    def test_fetches_missing_revision_and_restores_missing_change_id_footer(self) -> None:
        git = FakeGitService(current_branch_name="studio-main")
        git.seed_commit("base_sha", "chore: Base\n")
        git.seed_commit("target_sha", "feat: Target\n", parents=("base_sha",))
        git.refs["aosp/studio-main"] = "target_sha"

        c841 = _sync_change(841, parent_commit="base_sha", files=["src/fetched.py"])
        client = FakeStackGerritClient([c841], {})
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-fetch", services={"git_service": git})

        # With --no-fetch, missing local revision raises ExecutionError.
        with self.assertRaises(ExecutionError) as err:
            _command(client).execute(
                StackSyncArgs(cl="841", repo="/fake/repo", no_fetch=True),
                ctx,
            )
        self.assertIn("is not present in '/fake/repo' and --no-fetch was set", err.exception.message)

        # When fetch succeeds and commit lacks a Change-Id footer, _preserve_change_id amends it.
        git.seed_commit(
            "sha_841_ps1",
            "feat: Commit without footer\n",
            parents=("base_sha",),
            update_head=False,
        )
        res = _command(client).execute(
            StackSyncArgs(cl="841", repo="/fake/repo", no_push=True),
            ctx,
        )
        self.assertTrue(res.ok)
        self.assertIn(f"Change-Id: {change_id(841)}", git.commit_message("HEAD"))
        self.assertEqual(
            _ensure_change_id_footer(
                f"feat: Test\n\nChange-Id: {change_id(841)}\n", change_id(841)
            ),
            f"feat: Test\n\nChange-Id: {change_id(841)}\n",
        )

    def test_fetch_failure_and_push_failure_raise_actionable_execution_errors(self) -> None:
        git = FakeGitService(current_branch_name="studio-main")
        git.seed_commit("base_sha", "chore: Base\n")
        git.seed_commit("target_sha", "feat: Target\n", parents=("base_sha",))
        git.refs["aosp/studio-main"] = "target_sha"
        c842 = _sync_change(842, parent_commit="base_sha")
        client = FakeStackGerritClient([c842], {})
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-fail", services={"git_service": git})

        # Simulate git fetch failure for refs/changes/42/842/1
        git.process_runner.register_prefix(
            ["git", "fetch", "aosp", "refs/changes/42/842/1"],
            ProcessResult(
                args=("git", "fetch"),
                returncode=128,
                stderr="fatal: couldn't find remote ref refs/changes/42/842/1\n",
            ),
        )
        with self.assertRaises(ExecutionError) as fetch_err:
            _command(client).execute(StackSyncArgs(cl="842", repo="/fake/repo"), ctx)
        self.assertIn("Failed to fetch ag/842 PS1", fetch_err.exception.message)

        # Clear prefix override, seed commit, and simulate push failure.
        git.process_runner._prefix_rules.clear()
        git.seed_commit(
            "sha_842_ps1",
            f"feat: Push fail\n\nChange-Id: {change_id(842)}\n",
            parents=("base_sha",),
        )
        git.push_returncode = 1
        git.push_stderr = "remote: Processing changes: refs: 1, done\nTo aosp\n ! [remote rejected] HEAD -> refs/for/studio-main (prohibited by Gerrit)\n"
        with self.assertRaises(ExecutionError) as push_err:
            _command(client).execute(StackSyncArgs(cl="842", repo="/fake/repo"), ctx)
        self.assertIn("prohibited by Gerrit", push_err.exception.message)

    def test_selector_modes_and_validation_errors(self) -> None:
        git, client = self._seed_two_cl_stack_repo()
        git.remotes_list = ["custom-remote"]
        git.refs["custom-remote/studio-main"] = "up_sha_2"
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-modes", services={"git_service": git})

        # 1. --topic mode
        topic_res = _command(client).execute(
            StackSyncArgs(topic=TOPIC, repo="/fake/repo", dry_run=True),
            ctx,
        )
        self.assertTrue(topic_res.ok)
        self.assertEqual(topic_res.data.query["mode"], "topic")

        # 2. --branch mode
        branch_res = _command(client).execute(
            StackSyncArgs(branch="feat-stack", repo="/fake/repo", dry_run=True),
            ctx,
        )
        self.assertTrue(branch_res.ok)
        self.assertEqual(branch_res.data.query["mode"], "branch")

        # 3. Positional branch name fallback when cl is not a numeric CL or Change-Id
        pos_branch_res = _command(client).execute(
            StackSyncArgs(cl="feat-stack", repo="/fake/repo", dry_run=True, remote="custom-remote"),
            ctx,
        )
        self.assertTrue(pos_branch_res.ok)
        self.assertEqual(pos_branch_res.data.query["mode"], "branch")

        # 4. Validation errors: conflicting selectors, not a git repo, dirty working tree, missing topic, bad ref
        with self.assertRaises(UsageError) as err:
            _command(client).execute(
                StackSyncArgs(cl="801", topic=TOPIC, repo="/fake/repo"), ctx
            )
        self.assertIn("Conflicting stack selectors", err.exception.message)

        git.set_dirty(" M src/dirty.py\n")
        with self.assertRaises(UsageError) as err:
            _command(client).execute(StackSyncArgs(cl="801", repo="/fake/repo"), ctx)
        self.assertIn("has uncommitted modifications", err.exception.message)
        git.set_clean()

        with self.assertRaises(UsageError) as err:
            _command(client).execute(
                StackSyncArgs(topic="nonexistent-topic", repo="/fake/repo", dry_run=True),
                ctx,
            )
        self.assertIn("No Gerrit changes were found for topic 'nonexistent-topic'", err.exception.message)

        with self.assertRaises(UsageError) as err:
            _command(client).execute(
                StackSyncArgs(cl="unknown-branch-or-cl", repo="/fake/repo", dry_run=True),
                ctx,
            )
        self.assertIn("Could not extract a Gerrit CL identifier", err.exception.message)

        not_repo_ctx = ExecutionContext.for_testing(
            "/tmp/emu-sync-norepo",
            services={"git_service": FakeGitService(inside_work_tree=False)},
        )
        with self.assertRaises(UsageError) as err:
            _command(client).execute(StackSyncArgs(cl="801", repo="/nowhere"), not_repo_ctx)
        self.assertIn("is not inside a Git work tree", err.exception.message)

        # Transport error wrapped in ExecutionError
        client.fail_with = RuntimeError("network timeout")
        with self.assertRaises(ExecutionError) as exec_err:
            _command(client).execute(
                StackSyncArgs(cl="801", repo="/fake/repo", dry_run=True),
                ctx,
            )
        self.assertIn("Failed to sync Gerrit stack for '801'", exec_err.exception.message)

    def test_render_truncation_for_many_commits_and_collisions(self) -> None:
        git = FakeGitService()
        prev = "base_sha"
        git.seed_commit(prev, "chore: Base\n")
        for i in range(1, 8):
            sha = f"up_{i:02d}_sha"
            git.seed_commit(
                sha,
                f"feat: Upstream {i}\n",
                parents=(prev,),
                changed_files=(f"src/file_{i}.py",),
            )
            prev = sha
        git.refs["aosp/studio-main"] = prev
        files = [f"src/file_{i}.py" for i in range(1, 8)]
        c850 = _sync_change(850, parent_commit="base_sha", files=files)
        client = FakeStackGerritClient([c850], {})
        ctx = ExecutionContext.for_testing("/tmp/emu-sync-trunc", services={"git_service": git})

        res = _command(client).execute(
            StackSyncArgs(cl="850", repo="/fake/repo", dry_run=True),
            ctx,
        )
        rendered = render_stack_sync_report(res.data)
        self.assertIn("... and 2 more upstream commit(s)", rendered)
        self.assertIn("... and 2 more colliding file(s)", rendered)


class StackSyncCliTest(unittest.TestCase):
    """Tests CLI parser registration and `--json` dispatch for `gerrit stack sync`."""

    def test_cli_dispatch_json_dry_run(self) -> None:
        c860 = _sync_change(860, parent_commit="base_sha", files=[UNICODE_PATH])

        def gerrit_rest(call: FakeProcessCall) -> ProcessResult:
            url = call.args[-1]
            if "/related" in url:
                body: Any = {}
            elif "/mergeable" in url:
                body = {"mergeable": True}
            else:
                body = [c860]
            return ProcessResult(
                args=call.args, returncode=0, stdout=")]}'\n" + json.dumps(body)
            )

        git = FakeGitService()
        git.seed_commit("base_sha", "chore: Base\n")
        git.refs["aosp/studio-main"] = "base_sha"
        git.seed_commit(
            "sha_860_ps1",
            f"feat: CLI sync\n\nChange-Id: {change_id(860)}\n",
            parents=("base_sha",),
        )
        ctx = ExecutionContext.for_testing(
            "/tmp/emu-sync-cli",
            json_mode=True,
            services={"git_service": git},
        )
        gob_curl = shutil.which("gob-curl") or "/usr/bin/gob-curl"
        ctx.fake_process_runner.register_prefix(
            [gob_curl, "-s", "-f", "-S", "--compressed", "-X", "GET"],
            gerrit_rest,
        )

        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        parser.add_argument("--json", action="store_true")
        register_parser(parser.add_subparsers(dest="subcommand"))
        args = parser.parse_args(
            ["gerrit", "stack", "sync", "860", "--dry-run", "--no-fetch", "--json"]
        )
        stdout = StringIO()
        exit_code = CliDispatcher(
            renderer=OutputRenderer(stdout=stdout, stderr=stdout)
        ).dispatch(args, ctx=ctx)

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "gerrit stack sync")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["stacks"][0]["changes"][0]["number"], 860)


if __name__ == "__main__":
    unittest.main()
