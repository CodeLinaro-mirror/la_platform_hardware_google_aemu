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

"""Terminal window title updates via ANSI/xterm escape sequences for AEMU Harness hooks.

Presents live agent execution state in the terminal title bar when a controlling
TTY is available:
  - SessionStart:  `🧠 Session started` (after pushing original title via `\\033[22;0t`)
  - PreInvocation: `🧠 Thinking... ({elapsed}s)`
  - PreToolUse:    `⚡ {short_command} ({elapsed}s)`, `💾 {filename}`, or `🔧 {tool_name}`
  - PostToolUse:   records tool duration and transitions back to `🧠 Thinking... (0s)`
  - Stop:          `✅ Done` followed by `\\033[23;0t` to pop and restore original title

Writes strictly to `/dev/tty` (preferred) or `sys.stderr` when it is a TTY, and
never touches `sys.stdout` (which Jetski captures for JSON hook protocol output).
"""

import argparse
import contextlib
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any, BinaryIO, Callable, Dict, Iterator, List, Mapping, Optional, TextIO
import uuid
import warnings

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from commands.harness.code_guardrails import (  # noqa: E402
    extract_command_line,
    extract_file_path,
    extract_tool_name,
)
from commands.harness.constants import (  # noqa: E402
    FILE_EDIT_TOOL_NAMES,
    HookDecision,
    HookEvent,
    SHELL_COMMAND_TOOL_NAMES,
)
from commands.harness.ledger import (  # noqa: E402
    get_harness_base_dir,
    sanitize_conversation_id,
)
from commands.harness.shell_segments import iter_command_segments  # noqa: E402
from lib.path_utils import atomic_write_text, read_json_dict  # noqa: E402

OSC_TITLE_PREFIX: str = "\033]0;"
OSC_TITLE_SUFFIX: str = "\007"
XTWINOPS_PUSH_TITLE: str = "\033[22;0t"
XTWINOPS_POP_TITLE: str = "\033[23;0t"

TITLE_SESSION_STARTED: str = "🧠 Session started"
TITLE_THINKING_PREFIX: str = "🧠 Thinking..."
TITLE_COMMAND_PREFIX: str = "⚡ "
TITLE_FILE_EDIT_PREFIX: str = "💾 "
TITLE_OTHER_TOOL_PREFIX: str = "🔧 "
TITLE_DONE: str = "✅ Done"

DEFAULT_COMMAND_LABEL: str = "command"
DEFAULT_TOOL_LABEL: str = "tool"

ENV_TERM: str = "TERM"
ENV_NO_COLOR: str = "NO_COLOR"
ENV_NO_TITLE: str = "EMU_HARNESS_NO_TITLE"
ENV_NO_TICKER: str = "EMU_HARNESS_NO_TICKER"
DUMB_TERM_VALUE: str = "dumb"
FALSY_ENV_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

POSIX_TTY_PATH: Path = Path("/dev/tty")
WINDOWS_TTY_PATH: Path = Path("CONOUT$")
DEFAULT_TTY_PATH: Path = WINDOWS_TTY_PATH if os.name == "nt" else POSIX_TTY_PATH
POSIX_SHELL_PATH: Path = Path("/bin/sh")

TERMINAL_TITLE_SUBDIR: str = "terminal_title"
STATE_FILE_SUFFIX: str = ".json"
LOCK_FILE_SUFFIX: str = ".lock"
MAX_TITLE_TEXT_CHARS: int = 80
MAX_SHORT_COMMAND_CHARS: int = 48
MAX_TOOL_LABEL_CHARS: int = 48
TRUNCATION_ELLIPSIS: str = "..."

ASCII_CONTROL_MAX_ORD: int = 0x1F
ASCII_DEL_ORD: int = 0x7F
UNICODE_C1_MAX_ORD: int = 0x9F

TICKER_POLL_INTERVAL_SEC: float = 0.2
MAX_TICKER_DURATION_SEC: int = 3600
TICKER_LAUNCH_TIMEOUT_SEC: float = 2.0

