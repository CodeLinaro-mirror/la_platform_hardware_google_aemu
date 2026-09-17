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

import { GrpcWebTransport } from "./transport";
import {
  DeviceCapabilities,
  GetDeviceCapabilitiesRequest,
  DeviceMetadata,
  BatteryCapabilities,
  LocationCapabilities,
  DisplayCapabilities,
  DisplayHardware,
  SensorsCapabilities,
  InputCapabilities,
  PostureCapabilities,
  BiometricsCapabilities,
  TelephonyCapabilities,
  CameraCapabilities,
  AudioCapabilities,
  ScreenCaptureCapabilities,
  ClipboardCapabilities,
  VmCapabilities,
  SnapshotCapabilities,
  SystemCapabilities,
  ServiceExtension,
} from "@android/emulator-services/discovery/capabilities_service";
import { AccessMode } from "@android/emulator-services/common/common";

export type {
  DeviceCapabilities,
  GetDeviceCapabilitiesRequest,
  DeviceMetadata,
  BatteryCapabilities,
  LocationCapabilities,
  DisplayCapabilities,
  DisplayHardware,
  SensorsCapabilities,
  InputCapabilities,
  PostureCapabilities,
  BiometricsCapabilities,
  TelephonyCapabilities,
  CameraCapabilities,
  AudioCapabilities,
  ScreenCaptureCapabilities,
  ClipboardCapabilities,
  VmCapabilities,
  SnapshotCapabilities,
  SystemCapabilities,
  ServiceExtension,
};
export { AccessMode };

/**
 * Headless API client for CapabilitiesService.
 * Queries device hardware limits and dynamically discovered service extensions.
 */
export class CapabilitiesClient {
  private readonly transport: GrpcWebTransport;
  private cachedCapabilities: DeviceCapabilities | null = null;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  /**
   * Fetches full device capabilities snapshot from upstream emulator.
   */
  public async getCapabilities(): Promise<DeviceCapabilities> {
    return this.getDeviceCapabilities();
  }

  public async getDeviceCapabilities(forceRefresh = false): Promise<DeviceCapabilities> {
    if (this.cachedCapabilities && !forceRefresh) {
      return this.cachedCapabilities;
    }

    const requestBytes = GetDeviceCapabilitiesRequest.encode({}).finish();
    const responseBytes = await this.transport.request(
      "android.emulation.v2.discovery.CapabilitiesService",
      "GetDeviceCapabilities",
      requestBytes
    );

    const caps = DeviceCapabilities.decode(responseBytes);
    this.cachedCapabilities = caps;
    return caps;
  }

  /**
   * Returns metadata about the emulated hardware and OS.
   */
  public async getDeviceMetadata(): Promise<DeviceMetadata | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.metadata;
  }

  /**
   * Queries display capabilities and configurations.
   */
  public async getDisplayCapabilities(): Promise<DisplayCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.display;
  }

  /**
   * Queries available hardware sensors.
   */
  public async getSensorsCapabilities(): Promise<SensorsCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.sensors;
  }

  /**
   * Queries touch and input device capabilities.
   */
  public async getInputCapabilities(): Promise<InputCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.input;
  }

  /**
   * Queries posture and hinge capabilities.
   */
  public async getPostureCapabilities(): Promise<PostureCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.posture;
  }

  /**
   * Queries location and GPS capabilities.
   */
  public async getLocationCapabilities(): Promise<LocationCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.location;
  }

  /**
   * Queries VM execution lifecycle capabilities.
   */
  public async getVmCapabilities(): Promise<VmCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.vm;
  }

  /**
   * Queries snapshot capabilities.
   */
  public async getSnapshotCapabilities(): Promise<SnapshotCapabilities | undefined> {
    const caps = await this.getDeviceCapabilities();
    return caps.snapshot;
  }
}
