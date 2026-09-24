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
  CreateDisplayRequest,
  DeepPartial,
  DeleteDisplayRequest,
  DeleteDisplayResponse,
  Display,
  DisplayFlag,
  DisplayService,
  DisplayServiceClientImpl,
  GetDisplayRequest,
  ListDisplaysRequest,
  ListDisplaysResponse,
  StreamDisplaysRequest,
  UpdateDisplayRequest,
} from '@android/emulator-services/display_service';
import { Rotation } from '@android/emulator-services/common';

export {
  Display,
  ListDisplaysResponse,
  Rotation,
  DisplayFlag,
  UpdateDisplayRequest,
  DeleteDisplayResponse,
};

/**
 * The fields of {@link Display} a caller is allowed to change.
 */
export type DisplayUpdate = Pick<UpdateDisplayRequest, 'displayId'> &
  DeepPartial<UpdateDisplayRequest>;

/**
 * Headless API client for DisplayService (multi-display and resolution
 * controls).
 *
 * Methods named in `PascalCase` are the RPCs of `DisplayService`, spelled
 * exactly as `display_service.proto` declares them, and return the response
 * message the proto declares. `camelCase` methods are conveniences this package
 * adds and have no RPC of their own.
 */
export class DisplayClient {
  private readonly rpc: DisplayService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new DisplayServiceClientImpl(transport);
  }

  public ListDisplays(
    request: DeepPartial<ListDisplaysRequest> = {}
  ): Promise<ListDisplaysResponse> {
    return this.rpc.ListDisplays(ListDisplaysRequest.fromPartial(request));
  }

  public GetDisplay(request: DeepPartial<GetDisplayRequest> = {}): Promise<Display> {
    return this.rpc.GetDisplay(GetDisplayRequest.fromPartial(request));
  }

  /**
   * Each message carries the whole display list, not a delta, so a consumer
   * that misses a message has not missed a state.
   */
  public StreamDisplays(
    request: DeepPartial<StreamDisplaysRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<ListDisplaysResponse> {
    return this.rpc.StreamDisplays(StreamDisplaysRequest.fromPartial(request), signal);
  }

  /**
   * Applies a partial update to one display. Only the fields present in
   * `update` are changed; the rest are left alone by the server.
   */
  public UpdateDisplay(update: DisplayUpdate): Promise<Display> {
    return this.rpc.UpdateDisplay(UpdateDisplayRequest.fromPartial(update));
  }

  public CreateDisplay(request: DeepPartial<CreateDisplayRequest>): Promise<Display> {
    return this.rpc.CreateDisplay(CreateDisplayRequest.fromPartial(request));
  }

  public DeleteDisplay(
    request: DeepPartial<DeleteDisplayRequest> = {}
  ): Promise<DeleteDisplayResponse> {
    return this.rpc.DeleteDisplay(DeleteDisplayRequest.fromPartial(request));
  }

  /** Callback form of {@link DisplayClient.StreamDisplays}. */
  public subscribeDisplayState(
    onDisplays: (response: ListDisplaysResponse) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamDisplaysRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamDisplays(request, signal), onDisplays, onError);
  }
}