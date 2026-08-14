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

"""Buganizer client wrapper for issue searching, deduplication, and bug creation."""

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BuganizerIssuePayload:
    title: str
    description: str
    component_id: int
    priority: str = "P2"
    severity: str = "S2"
    tags: Optional[List[str]] = None


def format_buganizer_search_query(
    component_id: int, test_identifier: str
) -> str:
    """Formats Buganizer search query string to check for open bugs by test signature."""
    return f'componentid:{component_id} status:open "{test_identifier}"'


class BuganizerClient:
    """Client wrapper for Buganizer operations (using issues CLI or REST endpoint)."""

    def __init__(self, issues_bin: Optional[str] = None):
        self.issues_bin = (
            issues_bin or "/google/bin/releases/issues-cli/issues"
        )
        self._component_bugs_cache: Dict[int, List[str]] = {}

    def fetch_all_component_open_bugs(self, component_id: int) -> List[Dict[str, str]]:
        """Fetches all open bug titles/contents for a component in a single batch call."""
        cid = int(component_id)
        if cid in self._component_bugs_cache:
            return self._component_bugs_cache[cid]

        if not os.path.exists(self.issues_bin):
            self._component_bugs_cache[cid] = []
            return []

        try:
            query = f"componentid:{cid} status:open"
            out = subprocess.check_output(
                [self.issues_bin, "readonly", "search", f"--query={query}"],
                timeout=15,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8")

            parsed_bugs: List[Dict[str, str]] = []
            clean_out = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", out)

            current_id = ""
            current_title = ""

            for line in clean_out.splitlines():
                line_str = line.strip()
                if not line_str or line_str.startswith("---"):
                    if current_id and current_title:
                        parsed_bugs.append({"issue_id": current_id, "title": current_title})
                        current_id = ""
                        current_title = ""
                    continue

                m_id = re.search(r"^(?:Issue ID|ID):\s*(\d+)", line_str, re.IGNORECASE)
                if m_id:
                    current_id = m_id.group(1).strip()
                    continue

                m_title = re.search(r"^Title:\s*(.+)", line_str, re.IGNORECASE)
                if m_title:
                    current_title = m_title.group(1).strip()
                    continue

                m_single = re.search(r"^(?:Issue\s+|b/)?(\d+)\s*[:\-\t]\s*(.+)$", line_str, re.IGNORECASE)
                if m_single:
                    parsed_bugs.append({"issue_id": m_single.group(1).strip(), "title": m_single.group(2).strip()})

            if current_id and current_title:
                parsed_bugs.append({"issue_id": current_id, "title": current_title})

            self._component_bugs_cache[cid] = parsed_bugs
            return parsed_bugs
        except Exception:
            self._component_bugs_cache[cid] = []
            return []

    def find_existing_open_bug(
        self, component_id: int, test_identifier: str
    ) -> Optional[str]:
        """Returns the open Buganizer Issue ID string (e.g. '543049976') if a bug for test_identifier exists."""
        clean_target = test_identifier.replace("@@goldfish+//", "@goldfish//")
        bugs = self.fetch_all_component_open_bugs(component_id)
        if not bugs:
            return None
        for b in bugs:
            if isinstance(b, dict):
                title = b.get("title", "")
                issue_id = b.get("issue_id")
            else:
                title = str(b)
                m = re.search(r"(\d+)", title)
                issue_id = m.group(1) if m else title
            if clean_target in title or test_identifier in title:
                return issue_id
        return None

    def is_test_already_reported(
        self, component_id: int, test_identifier: str
    ) -> bool:
        """Checks if a test identifier is already reported in an open bug for component."""
        return self.find_existing_open_bug(component_id, test_identifier) is not None

    def search_open_bugs(
        self, component_id: int, test_identifier: str
    ) -> List[dict]:
        """Searches for open Buganizer issues matching test identifier."""
        if self.is_test_already_reported(component_id, test_identifier):
            return [{"raw": "matching_open_bug"}]

        if not os.path.exists(self.issues_bin):
            return []

        query = format_buganizer_search_query(component_id, test_identifier)
        try:
            out = subprocess.check_output(
                [self.issues_bin, "readonly", "search", f"--query={query}"],
                timeout=10,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8")
            issues = []
            for line in out.splitlines():
                if "issue" in line.lower() or "b/" in line:
                    issues.append({"raw": line.strip()})
            return issues
        except Exception:
            return []

    def create_bug(
        self, payload: BuganizerIssuePayload, dry_run: bool = False
    ) -> Optional[str]:
        """Creates a new Buganizer issue or returns dry-run placeholder."""
        if dry_run:
            return "b/DRY_RUN_BUG_ID"

        if not os.path.exists(self.issues_bin):
            return None

        try:
            import tempfile

            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                tf.write(payload.description)
                tf_path = tf.name

            cmd = [
                self.issues_bin,
                "mutate",
                "create",
                f"--component_id={payload.component_id}",
                f"--title={payload.title}",
                f"--description_file={tf_path}",
                f"--priority={payload.priority}",
                f"--severity={payload.severity}",
            ]
            out = subprocess.check_output(cmd, timeout=15).decode("utf-8").strip()
            try:
                os.unlink(tf_path)
            except OSError:
                pass
            m = re.search(r"b/(\d+)", out) or re.search(r"(\d{6,})", out)
            if m:
                return f"b/{m.group(1)}"
            return out
        except Exception:
            return None
