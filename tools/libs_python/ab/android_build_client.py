# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import io
import json
import logging
import platform
import shutil
import subprocess
import urllib.parse
import urllib.request
from getpass import getuser
from pathlib import Path

import googleapiclient.discovery
import googleapiclient.http
from oauth2client.client import AccessTokenCredentials
from tqdm import tqdm


def raise_if_none(x, msg):
    if x is None:
        raise ValueError(msg)


class AndroidBuildClient(object):
    """A client to talk to go/ab via Android Build API v4."""

    def __init__(self, token):
        if not token:
            if platform.system() != "Linux":
                raise ValueError(
                    """You will have to manually generate a token on glinux by running:

~/go/bin/oauth2l fetch --sso $USER@google.com androidbuild.internal

Pass the generated token in using the --token option.
"""
                )
            else:
                token = self.obtain_token()

        logging.debug("Using token: %s", token)
        self.token = token.strip()
        credentials = AccessTokenCredentials(self.token, "aemu-build-client/1.0")
        try:
            self.service = googleapiclient.discovery.build(
                "androidbuildinternal",
                "v4",
                discoveryServiceUrl="https://androidbuild-pa.googleapis.com/$discovery/rest?version=v4",
                credentials=credentials,
            )
        except Exception as e:
            logging.debug("Could not build discovery service: %s", e)
            self.service = None

    def obtain_token(self):
        token_proc = shutil.which("oauth2l")
        if not token_proc:
            token_proc = shutil.which("oauth2l", path=Path.home() / "go" / "bin")
        raise_if_none(
            token_proc,
            "Unable to find oauth2l on the path or in ~/go/bin, please install it. See http://go/oauth2l for details.",
        )
        try:
            return subprocess.check_output(
                [
                    token_proc,
                    "fetch",
                    "--sso",
                    f"{getuser()}@google.com",
                    "androidbuild.internal",
                ],
                timeout=60,
            ).decode("utf-8")
        except subprocess.TimeoutExpired:
            logging.error("Timeout while trying to retrieve token, have you run gcert?")

    def _make_v4_request(self, url):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_latest_build_id(self, branch, build_target):
        raise_if_none(branch, "branch")

        params = urllib.parse.urlencode({
            "branches": branch,
            "targets": build_target,
            "buildType": "SUBMITTED",
            "pageSize": 1,
        })
        url = f"https://androidbuild-pa.googleapis.com/v4/builds?{params}"
        response = self._make_v4_request(url)
        builds = response.get("builds", [])
        if len(builds) >= 1:
            return builds[0]["buildId"]
        else:
            raise RuntimeError(
                "No builds found for %s/%s, response=%s"
                % (branch, build_target, json.dumps(response))
            )

    def list_builds(
        self, branch, target, buildId=None, endBuildId=None, results=None, success=True
    ):
        """Returns a list of build ids that meets the criteria.

        Args:
            branch: the branch
            target: the target
            results: The max number of builds to fetch.
            endBuildId: The latest buildId in the range, or none if we should use results
            success: look for successful or failed builds.
        Returns:
            A list of build ids
        """
        logging.debug(
            f'Listing submitted builds buildType="SUBMITTED", branch={branch}, target={target}, buildId={buildId}, successful={success}, maxResults={results}'
        )
        query_params = {
            "buildType": "SUBMITTED",
            "pageSize": results or 100,
        }
        if branch:
            query_params["branches"] = branch
        if target:
            query_params["targets"] = target

        url = f"https://androidbuild-pa.googleapis.com/v4/builds?{urllib.parse.urlencode(query_params)}"
        result = self._make_v4_request(url)
        logging.debug("Server response: %s", result)
        builds = result.get("builds", [])
        if not builds:
            return None
        return [x["buildId"] for x in builds]

    def list_artifacts(self, bid, build_target):
        raise_if_none(bid, "no bid provided")
        raise_if_none(build_target, "build_target should not be none")

        url = f"https://androidbuild-pa.googleapis.com/v4/builds/{bid}/{build_target}/attempts/latest/artifacts?pageSize=1000"
        response = self._make_v4_request(url)
        artifacts = response.get("artifacts", [])
        if artifacts:
            return [a["name"] for a in artifacts]
        else:
            raise RuntimeError(
                "No artifacts found for %s at %s, response=%s"
                % (build_target, bid, json.dumps(response))
            )

    def fetch_bits(self, dst, bid, build_target, artifact):
        """Downloads the artifact pointed by the bid/build_target/artifact path from go/ab
        into dest.

        Args:
          dst: destination path
          bid: build id
          build_target: build target
          artifact: artifact name

        Returns:
          None
        """

        raise_if_none(dst, "dst")
        raise_if_none(bid, "bid")
        raise_if_none(build_target, "build_target")
        raise_if_none(artifact, "artifact")

        meta_url = f"https://androidbuild-pa.googleapis.com/v4/builds/{bid}/{build_target}/attempts/latest/artifacts/{artifact}"
        response = self._make_v4_request(meta_url)
        meta = response.get("buildArtifactMetadata", response)
        total_size = int(meta.get("size", 0))

        download_url = None
        try:
            url_endpoint = f"https://androidbuild-pa.googleapis.com/v4/builds/{bid}/{build_target}/attempts/latest/artifacts/{artifact}/url"
            url_resp = self._make_v4_request(url_endpoint)
            download_url = url_resp.get("signedUrl")
        except Exception as e:
            logging.debug("Could not retrieve signedUrl from /url: %s", e)

        headers = {}
        if not download_url:
            download_url = f"https://androidbuild-pa.googleapis.com/v4/builds/{bid}/{build_target}/attempts/latest/artifacts/{artifact}?alt=media"
            headers["Authorization"] = f"Bearer {self.token}"

        media_req = urllib.request.Request(download_url, headers=headers)
        with tqdm(
            total=total_size, unit="B", unit_scale=True, unit_divisor=1024, miniters=1
        ) as progress_bar:
            with io.FileIO(dst, mode="wb") as fh:
                with urllib.request.urlopen(media_req) as resp:
                    chunk_size = 1024 * 1024  # 1MB chunks
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        fh.write(chunk)
                        progress_bar.update(len(chunk))

