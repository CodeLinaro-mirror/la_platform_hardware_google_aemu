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

"""Hermetic tests for `gerrit stack pre-upload` over FakeGitService (zero @patch)."""

import argparse
import difflib
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.gerrit import register_parser
from commands.gerrit.stack import PreUploadArgs, PreUploadReport, Severity, StackPreUploadCommand
from commands.gerrit.stack.pre_upload import render_pre_upload_report
from commands.gerrit.stack.pre_upload_checks import (
    cc_binary_calls,
    is_secret_identifier,
    leaked_cpp_secrets,
    non_imperative_lead_word,
)
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import ExecutionError, ExitCode, UsageError
from core.git import FakeGitService
from core.process import FakeProcessCall, ProcessResult

GOLDFISH_ROOT = "/work/hardware/generic/goldfish"
AEMU_ROOT = "/work/hardware/google/aemu"
GOOD_MESSAGE = (
    "feat(stack): Add pre-upload audit 测试_🚀_é\n\n"
    "Runs review checks locally so problems surface before upload.\n\n"
    "Bug: 580991599\n"
    "Test: python3 -m unittest\n"
    "Change-Id: I0123456789abcdef0123456789abcdef01234567\n"
)


def unified_diff(path: str, old: str, new: str) -> str:
    old_name = f"a/{path}" if old else "/dev/null"
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), old_name, f"b/{path}", n=3
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(lines)


class FakeRepo:
    """Serves commit diffs, file blobs, and stack listings to a FakeGitService."""

    def __init__(self, root: str = GOLDFISH_ROOT) -> None:
        self.git = FakeGitService(repo_root=Path(root))
        self.diffs: dict[str, str] = {}
        self.blobs: dict[str, str] = {}
        self.local: list[str] = []
        self.show_returncode = 0
        runner = self.git.process_runner
        runner.register_prefix(("git", "-c", "core.quotePath=false", "show"), self._show_diff)
        runner.register_prefix(("git", "show"), self._show_blob)
        runner.register_prefix(("git", "rev-list", "--reverse"), self._rev_list)

    def upstream(self, sha: str = "u" * 40) -> None:
        self.git.seed_commit(sha, "chore: Upstream\n")
        self.git.refs["@{upstream}"] = sha

    def commit(self, sha: str, message: str, new: dict[str, str], old: dict[str, str] | None = None) -> None:
        self.git.seed_commit(sha, message)
        old = old or {}
        self.diffs[sha] = "".join(unified_diff(p, old.get(p, ""), content) for p, content in new.items())
        for path, content in new.items():
            self.blobs[f"{sha}:{path}"] = content
        self.local.append(sha)

    def _show_diff(self, call: FakeProcessCall) -> ProcessResult:
        if self.show_returncode:
            return ProcessResult(args=call.args, returncode=self.show_returncode, stderr="fatal: bad object\n")
        return ProcessResult(args=call.args, returncode=0, stdout=self.diffs.get(call.args[-2], ""))

    def _show_blob(self, call: FakeProcessCall) -> ProcessResult:
        blob = self.blobs.get(call.args[2])
        if blob is None:
            return ProcessResult(args=call.args, returncode=128, stderr="fatal: path not found\n")
        return ProcessResult(args=call.args, returncode=0, stdout=blob)

    def _rev_list(self, call: FakeProcessCall) -> ProcessResult:
        return ProcessResult(args=call.args, returncode=0, stdout="".join(f"{sha}\n" for sha in self.local))

    def context(self, *, json_mode: bool = False) -> ExecutionContext:
        return ExecutionContext.for_testing(
            "/tmp/emu-pre-upload", json_mode=json_mode, services={"git_service": self.git}
        )


def _run(repo: FakeRepo, **kwargs) -> tuple[PreUploadReport, int]:
    result = StackPreUploadCommand().execute(PreUploadArgs(repo=str(repo.git.repo_root), **kwargs), repo.context())
    return result.data, int(result)


def _by_check(report: PreUploadReport, check: str) -> list:
    return [f for t in report.targets for f in t.findings if f.check == check]


