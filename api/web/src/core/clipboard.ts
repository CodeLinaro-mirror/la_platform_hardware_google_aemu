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
  ClipboardState,
  GetClipboardStateRequest,
  StreamClipboardStateRequest,
  UpdateClipboardStateRequest,
} from '@android/emulator-services/system/clipboard_service';

export { ClipboardState };

/**
 * Headless API client for ClipboardService (Host <-> Guest clipboard synchronization).
 */
export class ClipboardClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getClipboard(): Promise<ClipboardState> {
    const reqBytes = GetClipboardStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.system.ClipboardService',
      'GetClipboardState',
      reqBytes
    );
    return ClipboardState.decode(resBytes);
  }

  public streamClipboard(
    onState: (state: ClipboardState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamClipboardStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.system.ClipboardService',
      'StreamClipboardState',
      reqBytes,
      (bytes) => onState(ClipboardState.decode(bytes)),
      onError
    );
  }

  public streamClipboardIterator(): AsyncIterable<ClipboardState> {
    const reqBytes = StreamClipboardStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.system.ClipboardService',
      'StreamClipboardState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield ClipboardState.decode(chunkBytes);
        }
      },
    };
  }

  public async getClipboardText(): Promise<string> {
    const state = await this.getClipboard();
    if (!state.data || state.data.length === 0) {
      return '';
    }
    return new TextDecoder().decode(state.data);
  }

  public async setClipboard(data: Uint8Array): Promise<ClipboardState> {
    const req: UpdateClipboardStateRequest = {
      clipboardState: { data },
      updateMask: ['data'],
    };

    const reqBytes = UpdateClipboardStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.system.ClipboardService',
      'UpdateClipboardState',
      reqBytes
    );
    return ClipboardState.decode(resBytes);
  }

  public async setClipboardText(text: string): Promise<ClipboardState> {
    const data = new TextEncoder().encode(text);
    return this.setClipboard(data);
  }
}