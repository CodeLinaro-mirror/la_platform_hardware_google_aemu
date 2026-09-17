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
  SystemState,
  SystemState_Lifecycle,
  SystemState_Connection,
  SystemState_Lock,
  GetSystemStateRequest,
  StreamSystemStateRequest,
  RebootDeviceRequest,
} from '@android/emulator-services/system/system_service';

export {
  SystemState,
  SystemState_Lifecycle,
  SystemState_Connection,
  SystemState_Lock,
};

/**
 * Headless API client for SystemService (OS lifecycle, adb connection, lockscreen & reboot).
 */
export class SystemClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getSystemState(): Promise<SystemState> {
    const reqBytes = GetSystemStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.system.SystemService',
      'GetSystemState',
      reqBytes
    );
    return SystemState.decode(resBytes);
  }

  public streamSystemState(): AsyncIterable<SystemState> {
    const reqBytes = StreamSystemStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.system.SystemService',
      'StreamSystemState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield SystemState.decode(chunkBytes);
        }
      },
    };
  }

  public subscribeSystemState(
    onState: (state: SystemState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamSystemStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.system.SystemService',
      'StreamSystemState',
      reqBytes,
      (bytes) => onState(SystemState.decode(bytes)),
      onError
    );
  }

  public async rebootDevice(): Promise<Uint8Array> {
    const reqBytes = RebootDeviceRequest.encode({}).finish();
    return this.transport.request(
      'android.emulation.v2.system.SystemService',
      'RebootDevice',
      reqBytes
    );
  }
}