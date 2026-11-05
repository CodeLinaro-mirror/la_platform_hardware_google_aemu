#!/usr/bin/env python3
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

"""Delegation shim to `workflows.common.git_commit.verifier_cli` (`--mode=get-base-commit`)."""

from workflows.common.git_commit.verifier_cli import (
    get_base_commit,
    run_cli_entrypoint,
)

__all__ = ["get_base_commit"]

if __name__ == "__main__":
    run_cli_entrypoint(["--mode=get-base-commit"])
