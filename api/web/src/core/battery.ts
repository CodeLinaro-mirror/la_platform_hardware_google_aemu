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
  BatteryService,
  BatteryServiceClientImpl,
  BatteryState,
  BatteryState_Health,
  BatteryState_State,
  ChargerSource,
  DeepPartial,
  GetBatteryStateRequest,
  StreamBatteryStateRequest,
  UpdateBatteryStateRequest,
} from '@android/emulator-services/battery_service';

export {
  BatteryState,
  BatteryState_State,
  BatteryState_Health,
  ChargerSource,
  UpdateBatteryStateRequest,
};

/**
 * The fields of {@link BatteryState} a caller is allowed to change.
 */
export type BatteryStateUpdate = DeepPartial<UpdateBatteryStateRequest>;

/**
 * Headless API client for BatteryService.
 *
 * Methods named in `PascalCase` are the RPCs of `BatteryService`, spelled
 * exactly as `battery_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class BatteryClient {
  private readonly rpc: BatteryService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new BatteryServiceClientImpl(transport);
  }

  public GetBatteryState(
    request: DeepPartial<GetBatteryStateRequest> = {}
  ): Promise<BatteryState> {
    return this.rpc.GetBatteryState(GetBatteryStateRequest.fromPartial(request));
  }

  public StreamBatteryState(
    request: DeepPartial<StreamBatteryStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<BatteryState> {
    return this.rpc.StreamBatteryState(StreamBatteryStateRequest.fromPartial(request), signal);
  }

  /**
   * Applies a partial update. Only the fields present in `update` are changed;
   * the rest are left alone by the server.
   */
  public UpdateBatteryState(update: BatteryStateUpdate): Promise<BatteryState> {
    return this.rpc.UpdateBatteryState(UpdateBatteryStateRequest.fromPartial(update));
  }

  /** Callback form of {@link BatteryClient.StreamBatteryState}. */
  public subscribeBatteryState(
    onState: (state: BatteryState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamBatteryStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamBatteryState(request, signal), onState, onError);
  }
}