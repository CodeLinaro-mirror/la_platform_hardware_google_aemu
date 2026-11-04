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

"""Hermetic tests for `gerrit stack status` and the Review-Enforcement audit (zero @patch)."""

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
    REQUIRED_TRUSTED_APPROVALS,
    StackStatusArgs,
    StackStatusCommand,
    StackStatusReport,
    audit_review_enforcement,
)
from commands.gerrit.stack.status import (
    MERGEABLE_CONFLICT,
    MERGEABLE_FALSE,
    MERGEABLE_NOT_APPLICABLE,
    MERGEABLE_TRUE,
    MERGEABLE_UNKNOWN,
    classify_mergeable,
)
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import ExecutionError, UsageError
from core.git import FakeGitService
from core.process import FakeProcessCall, ProcessResult
from lib.gerrit_api import GerritClient
from testlib.gerrit_fakes import (
    AGENT,
    DENALEX,
    ERWIN,
    JOSHUA,
    SUBJECT_UNICODE,
    FakeStackGerritClient,
    change_id,
    related_entry,
    stack_change,
    submit_requirements,
)


def _command(client: FakeStackGerritClient) -> StackStatusCommand:
    return StackStatusCommand(gerrit_client_factory=lambda ctx, host: client)


def _presubmit_messages(patch_set: int, workplan: str, summary: str) -> list[dict[str, Any]]:
    return [
        {"_revision_number": patch_set, "message": f"Patch Set {patch_set}:\n\n## Presubmit started: {workplan}"},
        {"_revision_number": patch_set, "message": f"Patch Set {patch_set}: Presubmit-Verified+2\n\n## Presubmit finished: {summary}"},
    ]


