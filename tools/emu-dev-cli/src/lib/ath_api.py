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

"""Android Test Hub (ATH) / AnTS and Android Build API integration."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request
from urllib.error import HTTPError

logger = logging.getLogger(__name__)

SUPPORTED_TARGET_PLATFORMS: List[str] = [
    "emulator_linux_x64",
    "emulator_linux_x64_asan",
    "emulator_linux_x64_tsan",
    "emulator_windows_x64",
    "emulator_mac_aarch64",
]

SUPPORTED_TARGET_CHOICES: List[str] = SUPPORTED_TARGET_PLATFORMS + ["all"]

TARGET_BAZEL_CONFIG_MAP: Dict[str, List[str]] = {
    "emulator_linux_x64": [],
    "emulator_linux_x64_asan": ["--config=asan"],
    "emulator_linux_x64_tsan": ["--config=tsan"],
    "emulator_windows_x64": (
        ["--config=rbe-win-x64"] if sys.platform.startswith("linux") else ["--config=windows"]
    ),
    "emulator_mac_aarch64": ["--config=macos_arm64"],
}


@dataclass
class FlakyTestRecord:
    test_identifier: str
    module_name: str
    target: str
    config_name: str
    total_runs: int
    failed_runs: int
    flake_rate_pct: float
    latest_build_id: str
    latest_invocation_id: str
    latest_work_unit_id: str
    stack_trace_snippet: Optional[str] = None


@dataclass
class TestHistoryEntry:
    invocation_id: str
    build_id: str
    target: str
    timestamp: str
    status: str
    run_type: str
    test_uri: str
    work_unit_id: str = ""
    duration_ms: Optional[int] = None


def sanitize_bazel_target(test_name: str) -> str:
    """Cleans up AnTS/ATH raw test labels into standard Bazel workspace labels.
    Maps canonical Bzlmod repo names (e.g. @@goldfish+//) to apparent names (@goldfish//).
    """
    target = test_name
    if target.startswith("@@goldfish+//") or target.startswith("@goldfish+//"):
        target = "@goldfish//" + target.split("//", 1)[1]

    if ":" in target:
        pkg, target_name = target.split(":", 1)
        clean_target_name = re.sub(r"-[0-9a-f]{32,}.*$", "", target_name)
        clean_target_name = re.sub(r"-run\d+.*$", "", clean_target_name)
        target = f"{pkg}:{clean_target_name}"

    return target


def get_bazel_flags_for_target(
    test_name: Optional[str] = None,
    target: str = "emulator_linux_x64",
    iterations: int = 1,
    runs_per_test: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Generates bazel test command string and flags for a given target platform and iteration count."""
    if test_name in SUPPORTED_TARGET_PLATFORMS and target not in SUPPORTED_TARGET_PLATFORMS:
        target, test_name = test_name, None

    if target not in TARGET_BAZEL_CONFIG_MAP:
        raise ValueError(
            f"Unsupported target platform '{target}'. Supported: {SUPPORTED_TARGET_PLATFORMS}"
        )

    effective_iterations = runs_per_test if runs_per_test is not None else iterations
    flags: List[str] = []
    if test_name:
        flags.append(sanitize_bazel_target(test_name))

    config_flags = TARGET_BAZEL_CONFIG_MAP[target]
    flags.extend(config_flags)
    if effective_iterations > 1:
        flags.append(f"--runs_per_test={effective_iterations}")
    else:
        flags.append("--runs_per_test=1")

    return "test", flags


def calculate_stress_test_timeout(
    target: str = "emulator_linux_x64",
    iterations: int = 1,
    explicit_timeout: Optional[int] = None,
) -> int:
    """Calculates dynamic execution timeout in seconds based on target platform sanitizers and iteration count.

    Args:
        target: Target platform string (e.g. 'emulator_linux_x64_tsan').
        iterations: Number of test iterations (e.g. 50).
        explicit_timeout: Optional explicit user-provided timeout override.

    Returns:
        Timeout duration in seconds.
    """
    if explicit_timeout and explicit_timeout > 0:
        return explicit_timeout

    multiplier = 1.0
    if "tsan" in target.lower():
        multiplier = 3.5
    elif "asan" in target.lower():
        multiplier = 2.5

    base_seconds = 180
    per_iteration_seconds = 12.0
    calculated = int(base_seconds + (iterations * per_iteration_seconds * multiplier))
    return max(300, calculated)



def parse_ath_test_results(
    raw_json: Dict[str, Any],
    target: str = "emulator_linux_x64",
    config_name: str = "devtools/emulator",
) -> List[FlakyTestRecord]:
    """Parses raw Android Test Hub / AnTS API JSON response into structured FlakyTestRecord list."""
    records: List[FlakyTestRecord] = []
    results = raw_json.get("testResults", raw_json.get("tests", []))
    for item in results:
        test_id = item.get("testIdentifier", item.get("name", item.get("test", "")))
        module_name = item.get("moduleName", item.get("module", ""))
        total = item.get("totalRuns", item.get("totalCount", 0))
        failed = item.get("failedRuns", item.get("failCount", 0))
        flake_rate = (failed / total * 100.0) if total > 0 else float(item.get("flakeRate", 0.0))

        records.append(
            FlakyTestRecord(
                test_identifier=test_id,
                module_name=module_name,
                target=target,
                config_name=config_name,
                total_runs=total,
                failed_runs=failed,
                flake_rate_pct=flake_rate,
                latest_build_id=item.get("latestBuildId", item.get("buildId", "")),
                latest_invocation_id=item.get("latestInvocationId", item.get("invocationId", "")),
                latest_work_unit_id=item.get("latestWorkUnitId", item.get("workUnitId", "")),
                stack_trace_snippet=item.get("stackTraceSnippet", item.get("stackTrace")),
            )
        )
    return records


def get_android_build_token(
    user_token: Optional[str] = None, force_refresh: bool = False
) -> str:
    """Acquires OAuth2 token for Android Build API via OAuthTokenManager."""
    from lib.oauth import OAuthTokenManager

    manager = OAuthTokenManager(
        scopes=["https://www.googleapis.com/auth/androidbuild.internal"],
        env_vars=["ANDROID_BUILD_TOKEN", "BUGANIZER_TOKEN", "OAUTH2_TOKEN"],
    )
    token = manager.get_token(user_token=user_token, force_refresh=force_refresh)
    return token or ""


class AthHttpClient:
    """Connection-pooled HTTP client for high-throughput Android Build API queries."""

    def __init__(self, max_connections: int = 32):
        self.max_connections = max_connections
        self._token: Optional[str] = None
        self._headers: Dict[str, str] = {}
        self._pool = None
        try:
            import urllib3

            retries = urllib3.util.Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            self._pool = urllib3.PoolManager(
                num_pools=10,
                maxsize=max_connections,
                retries=retries,
            )
        except Exception:
            self._pool = None

    def set_token(self, token: Optional[str]) -> None:
        if token:
            self._token = token
            self._headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            }

    def get_token(self, user_token: Optional[str] = None, force_refresh: bool = False) -> str:
        if user_token:
            self.set_token(user_token)
            return self._token or ""
        if not self._token or force_refresh:
            self._token = get_android_build_token(force_refresh=force_refresh)
            self._headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            }
        return self._token or ""

    def get_json(
        self,
        url: str,
        user_token: Optional[str] = None,
        retry_on_401: bool = True,
    ) -> Dict[str, Any]:
        self.get_token(user_token=user_token)
        if self._pool is not None:
            try:
                resp = self._pool.request("GET", url, headers=self._headers, timeout=15.0)
                if resp.status == 401 and retry_on_401:
                    self.get_token(force_refresh=True)
                    resp = self._pool.request("GET", url, headers=self._headers, timeout=15.0)
                if resp.status in (401, 403):
                    raise HTTPError(
                        url,
                        resp.status,
                        f"HTTP {resp.status} Forbidden/Unauthorized",
                        resp.headers,
                        None,
                    )
                if resp.status >= 400:
                    raise HTTPError(
                        url,
                        resp.status,
                        f"HTTP {resp.status} Error",
                        resp.headers,
                        None,
                    )
                return json.loads(resp.data.decode("utf-8"))
            except HTTPError:
                raise
            except Exception:
                pass

        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 401 and retry_on_401:
                self.get_token(force_refresh=True)
                req = urllib.request.Request(url, headers=self._headers)
                with urllib.request.urlopen(req, timeout=15) as retry_resp:
                    return json.loads(retry_resp.read().decode("utf-8"))
            raise


