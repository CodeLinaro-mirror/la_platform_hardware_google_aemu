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

echo "Building @android/emulator-services and deprecated v1/incubating protos with Bazel from ${EMU_ROOT}..."
(cd "${EMU_ROOT}" && bazel build @@aemu+//api/web:emulator_services_node_module @@aemu+//protos/services/emulator-controller:emulator_controller_ts_proto @@aemu+//protos/services/incubating:incubating_ts_proto)

sync_generated_package() {
  local pkg_name="$1"
  local main_entry="$2"
  local dest_dir="${WEB_DIR}/node_modules/${pkg_name}"
  local primary_src_dir="${EMU_ROOT}/bazel-bin/external/aemu+/api/web/node_modules/${pkg_name}"
  local fallback_src_dir="${EMU_ROOT}/bazel-bin/hardware/google/aemu/api/web/node_modules/${pkg_name}"
  local src_dir=""

  if [ -d "${primary_src_dir}" ]; then
    src_dir="${primary_src_dir}"
  elif [ -d "${fallback_src_dir}" ]; then
    src_dir="${fallback_src_dir}"
  else
    echo "Error: Failed to sync generated TypeScript package '${pkg_name}' into '${dest_dir}': Bazel output directory was not found at '${primary_src_dir}' or '${fallback_src_dir}'. Verify EMU_ROOT ('${EMU_ROOT}') points to the workspace root and that 'bazel build' created the bazel-bin symlink (check 'bazel info bazel-bin')." >&2
    exit 1
  fi

  chmod -R u+w "${dest_dir}" 2>/dev/null || true
  rm -rf "${dest_dir}"
  mkdir -p "${dest_dir}"

  echo "Syncing generated TypeScript services for ${pkg_name} into ${dest_dir}..."
  cp -R "${src_dir}/"* "${dest_dir}/"
  chmod -R u+w "${dest_dir}"

  cat << EOF > "${dest_dir}/package.json"
{
  "name": "${pkg_name}",
  "version": "0.1.0",
  "private": true,
  "main": "${main_entry}",
  "types": "${main_entry}"
}
EOF
}

copy_bazel_proto_file() {
  local rel_src="$1"
  local dest_file="$2"
  local primary_src="${EMU_ROOT}/bazel-bin/external/aemu+/${rel_src}"
  local fallback_src="${EMU_ROOT}/bazel-bin/hardware/google/aemu/${rel_src}"
  local resolved_src=""

  if [ -f "${primary_src}" ]; then
    resolved_src="${primary_src}"
  elif [ -f "${fallback_src}" ]; then
    resolved_src="${fallback_src}"
  else
    echo "Error: Failed to copy generated TypeScript proto '${rel_src}' into '${dest_file}': Bazel output file was not found at '${primary_src}' or '${fallback_src}'. Verify EMU_ROOT ('${EMU_ROOT}') points to the workspace root and that 'bazel build' succeeded (check 'bazel info bazel-bin')." >&2
    exit 1
  fi

  mkdir -p "$(dirname "${dest_file}")"
  cp "${resolved_src}" "${dest_file}"
  chmod 0644 "${dest_file}"
}

sync_generated_package "@android/emulator-services" "grpc_endpoint_description.ts"

# Sync deprecated v1 and incubating proto codegen into src/deprecated/v1
V1_DEST_DIR="${WEB_DIR}/src/deprecated/v1"
INCUBATING_DEST_DIR="${V1_DEST_DIR}/incubating"
mkdir -p "${V1_DEST_DIR}/google/protobuf" "${INCUBATING_DEST_DIR}/google/protobuf"

copy_bazel_proto_file "protos/services/emulator-controller/emulator_controller.ts" "${V1_DEST_DIR}/emulator_controller.ts"
cp "${WEB_DIR}/node_modules/@android/emulator-services/google/protobuf/empty.ts" "${V1_DEST_DIR}/google/protobuf/empty.ts"

copy_bazel_proto_file "protos/services/incubating/avd/avd_service.ts" "${INCUBATING_DEST_DIR}/avd_service.ts"
copy_bazel_proto_file "protos/services/incubating/car/car_service.ts" "${INCUBATING_DEST_DIR}/car_service.ts"
copy_bazel_proto_file "protos/services/incubating/car/vehicle_service.ts" "${INCUBATING_DEST_DIR}/vehicle_service.ts"
copy_bazel_proto_file "protos/services/incubating/modem/modem_service.ts" "${INCUBATING_DEST_DIR}/modem_service.ts"
copy_bazel_proto_file "protos/services/incubating/recording/screen_recording_service.ts" "${INCUBATING_DEST_DIR}/screen_recording_service.ts"
copy_bazel_proto_file "protos/services/incubating/sensor/sensor_service.ts" "${INCUBATING_DEST_DIR}/sensor_service.ts"
copy_bazel_proto_file "protos/services/incubating/virtualscene/virtual_scene_service.ts" "${INCUBATING_DEST_DIR}/virtual_scene_service.ts"
copy_bazel_proto_file "protos/services_v2/google/protobuf/empty.ts" "${INCUBATING_DEST_DIR}/google/protobuf/empty.ts"
copy_bazel_proto_file "protos/services_v2/google/protobuf/wrappers.ts" "${INCUBATING_DEST_DIR}/google/protobuf/wrappers.ts"
chmod -R u+w "${V1_DEST_DIR}"

echo "Successfully synced @android/emulator-services and src/deprecated/v1 (including incubating) for local IDE development."

