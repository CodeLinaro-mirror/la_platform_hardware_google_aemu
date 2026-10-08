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
  DisplayService,
  DisplayServiceClientImpl,
  GetScreenshotRequest,
  Screenshot,
  StreamScreenshotsRequest,
} from '@android/emulator-services/display_service';
import { ImageFormat } from '@android/emulator-services/common';

export { Screenshot, ImageFormat, GetScreenshotRequest, StreamScreenshotsRequest };

/**
 * Headless API client for screenshot and frame streaming RPCs on DisplayService.
 *
 * Methods named in `PascalCase` are the RPCs of `DisplayService`, spelled
 * exactly as `display_service.proto` declares them. `camelCase` methods
 * are conveniences this package adds and have no RPC of their own.
 */
export class ScreenCaptureClient {
  private readonly rpc: DisplayService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new DisplayServiceClientImpl(transport);
  }

  /**
   * An unset `format` asks the server for its default encoding; an unset
   * `displayId` selects the primary display.
   */
  public GetScreenshot(request: DeepPartial<GetScreenshotRequest> = {}): Promise<Screenshot> {
    return this.rpc.GetScreenshot(GetScreenshotRequest.fromPartial(request));
  }

  /**
   * `maxFramesPerSecond` is optional in the proto; leaving it out lets the
   * server emit a frame per composite rather than throttling.
   */
  public StreamScreenshots(
    request: DeepPartial<StreamScreenshotsRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<Screenshot> {
    return this.rpc.StreamScreenshots(StreamScreenshotsRequest.fromPartial(request), signal);
  }

  /** Callback form of {@link ScreenCaptureClient.StreamScreenshots}. */
  public subscribeScreenshots(
    onScreenshot: (screenshot: Screenshot) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamScreenshotsRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamScreenshots(request, signal), onScreenshot, onError);
  }
}