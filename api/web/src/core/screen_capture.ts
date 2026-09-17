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
  Screenshot,
  GetScreenshotRequest,
  StreamScreenshotsRequest,
} from '@android/emulator-services/media/screen_capture_service';
import { ImageFormat } from '@android/emulator-services/common/common';

export { Screenshot, ImageFormat };

export interface ScreenshotOptions {
  displayId?: number;
  format?: ImageFormat;
}

export interface StreamScreenshotsOptions extends ScreenshotOptions {
  maxFramesPerSecond?: number;
}

/**
 * Headless API client for ScreenCaptureService (Screenshots & Frame Streaming).
 */
export class ScreenCaptureClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getScreenshot(options?: ScreenshotOptions): Promise<Screenshot> {
    const req: GetScreenshotRequest = {
      displayId: options?.displayId ?? 0,
      format: options?.format ?? ImageFormat.IMAGE_FORMAT_PNG,
    };
    const reqBytes = GetScreenshotRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.media.ScreenCaptureService',
      'GetScreenshot',
      reqBytes
    );
    return Screenshot.decode(resBytes);
  }

  public streamScreenshots(
    options?: StreamScreenshotsOptions
  ): AsyncIterable<Screenshot> {
    const req: StreamScreenshotsRequest = {
      displayId: options?.displayId ?? 0,
      format: options?.format ?? ImageFormat.IMAGE_FORMAT_PNG,
      maxFramesPerSecond: options?.maxFramesPerSecond ?? 10,
    };
    const reqBytes = StreamScreenshotsRequest.encode(req).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.media.ScreenCaptureService',
      'StreamScreenshots',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield Screenshot.decode(chunkBytes);
        }
      },
    };
  }

  public subscribeScreenshots(
    onScreenshot: (screenshot: Screenshot) => void,
    onError?: (err: any) => void,
    options?: StreamScreenshotsOptions
  ): () => void {
    const req: StreamScreenshotsRequest = {
      displayId: options?.displayId ?? 0,
      format: options?.format ?? ImageFormat.IMAGE_FORMAT_PNG,
      maxFramesPerSecond: options?.maxFramesPerSecond ?? 10,
    };
    const reqBytes = StreamScreenshotsRequest.encode(req).finish();
    return this.transport.stream(
      'android.emulation.v2.media.ScreenCaptureService',
      'StreamScreenshots',
      reqBytes,
      (bytes) => onScreenshot(Screenshot.decode(bytes)),
      onError
    );
  }
}