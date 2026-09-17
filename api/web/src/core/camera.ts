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
  CameraFrame,
  ResetCameraRequest,
} from '@android/emulator-services/environment/camera_service';
import { ImageFormat } from '@android/emulator-services/common/common';

export { CameraFrame, ImageFormat };

export interface InjectFrameOptions {
  cameraId?: string;
  frameData: Uint8Array;
  format?: ImageFormat;
  width?: number;
  height?: number;
  rowStrideBytes?: number;
}

/**
 * Headless API client for CameraService (Synthetic camera feed & static photo injection).
 */
export class CameraClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async injectFrame(options: InjectFrameOptions): Promise<Uint8Array> {
    const frame: CameraFrame = {
      cameraId: options.cameraId ?? '0',
      frameData: options.frameData,
      format: options.format ?? ImageFormat.IMAGE_FORMAT_PNG,
      width: options.width,
      height: options.height,
      rowStrideBytes: options.rowStrideBytes,
    };
    const reqBytes = CameraFrame.encode(frame).finish();
    return this.transport.request(
      'android.emulation.v2.environment.CameraService',
      'InjectFrame',
      reqBytes
    );
  }

  public async resetCamera(cameraId: string = '0'): Promise<Uint8Array> {
    const req: ResetCameraRequest = { cameraId };
    const reqBytes = ResetCameraRequest.encode(req).finish();
    return this.transport.request(
      'android.emulation.v2.environment.CameraService',
      'ResetCamera',
      reqBytes
    );
  }
}