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
  GetPostureStateRequest,
  HingeState,
  PostureState,
  PostureType,
  RollableState,
  SensorsService,
  SensorsServiceClientImpl,
  StreamPostureStateRequest,
  TransitionMode,
  UpdatePostureStateRequest,
} from '@android/emulator-services/sensors_service';

export {
  HingeState,
  PostureState,
  PostureType,
  RollableState,
  TransitionMode,
  UpdatePostureStateRequest,
};

/**
 * The fields of {@link PostureState} a caller is allowed to change.
 */
export type PostureStateUpdate = DeepPartial<UpdatePostureStateRequest>;

/**
 * Headless API client for foldable posture and hinge angle RPCs on SensorsService.
 *
 * Methods named in `PascalCase` are the RPCs of `SensorsService`, spelled
 * exactly as `sensors_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class PostureClient {
  private readonly rpc: SensorsService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new SensorsServiceClientImpl(transport);
  }

  public GetPostureState(
    request: DeepPartial<GetPostureStateRequest> = {}
  ): Promise<PostureState> {
    return this.rpc.GetPostureState(GetPostureStateRequest.fromPartial(request));
  }

  public StreamPostureState(
    request: DeepPartial<StreamPostureStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<PostureState> {
    return this.rpc.StreamPostureState(StreamPostureStateRequest.fromPartial(request), signal);
  }

  /**
   * Applies a partial update. Only the fields present in `update` are changed;
   * the rest are left alone by the server.
   */
  public UpdatePostureState(update: PostureStateUpdate): Promise<PostureState> {
    return this.rpc.UpdatePostureState(UpdatePostureStateRequest.fromPartial(update));
  }

  /** Callback form of {@link PostureClient.StreamPostureState}. */
  public subscribePostureState(
    onState: (state: PostureState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamPostureStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamPostureState(request, signal), onState, onError);
  }
}