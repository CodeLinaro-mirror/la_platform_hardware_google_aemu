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
  DisplayState,
  Rotation,
  DisplayFlag,
  ListDisplaysRequest,
  ListDisplaysResponse,
  GetDisplayRequest,
  StreamDisplayStateRequest,
  CreateDisplayRequest,
  DeleteDisplayRequest,
  UpdateDisplayStateRequest,
} from '@android/emulator-services/environment/display_service';

export { DisplayState, Rotation, DisplayFlag };

export interface DisplayStateUpdate {
  displayId: number;
  width?: number;
  height?: number;
  densityDpi?: number;
  rotation?: Rotation;
  brightnessPercent?: number;
  autoBrightnessEnabled?: boolean;
}

/**
 * Headless API client for DisplayService (Multi-display and resolution controls).
 */
export class DisplayClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async listDisplays(): Promise<DisplayState[]> {
    const reqBytes = ListDisplaysRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.DisplayService',
      'ListDisplays',
      reqBytes
    );
    const res = ListDisplaysResponse.decode(resBytes);
    return res.displays ?? [];
  }

  public streamDisplays(
    onDisplays: (displays: DisplayState[]) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamDisplayStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.environment.DisplayService',
      'StreamDisplayState',
      reqBytes,
      (bytes) => {
        const res = ListDisplaysResponse.decode(bytes);
        onDisplays(res.displays ?? []);
      },
      onError
    );
  }

  public streamDisplaysIterator(): AsyncIterable<DisplayState[]> {
    const reqBytes = StreamDisplayStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.environment.DisplayService',
      'StreamDisplayState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          const res = ListDisplaysResponse.decode(chunkBytes);
          yield res.displays ?? [];
        }
      },
    };
  }

  public async getDisplay(displayId: number): Promise<DisplayState> {
    const req: GetDisplayRequest = { displayId };
    const reqBytes = GetDisplayRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.DisplayService',
      'GetDisplay',
      reqBytes
    );
    return DisplayState.decode(resBytes);
  }

  public async createDisplay(
    width: number,
    height: number,
    densityDpi: number
  ): Promise<DisplayState> {
    const req: CreateDisplayRequest = {
      displayState: {
        displayId: 0,
        primary: false,
        width,
        height,
        densityDpi,
        rotation: Rotation.ROTATION_DEG0,
        brightnessPercent: 100,
        autoBrightnessEnabled: false,
        flags: [],
      },
    };
    const reqBytes = CreateDisplayRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.DisplayService',
      'CreateDisplay',
      reqBytes
    );
    return DisplayState.decode(resBytes);
  }

  public async deleteDisplay(displayId: number): Promise<Uint8Array> {
    const req: DeleteDisplayRequest = { displayId };
    const reqBytes = DeleteDisplayRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.environment.DisplayService',
      'DeleteDisplay',
      reqBytes
    );
  }

  public async updateDisplay(update: DisplayStateUpdate): Promise<DisplayState> {
    const paths: string[] = ['display_id'];
    const state: DisplayState = {
      displayId: update.displayId,
      primary: false,
      width: update.width ?? 0,
      height: update.height ?? 0,
      densityDpi: update.densityDpi ?? 0,
      rotation: update.rotation ?? Rotation.ROTATION_UNSPECIFIED,
      brightnessPercent: update.brightnessPercent ?? 100,
      autoBrightnessEnabled: update.autoBrightnessEnabled ?? false,
      flags: [],
    };

    if (update.width !== undefined) paths.push('width');
    if (update.height !== undefined) paths.push('height');
    if (update.densityDpi !== undefined) paths.push('density_dpi');
    if (update.rotation !== undefined) paths.push('rotation');
    if (update.brightnessPercent !== undefined) paths.push('brightness_percent');
    if (update.autoBrightnessEnabled !== undefined) paths.push('auto_brightness_enabled');

    const req: UpdateDisplayStateRequest = {
      displayState: state,
      updateMask: paths,
    };
    const reqBytes = UpdateDisplayStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.DisplayService',
      'UpdateDisplayState',
      reqBytes
    );
    return DisplayState.decode(resBytes);
  }
}