# Global pooled client instance
_ath_http_client = AthHttpClient()


def execute_http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    user_token: Optional[str] = None,
    retry_on_401: bool = True,
) -> Dict[str, Any]:
    """Executes authenticated HTTP GET request using connection pool with automatic 401 token refresh."""
    return _ath_http_client.get_json(url, user_token=user_token, retry_on_401=retry_on_401)


def fetch_invocation_artifacts(
    invocation_id: str,
    artifact_type: str = "LOGCAT",
    out_dir: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    from lib.workspace import get_flakiness_sandbox_dir

    output_dir = (
        Path(out_dir).resolve()
        if out_dir
        else get_flakiness_sandbox_dir(invocation_id)
    )
    output_dir.mkdir(parents=True, exist_ok=True)


    auth_token = get_android_build_token(user_token=token)
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/json",
    }

    type_to_files = {
        "LOGCAT": ["logcat.txt"],
        "HOST_LOG": ["host_stdout.log", "host_stderr.log"],
        "PERFETTO": ["trace.perfetto-trace"],
        "THREAD_DUMP": ["threads.dump"],
        "ALL": [
            "logcat.txt",
            "host_stdout.log",
            "host_stderr.log",
            "trace.perfetto-trace",
            "threads.dump",
        ],
    }

    files_to_download = type_to_files.get(artifact_type.upper(), ["logcat.txt"])
    downloaded_files: List[Dict[str, Any]] = []

    artifacts_url = f"https://androidbuildinternal.googleapis.com/android/internal/build/v3/invocations/{invocation_id}/artifacts"
    remote_artifacts: List[Dict[str, Any]] = []
    try:
        data = execute_http_get(artifacts_url, user_token=auth_token)
        remote_artifacts = data.get("artifacts", [])
    except Exception as e:
        sys.stderr.write(f"ℹ️ Artifacts endpoint note for {invocation_id}: {e}\n")

    for fname in files_to_download:
        dest_path = output_dir / fname
        source = "LOCAL_STUB"
        is_synthetic = True
        content = (
            f"=== Diagnostic Log: {fname} for Invocation {invocation_id} ===\n"
            f"Timestamp: {datetime.datetime.now().isoformat()}\n"
            f"Artifact Type: {artifact_type}\n"
            f"Source Status: LOCAL_STUB (Remote artifact 404 / unavailable upstream)\n"
        )

        matched_remote = next(
            (a for a in remote_artifacts if a.get("name", "").endswith(fname)),
            None,
        )
        if matched_remote and matched_remote.get("downloadUri"):
            try:
                dl_req = urllib.request.Request(
                    matched_remote["downloadUri"], headers=headers
                )
                with urllib.request.urlopen(dl_req, timeout=30) as dl_resp:
                    content = dl_resp.read().decode("utf-8", errors="replace")
                    source = "REMOTE"
                    is_synthetic = False
            except Exception as dl_err:
                sys.stderr.write(f"⚠️ Warning downloading {fname}: {dl_err}\n")

        dest_path.write_text(content, encoding="utf-8")
        file_size = dest_path.stat().st_size
        downloaded_files.append(
            {
                "file_name": fname,
                "file_path": str(dest_path.resolve()),
                "size_bytes": file_size,
                "source": source,
                "is_synthetic": is_synthetic,
            }
        )

    return {
        "invocation_id": invocation_id,
        "artifact_type": artifact_type,
        "out_dir": str(output_dir.resolve()),
        "downloaded_files": downloaded_files,
        "total_files": len(downloaded_files),
    }


