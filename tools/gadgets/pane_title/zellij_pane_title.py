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

"""Updates the current terminal/Zellij/tmux pane title via its ancestor PTY."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

STATE_DIR = Path(f"/tmp/jetski_zellij_titles_{os.getuid()}")
STATUS_PREFIX_RE = re.compile(r"^[⏳✅🔄🔧]\s*")
BUG_ID_RE = re.compile(r"(?:b/|issues/)(\d{6,})")


def find_pane_pty() -> str | None:
    """Walks /proc up to the multiplexer/session boundary to find the pane PTY."""
    pid = os.getpid()
    outermost_pty: str | None = None

    visited: set[int] = set()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            left, right = stat_text.rsplit(")", 1)
            comm = left.split("(", 1)[1]
            ppid = int(right.split()[1])
        except (OSError, IndexError, ValueError):
            break

        if comm.startswith(("zellij", "tmux", "sshd", "systemd")):
            break

        for fd in ("0", "1", "2", "12", "13"):
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
                if target.startswith("/dev/pts/"):
                    outermost_pty = target
                    break
            except OSError:
                continue

        pid = ppid

    return outermost_pty


def get_state_path(pane_pty: str, state_dir: Path = STATE_DIR) -> Path:
    session = os.environ.get("ZELLIJ_SESSION_NAME", "default")
    pane_id = os.environ.get("ZELLIJ_PANE_ID")
    pty_slug = pane_pty.replace("/dev/pts/", "pts_")
    key = (
        f"{session}_pane{pane_id}_{pty_slug}"
        if pane_id is not None
        else f"{session}_{pty_slug}"
    )
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return state_dir / f"{safe_key}.json"


def load_state(state_path: Path) -> dict[str, str]:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state_path: Path, title: str, conversation_id: str = "") -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = load_state(state_path)
        state["title"] = title
        if conversation_id:
            state["conversationId"] = conversation_id
        state_path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def extract_fallback_title(transcript_path: str) -> str:
    """Extracts a concise fallback title from the latest USER_INPUT step."""
    if not transcript_path:
        return ""
    path = Path(transcript_path)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if '"USER_INPUT"' not in line:
                continue
            step = json.loads(line)
            if step.get("type") != "USER_INPUT":
                continue
            content = step.get("content", "")
            content = re.sub(r"</?USER_REQUEST>", "", content).strip()
            if not content:
                continue
            bug_match = BUG_ID_RE.search(content)
            first_line = " ".join(content.splitlines()[0].split())
            if len(first_line) > 36:
                first_line = first_line[:33].rstrip() + "..."
            if bug_match and f"b/{bug_match.group(1)}" not in first_line:
                return f"b/{bug_match.group(1)}: {first_line}"[:42]
            return first_line
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def format_osc_sequence(full_title: str) -> str:
    sanitized = (
        full_title.replace("\033", "").replace("\007", "").replace("\n", " ").strip()
    )
    return f"\033]0;{sanitized}\007\033]2;{sanitized}\007"


def write_osc_title(pane_pty: str, full_title: str) -> None:
    seq = format_osc_sequence(full_title)
    try:
        with open(pane_pty, "w", encoding="utf-8") as tty:
            tty.write(seq)
            tty.flush()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage pane/tab title for Jetski.")
    parser.add_argument("title", nargs="?", help="Semantic task title to set.")
    parser.add_argument("--set", dest="set_title", help="Semantic task title to set.")
    parser.add_argument("--status", choices=["working", "done"], default="working")
    parser.add_argument(
        "--hook", choices=["pre", "stop"], help="Run as a Jetski lifecycle hook."
    )
    args = parser.parse_args(argv)

    pane_pty = find_pane_pty()

    if args.hook:
        raw_stdin = sys.stdin.read().strip()
        payload = json.loads(raw_stdin) if raw_stdin else {}
        if pane_pty:
            state_path = get_state_path(pane_pty)
            state = load_state(state_path)
            conv_id = payload.get("conversationId", "")
            title = state.get("title", "")
            if not title:
                title = (
                    extract_fallback_title(payload.get("transcriptPath", ""))
                    or "Jetski"
                )
                save_state(state_path, title, conv_id)

            if args.hook == "pre":
                write_osc_title(pane_pty, f"⏳ {title}")
            elif args.hook == "stop":
                fully_idle = payload.get("fullyIdle", True)
                icon = "✅" if fully_idle else "⏳"
                write_osc_title(pane_pty, f"{icon} {title}")

        if args.hook == "pre":
            print("{}")
        else:
            print(json.dumps({"decision": "stop"}))
        return 0

    raw_title = args.set_title or args.title
    if not raw_title or not pane_pty:
        return 0

    clean_title = STATUS_PREFIX_RE.sub("", raw_title).strip()
    state_path = get_state_path(pane_pty)
    save_state(state_path, clean_title)
    icon = "✅" if args.status == "done" else "⏳"
    write_osc_title(pane_pty, f"{icon} {clean_title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
