#!/usr/bin/env bash
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

set -euo pipefail

# Optional version parameter (default: 2.6.1)
TS_PROTO_VERSION="${1:-2.6.1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR="${SCRIPT_DIR}/.tmp_bundle"

mkdir -p "${TEMP_DIR}"
trap "rm -rf \"${TEMP_DIR}\"" EXIT

echo "module.exports = { format: (_file, code, _options) => code };" > "${TEMP_DIR}/dprint_shim.js"

echo "Installing ts-proto@${TS_PROTO_VERSION} and esbuild in temporary workspace..."
(
  cd "${TEMP_DIR}"
  npm init -y --silent > /dev/null 2>&1
  npm install --silent --no-audit --no-fund "ts-proto@${TS_PROTO_VERSION}" esbuild
)

echo "Bundling ts-proto v${TS_PROTO_VERSION} into standalone protoc_gen_ts_proto.js..."
"${TEMP_DIR}/node_modules/esbuild/bin/esbuild" \
  --bundle \
  --platform=node \
  --target=node20 \
  --alias:dprint-node="${TEMP_DIR}/dprint_shim.js" \
  --define:process.env.npm_package_version="\"${TS_PROTO_VERSION}\"" \
  --outfile="${SCRIPT_DIR}/protoc_gen_ts_proto.js" \
  "${TEMP_DIR}/node_modules/ts-proto/build/src/plugin.js"

# Patch readPackageJson so it returns the static version without filesystem access at runtime
node -e '
const fs = require("fs");
const file = process.argv[1];
const version = process.argv[2];
let content = fs.readFileSync(file, "utf8");
content = content.replace(/async function readPackageJson\(\)\s*\{[\s\S]*?\n\s*\}/, `async function readPackageJson() {\n      return { version: "${version}" };\n    }`);
fs.writeFileSync(file, content);
' "${SCRIPT_DIR}/protoc_gen_ts_proto.js" "${TS_PROTO_VERSION}"

echo "Successfully updated ${SCRIPT_DIR}/protoc_gen_ts_proto.js (ts-proto v${TS_PROTO_VERSION})"
