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
import {
  AmbientEnvironment,
  DeepPartial,
  DevicePose,
  GetSensorsStateRequest,
  ImuTelemetry,
  RawSensorOverrides,
  SensorsService,
  SensorsServiceClientImpl,
  SensorsState,
  StreamSensorsStateRequest,
  TransitionMode,
  UpdateSensorsStateRequest,
  Vector3f,
} from '@android/emulator-services/sensors_service';

export {
  AmbientEnvironment,
  DevicePose,
  ImuTelemetry,
  RawSensorOverrides,
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

  constructor(transport: GrpcWebTransport) {
    this.rpc = new SensorsServiceClientImpl(transport);
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

  /** Callback form of {@link SensorsClient.StreamSensorsState}. */
  public subscribeSensorsState(
    onState: (state: SensorsState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamSensorsStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamSensorsState(request, signal), onState, onError);
  }
}
