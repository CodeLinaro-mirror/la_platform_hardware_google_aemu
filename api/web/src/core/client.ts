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

import { AuthProvider } from './auth';
import { GrpcWebTransport } from './transport';
import { InputChannel } from './input_channel';
import { RtcController } from './rtc_controller';
import { SensorsClient } from './sensors';
import { CapabilitiesClient } from './capabilities';
import { LocationClient } from './location';
import { VmClient } from './vm';
import { BatteryClient } from './battery';
import { PostureClient } from './posture';
import { ClipboardClient } from './clipboard';
import { SnapshotClient } from './snapshot';
import { DisplayClient } from './display';
import { AudioClient } from './audio';
import { BiometricsClient } from './biometrics';
import { TelephonyClient } from './telephony';
import { ScreenCaptureClient } from './screen_capture';
import { CameraClient } from './camera';
import { SystemClient } from './system';

/**
 * Options to initialize DeviceClient.
 */
export interface DeviceClientOptions {
  /**
   * Base endpoint of the gRPC-Web proxy (e.g. "http://localhost:8554").
   */
  endpoint: string;

  /**
   * Optional static JWT string or dynamic AuthProvider callback.
   */
  authProvider?: AuthProvider;

  /**
   * Optional extra HTTP headers.
   */
  headers?: Record<string, string>;

  /**
   * Optional WebRTC PeerConnection configuration (STUN/TURN servers).
   */
  rtcConfig?: RTCConfiguration;

  /**
   * Optional custom fetch implementation.
   */
  fetch?: typeof fetch;
}

/**
 * Unified Headless Client Root for Android Devices and Emulators.
 */
export class DeviceClient {
  public readonly transport: GrpcWebTransport;
  public readonly input: InputChannel;
  public readonly rtc: RtcController;
  public readonly sensors: SensorsClient;
  public readonly capabilities: CapabilitiesClient;
  public readonly location: LocationClient;
  public readonly vm: VmClient;
  public readonly battery: BatteryClient;
  public readonly posture: PostureClient;
  public readonly clipboard: ClipboardClient;
  public readonly snapshots: SnapshotClient;
  public readonly display: DisplayClient;
  public readonly audio: AudioClient;
  public readonly biometrics: BiometricsClient;
  public readonly telephony: TelephonyClient;
  public readonly screenCapture: ScreenCaptureClient;
  public readonly camera: CameraClient;
  public readonly system: SystemClient;

  constructor(options: DeviceClientOptions) {
    this.transport = new GrpcWebTransport({
      endpoint: options.endpoint,
      authProvider: options.authProvider,
      headers: options.headers,
      fetch: options.fetch,
    });

    this.input = new InputChannel();

    this.rtc = new RtcController({
      transport: this.transport,
      inputChannel: this.input,
      peerConnectionConfig: options.rtcConfig,
    });

    this.sensors = new SensorsClient(this.transport);
    this.capabilities = new CapabilitiesClient(this.transport);
    this.location = new LocationClient(this.transport);
    this.vm = new VmClient(this.transport);
    this.battery = new BatteryClient(this.transport);
    this.posture = new PostureClient(this.transport);
    this.clipboard = new ClipboardClient(this.transport);
    this.snapshots = new SnapshotClient(this.transport);
    this.display = new DisplayClient(this.transport);
    this.audio = new AudioClient(this.transport);
    this.biometrics = new BiometricsClient(this.transport);
    this.telephony = new TelephonyClient(this.transport);
    this.screenCapture = new ScreenCaptureClient(this.transport);
    this.camera = new CameraClient(this.transport);
    this.system = new SystemClient(this.transport);
  }

  public static create(options: DeviceClientOptions): DeviceClient {
    return new DeviceClient(options);
  }

  /**
   * Starts the WebRTC video streaming session.
   */
  public async connect(): Promise<void> {
    return this.rtc.connect();
  }

  /**
   * Disconnects the WebRTC session and detaches input channels.
   */
  public disconnect(): void {
    this.rtc.disconnect();
  }
}
