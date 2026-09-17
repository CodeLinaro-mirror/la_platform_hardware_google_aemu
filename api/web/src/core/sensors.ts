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
  SensorsState,
  GetSensorsStateRequest,
  StreamSensorsStateRequest,
  UpdateSensorsStateRequest,
  Vector3f,
} from "@android/emulator-services/environment/sensors_service";

export { SensorsState, Vector3f };

export interface SensorsStateUpdate {
  acceleration?: Vector3f;
  gyroscope?: Vector3f;
  magnetometer?: Vector3f;
  orientationDegrees?: Vector3f;
  ambientTemperatureCelsius?: number;
  proximityCentimeters?: number;
  lightLux?: number;
  pressureHpa?: number;
  relativeHumidityPercent?: number;
  heartRateBpm?: number;
}

/**
 * Headless API client for SensorsService.
 */
export class SensorsClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getSensorsState(): Promise<SensorsState> {
    const reqBytes = GetSensorsStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      "android.emulation.v2.environment.SensorsService",
      "GetSensorsState",
      reqBytes
    );
    return SensorsState.decode(resBytes);
  }

  public streamSensors(
    onState: (state: SensorsState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamSensorsStateRequest.encode({}).finish();
    return this.transport.stream(
      "android.emulation.v2.environment.SensorsService",
      "StreamSensorsState",
      reqBytes,
      (bytes) => onState(SensorsState.decode(bytes)),
      onError
    );
  }

  public streamSensorsIterator(): AsyncIterable<SensorsState> {
    const reqBytes = StreamSensorsStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      "android.emulation.v2.environment.SensorsService",
      "StreamSensorsState",
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield SensorsState.decode(chunkBytes);
        }
      },
    };
  }

  public async updateSensorsState(update: SensorsStateUpdate): Promise<SensorsState> {
    const paths: string[] = [];
    const state: any = {
      timestampNanos: Date.now() * 1000000,
      ambientTemperatureCelsius: 0,
      proximityCentimeters: 0,
      lightLux: 0,
      pressureHpa: 0,
      relativeHumidityPercent: 0,
      heartRateBpm: 0,
      wristTilt: 0,
      headingDegrees: 0,
    };

    if (update.acceleration) {
      paths.push("accelerometer");
      state.accelerometer = update.acceleration;
    }
    if (update.gyroscope) {
      paths.push("gyroscope");
      state.gyroscope = update.gyroscope;
    }
    if (update.magnetometer) {
      paths.push("magnetometer");
      state.magnetometer = update.magnetometer;
    }
    if (update.orientationDegrees) {
      paths.push("orientation_degrees");
      state.orientationDegrees = update.orientationDegrees;
    }
    if (update.ambientTemperatureCelsius !== undefined) {
      paths.push("ambient_temperature_celsius");
      state.ambientTemperatureCelsius = update.ambientTemperatureCelsius;
    }
    if (update.proximityCentimeters !== undefined) {
      paths.push("proximity_centimeters");
      state.proximityCentimeters = update.proximityCentimeters;
    }
    if (update.lightLux !== undefined) {
      paths.push("light_lux");
      state.lightLux = update.lightLux;
    }
    if (update.pressureHpa !== undefined) {
      paths.push("pressure_hpa");
      state.pressureHpa = update.pressureHpa;
    }
    if (update.relativeHumidityPercent !== undefined) {
      paths.push("relative_humidity_percent");
      state.relativeHumidityPercent = update.relativeHumidityPercent;
    }
    if (update.heartRateBpm !== undefined) {
      paths.push("heart_rate_bpm");
      state.heartRateBpm = update.heartRateBpm;
    }

    const req: UpdateSensorsStateRequest = {
      sensorsState: state,
      updateMask: paths,
    };

    const reqBytes = UpdateSensorsStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      "android.emulation.v2.environment.SensorsService",
      "UpdateSensorsState",
      reqBytes
    );
    return SensorsState.decode(resBytes);
  }
}
