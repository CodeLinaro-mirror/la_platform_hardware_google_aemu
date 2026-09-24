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

"""Android Build API v4 client for fetching artifacts directly from go/ab."""

import fnmatch
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request
from urllib.error import HTTPError

from lib.oauth import OAuthTokenManager, get_quota_project, get_ssl_context

logger = logging.getLogger(__name__)

BUILD_API_BASE_URL = "https://androidbuild-pa.googleapis.com/v4"
FETCH_ARTIFACT_BIN = "/google/data/ro/projects/android/fetch_artifact"

AUTH_PROCEDURE_INSTRUCTIONS = """1. Generate Application Default Credentials (ADC) with the internal Android Build scope:
   gcloud auth application-default login \\
     --scopes=https://www.googleapis.com/auth/androidbuild.internal,https://www.googleapis.com/auth/cloud-platform

2. (Optional) Set your quota project:
   gcloud auth application-default set-quota-project emulator-builds

Alternatively, provide an explicit access token via:
   export ANDROID_BUILD_TOKEN="<token>"
or pass the '--token "<token>"' flag."""


def check_authentication_status(token: Optional[str] = None) -> Dict[str, Any]:
    """Verifies that the authentication needed for Android Build API exists and works.

    Returns a dict with:
      - valid: bool
      - auth_method: str ('EXPLICIT_TOKEN', 'ENV_VAR', 'ADC', 'FETCH_ARTIFACT_CLI')
      - file_path: Optional[str]
      - file_exists: bool
      - error_message: Optional[str]
      - procedure: Optional[str]
    """
    fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")

    # If running on Linux CFS environment where fetch_artifact is installed
    if fetch_tool and os.path.exists("/google/data"):
        if shutil.which("gcertstatus"):
            try:
                res = subprocess.run(["gcertstatus"], capture_output=True, text=True, timeout=5.0)
                if res.returncode != 0:
                    return {
                        "valid": False,
                        "auth_method": "FETCH_ARTIFACT_CLI",
                        "file_path": fetch_tool,
                        "file_exists": True,
                        "error_message": "Corporate LOAS / gcert certificate is missing or expired.",
                        "procedure": "Run 'gcert' in your terminal to renew your corporate authentication certificate.",
                    }
            except Exception:
                pass
        return {
            "valid": True,
            "auth_method": "FETCH_ARTIFACT_CLI",
            "file_path": fetch_tool,
            "file_exists": True,
            "error_message": None,
            "procedure": None,
        }

    # Direct v4 REST API path (e.g. macOS / non-CFS)
    auth_method = "ADC"
    file_path = None
    file_exists = False

    if token:
        auth_method = "EXPLICIT_TOKEN"
    elif os.environ.get("ANDROID_BUILD_TOKEN") or os.environ.get("OAUTH2_TOKEN"):
        auth_method = "ENV_VAR"
    else:
        file_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.expanduser(
            "~/.config/gcloud/application_default_credentials.json"
        )
        file_exists = os.path.exists(file_path)
        if not file_exists:
            return {
                "valid": False,
                "auth_method": auth_method,
                "file_path": file_path,
                "file_exists": False,
                "error_message": f"Application Default Credentials (ADC) file was not found at '{file_path}'.",
                "procedure": AUTH_PROCEDURE_INSTRUCTIONS,
            }

    # Attempt to acquire/refresh token
    token_manager = OAuthTokenManager(
        scopes=["https://www.googleapis.com/auth/androidbuild.internal"],
        env_vars=["ANDROID_BUILD_TOKEN", "BUGANIZER_TOKEN", "OAUTH2_TOKEN"],
    )
    acquired_token = token_manager.get_token(user_token=token)
    if not acquired_token:
        return {
            "valid": False,
            "auth_method": auth_method,
            "file_path": file_path,
            "file_exists": file_exists,
            "error_message": f"Could not acquire or refresh OAuth2 token from {auth_method}.",
            "procedure": AUTH_PROCEDURE_INSTRUCTIONS,
        }

    # Probe Android Build v4 endpoint to verify the credentials actually work
    quota_project = get_quota_project()
    probe_url = (
        f"{BUILD_API_BASE_URL}/builds?branches=git_emu-main-next"
        f"&targets=emulator_mac_aarch64&buildType=SUBMITTED&pageSize=1"
    )
    headers = {
        "Authorization": f"Bearer {acquired_token}",
        "Accept": "application/json",
    }
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project

    req = urllib.request.Request(probe_url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=10.0) as resp:
            if resp.status == 200:
                return {
                    "valid": True,
                    "auth_method": auth_method,
                    "file_path": file_path,
                    "file_exists": file_exists,
                    "error_message": None,
                    "procedure": None,
                }
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in error_body or "insufficient authentication scopes" in error_body:
            err_msg = (
                f"Authentication file '{file_path}' exists, but lacks the required scope "
                "'https://www.googleapis.com/auth/androidbuild.internal'."
            )
        elif "quota project" in error_body.lower():
            err_msg = "The Android Build API requires a quota project, which is not set or invalid."
        else:
            err_msg = f"Android Build API authentication check failed (HTTP {e.code}: {e.reason})."

        return {
            "valid": False,
            "auth_method": auth_method,
            "file_path": file_path,
            "file_exists": file_exists,
            "error_message": err_msg,
            "procedure": AUTH_PROCEDURE_INSTRUCTIONS,
        }
    except Exception as e:
        return {
            "valid": False,
            "auth_method": auth_method,
            "file_path": file_path,
            "file_exists": file_exists,
            "error_message": f"Preflight connection check to Android Build API failed: {e}",
            "procedure": AUTH_PROCEDURE_INSTRUCTIONS,
        }

    return {
        "valid": True,
        "auth_method": auth_method,
        "file_path": file_path,
        "file_exists": file_exists,
        "error_message": None,
        "procedure": None,
    }