_PURE_SETUP_SEGMENT_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(?:cd|pushd)\b[^;&|\r\n]*$"
    r"|^\s*(?:export\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s;&|]*)\s*)+$"
)
_LEADING_ENV_ASSIGNMENTS_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s;&|]*)\s+)+(?=\S)"
)


class TitlePhase(str, Enum):
    """Per-conversation execution phases reflected in the terminal title."""

    IDLE = "idle"
    THINKING = "thinking"
    COMMAND = "command"
    TOOL = "tool"
    DONE = "done"


@dataclass(frozen=True)
class _TitleTransition:
    """Computed escape sequence and persisted state for a single hook event."""

    sequence: str
    state: Dict[str, Any]
    should_tick: bool


@dataclass(frozen=True)
class _EmittedTitle:
    """A transition that reached the terminal, plus what a ticker needs to follow it."""

    transition: _TitleTransition
    wrote_to_tty: bool
    owner_pgrp: Optional[int]


def _is_control_codepoint(codepoint: int) -> bool:
    """Returns True for C0, DEL, or C1 control characters unsafe inside OSC titles."""
    return codepoint <= ASCII_CONTROL_MAX_ORD or (
        ASCII_DEL_ORD <= codepoint <= UNICODE_C1_MAX_ORD
    )


def sanitize_title_text(text: str, max_chars: int = MAX_TITLE_TEXT_CHARS) -> str:
    """Strips control characters, collapses whitespace, and bounds title length."""
    cleaned_chars = [
        " " if _is_control_codepoint(ord(ch)) else ch
        for ch in str(text or "")
    ]
    collapsed = " ".join("".join(cleaned_chars).split())
    if max_chars > len(TRUNCATION_ELLIPSIS) and len(collapsed) > max_chars:
        limit = max_chars - len(TRUNCATION_ELLIPSIS)
        return collapsed[:limit].rstrip() + TRUNCATION_ELLIPSIS
    return collapsed[:max_chars] if max_chars > 0 else ""


def build_osc_title(title: str) -> str:
    """Wraps sanitized `title` in an OSC 0 (`\\033]0;{title}\\007`) escape sequence."""
    return f"{OSC_TITLE_PREFIX}{sanitize_title_text(title)}{OSC_TITLE_SUFFIX}"


def format_elapsed_title(prefix_text: str, elapsed_sec: int) -> str:
    """Formats a timed phase title such as `🧠 Thinking... (4s)`."""
    bounded_elapsed = max(0, int(elapsed_sec))
    return f"{prefix_text} ({bounded_elapsed}s)"


def _elapsed_seconds(started_at: Any, now: float) -> int:
    """Returns whole seconds since `started_at`, or 0 if it is not a timestamp."""
    if not isinstance(started_at, (int, float)):
        return 0
    return max(0, int(now - float(started_at)))


def format_short_command(
    command: str,
    max_chars: int = MAX_SHORT_COMMAND_CHARS,
) -> str:
    """Extracts a concise, single-line summary of a shell command for the title bar."""
    segments = [seg.strip() for _, seg in iter_command_segments(command) if seg.strip()]
    if not segments:
        return sanitize_title_text(command, max_chars=max_chars) or DEFAULT_COMMAND_LABEL

    chosen = segments[0]
    for segment in segments:
        if not _PURE_SETUP_SEGMENT_PATTERN.match(segment):
            chosen = segment
            break

    stripped = _LEADING_ENV_ASSIGNMENTS_PATTERN.sub("", chosen).strip()
    candidate = stripped or chosen
    return sanitize_title_text(candidate, max_chars=max_chars) or DEFAULT_COMMAND_LABEL


