# Copyright 2025 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bazel macro for running amc tests."""

load("@rules_shell//shell:sh_test.bzl", "sh_test")

def amc_test(name, build_config, shim, srcs = []):
    """A test that runs amc.py bazel on a given meson project.

    Args:
        name: The name of the test.
        build_config: The build-config.jsonc file for the test.
        shim: The shim.jsonc file for the test.
        srcs: A list of source files for the meson project.
    """
    sh_test(
        name = name,
        srcs = ["//tools/toolchain/tests/amc/utils:amc_test_wrapper.sh"],
        args = [
            "$(location //tools/toolchain/tests/amc/utils:amc_test.py)",
            "--amc",
            "$(location //tools/toolchain:amc)",
            "--build-config",
            "$(location %s)" % build_config,
            "--shim",
            "$(location %s)" % shim,
        ],
        data = [
            "//tools/toolchain:amc",
            build_config,
            shim,
            "//tools/toolchain/tests/amc/utils:amc_test.py",
        ] + srcs,
        local = True,
    )
