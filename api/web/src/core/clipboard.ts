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
  ClipboardState,
  DeepPartial,
  GetClipboardStateRequest,
  StreamClipboardStateRequest,
  SystemService,
  SystemServiceClientImpl,
  UpdateClipboardStateRequest,
} from '@android/emulator-services/system_service';

export { ClipboardState, UpdateClipboardStateRequest };

/**
 * The fields of {@link ClipboardState} a caller is allowed to change.
 */
export type ClipboardStateUpdate = DeepPartial<UpdateClipboardStateRequest>;

/**
 * Headless API client for clipboard synchronization RPCs on SystemService.
 *
 * Methods named in `PascalCase` are the RPCs of `SystemService`, spelled
 * exactly as `system_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class ClipboardClient {
  private readonly rpc: SystemService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new SystemServiceClientImpl(transport);
  }

  public GetClipboardState(
    request: DeepPartial<GetClipboardStateRequest> = {}
  ): Promise<ClipboardState> {
    return this.rpc.GetClipboardState(GetClipboardStateRequest.fromPartial(request));
  }

  public StreamClipboardState(
    request: DeepPartial<StreamClipboardStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<ClipboardState> {
    return this.rpc.StreamClipboardState(StreamClipboardStateRequest.fromPartial(request), signal);
  }

  /**
   * Replaces or clears the guest clipboard contents.
   */
  public UpdateClipboardState(update: ClipboardStateUpdate): Promise<ClipboardState> {
    return this.rpc.UpdateClipboardState(UpdateClipboardStateRequest.fromPartial(update));
  }

  /** Callback form of {@link ClipboardClient.StreamClipboardState}. */
  public subscribeClipboardState(
    onState: (state: ClipboardState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamClipboardStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamClipboardState(request, signal), onState, onError);
  }
}