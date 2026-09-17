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
  BatteryState,
  BatteryState_State,
  BatteryState_Health,
  GetBatteryStateRequest,
  StreamBatteryStateRequest,
  UpdateBatteryStateRequest,
} from '@android/emulator-services/environment/battery_service';
import { ChargerSource } from '@android/emulator-services/common/common';

export { BatteryState, BatteryState_State, BatteryState_Health, ChargerSource };

export interface BatteryStateUpdate {
  levelPercent?: number;
  state?: BatteryState_State;
  charger?: ChargerSource;
  health?: BatteryState_Health;
}

/**
 * Headless API client for BatteryService.
 */
export class BatteryClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getBatteryState(): Promise<BatteryState> {
    const reqBytes = GetBatteryStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.BatteryService',
      'GetBatteryState',
      reqBytes
    );
    return BatteryState.decode(resBytes);
  }

  public streamBatteryState(
    onState: (state: BatteryState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamBatteryStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.environment.BatteryService',
      'StreamBatteryState',
      reqBytes,
      (bytes) => onState(BatteryState.decode(bytes)),
      onError
    );
  }

  public streamBatteryStateIterator(): AsyncIterable<BatteryState> {
    const reqBytes = StreamBatteryStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.environment.BatteryService',
      'StreamBatteryState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield BatteryState.decode(chunkBytes);
        }
      },
    };
  }

  public async updateBatteryState(update: BatteryStateUpdate): Promise<BatteryState> {
    const paths: string[] = [];
    const state: BatteryState = {
      levelPercent: update.levelPercent ?? 100,
      state: update.state ?? BatteryState_State.DISCHARGING,
      charger: update.charger ?? ChargerSource.CHARGER_SOURCE_UNSPECIFIED,
      health: update.health ?? BatteryState_Health.GOOD,
    };

    if (update.levelPercent !== undefined) {
      paths.push('level_percent');
    }
    if (update.state !== undefined) {
      paths.push('state');
    }
    if (update.charger !== undefined) {
      paths.push('charger');
    }
    if (update.health !== undefined) {
      paths.push('health');
    }

    const req: UpdateBatteryStateRequest = {
      batteryState: state,
      updateMask: paths,
    };

    const reqBytes = UpdateBatteryStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.BatteryService',
      'UpdateBatteryState',
      reqBytes
    );
    return BatteryState.decode(resBytes);
  }

  public async setBatteryLevel(percent: number): Promise<BatteryState> {
    return this.updateBatteryState({ levelPercent: percent });
  }

  public async setChargingState(
    state: BatteryState_State,
    charger: ChargerSource = ChargerSource.CHARGER_SOURCE_AC
  ): Promise<BatteryState> {
    return this.updateBatteryState({ state, charger });
  }
}