class AndroidBuildClient:
    """Client for querying and downloading artifacts from Android Build v4 REST API."""

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.quota_project = get_quota_project()
        if not self.token:
            token_manager = OAuthTokenManager(
                scopes=["https://www.googleapis.com/auth/androidbuild.internal"],
                env_vars=["ANDROID_BUILD_TOKEN", "BUGANIZER_TOKEN", "OAUTH2_TOKEN"],
            )
            self.token = token_manager.get_token()

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.quota_project:
            headers["X-Goog-User-Project"] = self.quota_project
        return headers

    def _make_request(self, url: str, _retried: bool = False) -> Dict[str, Any]:
        req = urllib.request.Request(url, headers=self._get_headers())
        try:
            with urllib.request.urlopen(req, context=get_ssl_context(), timeout=60) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except HTTPError as e:
            if e.code in (401, 403) and not _retried:
                token_manager = OAuthTokenManager(
                    scopes=["https://www.googleapis.com/auth/androidbuild.internal"],
                    env_vars=["ANDROID_BUILD_TOKEN", "BUGANIZER_TOKEN", "OAUTH2_TOKEN"],
                )
                fallback_token = token_manager.fetch_oauth2l_token()
                if fallback_token and fallback_token != self.token:
                    self.token = fallback_token
                    return self._make_request(url, _retried=True)
            if e.code in (401, 403):
                msg = (
                    f"Android Build API authentication error (HTTP {e.code}: {e.reason}).\n"
                    "On macOS workstations, access to Android Build v4 API requires Application Default Credentials (ADC)\n"
                    "with the internal scope. Generate credentials by running:\n\n"
                    "  gcloud auth application-default login \\\n"
                    "    --scopes=https://www.googleapis.com/auth/androidbuild.internal,https://www.googleapis.com/auth/cloud-platform\n\n"
                    "Or supply an explicit token via:\n"
                    "  export ANDROID_BUILD_TOKEN=\"<token>\"\n"
                    "or by passing:\n"
                    "  --token \"<token>\""
                )
                logger.error(msg)
                raise RuntimeError(msg) from e
            raise RuntimeError(f"HTTP Error {e.code} for URL {url}: {e.reason}") from e
        except Exception as e:
            logger.error("Failed request to %s: %s", url, e)
            raise

    def list_builds(
        self,
        branch: str,
        target: str,
        limit: int = 10,
        build_type: str = "SUBMITTED",
        include_building: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieves recent build objects for a branch and target."""
        branches_to_try = [branch]
        if not branch.startswith("git_"):
            branches_to_try.append(f"git_{branch}")

        page_size = max(limit * 2, 15)
        for b in branches_to_try:
            params: Dict[str, Any] = {
                "branches": b,
                "targets": target,
                "buildType": build_type,
                "pageSize": page_size,
            }
            url = f"{BUILD_API_BASE_URL}/builds?{urllib.parse.urlencode(params)}"
            try:
                response = self._make_request(url)
                builds = response.get("builds", [])
                if not builds:
                    continue
                filtered: List[Dict[str, Any]] = []
                for build in builds:
                    status = build.get("buildAttemptStatus", "")
                    if not include_building and (
                        status not in ("COMPLETE", "SUCCESSFUL")
                        or not build.get("completionTimestamp")
                    ):
                        continue
                    filtered.append(build)
                    if len(filtered) >= limit:
                        break
                if filtered:
                    return filtered
            except Exception as e:
                logger.debug("Querying branch '%s' note: %s", b, e)

        return []

    def get_build(self, build_id: str, target: str) -> Optional[Dict[str, Any]]:
        """Retrieves metadata for a specific build ID and target."""
        params = urllib.parse.urlencode({
            "buildId": build_id,
            "targets": target,
            "pageSize": 5,
        })
        url = f"{BUILD_API_BASE_URL}/builds?{params}"
        try:
            response = self._make_request(url)
            builds = response.get("builds", [])
            for b in builds:
                if str(b.get("buildId", "")) == str(build_id):
                    return b
            if builds:
                return builds[0]
        except Exception as e:
            logger.debug("get_build(%s, %s) failed: %s", build_id, target, e)
        return None

    def get_latest_build_id(self, branch: str, target: str) -> str:
        """Retrieves the latest submitted build ID for a branch and target."""
        branches_to_try = [branch]
        if not branch.startswith("git_"):
            branches_to_try.append(f"git_{branch}")

        for b in branches_to_try:
            params = urllib.parse.urlencode({
                "branches": b,
                "targets": target,
                "buildType": "SUBMITTED",
                "successful": "true",
                "pageSize": 5,
            })
            url = f"{BUILD_API_BASE_URL}/builds?{params}"
            try:
                response = self._make_request(url)
                builds = response.get("builds", [])
                for build in builds:
                    bid = str(build.get("buildId", ""))
                    status = build.get("buildAttemptStatus", "")
                    if bid and (status in ("COMPLETE", "SUCCESSFUL") or not status):
                        return bid
                if builds and builds[0].get("buildId"):
                    return str(builds[0]["buildId"])
            except Exception as e:
                logger.debug("Querying branch '%s' note: %s", b, e)

        raise RuntimeError(
            f"No submitted builds found for branch '{branch}' and target '{target}'."
        )

    def list_artifacts(self, build_id: str, target: str) -> List[str]:
        """Lists artifact names available for a build attempt."""
        url = f"{BUILD_API_BASE_URL}/builds/{build_id}/{target}/attempts/latest/artifacts?pageSize=1000"
        response = self._make_request(url)
        artifacts = response.get("artifacts", [])
        return [a["name"] for a in artifacts if "name" in a]

    def find_matching_artifact(
        self, build_id: str, target: str, patterns: List[str]
    ) -> Optional[str]:
        """Finds the first artifact matching one of the given glob patterns."""
        all_artifacts = self.list_artifacts(build_id, target)
        for pattern in patterns:
            for art in all_artifacts:
                if fnmatch.fnmatch(art, pattern):
                    return art
        return None

    def download_artifact(
        self,
        build_id: str,
        target: str,
        artifact: str,
        dest_dir: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """Downloads an artifact from Android Build API into dest_dir.

        Returns the absolute path to the downloaded file.
        """
        os.makedirs(dest_dir, exist_ok=True)
        dest_file = os.path.join(dest_dir, artifact)
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)

        # 0. Use fetch_artifact CLI if available on GLinux (fastest & handles LOAS)
        fetch_tool = FETCH_ARTIFACT_BIN if os.path.exists(FETCH_ARTIFACT_BIN) else shutil.which("fetch_artifact")
        if fetch_tool and os.path.exists("/google/data"):
            try:
                cmd = [
                    fetch_tool,
                    "--bid",
                    str(build_id),
                    "--target",
                    str(target),
                    artifact,
                    dest_file,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if res.returncode == 0 and os.path.exists(dest_file) and os.path.getsize(dest_file) > 0:
                    return dest_file
            except Exception as e:
                logger.debug("fetch_artifact fallback to REST for %s: %s", artifact, e)

        encoded_artifact = urllib.parse.quote(artifact, safe="")
        # 1. Try to obtain a signed URL
        download_url = None
        try:
            url_endpoint = f"{BUILD_API_BASE_URL}/builds/{build_id}/{target}/attempts/latest/artifacts/{encoded_artifact}/url"
            url_resp = self._make_request(url_endpoint)
            download_url = url_resp.get("signedUrl")
        except Exception as e:
            logger.debug("Signed URL not returned for %s: %s", artifact, e)

        headers = {}
        if not download_url:
            download_url = f"{BUILD_API_BASE_URL}/builds/{build_id}/{target}/attempts/latest/artifacts/{encoded_artifact}?alt=media"
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            if self.quota_project:
                headers["X-Goog-User-Project"] = self.quota_project

        req = urllib.request.Request(download_url, headers=headers)
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=300) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            with open(dest_file, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)

        return dest_file
