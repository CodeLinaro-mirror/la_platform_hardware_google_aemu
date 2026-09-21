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
  CameraFrame,
  CameraService,
  CameraServiceClientImpl,
  CameraState,
  CameraState_ActiveSource,
  CameraState_SensorStatus,
  DeepPartial,
  GetCameraStateRequest,
  InjectFrameResponse,
  ResetCameraRequest,
  ResetCameraResponse,
  StreamCameraStateRequest,
} from '@android/emulator-services/camera_service';
import { ImageFormat } from '@android/emulator-services/common';

export {
  CameraFrame,
  CameraState,
  CameraState_ActiveSource,
  CameraState_SensorStatus,
  ImageFormat,
  InjectFrameResponse,
  ResetCameraRequest,
  ResetCameraResponse,
};

/**
 * Headless API client for CameraService (synthetic camera feed, state
 * observation, and static photo injection).
 *
 * Methods named in `PascalCase` are the RPCs of `CameraService`, spelled
 * exactly as `camera_service.proto` declares them.
 */
export class CameraClient {
  private readonly rpc: CameraService;

  constructor(transport: GrpcWebTransport) {
    this.rpc = new CameraServiceClientImpl(transport);
  }

  public GetCameraState(
    request: DeepPartial<GetCameraStateRequest> = {}
  ): Promise<CameraState> {
    return this.rpc.GetCameraState(GetCameraStateRequest.fromPartial(request));
  }

  public StreamCameraState(
    request: DeepPartial<StreamCameraStateRequest> = {},
    signal?: AbortSignal
  ): AsyncIterable<CameraState> {
    return this.rpc.StreamCameraState(StreamCameraStateRequest.fromPartial(request), signal);
  }

  /**
   * Pushes a single frame. `width`, `height` and `rowStrideBytes` are only
   * needed for the `IMAGE_FORMAT_RAW_*` formats; encoded containers carry
   * their own dimensions.
   */
  public InjectFrame(frame: DeepPartial<CameraFrame>): Promise<InjectFrameResponse> {
    return this.rpc.InjectFrame(CameraFrame.fromPartial(frame));
  }

  public ResetCamera(
    request: DeepPartial<ResetCameraRequest> = {}
  ): Promise<ResetCameraResponse> {
    return this.rpc.ResetCamera(ResetCameraRequest.fromPartial(request));
  }

  /** Callback form of {@link CameraClient.StreamCameraState}. */
  public subscribeCameraState(
    onState: (state: CameraState) => void,
    onError?: (err: unknown) => void,
    request: DeepPartial<StreamCameraStateRequest> = {}
  ): Unsubscribe {
    return subscribe((signal) => this.StreamCameraState(request, signal), onState, onError);
  }
}