def extract_edit_filename(payload: Dict[str, Any]) -> str:
    """Extracts the basename of a modified file across POSIX and Windows path styles."""
    raw_path = extract_file_path(payload)
    normalized = raw_path.replace("\\", "/").rstrip("/")
    basename = PurePosixPath(normalized).name if normalized else ""
    clean_name = sanitize_title_text(basename, max_chars=MAX_SHORT_COMMAND_CHARS)
    if clean_name:
        return clean_name
    fallback_tool = sanitize_title_text(
        extract_tool_name(payload), max_chars=MAX_TOOL_LABEL_CHARS
    )
    return fallback_tool or DEFAULT_TOOL_LABEL


def format_static_tool_title(payload: Dict[str, Any], tool_name: str) -> str:
    """Formats the untimed title for a file edit (`💾 {filename}`) or other tool (`🔧 {name}`)."""
    if tool_name in FILE_EDIT_TOOL_NAMES:
        return f"{TITLE_FILE_EDIT_PREFIX}{extract_edit_filename(payload)}"
    clean_tool = sanitize_title_text(tool_name, max_chars=MAX_TOOL_LABEL_CHARS)
    return f"{TITLE_OTHER_TOOL_PREFIX}{clean_tool or DEFAULT_TOOL_LABEL}"


def _is_env_flag_truthy(raw_value: Optional[str]) -> bool:
    """Returns True when an opt-out environment variable is set to a truthy value."""
    normalized = (raw_value or "").strip().lower()
    return bool(normalized and normalized not in FALSY_ENV_VALUES)


