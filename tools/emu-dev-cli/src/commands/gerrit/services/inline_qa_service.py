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

"""Jetski transcript parsing, inline code question extraction, and AI analysis orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any
import urllib.request

from clients.agent_runtime_service import ProdAgentRuntimeService

from commands.gerrit.ai_analysis import (
    build_ai_analysis_prompt,
    parse_ai_analysis_from_conversation,
)
from commands.gerrit.services.diff_parser import (
    GerritDiffService,
    GerritServiceBase,
    extract_enclosing_function_from_diff_lines,
    extract_symbol_from_code_line,
)
from commands.gerrit.services.context import (
    DEFAULT_SHARED_GERRIT_RO_DIR,
    DEFAULT_SHARED_GERRIT_RW_DIR,
)
from commands.gerrit.services.queue_service import GerritQueueService
from core.process import SubprocessRunner
from lib.gerrit_api import DEFAULT_GERRIT_HOST, parse_cl_identifier

_SHARED_CTX_OPT_RE = (
    r"(?:\[SHARED_GROUP_CONTEXT(?P<shared_meta>[^\]]*)\].*?\[/SHARED_GROUP_CONTEXT\]\s*)?"
)
_QUESTION_HEAD_RE = r"\*\*Question(?:\s*\(@(?P<author>[a-zA-Z0-9._-]+)\))?:\*\*\s*"

_CODE_QUESTION_PROMPT_RE = re.compile(
    r"Regarding Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    r"```[^\n]*\n(?P<snippet>.*?)\n```\s*"
    + _SHARED_CTX_OPT_RE
    + _QUESTION_HEAD_RE
    + r"(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)

_CODE_FOLLOWUP_PROMPT_RE = re.compile(
    r"Follow-up on Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    + _SHARED_CTX_OPT_RE
    + _QUESTION_HEAD_RE
    + r"(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)

_SHARED_META_KV_RE = re.compile(r"([a-zA-Z_]+)=([^\s\]]+)")
_STRIP_SHARED_CTX_BLOCK_RE = re.compile(
    r"\[SHARED_GROUP_CONTEXT[^\]]*\].*?\[/SHARED_GROUP_CONTEXT\]\s*",
    re.DOTALL,
)

MAX_RECENT_BRAIN_DIRS = 60
LS_CONNECT_RPC_TIMEOUT_SEC = 10
MAX_SHARED_CONTEXT_TURNS = 12
MAX_SHARED_TURN_REPLY_CHARS = 1200


def _normalize_author_identity(
    raw_author: str = "", default_viewer: str = ""
) -> tuple[str, str]:
    """Resolves `(author_ldap, author_email)` for attributing shared Q&A turns."""
    candidate = (raw_author or default_viewer or os.environ.get("USER") or "reviewer").strip().lower()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if "@" in candidate:
        ldap = candidate.split("@", 1)[0].strip() or "reviewer"
        return ldap, candidate
    ldap = candidate or "reviewer"
    email = f"{ldap}@google.com" if ldap != "reviewer" else ""
    return ldap, email


def _parse_shared_group_meta(raw_meta: str | None) -> dict[str, str]:
    """Parses `key=value` attributes from a `[SHARED_GROUP_CONTEXT ...]` header."""
    if not raw_meta:
        return {}
    return {m.group(1): m.group(2) for m in _SHARED_META_KV_RE.finditer(raw_meta)}


def build_shared_group_context_block(
    thread_id: str,
    turn_offset: int,
    author: str,
    prior_turns: list[dict[str, Any]],
) -> str:
    """Formats prior multi-human Q&A turns so a teammate's agent continues with full context."""
    if not prior_turns:
        return ""
    safe_tid = (thread_id or "shared").strip()
    safe_author = (author or "reviewer").strip()
    lines = [
        f"[SHARED_GROUP_CONTEXT thread_id={safe_tid} turn_offset={max(0, int(turn_offset))} author={safe_author}]",
        "Prior Group Analysis Transcript on this code block:",
    ]
    for idx, turn in enumerate(prior_turns[-MAX_SHARED_CONTEXT_TURNS:], 1):
        t_author = str(turn.get("author") or "reviewer").strip()
        q_text = str(turn.get("question") or "").strip()
        r_text = str(turn.get("reply") or "").strip()
        if len(r_text) > MAX_SHARED_TURN_REPLY_CHARS:
            r_text = r_text[:MAX_SHARED_TURN_REPLY_CHARS] + "…"
        lines.append(f"Turn {idx} (@{t_author}): {q_text}")
        if r_text:
            lines.append(f"Agent Reply {idx}: {r_text}")
    lines.append("[/SHARED_GROUP_CONTEXT]\n\n")
    return "\n".join(lines)


def _collect_thread_participants(
    turns: list[dict[str, Any]], fallback_author: str = ""
) -> list[str]:
    """Returns ordered unique human LDAPs that participated across `turns`."""
    participants: list[str] = []
    for turn in turns:
        ldap = str(turn.get("author") or "").strip().lstrip("@").lower()
        if "@" in ldap:
            ldap = ldap.split("@", 1)[0]
        if ldap and ldap not in participants:
            participants.append(ldap)
    if not participants and fallback_author:
        clean_fb = fallback_author.strip().lstrip("@").lower().split("@", 1)[0]
        if clean_fb:
            participants.append(clean_fb)
    return participants


def build_code_question_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    code_snippet: str,
    question: str,
    *,
    side: str = "REVISION",
    author: str = "",
    shared_context_block: str = "",
) -> str:
    """Builds a structured Markdown prompt for asking an agent about a highlighted code block."""
    cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
    cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
    s_ln = max(1, int(start_line or 1))
    e_ln = max(s_ln, int(end_line or s_ln))
    range_label = f"L{s_ln}" if s_ln == e_ln else f"L{s_ln}-L{e_ln}"
    side_note = " (PARENT / base)" if str(side).upper() == "PARENT" else ""
    cleaned_question = (
        (question or "").strip()
        or "Please explain this code block and check for any bugs or edge cases."
    )
    snippet = (code_snippet or "").rstrip()
    author_tag = f" (@{author.strip().lstrip('@')})" if author and shared_context_block else ""
    return (
        f"Regarding Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"```\n{snippet}\n```\n\n"
        f"{shared_context_block}"
        f"**Question{author_tag}:** {cleaned_question}"
    )


def build_code_followup_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    question: str,
    *,
    side: str = "REVISION",
    author: str = "",
    shared_context_block: str = "",
) -> str:
    """Builds a structured follow-up prompt within an existing inline code Q&A conversation."""
    cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
    cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
    s_ln = max(1, int(start_line or 1))
    e_ln = max(s_ln, int(end_line or s_ln))
    range_label = f"L{s_ln}" if s_ln == e_ln else f"L{s_ln}-L{e_ln}"
    side_note = " (PARENT / base)" if str(side).upper() == "PARENT" else ""
    cleaned_question = (question or "").strip()
    author_tag = f" (@{author.strip().lstrip('@')})" if author and shared_context_block else ""
    return (
        f"Follow-up on Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"{shared_context_block}"
        f"**Question{author_tag}:** {cleaned_question}"
    )


def _collect_planner_reply_until_next_prompt(
    steps: list[dict[str, Any]], start_idx: int
) -> tuple[str, int]:
    """Collects `PLANNER_RESPONSE` text after `start_idx` up to the next user/question step."""
    reply_parts: list[str] = []
    last_step_index = int(steps[start_idx].get("step_index", start_idx) or start_idx)
    for next_step in steps[start_idx + 1 :]:
        next_type = str(next_step.get("type") or "")
        next_content = str(next_step.get("content") or "")
        if next_type == "USER_INPUT":
            break
        if next_type == "SYSTEM_MESSAGE" and (
            "Regarding Gerrit CL `ag/" in next_content
            or "Follow-up on Gerrit CL `ag/" in next_content
        ):
            break
        step_idx = int(next_step.get("step_index", last_step_index) or last_step_index)
        last_step_index = max(last_step_index, step_idx)
        if next_type == "PLANNER_RESPONSE" and next_content.strip():
            reply_parts.append(next_content.strip())
    return "\n\n".join(reply_parts).strip(), last_step_index


def _extract_followup_question_info(
    step: dict[str, Any], default_author: str = ""
) -> tuple[str, str]:
    """Extracts `(question_text, author_ldap)` from a `USER_INPUT` or follow-up `SYSTEM_MESSAGE` step."""
    step_type = str(step.get("type") or "")
    content = str(step.get("content") or "")
    m_followup = _CODE_FOLLOWUP_PROMPT_RE.search(content)
    if m_followup:
        meta = _parse_shared_group_meta(m_followup.group("shared_meta"))
        q_author = (m_followup.group("author") or meta.get("author") or default_author or "").strip()
        return (m_followup.group("question") or "").strip(), q_author
    if step_type != "USER_INPUT":
        return "", ""
    cleaned = _STRIP_SHARED_CTX_BLOCK_RE.sub("", re.sub(r"</?USER_REQUEST>", "", content)).strip()
    if not cleaned or "Regarding Gerrit CL `ag/" in cleaned:
        return "", ""
    return cleaned, default_author


