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

"""Target handler for 'emu-dev-cli fetch-build check-auth'."""

import argparse
from typing import Any, Optional

from clients.artifact_service import check_authentication_status
from core.command import Command, CommandResult
from core.context import ExecutionContext
from lib.output import print_result


class FetchCheckAuthCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Verifies that Android Build API authentication file exists and works."""

    name = "check-auth"
    help = "Verify that Android Build API authentication file exists and works"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--token",
            type=str,
            default=None,
            help="Explicit OAuth2 access token to test",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_check_auth(args)


def register_parser(subparser, default_host):
    del default_host
    return FetchCheckAuthCommand().register(subparser)


def run_check_auth(args) -> CommandResult[dict[str, Any]]:
    json_mode = getattr(args, "json", False)
    token = getattr(args, "token", None)

    status = check_authentication_status(token=token)
    if status["valid"]:
        data = {
            "status": "success",
            "action": "fetch-build check-auth",
            "summary": "Android Build API authentication verified and working",
            "auth_method": status["auth_method"],
        }
        if status.get("file_path"):
            data["auth_file"] = status["file_path"]
            data["file_exists"] = status["file_exists"]
        return print_result(data, json_mode=json_mode)
    else:
        data = {
            "status": "error",
            "action": "fetch-build check-auth",
            "summary": "Android Build API authentication check failed",
            "auth_method": status["auth_method"],
            "error_message": status["error_message"],
            "procedure": status["procedure"],
            "exit_code": 1,
        }
        if status.get("file_path"):
            data["auth_file"] = status["file_path"]
            data["file_exists"] = status["file_exists"]
        return print_result(data, json_mode=json_mode, is_error=True)