class ReviewEnforcementAuditTest(unittest.TestCase):
    """Counts trusted approvals the way go/gerrit-onboarding#review-enforcement does."""

    def test_agent_upload_with_one_human_plus_two_needs_one_more_from_pending_reviewers(self) -> None:
        audit = audit_review_enforcement(stack_change(
            1, votes=[(ERWIN, 2), (AGENT, 1)],
            requirements=submit_requirements(review_enforcement="UNSATISFIED", code_review="SATISFIED"),
        ))
        self.assertFalse(audit.uploader_counts)
        self.assertEqual(audit.approvers, (ERWIN,))
        self.assertEqual((audit.approvals, audit.missing_approvals), (1, 1))
        self.assertEqual(audit.pending_reviewers, (JOSHUA, DENALEX))
        self.assertFalse(audit.satisfied)
        self.assertTrue(audit.code_review_satisfied)
        self.assertEqual(audit.describe_missing(), "needs 1 more trusted human approval from joshuaduong@ or corakwue@")
        self.assertIn("1/2 approvals (jansene@)", audit.describe())

    def test_two_human_votes_satisfy_agent_upload(self) -> None:
        audit = audit_review_enforcement(stack_change(
            2, votes=[(ERWIN, 2), (JOSHUA, 1)],
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
        ))
        self.assertEqual(audit.approvals, REQUIRED_TRUSTED_APPROVALS)
        self.assertTrue(audit.satisfied)
        self.assertEqual(audit.to_dict()["approvers"], [ERWIN, JOSHUA])
        self.assertTrue(audit.describe().startswith("SATISFIED: 2/2 approvals"))

    def test_human_uploader_counts_once_and_own_vote_is_not_double_counted(self) -> None:
        audit = audit_review_enforcement(stack_change(
            3, uploader=ERWIN, votes=[(ERWIN, 2), (JOSHUA, 1)], reviewers=(ERWIN, JOSHUA),
        ))
        self.assertTrue(audit.uploader_counts)
        self.assertEqual(audit.approvers, (JOSHUA,))
        self.assertEqual(audit.approvals, 2)
        self.assertTrue(audit.satisfied)
        # Without submit requirement data the +2 must come from a non-uploader.
        self.assertFalse(audit.has_non_uploader_max)
        self.assertFalse(audit.code_review_satisfied)
        self.assertEqual(audit.requirement_status, "UNKNOWN")

    def test_gerrit_rejection_despite_two_counted_approvals_flags_untrusted_vote(self) -> None:
        audit = audit_review_enforcement(stack_change(
            4, votes=[(ERWIN, 2), (JOSHUA, 2)],
            requirements=submit_requirements(review_enforcement="UNSATISFIED"),
        ))
        self.assertTrue(audit.untrusted_approval_suspected)
        self.assertIn("Gerrit does not trust one of them", audit.describe())

    def test_minus_two_blocks_and_bots_never_count(self) -> None:
        audit = audit_review_enforcement(stack_change(
            5, votes=[(DENALEX, -2), ("ayeaye-gerrit@google.com", 1), (AGENT, 1)],
            reviewers=("ayeaye-gerrit@google.com", DENALEX),
        ))
        self.assertEqual(audit.blocking_voters, (DENALEX,))
        self.assertEqual(audit.approvers, ())
        self.assertEqual(audit.pending_reviewers, (DENALEX,))
        self.assertFalse(audit.satisfied)

    def test_no_human_reviewer_left_asks_to_add_one(self) -> None:
        audit = audit_review_enforcement(stack_change(6, uploader=ERWIN, reviewers=(ERWIN,)))
        self.assertEqual(audit.describe_missing(), "needs 1 more trusted human approval; add a reviewer")

    def test_two_plus_ones_ask_existing_voters_to_raise_to_plus_two(self) -> None:
        client = FakeStackGerritClient([stack_change(
            7, votes=[(ERWIN, 1), (JOSHUA, 1)], labels={"Presubmit-Verified": 1},
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="UNSATISFIED"),
        )], {})
        change = _command(client).execute(
            StackStatusArgs(cl="7"), ExecutionContext.for_testing("/tmp/emu-stack-plus-one")
        ).data.stacks[0].changes[0]
        self.assertEqual(change.review.plus_one_approvers, (ERWIN, JOSHUA))
        self.assertEqual(change.presubmit.status, "warning")
        self.assertEqual([b.reason for b in change.blockers], [
            "No Code-Review +2 from someone other than the uploader",
        ])
        self.assertEqual(change.next_action, "Ask jansene@ or joshuaduong@ or corakwue@ for Code-Review +2")


class ClassifyMergeableTest(unittest.TestCase):
    def test_maps_mergeable_info_to_tri_state(self) -> None:
        self.assertEqual(classify_mergeable({"mergeable": True}), (MERGEABLE_TRUE, ()))
        self.assertEqual(classify_mergeable({"mergeable": False}), (MERGEABLE_FALSE, ()))
        self.assertEqual(
            classify_mergeable({"mergeable": False, "conflicts": ["src/测试_🚀_é.py"]}),
            (MERGEABLE_CONFLICT, ("src/测试_🚀_é.py",)),
        )
        self.assertEqual(classify_mergeable({}), (MERGEABLE_UNKNOWN, ()))
        self.assertEqual(classify_mergeable(None), (MERGEABLE_UNKNOWN, ()))


