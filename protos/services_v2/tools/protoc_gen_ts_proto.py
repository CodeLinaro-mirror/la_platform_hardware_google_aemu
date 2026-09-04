#!/usr/bin/env python3
# Copyright (C) 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-platform launcher for protoc-gen-ts_proto."""

import os
from pathlib import Path
import subprocess
import sys


def find_node() -> Path:
    """Resolves the hermetic Node.js binary passed by Bazel via NODE_BINARY."""
    if "NODE_BINARY" not in os.environ:
        sys.stderr.write(
            "Error: NODE_BINARY environment variable is not set. "
            "protoc_gen_ts_proto must be invoked within a Bazel action providing the hermetic Node toolchain.\n"
        )
        sys.exit(1)

    node = Path(os.environ["NODE_BINARY"])
    if node.exists():
        return node.resolve()

    cwd_node = Path.cwd() / node
    if cwd_node.exists():
        return cwd_node.resolve()

    sys.stderr.write(f"Error: Node binary specified by NODE_BINARY='{node}' was not found in the sandbox.\n")
    sys.exit(1)


def find_bundle() -> Path:
    """Locates the bundled protoc_gen_ts_proto.js file adjacent to this launcher."""
    bundle = Path(__file__).resolve().parent / "protoc_gen_ts_proto.js"
    if bundle.is_file():
        return bundle

    sys.stderr.write(f"Error: protoc_gen_ts_proto.js bundle was not found at '{bundle}'.\n")
    sys.exit(1)


def main() -> None:
    node_bin = find_node()
    bundle_js = find_bundle()

    cmd = [str(node_bin), str(bundle_js)] + sys.argv[1:]
    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error executing {node_bin}: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