def fetch_ath_invocations(
    target: str,
    branch: str = "git_emu-main-next",
    days: int = 7,
    mode: str = "all",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetches and filters Android Test Hub invocations by target platform, branch, date window, and run mode.

    If target == 'all', queries across all SUPPORTED_TARGET_PLATFORMS.
    """
    targets_to_query = SUPPORTED_TARGET_PLATFORMS if target == "all" else [target]
    auth_token = get_android_build_token(user_token=token)
    cutoff_ms = (time.time() - days * 86400) * 1000
    all_invocations: List[Dict[str, Any]] = []

    for tgt in targets_to_query:
        presubmit_invs: List[Dict[str, Any]] = []
        page_token = None
        while len(presubmit_invs) < 1500:
            inv_url = f"https://androidbuildinternal.googleapis.com/android/internal/build/v3/invocations?branch={branch}&target={tgt}&maxResults=100"
            if page_token:
                inv_url += f"&pageToken={page_token}"
            inv_data = execute_http_get(inv_url, user_token=auth_token)
            page_items = inv_data.get("invocations", [])
            if not page_items:
                break
            presubmit_invs.extend(page_items)
            oldest_ts = int(page_items[-1].get("timing", {}).get("creationTimestamp", "0"))
            page_token = inv_data.get("nextPageToken")
            if (oldest_ts and oldest_ts < cutoff_ms) or not page_token:
                break

        presubmit_invs = [
            i for i in presubmit_invs
            if int(i.get("timing", {}).get("creationTimestamp", "0")) >= cutoff_ms
            and i.get("primaryBuild", {}).get("buildTarget") == tgt
        ]

        postsubmit_build_ids = set()
        for inv in presubmit_invs:
            for prop in inv.get("properties", []):
                if prop.get("name") == "reference_build_id" and prop.get("value"):
                    postsubmit_build_ids.add(prop.get("value"))

        postsubmit_invs: List[Dict[str, Any]] = []
        if mode in ("all", "postsubmit"):
            def fetch_postsubmit_invs_for_build(bid: str) -> List[Dict[str, Any]]:
                p_url = f"https://androidbuildinternal.googleapis.com/android/internal/build/v3/invocations?buildId={bid}"
                try:
                    b_data = execute_http_get(p_url, user_token=auth_token)
                    return [
                        i for i in b_data.get("invocations", [])
                        if i.get("primaryBuild", {}).get("buildTarget") == tgt
                    ]
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=8) as p_executor:
                p_futs = [p_executor.submit(fetch_postsubmit_invs_for_build, bid) for bid in postsubmit_build_ids]
                for f in as_completed(p_futs):
                    postsubmit_invs.extend(f.result())

        if mode == "presubmit":
            all_invocations.extend(presubmit_invs)
        elif mode == "postsubmit":
            all_invocations.extend(postsubmit_invs)
        else:
            seen_inv_ids = set()
            for inv in presubmit_invs + postsubmit_invs:
                iid = inv.get("invocationId")
                if iid and iid not in seen_inv_ids:
                    seen_inv_ids.add(iid)
                    all_invocations.append(inv)

    return all_invocations


def query_ath_flaky_tests(
    target: str,
    config_name: str = "devtools/emulator",
    branch: str = "git_emu-main-next",
    min_flake_rate: float = 5.0,
    days: int = 7,
    mode: str = "all",
    token: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 32,
) -> List[FlakyTestRecord]:
    """Queries Android Test Hub / AnTS API for flaky test records matching target and branch in parallel.
    Supports mode='all', mode='presubmit', or mode='postsubmit'.
    """
    records: List[FlakyTestRecord] = []

    try:
        invocations = fetch_ath_invocations(
            target=target,
            branch=branch,
            days=days,
            mode=mode,
            token=token,
        )

        total_invs = len(invocations)
        if progress_callback:
            progress_callback(0, total_invs, f"Fetched {total_invs} invocations ({mode}) for {target}")

        def fetch_work_units_for_invocation(inv_item: Dict[str, Any]) -> Tuple[str, str, List[Dict[str, Any]]]:
            inv_id = inv_item.get("invocationId", "")
            build_id = inv_item.get("primaryBuild", {}).get("buildId", inv_item.get("primaryBuildId", ""))
            if not inv_id:
                return inv_id, build_id, []

            all_wus: List[Dict[str, Any]] = []
            page_token = None
            while True:
                wu_url = f"https://androidbuildinternal.googleapis.com/android/internal/build/v3/workunits?invocationId={inv_id}&maxResults=100"
                if page_token:
                    wu_url += f"&pageToken={page_token}"
                try:
                    wu_data = execute_http_get(wu_url, user_token=auth_token)
                    page_units = wu_data.get("workUnits", [])
                    if not page_units:
                        break
                    all_wus.extend(page_units)
                    page_token = wu_data.get("nextPageToken")
                    if not page_token:
                        break
                except Exception:
                    break
            return inv_id, build_id, all_wus

        test_stats: Dict[str, Dict[str, Any]] = {}
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_inv = {executor.submit(fetch_work_units_for_invocation, inv): inv for inv in invocations}
            for future in as_completed(future_to_inv):
                completed_count += 1
                inv_id, build_id, work_units = future.result()

                if progress_callback:
                    progress_callback(
                        completed_count,
                        total_invs,
                        f"Processed invocation {inv_id or 'unknown'} ({len(work_units)} work units)",
                    )

                for wu in work_units:
                    if wu.get("type") and wu.get("type") != "BAZEL_TARGET":
                        continue

                    raw_name = wu.get("name")
                    if not raw_name or raw_name in ("None", "root"):
                        continue

                    test_name = sanitize_bazel_target(raw_name)

                    if test_name not in test_stats:
                        test_stats[test_name] = {
                            "total": 0,
                            "failed": 0,
                            "latest_build_id": build_id,
                            "latest_invocation_id": inv_id,
                            "module_name": wu.get("moduleName", test_name),
                        }

                    test_stats[test_name]["total"] += 1
                    state = str(wu.get("state", "")).lower()
                    status = str(wu.get("status", "")).lower()
                    error_count = wu.get("errorCount", 0)
                    if state in ("failed", "error") or status in ("failed", "error") or error_count > 0:
                        test_stats[test_name]["failed"] += 1

        for test_name, stats in test_stats.items():
            total = stats["total"]
            failed = stats["failed"]
            flake_rate = (failed / total * 100.0) if total > 0 else 0.0
            if flake_rate >= min_flake_rate or (min_flake_rate == 0.0 and total > 0):
                records.append(
                    FlakyTestRecord(
                        test_identifier=test_name,
                        module_name=stats["module_name"],
                        target=target,
                        config_name=config_name,
                        total_runs=total,
                        failed_runs=failed,
                        flake_rate_pct=flake_rate,
                        latest_build_id=stats["latest_build_id"],
                        latest_invocation_id=stats["latest_invocation_id"],
                        latest_work_unit_id="",
                    )
                )

        records.sort(key=lambda r: (r.failed_runs, r.flake_rate_pct, r.total_runs), reverse=True)
    except HTTPError as e:
        sys.stderr.write(f"\n⚠️ HTTP Error {e.code} querying Android Test Hub API: {e.reason}\n")
        if e.code in (401, 403):
            sys.stderr.write(
                "\n=== Android Build / ATH Authentication Scope Notice (macOS) ===\n"
                "The Android Build API endpoint (androidbuildinternal.googleapis.com) requires an internal\n"
                "Google corporate token with scope 'https://www.googleapis.com/auth/androidbuild.internal'.\n\n"
                "On macOS workstations where the internal SSO binary (/google/data/ro/teams/oneplatform/sso) is unavailable,\n"
                "plain 'oauth2l fetch' yields public/cloud-platform credentials that are rejected with HTTP 403.\n\n"
                "To resolve on macOS:\n"
                "1. Generate an internal token on a GLinux workstation:\n"
                "   oauth2l fetch --sso $USER@google.com androidbuild.internal\n"
                "2. Export it locally:\n"
                "   export ANDROID_BUILD_TOKEN=\"<token>\"\n"
                "   # or pass --token <token>\n"
                "=================================================================\n\n"
            )
            raise RuntimeError(
                f"Authentication/Authorization failed for Android Test Hub API (HTTP {e.code}: {e.reason}). "
                "Ensure valid credentials with 'export ANDROID_BUILD_TOKEN=\"<token>\"' or '--token <token>'."
            ) from e
        raise RuntimeError(f"HTTP Error {e.code} querying Android Test Hub API: {e.reason}") from e
    except Exception as e:
        sys.stderr.write(f"\nWarning: Error querying Android Test Hub API: {e}\n")
        raise RuntimeError(f"Error querying Android Test Hub API: {e}") from e

    return records


def query_test_history(
    test_target: str,
    target: str = "emulator_linux_x64_tsan",
    branch: str = "git_emu-main-next",
    days: int = 7,
    mode: str = "all",
    limit: int = 50,
    token: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 32,
) -> Tuple[Dict[str, Any], List[TestHistoryEntry]]:
    """Queries Android Test Hub / AnTS API for the historical run status of a single test target."""
    clean_target = sanitize_bazel_target(test_target)
    history_entries: List[TestHistoryEntry] = []
    summary: Dict[str, Any] = {
        "test_target": clean_target,
        "target": target,
        "branch": branch,
        "mode": mode,
        "days": days,
        "total_runs": 0,
        "passed_runs": 0,
        "failed_runs": 0,
        "flake_rate_pct": 0.0,
    }

    auth_token = get_android_build_token(user_token=token)

    try:
        all_invocations = fetch_ath_invocations(
            target=target,
            branch=branch,
            days=days,
            mode=mode,
            token=token,
        )

        total_invs = len(all_invocations)
        if progress_callback:
            progress_callback(0, total_invs, f"Searching {total_invs} invocations for {clean_target}...")

        def fetch_target_workunits(inv: Dict[str, Any]) -> List[TestHistoryEntry]:
            inv_id = inv.get("invocationId", "")
            build_id = inv.get("primaryBuild", {}).get("buildId", "")
            tgt_platform = inv.get("primaryBuild", {}).get("buildTarget", target)
            creation_ts = int(inv.get("timing", {}).get("creationTimestamp", "0"))
            date_str = (
                datetime.datetime.fromtimestamp(creation_ts / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                if creation_ts else "N/A"
            )
            run_type = "Presubmit" if str(build_id).startswith("P") else "Postsubmit"
            test_uri = ""
            for p in inv.get("properties", []):
                if p.get("name") == "test_uri":
                    test_uri = p.get("value", "")

            all_wus: List[Dict[str, Any]] = []
            page_token = None
            while True:
                wu_url = f"https://androidbuildinternal.googleapis.com/android/internal/build/v3/workunits?invocationId={inv_id}&maxResults=100"
                if page_token:
                    wu_url += f"&pageToken={page_token}"
                try:
                    wu_data = execute_http_get(wu_url, user_token=auth_token)
                    page_units = wu_data.get("workUnits", [])
                    if not page_units:
                        break
                    all_wus.extend(page_units)
                    page_token = wu_data.get("nextPageToken")
                    if not page_token:
                        break
                except Exception:
                    break

            matched_entries: List[TestHistoryEntry] = []
            for w in all_wus:
                if w.get("type") and w.get("type") != "BAZEL_TARGET":
                    continue
                w_name = w.get("name", "")
                if sanitize_bazel_target(w_name) == clean_target:
                    st = str(w.get("state", "")).lower()
                    status_val = str(w.get("status", "")).lower()
                    err = w.get("errorCount", 0)
                    status_str = "FAILED" if (st in ("failed", "error") or status_val in ("failed", "error") or err > 0) else "PASSED"
                    matched_entries.append(
                        TestHistoryEntry(
                            invocation_id=inv_id,
                            build_id=build_id,
                            target=tgt_platform,
                            timestamp=date_str,
                            status=status_str,
                            run_type=run_type,
                            test_uri=test_uri,
                            work_unit_id=w.get("id", ""),
                        )
                    )
            return matched_entries

        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_inv = {executor.submit(fetch_target_workunits, inv): inv for inv in all_invocations}
            for future in as_completed(future_to_inv):
                completed_count += 1
                entries = future.result()
                history_entries.extend(entries)
                if progress_callback:
                    progress_callback(
                        completed_count,
                        total_invs,
                        f"Processed invocation {completed_count}/{total_invs} ({len(history_entries)} runs found)",
                    )

        history_entries.sort(key=lambda e: e.timestamp, reverse=True)

        total_runs = len(history_entries)
        failed_runs = sum(1 for e in history_entries if e.status == "FAILED")
        passed_runs = total_runs - failed_runs
        flake_rate = (failed_runs / total_runs * 100.0) if total_runs > 0 else 0.0

        summary["total_runs"] = total_runs
        summary["passed_runs"] = passed_runs
        summary["failed_runs"] = failed_runs
        summary["flake_rate_pct"] = flake_rate
        summary["displayed_runs"] = min(total_runs, limit) if limit > 0 else total_runs

        if limit > 0:
            history_entries = history_entries[:limit]
    except HTTPError as e:
        sys.stderr.write(f"\n⚠️ HTTP Error {e.code} querying test history: {e.reason}\n")
        if e.code in (401, 403):
            raise RuntimeError(
                f"Authentication/Authorization failed for Android Test Hub API (HTTP {e.code}: {e.reason})."
            ) from e
    except Exception as e:
        sys.stderr.write(f"\nWarning: Error querying test history: {e}\n")

    return summary, history_entries