def _extract_followup_question_text(step: dict[str, Any]) -> str:
    """Extracts follow-up question text from a `USER_INPUT` or follow-up `SYSTEM_MESSAGE` step."""
    q_text, _ = _extract_followup_question_info(step)
    return q_text


def _collect_followup_turns(
    steps: list[dict[str, Any]],
    start_idx: int,
    next_root_idx: int,
    conv_id: str,
    root_author: str,
    turn_offset: int,
    skip_steps: set[int],
) -> list[dict[str, Any]]:
    """Extracts follow-up turns from `steps[start_idx:next_root_idx]`."""
    followups: list[dict[str, Any]] = []
    for sub_idx in range(start_idx, next_root_idx):
        sub_step = steps[sub_idx]
        sub_step_num = int(sub_step.get("step_index", sub_idx) or sub_idx)
        if sub_step_num in skip_steps:
            continue
        followup_q, f_author_raw = _extract_followup_question_info(sub_step, root_author)
        if not followup_q:
            continue
        f_author, f_email = _normalize_author_identity(f_author_raw or root_author)
        f_reply, f_end_step = _collect_planner_reply_until_next_prompt(steps, sub_idx)
        followups.append({
            "turn_index": turn_offset + 1 + len(followups),
            "step_index": sub_step_num,
            "end_step_index": f_end_step,
            "author": f_author,
            "author_email": f_email,
            "conversation_id": conv_id,
            "created_at": str(sub_step.get("created_at") or ""),
            "question": followup_q,
            "reply": f_reply,
            "status": "answered" if f_reply else "thinking",
        })
    return followups


def _build_transcript_question_item(
    steps: list[dict[str, Any]],
    idx: int,
    next_root_idx: int,
    conv_id: str,
    is_dedicated_qa_conv: bool,
    skip_steps: set[int],
    filter_cls: set[str] | None,
    default_author: str,
) -> dict[str, Any] | None:
    """Builds a single parsed inline question dictionary from `steps[idx:next_root_idx]`."""
    step = steps[idx]
    step_index = int(step.get("step_index", idx) or idx)
    if step_index in skip_steps:
        return None
    m = _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
    if not m or (filter_cls and m.group("cl").strip() not in filter_cls):
        return None
    cl_str, meta = m.group("cl").strip(), _parse_shared_group_meta(m.group("shared_meta"))
    root_author, root_email = _normalize_author_identity(
        m.group("author") or meta.get("author") or default_author
    )
    turn_offset, s_ln = max(0, int(meta.get("turn_offset") or 0)), max(1, int(m.group("start_line") or 1))
    e_ln, first_q = max(s_ln, int(m.group("end_line") or s_ln)), (m.group("question") or "").strip()
    created_at, (reply_text, end_step_idx) = str(step.get("created_at") or ""), _collect_planner_reply_until_next_prompt(steps, idx)
    turns: list[dict[str, Any]] = [{
        "turn_index": turn_offset, "step_index": step_index, "end_step_index": end_step_idx,
        "author": root_author, "author_email": root_email, "conversation_id": conv_id,
        "created_at": created_at, "question": first_q, "reply": reply_text,
        "status": "answered" if reply_text else "thinking",
    }]
    if is_dedicated_qa_conv:
        turns.extend(_collect_followup_turns(steps, idx + 1, next_root_idx, conv_id, root_author, turn_offset, skip_steps))
    latest, item_id = turns[-1], meta.get("thread_id") or f"tx-q-{(conv_id or 'local')[:8]}-{step_index}"
    return {
        "id": item_id, "thread_id": item_id, "shared_thread_id": meta.get("thread_id") or "",
        "turn_offset": turn_offset, "conversation_id": conv_id,
        "conversations_by_user": {root_author: conv_id} if conv_id else {},
        "author": root_author, "author_email": root_email,
        "participants": _collect_thread_participants(turns, root_author),
        "step_index": step_index, "cl": int(cl_str) if cl_str.isdigit() else cl_str,
        "file_path": m.group("file_path").strip() or "/COMMIT_MSG",
        "line": e_ln, "start_line": s_ln, "end_line": e_ln,
        "side": "PARENT" if "PARENT" in (m.group("side_note") or "").upper() else "REVISION",
        "code_snippet": m.group("snippet") or "", "initial_question": first_q,
        "question": latest["question"], "reply": latest["reply"], "status": latest["status"],
        "turns": turns, "created_at": created_at,
    }


def parse_code_questions_from_transcript_lines(
    lines: list[str],
    conv_id: str = "",
    filter_cls: set[str] | None = None,
    ignored_steps: set[int] | None = None,
    default_author: str = "",
) -> list[dict[str, Any]]:
    """Extracts inline code block Q&A threads (including multi-turn follow-ups) from JSONL transcript lines."""
    steps: list[dict[str, Any]] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                steps.append(obj)
        except Exception:
            continue

    root_prompt_indices = [
        idx
        for idx, step in enumerate(steps)
        if str(step.get("type") or "") in ("SYSTEM_MESSAGE", "USER_INPUT")
        and "Regarding Gerrit CL `ag/" in str(step.get("content") or "")
        and _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
    ]
    is_dedicated_qa_conv = len(root_prompt_indices) == 1
    skip_steps = ignored_steps or set()
    results: list[dict[str, Any]] = []

    for pos, idx in enumerate(root_prompt_indices):
        next_root_idx = (
            root_prompt_indices[pos + 1] if pos + 1 < len(root_prompt_indices) else len(steps)
        )
        built = _build_transcript_question_item(
            steps,
            idx,
            next_root_idx,
            conv_id,
            is_dedicated_qa_conv,
            skip_steps,
            filter_cls,
            default_author,
        )
        if built is not None:
            results.append(built)
    return results


def _resolve_candidate_conversation_dirs(
    root: Path,
    conversation_id: str | None = None,
    extra_conversation_ids: set[str] | None = None,
    excluded_conversation_ids: set[str] | None = None,
) -> list[Path]:
    """Resolves tracked, active, and recently updated Jetski conversation directories."""
    excluded = excluded_conversation_ids or set()
    candidate_dirs: list[Path] = []
    seen: set[Path] = set()

    def _add_conv_id(cid: str) -> None:
        cleaned = (cid or "").strip()
        if not cleaned or cleaned in excluded:
            return
        d = root / cleaned
        if d.is_dir() and d not in seen:
            seen.add(d)
            candidate_dirs.append(d)

    for cid in sorted(extra_conversation_ids or set()):
        _add_conv_id(cid)
    _add_conv_id(
        str(
            conversation_id
            or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
            or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            or ""
        )
    )

    try:
        subdirs = [d for d in root.iterdir() if d.is_dir() and d.name not in excluded and d not in seen]
        subdirs.sort(
            key=lambda d: (
                (d / ".system_generated" / "logs" / "transcript_full.jsonl").stat().st_mtime
                if (d / ".system_generated" / "logs" / "transcript_full.jsonl").is_file()
                else 0.0
            ),
            reverse=True,
        )
        candidate_dirs.extend(subdirs[:MAX_RECENT_BRAIN_DIRS])
    except OSError:
        pass
    return candidate_dirs


