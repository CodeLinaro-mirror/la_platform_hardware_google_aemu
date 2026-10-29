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

"""Compatibility facade delegating Android Build queries to `clients.artifact_service`."""

import fnmatch
import logging
from typing import Any, Callable, Dict, List, Optional

from clients.artifact_service import (
    ANDROID_BUILD_SCOPE,
    ANDROID_BUILD_TOKEN_ENV_VARS,
    AUTH_PROCEDURE_INSTRUCTIONS,
    BUILD_API_BASE_URL,
    DEFAULT_FETCH_ARTIFACT_BIN as FETCH_ARTIFACT_BIN,
    ArtifactService,
    BuildMetadata,
    ProdArtifactService,
    check_authentication_status as _service_check_authentication_status,
)
from core.errors import EmuCliError
from core.process import ProcessRunner, SubprocessRunner
from lib.oauth import OAuthTokenManager, get_quota_project

logger = logging.getLogger(__name__)


def _metadata_to_dict(meta: BuildMetadata) -> Dict[str, Any]:
    """Converts a `BuildMetadata` instance back into an Android Build v4 dictionary."""
    record = dict(meta.raw)
    record.setdefault("buildId", meta.build_id)
    record.setdefault("target", {"name": meta.target})
    record.setdefault("branch", meta.branch)
    record.setdefault("buildAttemptStatus", meta.status)
    record.setdefault("successful", meta.successful)
    record.setdefault("creationTimestamp", meta.creation_timestamp)
    record.setdefault("completionTimestamp", meta.completion_timestamp)
    record.setdefault("machineName", meta.machine_name)
    return record


def check_authentication_status(
    token: Optional[str] = None,
    *,
    process_runner: Optional[ProcessRunner] = None,
) -> Dict[str, Any]:
    """Verifies Android Build authentication readiness via `clients.artifact_service`."""
    return _service_check_authentication_status(
        token=token,
        process_runner=process_runner,
        fetch_artifact_bin=FETCH_ARTIFACT_BIN,
        token_manager_cls=OAuthTokenManager,
    )


class AndroidBuildClient:
    """Compatibility wrapper around `ProdArtifactService` (`clients.artifact_service`)."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        service: Optional[ArtifactService] = None,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        self.token = token
        self.quota_project = get_quota_project()
        if not self.token and service is None:
            token_manager = OAuthTokenManager(
                scopes=[ANDROID_BUILD_SCOPE],
                env_vars=list(ANDROID_BUILD_TOKEN_ENV_VARS),
            )
            self.token = token_manager.get_token()
        self._runner = process_runner or SubprocessRunner()
        self._service: ArtifactService = service or ProdArtifactService(
            self._runner,
            token=self.token,
            quota_project=self.quota_project,
            use_fetch_artifact_cli=False,
        )
        self._download_service: ArtifactService = service or ProdArtifactService(
            self._runner,
            token=self.token,
            quota_project=self.quota_project,
            use_fetch_artifact_cli=True,
        )

    def list_builds(
        self,
        branch: str,
        target: str,
        limit: int = 10,
        build_type: str = "SUBMITTED",
        include_building: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieves recent build objects for a branch and target."""
        try:
            builds = self._service.list_builds(
                branch,
                target,
                limit=limit,
                build_type=build_type,
                include_building=include_building,
            )
            return [_metadata_to_dict(b) for b in builds]
        except EmuCliError as exc:
            logger.debug("list_builds(%s, %s) failed: %s", branch, target, exc)
            return []

    def get_build(self, build_id: str, target: str) -> Optional[Dict[str, Any]]:
        """Retrieves metadata for a specific build ID and target."""
        try:
            meta = self._service.get_build(str(build_id), target)
            return _metadata_to_dict(meta) if meta is not None else None
        except EmuCliError as exc:
            logger.debug("get_build(%s, %s) failed: %s", build_id, target, exc)
            return None

    def get_latest_build_id(self, branch: str, target: str) -> str:
        """Retrieves the latest submitted build ID for a branch and target."""
        try:
            return self._service.resolve_build_id(branch, target)
        except EmuCliError as exc:
            raise RuntimeError(str(exc)) from exc

    def list_artifacts(self, build_id: str, target: str) -> List[str]:
        """Lists artifact names available for a build attempt."""
        try:
            return [
                art.name
                for art in self._service.list_artifacts(str(build_id), target)
                if art.name
            ]
        except EmuCliError as exc:
            raise RuntimeError(str(exc)) from exc

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
        """Downloads an artifact from Android Build into `dest_dir` via `ArtifactService`."""
        try:
            path = self._download_service.download_artifact(
                str(build_id),
                target,
                artifact,
                dest_dir,
                progress_callback=progress_callback,
            )
            return str(path)
        except EmuCliError as exc:
            raise RuntimeError(str(exc)) from exc
