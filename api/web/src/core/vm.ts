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

import { BinaryWriter } from '@bufbuild/protobuf/wire';
import { GrpcWebTransport } from './transport';
import {
  VmState,
  VmState_State,
  StreamVmStateRequest,
} from '@android/emulator-services/vm/vm_service';

export { VmState, VmState_State };

export enum VmStateEnum {
  UNSPECIFIED = 0,
  RUNNING = 1,
  PAUSED = 2,
  STARTING = 3,
  STOPPING = 4,
  STOPPED = 5,
  SAVING_SNAPSHOT = 6,
  RESTORING_SNAPSHOT = 7,
}

/**
 * Headless API client for VmService.
 */
export class VmClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getVmState(): Promise<Uint8Array> {
    return this.transport.request(
      'android.emulation.v2.vm.VmService',
      'GetVmState',
      new Uint8Array(0)
    );
  }

  public streamVmState(
    onState: (state: VmState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamVmStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.vm.VmService',
      'StreamVmState',
      reqBytes,
      (bytes) => onState(VmState.decode(bytes)),
      onError
    );
  }

  public streamVmStateIterator(): AsyncIterable<VmState> {
    const reqBytes = StreamVmStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.vm.VmService',
      'StreamVmState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield VmState.decode(chunkBytes);
        }
      },
    };
  }

  public async setVmState(state: VmStateEnum): Promise<Uint8Array> {
    const writer = new BinaryWriter();
    // 1. target_state = 1
    writer.uint32(8).int32(state);
    return this.transport.request(
      'android.emulation.v2.vm.VmService',
      'SetVmState',
      writer.finish()
    );
  }

  public async pause(): Promise<Uint8Array> {
    return this.setVmState(VmStateEnum.PAUSED);
  }

  public async resume(): Promise<Uint8Array> {
    return this.setVmState(VmStateEnum.RUNNING);
  }
}
