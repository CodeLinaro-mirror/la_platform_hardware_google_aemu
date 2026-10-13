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
import { subscribe, Unsubscribe } from './streaming';
import { SensorsChannel } from './sensors_channel';
import {
  AmbientEnvironment,
  DeepPartial,
  DevicePose,
  GetSensorsStateRequest,
  ImuTelemetry,
  RawSensorOverrides,
  SensorType,
  SensorsService,
  SensorsServiceClientImpl,
  SensorsState,
  StreamSensorsStateRequest,
  TransitionMode,
  UpdateSensorsStateRequest,
  Vector3f,
} from '@android/emulator-services/sensors_service';

/** Alias for TransitionMode.TRANSITION_MODE_STEP (instantaneous state jump without interpolation). */
export const TRANSITION_MODE_IMMEDIATE = TransitionMode.TRANSITION_MODE_STEP;

export {
  AmbientEnvironment,
  DevicePose,
  ImuTelemetry,
  RawSensorOverrides,
  SensorType,
  SensorsState,
  TransitionMode,
  UpdateSensorsStateRequest,
  Vector3f,
};

/**
 * The sensors a caller is allowed to drive.
 */
export type SensorsStateUpdate = DeepPartial<UpdateSensorsStateRequest>;

/**
 * Headless API client for SensorsService.
 *
 * Methods named in `PascalCase` are the RPCs of `SensorsService`, spelled
 * exactly as `sensors_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class SensorsClient {
  private readonly rpc: SensorsService;
  private readonly channel?: SensorsChannel;
  private orientationListener: ((event: DeviceOrientationEvent) => void) | null = null;

  constructor(transport: GrpcWebTransport, channel?: SensorsChannel) {
    this.rpc = new SensorsServiceClientImpl(transport);
    this.channel = channel;
  }

  public get isBrowserStreaming(): boolean {
    return this.orientationListener !== null;
  }

  public GetSensorsState(
    request: DeepPartial<GetSensorsStateRequest> = {}
  ): Promise<SensorsState> {
    return this.rpc.GetSensorsState(GetSensorsStateRequest.fromPartial(request));
  }

  public StreamSensorsState(
    request: DeepPartial<StreamSensorsStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<SensorsState> {
    return this.rpc.StreamSensorsState(StreamSensorsStateRequest.fromPartial(request), signal);
  }

  /**
   * Applies a partial update. Only the sensors present in `update` are driven;
   * the rest keep whatever the emulator is already reporting.
   */
  public UpdateSensorsState(update: SensorsStateUpdate): Promise<SensorsState> {
    return this.rpc.UpdateSensorsState(UpdateSensorsStateRequest.fromPartial(update));
  }

  /**
   * Sends a high-frequency partial sensor update over the WebRTC "sensors_v2"
   * SCTP DataChannel when open.
   *
   * Drops the update when the DataChannel is unavailable or congested rather
   * than falling back to un-throttled unary gRPC calls, preventing backend
   * congestion collapse and denial of service.
   *
   * @returns true if the update was sent immediately over the open DataChannel,
   * false if dropped or queued.
   */
  public sendSensorUpdate(update: SensorsStateUpdate): boolean {
    if (!this.channel) {
      return false;
    }
    return this.channel.sendUpdate(update);
  }

  /**
   * Starts streaming the client browser's physical DeviceOrientationEvent
   * (alpha/beta/gamma) to the emulator's 3D DevicePose over the WebRTC
   * "sensors_v2" SCTP DataChannel.
   *
   * Uses TransitionMode.TRANSITION_MODE_STEP (TRANSITION_MODE_IMMEDIATE) for continuous 60Hz streaming
   * to avoid overlapping animation conflicts and lag in the guest physics engine.
   */
  public startBrowserSensorStream(): void {
    if (this.orientationListener || typeof window === 'undefined') {
      return;
    }
    this.orientationListener = (ev: DeviceOrientationEvent) => {
      const pitch = ev.beta ?? 0;
      const yaw = ev.alpha ?? 0;
      const roll = ev.gamma ?? 0;
      this.sendSensorUpdate({
        pose: {
          rotationDegrees: { x: pitch, y: yaw, z: roll },
        },
        transition: TRANSITION_MODE_IMMEDIATE,
      });
    };
    window.addEventListener('deviceorientation', this.orientationListener);
  }

  /**
   * Stops streaming the client browser's physical DeviceOrientationEvent.
   */
  public stopBrowserSensorStream(): void {
    if (this.orientationListener && typeof window !== 'undefined') {
      window.removeEventListener('deviceorientation', this.orientationListener);
    }
    this.orientationListener = null;
  }

  /** Callback form of {@link SensorsClient.StreamSensorsState}. */
  public subscribeSensorsState(
    onState: (state: SensorsState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamSensorsStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamSensorsState(request, signal), onState, onError);
  }
}
