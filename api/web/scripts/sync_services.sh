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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Resolve EMU_ROOT by walking up until .repo or WORKSPACE is found
CURRENT_DIR="${WEB_DIR}"
EMU_ROOT=""
while [ "${CURRENT_DIR}" != "/" ]; do
  if [ -d "${CURRENT_DIR}/.repo" ] || [ -f "${CURRENT_DIR}/WORKSPACE" ]; then
    EMU_ROOT="${CURRENT_DIR}"
    break
  fi
  CURRENT_DIR="$(dirname "${CURRENT_DIR}")"
done

if [ -z "${EMU_ROOT}" ]; then
  EMU_ROOT="$(cd "${WEB_DIR}/../../.." && pwd)"
fi

echo "Building @android/emulator-services with Bazel from ${EMU_ROOT}..."
(cd "${EMU_ROOT}" && bazel build @@aemu+//api/web:emulator_services_node_module)

DEST_DIR="${WEB_DIR}/node_modules/@android/emulator-services"
chmod -R u+w "${DEST_DIR}" 2>/dev/null || true
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"

SRC_DIR="${EMU_ROOT}/bazel-bin/external/aemu+/api/web/node_modules/@android/emulator-services"
if [ ! -d "${SRC_DIR}" ]; then
  SRC_DIR="${EMU_ROOT}/bazel-bin/hardware/google/aemu/api/web/node_modules/@android/emulator-services"
fi

if [ ! -d "${SRC_DIR}" ]; then
  echo "Error: Generated services directory not found at ${SRC_DIR}" >&2
  exit 1
fi

echo "Syncing generated TypeScript services into ${DEST_DIR}..."
cp -R "${SRC_DIR}/"* "${DEST_DIR}/"
chmod -R u+w "${DEST_DIR}"

# Write module descriptor for node module resolution
cat << 'EOF' > "${DEST_DIR}/package.json"
{
  "name": "@android/emulator-services",
  "version": "0.1.0",
  "private": true,
  "main": "grpc_endpoint_description.ts",
  "types": "grpc_endpoint_description.ts"
}
EOF

echo "Successfully synced @android/emulator-services for local IDE development."
