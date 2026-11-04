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
from commands.gerrit.services.queue_service import GerritQueueService
from core.process import SubprocessRunner
from lib.gerrit_api import DEFAULT_GERRIT_HOST, parse_cl_identifier

_CODE_QUESTION_PROMPT_RE = re.compile(
    r"Regarding Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    r"```[^\n]*\n(?P<snippet>.*?)\n```\s*"
    r"\*\*Question:\*\*\s*(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)

_CODE_FOLLOWUP_PROMPT_RE = re.compile(
    r"Follow-up on Gerrit CL `ag/(?P<cl>\d+)` in `(?P<file_path>[^`]+)` "
    r"\(L(?P<start_line>\d+)(?:[-–](?:L)?(?P<end_line>\d+))?(?P<side_note>[^)]*)\):\s*"
    r"\*\*Question:\*\*\s*(?P<question>.*?)(?:</SYSTEM_MESSAGE>|</USER_REQUEST>|\Z)",
    re.DOTALL,
)


MAX_RECENT_BRAIN_DIRS = 15
LS_CONNECT_RPC_TIMEOUT_SEC = 10



def build_code_question_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    code_snippet: str,
    question: str,
    *,
    side: str = "REVISION",
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
    return (
        f"Regarding Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"```\n{snippet}\n```\n\n"
        f"**Question:** {cleaned_question}"
    )


def build_code_followup_prompt(
    cl_number: str | int,
    file_path: str,
    start_line: int,
    end_line: int,
    question: str,
    *,
    side: str = "REVISION",
) -> str:
    """Builds a structured follow-up prompt within an existing inline code Q&A conversation."""
    cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
    cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
    s_ln = max(1, int(start_line or 1))
    e_ln = max(s_ln, int(end_line or s_ln))
    range_label = f"L{s_ln}" if s_ln == e_ln else f"L{s_ln}-L{e_ln}"
    side_note = " (PARENT / base)" if str(side).upper() == "PARENT" else ""
    cleaned_question = (question or "").strip()
    return (
        f"Follow-up on Gerrit CL `ag/{cl_id}` in `{cleaned_file}` ({range_label}{side_note}):\n\n"
        f"**Question:** {cleaned_question}"
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


def _extract_followup_question_text(step: dict[str, Any]) -> str:
    """Extracts follow-up question text from a `USER_INPUT` or follow-up `SYSTEM_MESSAGE` step."""
    step_type = str(step.get("type") or "")
    content = str(step.get("content") or "")
    m_followup = _CODE_FOLLOWUP_PROMPT_RE.search(content)
    if m_followup:
        return (m_followup.group("question") or "").strip()
    if step_type != "USER_INPUT":
        return ""
    cleaned = re.sub(r"</?USER_REQUEST>", "", content).strip()
    if not cleaned or "Regarding Gerrit CL `ag/" in cleaned:
        return ""
    return cleaned


def parse_code_questions_from_transcript_lines(
    lines: list[str],
    conv_id: str = "",
    filter_cls: set[str] | None = None,
    ignored_steps: set[int] | None = None,
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

    results: list[dict[str, Any]] = []
    root_prompt_indices = [
        idx
        for idx, step in enumerate(steps)
        if str(step.get("type") or "") in ("SYSTEM_MESSAGE", "USER_INPUT")
        and "Regarding Gerrit CL `ag/" in str(step.get("content") or "")
        and _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
    ]
    is_dedicated_qa_conv = len(root_prompt_indices) == 1
    skip_steps = ignored_steps or set()

    for pos, idx in enumerate(root_prompt_indices):
        step = steps[idx]
        step_index = int(step.get("step_index", idx) or idx)
        if step_index in skip_steps:
            continue
        m = _CODE_QUESTION_PROMPT_RE.search(str(step.get("content") or ""))
        if not m:
            continue
        cl_str = m.group("cl").strip()
        if filter_cls and cl_str not in filter_cls:
            continue
        file_path = m.group("file_path").strip() or "/COMMIT_MSG"
        s_ln = max(1, int(m.group("start_line") or 1))
        e_ln = max(s_ln, int(m.group("end_line") or s_ln))
        side_note = m.group("side_note") or ""
        side = "PARENT" if "PARENT" in side_note.upper() else "REVISION"
        snippet = m.group("snippet") or ""
        first_q = (m.group("question") or "").strip()

        reply_text, end_step_idx = _collect_planner_reply_until_next_prompt(steps, idx)
        turns: list[dict[str, Any]] = [
            {
                "turn_index": 0,
                "step_index": step_index,
                "end_step_index": end_step_idx,
                "question": first_q,
                "reply": reply_text,
                "status": "answered" if reply_text else "thinking",
            }
        ]

        next_root_idx = root_prompt_indices[pos + 1] if pos + 1 < len(root_prompt_indices) else len(steps)
        if is_dedicated_qa_conv:
            for sub_idx in range(idx + 1, next_root_idx):
                sub_step = steps[sub_idx]
                sub_step_num = int(sub_step.get("step_index", sub_idx) or sub_idx)
                if sub_step_num in skip_steps:
                    continue
                followup_q = _extract_followup_question_text(sub_step)
                if not followup_q:
                    continue
                f_reply, f_end_step = _collect_planner_reply_until_next_prompt(steps, sub_idx)
                turns.append(
                    {
                        "turn_index": len(turns),
                        "step_index": sub_step_num,
                        "end_step_index": f_end_step,
                        "question": followup_q,
                        "reply": f_reply,
                        "status": "answered" if f_reply else "thinking",
                    }
                )

        latest = turns[-1]
        short_conv = (conv_id or "local")[:8]
        results.append(
            {
                "id": f"tx-q-{short_conv}-{step_index}",
                "conversation_id": conv_id,
                "step_index": step_index,
                "cl": int(cl_str) if cl_str.isdigit() else cl_str,
                "file_path": file_path,
                "line": e_ln,
                "start_line": s_ln,
                "end_line": e_ln,
                "side": side,
                "code_snippet": snippet,
                "initial_question": first_q,
                "question": latest["question"],
                "reply": latest["reply"],
                "status": latest["status"],
                "turns": turns,
                "created_at": str(step.get("created_at") or ""),
            }
        )
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


def discover_transcript_code_questions(
    conversation_id: str | None = None,
    cl_numbers: list[str | int] | None = None,
    brain_root: Path | None = None,
    *,
    extra_conversation_ids: set[str] | None = None,
    excluded_conversation_ids: set[str] | None = None,
    ignored_steps_by_conv: dict[str, set[int]] | None = None,
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
        logs_dir = conv_dir / ".system_generated" / "logs"
        tx_file = logs_dir / "transcript_full.jsonl"
        if not tx_file.is_file():
            tx_file = logs_dir / "transcript.jsonl"
        if not tx_file.is_file():
            continue
        try:
            raw_text = tx_file.read_text(encoding="utf-8", errors="replace")
            if "Regarding Gerrit CL `ag/" not in raw_text:
                continue
            items = parse_code_questions_from_transcript_lines(
                raw_text.splitlines(),
                conv_id=conv_dir.name,
                filter_cls=filter_cls,
                ignored_steps=(ignored_steps_by_conv or {}).get(conv_dir.name),
            )
            for item in items:
                dedupe_key = (
                    f"conv:{conv_dir.name}:{item['step_index']}"
                    if len(items) == 1
                    else (
                        f"{item['cl']}:{item['file_path']}:{item['start_line']}:"
                        f"{item['end_line']}:{str(item.get('initial_question') or item['question']).strip()}"
                    )
                )
                prev = by_key.get(dedupe_key)
                if prev is None or (item.get("reply") and not prev.get("reply")):
                    by_key[dedupe_key] = item
        except OSError:
            continue

    return list(by_key.values())




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

    def _merge_tracked_qa_state(
        self,
        transcript_items: list[dict[str, Any]],
        filter_cls: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Merges in-memory `self._qa_threads` metadata and pending turns with transcript items."""
        with self._qa_lock:
            threads_snapshot = [
                dict(t, turns=[dict(turn) for turn in (t.get("turns") or [])])
                for t in self._qa_threads.values()
                if not t.get("deleted")
            ]

        by_conv = {
            str(it.get("conversation_id") or ""): it
            for it in transcript_items
            if it.get("conversation_id")
        }
        merged: list[dict[str, Any]] = []
        handled_convs: set[str] = set()

        for th in threads_snapshot:
            cl_str = str(th.get("cl") or "")
            if filter_cls and cl_str not in filter_cls:
                continue
            cid = str(th.get("conversation_id") or "")
            tx_item = by_conv.get(cid) if cid else None
            if tx_item is not None:
                handled_convs.add(cid)
                combined = dict(tx_item)
                combined["thread_id"] = th["thread_id"]
                if th.get("function_name"):
                    combined["function_name"] = th["function_name"]
                tx_turns = list(tx_item.get("turns") or [])
                mem_turns = list(th.get("turns") or [])
                if len(mem_turns) > len(tx_turns):
                    tx_turns.extend(mem_turns[len(tx_turns) :])
                if tx_turns:
                    combined["turns"] = tx_turns
                    combined["question"] = tx_turns[-1]["question"]
                    combined["reply"] = tx_turns[-1]["reply"]
                    combined["status"] = tx_turns[-1]["status"]
                self._sync_cached_turns_from_transcript(th["thread_id"], tx_turns)
                merged.append(combined)
                continue
            turns = list(th.get("turns") or [])
            latest = turns[-1] if turns else {"question": "", "reply": "", "status": "thinking"}
            merged.append({
                "id": th["thread_id"],
                "thread_id": th["thread_id"],
                "conversation_id": cid,
                "cl": int(cl_str) if cl_str.isdigit() else cl_str,
                "file_path": th.get("file_path") or "/COMMIT_MSG",
                "line": th.get("end_line") or 1,
                "start_line": th.get("start_line") or 1,
                "end_line": th.get("end_line") or 1,
                "side": th.get("side") or "REVISION",
                "function_name": th.get("function_name") or "",
                "code_snippet": th.get("code_snippet") or "",
                "question": latest["question"],
                "reply": latest["reply"],
                "status": latest["status"],
                "turns": turns,
            })

        for it in transcript_items:
            cid = str(it.get("conversation_id") or "")
            if cid and cid in handled_convs:
                continue
            merged.append(it)
        return merged

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
        with self._qa_lock:
            tracked = set(self._tracked_qa_conv_ids)
            deleted = set(self._deleted_qa_conv_ids) | set(self._analyzer_conv_ids)
            ignored = {k: set(v) for k, v in self._ignored_steps_by_conv.items()}
        tx_items = discover_transcript_code_questions(
            conversation_id=conversation_id,
            cl_numbers=cleaned or None,
            extra_conversation_ids=tracked,
            excluded_conversation_ids=deleted,
            ignored_steps_by_conv=ignored,
        )
        return self._merge_tracked_qa_state(tx_items, filter_cls)


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
        self, thread_id: str | None, conv_id: str | None
    ) -> dict[str, Any] | None:
        """Finds an active in-memory Q&A thread by `thread_id` or `conversation_id`."""
        with self._qa_lock:
            if thread_id and thread_id in self._qa_threads:
                entry = self._qa_threads[thread_id]
                if not entry.get("deleted"):
                    return entry
            if conv_id:
                for entry in self._qa_threads.values():
                    if not entry.get("deleted") and entry.get("conversation_id") == conv_id:
                        return entry
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
    ) -> dict[str, Any]:
        """Records a newly submitted root or follow-up turn in `self._qa_threads`."""
        with self._qa_lock:
            resolved_tid = thread_id or (f"qa-{conv_id[:12]}" if conv_id else "")
            if not resolved_tid:
                resolved_tid = f"qa-local-{self._next_qa_seq}"
                self._next_qa_seq += 1
            existing = self._qa_threads.get(resolved_tid)
            if existing is None and conv_id:
                for cand in self._qa_threads.values():
                    if not cand.get("deleted") and cand.get("conversation_id") == conv_id:
                        existing = cand
                        resolved_tid = cand["thread_id"]
                        break
            if conv_id:
                self._tracked_qa_conv_ids.add(conv_id)
                self._deleted_qa_conv_ids.discard(conv_id)

            new_turn = {
                "turn_index": len(existing["turns"]) if (existing and is_followup) else 0,
                "question": question,
                "reply": "",
                "status": "thinking",
            }
            if existing and is_followup:
                existing["conversation_id"] = conv_id or existing.get("conversation_id", "")
                if function_name:
                    existing["function_name"] = function_name
                existing["turns"].append(new_turn)
                return dict(existing, turns=[dict(t) for t in existing["turns"]])

            entry = {
                "thread_id": resolved_tid,
                "conversation_id": conv_id,
                "cl": cl_id,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "side": side,
                "function_name": function_name,
                "code_snippet": code_snippet,
                "deleted": False,
                "turns": [new_turn],
            }
            self._qa_threads[resolved_tid] = entry
            return dict(entry, turns=[dict(new_turn)])

    def ask_agent_about_code(
        self,
        cl_number: str | int,
        file_path: str,
        start_line: int,
        end_line: int,
        code_snippet: str,
        question: str,
        *,
        side: str = "REVISION",
        conversation_id: str | None = None,
        project_id: str | None = None,
        spawn_new: bool = False,
        thread_id: str | None = None,
        function_name: str = "",
    ) -> dict[str, Any]:
        """Spawns an isolated per-highlight Q&A conversation or sends a follow-up when answered."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
        s_ln = max(1, int(start_line or 1))
        e_ln = max(s_ln, int(end_line or s_ln))
        cleaned_q = (
            (question or "").strip()
            or "Please explain this code block and check for any bugs or edge cases."
        )
        target_conv = "" if spawn_new else str(conversation_id or "").strip()
        if target_conv and self._is_conversation_turn_inflight(target_conv):
            raise ValueError(
                f"Cannot submit another question in conversation '{target_conv}' on ag/{cl_id} "
                "while the previous question is still being processed by the agent. "
                "Wait for the agent's reply, or modify/delete the in-flight question."
            )

        existing_entry = self._find_qa_thread_entry(thread_id, target_conv) if target_conv else None
        is_followup = bool(target_conv and existing_entry and existing_entry.get("turns"))
        prompt = (
            build_code_followup_prompt(
                cl_number=cl_id,
                file_path=cleaned_file,
                start_line=s_ln,
                end_line=e_ln,
                question=cleaned_q,
                side=side,
            )
            if is_followup
            else build_code_question_prompt(
                cl_number=cl_id,
                file_path=cleaned_file,
                start_line=s_ln,
                end_line=e_ln,
                code_snippet=code_snippet,
                question=cleaned_q,
                side=side,
            )
        )
        short_name = Path(cleaned_file).name or cleaned_file
        loc_tag = f"{function_name}:L{s_ln}" if function_name else f"{short_name}:L{s_ln}"
        title = f"Q&A ag/{cl_id} ({loc_tag})"

        dispatched, resolved_conv, agent_res = self._dispatch_code_qa_prompt(
            target_conv_id=target_conv,
            title=title,
            prompt=prompt,
            project_id=project_id,
        )
        thread_state = self._upsert_qa_thread_turn(
            thread_id=thread_id,
            conv_id=resolved_conv,
            cl_id=cl_id,
            file_path=cleaned_file,
            start_line=s_ln,
            end_line=e_ln,
            side=side,
            function_name=function_name,
            code_snippet=code_snippet,
            question=cleaned_q,
            is_followup=is_followup,
        )
        self._invalidate_cl_caches([cl_id])
        return {
            "status": "ok",
            "cl": cl_id,
            "thread_id": thread_state["thread_id"],
            "file_path": cleaned_file,
            "start_line": s_ln,
            "end_line": e_ln,
            "function_name": thread_state.get("function_name", ""),
            "dispatched": dispatched,
            "conversation_id": resolved_conv,
            "prompt": prompt,
            "turns": thread_state["turns"],
            "agent_response": agent_res,
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

    def modify_or_delete_code_question(
        self,
        *,
        action: str,
        cl_number: str | int,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        question: str = "",
        file_path: str = "",
        start_line: int = 1,
        end_line: int = 1,
        code_snippet: str = "",
        side: str = "REVISION",
        function_name: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Modifies or deletes the latest in-flight question in an inline Q&A conversation."""
        norm_action = (action or "").strip().lower()
        if norm_action not in ("modify", "delete"):
            raise ValueError(
                f"Invalid code-question action '{action}': expected 'modify' or 'delete'. "
                "Pass action='modify' or action='delete'."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        conv_id = str(conversation_id or "").strip()
        entry = self._find_qa_thread_entry(thread_id, conv_id)
        if not conv_id and entry:
            conv_id = str(entry.get("conversation_id") or "")

        live_questions = self.get_code_questions([cl_id], conversation_id=conv_id or None)
        matched_live = next(
            (
                q
                for q in live_questions
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
        is_root_turn = len(turns) <= 1

        if norm_action == "delete":
            self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
            remaining = turns[:-1] if len(turns) > 1 else []
            with self._qa_lock:
                if entry is not None:
                    if is_root_turn:
                        entry["deleted"] = True
                        entry["turns"] = []
                    else:
                        entry["turns"] = [dict(t) for t in remaining]
            self._invalidate_cl_caches([cl_id])
            return {
                "status": "ok",
                "action": "delete",
                "cl": cl_id,
                "thread_id": entry["thread_id"] if entry else (thread_id or ""),
                "conversation_id": "" if is_root_turn else conv_id,
                "thread_deleted": is_root_turn,
                "turns": remaining,
            }

        cleaned_q = (question or "").strip()
        if not cleaned_q:
            raise ValueError(
                f"Cannot modify code question on ag/{cl_id} with an empty question string. "
                "Enter a non-empty question or delete the in-flight question instead."
            )
        self._cancel_or_rewind_inflight_turn(conv_id, turns, delete_entire_conv=is_root_turn)
        resolved_file = (
            (file_path or "").strip()
            or (entry.get("file_path") if entry else "")
            or "/COMMIT_MSG"
        )
        s_ln = max(1, int(start_line or (entry.get("start_line") if entry else 1) or 1))
        e_ln = max(s_ln, int(end_line or (entry.get("end_line") if entry else s_ln) or s_ln))
        resolved_snippet = code_snippet or (entry.get("code_snippet") if entry else "") or ""
        resolved_side = side or (entry.get("side") if entry else "REVISION") or "REVISION"
        resolved_fn = function_name or (entry.get("function_name") if entry else "") or ""

        if not is_root_turn and entry is not None:
            with self._qa_lock:
                entry["turns"] = [dict(t) for t in turns[:-1]]

        ask_res = self.ask_agent_about_code(
            cl_number=cl_id,
            file_path=resolved_file,
            start_line=s_ln,
            end_line=e_ln,
            code_snippet=resolved_snippet,
            question=cleaned_q,
            side=resolved_side,
            conversation_id=None if is_root_turn else conv_id,
            project_id=project_id,
            spawn_new=is_root_turn,
            thread_id=entry["thread_id"] if entry else thread_id,
            function_name=resolved_fn,
        )
        ask_res["action"] = "modify"
        return ask_res

    def _ai_analysis_disk_path(self, cl_id: str, patchset: int) -> Path:
        """Returns the disk cache path for Intangibles AI Analysis on `(cl_id, patchset)`."""
        return self._ai_analysis_cache_dir / f"cl_{cl_id}_ps_{patchset}.json"

    def _load_persisted_ai_analysis(self, cl_id: str, patchset: int) -> dict[str, Any] | None:
        """Loads persisted Intangibles AI Analysis from disk if present."""
        cache_file = self._ai_analysis_disk_path(cl_id, patchset)
        if not cache_file.is_file():
            return None
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        conv_id = str(raw.get("conversation_id") or "").strip()
        if conv_id:
            with self._qa_lock:
                self._analyzer_conv_ids.add(conv_id)
        return raw

    def _save_persisted_ai_analysis(
        self, cl_id: str, patchset: int, entry: dict[str, Any]
    ) -> None:
        """Persists Intangibles AI Analysis state/results to disk."""
        try:
            cache_file = self._ai_analysis_disk_path(cl_id, patchset)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _delete_persisted_ai_analysis(self, cl_id: str, patchset: int) -> None:
        """Deletes persisted Intangibles AI Analysis state from disk when cancelled."""
        try:
            cache_file = self._ai_analysis_disk_path(cl_id, patchset)
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
        if entry is None:
            loaded = self._load_persisted_ai_analysis(cl_id, resolved_ps)
            if loaded is not None:
                with self._ai_analysis_lock:
                    if self._ai_analysis_gen.get(key, 0) == gen and key not in self._ai_analysis_state:
                        self._ai_analysis_state[key] = loaded
                    entry = self._ai_analysis_state.get(key)

        if entry is None:
            return {
                "status": "ok",
                "cl": cl_id,
                "patch_set": resolved_ps,
                "analysis_status": "not_analyzed",
                "conversation_id": "",
                "analysis": None,
                "error": "",
            }

        status_val = str(entry.get("analysis_status") or "not_analyzed")
        conv_id = str(entry.get("conversation_id") or "").strip()
        if status_val == "thinking" and conv_id:
            parsed = parse_ai_analysis_from_conversation(
                conv_id,
                brain_root=brain_root,
            )
            if parsed is not None:
                ready_entry = {
                    "analysis_status": "ready",
                    "conversation_id": conv_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "analysis": parsed,
                    "error": "",
                }
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

        return {
            "status": "ok",
            "cl": cl_id,
            "patch_set": resolved_ps,
            "analysis_status": str(entry.get("analysis_status") or "not_analyzed"),
            "conversation_id": conv_id,
            "updated_at": str(entry.get("updated_at") or ""),
            "analysis": entry.get("analysis"),
            "error": str(entry.get("error") or ""),
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
            "status": "ok",
            "cl": cl_id,
            "patch_set": resolved_ps,
            "analysis_status": "not_analyzed",
            "conversation_id": "",
            "analysis": None,
            "error": "",
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
    ) -> dict[str, Any]:
        """Commits spawned `agentapi` conversation state if `gen` is still current, else cancels it."""
        key = (cl_id, resolved_ps)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not dispatched or not spawned_conv:
            err_entry = {
                "analysis_status": "error",
                "conversation_id": "",
                "updated_at": now_iso,
                "analysis": None,
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
            "analysis_status": "thinking",
            "conversation_id": spawned_conv,
            "updated_at": now_iso,
            "analysis": None,
            "error": "",
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
    ) -> dict[str, Any]:
        """Reserves an epoch and launches an isolated `agentapi` conversation for `cl_id`."""
        resolved_ps, resolved_proj = self._resolve_change_patchset_and_project(
            cl_id, patchset, project
        )
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
                if existing and existing.get("analysis_status") == "thinking"
                else ""
            )
            gen = self._ai_analysis_gen.get(key, 0) + 1
            self._ai_analysis_gen[key] = gen
            self._ai_analysis_state[key] = {
                "analysis_status": "thinking",
                "conversation_id": "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "analysis": None,
                "error": "",
            }

        self._cancel_or_rewind_inflight_turn(superseded_conv, [], delete_entire_conv=True)
        prompt = self._build_ai_analysis_prompt_for_cl(cl_id, resolved_ps, resolved_proj, subject)
        dispatched, spawned_conv, _ = self._dispatch_code_qa_prompt(
            target_conv_id="",
            title=f"Problem & Alternatives · ag/{cl_id} (PS{resolved_ps})",
            prompt=prompt,
            project_id=project_id,
        )
        return self._commit_spawned_ai_analysis(
            cl_id, resolved_ps, gen, dispatched, spawned_conv, brain_root
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
        return self._start_ai_analysis(
            cl_id,
            patchset=patchset,
            project=project,
            subject=subject,
            project_id=project_id,
            force=force,
            brain_root=brain_root,
        )


InlineQaService = GerritInlineQaService


