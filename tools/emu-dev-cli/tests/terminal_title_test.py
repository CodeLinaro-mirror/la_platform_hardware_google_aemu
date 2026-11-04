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

"""Unit tests for AEMU Harness terminal title escape sequence updates."""

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Dict, List, Optional
import unittest
from unittest import mock
import warnings
import yaml

from commands.harness import run_harness_clear, run_harness_hook, terminal_title
from commands.harness.constants import HookDecision, HookEvent
from commands.harness.guardrails import dispatch_hook_event
from commands.harness.ledger import SessionLedgerStore
from commands.harness.terminal_title import (
    OSC_TITLE_PREFIX,
    OSC_TITLE_SUFFIX,
    TerminalTitleUpdater,
    XTWINOPS_POP_TITLE,
    XTWINOPS_PUSH_TITLE,
    build_osc_title,
    extract_edit_filename,
    format_short_command,
    is_terminal_title_enabled,
    run_ticker_loop,
    sanitize_title_text,
)

XTERM_ENV: Dict[str, str] = {"TERM": "xterm-256color"}
CONVERSATION_ID: str = "conv-term-title-1"


class FakeClock:
    """Deterministic monotonic test clock."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class FakeTtyStream(io.StringIO):
    """In-memory text stream reporting `isatty()` and counting `flush()` calls."""

    def __init__(self, is_tty: bool = True) -> None:
        super().__init__()
        self._is_tty = is_tty
        self.flush_count = 0

    def isatty(self) -> bool:
        return self._is_tty

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class TerminalTitleFormattingAndGatesTest(unittest.TestCase):
    """Tests environment gates, command shortening, filename extraction, and sanitization."""

    def test_environment_availability_gates(self) -> None:
        self.assertFalse(is_terminal_title_enabled({}))
        self.assertFalse(is_terminal_title_enabled({"TERM": ""}))
        self.assertFalse(is_terminal_title_enabled({"TERM": "dumb"}))
        self.assertFalse(is_terminal_title_enabled({"TERM": "DUMB"}))
        self.assertTrue(is_terminal_title_enabled({"TERM": "xterm-256color"}))
        self.assertTrue(is_terminal_title_enabled({"TERM": "tmux-256color", "NO_COLOR": ""}))
        self.assertFalse(
            is_terminal_title_enabled({"TERM": "xterm-256color", "NO_COLOR": "1"})
        )
        self.assertFalse(
            is_terminal_title_enabled(
                {"TERM": "xterm-256color", "EMU_HARNESS_NO_TITLE": "1"}
            )
        )
        self.assertFalse(
            is_terminal_title_enabled(
                {"TERM": "xterm-256color", "EMU_HARNESS_NO_TITLE": "true"}
            )
        )
        self.assertTrue(
            is_terminal_title_enabled(
                {"TERM": "xterm-256color", "EMU_HARNESS_NO_TITLE": "0"}
            )
        )
        self.assertTrue(
            is_terminal_title_enabled(
                {"TERM": "xterm-256color", "EMU_HARNESS_NO_TITLE": "false"}
            )
        )

    def test_sanitize_title_strips_escape_and_control_injection(self) -> None:
        malicious = "bazel\x1b]52;c;evil\x07\ntest\r\x7f\x9c //..."
        cleaned = sanitize_title_text(malicious)
        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("\x07", cleaned)
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\x9c", cleaned)
        self.assertEqual(cleaned, "bazel ]52;c;evil test //...")

    def test_format_short_command_strips_setup_and_truncates(self) -> None:
        self.assertEqual(
            format_short_command("cd /tmp/worktree && PYTHONPATH=src bazel test //..."),
            "bazel test //...",
        )
        self.assertEqual(
            format_short_command("export FOO=1 && BAR='baz' git status -sb"),
            "git status -sb",
        )
        self.assertEqual(format_short_command("cd /only/chdir"), "cd /only/chdir")
        long_cmd = (
            "python3 -m unittest discover -s /very/long/path/to/tools/emu-dev-cli/tests "
            "-p '*_test.py'"
        )
        shortened = format_short_command(long_cmd, max_chars=32)
        self.assertLessEqual(len(shortened), 32)
        self.assertTrue(shortened.endswith("..."))

    def test_extract_edit_filename_supports_posix_windows_and_unicode(self) -> None:
        posix_payload = {
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": "/repo/emulator/display/virtual_display.cc"},
            }
        }
        self.assertEqual(extract_edit_filename(posix_payload), "virtual_display.cc")

        win_unicode_payload = {
            "toolCall": {
                "name": "write_file",
                "args": {"TargetFile": r"C:\Users\测试\goldfish\虚拟显示_🚀_é.cc"},
            }
        }
        self.assertEqual(extract_edit_filename(win_unicode_payload), "虚拟显示_🚀_é.cc")


class TerminalTitleHookLifecycleTest(unittest.TestCase):
    """Tests full hook lifecycle transitions, stack push/pop, TTY routing, and tickers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.harness_dir = self.base_path / "harness"
        self.ledger_store = SessionLedgerStore(base_dir=self.harness_dir)
        self.workflow_state_root = self.base_path / "workflows" / "state"
        self.workflow_state_root.mkdir(parents=True, exist_ok=True)
        self.clock = FakeClock()
        self.missing_tty = self.base_path / "nonexistent_tty"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_stderr_updater(
        self,
        stderr: Optional[FakeTtyStream] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> tuple[TerminalTitleUpdater, FakeTtyStream]:
        stream = stderr or FakeTtyStream(is_tty=True)
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=self.missing_tty,
            stderr=stream,
            env=XTERM_ENV if env is None else env,
            clock=self.clock,
            enable_ticker=False,
        )
        return updater, stream

    def test_full_lifecycle_formats_and_title_stack_push_pop(self) -> None:
        updater, stream = self._make_stderr_updater()
        emitted: List[str] = []

        def dispatch(event: str, extra: Dict[str, object]) -> Dict[str, object]:
            before = len(stream.getvalue())
            res = dispatch_hook_event(
                event,
                {"conversationId": CONVERSATION_ID, **extra},
                store=self.ledger_store,
                state_root=self.workflow_state_root,
                title_updater=updater,
            )
            emitted.append(stream.getvalue()[before:])
            return res

        # 1. SessionStart pushes title stack once and shows session started.
        dispatch(HookEvent.SESSION_START.value, {})
        self.assertEqual(
            emitted[-1],
            f"{XTWINOPS_PUSH_TITLE}{OSC_TITLE_PREFIX}🧠 Session started{OSC_TITLE_SUFFIX}",
        )

        # 2. PreInvocation does not double-push and starts thinking timer at 0s -> 4s.
        self.clock.advance(30.0)
        dispatch(HookEvent.PRE_INVOCATION.value, {})
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}🧠 Thinking... (0s){OSC_TITLE_SUFFIX}",
        )
        self.clock.advance(4.0)
        dispatch(HookEvent.PRE_INVOCATION.value, {})
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}🧠 Thinking... (4s){OSC_TITLE_SUFFIX}",
        )

        # 3. PreToolUse (run_command) shows ⚡ short command (preserving original before rewrite).
        pre_cmd_out = dispatch(
            HookEvent.PRE_TOOL.value,
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test //..."},
                }
            },
        )
        self.assertEqual(pre_cmd_out["decision"], HookDecision.ALLOW.value)
        self.assertIn("--test_output=errors", pre_cmd_out["overwrite"]["CommandLine"])  # type: ignore[index]
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}⚡ bazel test //... (0s){OSC_TITLE_SUFFIX}",
        )

        # 4. PostToolUse records 18s command duration and transitions back to thinking.
        self.clock.advance(18.0)
        dispatch(
            HookEvent.POST_TOOL.value,
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test --test_output=errors //..."},
                },
                "result": "PASSED",
            },
        )
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}🧠 Thinking... (0s){OSC_TITLE_SUFFIX}",
        )
        saved_state = json.loads(
            updater.state_path(CONVERSATION_ID).read_text(encoding="utf-8")
        )
        self.assertEqual(saved_state["last_tool_elapsed_sec"], 18)

        # 5. PreToolUse (edit_file / write_file) shows 💾 filename (including Unicode).
        dispatch(
            HookEvent.PRE_TOOL.value,
            {
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/work/aemu/display/虚拟显示_🚀_é.cc",
                        "Target": "int a = 1;",
                        "Replacement": "int a = 2;",
                    },
                }
            },
        )
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}💾 虚拟显示_🚀_é.cc{OSC_TITLE_SUFFIX}",
        )

        # 6. PreToolUse (other tool) shows 🔧 tool_name.
        dispatch(
            HookEvent.PRE_TOOL.value,
            {"toolCall": {"name": "code_search", "args": {"Query": "WebServer"}}},
        )
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}🔧 code_search{OSC_TITLE_SUFFIX}",
        )

        # 7. Stop emits ✅ Done and pops original window title from stack (\033[23;0t).
        stop_out = dispatch(HookEvent.STOP.value, {})
        self.assertEqual(stop_out["decision"], HookDecision.STOP.value)
        self.assertEqual(
            emitted[-1],
            f"{OSC_TITLE_PREFIX}✅ Done{OSC_TITLE_SUFFIX}{XTWINOPS_POP_TITLE}",
        )
        self.assertGreaterEqual(stream.flush_count, len(emitted))

        # 8. Turn 2 re-pushes title stack cleanly on next PreInvocation.
        dispatch(HookEvent.PRE_INVOCATION.value, {})
        self.assertEqual(
            emitted[-1],
            f"{XTWINOPS_PUSH_TITLE}{OSC_TITLE_PREFIX}🧠 Thinking... (0s){OSC_TITLE_SUFFIX}",
        )

    def test_stop_auto_continuation_keeps_stack_pushed_until_final_stop(self) -> None:
        updater, stream = self._make_stderr_updater()
        wf_dir = self.workflow_state_root / "create-commit"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / "STATE-wf1.yaml"
        wf_file.write_text(
            yaml.safe_dump(
                {
                    "workflow_name": "create-commit",
                    "state_id": "wf1",
                    "status": "RUNNING",
                    "current_step": 1,
                    "metadata": {"conversation_id": CONVERSATION_ID},
                }
            ),
            encoding="utf-8",
        )

        dispatch_hook_event(
            HookEvent.SESSION_START.value,
            {"conversationId": CONVERSATION_ID},
            store=self.ledger_store,
            state_root=self.workflow_state_root,
            title_updater=updater,
        )
        before_cont = len(stream.getvalue())
        cont_res = dispatch_hook_event(
            HookEvent.STOP.value,
            {"conversationId": CONVERSATION_ID},
            store=self.ledger_store,
            state_root=self.workflow_state_root,
            title_updater=updater,
        )
        self.assertEqual(cont_res["decision"], HookDecision.CONTINUE.value)
        cont_seq = stream.getvalue()[before_cont:]
        self.assertEqual(cont_seq, build_osc_title("🧠 Thinking... (0s)"))
        self.assertNotIn(XTWINOPS_POP_TITLE, cont_seq)

        wf_file.write_text(
            yaml.safe_dump(
                {
                    "workflow_name": "create-commit",
                    "state_id": "wf1",
                    "status": "DONE",
                    "current_step": 2,
                    "metadata": {"conversation_id": CONVERSATION_ID},
                }
            ),
            encoding="utf-8",
        )
        before_done = len(stream.getvalue())
        done_res = dispatch_hook_event(
            HookEvent.STOP.value,
            {"conversationId": CONVERSATION_ID},
            store=self.ledger_store,
            state_root=self.workflow_state_root,
            title_updater=updater,
        )
        self.assertEqual(done_res["decision"], HookDecision.STOP.value)
        self.assertEqual(
            stream.getvalue()[before_done:],
            f"{build_osc_title('✅ Done')}{XTWINOPS_POP_TITLE}",
        )

    def test_prefers_dev_tty_over_stderr_and_never_touches_stdout(self) -> None:
        fake_tty_file = self.base_path / "fake_dev_tty"
        fake_tty_file.write_text("", encoding="utf-8")
        fake_stderr = FakeTtyStream(is_tty=True)
        fake_stdout = FakeTtyStream(is_tty=True)

        env_vars = {
            "TERM": "xterm-256color",
            "EMU_DEV_CLI_HARNESS_DIR": str(self.harness_dir),
            "EMU_HARNESS_NO_TICKER": "1",
        }
        stdin_json = io.StringIO(
            json.dumps(
                {
                    "conversationId": "conv-cli-stdout",
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "git rev-parse HEAD^{commit}"},
                    },
                }
            )
        )
        stdin_json.isatty = lambda: False  # type: ignore[method-assign]

        with (
            mock.patch.dict(os.environ, env_vars, clear=False),
            mock.patch("commands.harness.terminal_title.DEFAULT_TTY_PATH", fake_tty_file),
            mock.patch("commands.harness.terminal_title.os.isatty", return_value=True),
            mock.patch("sys.stdin", stdin_json),
            mock.patch("sys.stdout", fake_stdout),
            mock.patch("sys.stderr", fake_stderr),
        ):
            os.environ.pop("NO_COLOR", None)
            os.environ.pop("EMU_HARNESS_NO_TITLE", None)
            run_harness_hook(argparse.Namespace(event=HookEvent.PRE_TOOL.value))

        # stdout must be pure JSON with zero ANSI/OSC escape sequences.
        stdout_raw = fake_stdout.getvalue()
        self.assertNotIn("\033", stdout_raw)
        self.assertNotIn("\007", stdout_raw)
        parsed_stdout = json.loads(stdout_raw)
        self.assertEqual(parsed_stdout["decision"], HookDecision.ALLOW.value)

        # /dev/tty received the escape sequence; stderr received nothing.
        tty_written = fake_tty_file.read_text(encoding="utf-8")
        self.assertEqual(
            tty_written,
            f"{XTWINOPS_PUSH_TITLE}{OSC_TITLE_PREFIX}⚡ git rev-parse HEAD^{{commit}} (0s){OSC_TITLE_SUFFIX}",
        )
        self.assertEqual(fake_stderr.getvalue(), "")

    def test_silent_noop_when_neither_tty_nor_stderr_is_interactive(self) -> None:
        non_tty_stderr = FakeTtyStream(is_tty=False)
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=self.missing_tty,
            stderr=non_tty_stderr,
            env=XTERM_ENV,
            clock=self.clock,
            enable_ticker=False,
        )
        seq = updater.handle_event(
            HookEvent.SESSION_START.value, {"conversationId": CONVERSATION_ID}
        )
        self.assertIsNone(seq)
        self.assertEqual(non_tty_stderr.getvalue(), "")

    def test_denied_pre_tool_command_does_not_display_blocked_command(self) -> None:
        updater, stream = self._make_stderr_updater()
        res = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "gerrit vote 12345 Code-Review +1"},
                },
            },
            store=self.ledger_store,
            title_updater=updater,
        )
        self.assertEqual(res["decision"], HookDecision.DENY.value)
        self.assertNotIn("gerrit vote", stream.getvalue())
        self.assertIn("🧠 Thinking... (0s)", stream.getvalue())

    def test_post_tool_ignores_non_shell_tools_with_wildcard_matcher(self) -> None:
        updater, _ = self._make_stderr_updater()
        post_out = dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {
                    "name": "custom_mcp_lookup",
                    "args": {"command": "emu-dev-cli launch emulator -avd leaked_avd"},
                },
                "result": "ok",
            },
            store=self.ledger_store,
            title_updater=updater,
        )
        self.assertEqual(post_out, {})
        self.assertEqual(
            self.ledger_store.load(CONVERSATION_ID)["active_emulators"], []
        )

    def test_ticker_loop_updates_elapsed_seconds_and_exits_on_stop(self) -> None:
        fake_tty = self.base_path / "ticker_tty"
        fake_tty.write_text("", encoding="utf-8")
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=fake_tty,
            env=XTERM_ENV,
            clock=self.clock,
            require_tty_isatty=False,
            enable_ticker=False,
        )
        updater.handle_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test //..."},
                },
            },
        )
        state = json.loads(
            updater.state_path(CONVERSATION_ID).read_text(encoding="utf-8")
        )
        token = state["token"]

        steps = iter([1.0, 17.0, 1.0])

        def fake_sleep(_interval: float) -> None:
            try:
                delta = next(steps)
                self.clock.advance(delta)
            except StopIteration:
                updater.handle_event(
                    HookEvent.STOP.value,
                    {"conversationId": CONVERSATION_ID},
                    response={"decision": HookDecision.STOP.value},
                )

        rc = run_ticker_loop(
            state_path=updater.state_path(CONVERSATION_ID),
            lock_path=updater.lock_path(CONVERSATION_ID),
            tty_path=fake_tty,
            token=token,
            require_isatty=False,
            clock=self.clock,
            sleeper=fake_sleep,
        )
        self.assertEqual(rc, 0)
        final_tty_content = fake_tty.read_text(encoding="utf-8")
        self.assertIn(build_osc_title("⚡ bazel test //... (1s)"), final_tty_content)
        self.assertIn(build_osc_title("⚡ bazel test //... (18s)"), final_tty_content)
        self.assertTrue(
            final_tty_content.endswith(
                f"{OSC_TITLE_PREFIX}✅ Done{OSC_TITLE_SUFFIX}{XTWINOPS_POP_TITLE}"
            ),
            msg=repr(final_tty_content),
        )

    def test_subprocess_ticker_stops_cleanly_without_overwriting_done(self) -> None:
        fake_tty = self.base_path / "subproc_tty"
        fake_tty.write_text("", encoding="utf-8")
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=fake_tty,
            env=XTERM_ENV,
            require_tty_isatty=False,
            enable_ticker=True,
        )
        updater.handle_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test //emulator/..."},
                },
            },
        )
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            if "(1s)" in fake_tty.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        self.assertIn("⚡ bazel test //emulator/... (1s)", fake_tty.read_text(encoding="utf-8"))

        updater.handle_event(
            HookEvent.STOP.value,
            {"conversationId": CONVERSATION_ID},
            response={"decision": HookDecision.STOP.value},
        )
        time.sleep(0.35)
        self.assertTrue(
            fake_tty.read_text(encoding="utf-8").endswith(
                f"{OSC_TITLE_PREFIX}✅ Done{OSC_TITLE_SUFFIX}{XTWINOPS_POP_TITLE}"
            )
        )

        self.assertEqual(updater.clear_state(CONVERSATION_ID), 1)
        self.assertFalse(updater.state_path(CONVERSATION_ID).exists())
        # Lock files are kept so concurrent waiters never lock a different inode.
        self.assertTrue(updater.lock_path(CONVERSATION_ID).exists())

    def test_ticker_stops_when_jetski_loses_terminal_foreground(self) -> None:
        fake_tty = self.base_path / "fg_tty"
        fake_tty.write_text("", encoding="utf-8")
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=fake_tty,
            env=XTERM_ENV,
            clock=self.clock,
            require_tty_isatty=False,
            enable_ticker=False,
        )
        updater.handle_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {"name": "run_command", "args": {"CommandLine": "sleep 600"}},
            },
        )
        token = json.loads(
            updater.state_path(CONVERSATION_ID).read_text(encoding="utf-8")
        )["token"]
        jetski_pgrp, shell_pgrp = 4242, 777
        foreground = iter([jetski_pgrp, shell_pgrp])

        with mock.patch.object(
            terminal_title, "_terminal_foreground_pgrp", side_effect=lambda _fd: next(foreground)
        ):
            rc = run_ticker_loop(
                state_path=updater.state_path(CONVERSATION_ID),
                lock_path=updater.lock_path(CONVERSATION_ID),
                tty_path=fake_tty,
                token=token,
                require_isatty=False,
                owner_pgrp=jetski_pgrp,
                clock=self.clock,
                sleeper=lambda _interval: self.clock.advance(1.0),
            )

        self.assertEqual(rc, 0)
        tty_content = fake_tty.read_text(encoding="utf-8")
        self.assertIn(build_osc_title("⚡ sleep 600 (1s)"), tty_content)
        self.assertNotIn("(2s)", tty_content)

    def test_handle_event_closes_tty_sink_when_transition_fails(self) -> None:
        fake_tty = self.base_path / "leak_tty"
        fake_tty.write_text("", encoding="utf-8")
        updater = TerminalTitleUpdater(
            base_dir=self.harness_dir,
            tty_path=fake_tty,
            env=XTERM_ENV,
            clock=self.clock,
            require_tty_isatty=False,
            enable_ticker=False,
        )
        opened_sinks: List[object] = []
        real_open = terminal_title._open_tty_sink

        def tracking_open(*args: object, **kwargs: object) -> object:
            sink = real_open(*args, **kwargs)  # type: ignore[arg-type]
            if sink is not None:
                opened_sinks.append(sink)
            return sink

        with (
            mock.patch.object(terminal_title, "_open_tty_sink", side_effect=tracking_open),
            mock.patch.object(
                TerminalTitleUpdater, "_compute_transition", side_effect=RuntimeError("boom")
            ),
        ):
            with self.assertRaises(RuntimeError):
                updater.handle_event(
                    HookEvent.SESSION_START.value, {"conversationId": CONVERSATION_ID}
                )

        self.assertEqual(len(opened_sinks), 1)
        self.assertTrue(opened_sinks[0].closed)  # type: ignore[attr-defined]

    def test_stop_without_prior_push_does_not_pop_foreign_title(self) -> None:
        updater, stream = self._make_stderr_updater()
        seq = updater.handle_event(
            HookEvent.STOP.value,
            {"conversationId": "conv-never-pushed"},
            response={"decision": HookDecision.STOP.value},
        )
        self.assertEqual(seq, build_osc_title("✅ Done"))
        self.assertNotIn(XTWINOPS_POP_TITLE, stream.getvalue())

    def test_harness_clear_removes_title_state(self) -> None:
        updater, _ = self._make_stderr_updater()
        updater.handle_event(HookEvent.SESSION_START.value, {"conversationId": CONVERSATION_ID})
        state_path = updater.state_path(CONVERSATION_ID)
        self.assertTrue(state_path.is_file())

        with (
            mock.patch.dict(os.environ, {"EMU_DEV_CLI_HARNESS_DIR": str(self.harness_dir)}),
            mock.patch("sys.stdout", io.StringIO()),
            self.assertRaises(SystemExit) as exit_ctx,
        ):
            run_harness_clear(argparse.Namespace(conversation=CONVERSATION_ID, json=True))

        self.assertEqual(exit_ctx.exception.code, 0)
        self.assertFalse(state_path.exists())

    def test_non_shell_ticker_launch_inherits_console_without_resource_warning(self) -> None:
        missing_shell = self.base_path / "no_such_sh"
        with (
            mock.patch.object(terminal_title, "POSIX_SHELL_PATH", missing_shell),
            mock.patch.object(
                terminal_title.subprocess, "Popen", wraps=subprocess.Popen
            ) as popen_spy,
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            terminal_title._spawn_ticker_process(
                state_path=self.base_path / "missing_state.json",
                lock_path=self.base_path / "missing_state.lock",
                tty_path=self.missing_tty,
                token="stale-token",
                require_isatty=True,
                owner_pgrp=None,
                env=dict(os.environ),
            )

        popen_spy.assert_called_once()
        self.assertNotIn("creationflags", popen_spy.call_args.kwargs)
        self.assertEqual(
            [w for w in caught if issubclass(w.category, ResourceWarning)], []
        )

    def test_title_updater_error_fails_open_without_breaking_hook_response(self) -> None:
        failing_updater = mock.Mock(spec=TerminalTitleUpdater)
        failing_updater.handle_event.side_effect = RuntimeError("simulated tty failure")
        res = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": CONVERSATION_ID,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "git status"},
                },
            },
            store=self.ledger_store,
            title_updater=failing_updater,
        )
        self.assertEqual(res, {"decision": HookDecision.ALLOW.value})


if __name__ == "__main__":
    unittest.main()