class HelperFunctionTest(unittest.TestCase):
    def test_non_imperative_lead_word(self) -> None:
        cases = {
            "feat(x): Added stack status": "Added",
            "fix: Adds retries": "Adds",
            "docs: Updating readme": "Updating",
            "fix: Updates the parser": "Updates",
            "feat: Add stack status": "",
            "fix: Address review comments": "",
            "perf: Process events in batches": "",
            "refactor: Focus the input": "",
            "feat: Embed the manifest": "",
            "feat: Ping reviewers": "",
            "Not semantic at all": "",
            "feat: 测试 unicode": "",
        }
        for subject, expected in cases.items():
            self.assertEqual(non_imperative_lead_word(subject), expected, subject)

    def test_secret_identifiers(self) -> None:
        for name in ("token", "access_token_", "mApiKey", "clientSecret", "auth_header", "password", "credentials"):
            self.assertTrue(is_secret_identifier(name), name)
        for name in ("token_count", "tokenizer", "password_hint", "kTokenHeaderName", "secret_name", "path"):
            self.assertFalse(is_secret_identifier(name), name)
        self.assertEqual(leaked_cpp_secrets('LOG(ERROR) << "token: " << token;'), ["token"])
        self.assertEqual(leaked_cpp_secrets('LOG(ERROR) << "bad token " << token.size();'), [])
        self.assertEqual(leaked_cpp_secrets('LOG(ERROR) << RedactToken(token);'), [])
        self.assertEqual(leaked_cpp_secrets('absl::InternalError("Invalid token format")'), [])

    def test_cc_binary_calls_handle_nested_arguments_and_regex_fallback(self) -> None:
        build = (
            'cc_binary(\n    name = "tool",\n    srcs = glob(["*.cc"]),\n)\n'
            'cc_binary(name = "plugin", srcs = select({"//c": ["a.cc"]}), linkshared = True)\n'
            'cc_binary_with_manifest(name = "ok")\n'
        )
        self.assertEqual(cc_binary_calls(build), [("tool", 1, 4, False), ("plugin", 5, 5, True)])
        broken = 'x = (\ncc_binary(name = "regex_tool", srcs = ["a.cc"])\n'
        self.assertEqual(cc_binary_calls(broken), [("regex_tool", 2, 2, False)])


