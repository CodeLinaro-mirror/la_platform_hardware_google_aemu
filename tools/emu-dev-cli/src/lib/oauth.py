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

"""Reusable OAuth2 token management module for emu-dev-cli."""

import getpass
import json
import logging
import os
from pathlib import Path
import ssl
import subprocess
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)


def get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context configured with macOS and Linux standard root CA certs."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for ca_file in ["/etc/ssl/cert.pem", "/etc/pki/tls/cert.pem"]:
            if os.path.exists(ca_file):
                try:
                    ctx.load_verify_locations(cafile=ca_file)
                    break
                except Exception:
                    pass
    return ctx


def get_quota_project() -> str:
    """Resolves quota project from environment variables or Application Default Credentials,
    defaulting to 'emulator-builds' if unspecified.
    """
    for var in ["GOOGLE_CLOUD_QUOTA_PROJECT", "CLOUDSDK_CORE_PROJECT", "GOOGLE_CLOUD_PROJECT"]:
        if os.environ.get(var):
            return os.environ[var].strip()
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.expanduser(
        "~/.config/gcloud/application_default_credentials.json"
    )
    if os.path.exists(adc_path):
        try:
            with open(adc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("quota_project_id"):
                return data["quota_project_id"].strip()
        except Exception:
            pass
    return "emulator-builds"


class OAuthTokenManager:
    """Reusable OAuth2 token manager providing fallback resolution via explicit tokens,
    environment variables, Application Default Credentials (ADC), and automated oauth2l routines.
    """

    DEFAULT_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/buganizer",
        "https://www.googleapis.com/auth/androidbuild.internal",
    ]

    def __init__(
        self,
        scopes: Optional[List[str]] = None,
        env_vars: Optional[List[str]] = None,
    ) -> None:
        """Initializes OAuthTokenManager.

        Args:
            scopes: List of OAuth2 permission scopes required. Defaults to Buganizer & Android Build.
            env_vars: List of environment variable names to check for pre-set tokens.
        """
        self.scopes = scopes or list(self.DEFAULT_SCOPES)
        self.env_vars = env_vars or ["BUGANIZER_TOKEN", "ANDROID_BUILD_TOKEN", "OAUTH2_TOKEN"]
        self._cached_token: Optional[str] = None

    def save_cached_token(self, token: str) -> None:
        """Caches the acquired token in instance memory and environment."""
        cleaned = self.clean_token(token)
        if cleaned:
            self._cached_token = cleaned
            os.environ["OAUTH2_TOKEN"] = cleaned

    @staticmethod
    def clean_token(raw_token: Optional[str]) -> Optional[str]:
        """Strips whitespace and trailing newlines, and removes 'Authorization:' / 'Bearer ' prefixes if present."""
        if not raw_token:
            return None
        cleaned = raw_token.strip()
        if "Bearer " in cleaned:
            cleaned = cleaned.split("Bearer ")[-1].strip()
        elif cleaned.startswith("Authorization:"):
            cleaned = cleaned.replace("Authorization:", "").strip()
        if not cleaned or cleaned.startswith("Error") or " " in cleaned:
            return None
        return cleaned

    def get_token_from_env(self) -> Optional[str]:
        """Checks configured environment variables for a valid OAuth2 token."""
        for var in self.env_vars:
            val = os.environ.get(var)
            if val:
                cleaned = self.clean_token(val)
                if cleaned:
                    return cleaned
        return None

    def fetch_adc_token(self) -> Optional[str]:
        """Attempts to load token from Application Default Credentials (ADC)."""
        # 1. Try google.auth if installed
        try:
            import google.auth
            from google.auth.transport.requests import Request

            credentials, _ = google.auth.default(scopes=self.scopes)
            credentials.refresh(Request())
            if credentials.token:
                cleaned = self.clean_token(credentials.token)
                if cleaned:
                    self.save_cached_token(cleaned)
                    return cleaned
        except Exception as e:
            logger.debug("google.auth ADC resolution note: %s", e)

        # 2. Try direct ADC file refresh via https://oauth2.googleapis.com/token
        adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.expanduser(
            "~/.config/gcloud/application_default_credentials.json"
        )
        if os.path.exists(adc_path):
            try:
                import urllib.parse
                import urllib.request

                with open(adc_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("type") == "authorized_user":
                    client_id = data.get("client_id")
                    client_secret = data.get("client_secret")
                    refresh_token = data.get("refresh_token")
                    if client_id and refresh_token:
                        body = urllib.parse.urlencode({
                            "client_id": client_id,
                            "client_secret": client_secret or "",
                            "refresh_token": refresh_token,
                            "grant_type": "refresh_token",
                        }).encode("utf-8")
                        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
                        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=10.0) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            access_token = res.get("access_token")
                            cleaned = self.clean_token(access_token)
                            if cleaned:
                                self.save_cached_token(cleaned)
                                return cleaned
            except Exception as e:
                logger.debug("Direct ADC file refresh failed: %s", e)

        # 3. Try gcloud auth application-default print-access-token
        try:
            res = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in reversed(res.stdout.strip().splitlines()):
                    cleaned = self.clean_token(line)
                    if cleaned:
                        self.save_cached_token(cleaned)
                        return cleaned
        except Exception as e:
            logger.debug("gcloud print-access-token failed: %s", e)

        return None

    def fetch_oauth2l_token(self) -> Optional[str]:
        """Attempts to fetch or refresh token via local `oauth2l` SSO or SSH remote GLinux retrieval."""
        user = os.environ.get("USER") or getpass.getuser()
        sso_email = f"{user}@google.com"

        cmd_variants = []
        if sys.platform == "linux" and Path("/google/data").exists():
            cmd_variants.append(["oauth2l", "header", "--sso", sso_email] + self.scopes)
            cmd_variants.append(["oauth2l", "fetch", "--sso", sso_email] + self.scopes)
        cmd_variants.append(["oauth2l", "header"] + self.scopes)
        cmd_variants.append(["oauth2l", "fetch"] + self.scopes)

        for cmd in cmd_variants:
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                )
                if res.returncode == 0 and res.stdout.strip():
                    cleaned = self.clean_token(res.stdout)
                    if cleaned:
                        self.save_cached_token(cleaned)
                        return cleaned
            except Exception:
                pass

        # Remote GLinux SSH Fallback (for macOS workstations)
        if sys.platform == "darwin" or not Path("/google/data").exists():
            glinux_hosts = []
            if os.environ.get("GLINUX_HOST"):
                glinux_hosts.append(os.environ.get("GLINUX_HOST"))
            if os.environ.get("REMOTE_GLINUX_HOST"):
                glinux_hosts.append(os.environ.get("REMOTE_GLINUX_HOST"))
            glinux_hosts.append(f"{user}.c.googlers.com")

            ssh_commands = [
                f"export PATH=$PATH:/google/data/ro/teams/oneplatform:~/go/bin:/usr/local/bin; oauth2l header --sso {sso_email} " + " ".join(self.scopes),
                f"export PATH=$PATH:/google/data/ro/teams/oneplatform:~/go/bin:/usr/local/bin; oauth2l fetch --sso {sso_email} " + " ".join(self.scopes),
            ]

            for host in glinux_hosts:
                for subcmd in ssh_commands:
                    try:
                        remote_cmd = [
                            "ssh",
                            "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=4",
                            host,
                            subcmd,
                        ]
                        res = subprocess.run(
                            remote_cmd,
                            capture_output=True,
                            text=True,
                            timeout=8.0,
                        )
                        if res.returncode == 0 and res.stdout.strip():
                            cleaned = self.clean_token(res.stdout)
                            if cleaned:
                                self.save_cached_token(cleaned)
                                return cleaned
                    except Exception:
                        pass

        return None

    def acquire_token(self, user_token: Optional[str] = None) -> Optional[str]:
        """Resolves OAuth2 token from explicit CLI argument, environment variables, ADC, or oauth2l."""
        if user_token:
            cleaned = self.clean_token(user_token)
            if cleaned:
                logger.debug("Acquired OAuth2 token from user argument")
                return cleaned

        if self._cached_token:
            cleaned = self.clean_token(self._cached_token)
            if cleaned:
                logger.debug("Acquired OAuth2 token from in-memory cache")
                return cleaned

        env_token = self.get_token_from_env()
        if env_token:
            logger.debug("Acquired OAuth2 token from environment variable")
            return env_token

        # Application Default Credentials (ADC) resolution
        adc_token = self.fetch_adc_token()
        if adc_token:
            logger.debug("Acquired OAuth2 token via Application Default Credentials (ADC)")
            return adc_token

        token = self.fetch_oauth2l_token()
        if token:
            logger.debug("Acquired OAuth2 token via oauth2l helper")
            self.save_cached_token(token)
        else:
            logger.debug("No OAuth2 token could be acquired")
        return token

    def get_token(
        self, user_token: Optional[str] = None, force_refresh: bool = False
    ) -> Optional[str]:
        """Convenience method to acquire token, optionally forcing cache refresh."""
        if force_refresh:
            self._cached_token = None
            try:
                subprocess.run(["oauth2l", "reset"], capture_output=True, timeout=5.0)
            except Exception:
                pass
        return self.acquire_token(user_token=user_token)