def is_terminal_title_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Returns True when environment variables allow ANSI/OSC terminal title updates."""
    effective_env = os.environ if env is None else env
    if (effective_env.get(ENV_NO_COLOR) or "") != "":
        return False
    if _is_env_flag_truthy(effective_env.get(ENV_NO_TITLE)):
        return False
    term = (effective_env.get(ENV_TERM) or "").strip()
    if not term or term.lower() == DUMB_TERM_VALUE:
        return False
    return True


def _close_fd_quietly(fd: int) -> None:
    """Closes `fd`, ignoring errors from descriptors that are already gone."""
    try:
        os.close(fd)
    except OSError:
        pass


def _close_quietly(sink: Optional[BinaryIO]) -> None:
    """Closes `sink` if present, ignoring errors from handles that are already gone."""
    if sink is None:
        return
    try:
        sink.close()
    except (OSError, ValueError):
        pass


def _open_tty_sink(tty_path: Path, require_isatty: bool = True) -> Optional[BinaryIO]:
    """Opens `tty_path` as an unbuffered binary sink, or returns None if it is not a usable TTY.

    On Windows, opening `CONOUT$` by name yields `_WindowsConsoleIO` (PEP 528), which hands
    the UTF-8 bytes to WriteConsoleW so emoji survive legacy console code pages. On POSIX,
    the descriptor is opened with `O_NOCTTY` and never created or truncated.
    """
    flags = os.O_WRONLY | getattr(os, "O_NOCTTY", 0)
    if not require_isatty:
        flags |= os.O_APPEND
    opener = None if os.name == "nt" else (lambda path, _flags: os.open(path, flags))
    try:
        sink = open(  # noqa: SIM115 - ownership is handed to the caller.
            str(tty_path), "wb" if require_isatty else "ab", buffering=0, opener=opener
        )
    except OSError:
        return None
    if not require_isatty:
        return sink
    try:
        is_tty = os.isatty(sink.fileno())
    except (OSError, ValueError):
        is_tty = False
    if not is_tty:
        _close_quietly(sink)
        return None
    return sink


def _write_sink_unbuffered(sink: BinaryIO, payload_bytes: bytes) -> bool:
    """Writes all `payload_bytes` to the unbuffered `sink` and always closes it."""
    try:
        view = memoryview(payload_bytes)
        written = 0
        while written < len(view):
            chunk = sink.write(view[written:])
            if not chunk:
                return False
            written += chunk
        return True
    except (OSError, ValueError):
        return False
    finally:
        _close_quietly(sink)


def _terminal_foreground_pgrp(fd: int) -> Optional[int]:
    """Returns the foreground process group of the terminal behind `fd`, if known."""
    tcgetpgrp = getattr(os, "tcgetpgrp", None)
    if tcgetpgrp is None:
        return None
    try:
        return tcgetpgrp(fd)
    except OSError:
        return None


def _is_stream_tty(stream: Optional[TextIO]) -> bool:
    """Safely checks whether `stream` is an open interactive TTY."""
    if stream is None or not hasattr(stream, "isatty"):
        return False
    try:
        return bool(stream.isatty())
    except (OSError, ValueError):
        return False


def _write_stream_flushed(stream: Optional[TextIO], sequence: str) -> bool:
    """Writes `sequence` to `stream` if it is a TTY and flushes immediately."""
    if not _is_stream_tty(stream) or stream is None:
        return False
    try:
        stream.write(sequence)
        stream.flush()
        return True
    except (OSError, ValueError):
        return False


@contextlib.contextmanager
def _state_file_lock(lock_path: Path) -> Iterator[None]:
    """Serializes state updates and `/dev/tty` writes across hooks and tickers."""
    if fcntl is None:
        yield
        return
    fd: Optional[int] = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        if fd is not None:
            _close_fd_quietly(fd)
            fd = None
    try:
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            _close_fd_quietly(fd)


def _write_json_quietly(path: Path, data: Dict[str, Any]) -> None:
    """Persists `data` as JSON; title state is cosmetic, so write failures are ignored."""
    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        pass


def _extract_conversation_id(payload: Dict[str, Any]) -> Optional[str]:
    """Extracts the conversation identifier from a Jetski hook payload."""
    raw = payload.get("conversationId") or payload.get("conversation_id")
    return str(raw) if raw is not None else None


class TerminalTitleUpdater:
    """Manages terminal title state, XTWINOPS stack push/pop, and OSC 0 output."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        *,
        tty_path: Optional[Path] = None,
        stderr: Optional[TextIO] = None,
        env: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.time,
        require_tty_isatty: bool = True,
        enable_ticker: Optional[bool] = None,
    ) -> None:
        root_dir = base_dir or get_harness_base_dir()
        self.title_dir = root_dir / TERMINAL_TITLE_SUBDIR
        self.tty_path = DEFAULT_TTY_PATH if tty_path is None else tty_path
        self._stderr = stderr
        self._env = env
        self._clock = clock
        self.require_tty_isatty = require_tty_isatty
        self._enable_ticker = enable_ticker

    def state_path(self, conversation_id: Optional[str]) -> Path:
        """Returns the JSON state file path for `conversation_id`."""
        safe_id = sanitize_conversation_id(conversation_id)
        return self.title_dir / f"{safe_id}{STATE_FILE_SUFFIX}"

    def lock_path(self, conversation_id: Optional[str]) -> Path:
        """Returns the advisory lock file path for `conversation_id`."""
        safe_id = sanitize_conversation_id(conversation_id)
        return self.title_dir / f"{safe_id}{LOCK_FILE_SUFFIX}"

    def _effective_env(self) -> Mapping[str, str]:
        return os.environ if self._env is None else self._env

    def _effective_stderr(self) -> Optional[TextIO]:
        return sys.stderr if self._stderr is None else self._stderr

    def _is_ticker_enabled(self) -> bool:
        if self._enable_ticker is not None:
            return self._enable_ticker
        return not _is_env_flag_truthy(self._effective_env().get(ENV_NO_TICKER))

    @staticmethod
    def _pushed_transition(
        state: Dict[str, Any],
        *,
        phase: TitlePhase,
        title: str,
        token: str,
        started_at: float,
        prefix_text: str = "",
        ticking: bool = False,
    ) -> _TitleTransition:
        """Builds a transition that keeps the user's original title pushed on the stack.

        The push sequence is only emitted when the stack has not been pushed yet, so
        every push is matched by exactly one pop on the final `Stop`.
        """
        push = "" if state.get("title_stack_pushed") else XTWINOPS_PUSH_TITLE
        return _TitleTransition(
            sequence=f"{push}{build_osc_title(title)}",
            state={
                "phase": phase.value,
                "active": ticking,
                "token": token,
                "started_at": started_at,
                "prefix_text": prefix_text,
                "title_stack_pushed": True,
            },
            should_tick=ticking,
        )

    def _thinking_transition(
        self,
        state: Dict[str, Any],
        now: float,
        token: str,
        *,
        preserve_timer: bool,
    ) -> _TitleTransition:
        prev_started = state.get("started_at")
        reuse_timer = (
            preserve_timer
            and state.get("phase") == TitlePhase.THINKING.value
            and isinstance(prev_started, (int, float))
        )
        started_at = float(prev_started) if reuse_timer else now
        return self._pushed_transition(
            state,
            phase=TitlePhase.THINKING,
            title=format_elapsed_title(TITLE_THINKING_PREFIX, _elapsed_seconds(started_at, now)),
            token=token,
            started_at=started_at,
            prefix_text=TITLE_THINKING_PREFIX,
            ticking=True,
        )

    def _pre_tool_transition(
        self,
        payload: Dict[str, Any],
        response: Optional[Dict[str, Any]],
        state: Dict[str, Any],
        now: float,
        token: str,
    ) -> _TitleTransition:
        if (response or {}).get("decision") == HookDecision.DENY.value:
            # A denied tool never runs, so do not advertise it in the title.
            return self._thinking_transition(state, now, token, preserve_timer=False)

        tool_name = extract_tool_name(payload)
        command = extract_command_line(payload)
        if tool_name in SHELL_COMMAND_TOOL_NAMES or (not tool_name and command):
            prefix_text = f"{TITLE_COMMAND_PREFIX}{format_short_command(command)}"
            return self._pushed_transition(
                state,
                phase=TitlePhase.COMMAND,
                title=format_elapsed_title(prefix_text, 0),
                token=token,
                started_at=now,
                prefix_text=prefix_text,
                ticking=True,
            )
        return self._pushed_transition(
            state,
            phase=TitlePhase.TOOL,
            title=format_static_tool_title(payload, tool_name),
            token=token,
            started_at=now,
        )

    @staticmethod
    def _stop_transition(
        response: Optional[Dict[str, Any]],
        state: Dict[str, Any],
        now: float,
        token: str,
    ) -> Optional[_TitleTransition]:
        """Returns the `✅ Done` + pop transition, or None when the turn continues."""
        if (response or {}).get("decision") == HookDecision.CONTINUE.value:
            return None
        pop = XTWINOPS_POP_TITLE if state.get("title_stack_pushed") else ""
        return _TitleTransition(
            sequence=f"{build_osc_title(TITLE_DONE)}{pop}",
            state={
                "phase": TitlePhase.DONE.value,
                "active": False,
                "token": token,
                "started_at": now,
                "prefix_text": "",
                "title_stack_pushed": False,
            },
            should_tick=False,
        )

    def _compute_transition(
        self,
        event: str,
        payload: Dict[str, Any],
        response: Optional[Dict[str, Any]],
        prior_state: Optional[Dict[str, Any]],
    ) -> Optional[_TitleTransition]:
        state = prior_state or {}
        now = self._clock()
        token = uuid.uuid4().hex

        if event == HookEvent.SESSION_START.value:
            return self._pushed_transition(
                state,
                phase=TitlePhase.IDLE,
                title=TITLE_SESSION_STARTED,
                token=token,
                started_at=now,
            )
        if event == HookEvent.PRE_INVOCATION.value:
            return self._thinking_transition(state, now, token, preserve_timer=True)
        if event == HookEvent.PRE_TOOL.value:
            return self._pre_tool_transition(payload, response, state, now, token)
        if event == HookEvent.POST_TOOL.value:
            transition = self._thinking_transition(state, now, token, preserve_timer=False)
            transition.state["last_tool_elapsed_sec"] = _elapsed_seconds(
                state.get("started_at"), now
            )
            return transition
        if event == HookEvent.STOP.value:
            stop = self._stop_transition(response, state, now, token)
            if stop is not None:
                return stop
            # A Stop hook auto-continuation keeps the turn (and the pushed title) alive.
            return self._thinking_transition(state, now, token, preserve_timer=False)
        return None

    def _emit_locked(
        self,
        event: str,
        payload: Dict[str, Any],
        response: Optional[Dict[str, Any]],
        state_path: Path,
    ) -> Optional[_EmittedTitle]:
        """Computes, persists, and writes the title for `event`; caller holds the state lock."""
        tty_sink = _open_tty_sink(self.tty_path, require_isatty=self.require_tty_isatty)
        try:
            stderr = self._effective_stderr()
            if tty_sink is None and not _is_stream_tty(stderr):
                return None
            transition = self._compute_transition(
                event, payload, response, read_json_dict(state_path)
            )
            if transition is None:
                return None
            _write_json_quietly(state_path, transition.state)

            owner_pgrp = None if tty_sink is None else _terminal_foreground_pgrp(tty_sink.fileno())
            sink, tty_sink = tty_sink, None  # _write_sink_unbuffered takes ownership.
            wrote_to_tty = sink is not None and _write_sink_unbuffered(
                sink, transition.sequence.encode("utf-8", errors="replace")
            )
            if not wrote_to_tty and not _write_stream_flushed(stderr, transition.sequence):
                return None
            return _EmittedTitle(transition, wrote_to_tty, owner_pgrp)
        finally:
            _close_quietly(tty_sink)

    def handle_event(
        self,
        event: str,
        payload: Dict[str, Any],
        response: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Updates terminal title state and emits escape sequences when a TTY is available."""
        if not is_terminal_title_enabled(self._effective_env()):
            return None

        conversation_id = _extract_conversation_id(payload)
        state_path = self.state_path(conversation_id)
        lock_path = self.lock_path(conversation_id)
        with _state_file_lock(lock_path):
            emitted = self._emit_locked(event.strip().lower(), payload, response, state_path)
        if emitted is None:
            return None

        transition = emitted.transition
        if transition.should_tick and emitted.wrote_to_tty and self._is_ticker_enabled():
            _spawn_ticker_process(
                state_path=state_path,
                lock_path=lock_path,
                tty_path=self.tty_path,
                token=str(transition.state["token"]),
                require_isatty=self.require_tty_isatty,
                owner_pgrp=emitted.owner_pgrp,
                env=self._effective_env(),
            )
        return transition.sequence

    def clear_state(self, conversation_id: Optional[str] = None) -> int:
        """Deletes persisted title state for one conversation, or for all when omitted.

        Each state file is removed while holding its lock, and lock files are kept, so
        a hook or ticker waiting on a lock never ends up locking a different inode.
        """
        if conversation_id:
            state_paths = [self.state_path(conversation_id)]
        elif self.title_dir.is_dir():
            state_paths = sorted(self.title_dir.glob(f"*{STATE_FILE_SUFFIX}"))
        else:
            state_paths = []

        removed = 0
        for state_path in state_paths:
            with _state_file_lock(state_path.with_suffix(LOCK_FILE_SUFFIX)):
                try:
                    state_path.unlink()
                except OSError:
                    continue
                removed += 1
        return removed


def _spawn_ticker_process(
    *,
    state_path: Path,
    lock_path: Path,
    tty_path: Path,
    token: str,
    require_isatty: bool,
    owner_pgrp: Optional[int],
    env: Mapping[str, str],
) -> None:
    """Spawns a detached-stdio background loop to refresh `({elapsed}s)` once per second."""
    cmd: List[str] = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--ticker",
        "--state-file",
        str(state_path),
        "--lock-file",
        str(lock_path),
        "--tty-path",
        str(tty_path),
        "--token",
        token,
    ]
    if owner_pgrp is not None:
        cmd += ["--owner-pgrp", str(owner_pgrp)]
    if not require_isatty:
        cmd.append("--allow-non-tty-file")

    child_env = dict(env)
    try:
        if os.name != "nt" and POSIX_SHELL_PATH.is_file():
            # `sh` backgrounds the ticker and exits at once, so it is reaped here (no
            # zombie, no ResourceWarning) and the ticker never holds the hook's pipes.
            subprocess.run(
                [str(POSIX_SHELL_PATH), "-c", '"$@" </dev/null >/dev/null 2>&1 &', "sh", *cmd],
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                check=False,
                timeout=TICKER_LAUNCH_TIMEOUT_SEC,
            )
            return
        # No creation flags: the ticker must inherit the parent's console so that its
        # CONOUT$ writes reach the user's terminal window.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            ticker = subprocess.Popen(
                cmd,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            del ticker  # Intentionally detached; drop it while the warning is suppressed.
    except (OSError, subprocess.SubprocessError):
        pass


def _refresh_ticker_title(
    state_path: Path,
    tty_path: Path,
    token: str,
    *,
    require_isatty: bool,
    owner_pgrp: Optional[int],
    last_elapsed: int,
    now: float,
) -> Optional[int]:
    """Writes one elapsed-time refresh; returns the latest elapsed second, or None to stop."""
    state = read_json_dict(state_path)
    if not state or not state.get("active") or state.get("token") != token:
        return None
    prefix_text = str(state.get("prefix_text") or "").strip()
    if not prefix_text or not isinstance(state.get("started_at"), (int, float)):
        return None
    elapsed = _elapsed_seconds(state["started_at"], now)
    if elapsed <= last_elapsed:
        return last_elapsed

    sink = _open_tty_sink(tty_path, require_isatty=require_isatty)
    if sink is None:
        return None
    if owner_pgrp is not None and _terminal_foreground_pgrp(sink.fileno()) != owner_pgrp:
        # Jetski no longer owns the terminal (it exited or was suspended without a
        # Stop hook), so stop instead of overwriting the shell's title.
        _close_quietly(sink)
        return None
    sequence = build_osc_title(format_elapsed_title(prefix_text, elapsed))
    if not _write_sink_unbuffered(sink, sequence.encode("utf-8", errors="replace")):
        return None
    return elapsed


def run_ticker_loop(
    state_path: Path,
    lock_path: Path,
    tty_path: Path,
    token: str,
    *,
    require_isatty: bool = True,
    owner_pgrp: Optional[int] = None,
    poll_interval_sec: float = TICKER_POLL_INTERVAL_SEC,
    max_duration_sec: int = MAX_TICKER_DURATION_SEC,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Refreshes the terminal title once per elapsed second until `token` changes."""
    last_elapsed = 0
    loop_started = clock()
    while (clock() - loop_started) < max_duration_sec:
        sleeper(poll_interval_sec)
        with _state_file_lock(lock_path):
            refreshed = _refresh_ticker_title(
                state_path,
                tty_path,
                token,
                require_isatty=require_isatty,
                owner_pgrp=owner_pgrp,
                last_elapsed=last_elapsed,
                now=clock(),
            )
        if refreshed is None:
            return 0
        last_elapsed = refreshed
    return 0


def _main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for the internal background elapsed-time ticker process."""
    parser = argparse.ArgumentParser(description="AEMU Harness terminal title ticker")
    parser.add_argument("--ticker", action="store_true", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--tty-path", type=Path, required=True)
    parser.add_argument("--token", type=str, required=True)
    parser.add_argument("--owner-pgrp", type=int, default=None)
    parser.add_argument("--allow-non-tty-file", action="store_true")
    args = parser.parse_args(argv)
    return run_ticker_loop(
        state_path=args.state_file,
        lock_path=args.lock_file,
        tty_path=args.tty_path,
        token=args.token,
        require_isatty=not args.allow_non_tty_file,
        owner_pgrp=args.owner_pgrp,
    )


if __name__ == "__main__":
    raise SystemExit(_main())
