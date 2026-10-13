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
  GetLocationStateRequest,
  LocationService,
  LocationServiceClientImpl,
  LocationState,
  PlayRouteRequest,
  PlayRouteResponse,
  RouteWaypoint,
  StreamLocationStateRequest,
  UpdateLocationStateRequest,
} from '@android/emulator-services/location_service';

export {
  LocationState,
  PlayRouteRequest,
  PlayRouteResponse,
  RouteWaypoint,
  UpdateLocationStateRequest,
};

/**
 * A GPS fix to push to the guest.
 *
 * Latitude and longitude are always sent; the remaining fields are only sent
 * when supplied.
 */
export type LocationStateUpdate = Pick<LocationState, 'latitude' | 'longitude'> &
  DeepPartial<UpdateLocationStateRequest>;

/**
 * Headless API client for LocationService.
 *
 * Methods named in `PascalCase` are the RPCs of `LocationService`, spelled
 * exactly as `location_service.proto` declares them. `camelCase` methods are
 * conveniences this package adds and have no RPC of their own.
 */
export class LocationClient {
  private readonly rpc: LocationService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new LocationServiceClientImpl(transport);
  }

  public GetLocationState(
    request: DeepPartial<GetLocationStateRequest> = {}
  ): Promise<LocationState> {
    return this.rpc.GetLocationState(GetLocationStateRequest.fromPartial(request));
  }

  public StreamLocationState(
    request: DeepPartial<StreamLocationStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<LocationState> {
    return this.rpc.StreamLocationState(StreamLocationStateRequest.fromPartial(request), signal);
  }

  /**
   * Moves the simulated device. Only the fields present in `update` are
   * changed; the rest are left alone by the server.
   */
  public UpdateLocationState(update: LocationStateUpdate): Promise<LocationState> {
    return this.rpc.UpdateLocationState(UpdateLocationStateRequest.fromPartial(update));
  }

  public PlayRoute(request: DeepPartial<PlayRouteRequest>): Promise<PlayRouteResponse> {
    return this.rpc.PlayRoute(PlayRouteRequest.fromPartial(request));
  }

  /** Callback form of {@link LocationClient.StreamLocationState}. */
  public subscribeLocationState(
    onState: (state: LocationState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamLocationStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamLocationState(request, signal), onState, onError);
  }
}
