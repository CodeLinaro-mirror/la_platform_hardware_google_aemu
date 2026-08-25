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

"""ResultStore and Sponge / Fusion2 API integration for emulator test diagnostics."""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CORP_RESULTSTORE_BLADE = "blade:google.devtools.resultstore.v2.corpresultstoredownload-prod"


@dataclass
class ResultStoreFile:
    uid: str
    uri: str
    length: int = 0
    digest: str = ""


@dataclass
class ResultStoreAction:
    invocation_id: str
    target_id: str
    action_id: str
    configuration_id: str
    status: str
    description: str = ""
    error_messages: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    files: List[ResultStoreFile] = field(default_factory=list)


@dataclass
class ResultStoreInvocation:
    invocation_id: str
    fusion_url: str
    sponge_url: str
    actions: List[ResultStoreAction] = field(default_factory=list)
    failed_actions: List[ResultStoreAction] = field(default_factory=list)
    passed_actions: List[ResultStoreAction] = field(default_factory=list)


def extract_uuid(identifier_or_url: str) -> str:
    """Extracts a 36-char invocation UUID from a raw UUID or Fusion2 / Sponge URL."""
    uuid_pattern = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    match = re.search(uuid_pattern, identifier_or_url)
    if match:
        return match.group(1).lower()
    return identifier_or_url.strip()


def query_resultstore_invocation(
    invocation_id_or_url: str,
    target_filter: Optional[str] = None,
    timeout_seconds: int = 45,
) -> Optional[ResultStoreInvocation]:
    """Queries ResultStore via Stubby RPC for complete invocation metadata, actions, and test logs."""
    uuid = extract_uuid(invocation_id_or_url)
    if not uuid:
        return None

    req_ext_content = (
        "[google.rpc.context.system_parameter_context] {\n"
        '  field_mask: "next_page_token,actions.id,actions.status_attributes,actions.test_action,actions.files"\n'
        "}\n"
    )

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as ext_file:
        ext_file.write(req_ext_content)
        ext_path = ext_file.name

    try:
        cmd = [
            "stubby",
            "call",
            "--globaldb",
            f"--request_extensions_file={ext_path}",
            CORP_RESULTSTORE_BLADE,
            "CorpResultStoreDownload.ExportInvocation",
            f'name:"invocations/{uuid}"',
        ]
        logger.debug("Executing Stubby ResultStore RPC: %s", " ".join(cmd))
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if res.returncode != 0:
            logger.warning("ResultStore ExportInvocation failed (code %d): %s", res.returncode, res.stderr)
            return None

        raw_output = res.stdout
        return parse_resultstore_textproto(uuid, raw_output, target_filter=target_filter)
    except Exception as e:
        logger.error("Exception querying ResultStore for invocation %s: %s", uuid, e)
        return None
    finally:
        try:
            os.remove(ext_path)
        except OSError:
            pass


def parse_resultstore_textproto(
    uuid: str,
    raw_text: str,
    target_filter: Optional[str] = None,
) -> ResultStoreInvocation:
    """Parses textproto output from CorpResultStoreDownload.ExportInvocation."""
    fusion_url = f"https://fusion2.corp.google.com/invocations/{uuid}"
    sponge_url = f"http://sponge2/{uuid}"

    actions: List[ResultStoreAction] = []
    action_blocks = raw_text.split("actions {")

    for block in action_blocks[1:]:
        target_id_m = re.search(r'target_id:\s*"([^"]+)"', block)
        action_id_m = re.search(r'action_id:\s*"([^"]+)"', block)
        config_id_m = re.search(r'configuration_id:\s*"([^"]+)"', block)
        status_m = re.search(r'status:\s*([A-Z_]+)', block)
        desc_m = re.search(r'description:\s*"([^"]+)"', block)

        target_id = target_id_m.group(1) if target_id_m else ""
        action_id = action_id_m.group(1) if action_id_m else ""
        config_id = config_id_m.group(1) if config_id_m else ""
        status = status_m.group(1) if status_m else "UNKNOWN"
        description = desc_m.group(1) if desc_m else ""

        if target_filter:
            norm_filter = target_filter.replace("@@goldfish+//", "@goldfish//").replace("@@", "@")
            norm_target = target_id.replace("@@goldfish+//", "@goldfish//").replace("@@", "@")
            if norm_filter not in norm_target:
                continue

        error_messages = re.findall(r'error_message:\s*"([^"]+)"', block)

        # Extract timing
        duration_seconds = 0.0
        sec_m = re.search(r'test_process_duration\s*{\s*seconds:\s*(\d+)', block)
        nano_m = re.search(r'test_process_duration\s*{[^}]*nanos:\s*(\d+)', block)
        if sec_m:
            duration_seconds += float(sec_m.group(1))
        if nano_m:
            duration_seconds += float(nano_m.group(1)) / 1e9

        # Extract files
        files: List[ResultStoreFile] = []
        file_blocks = block.split("files {")
        for fb in file_blocks[1:]:
            uid_m = re.search(r'uid:\s*"([^"]+)"', fb)
            uri_m = re.search(r'uri:\s*"([^"]+)"', fb)
            len_m = re.search(r'value:\s*(\d+)', fb)
            dig_m = re.search(r'digest:\s*"([^"]+)"', fb)
            if uid_m and uri_m:
                files.append(
                    ResultStoreFile(
                        uid=uid_m.group(1),
                        uri=uri_m.group(1),
                        length=int(len_m.group(1)) if len_m else 0,
                        digest=dig_m.group(1) if dig_m else "",
                    )
                )

        actions.append(
            ResultStoreAction(
                invocation_id=uuid,
                target_id=target_id,
                action_id=action_id,
                configuration_id=config_id,
                status=status,
                description=description,
                error_messages=error_messages,
                duration_seconds=duration_seconds,
                files=files,
            )
        )

    failed = [a for a in actions if a.status in ("FAILED", "INCOMPLETE", "INTERRUPTED", "CANCELLED")]
    passed = [a for a in actions if a.status in ("PASSED", "SUCCEEDED", "BUILT")]

    return ResultStoreInvocation(
        invocation_id=uuid,
        fusion_url=fusion_url,
        sponge_url=sponge_url,
        actions=actions,
        failed_actions=failed,
        passed_actions=passed,
    )