def _read_conv_transcript_questions(
    conv_dir: Path,
    filter_cls: set[str] | None,
    ignored_steps: set[int] | None,
    default_author: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Reads and parses `(dedupe_key, item)` inline Q&A items from a single conversation directory."""
    logs_dir = conv_dir / ".system_generated" / "logs"
    tx_file = logs_dir / "transcript_full.jsonl"
    if not tx_file.is_file():
        tx_file = logs_dir / "transcript.jsonl"
    if not tx_file.is_file():
        return []
    try:
        raw_text = tx_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if "Regarding Gerrit CL `ag/" not in raw_text:
        return []
    items = parse_code_questions_from_transcript_lines(
        raw_text.splitlines(),
        conv_id=conv_dir.name,
        filter_cls=filter_cls,
        ignored_steps=ignored_steps,
        default_author=default_author,
    )
    results: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        is_cont = int(item.get("turn_offset") or 0) > 0 or bool(item.get("shared_thread_id"))
        key = (
            f"conv:{conv_dir.name}:{item['step_index']}"
            if (len(items) == 1 or is_cont)
            else (
                f"{item['cl']}:{item['file_path']}:{item['start_line']}:"
                f"{item['end_line']}:{str(item.get('initial_question') or item['question']).strip()}"
            )
        )
        results.append((key, item))
    return results


def discover_transcript_code_questions(
    conversation_id: str | None = None,
    cl_numbers: list[str | int] | None = None,
    brain_root: Path | None = None,
    *,
    extra_conversation_ids: set[str] | None = None,
    excluded_conversation_ids: set[str] | None = None,
    ignored_steps_by_conv: dict[str, set[int]] | None = None,
    default_author: str = "",
) -> list[dict[str, Any]]:
    """Finds inline code questions and agent replies from Jetski conversation transcripts."""
    filter_cls = (
        {parse_cl_identifier(str(x), allow_head=False) for x in cl_numbers if str(x).strip()}
        if cl_numbers
        else None
    )
    root = brain_root or (Path.home() / ".gemini" / "jetski" / "brain")
    if not root.is_dir():
        return []
    by_key: dict[str, dict[str, Any]] = {}
    conv_dirs = _resolve_candidate_conversation_dirs(
        root,
        conversation_id=conversation_id,
        extra_conversation_ids=extra_conversation_ids,
        excluded_conversation_ids=excluded_conversation_ids,
    )
    for conv_dir in conv_dirs:
        pairs = _read_conv_transcript_questions(
            conv_dir,
            filter_cls,
            (ignored_steps_by_conv or {}).get(conv_dir.name),
            default_author,
        )
        for dedupe_key, item in pairs:
            prev = by_key.get(dedupe_key)
            if prev is None or (item.get("reply") and not prev.get("reply")):
                by_key[dedupe_key] = item
    return list(by_key.values())


def _merge_turn_lists(
    base_turns: list[dict[str, Any]],
    incoming_turns: list[dict[str, Any]],
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Merges `incoming_turns` into `base_turns` starting at `offset`."""
    merged = [dict(t) for t in base_turns]
    start_idx = max(0, int(offset or 0))
    for idx, inc in enumerate(incoming_turns):
        target_idx = start_idx + idx
        if target_idx < len(merged):
            cur = merged[target_idx]
            if inc.get("question"):
                cur["question"] = inc["question"]
            if inc.get("reply") and (not cur.get("reply") or inc.get("status") == "answered"):
                cur["reply"] = inc["reply"]
                cur["status"] = inc.get("status") or "answered"
            for field in ("author", "author_email", "created_at", "conversation_id", "step_index", "end_step_index"):
                if inc.get(field) not in (None, "") and not cur.get(field):
                    cur[field] = inc[field]
            cur["turn_index"] = target_idx
        else:
            new_turn = dict(inc)
            new_turn["turn_index"] = len(merged)
            merged.append(new_turn)
    for i, turn in enumerate(merged):
        turn["turn_index"] = i
    return merged


def _normalize_thread_record(
    raw: dict[str, Any],
    *,
    default_tid: str = "",
    default_author: str = "",
    is_shared: bool = False,
) -> dict[str, Any]:
    """Normalizes a thread dict from memory, transcript discovery, or shared JSON storage."""
    cl_raw = str(raw.get("cl") or "")
    cid = str(raw.get("conversation_id") or "")
    tid = str(raw.get("thread_id") or default_tid or raw.get("id") or (f"qa-{cid[:12]}" if cid else "qa-local"))
    author_ldap, author_email = _normalize_author_identity(raw.get("author") or raw.get("author_email") or default_author)
    turns: list[dict[str, Any]] = []
    for idx, t in enumerate(raw.get("turns") or []):
        t_copy = dict(t)
        t_ldap, t_email = _normalize_author_identity(t_copy.get("author") or t_copy.get("author_email") or author_ldap)
        t_copy.update({"turn_index": idx, "author": t_ldap, "author_email": t_copy.get("author_email") or t_email})
        turns.append(t_copy)
    latest = turns[-1] if turns else {"question": "", "reply": "", "status": "thinking"}
    participants = _collect_thread_participants(turns, author_ldap)
    convs_by_user = dict(raw.get("conversations_by_user") or {})
    if author_ldap and cid and author_ldap not in convs_by_user:
        convs_by_user[author_ldap] = cid
    return {
        "id": str(raw.get("id") or tid), "thread_id": tid,
        "shared_thread_id": str(raw.get("shared_thread_id") or ""),
        "turn_offset": int(raw.get("turn_offset") or 0), "conversation_id": cid,
        "cl": int(cl_raw) if cl_raw.isdigit() else cl_raw,
        "file_path": raw.get("file_path") or "/COMMIT_MSG",
        "line": int(raw.get("end_line") or raw.get("line") or 1),
        "start_line": int(raw.get("start_line") or 1),
        "end_line": int(raw.get("end_line") or raw.get("line") or 1),
        "side": raw.get("side") or "REVISION", "function_name": raw.get("function_name") or "",
        "code_snippet": raw.get("code_snippet") or "",
        "author": author_ldap, "author_email": raw.get("author_email") or author_email,
        "participants": participants, "conversations_by_user": convs_by_user,
        "is_shared": bool(is_shared or raw.get("is_shared") or len(participants) > 1),
        "question": str(latest.get("question") or raw.get("question") or ""),
        "reply": str(latest.get("reply") or ""), "status": str(latest.get("status") or "thinking"),
        "turns": turns,
    }


def _merge_thread_dicts(
    base_thread: dict[str, Any],
    inc_thread: dict[str, Any],
    *,
    is_shared: bool = False,
) -> dict[str, Any]:
    """Merges `inc_thread` (including continuation turns at `turn_offset`) into `base_thread`."""
    combined = dict(base_thread)
    offset = int(inc_thread.get("turn_offset") or 0)
    turns = _merge_turn_lists(
        combined.get("turns") or [],
        inc_thread.get("turns") or [],
        offset=offset,
    )
    combined["turns"] = turns
    if turns:
        combined["question"] = str(turns[-1].get("question") or "")
        combined["reply"] = str(turns[-1].get("reply") or "")
        combined["status"] = str(turns[-1].get("status") or "thinking")
    if inc_thread.get("function_name") and not combined.get("function_name"):
        combined["function_name"] = inc_thread["function_name"]
    if inc_thread.get("code_snippet") and not combined.get("code_snippet"):
        combined["code_snippet"] = inc_thread["code_snippet"]
    convs = dict(combined.get("conversations_by_user") or {})
    existing_cids = set(convs.values())
    for u_key, u_cid in (inc_thread.get("conversations_by_user") or {}).items():
        if u_cid and u_cid not in existing_cids:
            convs[u_key] = u_cid
            existing_cids.add(u_cid)
    inc_author = str(inc_thread.get("author") or "")
    inc_cid = str(inc_thread.get("conversation_id") or "")
    if inc_author and inc_cid and inc_cid not in existing_cids:
        convs[inc_author] = inc_cid
    combined["conversations_by_user"] = convs
    combined["participants"] = _collect_thread_participants(turns, str(combined.get("author") or ""))
    combined["is_shared"] = bool(
        is_shared
        or combined.get("is_shared")
        or inc_thread.get("is_shared")
        or len(combined["participants"]) > 1
    )
    return combined


def _write_json_safely(path: Path, payload: dict[str, Any]) -> None:
    """Writes `payload` as formatted UTF-8 JSON to `path`, ignoring permission/OS errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass




def _extract_agentapi_conversation_id(payload: dict[str, Any]) -> str:
    """Extracts `conversationId` from `agentapi new-conversation` JSON output."""
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("conversationId") or payload.get("conversation_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    resp = payload.get("response")
    if not isinstance(resp, dict):
        return ""
    new_conv = resp.get("newConversation") or resp.get("new_conversation") or {}
    if isinstance(new_conv, dict):
        cid = new_conv.get("conversationId") or new_conv.get("conversation_id")
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
    return ""


def _extract_agentapi_project_id(payload: dict[str, Any]) -> str:
    """Extracts `projectId` from `agentapi get-conversation-metadata` JSON output."""
    if not isinstance(payload, dict):
        return ""
    resp = payload.get("response")
    if not isinstance(resp, dict):
        return ""
    meta_evt = resp.get("conversationMetadata") or resp.get("conversation_metadata") or {}
    meta = meta_evt.get("metadata") if isinstance(meta_evt, dict) else {}
    if isinstance(meta, dict):
        pid = meta.get("projectId") or meta.get("project_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    return ""


def _call_local_language_server_rpc(method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invokes a Connect JSON RPC on the local Jetski Language Server when `ANTIGRAVITY_LS_ADDRESS` is set."""
    addr = (os.environ.get("ANTIGRAVITY_LS_ADDRESS") or "").strip()
    if not addr:
        return {"skipped": True}
    csrf = (os.environ.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    base_url = addr if addr.startswith(("http://", "https://")) else f"http://{addr}"
    url = f"{base_url.rstrip('/')}/exa.language_server_pb.LanguageServerService/{method_name}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LS_CONNECT_RPC_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            return json.loads(raw) if raw else {}
    except Exception as exc:
        return {"error": str(exc)}


def _run_sidecar_agentapi(args: list[str], project_id: str | None = None) -> dict[str, Any]:
    """Invokes `agentapi` for Jetski Web sidecar conversation integration via AgentRuntimeService."""
    return ProdAgentRuntimeService(SubprocessRunner()).invoke_agentapi_raw(
        args, project_id=project_id
    )


class GerritInlineQaService(GerritServiceBase):
    """Domain service for Jetski inline code Q&A threads and AI intangibles analysis."""

    def __init__(
        self,
        host: str = DEFAULT_GERRIT_HOST,
        default_viewer: str = "",
        default_query: str = "",
        sidecar_updater: Any | None = None,
        *,
        context: Any | None = None,
        diff_service: GerritDiffService | None = None,
        queue_service: GerritQueueService | None = None,
    ) -> None:
        super().__init__(
            host=host,
            default_viewer=default_viewer,
            default_query=default_query,
            sidecar_updater=sidecar_updater,
            context=context,
        )
        self._diff_service = diff_service
        self._queue_service = queue_service

    def _get_diff_service(self) -> GerritDiffService:
        if self._diff_service is None:
            self._diff_service = GerritDiffService(
                context=self.context, inline_qa_service=self
            )
        return self._diff_service

    def _get_queue_service(self) -> GerritQueueService:
        if self._queue_service is None:
            self._queue_service = GerritQueueService(
                context=self.context, diff_service=self._get_diff_service()
            )
        return self._queue_service

    def fetch_commit_diff_bundle(
        self,
        cl_number: str | int,
        patch_set: str | int | None = None,
        base: str | int | None = None,
    ) -> dict[str, Any]:
        """Delegates diff bundle lookup to `GerritDiffService`."""
        return self._get_diff_service().fetch_commit_diff_bundle(
            cl_number, patch_set=patch_set, base=base
        )

    def _resolve_change_patchset_and_project(
        self, cl_id: str, patchset: int | None, project: str
    ) -> tuple[int, str]:
        """Delegates `(patch_set, project)` resolution to `GerritQueueService`."""
        return self._get_queue_service()._resolve_change_patchset_and_project(
            cl_id, patchset, project
        )

    def _qa_disk_paths_for_cl(
        self, cl_id: str, *, for_write: bool = False
    ) -> list[tuple[Path, bool]]:
        """Returns `(path, is_shared)` JSON persistence files for inline Q&A on `cl_id`."""
        paths: list[tuple[Path, bool]] = []
        if self._qa_cache_dir:
            paths.append((Path(self._qa_cache_dir) / f"cl_{cl_id}.json", False))
        if self._shared_gerrit_dir:
            shared_root = Path(self._shared_gerrit_dir)
            paths.append((shared_root / "code_questions" / f"cl_{cl_id}.json", True))
            if not for_write and str(shared_root) == DEFAULT_SHARED_GERRIT_RW_DIR:
                ro_path = Path(DEFAULT_SHARED_GERRIT_RO_DIR) / "code_questions" / f"cl_{cl_id}.json"
                if ro_path != paths[-1][0]:
                    paths.append((ro_path, True))
        return paths

    def _load_persisted_qa_threads_for_cl(self, cl_id: str) -> list[dict[str, Any]]:
        """Loads and merges persisted/shared inline Q&A threads for `cl_id` from disk."""
        with self._qa_lock:
            deleted = set(self._deleted_qa_conv_ids) | set(self._analyzer_conv_ids)
        by_tid: dict[str, dict[str, Any]] = {}
        conv_to_tid: dict[str, str] = {}
        for disk_path, is_shared in self._qa_disk_paths_for_cl(cl_id, for_write=False):
            if not disk_path.is_file():
                continue
            try:
                raw_data = json.loads(disk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            raw_threads = (
                raw_data.get("threads")
                if isinstance(raw_data, dict)
                else (raw_data if isinstance(raw_data, list) else [])
            )
            for item in raw_threads or []:
                if not isinstance(item, dict) or item.get("deleted"):
                    continue
                cid = str(item.get("conversation_id") or "")
                if cid and cid in deleted:
                    continue
                norm = _normalize_thread_record(
                    item, default_author=self.default_viewer, is_shared=is_shared
                )
                tid = norm["thread_id"]
                target_tid = tid if tid in by_tid else conv_to_tid.get(cid, "")
                if target_tid and target_tid in by_tid:
                    by_tid[target_tid] = _merge_thread_dicts(
                        by_tid[target_tid], norm, is_shared=is_shared
                    )
                else:
                    by_tid[tid] = norm
                    target_tid = tid
                if cid:
                    conv_to_tid[cid] = target_tid
                for u_cid in (norm.get("conversations_by_user") or {}).values():
                    if u_cid:
                        conv_to_tid[str(u_cid)] = target_tid
        return list(by_tid.values())

    def _save_persisted_qa_threads_for_cl(
        self, cl_id: str, threads: list[dict[str, Any]]
    ) -> None:
        """Writes merged inline Q&A `threads` for `cl_id` to local cache and shared team storage."""
        disk_targets = self._qa_disk_paths_for_cl(cl_id, for_write=True)
        if not disk_targets:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        for disk_path, is_shared in disk_targets:
            serialized = []
            for th in threads:
                if is_shared:
                    th["is_shared"] = True
                copy_th = dict(th)
                serialized.append(copy_th)
            _write_json_safely(
                disk_path,
                {"cl": cl_id, "updated_at": now_iso, "threads": serialized},
            )

    def _remove_or_update_persisted_qa_thread(
        self,
        cl_id: str,
        thread_id: str,
        conv_id: str,
        remaining_turns: list[dict[str, Any]] | None,
    ) -> None:
        """Removes a deleted thread or truncates its turns in persisted Q&A JSON files."""
        existing = self._load_persisted_qa_threads_for_cl(cl_id)
        if not existing:
            return
        updated: list[dict[str, Any]] = []
        for th in existing:
            matches = (thread_id and th.get("thread_id") == thread_id) or (
                conv_id and th.get("conversation_id") == conv_id
            )
            if not matches:
                updated.append(th)
                continue
            if remaining_turns:
                mod = dict(th, turns=[dict(t) for t in remaining_turns])
                mod["question"] = str(remaining_turns[-1].get("question") or "")
                mod["reply"] = str(remaining_turns[-1].get("reply") or "")
                mod["status"] = str(remaining_turns[-1].get("status") or "thinking")
                updated.append(mod)
        self._save_persisted_qa_threads_for_cl(cl_id, updated)

    def _index_thread_into_map(
        self,
        by_tid: dict[str, dict[str, Any]],
        conv_to_tid: dict[str, str],
        item: dict[str, Any],
        *,
        is_shared: bool = False,
    ) -> str:
        """Merges or inserts `item` into `by_tid` and updates `conv_to_tid` mappings."""
        cid = str(item.get("conversation_id") or "")
        shared_tid = str(item.get("shared_thread_id") or "")
        tid = str(item.get("thread_id") or "")
        target_tid = (
            shared_tid
            if (shared_tid and shared_tid in by_tid)
            else (tid if tid in by_tid else conv_to_tid.get(cid, ""))
        )
        norm = _normalize_thread_record(
            item,
            default_tid=shared_tid or tid,
            default_author=self.default_viewer,
            is_shared=is_shared,
        )
        if target_tid and target_tid in by_tid:
            by_tid[target_tid] = _merge_thread_dicts(
                by_tid[target_tid], norm, is_shared=is_shared
            )
        else:
            target_tid = norm["thread_id"]
            by_tid[target_tid] = norm
        if cid:
            conv_to_tid[cid] = target_tid
        for u_cid in (by_tid[target_tid].get("conversations_by_user") or {}).values():
            if u_cid:
                conv_to_tid[str(u_cid)] = target_tid
        return target_tid

    def _merge_tracked_qa_state(
        self,
        transcript_items: list[dict[str, Any]],
        filter_cls: set[str] | None,
        persisted_threads: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Merges persisted/shared threads, in-memory `self._qa_threads`, and transcript items."""
        with self._qa_lock:
            threads_snapshot = [
                dict(t, turns=[dict(turn) for turn in (t.get("turns") or [])])
                for t in self._qa_threads.values()
                if not t.get("deleted")
            ]

        by_tid: dict[str, dict[str, Any]] = {}
        conv_to_tid: dict[str, str] = {}
        for pth in persisted_threads or []:
            cl_str = str(pth.get("cl") or "")
            if filter_cls and cl_str not in filter_cls:
                continue
            self._index_thread_into_map(
                by_tid, conv_to_tid, pth, is_shared=bool(pth.get("is_shared"))
            )

        for mth in threads_snapshot:
            cl_str = str(mth.get("cl") or "")
            if filter_cls and cl_str not in filter_cls:
                continue
            self._index_thread_into_map(by_tid, conv_to_tid, mth)

        sorted_tx = sorted(
            transcript_items,
            key=lambda it: (1 if int(it.get("turn_offset") or 0) > 0 else 0),
        )
        for tx_item in sorted_tx:
            cl_str = str(tx_item.get("cl") or "")
            if filter_cls and cl_str not in filter_cls:
                continue
            target_tid = self._index_thread_into_map(by_tid, conv_to_tid, tx_item)
            self._sync_cached_turns_from_transcript(
                target_tid, by_tid[target_tid].get("turns") or []
            )

        return list(by_tid.values())

    def _sync_cached_turns_from_transcript(
        self, thread_id: str, tx_turns: list[dict[str, Any]]
    ) -> None:
        """Updates cached `turns` on `thread_id` once transcript replies land on disk."""
        if not tx_turns:
            return
        with self._qa_lock:
            entry = self._qa_threads.get(thread_id)
            if entry and not entry.get("deleted"):
                entry["turns"] = [dict(t) for t in tx_turns]

    def get_code_questions(
        self,
        cl_numbers: list[str | int],
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns inline code questions and their Jetski agent replies for `cl_numbers`."""
        cleaned = [str(x).strip() for x in cl_numbers if str(x).strip()]
        filter_cls = (
            {parse_cl_identifier(x, allow_head=False) for x in cleaned}
            if cleaned
            else None
        )
        persisted_all: list[dict[str, Any]] = []
        extra_convs: set[str] = set()
        for cl_id in sorted(filter_cls or set()):
            cl_persisted = self._load_persisted_qa_threads_for_cl(cl_id)
            persisted_all.extend(cl_persisted)
            for pth in cl_persisted:
                if pth.get("conversation_id"):
                    extra_convs.add(str(pth["conversation_id"]))
                for u_cid in (pth.get("conversations_by_user") or {}).values():
                    if u_cid:
                        extra_convs.add(str(u_cid))

        with self._qa_lock:
            tracked = set(self._tracked_qa_conv_ids) | extra_convs
            deleted = set(self._deleted_qa_conv_ids) | set(self._analyzer_conv_ids)
            ignored = {k: set(v) for k, v in self._ignored_steps_by_conv.items()}
        tx_items = discover_transcript_code_questions(
            conversation_id=conversation_id,
            cl_numbers=cleaned or None,
            extra_conversation_ids=tracked,
            excluded_conversation_ids=deleted,
            ignored_steps_by_conv=ignored,
            default_author=self.default_viewer,
        )
        merged = self._merge_tracked_qa_state(
            tx_items, filter_cls, persisted_threads=persisted_all
        )
        for cl_id in sorted(filter_cls or set()):
            cl_threads = [t for t in merged if str(t.get("cl") or "") == cl_id]
            if cl_threads:
                self._save_persisted_qa_threads_for_cl(cl_id, cl_threads)
        return merged

    def _resolve_sidecar_project_id(self, explicit_project_id: str | None) -> str | None:
        """Resolves `projectId` from `explicit_project_id` or parent sidecar conversation metadata."""
        if explicit_project_id and explicit_project_id.strip():
            return explicit_project_id.strip()
        if self._cached_project_id:
            return self._cached_project_id
        parent_conv = (os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID") or "").strip()
        if not parent_conv:
            return None
        try:
            meta_res = _run_sidecar_agentapi(["get-conversation-metadata", parent_conv])
            pid = _extract_agentapi_project_id(meta_res)
            if pid:
                self._cached_project_id = pid
                return pid
        except Exception:
            pass
        return None

    def _find_qa_thread_entry(
        self, thread_id: str | None, conv_id: str | None, cl_id: str = ""
    ) -> dict[str, Any] | None:
        """Finds an active in-memory or persisted Q&A thread by `thread_id` or `conversation_id`."""
        with self._qa_lock:
            if thread_id and thread_id in self._qa_threads:
                entry = self._qa_threads[thread_id]
                if not entry.get("deleted"):
                    return entry
            if conv_id:
                for entry in self._qa_threads.values():
                    if not entry.get("deleted") and (
                        entry.get("conversation_id") == conv_id
                        or conv_id in (entry.get("conversations_by_user") or {}).values()
                    ):
                        return entry
        if cl_id:
            for pth in self._load_persisted_qa_threads_for_cl(cl_id):
                if (thread_id and pth.get("thread_id") == thread_id) or (
                    conv_id and pth.get("conversation_id") == conv_id
                ):
                    with self._qa_lock:
                        self._qa_threads[pth["thread_id"]] = dict(pth, deleted=False)
                        return self._qa_threads[pth["thread_id"]]
        return None

    def _is_conversation_turn_inflight(self, conv_id: str) -> bool:
        """Returns `True` if `conv_id` already has an unanswered code question in flight."""
        if not conv_id:
            return False
        with self._qa_lock:
            deleted = set(self._deleted_qa_conv_ids)
            ignored = {k: set(v) for k, v in self._ignored_steps_by_conv.items()}
        if conv_id in deleted:
            return False
        tx_items = discover_transcript_code_questions(
            conversation_id=conv_id,
            extra_conversation_ids={conv_id},
            excluded_conversation_ids=deleted,
            ignored_steps_by_conv=ignored,
            default_author=self.default_viewer,
        )
        matched_tx = next((it for it in tx_items if it.get("conversation_id") == conv_id), None)
        entry = self._find_qa_thread_entry(None, conv_id)
        if matched_tx is not None:
            tx_turns = list(matched_tx.get("turns") or [])
            mem_turns = list((entry.get("turns") or []) if entry else [])
            if len(mem_turns) > len(tx_turns):
                return not bool(str(mem_turns[-1].get("reply") or "").strip())
            if entry is not None and tx_turns:
                self._sync_cached_turns_from_transcript(entry["thread_id"], tx_turns)
            return matched_tx.get("status") == "thinking"
        if not entry:
            return False
        turns = entry.get("turns") or []
        return bool(turns and not str(turns[-1].get("reply") or "").strip())

    def _dispatch_code_qa_prompt(
        self,
        *,
        target_conv_id: str,
        title: str,
        prompt: str,
        project_id: str | None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Dispatches `prompt` to `target_conv_id` or starts an isolated background conversation."""
        try:
            if target_conv_id:
                res = _run_sidecar_agentapi(
                    ["send-message", target_conv_id, prompt],
                    project_id=project_id,
                )
                return True, target_conv_id, res
            resolved_pid = self._resolve_sidecar_project_id(project_id)
            res = _run_sidecar_agentapi(
                ["new-conversation", "--title", title, prompt],
                project_id=resolved_pid,
            )
            spawned_id = _extract_agentapi_conversation_id(res)
            return True, spawned_id, res
        except Exception:
            return False, target_conv_id, {}

    def _build_qa_dispatch_plan(
        self,
        *,
        cl_id: str,
        cleaned_file: str,
        s_ln: int,
        e_ln: int,
        side: str,
        code_snippet: str,
        cleaned_q: str,
        target_conv: str,
        existing_entry: dict[str, Any] | None,
        author_ldap: str,
    ) -> tuple[bool, str, str]:
        """Determines `(is_followup, dispatch_conv_id, prompt)` for single-user or multi-human Q&A."""
        prior_turns = list((existing_entry or {}).get("turns") or [])
        is_followup = bool((target_conv or existing_entry) and prior_turns)
        if not is_followup or existing_entry is None:
            return False, target_conv, build_code_question_prompt(
                cl_number=cl_id, file_path=cleaned_file, start_line=s_ln, end_line=e_ln,
                code_snippet=code_snippet, question=cleaned_q, side=side,
            )

        root_author, _ = _normalize_author_identity(existing_entry.get("author") or "")
        convs_by_user = dict(existing_entry.get("conversations_by_user") or {})
        is_cross_user = bool(author_ldap and root_author and author_ldap != root_author)
        has_other = any(t.get("author") and author_ldap and t.get("author") != author_ldap for t in prior_turns)
        shared_block = (
            build_shared_group_context_block(
                thread_id=str(existing_entry.get("thread_id") or ""),
                turn_offset=len(prior_turns), author=author_ldap, prior_turns=prior_turns,
            )
            if (is_cross_user or has_other) else ""
        )
        dispatch_conv = convs_by_user.get(author_ldap, "") if is_cross_user else target_conv
        if dispatch_conv:
            return True, dispatch_conv, build_code_followup_prompt(
                cl_number=cl_id, file_path=cleaned_file, start_line=s_ln, end_line=e_ln,
                question=cleaned_q, side=side, shared_context_block=shared_block,
            )
        return True, "", build_code_question_prompt(
            cl_number=cl_id, file_path=cleaned_file, start_line=s_ln, end_line=e_ln,
            code_snippet=code_snippet or str(existing_entry.get("code_snippet") or ""),
            question=cleaned_q, side=side, shared_context_block=shared_block,
        )

    def _append_followup_turn_locked(
        self,
        existing: dict[str, Any],
        new_turn: dict[str, Any],
        conv_id: str,
        function_name: str,
        author_ldap: str,
    ) -> dict[str, Any]:
        """Appends `new_turn` onto an existing thread entry while holding `_qa_lock`."""
        if not existing.get("conversation_id") and conv_id:
            existing["conversation_id"] = conv_id
        if function_name:
            existing["function_name"] = function_name
        convs = dict(existing.get("conversations_by_user") or {})
        if author_ldap and conv_id:
            convs[author_ldap] = conv_id
        existing["conversations_by_user"] = convs
        existing["turns"].append(new_turn)
        existing["participants"] = _collect_thread_participants(
            existing["turns"], str(existing.get("author") or author_ldap)
        )
        existing["is_shared"] = bool(
            existing.get("is_shared") or len(existing["participants"]) > 1
        )
        return dict(existing, turns=[dict(t) for t in existing["turns"]])

    def _resolve_tid_and_existing_locked(
        self, thread_id: str | None, conv_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Resolves `(resolved_tid, existing)` while holding `_qa_lock`."""
        resolved_tid = thread_id or (f"qa-{conv_id[:12]}" if conv_id else f"qa-local-{self._next_qa_seq}")
        if not thread_id and not conv_id:
            self._next_qa_seq += 1
        existing = self._qa_threads.get(resolved_tid)
        if existing is None and conv_id:
            existing = next(
                (c for c in self._qa_threads.values() if not c.get("deleted") and c.get("conversation_id") == conv_id),
                None,
            )
            if existing is not None:
                resolved_tid = existing["thread_id"]
        if conv_id:
            self._tracked_qa_conv_ids.add(conv_id)
            self._deleted_qa_conv_ids.discard(conv_id)
        return resolved_tid, existing

    def _upsert_qa_thread_turn(
        self,
        *,
        thread_id: str | None,
        conv_id: str,
        cl_id: str,
        file_path: str,
        start_line: int,
        end_line: int,
        side: str,
        function_name: str,
        code_snippet: str,
        question: str,
        is_followup: bool,
        author_ldap: str = "",
        author_email: str = "",
    ) -> dict[str, Any]:
        """Records a newly submitted root or follow-up turn in `self._qa_threads` and persists it."""
        with self._qa_lock:
            resolved_tid, existing = self._resolve_tid_and_existing_locked(thread_id, conv_id)
            new_turn = {
                "turn_index": len(existing["turns"]) if (existing and is_followup) else 0,
                "question": question, "reply": "", "status": "thinking",
                "author": author_ldap, "author_email": author_email,
                "conversation_id": conv_id, "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if existing and is_followup:
                result = self._append_followup_turn_locked(
                    existing, new_turn, conv_id, function_name, author_ldap
                )
            else:
                entry = {
                    "thread_id": resolved_tid, "conversation_id": conv_id, "cl": cl_id,
                    "file_path": file_path, "start_line": start_line, "end_line": end_line,
                    "side": side, "function_name": function_name, "code_snippet": code_snippet,
                    "author": author_ldap, "author_email": author_email,
                    "participants": [author_ldap] if author_ldap else [],
                    "conversations_by_user": {author_ldap: conv_id} if (author_ldap and conv_id) else {},
                    "is_shared": bool(self._shared_gerrit_dir), "deleted": False, "turns": [new_turn],
                }
                self._qa_threads[resolved_tid] = entry
                result = dict(entry, turns=[dict(new_turn)])
        if self._qa_cache_dir or self._shared_gerrit_dir:
            all_cl = self._merge_tracked_qa_state([], {cl_id}, persisted_threads=self._load_persisted_qa_threads_for_cl(cl_id))
            self._save_persisted_qa_threads_for_cl(cl_id, all_cl)
        return result

    def _assert_qa_thread_not_inflight(
        self, cl_id: str, target_conv: str, existing_entry: dict[str, Any] | None
    ) -> None:
        """Raises `ValueError` if `target_conv` or the latest turn of `existing_entry` is in flight."""
        inflight_conv = target_conv
        if existing_entry and existing_entry.get("turns"):
            last_turn_conv = str(existing_entry["turns"][-1].get("conversation_id") or "")
            if last_turn_conv:
                inflight_conv = last_turn_conv
        if inflight_conv and self._is_conversation_turn_inflight(inflight_conv):
            raise ValueError(
                f"Cannot submit another question in conversation '{inflight_conv}' on ag/{cl_id} "
                "while the previous question is still being processed by the agent. "
                "Wait for the agent's reply, or modify/delete the in-flight question."
            )

    def ask_agent_about_code(
        self, cl_number: str | int, file_path: str, start_line: int, end_line: int,
        code_snippet: str, question: str, *, side: str = "REVISION",
        conversation_id: str | None = None, project_id: str | None = None,
        spawn_new: bool = False, thread_id: str | None = None,
        function_name: str = "", viewer_email: str = "",
    ) -> dict[str, Any]:
        """Spawns an isolated per-highlight Q&A conversation or sends a follow-up when answered."""
        cl_id, cleaned_file = parse_cl_identifier(str(cl_number), allow_head=False), (file_path or "").strip() or "/COMMIT_MSG"
        s_ln = max(1, int(start_line or 1))
        e_ln = max(s_ln, int(end_line or s_ln))
        cleaned_q = (question or "").strip() or "Please explain this code block and check for any bugs or edge cases."
        author_ldap, author_email = _normalize_author_identity(viewer_email or self.default_viewer)
        target_conv = "" if spawn_new else str(conversation_id or "").strip()
        existing_entry = None if spawn_new else self._find_qa_thread_entry(thread_id, target_conv, cl_id=cl_id)
        self._assert_qa_thread_not_inflight(cl_id, target_conv, existing_entry)

        is_followup, dispatch_conv, prompt = self._build_qa_dispatch_plan(
            cl_id=cl_id, cleaned_file=cleaned_file, s_ln=s_ln, e_ln=e_ln, side=side,
            code_snippet=code_snippet, cleaned_q=cleaned_q, target_conv=target_conv,
            existing_entry=existing_entry, author_ldap=author_ldap,
        )
        loc_tag = f"{function_name or (Path(cleaned_file).name or cleaned_file)}:L{s_ln}"
        dispatched, resolved_conv, agent_res = self._dispatch_code_qa_prompt(
            target_conv_id=dispatch_conv, title=f"Q&A ag/{cl_id} ({loc_tag})",
            prompt=prompt, project_id=project_id,
        )
        thread_state = self._upsert_qa_thread_turn(
            thread_id=existing_entry["thread_id"] if existing_entry else thread_id,
            conv_id=resolved_conv, cl_id=cl_id, file_path=cleaned_file, start_line=s_ln,
            end_line=e_ln, side=side, function_name=function_name, code_snippet=code_snippet,
            question=cleaned_q, is_followup=is_followup, author_ldap=author_ldap, author_email=author_email,
        )
        self._invalidate_cl_caches([cl_id])
        return {
            "status": "ok", "cl": cl_id, "thread_id": thread_state["thread_id"],
            "file_path": cleaned_file, "start_line": s_ln, "end_line": e_ln,
            "function_name": thread_state.get("function_name", ""),
            "author": thread_state.get("author", author_ldap),
            "participants": thread_state.get("participants", []),
            "is_shared": bool(thread_state.get("is_shared")), "dispatched": dispatched,
            "conversation_id": resolved_conv or thread_state.get("conversation_id") or "",
            "prompt": prompt, "turns": thread_state["turns"], "agent_response": agent_res,
        }

    def _cancel_or_rewind_inflight_turn(
        self,
        conv_id: str,
        turns: list[dict[str, Any]],
        delete_entire_conv: bool,
    ) -> None:
        """Cancels an in-flight LS invocation and deletes or rewinds its latest turn step."""
        if not conv_id:
            return
        _call_local_language_server_rpc(
            "CancelCascadeInvocation",
            {"cascadeId": conv_id, "killBackgroundTasks": True},
        )
        if delete_entire_conv:
            _call_local_language_server_rpc(
                "DeleteCascadeTrajectory",
                {"cascadeId": conv_id},
            )
            with self._qa_lock:
                self._deleted_qa_conv_ids.add(conv_id)
                self._tracked_qa_conv_ids.discard(conv_id)
            return

        prev_turn = turns[-2] if len(turns) >= 2 else None
        prev_end_step = prev_turn.get("end_step_index") if prev_turn else None
        latest_step = turns[-1].get("step_index") if turns else None
        if prev_end_step is not None:
            _call_local_language_server_rpc(
                "RevertToCascadeStep",
                {
                    "cascadeId": conv_id,
                    "stepIndex": int(prev_end_step),
                    "conversationOnly": True,
                },
            )
        if latest_step is not None:
            with self._qa_lock:
                self._ignored_steps_by_conv.setdefault(conv_id, set()).add(int(latest_step))

    def _execute_delete_code_question(
        self,
        cl_id: str,
        conv_id: str,
        thread_id: str | None,
        entry: dict[str, Any] | None,
        turns: list[dict[str, Any]],
        is_root_turn: bool,
    ) -> dict[str, Any]:
        """Cancels and deletes the latest turn (or entire root thread) and updates persistence."""
        self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
        remaining = turns[:-1] if len(turns) > 1 else []
        resolved_tid = entry["thread_id"] if entry else (thread_id or "")
        with self._qa_lock:
            if entry is not None:
                if is_root_turn:
                    entry["deleted"] = True
                    entry["turns"] = []
                else:
                    entry["turns"] = [dict(t) for t in remaining]
        self._remove_or_update_persisted_qa_thread(
            cl_id, resolved_tid, conv_id, None if is_root_turn else remaining
        )
        self._invalidate_cl_caches([cl_id])
        return {
            "status": "ok",
            "action": "delete",
            "cl": cl_id,
            "thread_id": resolved_tid,
            "conversation_id": "" if is_root_turn else conv_id,
            "thread_deleted": is_root_turn,
            "turns": remaining,
        }

    def _resolve_live_qa_turns(
        self, cl_id: str, thread_id: str | None, conv_id: str
    ) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """Resolves `(entry, conv_id, turns)` for modifying or deleting a question on `cl_id`."""
        entry = self._find_qa_thread_entry(thread_id, conv_id, cl_id=cl_id)
        if not conv_id and entry:
            conv_id = str(entry.get("conversation_id") or "")
        live_questions = self.get_code_questions([cl_id], conversation_id=conv_id or None)
        matched_live = next(
            (
                q for q in live_questions
                if (conv_id and q.get("conversation_id") == conv_id)
                or (thread_id and q.get("thread_id") == thread_id)
            ),
            None,
        )
        turns = list(
            (matched_live.get("turns") if matched_live else None)
            or (entry.get("turns") if entry else None)
            or []
        )
        return entry, conv_id, turns

    def modify_or_delete_code_question(
        self, *, action: str, cl_number: str | int,
        conversation_id: str | None = None, thread_id: str | None = None,
        question: str = "", file_path: str = "", start_line: int = 1, end_line: int = 1,
        code_snippet: str = "", side: str = "REVISION", function_name: str = "",
        project_id: str | None = None, viewer_email: str = "",
    ) -> dict[str, Any]:
        """Modifies or deletes the latest in-flight question in an inline Q&A conversation."""
        norm_action = (action or "").strip().lower()
        if norm_action not in ("modify", "delete"):
            raise ValueError(
                f"Invalid code-question action '{action}': expected 'modify' or 'delete'. "
                "Pass action='modify' or action='delete'."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        entry, conv_id, turns = self._resolve_live_qa_turns(cl_id, thread_id, str(conversation_id or "").strip())
        is_root_turn = len(turns) <= 1
        if norm_action == "delete":
            return self._execute_delete_code_question(cl_id, conv_id, thread_id, entry, turns, is_root_turn)

        cleaned_q = (question or "").strip()
        if not cleaned_q:
            raise ValueError(
                f"Cannot modify code question on ag/{cl_id} with an empty question string. "
                "Enter a non-empty question or delete the in-flight question instead."
            )
        self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
        resolved_tid, remaining = (entry["thread_id"] if entry else (thread_id or "")), (turns[:-1] if not is_root_turn else [])
        if not is_root_turn and entry is not None:
            with self._qa_lock:
                entry["turns"] = [dict(t) for t in remaining]
        self._remove_or_update_persisted_qa_thread(cl_id, resolved_tid, conv_id, None if is_root_turn else remaining)
        e_dict = entry or {}
        ask_res = self.ask_agent_about_code(
            cl_number=cl_id, file_path=(file_path or "").strip() or e_dict.get("file_path") or "/COMMIT_MSG",
            start_line=max(1, int(start_line or e_dict.get("start_line") or 1)),
            end_line=max(1, int(end_line or e_dict.get("end_line") or start_line or 1)),
            code_snippet=code_snippet or e_dict.get("code_snippet") or "", question=cleaned_q,
            side=side or e_dict.get("side") or "REVISION", conversation_id=None if is_root_turn else conv_id,
            project_id=project_id, spawn_new=is_root_turn, thread_id=resolved_tid or None,
            function_name=function_name or e_dict.get("function_name") or "", viewer_email=viewer_email,
        )
        ask_res["action"] = "modify"
        return ask_res

    def _ai_analysis_disk_paths(
        self, cl_id: str, patchset: int, *, for_write: bool = False
    ) -> list[tuple[Path, bool]]:
        """Returns `(path, is_shared)` candidate disk paths for Intangibles AI Analysis."""
        fname = f"cl_{cl_id}_ps_{patchset}.json"
        paths: list[tuple[Path, bool]] = []
        if self._ai_analysis_cache_dir:
            paths.append((Path(self._ai_analysis_cache_dir) / fname, False))
        if self._shared_gerrit_dir:
            shared_root = Path(self._shared_gerrit_dir)
            paths.append((shared_root / "ai_analysis" / fname, True))
            if not for_write and str(shared_root) == DEFAULT_SHARED_GERRIT_RW_DIR:
                ro_path = Path(DEFAULT_SHARED_GERRIT_RO_DIR) / "ai_analysis" / fname
                if ro_path != paths[-1][0]:
                    paths.append((ro_path, True))
        return paths

    def _load_persisted_ai_analysis(self, cl_id: str, patchset: int) -> dict[str, Any] | None:
        """Loads persisted Intangibles AI Analysis from local cache or shared team storage."""
        best: dict[str, Any] | None = None
        for cache_file, is_shared in self._ai_analysis_disk_paths(cl_id, patchset, for_write=False):
            if not cache_file.is_file():
                continue
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            conv_id = str(raw.get("conversation_id") or "").strip()
            if conv_id:
                with self._qa_lock:
                    self._analyzer_conv_ids.add(conv_id)
            if is_shared:
                raw = dict(raw, shared=True)
            if best is None or (raw.get("analysis_status") == "ready" and best.get("analysis_status") != "ready"):
                best = raw
        return best

    def _save_persisted_ai_analysis(
        self, cl_id: str, patchset: int, entry: dict[str, Any]
    ) -> None:
        """Persists Intangibles AI Analysis state/results to local cache and shared team storage."""
        for cache_file, is_shared in self._ai_analysis_disk_paths(cl_id, patchset, for_write=True):
            payload = dict(entry, shared=True) if is_shared else dict(entry)
            _write_json_safely(cache_file, payload)

    def _delete_persisted_ai_analysis(self, cl_id: str, patchset: int) -> None:
        """Deletes persisted Intangibles AI Analysis state from local and shared disk when cancelled."""
        for cache_file, _ in self._ai_analysis_disk_paths(cl_id, patchset, for_write=True):
            try:
                if cache_file.is_file():
                    cache_file.unlink()
            except OSError:
                pass

    def _resolve_analysis_patchset(self, cl_id: str, patchset: int | None) -> int:
        """Resolves `patchset` from explicit value, known queue revisions, or Gerrit detail."""
        if patchset and int(patchset) > 0:
            return int(patchset)
        _, known_ps = self._get_known_revision(cl_id)
        if known_ps > 0:
            return known_ps
        resolved_ps, _ = self._resolve_change_patchset_and_project(cl_id, None, "")
        return resolved_ps

    def _refresh_thinking_ai_analysis(
        self,
        cl_id: str,
        resolved_ps: int,
        gen: int,
        entry: dict[str, Any],
        brain_root: Path | None,
    ) -> dict[str, Any]:
        """Checks whether a `thinking` AI analysis conversation has finished and persists it."""
        conv_id = str(entry.get("conversation_id") or "").strip()
        if str(entry.get("analysis_status") or "") != "thinking" or not conv_id:
            return entry
        parsed = parse_ai_analysis_from_conversation(conv_id, brain_root=brain_root)
        if parsed is None:
            return entry
        ready_entry = {
            "analysis_status": "ready",
            "conversation_id": conv_id,
            "analyzed_by": str(entry.get("analyzed_by") or ""),
            "shared": bool(entry.get("shared") or self._shared_gerrit_dir),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "analysis": parsed,
            "error": "",
        }
        key = (cl_id, resolved_ps)
        committed = False
        with self._ai_analysis_lock:
            cur = self._ai_analysis_state.get(key)
            if (
                self._ai_analysis_gen.get(key, 0) == gen
                and cur is not None
                and str(cur.get("conversation_id") or "").strip() == conv_id
            ):
                self._ai_analysis_state[key] = ready_entry
                entry = ready_entry
                committed = True
        if committed:
            self._save_persisted_ai_analysis(cl_id, resolved_ps, ready_entry)
        return entry

    def get_ai_analysis(
        self,
        cl_number: str | int,
        *,
        patchset: int | None = None,
        brain_root: Path | None = None,
    ) -> dict[str, Any]:
        """Returns Intangibles AI Analysis for `(cl_number, patchset)` without auto-triggering."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False, strict=True)
        resolved_ps = self._resolve_analysis_patchset(cl_id, patchset)
        key = (cl_id, resolved_ps)
        with self._ai_analysis_lock:
            gen = self._ai_analysis_gen.get(key, 0)
            entry = self._ai_analysis_state.get(key)
        if entry is None or (entry.get("analysis_status") == "thinking" and self._shared_gerrit_dir):
            loaded = self._load_persisted_ai_analysis(cl_id, resolved_ps)
            if loaded is not None and (entry is None or loaded.get("analysis_status") == "ready"):
                with self._ai_analysis_lock:
                    if self._ai_analysis_gen.get(key, 0) == gen:
                        self._ai_analysis_state[key] = loaded
                        entry = loaded
        if entry is None:
            return {
                "status": "ok", "cl": cl_id, "patch_set": resolved_ps,
                "analysis_status": "not_analyzed", "conversation_id": "",
                "analyzed_by": "", "shared": False, "analysis": None, "error": "",
            }
        entry = self._refresh_thinking_ai_analysis(cl_id, resolved_ps, gen, entry, brain_root)
        return {
            "status": "ok", "cl": cl_id, "patch_set": resolved_ps,
            "analysis_status": str(entry.get("analysis_status") or "not_analyzed"),
            "conversation_id": str(entry.get("conversation_id") or "").strip(),
            "analyzed_by": str(entry.get("analyzed_by") or ""),
            "shared": bool(entry.get("shared") or self._shared_gerrit_dir),
            "updated_at": str(entry.get("updated_at") or ""),
            "analysis": entry.get("analysis"), "error": str(entry.get("error") or ""),
        }

    def _cancel_ai_analysis(self, cl_id: str, patchset: int | None) -> dict[str, Any]:
        """Cancels an in-flight or persisted Intangibles AI Analysis and cleans up its trajectory."""
        resolved_ps = self._resolve_analysis_patchset(cl_id, patchset)
        key = (cl_id, resolved_ps)
        with self._ai_analysis_lock:
            self._ai_analysis_gen[key] = self._ai_analysis_gen.get(key, 0) + 1
            prev = self._ai_analysis_state.pop(key, None)
        if prev is None:
            prev = self._load_persisted_ai_analysis(cl_id, resolved_ps)
        prev_conv = str((prev or {}).get("conversation_id") or "").strip()
        self._cancel_or_rewind_inflight_turn(prev_conv, [], delete_entire_conv=True)
        self._delete_persisted_ai_analysis(cl_id, resolved_ps)
        return {
            "status": "ok", "cl": cl_id, "patch_set": resolved_ps,
            "analysis_status": "not_analyzed", "conversation_id": "",
            "analyzed_by": "", "shared": False, "analysis": None, "error": "",
        }

    def _build_ai_analysis_prompt_for_cl(
        self, cl_id: str, resolved_ps: int, resolved_proj: str, subject: str
    ) -> str:
        """Fetches diff bundle metadata and constructs the Intangibles prompt for `cl_id`."""
        bundle = self.fetch_commit_diff_bundle(cl_id)
        commit_msg = str(bundle.get("commit_message") or "")
        first_lines = commit_msg.splitlines()
        resolved_subj = (subject or "").strip() or (
            first_lines[0].strip() if first_lines else f"ag/{cl_id}"
        )
        return build_ai_analysis_prompt(
            cl_id=cl_id,
            patch_set=resolved_ps,
            subject=resolved_subj,
            project=resolved_proj,
            commit_message=commit_msg,
            diff_files=bundle.get("files") or [],
        )

    def _commit_spawned_ai_analysis(
        self,
        cl_id: str,
        resolved_ps: int,
        gen: int,
        dispatched: bool,
        spawned_conv: str,
        brain_root: Path | None,
        analyzed_by: str = "",
    ) -> dict[str, Any]:
        """Commits spawned `agentapi` conversation state if `gen` is still current, else cancels it."""
        key = (cl_id, resolved_ps)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not dispatched or not spawned_conv:
            err_entry = {
                "analysis_status": "error", "conversation_id": "", "analyzed_by": analyzed_by,
                "shared": False, "updated_at": now_iso, "analysis": None,
                "error": (
                    f"Failed to start Problem & Alternatives analysis for ag/{cl_id}: sidecar agentapi bridge "
                    "is unavailable. Open Fast Gerrit UI inside Jetski Sidecar and retry."
                ),
            }
            with self._ai_analysis_lock:
                if self._ai_analysis_gen.get(key) == gen:
                    self._ai_analysis_state[key] = err_entry
            return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **err_entry}

        with self._qa_lock:
            self._analyzer_conv_ids.add(spawned_conv)
        thinking_entry = {
            "analysis_status": "thinking", "conversation_id": spawned_conv,
            "analyzed_by": analyzed_by, "shared": bool(self._shared_gerrit_dir),
            "updated_at": now_iso, "analysis": None, "error": "",
        }
        with self._ai_analysis_lock:
            is_current = self._ai_analysis_gen.get(key) == gen
            if is_current:
                self._ai_analysis_state[key] = thinking_entry
        if not is_current:
            self._cancel_or_rewind_inflight_turn(spawned_conv, [], delete_entire_conv=True)
            return self.get_ai_analysis(cl_id, patchset=resolved_ps, brain_root=brain_root)
        self._save_persisted_ai_analysis(cl_id, resolved_ps, thinking_entry)
        return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **thinking_entry}

    def _start_ai_analysis(
        self,
        cl_id: str,
        *,
        patchset: int | None,
        project: str,
        subject: str,
        project_id: str | None,
        force: bool,
        brain_root: Path | None,
        analyzed_by: str = "",
    ) -> dict[str, Any]:
        """Reserves an epoch and launches an isolated `agentapi` conversation for `cl_id`."""
        resolved_ps, resolved_proj = self._resolve_change_patchset_and_project(cl_id, patchset, project)
        key = (cl_id, resolved_ps)
        if not force:
            current = self.get_ai_analysis(cl_id, patchset=resolved_ps, brain_root=brain_root)
            if current["analysis_status"] in ("ready", "thinking"):
                return current

        with self._ai_analysis_lock:
            existing = self._ai_analysis_state.get(key)
            if not force and existing and existing.get("analysis_status") in ("ready", "thinking"):
                return {"status": "ok", "cl": cl_id, "patch_set": resolved_ps, **existing}
            superseded_conv = (
                str(existing.get("conversation_id") or "").strip()
                if existing and existing.get("analysis_status") == "thinking" else ""
            )
            gen = self._ai_analysis_gen.get(key, 0) + 1
            self._ai_analysis_gen[key] = gen
            self._ai_analysis_state[key] = {
                "analysis_status": "thinking", "conversation_id": "", "analyzed_by": analyzed_by,
                "shared": bool(self._shared_gerrit_dir),
                "updated_at": datetime.now(timezone.utc).isoformat(), "analysis": None, "error": "",
            }

        self._cancel_or_rewind_inflight_turn(superseded_conv, [], delete_entire_conv=True)
        prompt = self._build_ai_analysis_prompt_for_cl(cl_id, resolved_ps, resolved_proj, subject)
        dispatched, spawned_conv, _ = self._dispatch_code_qa_prompt(
            target_conv_id="", title=f"Problem & Alternatives · ag/{cl_id} (PS{resolved_ps})",
            prompt=prompt, project_id=project_id,
        )
        return self._commit_spawned_ai_analysis(
            cl_id, resolved_ps, gen, dispatched, spawned_conv, brain_root, analyzed_by=analyzed_by
        )

    def trigger_or_cancel_ai_analysis(
        self,
        cl_number: str | int,
        *,
        action: str = "analyze",
        patchset: int | None = None,
        project: str = "",
        subject: str = "",
        project_id: str | None = None,
        force: bool = False,
        brain_root: Path | None = None,
        viewer_email: str = "",
    ) -> dict[str, Any]:
        """Explicitly starts or cancels a single-CL Intangibles AI Analysis."""
        norm_action = (action or "analyze").strip().lower()
        if norm_action not in ("analyze", "cancel"):
            raise ValueError(
                f"Invalid Problem & Alternatives action '{action}': expected 'analyze' or 'cancel'. "
                "Use 'Analyze Problem & Alternatives' under Harness Actions in the toolbar."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False, strict=True)
        if norm_action == "cancel":
            return self._cancel_ai_analysis(cl_id, patchset)
        author_ldap, _ = _normalize_author_identity(viewer_email or self.default_viewer)
        return self._start_ai_analysis(
            cl_id,
            patchset=patchset,
            project=project,
            subject=subject,
            project_id=project_id,
            force=force,
            brain_root=brain_root,
            analyzed_by=author_ldap,
        )


InlineQaService = GerritInlineQaService


