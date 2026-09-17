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

import { GrpcWebTransport } from "./transport";
import {
  LocationState,
  GetLocationStateRequest,
  StreamLocationStateRequest,
  UpdateLocationStateRequest,
} from "@android/emulator-services/environment/location_service";

export { LocationState };

export interface LocationUpdate {
  latitude: number;
  longitude: number;
  altitudeMeters?: number;
  speedMetersPerSecond?: number;
  bearingDegrees?: number;
  accuracyMeters?: number;
  satelliteCount?: number;
}

/**
 * Headless API client for LocationService.
 */
export class LocationClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getLocationState(): Promise<LocationState> {
    const reqBytes = GetLocationStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      "android.emulation.v2.environment.LocationService",
      "GetLocationState",
      reqBytes
    );
    return LocationState.decode(resBytes);
  }

  public streamLocation(
    onState: (state: LocationState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamLocationStateRequest.encode({}).finish();
    return this.transport.stream(
      "android.emulation.v2.environment.LocationService",
      "StreamLocationState",
      reqBytes,
      (bytes) => onState(LocationState.decode(bytes)),
      onError
    );
  }

  public streamLocationIterator(): AsyncIterable<LocationState> {
    const reqBytes = StreamLocationStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      "android.emulation.v2.environment.LocationService",
      "StreamLocationState",
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield LocationState.decode(chunkBytes);
        }
      },
    };
  }

  public async setLocation(loc: LocationUpdate): Promise<LocationState> {
    const paths = ["latitude", "longitude"];
    const state: any = {
      latitude: loc.latitude,
      longitude: loc.longitude,
      altitudeMeters: loc.altitudeMeters ?? 0,
      speedMetersPerSecond: loc.speedMetersPerSecond ?? 0,
      bearingDegrees: loc.bearingDegrees ?? 0,
      accuracyMeters: loc.accuracyMeters ?? 0,
      satelliteCount: loc.satelliteCount ?? 0,
      timestampNanos: Date.now() * 1000000,
    };

    if (loc.altitudeMeters !== undefined) {
      paths.push("altitude_meters");
    }
    if (loc.speedMetersPerSecond !== undefined) {
      paths.push("speed_meters_per_second");
    }
    if (loc.bearingDegrees !== undefined) {
      paths.push("bearing_degrees");
    }
    if (loc.accuracyMeters !== undefined) {
      paths.push("accuracy_meters");
    }
    if (loc.satelliteCount !== undefined) {
      paths.push("satellite_count");
    }

    const req: UpdateLocationStateRequest = {
      locationState: state,
      updateMask: paths,
    };

    const reqBytes = UpdateLocationStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      "android.emulation.v2.environment.LocationService",
      "UpdateLocationState",
      reqBytes
    );
    return LocationState.decode(resBytes);
  }
}
