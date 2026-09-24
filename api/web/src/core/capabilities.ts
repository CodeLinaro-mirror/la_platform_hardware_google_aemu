// Copyright (C) 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { GrpcWebTransport } from './transport';
import {
  AudioCapabilities,
  BatteryCapabilities,
  BiometricsCapabilities,
  CameraCapabilities,
  CameraInjectionMode,
  CapabilitiesService,
  CapabilitiesServiceClientImpl,
  ClipboardCapabilities,
  DeepPartial,
  DeviceCapabilities,
  DeviceMetadata,
  DisplayCapabilities,
  DisplayHardware,
  GetDeviceCapabilitiesRequest,
  InputCapabilities,
  InputFeature,
  LocationCapabilities,
  PostureCapabilities,
  RtcCapabilities,
  ScreenCaptureCapabilities,
  SensorsCapabilities,
  SnapshotCapabilities,
  SnapshotFeature,
  SystemCapabilities,
  TelephonyCapabilities,
  TelephonyFeature,
  VmCapabilities,
  VmOperation,
} from '@android/emulator-services/capabilities_service';
import { AccessMode, DeviceKind, Fidelity } from '@android/emulator-services/common';

export type {
  AudioCapabilities,
  BatteryCapabilities,
  BiometricsCapabilities,
  CameraCapabilities,
  ClipboardCapabilities,
  DeviceCapabilities,
  DeviceMetadata,
  DisplayCapabilities,
  DisplayHardware,
  GetDeviceCapabilitiesRequest,
  InputCapabilities,
  LocationCapabilities,
  PostureCapabilities,
  RtcCapabilities,
  ScreenCaptureCapabilities,
  SensorsCapabilities,
  SnapshotCapabilities,
  SystemCapabilities,
  TelephonyCapabilities,
  VmCapabilities,
};
export {
  AccessMode,
  CameraInjectionMode,
  DeviceKind,
  Fidelity,
  InputFeature,
  SnapshotFeature,
  TelephonyFeature,
  VmOperation,
};

/**
 * Headless API client for CapabilitiesService.
 *
 * Queries device hardware limits and subsystem capabilities. Methods named
 * in `PascalCase` are the RPCs of
 * `CapabilitiesService`, spelled exactly as `capabilities_service.proto`
 * declares them; `camelCase` methods are conveniences this package adds and
 * have no RPC of their own.
 *
 * There are no per-subsystem accessors here. {@link DeviceCapabilities} is a
 * single message and reaching into it (`caps.display`, `caps.sensors`) is both
 * shorter and type-checked against the proto, whereas a wrapper per submessage
 * is a list that has to be kept in step with it by hand.
 */
export class CapabilitiesClient {
  private readonly rpc: CapabilitiesService;
  private cached: DeviceCapabilities | null = null;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new CapabilitiesServiceClientImpl(transport);
  }

  /** Always queries the device. */
  public GetDeviceCapabilities(
    request: DeepPartial<GetDeviceCapabilitiesRequest> = {}
  ): Promise<DeviceCapabilities> {
    return this.rpc.GetDeviceCapabilities(GetDeviceCapabilitiesRequest.fromPartial(request));
  }

  /**
   * Serves the snapshot from memory after the first call.
   *
   * Separate from {@link CapabilitiesClient.GetDeviceCapabilities} rather than
   * folded into it so that a caller can tell from the call site whether it is
   * about to touch the wire. The snapshot is fixed for the lifetime of a
   * connection, so the two agree; a caller that suspects otherwise should use
   * the RPC.
   */
  public async getCachedDeviceCapabilities(): Promise<DeviceCapabilities> {
    this.cached ??= await this.GetDeviceCapabilities();
    return this.cached;
  }

  /** Drops the memoized snapshot, so the next cached read queries the device. */
  public invalidateCache(): void {
    this.cached = null;
  }
}