class StackPreUploadCommandTest(unittest.TestCase):
    """Audits fake commits with every check through StackPreUploadCommand."""

    def test_stack_audit_reports_each_local_commit(self) -> None:
        repo = FakeRepo()
        repo.upstream()
        repo.commit("1" * 40, "feat(stack): Added stack status\n\nExplains why.\n", {"tools/a.py": "x = 1\n"})
        repo.commit("2" * 40, GOOD_MESSAGE, {"tools/b.py": "y = 2\n"})
        report, exit_code = _run(repo)

        self.assertEqual(exit_code, int(ExitCode.FAILURE))
        self.assertEqual((report.mode, report.base), ("stack", "@{upstream}"))
        first, second = report.targets
        self.assertEqual(first.label, "1111111")
        messages = [f.message for f in first.findings]
        self.assertTrue(any("'Bug:" in m for m in messages))
        self.assertTrue(any("'Test:" in m for m in messages))
        self.assertTrue(any("no 'Change-Id:' footer" in m for m in messages))
        mood = [f for f in first.findings if f.severity == Severity.WARNING]
        self.assertEqual(len(mood), 1)
        self.assertIn("starts with 'Added'", mood[0].message)
        self.assertEqual(second.findings, ())
        self.assertEqual(second.subject, "feat(stack): Add pre-upload audit 测试_🚀_é")
        self.assertEqual(report.errors, first.count(Severity.ERROR))
        self.assertFalse(report.passed)
        self.assertIn("fix before uploading", report.summary)

    def test_conversation_and_agent_trailers_are_rejected(self) -> None:
        repo = FakeRepo()
        message = GOOD_MESSAGE.replace("Bug:", "CONVERSATION=https://example\nORIGINAL_AUTHOR=someone\nBug:")
        repo.commit("3" * 40, message, {"tools/c.py": "z = 3\n"})
        report, _ = _run(repo, commit="HEAD")
        messages = [f.message for f in _by_check(report, "commit-message")]
        self.assertTrue(any("'CONVERSATION='" in m for m in messages))
        self.assertTrue(any("forbidden trailer 'ORIGINAL_AUTHOR='" in m for m in messages))

    def test_cc_binary_manifest_flags_only_touched_raw_executables(self) -> None:
        repo = FakeRepo()
        old_build = 'cc_binary(\n    name = "legacy_tool",\n    srcs = ["legacy.cc"],\n)\n'
        new_build = old_build + (
            '\ncc_binary(\n    name = "new_tool",\n    srcs = glob(["*.cc"]),\n)\n'
            '\ncc_binary(name = "plugin", srcs = ["p.cc"], linkshared = True)\n'
            '\ncc_binary_with_manifest(name = "ok_tool", srcs = ["ok.cc"])\n'
        )
        repo.commit("4" * 40, GOOD_MESSAGE, {"emulator/测试/BUILD.bazel": new_build},
                    old={"emulator/测试/BUILD.bazel": old_build})
        report, exit_code = _run(repo, commit="HEAD")
        findings = _by_check(report, "cc-binary-manifest")
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].path, findings[0].line), ("emulator/测试/BUILD.bazel", 6))
        self.assertIn("cc_binary 'new_tool'", findings[0].message)
        self.assertIn("//emulator/tools:cc_binary_with_manifest.bzl", findings[0].remediation)
        self.assertEqual(exit_code, int(ExitCode.FAILURE))

    def test_error_message_check_cpp(self) -> None:
        source = (
            "absl::Status Load(const std::string& path) {\n"
            "  auto status = ReadConfig(path);\n"
            "  if (!status.ok()) {\n"
            '    return absl::InternalError("Failed to load the AVD config file");\n'
            "  }\n"
            '  if (path.empty()) return absl::UnknownError("failed");\n'
            '  LOG(ERROR) << "auth failed for " << path << ": " << access_token_;\n'
            '  LOG(INFO) << "token size " << token.size() << GetToken().status();\n'
            '  return absl::NotFoundError(absl::StrCat("Config ", path, " missing: ", status.message()));\n'
            "}\n"
        )
        repo = FakeRepo()
        repo.commit("5" * 40, GOOD_MESSAGE, {"emulator/测试_🚀_é/config_loader.cc": source})
        report, exit_code = _run(repo, commit="HEAD", checks=("error-messages",))
        found = {(f.line, f.severity.value, f.message.split(",")[0][:40]) for f in _by_check(report, "error-messages")}
        lines = sorted(line for line, _, _ in found)
        self.assertEqual(lines, [4, 6, 6, 7])
        dropped = next(f for f in _by_check(report, "error-messages") if f.line == 4)
        self.assertIn("failed status 'status'", dropped.message)
        secret = next(f for f in _by_check(report, "error-messages") if f.line == 7)
        self.assertEqual(secret.severity, Severity.ERROR)
        self.assertIn("'access_token_'", secret.message)
        self.assertEqual(exit_code, int(ExitCode.FAILURE))

    def test_error_message_check_python(self) -> None:
        source = (
            "import logging\n"
            "LOGGER = logging.getLogger(__name__)\n"
            "def run(token, path, api_key):\n"
            "    try:\n"
            "        open(path)\n"
            "    except OSError as exc:\n"
            '        raise RuntimeError(f"Could not open the config file {path}: {exc}")\n'
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        pass\n"
            "    if not path:\n"
            '        raise UsageError("Bad path")\n'
            '    LOGGER.error("Gerrit call failed with key %s", api_key)\n'
            '    raise ExecutionError(f"Auth with {token} failed for {path}", remediation="Run gcert.")\n'
        )
        repo = FakeRepo(AEMU_ROOT)
        repo.commit("6" * 40, GOOD_MESSAGE, {"tools/测试_runner.py": source})
        report, _ = _run(repo, commit="HEAD", checks=("error-messages",))
        found = sorted((f.line, f.severity.value) for f in _by_check(report, "error-messages"))
        self.assertEqual(found, [(7, "warning"), (10, "warning"), (13, "warning"), (13, "warning"),
                                 (14, "error"), (15, "error")])
        texts = " ".join(f.message for f in _by_check(report, "error-messages"))
        self.assertIn("does not chain the caught exception", texts)
        self.assertIn("UsageError is raised without a remediation", texts)
        self.assertIn("prints 'api_key'", texts)
        self.assertIn("prints 'token'", texts)

    def test_python_nested_handlers_idioms_and_keyword_or_fstring_secrets(self) -> None:
        source = (
            "def go(secret, path):\n"
            "    try:\n"
            "        load(path)\n"
            "    except OSError as outer:\n"
            "        try:\n"
            "            retry(path)\n"
            "        except ValueError as inner:\n"
            '            raise RuntimeError(f"Retrying {path} failed twice: {inner}")\n'
            '    raise NotImplementedError("Not supported yet")\n'
            '    LOGGER.warning(f"Using credentials {secret} for {path}")\n'
            '    raise ExecutionError(f"Upload of {path} was rejected", remediation=f"Check {secret}")\n'
        )
        repo = FakeRepo(AEMU_ROOT)
        repo.commit("f" * 40, GOOD_MESSAGE, {"tools/nested.py": source})
        report, _ = _run(repo, commit="HEAD", checks=("error-messages",))
        found = sorted((f.line, f.severity.value) for f in _by_check(report, "error-messages"))
        self.assertEqual(found, [(8, "warning"), (10, "error"), (11, "error")])

    def test_empty_stack_explains_how_to_audit_something_else(self) -> None:
        repo = FakeRepo()
        repo.upstream()
        report, exit_code = _run(repo)
        self.assertEqual((report.mode, report.targets, exit_code), ("stack", (), 0))
        self.assertIn("Nothing to audit: @{upstream}..HEAD has no commits", report.summary)

    def test_library_reuse_in_goldfish_points_at_catalog_targets(self) -> None:
        source = (
            "#include <sys/socket.h>\n"
            "// Reads config.ini via getenv(\"HOME\")\n"
            'const char* home = std::getenv("HOME");\n'
            "std::ifstream in(path);\n"
            "#ifdef _WIN32\n"
            "#endif\n"
            'const char* kOk = "HTTP/1.1 200 OK";\n'
            "CURL* curl = curl_easy_init();\n"
            "auto pos = line.find('=');\n"
        )
        repo = FakeRepo()
        repo.commit("7" * 40, GOOD_MESSAGE, {
            "emulator/net/测试_server.cc": source,
            "emulator/libs/system/system.cc": 'const char* v = getenv("PATH");\n',
        })
        report, exit_code = _run(repo, commit="HEAD", checks=("library-reuse",))
        findings = _by_check(report, "library-reuse")
        rules = sorted(f.message.rsplit("(", 1)[1].rstrip(").") for f in findings)
        self.assertEqual(rules, sorted([
            "raw-socket", "raw-env", "raw-fstream", "win32-shim", "manual-http-server",
            "manual-http-client", "manual-ini-parsing",
        ]))
        self.assertTrue(all(f.path == "emulator/net/测试_server.cc" for f in findings))
        env = next(f for f in findings if "raw-env" in f.message)
        self.assertIn("android::base::System (//emulator/libs/system, \"android/base/system.h\")", env.remediation)
        self.assertIn("//emulator/libs/system:test-headers", env.remediation)
        fstream = next(f for f in findings if "raw-fstream" in f.message)
        self.assertEqual((fstream.severity, fstream.line), (Severity.ERROR, 4))
        self.assertEqual(exit_code, int(ExitCode.FAILURE))

    def test_other_repos_only_enforce_utf8_file_io_and_skip_manifest_check(self) -> None:
        repo = FakeRepo(AEMU_ROOT)
        repo.commit("8" * 40, GOOD_MESSAGE, {
            "host-common/io.cpp": 'std::ofstream out(path);\nconst char* h = getenv("HOME");\n',
            "BUILD.bazel": 'cc_binary(name = "aemu_tool", srcs = ["a.cc"])\n',
        })
        report, _ = _run(repo, commit="HEAD")
        findings = _by_check(report, "library-reuse")
        self.assertEqual(len(findings), 1)
        self.assertIn("android/utils/file_io.h", findings[0].remediation)
        self.assertEqual(_by_check(report, "cc-binary-manifest"), [])
        self.assertIn(
            "cc-binary-manifest: only goldfish provides //emulator/tools:cc_binary_with_manifest.bzl",
            report.targets[0].skipped,
        )

    def test_strict_mode_and_check_selection(self) -> None:
        repo = FakeRepo()
        repo.commit("9" * 40, "fix: broken subject.\n", {"emulator/a.cc": "#if defined(_WIN32)\n#endif\n"})
        lenient, lenient_code = _run(repo, commit="HEAD", checks=("library-reuse",))
        self.assertEqual(lenient.checks, ("library-reuse",))
        self.assertEqual((lenient.errors, lenient.warnings, lenient_code), (0, 1, 0))
        self.assertTrue(lenient.passed)
        strict, strict_code = _run(repo, commit="HEAD", checks=("library-reuse",), strict=True)
        self.assertFalse(strict.passed)
        self.assertEqual(strict_code, int(ExitCode.FAILURE))

    def test_working_tree_audit_reads_diff_untracked_files_and_draft_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "goldfish"
            (root / "emulator").mkdir(parents=True)
            (root / "emulator" / "foo.cc").write_text('int a;\nconst char* p = getenv("X");\n', encoding="utf-8")
            (root / "emulator" / "新しい.cc").write_text("std::ifstream f(p);\n", encoding="utf-8")
            draft = Path(tmp) / "draft.txt"
            draft.write_text("fix(stack): Handle drafts\n\nKeeps drafts checkable.\n\nBug: 1\nTest: unit\n",
                             encoding="utf-8")
            repo = FakeRepo(str(root))
            repo.git.process_runner.register_prefix(
                ("git", "-c", "core.quotePath=false", "diff"),
                ProcessResult(args=(), stdout=unified_diff("emulator/foo.cc", "int a;\n",
                                                            'int a;\nconst char* p = getenv("X");\n')),
            )
            repo.git.process_runner.register_prefix(
                ("git", "ls-files"), ProcessResult(args=(), stdout="emulator/新しい.cc\0")
            )
            report, exit_code = _run(repo, working_tree=True, message_file=str(draft))
            self.assertEqual(report.mode, "working_tree")
            target = report.targets[0]
            self.assertEqual(target.label, "working tree")
            self.assertEqual(target.subject, "fix(stack): Handle drafts")
            self.assertEqual(_by_check(report, "commit-message"), [])
            paths = sorted((f.path, f.line) for f in _by_check(report, "library-reuse"))
            self.assertEqual(paths, [("emulator/foo.cc", 2), ("emulator/新しい.cc", 1)])
            self.assertEqual(exit_code, int(ExitCode.FAILURE))

            no_draft, _ = _run(repo, working_tree=True)
            self.assertIn(
                "commit-message: no commit message to check (pass --message-file to check a draft)",
                no_draft.targets[0].skipped,
            )

    def test_scope_resolution_and_validation_errors(self) -> None:
        repo = FakeRepo()
        repo.commit("a" * 40, GOOD_MESSAGE, {"tools/a.py": "x = 1\n"})
        fallback, _ = _run(repo)
        self.assertEqual((fallback.mode, fallback.targets[0].commit), ("commit", "a" * 40))

        cmd = StackPreUploadCommand()
        ctx = repo.context()
        for args, needle in (
            (PreUploadArgs(checks=("spelling",)), "Unknown pre-upload check(s): spelling"),
            (PreUploadArgs(message_file="m.txt"), "--message-file only applies to --working-tree"),
            (PreUploadArgs(commit="HEAD", base="origin/studio-main"), "select different targets"),
            (PreUploadArgs(commit="nope"), "--commit 'nope' is not a commit"),
            (PreUploadArgs(base="nope"), "--base 'nope' is not a commit"),
            (PreUploadArgs(working_tree=True, message_file="/missing/draft.txt"), "Cannot read --message-file"),
        ):
            with self.assertRaises(UsageError, msg=needle) as err:
                cmd.execute(args, ctx)
            self.assertIn(needle, err.exception.message)

        repo.upstream("f" * 40)
        repo.local = [f"{i:040x}" for i in range(21)]
        with self.assertRaises(UsageError) as err:
            cmd.execute(PreUploadArgs(), ctx)
        self.assertIn("contains 21 commits", err.exception.message)

        not_repo = ExecutionContext.for_testing(
            "/tmp/emu-pre-upload-norepo", services={"git_service": FakeGitService(inside_work_tree=False)}
        )
        with self.assertRaises(UsageError) as err:
            cmd.execute(PreUploadArgs(repo="/nowhere"), not_repo)
        self.assertIn("'/nowhere' is not inside a Git work tree", err.exception.message)

    def test_git_failure_is_reported_as_execution_error(self) -> None:
        repo = FakeRepo()
        repo.commit("b" * 40, GOOD_MESSAGE, {"tools/a.py": "x = 1\n"})
        repo.show_returncode = 128
        with self.assertRaises(ExecutionError) as err:
            _run(repo, commit="HEAD")
        self.assertIn("git could not show the diff of " + "b" * 40, err.exception.message)
        self.assertIn("fatal: bad object", err.exception.message)

    def test_rendered_report_lists_findings_fixes_and_skips(self) -> None:
        repo = FakeRepo(AEMU_ROOT)
        repo.commit("c" * 40, "fix: Fixed it\n\nBody.\n", {"tools/a.py": "x = 1\n"})
        report, _ = _run(repo, commit="HEAD")
        text = render_pre_upload_report(report)
        self.assertIn("Checks: commit-message, cc-binary-manifest, error-messages, library-reuse", text)
        self.assertIn("cccccc fix: Fixed it", text)
        self.assertIn("✗ [commit-message] /COMMIT_MSG:", text)
        self.assertIn("⚠ [commit-message] /COMMIT_MSG:1: The subject description starts with 'Fixed'", text)
        self.assertIn("      fix: ", text)
        self.assertIn("  - skipped cc-binary-manifest:", text)

        clean = FakeRepo(AEMU_ROOT)
        clean.commit("d" * 40, GOOD_MESSAGE, {"tools/a.py": "x = 1\n"})
        clean_report, clean_code = _run(clean, commit="HEAD")
        self.assertEqual(clean_code, 0)
        self.assertIn("✓ no findings", render_pre_upload_report(clean_report))
        self.assertIn("ready to upload", clean_report.summary)


class PreUploadCliTest(unittest.TestCase):
    def test_json_cli_run_returns_failure_exit_code_and_structured_findings(self) -> None:
        repo = FakeRepo()
        repo.commit("e" * 40, "feat: no capital\n", {"emulator/x.cc": 'auto v = std::getenv("A");\n'})
        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        parser.add_argument("--json", action="store_true")
        register_parser(parser.add_subparsers(dest="subcommand"))
        args = parser.parse_args(["gerrit", "stack", "pre-upload", "--commit", "HEAD", "--json",
                                  "--repo", GOLDFISH_ROOT])
        out = StringIO()
        exit_code = CliDispatcher(renderer=OutputRenderer(stdout=out, stderr=out)).dispatch(
            args, ctx=repo.context()
        )
        self.assertEqual(exit_code, int(ExitCode.FAILURE))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["passed"])
        self.assertEqual(payload["mode"], "commit")
        self.assertNotIn("markdown_table", payload)
        checks = {f["check"] for f in payload["targets"][0]["findings"]}
        self.assertEqual(checks, {"commit-message", "library-reuse"})


if __name__ == "__main__":
    unittest.main()