class StackStatusCommandTest(unittest.TestCase):
    """Drives StackStatusCommand through an in-memory Gerrit fake and FakeGitService."""

    def _three_cl_stack(self) -> FakeStackGerritClient:
        base = stack_change(
            101, patch_set=2, votes=[(ERWIN, 2), (JOSHUA, 1)],
            labels={"Presubmit-Verified": 2, "Lint": 1, "Performance": 2, "Autosubmit": 1},
            requirements=submit_requirements(
                review_enforcement="SATISFIED", code_review="SATISFIED", presubmit_verified="SATISFIED",
                lint="SATISFIED", security_review="NOT_APPLICABLE",
            ),
            messages=_presubmit_messages(2, "L111", "27 passed."),
        )
        middle = stack_change(
            102, parent_commit="sha_101_ps1", votes=[(ERWIN, 2)],
            labels={"Presubmit-Verified": 2},
            requirements=submit_requirements(review_enforcement="UNSATISFIED", code_review="SATISFIED"),
        )
        tip = stack_change(
            103, parent_commit="sha_102_ps1", votes=[],
            labels={"Presubmit-Verified": -1},
            requirements=submit_requirements(
                review_enforcement="UNSATISFIED", code_review="UNSATISFIED", code_owners="UNSATISFIED",
            ),
            extra={"unresolved_comment_count": 2, "work_in_progress": True},
        )
        related = [
            related_entry(103, parent_commit="sha_102_ps1"),
            related_entry(102, parent_commit="sha_101_ps1"),
            related_entry(101, patch_set=1, current=2),
        ]
        return FakeStackGerritClient(
            [base, middle, tip],
            {103: related, 101: related, 102: related},
            {103: {"mergeable": False, "conflicts": ["emulator/测试_🚀_é.cc"]}},
        )

    def test_reports_order_patch_sets_mergeability_presubmit_and_blockers(self) -> None:
        client = self._three_cl_stack()
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-status")
        result = _command(client).execute(StackStatusArgs(cl="ag/103"), ctx)

        self.assertTrue(result.ok)
        self.assertTrue(client.closed)
        self.assertEqual(client.queries[0], "change:103")
        self.assertEqual(sorted(client.mergeable_calls), [101, 102, 103])
        report = result.data
        self.assertIsInstance(report, StackStatusReport)
        stack = report.stacks[0]
        self.assertEqual([c.number for c in stack.changes], [101, 102, 103])
        base, middle, tip = stack.changes

        self.assertEqual(base.patch_set, 2)
        self.assertEqual(base.parent, "studio-main@base_sh")
        self.assertEqual(base.mergeable, MERGEABLE_TRUE)
        self.assertEqual(base.presubmit.status, "passed")
        self.assertEqual(base.presubmit.workplan_id, "L111")
        self.assertEqual(base.presubmit.build_url, "https://android-build.corp.google.com/workplan/L111")
        self.assertEqual(base.presubmit.summary, "27 passed")
        self.assertEqual((base.presubmit.lint, base.presubmit.performance), (1, 2))
        self.assertEqual(base.blockers, ())
        self.assertEqual(base.next_action, "Ready; Autosubmit will merge it")

        self.assertEqual(middle.parent, "ag/101 PS1")
        self.assertIn("Based on ag/101 PS1, but its latest is PS2", middle.blockers[0].reason)
        self.assertEqual(middle.next_action, "Rebase onto ag/101 PS2")
        self.assertIn(
            "Review-Enforcement: needs 1 more trusted human approval from joshuaduong@ or corakwue@",
            [b.reason for b in middle.blockers],
        )

        self.assertTrue(tip.is_target)
        self.assertEqual(tip.mergeable, MERGEABLE_CONFLICT)
        self.assertEqual(tip.conflicts, ("emulator/测试_🚀_é.cc",))
        reasons = [b.reason for b in tip.blockers]
        self.assertTrue(reasons[0].startswith("Does not merge cleanly into studio-main in emulator/测试_🚀_é.cc"))
        self.assertIn("Marked work in progress", reasons)
        self.assertIn("Presubmit failed (PV -1)", reasons)
        self.assertIn("2 unresolved comment thread(s)", reasons)
        self.assertIn("Unsatisfied submit requirements: Code-Owners", reasons)
        self.assertEqual(tip.other_unsatisfied, ("Code-Owners",))
        self.assertEqual(stack.summary(), (
            "3 open CLs; first blocker ag/102: Based on ag/101 PS1, but its latest is PS2"
        ))

        payload = report.to_dict()
        self.assertEqual(payload["query"], {"mode": "change", "input": "ag/103", "resolved": "103",
                                            "host": "https://googleplex-android-review.git.corp.google.com"})
        self.assertNotIn("local", payload)
        tip_json = payload["stacks"][0]["changes"][2]
        self.assertEqual(tip_json["mergeable"], "conflict")
        self.assertEqual(tip_json["review_enforcement"]["missing_approvals"], 2)
        self.assertEqual(tip_json["presubmit"]["presubmit_verified"], -1)
        json.dumps(payload, ensure_ascii=False)

        rendered = result.markdown_table or ""
        self.assertIn("3/3 ag/103 * PS1 · NEW · feat(gerrit): Add stack status 测试_🚀_é", rendered)
        self.assertIn("presubmit: passed (PV +2, Lint +1, Performance +2) · 27 passed", rendered)
        self.assertIn("review-enforcement: SATISFIED: 2/2 approvals (jansene@, joshuaduong@)", rendered)
        self.assertIn("blocker: Marked work in progress", rendered)
        self.assertIn("blocker: Does not merge cleanly into studio-main in emulator/测试_🚀_é.cc", rendered)

    def test_all_closed_stack_summary(self) -> None:
        merged = stack_change(250, status="MERGED")
        abandoned = stack_change(251, status="ABANDONED", parent_commit="sha_250_ps1")
        related = [related_entry(251, status="ABANDONED", parent_commit="sha_250_ps1"),
                   related_entry(250, status="MERGED")]
        client = FakeStackGerritClient([merged, abandoned], {250: related})
        result = _command(client).execute(StackStatusArgs(cl="250"), ExecutionContext.for_testing("/tmp/e"))
        self.assertEqual(result.data.stacks[0].summary(), "All 2 CLs in the stack are closed.")
        self.assertEqual([c.next_action for c in result.data.stacks[0].changes], ["Merged", "Abandoned"])
        self.assertEqual(client.mergeable_calls, [])

    def test_abandoned_parent_blocks_child(self) -> None:
        parent = stack_change(260, status="ABANDONED")
        child = stack_change(261, parent_commit="sha_260_ps1", votes=[(ERWIN, 2), (JOSHUA, 1)],
                             labels={"Presubmit-Verified": 2},
                             requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"))
        related = [related_entry(261, parent_commit="sha_260_ps1"), related_entry(260, status="ABANDONED")]
        client = FakeStackGerritClient([parent, child], {261: related})
        child_status = _command(client).execute(
            StackStatusArgs(cl="261"), ExecutionContext.for_testing("/tmp/e2")
        ).data.stacks[0].changes[1]
        self.assertEqual(child_status.blockers[0].reason, "Parent ag/260 is abandoned")
        self.assertEqual(child_status.next_action, "Rebase onto studio-main or restore ag/260")

    def test_merged_parent_lets_child_merge_and_skips_closed_cl_audits(self) -> None:
        merged = stack_change(201, status="MERGED")
        child = stack_change(
            202, parent_commit="sha_201_ps1", uploader=ERWIN, votes=[(JOSHUA, 2)],
            labels={"Presubmit-Verified": 2},
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
        )
        related = [related_entry(202, parent_commit="sha_201_ps1"), related_entry(201, status="MERGED")]
        client = FakeStackGerritClient([merged, child], {202: related})
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-merged")
        result = _command(client).execute(StackStatusArgs(cl="202"), ctx)

        first, second = result.data.stacks[0].changes
        self.assertEqual(first.status, "MERGED")
        self.assertEqual(first.mergeable, MERGEABLE_NOT_APPLICABLE)
        self.assertIsNone(first.review)
        self.assertEqual(first.next_action, "Merged")
        self.assertEqual(client.mergeable_calls, [202])
        self.assertEqual(second.next_action, "Ready; vote Autosubmit +1 or submit it")
        self.assertEqual(result.data.stacks[0].summary(), "1 open CL, all ready to merge.")

    def test_open_parent_makes_ready_child_wait_and_presubmit_states_are_reported(self) -> None:
        parent = stack_change(
            301, labels={"Presubmit-Ready": 1}, votes=[(ERWIN, 2), (JOSHUA, 1)],
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
        )
        child = stack_change(
            302, parent_commit="sha_301_ps1", votes=[(ERWIN, 2), (JOSHUA, 1)], labels={"Presubmit-Verified": 2},
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
        )
        lone = stack_change(
            303, votes=[(ERWIN, 2), (JOSHUA, 1)],
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
        )
        related = [related_entry(302, parent_commit="sha_301_ps1"), related_entry(301)]
        client = FakeStackGerritClient([parent, child, lone], {302: related, 301: related})
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-ready")
        changes = _command(client).execute(StackStatusArgs(cl="302"), ctx).data.stacks[0].changes
        self.assertEqual(changes[0].presubmit.status, "queued")
        self.assertEqual(changes[0].next_action, "Wait for presubmit to finish")
        self.assertEqual(changes[1].next_action, "Ready; it merges after ag/301")

        lone_stack = _command(client).execute(StackStatusArgs(cl="303"), ctx).data.stacks[0]
        self.assertEqual(len(lone_stack.changes), 1)
        self.assertEqual(lone_stack.changes[0].parent, "studio-main@base_sh")
        self.assertEqual(lone_stack.changes[0].blockers[0].action, "Vote Presubmit-Ready +1")

    def test_head_is_resolved_through_git_service_and_local_drift_is_reported(self) -> None:
        change = stack_change(
            401, votes=[(ERWIN, 2)],
            requirements=submit_requirements(review_enforcement="UNSATISFIED", code_review="SATISFIED"),
        )
        client = FakeStackGerritClient([change], {})
        git = FakeGitService()
        git.seed_commit("a" * 40, f"{SUBJECT_UNICODE}\n\nBody.\n\nChange-Id: {change_id(401)}\n")
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-head", services={"git_service": git})
        result = _command(client).execute(StackStatusArgs(cl="HEAD", repo="/work/aemu"), ctx)

        self.assertEqual(client.queries[0], f"change:{change_id(401)}")
        self.assertEqual(result.data.query["mode"], "head")
        local = result.data.local
        self.assertFalse(local["in_sync"])
        self.assertIn("Local HEAD aaaaaaa in '/work/aemu' is not the uploaded ag/401 PS1", local["note"])
        self.assertIn("is not the uploaded ag/401 PS1", result.markdown_table)
        self.assertIn(("git", "log", "-1", "--format=%B", "HEAD"), git.recorded_commands)

    def test_head_matching_the_uploaded_patch_set_is_in_sync(self) -> None:
        client = FakeStackGerritClient([stack_change(402)], {})
        git = FakeGitService()
        git.seed_commit("sha_402_ps1", f"fix: Sync\n\nBody.\n\nChange-Id: {change_id(402)}\n")
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-sync", services={"git_service": git})
        result = _command(client).execute(StackStatusArgs(), ctx)
        self.assertTrue(result.data.local["in_sync"])
        self.assertNotIn("not the uploaded", result.markdown_table)

    def test_head_errors_are_actionable_usage_errors(self) -> None:
        client = FakeStackGerritClient([], {})
        not_repo = ExecutionContext.for_testing(
            "/tmp/emu-stack-norepo", services={"git_service": FakeGitService(inside_work_tree=False)}
        )
        with self.assertRaises(UsageError) as ctx_err:
            _command(client).execute(StackStatusArgs(repo="/nowhere"), not_repo)
        self.assertIn("'/nowhere' is not inside a Git work tree", ctx_err.exception.message)

        git = FakeGitService()
        git.seed_commit("b" * 40, "feat: Local only\n\nNo footer here.\n")
        no_footer = ExecutionContext.for_testing("/tmp/emu-stack-nofooter", services={"git_service": git})
        with self.assertRaises(UsageError) as ctx_err:
            _command(client).execute(StackStatusArgs(repo="/work"), no_footer)
        self.assertIn("has no 'Change-Id:' footer", ctx_err.exception.message)
        self.assertTrue(client.closed)

    def test_topic_mode_builds_one_stack_per_chain_without_duplicate_lookups(self) -> None:
        first = stack_change(501, extra={"topic": "emu-dev-cli-gerrit-stack-phase1"})
        second = stack_change(502, parent_commit="sha_501_ps1", extra={"topic": "emu-dev-cli-gerrit-stack-phase1"})
        other_repo = stack_change(
            503, extra={"topic": "emu-dev-cli-gerrit-stack-phase1", "project": "platform/hardware/generic/goldfish",
                        "branch": "emu-main-next"},
        )
        related = [related_entry(502, parent_commit="sha_501_ps1"), related_entry(501)]
        client = FakeStackGerritClient([first, second, other_repo], {501: related, 502: related})
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-topic")
        result = _command(client).execute(StackStatusArgs(topic="emu-dev-cli-gerrit-stack-phase1"), ctx)

        self.assertEqual(client.related_calls, [501, 503])
        self.assertEqual([len(s.changes) for s in result.data.stacks], [2, 1])
        self.assertEqual(result.data.stacks[1].project, "platform/hardware/generic/goldfish")
        self.assertFalse(any(c.is_target for s in result.data.stacks for c in s.changes))
        self.assertIn(" | platform/hardware/generic/goldfish (emu-main-next): ", result.summary)

    def test_lookup_errors(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-errors")
        with self.assertRaises(UsageError) as err:
            _command(FakeStackGerritClient([], {})).execute(StackStatusArgs(topic="missing"), ctx)
        self.assertIn("No Gerrit changes are in topic 'missing'", err.exception.message)

        with self.assertRaises(UsageError) as err:
            _command(FakeStackGerritClient([], {})).execute(StackStatusArgs(cl="999"), ctx)
        self.assertIn("No Gerrit change matches '999'", err.exception.message)

        twin_a = stack_change(601, extra={"change_id": change_id(600), "branch": "studio-main"})
        twin_b = stack_change(602, extra={"change_id": change_id(600), "branch": "main"})
        with self.assertRaises(UsageError) as err:
            _command(FakeStackGerritClient([twin_a, twin_b], {})).execute(StackStatusArgs(cl=change_id(600)), ctx)
        self.assertIn("matches 2 CLs: ag/601 (studio-main), ag/602 (main)", err.exception.message)

        with self.assertRaises(UsageError):
            _command(FakeStackGerritClient([], {})).execute(StackStatusArgs(cl="not a cl"), ctx)
        with self.assertRaises(UsageError) as err:
            StackStatusCommand().validate(StackStatusArgs(cl="123", topic="t"), ctx)
        self.assertIn("Pass either a CL identifier or --topic", err.exception.remediation)

    def test_transport_failure_is_wrapped_and_client_is_closed(self) -> None:
        client = FakeStackGerritClient([], {})
        client.fail_with = RuntimeError("gob-curl exit code 1")
        ctx = ExecutionContext.for_testing("/tmp/emu-stack-transport")
        with self.assertRaises(ExecutionError) as err:
            _command(client).execute(StackStatusArgs(cl="42"), ctx)
        self.assertIn("Failed to read the stack for '42'", err.exception.message)
        self.assertIn("gob-curl exit code 1", err.exception.message)
        self.assertTrue(client.closed)


class StackStatusCliTest(unittest.TestCase):
    """Parses the real CLI and serves GerritClient's REST calls from FakeProcessRunner."""

    def test_json_cli_run_uses_gerrit_client_rest_endpoints(self) -> None:
        change = stack_change(
            701, uploader=ERWIN, votes=[(JOSHUA, 2)],
            requirements=submit_requirements(review_enforcement="SATISFIED", code_review="SATISFIED"),
            labels={"Presubmit-Verified": 2},
        )
        urls: list[str] = []

        def gerrit_rest(call: FakeProcessCall) -> ProcessResult:
            url = call.args[-1]
            urls.append(url)
            if "/related" in url:
                body: Any = {}
            elif "/mergeable" in url:
                body = {"submit_type": "MERGE_IF_NECESSARY", "mergeable": True}
            else:
                body = [change]
            return ProcessResult(args=call.args, returncode=0, stdout=")]}'\n" + json.dumps(body))

        ctx = ExecutionContext.for_testing("/tmp/emu-stack-cli", json_mode=True)
        gob_curl = shutil.which("gob-curl") or "/usr/bin/gob-curl"
        ctx.fake_process_runner.register_prefix([gob_curl, "-s", "-f", "-S", "--compressed", "-X", "GET"], gerrit_rest)

        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        parser.add_argument("--json", action="store_true")
        register_parser(parser.add_subparsers(dest="subcommand"))
        args = parser.parse_args(["gerrit", "stack", "status", "701", "--json"])
        stdout = StringIO()
        exit_code = CliDispatcher(renderer=OutputRenderer(stdout=stdout, stderr=stdout)).dispatch(args, ctx=ctx)

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "gerrit stack status")
        self.assertNotIn("markdown_table", payload)
        self.assertEqual(payload["stacks"][0]["changes"][0]["number"], 701)
        self.assertTrue(payload["stacks"][0]["changes"][0]["review_enforcement"]["satisfied"])
        query_url = next(u for u in urls if "/changes/?q=" in u)
        self.assertIn("q=change:701", query_url)
        self.assertIn("o=SUBMIT_REQUIREMENTS", query_url)
        self.assertTrue(any(u.endswith("/a/changes/701/revisions/current/related") for u in urls))
        self.assertTrue(any(u.endswith("/a/changes/701/revisions/current/mergeable") for u in urls))


class GerritClientStackEndpointsTest(unittest.TestCase):
    """Covers GerritClient.query_changes, get_related_changes, and get_mergeable."""

    def _client(self, ctx: ExecutionContext, body: str) -> GerritClient:
        ctx.fake_process_runner.set_default_response(stdout=body)
        return GerritClient(runner=ctx.process_runner, prefer_cli=True, gob_curl_bin="gob-curl")

    def test_query_changes_encodes_query_and_options(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-client-query")
        client = self._client(ctx, ")]}'\n[{\"_number\": 1}, \"junk\"]")
        changes = client.query_changes('topic:"a+b" OR change:2', options=("CURRENT_REVISION", "MESSAGES"), limit=3)
        self.assertEqual(changes, [{"_number": 1}])
        url = ctx.fake_process_runner.calls[-1].args[-1]
        self.assertIn("/a/changes/?q=topic:%22a%2Bb%22%20OR%20change:2&n=3&o=CURRENT_REVISION&o=MESSAGES", url)

    def test_query_changes_rejects_non_list(self) -> None:
        client = self._client(ExecutionContext.for_testing("/tmp/emu-client-bad"), ")]}'\n{}")
        with self.assertRaises(RuntimeError) as err:
            client.query_changes("change:1")
        self.assertIn("Unexpected Gerrit search response type (dict)", str(err.exception))

    def test_related_changes_handles_empty_chain_and_mergeable_validates_type(self) -> None:
        client = self._client(ExecutionContext.for_testing("/tmp/emu-client-related"), ")]}'\n{}")
        self.assertEqual(client.get_related_changes(5), [])
        with self.assertRaises(RuntimeError) as err:
            self._client(ExecutionContext.for_testing("/tmp/emu-client-merge"), ")]}'\n[]").get_mergeable(5)
        self.assertIn("Unexpected Gerrit mergeable response type (list)", str(err.exception))
        related = self._client(
            ExecutionContext.for_testing("/tmp/emu-client-related2"),
            ")]}'\n{\"changes\": [{\"_change_number\": 7}, 3]}",
        )
        self.assertEqual(related.get_related_changes("I" + "0" * 40), [{"_change_number": 7}])


if __name__ == "__main__":
    unittest.main()
