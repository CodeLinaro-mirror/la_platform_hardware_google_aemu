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

"""Gerrit review mutations, batch voting, submission, and reviewer management (`notify="OWNER"`)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any
import urllib.parse

from commands.gerrit.services.diff_parser import (
    DIFF_FETCH_WORKERS,
    GerritServiceBase,
    register_ui_hook,
    resolve_ui_hook,
)
from commands.gerrit.services.queue_service import (
    GerritQueueService,
    TEAM_ROSTER_EMAILS,
    _SIMPLE_LDAP_RE,
    is_agent_or_bot_email,
)
from commands.harness.review_swarm_status import (
    GERRIT_REVIEW_PAYLOAD_FILENAME,
    resolve_swarm_dir_for_cl,
)
from core.process import ProcessResult, SubprocessRunner
from lib.gerrit_api import (
    GERRIT_CLI_TIMEOUT_SEC,
    parse_cl_identifier,
    strip_xssi_prefix,
)

OWNER_NOTIFY_HANDLING = "OWNER"
register_ui_hook("resolve_swarm_dir_for_cl", resolve_swarm_dir_for_cl)

DEFAULT_GCHAT_CLI_BIN = "/google/bin/releases/gemini-agents-gchat/gchat"
GCHAT_DM_TIMEOUT_SEC = 25
MAX_CHAT_DM_LENGTH = 4000
_GOOGLE_CORP_EMAIL_RE = re.compile(r"^[a-z0-9._+-]+@google\.com$")



def _resolve_gchat_cli_bin() -> str | None:
    """Resolves the `gchat` CLI executable path from env override, `/google/bin`, or `PATH`."""
    override = (os.environ.get("AEMU_GCHAT_CLI_BIN") or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    if os.path.isfile(DEFAULT_GCHAT_CLI_BIN) and os.access(DEFAULT_GCHAT_CLI_BIN, os.X_OK):
        return DEFAULT_GCHAT_CLI_BIN
    return shutil.which("gchat")


def _normalize_chat_dm_recipient(raw_recipient: str, sender_email: str) -> str:
    """Validates and normalizes a single human `@google.com` recipient for a 1:1 Google Chat DM."""
    cleaned = str(raw_recipient or "").strip().lower()
    if not cleaned:
        raise ValueError(
            "Failed to validate Google Chat DM recipient: recipient is empty. "
            "Select a human '@google.com' reviewer on the CL."
        )
    if "," in cleaned or any(ch.isspace() for ch in cleaned):
        raise ValueError(
            f"Failed to validate Google Chat DM recipient '{raw_recipient}': "
            "multi-recipient DMs are blocked to prevent creating duplicate group chats. "
            "Send 1:1 DMs one reviewer at a time."
        )
    resolved = (
        f"{cleaned}@google.com"
        if _SIMPLE_LDAP_RE.fullmatch(cleaned) and not cleaned.isdigit()
        else cleaned
    )
    if not _GOOGLE_CORP_EMAIL_RE.fullmatch(resolved) or is_agent_or_bot_email(resolved):
        raise ValueError(
            f"Failed to validate Google Chat DM recipient '{resolved}': "
            "only human '@google.com' accounts can receive 1:1 Chat DMs. "
            "Select a human reviewer on the CL."
        )
    if sender_email and resolved == sender_email.lower():
        raise ValueError(
            f"Failed to send Google Chat DM to '{resolved}': "
            "Google Chat API does not support 1:1 DMs with yourself. "
            "Choose another reviewer on the CL."
        )
    return resolved


def _run_gchat_send_dm_command(
    gchat_bin: str, recipient_email: str, msg_text: str, cl_id: str
) -> ProcessResult:
    """Executes `gchat mutate send-direct-message` for one verified 1:1 recipient."""
    cmd = [
        gchat_bin,
        "mutate",
        "send-direct-message",
        "--usernames",
        recipient_email,
        "--text",
        msg_text,
        "--json",
    ]
    proc = SubprocessRunner().run(
        cmd,
        check=False,
        timeout_sec=GCHAT_DM_TIMEOUT_SEC,
    )
    if proc.timed_out:
        raise RuntimeError(
            f"Timed out after {GCHAT_DM_TIMEOUT_SEC}s while sending Google Chat DM to "
            f"'{recipient_email}' for ag/{cl_id}. Run 'gcert' in your terminal and retry, "
            "or click 'Copy Text' to paste into Google Chat."
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        raise RuntimeError(
            f"Failed to send Google Chat DM to '{recipient_email}' for ag/{cl_id}: {detail}. "
            "Run 'gcert' in your terminal and retry, or click 'Copy Text' to paste into Google Chat."
        )
    return proc


def _parse_gchat_dm_output(stdout_str: str) -> tuple[str, str, str]:
    """Parses `gchat mutate send-direct-message --json` stdout into `(space_name, message_name, chat_url)`."""
    raw = (stdout_str or "").strip()
    if not raw:
        return "", "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    space_name = str(payload.get("space_name") or "").strip()
    message_name = str(payload.get("message_name") or "").strip()
    space_id = space_name.split("/", 1)[1] if space_name.startswith("spaces/") else space_name
    chat_url = f"https://chat.google.com/dm/{space_id}" if space_id else ""
    return space_name, message_name, chat_url


register_ui_hook("_resolve_gchat_cli_bin", _resolve_gchat_cli_bin)





class GerritVoteSubmitService(GerritServiceBase):
    """Domain service for Gerrit voting, submission, reviewer updates, and inline comments."""

    def fetch_single_enriched_change(
        self, cl_id: str, viewer_email: str | None = None
    ) -> dict[str, Any] | None:
        """Delegates enriched single-change lookup to `GerritQueueService`."""
        return GerritQueueService.fetch_single_enriched_change(
            self, cl_id, viewer_email=viewer_email
        )  # type: ignore[arg-type]

    def _post_single_vote(
        self,
        gob_curl: str,
        cl_id: str,
        payload_json: str,
        code_review: int,
        revision_segment: str = "current",
    ) -> dict[str, Any]:
        """Posts a single `gob-curl` review payload with `notify: OWNER` for one CL."""
        url = f"{self.client.api_host}/a/changes/{cl_id}/revisions/{revision_segment}/review"
        res = SubprocessRunner().run(
            [
                gob_curl,
                "-s",
                "-f",
                "-S",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                payload_json,
                url,
            ],
            check=False,
            timeout_sec=GERRIT_CLI_TIMEOUT_SEC,
        )
        err_msg = (res.stderr or "").strip() if res.returncode != 0 else ""
        if (
            res.returncode != 0
            and self.auth_email
            and is_agent_or_bot_email(self.auth_email)
            and int(code_review) >= 2
        ):
            err_msg = (
                f"Capsule backend is authenticated as role account '{self.auth_email}' "
                f"(restricted to Code-Review +1). Open ag/{cl_id} in Gerrit or run the copied "
                "Cloudtop command to cast your personal +2."
            )
        parsed_resp: Any = None
        if res.returncode == 0 and (res.stdout or "").strip():
            try:
                parsed_resp = json.loads(strip_xssi_prefix(res.stdout))
            except ValueError:
                parsed_resp = None
        return {
            "cl": cl_id,
            "ok": res.returncode == 0,
            "error": err_msg,
            "response": parsed_resp,
        }

    @staticmethod
    def _build_vote_labels(
        code_review: int | None,
        autosubmit: bool | int | None,
        presubmit_ready: bool | int | None,
        *,
        allow_empty: bool = False,
    ) -> dict[str, int]:
        """Constructs Gerrit `labels` dict for `Code-Review`, `Autosubmit`, and `Presubmit-Ready`."""
        labels: dict[str, int] = {}
        if code_review is not None:
            labels["Code-Review"] = int(code_review)
        if isinstance(autosubmit, bool):
            if autosubmit:
                labels["Autosubmit"] = 1
        elif autosubmit is not None:
            labels["Autosubmit"] = int(autosubmit)
        if isinstance(presubmit_ready, bool):
            if presubmit_ready:
                labels["Presubmit-Ready"] = 1
        elif presubmit_ready is not None:
            labels["Presubmit-Ready"] = int(presubmit_ready)
        if not labels and not allow_empty:
            raise ValueError(
                "Cannot cast Gerrit vote: no label values specified "
                "(provide code_review, autosubmit, or presubmit_ready)."
            )
        return labels

    def _record_session_vote_results(
        self, results: list[dict[str, Any]], labels: dict[str, int]
    ) -> None:
        """Caches successful session votes in memory to mask Gerrit secondary-index lag."""
        cr_label = labels.get("Code-Review")
        has_pr = "Presubmit-Ready" in labels
        now_mono = time.monotonic()
        mutated_cl_ids: list[str] = []
        with self._session_lock:
            for r in results:
                raw_cl_num = str(r.get("cl") or "").strip()
                if not (r.get("ok") and raw_cl_num.isdigit()):
                    continue
                mutated_cl_ids.append(raw_cl_num)
                cl_num = int(raw_cl_num)
                self._session_votes[cl_num] = {**self._session_votes.get(cl_num, {}), **labels}
                if has_pr:
                    self._session_presubmit_voted_at[cl_num] = now_mono
                    stale_keys = [k for k in self._presubmit_targets_cache if str(k[0]) == raw_cl_num]
                    for k in stale_keys:
                        self._presubmit_targets_cache.pop(k, None)
                if cr_label is None:
                    continue
                if cr_label >= 2:
                    self._session_approved_cls.add(cl_num)
                else:
                    self._session_approved_cls.discard(cl_num)
        if mutated_cl_ids:
            self._invalidate_cl_caches(mutated_cl_ids)

    def cast_batch_vote(
        self,
        cl_numbers: list[str | int],
        code_review: int | None = 2,
        autosubmit: bool | int | None = False,
        presubmit_ready: bool | int | None = False,
        message: str = "",
    ) -> dict[str, Any]:
        """Casts labels across one or more CLs using `gob-curl` with mandatory `notify: OWNER`."""
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot cast Gerrit vote: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        clean_msg = message.strip()
        labels = self._build_vote_labels(
            code_review,
            autosubmit,
            presubmit_ready,
            allow_empty=bool(clean_msg),
        )
        cr_hint = int(labels.get("Code-Review", 0))
        payload: dict[str, Any] = {"notify": OWNER_NOTIFY_HANDLING}
        if labels:
            payload["labels"] = labels
        if clean_msg:
            payload["message"] = clean_msg

        cleaned_cls = [parse_cl_identifier(str(raw_cl), allow_head=False) for raw_cl in cl_numbers]
        payload_json = json.dumps(payload)
        workers = max(1, min(DIFF_FETCH_WORKERS, len(cleaned_cls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda cid: self._post_single_vote(gob_curl, cid, payload_json, cr_hint),
                    cleaned_cls,
                )
            )

        if labels:
            self._record_session_vote_results(results, labels)
        elif cleaned_cls:
            self._invalidate_cl_caches(cleaned_cls)
        all_ok = all(r["ok"] for r in results)
        cloudtop_cmd = " && ".join(
            f"gob-curl -s -X POST -H 'Content-Type: application/json' -d '{payload_json}' "
            f"'{self.client.api_host}/a/changes/{cid}/revisions/current/review' >/dev/null"
            for cid in cleaned_cls
        )
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "partial_error",
            "auth_email": self.auth_email,
            "labels": labels,
            "cloudtop_cmd": cloudtop_cmd,
            "results": results,
        }
        if not all_ok:
            first_err = next((r["error"] for r in results if not r["ok"] and r.get("error")), "Gerrit vote failed")
            out["error"] = first_err
        return out

    def _post_single_submit(
        self, gob_curl: str, cl_id: str, payload_json: str
    ) -> dict[str, Any]:
        """Submits one CL (or its whole topic when `submitWholeTopic` is active) with `notify: OWNER`."""
        url = f"{self.client.api_host}/a/changes/{cl_id}/submit"
        try:
            res = SubprocessRunner().run(
                [
                    gob_curl,
                    "-s",
                    "-S",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload_json,
                    url,
                ],
                check=False,
                timeout_sec=GERRIT_CLI_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, ProcessTimeoutError):
            return {
                "cl": cl_id,
                "ok": False,
                "status": "TIMEOUT",
                "error": (
                    f"Timed out after {GERRIT_CLI_TIMEOUT_SEC}s while submitting ag/{cl_id} via `gob-curl`. "
                    f"Check Gerrit whether ag/{cl_id} already merged and verify network/`gcert` before retrying."
                ),
            }
        except OSError as exc:
            return {
                "cl": cl_id,
                "ok": False,
                "status": "ERROR",
                "error": (
                    f"Failed to execute `{gob_curl}` while submitting ag/{cl_id}: {exc}. "
                    "Verify `gob-curl` installation and permissions on PATH."
                ),
            }
        combined_out = f"{(res.stdout or '').strip()} {(res.stderr or '').strip()}".strip()
        if "change is merged" in combined_out.lower():
            return {"cl": cl_id, "ok": True, "status": "MERGED", "error": ""}
        if res.returncode == 0:
            raw_body = strip_xssi_prefix(res.stdout or "").strip()
            if raw_body:
                try:
                    parsed_body = json.loads(raw_body)
                    if isinstance(parsed_body, dict) and str(parsed_body.get("status") or "").upper() in {
                        "MERGED",
                        "SUBMITTED",
                    }:
                        return {
                            "cl": cl_id,
                            "ok": True,
                            "status": str(parsed_body["status"]),
                            "error": "",
                        }
                except ValueError:
                    pass
        detail = combined_out or f"gob-curl exited with status {res.returncode}"
        err_msg = (
            f"Failed to submit ag/{cl_id}: {detail}. "
            f"Verify submit requirements, mergeability, and `gcert` credentials, or open ag/{cl_id} in Gerrit."
        )
        if self.auth_email and is_agent_or_bot_email(self.auth_email):
            err_msg = (
                f"Backend is authenticated as role account '{self.auth_email}' "
                f"and cannot submit ag/{cl_id} directly ({detail}). "
                "Open in Gerrit or run the copied Cloudtop command to submit."
            )
        return {"cl": cl_id, "ok": False, "status": "ERROR", "error": err_msg}

    def submit_changes(self, cl_numbers: list[str | int]) -> dict[str, Any]:
        """Submits one or more submittable CLs/topics via `gob-curl` with mandatory `notify: OWNER`."""
        if not cl_numbers:
            raise ValueError(
                "Cannot submit Gerrit change: no CL numbers specified (provide `cl` or non-empty `cls`)."
            )
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot submit Gerrit change: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        cleaned_cls: list[str] = []
        seen: set[str] = set()
        for raw_cl in cl_numbers:
            cid = parse_cl_identifier(str(raw_cl), allow_head=False)
            if cid not in seen:
                seen.add(cid)
                cleaned_cls.append(cid)

        payload_json = json.dumps({"notify": OWNER_NOTIFY_HANDLING})
        results: list[dict[str, Any]] = []
        for cid in cleaned_cls:
            step = self._post_single_submit(gob_curl, cid, payload_json)
            results.append(step)
            if not step["ok"]:
                break

        merged_ids = [r["cl"] for r in results if r["ok"]]
        if merged_ids:
            with self._session_lock:
                self._raw_queue_cache.clear()
                for cid in merged_ids:
                    if cid.isdigit():
                        self._session_approved_cls.discard(int(cid))
                        self._session_votes.pop(int(cid), None)
            self._invalidate_cl_caches(merged_ids)

        all_ok = len(results) == len(cleaned_cls) and all(r["ok"] for r in results)
        cloudtop_cmd = " && ".join(
            f"gob-curl -s -X POST -H 'Content-Type: application/json' -d '{payload_json}' "
            f"'{self.client.api_host}/a/changes/{cid}/submit' >/dev/null"
            for cid in cleaned_cls
        )
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "error",
            "auth_email": self.auth_email,
            "submitted_cls": merged_ids,
            "cloudtop_cmd": cloudtop_cmd,
            "results": results,
        }
        if not all_ok:
            first_err = next(
                (r["error"] for r in results if not r["ok"] and r.get("error")),
                "Gerrit submit failed (verify all submit requirements and mergeability).",
            )
            out["error"] = first_err
        return out

    def assign_second_human_reviewer(self, cl_number: str | int, reviewer_email: str) -> dict[str, Any]:
        """Adds a 2nd human reviewer to an agent CL with `notify: OWNER`."""
        return self.modify_reviewer(
            cl_number=cl_number,
            action="add",
            account=reviewer_email,
            state="REVIEWER",
        )


    def _fetch_remote_reviewer_suggestions(
        self, cl_number: str | int, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Queries Gerrit `/suggest_reviewers` and normalizes account/group entries."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        enc_q = urllib.parse.quote(query, safe="@._-")
        raw_list = self.client._request_json(
            f"/a/changes/{cl_id}/suggest_reviewers?q={enc_q}&n={max(1, int(limit))}"
        )
        if not isinstance(raw_list, list):
            return []
        parsed: list[dict[str, Any]] = []
        for item in raw_list:
            acct = item.get("account") if isinstance(item, dict) else None
            grp = item.get("group") if isinstance(item, dict) else None
            if isinstance(acct, dict):
                em = (acct.get("email") or "").strip().lower()
                val = em or str(acct.get("_account_id") or "")
                short = em.split("@", 1)[0] if em else (acct.get("name") or val)
                if val:
                    parsed.append({
                        "value": val,
                        "email": em,
                        "name": (acct.get("display_name") or acct.get("name") or short).strip(),
                        "short": short,
                        "is_group": False,
                    })
            elif isinstance(grp, dict) and grp.get("name"):
                gname = str(grp["name"]).strip()
                parsed.append({
                    "value": gname,
                    "email": "",
                    "name": gname,
                    "short": gname,
                    "is_group": True,
                })
        return parsed

    def suggest_reviewers(
        self, cl_number: str | int, query: str = "", limit: int = 8
    ) -> list[dict[str, Any]]:
        """Returns reviewer/CC autocomplete candidates combining Gerrit `suggest_reviewers` and team roster."""
        q = (query or "").strip()
        q_lower = q.lower()
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        if len(q) >= 2 and str(cl_number).strip():
            try:
                for entry in self._fetch_remote_reviewer_suggestions(cl_number, q, limit):
                    dedup_key = entry["value"].lower()
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        suggestions.append(entry)
            except Exception:
                pass

        for roster_em in TEAM_ROSTER_EMAILS:
            if roster_em not in seen and (not q_lower or q_lower in roster_em):
                seen.add(roster_em)
                short = roster_em.split("@", 1)[0]
                suggestions.append({
                    "value": roster_em,
                    "email": roster_em,
                    "name": short,
                    "short": short,
                    "is_group": False,
                })
        return suggestions[: max(1, int(limit))]

    def _execute_reviewer_mutation(
        self, cl_id: str, norm_action: str, raw_acct: str, state: str
    ) -> tuple[str, str, Any]:
        """Executes the Gerrit REST reviewer add/move or delete call with `notify: OWNER`."""
        if norm_action == "remove":
            enc_acct = urllib.parse.quote(raw_acct, safe="")
            try:
                resp = self.client._request_json(
                    f"/a/changes/{cl_id}/reviewers/{enc_acct}/delete",
                    method="POST",
                    data={"notify": OWNER_NOTIFY_HANDLING},
                )
            except RuntimeError as exc:
                if "exit code 0" not in str(exc):
                    raise
                resp = {}
            return "REMOVED", raw_acct, resp
        if norm_action == "add":
            norm_state = "CC" if str(state or "").strip().upper() == "CC" else "REVIEWER"
            resolved_acct = (
                f"{raw_acct.lower()}@google.com"
                if _SIMPLE_LDAP_RE.fullmatch(raw_acct) and not raw_acct.isdigit()
                else raw_acct
            )
            resp = self.client._request_json(
                f"/a/changes/{cl_id}/reviewers",
                method="POST",
                data={"reviewer": resolved_acct, "state": norm_state, "notify": OWNER_NOTIFY_HANDLING},
            )
            if isinstance(resp, dict) and resp.get("error"):
                err_detail = str(resp["error"]).strip()
                raise RuntimeError(
                    f"Failed to add {norm_state} '{resolved_acct}' to CL {cl_id}: {err_detail}. "
                    "Verify the Gerrit email, LDAP, or group name and your CL permissions."
                )
            return norm_state, resolved_acct, resp
        raise ValueError(f"Unsupported reviewer action '{norm_action}' (expected 'add' or 'remove').")

    def modify_reviewer(
        self,
        cl_number: str | int,
        action: str,
        account: str | int,
        *,
        state: str = "REVIEWER",
        viewer_email: str | None = None,
    ) -> dict[str, Any]:
        """Adds, moves (`REVIEWER` <-> `CC`), or removes a reviewer/CC on `cl_number` with `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        norm_action = (action or "add").strip().lower()
        raw_acct = str(account or "").strip()
        if not raw_acct:
            raise ValueError("Reviewer or CC account email/ID is required.")
        norm_state, resolved_acct, resp = self._execute_reviewer_mutation(
            cl_id, norm_action, raw_acct, state
        )
        self._invalidate_cl_caches([cl_id])
        updated_change: dict[str, Any] | None = None
        try:
            updated_change = self.fetch_single_enriched_change(cl_id, viewer_email=viewer_email)
        except Exception:
            updated_change = None
        return {
            "status": "ok",
            "cl": cl_id,
            "action": norm_action,
            "reviewer": resolved_acct,
            "state": norm_state,
            "notify": OWNER_NOTIFY_HANDLING,
            "response": resp,
            "change": updated_change,
        }

    def _modify_single_reviewer_for_batch(
        self,
        cl_id: str,
        norm_action: str,
        raw_acct: str,
        state: str,
        viewer_email: str | None,
    ) -> dict[str, Any]:
        """Executes a reviewer/CC mutation on one CL and returns structured status."""
        try:
            res = self.modify_reviewer(
                cl_id,
                norm_action,
                raw_acct,
                state=state,
                viewer_email=viewer_email,
            )
            return {
                "cl": cl_id,
                "ok": True,
                "reviewer": res["reviewer"],
                "state": res["state"],
                "response": res["response"],
                "change": res["change"],
                "error": "",
            }
        except Exception as exc:
            return {
                "cl": cl_id,
                "ok": False,
                "reviewer": raw_acct,
                "state": (state or "REVIEWER").strip().upper(),
                "error": str(exc),
            }

    def modify_reviewers_batch(
        self,
        cl_numbers: list[str | int],
        action: str,
        account: str | int,
        *,
        state: str = "REVIEWER",
        viewer_email: str | None = None,
    ) -> dict[str, Any]:
        """Batch adds, moves (`REVIEWER` <-> `CC`), or removes a reviewer/CC across multiple CLs with `notify: OWNER`."""
        if not cl_numbers:
            raise ValueError(
                "Cannot modify reviewers in batch: no target CL numbers provided "
                "(pass non-empty 'cls' or 'cl')."
            )
        norm_action = (action or "add").strip().lower()
        if norm_action not in {"add", "remove"}:
            raise ValueError(
                f"Cannot modify reviewers in batch: unsupported action '{norm_action}' "
                "(expected 'add' or 'remove')."
            )
        raw_acct = str(account or "").strip()
        if not raw_acct:
            raise ValueError(
                "Cannot modify reviewers in batch: reviewer or CC account is empty "
                "(provide a non-empty 'account' or 'reviewer' email, LDAP, or group name)."
            )
        cleaned_cls: list[str] = []
        for raw_cl in cl_numbers:
            cid = parse_cl_identifier(str(raw_cl), allow_head=False)
            if cid not in cleaned_cls:
                cleaned_cls.append(cid)
        workers = max(1, min(DIFF_FETCH_WORKERS, len(cleaned_cls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda cid: self._modify_single_reviewer_for_batch(
                        cid, norm_action, raw_acct, state, viewer_email
                    ),
                    cleaned_cls,
                )
            )
        all_ok = all(r["ok"] for r in results)
        resolved_reviewer = next(
            (r["reviewer"] for r in results if r["ok"] and r.get("reviewer")),
            raw_acct,
        )
        norm_state = next(
            (r["state"] for r in results if r.get("state")),
            (state or "REVIEWER").strip().upper(),
        )
        updated_changes = [
            r["change"] for r in results if r["ok"] and isinstance(r.get("change"), dict)
        ]
        out: dict[str, Any] = {
            "status": "ok" if all_ok else "partial_error",
            "cls": cleaned_cls,
            "action": norm_action,
            "reviewer": resolved_reviewer,
            "state": norm_state,
            "notify": OWNER_NOTIFY_HANDLING,
            "results": results,
            "changes": updated_changes,
        }
        if len(updated_changes) == 1 and len(cleaned_cls) == 1:
            out["change"] = updated_changes[0]
        if not all_ok:
            first_err = next(
                (r["error"] for r in results if not r["ok"] and r.get("error")),
                f"Batch reviewer {norm_action} failed on one or more CLs.",
            )
            out["error"] = first_err
        return out

    def _resolve_chat_sender_email(self) -> str:
        """Returns the authenticated human `@google.com` email running the UI server."""
        auth = (self.auth_email or "").strip().lower()
        if auth and _GOOGLE_CORP_EMAIL_RE.fullmatch(auth) and not is_agent_or_bot_email(auth):
            return auth
        os_user = (os.environ.get("USER") or "").strip().lower()
        if os_user and _SIMPLE_LDAP_RE.fullmatch(os_user):
            return f"{os_user}@google.com"
        return ""

    def send_reviewer_chat_dm(
        self,
        cl_number: str | int,
        recipient: str,
        message: str,
    ) -> dict[str, Any]:
        """Sends a 1:1 Google Chat direct message to `recipient` via `gchat` after UI confirmation."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        resolved_email = _normalize_chat_dm_recipient(recipient, self._resolve_chat_sender_email())
        msg_text = str(message or "").strip()
        if not msg_text:
            raise ValueError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                "message text is empty. Enter a non-empty review reminder message."
            )
        if len(msg_text) > MAX_CHAT_DM_LENGTH:
            raise ValueError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                f"message length ({len(msg_text)} chars) exceeds {MAX_CHAT_DM_LENGTH}. "
                "Shorten the message and retry."
            )
        gchat_bin = resolve_ui_hook("_resolve_gchat_cli_bin", _resolve_gchat_cli_bin)()
        if not gchat_bin:
            raise RuntimeError(
                f"Failed to send Google Chat DM to '{resolved_email}' for ag/{cl_id}: "
                f"Google Chat CLI ('{DEFAULT_GCHAT_CLI_BIN}') was not found on this host. "
                "Run Fast Gerrit UI on gLinux/Cloudtop or click 'Copy Text' to paste into Chat."
            )
        proc = _run_gchat_send_dm_command(gchat_bin, resolved_email, msg_text, cl_id)
        space_name, message_name, chat_url = _parse_gchat_dm_output(proc.stdout)
        return {
            "status": "ok",
            "cl": cl_id,
            "recipient": resolved_email,
            "space_name": space_name,
            "message_name": message_name,
            "chat_url": chat_url,
        }


    def post_swarm_review(self, cl_number: str | int) -> dict[str, Any]:
        """Posts saved `gerrit_review_payload.json` comments for `cl_number` with `notify: OWNER`."""
        gob_curl = shutil.which("gob-curl")
        if not gob_curl:
            raise RuntimeError(
                "Cannot post Review Swarm payload: `gob-curl` is not installed on PATH. "
                "Run `gcert` on a gLinux / Cloudtop machine with `gob-curl` installed."
            )
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        swarm_dir = resolve_ui_hook("resolve_swarm_dir_for_cl", resolve_swarm_dir_for_cl)(cl_id)
        payload_path = (swarm_dir / GERRIT_REVIEW_PAYLOAD_FILENAME) if swarm_dir else None
        if payload_path is None or not payload_path.is_file():
            raise ValueError(
                f"No completed Review Swarm payload found for ag/{cl_id} "
                f"(expected '{GERRIT_REVIEW_PAYLOAD_FILENAME}'). Run Review Swarm first."
            )
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["notify"] = OWNER_NOTIFY_HANDLING
        post_res = self._post_single_vote(gob_curl, cl_id, json.dumps(payload), code_review=0)
        if post_res["ok"]:
            self._invalidate_cl_caches([cl_id])
        return {
            "status": "ok" if post_res["ok"] else "error",
            "cl": cl_id,
            "error": post_res.get("error", ""),
        }


    @staticmethod
    def _extract_posted_comment_meta(
        review_resp: Any, file_path: str
    ) -> tuple[str, int | None]:
        """Extracts `(comment_id, patch_set)` from Gerrit `ReviewResult` JSON when present."""
        comments_map = review_resp.get("comments") if isinstance(review_resp, dict) else None
        if not isinstance(comments_map, dict):
            return "", None
        entries = comments_map.get(file_path)
        if not isinstance(entries, list) or not entries:
            entries = next((v for v in comments_map.values() if isinstance(v, list) and v), None)
        first = (
            entries[0]
            if isinstance(entries, list) and entries and isinstance(entries[0], dict)
            else None
        )
        if not first:
            return "", None
        comment_id = str(first.get("id") or "").strip()
        raw_ps = first.get("patch_set")
        patch_set = int(raw_ps) if str(raw_ps or "").isdigit() else None
        return comment_id, patch_set

    def _post_review_payload(
        self, cl_id: str, revision_segment: str, payload: dict[str, Any]
    ) -> Any:
        """Posts `payload` to `/a/changes/{cl_id}/revisions/{revision_segment}/review` with `gob-curl` fallback."""
        endpoint = f"/a/changes/{cl_id}/revisions/{revision_segment}/review"
        try:
            return self.client._request_json(endpoint, method="POST", data=payload)
        except Exception as exc:
            gob_curl = shutil.which("gob-curl")
            if not gob_curl:
                raise
            post_res = self._post_single_vote(
                gob_curl,
                cl_id,
                json.dumps(payload),
                code_review=0,
                revision_segment=revision_segment,
            )
            if not post_res.get("ok"):
                err_detail = str(post_res.get("error") or exc).strip()
                raise RuntimeError(
                    f"Failed to post review comment on ag/{cl_id} (revision '{revision_segment}'): "
                    f"{err_detail}. Verify `gcert` SSO credentials and CL access, then retry."
                ) from exc
            return post_res.get("response")

    def post_inline_comment(
        self,
        cl_number: str | int,
        file_path: str,
        line: int,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        side: str = "REVISION",
        unresolved: bool = True,
    ) -> dict[str, Any]:
        """Posts an inline file/line/range comment on `cl_number` with mandatory `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
        cleaned_msg = (message or "").strip()
        if not cleaned_msg:
            raise ValueError(
                f"Cannot post inline comment on ag/{cl_id} ({cleaned_file}): "
                "comment message is empty. Enter a non-empty comment message and retry."
            )

        target_line = max(1, int(end_line or line or 1))
        s_ln = max(1, int(start_line or target_line))
        e_ln = max(s_ln, int(end_line or target_line))
        side_norm = "PARENT" if str(side or "").strip().upper() == "PARENT" else "REVISION"

        comment_entry: dict[str, Any] = {
            "line": e_ln,
            "side": side_norm,
            "message": cleaned_msg,
            "unresolved": bool(unresolved),
        }
        if e_ln > s_ln:
            comment_entry["range"] = {
                "start_line": s_ln,
                "start_character": 0,
                "end_line": e_ln,
                "end_character": 0,
            }

        payload: dict[str, Any] = {
            "notify": OWNER_NOTIFY_HANDLING,
            "comments": {
                cleaned_file: [comment_entry],
            },
        }
        review_resp = self._post_review_payload(cl_id, "current", payload)
        posted_id, posted_ps = self._extract_posted_comment_meta(review_resp, cleaned_file)

        self._invalidate_cl_caches([cl_id])
        author = (self.auth_email or self.default_viewer or "you").split("@")[0]
        return {
            "status": "ok",
            "cl": cl_id,
            "comment_id": posted_id,
            "patch_set": posted_ps,
            "file_path": cleaned_file,
            "line": e_ln,
            "start_line": s_ln,
            "end_line": e_ln,
            "side": side_norm,
            "unresolved": bool(unresolved),
            "message": cleaned_msg,
            "author_name": author,
            "notify": OWNER_NOTIFY_HANDLING,
        }

    def reply_inline_comment(
        self,
        cl_number: str | int,
        comment_id: str,
        file_path: str,
        message: str,
        *,
        line: int | None = None,
        patch_set: int | None = None,
        side: str = "REVISION",
        unresolved: bool = False,
    ) -> dict[str, Any]:
        """Posts an inline reply (`in_reply_to`) on `cl_number` with mandatory `notify: OWNER`."""
        cl_id = parse_cl_identifier(str(cl_number), allow_head=False)
        cleaned_comment_id = str(comment_id or "").strip()
        if not cleaned_comment_id:
            raise ValueError(
                f"Cannot post inline reply on ag/{cl_id}: target Gerrit comment_id is empty. "
                "Refresh the CL diff to load the Gerrit comment ID and retry."
            )
        cleaned_msg = str(message or "").strip()
        if not cleaned_msg:
            raise ValueError(
                f"Cannot post inline reply to comment '{cleaned_comment_id}' on ag/{cl_id}: "
                "reply message is empty. Enter a non-empty reply message and retry."
            )

        cleaned_file = (file_path or "").strip() or "/COMMIT_MSG"
        side_norm = "PARENT" if str(side or "").strip().upper() == "PARENT" else "REVISION"
        resolved_ps = int(patch_set) if patch_set is not None and int(patch_set) > 0 else None
        revision_segment = str(resolved_ps) if resolved_ps is not None else "current"
        target_line = int(line) if line is not None and int(line) > 0 else None

        reply_entry: dict[str, Any] = {
            "in_reply_to": cleaned_comment_id,
            "message": cleaned_msg,
            "unresolved": bool(unresolved),
        }
        if target_line is not None:
            reply_entry["line"] = target_line

        payload: dict[str, Any] = {
            "notify": OWNER_NOTIFY_HANDLING,
            "comments": {
                cleaned_file: [reply_entry],
            },
        }
        review_resp = self._post_review_payload(cl_id, revision_segment, payload)
        posted_id, posted_ps = self._extract_posted_comment_meta(review_resp, cleaned_file)

        self._invalidate_cl_caches([cl_id])
        author = (self.auth_email or self.default_viewer or "you").split("@")[0]
        return {
            "status": "ok",
            "cl": cl_id,
            "in_reply_to": cleaned_comment_id,
            "comment_id": posted_id,
            "file_path": cleaned_file,
            "line": target_line or 1,
            "patch_set": posted_ps or resolved_ps,
            "side": side_norm,
            "unresolved": bool(unresolved),
            "message": cleaned_msg,
            "author_name": author,
            "notify": OWNER_NOTIFY_HANDLING,
        }

