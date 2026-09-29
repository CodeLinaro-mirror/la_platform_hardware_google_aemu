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
  DeepPartial,
  GetSystemStateRequest,
  RebootDeviceRequest,
  RebootDeviceResponse,
  StreamSystemStateRequest,
  SystemService,
  SystemServiceClientImpl,
  SystemState,
  SystemState_Connection,
  SystemState_Lifecycle,
  SystemState_Lock,
} from '@android/emulator-services/system_service';

export {
  SystemState,
  SystemState_Lifecycle,
  SystemState_Connection,
  SystemState_Lock,
  RebootDeviceResponse,
};

/**
 * Headless API client for SystemService (OS lifecycle, adb connection,
 * lockscreen and reboot).
 *
 * Methods named in `PascalCase` are the RPCs of `SystemService`, spelled
 * exactly as `system_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class SystemClient {
  private readonly rpc: SystemService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new SystemServiceClientImpl(transport);
  }

  public GetSystemState(request: DeepPartial<GetSystemStateRequest> = {}): Promise<SystemState> {
    return this.rpc.GetSystemState(GetSystemStateRequest.fromPartial(request));
  }

  public StreamSystemState(
    request: DeepPartial<StreamSystemStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<SystemState> {
    return this.rpc.StreamSystemState(StreamSystemStateRequest.fromPartial(request), signal);
  }

  public RebootDevice(
    request: DeepPartial<RebootDeviceRequest> = {}
  ): Promise<RebootDeviceResponse> {
    return this.rpc.RebootDevice(RebootDeviceRequest.fromPartial(request));
  }

  /** Callback form of {@link SystemClient.StreamSystemState}. */
  public subscribeSystemState(
    onState: (state: SystemState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamSystemStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamSystemState(request, signal), onState, onError);
  }
}